"""Calidad de catálogo 2026-10-01: separador de miles, imperial vs métrico y display."""
from __future__ import annotations

from decimal import Decimal

import pytest

from precios_supermercados.consumer_catalog_display import canonical_presentation
from precios_supermercados.product_homologation import (
    SourceProductRecord,
    presentations_compatible,
)
from precios_supermercados.product_identity_v2 import (
    canonical_presentation_fields,
    display_quantity,
    homologate_products_v2,
    normalize_thousands_separators,
    resolve_presentation_v2,
)


def record(
    name: str,
    *,
    supermarket: str = "walmart",
    presentation: str | None = None,
    barcode: str | None = None,
    record_id: str | None = None,
) -> SourceProductRecord:
    return SourceProductRecord(
        source_record_id=record_id or f"{supermarket}:{name}",
        supermarket_id=supermarket,
        source_name=name,
        source_presentation=presentation,
        barcode=barcode,
    )


def signature(name: str, **kwargs):
    value, _ = resolve_presentation_v2(record(name, **kwargs))
    return value


@pytest.mark.parametrize(
    ("name", "presentation", "dimension", "total"),
    [
        # Walmart/Paiz publican "1,400 ml" y el scraper derivó "1.4 ml".
        ("Aceite Clover Brand 1,400 ml", "1.4 ml", "volume_ml", Decimal("1400")),
        ("Aceite Clover Brand - 2,750 ml", "2.75 ml", "volume_ml", Decimal("2750")),
        ("Manteca Clover Brand Ultra Kilo - 1,230 g", "1.23 g", "mass_g", Decimal("1230")),
        ("SULA PoncheDeFruta 1.750 ml", "1.75 ml", "volume_ml", Decimal("1750")),
        ("Desinfectante Magia Blanca Lavanda Francesa 3.785 Ml", "3.785 ml", "volume_ml", Decimal("3785")),
        ("Helado Osos Polar - 1892Ml", "1,892 ml", "volume_ml", Decimal("1892")),
    ],
)
def test_thousands_separator_in_small_units(name, presentation, dimension, total) -> None:
    value = signature(name, presentation=presentation)
    assert value is not None
    assert (value.dimension, value.total_base) == (dimension, total)


@pytest.mark.parametrize(
    ("name", "dimension", "total"),
    [
        # Unidades grandes conservan el decimal.
        ("SALUT Jugo Maracuya 1.892 Lts", "volume_ml", Decimal("1892")),
        ("Harina La Rosa De Trigo - 2.268 Kg", "mass_g", Decimal("2268")),
        ("Jugo Del Valle Naranja 1,5 L", "volume_ml", Decimal("1500")),
        ("Ideal aceite sin colesterol 3.750 ltrs", "volume_ml", Decimal("3750")),
        ("Doritos nacho cheese picante 2.125 oz", "ounce", Decimal("2.125")),
        # Parte entera 0: no es separador de miles.
        ("Leche Entera Sula Uht Enriquecida y Fortificada - 0.946 ml", "volume_ml", Decimal("0.946")),
    ],
)
def test_decimal_separator_is_kept_for_large_units(name, dimension, total) -> None:
    value = signature(name)
    assert value is not None
    assert (value.dimension, value.total_base) == (dimension, total)


def test_thousands_rule_text_examples() -> None:
    assert normalize_thousands_separators("Aceite 1,400 ml") == "Aceite 1400 ml"
    assert normalize_thousands_separators("Azúcar 1.500 g") == "Azúcar 1500 g"
    assert normalize_thousands_separators("Toallitas 1.000 unidades") == "Toallitas 1000 unidades"
    assert normalize_thousands_separators("Azúcar 1,5 kg") == "Azúcar 1,5 kg"
    assert normalize_thousands_separators("Azúcar 1,500 kg") == "Azúcar 1,500 kg"
    assert normalize_thousands_separators("Refresco 1.25 L") == "Refresco 1.25 L"
    assert normalize_thousands_separators("Lote 1,400,000 ml") == "Lote 1,400,000 ml"


def test_independent_source_presentation_still_conflicts_with_thousands_name() -> None:
    # Una fuente que publica otra cantidad sigue siendo un conflicto.
    _, status = resolve_presentation_v2(record("Aceite Clover Brand 1,400 ml", presentation="900 ml"))
    assert status == "conflict"


def test_explicit_metric_wins_over_pounds() -> None:
    value = signature("Carne Molida Member's Selection 623.7 g / 1.37 lb", supermarket="pricesmart")
    assert (value.dimension, value.total_base) == ("mass_g", Decimal("623.7"))
    reverse = signature("Pechuga de pollo 2 Lb (908 g)", supermarket="pricesmart")
    assert reverse.total_base == Decimal("908")


def test_inconsistent_pound_and_gram_label_is_not_resolved() -> None:
    assert signature("Queso Cheddar 2 lb 500 g") is None


def test_pounds_versus_grams_agree_on_size() -> None:
    pounds = signature("Arroz Vigo Jasmin 2 Lb", supermarket="la_colonia")
    grams = signature("VIGO Jazmine Rice 908 g", supermarket="colonial")
    assert pounds.dimension == grams.dimension == "mass_g"
    assert pounds.total_base == Decimal("907.18474")
    assert presentations_compatible(pounds, grams)
    # Redondeo de fabricante dentro del 2 % imperial: "1 lb" ≈ "450 g".
    assert presentations_compatible(
        signature("Mantequilla Leyde Crema 1 lbs", supermarket="colonial"),
        signature("Mantequilla Leyde Crema 450 g", supermarket="walmart"),
    )
    # Fuera de tolerancia sigue siendo otra presentación.
    assert not presentations_compatible(
        signature("Mantequilla Leyde Crema 1 lbs", supermarket="colonial"),
        signature("Mantequilla Leyde Crema 400 g", supermarket="walmart"),
    )


