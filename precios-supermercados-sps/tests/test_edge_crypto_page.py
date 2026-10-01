from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from precios_supermercados.edge_catalog_page import ValidatedEdgeCatalogPage
from precios_supermercados.edge_crypto_page import (
    CryptographicallyVerifiedEdgeCatalogPage,
    EdgeCatalogPageCryptoVerifier,
    EdgeCryptoPageError,
)
from precios_supermercados.edge_gateway_client import EdgeGatewayEvidence
from precios_supermercados.edge_provenance import (
    EdgeReceiptPayload,
    SignedEdgeReceipt,
    canonical_json_bytes,
)
from precios_supermercados.edge_receipt_crypto import (
    Ed25519ReceiptVerifier,
    receipt_signing_bytes,
)
from precios_supermercados.la_colonia_edge_body import validate_la_colonia_edge_body
from precios_supermercados.la_colonia_edge_request import validate_la_colonia_edge_request
from precios_supermercados.scrapers.la_colonia_graphql import build_product_search_url


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _worker_evidence_id(payload: dict[str, object], signature: str) -> str:
    return hashlib.sha256(
        canonical_json_bytes(payload) + b"\0" + signature.encode("ascii")
    ).hexdigest()


def _fixture() -> tuple[
    ValidatedEdgeCatalogPage,
    Ed25519ReceiptVerifier,
    Ed25519PrivateKey,
]:
    url = build_product_search_url(
        page=1,
        page_size=2,
        query="supermercado",
        category_map="category-1",
        order_by="OrderByNameASC",
    )
    request = validate_la_colonia_edge_request(url)
    raw = json.dumps(
        {
            "data": {
                "productSearch": {
                    "recordsFiltered": 2,
                    "products": [{}, {}],
                }
            }
        },
        separators=(",", ":"),
    ).encode("utf-8")
    body = validate_la_colonia_edge_body(raw, request)

    receipt_payload = EdgeReceiptPayload(
        run_id="32515112311:1",
        request_id="request-crypto-page-001",
        reservation_id="reservation-crypto-page-001",
        authorization_id="authorization-crypto-page-001",
        approved_commit_sha="a" * 40,
        request_digest=request.canonical_request_sha256,
        traversal_id="traversal-primary-crypto-page-001",
        traversal_role="primary",
        order_by=request.order_by,
        partition_id="root",
        from_index=request.from_index,
        to_index=request.to_index,
        http_method="GET",
        target_scheme="https",
        target_host="www.lacolonia.com",
        target_path="/_v/segment/graphql/v1",
        canonical_request_sha256=request.canonical_request_sha256,
        raw_response_sha256=hashlib.sha256(raw).hexdigest(),
        response_status=200,
        response_body_bytes=len(raw),
        physical_started_at_utc=datetime(2026, 8, 21, 18, 50, 0, 123_000, tzinfo=timezone.utc),
        response_completed_at_utc=datetime(2026, 8, 21, 18, 50, 0, 456_000, tzinfo=timezone.utc),
        github_repository="jchernandez-portfolio/precios-supermercados-sps",
        github_repository_id="1282475205",
        github_ref="refs/heads/main",
        github_workflow_ref="jchernandez-portfolio/precios-supermercados-sps/.github/workflows/precios-supermercados-sps-la-colonia-live.yml@refs/heads/main",
        github_environment="la-colonia-live",
        github_run_id="32515112311",
        github_run_attempt=1,
        oidc_subject="repo:jchernandez-portfolio/precios-supermercados-sps:environment:la-colonia-live",
        oidc_jti="oidc-jti-crypto-page-001",
        collector_provider="cloudflare_workers",
        collector_principal="cloudflare-worker:precios-sps-provenance",
        collector_execution="execution-crypto-page-001",
        collector_release_id="release-crypto-page-001",
        collector_code_sha256="d" * 64,
        signing_algorithm="Ed25519",
        signing_key_id="edge-signing-key-page-001",
        nonce="nonce-crypto-page-001",
    )
    private_key = Ed25519PrivateKey.generate()
    provisional = SignedEdgeReceipt(
        payload=receipt_payload,
        signature_b64url=_b64url(b"x" * 64),
    )
    signature = _b64url(private_key.sign(receipt_signing_bytes(provisional)))
    receipt = SignedEdgeReceipt(payload=receipt_payload, signature_b64url=signature)
    payload_dict = receipt_payload.canonical_dict()
    evidence = EdgeGatewayEvidence(
        response_status=200,
        raw_body=raw,
        receipt=receipt,
        worker_evidence_id=_worker_evidence_id(payload_dict, signature),
        replayed=False,
    )
    page = ValidatedEdgeCatalogPage(
        request=request,
        evidence=evidence,
        body=body,
    )
    public_der = private_key.public_key().public_bytes(
        Encoding.DER,
        PublicFormat.SubjectPublicKeyInfo,
    )
    verifier = Ed25519ReceiptVerifier(
        {receipt_payload.signing_key_id: _b64url(public_der)}
    )
    return page, verifier, private_key


