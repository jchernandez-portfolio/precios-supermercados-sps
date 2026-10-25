"""Contexto efímero que permite al gateway aplicar y atestiguar SPS.

El raw ``regionId`` sólo se conserva en memoria y sólo sale por ``wire_dict`` hacia
el endpoint autenticado del collector. Representaciones públicas y ``repr`` nunca
lo exponen. El worker debe volver a calcular su fingerprint y el fingerprint del
request wire antes de firmar un receipt v2.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import NoReturn
from urllib.parse import parse_qsl, urlsplit

from precios_supermercados.scrapers.la_colonia_sps_facet_context import (
    ConfirmedSpsFacetBinding,
    RequestContextPlacement,
)
from precios_supermercados.scrapers.la_colonia_sps_structural_plan import (
    SpsStructuralFacetPlan,
    SpsStructuralPlanRequest,
)

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_REGION_KEY = re.compile(r"[^a-z0-9]")


class StructuralLocationContextError(ValueError):
    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


def _fail(code: str, message: str | None = None) -> NoReturn:
    raise StructuralLocationContextError(code, message)


def _fingerprint(value: str) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _canonical_region_key(value: str) -> str:
    return _REGION_KEY.sub("", value.casefold())


@dataclass(frozen=True, slots=True)
class StructuralEdgeLocationContext:
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
            _fail("structural_location_id_invalid")
        if not isinstance(self.binding_source_key, str) or not self.binding_source_key:
            _fail("structural_binding_source_key_invalid")
        if not isinstance(self.binding_evidence, str) or not self.binding_evidence:
            _fail("structural_binding_evidence_invalid")
        if not isinstance(self.context_fingerprint, str) or not _SHA256.fullmatch(self.context_fingerprint):
            _fail("structural_context_fingerprint_invalid")
        if self.placement not in {RequestContextPlacement.QUERY, RequestContextPlacement.HEADER}:
            _fail("structural_context_placement_invalid")
        if not isinstance(self.wire_key, str) or not self.wire_key or len(self.wire_key) > 160:
            _fail("structural_context_wire_key_invalid")
        if _canonical_region_key(self.wire_key) not in {"region", "regionid", "xvtexregion"}:
            _fail("structural_context_wire_key_not_region")
        if self.value_path != ():
            _fail("structural_context_nested_forbidden")
        if not isinstance(self.wire_request_fingerprint, str) or not _SHA256.fullmatch(self.wire_request_fingerprint):
            _fail("structural_wire_request_fingerprint_invalid")
        if not isinstance(self._raw_value, str) or not self._raw_value or len(self._raw_value) > 4096:
            _fail("structural_context_raw_value_invalid")
        if _fingerprint(self._raw_value) != self.context_fingerprint:
            _fail("structural_context_raw_fingerprint_mismatch")
        expected_source = f"request:regionid:sha256:{self.context_fingerprint}"
        if self.binding_source_key != expected_source:
            _fail("structural_binding_source_fingerprint_mismatch")

    def __repr__(self) -> str:
        return (
            "StructuralEdgeLocationContext("
            f"location_id={self.location_id!r}, binding_source_key={self.binding_source_key!r}, "
            f"binding_evidence={self.binding_evidence!r}, context_fingerprint={self.context_fingerprint!r}, "
            f"placement={self.placement.value!r}, wire_key={self.wire_key!r}, "
            f"value_path={self.value_path!r}, wire_request_fingerprint={self.wire_request_fingerprint!r}, "
            "raw_value='<redacted>')"
        )

    def public_dict(self) -> dict[str, object]:
        return {
            "location_id": self.location_id,
            "binding_source_key": self.binding_source_key,
            "binding_evidence": self.binding_evidence,
            "context_fingerprint": self.context_fingerprint,
            "placement": self.placement.value,
            "wire_key": self.wire_key,
            "value_path": list(self.value_path),
            "wire_request_fingerprint": self.wire_request_fingerprint,
            "raw_values_exposed": False,
        }

    def wire_dict(self) -> dict[str, object]:
        return {
            "locationId": self.location_id,
            "bindingSourceKey": self.binding_source_key,
            "bindingEvidence": self.binding_evidence,
            "contextFingerprint": self.context_fingerprint,
            "placement": self.placement.value,
            "wireKey": self.wire_key,
            "valuePath": list(self.value_path),
            "wireRequestFingerprint": self.wire_request_fingerprint,
            "rawValue": self._raw_value,
        }


def structural_location_context_for_plan_request(
    plan: SpsStructuralFacetPlan,
    request: SpsStructuralPlanRequest,
    *,
    binding: ConfirmedSpsFacetBinding,
) -> StructuralEdgeLocationContext:
    """Deriva el envelope raw sólo desde un request que pertenece al plan SPS."""

    if not isinstance(plan, SpsStructuralFacetPlan):
        _fail("sps_structural_plan_required")
    if not isinstance(request, SpsStructuralPlanRequest) or request not in plan.requests:
        _fail("sps_structural_plan_request_required")
    if not isinstance(binding, ConfirmedSpsFacetBinding):
        _fail("confirmed_sps_binding_required")
    if binding.source_key != plan.binding_source_key or binding.evidence != plan.binding_evidence:
        _fail("sps_structural_plan_binding_mismatch")

    url, headers = request.wire.reveal_for_transport(binding)
    raw: str | None = None
    if plan.placement is RequestContextPlacement.QUERY:
        matches = [
            value
            for key, value in parse_qsl(urlsplit(url).query, keep_blank_values=True)
            if key.casefold() == plan.wire_key.casefold()
        ]
        if len(matches) != 1:
            _fail("structural_query_context_not_unique")
        raw = matches[0]
    elif plan.placement is RequestContextPlacement.HEADER:
        matches = [value for key, value in headers.items() if key.casefold() == plan.wire_key.casefold()]
        if len(matches) != 1:
            _fail("structural_header_context_not_unique")
        raw = matches[0]
    else:  # pragma: no cover - plan ya lo prohíbe
        _fail("structural_context_placement_invalid")

    return StructuralEdgeLocationContext(
        location_id=plan.location_id,
        binding_source_key=plan.binding_source_key,
        binding_evidence=plan.binding_evidence,
        context_fingerprint=plan.context_fingerprint,
        placement=plan.placement,
        wire_key=plan.wire_key,
        value_path=plan.value_path,
        wire_request_fingerprint=request.wire_request_fingerprint,
        _raw_value=raw,
    )
