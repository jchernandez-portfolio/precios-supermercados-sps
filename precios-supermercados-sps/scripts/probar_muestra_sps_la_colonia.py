#!/usr/bin/env python3
"""Obtiene una muestra live mínima de La Colonia bajo contexto SPS.

Este entrypoint existe únicamente para el MVP read-only. Reutiliza el selector de
ciudad y el parser comercial ya probados; no usa Cloudflare, no persiste en Sheets
y nunca concede autoridad productiva. El único payload comercial que conserva es
una muestra pública y acotada de productos/precios ya normalizada para inspección.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from precios_supermercados.diagnostics.la_colonia_sps_context_diagnostic import (  # noqa: E402
    DiagnosticSafetyError,
    launch_compatible_chromium,
)
from precios_supermercados.diagnostics.location_binding_dom_controls import (  # noqa: E402
    LocationControlResolutionError,
    activate_city_control,
    open_location_selector,
    resolve_exact_city_control,
    verify_structural_city_selection,
)
from precios_supermercados.scrapers.base import StructureChangedError  # noqa: E402
from precios_supermercados.scrapers.la_colonia import (  # noqa: E402
    CATALOG_URL,
    LaColoniaExtractor,
)

TARGET_HOME = "https://www.lacolonia.com/"
TARGET_HOST = "www.lacolonia.com"
TARGET_CITY = "San Pedro Sula"
MVP_LOCATION_ID = "la_colonia_sps"
ALLOWED_SAMPLE_SIZES = (5, 10)
CAPTURE_TIMEOUT_SECONDS = 15.0
CANDIDATE_SETTLE_SECONDS = 1.5
CITY_VERIFY_TIMEOUT_SECONDS = 5.0
MINIMUM_ACTION_DELAY_MS = 1_500

_FORBIDDEN_ARTIFACT_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "cookies",
        "headers",
        "jwt",
        "regionid",
        "request_url",
        "session",
        "sessionid",
        "token",
        "vtex_session",
        "vtex_segment",
    }
)
_BLOCK_PAGE_MARKERS = (
    "captcha",
    "verify you are human",
    "verifica que eres humano",
    "access denied",
    "acceso denegado",
)


class MvpSampleError(RuntimeError):
    """Detención controlada del sample MVP con diagnóstico durable sanitizado."""

    def __init__(
        self,
        reason: str,
        *,
        diagnostic: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.diagnostic = dict(diagnostic or {})


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _product_search_payload(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    data = value.get("data")
    if not isinstance(data, Mapping):
        return False
    search = data.get("productSearch")
    if not isinstance(search, Mapping):
        return False
    products = search.get("products")
    return (
        isinstance(products, Sequence)
        and not isinstance(products, (str, bytes))
        and any(isinstance(product, Mapping) for product in products)
    )


def _product_search_shape(value: Mapping[str, Any]) -> tuple[int, int] | None:
    """Devuelve (total, productos) sin conservar payload ni contexto de request."""

    if not _product_search_payload(value):
        return None
    search = value["data"]["productSearch"]
    products = search.get("products") or []
    try:
        total = int(search.get("recordsFiltered", len(products)))
    except (TypeError, ValueError):
        total = len(products)
    return max(total, len(products)), len(products)


def _request_variables(request: Any) -> Mapping[str, Any]:
    """Lee variables sólo en memoria para distinguir candidatos; nunca se persisten."""

    try:
        payload = request.post_data_json
    except Exception:
        payload = None
    if isinstance(payload, Mapping):
        variables = payload.get("variables")
        if isinstance(variables, Mapping):
            return variables
        if isinstance(variables, str):
            try:
                decoded = json.loads(variables)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, Mapping):
                return decoded
    try:
        raw = parse_qs(urlsplit(str(request.url)).query).get("variables", [None])[0]
        decoded = json.loads(raw) if raw else None
    except (TypeError, ValueError, json.JSONDecodeError):
        decoded = None
    return decoded if isinstance(decoded, Mapping) else {}


def _historical_catalog_signature(response: Any) -> bool:
    """Reconoce la forma histórica sin exigirla al sitio actual."""

    try:
        variables = _request_variables(response.request)
    except Exception:
        return False
    if str(variables.get("query") or "").casefold() == "supermercado":
        return True
    facets = variables.get("selectedFacets")
    if not isinstance(facets, Sequence) or isinstance(facets, (str, bytes)):
        return False
    return any(
        isinstance(facet, Mapping)
        and str(facet.get("key") or "").casefold() == "category-1"
        and str(facet.get("value") or "").casefold() == "supermercado"
        for facet in facets
    )


def _catalog_candidate_rank(
    response: Any,
    payload: Mapping[str, Any],
) -> tuple[int, int, int, int]:
    """Ordena respuestas productSearch observadas durante la navegación al catálogo.

    La forma exacta `query=supermercado/category-1` observada históricamente recibe
    prioridad. Si VTEX cambia únicamente las variables de routing, el fallback usa
    señales públicas del payload y del rango solicitado, prefiriendo la búsqueda
    con mayor `recordsFiltered`. No se persiste ninguna variable ni URL de request.
    """

    shape = _product_search_shape(payload)
    if shape is None:
        return (0, 0, 0, 0)
    total, product_count = shape
    variables = _request_variables(response.request)
    try:
        start = int(variables.get("from", 0))
    except (TypeError, ValueError):
        start = 0
    try:
        end = int(variables.get("to", start + product_count - 1))
    except (TypeError, ValueError):
        end = start + product_count - 1
    requested_span = max(end - start + 1, 0)
    historical = 1 if _historical_catalog_signature(response) else 0
    return (historical, total, requested_span, product_count)


def _is_graphql_response(response: Any) -> bool:
    try:
        parts = urlsplit(str(response.url))
    except Exception:
        return False
    return (
        (parts.hostname or "").casefold() == TARGET_HOST
        and parts.path == "/_v/segment/graphql/v1"
    )


def _public_product(product: Any) -> dict[str, Any]:
    raw = product.raw_values
    return {
        "source_key_type": product.source_key_type.value,
        "source_key": product.source_key,
        "source_name": product.source_name,
        "brand": product.source_brand,
        "presentation": product.source_presentation,
        "category": product.source_category,
        "product_id": raw.get("product_id"),
        "item_id": raw.get("item_id"),
        "reference": raw.get("reference"),
        "ean": raw.get("ean"),
        "current_price": raw.get("current_price"),
        "reported_regular_price": raw.get("reported_regular_price"),
        "is_promotion": raw.get("is_promotion"),
        "availability": raw.get("availability"),
    }


def _validate_artifact_shape(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = "".join(
                ch for ch in str(key).casefold() if ch.isalnum() or ch == "_"
            )
            if normalized in _FORBIDDEN_ARTIFACT_KEYS:
                raise MvpSampleError(f"artifact_forbidden_key:{key}")
            _validate_artifact_shape(nested)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for nested in value:
            _validate_artifact_shape(nested)


def build_sample_artifact(
    *,
    extraction_result: Any,
    sample_size: int,
    observed_at_utc: datetime,
    location_verified_same_run: bool,
) -> dict[str, Any]:
    """Construye la única salida durable del MVP sin filtrar contexto raw."""

    if sample_size not in ALLOWED_SAMPLE_SIZES:
        raise ValueError("sample_size fuera del contrato MVP")
    if observed_at_utc.tzinfo is None or observed_at_utc.utcoffset() is None:
        raise ValueError("observed_at_utc debe incluir zona horaria")
    if location_verified_same_run is not True:
        raise MvpSampleError("sps_location_not_verified_same_run")

    products = tuple(extraction_result.products)[:sample_size]
    if not products:
        raise MvpSampleError("sample_without_products")
    with_price = sum(
        product.raw_values.get("current_price") is not None for product in products
    )
    validation_passed = bool(extraction_result.accepted and with_price > 0)
    metrics = extraction_result.metrics
    artifact = {
        "schema_version": "1",
        "sample_type": "la_colonia_sps_mvp_read_only",
        "sample_only": True,
        "supermarket_id": "la_colonia",
        "location_id": MVP_LOCATION_ID,
        "city": TARGET_CITY,
        "location_verified_same_run": True,
        "observed_at_utc": observed_at_utc.astimezone(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "sample_size_requested": sample_size,
        "sample_skus_returned": len(products),
        "sample_skus_with_price": with_price,
        "catalog_products_reported": metrics.products_discovered,
        "page_products_returned": metrics.products_returned,
        "page_skus_parsed": metrics.skus_extracted,
        "validation_passed": validation_passed,
        "quality_events": list(extraction_result.quality_events),
        "products": [_public_product(product) for product in products],
        "production_authority": False,
        "catalog_accepted": False,
        "commercial_persistence": False,
        "extraction_enabled": False,
        "raw_context_persisted": False,
    }
    _validate_artifact_shape(artifact)
    return artifact


def build_failure_artifact(
    *,
    reason: str,
    diagnostic: Mapping[str, Any] | None = None,
    observed_at_utc: datetime | None = None,
) -> dict[str, Any]:
    """Conserva sólo contadores sanitizados cuando el sample no produce productos."""

    observed_at = observed_at_utc or _utc_now()
    safe_diagnostic = {
        "location_verified_same_run": bool(
            (diagnostic or {}).get("location_verified_same_run", False)
        ),
        "graphql_responses_seen": int((diagnostic or {}).get("graphql_responses_seen", 0)),
        "product_search_payloads_seen": int(
            (diagnostic or {}).get("product_search_payloads_seen", 0)
        ),
        "catalog_candidates_seen": int(
            (diagnostic or {}).get("catalog_candidates_seen", 0)
        ),
        "blocked_http_status_observed": (
            int((diagnostic or {})["blocked_http_status_observed"])
            if (diagnostic or {}).get("blocked_http_status_observed") in {403, 429}
            else None
        ),
    }
    artifact = {
        "schema_version": "1",
        "sample_type": "la_colonia_sps_mvp_read_only",
        "sample_only": True,
        "result": "stopped",
        "reason": reason,
        "supermarket_id": "la_colonia",
        "location_id": MVP_LOCATION_ID,
        "city": TARGET_CITY,
        "observed_at_utc": observed_at.astimezone(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        **safe_diagnostic,
        "production_authority": False,
        "catalog_accepted": False,
        "commercial_persistence": False,
        "extraction_enabled": False,
        "raw_context_persisted": False,
    }
    _validate_artifact_shape(artifact)
    return artifact


def _blocked_surface(page: Any) -> bool:
    try:
        current = urlsplit(str(page.url))
        if (current.hostname or "").casefold() != TARGET_HOST:
            return True
        if current.path.casefold().startswith(("/login", "/account", "/checkout")):
            return True
    except Exception:
        return True
    try:
        text = str(page.locator("body").inner_text(timeout=1_000))[:8_000].casefold()
    except Exception:
        return False
    return any(marker in text for marker in _BLOCK_PAGE_MARKERS)


def _wait_for_city(page: Any) -> str:
    deadline = time.monotonic() + CITY_VERIFY_TIMEOUT_SECONDS
    last_error: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            verified = verify_structural_city_selection(page, TARGET_CITY)
            if verified and verified.casefold() == TARGET_CITY.casefold():
                return verified
        except LocationControlResolutionError as exc:
            last_error = exc
        page.wait_for_timeout(100)
    raise MvpSampleError("sps_city_selection_unverified") from last_error


def _run_live_sample(*, sample_size: int) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - dependencia fijada en requirements
        raise MvpSampleError("playwright_not_installed") from exc

    captured: list[
        tuple[tuple[int, int, int, int], str, Mapping[str, Any]]
    ] = []
    diagnostic: dict[str, Any] = {
        "location_verified_same_run": False,
        "graphql_responses_seen": 0,
        "product_search_payloads_seen": 0,
        "catalog_candidates_seen": 0,
        "blocked_http_status_observed": None,
    }

    with sync_playwright() as pw:
        browser, _ = launch_compatible_chromium(pw)
        try:
            context = browser.new_context(service_workers="block")
            page = context.new_page()

            home_response = page.goto(
                TARGET_HOME,
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            if home_response is not None and home_response.status in {403, 429}:
                diagnostic["blocked_http_status_observed"] = int(home_response.status)
                raise MvpSampleError(
                    f"http_{home_response.status}", diagnostic=diagnostic
                )
            if _blocked_surface(page):
                raise MvpSampleError("blocked_or_login_surface", diagnostic=diagnostic)

            open_location_selector(page)
            control = resolve_exact_city_control(page, TARGET_CITY)
            activate_city_control(control, TARGET_CITY)
            _wait_for_city(page)
            diagnostic["location_verified_same_run"] = True
            page.wait_for_timeout(MINIMUM_ACTION_DELAY_MS)

            def observe(response: Any) -> None:
                try:
                    parts = urlsplit(str(response.url))
                    if (parts.hostname or "").casefold() != TARGET_HOST:
                        return
                    if int(response.status) in {403, 429}:
                        diagnostic["blocked_http_status_observed"] = int(response.status)
                        return
                    if not _is_graphql_response(response):
                        return
                    diagnostic["graphql_responses_seen"] += 1
                    payload = response.json()
                    if not isinstance(payload, Mapping) or not _product_search_payload(payload):
                        return
                    diagnostic["product_search_payloads_seen"] += 1
                    rank = _catalog_candidate_rank(response, payload)
                    if rank == (0, 0, 0, 0):
                        return
                    diagnostic["catalog_candidates_seen"] += 1
                    captured.append((rank, str(response.url), payload))
                except Exception:
                    return

            page.on("response", observe)
            catalog_response = page.goto(
                CATALOG_URL,
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            if catalog_response is not None and catalog_response.status in {403, 429}:
                diagnostic["blocked_http_status_observed"] = int(catalog_response.status)
                raise MvpSampleError(
                    f"http_{catalog_response.status}", diagnostic=diagnostic
                )

            deadline = time.monotonic() + CAPTURE_TIMEOUT_SECONDS
            first_candidate_at: float | None = None
            while time.monotonic() < deadline:
                if diagnostic["blocked_http_status_observed"] in {403, 429}:
                    raise MvpSampleError(
                        f"http_{diagnostic['blocked_http_status_observed']}",
                        diagnostic=diagnostic,
                    )
                if _blocked_surface(page):
                    raise MvpSampleError(
                        "blocked_or_login_surface", diagnostic=diagnostic
                    )
                if captured and first_candidate_at is None:
                    first_candidate_at = time.monotonic()
                if first_candidate_at is not None:
                    if captured and max(item[0][0] for item in captured) == 1:
                        break
                    if time.monotonic() - first_candidate_at >= CANDIDATE_SETTLE_SECONDS:
                        break
                page.wait_for_timeout(250)
            if not captured:
                raise MvpSampleError(
                    "catalog_product_search_response_not_observed",
                    diagnostic=diagnostic,
                )

            _wait_for_city(page)
            diagnostic["location_verified_same_run"] = True
            _, source_url, payload = max(captured, key=lambda item: item[0])
            observed_at = _utc_now()
            search = payload["data"]["productSearch"]
            products = search.get("products") or []
            page_size = max(len(products), 1)
            try:
                result = LaColoniaExtractor(clock=lambda: observed_at).parse_payload(
                    payload,
                    scrape_run_id=f"mvp_sps_{observed_at.strftime('%Y%m%dT%H%M%SZ')}",
                    source_url=source_url,
                    page_size=page_size,
                )
            except StructureChangedError as exc:
                raise MvpSampleError(
                    "product_payload_not_parseable", diagnostic=diagnostic
                ) from exc
            return build_sample_artifact(
                extraction_result=result,
                sample_size=sample_size,
                observed_at_utc=observed_at,
                location_verified_same_run=True,
            )
        finally:
            browser.close()


def _write_artifact(value: Mapping[str, Any], output_path: Path) -> None:
    _validate_artifact_shape(value)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live-read-only",
        action="store_true",
        help="Fuse explícito: esta invocación puede abrir tráfico público read-only",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        choices=ALLOWED_SAMPLE_SIZES,
        default=10,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "run-artifacts" / "mvp-sample.json",
    )
    args = parser.parse_args(argv)
    if not args.live_read_only:
        parser.error("la muestra live requiere --live-read-only")

    try:
        artifact = _run_live_sample(sample_size=args.sample_size)
        _write_artifact(artifact, args.output)
    except MvpSampleError as exc:
        failure = build_failure_artifact(
            reason=exc.reason,
            diagnostic=exc.diagnostic,
        )
        _write_artifact(failure, args.output)
        print(f"mvp_sample_stopped:{type(exc).__name__}:{exc}", file=sys.stderr)
        return 3
    except (DiagnosticSafetyError, LocationControlResolutionError) as exc:
        failure = build_failure_artifact(reason=str(exc))
        _write_artifact(failure, args.output)
        print(f"mvp_sample_stopped:{type(exc).__name__}:{exc}", file=sys.stderr)
        return 3

    print(
        json.dumps(
            {
                "location_id": artifact["location_id"],
                "sample_skus_returned": artifact["sample_skus_returned"],
                "sample_skus_with_price": artifact["sample_skus_with_price"],
                "validation_passed": artifact["validation_passed"],
                "production_authority": False,
                "catalog_accepted": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if artifact["validation_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
