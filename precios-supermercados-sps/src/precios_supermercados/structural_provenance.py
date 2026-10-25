"""Receipt criptográfico dedicado al descubrimiento estructural de La Colonia.

El schema v1 preserva el contrato estructural histórico. El schema v2 añade una
frontera explícita de ubicación: el receipt firmado liga la respuesta física al
binding confirmado de San Pedro Sula, al fingerprint del ``regionId`` y al
fingerprint del request wire que el collector ejecutó. El valor raw de región
nunca forma parte del receipt.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, NoReturn

STRUCTURAL_PROVENANCE_SCHEMA_VERSION = "1"
STRUCTURAL_PROVENANCE_CONTEXT_SCHEMA_VERSION = "2"
STRUCTURAL_SIGNATURE_DOMAIN = b"precios-sps/structural-receipt-signature/v1\0"
STRUCTURAL_DIGEST_DOMAIN = b"precios-sps/structural-receipt/v1\0"
STRUCTURAL_CONTEXT_SIGNATURE_DOMAIN = b"precios-sps/structural-receipt-signature/v2\0"
STRUCTURAL_CONTEXT_DIGEST_DOMAIN = b"precios-sps/structural-receipt/v2\0"
_ALLOWED_KINDS = {"root_total", "category_tree"}
_CONTEXT_PLACEMENTS = {"query", "header"}
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_B64URL = re.compile(r"[A-Za-z0-9_-]+\Z")
_OPAQUE = re.compile(r"[^\s]{1,512}\Z")
_BINDING_SOURCE = re.compile(r"request:regionid:sha256:(?P<digest>[0-9a-f]{64})\Z")
_BINDING_EVIDENCE = re.compile(r"location_binding_radiography:sha256:[0-9a-f]{64}\Z")


class StructuralProvenanceError(ValueError):
    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


def _fail(code: str, message: str | None = None) -> NoReturn:
    raise StructuralProvenanceError(code, message)


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


def _safe_int(value: object, code: str, *, minimum: int = 0, maximum: int = 2**53 - 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        _fail(code)
    return value


def _utc_ms(value: object, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    normalized = value.astimezone(UTC)
    if normalized.microsecond % 1000:
        _fail(code)
    return normalized


def _iso_ms(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _canonical_b64url(value: object, code: str, *, maximum: int = 4096) -> tuple[str, bytes]:
    text = _text(value, code, maximum=maximum)
    if "=" in text or "+" in text or "/" in text or not _B64URL.fullmatch(text):
        _fail(code)
    padding = "=" * ((4 - len(text) % 4) % 4)
    try:
        decoded = base64.b64decode(text + padding, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise StructuralProvenanceError(code) from exc
    if not decoded:
        _fail(code)
    canonical = base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=")
    if canonical != text:
        _fail(code)
    return text, decoded


def structural_canonical_json_bytes(value: dict[str, object]) -> bytes:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise StructuralProvenanceError("structural_canonical_json_invalid") from exc
    return rendered.encode("utf-8")


@dataclass(frozen=True, slots=True)
class StructuralReceiptPayload:
    run_id: str
    request_kind: Literal["root_total", "category_tree"]
    request_id: str
    reservation_id: str
    authorization_id: str
    approved_commit_sha: str
    request_digest: str
    canonical_request_sha256: str
    graphql_query_sha256: str
    http_method: Literal["GET"]
    target_scheme: Literal["https"]
    target_host: str
    target_path: str
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
    collector_provider: Literal["cloudflare_workers"]
    collector_principal: str
    collector_execution: str
    collector_release_id: str
    collector_code_sha256: str
    signing_algorithm: Literal["Ed25519"]
    signing_key_id: str
    nonce: str
    schema_version: str = STRUCTURAL_PROVENANCE_SCHEMA_VERSION
    location_id: str | None = None
    binding_source_key: str | None = None
    binding_evidence: str | None = None
    context_fingerprint: str | None = None
    context_placement: str | None = None
    context_wire_key: str | None = None
    context_value_path: tuple[str, ...] | None = None
    wire_request_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version not in {
            STRUCTURAL_PROVENANCE_SCHEMA_VERSION,
            STRUCTURAL_PROVENANCE_CONTEXT_SCHEMA_VERSION,
        }:
            _fail("structural_receipt_schema_version_invalid")
        if self.request_kind not in _ALLOWED_KINDS:
            _fail("structural_receipt_request_kind_invalid")
        for name in (
            "run_id",
            "request_id",
            "reservation_id",
            "authorization_id",
            "github_repository",
            "github_repository_id",
            "github_ref",
            "github_workflow_ref",
            "github_environment",
            "github_run_id",
            "oidc_subject",
            "oidc_jti",
            "collector_principal",
            "collector_execution",
            "collector_release_id",
            "signing_key_id",
            "nonce",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), f"structural_receipt_{name}_invalid"))
        object.__setattr__(
            self,
            "approved_commit_sha",
            _sha1(self.approved_commit_sha, "structural_receipt_approved_commit_sha_invalid"),
        )
        for name in (
            "request_digest",
            "canonical_request_sha256",
            "graphql_query_sha256",
            "raw_response_sha256",
            "collector_code_sha256",
        ):
            object.__setattr__(self, name, _sha256(getattr(self, name), f"structural_receipt_{name}_invalid"))
        if self.request_digest != self.canonical_request_sha256:
            _fail("structural_receipt_request_digest_mismatch")
        if self.http_method != "GET":
            _fail("structural_receipt_http_method_invalid")
        if self.target_scheme != "https":
            _fail("structural_receipt_target_scheme_invalid")
        if self.target_host != "www.lacolonia.com":
            _fail("structural_receipt_target_host_invalid")
        if self.target_path != "/_v/segment/graphql/v1":
            _fail("structural_receipt_target_path_invalid")
        if self.response_status != 200:
            _fail("structural_receipt_response_status_invalid")
        _safe_int(self.response_body_bytes, "structural_receipt_response_body_bytes_invalid", minimum=1)
        _safe_int(self.github_run_attempt, "structural_receipt_github_run_attempt_invalid", minimum=1, maximum=100)
        if self.collector_provider != "cloudflare_workers":
            _fail("structural_receipt_collector_provider_invalid")
        if self.signing_algorithm != "Ed25519":
            _fail("structural_receipt_signing_algorithm_invalid")
        started = _utc_ms(self.physical_started_at_utc, "structural_receipt_physical_started_at_invalid")
        completed = _utc_ms(self.response_completed_at_utc, "structural_receipt_response_completed_at_invalid")
        if completed < started:
            _fail("structural_receipt_time_order_invalid")
        object.__setattr__(self, "physical_started_at_utc", started)
        object.__setattr__(self, "response_completed_at_utc", completed)
        self._validate_location_context()

    def _validate_location_context(self) -> None:
        fields = (
            self.location_id,
            self.binding_source_key,
            self.binding_evidence,
            self.context_fingerprint,
            self.context_placement,
            self.context_wire_key,
            self.context_value_path,
            self.wire_request_fingerprint,
        )
        if self.schema_version == STRUCTURAL_PROVENANCE_SCHEMA_VERSION:
            if any(value is not None for value in fields):
                _fail("structural_receipt_v1_location_context_forbidden")
            return
        if any(value is None for value in fields):
            _fail("structural_receipt_location_context_incomplete")
        if self.location_id != "la_colonia_sps":
            _fail("structural_receipt_location_id_invalid")
        source = _text(self.binding_source_key, "structural_receipt_binding_source_key_invalid")
        match = _BINDING_SOURCE.fullmatch(source)
        if match is None:
            _fail("structural_receipt_binding_source_key_invalid")
        evidence = _text(self.binding_evidence, "structural_receipt_binding_evidence_invalid")
        if _BINDING_EVIDENCE.fullmatch(evidence) is None:
            _fail("structural_receipt_binding_evidence_invalid")
        fingerprint = _sha256(
            self.context_fingerprint,
            "structural_receipt_context_fingerprint_invalid",
        )
        if match.group("digest") != fingerprint:
            _fail("structural_receipt_binding_context_fingerprint_mismatch")
        if self.context_placement not in _CONTEXT_PLACEMENTS:
            _fail("structural_receipt_context_placement_invalid")
        _text(self.context_wire_key, "structural_receipt_context_wire_key_invalid", maximum=160)
        if self.context_value_path != ():
            _fail("structural_receipt_context_value_path_invalid")
        _sha256(
            self.wire_request_fingerprint,
            "structural_receipt_wire_request_fingerprint_invalid",
        )

    @property
    def location_context_bound(self) -> bool:
        return self.schema_version == STRUCTURAL_PROVENANCE_CONTEXT_SCHEMA_VERSION

    def canonical_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "approved_commit_sha": self.approved_commit_sha,
            "authorization_id": self.authorization_id,
            "canonical_request_sha256": self.canonical_request_sha256,
            "collector_code_sha256": self.collector_code_sha256,
            "collector_execution": self.collector_execution,
            "collector_principal": self.collector_principal,
            "collector_provider": self.collector_provider,
            "collector_release_id": self.collector_release_id,
            "github_environment": self.github_environment,
            "github_ref": self.github_ref,
            "github_repository": self.github_repository,
            "github_repository_id": self.github_repository_id,
            "github_run_attempt": self.github_run_attempt,
            "github_run_id": self.github_run_id,
            "github_workflow_ref": self.github_workflow_ref,
            "graphql_query_sha256": self.graphql_query_sha256,
            "http_method": self.http_method,
            "nonce": self.nonce,
            "oidc_jti": self.oidc_jti,
            "oidc_subject": self.oidc_subject,
            "physical_started_at_utc": _iso_ms(self.physical_started_at_utc),
            "raw_response_sha256": self.raw_response_sha256,
            "request_digest": self.request_digest,
            "request_id": self.request_id,
            "request_kind": self.request_kind,
            "reservation_id": self.reservation_id,
            "response_body_bytes": self.response_body_bytes,
            "response_completed_at_utc": _iso_ms(self.response_completed_at_utc),
            "response_status": self.response_status,
            "run_id": self.run_id,
            "schema_version": self.schema_version,
            "signing_algorithm": self.signing_algorithm,
            "signing_key_id": self.signing_key_id,
            "target_host": self.target_host,
            "target_path": self.target_path,
            "target_scheme": self.target_scheme,
        }
        if self.location_context_bound:
            result.update(
                {
                    "binding_evidence": self.binding_evidence,
                    "binding_source_key": self.binding_source_key,
                    "context_fingerprint": self.context_fingerprint,
                    "context_placement": self.context_placement,
                    "context_value_path": list(self.context_value_path or ()),
                    "context_wire_key": self.context_wire_key,
                    "location_id": self.location_id,
                    "wire_request_fingerprint": self.wire_request_fingerprint,
                }
            )
        return result

    def canonical_bytes(self) -> bytes:
        return structural_canonical_json_bytes(self.canonical_dict())


@dataclass(frozen=True, slots=True)
class SignedStructuralReceipt:
    payload: StructuralReceiptPayload
    signature_b64url: str

    def __post_init__(self) -> None:
        if not isinstance(self.payload, StructuralReceiptPayload):
            _fail("structural_receipt_payload_invalid")
        text, decoded = _canonical_b64url(
            self.signature_b64url,
            "structural_receipt_signature_invalid",
            maximum=1024,
        )
        if len(decoded) != 64:
            _fail("structural_receipt_signature_length_invalid")
        object.__setattr__(self, "signature_b64url", text)

    def signing_bytes(self) -> bytes:
        domain = (
            STRUCTURAL_CONTEXT_SIGNATURE_DOMAIN
            if self.payload.location_context_bound
            else STRUCTURAL_SIGNATURE_DOMAIN
        )
        return domain + self.payload.canonical_bytes()

    @property
    def digest(self) -> str:
        _, signature = _canonical_b64url(
            self.signature_b64url,
            "structural_receipt_signature_invalid",
            maximum=1024,
        )
        domain = (
            STRUCTURAL_CONTEXT_DIGEST_DOMAIN
            if self.payload.location_context_bound
            else STRUCTURAL_DIGEST_DOMAIN
        )
        material = domain + self.payload.canonical_bytes() + b"\0" + signature
        return hashlib.sha256(material).hexdigest()
