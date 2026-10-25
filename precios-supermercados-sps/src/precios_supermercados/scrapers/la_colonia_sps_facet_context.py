"""Frontera offline entre el binding SPS confirmado y un futuro facet discovery.

El binding live ya demostró que ``request:regionid`` cambia al seleccionar San
Pedro Sula, pero el artefacto sanitizado deliberadamente no conserva el valor en
claro ni dónde aparece dentro del request. Este módulo evita llenar ese hueco con
una suposición.

La capa:

- valida que ``la_colonia_sps`` tenga binding técnico de ciudad confirmado;
- observa un ``regionId`` sólo en memoria y sólo en el endpoint GraphQL estructural;
- conserva placement, nombre público del contenedor y ruta estructural exacta;
- exige que el fingerprint coincida con la evidencia canónica;
- falla cerrado ante múltiples placements, wire keys, rutas o valores;
- prepara únicamente las dos requests estructurales cerradas de facets;
- nunca abre red, no acepta catálogo y no habilita extracción.

El valor raw queda deliberadamente fuera de ``repr`` y de cualquier representación
pública. Sólo una capa de transporte futura, explícitamente auditada, podrá pedirlo
mediante ``reveal_for_transport`` y deberá aplicar exactamente la estructura
observada en una request relevante. Mientras esa relación no se observe, no existe
una ejecución live válida de facets bajo SPS.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from precios_supermercados.la_colonia_edge_structural_request import (
    ValidatedLaColoniaStructuralRequest,
    build_structural_discovery_url,
    validate_la_colonia_structural_request,
)
from precios_supermercados.locations import (
    LA_COLONIA_SPS,
    LocationConfig,
    LocationGranularity,
)
from precios_supermercados.scrapers.la_colonia_facet_discovery import (
    FacetDiscoveryRequest,
)


class SpsFacetContextError(ValueError):
    """La evidencia de contexto no permite preparar un request SPS."""


class RequestContextPlacement(StrEnum):
    QUERY = "query"
    HEADER = "header"
    BODY = "body"


_SOURCE_KEY_RE = re.compile(
    r"^request:(?P<key>[a-z0-9_-]+):sha256:(?P<digest>[0-9a-f]{64})$"
)
_EVIDENCE_RE = re.compile(r"^location_binding_radiography:sha256:[0-9a-f]{64}$")
_REGION_ALIASES = frozenset({"region", "regionid", "xvtexregion"})
_JSON_QUERY_CONTAINERS = frozenset({"variables", "extensions"})
FACET_CONTEXT_HOST = "www.lacolonia.com"
FACET_CONTEXT_PATH = "/_v/segment/graphql/v1"


def _canonical_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _is_region_key(value: str) -> bool:
    return _canonical_key(value) in _REGION_ALIASES


def _stable_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise SpsFacetContextError("region_context_value_not_serializable") from exc


def fingerprint_context_value(value: Any) -> str:
    """Replica el fingerprint usado por ``location_binding_radiography``."""

    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _eligible_graphql_request(request: Any) -> bool:
    try:
        parsed = urlsplit(str(request.url))
    except Exception:
        return False
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.casefold() == "https"
        and (parsed.hostname or "").casefold() == FACET_CONTEXT_HOST
        and port in {None, 443}
        and parsed.path == FACET_CONTEXT_PATH
    )


def _path_component(value: object) -> str:
    text = str(value)
    if not text or len(text) > 160:
        raise SpsFacetContextError("sps_region_value_path_invalid")
    return text


@dataclass(frozen=True, slots=True)
class ConfirmedSpsFacetBinding:
    location_id: str
    city_name: str
    source_key: str
    context_key: str
    expected_fingerprint: str
    evidence: str

    def __post_init__(self) -> None:
        if self.location_id != "la_colonia_sps":
            raise SpsFacetContextError("sps_location_required")
        if self.city_name != "San Pedro Sula":
            raise SpsFacetContextError("sps_city_required")
        if self.context_key != "regionid":
            raise SpsFacetContextError("sps_region_context_required")
        if not re.fullmatch(r"[0-9a-f]{64}", self.expected_fingerprint):
            raise SpsFacetContextError("sps_context_fingerprint_invalid")
        if not _EVIDENCE_RE.fullmatch(self.evidence):
            raise SpsFacetContextError("sps_binding_evidence_invalid")

    def public_dict(self) -> dict[str, object]:
        return {
            "location_id": self.location_id,
            "city_name": self.city_name,
            "source_key": self.source_key,
            "context_key": self.context_key,
            "expected_fingerprint": self.expected_fingerprint,
            "evidence": self.evidence,
            "raw_values_exposed": False,
        }


def confirmed_sps_facet_binding(
    location: LocationConfig = LA_COLONIA_SPS,
) -> ConfirmedSpsFacetBinding:
    """Carga sólo un binding SPS ya demostrado; nunca lo infiere."""

    if not isinstance(location, LocationConfig):
        raise SpsFacetContextError("location_config_required")
    if location.location_id != "la_colonia_sps" or location.supermarket_id != "la_colonia":
        raise SpsFacetContextError("sps_location_required")
    if location.city_id != "sps" or location.city_name != "San Pedro Sula":
        raise SpsFacetContextError("sps_city_required")
    if location.granularity is not LocationGranularity.CITY:
        raise SpsFacetContextError("sps_city_granularity_required")
    if location.technical_binding_confirmed is not True:
        raise SpsFacetContextError("sps_technical_binding_required")
    if not location.source_location_key:
        raise SpsFacetContextError("sps_source_location_key_required")
    match = _SOURCE_KEY_RE.fullmatch(location.source_location_key)
    if match is None:
        raise SpsFacetContextError("sps_source_location_key_invalid")
    context_key = _canonical_key(match.group("key"))
    if context_key != "regionid":
        raise SpsFacetContextError("sps_region_context_required")
    if not location.evidence:
        raise SpsFacetContextError("sps_binding_evidence_required")
    return ConfirmedSpsFacetBinding(
        location_id=location.location_id,
        city_name=location.city_name,
        source_key=location.source_location_key,
        context_key=context_key,
        expected_fingerprint=match.group("digest"),
        evidence=location.evidence,
    )


class EphemeralSpsRequestContext:
    """Valor de contexto verificado mantenido sólo en memoria.

    No es dataclass a propósito: ``dataclasses.asdict`` no puede serializarlo por
    accidente. ``repr`` y ``public_dict`` nunca muestran el valor raw.
    """

    __slots__ = (
        "placement",
        "context_key",
        "wire_key",
        "value_path",
        "fingerprint",
        "_raw_value",
        "_binding_source_key",
    )

    def __init__(
        self,
        *,
        placement: RequestContextPlacement,
        context_key: str,
        wire_key: str,
        value_path: tuple[str, ...],
        fingerprint: str,
        raw_value: Any,
        binding_source_key: str,
    ) -> None:
        if not isinstance(wire_key, str) or not wire_key.strip() or wire_key != wire_key.strip():
            raise SpsFacetContextError("sps_region_wire_key_invalid")
        if len(wire_key) > 160:
            raise SpsFacetContextError("sps_region_wire_key_invalid")
        if not isinstance(value_path, tuple) or any(
            not isinstance(item, str) or not item for item in value_path
        ):
            raise SpsFacetContextError("sps_region_value_path_invalid")
        if value_path:
            if not _is_region_key(value_path[-1]):
                raise SpsFacetContextError("sps_region_value_path_invalid")
        elif not _is_region_key(wire_key):
            raise SpsFacetContextError("sps_region_wire_key_invalid")
        self.placement = placement
        self.context_key = context_key
        self.wire_key = wire_key
        self.value_path = value_path
        self.fingerprint = fingerprint
        self._raw_value = raw_value
        self._binding_source_key = binding_source_key

    def __repr__(self) -> str:
        return (
            "EphemeralSpsRequestContext("
            f"placement={self.placement.value!r}, context_key={self.context_key!r}, "
            f"wire_key={self.wire_key!r}, value_path={self.value_path!r}, "
            f"fingerprint={self.fingerprint!r}, raw_value='<redacted>')"
        )

    def public_dict(self) -> dict[str, object]:
        return {
            "placement": self.placement.value,
            "context_key": self.context_key,
            "wire_key": self.wire_key,
            "value_path": list(self.value_path),
            "fingerprint": self.fingerprint,
            "target_host": FACET_CONTEXT_HOST,
            "target_path": FACET_CONTEXT_PATH,
            "raw_values_exposed": False,
        }

    def reveal_for_transport(self, binding: ConfirmedSpsFacetBinding) -> Any:
        if not isinstance(binding, ConfirmedSpsFacetBinding):
            raise SpsFacetContextError("confirmed_sps_binding_required")
        if binding.source_key != self._binding_source_key:
            raise SpsFacetContextError("sps_binding_changed")
        if binding.context_key != self.context_key:
            raise SpsFacetContextError("sps_context_key_changed")
        if binding.expected_fingerprint != self.fingerprint:
            raise SpsFacetContextError("sps_context_fingerprint_changed")
        return self._raw_value


@dataclass(frozen=True, slots=True)
class _Occurrence:
    placement: RequestContextPlacement
    wire_key: str
    value_path: tuple[str, ...]
    value: Any


class EphemeralSpsRequestContextCollector:
    """Observa ``regionId`` sólo en requests del endpoint estructural esperado."""

    def __init__(self) -> None:
        self._occurrences: list[_Occurrence] = []

    def reset(self) -> None:
        self._occurrences.clear()

    def _add(
        self,
        placement: RequestContextPlacement,
        wire_key: str,
        value: Any,
        *,
        value_path: tuple[str, ...] = (),
    ) -> None:
        if value_path:
            if not _is_region_key(value_path[-1]):
                return
        elif not _is_region_key(wire_key):
            return
        wire_key = str(wire_key).strip()
        normalized_path = tuple(_path_component(item) for item in value_path)
        occurrence = _Occurrence(
            placement=placement,
            wire_key=wire_key,
            value_path=normalized_path,
            value=value,
        )
        marker = (
            placement.value,
            wire_key.casefold(),
            tuple(item.casefold() for item in normalized_path),
            _stable_json(value),
        )
        if all(
            (
                item.placement.value,
                item.wire_key.casefold(),
                tuple(part.casefold() for part in item.value_path),
                _stable_json(item.value),
            )
            != marker
            for item in self._occurrences
        ):
            self._occurrences.append(occurrence)

    def _walk_nested(
        self,
        value: Any,
        *,
        placement: RequestContextPlacement,
        wire_key: str,
        path: tuple[str, ...] = (),
    ) -> None:
        if isinstance(value, Mapping):
            for key, nested in value.items():
                key_text = _path_component(key)
                nested_path = (*path, key_text)
                if _is_region_key(key_text):
                    self._add(
                        placement,
                        wire_key,
                        nested,
                        value_path=nested_path,
                    )
                self._walk_nested(
                    nested,
                    placement=placement,
                    wire_key=wire_key,
                    path=nested_path,
                )
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for index, nested in enumerate(value):
                nested_path = (*path, f"[{index}]")
                self._walk_nested(
                    nested,
                    placement=placement,
                    wire_key=wire_key,
                    path=nested_path,
                )

    def observe_request(self, request: Any) -> None:
        if not _eligible_graphql_request(request):
            return
        try:
            for key, value in request.headers.items():
                self._add(RequestContextPlacement.HEADER, str(key), value)
        except Exception:
            pass
        try:
            query_pairs = parse_qsl(
                urlsplit(str(request.url)).query,
                keep_blank_values=True,
            )
            for key, value in query_pairs:
                if _is_region_key(key):
                    self._add(RequestContextPlacement.QUERY, key, value)
                    continue
                if key.casefold() in _JSON_QUERY_CONTAINERS:
                    try:
                        nested = json.loads(value)
                    except (json.JSONDecodeError, TypeError, ValueError):
                        continue
                    self._walk_nested(
                        nested,
                        placement=RequestContextPlacement.QUERY,
                        wire_key=key,
                    )
        except Exception:
            pass

        payload: Any = None
        try:
            payload = request.post_data_json
        except Exception:
            try:
                raw = request.post_data
                payload = json.loads(raw) if raw else None
            except Exception:
                payload = None
        self._walk_nested(
            payload,
            placement=RequestContextPlacement.BODY,
            wire_key="$body",
        )

    def resolve(
        self,
        binding: ConfirmedSpsFacetBinding,
    ) -> EphemeralSpsRequestContext:
        if not isinstance(binding, ConfirmedSpsFacetBinding):
            raise SpsFacetContextError("confirmed_sps_binding_required")
        if not self._occurrences:
            raise SpsFacetContextError("sps_region_context_not_observed")

        matches: list[_Occurrence] = []
        mismatches: list[_Occurrence] = []
        for occurrence in self._occurrences:
            if fingerprint_context_value(occurrence.value) == binding.expected_fingerprint:
                matches.append(occurrence)
            else:
                mismatches.append(occurrence)
        if not matches:
            raise SpsFacetContextError("sps_region_context_fingerprint_mismatch")
        if mismatches:
            raise SpsFacetContextError("sps_region_context_conflict")

        placements = {item.placement for item in matches}
        if len(placements) != 1:
            raise SpsFacetContextError("sps_region_context_placement_ambiguous")
        wire_keys = {item.wire_key.casefold() for item in matches}
        if len(wire_keys) != 1:
            raise SpsFacetContextError("sps_region_context_wire_key_ambiguous")
        paths = {
            tuple(part.casefold() for part in item.value_path)
            for item in matches
        }
        if len(paths) != 1:
            raise SpsFacetContextError("sps_region_context_value_path_ambiguous")
        raw_markers = {_stable_json(item.value) for item in matches}
        if len(raw_markers) != 1:
            raise SpsFacetContextError("sps_region_context_value_ambiguous")

        selected = matches[0]
        return EphemeralSpsRequestContext(
            placement=selected.placement,
            context_key=binding.context_key,
            wire_key=selected.wire_key,
            value_path=selected.value_path,
            fingerprint=binding.expected_fingerprint,
            raw_value=selected.value,
            binding_source_key=binding.source_key,
        )


@dataclass(frozen=True, slots=True)
class PreparedSpsFacetExecution:
    """Request estructural cerrado unido a contexto SPS verificado, aún sin red."""

    logical_request: FacetDiscoveryRequest
    structural_request: ValidatedLaColoniaStructuralRequest
    binding: ConfirmedSpsFacetBinding
    context: EphemeralSpsRequestContext

    def __post_init__(self) -> None:
        if not isinstance(self.logical_request, FacetDiscoveryRequest):
            raise SpsFacetContextError("facet_logical_request_invalid")
        if not isinstance(self.structural_request, ValidatedLaColoniaStructuralRequest):
            raise SpsFacetContextError("facet_structural_request_invalid")
        if self.structural_request.request_kind != self.logical_request.name:
            raise SpsFacetContextError("facet_request_kind_mismatch")
        if not isinstance(self.binding, ConfirmedSpsFacetBinding):
            raise SpsFacetContextError("confirmed_sps_binding_required")
        if not isinstance(self.context, EphemeralSpsRequestContext):
            raise SpsFacetContextError("ephemeral_sps_context_required")
        self.context.reveal_for_transport(self.binding)

    def public_dict(self) -> dict[str, object]:
        return {
            "logical_request": self.logical_request.name,
            "sequence": self.logical_request.sequence,
            "request_digest": self.structural_request.canonical_request_sha256,
            "binding": self.binding.public_dict(),
            "context": self.context.public_dict(),
            "network_executed": False,
            "production_authority": False,
            "catalog_accepted": False,
            "extraction_enabled": False,
            "raw_values_exposed": False,
        }


def prepare_sps_facet_execution(
    logical_request: FacetDiscoveryRequest,
    context: EphemeralSpsRequestContext,
    *,
    binding: ConfirmedSpsFacetBinding | None = None,
) -> PreparedSpsFacetExecution:
    """Une request cerrado + binding + contexto efímero sin ejecutar HTTP."""

    effective_binding = binding or confirmed_sps_facet_binding()
    if not isinstance(logical_request, FacetDiscoveryRequest):
        raise SpsFacetContextError("facet_logical_request_invalid")
    if logical_request.name not in {"root_total", "category_tree"}:
        raise SpsFacetContextError("facet_request_kind_not_allowed")
    source_url = build_structural_discovery_url(logical_request.name)
    structural = validate_la_colonia_structural_request(source_url)
    return PreparedSpsFacetExecution(
        logical_request=logical_request,
        structural_request=structural,
        binding=effective_binding,
        context=context,
    )
