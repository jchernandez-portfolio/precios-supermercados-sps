from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "exportar_consumer_catalog.py"
SPEC = importlib.util.spec_from_file_location("exportar_consumer_catalog_display_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def offer(
    *,
    name: str,
    brand: str | None,
    presentation: str | None,
    dimension: str | None,
    total: str | None,
    status: str = "confirmed",
    product_type: str | None = "Huevo",
    category: str | None = "Alimentos",
    availability: str = "in_stock",
) -> object:
    return MODULE.VisibleOffer(
        source_product_id="colonial:1",
        supermarket_id="colonial",
        location_id="colonial_sps",
        product_name=name,
        brand=brand,
        presentation=presentation,
        current_price_minor=10000,
        reported_regular_price_minor=None,
        is_promotion=False,
        availability=availability,
        observed_at="2026-09-10T12:00:00Z",
        canonical_product_id=None,
        category=category,
        product_type=product_type,
        presentation_dimension=dimension,
        presentation_total_base=total,
        presentation_status=status,
        comparison_status="unmapped",
    )


def test_count_aliases_collapse_to_one_consumer_presentation() -> None:
    for raw in ("30 Un", "30Un", "30 unidades", "30 UND", "30u"):
        item = offer(
            name=f"Huevos Bonovo Cartón M {raw}",
            brand="Bonovo",
            presentation=raw,
            dimension="count",
            total="30",
        )
        assert MODULE._derived_presentation(item) == "30 unidades"


def test_mass_alias_is_normalized_without_changing_quantity() -> None:
    item = offer(
        name="Tallarin de Huevo Long Life 400 Gr",
        brand="LONG LIFE",
        presentation="400 Gr",
        dimension="mass_g",
        total="400",
        product_type="Pasta",
    )
    assert MODULE._derived_presentation(item) == "400 g"


def test_egg_grade_g_before_und_is_not_interpreted_as_grams() -> None:
    item = offer(
        name="Norteño Huevos 30G UND",
        brand="RMS",
        presentation=None,
        dimension="mass_g",
        total="30",
        status="name_only",
    )
    assert MODULE._derived_presentation(item) == "30 unidades"
    rows = MODULE.build_rows(
        (item,),
        {("colonial", "colonial_sps"): "FRESH"},
    )
    assert rows[0]["brand"] == "Norteño"
    assert rows[0]["presentation"] == "30 unidades"
    assert rows[0]["variant"] == "Grande"


def test_shell_egg_pack_is_normalized_as_count() -> None:
    item = offer(
        name="Nutri yema huevo grande 60 pack",
        brand="Marca COMANDES",
        presentation=None,
        dimension=None,
        total=None,
        status="missing",
    )
    rows = MODULE.build_rows(
        (item,),
        {("colonial", "colonial_sps"): "FRESH"},
    )
    assert rows[0]["brand"] == "Nutri Yema"
    assert rows[0]["presentation"] == "60 unidades"
    assert rows[0]["variant"] == "Grande"


def test_egg_beaters_placeholder_is_recovered_into_consumer_egg_taxonomy() -> None:
    item = offer(
        name="EGG Beaters Wht 16 oz",
        brand="RMS",
        presentation=None,
        dimension=None,
        total=None,
        status="missing",
        product_type=None,
        category=None,
    )
    rows = MODULE.build_rows(
        (item,),
        {("colonial", "colonial_sps"): "FRESH"},
    )
    assert len(rows) == 1
    assert rows[0]["brand"] == "Egg Beaters"
    assert rows[0]["category"] == "Alimentos"
    assert rows[0]["product_type"] == "Huevo"
    assert rows[0]["presentation"] == "16 oz"
    assert rows[0]["offers"][0]["current_price"] == "100.00"


def test_out_of_stock_only_row_is_not_visible_in_compra_inteligente() -> None:
    item = offer(
        name="Claras De Huevo Egg Beaters 16 Oz",
        brand="Egg Beaters",
        presentation="16 Oz",
        dimension="ounce",
        total="16",
        availability="out_of_stock",
    )
    rows = MODULE.build_rows(
        (item,),
        {("colonial", "colonial_sps"): "FRESH"},
    )
    assert rows == []


