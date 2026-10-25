"""Decide cómo usar una radiografía sanitizada de ubicación sin conceder autoridad.

La radiografía live puede demostrar dos escenarios distintos:

- ``city``: el contexto comercial queda ligado a San Pedro Sula y puede proponerse
  el binding técnico de ``la_colonia_sps``;
- ``store``: la ciudad NO puede promoverse como una sola ubicación comercial;
  primero deben modelarse y bindearse las tiendas dentro de SPS.

Este módulo es 100% offline. No abre red, no modifica el catálogo global y nunca
habilita extracción. Incluso una evidencia fuerte sólo produce una propuesta que
mantiene ``extraction_enabled=False``.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from .locations import LocationConfig, LocationGranularity


class LocationBindingTransitionError(ValueError):
    """El artefacto no es apto para proponer una transición de ubicación."""


class LocationBindingTransitionStatus(StrEnum):
    CITY_BINDING_READY = "city_binding_ready"
    STORE_BINDING_DISCOVERY_REQUIRED = "store_binding_discovery_required"
    INCONCLUSIVE = "inconclusive"


_SOURCE_KEY_PATTERN = re.compile(
    r"^[A-Za-z][A-Za-z0-9_]*:[a-z0-9]+:sha256:[0-9a-f]{64}$"
)


@dataclass(frozen=True, slots=True)
class LocationBindingTransition:
    status: LocationBindingTransitionStatus
    location_id: str
    granularity_candidate: LocationGranularity
    confidence: str
    source_location_key_candidate: str | None
    selected_store: str | None
    artifact_sha256: str
    evidence_ref: str
    technical_binding_confirmed_for_location: bool
    requires_store_binding_discovery: bool
    extraction_enabled: bool = False
    production_authority: bool = False
    catalog_accepted: bool = False

    def __post_init__(self) -> None:
        if self.extraction_enabled or self.production_authority or self.catalog_accepted:
            raise LocationBindingTransitionError("transition_cannot_grant_authority")
        if not re.fullmatch(r"[0-9a-f]{64}", self.artifact_sha256):
            raise LocationBindingTransitionError("artifact_sha256_invalid")
        expected_ref = f"location_binding_radiography:sha256:{self.artifact_sha256}"
        if self.evidence_ref != expected_ref:
            raise LocationBindingTransitionError("evidence_ref_invalid")
        if self.technical_binding_confirmed_for_location:
            if self.status is not LocationBindingTransitionStatus.CITY_BINDING_READY:
                raise LocationBindingTransitionError("binding_confirmation_status_invalid")
            if self.granularity_candidate is not LocationGranularity.CITY:
                raise LocationBindingTransitionError("binding_confirmation_granularity_invalid")
            if not self.source_location_key_candidate:
                raise LocationBindingTransitionError("binding_confirmation_key_required")
        if self.requires_store_binding_discovery != (
            self.status is LocationBindingTransitionStatus.STORE_BINDING_DISCOVERY_REQUIRED
        ):
            raise LocationBindingTransitionError("store_discovery_status_invalid")


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    try:
        rendered = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise LocationBindingTransitionError("artifact_not_json_serializable") from exc
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _require_false(payload: Mapping[str, Any], field: str) -> None:
    if payload.get(field) is not False:
        raise LocationBindingTransitionError(f"{field}_must_be_false")


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise LocationBindingTransitionError("text_field_invalid")
    return value


def _source_key(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _SOURCE_KEY_PATTERN.fullmatch(value):
        raise LocationBindingTransitionError("source_location_key_candidate_invalid")
    return value


def evaluate_location_binding_artifact(
    payload: Mapping[str, Any],
    *,
    location_id: str = "la_colonia_sps",
) -> LocationBindingTransition:
    """Evalúa un artifact sanitizado; sólo evidencia live puede proponer binding."""

    if not isinstance(payload, Mapping):
        raise LocationBindingTransitionError("artifact_mapping_required")
    if payload.get("mode") != "live":
        raise LocationBindingTransitionError("live_evidence_required")
    if payload.get("stop_reason") is not None:
        raise LocationBindingTransitionError("successful_capture_required")
    if payload.get("target_host") != "www.lacolonia.com":
        raise LocationBindingTransitionError("unexpected_target_host")
    if payload.get("target_city") != "San Pedro Sula":
        raise LocationBindingTransitionError("unexpected_target_city")
    for field in ("production_authority", "catalog_accepted", "extraction_enabled"):
        _require_false(payload, field)

    binding = payload.get("binding_report")
    if not isinstance(binding, Mapping):
        raise LocationBindingTransitionError("binding_report_required")
    if binding.get("raw_values_exposed") is not False:
        raise LocationBindingTransitionError("sanitized_binding_report_required")

    granularity_raw = binding.get("granularity_candidate")
    try:
        granularity = LocationGranularity(granularity_raw)
    except (TypeError, ValueError) as exc:
        raise LocationBindingTransitionError("granularity_candidate_invalid") from exc
    if granularity not in {
        LocationGranularity.CITY,
        LocationGranularity.STORE,
        LocationGranularity.UNKNOWN,
    }:
        raise LocationBindingTransitionError("granularity_candidate_unsupported")

    confidence = binding.get("confidence")
    if confidence not in {"none", "weak", "strong"}:
        raise LocationBindingTransitionError("binding_confidence_invalid")
    technical = binding.get("technical_binding_observed")
    if not isinstance(technical, bool):
        raise LocationBindingTransitionError("technical_binding_observed_invalid")
    source_key = _source_key(binding.get("source_location_key_candidate"))
    selected_store = _optional_text(payload.get("selected_store"))
    store_selection_observed = payload.get("store_selection_observed")
    if not isinstance(store_selection_observed, bool):
        raise LocationBindingTransitionError("store_selection_observed_invalid")

    digest = _canonical_sha256(payload)
    evidence_ref = f"location_binding_radiography:sha256:{digest}"

    if granularity is LocationGranularity.STORE:
        if confidence != "strong" or not technical or source_key is None:
            raise LocationBindingTransitionError("strong_store_binding_required")
        if not store_selection_observed or selected_store is None:
            raise LocationBindingTransitionError("selected_store_evidence_required")
        return LocationBindingTransition(
            status=LocationBindingTransitionStatus.STORE_BINDING_DISCOVERY_REQUIRED,
            location_id=location_id,
            granularity_candidate=granularity,
            confidence=confidence,
            source_location_key_candidate=source_key,
            selected_store=selected_store,
            artifact_sha256=digest,
            evidence_ref=evidence_ref,
            technical_binding_confirmed_for_location=False,
            requires_store_binding_discovery=True,
        )

    if granularity is LocationGranularity.CITY:
        if confidence != "strong" or not technical or source_key is None:
            raise LocationBindingTransitionError("strong_city_binding_required")
        return LocationBindingTransition(
            status=LocationBindingTransitionStatus.CITY_BINDING_READY,
            location_id=location_id,
            granularity_candidate=granularity,
            confidence=confidence,
            source_location_key_candidate=source_key,
            selected_store=selected_store,
            artifact_sha256=digest,
            evidence_ref=evidence_ref,
            technical_binding_confirmed_for_location=True,
            requires_store_binding_discovery=False,
        )

    if technical or source_key is not None or confidence == "strong":
        raise LocationBindingTransitionError("unknown_binding_must_remain_non_authoritative")
    return LocationBindingTransition(
        status=LocationBindingTransitionStatus.INCONCLUSIVE,
        location_id=location_id,
        granularity_candidate=LocationGranularity.UNKNOWN,
        confidence=confidence,
        source_location_key_candidate=None,
        selected_store=selected_store,
        artifact_sha256=digest,
        evidence_ref=evidence_ref,
        technical_binding_confirmed_for_location=False,
        requires_store_binding_discovery=False,
    )


def propose_city_location_binding(
    current: LocationConfig,
    transition: LocationBindingTransition,
) -> LocationConfig:
    """Devuelve una copia propuesta; jamás habilita extracción automáticamente."""

    if not isinstance(current, LocationConfig):
        raise LocationBindingTransitionError("current_location_invalid")
    if not isinstance(transition, LocationBindingTransition):
        raise LocationBindingTransitionError("transition_invalid")
    if transition.status is not LocationBindingTransitionStatus.CITY_BINDING_READY:
        raise LocationBindingTransitionError("city_binding_transition_required")
    if current.location_id != transition.location_id:
        raise LocationBindingTransitionError("location_id_mismatch")
    if current.supermarket_id != "la_colonia" or current.city_id != "sps":
        raise LocationBindingTransitionError("la_colonia_sps_required")
    if current.extraction_enabled:
        raise LocationBindingTransitionError("current_location_must_be_disabled")
    if not transition.source_location_key_candidate:
        raise LocationBindingTransitionError("source_location_key_candidate_required")

    return replace(
        current,
        granularity=LocationGranularity.CITY,
        technical_binding_confirmed=True,
        source_location_key=transition.source_location_key_candidate,
        evidence=transition.evidence_ref,
        extraction_enabled=False,
    )
