"""Contratos v2 provider-neutral para provenance física en edge.

Esta capa define payloads canónicos y reconciliación estructural para receipts
firmados por un collector externo. NO verifica la firma criptográfica contra
una clave pública confiable, NO consulta evidencia externa y NO concede
`catalog_accepted`. Su objetivo es impedir que la forma del contrato quede
atada a Google Cloud mientras se valida Cloudflare Workers como ruta gratuita.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

EDGE_PROVENANCE_SCHEMA_VERSION = "2"
_RECEIPT_DOMAIN = b"precios-sps/edge-receipt/v2\0"
_ATTESTATION_DOMAIN = b"precios-sps/edge-attestation/v2\0"
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_OPAQUE_ID = re.compile(r"[^\s]{1,256}\Z")
_ALLOWED_PROVIDERS = {"cloudflare_workers", "google_cloud_run"}
_ALLOWED_SIGNATURE_ALGORITHMS = {"Ed25519"}
_ALLOWED_HTTP_METHODS = {"GET", "POST"}


def _required_text(value: object, label: str, *, max_length: int = 512) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} debe ser texto")
    text = value.strip()
    if not text:
        raise ValueError(f"{label} no puede estar vacío")
    if len(text) > max_length:
        raise ValueError(f"{label} excede {max_length} caracteres")
    return text


def _opaque_id(value: object, label: str) -> str:
    text = _required_text(value, label, max_length=256)
    if not _OPAQUE_ID.fullmatch(text):
        raise ValueError(f"{label} contiene espacios o formato inválido")
    return text


def _exact_sha1(value: object, label: str) -> str:
    text = _required_text(value, label, max_length=40)
    if not _SHA1.fullmatch(text):
        raise ValueError(f"{label} debe ser SHA-1 hexadecimal minúsculo")
    return text


def _exact_sha256(value: object, label: str) -> str:
    text = _required_text(value, label, max_length=64)
    if not _SHA256.fullmatch(text):
        raise ValueError(f"{label} debe ser SHA-256 hexadecimal minúsculo")
    return text


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} debe ser entero positivo")
    return value


def _non_negative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} debe ser entero no negativo")
    return value


def _utc(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{label} debe tener zona horaria")
    return value.astimezone(UTC)


def _iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _provider(value: object, label: str) -> str:
    text = _required_text(value, label, max_length=64)
    if text not in _ALLOWED_PROVIDERS:
        raise ValueError(f"{label} no soportado")
    return text


def _signature_algorithm(value: object, label: str) -> str:
    text = _required_text(value, label, max_length=32)
    if text not in _ALLOWED_SIGNATURE_ALGORITHMS:
        raise ValueError(f"{label} no soportado")
    return text


def _decode_b64url(value: str, label: str) -> bytes:
    text = _opaque_id(value, label)
    if "=" in text or "+" in text or "/" in text:
        raise ValueError(f"{label} debe usar base64url sin padding")
    padding = "=" * ((4 - len(text) % 4) % 4)
    try:
        decoded = base64.b64decode(text + padding, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError(f"{label} debe ser base64url válido") from exc
    if not decoded:
        raise ValueError(f"{label} no puede decodificar vacío")
    canonical = base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=")
    if canonical != text:
        raise ValueError(f"{label} no está en forma base64url canónica")
    return decoded


def canonical_json_bytes(value: dict[str, object]) -> bytes:
    """Serialización canónica compartible con runtimes JavaScript."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class EdgeReceiptPayload:
    """Evidencia canónica emitida después de un request físico externo."""

    run_id: str
    request_id: str
    reservation_id: str
    authorization_id: str
    approved_commit_sha: str
    request_digest: str
    traversal_id: str
    traversal_role: Literal["primary", "reconciliation"]
    order_by: str
    partition_id: str
    from_index: int
    to_index: int
    http_method: str
    target_scheme: str
    target_host: str
    target_path: str
    canonical_request_sha256: str
    raw_response_sha256: str
    response_status: int
    response_body_bytes: int
    physical_started_at_utc: datetime
    response_completed_at_utc: datetime
    github_repository: str
    github_repository_id: str
    github_ref: str
    github_workflow_ref: str
    github_environment: str
    github_run_id: str
    github_run_attempt: int
    oidc_subject: str
    oidc_jti: str
    collector_provider: Literal["cloudflare_workers", "google_cloud_run"]
    collector_principal: str
    collector_execution: str
    collector_release_id: str
    collector_code_sha256: str
    signing_algorithm: Literal["Ed25519"]
    signing_key_id: str
    nonce: str
    schema_version: str = EDGE_PROVENANCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != EDGE_PROVENANCE_SCHEMA_VERSION:
            raise ValueError("schema_version de edge receipt no soportada")

        for label in (
            "run_id",
            "request_id",
            "reservation_id",
            "authorization_id",
            "traversal_id",
            "order_by",
            "partition_id",
            "github_repository",
            "github_repository_id",
            "github_ref",
            "github_workflow_ref",
            "github_environment",
            "oidc_subject",
            "oidc_jti",
            "collector_principal",
            "collector_execution",
            "collector_release_id",
            "signing_key_id",
            "nonce",
        ):
            normalized = _opaque_id(getattr(self, label), label)
            object.__setattr__(self, label, normalized)

        object.__setattr__(self, "approved_commit_sha", _exact_sha1(self.approved_commit_sha, "approved_commit_sha"))
        object.__setattr__(self, "request_digest", _exact_sha256(self.request_digest, "request_digest"))
        object.__setattr__(
            self,
            "canonical_request_sha256",
            _exact_sha256(self.canonical_request_sha256, "canonical_request_sha256"),
        )
        object.__setattr__(
            self,
            "raw_response_sha256",
            _exact_sha256(self.raw_response_sha256, "raw_response_sha256"),
        )
        object.__setattr__(
            self,
            "collector_code_sha256",
            _exact_sha256(self.collector_code_sha256, "collector_code_sha256"),
        )

        if self.traversal_role not in {"primary", "reconciliation"}:
            raise ValueError("traversal_role no permitido")

        _non_negative_int(self.from_index, "from_index")
        _non_negative_int(self.to_index, "to_index")
        if self.to_index < self.from_index:
            raise ValueError("to_index no puede preceder from_index")

        if self.http_method not in _ALLOWED_HTTP_METHODS:
            raise ValueError("http_method debe ser GET o POST")
        if self.target_scheme != "https":
            raise ValueError("target_scheme debe ser https")
        target_host = _required_text(self.target_host, "target_host", max_length=253).lower()
        if "/" in target_host or "://" in target_host or " " in target_host:
            raise ValueError("target_host inválido")
        object.__setattr__(self, "target_host", target_host)
        if not isinstance(self.target_path, str) or not self.target_path.startswith("/"):
            raise ValueError("target_path debe ser absoluto")
        if "#" in self.target_path:
            raise ValueError("target_path no puede contener fragmento")

        if (
            isinstance(self.response_status, bool)
            or not isinstance(self.response_status, int)
            or not 100 <= self.response_status <= 599
        ):
            raise ValueError("response_status inválido")
        _non_negative_int(self.response_body_bytes, "response_body_bytes")
        _positive_int(self.github_run_attempt, "github_run_attempt")

        started = _utc(self.physical_started_at_utc, "physical_started_at_utc")
        completed = _utc(self.response_completed_at_utc, "response_completed_at_utc")
        if completed < started:
            raise ValueError("response_completed_at_utc precede al inicio físico")
        object.__setattr__(self, "physical_started_at_utc", started)
        object.__setattr__(self, "response_completed_at_utc", completed)

        object.__setattr__(self, "collector_provider", _provider(self.collector_provider, "collector_provider"))
        object.__setattr__(
            self,
            "signing_algorithm",
            _signature_algorithm(self.signing_algorithm, "signing_algorithm"),
        )

    def canonical_dict(self) -> dict[str, object]:
        return {
            "approved_commit_sha": self.approved_commit_sha,
            "authorization_id": self.authorization_id,
            "canonical_request_sha256": self.canonical_request_sha256,
            "collector_code_sha256": self.collector_code_sha256,
            "collector_execution": self.collector_execution,
            "collector_principal": self.collector_principal,
            "collector_provider": self.collector_provider,
            "collector_release_id": self.collector_release_id,
            "from_index": self.from_index,
            "github_environment": self.github_environment,
            "github_ref": self.github_ref,
            "github_repository": self.github_repository,
            "github_repository_id": self.github_repository_id,
            "github_run_attempt": self.github_run_attempt,
            "github_run_id": self.github_run_id,
            "github_workflow_ref": self.github_workflow_ref,
            "http_method": self.http_method,
            "nonce": self.nonce,
            "oidc_jti": self.oidc_jti,
            "oidc_subject": self.oidc_subject,
            "order_by": self.order_by,
            "partition_id": self.partition_id,
            "physical_started_at_utc": _iso_z(self.physical_started_at_utc),
            "raw_response_sha256": self.raw_response_sha256,
            "request_digest": self.request_digest,
            "request_id": self.request_id,
            "reservation_id": self.reservation_id,
            "response_body_bytes": self.response_body_bytes,
            "response_completed_at_utc": _iso_z(self.response_completed_at_utc),
            "response_status": self.response_status,
            "run_id": self.run_id,
            "schema_version": self.schema_version,
            "signing_algorithm": self.signing_algorithm,
            "signing_key_id": self.signing_key_id,
            "target_host": self.target_host,
            "target_path": self.target_path,
            "target_scheme": self.target_scheme,
            "to_index": self.to_index,
            "traversal_id": self.traversal_id,
            "traversal_role": self.traversal_role,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.canonical_dict())


