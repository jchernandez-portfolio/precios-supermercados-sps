"""Base SQLite realista para las pruebas del producto maestro.

Esquema productivo (``create_schema`` + perfiles derivados), seis cadenas y
once contextos, GTIN válidos (check digit GS1) compartidos entre cadenas,
GTIN derivados de SKU (Colonial/Comisariato), single_source, colisiones y
productos sin GTIN (PriceSmart, Comisariato). Los perfiles se calculan con el
motor real (``build_homologation_rows``) igual que el refresco diario.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from generar_mvp_sqlite_la_colonia import HISTORY_INDEX_SQL, create_schema  # noqa: E402
from precios_supermercados.product_homologation_persistence import (  # noqa: E402
    SCHEMA_SQL as PROFILE_SCHEMA_SQL,
    build_homologation_rows,
    persist_sqlite_rows,
    records_from_product_rows,
)

CONTEXTS = (
    ("la_colonia", "la_colonia_sps", "San Pedro Sula"),
    ("la_colonia", "la_colonia_tgu", "Tegucigalpa"),
    ("colonial", "colonial_sps", "San Pedro Sula"),
    ("walmart", "walmart_sps", "San Pedro Sula"),
    ("walmart", "walmart_tgu_ffaa", "Tegucigalpa"),
    ("walmart", "walmart_tgu_el_sauce", "Tegucigalpa"),
    ("pricesmart", "pricesmart_sps", "San Pedro Sula"),
    ("pricesmart", "pricesmart_tgu", "Tegucigalpa"),
    ("comisariato_los_andes", "comisariato_los_andes_sps", "San Pedro Sula"),
    ("paiz", "paiz_tgu_multiplaza", "Tegucigalpa Multiplaza"),
    ("paiz", "paiz_tgu_proceres", "Tegucigalpa Proceres"),
)
SUPERMARKETS = tuple(dict.fromkeys(context[0] for context in CONTEXTS))
LOCATIONS_BY_SUPERMARKET: dict[str, tuple[str, ...]] = {}
for _supermarket, _location, _ in CONTEXTS:
    LOCATIONS_BY_SUPERMARKET[_supermarket] = (*LOCATIONS_BY_SUPERMARKET.get(_supermarket, ()), _location)


def gtin13(base12: str) -> str:
    digits = [int(char) for char in base12]
    total = sum(digit * (3 if index % 2 else 1) for index, digit in enumerate(digits))
    return base12 + str((10 - total % 10) % 10)


BIMBO = "7441029556773"
PEPSI = "7421600300247"
DORAO = gtin13("742100000101")
SULA = gtin13("742100000202")
MAYA = gtin13("742100000303")
DUCAL = gtin13("742100000404")
XEDEX_1 = gtin13("742100000505")
XEDEX_2 = gtin13("742100000606")
LEYDE = gtin13("742100000707")
OREO = gtin13("742100000808")

# (supermarket, name, brand, presentation, category, ean, price)
PRODUCTS: tuple[tuple[str, str, str | None, str | None, str | None, str | None, int], ...] = (
    # GTIN compartido por tres cadenas (Colonial con GTIN derivado de SKU).
    ("la_colonia", "Pan Blanco Bimbo 720 g", "Bimbo", "720 g", "Panadería", BIMBO, 6500),
    ("walmart", "Pan Bimbo Blanco Grande - 720 g", "Bimbo", "720 g", "/Panadería/Pan de caja/", BIMBO, 6450),
    ("colonial", "BIMBO Pan Blanco 720g", "Bimbo", None, "Panaderia", BIMBO, 6600),
    # Walmart + Paiz + La Colonia.
    ("walmart", "Refresco Pepsi Botella - 2 L", "Pepsi", "2 L", "/Bebidas/Refrescos/", PEPSI, 4200),
    ("paiz", "Refresco Pepsi Botella - 2 L", "Pepsi", "2 L", "/Bebidas/Refrescos/", PEPSI, 4300),
    ("la_colonia", "Refresco Pepsi 2 L", "Pepsi", "2 L", "Bebidas", PEPSI, 4150),
    # single_source Walmart y candidato PriceSmart sin GTIN.
    ("walmart", "Café Molido Dorao Tradicional - 454 g", "Dorao", "454 g", "/Despensa/Café/", DORAO, 11000),
    ("pricesmart", "Café Dorao Molido Tradicional 454 g", "Dorao", None, "Alimentos", None, 10500),
    # Ready Walmart + La Colonia; Comisariato sin GTIN (candidato).
    ("walmart", "Leche Entera Sula - 946 ml", "Sula", "946 ml", "/Lácteos/Leche/", SULA, 3200),
    ("la_colonia", "Leche Sula Entera 946 Ml", "Sula", "946 ml", "Lácteos", SULA, 3150),
    ("comisariato_los_andes", "Leche sula entera 946 ml", None, None, None, None, 3100),
    # Ready + miembro excluido por variante de un solo lado.
    ("walmart", "Café Maya Original - 400 g", "Maya", "400 g", "/Despensa/Café/", MAYA, 9900),
    ("paiz", "Café Maya Original - 400 g", "Maya", "400 g", "/Despensa/Café/", MAYA, 9950),
    ("la_colonia", "Café Maya Descafeinado 400 g", "Maya", "400 g", "Café", MAYA, 10100),
    # single_source PriceSmart? no: Ducal sólo en Walmart (single_source) y Paiz con otro GTIN.
    ("walmart", "Frijol Rojo Ducal Volteado - 400 g", "Ducal", "400 g", "/Despensa/Frijoles/", DUCAL, 3000),
    ("paiz", "Frijol Rojo Ducal Volteado - 400 g", "Ducal", "400 g", "/Despensa/Frijoles/", gtin13("742100000999"), 3050),
    # Dos productos del mismo supermercado con el mismo GTIN (colisión single_source).
    ("walmart", "Detergente Xedex Limón - 800 g", "Xedex", "800 g", "/Limpieza/", XEDEX_1, 5500),
    ("walmart", "Detergente Xedex Limon Bolsa - 800 g", "Xedex", "800 g", "/Limpieza/", XEDEX_1, 5600),
    # Ready con conflicto de presentación (1 L vs 2 L): todo el grupo en revisión.
    ("walmart", "Leche Leyde Entera - 1 L", "Leyde", "1 L", "/Lácteos/", LEYDE, 3300),
    ("la_colonia", "Leche Leyde Entera 2 L", "Leyde", "2 L", "Lácteos", LEYDE, 6200),
    # Comisariato con GTIN reconstruido (sku-derived) compartido con Walmart.
    ("comisariato_los_andes", "Galleta oreo original 432 g", "Marca COMANDES", None, None, OREO, 7400),
    ("walmart", "Galleta Oreo Original - 432 g", "Oreo", "432 g", "/Despensa/Galletas/", OREO, 7500),
    # Sin GTIN en ninguna cadena.
    ("pricesmart", "Member's Selection Agua Purificada 24 x 500 ml", "Member's Selection", None, "Bebidas", None, 18900),
    ("colonial", "XEDEX Detergente Floral 800g", "Xedex", None, "Limpieza", None, 5400),
)


def insert_catalog(con: sqlite3.Connection, products=PRODUCTS) -> dict[int, tuple[Any, ...]]:  # type: ignore[no-untyped-def]
    for supermarket in SUPERMARKETS:
        con.execute("INSERT INTO supermarkets VALUES(?,?,'HN')", (supermarket, supermarket.title()))
    for supermarket, location, city in CONTEXTS:
        con.execute("INSERT INTO locations VALUES(?,?,?,'HN')", (location, supermarket, city))
        con.execute(
            "INSERT INTO scrape_runs VALUES(?,?,?,?,'success',1,1,NULL,NULL,NULL)",
            (f"run-{location}", supermarket, location, "2026-09-20T10:00:00Z"),
        )
    by_id: dict[int, tuple[Any, ...]] = {}
    for product_id, (supermarket, name, brand, presentation, category, ean, price) in enumerate(products, start=1):
        reference = ean if supermarket == "colonial" else str(product_id)
        con.execute(
            "INSERT INTO products VALUES(?,?,'item_id',?,?,?,?,?,?,?,?,?)",
            (
                product_id, supermarket, str(product_id), str(product_id), str(product_id),
                reference, ean, name, brand, presentation, category,
            ),
        )
        by_id[product_id] = (supermarket, name, brand, presentation, category, ean, price)
        for index, location in enumerate(LOCATIONS_BY_SUPERMARKET[supermarket]):
            con.execute(
                "INSERT INTO price_history VALUES(?,?,?,?,?,?,?,'HNL',?,?,?)",
                (
                    product_id, supermarket, location, price + index * 10, price + 500, 0,
                    "in_stock", "2026-09-10T10:00:00Z", "2026-09-15T10:00:00Z", f"run-{location}",
                ),
            )
            con.execute(
                "INSERT INTO price_history VALUES(?,?,?,?,?,?,?,'HNL',?,?,?)",
                (
                    product_id, supermarket, location, price + index * 10 - 50, price + 500, 1,
                    "in_stock", "2026-09-15T10:00:00Z", None, f"run-{location}",
                ),
            )
    return by_id


def product_rows(con: sqlite3.Connection) -> tuple[tuple[object, ...], ...]:
    return tuple(
        tuple(row)
        for row in con.execute(
            "SELECT product_id,supermarket_id,name,brand,presentation,category,ean FROM products ORDER BY product_id"
        )
    )


def derive_profiles(con: sqlite3.Connection, *, updated_at_utc: str = "2026-09-20T11:00:00Z"):  # type: ignore[no-untyped-def]
    records = records_from_product_rows(product_rows(con))
    rows = build_homologation_rows(records, updated_at_utc=updated_at_utc)
    return records, rows


def build_database(path: Path, products=PRODUCTS) -> tuple[Any, Any]:  # type: ignore[no-untyped-def]
    con = sqlite3.connect(path)
    try:
        create_schema(con)
        con.executescript(PROFILE_SCHEMA_SQL)
        con.execute(HISTORY_INDEX_SQL)
        insert_catalog(con, products)
        records, rows = derive_profiles(con)
        persist_sqlite_rows(con, rows)
        con.commit()
    finally:
        con.close()
    return records, rows


# --- Fake Hrana: protocolo Turso contra SQLite, contando filas devueltas ------------


def _encode(value: object) -> dict[str, object]:
    if value is None:
        return {"type": "null"}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": value}
    return {"type": "text", "value": str(value)}


def _decode(arg: dict[str, Any]) -> object:
    if arg["type"] == "null":
        return None
    if arg["type"] == "integer":
        return int(arg["value"])
    if arg["type"] == "float":
        return float(arg["value"])
    return arg["value"]


class FakeTurso:
    """Ejecuta pipelines/lotes Hrana en SQLite y cuenta filas leídas (VM) y devueltas."""

    def __init__(self, path: Path) -> None:
        self.con = sqlite3.connect(path, isolation_level=None)
        self.sql: list[str] = []
        self.rows_returned = 0

    def pipeline(self, _url: str, _token: str, requests: list[dict[str, Any]]) -> dict[str, Any]:
        results = []
        for request in requests:
            if request["type"] == "close":
                results.append({"type": "ok", "response": {"type": "close"}})
                continue
            stmt = request["stmt"]
            self.sql.append(" ".join(stmt["sql"].split()))
            rows = self.con.execute(stmt["sql"], [_decode(arg) for arg in stmt["args"]]).fetchall()
            self.rows_returned += len(rows)
            results.append(
                {
                    "type": "ok",
                    "response": {"type": "execute", "result": {"rows": [[_encode(v) for v in row] for row in rows]}},
                }
            )
        return {"results": results}

    def run_batch(self, _url: str, _token: str, steps):  # type: ignore[no-untyped-def]
        out = []
        for _, sql, args in steps:
            self.sql.append(" ".join(sql.split()))
            cursor = self.con.execute(sql, args)
            out.append({"affected_row_count": cursor.rowcount})
        return out
