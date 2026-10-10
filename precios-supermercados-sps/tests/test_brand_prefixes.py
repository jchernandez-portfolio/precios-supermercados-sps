"""Marcas curadas por prefijo del nombre (Colonial, 2026-10-09)."""
from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from precios_supermercados import consumer_catalog_display as display  # noqa: E402

SCRIPT = ROOT / "scripts" / "exportar_consumer_catalog.py"
SPEC = importlib.util.spec_from_file_location("exportar_consumer_catalog_brand_prefix_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_prefix_file_is_unique_and_curated() -> None:
    with display.BRAND_PREFIXES_PATH.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows and set(rows[0]) == {"supermarket_id", "prefix", "brand"}
    keys = [(row["supermarket_id"], display._fold(row["prefix"])) for row in rows]
    assert len(keys) == len(set(keys))
    produce = {"res", "cerdo", "manzana", "limon", "uva", "costilla", "lechuga", "kimchi", "kefir", "sm"}
    assert not {prefix for _, prefix in keys} & produce


def test_brand_from_prefix() -> None:
    assert display.brand_from_prefix("colonial", "LA HOGAZA Pan Masa Madre 48H") == "La Hogaza"
    assert display.brand_from_prefix("colonial", "SCOTCHB EsponjaCocina LimpProf") == "Scotch-Brite"
    assert display.brand_from_prefix("colonial", "HQD SUPER Blue Raz-600hits") == "HQD"
    assert display.brand_from_prefix("colonial", "LEA & PERRIN WorcestSauc 296ml") == "Lea & Perrins"
    # Sólo al inicio y sólo palabra completa.
    assert display.brand_from_prefix("colonial", "Pan LA HOGAZA") is None
    assert display.brand_from_prefix("colonial", "FOMENTO Algo") is None
    # Sólo el súper para el que se curó.
    assert display.brand_from_prefix("walmart", "LA HOGAZA Pan Masa Madre") is None
    assert display.brand_from_prefix("colonial", None) is None


def offer(supermarket: str, name: str, brand: str | None):
    return MODULE.VisibleOffer(
        source_product_id=f"{supermarket}:1", supermarket_id=supermarket, location_id=f"{supermarket}_sps",
        product_name=name, brand=brand, presentation="350 g", current_price_minor=5000,
        reported_regular_price_minor=None, is_promotion=False, availability="in_stock",
        observed_at="2026-10-09T12:00:00Z", canonical_product_id=None, category="Alimentos",
        product_type=None, presentation_dimension="mass_g", presentation_total_base="350",
        presentation_status="confirmed", comparison_status="unmapped", source_category="Abarrotes",
    )


FRESH = {(sm, f"{sm}_sps"): "FRESH" for sm in ("la_colonia", "colonial", "walmart", "pricesmart", "comisariato_los_andes")}


def test_build_rows_fills_missing_brand_from_curated_prefix_only() -> None:
    [row] = MODULE.build_rows((offer("colonial", "LA HOGAZA Pan Masa Madre 48H", None),), FRESH)
    assert row["brand"] == "La Hogaza"
    [kept] = MODULE.build_rows((offer("colonial", "LA HOGAZA Pan Masa Madre 48H", "Otra Marca"),), FRESH)
    assert kept["brand"] == "Otra Marca"
    [unknown] = MODULE.build_rows((offer("colonial", "MARCANUEVA Pan 350g", None),), FRESH)
    assert unknown["brand"] is None