@dataclass(frozen=True, slots=True)
class SignedEdgeReceipt:
    payload: EdgeReceiptPayload
    signature_b64url: str

    def __post_init__(self) -> None:
        _decode_b64url(self.signature_b64url, "signature_b64url")

    @property
    def digest(self) -> str:
        envelope = (
            _RECEIPT_DOMAIN
            + self.payload.canonical_bytes()
            + b"\0"
            + _decode_b64url(self.signature_b64url, "signature_b64url")
        )
        return hashlib.sha256(envelope).hexdigest()


@dataclass(frozen=True, slots=True)
class EdgeAttestationPayload:
    """Resumen firmado por un verifier externo separado del caller."""

    run_id: str
    authorization_id: str
    approved_commit_sha: str
    github_repository: str
    github_repository_id: str
    github_ref: str
    github_workflow_ref: str
    github_environment: str
    collector_provider: Literal["cloudflare_workers", "google_cloud_run"]
    collector_principal: str
    collector_receipt_digests: tuple[str, ...]
    physical_evidence_ids: tuple[str, ...]
    primary_traversal_id: str
    reconciliation_traversal_id: str
    primary_order_by: str
    reconciliation_order_by: str
    verified_request_count: int
    verification_started_at_utc: datetime
    verification_completed_at_utc: datetime
    verdict: Literal["VERIFIED", "REJECTED"]
    reasons: tuple[str, ...]
    verifier_provider: Literal["cloudflare_workers", "google_cloud_run"]
    verifier_principal: str
    verifier_execution: str
    signing_algorithm: Literal["Ed25519"]
    verifier_signing_key_id: str
    schema_version: str = EDGE_PROVENANCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != EDGE_PROVENANCE_SCHEMA_VERSION:
            raise ValueError("schema_version de edge attestation no soportada")

        for label in (
            "run_id",
            "authorization_id",
            "github_repository",
            "github_repository_id",
            "github_ref",
            "github_workflow_ref",
            "github_environment",
            "collector_principal",
            "primary_traversal_id",
            "reconciliation_traversal_id",
            "primary_order_by",
            "reconciliation_order_by",
            "verifier_principal",
            "verifier_execution",
            "verifier_signing_key_id",
        ):
            normalized = _opaque_id(getattr(self, label), label)
            object.__setattr__(self, label, normalized)

        object.__setattr__(self, "approved_commit_sha", _exact_sha1(self.approved_commit_sha, "approved_commit_sha"))
        object.__setattr__(self, "collector_provider", _provider(self.collector_provider, "collector_provider"))
        object.__setattr__(self, "verifier_provider", _provider(self.verifier_provider, "verifier_provider"))
        object.__setattr__(
            self,
            "signing_algorithm",
            _signature_algorithm(self.signing_algorithm, "signing_algorithm"),
        )

        receipts = tuple(self.collector_receipt_digests)
        evidence = tuple(self.physical_evidence_ids)
        if not receipts:
            raise ValueError("collector_receipt_digests no puede estar vacío")
        if any(not _SHA256.fullmatch(item) for item in receipts):
            raise ValueError("collector_receipt_digests contiene digest inválido")
        if len(set(receipts)) != len(receipts):
            raise ValueError("collector_receipt_digests no puede repetir receipts")
        if len(evidence) != len(receipts):
            raise ValueError("debe existir una evidencia física por receipt")
        normalized_evidence = tuple(_opaque_id(item, "physical_evidence_id") for item in evidence)
        if len(set(normalized_evidence)) != len(normalized_evidence):
            raise ValueError("physical_evidence_ids no puede repetir evidencia")

        if self.primary_traversal_id == self.reconciliation_traversal_id:
            raise ValueError("primary y reconciliation deben ser traversals distintas")
        if self.primary_order_by == self.reconciliation_order_by:
            raise ValueError("primary y reconciliation deben usar order_by distintos")

        _positive_int(self.verified_request_count, "verified_request_count")
        if self.verified_request_count != len(receipts):
            raise ValueError("verified_request_count no coincide con receipts")

        started = _utc(self.verification_started_at_utc, "verification_started_at_utc")
        completed = _utc(self.verification_completed_at_utc, "verification_completed_at_utc")
        if completed < started:
            raise ValueError("verification_completed_at_utc precede al inicio")
        object.__setattr__(self, "verification_started_at_utc", started)
        object.__setattr__(self, "verification_completed_at_utc", completed)

        if self.verdict not in {"VERIFIED", "REJECTED"}:
            raise ValueError("verdict no permitido")
        reasons = tuple(_opaque_id(item, "reason") for item in self.reasons)
        if len(set(reasons)) != len(reasons):
            raise ValueError("reasons no puede contener duplicados")
        if self.verdict == "VERIFIED" and reasons:
            raise ValueError("VERIFIED no puede contener reasons")
        if self.verdict == "REJECTED" and not reasons:
            raise ValueError("REJECTED requiere al menos un reason")

        object.__setattr__(self, "collector_receipt_digests", receipts)
        object.__setattr__(self, "physical_evidence_ids", normalized_evidence)
        object.__setattr__(self, "reasons", reasons)

    def canonical_dict(self) -> dict[str, object]:
        return {
            "approved_commit_sha": self.approved_commit_sha,
            "authorization_id": self.authorization_id,
            "collector_principal": self.collector_principal,
            "collector_provider": self.collector_provider,
            "collector_receipt_digests": list(self.collector_receipt_digests),
            "github_environment": self.github_environment,
            "github_ref": self.github_ref,
            "github_repository": self.github_repository,
            "github_repository_id": self.github_repository_id,
            "github_workflow_ref": self.github_workflow_ref,
            "physical_evidence_ids": list(self.physical_evidence_ids),
            "primary_order_by": self.primary_order_by,
            "primary_traversal_id": self.primary_traversal_id,
            "reasons": list(self.reasons),
            "reconciliation_order_by": self.reconciliation_order_by,
            "reconciliation_traversal_id": self.reconciliation_traversal_id,
            "run_id": self.run_id,
            "schema_version": self.schema_version,
            "signing_algorithm": self.signing_algorithm,
            "verdict": self.verdict,
            "verification_completed_at_utc": _iso_z(self.verification_completed_at_utc),
            "verification_started_at_utc": _iso_z(self.verification_started_at_utc),
            "verified_request_count": self.verified_request_count,
            "verifier_execution": self.verifier_execution,
            "verifier_principal": self.verifier_principal,
            "verifier_provider": self.verifier_provider,
            "verifier_signing_key_id": self.verifier_signing_key_id,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.canonical_dict())


