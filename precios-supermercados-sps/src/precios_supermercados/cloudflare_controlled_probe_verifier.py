"""Verificación independiente de la sonda Cloudflare contra origen controlado.

Esta frontera corre fuera del Worker que produce la evidencia. Verifica la firma
Ed25519 con una clave pública confiable suministrada por el entorno de GitHub,
recalcula hashes y liga la evidencia al run OIDC esperado.

La sonda sólo demuestra propiedades de infraestructura Cloudflare contra un
origen controlado. Nunca concede ``catalog_accepted`` ni ``production_authority``
y no contiene ninguna ruta para contactar La Colonia.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, NoReturn

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.serialization import load_der_public_key
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from precios_supermercados.edge_provenance import canonical_json_bytes

CONTROLLED_PROBE_PURPOSE = "precios-sps-controlled-origin-probe-v1"
CONTROLLED_PROBE_SCHEMA_VERSION = "probe-1"
CONTROLLED_PROBE_SIGNATURE_DOMAIN = (
    b"precios-sps/cloudflare-controlled-origin-probe-receipt/v1\0"
)
CONTROLLED_PROBE_SIGNING_KEY_ID = "cloudflare-probe-ed25519-v1"
CONTROLLED_PROBE_ORIGIN_PATH = "/v1/probe-origin"
CONTROLLED_PROBE_REPOSITORY = "jchernandez-portfolio/precios-supermercados-sps"
CONTROLLED_PROBE_REPOSITORY_ID = "1282475205"
CONTROLLED_PROBE_REF = "refs/heads/main"
CONTROLLED_PROBE_ENVIRONMENT = "cloudflare-probe"
CONTROLLED_PROBE_WORKFLOW_REF = (
    "jchernandez-portfolio/precios-supermercados-sps/.github/workflows/"
    "precios-supermercados-sps-cloudflare-probe.yml@refs/heads/main"
)
CONTROLLED_PROBE_SUBJECT = (
    "repo:jchernandez-portfolio/precios-supermercados-sps:environment:cloudflare-probe"
)

_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_B64URL = re.compile(r"[A-Za-z0-9_-]+\Z")
_PROBE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_CHALLENGE = re.compile(r"[A-Za-z0-9._:-]{8,256}\Z")
_WORKERS_DEV_HOST = re.compile(
    r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.workers\.dev\Z"
)

_TOP_LEVEL_KEYS = frozenset(
    {
        "ok",
        "decision",
        "replayed",
        "purpose",
        "rawBodyB64Url",
        "receiptPayload",
        "signatureB64Url",
        "signingKeyId",
        "evidenceId",
    }
)
_RECEIPT_KEYS = frozenset(
    {
        "approved_commit_sha",
        "canonical_request_sha256",
        "collector_provider",
        "collector_principal",
        "collector_release_id",
        "durable_object_name",
        "github_environment",
        "github_ref",
        "github_repository",
        "github_repository_id",
        "github_run_attempt",
        "github_run_id",
        "github_workflow_ref",
        "oidc_jti",
        "oidc_subject",
        "physical_started_at_utc",
        "probe_id",
        "purpose",
        "raw_response_sha256",
        "response_body_bytes",
        "response_completed_at_utc",
        "response_status",
        "schema_version",
        "signing_algorithm",
        "signing_key_id",
        "target_host",
        "target_path",
        "target_scheme",
    }
)


class ControlledProbeVerificationError(ValueError):
    """La evidencia de sonda no puede considerarse verificada."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


def _fail(code: str, message: str | None = None) -> NoReturn:
    raise ControlledProbeVerificationError(code, message)


