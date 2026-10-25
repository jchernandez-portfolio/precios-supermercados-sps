"""Deriva requests de catálogo ligados al contexto SPS ya probado estructuralmente.

La capa consume ``VerifiedSpsStructuralContext`` y un request de catálogo ya
validado. Nunca acepta ``location_id``, ``regionId`` ni fingerprints separados del
caller. El valor raw se recupera únicamente del plan estructural privado que fue
relacionado con receipts contextuales firmados y se mantiene oculto de ``repr`` y
representaciones públicas.

No abre red, no firma receipts y no concede autoridad comercial.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import NoReturn
from urllib.parse import parse_qsl, quote, urlsplit

from precios_supermercados.la_colonia_edge_request import ValidatedLaColoniaEdgeRequest
from precios_supermercados.scrapers.la_colonia_sps_facet_context import (
    ConfirmedSpsFacetBinding,
    RequestContextPlacement,
    fingerprint_context_value,
)
from precios_supermercados.sps_context_bound_discovery import (
    VerifiedSpsStructuralContext,
)

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_REGION_KEY = re.compile(r"[^a-z0-9]")
_ALLOWED_REGION_KEYS = {"region", "regionid", "xvtexregion"}


class CatalogLocationContextError(ValueError):
    """El contexto SPS no puede aplicarse de forma inequívoca al request."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


def _fail(code: str, message: str | None = None) -> NoReturn:
    raise CatalogLocationContextError(code, message)


def _canonical_region_key(value: str) -> str:
    return _REGION_KEY.sub("", value.casefold())


