from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from precios_supermercados.edge_structural_observation import (
    CryptographicallyVerifiedStructuralObservation,
    EdgeStructuralObservationError,
    EdgeStructuralObservationVerifier,
)
from precios_supermercados.la_colonia_edge_structural_request import (
    build_structural_discovery_url,
    validate_la_colonia_structural_request,
)
from precios_supermercados.structural_provenance import (
    SignedStructuralReceipt,
    StructuralReceiptPayload,
)
from precios_supermercados.structural_receipt_crypto import Ed25519StructuralReceiptVerifier


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _body(kind: str) -> bytes:
    if kind == "root_total":
        value = {"data": {"productSearch": {"recordsFiltered": 7}}}
    else:
        value = {
            "data": {
                "productSearch": {"recordsFiltered": 7},
                "facets": {
                    "sampling": False,
                    "facets": [
                        {
                            "type": "CATEGORY",
                            "values": [
                                {
                                    "key": "category-1",
                                    "value": "supermercado",
                                    "quantity": 7,
                                    "children": [],
                                }
                            ],
                        }
                    ],
                },
            }
        }
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _payload(kind: str, raw_body: bytes, **overrides: object) -> StructuralReceiptPayload:
    request = validate_la_colonia_structural_request(build_structural_discovery_url(kind))
    values: dict[str, object] = {
        "run_id": "32523000000:1",
        "request_kind": kind,
        "request_id": f"request-{kind}",
        "reservation_id": f"reservation-{kind}",
        "authorization_id": "authorization-structural-observation",
        "approved_commit_sha": "a" * 40,
        "request_digest": request.canonical_request_sha256,
        "canonical_request_sha256": request.canonical_request_sha256,
        "graphql_query_sha256": request.graphql_query_sha256,
        "http_method": "GET",
        "target_scheme": "https",
        "target_host": "www.lacolonia.com",
        "target_path": "/_v/segment/graphql/v1",
        "raw_response_sha256": hashlib.sha256(raw_body).hexdigest(),
        "response_status": 200,
        "response_body_bytes": len(raw_body),
        "physical_started_at_utc": datetime(2026, 8, 21, 20, 20, 0, 100_000, tzinfo=timezone.utc),
        "response_completed_at_utc": datetime(2026, 8, 21, 20, 20, 0, 300_000, tzinfo=timezone.utc),
        "github_repository": "jchernandez-portfolio/precios-supermercados-sps",
        "github_repository_id": "1282475205",
        "github_ref": "refs/heads/main",
        "github_workflow_ref": "jchernandez-portfolio/precios-supermercados-sps/.github/workflows/precios-supermercados-sps-la-colonia-live.yml@refs/heads/main",
        "github_environment": "la-colonia-live",
        "github_run_id": "32523000000",
        "github_run_attempt": 1,
        "oidc_subject": "repo:jchernandez-portfolio/precios-supermercados-sps:environment:la-colonia-live",
        "oidc_jti": f"jti-{kind}",
        "collector_provider": "cloudflare_workers",
        "collector_principal": "cloudflare-worker:precios-sps-provenance",
        "collector_execution": f"execution-{kind}",
        "collector_release_id": "release-structural-001",
        "collector_code_sha256": "d" * 64,
        "signing_algorithm": "Ed25519",
        "signing_key_id": "cloudflare-ed25519-v1",
        "nonce": f"nonce-{kind}",
    }
    values.update(overrides)
    return StructuralReceiptPayload(**values)  # type: ignore[arg-type]


def _signed(payload: StructuralReceiptPayload, private_key: Ed25519PrivateKey) -> SignedStructuralReceipt:
    placeholder = SignedStructuralReceipt(
        payload=payload,
        signature_b64url=_b64url(b"0" * 64),
    )
    signature = private_key.sign(placeholder.signing_bytes())
    return SignedStructuralReceipt(payload=payload, signature_b64url=_b64url(signature))


def _verifier(private_key: Ed25519PrivateKey) -> EdgeStructuralObservationVerifier:
    public_der = private_key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    return EdgeStructuralObservationVerifier(
        Ed25519StructuralReceiptVerifier({"cloudflare-ed25519-v1": _b64url(public_der)})
    )


@pytest.mark.parametrize("kind", ["root_total", "category_tree"])
def test_compone_request_receipt_y_body_autenticados(kind: str) -> None:
    private_key = Ed25519PrivateKey.generate()
    raw_body = _body(kind)
    payload = _payload(kind, raw_body)

    result = _verifier(private_key).verify(
        source_url=build_structural_discovery_url(kind),
        raw_body=raw_body,
        signed_receipt=_signed(payload, private_key),
    )

    assert isinstance(result, CryptographicallyVerifiedStructuralObservation)
    assert result.request_kind == kind
    assert result.records_filtered == 7
    assert result.raw_body_sha256 == hashlib.sha256(raw_body).hexdigest()
    assert result.cryptographic_signature_verified is True
    assert result.structural_body_validated is True
    assert result.production_authority is False


