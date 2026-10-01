#!/usr/bin/env python3
"""Genera el SQLite inicial de Precios de Supermercados desde el snapshot aprobado.

No consulta La Colonia, no usa credenciales y no conecta con Turso. El archivo
resultante se importa con la opción nativa "Upload SQLite File" de Turso.

El modelo físico mínimo separa supermercado, ubicación, producto, historial de
precio y ejecución. La primera carga contiene datos comerciales de SPS; TGU se
registra como ubicación conocida pero queda sin precios hasta tener su propio run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from decimal import Decimal, InvalidOperation
from pathlib import Path

EXPECTED_JSON_SHA256 = "2780eeffa5ef62f2d1c8c2c8365e88da1ca0006622d2f7b1c3529f834c9b5e50"
EXPECTED_SUPERMARKET_ID = "la_colonia"
EXPECTED_SPS_LOCATION_ID = "la_colonia_sps"
EXPECTED_TGU_LOCATION_ID = "la_colonia_tgu"
EXPECTED_SKUS = 9439
EXPECTED_PRODUCTS = 9437
EXPECTED_RUN_ID = "32922877781"
EXPECTED_ARTIFACT_ID = "9590684834"

PRODUCT_KEYS = {
    "availability",
    "brand",
    "category",
    "current_price",
    "ean",
    "is_promotion",
    "item_id",
    "presentation",
    "product_id",
    "reference",
    "reported_regular_price",
    "source_key",
    "source_key_type",
    "source_name",
}


def fail(message: str) -> None:
    raise SystemExit(message)


def price_minor(value: object, *, required: bool) -> int | None:
    if value is None:
        if required:
            fail("precio requerido ausente")
        return None
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        fail("precio inválido")
    try:
        amount = Decimal(value)
    except InvalidOperation:
        fail("precio inválido")
    if not amount.is_finite() or amount < 0:
        fail("precio inválido")
    minor = amount * 100
    if minor != minor.to_integral_value():
        fail("precio con más de dos decimales")
    return int(minor)


def load_snapshot(path: Path) -> dict:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != EXPECTED_JSON_SHA256:
        fail("snapshot_json_digest_mismatch")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        fail("snapshot_json_invalid")

    expected = {
        "result": "success",
        "supermarket_id": EXPECTED_SUPERMARKET_ID,
        "location_id": EXPECTED_SPS_LOCATION_ID,
        "catalog_complete": True,
        "validation_passed": True,
        "location_verified_same_run": True,
        "catalog_products_reported": EXPECTED_PRODUCTS,
        "unique_products_extracted": EXPECTED_PRODUCTS,
        "skus_extracted": EXPECTED_SKUS,
        "skus_with_price": EXPECTED_SKUS,
        "catalog_product_coverage": 1.0,
        "extraction_enabled": False,
    }
    for key, value in expected.items():
        if data.get(key) != value:
            fail(f"snapshot_metadata_mismatch:{key}")

    observed_at = data.get("observed_at_utc")
    if not isinstance(observed_at, str) or not observed_at.endswith("Z"):
        fail("snapshot_observed_at_invalid")

    products = data.get("products")
    if not isinstance(products, list) or len(products) != EXPECTED_SKUS:
        fail("snapshot_offer_count_mismatch")

    identities: set[tuple[str, str]] = set()
    source_product_ids: set[str] = set()
    for row in products:
        if not isinstance(row, dict) or set(row) != PRODUCT_KEYS:
            fail("snapshot_product_schema_mismatch")
        key_type = row["source_key_type"]
        source_key = row["source_key"]
        if (
            not isinstance(key_type, str)
            or not key_type
            or not isinstance(source_key, str)
            or not source_key
        ):
            fail("snapshot_source_identity_invalid")
        identity = (key_type, source_key)
        if identity in identities:
            fail("snapshot_source_identity_duplicate")
        identities.add(identity)
        source_product_ids.add(str(row["product_id"]))
        price_minor(row["current_price"], required=True)
        price_minor(row["reported_regular_price"], required=False)
        if type(row["is_promotion"]) is not bool:
            fail("snapshot_promotion_invalid")
        if row["availability"] not in {"in_stock", "unknown"}:
            fail("snapshot_availability_invalid")

    if len(source_product_ids) != EXPECTED_PRODUCTS:
        fail("snapshot_product_identity_count_mismatch")
    return data


# Índice de lectura histórica por contexto (Optimización Turso 2026-09-30).
# No forma parte de `create_schema`: las migraciones Walmart/PriceSmart congelan
# su huella del esquema base. Se aplica de forma idempotente desde
# `migrar_mvp_paiz.py` (SQLite y Turso) igual que `idx_price_history_current`
# se recrea en cada migración.
HISTORY_INDEX_NAME = "idx_ph_loc_hist"
HISTORY_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_ph_loc_hist "
    "ON price_history(location_id, product_id, valid_from_utc)"
)


def create_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE supermarkets (
            supermarket_id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            country_code TEXT NOT NULL CHECK (length(country_code) = 2)
        ) STRICT;

        CREATE TABLE locations (
            location_id TEXT PRIMARY KEY,
            supermarket_id TEXT NOT NULL,
            city_name TEXT NOT NULL,
            country_code TEXT NOT NULL CHECK (length(country_code) = 2),
            FOREIGN KEY (supermarket_id) REFERENCES supermarkets(supermarket_id),
            UNIQUE (location_id, supermarket_id)
        ) STRICT;

        CREATE TABLE products (
            product_id INTEGER PRIMARY KEY,
            supermarket_id TEXT NOT NULL,
            source_key_type TEXT NOT NULL,
            source_key TEXT NOT NULL,
            source_catalog_product_id TEXT NOT NULL,
            source_item_id TEXT NOT NULL,
            reference TEXT,
            ean TEXT,
            name TEXT NOT NULL,
            brand TEXT,
            presentation TEXT,
            category TEXT,
            FOREIGN KEY (supermarket_id) REFERENCES supermarkets(supermarket_id),
            UNIQUE (supermarket_id, source_key_type, source_key),
            UNIQUE (product_id, supermarket_id)
        ) STRICT;

        CREATE TABLE scrape_runs (
            scrape_run_id TEXT PRIMARY KEY,
            supermarket_id TEXT NOT NULL,
            location_id TEXT NOT NULL,
            observed_at_utc TEXT NOT NULL,
            run_status TEXT NOT NULL CHECK (run_status IN ('success', 'rejected', 'failed')),
            sku_count INTEGER NOT NULL CHECK (sku_count >= 0),
            catalog_product_count INTEGER NOT NULL CHECK (catalog_product_count >= 0),
            source_artifact_id TEXT,
            source_json_sha256 TEXT,
            error_reason TEXT,
            FOREIGN KEY (supermarket_id) REFERENCES supermarkets(supermarket_id),
            FOREIGN KEY (location_id, supermarket_id)
                REFERENCES locations(location_id, supermarket_id)
        ) STRICT;

        CREATE TABLE price_history (
            product_id INTEGER NOT NULL,
            supermarket_id TEXT NOT NULL,
            location_id TEXT NOT NULL,
            current_price_minor INTEGER CHECK (current_price_minor >= 0),
            reported_regular_price_minor INTEGER CHECK (
                reported_regular_price_minor IS NULL
                OR reported_regular_price_minor >= 0
            ),
            is_promotion INTEGER CHECK (is_promotion IN (0, 1)),
            availability TEXT NOT NULL CHECK (
                availability IN ('in_stock', 'out_of_stock', 'unknown')
            ),
            currency TEXT NOT NULL CHECK (currency = 'HNL'),
            valid_from_utc TEXT NOT NULL,
            valid_to_utc TEXT,
            scrape_run_id TEXT NOT NULL,
            PRIMARY KEY (product_id, location_id, valid_from_utc),
            FOREIGN KEY (product_id, supermarket_id)
                REFERENCES products(product_id, supermarket_id),
            FOREIGN KEY (location_id, supermarket_id)
                REFERENCES locations(location_id, supermarket_id),
            FOREIGN KEY (scrape_run_id) REFERENCES scrape_runs(scrape_run_id),
            CHECK (
                (current_price_minor IS NOT NULL AND is_promotion IS NOT NULL)
                OR (supermarket_id IN ('walmart', 'pricesmart') AND availability = 'out_of_stock'
                    AND current_price_minor IS NULL AND reported_regular_price_minor IS NULL
                    AND is_promotion IS NULL)
            )
        ) STRICT;

        CREATE UNIQUE INDEX idx_locations_city_legacy
            ON locations(supermarket_id, city_name) WHERE supermarket_id != 'walmart';
        CREATE INDEX idx_products_name ON products(name);
        CREATE INDEX idx_price_history_current
            ON price_history(location_id, product_id)
            WHERE valid_to_utc IS NULL;
        """
    )