def _wire_fingerprint(method: str, url: str, headers: Mapping[str, str]) -> str:
    rendered = json.dumps(
        {
            "headers": dict(sorted(headers.items(), key=lambda item: item[0].casefold())),
            "method": method,
            "url": url,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _append_query_parameter_preserving_origin(url: str, key: str, value: str) -> str:
    """Añade un par RFC3986 sin reserializar los parámetros GraphQL existentes."""

    parsed = urlsplit(url)
    if parsed.fragment:
        _fail("catalog_wire_fragment_forbidden")
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if any(existing.casefold() == key.casefold() for existing, _ in pairs):
        _fail("catalog_region_query_key_already_present")
    separator = "&" if parsed.query else "?"
    encoded_key = quote(key, safe="-._~")
    encoded_value = quote(value, safe="-._~")
    return f"{url}{separator}{encoded_key}={encoded_value}"


def _binding_from_proof(proof: VerifiedSpsStructuralContext) -> ConfirmedSpsFacetBinding:
    return ConfirmedSpsFacetBinding(
        location_id=proof.location_id,
        city_name="San Pedro Sula",
        source_key=proof.binding_source_key,
        context_key="regionid",
        expected_fingerprint=proof.context_fingerprint,
        evidence=proof.binding_evidence,
    )


def _raw_from_wire(
    *,
    url: str,
    headers: Mapping[str, str],
    placement: RequestContextPlacement,
    wire_key: str,
) -> str:
    if placement is RequestContextPlacement.QUERY:
        values = [
            value
            for key, value in parse_qsl(urlsplit(url).query, keep_blank_values=True)
            if key.casefold() == wire_key.casefold()
        ]
        if len(values) != 1:
            _fail("catalog_structural_query_context_not_unique")
        raw = values[0]
    elif placement is RequestContextPlacement.HEADER:
        values = [
            value
            for key, value in headers.items()
            if key.casefold() == wire_key.casefold()
        ]
        if len(values) != 1:
            _fail("catalog_structural_header_context_not_unique")
        raw = values[0]
    else:
        _fail("catalog_context_placement_invalid")
    if not isinstance(raw, str) or not raw or len(raw) > 4096:
        _fail("catalog_context_raw_value_invalid")
    return raw


def _recover_verified_raw_context(proof: VerifiedSpsStructuralContext) -> str:
    """Extrae el mismo raw usado por root/tree del plan privado ya atestiguado."""

    plan = proof._private_plan_for_transport()
    binding = _binding_from_proof(proof)
    values: list[str] = []
    for request in plan.requests:
        try:
            url, headers = request.wire.reveal_for_transport(binding)
        except Exception as exc:
            raise CatalogLocationContextError(
                "catalog_structural_wire_reveal_failed"
            ) from exc
        values.append(
            _raw_from_wire(
                url=url,
                headers=headers,
                placement=proof.context_placement,
                wire_key=proof.context_wire_key,
            )
        )
    if len(values) != 2 or values[0] != values[1]:
        _fail("catalog_structural_raw_context_changed")
    raw = values[0]
    if fingerprint_context_value(raw) != proof.context_fingerprint:
        _fail("catalog_structural_raw_context_fingerprint_mismatch")
    return raw


@dataclass(frozen=True, slots=True)
class PreparedSpsCatalogWireRequest:
    """Material privado de un page request después de aplicar el contexto SPS."""

    base_request_digest: str
    location_id: str
    binding_source_key: str
    binding_evidence: str
    context_fingerprint: str
    placement: RequestContextPlacement
    wire_key: str
    value_path: tuple[str, ...]
    wire_request_fingerprint: str
    _url: str = field(repr=False, compare=False)
    _headers: Mapping[str, str] = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if not _SHA256.fullmatch(self.base_request_digest):
            _fail("catalog_base_request_digest_invalid")
        if self.location_id != "la_colonia_sps":
            _fail("catalog_location_id_invalid")
        if not self.binding_source_key or not self.binding_evidence:
            _fail("catalog_binding_invalid")
        if not _SHA256.fullmatch(self.context_fingerprint):
            _fail("catalog_context_fingerprint_invalid")
        if self.placement not in {
            RequestContextPlacement.QUERY,
            RequestContextPlacement.HEADER,
        }:
            _fail("catalog_context_placement_invalid")
        if (
            not isinstance(self.wire_key, str)
            or not self.wire_key
            or len(self.wire_key) > 160
            or _canonical_region_key(self.wire_key) not in _ALLOWED_REGION_KEYS
        ):
            _fail("catalog_context_wire_key_invalid")
        if self.value_path != ():
            _fail("catalog_context_nested_forbidden")
        if not _SHA256.fullmatch(self.wire_request_fingerprint):
            _fail("catalog_wire_request_fingerprint_invalid")
        if not isinstance(self._url, str) or not self._url.startswith("https://"):
            _fail("catalog_wire_url_invalid")
        if not isinstance(self._headers, Mapping):
            _fail("catalog_wire_headers_invalid")
        normalized_headers = {str(key): str(value) for key, value in self._headers.items()}
        object.__setattr__(self, "_headers", MappingProxyType(normalized_headers))
        if _wire_fingerprint("GET", self._url, normalized_headers) != self.wire_request_fingerprint:
            _fail("catalog_wire_request_fingerprint_mismatch")

    def public_dict(self) -> dict[str, object]:
        return {
            "base_request_digest": self.base_request_digest,
            "binding_evidence": self.binding_evidence,
            "binding_source_key": self.binding_source_key,
            "catalog_accepted": False,
            "context_fingerprint": self.context_fingerprint,
            "extraction_enabled": False,
            "location_id": self.location_id,
            "placement": self.placement.value,
            "production_authority": False,
            "raw_values_exposed": False,
            "value_path": list(self.value_path),
            "wire_key": self.wire_key,
            "wire_request_fingerprint": self.wire_request_fingerprint,
        }

    def _reveal_for_gateway(self) -> tuple[str, Mapping[str, str]]:
        return self._url, MappingProxyType(dict(self._headers))


@dataclass(frozen=True, slots=True)
class CatalogEdgeLocationContext:
    """Envelope privado que el gateway contextual debe atestiguar en su receipt."""

    location_id: str
    binding_source_key: str
    binding_evidence: str
    context_fingerprint: str
    placement: RequestContextPlacement
    wire_key: str
    value_path: tuple[str, ...]
    wire_request_fingerprint: str
    _raw_value: str = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.location_id != "la_colonia_sps":
            _fail("catalog_location_id_invalid")
        if not isinstance(self.binding_source_key, str) or not self.binding_source_key:
            _fail("catalog_binding_source_key_invalid")
        if not isinstance(self.binding_evidence, str) or not self.binding_evidence:
            _fail("catalog_binding_evidence_invalid")
        if not _SHA256.fullmatch(self.context_fingerprint):
            _fail("catalog_context_fingerprint_invalid")
        expected_source = f"request:regionid:sha256:{self.context_fingerprint}"
        if self.binding_source_key != expected_source:
            _fail("catalog_binding_source_fingerprint_mismatch")
        if self.placement not in {
            RequestContextPlacement.QUERY,
            RequestContextPlacement.HEADER,
        }:
            _fail("catalog_context_placement_invalid")
        if (
            not isinstance(self.wire_key, str)
            or not self.wire_key
            or _canonical_region_key(self.wire_key) not in _ALLOWED_REGION_KEYS
        ):
            _fail("catalog_context_wire_key_invalid")
        if self.value_path != ():
            _fail("catalog_context_nested_forbidden")
        if not _SHA256.fullmatch(self.wire_request_fingerprint):
            _fail("catalog_wire_request_fingerprint_invalid")
        if not isinstance(self._raw_value, str) or not self._raw_value or len(self._raw_value) > 4096:
            _fail("catalog_context_raw_value_invalid")
        if fingerprint_context_value(self._raw_value) != self.context_fingerprint:
            _fail("catalog_context_raw_fingerprint_mismatch")

    def public_dict(self) -> dict[str, object]:
        return {
            "binding_evidence": self.binding_evidence,
            "binding_source_key": self.binding_source_key,
            "context_fingerprint": self.context_fingerprint,
            "location_id": self.location_id,
            "placement": self.placement.value,
            "raw_values_exposed": False,
            "value_path": list(self.value_path),
            "wire_key": self.wire_key,
            "wire_request_fingerprint": self.wire_request_fingerprint,
        }

    def wire_dict(self) -> dict[str, object]:
        return {
            "bindingEvidence": self.binding_evidence,
            "bindingSourceKey": self.binding_source_key,
            "contextFingerprint": self.context_fingerprint,
            "locationId": self.location_id,
            "placement": self.placement.value,
            "rawValue": self._raw_value,
            "valuePath": list(self.value_path),
            "wireKey": self.wire_key,
            "wireRequestFingerprint": self.wire_request_fingerprint,
        }


def prepare_sps_catalog_wire_request(
    proof: VerifiedSpsStructuralContext,
    request: ValidatedLaColoniaEdgeRequest,
) -> PreparedSpsCatalogWireRequest:
    """Aplica el mismo contexto raw probado a un request base de catálogo."""

    if not isinstance(proof, VerifiedSpsStructuralContext):
        _fail("verified_sps_structural_context_required")
    if not isinstance(request, ValidatedLaColoniaEdgeRequest):
        _fail("validated_catalog_request_required")
    if proof.production_authority is not False or proof.catalog_accepted is not False:
        _fail("verified_sps_structural_context_authority_invalid")

    raw = _recover_verified_raw_context(proof)
    url = request.source_url
    headers: dict[str, str] = {}
    if proof.context_placement is RequestContextPlacement.QUERY:
        url = _append_query_parameter_preserving_origin(
            url,
            proof.context_wire_key,
            raw,
        )
    elif proof.context_placement is RequestContextPlacement.HEADER:
        headers[proof.context_wire_key] = raw
    else:
        _fail("catalog_context_placement_invalid")

    return PreparedSpsCatalogWireRequest(
        base_request_digest=request.canonical_request_sha256,
        location_id=proof.location_id,
        binding_source_key=proof.binding_source_key,
        binding_evidence=proof.binding_evidence,
        context_fingerprint=proof.context_fingerprint,
        placement=proof.context_placement,
        wire_key=proof.context_wire_key,
        value_path=proof.context_value_path,
        wire_request_fingerprint=_wire_fingerprint("GET", url, headers),
        _url=url,
        _headers=headers,
    )


def catalog_edge_location_context_for_request(
    proof: VerifiedSpsStructuralContext,
    request: ValidatedLaColoniaEdgeRequest,
) -> CatalogEdgeLocationContext:
    """Construye el envelope raw sólo después de preparar/verificar el wire exacto."""

    prepared = prepare_sps_catalog_wire_request(proof, request)
    raw = _recover_verified_raw_context(proof)
    return CatalogEdgeLocationContext(
        location_id=prepared.location_id,
        binding_source_key=prepared.binding_source_key,
        binding_evidence=prepared.binding_evidence,
        context_fingerprint=prepared.context_fingerprint,
        placement=prepared.placement,
        wire_key=prepared.wire_key,
        value_path=prepared.value_path,
        wire_request_fingerprint=prepared.wire_request_fingerprint,
        _raw_value=raw,
    )
