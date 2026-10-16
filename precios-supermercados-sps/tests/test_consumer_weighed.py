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
    connection.execute("CREATE TABLE products(product_id INT, supermarket_id TEXT, ean TEXT, reference TEXT, presentation TEXT)")
    connection.executemany("INSERT INTO products VALUES (?,?,?,?,?)", [
        (1, "walmart", "2598600000004", None, None), (2, "walmart", "7401234567890", None, None),
        (3, "colonial", None, "2063710000000", None), (4, "colonial", "7424101113507", "7424101113507", None),
        (5, "la_colonia", "2020006000000", None, None), (6, "walmart", "02598600000004", None, None),
        (7, "paiz", "2045128000000", None, None), (8, "pricesmart", "2598600000004", None, None),
        (9, "comisariato_los_andes", None, "0001-000028436000000", "UN"),
        (10, "comisariato_los_andes", None, "0001-000742410110011", "LB"),
        (11, "comisariato_los_andes", None, "0001-000742411011006", "UN"),
    ])
    assert sorted(connection.execute(MODULE.WEIGHED_SQL).fetchall()) == [
        ("colonial", 3), ("comisariato_los_andes", 9), ("la_colonia", 5), ("paiz", 7), ("walmart", 1), ("walmart", 6),
    ]
    assert connection.execute(MODULE.SOURCE_POUND_SQL).fetchall() == [("comisariato_los_andes", 10)]


def test_los_andes_pound_unit_skips_the_type_check() -> None:
    MODULE.WEIGHED_BY_POUND.clear()
    MODULE.SOURCE_POUND.clear()
    MODULE.SOURCE_POUND.add("comisariato_los_andes:10")
    item = row(("comisariato_los_andes:10", "179.00"), product_type="Comida preparada y deli")
    item["product_name"] = "Alitas fritas en salsa barbacoa"
    MODULE._price_per_pound(item)
    assert item["presentation"] == "por libra" and item["offers"][0]["unit_price"]["per"] == "libra"
    MODULE.SOURCE_POUND.clear()


def test_closed_packages_and_unit_trays_are_not_per_pound() -> None:
    MODULE.WEIGHED_BY_POUND.clear()
    MODULE.WEIGHED_BY_POUND.update({"colonial:8", "comisariato_los_andes:9", "comisariato_los_andes:12"})
    for name, sp in (("MH ShortRib AsadoTira CoreanoPaq", "colonial:8"),
                     ("Torta p/hamburguesa  bandeja 4und", "comisariato_los_andes:9")):
        item = row((sp, "683.99"), product_type="Res")
        item["product_name"] = name
        MODULE._price_per_pound(item)
        assert item["presentation"] == "5 lb", name
    puyazo = row(("comisariato_los_andes:12", "303.50"), product_type="Res")
    puyazo["product_name"] = "Del corral puyazo paq x libra plu 210"
    MODULE._price_per_pound(puyazo)
    assert puyazo["presentation"] == "por libra"


def test_name_saying_per_unit_wins() -> None:
    MODULE.WEIGHED_BY_POUND.clear()
    MODULE.WEIGHED_BY_POUND.add("walmart:9")
    item = row(("walmart:9", "7.70"), product_type="Frutas")
    item["product_name"] = "Ayote Sazon Libra Ayote Sazon Amarillo Hortifruti - Precio Por Unidad"
    MODULE._price_per_pound(item)
    assert item["presentation"] == "5 lb" and item["offers"][0]["unit_price"]["per"] == "kg"


def test_name_ending_in_unit_wins() -> None:
    MODULE.WEIGHED_BY_POUND.clear()
    MODULE.WEIGHED_BY_POUND.add("colonial:7")
    item = row(("colonial:7", "44.99"), product_type="Frutas y verduras")
    item["product_name"] = "COLONIAL Encurtido Rojo und"
    MODULE._price_per_pound(item)
    assert item["presentation"] == "5 lb"