@dataclass(frozen=True, slots=True)
class SignedEdgeAttestation:
    payload: EdgeAttestationPayload
    signature_b64url: str

    def __post_init__(self) -> None:
        _decode_b64url(self.signature_b64url, "signature_b64url")

    @property
    def digest(self) -> str:
        envelope = (
            _ATTESTATION_DOMAIN
            + self.payload.canonical_bytes()
            + b"\0"
            + _decode_b64url(self.signature_b64url, "signature_b64url")
        )
        return hashlib.sha256(envelope).hexdigest()


@dataclass(frozen=True, slots=True)
class EdgeStructuralValidation:
    """Coherencia interna únicamente; nunca equivale a autoridad productiva."""

    consistent: bool
    reasons: tuple[str, ...]
    production_authority: bool = False


def validate_edge_provenance_structure(
    receipts: Iterable[SignedEdgeReceipt],
    attestation: SignedEdgeAttestation,
) -> EdgeStructuralValidation:
    """Reconcilia la forma v2 sin verificar firma/issuer/evidencia externa."""

    values = tuple(receipts)
    payload = attestation.payload
    reasons: list[str] = []

    if payload.verdict != "VERIFIED":
        reasons.append("attestation_not_verified")
    if len(values) != payload.verified_request_count:
        reasons.append("receipt_count_mismatch")

    digests = tuple(item.digest for item in values)
    if digests != payload.collector_receipt_digests:
        reasons.append("receipt_digest_sequence_mismatch")

    request_ids: set[str] = set()
    reservation_ids: set[str] = set()
    nonces: set[str] = set()
    primary = 0
    reconciliation = 0
    latest_completed: datetime | None = None

    for signed in values:
        item = signed.payload
        if item.run_id != payload.run_id:
            reasons.append("receipt_run_id_mismatch")
        if item.authorization_id != payload.authorization_id:
            reasons.append("receipt_authorization_id_mismatch")
        if item.approved_commit_sha != payload.approved_commit_sha:
            reasons.append("receipt_commit_mismatch")
        if item.github_repository != payload.github_repository:
            reasons.append("receipt_repository_mismatch")
        if item.github_repository_id != payload.github_repository_id:
            reasons.append("receipt_repository_id_mismatch")
        if item.github_ref != payload.github_ref:
            reasons.append("receipt_ref_mismatch")
        if item.github_workflow_ref != payload.github_workflow_ref:
            reasons.append("receipt_workflow_ref_mismatch")
        if item.github_environment != payload.github_environment:
            reasons.append("receipt_environment_mismatch")
        if item.collector_provider != payload.collector_provider:
            reasons.append("receipt_collector_provider_mismatch")
        if item.collector_principal != payload.collector_principal:
            reasons.append("receipt_collector_principal_mismatch")

        if item.request_id in request_ids:
            reasons.append("duplicate_request_id")
        request_ids.add(item.request_id)
        if item.reservation_id in reservation_ids:
            reasons.append("duplicate_reservation_id")
        reservation_ids.add(item.reservation_id)
        if item.nonce in nonces:
            reasons.append("duplicate_nonce")
        nonces.add(item.nonce)

        if item.traversal_role == "primary":
            primary += 1
            if item.traversal_id != payload.primary_traversal_id:
                reasons.append("primary_traversal_id_mismatch")
            if item.order_by != payload.primary_order_by:
                reasons.append("primary_order_by_mismatch")
        elif item.traversal_role == "reconciliation":
            reconciliation += 1
            if item.traversal_id != payload.reconciliation_traversal_id:
                reasons.append("reconciliation_traversal_id_mismatch")
            if item.order_by != payload.reconciliation_order_by:
                reasons.append("reconciliation_order_by_mismatch")

        completed = item.response_completed_at_utc
        if latest_completed is None or completed > latest_completed:
            latest_completed = completed

    if primary == 0:
        reasons.append("primary_receipts_missing")
    if reconciliation == 0:
        reasons.append("reconciliation_receipts_missing")
    if latest_completed is not None and payload.verification_started_at_utc < latest_completed:
        reasons.append("verification_started_before_receipts_completed")

    unique_reasons = tuple(dict.fromkeys(reasons))
    return EdgeStructuralValidation(
        consistent=not unique_reasons,
        reasons=unique_reasons,
        production_authority=False,
    )
