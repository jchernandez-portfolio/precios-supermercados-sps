"""Parser de presentación v2.4: etiquetas duales, multipacks y combos."""
from __future__ import annotations

from decimal import Decimal

import pytest

from precios_supermercados.product_homologation import (
    SourceProductRecord,
    assign_taxonomy,
    presentations_compatible,
)
from precios_supermercados.product_identity_v2 import (
    canonical_presentation_fields,
    homologate_products_v2,
    is_bundle_name,
    resolve_presentation_v2,
)


def record(name: str, *, supermarket: str = "walmart", record_id: str | None = None, barcode: str | None = None, presentation: str | None = None) -> SourceProductRecord:
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
    "name",
    [
        "Salchicha Gwaltney 16 oz (454 g)",
        "Salchicha Gwaltney 454 g / 16 oz",
        "Salchicha Gwaltney 454g o 16oz",
        "Salchicha Gwaltney - 16 Oz 454 Gr",
    ],
)
def test_dual_label_prefers_metric_regardless_of_order(name: str) -> None:
    value = signature(name)
    assert value is not None
    assert (value.dimension, value.total_base, value.pack_count) == ("mass_g", Decimal("454"), 1)
    assert value.declared_ounces == Decimal("16")


def test_dual_fluid_label_prefers_millilitres() -> None:
    value = signature("Bebida Blue Diamond Almond Breeze 32 oz / 946 ml")
    assert (value.dimension, value.total_base) == ("volume_ml", Decimal("946"))
    assert value.declared_ounces == Decimal("32")


def test_inconsistent_dual_label_is_not_resolved() -> None:
    # 11.7 oz = 331.7 g: las dos etiquetas no describen la misma cantidad.
    assert signature("Piazza Jirafa Fresa 24pl 140g 11.7oz") is None


def test_dual_label_stays_compatible_with_ounce_only_and_metric_only_sources() -> None:
    dual = signature("Salchicha Gwaltney 16 oz (454 g)")
    ounce_only = signature("Salchicha Gwalney Con Queso- 16Oz", supermarket="la_colonia")
    metric_only = signature("Salchicha Gwaltney - 454 g", supermarket="paiz")
    assert presentations_compatible(dual, ounce_only)
    assert presentations_compatible(ounce_only, dual)
    assert presentations_compatible(dual, metric_only)
    assert not presentations_compatible(ounce_only, signature("Salchicha Gwaltney 12 oz (340 g)"))


def test_ounce_only_versus_metric_only_remains_a_conflict() -> None:
    # Onza sin calificar puede ser peso o volumen: sin etiqueta dual no se convierte.
    assert not presentations_compatible(
        signature("Aderezo Kraft Ranch Classic 8 Oz"),
        signature("Aderezo Kraft Ranch 237 ml"),
    )


@pytest.mark.parametrize(
    ("name", "pack_count", "unit_amount", "total", "dimension"),
    [
        ("Jugo Del Valle 6x355ml", 6, "355", "2130", "volume_ml"),
        ("Cerveza Salva Vida Pack 12 x 330 ml", 12, "330", "3960", "volume_ml"),
        ("Leche Sula Entera 2 x 1L", 2, "1000", "2000", "volume_ml"),
        ("Gaseosa Coca Cola Lata 355 ml x 6", 6, "355", "2130", "volume_ml"),
        ("Cerveza Imperial 12 latas de 355 ml", 12, "355", "4260", "volume_ml"),
        ("Agua Aquafina 6 botellas x 600 ml", 6, "600", "3600", "volume_ml"),
        ("Café Maya 10 sobres de 25 g", 10, "25", "250", "mass_g"),
        ("Jabón Xtra 3 barras de 110 g", 3, "110", "330", "mass_g"),
        ("Barra Desodorante Old Spice Fresh 50 g X 3 Unidades", 3, "50", "150", "mass_g"),
    ],
)
def test_multipacks_expose_total_and_unit_count(name, pack_count, unit_amount, total, dimension) -> None:
    value = signature(name)
    assert value is not None
    assert value.dimension == dimension
    assert value.pack_count == pack_count
    assert value.unit_amount_base == Decimal(unit_amount)
    assert value.total_base == Decimal(total)


