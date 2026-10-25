#!/usr/bin/env python3
"""Obtiene una muestra MVP SPS con un único fallback GraphQL evidence-bound.

Este entrypoint existe porque dos navegaciones reales al catálogo verificaron SPS
pero no emitieron ``data.productSearch`` de forma pasiva. Mantiene el mismo
BrowserContext, observa primero sin añadir tráfico comercial y sólo permite una
consulta explícita si una señal ``regionId`` del mismo run coincide exactamente con
el fingerprint SPS ya demostrado. El valor raw vive sólo en memoria y nunca se
escribe al artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import probar_muestra_sps_la_colonia as passive  # noqa: E402
from precios_supermercados.diagnostics.la_colonia_sps_context_diagnostic import (  # noqa: E402
    DiagnosticSafetyError,
    launch_compatible_chromium,
)
from precios_supermercados.diagnostics.location_binding_dom_controls import (  # noqa: E402
    LocationControlResolutionError,
    activate_city_control,
    open_location_selector,
    resolve_exact_city_control,
)
from precios_supermercados.scrapers.base import StructureChangedError  # noqa: E402
from precios_supermercados.scrapers.la_colonia import LaColoniaExtractor  # noqa: E402
from precios_supermercados.scrapers.la_colonia_graphql import (  # noqa: E402
    GRAPHQL_ENDPOINT,
    build_product_search_url,
)

SPS_REGION_FINGERPRINT = "d7732eccc99c8530a6d29cce4244920e65e85c1d5492facb05469dc3589cb8b7"
PASSIVE_OBSERVE_SECONDS = 3.0
EXPLICIT_REQUEST_TIMEOUT_MS = 15_000
MAX_REPLAYABLE_REGION_PLACEMENTS = 4
_REGION_KEY_ALIASES = frozenset({"region", "regionid", "xvtexregion"})


def _stable_fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_key(value: str) -> str:
    return "".join(character for character in str(value).casefold() if character.isalnum())


def _is_region_key(value: str) -> bool:
    return _canonical_key(value) in _REGION_KEY_ALIASES


def _nested_region_values(value: Any) -> list[Any]:
    found: list[Any] = []
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if _is_region_key(str(key)):
                found.append(nested)
            found.extend(_nested_region_values(nested))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for nested in value:
            found.extend(_nested_region_values(nested))
    return found


class RegionContextTracker:
    """Conserva sólo regionId efímero que coincide con el binding SPS canónico."""

    def __init__(self, *, expected_fingerprint: str = SPS_REGION_FINGERPRINT) -> None:
        self.expected_fingerprint = expected_fingerprint
        self.active = False
        self.fingerprint_verified = False
        self.body_only_match_observed = False
        self._replayable: list[tuple[str, str, str]] = []

    def reset_and_enable(self) -> None:
        self.active = True
        self.fingerprint_verified = False
        self.body_only_match_observed = False
        self._replayable.clear()

    def _matches(self, value: Any) -> bool:
        try:
            matched = _stable_fingerprint(value) == self.expected_fingerprint
        except (TypeError, ValueError):
            return False
        if matched:
            self.fingerprint_verified = True
        return matched

    def _add_replayable(self, placement: str, key: str, value: Any) -> None:
        if not isinstance(value, str) or not self._matches(value):
            return
        item = (placement, str(key), value)
        if item not in self._replayable:
            self._replayable.append(item)

    def observe_request(self, request: Any) -> None:
        if not self.active:
            return
        try:
            for key, value in request.headers.items():
                if _is_region_key(str(key)):
                    self._add_replayable("header", str(key), value)
        except Exception:
            pass
        try:
            for key, value in parse_qsl(
                urlsplit(str(request.url)).query,
                keep_blank_values=True,
            ):
                if _is_region_key(key):
                    self._add_replayable("query", key, value)
        except Exception:
            pass
        payload: Any = None
        try:
            payload = request.post_data_json
        except Exception:
            payload = None
        for value in _nested_region_values(payload):
            if self._matches(value):
                replayed = any(item[2] == value for item in self._replayable)
                if not replayed:
                    self.body_only_match_observed = True

    @property
    def replayable_count(self) -> int:
        return len(self._replayable)

    def replay_context(self) -> tuple[dict[str, str], tuple[tuple[str, str], ...]]:
        if not self.fingerprint_verified:
            raise passive.MvpSampleError("sps_region_binding_not_observed_same_run")
        if not self._replayable:
            reason = (
                "sps_region_binding_observed_but_not_replayable"
                if self.body_only_match_observed
                else "sps_region_binding_not_replayable"
            )
            raise passive.MvpSampleError(reason)
        if len(self._replayable) > MAX_REPLAYABLE_REGION_PLACEMENTS:
            raise passive.MvpSampleError("sps_region_binding_placement_unreasonable")
        headers: dict[str, str] = {}
        query: list[tuple[str, str]] = []
        for placement, key, value in self._replayable:
            if placement == "header":
                headers[key] = value
            elif placement == "query":
                query.append((key, value))
        if not headers and not query:
            raise passive.MvpSampleError("sps_region_binding_not_replayable")
        return headers, tuple(query)


def _append_query_context(url: str, additions: Sequence[tuple[str, str]]) -> str:
    parts = urlsplit(url)
    query = list(parse_qsl(parts.query, keep_blank_values=True))
    existing = {(key.casefold(), value) for key, value in query}
    for key, value in additions:
        marker = (key.casefold(), value)
        if marker not in existing:
            query.append((key, value))
            existing.add(marker)
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def _failure_artifact(reason: str, diagnostic: Mapping[str, Any]) -> dict[str, Any]:
    artifact = passive.build_failure_artifact(reason=reason, diagnostic=diagnostic)
    artifact.update(
        {
            "capture_strategy": "passive_then_single_explicit_bound",
            "region_binding_fingerprint_verified": bool(
                diagnostic.get("region_binding_fingerprint_verified", False)
            ),
            "region_context_replayable_placements": int(
                diagnostic.get("region_context_replayable_placements", 0)
            ),
            "region_context_body_only_observed": bool(
                diagnostic.get("region_context_body_only_observed", False)
            ),
            "explicit_product_search_requests": int(
                diagnostic.get("explicit_product_search_requests", 0)
            ),
        }
    )
    passive._validate_artifact_shape(artifact)
    return artifact


def _success_artifact(
    *,
    extraction_result: Any,
    sample_size: int,
    capture_mode: str,
    explicit_requests: int,
) -> dict[str, Any]:
    artifact = passive.build_sample_artifact(
        extraction_result=extraction_result,
        sample_size=sample_size,
        observed_at_utc=passive._utc_now(),
        location_verified_same_run=True,
    )
    artifact.update(
        {
            "capture_strategy": "passive_then_single_explicit_bound",
            "capture_mode": capture_mode,
            "region_binding_fingerprint_verified": True,
            "explicit_product_search_requests": explicit_requests,
        }
    )
    passive._validate_artifact_shape(artifact)
    return artifact


def _parse_payload(payload: Mapping[str, Any], *, sample_size: int) -> Any:
    search = payload["data"]["productSearch"]
    products = search.get("products") or []
    if not products:
        raise passive.MvpSampleError("sample_without_products")
    extractor = LaColoniaExtractor()
    try:
        return extractor.parse_payload(
            payload,
            scrape_run_id="mvp_bound_sample",
            source_url=GRAPHQL_ENDPOINT,
            page_size=max(1, min(len(products), sample_size)),
        )
    except StructureChangedError as exc:
        raise passive.MvpSampleError("product_payload_not_parseable") from exc


def _run_live_bound_sample(*, sample_size: int) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise passive.MvpSampleError("playwright_not_installed") from exc

    captured: list[tuple[tuple[int, int, int, int], Mapping[str, Any]]] = []
    diagnostic: dict[str, Any] = {
        "location_verified_same_run": False,
        "graphql_responses_seen": 0,
        "product_search_payloads_seen": 0,
        "catalog_candidates_seen": 0,
        "blocked_http_status_observed": None,
        "region_binding_fingerprint_verified": False,
        "region_context_replayable_placements": 0,
        "region_context_body_only_observed": False,
        "explicit_product_search_requests": 0,
    }
    tracker = RegionContextTracker()

    with sync_playwright() as pw:
        browser, _ = launch_compatible_chromium(pw)
        try:
            context = browser.new_context(service_workers="block")
            page = context.new_page()
            page.on("request", tracker.observe_request)

            home_response = page.goto(
                passive.TARGET_HOME,
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            if home_response is not None and home_response.status in {403, 429}:
                diagnostic["blocked_http_status_observed"] = int(home_response.status)
                raise passive.MvpSampleError(
                    f"http_{home_response.status}", diagnostic=diagnostic
                )
            if passive._blocked_surface(page):
                raise passive.MvpSampleError(
                    "blocked_or_login_surface", diagnostic=diagnostic
                )

            open_location_selector(page)
            control = resolve_exact_city_control(page, passive.TARGET_CITY)
            tracker.reset_and_enable()
            activate_city_control(control, passive.TARGET_CITY)
            passive._wait_for_city(page)
            diagnostic["location_verified_same_run"] = True
            page.wait_for_timeout(passive.MINIMUM_ACTION_DELAY_MS)

            def observe_response(response: Any) -> None:
                try:
                    parts = urlsplit(str(response.url))
                    if (parts.hostname or "").casefold() != passive.TARGET_HOST:
                        return
                    if int(response.status) in {403, 429}:
                        diagnostic["blocked_http_status_observed"] = int(response.status)
                        return
                    if not passive._is_graphql_response(response):
                        return
                    diagnostic["graphql_responses_seen"] += 1
                    payload = response.json()
                    if not isinstance(payload, Mapping) or not passive._product_search_payload(payload):
                        return
                    diagnostic["product_search_payloads_seen"] += 1
                    rank = passive._catalog_candidate_rank(response, payload)
                    if rank == (0, 0, 0, 0):
                        return
                    diagnostic["catalog_candidates_seen"] += 1
                    captured.append((rank, payload))
                except Exception:
                    return

            page.on("response", observe_response)
            catalog_response = page.goto(
                passive.CATALOG_URL,
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            if catalog_response is not None and catalog_response.status in {403, 429}:
                diagnostic["blocked_http_status_observed"] = int(catalog_response.status)
                raise passive.MvpSampleError(
                    f"http_{catalog_response.status}", diagnostic=diagnostic
                )

            deadline = time.monotonic() + PASSIVE_OBSERVE_SECONDS
            while time.monotonic() < deadline and not captured:
                if diagnostic["blocked_http_status_observed"] in {403, 429}:
                    raise passive.MvpSampleError(
                        f"http_{diagnostic['blocked_http_status_observed']}",
                        diagnostic=diagnostic,
                    )
                if passive._blocked_surface(page):
                    raise passive.MvpSampleError(
                        "blocked_or_login_surface", diagnostic=diagnostic
                    )
                page.wait_for_timeout(250)

            passive._wait_for_city(page)
            diagnostic["location_verified_same_run"] = True
            diagnostic["region_binding_fingerprint_verified"] = tracker.fingerprint_verified
            diagnostic["region_context_replayable_placements"] = tracker.replayable_count
            diagnostic["region_context_body_only_observed"] = tracker.body_only_match_observed

            if captured:
                _, payload = max(captured, key=lambda item: item[0])
                result = _parse_payload(payload, sample_size=sample_size)
                return _success_artifact(
                    extraction_result=result,
                    sample_size=sample_size,
                    capture_mode="passive",
                    explicit_requests=0,
                )

            headers, query_context = tracker.replay_context()
            request_url = _append_query_context(
                build_product_search_url(page=1, page_size=sample_size),
                query_context,
            )
            diagnostic["explicit_product_search_requests"] = 1
            response = context.request.get(
                request_url,
                headers=headers or None,
                timeout=EXPLICIT_REQUEST_TIMEOUT_MS,
                fail_on_status_code=False,
            )
            status = int(response.status)
            if status in {403, 429}:
                diagnostic["blocked_http_status_observed"] = status
                raise passive.MvpSampleError(f"http_{status}", diagnostic=diagnostic)
            if status != 200:
                raise passive.MvpSampleError(
                    f"explicit_product_search_http_{status}", diagnostic=diagnostic
                )
            try:
                payload = response.json()
            except Exception as exc:
                raise passive.MvpSampleError(
                    "explicit_product_search_not_json", diagnostic=diagnostic
                ) from exc
            if not isinstance(payload, Mapping) or not passive._product_search_payload(payload):
                raise passive.MvpSampleError(
                    "explicit_product_search_payload_not_observed", diagnostic=diagnostic
                )
            result = _parse_payload(payload, sample_size=sample_size)
            return _success_artifact(
                extraction_result=result,
                sample_size=sample_size,
                capture_mode="single_explicit_bound_fallback",
                explicit_requests=1,
            )
        except passive.MvpSampleError as exc:
            if exc.diagnostic:
                raise
            raise passive.MvpSampleError(str(exc), diagnostic=diagnostic) from exc
        except (DiagnosticSafetyError, LocationControlResolutionError) as exc:
            raise passive.MvpSampleError(str(exc), diagnostic=diagnostic) from exc
        finally:
            browser.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-read-only", action="store_true")
    parser.add_argument(
        "--allow-single-explicit-product-search",
        action="store_true",
        help="Autoriza exactamente un GET productSearch sólo tras verificar binding SPS",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        choices=passive.ALLOWED_SAMPLE_SIZES,
        default=10,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "run-artifacts" / "mvp-bound-sample.json",
    )
    args = parser.parse_args(argv)
    if not args.live_read_only:
        parser.error("la muestra bound requiere --live-read-only")
    if not args.allow_single_explicit_product_search:
        parser.error(
            "el fallback explícito requiere --allow-single-explicit-product-search"
        )

    try:
        artifact = _run_live_bound_sample(sample_size=args.sample_size)
        passive._write_artifact(artifact, args.output)
    except passive.MvpSampleError as exc:
        diagnostic = dict(getattr(exc, "diagnostic", {}) or {})
        artifact = _failure_artifact(str(exc), diagnostic)
        passive._write_artifact(artifact, args.output)
        print(f"mvp_bound_sample_stopped:{type(exc).__name__}:{exc}", file=sys.stderr)
        return 3

    print(
        json.dumps(
            {
                "location_id": artifact["location_id"],
                "sample_skus_returned": artifact["sample_skus_returned"],
                "sample_skus_with_price": artifact["sample_skus_with_price"],
                "capture_mode": artifact["capture_mode"],
                "explicit_product_search_requests": artifact[
                    "explicit_product_search_requests"
                ],
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
