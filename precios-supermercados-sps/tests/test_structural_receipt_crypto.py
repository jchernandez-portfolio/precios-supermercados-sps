from __future__ import annotations

import base64
import json
import shutil
import subprocess
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from precios_supermercados.structural_provenance import (
    SignedStructuralReceipt,
    StructuralProvenanceError,
    StructuralReceiptPayload,
)
from precios_supermercados.structural_receipt_crypto import (
    Ed25519StructuralReceiptVerifier,
    StructuralReceiptCryptoError,
    VerifiedStructuralReceipt,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLI = PROJECT_ROOT / "edge" / "cloudflare" / "test" / "structural-receipt-sign-cli.mjs"


def _node() -> str:
    executable = shutil.which("node")
    assert executable is not None
    return executable


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _keys() -> tuple[str, str]:
    private = Ed25519PrivateKey.generate()
    private_der = private.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption())
    public_der = private.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    return _b64url(private_der), _b64url(public_der)


def _payload(**overrides: object) -> StructuralReceiptPayload:
    values: dict[str, object] = {
        "run_id": "32520000000:1",
        "request_kind": "root_total",
        "request_id": "structural-request-001",
        "reservation_id": "structural-reservation-001",
        "authorization_id": "structural-auth-001",
        "approved_commit_sha": "a" * 40,
        "request_digest": "b" * 64,
        "canonical_request_sha256": "b" * 64,
        "graphql_query_sha256": "c" * 64,
        "http_method": "GET",
        "target_scheme": "https",
        "target_host": "www.lacolonia.com",
        "target_path": "/_v/segment/graphql/v1",
        "raw_response_sha256": "d" * 64,
        "response_status": 200,
        "response_body_bytes": 123,
        "physical_started_at_utc": datetime(2026, 8, 21, 20, 10, 0, 123_000, tzinfo=timezone.utc),
        "response_completed_at_utc": datetime(2026, 8, 21, 20, 10, 0, 456_000, tzinfo=timezone.utc),
        "github_repository": "jchernandez-portfolio/precios-supermercados-sps",
        "github_repository_id": "1282475205",
        "github_ref": "refs/heads/main",
        "github_workflow_ref": "jchernandez-portfolio/precios-supermercados-sps/.github/workflows/precios-supermercados-sps-la-colonia-live.yml@refs/heads/main",
        "github_environment": "la-colonia-live",
        "github_run_id": "32520000000",
        "github_run_attempt": 1,
        "oidc_subject": "repo:jchernandez-portfolio/precios-supermercados-sps:environment:la-colonia-live",
        "oidc_jti": "structural-jti-001",
        "collector_provider": "cloudflare_workers",
        "collector_principal": "cloudflare-worker:precios-sps-provenance",
        "collector_execution": "structural-execution-001",
        "collector_release_id": "structural-release-001",
        "collector_code_sha256": "e" * 64,
        "signing_algorithm": "Ed25519",
        "signing_key_id": "cloudflare-ed25519-v1",
        "nonce": "structural-nonce-001",
    }
    values.update(overrides)
    return StructuralReceiptPayload(**values)  # type: ignore[arg-type]


def _js_sign(payload: StructuralReceiptPayload, private_key: str, public_key: str) -> dict[str, object]:
    completed = subprocess.run(
        [_node(), str(CLI)],
        cwd=PROJECT_ROOT,
        input=json.dumps(
            {
                "receiptPayload": payload.canonical_dict(),
                "privateKeyPkcs8B64Url": private_key,
                "publicKeySpkiB64Url": public_key,
            },
            separators=(",", ":"),
        ),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    assert result["ok"] is True
    return result


def test_javascript_y_python_comparten_firma_y_digest() -> None:
    private_key, public_key = _keys()
    payload = _payload()
    js = _js_sign(payload, private_key, public_key)
    receipt = SignedStructuralReceipt(payload=payload, signature_b64url=str(js["signatureB64Url"]))

    verified = Ed25519StructuralReceiptVerifier({payload.signing_key_id: public_key}).verify(receipt)

    assert isinstance(verified, VerifiedStructuralReceipt)
    assert verified.cryptographic_signature_verified is True
    assert verified.production_authority is False
    assert verified.receipt_digest == receipt.digest == js["digest"]
    assert js["verified"] is True


def test_payload_modificado_rompe_firma() -> None:
    private_key, public_key = _keys()
    payload = _payload()
    js = _js_sign(payload, private_key, public_key)
    tampered = replace(payload, raw_response_sha256="f" * 64)
    receipt = SignedStructuralReceipt(payload=tampered, signature_b64url=str(js["signatureB64Url"]))

    with pytest.raises(StructuralReceiptCryptoError) as exc:
        Ed25519StructuralReceiptVerifier({payload.signing_key_id: public_key}).verify(receipt)
    assert exc.value.code == "structural_receipt_signature_invalid"


def test_key_id_no_confiado_falla_cerrado() -> None:
    private_key, public_key = _keys()
    payload = _payload()
    js = _js_sign(payload, private_key, public_key)
    receipt = SignedStructuralReceipt(payload=payload, signature_b64url=str(js["signatureB64Url"]))

    with pytest.raises(StructuralReceiptCryptoError) as exc:
        Ed25519StructuralReceiptVerifier({"other-key": public_key}).verify(receipt)
    assert exc.value.code == "structural_receipt_signing_key_untrusted"


def test_receipt_rechaza_firma_no_ed25519_y_tiempos_no_milisegundo() -> None:
    with pytest.raises(StructuralProvenanceError) as short:
        SignedStructuralReceipt(payload=_payload(), signature_b64url=_b64url(b"x" * 32))
    assert short.value.code == "structural_receipt_signature_length_invalid"

    with pytest.raises(StructuralProvenanceError) as timestamp:
        _payload(
            physical_started_at_utc=datetime(2026, 8, 21, 20, 10, 0, 123_456, tzinfo=timezone.utc)
        )
    assert timestamp.value.code == "structural_receipt_physical_started_at_invalid"


def test_root_y_category_tree_tienen_identidad_estructural_explicita() -> None:
    root = _payload(request_kind="root_total", request_id="root-request")
    tree = _payload(
        request_kind="category_tree",
        request_id="tree-request",
        reservation_id="tree-reservation",
        nonce="tree-nonce",
        graphql_query_sha256="f" * 64,
    )
    assert root.request_kind == "root_total"
    assert tree.request_kind == "category_tree"
    assert root.canonical_bytes() != tree.canonical_bytes()
