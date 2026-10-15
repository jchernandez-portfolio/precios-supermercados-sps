"""Onzas de peso en el catálogo público: "16 oz" de huevo líquido → 454 g con precio por kg."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "exportar_consumer_catalog.py"
SPEC = importlib.util.spec_from_file_location("exportar_consumer_catalog_ounce", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

DEPARTMENT, PANTRY, DAIRY = "Alimentos", "Abarrotes", "Lácteos y huevos"
FRESH = {(sm, f"{sm}_sps"): "FRESH" for sm in ("la_colonia", "colonial", "walmart", "pricesmart", "comisariato_los_andes")}


def offer(name, *, dimension="ounce", total="16", source_category=None, category=None, product_type=None,
          supermarket="colonial", presentation="16 oz", price=15999):
    return MODULE.VisibleOffer(
        source_product_id=f"{supermarket}:1", supermarket_id=supermarket, location_id=f"{supermarket}_sps",
        product_name=name, brand=None, presentation=presentation, current_price_minor=price,
        reported_regular_price_minor=None, is_promotion=False, availability="in_stock",
        observed_at="2026-10-15T12:00:00Z", canonical_product_id=None, category=category,
        product_type=product_type, presentation_dimension=dimension, presentation_total_base=total,
        presentation_status="confirmed", comparison_status="single_source", source_category=source_category,
    )


def test_egg_beaters_ounces_become_grams_with_price_per_mass() -> None:
    converted = MODULE.ounce_as_mass(offer("EGG Beaters Wht 16 oz"))
    assert converted.presentation_dimension == "mass_g"
    assert converted.presentation_total_base == "453.59237"
    [row] = MODULE.build_rows((offer("EGG Beaters Wht 16 oz"),), FRESH)
    assert row["presentation"] in {"453.59 g", "454 g"}
    assert row["product_type"] == "Huevo"
    unit = row["offers"][0]["unit_price"]
    assert unit is not None and unit["per"] in {"kg", "100 g"}


def test_mass_subcategory_from_public_assignment_converts(monkeypatch) -> None:
    taxonomy = MODULE.master_taxonomy
    pastas = taxonomy.Node(DEPARTMENT, PANTRY, "Pastas")
    assert taxonomy.node_attribute(pastas, "ounce") == "mass"
    monkeypatch.setattr(taxonomy, "assign_offer", lambda *a, **k: taxonomy.Assignment(pastas, "test", None))
    converted = MODULE.ounce_as_mass(offer("Spaghetti 12 oz", total="12", presentation="12 oz"))
    assert converted.presentation_dimension == "mass_g" and converted.presentation_total_base == "340.19428"
    milk = taxonomy.Node(DEPARTMENT, DAIRY, "Leche")
    monkeypatch.setattr(taxonomy, "assign_offer", lambda *a, **k: taxonomy.Assignment(milk, "test", None))
    unchanged = offer("Leche evaporada 12 oz", total="12")
    assert MODULE.ounce_as_mass(unchanged) is unchanged


def test_liquid_unknown_and_aerosol_ounces_stay_as_ounces() -> None:
    for item in (
        offer("Salsa picante 5 oz", total="5"),
        offer("Desodorante spray 4 oz", total="4"),
        offer("Leche 1 L", dimension="volume_ml", total="1000", presentation="1 L"),
        offer("Producto sin tamaño", total="0"),
    ):
        assert MODULE.ounce_as_mass(item) is item
