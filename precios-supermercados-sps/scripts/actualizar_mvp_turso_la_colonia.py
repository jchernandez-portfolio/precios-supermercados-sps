#!/usr/bin/env python3
"""Aplica snapshots completos por supermercado a las cinco tablas Turso."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from actualizar_mvp_sqlite_la_colonia import (  # noqa: E402
    LOCATIONS,
    COLONIAL_LOCATIONS,
    PRICESMART_LOCATIONS,
    WALMART_LOCATIONS,
    SUPERMARKET_ID,
    SnapshotError,
    _minor,
    validate_snapshot_bytes,
)

EXPECTED_TABLES = {"supermarkets", "locations", "products", "price_history", "scrape_runs"}
# Tablas derivadas que la persistencia diaria tolera sin tocarlas: perfiles de
# homologación y producto maestro (docs/homologation/product-master-v1.md).
OPTIONAL_DERIVED_TABLES = {
    "product_homologation_profiles",
    "master_products",
    "master_product_links",
    "master_link_rejections",
    "master_sync_state",
    "master_sync_dirty",
}


def _validate_table_names(names: object) -> None:
    try:
        observed = {str(name) for name in names}
    except TypeError as exc:
        raise SnapshotError("turso_schema_mismatch") from exc
    if not EXPECTED_TABLES <= observed:
        raise SnapshotError("turso_schema_mismatch")
    if not observed <= EXPECTED_TABLES | OPTIONAL_DERIVED_TABLES:
        raise SnapshotError("turso_schema_mismatch")


def _http_url(value: str) -> str:
    url = value.strip().rstrip("/")
    if url.startswith("libsql://"):
        return "https://" + url.removeprefix("libsql://")
    if url.startswith("https://"):
        return url
    raise SnapshotError("turso_database_url_invalid")


def _value(value: object) -> dict[str, str]:
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "integer", "value": str(int(value))}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    return {"type": "text", "value": str(value)}


def _stmt(sql: str, args: tuple[object, ...] = ()) -> dict[str, Any]:
    return {"sql": sql, "args": [_value(value) for value in args]}


def _pipeline(url: str, token: str, requests: list[dict[str, Any]]) -> dict[str, Any]:
    req = urllib.request.Request(
        _http_url(url) + "/v2/pipeline",
        data=json.dumps({"requests": requests}, separators=(",", ":")).encode(),
        headers={
            "Authorization": "Bearer " + token.strip(),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise SnapshotError(f"turso_http_{exc.code}:{body[:300]}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise SnapshotError(f"turso_connection_failed:{exc}") from exc
    if not isinstance(data, dict):
        raise SnapshotError("turso_response_invalid")
    return data


def _scalar(value: object) -> object:
    if not isinstance(value, dict):
        return value
    if value.get("type") == "null":
        return None
    raw = value.get("value")
    return int(raw) if value.get("type") == "integer" and isinstance(raw, str) else raw


def _execute_rows(result: object) -> list[list[object]]:
    if not isinstance(result, dict) or result.get("type") != "ok":
        raise SnapshotError(f"turso_execute_failed:{result}")
    response = result.get("response")
    if not isinstance(response, dict) or response.get("type") != "execute":
        raise SnapshotError("turso_execute_response_invalid")
    query = response.get("result")
    if not isinstance(query, dict) or not isinstance(query.get("rows"), list):
        raise SnapshotError("turso_execute_result_invalid")
    return [[_scalar(value) for value in row] for row in query["rows"]]


def _normalised_json(snapshot: dict[str, Any]) -> str:
    rows = []
    for row in snapshot["products"]:
        rows.append({
            "source_key_type": str(row["source_key_type"]),
            "source_key": str(row["source_key"]),
            "source_catalog_product_id": str(row["product_id"]),
            "source_item_id": str(row["item_id"]),
            "reference": row["reference"],
            "ean": row["ean"],
            "name": row["source_name"],
            "brand": row["brand"],
            "presentation": row["presentation"],
            "category": row["category"],
            "current_price_minor": _minor(
                row["current_price"],
                snapshot["supermarket_id"] not in {"walmart", "pricesmart"},
            ),
            "reported_regular_price_minor": _minor(row["reported_regular_price"]),
            "is_promotion": None if row["is_promotion"] is None else int(row["is_promotion"]),
            "availability": row["availability"],
        })
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":"))


def _preflight(
    url: str, token: str, *, location_id: str, run_id: str, supermarket_id: str = SUPERMARKET_ID
) -> dict[str, object] | None:
    queries = [
        (
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name",
            (),
        ),
        (
            "SELECT supermarket_id, city_name FROM locations WHERE location_id=?",
            (location_id,),
        ),
        (
            "SELECT location_id, run_status, source_json_sha256 "
            "FROM scrape_runs WHERE scrape_run_id=?",
            (run_id,),
        ),
    ]
    if supermarket_id in {"walmart", "pricesmart"}:
        queries.append(("SELECT name,sql FROM sqlite_master WHERE name IN ('locations','price_history','idx_locations_city_legacy') ORDER BY name", ()))
    data = _pipeline(
        url,
        token,
        [{"type": "execute", "stmt": _stmt(sql, args)} for sql, args in queries]
        + [{"type": "close"}],
    )
    results = data.get("results")
    if not isinstance(results, list) or len(results) < 3:
        raise SnapshotError("turso_preflight_response_invalid")
    _validate_table_names(str(row[0]) for row in _execute_rows(results[0]))
    locations = {
        SUPERMARKET_ID: LOCATIONS, "colonial": COLONIAL_LOCATIONS,
        "walmart": WALMART_LOCATIONS, "pricesmart": PRICESMART_LOCATIONS,
    }[supermarket_id]
    if supermarket_id == "walmart":
        from migrar_mvp_walmart import schema_ready
        if len(results) < 4 or not schema_ready(_execute_rows(results[3])):
            raise SnapshotError("walmart_schema_migration_required")
    if supermarket_id == "pricesmart":
        from migrar_mvp_pricesmart import schema_ready
        if len(results) < 4 or not schema_ready(_execute_rows(results[3])):
            raise SnapshotError("pricesmart_schema_migration_required")
    found = _execute_rows(results[1])
    if found != [[supermarket_id, locations[location_id]]] and not (supermarket_id in {"colonial", "walmart", "pricesmart"} and not found):
        raise SnapshotError("turso_location_mismatch")
    rows = _execute_rows(results[2])
    if not rows:
        return None
    if len(rows) != 1:
        raise SnapshotError("turso_run_duplicate")
    return {"location_id": rows[0][0], "run_status": rows[0][1], "sha": rows[0][2]}


def _mutation_steps(
    incoming: str,
    *,
    location_id: str,
    observed_at: str,
    run_id: str,
    sku_count: int,
    catalog_count: int,
    artifact_id: str | None,
    digest: str,
    supermarket_id: str = SUPERMARKET_ID,
) -> list[tuple[str, str, tuple[object, ...]]]:
    # incoming se materializa e indexa una sola vez. Dentro de la transacción se
    # materializa delta para no repetir la misma comparación current-vs-snapshot
    # en guardas, cierre y apertura de histórico.
    #
    # Optimización Turso (rows read): las guardas de cardinalidad usan
    # `changes()` del INSERT inmediatamente anterior en vez de COUNT(*) sobre la
    # tabla recién cargada (mismo valor, sin releer N filas), y la verificación
    # final sólo busca en price_history los productos que cambiaron: los demás
    # conservan el periodo current ya comparado en delta_load, porque close_history
    # sólo toca `changed=1` dentro de la misma transacción.
    steps = [
        # Turso puede reutilizar la misma sesión Hrana aun después de `close`.
        # Limpiar staging residual evita que una segunda ubicación falle por
        # nombres TEMP conservados por esa sesión.
        ("drop_incoming_table", "DROP TABLE IF EXISTS temp.incoming", ()),
        ("drop_guard_table", "DROP TABLE IF EXISTS temp.guard_ok", ()),
        ("drop_delta_table", "DROP TABLE IF EXISTS temp.delta", ()),
        (
            "incoming_table",
            """CREATE TEMP TABLE incoming(
                source_key_type TEXT NOT NULL, source_key TEXT NOT NULL,
                source_catalog_product_id TEXT NOT NULL, source_item_id TEXT NOT NULL,
                reference TEXT, ean TEXT, name TEXT NOT NULL, brand TEXT,
                presentation TEXT, category TEXT, current_price_minor INTEGER,
                reported_regular_price_minor INTEGER, is_promotion INTEGER,
                availability TEXT NOT NULL,
                UNIQUE(source_key_type, source_key)) STRICT""",
            (),
        ),
        (
            "guard_table",
            "CREATE TEMP TABLE guard_ok(value INTEGER NOT NULL CHECK(value=0)) STRICT",
            (),
        ),
        (
            "incoming_load",
            """INSERT INTO incoming SELECT
                json_extract(value,'$.source_key_type'),json_extract(value,'$.source_key'),
                json_extract(value,'$.source_catalog_product_id'),
                json_extract(value,'$.source_item_id'),json_extract(value,'$.reference'),
                json_extract(value,'$.ean'),json_extract(value,'$.name'),
                json_extract(value,'$.brand'),json_extract(value,'$.presentation'),
                json_extract(value,'$.category'),
                CAST(json_extract(value,'$.current_price_minor') AS INTEGER),
                CAST(json_extract(value,'$.reported_regular_price_minor') AS INTEGER),
                CAST(json_extract(value,'$.is_promotion') AS INTEGER),
                json_extract(value,'$.availability') FROM json_each(?)""",
            (incoming,),
        ),
        (
            # Debe ir inmediatamente después de incoming_load.
            "guard_incoming",
            "INSERT INTO guard_ok SELECT CASE WHEN changes()=? THEN 0 ELSE 1 END",
            (sku_count,),
        ),
        ("begin", "BEGIN IMMEDIATE", ()),
        (
            "guard_existing_duplicates",
            """INSERT INTO guard_ok SELECT CASE WHEN EXISTS(
                SELECT 1 FROM price_history
                WHERE supermarket_id=? AND location_id=? AND valid_to_utc IS NULL
                GROUP BY product_id HAVING COUNT(*)>1) THEN 1 ELSE 0 END""",
            (supermarket_id, location_id),
        ),
        (
            "upsert_products",
            """INSERT INTO products(
                supermarket_id,source_key_type,source_key,source_catalog_product_id,
                source_item_id,reference,ean,name,brand,presentation,category)
                SELECT ?,source_key_type,source_key,source_catalog_product_id,
                source_item_id,reference,ean,name,brand,presentation,category
                FROM incoming WHERE 1
                ON CONFLICT(supermarket_id,source_key_type,source_key) DO UPDATE SET
                source_catalog_product_id=excluded.source_catalog_product_id,
                source_item_id=excluded.source_item_id,reference=excluded.reference,
                ean=excluded.ean,name=excluded.name,brand=excluded.brand,
                presentation=excluded.presentation,category=excluded.category
                WHERE products.source_catalog_product_id IS NOT excluded.source_catalog_product_id
                   OR products.source_item_id IS NOT excluded.source_item_id
                   OR products.reference IS NOT excluded.reference
                   OR products.ean IS NOT excluded.ean
                   OR products.name IS NOT excluded.name
                   OR products.brand IS NOT excluded.brand
                   OR products.presentation IS NOT excluded.presentation
                   OR products.category IS NOT excluded.category""",
            (supermarket_id,),
        ),
        (
            "delta_table",
            """CREATE TEMP TABLE delta(
                product_id INTEGER PRIMARY KEY,
                had_current INTEGER NOT NULL CHECK(had_current IN (0,1)),
                changed INTEGER NOT NULL CHECK(changed IN (0,1)),
                previous_valid_from_utc TEXT,
                current_price_minor INTEGER,
                reported_regular_price_minor INTEGER,
                is_promotion INTEGER,
                availability TEXT NOT NULL) STRICT""",
            (),
        ),
        (
            "delta_load",
            """INSERT INTO delta
                SELECT p.product_id,
                  CASE WHEN ph.product_id IS NULL THEN 0 ELSE 1 END,
                  CASE WHEN ph.product_id IS NULL
                    OR ph.current_price_minor IS NOT i.current_price_minor
                    OR ph.reported_regular_price_minor IS NOT i.reported_regular_price_minor
                    OR ph.is_promotion IS NOT i.is_promotion
                    OR ph.availability IS NOT i.availability
                    THEN 1 ELSE 0 END,
                  ph.valid_from_utc,
                  i.current_price_minor,i.reported_regular_price_minor,
                  i.is_promotion,i.availability
                FROM incoming i
                JOIN products p ON p.supermarket_id=?
                 AND p.source_key_type=i.source_key_type AND p.source_key=i.source_key
                LEFT JOIN price_history ph ON ph.product_id=p.product_id
                 AND ph.supermarket_id=? AND ph.location_id=? AND ph.valid_to_utc IS NULL""",
            (supermarket_id, supermarket_id, location_id),
        ),
        (
            # Debe ir inmediatamente después de delta_load.
            "guard_delta",
            "INSERT INTO guard_ok SELECT CASE WHEN changes()=? THEN 0 ELSE 1 END",
            (sku_count,),
        ),
        (
            "guard_out_of_order",
            """INSERT INTO guard_ok SELECT CASE WHEN EXISTS(
                SELECT 1 FROM delta
                WHERE changed=1 AND had_current=1
                  AND julianday(?)<=julianday(previous_valid_from_utc)
                ) THEN 1 ELSE 0 END""",
            (observed_at,),
        ),
        (
            "insert_run",
            """INSERT INTO scrape_runs(
                scrape_run_id,supermarket_id,location_id,observed_at_utc,run_status,
                sku_count,catalog_product_count,source_artifact_id,source_json_sha256,error_reason)
                VALUES(?,?,?,?,'success',?,?,?,?,NULL)""",
            (
                run_id,
                supermarket_id,
                location_id,
                observed_at,
                sku_count,
                catalog_count,
                artifact_id,
                digest,
            ),
        ),
        (
            "close_history",
            """UPDATE price_history AS ph SET valid_to_utc=?
                WHERE ph.supermarket_id=? AND ph.location_id=? AND ph.valid_to_utc IS NULL
                  AND ph.product_id IN (
                    SELECT product_id FROM delta WHERE changed=1 AND had_current=1
                  )""",
            (observed_at, supermarket_id, location_id),
        ),
        (
            "open_history",
            """INSERT INTO price_history(
                product_id,supermarket_id,location_id,current_price_minor,
                reported_regular_price_minor,is_promotion,availability,currency,
                valid_from_utc,valid_to_utc,scrape_run_id)
                SELECT product_id,?,?,current_price_minor,reported_regular_price_minor,
                  is_promotion,availability,'HNL',?,NULL,?
                FROM delta WHERE changed=1""",
            (supermarket_id, location_id, observed_at, run_id),
        ),
        (
            "guard_current",
            """INSERT INTO guard_ok SELECT CASE WHEN EXISTS(
                SELECT 1 FROM delta d
                LEFT JOIN price_history ph ON ph.product_id=d.product_id
                 AND ph.supermarket_id=? AND ph.location_id=? AND ph.valid_to_utc IS NULL
                WHERE d.changed=1 AND (
                   ph.product_id IS NULL
                   OR ph.current_price_minor IS NOT d.current_price_minor
                   OR ph.reported_regular_price_minor IS NOT d.reported_regular_price_minor
                   OR ph.is_promotion IS NOT d.is_promotion
                   OR ph.availability IS NOT d.availability)
                ) THEN 1 ELSE 0 END""",
            (supermarket_id, location_id),
        ),
        (
            "guard_run",
            """INSERT INTO guard_ok SELECT CASE WHEN COUNT(*)=1 THEN 0 ELSE 1 END
                FROM scrape_runs WHERE scrape_run_id=?""",
            (run_id,),
        ),
        ("commit", "COMMIT", ()),
    ]

    if supermarket_id in {"colonial", "walmart", "pricesmart"}:
        name = {
            "colonial": "Supermercados Colonial",
            "walmart": "Walmart Honduras",
            "pricesmart": "PriceSmart Honduras",
        }[supermarket_id]
        city = {
            "colonial": COLONIAL_LOCATIONS,
            "walmart": WALMART_LOCATIONS,
            "pricesmart": PRICESMART_LOCATIONS,
        }[supermarket_id][location_id]
        begin = next(i for i, step in enumerate(steps) if step[0] == "begin")
        steps[begin + 1:begin + 1] = [
            ("register_supermarket", "INSERT OR IGNORE INTO supermarkets VALUES(?,?,'HN')", (supermarket_id, name)),
            ("register_location", "INSERT OR IGNORE INTO locations VALUES(?,?,?,'HN')", (location_id, supermarket_id, city)),
            ("guard_registered_scope", """INSERT INTO guard_ok SELECT CASE WHEN COUNT(*)=1 THEN 0 ELSE 1 END
                FROM locations l JOIN supermarkets s ON s.supermarket_id=l.supermarket_id
                WHERE l.location_id=? AND l.supermarket_id=?
                  AND l.city_name=? AND l.country_code='HN'
                  AND s.name=? AND s.country_code='HN'""", (location_id, supermarket_id, city, name)),
        ]
    return steps


def _batch_request(steps: list[tuple[str, str, tuple[object, ...]]]) -> dict[str, Any]:
    begin = next(i for i, step in enumerate(steps) if step[0] == "begin")
    batch = []
    for index, (_, sql, args) in enumerate(steps):
        item: dict[str, Any] = {"stmt": _stmt(sql, args)}
        if index:
            item["condition"] = {"type": "ok", "step": index - 1}
        batch.append(item)
    batch.append({
        "condition": {
            "type": "or",
            "conds": [
                {"type": "error", "step": index}
                for index in range(begin + 1, len(steps))
            ],
        },
        "stmt": _stmt("ROLLBACK"),
    })
    return {"type": "batch", "batch": {"steps": batch}}


def _slots(value: object, size: int) -> list[object | None]:
    if isinstance(value, list):
        return [value[i] if i < len(value) else None for i in range(size)]
    if isinstance(value, dict):
        return [value.get(str(i)) for i in range(size)]
    raise SnapshotError("turso_batch_result_invalid")


def _run_batch(
    url: str, token: str, steps: list[tuple[str, str, tuple[object, ...]]]
) -> list[dict[str, Any] | None]:
    data = _pipeline(url, token, [_batch_request(steps), {"type": "close"}])
    results = data.get("results")
    if not isinstance(results, list) or not results or not isinstance(results[0], dict):
        raise SnapshotError("turso_batch_response_invalid")
    first = results[0]
    if first.get("type") != "ok":
        raise SnapshotError(f"turso_batch_failed:{first}")
    response = first.get("response")
    if not isinstance(response, dict) or response.get("type") != "batch":
        raise SnapshotError("turso_batch_response_invalid")
    result = response.get("result")
    if not isinstance(result, dict):
        raise SnapshotError("turso_batch_result_invalid")
    size = len(steps) + 1
    values = _slots(result.get("step_results"), size)
    errors = _slots(result.get("step_errors"), size)
    for index, error in enumerate(errors[:-1]):
        if error is None:
            continue
        name = steps[index][0]
        if name == "guard_out_of_order":
            raise SnapshotError("snapshot_out_of_order")
        if name.startswith("guard_"):
            raise SnapshotError(f"turso_invariant_failed:{name}")
        raise SnapshotError(f"turso_mutation_failed:{name}:{error}")
    if errors[-1] is not None:
        raise SnapshotError(f"turso_rollback_failed:{errors[-1]}")
    commit = next(i for i, step in enumerate(steps) if step[0] == "commit")
    if values[commit] is None:
        raise SnapshotError("turso_commit_missing")
    return [item if isinstance(item, dict) else None for item in values[:-1]]


def _affected(
    results: list[dict[str, Any] | None],
    steps: list[tuple[str, str, tuple[object, ...]]],
    name: str,
) -> int:
    index = next(i for i, step in enumerate(steps) if step[0] == name)
    result = results[index]
    if not isinstance(result, dict) or not isinstance(result.get("affected_row_count"), int):
        raise SnapshotError(f"turso_affected_count_invalid:{name}")
    return int(result["affected_row_count"])


def persist_snapshot(
    raw: bytes,
    *,
    database_url: str,
    auth_token: str,
    run_id: str,
    source_artifact_id: str | None = None,
    supermarket_id: str = SUPERMARKET_ID,
) -> dict[str, object]:
    snapshot = validate_snapshot_bytes(raw, supermarket_id=supermarket_id)
    if not run_id.strip():
        raise SnapshotError("run_id_missing")
    if not auth_token.strip():
        raise SnapshotError("turso_auth_token_missing")
    digest = hashlib.sha256(raw).hexdigest()
    location_id = str(snapshot["location_id"])
    previous = _preflight(
        database_url, auth_token, location_id=location_id, run_id=run_id, supermarket_id=supermarket_id
    )
    if previous is not None:
        if (
            previous["location_id"] == location_id
            and previous["run_status"] == "success"
            and previous["sha"] == digest
        ):
            return {
                "run_id": run_id,
                "location_id": location_id,
                "source_json_sha256": digest,
                "replayed": True,
                "products_processed": 0,
                "history_opened": 0,
                "history_closed": 0,
                "history_unchanged": 0,
            }
        raise SnapshotError("run_id_conflict")

    steps = _mutation_steps(
        _normalised_json(snapshot),
        location_id=location_id,
        observed_at=str(snapshot["observed_at_utc"]),
        run_id=run_id,
        sku_count=len(snapshot["products"]),
        catalog_count=int(snapshot["catalog_products_reported"]),
        artifact_id=source_artifact_id,
        digest=digest,
        supermarket_id=supermarket_id,
    )
    results = _run_batch(database_url, auth_token, steps)
    opened = _affected(results, steps, "open_history")
    return {
        "run_id": run_id,
        "location_id": location_id,
        "source_json_sha256": digest,
        "replayed": False,
        "products_processed": len(snapshot["products"]),
        "history_opened": opened,
        "history_closed": _affected(results, steps, "close_history"),
        "history_unchanged": len(snapshot["products"]) - opened,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot_json", type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--supermarket", choices=(SUPERMARKET_ID, "colonial", "walmart", "pricesmart"), default=SUPERMARKET_ID)
    parser.add_argument("--source-artifact-id")
    args = parser.parse_args()
    url = os.environ.get("TURSO_DATABASE_URL", "")
    token = os.environ.get("TURSO_AUTH_TOKEN", "")
    if not url.strip() or not token.strip():
        raise SystemExit("TURSO_DATABASE_URL/TURSO_AUTH_TOKEN missing")
    try:
        summary = persist_snapshot(
            args.snapshot_json.read_bytes(),
            database_url=url,
            auth_token=token,
            run_id=args.run_id,
            source_artifact_id=args.source_artifact_id,
            supermarket_id=args.supermarket,
        )
    except SnapshotError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
