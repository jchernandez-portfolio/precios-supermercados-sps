"""Evidencia física de Cloudflare Workers Tracing para facet discovery.

Este contrato es deliberadamente separado de ``CloudflareOriginTraceEvidence``:
root_total/category_tree no son páginas de traversal del catálogo. El módulo no
consulta Cloudflare; sólo normaliza/reconcilia evidencia de plataforma que un
adapter independiente deberá obtener después.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import NoReturn

from precios_supermercados.edge_provenance import canonical_json_bytes
from precios_supermercados.edge_structural_observation import (
    CryptographicallyVerifiedStructuralObservation,
)

STRUCTURAL_TRACE_EVIDENCE_DOMAIN = b"precios-sps/cloudflare-structural-trace/v1\0"
STRUCTURAL_TRACE_CONTRACT_VERSION = "1"
STRUCTURAL_EXECUTION_SPAN_NAME = "precios_sps_structural_execution"
STRUCTURAL_PLATFORM_PROVIDER = "cloudflare_workers_tracing"
CLOUD_PROVIDER = "cloudflare"
CLOUD_PLATFORM = "cloudflare.workers"
MAX_CLOCK_SKEW = timedelta(seconds=10)

_OPAQUE = re.compile(r"[^\s]{1,512}\Z")
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ALLOWED_KINDS = {"root_total", "category_tree"}


class CloudflareStructuralTraceEvidenceError(ValueError):
    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


def _fail(code: str, message: str | None = None) -> NoReturn:
    raise CloudflareStructuralTraceEvidenceError(code, message)


def _text(value: object, code: str, *, maximum: int = 512) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or len(value) > maximum
        or not _OPAQUE.fullmatch(value)
    ):
        _fail(code)
    return value


def _sha1(value: object, code: str) -> str:
    text = _text(value, code, maximum=40)
    if not _SHA1.fullmatch(text):
        _fail(code)
    return text


def _sha256(value: object, code: str) -> str:
    text = _text(value, code, maximum=64)
    if not _SHA256.fullmatch(text):
        _fail(code)
    return text


def _utc(value: object, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(UTC)


def _int(value: object, code: str, *, minimum: int = 0, maximum: int = 2**53 - 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        _fail(code)
    return value


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class CloudflareStructuralTraceEvidence:
    """Custom span estructural y su único child span de fetch físico."""

    trace_id: str
    custom_span_id: str
    fetch_span_id: str
    fetch_parent_span_id: str
    faas_invocation_id: str
    service_name: str
    script_version_id: str
    custom_span_name: str
    trace_contract_version: str
    cloud_provider: str
    cloud_platform: str
    collector_provider: str
    authorization_id: str
    run_id: str
    approved_commit_sha: str
    reservation_id: str
    request_id: str
    request_digest: str
    request_kind: str
    fetch_url: str
    fetch_method: str
    fetch_status: int
    fetch_response_body_size: int
    custom_started_at_utc: datetime
    custom_completed_at_utc: datetime
    fetch_started_at_utc: datetime
    fetch_completed_at_utc: datetime
    production_authority: bool = False

    def __post_init__(self) -> None:
        for name in (
            "trace_id",
            "custom_span_id",
            "fetch_span_id",
            "fetch_parent_span_id",
            "faas_invocation_id",
            "service_name",
            "script_version_id",
            "authorization_id",
            "run_id",
            "reservation_id",
            "request_id",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), f"{name}_invalid"))
        object.__setattr__(self, "approved_commit_sha", _sha1(self.approved_commit_sha, "approved_commit_sha_invalid"))
        object.__setattr__(self, "request_digest", _sha256(self.request_digest, "request_digest_invalid"))
        if self.request_kind not in _ALLOWED_KINDS:
            _fail("request_kind_invalid")
        if self.custom_span_name != STRUCTURAL_EXECUTION_SPAN_NAME:
            _fail("custom_span_name_invalid")
        if self.trace_contract_version != STRUCTURAL_TRACE_CONTRACT_VERSION:
            _fail("trace_contract_version_invalid")
        if self.cloud_provider != CLOUD_PROVIDER:
            _fail("cloud_provider_invalid")
        if self.cloud_platform != CLOUD_PLATFORM:
            _fail("cloud_platform_invalid")
        if self.collector_provider != "cloudflare_workers":
            _fail("collector_provider_invalid")
        if self.fetch_parent_span_id != self.custom_span_id:
            _fail("fetch_parent_span_mismatch")
        if self.fetch_span_id == self.custom_span_id:
            _fail("fetch_span_identity_invalid")
        if (
            not isinstance(self.fetch_url, str)
            or not self.fetch_url
            or self.fetch_url.strip() != self.fetch_url
            or len(self.fetch_url) > 20_000
        ):
            _fail("fetch_url_invalid")
        if self.fetch_method != "GET":
            _fail("fetch_method_invalid")
        _int(self.fetch_status, "fetch_status_invalid", minimum=100, maximum=599)
        _int(self.fetch_response_body_size, "fetch_response_body_size_invalid")

        custom_start = _utc(self.custom_started_at_utc, "custom_started_at_invalid")
        custom_end = _utc(self.custom_completed_at_utc, "custom_completed_at_invalid")
        fetch_start = _utc(self.fetch_started_at_utc, "fetch_started_at_invalid")
        fetch_end = _utc(self.fetch_completed_at_utc, "fetch_completed_at_invalid")
        if custom_end < custom_start:
            _fail("custom_span_time_order_invalid")
        if fetch_end < fetch_start:
            _fail("fetch_span_time_order_invalid")
        if fetch_start < custom_start or fetch_end > custom_end:
            _fail("fetch_span_outside_custom_span")
        object.__setattr__(self, "custom_started_at_utc", custom_start)
        object.__setattr__(self, "custom_completed_at_utc", custom_end)
        object.__setattr__(self, "fetch_started_at_utc", fetch_start)
        object.__setattr__(self, "fetch_completed_at_utc", fetch_end)
        if self.production_authority is not False:
            _fail("production_authority_forbidden")

    def canonical_dict(self) -> dict[str, object]:
        return {
            "approved_commit_sha": self.approved_commit_sha,
            "authorization_id": self.authorization_id,
            "cloud_platform": self.cloud_platform,
            "cloud_provider": self.cloud_provider,
            "collector_provider": self.collector_provider,
            "custom_completed_at_utc": _timestamp(self.custom_completed_at_utc),
            "custom_span_id": self.custom_span_id,
            "custom_span_name": self.custom_span_name,
            "custom_started_at_utc": _timestamp(self.custom_started_at_utc),
            "faas_invocation_id": self.faas_invocation_id,
            "fetch_completed_at_utc": _timestamp(self.fetch_completed_at_utc),
            "fetch_method": self.fetch_method,
            "fetch_parent_span_id": self.fetch_parent_span_id,
            "fetch_response_body_size": self.fetch_response_body_size,
            "fetch_span_id": self.fetch_span_id,
            "fetch_started_at_utc": _timestamp(self.fetch_started_at_utc),
            "fetch_status": self.fetch_status,
            "fetch_url": self.fetch_url,
            "platform_provider": STRUCTURAL_PLATFORM_PROVIDER,
            "request_digest": self.request_digest,
            "request_id": self.request_id,
            "request_kind": self.request_kind,
            "reservation_id": self.reservation_id,
            "run_id": self.run_id,
            "script_version_id": self.script_version_id,
            "service_name": self.service_name,
            "trace_contract_version": self.trace_contract_version,
            "trace_id": self.trace_id,
        }

    @property
    def physical_evidence_id(self) -> str:
        return hashlib.sha256(
            STRUCTURAL_TRACE_EVIDENCE_DOMAIN + canonical_json_bytes(self.canonical_dict())
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class PlatformReconciledStructuralObservation:
    observation: CryptographicallyVerifiedStructuralObservation
    trace_evidence: CloudflareStructuralTraceEvidence
    platform_evidence_reconciled: bool = True
    production_authority: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.observation, CryptographicallyVerifiedStructuralObservation):
            _fail("structural_observation_invalid")
        if not isinstance(self.trace_evidence, CloudflareStructuralTraceEvidence):
            _fail("structural_trace_evidence_invalid")
        if self.platform_evidence_reconciled is not True:
            _fail("platform_evidence_not_reconciled")
        if self.production_authority is not False:
            _fail("production_authority_forbidden")

    @property
    def physical_evidence_id(self) -> str:
        return self.trace_evidence.physical_evidence_id


def _matches_identity(
    evidence: CloudflareStructuralTraceEvidence,
    observation: CryptographicallyVerifiedStructuralObservation,
) -> bool:
    payload = observation.verified_receipt.receipt.payload
    return (
        evidence.authorization_id == payload.authorization_id
        and evidence.run_id == payload.run_id
        and evidence.approved_commit_sha == payload.approved_commit_sha
        and evidence.reservation_id == payload.reservation_id
        and evidence.request_id == payload.request_id
        and evidence.request_digest == payload.request_digest
        and evidence.request_kind == payload.request_kind
    )


def reconcile_cloudflare_structural_trace(
    observation: CryptographicallyVerifiedStructuralObservation,
    candidates: Sequence[CloudflareStructuralTraceEvidence],
    *,
    clock_skew: timedelta = MAX_CLOCK_SKEW,
) -> PlatformReconciledStructuralObservation:
    if not isinstance(observation, CryptographicallyVerifiedStructuralObservation):
        _fail("structural_observation_invalid")
    if observation.cryptographic_signature_verified is not True:
        _fail("structural_observation_signature_unverified")
    if observation.structural_body_validated is not True:
        _fail("structural_observation_body_unvalidated")
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)):
        _fail("trace_candidates_invalid")
    if not isinstance(clock_skew, timedelta) or clock_skew < timedelta(0) or clock_skew > timedelta(minutes=1):
        _fail("clock_skew_invalid")

    matches: list[CloudflareStructuralTraceEvidence] = []
    for candidate in candidates:
        if not isinstance(candidate, CloudflareStructuralTraceEvidence):
            _fail("trace_candidate_invalid")
        if _matches_identity(candidate, observation):
            matches.append(candidate)
    if not matches:
        _fail("matching_trace_missing")
    if len(matches) != 1:
        _fail("matching_trace_not_unique")

    evidence = matches[0]
    payload = observation.verified_receipt.receipt.payload
    if evidence.fetch_url != observation.source_url:
        _fail("trace_fetch_url_mismatch")
    if evidence.fetch_method != "GET":
        _fail("trace_fetch_method_mismatch")
    if evidence.fetch_status != payload.response_status or evidence.fetch_status != 200:
        _fail("trace_fetch_status_mismatch")
    if evidence.fetch_response_body_size != payload.response_body_bytes:
        _fail("trace_fetch_body_size_mismatch")
    if evidence.script_version_id != payload.collector_release_id:
        _fail("trace_script_version_mismatch")

    physical_start = payload.physical_started_at_utc.astimezone(UTC)
    response_end = payload.response_completed_at_utc.astimezone(UTC)
    if evidence.fetch_started_at_utc < physical_start - clock_skew:
        _fail("trace_fetch_started_too_early")
    if evidence.fetch_started_at_utc > response_end + clock_skew:
        _fail("trace_fetch_started_too_late")
    if evidence.fetch_completed_at_utc < physical_start - clock_skew:
        _fail("trace_fetch_completed_too_early")
    if evidence.fetch_completed_at_utc > response_end + clock_skew:
        _fail("trace_fetch_completed_too_late")

    return PlatformReconciledStructuralObservation(
        observation=observation,
        trace_evidence=evidence,
    )


def assert_distinct_structural_evidence(
    root_total: PlatformReconciledStructuralObservation,
    category_tree: PlatformReconciledStructuralObservation,
) -> None:
    if not isinstance(root_total, PlatformReconciledStructuralObservation) or not isinstance(
        category_tree, PlatformReconciledStructuralObservation
    ):
        _fail("platform_reconciled_structural_observation_invalid")
    left = root_total.trace_evidence
    right = category_tree.trace_evidence
    if left.request_kind != "root_total" or right.request_kind != "category_tree":
        _fail("structural_request_kinds_invalid")
    if left.authorization_id != right.authorization_id or left.run_id != right.run_id:
        _fail("structural_execution_context_mismatch")
    if left.physical_evidence_id == right.physical_evidence_id:
        _fail("structural_physical_evidence_reused")
    if left.fetch_span_id == right.fetch_span_id:
        _fail("structural_fetch_span_reused")
    if left.trace_id == right.trace_id and left.custom_span_id == right.custom_span_id:
        _fail("structural_custom_trace_span_reused")
