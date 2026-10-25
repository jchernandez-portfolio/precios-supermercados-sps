"""Plan offline de facet discovery ligado al contexto técnico de SPS.

Esta capa cierra una invariancia que las primitivas individuales no podían probar:
``root_total`` y ``category_tree`` deben prepararse como un único par, con el mismo
binding de San Pedro Sula y la misma forma observada de transportar ``regionId``.

No abre red ni fabrica autoridad. El valor raw del contexto permanece encapsulado
en ``EphemeralSpsRequestContext`` y nunca aparece en el plan público. El digest del
plan liga únicamente evidencia/fingerprints sanitizados, los dos requests
canónicos y sus wire fingerprints.

La futura capa live podrá consumir este objeto sólo después de que una sesión SPS
autorizada haya observado un placement inequívoco. Si el contexto aparece anidado
o en body, ``la_colonia_sps_facet_wire`` seguirá fallando cerrado hasta que exista
un contrato específico demostrado para esa estructura.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import NoReturn

from precios_supermercados.scrapers.la_colonia_facet_discovery import (
    CATALOG_CATEGORIES_V1,
    FacetDiscoveryRequest,
)
from precios_supermercados.scrapers.la_colonia_sps_facet_context import (
    ConfirmedSpsFacetBinding,
    EphemeralSpsRequestContext,
    RequestContextPlacement,
    SpsFacetContextError,
    confirmed_sps_facet_binding,
    prepare_sps_facet_execution,
)
from precios_supermercados.scrapers.la_colonia_sps_facet_wire import (
    PreparedSpsFacetWireRequest,
    SpsFacetWireError,
    prepare_sps_facet_wire_request,
)

SPS_STRUCTURAL_PLAN_SCHEMA_VERSION = "1"
_SPS_STRUCTURAL_PLAN_DOMAIN = b"precios-sps/la-colonia-sps-structural-plan/v1\0"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_BINDING_SOURCE = re.compile(r"request:regionid:sha256:(?P<digest>[0-9a-f]{64})\Z")
_BINDING_EVIDENCE = re.compile(r"location_binding_radiography:sha256:[0-9a-f]{64}\Z")
_EXPECTED_SEQUENCE = ("root_total", "category_tree")


class SpsStructuralPlanError(ValueError):
    """El par estructural no está ligado de forma inequívoca a SPS."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


def _fail(code: str, message: str | None = None) -> NoReturn:
    raise SpsStructuralPlanError(code, message)


