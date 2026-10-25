"""Adapter offline para ``Get Script Settings`` de Cloudflare Workers.

No realiza HTTP ni recibe tokens. Convierte una respuesta JSON ya obtenida del
endpoint oficial de Script Settings en evidencia normalizada y fail-closed para
tracing/observability. Esta evidencia todavía no concede autoridad productiva:
la llamada real y su procedencia deberán verificarse fuera de este parser.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import NoReturn

from precios_supermercados.edge_provenance import canonical_json_bytes

SCRIPT_SETTINGS_SCHEMA_VERSION = "1"
SETTINGS_SOURCE = "cloudflare_rest_api"
_FULL_RATE_PPM = 1_000_000
_DOMAIN = b"precios-sps/cloudflare-script-settings/v1\0"


class CloudflareScriptSettingsError(ValueError):
    """La respuesta de Script Settings es ambigua, incompleta o inválida."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


def _fail(code: str) -> NoReturn:
    raise CloudflareScriptSettingsError(code)


def _mapping(value: object, code: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _fail(code)
    return value


def _bool(value: object, code: str) -> bool:
    if not isinstance(value, bool):
        _fail(code)
    return value


def _optional_bool(value: object, code: str) -> bool | None:
    if value is None:
        return None
    return _bool(value, code)


def _sampling_ppm(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        _fail(code)
    try:
        decimal = Decimal(str(value))
    except InvalidOperation:
        _fail(code)
    if not decimal.is_finite() or decimal < 0 or decimal > 1:
        _fail(code)
    scaled = decimal * _FULL_RATE_PPM
    if scaled != scaled.to_integral_value():
        _fail(code)
    return int(scaled)


def _errors_are_empty(value: object) -> bool:
    return isinstance(value, list) and len(value) == 0


def _json_object(value: object, code: str) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            _fail(code)
        return _mapping(decoded, code)
    _fail(code)


@dataclass(frozen=True, slots=True)
class CloudflareScriptSettingsEvidence:
    """Subset de configuración de tracing/observability demostrado por settings."""

    observability_enabled: bool
    request_head_sampling_rate_ppm: int
    traces_enabled: bool
    traces_head_sampling_rate_ppm: int
    traces_persist: bool | None
    traces_propagation_policy: str | None
    source: str = SETTINGS_SOURCE
    schema_version: str = SCRIPT_SETTINGS_SCHEMA_VERSION
    production_authority: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != SCRIPT_SETTINGS_SCHEMA_VERSION:
            _fail("settings_schema_version_invalid")
        for name in ("observability_enabled", "traces_enabled"):
            if not isinstance(getattr(self, name), bool):
                _fail(f"settings_{name}_invalid")
        for name in ("request_head_sampling_rate_ppm", "traces_head_sampling_rate_ppm"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _FULL_RATE_PPM:
                _fail(f"settings_{name}_invalid")
        if self.traces_persist is not None and not isinstance(self.traces_persist, bool):
            _fail("settings_traces_persist_invalid")
        if self.traces_propagation_policy not in {None, "authenticated", "accept"}:
            _fail("settings_traces_propagation_policy_invalid")
        if self.source != SETTINGS_SOURCE:
            _fail("settings_source_invalid")
        if self.production_authority is not False:
            _fail("settings_production_authority_forbidden")

    @property
    def full_trace_sampling(self) -> bool:
        return self.traces_head_sampling_rate_ppm == _FULL_RATE_PPM

    def canonical_dict(self) -> dict[str, object]:
        return {
            "observability_enabled": self.observability_enabled,
            "production_authority": self.production_authority,
            "request_head_sampling_rate_ppm": self.request_head_sampling_rate_ppm,
            "schema_version": self.schema_version,
            "source": self.source,
            "traces_enabled": self.traces_enabled,
            "traces_head_sampling_rate_ppm": self.traces_head_sampling_rate_ppm,
            "traces_persist": self.traces_persist,
            "traces_propagation_policy": self.traces_propagation_policy,
        }

    @property
    def digest(self) -> str:
        return hashlib.sha256(_DOMAIN + canonical_json_bytes(self.canonical_dict())).hexdigest()


def parse_cloudflare_script_settings(response: object) -> CloudflareScriptSettingsEvidence:
    """Parsea fail-closed el envelope de ``Get Script Settings``.

    Cloudflare documenta ``observability.enabled`` y un bloque independiente
    ``observability.traces``. No se infiere tracing desde el flag global y los
    defaults documentados no se materializan cuando el API omite un campo: para
    esta frontera una omisión es evidencia insuficiente.
    """

    envelope = _json_object(response, "settings_response_invalid")
    if envelope.get("success") is not True:
        _fail("settings_api_unsuccessful")
    if not _errors_are_empty(envelope.get("errors")):
        _fail("settings_api_errors_present")
    result = _mapping(envelope.get("result"), "settings_result_invalid")
    observability = _mapping(result.get("observability"), "settings_observability_missing")
    traces = _mapping(observability.get("traces"), "settings_traces_missing")

    if "enabled" not in observability:
        _fail("settings_observability_enabled_missing")
    if "head_sampling_rate" not in observability:
        _fail("settings_observability_sampling_missing")
    if "enabled" not in traces:
        _fail("settings_traces_enabled_missing")
    if "head_sampling_rate" not in traces:
        _fail("settings_traces_sampling_missing")

    propagation = traces.get("propagation_policy")
    if propagation is not None and propagation not in {"authenticated", "accept"}:
        _fail("settings_traces_propagation_policy_invalid")

    return CloudflareScriptSettingsEvidence(
        observability_enabled=_bool(
            observability.get("enabled"), "settings_observability_enabled_invalid"
        ),
        request_head_sampling_rate_ppm=_sampling_ppm(
            observability.get("head_sampling_rate"), "settings_observability_sampling_invalid"
        ),
        traces_enabled=_bool(traces.get("enabled"), "settings_traces_enabled_invalid"),
        traces_head_sampling_rate_ppm=_sampling_ppm(
            traces.get("head_sampling_rate"), "settings_traces_sampling_invalid"
        ),
        traces_persist=_optional_bool(traces.get("persist"), "settings_traces_persist_invalid"),
        traces_propagation_policy=propagation,
    )


def immutable_settings_snapshot(evidence: CloudflareScriptSettingsEvidence) -> Mapping[str, object]:
    """Expone un snapshot de sólo lectura para logging/attestation posterior."""

    if not isinstance(evidence, CloudflareScriptSettingsEvidence):
        _fail("settings_evidence_invalid")
    return MappingProxyType(evidence.canonical_dict())