def test_body_alterado_falla_por_hash_antes_de_parsear() -> None:
    private_key = Ed25519PrivateKey.generate()
    original = _body("root_total")
    payload = _payload("root_total", original)
    altered = original.replace(b"7", b"8")

    with pytest.raises(EdgeStructuralObservationError) as exc:
        _verifier(private_key).verify(
            source_url=build_structural_discovery_url("root_total"),
            raw_body=altered,
            signed_receipt=_signed(payload, private_key),
        )
    assert exc.value.code == "structural_receipt_raw_body_hash_mismatch"


def test_kind_del_receipt_debe_coincidir_con_request() -> None:
    private_key = Ed25519PrivateKey.generate()
    raw_body = _body("root_total")
    payload = _payload("root_total", raw_body)

    with pytest.raises(EdgeStructuralObservationError) as exc:
        _verifier(private_key).verify(
            source_url=build_structural_discovery_url("category_tree"),
            raw_body=raw_body,
            signed_receipt=_signed(payload, private_key),
        )
    assert exc.value.code == "structural_receipt_request_kind_mismatch"


def test_query_hash_fabricado_falla_aunque_este_firmado() -> None:
    private_key = Ed25519PrivateKey.generate()
    raw_body = _body("root_total")
    payload = _payload("root_total", raw_body, graphql_query_sha256="f" * 64)

    with pytest.raises(EdgeStructuralObservationError) as exc:
        _verifier(private_key).verify(
            source_url=build_structural_discovery_url("root_total"),
            raw_body=raw_body,
            signed_receipt=_signed(payload, private_key),
        )
    assert exc.value.code == "structural_receipt_graphql_query_digest_mismatch"


def test_digest_fabricado_falla_aunque_este_firmado() -> None:
    private_key = Ed25519PrivateKey.generate()
    raw_body = _body("root_total")
    payload = _payload(
        "root_total",
        raw_body,
        request_digest="e" * 64,
        canonical_request_sha256="e" * 64,
    )

    with pytest.raises(EdgeStructuralObservationError) as exc:
        _verifier(private_key).verify(
            source_url=build_structural_discovery_url("root_total"),
            raw_body=raw_body,
            signed_receipt=_signed(payload, private_key),
        )
    assert exc.value.code == "structural_receipt_request_digest_mismatch"


def test_firma_invalida_falla_antes_de_json_invalido() -> None:
    trusted = Ed25519PrivateKey.generate()
    attacker = Ed25519PrivateKey.generate()
    invalid_body = b"not-json"
    payload = _payload(
        "root_total",
        invalid_body,
        raw_response_sha256=hashlib.sha256(invalid_body).hexdigest(),
        response_body_bytes=len(invalid_body),
    )

    with pytest.raises(EdgeStructuralObservationError) as exc:
        _verifier(trusted).verify(
            source_url=build_structural_discovery_url("root_total"),
            raw_body=invalid_body,
            signed_receipt=_signed(payload, attacker),
        )
    assert exc.value.code == "structural_receipt_crypto_structural_receipt_signature_invalid"


def test_body_graphql_invalido_falla_despues_de_firma_valida() -> None:
    private_key = Ed25519PrivateKey.generate()
    invalid_body = b'{"data":{"productSearch":{"recordsFiltered":0}}}'
    payload = _payload("root_total", invalid_body)

    with pytest.raises(EdgeStructuralObservationError) as exc:
        _verifier(private_key).verify(
            source_url=build_structural_discovery_url("root_total"),
            raw_body=invalid_body,
            signed_receipt=_signed(payload, private_key),
        )
    assert exc.value.code == "structural_body_structural_records_filtered_invalid"


def test_receipt_refirmado_con_body_size_falso_falla() -> None:
    private_key = Ed25519PrivateKey.generate()
    raw_body = _body("root_total")
    payload = _payload("root_total", raw_body, response_body_bytes=len(raw_body) + 1)

    with pytest.raises(EdgeStructuralObservationError) as exc:
        _verifier(private_key).verify(
            source_url=build_structural_discovery_url("root_total"),
            raw_body=raw_body,
            signed_receipt=_signed(payload, private_key),
        )
    assert exc.value.code == "structural_receipt_raw_body_size_mismatch"
