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

from precios_supermercados.edge_provenance import EdgeReceiptPayload, SignedEdgeReceipt
from precios_supermercados.edge_receipt_crypto import (
    CryptographicallyVerifiedEdgeReceipt,
    Ed25519ReceiptVerifier,
    EdgeReceiptCryptoError,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLI = PROJECT_ROOT / "edge" / "cloudflare" / "test" / "receipt-sign-cli.mjs"


def _node() -> str:
    executable = shutil.which("node")
    assert executable is not None, "Node.js es obligatorio para validar firma edge cross-runtime"
    return executable


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _payload(**overrides: object) -> EdgeReceiptPayload:
    values: dict[str, object] = {
        "run_id": "32514816676:1",
        "request_id": "request-crypto-001",
        "reservation_id": "reservation-crypto-001",
        "authorization_id": "authorization-crypto-001",
        "approved_commit_sha": "a" * 40,
        "request_digest": "b" * 64,
        "traversal_id": "traversal-primary-crypto-001",
        "traversal_role": "primary",
        "order_by": "OrderByNameASC",
        "partition_id": "root",
        "from_index": 0,
        "to_index": 49,
        "http_method": "GET",
        "target_scheme": "https",
        "target_host": "www.lacolonia.com",
        "target_path": "/_v/segment/graphql/v1",
        "canonical_request_sha256": "b" * 64,
        "raw_response_sha256": "c" * 64,
        "response_status": 200,
        "response_body_bytes": 1234,
        "physical_started_at_utc": datetime(2026, 8, 21, 18, 30, 0, 123_000, tzinfo=timezone.utc),
        "response_completed_at_utc": datetime(2026, 8, 21, 18, 30, 0, 456_000, tzinfo=timezone.utc),
        "github_repository": "jchernandez-portfolio/precios-supermercados-sps",
        "github_repository_id": "1282475205",
        "github_ref": "refs/heads/main",
        "github_workflow_ref": "jchernandez-portfolio/precios-supermercados-sps/.github/workflows/precios-supermercados-sps-la-colonia-live.yml@refs/heads/main",
        "github_environment": "la-colonia-live",
        "github_run_id": "32514816676",
        "github_run_attempt": 1,
        "oidc_subject": "repo:jchernandez-portfolio/precios-supermercados-sps:environment:la-colonia-live",
        "oidc_jti": "oidc-jti-crypto-001",
        "collector_provider": "cloudflare_workers",
        "collector_principal": "cloudflare-worker:precios-sps-provenance",
        "collector_execution": "execution-crypto-001",
        "collector_release_id": "release-crypto-001",
        "collector_code_sha256": "d" * 64,
        "signing_algorithm": "Ed25519",
        "signing_key_id": "edge-signing-key-crypto-001",
        "nonce": "nonce-crypto-001",
    }
    values.update(overrides)
    return EdgeReceiptPayload(**values)  # type: ignore[arg-type]


def _key_material() -> tuple[Ed25519PrivateKey, str, str]:
    private_key = Ed25519PrivateKey.generate()
    private_der = private_key.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption())
    public_der = private_key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    return private_key, _b64url(private_der), _b64url(public_der)


def _sign_in_javascript(payload: EdgeReceiptPayload, private_key_b64url: str) -> str:
    result = subprocess.run(
        [_node(), str(CLI)],
        cwd=PROJECT_ROOT,
        input=json.dumps(
            {
                "receiptPayload": payload.canonical_dict(),
                "privateKeyPkcs8B64Url": private_key_b64url,
            },
            separators=(",", ":"),
        ),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    parsed = json.loads(result.stdout)
    assert parsed["ok"] is True
    return parsed["signatureB64Url"]


def test_firma_generada_por_javascript_verifica_en_python() -> None:
    _private_key, private_b64url, public_b64url = _key_material()
    payload = _payload()
    signature = _sign_in_javascript(payload, private_b64url)
    receipt = SignedEdgeReceipt(payload=payload, signature_b64url=signature)

    verifier = Ed25519ReceiptVerifier({payload.signing_key_id: public_b64url})
    result = verifier.verify(receipt)

    assert isinstance(result, CryptographicallyVerifiedEdgeReceipt)
    assert result.receipt is receipt
    assert result.signing_key_id == payload.signing_key_id
    assert result.receipt_digest == receipt.digest
    assert len(result.public_key_spki_sha256) == 64
    assert result.cryptographic_signature_verified is True
    assert result.production_authority is False
    assert verifier.key_ids == (payload.signing_key_id,)


def test_payload_modificado_con_firma_original_falla() -> None:
    _private_key, private_b64url, public_b64url = _key_material()
    payload = _payload()
    signature = _sign_in_javascript(payload, private_b64url)
    tampered = replace(payload, raw_response_sha256="f" * 64)
    receipt = SignedEdgeReceipt(payload=tampered, signature_b64url=signature)

    with pytest.raises(EdgeReceiptCryptoError) as captured:
        Ed25519ReceiptVerifier({payload.signing_key_id: public_b64url}).verify(receipt)
    assert captured.value.code == "receipt_signature_invalid"


def test_key_id_no_confiado_falla_antes_de_verificar() -> None:
    _private_key, private_b64url, public_b64url = _key_material()
    payload = _payload()
    signature = _sign_in_javascript(payload, private_b64url)
    receipt = SignedEdgeReceipt(payload=payload, signature_b64url=signature)

    with pytest.raises(EdgeReceiptCryptoError) as captured:
        Ed25519ReceiptVerifier({"otra-key": public_b64url}).verify(receipt)
    assert captured.value.code == "receipt_signing_key_untrusted"


def test_misma_key_id_con_public_key_distinta_falla() -> None:
    _private_key, private_b64url, _public_b64url = _key_material()
    _other_private, _other_private_b64url, other_public_b64url = _key_material()
    payload = _payload()
    signature = _sign_in_javascript(payload, private_b64url)
    receipt = SignedEdgeReceipt(payload=payload, signature_b64url=signature)

    with pytest.raises(EdgeReceiptCryptoError) as captured:
        Ed25519ReceiptVerifier({payload.signing_key_id: other_public_b64url}).verify(receipt)
    assert captured.value.code == "receipt_signature_invalid"


def test_keyring_rechaza_spki_invalido_y_no_admite_vacio() -> None:
    with pytest.raises(EdgeReceiptCryptoError) as empty:
        Ed25519ReceiptVerifier({})
    assert empty.value.code == "public_keyring_invalid"

    with pytest.raises(EdgeReceiptCryptoError) as invalid:
        Ed25519ReceiptVerifier({"key-1": _b64url(b"not-a-der-public-key")})
    assert invalid.value.code == "public_key_spki_invalid"
