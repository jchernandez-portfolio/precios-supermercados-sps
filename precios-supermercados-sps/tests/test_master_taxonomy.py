"""Árbol maestro de categorías v1 y tabla de equivalencias (aprobado 2026-10-08)."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import exportar_consumer_catalog_core as core  # noqa: E402
from precios_supermercados import master_taxonomy as mt  # noqa: E402
from precios_supermercados import product_identity_v2  # noqa: E402

TAX = mt.load()


def offer(supermarket_id, source_category=None, product_type=None, category=None, n=1):
    return core.VisibleOffer(
        source_product_id=f"{supermarket_id}:{n}", supermarket_id=supermarket_id, location_id=f"{supermarket_id}_sps",
        product_name="Producto", brand=None, presentation=None, current_price_minor=1000,
        reported_regular_price_minor=None, is_promotion=False, availability="in_stock",
        observed_at="2026-10-08T10:00:00Z", canonical_product_id=None, category=category,
        product_type=product_type, presentation_dimension=None, presentation_total_base=None,
        presentation_status="missing", comparison_status="single_source", source_category=source_category,
    )


def test_tree_shape_and_codes():
    tree = json.loads(mt.TREE_PATH.read_text(encoding="utf-8"))
    assert tree["schema"] == mt.TREE_SCHEMA and tree["levels"] == ["department", "category", "subcategory", "product_type"]
    assert len(TAX.departments) == 14 and len(TAX.paths) == 180
    for d in tree["departments"]:
        assert d["gpc_reference"]
        for c in d["categories"]:
            assert c["code"].startswith(d["code"] + ".")
            for s in c["subcategories"]:
                assert s["code"].startswith(c["code"] + ".")


def test_every_identity_product_type_has_exactly_one_node():
    known = set(product_identity_v2._KNOWN_PRODUCT_TYPES)
    assert known <= set(TAX.type_nodes), sorted(known - set(TAX.type_nodes))


def test_crosswalk_is_complete_valid_and_sorted():
    with mt.CROSSWALK_PATH.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == len(TAX.crosswalk) == 1311
    assert {r["supermarket_id"] for r in rows} == {"walmart", "paiz", "colonial", "la_colonia", "comisariato_los_andes", "pricesmart"}
    assert rows == sorted(rows, key=lambda r: (r["supermarket_id"], r["source_category"]))
    by_level = {r["level"] for r in rows}
    assert by_level <= mt.CROSSWALK_LEVELS


@pytest.mark.parametrize(("supermarket", "category", "expected"), [
    ("comisariato_los_andes", "BOTES Y ENLATADOS", ("Alimentos", "Abarrotes", "Enlatados y conservas")),
    ("comisariato_los_andes", "LICORES", ("Bebidas y tabaco", "Bebidas alcohólicas", "Licores")),
    ("walmart", "/Ropa y Zapatería/Hombre/Ropa para Hombre/", ("Ropa, calzado y accesorios", "Ropa", "Hombre")),
    ("walmart", "/Artículos para el hogar/Artículos de temporada/Colgantes Navidad/", ("Hogar y cocina", "Decoración", "Decoración de temporada")),
    ("paiz", "/Limpieza/Detergente/Detergente en polvo/", ("Limpieza", "Lavandería", "Detergentes")),
    ("la_colonia", "Supermercado > Abarrotes > Snacks", ("Alimentos", "Snacks y dulces", None)),
    ("colonial", "Abarrotes", ("Alimentos", "Abarrotes", None)),
    ("pricesmart", "Moda y accesorios", ("Ropa, calzado y accesorios", None, None)),
])
def test_crosswalk_examples(supermarket, category, expected):
    entry = TAX.crosswalk[(supermarket, mt.fold_key(category))]
    assert (entry.node.department, entry.node.category, entry.node.subcategory) == expected


def test_crosswalk_special_levels():
    assert TAX.crosswalk[("pricesmart", mt.fold_key("Tarjetas de Regalo"))].level == "excluded"
    assert TAX.crosswalk[("pricesmart", mt.fold_key("Hogar"))].level == "by_name"


def test_matching_ignores_case_accents_and_spacing():
    a = mt.assign_offer("comisariato_los_andes", "  botes y  enlatádos ", None)
    assert a.node == mt.Node("Alimentos", "Abarrotes", "Enlatados y conservas")
    assert a.source == "crosswalk"


def test_name_type_refines_coarse_crosswalk_inside_its_branch():
    a = mt.assign_offer("colonial", "Abarrotes", "Pasta")
    assert a.node == mt.Node("Alimentos", "Abarrotes", "Pastas", "Pasta") and a.source == "crosswalk+type"


def test_crosswalk_wins_when_name_type_contradicts_it():
    a = mt.assign_offer("walmart", "/Ropa y Zapatería/Hombre/Ropa para Hombre/", "Shampoo")
    assert a.node == mt.Node("Ropa, calzado y accesorios", "Ropa", "Hombre") and a.source == "crosswalk"


def test_mixed_source_category_uses_name_or_stays_unassigned():
    assert mt.assign_offer("pricesmart", "Hogar", "Detergente").node == mt.Node("Limpieza", "Lavandería", "Detergentes", "Detergente")
    assert mt.assign_offer("pricesmart", "Hogar", None).node is None


def test_unknown_source_category_falls_back_to_type_then_legacy_then_nothing():
    assert mt.assign_offer("walmart", "/Nueva/Categoria/", "Arroz").source == "type"
    legacy = mt.assign_offer("walmart", "/Nueva/Categoria/", None, "Bebidas")
    assert legacy.node == mt.Node("Bebidas y tabaco") and legacy.source == "legacy"
    assert mt.assign_offer("walmart", "/Nueva/Categoria/", None, None).source == "unassigned"


def test_excluded_products_leave_the_catalog():
    assert core._public_taxonomy([offer("pricesmart", "Tarjetas de Regalo")]) is None


def test_comparable_group_shares_the_most_specific_node():
    group = [offer("colonial", "Abarrotes", None, n=1), offer("walmart", "/Abarrotes/Pastas/Spaghetti/", "Pasta", n=2)]
    assert core._public_taxonomy(group) == ("Alimentos", "Pasta")
    group = [offer("colonial", "Cuidado Personal", None, n=1), offer("la_colonia", "Supermercado > Belleza y Cuidado Personal > Cuidado del Cabello", None, n=2)]
    assert core._public_taxonomy(group) == ("Cuidado personal y belleza", "Cuidado del cabello")


def test_public_second_level_is_type_then_subcategory_then_category():
    assert mt.public_fields(mt.Node("Alimentos", "Abarrotes", "Pastas", "Pasta")) == ("Alimentos", "Pasta")
    assert mt.public_fields(mt.Node("Juguetes", "Juguetes", "Muñecas y peluches")) == ("Juguetes", "Muñecas y peluches")
    assert mt.public_fields(mt.Node("Alimentos", "Snacks y dulces")) == ("Alimentos", "Snacks y dulces")
    assert mt.public_fields(mt.Node("Alimentos")) == ("Alimentos", None)
    assert mt.public_fields(None) == (None, None)


def test_new_source_categories_are_reported():
    pairs = [("walmart", "/Abarrotes/Pastas/Spaghetti/"), ("walmart", "/Categoria Nueva/X/"), ("colonial", None)]
    assert mt.unmapped_source_categories(pairs) == [("walmart", "/Categoria Nueva/X/")]