def test_placeholder_brand_is_recovered_only_from_explicit_alias() -> None:
    assert MODULE.canonical_brand("RMS", "Bonovo Huevo G 30 und") == "Bonovo"
    assert MODULE.canonical_brand("Marca COMANDES", "Nutri yema huevos 30 unds") == "Nutri Yema"
    assert MODULE.canonical_brand("RMS", "Producto sin marca reconocible 30 und") is None


def test_unique_explicit_name_brand_overrides_conflicting_source_brand_for_display() -> None:
    assert (
        MODULE.canonical_brand(
            "DON CRISTOBAL",
            "Huevo de Gallina Marketside Jumbo - 20 Unidades",
        )
        == "Marketside"
    )
    assert (
        MODULE.canonical_brand(
            "MARKETSIDE",
            "Huevo de Gallina Don Cristobal Marron - 15 Unidades",
        )
        == "Don Cristobal"
    )


def test_non_generic_source_brand_is_kept_without_an_explicit_conflicting_alias() -> None:
    assert MODULE.canonical_brand("DON CRISTOBAL", "Huevo de Gallina Marron - 15 Unidades") == "Don Cristobal"
    assert MODULE.canonical_brand("Marca Propia", "Producto sin marca reconocible 30 und") == "Marca Propia"


def test_brand_case_and_accent_aliases_are_canonical() -> None:
    pairs = {
        "KRAFT": "Kraft",
        "Kraft": "Kraft",
        "REGIA": "Regia",
        "Regia": "Regia",
        "MCCORMICK": "McCormick",
        "McCormick": "McCormick",
        "NORTENO": "Norteño",
        "NORTEÑO": "Norteño",
        "MARKETSIDE": "Marketside",
        "GREAT VALUE": "Great Value",
        "HELLMANN'S": "Hellmann's",
        "Hellmanns": "Hellmann's",
    }
    for raw, expected in pairs.items():
        assert MODULE.canonical_brand(raw, f"Producto {raw}") == expected


def test_egg_sizes_are_separate_from_pack_count() -> None:
    cases = {
        "Huevos Bonovo Cartón P 30Un": "Pequeño",
        "Huevos Bonovo Cartón M 30Un": "Mediano",
        "Bonovo Huevo G 30 und": "Grande",
        "Norteño Huevos 30XL UND": "Extra grande",
        "Bonovo jumbo huevos carton 10 und": "Jumbo",
        "Nutri yema huevo grande 60 pack": "Grande",
    }
    for name, expected in cases.items():
        assert MODULE.canonical_egg_size(name, "Huevo") == expected
        assert MODULE.canonical_presentation(
            source_presentation=None,
            product_name=name,
            product_type="Huevo",
            presentation_dimension=None,
            presentation_total_base=None,
            presentation_status="missing",
        ).endswith(" unidades")


def test_non_shell_egg_names_do_not_trigger_grade_override() -> None:
    assert MODULE.canonical_presentation(
        source_presentation="20 g",
        product_name="KINDER Huevo Sorpresa Niño 20g",
        product_type="Huevo",
        presentation_dimension="mass_g",
        presentation_total_base="20",
        presentation_status="confirmed",
    ) == "20 g"
    assert MODULE.canonical_presentation(
        source_presentation="1774 ml",
        product_name="Kraft Mayonesa de Huevos de Gallina Libres 2 Unidades / 887 mL / 30 oz",
        product_type="Huevo",
        presentation_dimension="volume_ml",
        presentation_total_base="1774",
        presentation_status="confirmed",
    ) == "1.774 L"


def test_common_unmodeled_unit_is_normalized_without_becoming_unknown() -> None:
    assert MODULE.canonical_presentation(
        source_presentation="1 Gl",
        product_name="Mayonesa Country Best 1 Gl",
        product_type="Mayonesa",
        presentation_dimension=None,
        presentation_total_base=None,
        presentation_status="missing",
    ) == "1 gal"
