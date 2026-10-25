"""Contrato canónico offline para los dos requests estructurales de La Colonia.

Facet discovery define exactamente dos observaciones: total raíz y árbol de
categorías. Este módulo construye y valida sus GET sin abrir red, y calcula un
digest canónico compatible con el runtime edge.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from urllib.parse import parse_qsl, urlencode, urlsplit

from precios_supermercados.edge_provenance import canonical_json_bytes
from precios_supermercados.scrapers.la_colonia_facet_discovery_adapter import (
    CATEGORY_TREE_OPERATION,
    CATEGORY_TREE_QUERY,
    FACET_DISCOVERY_ENDPOINT,
    FACET_DISCOVERY_HOST,
    ROOT_TOTAL_OPERATION,
    ROOT_TOTAL_QUERY,
)

LA_COLONIA_PATH = "/_v/segment/graphql/v1"
STRUCTURAL_REQUEST_SCHEMA_VERSION = "1"
ROOT_TOTAL_QUERY_SHA256 = hashlib.sha256(ROOT_TOTAL_QUERY.encode("utf-8")).hexdigest()
CATEGORY_TREE_QUERY_SHA256 = hashlib.sha256(CATEGORY_TREE_QUERY.encode("utf-8")).hexdigest()

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
_VARIABLE_KEYS = ("query", "fullText", "selectedFacets", "from", "to")
_FIXED_SEARCH = {
    "workspace": "master",
    "maxAge": "short",
    "appsEtag": "remove",
    "domain": "store",
    "locale": "es-HN",
}
_FIXED_VARIABLES: dict[str, object] = {
    "query": "",
    "fullText": "",
    "selectedFacets": [],
    "from": 0,
    "to": 0,
}
_OPERATIONS = {
    "root_total": (ROOT_TOTAL_OPERATION, ROOT_TOTAL_QUERY, ROOT_TOTAL_QUERY_SHA256),
    "category_tree": (
        CATEGORY_TREE_OPERATION,
        CATEGORY_TREE_QUERY,
        CATEGORY_TREE_QUERY_SHA256,
    ),
}
_OPERATION_NAMES = {value[0]: (key, value[1], value[2]) for key, value in _OPERATIONS.items()}


class LaColoniaEdgeStructuralRequestError(ValueError):
    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


def _fail(code: str, message: str | None = None) -> None:
    raise LaColoniaEdgeStructuralRequestError(code, message)


@dataclass(frozen=True, slots=True)
class ValidatedLaColoniaStructuralRequest:
    request_kind: str
    source_url: str
    operation_name: str
    graphql_query_sha256: str
    canonical_request_sha256: str
    schema_version: str = STRUCTURAL_REQUEST_SCHEMA_VERSION

    @property
    def variables(self) -> Mapping[str, object]:
        return MappingProxyType(dict(_FIXED_VARIABLES))

    def canonical_dict(self) -> dict[str, object]:
        return {
            "method": "GET",
            "operation_name": self.operation_name,
            "origin_url": self.source_url,
            "target_host": FACET_DISCOVERY_HOST,
            "target_path": LA_COLONIA_PATH,
            "variables": dict(_FIXED_VARIABLES),
        }


def build_structural_discovery_url(request_kind: str) -> str:
    try:
        operation_name, query, _query_sha = _OPERATIONS[request_kind]
    except KeyError as exc:
        raise LaColoniaEdgeStructuralRequestError("structural_request_kind_invalid") from exc
    params = {
        "workspace": "master",
        "maxAge": "short",
        "appsEtag": "remove",
        "domain": "store",
        "locale": "es-HN",
        "operationName": operation_name,
        "query": query,
        "variables": json.dumps(_FIXED_VARIABLES, separators=(",", ":")),
    }
    return f"{FACET_DISCOVERY_ENDPOINT}?{urlencode(params)}"


def validate_la_colonia_structural_request(
    raw_url: str,
) -> ValidatedLaColoniaStructuralRequest:
    if (
        not isinstance(raw_url, str)
        or not raw_url
        or raw_url.strip() != raw_url
        or len(raw_url) > 20_000
    ):
        _fail("structural_origin_url_invalid")
    try:
        parsed = urlsplit(raw_url)
    except ValueError as exc:
        raise LaColoniaEdgeStructuralRequestError("structural_origin_url_invalid") from exc
    if parsed.scheme != "https" or parsed.hostname != FACET_DISCOVERY_HOST:
        _fail("structural_origin_target_invalid")
    try:
        port = parsed.port
    except ValueError as exc:
        raise LaColoniaEdgeStructuralRequestError("structural_origin_port_invalid") from exc
    if port is not None or parsed.username or parsed.password:
        _fail("structural_origin_authority_invalid")
    if parsed.path != LA_COLONIA_PATH or parsed.fragment or not parsed.query:
        _fail("structural_origin_path_invalid")

    try:
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise LaColoniaEdgeStructuralRequestError("structural_origin_query_invalid") from exc
    keys = tuple(key for key, _ in pairs)
    if keys != _SEARCH_KEYS:
        if len(set(keys)) != len(keys):
            _fail("structural_origin_query_parameter_duplicate")
        _fail("structural_origin_query_parameter_order_or_set_invalid")
    params = dict(pairs)
    for key, expected in _FIXED_SEARCH.items():
        if params.get(key) != expected:
            _fail(f"structural_origin_{key}_mismatch")

    operation_name = params.get("operationName")
    operation = _OPERATION_NAMES.get(operation_name or "")
    if operation is None:
        _fail("structural_operation_name_invalid")
    request_kind, expected_query, query_sha = operation
    if params.get("query") != expected_query:
        _fail("structural_graphql_query_mismatch")

    variables_text = params.get("variables")
    if variables_text is None:
        _fail("structural_variables_missing")
    try:
        variables = json.loads(variables_text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise LaColoniaEdgeStructuralRequestError("structural_variables_json_invalid") from exc
    if not isinstance(variables, dict) or tuple(variables) != _VARIABLE_KEYS:
        _fail("structural_variables_shape_or_order_invalid")
    if variables != _FIXED_VARIABLES:
        _fail("structural_variables_values_invalid")
    canonical_variables = json.dumps(_FIXED_VARIABLES, separators=(",", ":"))
    if variables_text != canonical_variables:
        _fail("structural_variables_json_noncanonical")

    canonical_url = build_structural_discovery_url(request_kind)
    if raw_url != canonical_url:
        _fail("structural_origin_url_noncanonical")

    provisional = ValidatedLaColoniaStructuralRequest(
        request_kind=request_kind,
        source_url=canonical_url,
        operation_name=operation_name or "",
        graphql_query_sha256=query_sha,
        canonical_request_sha256="0" * 64,
    )
    digest = hashlib.sha256(canonical_json_bytes(provisional.canonical_dict())).hexdigest()
    return ValidatedLaColoniaStructuralRequest(
        request_kind=request_kind,
        source_url=canonical_url,
        operation_name=operation_name or "",
        graphql_query_sha256=query_sha,
        canonical_request_sha256=digest,
    )
