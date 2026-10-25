from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXPORTER_PATH = ROOT / "scripts" / "exportar_modelo_analitico.py"
DESCRIPTOR_PATH = ROOT / "scripts" / "generar_descriptores_publicacion_segura.py"

EXPORTER_SPEC = importlib.util.spec_from_file_location("exportar_modelo_analitico", EXPORTER_PATH)
assert EXPORTER_SPEC is not None and EXPORTER_SPEC.loader is not None
exporter = importlib.util.module_from_spec(EXPORTER_SPEC)
EXPORTER_SPEC.loader.exec_module(exporter)

DESCRIPTOR_SPEC = importlib.util.spec_from_file_location("generar_descriptores_publicacion_segura", DESCRIPTOR_PATH)
assert DESCRIPTOR_SPEC is not None and DESCRIPTOR_SPEC.loader is not None
descriptors = importlib.util.module_from_spec(DESCRIPTOR_SPEC)
DESCRIPTOR_SPEC.loader.exec_module(descriptors)


def build_db(path: Path) -> None:
    with sqlite3.connect(path) as con:
        con.executescript(
            """
            CREATE TABLE products (
                product_id INTEGER PRIMARY KEY,
                supermarket_id TEXT NOT NULL,
                name TEXT NOT NULL,
                brand TEXT,
                presentation TEXT,
                category TEXT,
                ean TEXT
            );
            CREATE TABLE price_history (
                product_id INTEGER NOT NULL,
                supermarket_id TEXT NOT NULL,
                location_id TEXT NOT NULL,
                current_price_minor INTEGER,
                availability TEXT NOT NULL,
                valid_to_utc TEXT
            );
            """
        )
        con.executemany(
            "INSERT INTO products VALUES (?,?,?,?,?,?,?)",
            [
                (1, "colonial", "Suavizante Downy Pureza 800 ml", "Downy", "800 ml", "Limpieza", "7590002040003"),
                (2, "walmart", "Downy Suavizante Pureza 800 ML", "Downy", "800 ml", "Limpieza", "07590002040003"),
                (3, "colonial", "Café Passion Especial 1 lb", "Passion", "1 lb", "Café", "7501031311309"),
                (4, "walmart", "Café Passion Jaguar 1 lb", "Passion", "1 lb", "Café", "07501031311309"),
            ],
        )
        con.executemany(
            "INSERT INTO price_history VALUES (?,?,?,?,?,NULL)",
            [
                (1, "colonial", "colonial_sps", 10000, "in_stock"),
                (2, "walmart", "walmart_sps", 12000, "in_stock"),
                (3, "colonial", "colonial_sps", 21000, "in_stock"),
                (4, "walmart", "walmart_sps", 29000, "in_stock"),
            ],
        )


def export_safe(path: Path, output: Path) -> None:
    backend = exporter.SQLiteBackend(path)
    try:
        exporter.export_dataset(
            backend,
            exporter.parse_scope(("colonial=colonial_sps", "walmart=walmart_sps")),
            output,
            require_products=True,
        )
    finally:
        backend.close()


def test_descriptors_only_expose_source_rows_from_safe_publication(tmp_path: Path) -> None:
    database = tmp_path / "input.sqlite"
    output = tmp_path / "publication"
    build_db(database)
    export_safe(database, output)

    backend = exporter.SQLiteBackend(database)
    try:
        document = descriptors.build_descriptors(backend, output / "publication.json")
    finally:
        backend.close()

    assert document["schema"] == "precios-sps-safe-source-descriptors/v1"
    assert document["comparison_policy"] == "fail_closed_strong_identity_and_commercial_consistency"
    assert document["source_backend"] == "sqlite"
    assert document["row_count"] == 2
    assert document["canonical_product_count"] == 1
    assert {row["source_name"] for row in document["rows"]} == {
        "Suavizante Downy Pureza 800 ml",
        "Downy Suavizante Pureza 800 ML",
    }
    serialized = json.dumps(document, ensure_ascii=False)
    assert "Passion" not in serialized
    assert "Jaguar" not in serialized
    assert "Especial" not in serialized
    assert str(database) not in serialized


def test_descriptors_fail_closed_if_persisted_gtin_no_longer_matches_publication(tmp_path: Path) -> None:
    database = tmp_path / "input.sqlite"
    output = tmp_path / "publication"
    build_db(database)
    export_safe(database, output)
    with sqlite3.connect(database) as con:
        con.execute("UPDATE products SET ean='4006381333931' WHERE product_id=2")

    backend = exporter.SQLiteBackend(database)
    try:
        with pytest.raises(descriptors.ExportError, match="descriptor_gtin_mismatch"):
            descriptors.build_descriptors(backend, output / "publication.json")
    finally:
        backend.close()
