#!/usr/bin/env python3
"""Valida y persiste snapshots completos de Paiz por contexto TGU en Turso."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from actualizar_mvp_sqlite_la_colonia import SnapshotError, _minor
from actualizar_mvp_turso_la_colonia import (
    _affected,
    _execute_rows,
    _mutation_steps,
    _pipeline,
    _run_batch,
    _stmt,
    _validate_table_names,
    membership_keys_if_enabled,
    membership_summary,
)
from migrar_mvp_paiz import schema_ready_sql

SUPERMARKET_ID = "paiz"
SUPERMARKET_NAME = "Paiz"
COUNTRY = "HN"
SOURCE_COUNTRY = "HND"
SCOPE = "public_ecommerce_selected_store_not_universal_city_price"
SALES_CHANNEL = "2"
LOCATIONS = {
    "paiz_tgu_multiplaza": {
        "city": "Tegucigalpa",
        "seller_id": "walmarthnsp633",
        "store_name": "Paiz Multiplaza",
    },
    "paiz_tgu_proceres": {
        "city": "Tegucigalpa",
        "seller_id": "walmarthnsp4010",
        "store_name": "Paiz Próceres",
    },
}
PRODUCT_KEYS = {
    "availability", "brand", "category", "current_price", "ean", "is_promotion",
    "item_id", "presentation", "product_id", "reference", "reported_regular_price",
    "source_key", "source_key_type", "source_name",
}


def _region_id(selector: str) -> str:
    return base64.b64encode(("SW#" + selector).encode()).decode()


def _validate_page_evidence(value: object, *, selector: str) -> None:
    if not isinstance(value, list) or not value:
        raise SnapshotError("paiz_page_evidence_missing")
    expected_region = _region_id(selector)
    for entry in value:
        if not isinstance(entry, dict):
            raise SnapshotError("paiz_page_evidence_invalid")
        url = entry.get("url")
        digest = entry.get("sha256")
        observed = entry.get("observed_at")
        if not isinstance(url, str) or not url:
            raise SnapshotError("paiz_page_evidence_invalid")
        parsed = urlsplit(url)
        query = parse_qs(parsed.query)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "www.paiz.com.hn"
            or f"/accesscontrollist/{selector}/" not in parsed.path
            or query.get("regionId") != [expected_region]
            or query.get("sc") != [SALES_CHANNEL]
            or query.get("country") != [SOURCE_COUNTRY]
        ):
            raise SnapshotError("paiz_context_evidence_invalid")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(ch not in "0123456789abcdef" for ch in digest.lower())
            or not isinstance(observed, str)
            or not observed.endswith("Z")
        ):
            raise SnapshotError("paiz_page_evidence_invalid")


def validate_snapshot_bytes(raw: bytes) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SnapshotError("paiz_snapshot_json_invalid") from exc
    if not isinstance(data, dict):
        raise SnapshotError("paiz_snapshot_json_invalid")
    location_id = data.get("location_id")
    if location_id not in LOCATIONS:
        raise SnapshotError("paiz_location_invalid")
    meta = LOCATIONS[str(location_id)]
    selector = str(meta["seller_id"])
    expected = {
        "result": "success",
        "supermarket_id": SUPERMARKET_ID,
        "location_id": location_id,
        "city": meta["city"],
        "currency": "HNL",
        "scope": SCOPE,
        "seller_id": selector,
        "store_name": meta["store_name"],
        "region_id": _region_id(selector),
        "sales_channel": SALES_CHANNEL,
        "catalog_complete": True,
        "validation_passed": True,
        "location_verified_same_run": True,
    }
    for key, value in expected.items():
        if data.get(key) != value:
            raise SnapshotError(f"paiz_snapshot_metadata_invalid:{key}")
    observed = data.get("observed_at_utc")
    if not isinstance(observed, str) or not observed.endswith("Z"):
        raise SnapshotError("paiz_observed_at_invalid")
    _validate_page_evidence(data.get("page_evidence"), selector=selector)

    rows = data.get("products")
    details = data.get("source_details")
    if not isinstance(rows, list) or not rows or not isinstance(details, dict):
        raise SnapshotError("paiz_products_invalid")
    reported = data.get("catalog_products_reported")
    if type(reported) is not int or reported <= 0:
        raise SnapshotError("paiz_catalog_count_invalid")
    source_products: set[str] = set()
    identities: set[str] = set()
    priced = 0
    availability_counts: dict[str, int] = {}
    promotion_counts = {"promotion": 0, "not_promotion": 0, "unknown": 0}

    for row in rows:
        if not isinstance(row, dict) or set(row) != PRODUCT_KEYS:
            raise SnapshotError("paiz_product_schema_invalid")
        sku = row.get("source_key")
        pid = row.get("product_id")
        if (
            row.get("source_key_type") != "item_id"
            or not isinstance(sku, str) or not sku.isdigit() or int(sku) <= 0
            or row.get("item_id") != sku
            or sku in identities
            or not isinstance(pid, str) or not pid.isdigit() or int(pid) <= 0
            or not isinstance(row.get("source_name"), str) or not row["source_name"].strip()
        ):
            raise SnapshotError("paiz_product_identity_invalid")
        identities.add(sku)
        source_products.add(pid)
        detail = details.get(sku)
        if not isinstance(detail, dict):
            raise SnapshotError("paiz_source_details_invalid")
        offer_seller = detail.get("seller_id")
        if offer_seller is not None and (not isinstance(offer_seller, str) or not offer_seller):
            raise SnapshotError("paiz_offer_seller_invalid")
        quantity = detail.get("available_quantity_signal")
        if quantity is not None and (type(quantity) not in {int, float} or quantity < 0):
            raise SnapshotError("paiz_availability_evidence_invalid")
        expected_availability = "unknown" if quantity is None else "in_stock" if quantity > 0 else "out_of_stock"
        if row.get("availability") != expected_availability:
            raise SnapshotError("paiz_availability_evidence_invalid")
        availability_counts[expected_availability] = availability_counts.get(expected_availability, 0) + 1

        current = row.get("current_price")
        regular = row.get("reported_regular_price")
        if current is None:
            if (
                row.get("availability") != "out_of_stock"
                or regular is not None
                or row.get("is_promotion") is not None
                or detail.get("price_status") != "unavailable_zero_offer"
                or quantity != 0
                or detail.get("source_price") != "0.00"
                or detail.get("source_list_price") != "0.00"
            ):
                raise SnapshotError("paiz_unpriced_offer_invalid")
            promotion_counts["unknown"] += 1
        else:
            current_minor = _minor(current, True)
            regular_minor = _minor(regular, True)
            if (
                current_minor is None or current_minor <= 0
                or regular_minor is None or regular_minor < current_minor
                or row.get("is_promotion") is not (regular_minor > current_minor)
                or detail.get("price_status") != "observed"
                or detail.get("source_price") != current
                or detail.get("source_list_price") != regular
            ):
                raise SnapshotError("paiz_price_evidence_invalid")
            priced += 1
            promotion_counts["promotion" if row["is_promotion"] else "not_promotion"] += 1

    if set(details) != identities:
        raise SnapshotError("paiz_source_details_invalid")
    if (
        data.get("skus_extracted") != len(rows)
        or data.get("skus_with_price") != priced
        or data.get("unique_products_extracted") != reported
        or data.get("membership_count") != reported
        or len(source_products) != reported
    ):
        raise SnapshotError("paiz_count_mismatch")
    membership = hashlib.sha256("\n".join(sorted(source_products)).encode()).hexdigest()
    if data.get("membership_sha256") != membership:
        raise SnapshotError("paiz_membership_hash_invalid")
    if data.get("availability_counts") != availability_counts:
        raise SnapshotError("paiz_availability_counts_invalid")
    if data.get("promotion_counts") != promotion_counts:
        raise SnapshotError("paiz_promotion_counts_invalid")
    return data


def _normalised_json(snapshot: dict[str, Any]) -> str:
    rows = []
    for row in snapshot["products"]:
        rows.append({
            "source_key_type": row["source_key_type"],
            "source_key": row["source_key"],
            "source_catalog_product_id": row["product_id"],
            "source_item_id": row["item_id"],
            "reference": row["reference"],
            "ean": row["ean"],
            "name": row["source_name"],
            "brand": row["brand"],
            "presentation": row["presentation"],
            "category": row["category"],
            "current_price_minor": _minor(row["current_price"], False),
            "reported_regular_price_minor": _minor(row["reported_regular_price"], False),
            "is_promotion": None if row["is_promotion"] is None else int(row["is_promotion"]),
            "availability": row["availability"],
        })
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":"))


def _preflight(url: str, token: str, *, location_id: str, run_id: str) -> dict[str, object] | None:
    data = _pipeline(
        url,
        token,
        [
            {"type": "execute", "stmt": _stmt("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")},
            {"type": "execute", "stmt": _stmt("SELECT sql FROM sqlite_master WHERE type='table' AND name='price_history'")},
            {"type": "execute", "stmt": _stmt("SELECT name,country_code FROM supermarkets WHERE supermarket_id=?", (SUPERMARKET_ID,))},
            {"type": "execute", "stmt": _stmt("SELECT supermarket_id,city_name,country_code FROM locations WHERE location_id=?", (location_id,))},
            {"type": "execute", "stmt": _stmt("SELECT location_id,run_status,source_json_sha256 FROM scrape_runs WHERE scrape_run_id=?", (run_id,))},
            {"type": "close"},
        ],
    )
    results = data.get("results")
    if not isinstance(results, list) or len(results) < 5:
        raise SnapshotError("paiz_turso_preflight_invalid")
    _validate_table_names(str(row[0]) for row in _execute_rows(results[0]))
    ddl = _execute_rows(results[1])
    if len(ddl) != 1 or not schema_ready_sql(ddl[0][0]):
        raise SnapshotError("paiz_schema_migration_required")
    supermarket = _execute_rows(results[2])
    if supermarket and supermarket != [[SUPERMARKET_NAME, COUNTRY]]:
        raise SnapshotError("paiz_turso_supermarket_mismatch")
    location = _execute_rows(results[3])
    expected_location = [[SUPERMARKET_ID, LOCATIONS[location_id]["city"], COUNTRY]]
    if location and location != expected_location:
        raise SnapshotError("paiz_turso_location_mismatch")
    run = _execute_rows(results[4])
    if not run:
        return None
    if len(run) != 1:
        raise SnapshotError("turso_run_duplicate")
    return {"location_id": run[0][0], "run_status": run[0][1], "sha": run[0][2]}


def persist_snapshot(raw: bytes, *, database_url: str, auth_token: str, run_id: str, source_artifact_id: str | None = None) -> dict[str, object]:
    snapshot = validate_snapshot_bytes(raw)
    if not run_id.strip():
        raise SnapshotError("run_id_missing")
    if not auth_token.strip():
        raise SnapshotError("turso_auth_token_missing")
    location_id = str(snapshot["location_id"])
    digest = hashlib.sha256(raw).hexdigest()
    previous = _preflight(database_url, auth_token, location_id=location_id, run_id=run_id)
    if previous is not None:
        if previous == {"location_id": location_id, "run_status": "success", "sha": digest}:
            return {
                "run_id": run_id, "location_id": location_id, "source_json_sha256": digest,
                "replayed": True, "products_processed": 0, "history_opened": 0,
                "history_closed": 0, "history_unchanged": 0,
            }
        raise SnapshotError("run_id_conflict")

    membership_keys = membership_keys_if_enabled(snapshot)
    steps = _mutation_steps(
        _normalised_json(snapshot),
        location_id=location_id,
        observed_at=str(snapshot["observed_at_utc"]),
        run_id=run_id,
        sku_count=len(snapshot["products"]),
        catalog_count=int(snapshot["catalog_products_reported"]),
        artifact_id=source_artifact_id,
        digest=digest,
        supermarket_id=SUPERMARKET_ID,
        membership_keys=membership_keys,
    )
    begin = next(i for i, step in enumerate(steps) if step[0] == "begin")
    city = str(LOCATIONS[location_id]["city"])
    steps[begin + 1:begin + 1] = [
        ("register_supermarket", "INSERT OR IGNORE INTO supermarkets VALUES(?,?,?)", (SUPERMARKET_ID, SUPERMARKET_NAME, COUNTRY)),
        ("register_location", "INSERT OR IGNORE INTO locations VALUES(?,?,?,?)", (location_id, SUPERMARKET_ID, city, COUNTRY)),
        ("guard_registered_scope", """INSERT INTO guard_ok SELECT CASE WHEN COUNT(*)=1 THEN 0 ELSE 1 END
            FROM locations l JOIN supermarkets s ON s.supermarket_id=l.supermarket_id
            WHERE l.location_id=? AND l.supermarket_id=? AND l.city_name=? AND l.country_code=?
              AND s.name=? AND s.country_code=?""", (location_id, SUPERMARKET_ID, city, COUNTRY, SUPERMARKET_NAME, COUNTRY)),
    ]
    results = _run_batch(database_url, auth_token, steps)
    opened = _affected(results, steps, "open_history")
    closed = _affected(results, steps, "close_history")
    return {
        "run_id": run_id,
        "location_id": location_id,
        "source_json_sha256": digest,
        "replayed": False,
        "products_processed": len(snapshot["products"]),
        "history_opened": opened,
        "history_closed": closed,
        "history_unchanged": len(snapshot["products"]) - opened,
        **membership_summary(membership_keys),
    }


def verify_committed_run(*, database_url: str, auth_token: str, run_id: str, raw: bytes) -> dict[str, object]:
    snapshot = validate_snapshot_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    location_id = str(snapshot["location_id"])
    data = _pipeline(
        database_url,
        auth_token,
        [
            {"type": "execute", "stmt": _stmt("SELECT location_id,run_status,sku_count,catalog_product_count,source_json_sha256 FROM scrape_runs WHERE scrape_run_id=?", (run_id,))},
            {"type": "execute", "stmt": _stmt("SELECT COUNT(*) FROM (SELECT product_id FROM price_history WHERE supermarket_id=? AND location_id=? AND valid_to_utc IS NULL GROUP BY product_id HAVING COUNT(*)>1)", (SUPERMARKET_ID, location_id))},
            {"type": "execute", "stmt": _stmt("SELECT COUNT(*) FROM price_history WHERE supermarket_id=? AND location_id=? AND valid_to_utc IS NULL", (SUPERMARKET_ID, location_id))},
            {"type": "close"},
        ],
    )
    results = data.get("results")
    if not isinstance(results, list) or len(results) < 3:
        raise SnapshotError("paiz_turso_verify_invalid")
    run = _execute_rows(results[0])
    expected_run = [[location_id, "success", snapshot["skus_extracted"], snapshot["catalog_products_reported"], digest]]
    duplicates = _execute_rows(results[1])
    open_count = _execute_rows(results[2])
    if run != expected_run:
        raise SnapshotError(f"paiz_turso_run_not_confirmed:{run}")
    if duplicates != [[0]]:
        raise SnapshotError("paiz_turso_duplicate_open_periods")
    if len(open_count) != 1 or type(open_count[0][0]) is not int or open_count[0][0] < snapshot["skus_extracted"]:
        raise SnapshotError(f"paiz_turso_current_state_incomplete:{open_count}")
    return {"run": run[0], "duplicate_open_periods": 0, "open_price_history": open_count[0][0]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot_json", type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--source-artifact-id")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    url = os.environ.get("TURSO_DATABASE_URL", "")
    token = os.environ.get("TURSO_AUTH_TOKEN", "")
    if not url.strip() or not token.strip():
        raise SystemExit("TURSO_DATABASE_URL/TURSO_AUTH_TOKEN missing")
    raw = args.snapshot_json.read_bytes()
    try:
        result = persist_snapshot(raw, database_url=url, auth_token=token, run_id=args.run_id, source_artifact_id=args.source_artifact_id)
        if args.verify:
            result["verification"] = verify_committed_run(database_url=url, auth_token=token, run_id=args.run_id, raw=raw)
    except SnapshotError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
