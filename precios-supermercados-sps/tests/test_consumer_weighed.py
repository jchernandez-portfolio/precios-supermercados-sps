"""Walmart: productos pesados en tienda (código "2…") se publican por libra."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "exportar_consumer_catalog.py"
SPEC = importlib.util.spec_from_file_location("exportar_consumer_catalog_weighed", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def row(*offers, product_type="Pollo y pavo"):
    return {"category": "Alimentos", "product_type": product_type, "presentation": "5 lb",
            "offers": [{"source_product_id": sp, "current_price": price, "unit_price": {"amount": "18.52", "per": "kg"}}
                       for sp, price in offers]}


def test_weighed_walmart_offer_is_priced_per_pound() -> None:
    MODULE.WEIGHED_BY_POUND.clear()
    MODULE.WEIGHED_BY_POUND.add("walmart:66784")
    item = row(("walmart:66784", "42.00"))
    MODULE._price_per_pound(item)
    assert item["presentation"] == "por libra"
    assert item["offers"][0]["unit_price"] == {"amount": "42.00", "per": "libra"}


def test_other_offers_types_and_codes_are_untouched() -> None:
    MODULE.WEIGHED_BY_POUND.clear()
    MODULE.WEIGHED_BY_POUND.add("walmart:66784")
    mixed = row(("walmart:66784", "42.00"), ("colonial:5", "189.00"))
    MODULE._price_per_pound(mixed)
    assert mixed["presentation"] == "5 lb"
    assert mixed["offers"][1]["unit_price"] == {"amount": "18.52", "per": "kg"}
    assert mixed["offers"][0]["unit_price"]["per"] == "libra"
    empanada = row(("walmart:66784", "148.00"), product_type="Comida preparada y deli")
    MODULE._price_per_pound(empanada)
    assert empanada["presentation"] == "5 lb"
    other = row(("walmart:1", "42.00"))
    MODULE._price_per_pound(other)
    assert other["presentation"] == "5 lb"


def test_weighed_query_reads_walmart_ean_and_colonial_sku() -> None:
    import sqlite3

    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE products(product_id INT, supermarket_id TEXT, ean TEXT, reference TEXT)")
    connection.executemany("INSERT INTO products VALUES (?,?,?,?)", [
        (1, "walmart", "2598600000004", None), (2, "walmart", "7401234567890", None),
        (3, "colonial", None, "2063710000000"), (4, "colonial", "7424101113507", "7424101113507"),
        (5, "la_colonia", "2598600000004", None), (6, "walmart", "02598600000004", None),
    ])
    assert sorted(connection.execute(MODULE.WEIGHED_SQL).fetchall()) == [("colonial", 3), ("walmart", 1), ("walmart", 6)]


def test_name_saying_per_unit_wins() -> None:
    MODULE.WEIGHED_BY_POUND.clear()
    MODULE.WEIGHED_BY_POUND.add("walmart:9")
    item = row(("walmart:9", "7.70"), product_type="Frutas")
    item["product_name"] = "Ayote Sazon Libra Ayote Sazon Amarillo Hortifruti - Precio Por Unidad"
    MODULE._price_per_pound(item)
    assert item["presentation"] == "5 lb" and item["offers"][0]["unit_price"]["per"] == "kg"
