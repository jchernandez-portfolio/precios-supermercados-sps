"""Analiza evidencia de ubicación sin conservar valores fuente en claro.

Este módulo es deliberadamente offline y genérico. Recibe snapshots de contexto
obtenidos en tres momentos posibles (antes de seleccionar ubicación, después de
seleccionar ciudad y después de seleccionar tienda) y devuelve únicamente
fingerprints y una conclusión conservadora sobre la granularidad comercial.

No abre red, no usa Playwright, no concede autoridad live y no habilita
extracción. Su resultado sirve para interpretar una radiografía controlada que
se ejecute por otra capa explícitamente autorizada.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class LocationBindingRadiographyError(ValueError):
    """La evidencia no cumple el contrato cerrado del analizador."""


class BindingGranularity(StrEnum):
    UNKNOWN = "unknown"
    CITY = "city"
    STORE = "store"


class BindingConfidence(StrEnum):
    NONE = "none"
    WEAK = "weak"
    STRONG = "strong"


# Señales conocidas que normalmente representan contexto comercial y no datos de
# producto. Se comparan case-insensitive y sin depender del valor en claro.
_STRONG_CONTEXT_KEYS = frozenset(
    {
        "binding",
        "pickupPoint",
        "regionId",
        "salesChannel",
        "seller",
        "store",
        "storeId",
    }
)
_WEAK_CONTEXT_KEYS = frozenset(
    {
        "vtex_segment",
        "vtex_session",
    }
)


def _canonical_key(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LocationBindingRadiographyError("context_key_invalid")
    return re.sub(r"[^a-z0-9]", "", value.casefold())


_STRONG_CANONICAL = frozenset(_canonical_key(value) for value in _STRONG_CONTEXT_KEYS)
_WEAK_CANONICAL = frozenset(_canonical_key(value) for value in _WEAK_CONTEXT_KEYS)


def _stable_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise LocationBindingRadiographyError("context_value_not_serializable") from exc


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise LocationBindingRadiographyError(f"{field_name}_invalid")
    return value


@dataclass(frozen=True, slots=True)
class ContextStage:
    """Snapshot interno de mecanismos de contexto en un momento del flujo.

    Los valores pueden ser opacos; nunca se exponen en ``LocationBindingReport``.
    ``channels`` típicos: cookie, localStorage, sessionStorage, request_variable,
    request_header. El analizador no asigna semántica por canal, sólo por key.
    """

    name: str
    channels: Mapping[str, Mapping[str, Any]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _required_text(self.name, "stage_name"))
        if not isinstance(self.channels, Mapping):
            raise LocationBindingRadiographyError("stage_channels_invalid")
        normalized: dict[str, Mapping[str, Any]] = {}
        for channel, values in self.channels.items():
            channel_name = _required_text(str(channel), "channel_name")
            if not isinstance(values, Mapping):
                raise LocationBindingRadiographyError("channel_values_invalid")
            normalized[channel_name] = MappingProxyType(dict(values))
        object.__setattr__(self, "channels", MappingProxyType(normalized))


@dataclass(frozen=True, slots=True)
class BindingSignal:
    channel: str
    key: str
    strength: BindingConfidence
    changed_after_city: bool
    changed_after_store: bool
    city_fingerprint: str | None
    store_fingerprint: str | None

    def __post_init__(self) -> None:
        _required_text(self.channel, "signal_channel")
        _required_text(self.key, "signal_key")
        for digest in (self.city_fingerprint, self.store_fingerprint):
            if digest is not None and (
                len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise LocationBindingRadiographyError("signal_fingerprint_invalid")


@dataclass(frozen=True, slots=True)
class LocationBindingReport:
    city_name: str
    store_selection_observed: bool
    store_name: str | None
    granularity_candidate: BindingGranularity
    confidence: BindingConfidence
    technical_binding_observed: bool
    source_location_key_candidate: str | None
    decisive_stage: str | None
    signals: tuple[BindingSignal, ...]
    raw_values_exposed: bool = False

    def __post_init__(self) -> None:
        _required_text(self.city_name, "city_name")
        if not isinstance(self.store_selection_observed, bool):
            raise LocationBindingRadiographyError("store_selection_observed_invalid")
        if self.store_name is not None:
            _required_text(self.store_name, "store_name")
        if self.store_selection_observed and self.store_name is None:
            raise LocationBindingRadiographyError("store_name_required")
        if not self.store_selection_observed and self.store_name is not None:
            raise LocationBindingRadiographyError("store_name_without_selector")
        if self.raw_values_exposed:
            raise LocationBindingRadiographyError("raw_values_must_never_be_exposed")
        if self.technical_binding_observed != bool(self.source_location_key_candidate):
            raise LocationBindingRadiographyError("binding_candidate_consistency_invalid")
        if self.granularity_candidate is BindingGranularity.UNKNOWN:
            if self.confidence is BindingConfidence.STRONG:
                raise LocationBindingRadiographyError("unknown_granularity_cannot_be_strong")
        elif not self.technical_binding_observed:
            raise LocationBindingRadiographyError("granularity_requires_binding_signal")


def _known_strength(key: str) -> BindingConfidence | None:
    canonical = _canonical_key(key)
    if canonical in _STRONG_CANONICAL:
        return BindingConfidence.STRONG
    if canonical in _WEAK_CANONICAL:
        return BindingConfidence.WEAK
    return None


def _flatten(stage: ContextStage) -> dict[tuple[str, str], Any]:
    flattened: dict[tuple[str, str], Any] = {}
    for channel, values in stage.channels.items():
        for key, value in values.items():
            key_text = _required_text(str(key), "context_key")
            if _known_strength(key_text) is None:
                continue
            canonical = _canonical_key(key_text)
            identity = (channel, canonical)
            if identity in flattened:
                raise LocationBindingRadiographyError("duplicate_context_identity")
            # Validar serialización aquí hace fail-closed el snapshot aunque el valor
            # finalmente no cambie entre etapas.
            _stable_json(value)
            flattened[identity] = value
    return flattened


def _value_changed(
    before: Mapping[tuple[str, str], Any],
    after: Mapping[tuple[str, str], Any],
    identity: tuple[str, str],
) -> bool:
    before_present = identity in before
    after_present = identity in after
    if before_present != after_present:
        return True
    if not before_present:
        return False
    return _fingerprint(before[identity]) != _fingerprint(after[identity])


def _stage_digest(
    values: Mapping[tuple[str, str], Any],
    identity: tuple[str, str],
) -> str | None:
    if identity not in values:
        return None
    return _fingerprint(values[identity])


def _candidate_key(signal: BindingSignal, fingerprint: str) -> str:
    # No valor fuente en claro. El prefijo identifica el mecanismo; el hash
    # identifica de forma estable el contexto observado sin convertirlo en secreto.
    return f"{signal.channel}:{signal.key}:sha256:{fingerprint}"


def analyze_location_binding(
    *,
    city_name: str,
    before: ContextStage,
    after_city: ContextStage,
    store_selection_observed: bool,
    after_store: ContextStage | None = None,
    store_name: str | None = None,
) -> LocationBindingReport:
    """Infere ``city|store|unknown`` de forma conservadora.

    Reglas:
    - una señal fuerte que cambia al seleccionar tienda implica granularidad store;
    - cualquier cambio débil posterior a seleccionar tienda impide confirmar city,
      porque podría esconder contexto de tienda dentro de una sesión opaca;
    - si hay señal fuerte al seleccionar ciudad y después de tienda no cambia ni
      una señal fuerte ni una débil, la granularidad candidata es city;
    - cambios sólo en vtex_session/vtex_segment se consideran débiles y no bastan
      para confirmar city/store;
    - si existe selector de tienda se requiere snapshot posterior a la tienda.
    """

    city_name = _required_text(city_name, "city_name")
    if not isinstance(store_selection_observed, bool):
        raise LocationBindingRadiographyError("store_selection_observed_invalid")
    if store_selection_observed:
        if after_store is None:
            raise LocationBindingRadiographyError("after_store_required")
        store_name = _required_text(store_name or "", "store_name")
    elif after_store is not None or store_name is not None:
        raise LocationBindingRadiographyError("store_evidence_without_selector")

    before_values = _flatten(before)
    city_values = _flatten(after_city)
    store_values = _flatten(after_store) if after_store is not None else city_values
    identities = sorted(set(before_values) | set(city_values) | set(store_values))

    signals: list[BindingSignal] = []
    for channel, key in identities:
        identity = (channel, key)
        changed_city = _value_changed(before_values, city_values, identity)
        changed_store = (
            _value_changed(city_values, store_values, identity)
            if store_selection_observed
            else False
        )
        if not changed_city and not changed_store:
            continue
        strength = _known_strength(key)
        assert strength is not None
        signals.append(
            BindingSignal(
                channel=channel,
                key=key,
                strength=strength,
                changed_after_city=changed_city,
                changed_after_store=changed_store,
                city_fingerprint=_stage_digest(city_values, identity),
                store_fingerprint=(
                    _stage_digest(store_values, identity)
                    if store_selection_observed
                    else None
                ),
            )
        )

    strong_store = [
        signal
        for signal in signals
        if signal.strength is BindingConfidence.STRONG
        and signal.changed_after_store
        and signal.store_fingerprint is not None
    ]
    weak_store = [
        signal
        for signal in signals
        if signal.strength is BindingConfidence.WEAK
        and signal.changed_after_store
    ]
    strong_city = [
        signal
        for signal in signals
        if signal.strength is BindingConfidence.STRONG
        and signal.changed_after_city
        and signal.city_fingerprint is not None
    ]
    weak_city = [
        signal
        for signal in signals
        if signal.strength is BindingConfidence.WEAK
        and signal.changed_after_city
    ]

    granularity = BindingGranularity.UNKNOWN
    confidence = BindingConfidence.NONE
    decisive_stage: str | None = None
    decisive: BindingSignal | None = None
    decisive_fingerprint: str | None = None

    if strong_store:
        # Store es la granularidad más específica observada y por seguridad gana
        # incluso si también hubo un cambio fuerte al seleccionar ciudad.
        decisive = strong_store[0]
        decisive_fingerprint = decisive.store_fingerprint
        granularity = BindingGranularity.STORE
        confidence = BindingConfidence.STRONG
        decisive_stage = "after_store"
    elif weak_store:
        # No confirmar city cuando la selección de tienda sí mutó una sesión opaca.
        confidence = BindingConfidence.WEAK
        decisive_stage = "after_store"
    elif strong_city:
        decisive = strong_city[0]
        decisive_fingerprint = decisive.city_fingerprint
        granularity = BindingGranularity.CITY
        confidence = BindingConfidence.STRONG
        decisive_stage = "after_city"
    elif weak_city:
        confidence = BindingConfidence.WEAK
        decisive_stage = "after_city"

    candidate = (
        _candidate_key(decisive, decisive_fingerprint)
        if decisive is not None and decisive_fingerprint is not None
        else None
    )
    return LocationBindingReport(
        city_name=city_name,
        store_selection_observed=store_selection_observed,
        store_name=store_name,
        granularity_candidate=granularity,
        confidence=confidence,
        technical_binding_observed=candidate is not None,
        source_location_key_candidate=candidate,
        decisive_stage=decisive_stage,
        signals=tuple(signals),
        raw_values_exposed=False,
    )


def report_dict(report: LocationBindingReport) -> dict[str, Any]:
    """Envelope JSON seguro para artefactos/CI; jamás contiene valores originales."""

    if not isinstance(report, LocationBindingReport):
        raise LocationBindingRadiographyError("report_invalid")
    return {
        "city_name": report.city_name,
        "store_selection_observed": report.store_selection_observed,
        "store_name": report.store_name,
        "granularity_candidate": report.granularity_candidate.value,
        "confidence": report.confidence.value,
        "technical_binding_observed": report.technical_binding_observed,
        "source_location_key_candidate": report.source_location_key_candidate,
        "decisive_stage": report.decisive_stage,
        "signals": [
            {
                "channel": signal.channel,
                "key": signal.key,
                "strength": signal.strength.value,
                "changed_after_city": signal.changed_after_city,
                "changed_after_store": signal.changed_after_store,
                "city_fingerprint": signal.city_fingerprint,
                "store_fingerprint": signal.store_fingerprint,
            }
            for signal in report.signals
        ],
        "raw_values_exposed": False,
    }


def contains_raw_value(report: LocationBindingReport, raw_values: Sequence[str]) -> bool:
    """Helper defensivo para tests/adversarial review del envelope serializado."""

    serialized = json.dumps(report_dict(report), ensure_ascii=False, sort_keys=True)
    return any(value and value in serialized for value in raw_values)