def test_multipack_canonical_fields_keep_pack_and_total() -> None:
    fields = canonical_presentation_fields(record("Gaseosa Coca Cola Lata 355 ml x 6"))
    assert fields.normalized_pack_count == 6
    assert fields.normalized_quantity == Decimal("355")
    assert fields.canonical_total == Decimal("2130")
    assert fields.display_presentation == "6 × 355 ml"


def test_multipack_is_not_compatible_with_single_unit() -> None:
    assert not presentations_compatible(
        signature("Gaseosa Coca Cola Lata 355 ml x 6"),
        signature("Gaseosa Coca Cola Lata 355 ml"),
    )


def test_dimension_pairs_are_not_read_as_multipack() -> None:
    assert signature("Bolsa Plástica 10 cm x 20 cm") is None


def test_single_pack_is_not_ambiguous_but_n_pack_is() -> None:
    value, status = resolve_presentation_v2(record("1 Pack Aromatizante Little Tree Pinito Fresa 45 g"))
    assert status == "name_only" and value.total_base == Decimal("45")
    assert resolve_presentation_v2(record("Cerveza Coors Light Lata 6 Pack - 2124 ml")) == (
        None,
        "ambiguous_multipack",
    )


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Combo Mortadela Salchicha y Longaniza Suli - 1362 g", True),
        ("Kit Portátil Colgate con Cepillo Dental Plegable + Pasta Dental - 22 ml", True),
        ("Shampoo Pantene 400 ml + Acondicionador 200 ml", True),
        ("Detergente Ariel 1 kg + 20% Gratis", True),
        ("Mayonesa Hellmanns 2 Pack con Bowl Gratis - 760 g", True),
        ("Cepillo Dental Colgate Luminous 2x1", True),
        ("Galletas Chiky 3x2", True),
        ("Desodorante Lady Speed Stick Derma + Aclarado Perla Barra - 45 g", False),
        ("Protector Solar Nivea FPS50+ 200ML", False),
        ("BARILLA Farfalle Protein+ 14.5 oz", False),
        ("Contenedor Supermax 8X8 Div Des Foam - 25 Unidades", False),
        ("Desarmador Punta Phillips 1/4X4 Plg", False),
        ("Leche Sula Entera 1 L", False),
    ],
)
def test_bundle_detection(name: str, expected: bool) -> None:
    assert is_bundle_name(name) is expected


def test_bundle_member_is_excluded_from_same_gtin_group_of_singles() -> None:
    gtin = "7501055300075"
    result = homologate_products_v2(
        (
            record("Mayonesa Hellmanns 380 g", supermarket="walmart", barcode=gtin),
            record("Mayonesa Hellmanns 380 g", supermarket="paiz", barcode=gtin),
            record("Mayonesa Hellmanns 380 g Combo con Bowl", supermarket="la_colonia", barcode=gtin),
        )
    )
    (group,) = result.exact_gtin_groups
    assert group.comparison_status == "ready"
    assert dict(group.excluded_members) == {
        "la_colonia:Mayonesa Hellmanns 380 g Combo con Bowl": ("bundle_vs_single_conflict",)
    }


def test_bundle_never_becomes_candidate_for_single() -> None:
    result = homologate_products_v2(
        (
            record("Mayonesa Hellmanns 380 g", supermarket="walmart"),
            record("Mayonesa Hellmanns 380 g Combo con Bowl", supermarket="colonial"),
        ),
        candidate_threshold=Decimal("0"),
    )
    assert result.candidates == ()


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("COCA COLA Sin Azucar Lata 12oz", None),
        ("Gelatina Royal Bajo en Azúcar 25 g", "Gelatina"),
        ("Azúcar Morena Cañeros 2 lb", "Azúcar"),
    ],
)
def test_sugar_free_claim_is_not_sugar_product_type(name: str, expected: str | None) -> None:
    assert assign_taxonomy(record(name)).product_type == expected
