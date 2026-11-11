"""Auditoría de identidad de producto: sólo lectura, cifras por ciudad y muestra."""
from __future__ import annotations

import csv
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("auditar_identidad_productos", ROOT / "scripts" / "auditar_identidad_productos.py")
audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)

from precios_supermercados.product_homologation_persistence import SCHEMA_SQL  # noqa: E402
from precios_supermercados.product_master import INDEX_SQL, TABLE_SQL  # noqa: E402

BASE = "https://example.test/v3/"
GTIN_CORN = "0012656001065"  # Maíz El Migo (Walmart lo publica con 148 g)


class Backend:
    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(path)
        self.sql: list[str] = []

    def query(self, sql, args=()):
        self.sql.append(sql)
        return [tuple(row) for row in self.connection.execute(sql, tuple(args)).fetchall()]


def database(path: Path) -> Backend:
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE products (product_id INTEGER PRIMARY KEY, supermarket_id TEXT NOT NULL, name TEXT NOT NULL,
            brand TEXT, presentation TEXT, category TEXT, ean TEXT, reference TEXT,
            UNIQUE (product_id, supermarket_id));
    """)
    con.executescript(SCHEMA_SQL)
    for sql in TABLE_SQL.values():
        con.execute(sql)
    for _, sql in INDEX_SQL:
        con.execute(sql)
    con.executemany("INSERT INTO products VALUES (?,?,?,?,?,?,?,?)", [
        (1, "walmart", "Maíz Dulce En Granos El Migo - 148 g", "El Migo", None, "Enlatados", GTIN_CORN, "286"),
        (2, "colonial", "ELMIGO Maiz Dulce Enlatado 8.5oz", None, None, "Enlatados", "012656001065", "012656001065"),
        (3, "la_colonia", "Margarina Mirasol 450 Gr", "Mirasol", "450 Gr", "Lácteos", None, "M1"),
        (4, "comisariato_los_andes", "Cafe Maya 400 g", None, None, "Café", "7421000000013", "0001-000000001"),
        (5, "pricesmart", "Café Member's Selection 1.36 kg", None, None, "Café", None, "P5"),
        (6, "walmart", "Producto viejo sin oferta", None, None, None, "7421000000021", "999"),
    ])
    profile = ("INSERT INTO product_homologation_profiles (product_id, supermarket_id, normalized_name, normalized_brand, "
               "canonical_gtin, canonical_product_id, presentation_status, comparison_status, conflict_reasons_json, "
               "normalization_version, profile_hash, updated_at_utc, source_brand_role, brand_resolution_source, "
               "normalized_quantity, normalized_unit) VALUES (?,?,?,?,?,?,?,?,'[]','v','" + "0" * 64 + "','t',?,?,?,?)")
    con.executemany(profile, [
        (1, "walmart", "maiz", "el migo", "00012656001065", "prod_gtin_00012656001065", "name_only", "ready",
         "unknown", "source", "148", "g"),
        (2, "colonial", "maiz", "el migo", "00012656001065", "prod_gtin_00012656001065", "name_only", "ready",
         "unknown", "name_known_brand", "240.97", "g"),
        (3, "la_colonia", "margarina", "mirasol", None, None, "conflict", "unmapped", "unknown", "source", None, None),
        (4, "comisariato_los_andes", "cafe", None, "07421000000013", "prod_gtin_07421000000013", "missing",
         "single_source", "unknown", "missing", None, None),
    ])
    mp = "mp_" + "a" * 20
    con.execute("INSERT INTO master_products (master_product_id, primary_gtin, origin_method, display_name, "
                "attribute_provenance_json, status, record_hash, builder_version, created_at_utc, updated_at_utc, version) "
                "VALUES (?, '00012656001065', 'gtin', 'Maíz El Migo', '{}', 'active', ?, 'v', 't', 't', 1)", (mp, "0" * 64))
    con.execute("INSERT INTO master_product_links (product_id, supermarket_id, master_product_id, link_method, "
                "evidence_json, decided_by, decided_at_utc, status, link_hash) "
                "VALUES (1, 'walmart', ?, 'gtin_exact', '{}', 'system', 't', 'active', ?)", (mp, "0" * 64))
    con.commit()
    con.close()
    return Backend(path)


def catalog() -> dict[str, object]:
    def offer(sp):
        return {"source_product_id": sp, "current_price": "1.00"}
    files = {
        BASE + "cities.json": {"cities": [{"slug": "san-pedro-sula", "manifest": "manifest.json"}]},
        BASE + "manifest.json": {"files": [{"path": "facets.json"}, {"path": "catalog/a/part-001.json"}]},
        BASE + "catalog/a/part-001.json": {"rows": [
            {"row_id": "source-aa11", "product_name": "Maíz El Migo 148 g", "offers": [offer("walmart:1")]},
            {"row_id": "source-bb22", "product_name": "ELMIGO Maiz 8.5oz", "offers": [offer("colonial:2")]},
            {"row_id": "source-cc33", "product_name": "Margarina Mirasol", "offers": [offer("la_colonia:3")]},
            {"row_id": "canonical-dd44", "product_name": "Cafe Maya",
             "offers": [offer("comisariato_los_andes:4"), offer("pricesmart:5@pricesmart_sps")]},
        ]},
    }
    return files


def test_audit_counts_codes_attributes_master_and_split_gtin(tmp_path: Path) -> None:
    backend = database(tmp_path / "db.sqlite")
    files = catalog()
    photos = {"items": {"aa11": {"h": "0123456789"}, "bb22": {"none": 1}}}
    summary, schema_doc, rows = audit.audit(backend, files.__getitem__, BASE, photos)

    city = summary["ciudades"]["san-pedro-sula"]
    assert city["tarjetas"] == 4 and city["tarjetas_con_2_o_mas_supermercados"] == 1
    totals = city["productos_fuente"]
    assert totals["productos"] == 5 and totals["con_codigo"] == 3 and totals["gtin_valido"] == 3
    assert totals["origen:sku_colonial_gs1"] == 1 and totals["origen:codigo_los_andes_reconstruido"] == 1
    assert totals["con_contenido_neto"] == 2 and totals["con_producto_maestro"] == 1
    # El mismo GTIN en dos tarjetas: el duplicado que el maestro debe unir.
    assert city["gtin_repartidos_en_varias_tarjetas"] == 1
    assert city["tarjetas_afectadas_por_gtin_repartido"] == 2
    assert summary["ejemplos_gtin_repartidos"]["san-pedro-sula"] == [
        {"gtin": "00012656001065", "tarjetas": ["aa11", "bb22"]}]
    whole = summary["base_completa"]
    assert whole["productos_por_supermercado"]["walmart"] == {"productos": 2, "con_ean": 2}
    assert whole["producto_maestro"]["maestros"] == {"gtin/active": 1}
    assert whole["producto_maestro"]["vinculos"] == {"gtin_exact/active": 1}
    assert {"products", "product_homologation_profiles", "master_products"} <= set(schema_doc["tablas"])
    assert schema_doc["tablas"]["products"]["filas"] == 6

    assert len(rows) == 5  # todos los productos del catálogo (pide 100)
    corn = next(row for row in rows if row["product_id"] == 1)
    assert corn["motivo_muestra"] == "maiz" and corn["peso_neto"] == "148" and corn["unidad"] == "g"
    assert corn["codigo_barras"] == GTIN_CORN and corn["gtin_valido"] == "00012656001065"
    assert corn["url_imagen"].endswith("/800/aa/aa11-0123456789.webp")
    assert corn["producto_maestro"].startswith("mp_") and corn["metodo_maestro"] == "gtin_exact"
    assert next(row for row in rows if row["product_id"] == 2)["url_imagen"] is None
    assert {row["motivo_muestra"] for row in rows} >= {"maiz", "margarina", "cafe"}
    assert all(sql.lstrip().upper().startswith(("SELECT", "PRAGMA TABLE_INFO")) for sql in backend.sql)


def test_writes_are_refused(tmp_path: Path) -> None:
    reader = audit.ReadOnly(database(tmp_path / "db.sqlite"))
    for sql in ("DELETE FROM products", "UPDATE products SET name='x'", "SELECT 1; DROP TABLE products",
                "PRAGMA writable_schema=1", "INSERT INTO products VALUES (1)"):
        with pytest.raises(audit.AuditError, match="solo_lectura"):
            reader.query(sql)
    assert reader.query("SELECT COUNT(*) FROM products") == [(6,)]


def test_outputs_are_written(tmp_path: Path) -> None:
    backend = database(tmp_path / "db.sqlite")
    summary, schema_doc, rows = audit.audit(backend, catalog().__getitem__, BASE, None)
    audit.write_outputs(tmp_path / "out", summary, schema_doc, rows)
    with (tmp_path / "out" / "muestra-productos.csv").open(encoding="utf-8") as handle:
        read = list(csv.DictReader(handle))
    assert list(read[0]) == list(audit.CSV_COLUMNS) and len(read) == 5
    assert json.loads((tmp_path / "out" / "resumen.json").read_text(encoding="utf-8"))["productos_fuente_distintos_en_catalogo"] == 5
    assert "tablas" in json.loads((tmp_path / "out" / "esquema.json").read_text(encoding="utf-8"))
