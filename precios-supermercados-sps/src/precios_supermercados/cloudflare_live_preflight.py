"""Preflight offline para la futura frontera live sobre Cloudflare.

Este módulo no consulta Cloudflare, no despliega y no concede autoridad
productiva. Su propósito es cerrar el contrato exacto de la evidencia de
configuración que deberá obtenerse de Cloudflare antes de pedir una nueva
autorización humana para un smoke live.

La evidencia aquí modelada sigue siendo caller-controlled mientras no exista
un adapter que la obtenga y autentique contra la API real de Cloudflare. Por
eso ``production_authority`` permanece siempre ``False`` incluso cuando todos
los prerrequisitos técnicos declarados reconcilian.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import NoReturn

from precios_supermercados.edge_provenance import canonical_json_bytes
from precios_supermercados.edge_provenance_run import EdgeProvenanceRunManifest

PREFLIGHT_SCHEMA_VERSION = "1"
_DEPLOYMENT_EVIDENCE_DOMAIN = b"precios-sps/cloudflare-deployment-evidence/v1\0"
_PREFLIGHT_DOMAIN = b"precios-sps/cloudflare-live-preflight/v1\0"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_EXPECTED_PROVIDER = "cloudflare_workers"
_EXPECTED_SOURCE = "cloudflare_rest_api"
_EXPECTED_HOST = "www.lacolonia.com"
_FULL_TRACE_SAMPLING_PPM = 1_000_000


class CloudflareLivePreflightError(ValueError):
    """La evidencia declarada de despliegue no cumple el contrato offline."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


def _fail(code: str, message: str | None = None) -> NoReturn:
    raise CloudflareLivePreflightError(code, message)


def _text(value: object, code: str, *, maximum: int = 512) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or len(value) > maximum
        or any(character.isspace() for character in value)
    ):
        _fail(code)
    return value


def _sha256(value: object, code: str) -> str:
    text = _text(value, code, maximum=64)
    if not _SHA256.fullmatch(text):
        _fail(code)
    return text


