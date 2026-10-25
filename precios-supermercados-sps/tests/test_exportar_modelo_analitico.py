from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "exportar_modelo_analitico.py"
SPEC = importlib.util.spec_from_file_location("exportar_modelo_analitico", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


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


def test_exporter_writes_only_safe_comparisons_and_never_serializes_database_path(tmp_path: Path) -> None:
    database = tmp_path / "input.sqlite"
    output = tmp_path / "publication"
    build_db(database)

    backend = MODULE.SQLiteBackend(database)
    try:
        manifest = MODULE.export_dataset(
            backend,
            MODULE.parse_scope(("colonial=colonial_sps", "walmart=walmart_sps")),
            output,
            require_products=True,
        )
    finally:
        backend.close()

    document = json.loads((output / "publication.json").read_text(encoding="utf-8"))
    persisted_manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))

    assert len(document["products"]) == 1
    assert document["products"][0]["canonical_gtin"] == "07590002040003"
    assert document["products"][0]["best_price"] == "100.00"
    assert document["excluded_group_counts"] == {"not_comparable": 1}
    assert manifest["safe_products_published"] == 1
    assert persisted_manifest["source_backend"] == "sqlite"
    serialized = json.dumps(persisted_manifest)
    assert str(database) not in serialized
    assert "token" not in serialized.casefold()
    assert "database_url" not in serialized.casefold()

    assert (output / "offers.csv").is_file()
    assert (output / "products.csv").is_file()
    assert (output / "common-basket.csv").is_file()
    assert (output / "excluded-groups.csv").is_file()
