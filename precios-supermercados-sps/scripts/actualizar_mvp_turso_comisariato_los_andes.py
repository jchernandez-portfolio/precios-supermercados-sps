#!/usr/bin/env python3
"""Valida y persiste snapshots completos de Comisariato Los Andes SPS en Turso."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from actualizar_mvp_sqlite_la_colonia import SnapshotError, _minor  # noqa: E402
from actualizar_mvp_turso_la_colonia import (  # noqa: E402
    _affected,
    _execute_rows,
    _mutation_steps,
    _normalised_json,
    _pipeline,
    _run_batch,
    _stmt,
    _validate_table_names,
)
from precios_supermercados.scrapers.comisariato_los_andes import gtin_from_code  # noqa: E402

SUPERMARKET_ID = "comisariato_los_andes"
SUPERMARKET_NAME = "Comisariato Los Andes"
LOCATION_ID = "comisariato_los_andes_sps"
CITY = "San Pedro Sula"
COUNTRY = "HN"
SCOPE = "public_ecommerce_sps_store_1_pd"
STORE_ID = 1
STORE_NAME = "COMISARIATO LOS ANDES"
OFFICE_CODE = "00"
LOCATION_ONE_CODE = "COR"
LOCATION_TWO_CODE = "501"
PRODUCT_KEYS = {
    "availability", "brand", "category", "current_price", "ean", "is_promotion",
    "item_id", "presentation", "product_id", "reference", "reported_regular_price",
    "source_key", "source_key_type", "source_name",
}


def _decimal(value: object, reason: str, *, allow_none: bool = False) -> Decimal | None:
    if value is None and allow_none:
        return None
    if type(value) not in {int, float, str}:
        raise SnapshotError(reason)
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SnapshotError(reason) from exc
    if not amount.is_finite():
        raise SnapshotError(reason)
    return amount


def validate_snapshot_bytes(raw: bytes) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SnapshotError("los_andes_snapshot_json_invalid") from exc
    if not isinstance(data, dict):
        raise SnapshotError("los_andes_snapshot_json_invalid")

    expected = {
        "result": "success",
        "supermarket_id": SUPERMARKET_ID,
        "location_id": LOCATION_ID,
        "city": CITY,
        "currency": "HNL",
        "scope": SCOPE,
        "store_id": STORE_ID,
        "store_name": STORE_NAME,
        "office_code": OFFICE_CODE,
        "location_one_code": LOCATION_ONE_CODE,
        "location_two_code": LOCATION_TWO_CODE,
        "location_verified_same_run": True,
        "catalog_complete": True,
        "validation_passed": True,
    }
    for key, value in expected.items():
        if data.get(key) != value:
            raise SnapshotError(f"los_andes_snapshot_metadata_invalid:{key}")

    observed_at = data.get("observed_at_utc")
    if not isinstance(observed_at, str) or not observed_at.endswith("Z"):
        raise SnapshotError("los_andes_observed_at_invalid")

    rows = data.get("products")
    if not isinstance(rows, list) or not rows:
        raise SnapshotError("los_andes_products_invalid")
    reported = data.get("catalog_products_reported")
    if type(reported) is not int or reported <= 0:
        raise SnapshotError("los_andes_catalog_count_invalid")
    if (
        data.get("unique_products_extracted") != reported
        or data.get("membership_count") != reported
        or data.get("skus_extracted") != len(rows)
        or len(rows) != reported
        or data.get("skus_with_price") != len(rows)
    ):
        raise SnapshotError("los_andes_count_mismatch")

    details = data.get("source_details")
    if not isinstance(details, dict):
        raise SnapshotError("los_andes_source_details_invalid")

    identities: set[str] = set()
    promotion_count = 0
    not_promotion_count = 0
    for row in rows:
        if not isinstance(row, dict) or set(row) != PRODUCT_KEYS:
            raise SnapshotError("los_andes_product_schema_invalid")
        key = row.get("source_key")
        if not isinstance(key, str) or not key or key in identities:
            raise SnapshotError("los_andes_product_identity_invalid")
        identities.add(key)
        if (
            row.get("source_key_type") != "sku"
            or row.get("product_id") != key
            or row.get("item_id") != key
            or row.get("reference") != key
            # `ean` sólo puede ser el GTIN reconstruido desde el `code` (regla 19).
            or row.get("ean") != gtin_from_code(key)
            or not isinstance(row.get("source_name"), str)
            or not row["source_name"].strip()
            or row.get("availability") != "unknown"
        ):
            raise SnapshotError("los_andes_product_identity_invalid")

        current_minor = _minor(row.get("current_price"), True)
        if current_minor is None or current_minor <= 0:
            raise SnapshotError("los_andes_price_invalid")
        if type(row.get("is_promotion")) is not bool:
            raise SnapshotError("los_andes_promotion_invalid")

        detail = details.get(key)
        if not isinstance(detail, dict):
            raise SnapshotError("los_andes_source_details_invalid")
        if (
            detail.get("price_status") != "observed"
            or detail.get("availability_interpretation") != "not_proven"
            or detail.get("source_list_price") != "PD"
        ):
            raise SnapshotError("los_andes_source_semantics_invalid")
        signal = _decimal(
            detail.get("source_availibility_count"),
            "los_andes_availability_signal_invalid",
            allow_none=True,
        )
        if signal is not None and signal < 0:
            raise SnapshotError("los_andes_availability_signal_invalid")

        source_current = _decimal(detail.get("source_new_price"), "los_andes_source_price_invalid")
        source_mirror = _decimal(detail.get("source_price"), "los_andes_source_price_invalid")
        current = Decimal(str(row["current_price"]))
        if source_current != current or source_mirror != current:
            raise SnapshotError("los_andes_source_price_invalid")

        if row["is_promotion"] is True:
            regular_minor = _minor(row.get("reported_regular_price"), True)
            if regular_minor is None or regular_minor <= current_minor:
                raise SnapshotError("los_andes_promotion_invalid")
            regular = Decimal(str(row["reported_regular_price"]))
            old = _decimal(detail.get("source_old_price"), "los_andes_promotion_invalid")
            discount = _decimal(detail.get("source_discount"), "los_andes_promotion_invalid")
            if (
                old != regular
                or discount != regular - current
                or detail.get("regular_price_evidence") != ["oldPrice"]
            ):
                raise SnapshotError("los_andes_promotion_invalid")
            promotion_count += 1
        else:
            if (
                row.get("reported_regular_price") is not None
                or detail.get("source_old_price") is not None
                or detail.get("source_discount") is not None
                or detail.get("regular_price_evidence") != []
            ):
                raise SnapshotError("los_andes_nonpromotion_invalid")
            not_promotion_count += 1

    if set(details) != identities:
        raise SnapshotError("los_andes_source_details_invalid")
    membership = hashlib.sha256("\n".join(sorted(identities)).encode()).hexdigest()
    if data.get("membership_sha256") != membership:
        raise SnapshotError("los_andes_membership_hash_invalid")
    if data.get("availability_counts") != {"unknown": len(rows)}:
        raise SnapshotError("los_andes_availability_counts_invalid")
    if data.get("promotion_counts") != {
        "promotion": promotion_count,
        "not_promotion": not_promotion_count,
        "unknown": 0,
    }:
        raise SnapshotError("los_andes_promotion_counts_invalid")
    return data


def _preflight(
    database_url: str, auth_token: str, *, run_id: str
) -> dict[str, object] | None:
    requests = [
        {"type": "execute", "stmt": _stmt(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )},
        {"type": "execute", "stmt": _stmt(
            "SELECT name,country_code FROM supermarkets WHERE supermarket_id=?",
            (SUPERMARKET_ID,),
        )},
        {"type": "execute", "stmt": _stmt(
            "SELECT supermarket_id,city_name,country_code FROM locations WHERE location_id=?",
            (LOCATION_ID,),
        )},
        {"type": "execute", "stmt": _stmt(
            "SELECT location_id,run_status,source_json_sha256 FROM scrape_runs WHERE scrape_run_id=?",
            (run_id,),
        )},
        {"type": "close"},
    ]
    data = _pipeline(database_url, auth_token, requests)
    results = data.get("results")
    if not isinstance(results, list) or len(results) < 4:
        raise SnapshotError("los_andes_turso_preflight_invalid")
    _validate_table_names(str(row[0]) for row in _execute_rows(results[0]))
    supermarket = _execute_rows(results[1])
    if supermarket and supermarket != [[SUPERMARKET_NAME, COUNTRY]]:
        raise SnapshotError("los_andes_turso_supermarket_mismatch")
    location = _execute_rows(results[2])
    if location and location != [[SUPERMARKET_ID, CITY, COUNTRY]]:
        raise SnapshotError("los_andes_turso_location_mismatch")
    run = _execute_rows(results[3])
    if not run:
        return None
    if len(run) != 1:
        raise SnapshotError("turso_run_duplicate")
    return {"location_id": run[0][0], "run_status": run[0][1], "sha": run[0][2]}


def _register_scope(database_url: str, auth_token: str) -> None:
    steps = [
        ("begin", "BEGIN IMMEDIATE", ()),
        (
            "register_supermarket",
            "INSERT OR IGNORE INTO supermarkets(supermarket_id,name,country_code) VALUES(?,?,?)",
            (SUPERMARKET_ID, SUPERMARKET_NAME, COUNTRY),
        ),
        (
            "register_location",
            "INSERT OR IGNORE INTO locations(location_id,supermarket_id,city_name,country_code) VALUES(?,?,?,?)",
            (LOCATION_ID, SUPERMARKET_ID, CITY, COUNTRY),
        ),
        ("commit", "COMMIT", ()),
    ]
    _run_batch(database_url, auth_token, steps)
    data = _pipeline(
        database_url,
        auth_token,
        [
            {"type": "execute", "stmt": _stmt(
                "SELECT s.name,s.country_code,l.supermarket_id,l.city_name,l.country_code "
                "FROM supermarkets s JOIN locations l ON l.supermarket_id=s.supermarket_id "
                "WHERE s.supermarket_id=? AND l.location_id=?",
                (SUPERMARKET_ID, LOCATION_ID),
            )},
            {"type": "close"},
        ],
    )
    rows = _execute_rows(data["results"][0])
    if rows != [[SUPERMARKET_NAME, COUNTRY, SUPERMARKET_ID, CITY, COUNTRY]]:
        raise SnapshotError("los_andes_turso_scope_registration_failed")


def persist_snapshot(
    raw: bytes,
    *,
    database_url: str,
    auth_token: str,
    run_id: str,
    source_artifact_id: str | None = None,
) -> dict[str, object]:
    snapshot = validate_snapshot_bytes(raw)
    if not run_id.strip():
        raise SnapshotError("run_id_missing")
    if not auth_token.strip():
        raise SnapshotError("turso_auth_token_missing")
    digest = hashlib.sha256(raw).hexdigest()
    previous = _preflight(database_url, auth_token, run_id=run_id)
    if previous is not None:
        if (
            previous["location_id"] == LOCATION_ID
            and previous["run_status"] == "success"
            and previous["sha"] == digest
        ):
            return {
                "run_id": run_id,
                "location_id": LOCATION_ID,
                "source_json_sha256": digest,
                "replayed": True,
                "products_processed": 0,
                "history_opened": 0,
                "history_closed": 0,
                "history_unchanged": 0,
            }
        raise SnapshotError("run_id_conflict")

    _register_scope(database_url, auth_token)
    steps = _mutation_steps(
        _normalised_json(snapshot),
        location_id=LOCATION_ID,
        observed_at=str(snapshot["observed_at_utc"]),
        run_id=run_id,
        sku_count=len(snapshot["products"]),
        catalog_count=int(snapshot["catalog_products_reported"]),
        artifact_id=source_artifact_id,
        digest=digest,
        supermarket_id=SUPERMARKET_ID,
    )
    results = _run_batch(database_url, auth_token, steps)
    opened = _affected(results, steps, "open_history")
    closed = _affected(results, steps, "close_history")
    return {
        "run_id": run_id,
        "location_id": LOCATION_ID,
        "source_json_sha256": digest,
        "replayed": False,
        "products_processed": len(snapshot["products"]),
        "history_opened": opened,
        "history_closed": closed,
        "history_unchanged": len(snapshot["products"]) - opened,
    }


def verify_committed_run(
    *, database_url: str, auth_token: str, run_id: str, raw: bytes
) -> dict[str, object]:
    snapshot = validate_snapshot_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    data = _pipeline(
        database_url,
        auth_token,
        [
            {"type": "execute", "stmt": _stmt(
                "SELECT location_id,run_status,sku_count,catalog_product_count,source_json_sha256 "
                "FROM scrape_runs WHERE scrape_run_id=?",
                (run_id,),
            )},
            {"type": "execute", "stmt": _stmt(
                "SELECT COUNT(*) FROM (SELECT product_id FROM price_history "
                "WHERE supermarket_id=? AND location_id=? AND valid_to_utc IS NULL "
                "GROUP BY product_id HAVING COUNT(*)>1)",
                (SUPERMARKET_ID, LOCATION_ID),
            )},
            {"type": "execute", "stmt": _stmt(
                "SELECT COUNT(*) FROM price_history WHERE supermarket_id=? AND location_id=? AND valid_to_utc IS NULL",
                (SUPERMARKET_ID, LOCATION_ID),
            )},
            {"type": "close"},
        ],
    )
    results = data.get("results")
    if not isinstance(results, list) or len(results) < 3:
        raise SnapshotError("los_andes_turso_verify_invalid")
    run = _execute_rows(results[0])
    expected_run = [[
        LOCATION_ID,
        "success",
        snapshot["skus_extracted"],
        snapshot["catalog_products_reported"],
        digest,
    ]]
    if run != expected_run:
        raise SnapshotError(f"los_andes_turso_run_not_confirmed:{run}")
    duplicates = _execute_rows(results[1])
    if duplicates != [[0]]:
        raise SnapshotError("los_andes_turso_duplicate_open_periods")
    open_count = _execute_rows(results[2])
    if (
        len(open_count) != 1
        or len(open_count[0]) != 1
        or type(open_count[0][0]) is not int
        or open_count[0][0] < snapshot["skus_extracted"]
    ):
        raise SnapshotError(f"los_andes_turso_current_state_incomplete:{open_count}")
    return {
        "run": run[0],
        "duplicate_open_periods": 0,
        "open_price_history": open_count[0][0],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot_json", type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--source-artifact-id")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    url = os.environ.get("TURSO_DATABASE_URL", "")
    token = os.environ.get("TURSO_AUTH_TOKEN", "")
    if not url.strip() or not token.strip():
        raise SystemExit("TURSO_DATABASE_URL/TURSO_AUTH_TOKEN missing")
    raw = args.snapshot_json.read_bytes()
    try:
        if args.verify_only:
            result = verify_committed_run(
                database_url=url, auth_token=token, run_id=args.run_id, raw=raw
            )
        else:
            result = persist_snapshot(
                raw,
                database_url=url,
                auth_token=token,
                run_id=args.run_id,
                source_artifact_id=args.source_artifact_id,
            )
    except SnapshotError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