def test_metric_tolerance_is_unchanged_for_metric_labels() -> None:
    assert not presentations_compatible(
        signature("Arroz Progreso 450 g"),
        signature("Arroz Progreso 460 g", supermarket="paiz"),
    )


def test_ounces_convert_to_grams_only_for_solid_product_types() -> None:
    paella = signature("Paella Vigo Arroz Con Azafrán Y Mariscos 19 Oz", supermarket="la_colonia")
    assert paella.dimension == "mass_g"
    assert paella.declared_ounces == Decimal("19")
    assert presentations_compatible(paella, signature("VIGO Paella Valenciana Arroz 539 g", supermarket="colonial"))
    cheese = signature("Queso Crema Philadelphia Original 8 Oz", supermarket="la_colonia")
    assert cheese.dimension == "mass_g"
    assert presentations_compatible(cheese, signature("Queso Crema Philadelphia Original 226 g"))
    # Aderezo / jugo: la onza puede ser de volumen; queda sin convertir.
    assert signature("Aderezo Kraft Ranch Classic 8 Oz").dimension == "ounce"
    assert signature("Jugo Welch's Uva 64 oz").dimension == "ounce"


def test_converted_ounces_remain_compatible_with_ounce_only_sources() -> None:
    converted = signature("Queso Crema Philadelphia Original 8 Oz", supermarket="la_colonia")
    unconverted = signature("Philadelphia Original 8 Oz", supermarket="pricesmart")
    assert unconverted.dimension == "ounce"
    assert presentations_compatible(converted, unconverted)


def test_same_gtin_ounce_and_gram_listings_are_comparable_for_solids() -> None:
    gtin = "07501020515343"
    result = homologate_products_v2(
        [
            record("Paella Vigo Arroz Con Azafrán Y Mariscos 19 Oz", supermarket="la_colonia", barcode=gtin, record_id="lc:1"),
            record("Arroz Paella Vigo Con Azafrán Y Mariscos - 539 g", supermarket="walmart", barcode=gtin, record_id="wm:1"),
        ]
    )
    (group,) = result.exact_gtin_groups
    assert group.comparison_status == "ready"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("907.18474"), "907.18"),
        (Decimal("453.59237"), "453.59"),
        (Decimal("621.4215469"), "621.42"),
        (Decimal("1400"), "1400"),
        (Decimal("0.075"), "0.075"),
        (Decimal("0.0025"), "0.0025"),
        (Decimal("0.12345"), "0.123"),
    ],
)
def test_display_quantity_rounds_only_for_display(value, expected) -> None:
    assert display_quantity(value) == expected


def test_canonical_fields_keep_exact_total_but_round_display() -> None:
    fields = canonical_presentation_fields(record("Arroz Vigo Jasmin 2 Lb", supermarket="la_colonia"))
    assert fields.canonical_total == Decimal("907.18474")
    assert fields.display_presentation == "907.18 g"


def test_public_presentation_rounds_and_reads_thousands() -> None:
    assert canonical_presentation(
        source_presentation=None,
        product_name="Arroz Vigo Jasmin 2 Lb",
        product_type="Arroz",
        presentation_dimension="mass_g",
        presentation_total_base="907.18474",
        presentation_status="name_only",
    ) == "907.18 g"
    # Sin firma aceptada, el texto se lee con la misma regla de miles.
    assert canonical_presentation(
        source_presentation=None,
        product_name="Aceite Clover Brand 1,400 ml",
        product_type="Aceite comestible",
        presentation_dimension=None,
        presentation_total_base=None,
        presentation_status="conflict",
    ) == "1400 ml"


@pytest.mark.parametrize(
    ("name", "presentation", "expected"),
    [
        # PriceSmart repite la conversión imperial como presentación fuente.
        ("Pollo Entero Congelado sin Menudo Bolsa 10.8 kg / 24 lb", "10886.21688 g", "10800 g"),
        ("Edwards Tarta de Lima 861 g / 1.9 lb", "861.825503 g", "861 g"),
        ("Belgioioso Queso Mozzarella Fresco Rebanado 453 g / 16 oz", "16 oz", "453 g"),
        ("Dos Pinos Queso Manchego 340 g / 12 oz", "12 oz", "340 g"),
    ],
)
def test_explicit_metric_of_dual_label_wins_over_derived_source(name, presentation, expected) -> None:
    fields = canonical_presentation_fields(record(name, supermarket="pricesmart", presentation=presentation))
    assert (fields.display_presentation, fields.status) == (expected, "confirmed")


def test_dual_label_with_rounded_metric_accepts_source_imperial_conversion() -> None:
    # "1.3 kg / 3 lb": la fuente repite 3 lb = 1360.8 g; sigue siendo una etiqueta.
    fields = canonical_presentation_fields(
        record("Aguacate Hass Fresco 1.3 kg / 3 lb", supermarket="pricesmart", presentation="1360.77711 g")
    )
    assert (fields.display_presentation, fields.status) == ("1300 g", "confirmed")
    _, status = resolve_presentation_v2(
        record("Aguacate Hass Fresco 1.3 kg / 3 lb", supermarket="pricesmart", presentation="2 kg")
    )
    assert status == "conflict"
