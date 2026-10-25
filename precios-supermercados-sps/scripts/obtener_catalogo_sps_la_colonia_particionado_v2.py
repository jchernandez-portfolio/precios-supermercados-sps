#!/usr/bin/env python3
"""Obtiene el catálogo SPS completo usando una frontera de categorías VTEX.

La búsqueda de VTEX deja de responder al cruzar aproximadamente 2,500 productos
en una misma ventana. En vez de exigir hojas terminales del árbol, este runner
elige la categoría más superficial cuya cantidad ya cabe en esa ventana y sólo
desciende cuando una categoría todavía supera el límite. Así evita depender de
que el árbol devuelto empiece exactamente en category-1.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import obtener_catalogo_sps_la_colonia_particionado as base  # noqa: E402

_CATEGORY_KEY = re.compile(r"category-(\d+)\Z")
MAX_PARTITIONS = 250


@dataclass(frozen=True, slots=True)
class FrontierPartition:
    path: tuple[tuple[str, str], ...]
    quantity: int


def _category_values(normalized: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    facets = normalized.get("facets")
    if not isinstance(facets, Sequence) or isinstance(facets, (str, bytes)):
        raise ValueError("facets_not_sequence")

    typed: list[Sequence[Mapping[str, Any]]] = []
    fallback: list[Sequence[Mapping[str, Any]]] = []
    for facet in facets:
        if not isinstance(facet, Mapping):
            continue
        values = facet.get("values")
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            continue
        category_values = [
            item
            for item in values
            if isinstance(item, Mapping)
            and _CATEGORY_KEY.fullmatch(str(item.get("key") or "").strip())
        ]
        if not category_values:
            continue
        facet_type = str(facet.get("type") or "").strip().upper()
        if facet_type in {"CATEGORYTREE", "CATEGORY"}:
            typed.append(category_values)
        else:
            fallback.append(category_values)

    candidates = typed or fallback
    if not candidates:
        raise ValueError("category_values_missing")
    # Una sola facet de categorías es la forma normal. Si hubiese más de una,
    # elegimos la de mayor cobertura positiva sin publicar nombres ni valores.
    return max(
        candidates,
        key=lambda values: sum(
            int(item.get("quantity"))
            for item in values
            if isinstance(item.get("quantity"), int)
            and not isinstance(item.get("quantity"), bool)
            and int(item.get("quantity")) > 0
        ),
    )


def _build_frontier(
    values: Sequence[Mapping[str, Any]],
    *,
    search_window: int = base.SEARCH_WINDOW_MAX_PRODUCTS,
    max_partitions: int = MAX_PARTITIONS,
) -> tuple[FrontierPartition, ...]:
    if search_window <= 0 or max_partitions <= 0:
        raise ValueError("invalid_frontier_limits")

    partitions: list[FrontierPartition] = []

    def walk(
        node: Mapping[str, Any],
        *,
        parent_path: tuple[tuple[str, str], ...],
        parent_level: int | None,
    ) -> int:
        raw_key = node.get("key")
        raw_value = node.get("value")
        key = raw_key.strip() if isinstance(raw_key, str) else ""
        value = raw_value.strip() if isinstance(raw_value, str) else ""
        match = _CATEGORY_KEY.fullmatch(key)
        if not match or not value:
            raise ValueError("invalid_category_identity")
        level = int(match.group(1))
        if parent_level is not None and level <= parent_level:
            raise ValueError("non_increasing_category_level")

        raw_quantity = node.get("quantity")
        if isinstance(raw_quantity, bool) or not isinstance(raw_quantity, int):
            raise ValueError("invalid_category_quantity")
        quantity = int(raw_quantity)
        if quantity < 0:
            raise ValueError("negative_category_quantity")
        if quantity == 0:
            return 0

        path = (*parent_path, (key, value))
        if quantity <= search_window:
            partitions.append(FrontierPartition(path=path, quantity=quantity))
            if len(partitions) > max_partitions:
                raise ValueError("partition_limit_exceeded")
            return quantity

        children = node.get("children")
        if not isinstance(children, Sequence) or isinstance(children, (str, bytes)):
            raise ValueError("oversized_category_children_missing")
        positive_children = [
            child
            for child in children
            if isinstance(child, Mapping)
            and isinstance(child.get("quantity"), int)
            and not isinstance(child.get("quantity"), bool)
            and int(child.get("quantity")) > 0
        ]
        if not positive_children:
            raise ValueError("oversized_category_without_positive_children")

        covered = 0
        for child in positive_children:
            covered += walk(child, parent_path=path, parent_level=level)
        if covered < quantity:
            raise ValueError("frontier_below_parent_quantity")
        return covered

    for root in values:
        if not isinstance(root, Mapping):
            raise ValueError("category_root_not_object")
        walk(root, parent_path=(), parent_level=None)

    if not partitions:
        raise ValueError("frontier_empty")
    return tuple(partitions)


def _run_partitioned_catalog(*, page_size: int, delay_seconds: float) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise base.full.FullCatalogError("playwright_not_installed") from exc

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
        "partition_quantity_sum": 0,
    }
    run_id = f"sps_frontier_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    unique_products: set[str] = set()
    unique_skus: set[tuple[str, str]] = set()
    all_products: list[dict[str, Any]] = []

    with sync_playwright() as pw:
        browser, _ = base.full.launch_compatible_chromium(pw)
        try:
            context = browser.new_context(service_workers="block")
            page = context.new_page()
            collector = base.full.radiography.RequestContextCollector()
            page.on("request", collector.observe_request)

            home = page.goto(
                base.full.radiography.TARGET_URL,
                wait_until="domcontentloaded",
                timeout=20_000,
            )
            if home is not None and int(home.status) in {403, 429}:
                diagnostic["blocked_http_status_observed"] = int(home.status)
                raise base.full.FullCatalogError(
                    f"http_{home.status}", diagnostic=diagnostic
                )
            if base.full.passive._blocked_surface(page):
                raise base.full.FullCatalogError(
                    "blocked_or_login_surface", diagnostic=diagnostic
                )
            page.wait_for_timeout(250)

            base.full._verify_sps_binding(page, context, collector)
            diagnostic["location_verified_same_run"] = True
            diagnostic["region_binding_fingerprint_verified"] = True
            diagnostic["binding_source_key_verified"] = True

            tree_response = context.request.get(
                base._category_tree_url(),
                timeout=base.FACET_REQUEST_TIMEOUT_MS,
                fail_on_status_code=False,
            )
            tree_payload = base._read_json_response(
                tree_response, diagnostic, kind="category_tree"
            )
            data = tree_payload.get("data")
            if not isinstance(data, Mapping):
                raise base.full.FullCatalogError(
                    "category_tree_data_missing", diagnostic=diagnostic
                )
            try:
                normalized = base.LaColoniaFacetDiscoveryAdapter._normalize_tree(data)
                root_total = int(normalized["recordsFiltered"])
                sampling = normalized.get("sampling")
                if sampling is not False:
                    raise ValueError("category_tree_sampling_detected")
                values = _category_values(normalized)
                partitions = _build_frontier(values)
            except Exception as exc:
                diagnostic["frontier_error"] = str(exc)[:120]
                raise base.full.FullCatalogError(
                    "category_frontier_not_usable", diagnostic=diagnostic
                ) from exc

            partition_quantity_sum = sum(item.quantity for item in partitions)
            diagnostic["catalog_products_reported"] = root_total
            diagnostic["partitions_detected"] = len(partitions)
            diagnostic["partition_quantity_sum"] = partition_quantity_sum
            if partition_quantity_sum < root_total:
                raise base.full.FullCatalogError(
                    "partition_frontier_below_root_total", diagnostic=diagnostic
                )

            planned_requests = sum(
                math.ceil(item.quantity / page_size) for item in partitions
            )
            diagnostic["planned_product_requests"] = planned_requests
            if planned_requests > base.MAX_PLANNED_PRODUCT_REQUESTS:
                raise base.full.FullCatalogError(
                    "partition_request_budget_exceeded", diagnostic=diagnostic
                )

            extractor = base.LaColoniaExtractor()
            for partition_index, partition in enumerate(partitions, start=1):
                facets = base._partition_facets(partition.path)
                expected_partition_total = partition.quantity
                pages = max(math.ceil(expected_partition_total / page_size), 1)

                for partition_page in range(1, pages + 1):
                    request_url = base._product_url(
                        selected_facets=facets,
                        page=partition_page,
                        page_size=page_size,
                    )
                    diagnostic["pages_attempted"] += 1
                    response = context.request.get(
                        request_url,
                        timeout=base.PRODUCT_REQUEST_TIMEOUT_MS,
                        fail_on_status_code=False,
                    )
                    payload = base._read_json_response(
                        response, diagnostic, kind="product_search"
                    )
                    observed_total, products_returned = base._read_shape(payload)
                    if observed_total != expected_partition_total:
                        diagnostic["partition_expected_total"] = expected_partition_total
                        diagnostic["partition_observed_total"] = observed_total
                        raise base.full.FullCatalogError(
                            "partition_total_mismatch", diagnostic=diagnostic
                        )
                    expected_on_page = min(
                        page_size,
                        max(
                            expected_partition_total
                            - ((partition_page - 1) * page_size),
                            0,
                        ),
                    )
                    if products_returned != expected_on_page:
                        diagnostic["expected_products_on_page"] = expected_on_page
                        diagnostic["observed_products_on_page"] = products_returned
                        raise base.full.FullCatalogError(
                            "partial_or_unexpected_product_page", diagnostic=diagnostic
                        )

                    try:
                        result = extractor.parse_payload(
                            payload,
                            scrape_run_id=run_id,
                            source_url=request_url,
                            page_size=page_size,
                        )
                    except base.StructureChangedError as exc:
                        raise base.full.FullCatalogError(
                            "product_payload_not_parseable", diagnostic=diagnostic
                        ) from exc
                    if not result.accepted:
                        raise base.full.FullCatalogError(
                            "page_validation_failed", diagnostic=diagnostic
                        )

                    page_products = [
                        base.full.passive._public_product(product)
                        for product in result.products
                    ]
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
                raise base.full.FullCatalogError(
                    "partition_coverage_incomplete", diagnostic=diagnostic
                )
            if len(unique_products) != root_total:
                diagnostic["unique_products_extracted"] = len(unique_products)
                raise base.full.FullCatalogError(
                    "unique_product_coverage_mismatch", diagnostic=diagnostic
                )

            artifact = {
                "schema_version": "3",
                "result": "success",
                "catalog_type": "la_colonia_sps_full_read_only",
                "supermarket_id": "la_colonia",
                "location_id": base.full.passive.MVP_LOCATION_ID,
                "city": base.full.passive.TARGET_CITY,
                "capture_strategy": "radiography_same_context_category_frontier_productSearchV3",
                "location_verified_same_run": True,
                "region_binding_fingerprint_verified": True,
                "binding_source_key_verified": True,
                "page_size": page_size,
                "catalog_products_reported": root_total,
                "unique_products_extracted": len(unique_products),
                "catalog_product_coverage": len(unique_products) / root_total,
                "partitions_detected": len(partitions),
                "partitions_completed": diagnostic["partitions_completed"],
                "partition_quantity_sum": partition_quantity_sum,
                "product_requests_completed": diagnostic["pages_completed"],
                "skus_extracted": len(unique_skus),
                "skus_with_price": diagnostic["skus_with_price"],
                "skus_without_price": len(unique_skus) - diagnostic["skus_with_price"],
                "duplicate_skus_across_partitions": diagnostic[
                    "duplicate_skus_across_pages"
                ],
                "catalog_complete": True,
                "validation_passed": True,
                "observed_at_utc": base.full._utc_text(),
                "products": all_products,
                "raw_context_persisted": False,
                "commercial_persistence": False,
                "production_authority": False,
                "catalog_accepted": False,
                "extraction_enabled": False,
            }
            base.full.passive._validate_artifact_shape(artifact)
            return artifact
        finally:
            browser.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-read-only", action="store_true")
    parser.add_argument("--allow-full-catalog", action="store_true")
    parser.add_argument("--page-size", type=int, choices=(50,), default=50)
    parser.add_argument("--delay-seconds", type=float, default=1.5)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "run-artifacts" / "full-catalog.json",
    )
    parser.add_argument(
        "--csv-output",
        type=Path,
        default=ROOT / "run-artifacts" / "full-catalog.csv",
    )
    args = parser.parse_args(argv)

    if not args.live_read_only:
        parser.error("el catálogo live requiere --live-read-only")
    if not args.allow_full_catalog:
        parser.error("el catálogo completo requiere --allow-full-catalog")
    if args.delay_seconds < 1.0:
        parser.error("--delay-seconds debe ser al menos 1.0")

    try:
        artifact = _run_partitioned_catalog(
            page_size=args.page_size,
            delay_seconds=args.delay_seconds,
        )
        base.full._write_json(artifact, args.output)
        base.full._write_csv(list(artifact["products"]), args.csv_output)
    except base.full.FullCatalogError as exc:
        failure = base.full._safe_failure(exc.reason, exc.diagnostic)
        failure["capture_strategy"] = (
            "radiography_same_context_category_frontier_productSearchV3"
        )
        for key in (
            "partitions_detected",
            "partitions_completed",
            "partition_quantity_sum",
            "planned_product_requests",
            "unique_products_extracted",
            "partition_expected_total",
            "partition_observed_total",
            "expected_products_on_page",
            "observed_products_on_page",
        ):
            if key in exc.diagnostic:
                failure[key] = int(exc.diagnostic[key])
        if "frontier_error" in exc.diagnostic:
            failure["frontier_error"] = str(exc.diagnostic["frontier_error"])
        base.full._write_json(failure, args.output)
        print(f"sps_frontier_catalog_stopped:{exc.reason}", file=sys.stderr)
        return 3

    print(
        json.dumps(
            {
                "result": "success",
                "catalog_products_reported": artifact["catalog_products_reported"],
                "unique_products_extracted": artifact["unique_products_extracted"],
                "partitions_completed": artifact["partitions_completed"],
                "skus_extracted": artifact["skus_extracted"],
                "skus_with_price": artifact["skus_with_price"],
                "catalog_complete": artifact["catalog_complete"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
