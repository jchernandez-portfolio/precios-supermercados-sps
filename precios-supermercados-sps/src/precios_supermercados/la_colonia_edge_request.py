"""Validación Python independiente del GET canónico de La Colonia.

Este módulo no abre red. Reconstruye de forma estricta una URL generada por
``build_product_search_url`` y recalcula el mismo request canónico que el
runtime JavaScript del gateway edge. La intención es evitar que caller y
collector puedan compartir silenciosamente el mismo error de digest.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from urllib.parse import parse_qsl, urlencode, urlsplit

from precios_supermercados.edge_provenance import canonical_json_bytes
from precios_supermercados.scrapers.la_colonia_graphql import (
    ALLOWED_ORDER_BY,
    GRAPHQL_ENDPOINT,
    MAX_CATALOG_PAGE_SIZE,
    PRODUCT_SEARCH_QUERY,
)

LA_COLONIA_HOST = "www.lacolonia.com"
LA_COLONIA_PATH = "/_v/segment/graphql/v1"
EXPECTED_GRAPHQL_QUERY_SHA256 = hashlib.sha256(PRODUCT_SEARCH_QUERY.encode("utf-8")).hexdigest()

_SEARCH_KEYS = (
    "workspace",
    "maxAge",
    "appsEtag",
    "domain",
    "locale",
    "operationName",
    "query",
    "variables",
)
_VARIABLE_KEYS = (
    "query",
    "fullText",
    "selectedFacets",
    "orderBy",
    "from",
    "to",
    "hideUnavailableItems",
    "skusFilter",
)
_FIXED_SEARCH = {
    "workspace": "master",
    "maxAge": "short",
    "appsEtag": "remove",
    "domain": "store",
    "locale": "es-HN",
    "operationName": "productSearchV3",
}
_FACET_KEY = re.compile(r"category-[1-9][0-9]*\Z")


class LaColoniaEdgeRequestError(ValueError):
    """La URL no cumple el contrato canónico del collector edge."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


def _fail(code: str, message: str | None = None) -> None:
    raise LaColoniaEdgeRequestError(code, message)