def _utc(value: object, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(UTC)


def _iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class CloudflareDeploymentEvidence:
    """Snapshot normalizado que un futuro adapter deberá obtener de Cloudflare."""

    account_id_sha256: str
    script_name: str
    deployment_id: str
    script_version_id: str
    deployed_code_sha256: str
    signing_key_id: str
    signing_public_key_spki_sha256: str
    tracing_enabled: bool
    tracing_sampling_rate_ppm: int
    observability_enabled: bool
    observability_query_roundtrip_succeeded: bool
    egress_allowlist_enforced: bool
    allowed_origin_host: str
    observed_at_utc: datetime
    source: str = _EXPECTED_SOURCE
    schema_version: str = PREFLIGHT_SCHEMA_VERSION
    production_authority: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != PREFLIGHT_SCHEMA_VERSION:
            _fail("deployment_schema_version_invalid")
        for name in ("script_name", "deployment_id", "script_version_id", "signing_key_id"):
            object.__setattr__(self, name, _text(getattr(self, name), f"deployment_{name}_invalid"))
        for name in (
            "account_id_sha256",
            "deployed_code_sha256",
            "signing_public_key_spki_sha256",
        ):
            object.__setattr__(self, name, _sha256(getattr(self, name), f"deployment_{name}_invalid"))
        for name in (
            "tracing_enabled",
            "observability_enabled",
            "observability_query_roundtrip_succeeded",
            "egress_allowlist_enforced",
        ):
            if not isinstance(getattr(self, name), bool):
                _fail(f"deployment_{name}_invalid")
        if (
            isinstance(self.tracing_sampling_rate_ppm, bool)
            or not isinstance(self.tracing_sampling_rate_ppm, int)
            or not 0 <= self.tracing_sampling_rate_ppm <= _FULL_TRACE_SAMPLING_PPM
        ):
            _fail("deployment_tracing_sampling_rate_invalid")
        if self.allowed_origin_host != _EXPECTED_HOST:
            _fail("deployment_allowed_origin_host_invalid")
        if self.source != _EXPECTED_SOURCE:
            _fail("deployment_source_invalid")
        object.__setattr__(self, "observed_at_utc", _utc(self.observed_at_utc, "deployment_observed_at_invalid"))
        if self.production_authority is not False:
            _fail("deployment_production_authority_forbidden")

    def canonical_dict(self) -> dict[str, object]:
        return {
            "account_id_sha256": self.account_id_sha256,
            "allowed_origin_host": self.allowed_origin_host,
            "deployed_code_sha256": self.deployed_code_sha256,
            "deployment_id": self.deployment_id,
            "egress_allowlist_enforced": self.egress_allowlist_enforced,
            "observability_enabled": self.observability_enabled,
            "observability_query_roundtrip_succeeded": self.observability_query_roundtrip_succeeded,
            "observed_at_utc": _iso_z(self.observed_at_utc),
            "schema_version": self.schema_version,
            "script_name": self.script_name,
            "script_version_id": self.script_version_id,
            "signing_key_id": self.signing_key_id,
            "signing_public_key_spki_sha256": self.signing_public_key_spki_sha256,
            "source": self.source,
            "tracing_enabled": self.tracing_enabled,
            "tracing_sampling_rate_ppm": self.tracing_sampling_rate_ppm,
        }

    @property
    def digest(self) -> str:
        return hashlib.sha256(
            _DEPLOYMENT_EVIDENCE_DOMAIN + canonical_json_bytes(self.canonical_dict())
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class CloudflareLivePreflightAssessment:
    """Resultado cerrado: puede habilitar pedir permiso, nunca habilitar live."""

    run_manifest_digest: str
    deployment_evidence_digest: str
    technical_prerequisites_satisfied: bool
    ready_for_human_live_authorization_request: bool
    blockers: tuple[str, ...]
    schema_version: str = PREFLIGHT_SCHEMA_VERSION
    production_authority: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != PREFLIGHT_SCHEMA_VERSION:
            _fail("assessment_schema_version_invalid")
        object.__setattr__(
            self,
            "run_manifest_digest",
            _sha256(self.run_manifest_digest, "assessment_run_manifest_digest_invalid"),
        )
        object.__setattr__(
            self,
            "deployment_evidence_digest",
            _sha256(self.deployment_evidence_digest, "assessment_deployment_evidence_digest_invalid"),
        )
        if not isinstance(self.technical_prerequisites_satisfied, bool):
            _fail("assessment_technical_prerequisites_invalid")
        if not isinstance(self.ready_for_human_live_authorization_request, bool):
            _fail("assessment_ready_for_human_authorization_invalid")
        if self.ready_for_human_live_authorization_request != self.technical_prerequisites_satisfied:
            _fail("assessment_readiness_inconsistent")
        if not isinstance(self.blockers, tuple) or any(
            not isinstance(blocker, str) or not blocker for blocker in self.blockers
        ):
            _fail("assessment_blockers_invalid")
        if len(set(self.blockers)) != len(self.blockers):
            _fail("assessment_blockers_duplicate")
        mandatory = {
            "human_live_authorization_required",
            "sps_context_unconfirmed",
            "production_authority_not_established",
        }
        if not mandatory.issubset(self.blockers):
            _fail("assessment_mandatory_blocker_missing")
        if self.production_authority is not False:
            _fail("assessment_production_authority_forbidden")

    def canonical_dict(self) -> dict[str, object]:
        return {
            "blockers": list(self.blockers),
            "deployment_evidence_digest": self.deployment_evidence_digest,
            "production_authority": self.production_authority,
            "ready_for_human_live_authorization_request": self.ready_for_human_live_authorization_request,
            "run_manifest_digest": self.run_manifest_digest,
            "schema_version": self.schema_version,
            "technical_prerequisites_satisfied": self.technical_prerequisites_satisfied,
        }

    @property
    def digest(self) -> str:
        return hashlib.sha256(_PREFLIGHT_DOMAIN + canonical_json_bytes(self.canonical_dict())).hexdigest()


def assess_cloudflare_live_preflight(
    manifest: EdgeProvenanceRunManifest,
    deployment: CloudflareDeploymentEvidence,
) -> CloudflareLivePreflightAssessment:
    """Reconcilia manifest y snapshot sin convertirlos en permiso para tráfico."""

    if not isinstance(manifest, EdgeProvenanceRunManifest):
        _fail("run_manifest_invalid")
    if not isinstance(deployment, CloudflareDeploymentEvidence):
        _fail("deployment_evidence_invalid")
    if manifest.production_authority is not False:
        _fail("run_manifest_production_authority_invalid")
    if deployment.production_authority is not False:
        _fail("deployment_production_authority_invalid")

    technical_blockers: list[str] = []
    if manifest.collector_provider != _EXPECTED_PROVIDER:
        technical_blockers.append("collector_provider_not_cloudflare_workers")
    if deployment.script_version_id != manifest.collector_release_id:
        technical_blockers.append("deployment_release_mismatch")
    if deployment.deployed_code_sha256 != manifest.collector_code_sha256:
        technical_blockers.append("deployment_code_mismatch")
    if deployment.signing_key_id != manifest.collector_signing_key_id:
        technical_blockers.append("deployment_signing_key_mismatch")
    if deployment.tracing_enabled is not True:
        technical_blockers.append("tracing_disabled")
    if deployment.tracing_sampling_rate_ppm != _FULL_TRACE_SAMPLING_PPM:
        technical_blockers.append("tracing_sampling_not_full")
    if deployment.observability_enabled is not True:
        technical_blockers.append("observability_disabled")
    if deployment.observability_query_roundtrip_succeeded is not True:
        technical_blockers.append("observability_roundtrip_unverified")
    if deployment.egress_allowlist_enforced is not True:
        technical_blockers.append("egress_allowlist_unverified")

    technical_ok = not technical_blockers
    blockers = tuple(
        [
            *technical_blockers,
            "human_live_authorization_required",
            "sps_context_unconfirmed",
            "production_authority_not_established",
        ]
    )
    return CloudflareLivePreflightAssessment(
        run_manifest_digest=manifest.digest,
        deployment_evidence_digest=deployment.digest,
        technical_prerequisites_satisfied=technical_ok,
        ready_for_human_live_authorization_request=technical_ok,
        blockers=blockers,
    )