def create_database(snapshot: dict, output: Path) -> dict[str, int | str]:
    if output.exists():
        fail(f"output_exists:{output}")

    con = sqlite3.connect(output)
    try:
        con.execute("PRAGMA foreign_keys = ON")
        create_schema(con)

        con.execute(
            "INSERT INTO supermarkets (supermarket_id, name, country_code) VALUES (?, ?, ?)",
            (EXPECTED_SUPERMARKET_ID, "La Colonia", "HN"),
        )
        con.executemany(
            """
            INSERT INTO locations (location_id, supermarket_id, city_name, country_code)
            VALUES (?, ?, ?, ?)
            """,
            (
                (EXPECTED_SPS_LOCATION_ID, EXPECTED_SUPERMARKET_ID, "San Pedro Sula", "HN"),
                (EXPECTED_TGU_LOCATION_ID, EXPECTED_SUPERMARKET_ID, "Tegucigalpa", "HN"),
            ),
        )

        observed_at = snapshot["observed_at_utc"]
        con.execute(
            """
            INSERT INTO scrape_runs (
                scrape_run_id, supermarket_id, location_id, observed_at_utc,
                run_status, sku_count, catalog_product_count,
                source_artifact_id, source_json_sha256, error_reason
            ) VALUES (?, ?, ?, ?, 'success', ?, ?, ?, ?, NULL)
            """,
            (
                EXPECTED_RUN_ID,
                EXPECTED_SUPERMARKET_ID,
                EXPECTED_SPS_LOCATION_ID,
                observed_at,
                EXPECTED_SKUS,
                EXPECTED_PRODUCTS,
                EXPECTED_ARTIFACT_ID,
                EXPECTED_JSON_SHA256,
            ),
        )

        products = sorted(
            snapshot["products"],
            key=lambda item: (item["source_key_type"], item["source_key"]),
        )
        product_rows = []
        history_rows = []
        for product_id, item in enumerate(products, start=1):
            product_rows.append(
                (
                    product_id,
                    EXPECTED_SUPERMARKET_ID,
                    item["source_key_type"],
                    item["source_key"],
                    str(item["product_id"]),
                    str(item["item_id"]),
                    item["reference"],
                    item["ean"],
                    item["source_name"],
                    item["brand"],
                    item["presentation"],
                    item["category"],
                )
            )
            history_rows.append(
                (
                    product_id,
                    EXPECTED_SUPERMARKET_ID,
                    EXPECTED_SPS_LOCATION_ID,
                    price_minor(item["current_price"], required=True),
                    price_minor(item["reported_regular_price"], required=False),
                    int(item["is_promotion"]),
                    item["availability"],
                    "HNL",
                    observed_at,
                    None,
                    EXPECTED_RUN_ID,
                )
            )

        con.executemany(
            """
            INSERT INTO products (
                product_id, supermarket_id, source_key_type, source_key,
                source_catalog_product_id, source_item_id, reference, ean,
                name, brand, presentation, category
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            product_rows,
        )
        con.executemany(
            """
            INSERT INTO price_history (
                product_id, supermarket_id, location_id,
                current_price_minor, reported_regular_price_minor,
                is_promotion, availability, currency,
                valid_from_utc, valid_to_utc, scrape_run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            history_rows,
        )
        con.commit()

        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            fail(f"sqlite_integrity_failed:{integrity}")
        if con.execute("PRAGMA foreign_key_check").fetchall():
            fail("sqlite_foreign_key_check_failed")

        counts = {
            table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "supermarkets",
                "locations",
                "products",
                "price_history",
                "scrape_runs",
            )
        }
        open_count = con.execute(
            "SELECT COUNT(*) FROM price_history WHERE valid_to_utc IS NULL"
        ).fetchone()[0]
        priced_count = con.execute(
            "SELECT COUNT(*) FROM price_history WHERE current_price_minor IS NOT NULL"
        ).fetchone()[0]
        unknown_count = con.execute(
            "SELECT COUNT(*) FROM price_history WHERE availability = 'unknown'"
        ).fetchone()[0]
        in_stock_count = con.execute(
            "SELECT COUNT(*) FROM price_history WHERE availability = 'in_stock'"
        ).fetchone()[0]
        source_product_count = con.execute(
            "SELECT COUNT(DISTINCT source_catalog_product_id) FROM products"
        ).fetchone()[0]
        tgu_history_count = con.execute(
            "SELECT COUNT(*) FROM price_history WHERE location_id = ?",
            (EXPECTED_TGU_LOCATION_ID,),
        ).fetchone()[0]

        expected_counts = {
            "supermarkets": 1,
            "locations": 2,
            "products": EXPECTED_SKUS,
            "price_history": EXPECTED_SKUS,
            "scrape_runs": 1,
        }
        if counts != expected_counts:
            fail("sqlite_table_reconciliation_failed")
        if (
            open_count != EXPECTED_SKUS
            or priced_count != EXPECTED_SKUS
            or unknown_count != 2358
            or in_stock_count != 7081
            or source_product_count != EXPECTED_PRODUCTS
            or tgu_history_count != 0
        ):
            fail("sqlite_data_reconciliation_failed")

        con.execute("VACUUM")
        return {
            **counts,
            "open_prices": open_count,
            "priced_rows": priced_count,
            "availability_in_stock": in_stock_count,
            "availability_unknown": unknown_count,
            "distinct_source_products": source_product_count,
            "tgu_price_rows": tgu_history_count,
            "sqlite_integrity": integrity,
        }
    except Exception:
        con.rollback()
        con.close()
        if output.exists():
            output.unlink()
        raise
    finally:
        try:
            con.close()
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot_json", type=Path)
    parser.add_argument("output_db", type=Path)
    args = parser.parse_args()

    snapshot = load_snapshot(args.snapshot_json)
    summary = create_database(snapshot, args.output_db)
    summary["output"] = str(args.output_db)
    summary["sha256"] = hashlib.sha256(args.output_db.read_bytes()).hexdigest()
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
