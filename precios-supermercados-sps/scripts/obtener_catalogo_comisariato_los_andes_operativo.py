#!/usr/bin/env python3
"""Captura operativa del catálogo público SPS de Comisariato Los Andes.

El comando es deliberadamente fail-closed: solo ejecuta tráfico live con las dos
banderas explícitas, usa concurrencia 1, presupuesto duro de solicitudes, conserva
cada respuesta RAW con SHA-256 y permite como máximo un reintento por fallo de
transporte antes de exigir reconciliación offline.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from precios_supermercados.scrapers.comisariato_los_andes import (
    LOCATION_ID,
    LOCATION_TWO_CODE,
    OFFICE_CODE,
    PAGE_SIZE,
    STORE_ENDPOINT,
    STORE_ID,
    SUPERMARKET_ID,
    build_catalog_request,
    build_store_request,
    reconcile_capture,
)

MAX_REQUESTS = 400
MAX_RETRIES = 1
MAX_TOTAL_RETRIES = 10
DEFAULT_RETRIES = 0
DEFAULT_DELAY_SECONDS = 1.0
DEFAULT_TIMEOUT_SECONDS = 25.0


class LiveCaptureError(RuntimeError):
    pass


def _now_z() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _request_json(
    *,
    url: str,
    body: dict[str, object],
    output: Path,
    state: dict[str, float | int],
    delay_seconds: float,
    timeout_seconds: float,
    max_retries: int,
    opener: Callable[..., Any] = urllib.request.urlopen,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[dict[str, Any] | list[Any], dict[str, object]]:
    if max_retries not in {0, 1}:
        raise LiveCaptureError("retry_policy_invalid")

    retries_used = 0
    while True:
        if int(state["attempts"]) >= MAX_REQUESTS:
            raise LiveCaptureError("request_budget_exhausted")
        elapsed = time.monotonic() - float(state["last_request_at"])
        if state["last_request_at"] and elapsed < delay_seconds:
            sleeper(delay_seconds - elapsed)
        request = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode(),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "PreciosSupermercadosSPS/1.0 public-readonly",
            },
        )
        observed_at = _now_z()
        state["attempts"] = int(state["attempts"]) + 1
        state["last_request_at"] = time.monotonic()
        try:
            with opener(request, timeout=timeout_seconds) as response:
                status = int(response.status)
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise LiveCaptureError(f"http_error:{int(exc.code)}:{output.name}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if (
                retries_used >= max_retries
                or int(state["retries"]) >= MAX_TOTAL_RETRIES
            ):
                raise LiveCaptureError(f"network_error:{output.name}:{exc}") from exc
            retries_used += 1
            state["retries"] = int(state["retries"]) + 1
            continue

        if status not in {200, 201}:
            raise LiveCaptureError(f"http_status:{status}:{output.name}")
        output.write_bytes(raw)
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LiveCaptureError(f"json_invalid:{output.name}") from exc
        if not isinstance(payload, (dict, list)):
            raise LiveCaptureError(f"json_shape_invalid:{output.name}")
        state["requests"] = int(state["requests"]) + 1
        return payload, {
            "status": status,
            "retries": 0,
            "transport_retries": retries_used,
            "url": url,
            "request_body": body,
            "response_file": output.name,
            "response_sha256": hashlib.sha256(raw).hexdigest(),
            "response_bytes": len(raw),
            "observed_at_utc": observed_at,
        }


def capture_catalog(
    *,
    output: Path,
    raw_directory: Path,
    evidence_output: Path,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_RETRIES,
) -> dict[str, object]:
    if delay_seconds < 0.5:
        raise LiveCaptureError("delay_below_operational_floor")
    if timeout_seconds <= 0 or timeout_seconds > 60:
        raise LiveCaptureError("timeout_invalid")
    if max_retries not in {0, 1}:
        raise LiveCaptureError("retry_policy_invalid")

    shutil.rmtree(raw_directory, ignore_errors=True)
    raw_directory.mkdir(parents=True, exist_ok=True)
    state: dict[str, float | int] = {
        "requests": 0,
        "attempts": 0,
        "retries": 0,
        "last_request_at": 0.0,
    }
    pages: list[dict[str, object]] = []
    started = time.monotonic()

    store_payload, store_record = _request_json(
        url=STORE_ENDPOINT,
        body=build_store_request(),
        output=raw_directory / "store-evidence.json",
        state=state,
        delay_seconds=delay_seconds,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
    )
    if not isinstance(store_payload, list):
        raise LiveCaptureError("store_payload_shape_changed")

    def fetch_page(skip: int, filename: str) -> tuple[dict[str, Any], dict[str, object]]:
        url, body = build_catalog_request(skip, PAGE_SIZE)
        payload, record = _request_json(
            url=url,
            body=body,
            output=raw_directory / filename,
            state=state,
            delay_seconds=delay_seconds,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )
        if not isinstance(payload, dict):
            raise LiveCaptureError(f"catalog_payload_shape_changed:skip={skip}")
        record.update({"skip": skip, "take": PAGE_SIZE})
        return payload, record

    first, record = fetch_page(0, "page-00000.json")
    total = first.get("totalItems")
    total_pages = first.get("totalPages")
    if type(total) is not int or total <= 0 or type(total_pages) is not int or total_pages <= 0:
        raise LiveCaptureError("catalog_totals_invalid")
    expected_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    if total_pages != expected_pages:
        raise LiveCaptureError("total_pages_mismatch")
    if expected_pages + 2 > MAX_REQUESTS:
        raise LiveCaptureError("catalog_exceeds_request_budget")
    pages.append(record)

    for skip in range(PAGE_SIZE, total, PAGE_SIZE):
        payload, record = fetch_page(skip, f"page-{skip:05d}.json")
        if payload.get("totalItems") != total or payload.get("totalPages") != total_pages:
            raise LiveCaptureError(f"catalog_total_changed:skip={skip}")
        pages.append(record)

    final, final_record = fetch_page(0, "final-recheck.json")
    if final.get("totalItems") != total or final.get("totalPages") != total_pages:
        raise LiveCaptureError("catalog_total_changed_at_final_recheck")

    ledger = {
        "closed": True,
        "supermarket_id": SUPERMARKET_ID,
        "location_id": LOCATION_ID,
        "store_id": STORE_ID,
        "office_code": OFFICE_CODE,
        "location_two_code": LOCATION_TWO_CODE,
        "concurrency": 1,
        "max_requests": MAX_REQUESTS,
        "max_retries": max_retries,
        "delay_seconds": delay_seconds,
        "request_count": int(state["requests"]),
        "retry_count": int(state["retries"]),
        "transport_attempt_count": int(state["attempts"]),
        "store_evidence": store_record,
        "final_total_items": total,
        "final_recheck": final_record,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "pages": pages,
    }
    _write_json(raw_directory / "ledger.json", ledger)

    snapshot = reconcile_capture(raw_directory)
    raw_snapshot = json.dumps(
        snapshot, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(raw_snapshot)

    evidence = {
        "result": "success",
        "request_count": ledger["request_count"],
        "retry_count": ledger["retry_count"],
        "transport_attempt_count": ledger["transport_attempt_count"],
        "elapsed_seconds": ledger["elapsed_seconds"],
        "catalog_products_reported": snapshot["catalog_products_reported"],
        "unique_products_extracted": snapshot["unique_products_extracted"],
        "skus_with_price": snapshot["skus_with_price"],
        "availability_counts": snapshot["availability_counts"],
        "promotion_counts": snapshot["promotion_counts"],
        "membership_sha256": snapshot["membership_sha256"],
        "snapshot_sha256": hashlib.sha256(raw_snapshot).hexdigest(),
        "snapshot_bytes": len(raw_snapshot),
        "store": {
            "store_id": snapshot["store_id"],
            "store_name": snapshot["store_name"],
            "office_code": snapshot["office_code"],
            "location_one_code": snapshot["location_one_code"],
            "location_two_code": snapshot["location_two_code"],
        },
        "errors": [],
    }
    _write_json(evidence_output, evidence)
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-read-only", action="store_true")
    parser.add_argument("--allow-full-catalog", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--raw-directory", type=Path, required=True)
    parser.add_argument("--evidence-output", type=Path, required=True)
    parser.add_argument("--delay-seconds", type=float, default=DEFAULT_DELAY_SECONDS)
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--max-retries", type=int, default=DEFAULT_RETRIES)
    args = parser.parse_args()
    if not args.live_read_only:
        raise SystemExit("live_read_only_authorization_required")
    if not args.allow_full_catalog:
        raise SystemExit("full_catalog_authorization_required")
    try:
        evidence = capture_catalog(
            output=args.output,
            raw_directory=args.raw_directory,
            evidence_output=args.evidence_output,
            delay_seconds=args.delay_seconds,
            timeout_seconds=args.timeout_seconds,
            max_retries=args.max_retries,
        )
    except Exception as exc:
        args.evidence_output.parent.mkdir(parents=True, exist_ok=True)
        _write_json(
            args.evidence_output,
            {
                "result": "failure",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        raise SystemExit(str(exc)) from exc
    print(json.dumps(evidence, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
