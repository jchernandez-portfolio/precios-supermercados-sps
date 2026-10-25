#!/usr/bin/env python3
"""Obtiene todo el catálogo SPS dividiéndolo por hojas de categoría VTEX.

VTEX deja de devolver productSearchV3 al cruzar la ventana de ~2500 productos.
Este runner mantiene el BrowserContext SPS ya validado, consulta una sola vez el
árbol de categorías y recorre sus hojas disjuntas de búsqueda. Los productos y
SKU se deduplican globalmente; el run sólo se acepta si los productId únicos
cubren exactamente recordsFiltered del catálogo raíz.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import obtener_catalogo_sps_la_colonia_radiografia as full  # noqa: E402
import obtener_catalogo_sps_la_colonia_radiografia_v2 as stable  # noqa: E402,F401
from precios_supermercados.scrapers.base import StructureChangedError  # noqa: E402
from precios_supermercados.scrapers.la_colonia import LaColoniaExtractor  # noqa: E402
from precios_supermercados.scrapers.la_colonia_facet_discovery import (  # noqa: E402
    analyze_category_facets,
)
from precios_supermercados.scrapers.la_colonia_facet_discovery_adapter import (  # noqa: E402
    CATEGORY_TREE_OPERATION,
    CATEGORY_TREE_QUERY,
    LaColoniaFacetDiscoveryAdapter,
)
from precios_supermercados.scrapers.la_colonia_graphql import (  # noqa: E402
    GRAPHQL_ENDPOINT,
    PRODUCT_SEARCH_QUERY,
)

ROOT_FACET = {"key": "category-1", "value": "supermercado"}
SEARCH_WINDOW_MAX_PRODUCTS = 2500
MAX_PLANNED_PRODUCT_REQUESTS = 400
FACET_REQUEST_TIMEOUT_MS = 20_000
PRODUCT_REQUEST_TIMEOUT_MS = 20_000


def _params(operation_name: str, query: str, variables: Mapping[str, Any]) -> str:
    params = {
        "workspace": "master",
        "maxAge": "short",
        "appsEtag": "remove",
        "domain": "store",
        "locale": "es-HN",
        "operationName": operation_name,
        "query": query,
        "variables": json.dumps(variables, separators=(",", ":")),
    }
    return f"{GRAPHQL_ENDPOINT}?{urlencode(params)}"


def _category_tree_url() -> str:
    variables = {
        "query": "supermercado",
        "fullText": "",
        "selectedFacets": [ROOT_FACET],
        "from": 0,
        "to": 0,
    }
    return _params(CATEGORY_TREE_OPERATION, CATEGORY_TREE_QUERY, variables)


def _partition_facets(path: tuple[tuple[str, str], ...]) -> list[dict[str, str]]:
    facets: list[dict[str, str]] = [dict(ROOT_FACET)]
    seen = {(ROOT_FACET["key"], ROOT_FACET["value"])}
    for key, value in path:
        identity = (str(key), str(value))
        if identity in seen:
            continue
        facets.append({"key": identity[0], "value": identity[1]})
        seen.add(identity)
    return facets


def _product_url(*, selected_facets: list[dict[str, str]], page: int, page_size: int) -> str:
    from_index = (page - 1) * page_size
    variables = {
        "query": "",
        "fullText": "",
        "selectedFacets": selected_facets,
        "orderBy": "OrderByNameASC",
        "from": from_index,
        "to": from_index + page_size - 1,
        "hideUnavailableItems": False,
        "skusFilter": "ALL",
    }
    return _params("productSearchV3", PRODUCT_SEARCH_QUERY, variables)


def _read_shape(payload: Mapping[str, Any]) -> tuple[int, int]:
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise full.FullCatalogError("graphql_product_search_not_observed")
    search = data.get("productSearch")
    if not isinstance(search, Mapping):
        raise full.FullCatalogError("graphql_product_search_not_observed")
    products = search.get("products")
    if not isinstance(products, list):
        raise full.FullCatalogError("graphql_product_search_not_observed")
    try:
        total = int(search.get("recordsFiltered"))
    except (TypeError, ValueError) as exc:
        raise full.FullCatalogError("graphql_product_search_total_invalid") from exc
    return total, len(products)


def _read_json_response(response: Any, diagnostic: dict[str, Any], *, kind: str) -> Mapping[str, Any]:
    status = int(response.status)
    if status in {403, 429}:
        diagnostic["blocked_http_status_observed"] = status
        raise full.FullCatalogError(f"http_{status}", diagnostic=diagnostic)
    if status != 200:
        raise full.FullCatalogError(f"{kind}_http_{status}", diagnostic=diagnostic)
    try:
        payload = response.json()
    except Exception as exc:
        raise full.FullCatalogError(f"{kind}_not_json", diagnostic=diagnostic) from exc
    if not isinstance(payload, Mapping):
        raise full.FullCatalogError(f"{kind}_payload_invalid", diagnostic=diagnostic)
    if payload.get("errors"):
        raise full.FullCatalogError(f"{kind}_graphql_errors", diagnostic=diagnostic)
    return payload


def _run_partitioned_catalog(*, page_size: int, delay_seconds: float) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise full.FullCatalogError("playwright_not_installed") from exc

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
        "partitions_detected": 0,
        "partitions_completed": 0,
    }
    run_id = f"sps_partitioned_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    unique_products: set[str] = set()
    unique_skus: set[tuple[str, str]] = set()
    all_products: list[dict[str, Any]] = []

    with sync_playwright() as pw:
        browser, _ = full.launch_compatible_chromium(pw)
        try:
            context = browser.new_context(service_workers="block")
            page = context.new_page()
            collector = full.radiography.RequestContextCollector()
            page.on("request", collector.observe_request)

            home = page.goto(full.radiography.TARGET_URL, wait_until="domcontentloaded", timeout=20_000)
            if home is not None and int(home.status) in {403, 429}:
                diagnostic["blocked_http_status_observed"] = int(home.status)
                raise full.FullCatalogError(f"http_{home.status}", diagnostic=diagnostic)
            if full.passive._blocked_surface(page):
                raise full.FullCatalogError("blocked_or_login_surface", diagnostic=diagnostic)
            page.wait_for_timeout(250)

            full._verify_sps_binding(page, context, collector)
            diagnostic["location_verified_same_run"] = True
            diagnostic["region_binding_fingerprint_verified"] = True
            diagnostic["binding_source_key_verified"] = True

            tree_response = context.request.get(
                _category_tree_url(), timeout=FACET_REQUEST_TIMEOUT_MS, fail_on_status_code=False
            )
            tree_payload = _read_json_response(tree_response, diagnostic, kind="category_tree")
            data = tree_payload.get("data")
            if not isinstance(data, Mapping):
                raise full.FullCatalogError("category_tree_data_missing", diagnostic=diagnostic)
            try:
                normalized = LaColoniaFacetDiscoveryAdapter._normalize_tree(data)
                root_total = int(normalized["recordsFiltered"])
                analysis = analyze_category_facets(normalized, root_total=root_total)
            except Exception as exc:
                raise full.FullCatalogError("category_tree_not_usable", diagnostic=diagnostic) from exc

            if analysis.sampling:
                raise full.FullCatalogError("category_tree_sampling_detected", diagnostic=diagnostic)
            partitions = tuple(item for item in analysis.leaf_partitions if item.quantity > 0)
            if not partitions:
                raise full.FullCatalogError("category_partitions_missing", diagnostic=diagnostic)
            if any(item.quantity > SEARCH_WINDOW_MAX_PRODUCTS for item in partitions):
                raise full.FullCatalogError("category_partition_exceeds_search_window", diagnostic=diagnostic)

            planned_requests = sum(math.ceil(item.quantity / page_size) for item in partitions)
            if planned_requests > MAX_PLANNED_PRODUCT_REQUESTS:
                raise full.FullCatalogError("partition_request_budget_exceeded", diagnostic=diagnostic)

            diagnostic["catalog_products_reported"] = root_total
            diagnostic["partitions_detected"] = len(partitions)
            extractor = LaColoniaExtractor()

            for partition_index, partition in enumerate(partitions, start=1):
                facets = _partition_facets(partition._path)
                expected_partition_total = int(partition.quantity)
                pages = max(math.ceil(expected_partition_total / page_size), 1)

                for partition_page in range(1, pages + 1):
                    request_url = _product_url(
                        selected_facets=facets,
                        page=partition_page,
                        page_size=page_size,
                    )
                    diagnostic["pages_attempted"] += 1
                    response = context.request.get(
                        request_url,
                        timeout=PRODUCT_REQUEST_TIMEOUT_MS,
                        fail_on_status_code=False,
                    )
                    payload = _read_json_response(response, diagnostic, kind="product_search")
                    observed_total, products_returned = _read_shape(payload)
                    if observed_total != expected_partition_total:
                        raise full.FullCatalogError("partition_total_mismatch", diagnostic=diagnostic)
                    expected_on_page = min(
                        page_size,
                        max(expected_partition_total - ((partition_page - 1) * page_size), 0),
                    )
                    if products_returned != expected_on_page:
                        raise full.FullCatalogError("partial_or_unexpected_product_page", diagnostic=diagnostic)

                    try:
                        result = extractor.parse_payload(
                            payload,
                            scrape_run_id=run_id,
                            source_url=request_url,
                            page_size=page_size,
                        )
                    except StructureChangedError as exc:
                        raise full.FullCatalogError("product_payload_not_parseable", diagnostic=diagnostic) from exc
                    if not result.accepted:
                        raise full.FullCatalogError("page_validation_failed", diagnostic=diagnostic)

                    page_products = [full.passive._public_product(product) for product in result.products]
                    for product in page_products:
                        product_id = str(product.get("product_id") or "")
                        if product_id:
                            unique_products.add(product_id)
                        identity = (
                            str(product.get("source_key_type") or ""),
                            str(product.get("source_key") or ""),
                        )
                        if identity in unique_skus:
                            diagnostic["duplicate_skus_across_pages"] += 1
                            continue
                        unique_skus.add(identity)
                        all_products.append(product)
                        if product.get("current_price") is not None:
                            diagnostic["skus_with_price"] += 1
                    diagnostic["pages_completed"] += 1
                    diagnostic["skus_extracted"] = len(unique_skus)

                    if delay_seconds > 0 and (
                        partition_page < pages or partition_index < len(partitions)
                    ):
                        time.sleep(delay_seconds)

                diagnostic["partitions_completed"] += 1

            if diagnostic["partitions_completed"] != diagnostic["partitions_detected"]:
                raise full.FullCatalogError("partition_coverage_incomplete", diagnostic=diagnostic)
            if len(unique_products) != root_total:
                diagnostic["unique_products_extracted"] = len(unique_products)
                raise full.FullCatalogError("unique_product_coverage_mismatch", diagnostic=diagnostic)

            artifact = {
                "schema_version": "2",
                "result": "success",
                "catalog_type": "la_colonia_sps_full_read_only",
                "supermarket_id": "la_colonia",
                "location_id": full.passive.MVP_LOCATION_ID,
                "city": full.passive.TARGET_CITY,
                "capture_strategy": "radiography_same_context_category_partitioned_productSearchV3",
                "location_verified_same_run": True,
                "region_binding_fingerprint_verified": True,
                "binding_source_key_verified": True,
                "page_size": page_size,
                "catalog_products_reported": root_total,
                "unique_products_extracted": len(unique_products),
                "catalog_product_coverage": len(unique_products) / root_total,
                "partitions_detected": len(partitions),
                "partitions_completed": diagnostic["partitions_completed"],
                "product_requests_completed": diagnostic["pages_completed"],
                "skus_extracted": len(unique_skus),
                "skus_with_price": diagnostic["skus_with_price"],
                "skus_without_price": len(unique_skus) - diagnostic["skus_with_price"],
                "duplicate_skus_across_partitions": diagnostic["duplicate_skus_across_pages"],
                "catalog_complete": True,
                "validation_passed": True,
                "observed_at_utc": full._utc_text(),
                "products": all_products,
                "raw_context_persisted": False,
                "commercial_persistence": False,
                "production_authority": False,
                "catalog_accepted": False,
                "extraction_enabled": False,
            }
            full.passive._validate_artifact_shape(artifact)
            return artifact
        finally:
            browser.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-read-only", action="store_true")
    parser.add_argument("--allow-full-catalog", action="store_true")
    parser.add_argument("--page-size", type=int, choices=(50,), default=50)
    parser.add_argument("--delay-seconds", type=float, default=1.5)
    parser.add_argument("--output", type=Path, default=ROOT / "run-artifacts" / "full-catalog.json")
    parser.add_argument("--csv-output", type=Path, default=ROOT / "run-artifacts" / "full-catalog.csv")
    args = parser.parse_args(argv)

    if not args.live_read_only:
        parser.error("el catálogo live requiere --live-read-only")
    if not args.allow_full_catalog:
        parser.error("el catálogo completo requiere --allow-full-catalog")
    if args.delay_seconds < 1.0:
        parser.error("--delay-seconds debe ser al menos 1.0")

    try:
        artifact = _run_partitioned_catalog(page_size=args.page_size, delay_seconds=args.delay_seconds)
        full._write_json(artifact, args.output)
        full._write_csv(list(artifact["products"]), args.csv_output)
    except full.FullCatalogError as exc:
        failure = full._safe_failure(exc.reason, exc.diagnostic)
        failure["capture_strategy"] = "radiography_same_context_category_partitioned_productSearchV3"
        failure["partitions_detected"] = int(exc.diagnostic.get("partitions_detected", 0))
        failure["partitions_completed"] = int(exc.diagnostic.get("partitions_completed", 0))
        if "unique_products_extracted" in exc.diagnostic:
            failure["unique_products_extracted"] = int(exc.diagnostic["unique_products_extracted"])
        full._write_json(failure, args.output)
        print(f"sps_partitioned_catalog_stopped:{exc.reason}", file=sys.stderr)
        return 3

    print(json.dumps({
        "result": "success",
        "catalog_products_reported": artifact["catalog_products_reported"],
        "unique_products_extracted": artifact["unique_products_extracted"],
        "partitions_completed": artifact["partitions_completed"],
        "skus_extracted": artifact["skus_extracted"],
        "skus_with_price": artifact["skus_with_price"],
        "catalog_complete": artifact["catalog_complete"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
