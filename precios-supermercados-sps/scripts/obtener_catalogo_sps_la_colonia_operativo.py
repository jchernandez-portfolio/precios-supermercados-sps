#!/usr/bin/env python3
"""Obtiene el catálogo SPS completo sin volver a ejecutar la radiografía.

Las cantidades de facets se usan sólo para empacar marcas en buckets conservadores.
El total raíz y el total real de cada bucket se toman de productSearchV3, que es la
fuente autoritativa para paginación. Cada bucket limita su URL GET antes de enviar
y, si VTEX deja un hueco de paginación, se recupera sólo ese bucket en orden
inverso antes de aceptarlo.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import obtener_catalogo_sps_la_colonia_particionado_v2 as frontier  # noqa: E402
import obtener_catalogo_sps_la_colonia_particionado_v3 as brand  # noqa: E402
from precios_supermercados.scrapers.la_colonia_location import (  # noqa: E402
    LocationInitializationError,
    ensure_operational_city,
)
from precios_supermercados.unpriced_unavailable import (  # noqa: E402
    split_unpriced_unavailable,
)

core = frontier.base
full = core.full
CAPTURE_STRATEGY = "operational_city_url_safe_brand_buckets_recovery_productSearchV3"
PARTITION_STRATEGY = "brand_buckets_authoritative_transport_safe_reverse_recovery"
LOCATION_VERIFICATION_METHOD = "structural_exact_city_control"
BUCKET_ESTIMATE_CAPACITY = 2300
MAX_BRANDS_PER_BUCKET = 24
MAX_PRODUCT_SEARCH_URL_BYTES = 7000
PRIMARY_ORDER_BY = "OrderByNameASC"
RECOVERY_ORDER_BY = "OrderByNameDESC"


def _product_url(
    *,
    selected_facets: Sequence[Mapping[str, str]],
    page: int,
    page_size: int,
    order_by: str = PRIMARY_ORDER_BY,
) -> str:
    if page < 1:
        raise ValueError("page debe ser mayor o igual que 1")
    if order_by not in {PRIMARY_ORDER_BY, RECOVERY_ORDER_BY}:
        raise ValueError("order_by no permitido en catálogo operativo")
    from_index = (page - 1) * page_size
    variables = {
        "query": "",
        "fullText": "",
        "selectedFacets": [dict(item) for item in selected_facets],
        "orderBy": order_by,
        "from": from_index,
        "to": from_index + page_size - 1,
        "hideUnavailableItems": False,
        "skusFilter": "ALL",
    }
    return core._params("productSearchV3", core.PRODUCT_SEARCH_QUERY, variables)


def _bucket_path(values: Sequence[str]) -> tuple[tuple[str, str], ...]:
    return tuple(("brand", value) for value in values)


def _bucket_url_bytes(
    values: Sequence[str],
    *,
    page_size: int,
) -> int:
    """Mide la URL más larga posible del bucket dentro de la ventana VTEX."""

    if not values:
        raise ValueError("brand_bucket_empty")
    max_page = max(math.ceil(core.SEARCH_WINDOW_MAX_PRODUCTS / page_size), 1)
    facets = core._partition_facets(_bucket_path(values))
    return max(
        len(
            _product_url(
                selected_facets=facets,
                page=max_page,
                page_size=page_size,
                order_by=order_by,
            ).encode("utf-8")
        )
        for order_by in (PRIMARY_ORDER_BY, RECOVERY_ORDER_BY)
    )


def _build_brand_buckets(
    values: Sequence[Mapping[str, Any]],
    *,
    estimate_capacity: int = BUCKET_ESTIMATE_CAPACITY,
    page_size: int = 50,
    max_brands: int = MAX_BRANDS_PER_BUCKET,
    max_url_bytes: int = MAX_PRODUCT_SEARCH_URL_BYTES,
) -> tuple[frontier.FrontierPartition, ...]:
    if estimate_capacity <= 0 or estimate_capacity > core.SEARCH_WINDOW_MAX_PRODUCTS:
        raise ValueError("invalid_brand_bucket_capacity")
    if page_size <= 0:
        raise ValueError("invalid_brand_bucket_page_size")
    if max_brands <= 0:
        raise ValueError("invalid_brand_bucket_brand_limit")
    if max_url_bytes <= 0:
        raise ValueError("invalid_brand_bucket_url_limit")

    by_value: dict[str, int] = {}
    for item in values:
        key = str(item.get("key") or "").strip().lower()
        value = str(item.get("value") or "").strip()
        quantity = item.get("quantity")
        if key != "brand" or not value:
            raise ValueError("invalid_brand_identity")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise ValueError("invalid_brand_quantity")
        if quantity > core.SEARCH_WINDOW_MAX_PRODUCTS:
            raise ValueError("brand_partition_exceeds_search_window")
        previous = by_value.get(value)
        if previous is not None and previous != quantity:
            raise ValueError("duplicate_brand_quantity_conflict")
        by_value[value] = int(quantity)

    if not by_value:
        raise ValueError("brand_partitions_missing")

    buckets: list[dict[str, Any]] = []
    for value, quantity in sorted(by_value.items(), key=lambda item: (-item[1], item[0])):
        if _bucket_url_bytes([value], page_size=page_size) > max_url_bytes:
            raise ValueError("single_brand_url_too_long")

        candidates: list[tuple[int, int]] = []
        for index, bucket in enumerate(buckets):
            candidate_values = [*bucket["values"], value]
            if int(bucket["quantity"]) + quantity > estimate_capacity:
                continue
            if len(candidate_values) > max_brands:
                continue
            if _bucket_url_bytes(candidate_values, page_size=page_size) > max_url_bytes:
                continue
            candidates.append((index, int(bucket["quantity"])))

        if candidates:
            index = max(candidates, key=lambda item: item[1])[0]
            buckets[index]["values"].append(value)
            buckets[index]["quantity"] += quantity
        else:
            buckets.append({"values": [value], "quantity": quantity})

    if len(buckets) > core.MAX_PLANNED_PRODUCT_REQUESTS:
        raise ValueError("partition_limit_exceeded")

    partitions: list[frontier.FrontierPartition] = []
    for bucket in buckets:
        bucket_values = tuple(str(value) for value in bucket["values"])
        if len(bucket_values) > max_brands:
            raise ValueError("brand_bucket_brand_limit_exceeded")
        if _bucket_url_bytes(bucket_values, page_size=page_size) > max_url_bytes:
            raise ValueError("brand_bucket_url_limit_exceeded")
        partitions.append(
            frontier.FrontierPartition(
                path=_bucket_path(bucket_values),
                quantity=int(bucket["quantity"]),
            )
        )
    return tuple(partitions)


def _process_page(
    *,
    payload: Mapping[str, Any],
    request_url: str,
    extractor: Any,
    run_id: str,
    page_size: int,
    unique_products: set[str],
    unique_skus: set[tuple[str, str]],
    bucket_products: set[str],
    all_products: list[dict[str, Any]],
    diagnostic: dict[str, Any],
    recovery: bool,
) -> None:
    try:
        result = extractor.parse_payload(
            payload,
            scrape_run_id=run_id,
            source_url=request_url,
            page_size=page_size,
        )
    except core.StructureChangedError as exc:
        raise full.FullCatalogError(
            "product_payload_not_parseable", diagnostic=diagnostic
        ) from exc
    if not result.accepted:
        raise full.FullCatalogError("page_validation_failed", diagnostic=diagnostic)

    for parsed in result.products:
        product = full.passive._public_product(parsed)
        product_id = str(product.get("product_id") or "")
        if product_id:
            unique_products.add(product_id)
            bucket_products.add(product_id)
        identity = (
            str(product.get("source_key_type") or ""),
            str(product.get("source_key") or ""),
        )
        if identity in unique_skus:
            if recovery:
                diagnostic["recovery_duplicate_skus_ignored"] += 1
            else:
                diagnostic["duplicate_skus_across_pages"] += 1
            continue
        unique_skus.add(identity)
        all_products.append(product)
        if product.get("current_price") is not None:
            diagnostic["skus_with_price"] += 1
    diagnostic["pages_completed"] += 1
    if recovery:
        diagnostic["recovery_pages_completed"] += 1
    diagnostic["skus_extracted"] = len(unique_skus)


def catalog_rows_fields(
    all_products: Sequence[Mapping[str, Any]],
    *,
    observed_at_utc: str,
) -> dict[str, Any]:
    """Campos de filas/conteos del snapshot con la regla sin precio agotado.

    Los SKU listados sin precio y agotados ("NO DISPONIBLE") salen de las filas
    con precio a ``unpriced_unavailable``; los conteos ``skus_*`` describen sólo
    ``products``. La validación del snapshot acota su volumen y exige que todos
    estén agotados; un SKU sin precio no agotado queda en ``products`` y falla.
    """
    priced, unpriced = split_unpriced_unavailable(all_products, observed_at_utc=observed_at_utc)
    with_price = sum(product.get("current_price") is not None for product in priced)
    return {
        "skus_extracted": len(priced),
        "skus_with_price": with_price,
        "skus_without_price": len(priced) - with_price,
        "skus_unpriced_unavailable": len(unpriced),
        "products": priced,
        "unpriced_unavailable": unpriced,
    }


def _ensure_request_budget(diagnostic: dict[str, Any]) -> None:
    if diagnostic["product_requests_completed"] >= core.MAX_PLANNED_PRODUCT_REQUESTS:
        raise full.FullCatalogError(
            "partition_request_budget_exceeded", diagnostic=diagnostic
        )


def _fetch_known_total_page(
    *,
    context: Any,
    facets: Sequence[Mapping[str, str]],
    page: int,
    page_size: int,
    order_by: str,
    expected_total: int,
    extractor: Any,
    run_id: str,
    unique_products: set[str],
    unique_skus: set[tuple[str, str]],
    bucket_products: set[str],
    all_products: list[dict[str, Any]],
    diagnostic: dict[str, Any],
    recovery: bool,
) -> int:
    _ensure_request_budget(diagnostic)
    request_url = _product_url(
        selected_facets=facets,
        page=page,
        page_size=page_size,
        order_by=order_by,
    )
    request_url_bytes = len(request_url.encode("utf-8"))
    if request_url_bytes > MAX_PRODUCT_SEARCH_URL_BYTES:
        diagnostic["max_bucket_url_bytes"] = max(
            diagnostic["max_bucket_url_bytes"], request_url_bytes
        )
        raise full.FullCatalogError(
            "brand_bucket_url_limit_exceeded", diagnostic=diagnostic
        )
    diagnostic["pages_attempted"] += 1
    response = context.request.get(
        request_url,
        timeout=core.PRODUCT_REQUEST_TIMEOUT_MS,
        fail_on_status_code=False,
    )
    payload = core._read_json_response(response, diagnostic, kind="product_search")
    observed_total, products_returned = core._read_shape(payload)
    diagnostic["product_requests_completed"] += 1
    if observed_total != expected_total:
        diagnostic["partition_expected_total"] = expected_total
        diagnostic["partition_observed_total"] = observed_total
        raise full.FullCatalogError(
            "partition_total_changed_mid_run", diagnostic=diagnostic
        )
    if products_returned > page_size:
        diagnostic["observed_products_on_page"] = products_returned
        raise full.FullCatalogError(
            "unexpected_product_page_overflow", diagnostic=diagnostic
        )
    expected_on_page = min(
        page_size,
        max(expected_total - ((page - 1) * page_size), 0),
    )
    if products_returned < expected_on_page:
        diagnostic["short_product_pages"] += 1
        diagnostic["last_expected_products_on_page"] = expected_on_page
        diagnostic["last_observed_products_on_page"] = products_returned
    elif products_returned > expected_on_page:
        diagnostic["oversized_product_pages"] += 1
        diagnostic["last_expected_products_on_page"] = expected_on_page
        diagnostic["last_observed_products_on_page"] = products_returned

    _process_page(
        payload=payload,
        request_url=request_url,
        extractor=extractor,
        run_id=run_id,
        page_size=page_size,
        unique_products=unique_products,
        unique_skus=unique_skus,
        bucket_products=bucket_products,
        all_products=all_products,
        diagnostic=diagnostic,
        recovery=recovery,
    )
    return products_returned


def _resolve_root_total_after_partitions(
    *,
    context: Any,
    root_url: str,
    initial_total: int,
    unique_product_count: int,
    diagnostic: dict[str, Any],
) -> int:
    """Relee una sola vez el total raíz si el binding inicial quedó transitorio.

    Sólo corrige el total cuando los totales observados durante la travesía de
    todas las particiones coinciden exactamente con las identidades únicas
    extraídas. La estimación previa del árbol de facets puede quedar obsoleta
    mientras se recorre el catálogo; sus cambios quedan auditados por
    ``partition_total_adjustments`` y no sustituyen a la evidencia observada.
    Cualquier otra discrepancia conserva el comportamiento fail-closed.
    """

    if unique_product_count == initial_total:
        return initial_total
    if diagnostic["partition_observed_total_sum"] != unique_product_count:
        raise full.FullCatalogError(
            "unique_product_coverage_mismatch", diagnostic=diagnostic
        )

    _ensure_request_budget(diagnostic)
    response = context.request.get(
        root_url,
        timeout=core.PRODUCT_REQUEST_TIMEOUT_MS,
        fail_on_status_code=False,
    )
    payload = core._read_json_response(
        response, diagnostic, kind="root_product_search_recheck"
    )
    rechecked_total, _ = core._read_shape(payload)
    diagnostic["product_requests_completed"] += 1
    if rechecked_total <= 0 or rechecked_total != unique_product_count:
        raise full.FullCatalogError(
            "unique_product_coverage_mismatch", diagnostic=diagnostic
        )
    diagnostic["catalog_products_reported"] = rechecked_total
    return rechecked_total


def _run_catalog(*, page_size: int, delay_seconds: float) -> dict[str, Any]:
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
        "partition_quantity_estimate_sum": 0,
        "partition_observed_total_sum": 0,
        "planned_product_requests": 0,
        "product_requests_completed": 0,
        "partition_total_adjustments": 0,
        "max_bucket_brand_count": 0,
        "max_bucket_url_bytes": 0,
        "brand_bucket_max_brands": MAX_BRANDS_PER_BUCKET,
        "brand_bucket_url_limit_bytes": MAX_PRODUCT_SEARCH_URL_BYTES,
        "short_product_pages": 0,
        "oversized_product_pages": 0,
        "partition_recovery_passes": 0,
        "recovery_pages_completed": 0,
        "recovery_duplicate_skus_ignored": 0,
        "partition_products_recovered": 0,
    }
    run_id = f"sps_operational_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    unique_products: set[str] = set()
    unique_skus: set[tuple[str, str]] = set()
    all_products: list[dict[str, Any]] = []

    with sync_playwright() as pw:
        browser, _ = full.launch_compatible_chromium(pw)
        try:
            context = browser.new_context(service_workers="block")
            page = context.new_page()
            home = page.goto(
                full.radiography.TARGET_URL,
                wait_until="domcontentloaded",
                timeout=20_000,
            )
            if home is not None and int(home.status) in {403, 429}:
                diagnostic["blocked_http_status_observed"] = int(home.status)
                raise full.FullCatalogError(f"http_{home.status}", diagnostic=diagnostic)
            if full.passive._blocked_surface(page):
                raise full.FullCatalogError("blocked_or_login_surface", diagnostic=diagnostic)
            page.wait_for_timeout(250)

            try:
                ensure_operational_city(page, max_dom_reresolutions=1)
            except LocationInitializationError as exc:
                raise full.FullCatalogError(exc.reason, diagnostic=diagnostic) from exc
            diagnostic["location_verified_same_run"] = True

            root_url = _product_url(
                selected_facets=[dict(core.ROOT_FACET)],
                page=1,
                page_size=page_size,
            )
            root_response = context.request.get(
                root_url,
                timeout=core.PRODUCT_REQUEST_TIMEOUT_MS,
                fail_on_status_code=False,
            )
            root_payload = core._read_json_response(
                root_response, diagnostic, kind="root_product_search"
            )
            root_total, _ = core._read_shape(root_payload)
            if root_total <= 0:
                raise full.FullCatalogError("catalog_root_total_invalid", diagnostic=diagnostic)
            diagnostic["catalog_products_reported"] = root_total
            diagnostic["product_requests_completed"] += 1

            tree_response = context.request.get(
                core._category_tree_url(),
                timeout=core.FACET_REQUEST_TIMEOUT_MS,
                fail_on_status_code=False,
            )
            tree_payload = core._read_json_response(
                tree_response, diagnostic, kind="brand_facets"
            )
            data = tree_payload.get("data")
            if not isinstance(data, Mapping):
                raise full.FullCatalogError("brand_facets_data_missing", diagnostic=diagnostic)
            try:
                normalized = core.LaColoniaFacetDiscoveryAdapter._normalize_tree(data)
                if normalized.get("sampling") is not False:
                    raise ValueError("brand_facets_sampling_detected")
                brand_values = brand._brand_values(normalized)
                partitions = _build_brand_buckets(
                    brand_values,
                    page_size=page_size,
                )
            except Exception as exc:
                diagnostic["frontier_error"] = str(exc)[:120]
                raise full.FullCatalogError("brand_buckets_not_usable", diagnostic=diagnostic) from exc

            diagnostic["partitions_detected"] = len(partitions)
            diagnostic["partition_quantity_estimate_sum"] = sum(
                partition.quantity for partition in partitions
            )
            if partitions:
                diagnostic["max_bucket_brand_count"] = max(
                    len(partition.path) for partition in partitions
                )
                diagnostic["max_bucket_url_bytes"] = max(
                    _bucket_url_bytes(
                        [value for key, value in partition.path if key == "brand"],
                        page_size=page_size,
                    )
                    for partition in partitions
                )
            diagnostic["planned_product_requests"] = sum(
                math.ceil(partition.quantity / page_size) for partition in partitions
            ) + 1
            if diagnostic["planned_product_requests"] > core.MAX_PLANNED_PRODUCT_REQUESTS:
                raise full.FullCatalogError(
                    "partition_request_budget_exceeded", diagnostic=diagnostic
                )

            extractor = core.LaColoniaExtractor()
            for partition_index, partition in enumerate(partitions, start=1):
                facets = core._partition_facets(partition.path)
                brand_values_in_bucket = [
                    value for key, value in partition.path if key == "brand"
                ]
                preflight_url_bytes = _bucket_url_bytes(
                    brand_values_in_bucket,
                    page_size=page_size,
                )
                if preflight_url_bytes > MAX_PRODUCT_SEARCH_URL_BYTES:
                    diagnostic["max_bucket_url_bytes"] = max(
                        diagnostic["max_bucket_url_bytes"], preflight_url_bytes
                    )
                    raise full.FullCatalogError(
                        "brand_bucket_url_limit_exceeded", diagnostic=diagnostic
                    )

                _ensure_request_budget(diagnostic)
                first_url = _product_url(
                    selected_facets=facets,
                    page=1,
                    page_size=page_size,
                )
                diagnostic["pages_attempted"] += 1
                first_response = context.request.get(
                    first_url,
                    timeout=core.PRODUCT_REQUEST_TIMEOUT_MS,
                    fail_on_status_code=False,
                )
                first_payload = core._read_json_response(
                    first_response, diagnostic, kind="product_search"
                )
                actual_total, products_returned = core._read_shape(first_payload)
                diagnostic["product_requests_completed"] += 1
                if actual_total <= 0 or actual_total > core.SEARCH_WINDOW_MAX_PRODUCTS:
                    diagnostic["partition_observed_total"] = actual_total
                    raise full.FullCatalogError(
                        "brand_bucket_outside_search_window", diagnostic=diagnostic
                    )
                if actual_total != partition.quantity:
                    diagnostic["partition_total_adjustments"] += 1
                diagnostic["partition_observed_total_sum"] += actual_total

                bucket_products: set[str] = set()
                expected_first = min(page_size, actual_total)
                if products_returned < expected_first:
                    diagnostic["short_product_pages"] += 1
                    diagnostic["last_expected_products_on_page"] = expected_first
                    diagnostic["last_observed_products_on_page"] = products_returned
                elif products_returned > expected_first:
                    diagnostic["oversized_product_pages"] += 1
                    diagnostic["last_expected_products_on_page"] = expected_first
                    diagnostic["last_observed_products_on_page"] = products_returned
                if products_returned > page_size:
                    raise full.FullCatalogError(
                        "unexpected_product_page_overflow", diagnostic=diagnostic
                    )
                _process_page(
                    payload=first_payload,
                    request_url=first_url,
                    extractor=extractor,
                    run_id=run_id,
                    page_size=page_size,
                    unique_products=unique_products,
                    unique_skus=unique_skus,
                    bucket_products=bucket_products,
                    all_products=all_products,
                    diagnostic=diagnostic,
                    recovery=False,
                )

                pages = max(math.ceil(actual_total / page_size), 1)
                for partition_page in range(2, pages + 1):
                    if delay_seconds > 0:
                        time.sleep(delay_seconds)
                    _fetch_known_total_page(
                        context=context,
                        facets=facets,
                        page=partition_page,
                        page_size=page_size,
                        order_by=PRIMARY_ORDER_BY,
                        expected_total=actual_total,
                        extractor=extractor,
                        run_id=run_id,
                        unique_products=unique_products,
                        unique_skus=unique_skus,
                        bucket_products=bucket_products,
                        all_products=all_products,
                        diagnostic=diagnostic,
                        recovery=False,
                    )

                primary_bucket_count = len(bucket_products)
                if primary_bucket_count != actual_total:
                    diagnostic["partition_recovery_passes"] += 1
                    before_recovery = primary_bucket_count
                    for recovery_page in range(1, pages + 1):
                        if len(bucket_products) >= actual_total:
                            break
                        if delay_seconds > 0:
                            time.sleep(delay_seconds)
                        _fetch_known_total_page(
                            context=context,
                            facets=facets,
                            page=recovery_page,
                            page_size=page_size,
                            order_by=RECOVERY_ORDER_BY,
                            expected_total=actual_total,
                            extractor=extractor,
                            run_id=run_id,
                            unique_products=unique_products,
                            unique_skus=unique_skus,
                            bucket_products=bucket_products,
                            all_products=all_products,
                            diagnostic=diagnostic,
                            recovery=True,
                        )
                    diagnostic["partition_products_recovered"] += max(
                        len(bucket_products) - before_recovery, 0
                    )

                if len(bucket_products) != actual_total:
                    diagnostic["partition_expected_total"] = actual_total
                    diagnostic["partition_observed_total"] = len(bucket_products)
                    raise full.FullCatalogError(
                        "partition_unique_product_coverage_mismatch",
                        diagnostic=diagnostic,
                    )

                diagnostic["partitions_completed"] += 1
                if delay_seconds > 0 and partition_index < len(partitions):
                    time.sleep(delay_seconds)

            diagnostic["unique_products_extracted"] = len(unique_products)
            if diagnostic["partitions_completed"] != diagnostic["partitions_detected"]:
                raise full.FullCatalogError(
                    "partition_coverage_incomplete", diagnostic=diagnostic
                )
            root_total = _resolve_root_total_after_partitions(
                context=context,
                root_url=root_url,
                initial_total=root_total,
                unique_product_count=len(unique_products),
                diagnostic=diagnostic,
            )

            observed_at_utc = full._utc_text()
            rows_fields = catalog_rows_fields(all_products, observed_at_utc=observed_at_utc)
            artifact = {
                "schema_version": "7",
                "result": "success",
                "catalog_type": "la_colonia_sps_full_read_only",
                "supermarket_id": "la_colonia",
                "location_id": full.passive.MVP_LOCATION_ID,
                "city": full.passive.TARGET_CITY,
                "capture_strategy": CAPTURE_STRATEGY,
                "partition_strategy": PARTITION_STRATEGY,
                "location_verified_same_run": True,
                "location_verification_method": LOCATION_VERIFICATION_METHOD,
                "region_binding_fingerprint_verified": False,
                "binding_source_key_verified": False,
                "radiography_executed": False,
                "page_size": page_size,
                "brand_bucket_estimate_capacity": BUCKET_ESTIMATE_CAPACITY,
                "brand_bucket_max_brands": MAX_BRANDS_PER_BUCKET,
                "brand_bucket_url_limit_bytes": MAX_PRODUCT_SEARCH_URL_BYTES,
                "max_bucket_brand_count": diagnostic["max_bucket_brand_count"],
                "max_bucket_url_bytes": diagnostic["max_bucket_url_bytes"],
                "catalog_products_reported": root_total,
                "unique_products_extracted": len(unique_products),
                "catalog_product_coverage": len(unique_products) / root_total,
                "partitions_detected": diagnostic["partitions_detected"],
                "partitions_completed": diagnostic["partitions_completed"],
                "partition_quantity_estimate_sum": diagnostic[
                    "partition_quantity_estimate_sum"
                ],
                "partition_observed_total_sum": diagnostic[
                    "partition_observed_total_sum"
                ],
                "partition_total_adjustments": diagnostic[
                    "partition_total_adjustments"
                ],
                "planned_product_requests": diagnostic["planned_product_requests"],
                "product_requests_completed": diagnostic[
                    "product_requests_completed"
                ],
                "short_product_pages": diagnostic["short_product_pages"],
                "oversized_product_pages": diagnostic["oversized_product_pages"],
                "partition_recovery_passes": diagnostic["partition_recovery_passes"],
                "recovery_pages_completed": diagnostic["recovery_pages_completed"],
                "partition_products_recovered": diagnostic[
                    "partition_products_recovered"
                ],
                "recovery_duplicate_skus_ignored": diagnostic[
                    "recovery_duplicate_skus_ignored"
                ],
                "skus_extracted": rows_fields["skus_extracted"],
                "skus_with_price": rows_fields["skus_with_price"],
                "skus_without_price": rows_fields["skus_without_price"],
                "skus_unpriced_unavailable": rows_fields["skus_unpriced_unavailable"],
                "duplicate_skus_across_partitions": diagnostic[
                    "duplicate_skus_across_pages"
                ],
                "catalog_complete": True,
                "validation_passed": True,
                "observed_at_utc": observed_at_utc,
                "products": rows_fields["products"],
                "unpriced_unavailable": rows_fields["unpriced_unavailable"],
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


def _failure_artifact(exc: Any) -> dict[str, Any]:
    failure = full._safe_failure(exc.reason, exc.diagnostic)
    failure["capture_strategy"] = CAPTURE_STRATEGY
    failure["partition_strategy"] = PARTITION_STRATEGY
    failure["radiography_executed"] = False
    failure["location_verification_method"] = LOCATION_VERIFICATION_METHOD
    failure["region_binding_fingerprint_verified"] = False
    failure["binding_source_key_verified"] = False
    failure["brand_bucket_estimate_capacity"] = BUCKET_ESTIMATE_CAPACITY
    failure["brand_bucket_max_brands"] = MAX_BRANDS_PER_BUCKET
    failure["brand_bucket_url_limit_bytes"] = MAX_PRODUCT_SEARCH_URL_BYTES
    for key in (
        "partitions_detected",
        "partitions_completed",
        "partition_quantity_estimate_sum",
        "partition_observed_total_sum",
        "partition_total_adjustments",
        "planned_product_requests",
        "product_requests_completed",
        "unique_products_extracted",
        "partition_expected_total",
        "partition_observed_total",
        "last_expected_products_on_page",
        "last_observed_products_on_page",
        "max_bucket_brand_count",
        "max_bucket_url_bytes",
        "short_product_pages",
        "oversized_product_pages",
        "partition_recovery_passes",
        "recovery_pages_completed",
        "partition_products_recovered",
        "recovery_duplicate_skus_ignored",
    ):
        if key in exc.diagnostic:
            failure[key] = int(exc.diagnostic[key])
    if "frontier_error" in exc.diagnostic:
        failure["frontier_error"] = str(exc.diagnostic["frontier_error"])
    return failure


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
        artifact = _run_catalog(
            page_size=args.page_size,
            delay_seconds=args.delay_seconds,
        )
        full._write_json(artifact, args.output)
        full._write_csv(list(artifact["products"]), args.csv_output)
    except full.FullCatalogError as exc:
        full._write_json(_failure_artifact(exc), args.output)
        print(f"sps_operational_catalog_stopped:{exc.reason}", file=sys.stderr)
        return 3

    print(
        json.dumps(
            {
                "result": "success",
                "catalog_products_reported": artifact["catalog_products_reported"],
                "unique_products_extracted": artifact["unique_products_extracted"],
                "partitions_completed": artifact["partitions_completed"],
                "product_requests_completed": artifact["product_requests_completed"],
                "skus_extracted": artifact["skus_extracted"],
                "skus_with_price": artifact["skus_with_price"],
                "skus_unpriced_unavailable": artifact["skus_unpriced_unavailable"],
                "catalog_complete": artifact["catalog_complete"],
                "radiography_executed": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