def _exact_mapping(value: object, expected: frozenset[str], code: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != expected:
        _fail(code)
    return value


def _text(value: object, code: str, *, maximum: int = 2048) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or len(value) > maximum
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


def _integer(value: object, code: str, *, minimum: int = 0, maximum: int = 2**53 - 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        _fail(code)
    return value


def _b64url(value: object, code: str, *, maximum: int = 20_000) -> bytes:
    text = _text(value, code, maximum=maximum)
    if "=" in text or "+" in text or "/" in text or not _B64URL.fullmatch(text):
        _fail(code)
    padding = "=" * ((4 - len(text) % 4) % 4)
    try:
        decoded = base64.b64decode(text + padding, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ControlledProbeVerificationError(code) from exc
    if not decoded:
        _fail(code)
    canonical = base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=")
    if canonical != text:
        _fail(code)
    return decoded


def _utc_timestamp(value: object, code: str) -> datetime:
    text = _text(value, code, maximum=64)
    if not text.endswith("Z"):
        _fail(code)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise ControlledProbeVerificationError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        _fail(code)
    return parsed.astimezone(timezone.utc)


def _public_key(value: object) -> Ed25519PublicKey:
    encoded = _b64url(value, "probe_public_key_invalid", maximum=2048)
    try:
        key = load_der_public_key(encoded)
    except (TypeError, ValueError) as exc:
        raise ControlledProbeVerificationError("probe_public_key_invalid") from exc
    if not isinstance(key, Ed25519PublicKey):
        _fail("probe_public_key_algorithm_invalid")
    return key


@dataclass(frozen=True, slots=True)
class VerifiedControlledProbeEvidence:
    """Evidencia de infraestructura verificada fuera del Worker emisor."""

    evidence_id: str
    receipt_digest: str
    raw_response_sha256: str
    target_host: str
    collector_release_id: str
    probe_id: str
    github_run_id: str
    github_run_attempt: int
    cryptographic_signature_verified: bool = True
    production_authority: bool = False
    catalog_accepted: bool = False

    def __post_init__(self) -> None:
        _sha256(self.evidence_id, "verified_probe_evidence_id_invalid")
        _sha256(self.receipt_digest, "verified_probe_receipt_digest_invalid")
        _sha256(self.raw_response_sha256, "verified_probe_raw_hash_invalid")
        if self.cryptographic_signature_verified is not True:
            _fail("verified_probe_signature_state_invalid")
        if self.production_authority is not False or self.catalog_accepted is not False:
            _fail("verified_probe_authority_forbidden")


def verify_controlled_probe_artifact(
    artifact: Mapping[str, object],
    *,
    public_key_spki_b64url: str,
    expected_commit_sha: str,
    expected_run_id: str,
    expected_run_attempt: int,
) -> VerifiedControlledProbeEvidence:
    """Verifica firma, bytes, request canónico y contexto OIDC de una sonda.

    La clave pública debe provenir de una configuración confiable externa al
    Worker emisor. El resultado no es evidencia de catálogo ni de La Colonia.
    """

    top = _exact_mapping(artifact, _TOP_LEVEL_KEYS, "probe_artifact_shape_invalid")
    if top["ok"] is not True or top["decision"] != "PROBE_COMPLETED":
        _fail("probe_artifact_not_completed")
    if top["replayed"] is not False:
        _fail("probe_artifact_replay_forbidden")
    if top["purpose"] != CONTROLLED_PROBE_PURPOSE:
        _fail("probe_artifact_purpose_mismatch")
    if top["signingKeyId"] != CONTROLLED_PROBE_SIGNING_KEY_ID:
        _fail("probe_artifact_signing_key_id_mismatch")

    commit_sha = _sha1(expected_commit_sha, "expected_commit_sha_invalid")
    run_id = _text(expected_run_id, "expected_run_id_invalid", maximum=64)
    run_attempt = _integer(
        expected_run_attempt,
        "expected_run_attempt_invalid",
        minimum=1,
        maximum=100,
    )

    receipt = _exact_mapping(
        top["receiptPayload"],
        _RECEIPT_KEYS,
        "probe_receipt_shape_invalid",
    )
    if receipt["schema_version"] != CONTROLLED_PROBE_SCHEMA_VERSION:
        _fail("probe_receipt_schema_mismatch")
    if receipt["purpose"] != CONTROLLED_PROBE_PURPOSE:
        _fail("probe_receipt_purpose_mismatch")
    if receipt["collector_provider"] != "cloudflare_workers":
        _fail("probe_receipt_provider_mismatch")
    if receipt["collector_principal"] != "cloudflare-worker:precios-sps-controlled-probe":
        _fail("probe_receipt_principal_mismatch")
    collector_release_id = _text(
        receipt["collector_release_id"],
        "probe_receipt_release_invalid",
        maximum=256,
    )
    if receipt["signing_algorithm"] != "Ed25519":
        _fail("probe_receipt_signing_algorithm_mismatch")
    if receipt["signing_key_id"] != CONTROLLED_PROBE_SIGNING_KEY_ID:
        _fail("probe_receipt_signing_key_id_mismatch")

    if receipt["approved_commit_sha"] != commit_sha:
        _fail("probe_receipt_commit_mismatch")
    if receipt["github_repository"] != CONTROLLED_PROBE_REPOSITORY:
        _fail("probe_receipt_repository_mismatch")
    if receipt["github_repository_id"] != CONTROLLED_PROBE_REPOSITORY_ID:
        _fail("probe_receipt_repository_id_mismatch")
    if receipt["github_ref"] != CONTROLLED_PROBE_REF:
        _fail("probe_receipt_ref_mismatch")
    if receipt["github_workflow_ref"] != CONTROLLED_PROBE_WORKFLOW_REF:
        _fail("probe_receipt_workflow_ref_mismatch")
    if receipt["github_environment"] != CONTROLLED_PROBE_ENVIRONMENT:
        _fail("probe_receipt_environment_mismatch")
    if receipt["oidc_subject"] != CONTROLLED_PROBE_SUBJECT:
        _fail("probe_receipt_subject_mismatch")
    _text(receipt["oidc_jti"], "probe_receipt_jti_invalid", maximum=256)

    if receipt["github_run_id"] != run_id:
        _fail("probe_receipt_run_id_mismatch")
    if receipt["github_run_attempt"] != run_attempt:
        _fail("probe_receipt_run_attempt_mismatch")
    if receipt["durable_object_name"] != f"github-run:{run_id}:{run_attempt}":
        _fail("probe_receipt_durable_object_mismatch")
    probe_id = _text(receipt["probe_id"], "probe_receipt_probe_id_invalid", maximum=128)
    if not _PROBE_ID.fullmatch(probe_id) or probe_id != f"github-{run_id}-{run_attempt}":
        _fail("probe_receipt_probe_id_mismatch")

    if receipt["target_scheme"] != "https":
        _fail("probe_receipt_target_scheme_mismatch")
    target_host = _text(receipt["target_host"], "probe_receipt_target_host_invalid", maximum=512)
    if (
        target_host != target_host.lower()
        or not _WORKERS_DEV_HOST.fullmatch(target_host)
        or target_host == "www.lacolonia.com"
        or target_host.endswith(".lacolonia.com")
    ):
        _fail("probe_receipt_target_host_forbidden")
    if receipt["target_path"] != CONTROLLED_PROBE_ORIGIN_PATH:
        _fail("probe_receipt_target_path_mismatch")
    if receipt["response_status"] != 200:
        _fail("probe_receipt_response_status_mismatch")

    physical_start = _utc_timestamp(
        receipt["physical_started_at_utc"],
        "probe_receipt_physical_start_invalid",
    )
    response_end = _utc_timestamp(
        receipt["response_completed_at_utc"],
        "probe_receipt_response_end_invalid",
    )
    if response_end < physical_start:
        _fail("probe_receipt_time_order_invalid")

    raw_body = _b64url(top["rawBodyB64Url"], "probe_raw_body_invalid", maximum=100_000)
    body_size = _integer(
        receipt["response_body_bytes"],
        "probe_receipt_body_size_invalid",
        minimum=1,
        maximum=32 * 1024,
    )
    if len(raw_body) != body_size:
        _fail("probe_raw_body_size_mismatch")
    raw_hash = hashlib.sha256(raw_body).hexdigest()
    if receipt["raw_response_sha256"] != raw_hash:
        _fail("probe_raw_body_hash_mismatch")
    _sha256(receipt["raw_response_sha256"], "probe_receipt_raw_hash_invalid")

    try:
        raw_payload = json.loads(raw_body.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControlledProbeVerificationError("probe_raw_body_json_invalid") from exc
    if not isinstance(raw_payload, Mapping) or set(raw_payload) != {"challenge", "ok", "purpose"}:
        _fail("probe_raw_body_shape_invalid")
    if raw_payload["ok"] is not True or raw_payload["purpose"] != CONTROLLED_PROBE_PURPOSE:
        _fail("probe_raw_body_content_invalid")
    challenge = _text(raw_payload["challenge"], "probe_challenge_invalid", maximum=256)
    if not _CHALLENGE.fullmatch(challenge):
        _fail("probe_challenge_invalid")

    canonical_request = {
        "challenge": challenge,
        "method": "GET",
        "probe_id": probe_id,
        "purpose": CONTROLLED_PROBE_PURPOSE,
        "target_host": target_host,
        "target_path": CONTROLLED_PROBE_ORIGIN_PATH,
        "target_scheme": "https",
    }
    expected_request_hash = hashlib.sha256(canonical_json_bytes(canonical_request)).hexdigest()
    if receipt["canonical_request_sha256"] != expected_request_hash:
        _fail("probe_canonical_request_hash_mismatch")
    _sha256(receipt["canonical_request_sha256"], "probe_receipt_request_hash_invalid")

    signature_text = _text(top["signatureB64Url"], "probe_signature_invalid", maximum=256)
    signature = _b64url(signature_text, "probe_signature_invalid", maximum=256)
    if len(signature) != 64:
        _fail("probe_signature_length_invalid")
    receipt_bytes = canonical_json_bytes(dict(receipt))
    try:
        _public_key(public_key_spki_b64url).verify(
            signature,
            CONTROLLED_PROBE_SIGNATURE_DOMAIN + receipt_bytes,
        )
    except InvalidSignature as exc:
        raise ControlledProbeVerificationError("probe_signature_verification_failed") from exc

    evidence_id = _sha256(top["evidenceId"], "probe_evidence_id_invalid")
    recomputed_evidence_id = hashlib.sha256(
        CONTROLLED_PROBE_SIGNATURE_DOMAIN
        + receipt_bytes
        + b"\0"
        + signature_text.encode("ascii")
    ).hexdigest()
    if evidence_id != recomputed_evidence_id:
        _fail("probe_evidence_id_mismatch")

    return VerifiedControlledProbeEvidence(
        evidence_id=evidence_id,
        receipt_digest=hashlib.sha256(receipt_bytes).hexdigest(),
        raw_response_sha256=raw_hash,
        target_host=target_host,
        collector_release_id=collector_release_id,
        probe_id=probe_id,
        github_run_id=run_id,
        github_run_attempt=run_attempt,
    )
