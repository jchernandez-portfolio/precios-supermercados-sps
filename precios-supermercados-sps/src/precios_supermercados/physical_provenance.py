"""Contratos offline para futura provenance física productiva.

Este módulo NO verifica firmas KMS, NO consulta Cloud Logging y NO concede
`catalog_accepted`. Sólo define serialización canónica y reconciliación
estructural de la evidencia que un verifier productivo deberá validar contra
fuentes externas independientes.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

PROVENANCE_SCHEMA_VERSION = "1"
_RECEIPT_DOMAIN = b"precios-sps/physical-receipt/v1\0"
_ATTESTATION_DOMAIN = b"precios-sps/provenance-attestation/v1\0"
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_IMAGE_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SERVICE_ACCOUNT = re.compile(r"[^@\s]+@[^@\s]+\.iam\.gserviceaccount\.com\Z")


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} debe ser texto no vacío")
    return value.strip()


def _exact_sha256(value: object, label: str) -> str:
    text = _required_text(value, label)
    if not _SHA256.fullmatch(text):
        raise ValueError(f"{label} debe ser SHA-256 hexadecimal")
    return text


def _utc(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{label} debe tener zona horaria")
    normalized = value.astimezone(UTC)
    if normalized.utcoffset() != UTC.utcoffset(normalized):
        raise ValueError(f"{label} debe normalizar a UTC")
    return normalized


def _iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _signature_bytes(value: str) -> bytes:
    text = _required_text(value, "signature_b64")
    try:
        decoded = base64.b64decode(text, validate=True)
    except (ValueError, binascii.Error) as exc:  # type: ignore[name-defined]
        raise ValueError("signature_b64 debe ser base64 válido") from exc
    if not decoded:
        raise ValueError("signature_b64 no puede decodificar vacío")
    return decoded


# `binascii` se importa después para mantener agrupadas las dependencias estándar
# usadas únicamente por el validador estricto de base64.
import binascii  # noqa: E402


@dataclass(frozen=True, slots=True)
class PhysicalReceiptPayload:
    """Payload canónico que un collector productivo deberá firmar con KMS."""

    run_id: str
    request_id: str
    reservation_id: str
    authorization_id: str
    approved_commit_sha: str
    immutable_image_digest: str
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
    collector_service_account: str
    collector_execution: str
    kms_key_version: str
    nonce: str
    schema_version: str = PROVENANCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PROVENANCE_SCHEMA_VERSION:
            raise ValueError("schema_version de receipt no soportada")
        for label in (
            "run_id",
            "request_id",
            "reservation_id",
            "authorization_id",
            "traversal_id",
            "order_by",
            "partition_id",
            "target_host",
            "collector_execution",
            "kms_key_version",
            "nonce",
        ):
            _required_text(getattr(self, label), label)
        if not _SHA1.fullmatch(self.approved_commit_sha):
            raise ValueError("approved_commit_sha debe ser SHA-1 hexadecimal")
        if not _IMAGE_DIGEST.fullmatch(self.immutable_image_digest):
            raise ValueError("immutable_image_digest debe usar sha256:<hex>")
        _exact_sha256(self.request_digest, "request_digest")
        _exact_sha256(self.canonical_request_sha256, "canonical_request_sha256")
        _exact_sha256(self.raw_response_sha256, "raw_response_sha256")
        if self.traversal_role not in {"primary", "reconciliation"}:
            raise ValueError("traversal_role no permitido")
        if isinstance(self.from_index, bool) or not isinstance(self.from_index, int):
            raise ValueError("from_index debe ser entero")
        if isinstance(self.to_index, bool) or not isinstance(self.to_index, int):
            raise ValueError("to_index debe ser entero")
        if self.from_index < 0 or self.to_index < self.from_index:
            raise ValueError("rango de receipt inválido")
        if self.http_method != "POST":
            raise ValueError("el collector productivo sólo admite POST")
        if self.target_scheme != "https":
            raise ValueError("target_scheme debe ser https")
        if not self.target_path.startswith("/"):
            raise ValueError("target_path debe ser absoluto")
        if (
            isinstance(self.response_status, bool)
            or not isinstance(self.response_status, int)
            or not 100 <= self.response_status <= 599
        ):
            raise ValueError("response_status inválido")
        if (
            isinstance(self.response_body_bytes, bool)
            or not isinstance(self.response_body_bytes, int)
            or self.response_body_bytes < 0
        ):
            raise ValueError("response_body_bytes inválido")
        started = _utc(self.physical_started_at_utc, "physical_started_at_utc")
        completed = _utc(self.response_completed_at_utc, "response_completed_at_utc")
        if completed < started:
            raise ValueError("response_completed_at_utc precede al inicio físico")
        object.__setattr__(self, "physical_started_at_utc", started)
        object.__setattr__(self, "response_completed_at_utc", completed)
        if not _SERVICE_ACCOUNT.fullmatch(self.collector_service_account):
            raise ValueError("collector_service_account inválida")

    def canonical_dict(self) -> dict[str, object]:
        return {
            "approved_commit_sha": self.approved_commit_sha,
            "authorization_id": self.authorization_id,
            "canonical_request_sha256": self.canonical_request_sha256,
            "collector_execution": self.collector_execution,
            "collector_service_account": self.collector_service_account,
            "from_index": self.from_index,
            "http_method": self.http_method,
            "immutable_image_digest": self.immutable_image_digest,
            "kms_key_version": self.kms_key_version,
            "nonce": self.nonce,
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
            "target_host": self.target_host,
            "target_path": self.target_path,
            "target_scheme": self.target_scheme,
            "to_index": self.to_index,
            "traversal_id": self.traversal_id,
            "traversal_role": self.traversal_role,
        }

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.canonical_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class SignedPhysicalReceipt:
    payload: PhysicalReceiptPayload
    signature_b64: str

    def __post_init__(self) -> None:
        _signature_bytes(self.signature_b64)

    @property
    def digest(self) -> str:
        envelope = (
            _RECEIPT_DOMAIN
            + self.payload.canonical_bytes()
            + b"\0"
            + _signature_bytes(self.signature_b64)
        )
        return hashlib.sha256(envelope).hexdigest()


@dataclass(frozen=True, slots=True)
class ProvenanceAttestationPayload:
    """Decisión que un verifier productivo separado deberá firmar con KMS."""

    run_id: str
    authorization_id: str
    approved_commit_sha: str
    immutable_image_digest: str
    collector_receipt_digests: tuple[str, ...]
    swp_log_insert_ids: tuple[str, ...]
    primary_traversal_id: str
    reconciliation_traversal_id: str
    primary_order_by: str
    reconciliation_order_by: str
    verified_request_count: int
    verification_started_at_utc: datetime
    verification_completed_at_utc: datetime
    verdict: Literal["VERIFIED", "REJECTED"]
    reasons: tuple[str, ...]
    verifier_kms_key_version: str
    schema_version: str = PROVENANCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PROVENANCE_SCHEMA_VERSION:
            raise ValueError("schema_version de attestation no soportada")
        for label in (
            "run_id",
            "authorization_id",
            "primary_traversal_id",
            "reconciliation_traversal_id",
            "primary_order_by",
            "reconciliation_order_by",
            "verifier_kms_key_version",
        ):
            _required_text(getattr(self, label), label)
        if not _SHA1.fullmatch(self.approved_commit_sha):
            raise ValueError("approved_commit_sha debe ser SHA-1 hexadecimal")
        if not _IMAGE_DIGEST.fullmatch(self.immutable_image_digest):
            raise ValueError("immutable_image_digest debe usar sha256:<hex>")
        receipts = tuple(self.collector_receipt_digests)
        logs = tuple(self.swp_log_insert_ids)
        if not receipts:
            raise ValueError("collector_receipt_digests no puede estar vacío")
        if any(not _SHA256.fullmatch(item) for item in receipts):
            raise ValueError("collector_receipt_digests contiene un digest inválido")
        if len(set(receipts)) != len(receipts):
            raise ValueError("collector_receipt_digests no puede repetir receipts")
        if len(logs) != len(receipts):
            raise ValueError("debe existir un log SWP por receipt")
        if any(not isinstance(item, str) or not item.strip() for item in logs):
            raise ValueError("swp_log_insert_ids contiene un valor vacío")
        if len(set(logs)) != len(logs):
            raise ValueError("swp_log_insert_ids no puede repetir transacciones")
        if self.primary_traversal_id == self.reconciliation_traversal_id:
            raise ValueError("primary y reconciliation deben ser traversals distintas")
        if self.primary_order_by == self.reconciliation_order_by:
            raise ValueError("primary y reconciliation deben usar order_by distintos")
        if (
            isinstance(self.verified_request_count, bool)
            or not isinstance(self.verified_request_count, int)
            or self.verified_request_count != len(receipts)
        ):
            raise ValueError("verified_request_count no coincide con los receipts")
        started = _utc(self.verification_started_at_utc, "verification_started_at_utc")
        completed = _utc(self.verification_completed_at_utc, "verification_completed_at_utc")
        if completed < started:
            raise ValueError("verification_completed_at_utc precede al inicio")
        object.__setattr__(self, "verification_started_at_utc", started)
        object.__setattr__(self, "verification_completed_at_utc", completed)
        if self.verdict not in {"VERIFIED", "REJECTED"}:
            raise ValueError("verdict no permitido")
        reasons = tuple(_required_text(item, "reason") for item in self.reasons)
        if len(set(reasons)) != len(reasons):
            raise ValueError("reasons no puede contener duplicados")
        if self.verdict == "VERIFIED" and reasons:
            raise ValueError("VERIFIED no puede contener reasons")
        if self.verdict == "REJECTED" and not reasons:
            raise ValueError("REJECTED requiere al menos un reason")
        object.__setattr__(self, "collector_receipt_digests", receipts)
        object.__setattr__(self, "swp_log_insert_ids", logs)
        object.__setattr__(self, "reasons", reasons)

    def canonical_dict(self) -> dict[str, object]:
        return {
            "approved_commit_sha": self.approved_commit_sha,
            "authorization_id": self.authorization_id,
            "collector_receipt_digests": list(self.collector_receipt_digests),
            "immutable_image_digest": self.immutable_image_digest,
            "primary_order_by": self.primary_order_by,
            "primary_traversal_id": self.primary_traversal_id,
            "reasons": list(self.reasons),
            "reconciliation_order_by": self.reconciliation_order_by,
            "reconciliation_traversal_id": self.reconciliation_traversal_id,
            "run_id": self.run_id,
            "schema_version": self.schema_version,
            "swp_log_insert_ids": list(self.swp_log_insert_ids),
            "verdict": self.verdict,
            "verification_completed_at_utc": _iso_z(self.verification_completed_at_utc),
            "verification_started_at_utc": _iso_z(self.verification_started_at_utc),
            "verified_request_count": self.verified_request_count,
            "verifier_kms_key_version": self.verifier_kms_key_version,
        }

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.canonical_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class SignedProvenanceAttestation:
    payload: ProvenanceAttestationPayload
    signature_b64: str

    def __post_init__(self) -> None:
        _signature_bytes(self.signature_b64)

    @property
    def digest(self) -> str:
        envelope = (
            _ATTESTATION_DOMAIN
            + self.payload.canonical_bytes()
            + b"\0"
            + _signature_bytes(self.signature_b64)
        )
        return hashlib.sha256(envelope).hexdigest()


@dataclass(frozen=True, slots=True)
class StructuralProvenanceValidation:
    """Sólo coherencia interna; nunca equivale a autoridad productiva."""

    consistent: bool
    reasons: tuple[str, ...]
    production_authority: bool = False


def validate_provenance_structure(
    receipts: Iterable[SignedPhysicalReceipt],
    attestation: SignedProvenanceAttestation,
) -> StructuralProvenanceValidation:
    """Reconcilia receipts/attestation sin fingir verificación KMS o SWP.

    Incluso cuando `consistent=True`, `production_authority` permanece `False`.
    Sólo un verifier externo con KMS + logs físicos puede conceder esa autoridad.
    """

    values = tuple(receipts)
    reasons: list[str] = []
    payload = attestation.payload

    if payload.verdict != "VERIFIED":
        reasons.append("attestation_not_verified")
    if len(values) != payload.verified_request_count:
        reasons.append("receipt_count_mismatch")

    digests = tuple(item.digest for item in values)
    if tuple(payload.collector_receipt_digests) != digests:
        reasons.append("receipt_digest_sequence_mismatch")

    request_ids: set[str] = set()
    nonces: set[str] = set()
    primary = 0
    reconciliation = 0
    for receipt in values:
        item = receipt.payload
        if item.run_id != payload.run_id:
            reasons.append("receipt_run_id_mismatch")
        if item.authorization_id != payload.authorization_id:
            reasons.append("receipt_authorization_id_mismatch")
        if item.approved_commit_sha != payload.approved_commit_sha:
            reasons.append("receipt_commit_mismatch")
        if item.immutable_image_digest != payload.immutable_image_digest:
            reasons.append("receipt_image_digest_mismatch")
        if item.request_id in request_ids:
            reasons.append("duplicate_request_id")
        request_ids.add(item.request_id)
        if item.nonce in nonces:
            reasons.append("duplicate_receipt_nonce")
        nonces.add(item.nonce)
        if item.traversal_role == "primary":
            primary += 1
            if item.traversal_id != payload.primary_traversal_id:
                reasons.append("primary_traversal_id_mismatch")
            if item.order_by != payload.primary_order_by:
                reasons.append("primary_order_by_mismatch")
        else:
            reconciliation += 1
            if item.traversal_id != payload.reconciliation_traversal_id:
                reasons.append("reconciliation_traversal_id_mismatch")
            if item.order_by != payload.reconciliation_order_by:
                reasons.append("reconciliation_order_by_mismatch")

    if primary == 0:
        reasons.append("primary_receipts_missing")
    if reconciliation == 0:
        reasons.append("reconciliation_receipts_missing")

    unique = tuple(dict.fromkeys(reasons))
    return StructuralProvenanceValidation(
        consistent=not unique,
        reasons=unique,
        production_authority=False,
    )