def _clean_text(value: object, code: str, *, max_length: int = 256, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > max_length or value.strip() != value:
        _fail(code)
    if not value and not allow_empty:
        _fail(code)
    return value


def _safe_int(value: object, code: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum or value > 2**53 - 1:
        _fail(code)
    return value


def _validate_selected_facets(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list) or len(value) > 8:
        _fail("selected_facets_invalid")
    normalized: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for facet in value:
        if not isinstance(facet, dict) or list(facet) != ["key", "value"]:
            _fail("selected_facet_shape_invalid")
        key = _clean_text(facet.get("key"), "selected_facet_key_invalid", max_length=32)
        facet_value = _clean_text(facet.get("value"), "selected_facet_value_invalid")
        if not _FACET_KEY.fullmatch(key):
            _fail("selected_facet_key_invalid")
        identity = (key, facet_value)
        if identity in seen:
            _fail("selected_facet_duplicate")
        seen.add(identity)
        normalized.append(identity)
    return tuple(normalized)


def _facet_dicts(values: tuple[tuple[str, str], ...]) -> list[dict[str, str]]:
    return [{"key": key, "value": value} for key, value in values]


@dataclass(frozen=True, slots=True)
class ValidatedLaColoniaEdgeRequest:
    source_url: str
    from_index: int
    to_index: int
    order_by: str
    full_text: str
    query_value: str
    selected_facets: tuple[tuple[str, str], ...]
    graphql_query_sha256: str
    canonical_request_sha256: str

    @property
    def page_size(self) -> int:
        return self.to_index - self.from_index + 1

    def canonical_dict(self) -> dict[str, object]:
        return {
            "method": "GET",
            "origin_url": self.source_url,
            "target_host": LA_COLONIA_HOST,
            "target_path": LA_COLONIA_PATH,
            "variables": {
                "from": self.from_index,
                "fullText": self.full_text,
                "hideUnavailableItems": False,
                "orderBy": self.order_by,
                "query": self.query_value,
                "selectedFacets": _facet_dicts(self.selected_facets),
                "skusFilter": "ALL",
                "to": self.to_index,
            },
        }

    @property
    def variables(self) -> Mapping[str, object]:
        return MappingProxyType(self.canonical_dict()["variables"])  # type: ignore[arg-type]


def validate_la_colonia_edge_request(raw_url: str) -> ValidatedLaColoniaEdgeRequest:
    """Valida una URL builder-canónica y recalcula su digest edge.

    Se exige forma byte-a-byte reproducible por ``build_product_search_url``:
    orden de parámetros, JSON compacto de variables y codificación de URL.
    """

    if not isinstance(raw_url, str) or not raw_url or raw_url.strip() != raw_url or len(raw_url) > 20_000:
        _fail("origin_url_invalid")
    try:
        parsed = urlsplit(raw_url)
    except ValueError as exc:
        raise LaColoniaEdgeRequestError("origin_url_invalid") from exc
    if parsed.scheme != "https" or parsed.hostname != LA_COLONIA_HOST:
        _fail("origin_target_invalid")
    try:
        port = parsed.port
    except ValueError as exc:
        raise LaColoniaEdgeRequestError("origin_port_invalid") from exc
    if port is not None or parsed.username or parsed.password:
        _fail("origin_authority_invalid")
    if parsed.path != LA_COLONIA_PATH or parsed.fragment:
        _fail("origin_path_invalid")
    if not parsed.query:
        _fail("origin_query_missing")

    try:
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise LaColoniaEdgeRequestError("origin_query_invalid") from exc
    keys = tuple(key for key, _ in pairs)
    if keys != _SEARCH_KEYS:
        if len(set(keys)) != len(keys):
            _fail("origin_query_parameter_duplicate")
        _fail("origin_query_parameter_order_or_set_invalid")
    params = dict(pairs)

    for key, expected in _FIXED_SEARCH.items():
        if params.get(key) != expected:
            _fail(f"origin_{key}_mismatch")
    if params.get("query") != PRODUCT_SEARCH_QUERY:
        _fail("graphql_query_mismatch")

    variables_text = params.get("variables")
    if variables_text is None:
        _fail("variables_missing")
    try:
        variables = json.loads(variables_text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise LaColoniaEdgeRequestError("variables_json_invalid") from exc
    if not isinstance(variables, dict) or tuple(variables) != _VARIABLE_KEYS:
        _fail("variables_shape_or_order_invalid")

    query_value = _clean_text(
        variables.get("query"),
        "query_value_invalid",
        max_length=20_000,
        allow_empty=True,
    )
    full_text = _clean_text(
        variables.get("fullText"),
        "full_text_invalid",
        max_length=20_000,
        allow_empty=True,
    )
    selected_facets = _validate_selected_facets(variables.get("selectedFacets"))
    order_by = _clean_text(variables.get("orderBy"), "order_by_invalid", max_length=64)
    if order_by not in ALLOWED_ORDER_BY:
        _fail("order_by_not_allowed")
    from_index = _safe_int(variables.get("from"), "from_invalid")
    to_index = _safe_int(variables.get("to"), "to_invalid")
    if to_index < from_index:
        _fail("range_invalid")
    page_size = to_index - from_index + 1
    if page_size > MAX_CATALOG_PAGE_SIZE:
        _fail("page_size_above_limit")
    if from_index % page_size != 0:
        _fail("range_not_builder_page")
    if variables.get("hideUnavailableItems") is not False:
        _fail("hide_unavailable_must_be_false")
    if variables.get("skusFilter") != "ALL":
        _fail("skus_filter_must_be_all")

    if full_text:
        if query_value or selected_facets:
            _fail("full_text_mode_shape_mismatch")
    else:
        if not query_value or len(selected_facets) != 1:
            _fail("facet_mode_shape_mismatch")
        if selected_facets[0][1] != query_value:
            _fail("facet_value_query_mismatch")

    normalized_variables: dict[str, object] = {
        "query": query_value,
        "fullText": full_text,
        "selectedFacets": _facet_dicts(selected_facets),
        "orderBy": order_by,
        "from": from_index,
        "to": to_index,
        "hideUnavailableItems": False,
        "skusFilter": "ALL",
    }
    canonical_variables_text = json.dumps(normalized_variables, separators=(",", ":"))
    if variables_text != canonical_variables_text:
        _fail("variables_json_noncanonical")

    canonical_pairs = [
        ("workspace", "master"),
        ("maxAge", "short"),
        ("appsEtag", "remove"),
        ("domain", "store"),
        ("locale", "es-HN"),
        ("operationName", "productSearchV3"),
        ("query", PRODUCT_SEARCH_QUERY),
        ("variables", canonical_variables_text),
    ]
    canonical_url = f"{GRAPHQL_ENDPOINT}?{urlencode(canonical_pairs)}"
    if raw_url != canonical_url:
        _fail("origin_url_noncanonical")

    provisional = ValidatedLaColoniaEdgeRequest(
        source_url=canonical_url,
        from_index=from_index,
        to_index=to_index,
        order_by=order_by,
        full_text=full_text,
        query_value=query_value,
        selected_facets=selected_facets,
        graphql_query_sha256=EXPECTED_GRAPHQL_QUERY_SHA256,
        canonical_request_sha256="0" * 64,
    )
    digest = hashlib.sha256(canonical_json_bytes(provisional.canonical_dict())).hexdigest()
    return ValidatedLaColoniaEdgeRequest(
        source_url=canonical_url,
        from_index=from_index,
        to_index=to_index,
        order_by=order_by,
        full_text=full_text,
        query_value=query_value,
        selected_facets=selected_facets,
        graphql_query_sha256=EXPECTED_GRAPHQL_QUERY_SHA256,
        canonical_request_sha256=digest,
    )