def test_verifica_request_body_evidence_id_y_firma_sin_conceder_autoridad() -> None:
    page, verifier, _private = _fixture()
    result = EdgeCatalogPageCryptoVerifier(verifier).verify(page)

    assert isinstance(result, CryptographicallyVerifiedEdgeCatalogPage)
    assert result.cryptographic_signature_verified is True
    assert result.verified_receipt.cryptographic_signature_verified is True
    assert result.production_authority is False
    assert result.verified_receipt.production_authority is False
    assert result.payload["data"]["productSearch"]["recordsFiltered"] == 2  # type: ignore[index]
    assert result.source_url == page.source_url
    assert result.worker_evidence_id == page.evidence.worker_evidence_id


def test_evidence_id_fabricado_falla_aunque_firma_sea_valida() -> None:
    page, verifier, _private = _fixture()
    forged = ValidatedEdgeCatalogPage(
        request=page.request,
        body=page.body,
        evidence=EdgeGatewayEvidence(
            response_status=page.evidence.response_status,
            raw_body=page.evidence.raw_body,
            receipt=page.evidence.receipt,
            worker_evidence_id="f" * 64,
            replayed=False,
        ),
    )

    with pytest.raises(EdgeCryptoPageError) as captured:
        EdgeCatalogPageCryptoVerifier(verifier).verify(forged)
    assert captured.value.code == "worker_evidence_id_mismatch"


def test_body_distinto_del_receipt_falla_antes_de_crypto() -> None:
    page, verifier, _private = _fixture()
    changed_raw = page.evidence.raw_body.replace(b'"recordsFiltered":2', b'"recordsFiltered":1')
    forged = ValidatedEdgeCatalogPage(
        request=page.request,
        body=page.body,
        evidence=EdgeGatewayEvidence(
            response_status=200,
            raw_body=changed_raw,
            receipt=page.evidence.receipt,
            worker_evidence_id=page.evidence.worker_evidence_id,
            replayed=False,
        ),
    )

    with pytest.raises(EdgeCryptoPageError) as captured:
        EdgeCatalogPageCryptoVerifier(verifier).verify(forged)
    assert captured.value.code in {
        "body_graphql_partial_or_overfull_page",
        "receipt_raw_body_hash_mismatch",
    }


def test_public_key_equivocada_falla() -> None:
    page, _verifier, _private = _fixture()
    wrong = Ed25519PrivateKey.generate().public_key().public_bytes(
        Encoding.DER,
        PublicFormat.SubjectPublicKeyInfo,
    )
    verifier = Ed25519ReceiptVerifier(
        {page.evidence.receipt.payload.signing_key_id: _b64url(wrong)}
    )

    with pytest.raises(EdgeCryptoPageError) as captured:
        EdgeCatalogPageCryptoVerifier(verifier).verify(page)
    assert captured.value.code == "receipt_crypto_receipt_signature_invalid"


def test_rechaza_objetos_fuera_de_la_frontera() -> None:
    page, verifier, _private = _fixture()
    with pytest.raises(EdgeCryptoPageError) as client_error:
        EdgeCatalogPageCryptoVerifier(object())  # type: ignore[arg-type]
    assert client_error.value.code == "receipt_verifier_invalid"

    with pytest.raises(EdgeCryptoPageError) as page_error:
        EdgeCatalogPageCryptoVerifier(verifier).verify(object())  # type: ignore[arg-type]
    assert page_error.value.code == "edge_catalog_page_invalid"