def _sha256(value: object, code: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        _fail(code)
    return value


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SpsStructuralPlanError("sps_structural_plan_json_invalid") from exc


@dataclass(frozen=True, slots=True)
class SpsStructuralPlanRequest:
    request_kind: str
    sequence: int
    canonical_request_digest: str
    wire_request_fingerprint: str
    wire: PreparedSpsFacetWireRequest = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.request_kind not in _EXPECTED_SEQUENCE:
            _fail("sps_structural_plan_request_kind_invalid")
        expected_sequence = _EXPECTED_SEQUENCE.index(self.request_kind) + 1
        if self.sequence != expected_sequence:
            _fail("sps_structural_plan_request_sequence_invalid")
        object.__setattr__(
            self,
            "canonical_request_digest",
            _sha256(
                self.canonical_request_digest,
                "sps_structural_plan_request_digest_invalid",
            ),
        )
        object.__setattr__(
            self,
            "wire_request_fingerprint",
            _sha256(
                self.wire_request_fingerprint,
                "sps_structural_plan_wire_fingerprint_invalid",
            ),
        )
        if not isinstance(self.wire, PreparedSpsFacetWireRequest):
            _fail("sps_structural_plan_wire_invalid")
        if self.wire.base_request_digest != self.canonical_request_digest:
            _fail("sps_structural_plan_wire_digest_mismatch")
        if self.wire.wire_request_fingerprint != self.wire_request_fingerprint:
            _fail("sps_structural_plan_wire_fingerprint_mismatch")

    def canonical_dict(self) -> dict[str, object]:
        return {
            "canonical_request_digest": self.canonical_request_digest,
            "request_kind": self.request_kind,
            "sequence": self.sequence,
            "wire_request_fingerprint": self.wire_request_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class SpsStructuralFacetPlan:
    """Par exacto root/tree unido a un único binding/contexto SPS."""

    location_id: str
    city_name: str
    binding_source_key: str
    binding_evidence: str
    context_fingerprint: str
    placement: RequestContextPlacement
    wire_key: str
    value_path: tuple[str, ...]
    requests: tuple[SpsStructuralPlanRequest, SpsStructuralPlanRequest]
    schema_version: str = SPS_STRUCTURAL_PLAN_SCHEMA_VERSION
    requires_same_browser_context: bool = True
    network_executed: bool = False
    production_authority: bool = False
    catalog_accepted: bool = False
    extraction_enabled: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != SPS_STRUCTURAL_PLAN_SCHEMA_VERSION:
            _fail("sps_structural_plan_schema_version_invalid")
        if self.location_id != "la_colonia_sps":
            _fail("sps_structural_plan_location_invalid")
        if self.city_name != "San Pedro Sula":
            _fail("sps_structural_plan_city_invalid")

        source_match = (
            _BINDING_SOURCE.fullmatch(self.binding_source_key)
            if isinstance(self.binding_source_key, str)
            else None
        )
        if source_match is None:
            _fail("sps_structural_plan_binding_source_key_invalid")
        if (
            not isinstance(self.binding_evidence, str)
            or _BINDING_EVIDENCE.fullmatch(self.binding_evidence) is None
        ):
            _fail("sps_structural_plan_binding_evidence_invalid")

        fingerprint = _sha256(
            self.context_fingerprint,
            "sps_structural_plan_context_fingerprint_invalid",
        )
        object.__setattr__(self, "context_fingerprint", fingerprint)
        if source_match.group("digest") != fingerprint:
            _fail("sps_structural_plan_binding_context_mismatch")

        if not isinstance(self.placement, RequestContextPlacement):
            _fail("sps_structural_plan_placement_invalid")
        if not isinstance(self.wire_key, str) or not self.wire_key:
            _fail("sps_structural_plan_wire_key_invalid")
        if not isinstance(self.value_path, tuple) or any(
            not isinstance(item, str) or not item for item in self.value_path
        ):
            _fail("sps_structural_plan_value_path_invalid")
        if not isinstance(self.requests, tuple) or len(self.requests) != 2:
            _fail("sps_structural_plan_request_pair_invalid")
        if any(not isinstance(item, SpsStructuralPlanRequest) for item in self.requests):
            _fail("sps_structural_plan_request_invalid")
        if tuple(item.request_kind for item in self.requests) != _EXPECTED_SEQUENCE:
            _fail("sps_structural_plan_request_pair_order_invalid")
        if tuple(item.sequence for item in self.requests) != (1, 2):
            _fail("sps_structural_plan_request_pair_sequence_invalid")

        for item in self.requests:
            if item.wire.placement is not self.placement:
                _fail("sps_structural_plan_request_placement_mismatch")
            if item.wire.wire_key != self.wire_key:
                _fail("sps_structural_plan_request_wire_key_mismatch")
            if item.wire.value_path != self.value_path:
                _fail("sps_structural_plan_request_value_path_mismatch")
            if item.wire.public_dict().get("requires_same_browser_context") is not True:
                _fail("sps_structural_plan_browser_context_required")

        if self.requires_same_browser_context is not True:
            _fail("sps_structural_plan_browser_context_required")
        if self.network_executed is not False:
            _fail("sps_structural_plan_network_execution_forbidden")
        if self.production_authority is not False:
            _fail("sps_structural_plan_authority_forbidden")
        if self.catalog_accepted is not False:
            _fail("sps_structural_plan_catalog_acceptance_forbidden")
        if self.extraction_enabled is not False:
            _fail("sps_structural_plan_extraction_forbidden")

    def canonical_dict(self) -> dict[str, object]:
        return {
            "binding_evidence": self.binding_evidence,
            "binding_source_key": self.binding_source_key,
            "city_name": self.city_name,
            "context_fingerprint": self.context_fingerprint,
            "location_id": self.location_id,
            "placement": self.placement.value,
            "requests": [item.canonical_dict() for item in self.requests],
            "requires_same_browser_context": True,
            "schema_version": self.schema_version,
            "value_path": list(self.value_path),
            "wire_key": self.wire_key,
        }

    @property
    def digest(self) -> str:
        return hashlib.sha256(
            _SPS_STRUCTURAL_PLAN_DOMAIN + _canonical_json(self.canonical_dict())
        ).hexdigest()

    def public_dict(self) -> dict[str, object]:
        return {
            **self.canonical_dict(),
            "plan_digest": self.digest,
            "network_executed": False,
            "production_authority": False,
            "catalog_accepted": False,
            "extraction_enabled": False,
            "raw_values_exposed": False,
        }


def _plan_request(
    logical_request: FacetDiscoveryRequest,
    *,
    binding: ConfirmedSpsFacetBinding,
    context: EphemeralSpsRequestContext,
) -> SpsStructuralPlanRequest:
    execution = prepare_sps_facet_execution(
        logical_request,
        context,
        binding=binding,
    )
    try:
        wire = prepare_sps_facet_wire_request(execution)
    except SpsFacetWireError as exc:
        raise SpsStructuralPlanError(f"sps_structural_plan_wire_{exc}") from exc
    return SpsStructuralPlanRequest(
        request_kind=logical_request.name,
        sequence=logical_request.sequence,
        canonical_request_digest=execution.structural_request.canonical_request_sha256,
        wire_request_fingerprint=wire.wire_request_fingerprint,
        wire=wire,
    )


def build_sps_structural_facet_plan(
    context: EphemeralSpsRequestContext,
    *,
    binding: ConfirmedSpsFacetBinding | None = None,
) -> SpsStructuralFacetPlan:
    """Prepara exactamente root_total→category_tree bajo un único contexto SPS."""

    effective_binding = binding or confirmed_sps_facet_binding()
    if not isinstance(context, EphemeralSpsRequestContext):
        _fail("sps_structural_plan_context_invalid")

    try:
        # Valida coherencia binding/context antes de construir cualquier request.
        context.reveal_for_transport(effective_binding)
    except SpsFacetContextError as exc:
        raise SpsStructuralPlanError(f"sps_structural_plan_context_{exc}") from exc

    requests = tuple(
        _plan_request(
            logical,
            binding=effective_binding,
            context=context,
        )
        for logical in CATALOG_CATEGORIES_V1.requests
    )
    if len(requests) != 2:  # defensa frente a una futura mutación del plan base
        _fail("sps_structural_plan_request_pair_invalid")

    return SpsStructuralFacetPlan(
        location_id=effective_binding.location_id,
        city_name=effective_binding.city_name,
        binding_source_key=effective_binding.source_key,
        binding_evidence=effective_binding.evidence,
        context_fingerprint=context.fingerprint,
        placement=context.placement,
        wire_key=context.wire_key,
        value_path=context.value_path,
        requests=(requests[0], requests[1]),
    )
