#!/usr/bin/env python3
"""Captura read-only completa de SPS 6603 y Florencia 6602 en PriceSmart Honduras."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import unicodedata
import urllib.error
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from precios_supermercados.scrapers.pricesmart import (
    CLUBS,
    COMPLETE_CATALOG_SCOPE,
    ENDPOINT,
    parse_catalog_memberships,
)

ROWS = 200
DEFAULT_DELAY = 0.5
DEFAULT_MAX_RETRIES = 2
MAX_RETRIES_HARD = 2
MAX_REQUESTS_HARD = 80
RETRYABLE_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})
PUBLIC_BLOOMREACH_AUTH_KEY = "ev7libhybjg5h1d1"
ACCOUNT_ID = "7024"
DOMAIN_KEY = "pricesmart_bloomreach_io_es"
VIEW_ID = "HN"
UA = "Mozilla/5.0 (compatible; PreciosSupermercadosSPS-PriceSmart/1.0; read-only)"
ROOTS = (
    ("S10D45", "Productos de temporada"),
    ("G10D03", "Alimentos"),
    ("H30D22", "Hogar"),
    ("H20D09", "Salud y belleza"),
    ("G10D08014", "Licor, cerveza y vino"),
    ("P10D51", "Mascotas"),
    ("B10D27", "Bebé"),
    ("H10D21", "Ferretería y mejoras al hogar"),
    ("S30D26", "Deportes y fitness"),
    ("O20D30", "Exteriores"),
    ("E10D24", "Electrónicos"),
    ("S20D23", "Electrodomésticos"),
    ("C10D29", "Computadoras, tablets y accesorios"),
    ("M10D43", "Línea blanca"),
    ("F10D40", "Moda y accesorios"),
    ("F20D27", "Muebles"),
    ("O10D25", "Oficina"),
    ("R10D22", "Suministros para restaurantes"),
    ("A10D20", "Automotriz"),
    ("T10D46", "Juguetes y juegos"),
    ("L10D22", "Equipaje"),
    ("U10D72", "Óptica"),
    ("U11D13", "Audiología"),
    ("T20D42", "Películas, música y libros"),
    ("V10D79", "Tarjetas de Regalo"),
    ("J10D44", "Joyería y relojes"),
)


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def category_url(key: str, name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^A-Za-z0-9]+", "-", ascii_name).strip("-")
    return f"https://www.pricesmart.com/es-hn/categoria/{slug}-{key}/{key}"


def fields_for_club(club: str) -> str:
    if club not in CLUBS:
        raise RuntimeError("club_invalid")
    return ",".join((
        "pid", "title", "price", "thumb_image", "brand", "slug", "skuid",
        "currency", "fractionDigits", "master_sku", "sold_by_weight_HN", "weight_HN",
        "weight_uom_description_HN", "sign_price_HN", "price_per_uom_HN", "uom_description_HN",
        f"saving_amount_HN_{club}", f"original_price_without_saving_HN_{club}",
        f"availability_HN_{club}", f"price_HN_{club}", f"inventory_HN_{club}", "promoid_HN",
    ))


def query_for(club: str, key: str, name: str, start: int) -> dict:
    return {
        "url": category_url(key, name),
        "start": start,
        "q": key,
        "fq": [],
        "search_type": "category",
        "rows": ROWS,
        "account_id": ACCOUNT_ID,
        "auth_key": PUBLIC_BLOOMREACH_AUTH_KEY,
        "request_id": int(time.time() * 1000),
        "domain_key": DOMAIN_KEY,
        "fl": fields_for_club(club),
        "view_id": VIEW_ID,
    }


def _retryable(status: int | None, error: str | None) -> bool:
    if status in RETRYABLE_HTTP_STATUSES:
        return True
    if status is not None or not error:
        return False
    return error.startswith(("URLError:", "TimeoutError:", "ConnectionError:", "OSError:"))


class Capture:
    def __init__(self, raw_dir: Path, delay: float, max_requests: int, max_retries: int = DEFAULT_MAX_RETRIES) -> None:
        self.raw_dir = raw_dir
        self.delay = delay
        self.max_requests = max_requests
        self.max_retries = max_retries
        self.records: list[dict] = []
        self.retry_count = 0
        self.started = time.monotonic()

    def post(self, club: str, key: str, name: str, start_offset: int) -> tuple[dict, dict]:
        query = query_for(club, key, name, start_offset)
        body = json.dumps([query], ensure_ascii=False, separators=(",", ":")).encode()
        for attempt in range(self.max_retries + 1):
            if len(self.records) >= self.max_requests:
                raise RuntimeError("request_budget_exceeded")
            request = urllib.request.Request(
                ENDPOINT,
                data=body,
                headers={
                    "User-Agent": UA,
                    "Accept": "application/json, text/plain, */*",
                    "Content-Type": "application/json",
                    "Referer": query["url"],
                },
                method="POST",
            )
            response_body = b""
            status = None
            response_url = None
            error = None
            started = time.monotonic()
            try:
                with urllib.request.urlopen(request, timeout=45) as response:
                    status = response.status
                    response_url = response.geturl()
                    response_body = response.read()
            except urllib.error.HTTPError as exc:
                status = exc.code
                response_url = exc.geturl()
                response_body = exc.read()
                error = f"http_{exc.code}"
            except Exception as exc:  # noqa: BLE001
                error = f"{type(exc).__name__}:{exc}"
            if response_url and response_url.rstrip("/") != ENDPOINT.rstrip("/"):
                error = error or "endpoint_redirected"
            digest = hashlib.sha256(response_body).hexdigest() if response_body else None
            retry_suffix = "" if attempt == 0 else f"__retry-{attempt}"
            target = self.raw_dir / club / f"{key}__{start_offset:06d}{retry_suffix}.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            if response_body:
                target.write_bytes(response_body)
            record = {
                "index": len(self.records) + 1,
                "attempt": attempt + 1,
                "club": club,
                "category_key": key,
                "category_name": name,
                "start": start_offset,
                "rows": ROWS,
                "method": "POST",
                "url": ENDPOINT,
                "http_status": status,
                "response_url": response_url,
                "observed_at": utc_now(),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "request_body_sha256": hashlib.sha256(body).hexdigest(),
                "response_sha256": digest,
                "response_bytes": len(response_body),
                "file": str(target) if response_body else None,
                "error": error,
            }
            self.records.append(record)
            if self.delay:
                time.sleep(self.delay)
            if status == 200 and error is None:
                try:
                    payload = json.loads(response_body)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(f"response_not_json:{club}:{key}:{start_offset}") from exc
                if not isinstance(payload, dict) or not isinstance(payload.get("response"), dict):
                    raise RuntimeError(f"response_shape_invalid:{club}:{key}:{start_offset}")
                return payload, record
            if attempt >= self.max_retries or not _retryable(status, error):
                raise RuntimeError(f"request_failed:{club}:{key}:{start_offset}:{status}:{error}")
            self.retry_count += 1
        raise AssertionError("retry_loop_exhausted")


def capture_club(capture: Capture, club: str) -> dict:
    groups: list[dict] = []
    root_counts: dict[str, int] = {}
    evidence: list[dict] = []
    for key, name in ROOTS:
        first, first_record = capture.post(club, key, name, 0)
        response = first["response"]
        total = response.get("numFound")
        docs = response.get("docs")
        if type(total) is not int or total < 0 or response.get("start") != 0 or not isinstance(docs, list):
            raise RuntimeError(f"root_response_invalid:{club}:{key}")
        if len(docs) != min(ROWS, total):
            raise RuntimeError(f"root_first_page_incomplete:{club}:{key}")
        root_counts[key] = total
        evidence.append({"category_key": key, "start": 0, "sha256": first_record["response_sha256"], "observed_at": first_record["observed_at"]})
        documents = list(docs)
        seen = {str(doc.get("pid")) for doc in docs if isinstance(doc, dict)}
        if len(seen) != len(docs) or "None" in seen:
            raise RuntimeError(f"root_product_id_invalid:{club}:{key}:0")
        for start_offset in range(ROWS, total, ROWS):
            payload, record = capture.post(club, key, name, start_offset)
            page = payload["response"]
            page_docs = page.get("docs")
            expected_count = min(ROWS, total - start_offset)
            if page.get("numFound") != total or page.get("start") != start_offset or not isinstance(page_docs, list) or len(page_docs) != expected_count:
                raise RuntimeError(f"root_page_incomplete:{club}:{key}:{start_offset}")
            ids = {str(doc.get("pid")) for doc in page_docs if isinstance(doc, dict)}
            if len(ids) != len(page_docs) or "None" in ids or seen.intersection(ids):
                raise RuntimeError(f"root_page_overlap:{club}:{key}:{start_offset}")
            seen.update(ids)
            documents.extend(page_docs)
            evidence.append({"category_key": key, "start": start_offset, "sha256": record["response_sha256"], "observed_at": record["observed_at"]})
        if len(documents) != total:
            raise RuntimeError(f"root_membership_incomplete:{club}:{key}")
        if total:
            groups.append({"category_id": key, "category_name": name, "documents": documents})

    rows, details, parser_summary = parse_catalog_memberships(groups, club)
    product_ids = {row["product_id"] for row in rows}
    sku_ids = {row["source_key"] for row in rows}
    if len(sku_ids) != len(rows):
        raise RuntimeError(f"sku_membership_duplicate:{club}")
    observed = [datetime.fromisoformat(item["observed_at"].replace("Z", "+00:00")) for item in evidence]
    config = CLUBS[club]
    rows.sort(key=lambda row: row["source_key"])
    return {
        "result": "success",
        "supermarket_id": "pricesmart",
        "location_id": config["location_id"],
        "city": config["city"],
        "club_id": club,
        "club_name": config["name"],
        "channel_id": config["channel_id"],
        "currency": "HNL",
        "scope": COMPLETE_CATALOG_SCOPE,
        "category_id": "ALL_ROOTS",
        "category_name": "Todos los departamentos",
        "catalog_complete": True,
        "validation_passed": True,
        "location_verified_same_run": True,
        "observation_started_at_utc": min(observed).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "observed_at_utc": max(observed).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "catalog_products_reported": len(product_ids),
        "unique_products_extracted": len(product_ids),
        "skus_extracted": len(rows),
        "skus_with_price": sum(row["current_price"] is not None for row in rows),
        "membership_count": len(product_ids),
        "membership_sha256": hashlib.sha256("\n".join(sorted(product_ids)).encode()).hexdigest(),
        "sku_membership_sha256": hashlib.sha256("\n".join(sorted(sku_ids)).encode()).hexdigest(),
        "availability_counts": dict(Counter(row["availability"] for row in rows)),
        "promotion_counts": {
            "true": sum(row["is_promotion"] is True for row in rows),
            "false": sum(row["is_promotion"] is False for row in rows),
            "unknown_unpriced": sum(row["is_promotion"] is None for row in rows),
        },
        "root_membership_count": parser_summary["root_memberships"],
        "root_sku_membership_count": parser_summary["sku_memberships"],
        "root_counts": root_counts,
        "products": rows,
        "source_details": details,
        "page_evidence": evidence,
        "binding_evidence": {
            "club_id": club,
            "field_suffix": f"_HN_{club}",
            "view_id": VIEW_ID,
            "endpoint": ENDPOINT,
            "cookies_required": False,
            "authorization_header_required": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-read-only", action="store_true")
    parser.add_argument("--allow-full-catalog", action="store_true")
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--raw-directory", type=Path, required=True)
    parser.add_argument("--evidence-output", type=Path, required=True)
    parser.add_argument("--delay-seconds", type=float, default=DEFAULT_DELAY)
    parser.add_argument("--max-requests", type=int, default=MAX_REQUESTS_HARD)
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES)
    args = parser.parse_args()
    if not args.live_read_only or not args.allow_full_catalog:
        raise SystemExit("explicit_live_full_catalog_authorization_required")
    if args.delay_seconds < 0.25:
        raise SystemExit("delay_too_small")
    if args.max_requests <= 0 or args.max_requests > MAX_REQUESTS_HARD:
        raise SystemExit("request_budget_invalid")
    if args.max_retries < 0 or args.max_retries > MAX_RETRIES_HARD:
        raise SystemExit("retry_budget_invalid")

    args.output_directory.mkdir(parents=True, exist_ok=True)
    args.raw_directory.mkdir(parents=True, exist_ok=True)
    args.evidence_output.parent.mkdir(parents=True, exist_ok=True)
    capture = Capture(args.raw_directory, args.delay_seconds, args.max_requests, args.max_retries)
    snapshots: list[dict] = []
    error = None
    try:
        for club in ("6603", "6602"):
            snapshots.append(capture_club(capture, club))
        if snapshots[0]["membership_sha256"] != snapshots[1]["membership_sha256"] or snapshots[0]["sku_membership_sha256"] != snapshots[1]["sku_membership_sha256"]:
            raise RuntimeError("club_catalog_membership_mismatch")
        suffix = {"6603": "sps", "6602": "tgu"}
        for snapshot in snapshots:
            target = args.output_directory / f"snapshot-pricesmart-{suffix[snapshot['club_id']]}.json"
            target.write_text(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}:{exc}"

    evidence = {
        "result": "success" if error is None else "failed",
        "scope": COMPLETE_CATALOG_SCOPE,
        "clubs": ["6603", "6602"],
        "excluded_club": "6604",
        "root_count": len(ROOTS),
        "concurrency": 1,
        "automatic_retry_count": capture.retry_count,
        "max_retries_per_request": args.max_retries,
        "request_budget": args.max_requests,
        "request_count": len(capture.records),
        "elapsed_seconds": round(time.monotonic() - capture.started, 3),
        "stores": [
            {
                "location_id": snapshot["location_id"],
                "club_id": snapshot["club_id"],
                "catalog_products_reported": snapshot["catalog_products_reported"],
                "skus_extracted": snapshot["skus_extracted"],
                "skus_with_price": snapshot["skus_with_price"],
            }
            for snapshot in snapshots
        ],
        "error": error,
    }
    args.evidence_output.write_text(json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    (args.evidence_output.parent / "requests.json").write_text(
        json.dumps({"records": capture.records}, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
    )
    if error is not None:
        raise SystemExit(error)
    print(json.dumps(evidence, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
