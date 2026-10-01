#!/usr/bin/env python3
"""Migra de forma idempotente el esquema MVP para los contextos Paiz.

La migración hace dos cambios acotados:
1. amplía la excepción de ofertas agotadas sin precio a Paiz en price_history;
2. permite múltiples contextos Paiz en la misma ciudad ajustando únicamente el
   índice parcial legado de locations, sin reconstruir la tabla ni sus FKs.

Conserva filas, claves foráneas e índices y registra los dos contextos TGU.
También asegura `idx_ph_loc_hist` (lectura histórica por contexto).

En Turso corre a diario: cuando el esquema, el índice y los contextos ya están
aplicados es un no-op que sólo lee filas de `sqlite_master` y de Paiz (sin
COUNT(*), integrity_check ni foreign_key_check sobre tablas completas).
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from actualizar_mvp_sqlite_la_colonia import SnapshotError
from actualizar_mvp_turso_la_colonia import _execute_rows, _pipeline, _run_batch, _stmt
from generar_mvp_sqlite_la_colonia import HISTORY_INDEX_NAME, HISTORY_INDEX_SQL

SUPERMARKET_ID = "paiz"
SUPERMARKET_NAME = "Paiz"
COUNTRY = "HN"
LOCATIONS = {
    "paiz_tgu_multiplaza": "Tegucigalpa",
    "paiz_tgu_proceres": "Tegucigalpa",
}
OLD_FRAGMENT = "supermarket_id IN ('walmart', 'pricesmart')"
NEW_FRAGMENT = "supermarket_id IN ('walmart', 'pricesmart', 'paiz')"
LOCATION_INDEX_NAME = "idx_locations_city_legacy"
OLD_LOCATION_INDEX_FRAGMENT = "WHERE supermarket_id != 'walmart'"
NEW_LOCATION_INDEX_SQL = (
    "CREATE UNIQUE INDEX idx_locations_city_legacy "
    "ON locations(supermarket_id, city_name) "
    "WHERE supermarket_id NOT IN ('walmart', 'paiz')"
)


def schema_ready_sql(sql: object) -> bool:
    return isinstance(sql, str) and NEW_FRAGMENT in sql and OLD_FRAGMENT not in sql


def locations_index_ready_sql(sql: object) -> bool:
    if not isinstance(sql, str):
        return False
    normalized = " ".join(sql.lower().replace('"', "").split())
    return (
        "create unique index idx_locations_city_legacy" in normalized
        and "on locations(supermarket_id, city_name)" in normalized
        and "'walmart'" in normalized
        and "'paiz'" in normalized
        and "not in" in normalized
    )


def _target_ddl(current_sql: str, table_name: str = "paiz_new_price_history") -> str:
    if schema_ready_sql(current_sql):
        source = current_sql
    elif OLD_FRAGMENT in current_sql:
        source = current_sql.replace(OLD_FRAGMENT, NEW_FRAGMENT, 1)
    else:
        raise SnapshotError("paiz_unrecognized_price_history_check")
    marker = "CREATE TABLE price_history"
    if marker not in source:
        marker = 'CREATE TABLE "price_history"'
    if marker not in source:
        raise SnapshotError("paiz_price_history_ddl_invalid")
    return source.replace(marker, f"CREATE TABLE {table_name}", 1)


def _validate_scope_rows(supermarket: list[list[object]], locations: list[list[object]]) -> None:
    if supermarket and supermarket != [[SUPERMARKET_NAME, COUNTRY]]:
        raise SnapshotError(f"paiz_supermarket_conflict:{supermarket}")
    expected = [[location_id, SUPERMARKET_ID, city, COUNTRY] for location_id, city in sorted(LOCATIONS.items())]
    if locations and locations not in ([expected[0]], [expected[1]], expected):
        raise SnapshotError(f"paiz_location_conflict:{locations}")


def _register_scope_sqlite(con: sqlite3.Connection) -> None:
    con.execute("INSERT OR IGNORE INTO supermarkets VALUES(?,?,?)", (SUPERMARKET_ID, SUPERMARKET_NAME, COUNTRY))
    for location_id, city in LOCATIONS.items():
        con.execute("INSERT OR IGNORE INTO locations VALUES(?,?,?,?)", (location_id, SUPERMARKET_ID, city, COUNTRY))


def migrate_sqlite(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise SnapshotError("database_missing")
    con = sqlite3.connect(path, isolation_level=None)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        current = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='price_history'").fetchone()
        price_index = con.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_price_history_current'").fetchone()
        location_index = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (LOCATION_INDEX_NAME,)
        ).fetchone()
        if (
            not current or not isinstance(current[0], str)
            or not price_index or not isinstance(price_index[0], str)
            or not location_index or not isinstance(location_index[0], str)
        ):
            raise SnapshotError("paiz_schema_objects_missing")
        existing_supermarket = con.execute(
            "SELECT name,country_code FROM supermarkets WHERE supermarket_id=?", (SUPERMARKET_ID,)
        ).fetchall()
        existing_locations = con.execute(
            "SELECT location_id,supermarket_id,city_name,country_code FROM locations WHERE supermarket_id=? ORDER BY location_id",
            (SUPERMARKET_ID,),
        ).fetchall()
        _validate_scope_rows([list(row) for row in existing_supermarket], [list(row) for row in existing_locations])

        price_ready = schema_ready_sql(current[0])
        locations_ready = locations_index_ready_sql(location_index[0])
        before_price = con.execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
        before_locations = con.execute("SELECT COUNT(*) FROM locations").fetchone()[0]

        if not price_ready:
            ddl = _target_ddl(current[0])
            con.execute("PRAGMA foreign_keys=OFF")
            con.execute("BEGIN IMMEDIATE")
            con.execute(ddl)
            con.execute("INSERT INTO paiz_new_price_history SELECT * FROM price_history")
            copied = con.execute("SELECT COUNT(*) FROM paiz_new_price_history").fetchone()[0]
            if copied != before_price:
                raise SnapshotError("paiz_migration_row_count_mismatch")
            con.execute("DROP TABLE price_history")
            con.execute("ALTER TABLE paiz_new_price_history RENAME TO price_history")
            con.execute(price_index[0])
            con.execute("COMMIT")
            con.execute("PRAGMA foreign_keys=ON")

        history_index_ready = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (HISTORY_INDEX_NAME,)
        ).fetchone() is not None

        con.execute("BEGIN IMMEDIATE")
        if not locations_ready:
            con.execute(f"DROP INDEX {LOCATION_INDEX_NAME}")
            con.execute(NEW_LOCATION_INDEX_SQL)
        con.execute(HISTORY_INDEX_SQL)
        _register_scope_sqlite(con)
        con.execute("COMMIT")

        fk = con.execute("PRAGMA foreign_key_check").fetchall()
        integrity = con.execute("PRAGMA integrity_check").fetchall()
        duplicate = con.execute(
            "SELECT COUNT(*) FROM (SELECT product_id,location_id FROM price_history WHERE valid_to_utc IS NULL GROUP BY product_id,location_id HAVING COUNT(*)>1)"
        ).fetchone()[0]
        final_sql = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='price_history'").fetchone()[0]
        final_location_index = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (LOCATION_INDEX_NAME,)
        ).fetchone()[0]
        locations = con.execute(
            "SELECT location_id,supermarket_id,city_name,country_code FROM locations WHERE supermarket_id=? ORDER BY location_id",
            (SUPERMARKET_ID,),
        ).fetchall()
        expected_locations = [(location_id, SUPERMARKET_ID, city, COUNTRY) for location_id, city in sorted(LOCATIONS.items())]
        if (
            not schema_ready_sql(final_sql)
            or not locations_index_ready_sql(final_location_index)
            or fk or integrity != [("ok",)] or duplicate
            or locations != expected_locations
            or con.execute("SELECT COUNT(*) FROM price_history").fetchone()[0] != before_price
            or con.execute("SELECT COUNT(*) FROM locations").fetchone()[0] != before_locations + (2 - len(existing_locations))
            or con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (HISTORY_INDEX_NAME,)
            ).fetchone() is None
        ):
            raise SnapshotError("paiz_migration_postflight_failed")
        return {
            "migrated": not (
                price_ready and locations_ready and len(existing_locations) == 2 and history_index_ready
            ),
            "price_history_migrated": not price_ready,
            "locations_index_migrated": not locations_ready,
            "history_index_created": not history_index_ready,
            "foreign_key_violations": 0,
            "duplicate_open_periods": 0,
            "integrity_check": "ok",
            "locations": [row[0] for row in locations],
        }
    except Exception:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        try:
            con.execute("PRAGMA foreign_keys=ON")
        finally:
            con.close()


def _turso_schema_state(url: str, token: str) -> dict[str, Any]:
    """Estado barato: sólo filas de `sqlite_master` y las filas Paiz registradas."""
    data = _pipeline(
        url,
        token,
        [
            {"type": "execute", "stmt": _stmt("SELECT sql FROM sqlite_master WHERE type='table' AND name='price_history'")},
            {"type": "execute", "stmt": _stmt("SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_price_history_current'")},
            {"type": "execute", "stmt": _stmt("SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (LOCATION_INDEX_NAME,))},
            {"type": "execute", "stmt": _stmt("SELECT name FROM sqlite_master WHERE type='index' AND name=?", (HISTORY_INDEX_NAME,))},
            {"type": "execute", "stmt": _stmt("SELECT name,country_code FROM supermarkets WHERE supermarket_id=?", (SUPERMARKET_ID,))},
            {"type": "execute", "stmt": _stmt("SELECT location_id,supermarket_id,city_name,country_code FROM locations WHERE supermarket_id=? ORDER BY location_id", (SUPERMARKET_ID,))},
            {"type": "close"},
        ],
    )
    results = data.get("results")
    if not isinstance(results, list) or len(results) < 6:
        raise SnapshotError("paiz_turso_migration_preflight_invalid")
    ddl_rows = _execute_rows(results[0])
    price_index_rows = _execute_rows(results[1])
    location_index_rows = _execute_rows(results[2])
    history_index_rows = _execute_rows(results[3])
    supermarket = _execute_rows(results[4])
    locations = _execute_rows(results[5])
    if (
        len(ddl_rows) != 1 or not isinstance(ddl_rows[0][0], str)
        or len(price_index_rows) != 1 or not isinstance(price_index_rows[0][0], str)
        or len(location_index_rows) != 1 or not isinstance(location_index_rows[0][0], str)
    ):
        raise SnapshotError("paiz_turso_schema_objects_missing")
    _validate_scope_rows(supermarket, locations)
    return {
        "ddl": ddl_rows[0][0],
        "price_index": price_index_rows[0][0],
        "location_index": location_index_rows[0][0],
        "existing_locations": locations,
        "price_ready": schema_ready_sql(ddl_rows[0][0]),
        "locations_ready": locations_index_ready_sql(location_index_rows[0][0]),
        "history_index_ready": history_index_rows == [[HISTORY_INDEX_NAME]],
    }


def _turso_counts(url: str, token: str) -> dict[str, Any]:
    """COUNT(*) completos: sólo antes de una migración estructural real."""
    data = _pipeline(
        url,
        token,
        [
            {"type": "execute", "stmt": _stmt("SELECT COUNT(*) FROM price_history")},
            {"type": "execute", "stmt": _stmt("SELECT COUNT(*) FROM locations")},
            {"type": "close"},
        ],
    )
    results = data.get("results")
    if not isinstance(results, list) or len(results) < 2:
        raise SnapshotError("paiz_turso_migration_preflight_invalid")
    price_counts = _execute_rows(results[0])
    location_counts = _execute_rows(results[1])
    if len(price_counts) != 1 or len(location_counts) != 1:
        raise SnapshotError("paiz_turso_schema_objects_missing")
    return {"price_count": price_counts[0][0], "location_count": location_counts[0][0]}


def _turso_preflight(url: str, token: str) -> dict[str, Any]:
    return {**_turso_schema_state(url, token), **_turso_counts(url, token)}


def _structural_migration_needed(state: dict[str, Any]) -> bool:
    return not (
        state["price_ready"] and state["locations_ready"] and len(state["existing_locations"]) == 2
    )


def _ensure_history_index_turso(url: str, token: str) -> None:
    _run_batch(
        url,
        token,
        [
            ("begin", "BEGIN IMMEDIATE", ()),
            ("index_history", HISTORY_INDEX_SQL, ()),
            ("commit", "COMMIT", ()),
        ],
    )
    if not _turso_schema_state(url, token)["history_index_ready"]:
        raise SnapshotError("paiz_turso_history_index_missing")


def migrate_turso(url: str, token: str) -> dict[str, object]:
    if not url.strip() or not token.strip():
        raise SnapshotError("turso_credentials_missing")
    state = _turso_schema_state(url, token)
    if not _structural_migration_needed(state):
        # Camino diario: la migración ya está aplicada. No se leen tablas
        # completas; a lo sumo se crea una vez el índice histórico.
        history_created = not state["history_index_ready"]
        if history_created:
            _ensure_history_index_turso(url, token)
        return {
            "migrated": history_created,
            "price_history_migrated": False,
            "locations_index_migrated": False,
            "history_index_created": history_created,
            "already_applied": True,
            "locations": [row[0] for row in state["existing_locations"]],
        }

    pre = {**state, **_turso_counts(url, token)}
    price_ready = bool(pre["price_ready"])
    locations_ready = bool(pre["locations_ready"])
    existing_locations = list(pre["existing_locations"])

    steps: list[tuple[str, str, tuple[object, ...]]] = []
    if not price_ready:
        ddl = _target_ddl(str(pre["ddl"]))
        steps.extend([
            ("foreign_keys_off", "PRAGMA foreign_keys=OFF", ()),
            ("drop_guard", "DROP TABLE IF EXISTS temp.paiz_migration_guard", ()),
            ("guard_table", "CREATE TEMP TABLE paiz_migration_guard(value INTEGER NOT NULL CHECK(value=0)) STRICT", ()),
            ("begin", "BEGIN IMMEDIATE", ()),
            ("create_price_history", ddl, ()),
            ("copy_price_history", "INSERT INTO paiz_new_price_history SELECT * FROM price_history", ()),
            ("guard_copy", "INSERT INTO paiz_migration_guard SELECT CASE WHEN (SELECT COUNT(*) FROM paiz_new_price_history)=(SELECT COUNT(*) FROM price_history) THEN 0 ELSE 1 END", ()),
            ("drop_price_history", "DROP TABLE price_history", ()),
            ("rename_price_history", "ALTER TABLE paiz_new_price_history RENAME TO price_history", ()),
            ("index_current", str(pre["price_index"]), ()),
        ])
    else:
        steps.append(("begin", "BEGIN IMMEDIATE", ()))

    if not locations_ready:
        steps.extend([
            ("drop_location_index", f"DROP INDEX {LOCATION_INDEX_NAME}", ()),
            ("create_location_index", NEW_LOCATION_INDEX_SQL, ()),
        ])

    steps.extend([
        ("index_history", HISTORY_INDEX_SQL, ()),
        ("register_supermarket", "INSERT OR IGNORE INTO supermarkets VALUES(?,?,?)", (SUPERMARKET_ID, SUPERMARKET_NAME, COUNTRY)),
        *[(f"register_{location_id}", "INSERT OR IGNORE INTO locations VALUES(?,?,?,?)", (location_id, SUPERMARKET_ID, city, COUNTRY)) for location_id, city in LOCATIONS.items()],
    ])
    if not price_ready:
        steps.append(("guard_fk", "INSERT INTO paiz_migration_guard SELECT COUNT(*) FROM pragma_foreign_key_check", ()))
    steps.append(("commit", "COMMIT", ()))
    if not price_ready:
        steps.append(("foreign_keys_on", "PRAGMA foreign_keys=ON", ()))
    _run_batch(url, token, steps)

    data = _pipeline(
        url,
        token,
        [
            {"type": "execute", "stmt": _stmt("SELECT sql FROM sqlite_master WHERE type='table' AND name='price_history'")},
            {"type": "execute", "stmt": _stmt("SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (LOCATION_INDEX_NAME,))},
            {"type": "execute", "stmt": _stmt("SELECT COUNT(*) FROM price_history")},
            {"type": "execute", "stmt": _stmt("SELECT COUNT(*) FROM locations")},
            {"type": "execute", "stmt": _stmt("SELECT COUNT(*) FROM (SELECT product_id,location_id FROM price_history WHERE valid_to_utc IS NULL GROUP BY product_id,location_id HAVING COUNT(*)>1)")},
            {"type": "execute", "stmt": _stmt("SELECT COUNT(*) FROM pragma_foreign_key_check")},
            {"type": "execute", "stmt": _stmt("PRAGMA integrity_check")},
            {"type": "execute", "stmt": _stmt("SELECT location_id,supermarket_id,city_name,country_code FROM locations WHERE supermarket_id=? ORDER BY location_id", (SUPERMARKET_ID,))},
            {"type": "execute", "stmt": _stmt("SELECT name FROM sqlite_master WHERE type='index' AND name=?", (HISTORY_INDEX_NAME,))},
            {"type": "close"},
        ],
    )
    results = data.get("results")
    if not isinstance(results, list) or len(results) < 9:
        raise SnapshotError("paiz_turso_migration_postflight_invalid")
    ddl = _execute_rows(results[0])
    location_index = _execute_rows(results[1])
    price_count = _execute_rows(results[2])
    location_count = _execute_rows(results[3])
    dupes = _execute_rows(results[4])
    fk = _execute_rows(results[5])
    integrity = _execute_rows(results[6])
    locations = _execute_rows(results[7])
    history_index = _execute_rows(results[8])
    expected_locations = [[location_id, SUPERMARKET_ID, city, COUNTRY] for location_id, city in sorted(LOCATIONS.items())]
    expected_location_count = int(pre["location_count"]) + (2 - len(existing_locations))
    if (
        len(ddl) != 1 or not schema_ready_sql(ddl[0][0])
        or len(location_index) != 1 or not locations_index_ready_sql(location_index[0][0])
        or price_count != [[pre["price_count"]]]
        or location_count != [[expected_location_count]]
        or dupes != [[0]] or fk != [[0]] or integrity != [["ok"]]
        or locations != expected_locations
        or history_index != [[HISTORY_INDEX_NAME]]
    ):
        raise SnapshotError(
            f"paiz_turso_migration_postflight_failed:{price_count}:{location_count}:{dupes}:{fk}:{integrity}:{locations}"
        )
    return {
        "migrated": True,
        "price_history_migrated": not price_ready,
        "locations_index_migrated": not locations_ready,
        "history_index_created": not pre["history_index_ready"],
        "already_applied": False,
        "price_history_rows": price_count[0][0],
        "location_rows": location_count[0][0],
        "duplicate_open_periods": 0,
        "foreign_key_violations": 0,
        "integrity_check": "ok",
        "locations": [row[0] for row in locations],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", type=Path)
    parser.add_argument("--turso", action="store_true")
    args = parser.parse_args()
    if bool(args.sqlite) == bool(args.turso):
        raise SystemExit("choose_exactly_one_migration_target")
    try:
        result = migrate_sqlite(args.sqlite) if args.sqlite else migrate_turso(
            os.environ.get("TURSO_DATABASE_URL", ""), os.environ.get("TURSO_AUTH_TOKEN", "")
        )
    except SnapshotError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
