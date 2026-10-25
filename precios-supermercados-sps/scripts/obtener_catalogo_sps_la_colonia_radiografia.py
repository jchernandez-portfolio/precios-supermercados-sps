#!/usr/bin/env python3
"""Obtiene el catálogo completo de La Colonia para San Pedro Sula en una sola sesión.

Primero reproduce exactamente la radiografía ya validada para seleccionar SPS y
confirmar el fingerprint canónico de ubicación. Luego ejecuta productSearchV3 de
forma secuencial en el mismo BrowserContext. Las primeras páginas funcionan como
preflight de paginación; si son coherentes, el mismo run continúa hasta completar
el catálogo. No escribe en Google Sheets ni concede autoridad productiva.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import probar_muestra_sps_la_colonia as passive  # noqa: E402
import probar_muestra_sps_la_colonia_radiografia as sample  # noqa: E402
from precios_supermercados.diagnostics import la_colonia_location_binding_capture as radiography  # noqa: E402
from precios_supermercados.diagnostics.la_colonia_sps_context_diagnostic import (  # noqa: E402
    DiagnosticSafetyError,
    launch_compatible_chromium,
)
from precios_supermercados.diagnostics.location_binding_dom_controls import (  # noqa: E402
    LocationControlResolutionError,
)
from precios_supermercados.location_binding_radiography import (  # noqa: E402
    analyze_location_binding,
    report_dict,
)
from precios_supermercados.scrapers.base import StructureChangedError  # noqa: E402
from precios_supermercados.scrapers.la_colonia import LaColoniaExtractor  # noqa: E402
from precios_supermercados.scrapers.la_colonia_graphql import build_product_search_url  # noqa: E402

DEFAULT_PAGE_SIZE = 50
DEFAULT_STAGED_PAGES = 3
DEFAULT_MAX_CATALOG_PAGES = 250
DEFAULT_DELAY_SECONDS = 1.5
EXPLICIT_REQUEST_TIMEOUT_MS = 20_000


class FullCatalogError(RuntimeError):
    def __init__(self, reason: str, *, diagnostic: Mapping[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.diagnostic = dict(diagnostic or {})


def _utc_text(value: datetime | None = None) -> str:
    current = value or datetime.now(UTC)
    return current.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_failure(reason: str, diagnostic: Mapping[str, Any]) -> dict[str, Any]:
    artifact = {
        "schema_version": "1",
        "result": "stopped",
        "reason": reason,
        "supermarket_id": "la_colonia",
        "location_id": passive.MVP_LOCATION_ID,
        "city": passive.TARGET_CITY,
        "capture_strategy": "radiography_same_context_full_productSearchV3",
        "location_verified_same_run": bool(diagnostic.get("location_verified_same_run", False)),
        "region_binding_fingerprint_verified": bool(diagnostic.get("region_binding_fingerprint_verified", False)),
        "binding_source_key_verified": bool(diagnostic.get("binding_source_key_verified", False)),
        "pages_attempted": int(diagnostic.get("pages_attempted", 0)),
        "pages_completed": int(diagnostic.get("pages_completed", 0)),
        "catalog_products_reported": int(diagnostic.get("catalog_products_reported", 0)),
        "skus_extracted": int(diagnostic.get("skus_extracted", 0)),
        "skus_with_price": int(diagnostic.get("skus_with_price", 0)),
        "duplicate_skus_across_pages": int(diagnostic.get("duplicate_skus_across_pages", 0)),
        "blocked_http_status_observed": diagnostic.get("blocked_http_status_observed"),
        "observed_at_utc": _utc_text(),
        "raw_context_persisted": False,
        "commercial_persistence": False,
        "production_authority": False,
        "catalog_accepted": False,
        "extraction_enabled": False,
    }
    passive._validate_artifact_shape(artifact)
    return artifact


def _write_json(value: Mapping[str, Any], path: Path) -> None:
    passive._validate_artifact_shape(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_csv(products: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "source_key_type",
        "source_key",
        "source_name",
        "brand",
        "presentation",
        "category",
        "product_id",
        "item_id",
        "reference",
        "ean",
        "current_price",
        "reported_regular_price",
        "is_promotion",
        "availability",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(products)


def _verify_sps_binding(page: Any, context: Any, collector: Any) -> None:
    before = radiography._stage(page, context, collector, "before")
    radiography._open_location_selector(page)
    page.wait_for_timeout(150)
    city_control, cities = radiography._city_select_and_options(page, passive.TARGET_CITY)

    collector.reset()
    changed = False
    if city_control.state != radiography.CITY_STATE_SELECTED:
        changed = sample._activate_exact_city_with_single_recovery(city_control, passive.TARGET_CITY)
    page.wait_for_timeout(500 if changed else 100)
    radiography._verify_structural_city_selection(page, passive.TARGET_CITY)

    after_city = radiography._stage(page, context, collector, "after_city")
    store_option, stores = radiography._discover_store_options(page, cities)
    after_store = None
    selected_store = None
    store_selection_observed = False
    if store_option is not None:
        store_selection_observed = True
        selected_store = stores[0]
        collector.reset()
        radiography._activate_option(store_option, selected_store)
        page.wait_for_timeout(500)
        after_store = radiography._stage(page, context, collector, "after_store")

    analysis = analyze_location_binding(
        city_name=passive.TARGET_CITY,
        before=before,
        after_city=after_city,
        store_selection_observed=store_selection_observed,
        after_store=after_store,
        store_name=selected_store,
    )
    binding = report_dict(analysis)
    if str(binding.get("source_location_key_candidate") or "") != sample.EXPECTED_SOURCE_KEY:
        raise FullCatalogError("radiography_sps_binding_not_verified")


def _run_catalog(
    *,
    page_size: int,
    staged_pages: int,
    max_catalog_pages: int,
    delay_seconds: float,
) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise FullCatalogError("playwright_not_installed") from exc

    diagnostic: dict[str, Any] = {
        "location_verified_same_run": False,
        "region_binding_fingerprint_verified": False,
        "binding_source_key_verified": False,
        "pages_attempted": 0,
        "pages_completed": 0,
        "catalog_products_reported": 0,
        "skus_extracted": 0,
        "skus_with_price": 0,
        "duplicate_skus_across_pages": 0,
        "blocked_http_status_observed": None,
    }
    all_products: list[dict[str, Any]] = []
    seen_skus: set[tuple[str, str]] = set()
    total_reported: int | None = None
    total_pages: int | None = None
    staged_validation_passed = False
    run_id = f"sps_full_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"

    with sync_playwright() as pw:
        browser, _ = launch_compatible_chromium(pw)
        try:
            context = browser.new_context(service_workers="block")
            page = context.new_page()
            collector = radiography.RequestContextCollector()
            page.on("request", collector.observe_request)

            home = page.goto(radiography.TARGET_URL, wait_until="domcontentloaded", timeout=20_000)
            if home is not None and int(home.status) in {403, 429}:
                diagnostic["blocked_http_status_observed"] = int(home.status)
                raise FullCatalogError(f"http_{home.status}", diagnostic=diagnostic)
            if passive._blocked_surface(page):
                raise FullCatalogError("blocked_or_login_surface", diagnostic=diagnostic)
            page.wait_for_timeout(250)

            _verify_sps_binding(page, context, collector)
            diagnostic["location_verified_same_run"] = True
            diagnostic["region_binding_fingerprint_verified"] = True
            diagnostic["binding_source_key_verified"] = True

            extractor = LaColoniaExtractor()
            page_number = 1
            while total_pages is None or page_number <= total_pages:
                if page_number > max_catalog_pages:
                    raise FullCatalogError("catalog_page_count_exceeds_safety_limit", diagnostic=diagnostic)

                request_url = build_product_search_url(
                    page=page_number,
                    page_size=page_size,
                    order_by="OrderByNameASC",
                )
                diagnostic["pages_attempted"] += 1
                response = context.request.get(
                    request_url,
                    timeout=EXPLICIT_REQUEST_TIMEOUT_MS,
                    fail_on_status_code=False,
                )
                status = int(response.status)
                if status in {403, 429}:
                    diagnostic["blocked_http_status_observed"] = status
                    raise FullCatalogError(f"http_{status}", diagnostic=diagnostic)
                if status != 200:
                    raise FullCatalogError(f"graphql_http_{status}", diagnostic=diagnostic)
                try:
                    payload = response.json()
                except Exception as exc:
                    raise FullCatalogError("graphql_not_json", diagnostic=diagnostic) from exc
                shape = passive._product_search_shape(payload)
                if shape is None:
                    raise FullCatalogError("graphql_product_search_not_observed", diagnostic=diagnostic)
                observed_total, products_returned = shape
                if total_reported is None:
                    total_reported = observed_total
                    total_pages = max(math.ceil(total_reported / page_size), 1)
                    diagnostic["catalog_products_reported"] = total_reported
                    if total_pages > max_catalog_pages:
                        raise FullCatalogError("catalog_page_count_exceeds_safety_limit", diagnostic=diagnostic)
                elif observed_total != total_reported:
                    raise FullCatalogError("catalog_total_changed_during_run", diagnostic=diagnostic)

                expected_products = min(page_size, max(total_reported - ((page_number - 1) * page_size), 0))
                if products_returned != expected_products:
                    raise FullCatalogError("partial_or_unexpected_product_page", diagnostic=diagnostic)

                try:
                    result = extractor.parse_payload(
                        payload,
                        scrape_run_id=run_id,
                        source_url=request_url,
                        page_size=page_size,
                    )
                except StructureChangedError as exc:
                    raise FullCatalogError("product_payload_not_parseable", diagnostic=diagnostic) from exc
                if not result.accepted:
                    raise FullCatalogError("page_validation_failed", diagnostic=diagnostic)

                page_public = [passive._public_product(product) for product in result.products]
                page_identities: set[tuple[str, str]] = set()
                for product in page_public:
                    identity = (str(product.get("source_key_type") or ""), str(product.get("source_key") or ""))
                    if identity in page_identities or identity in seen_skus:
                        diagnostic["duplicate_skus_across_pages"] += 1
                        raise FullCatalogError("duplicate_sku_across_pages", diagnostic=diagnostic)
                    page_identities.add(identity)
                    seen_skus.add(identity)
                all_products.extend(page_public)
                diagnostic["pages_completed"] += 1
                diagnostic["skus_extracted"] += len(page_public)
                diagnostic["skus_with_price"] += sum(
                    product.get("current_price") is not None for product in page_public
                )

                if page_number == min(staged_pages, total_pages):
                    if diagnostic["pages_completed"] != min(staged_pages, total_pages):
                        raise FullCatalogError("staged_pagination_incomplete", diagnostic=diagnostic)
                    if not all_products:
                        raise FullCatalogError("staged_pagination_empty", diagnostic=diagnostic)
                    staged_validation_passed = True

                page_number += 1
                if total_pages is not None and page_number <= total_pages and delay_seconds > 0:
                    time.sleep(delay_seconds)

            if not staged_validation_passed:
                staged_validation_passed = True
            if total_pages is None or total_reported is None:
                raise FullCatalogError("catalog_totals_missing", diagnostic=diagnostic)
            if diagnostic["pages_completed"] != total_pages:
                raise FullCatalogError("catalog_page_coverage_incomplete", diagnostic=diagnostic)

            return {
                "schema_version": "1",
                "result": "success",
                "catalog_type": "la_colonia_sps_full_read_only",
                "supermarket_id": "la_colonia",
                "location_id": passive.MVP_LOCATION_ID,
                "city": passive.TARGET_CITY,
                "capture_strategy": "radiography_same_context_full_productSearchV3",
                "location_verified_same_run": True,
                "region_binding_fingerprint_verified": True,
                "binding_source_key_verified": True,
                "staged_validation_pages": min(staged_pages, total_pages),
                "staged_validation_passed": staged_validation_passed,
                "page_size": page_size,
                "catalog_products_reported": total_reported,
                "catalog_pages_expected": total_pages,
                "catalog_pages_completed": diagnostic["pages_completed"],
                "page_coverage": diagnostic["pages_completed"] / total_pages,
                "skus_extracted": diagnostic["skus_extracted"],
                "skus_with_price": diagnostic["skus_with_price"],
                "skus_without_price": diagnostic["skus_extracted"] - diagnostic["skus_with_price"],
                "duplicate_skus_across_pages": 0,
                "catalog_complete": True,
                "validation_passed": True,
                "observed_at_utc": _utc_text(),
                "products": all_products,
                "raw_context_persisted": False,
                "commercial_persistence": False,
                "production_authority": False,
                "catalog_accepted": False,
                "extraction_enabled": False,
            }
        except FullCatalogError:
            raise
        except (DiagnosticSafetyError, LocationControlResolutionError, radiography.LocationBindingCaptureError) as exc:
            raise FullCatalogError(str(exc), diagnostic=diagnostic) from exc
        finally:
            browser.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-read-only", action="store_true")
    parser.add_argument("--allow-full-catalog", action="store_true")
    parser.add_argument("--page-size", type=int, choices=(10, 20, 30, 50), default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--staged-pages", type=int, default=DEFAULT_STAGED_PAGES)
    parser.add_argument("--max-catalog-pages", type=int, default=DEFAULT_MAX_CATALOG_PAGES)
    parser.add_argument("--delay-seconds", type=float, default=DEFAULT_DELAY_SECONDS)
    parser.add_argument("--output", type=Path, default=ROOT / "run-artifacts" / "full-catalog.json")
    parser.add_argument("--csv-output", type=Path, default=ROOT / "run-artifacts" / "full-catalog.csv")
    args = parser.parse_args(argv)

    if not args.live_read_only:
        parser.error("el catálogo live requiere --live-read-only")
    if not args.allow_full_catalog:
        parser.error("el catálogo completo requiere --allow-full-catalog")
    if args.staged_pages < 1 or args.staged_pages > 10:
        parser.error("--staged-pages debe estar entre 1 y 10")
    if args.max_catalog_pages < args.staged_pages or args.max_catalog_pages > 300:
        parser.error("--max-catalog-pages fuera de límites")
    if args.delay_seconds < 1.0:
        parser.error("--delay-seconds debe ser al menos 1.0")

    try:
        artifact = _run_catalog(
            page_size=args.page_size,
            staged_pages=args.staged_pages,
            max_catalog_pages=args.max_catalog_pages,
            delay_seconds=args.delay_seconds,
        )
        _write_json(artifact, args.output)
        _write_csv(list(artifact["products"]), args.csv_output)
    except FullCatalogError as exc:
        failure = _safe_failure(exc.reason, exc.diagnostic)
        _write_json(failure, args.output)
        print(f"sps_full_catalog_stopped:{exc.reason}", file=sys.stderr)
        return 3

    print(json.dumps({
        "result": "success",
        "catalog_products_reported": artifact["catalog_products_reported"],
        "catalog_pages_completed": artifact["catalog_pages_completed"],
        "skus_extracted": artifact["skus_extracted"],
        "skus_with_price": artifact["skus_with_price"],
        "catalog_complete": artifact["catalog_complete"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
