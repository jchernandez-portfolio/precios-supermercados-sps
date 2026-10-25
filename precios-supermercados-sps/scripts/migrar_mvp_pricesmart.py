"""One-time offline-safe schema migration for nullable PriceSmart out-of-stock offers."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path

from generar_mvp_sqlite_la_colonia import create_schema

PRE_PRICESMART_FINGERPRINT = "f09ea1cf63f3de159c87872f842babcc42e5d14f8e2c33067782dd272c1a36f4"
SCHEMA_QUERY = "SELECT name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY name"
def _normalized(rows):
    return [(name, re.sub(r"\s+", "", sql.replace('"', "").rstrip(";"))) for name, sql in rows]
def fingerprint(rows):
    return hashlib.sha256(json.dumps(_normalized(rows), separators=(",", ":")).encode()).hexdigest()
def target_schema():
    con = sqlite3.connect(":memory:")
    try:
        create_schema(con)
        return con.execute(SCHEMA_QUERY).fetchall()
    finally:
        con.close()
def schema_ready(rows):
    expected = [
        (name, sql) for name, sql in target_schema()
        if name in {"locations", "price_history", "idx_locations_city_legacy"}
    ]
    post_paiz = []
    for name, sql in expected:
        if name == "price_history":
            sql = sql.replace(
                "supermarket_id IN ('walmart', 'pricesmart')",
                "supermarket_id IN ('walmart', 'pricesmart', 'paiz')",
                1,
            )
        elif name == "idx_locations_city_legacy":
            sql = (
                "CREATE UNIQUE INDEX idx_locations_city_legacy "
                "ON locations(supermarket_id, city_name) "
                "WHERE supermarket_id NOT IN ('walmart', 'paiz')"
            )
        post_paiz.append((name, sql))
    normalized = _normalized(rows)
    return normalized in (_normalized(expected), _normalized(post_paiz))
def migration_steps():
    schema = dict(target_schema())
    new_table = "pricesmart_new_price_history"
    ddl = schema["price_history"].replace(
        "CREATE TABLE price_history", f"CREATE TABLE {new_table}", 1
    )
    return [
        ("foreign_keys_off", "PRAGMA foreign_keys=OFF", ()),
        ("drop_guard_table", "DROP TABLE IF EXISTS temp.migration_guard", ()),
        ("guard_table", "CREATE TEMP TABLE migration_guard(value INTEGER NOT NULL CHECK(value=0)) STRICT", ()),
        ("begin", "BEGIN IMMEDIATE", ()),
        ("create_price_history", ddl, ()),
        ("copy_price_history", f"INSERT INTO {new_table} SELECT * FROM price_history", ()),
        ("guard_price_history", f"INSERT INTO migration_guard SELECT CASE WHEN (SELECT COUNT(*) FROM {new_table})=(SELECT COUNT(*) FROM price_history) THEN 0 ELSE 1 END", ()),
        ("drop_price_history", "DROP TABLE price_history", ()),
        ("rename_price_history", f"ALTER TABLE {new_table} RENAME TO price_history", ()),
        ("index_current", schema["idx_price_history_current"], ()),
        ("guard_foreign_keys", "INSERT INTO migration_guard SELECT COUNT(*) FROM pragma_foreign_key_check", ()),
        ("commit", "COMMIT", ()),
        ("foreign_keys_on", "PRAGMA foreign_keys=ON", ()),
    ]
def migrate_database(path):
    path = Path(path)
    if not path.is_file():
        raise ValueError("database_missing")
    con = sqlite3.connect(path, isolation_level=None)
    try:
        before = fingerprint(con.execute(SCHEMA_QUERY).fetchall())
        target = fingerprint(target_schema())
        if before == target:
            return {"migrated": False, "schema_fingerprint": target}
        if before != PRE_PRICESMART_FINGERPRINT:
            raise ValueError("unrecognized_schema_no_migration")
        for name, sql, args in migration_steps():
            if name == "commit" and fingerprint(con.execute(SCHEMA_QUERY).fetchall()) != target:
                raise ValueError("migration_schema_mismatch")
            con.execute(sql, args)
        return {"migrated": True, "schema_fingerprint": target}
    except Exception:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        con.execute("PRAGMA foreign_keys=ON")
        con.close()
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(migrate_database(args.sqlite)))
