from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from precios_supermercados.edge_structural_gateway_client import (
    STRUCTURAL_EXECUTE_PATH,
    StructuralEdgeExecutionRequest,
    StructuralEdgeGatewayClient,
    StructuralEdgeGatewayClientError,
    StructuralEdgeGatewayDenied,
    StructuralEdgeGatewayEvidence,
    StructuralEdgeGatewayWait,
    StructuralEdgeRequestContext,
)
from precios_supermercados.edge_structural_observation import EdgeStructuralObservationVerifier
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


def _request(kind: str = "root_total") -> StructuralEdgeExecutionRequest:
    url = build_structural_discovery_url(kind)
    validated = validate_la_colonia_structural_request(url)
    return StructuralEdgeExecutionRequest(
        origin_url=url,
        context=StructuralEdgeRequestContext(
            authorization_id="auth-structural-client-001",
            run_id="32530000000:1",
            approved_commit_sha="a" * 40,
            reservation_id=f"reservation-{kind}-001",
            request_id=f"request-{kind}-001",
            request_digest=validated.canonical_request_sha256,
            nonce=f"nonce-{kind}-001",
            request_kind=kind,
        ),
    )


def _raw_body() -> bytes:
    return json.dumps(
        {"data": {"productSearch": {"recordsFiltered": 321}}},
        separators=(",", ":"),
    ).encode("utf-8")


def _payload(request: StructuralEdgeExecutionRequest, raw_body: bytes) -> StructuralReceiptPayload:
    validated = validate_la_colonia_structural_request(request.origin_url)
    return StructuralReceiptPayload(
        run_id=request.context.run_id,
        request_kind=request.context.request_kind,  # type: ignore[arg-type]
        request_id=request.context.request_id,
        reservation_id=request.context.reservation_id,
        authorization_id=request.context.authorization_id,
        approved_commit_sha=request.context.approved_commit_sha,
        request_digest=request.context.request_digest,
        canonical_request_sha256=validated.canonical_request_sha256,
        graphql_query_sha256=validated.graphql_query_sha256,
        http_method="GET",
        target_scheme="https",
        target_host="www.lacolonia.com",
        target_path="/_v/segment/graphql/v1",
        raw_response_sha256=hashlib.sha256(raw_body).hexdigest(),
        response_status=200,
        response_body_bytes=len(raw_body),
        physical_started_at_utc=datetime(2026, 8, 21, 22, 20, 0, tzinfo=timezone.utc),
        response_completed_at_utc=datetime(2026, 8, 21, 22, 20, 0, 100_000, tzinfo=timezone.utc),
        github_repository="jchernandez-portfolio/precios-supermercados-sps",
        github_repository_id="1282475205",
        github_ref="refs/heads/main",
        github_workflow_ref="jchernandez-portfolio/precios-supermercados-sps/.github/workflows/precios-supermercados-sps-la-colonia-live.yml@refs/heads/main",
        github_environment="la-colonia-live",
        github_run_id="32530000000",
        github_run_attempt=1,
        oidc_subject="repo:jchernandez-portfolio/precios-supermercados-sps:environment:la-colonia-live",
        oidc_jti="oidc-jti-structural-client-001",
        collector_provider="cloudflare_workers",
        collector_principal="cloudflare-worker:precios-sps-provenance",
        collector_execution="execution-structural-client-001",
        collector_release_id="release-structural-client-001",
        collector_code_sha256="d" * 64,
        signing_algorithm="Ed25519",
        signing_key_id="cloudflare-ed25519-v1",
        nonce=request.context.nonce,
    )


class _Transport:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def post_json(self, path, *, bearer_token, payload):
        self.calls.append((path, bearer_token, dict(payload)))
        return self.response


def _completed_response(
    request: StructuralEdgeExecutionRequest,
    *,
    private_key: Ed25519PrivateKey | None = None,
    replayed: bool = False,
) -> tuple[dict[str, object], Ed25519PrivateKey]:
    raw_body = _raw_body()
    payload = _payload(request, raw_body)
    key = private_key or Ed25519PrivateKey.generate()
    unsigned = SignedStructuralReceipt(
        payload=payload,
        signature_b64url=_b64url(b"x" * 64),
    )
    signature = _b64url(key.sign(unsigned.signing_bytes()))
    receipt = SignedStructuralReceipt(payload=payload, signature_b64url=signature)
    return (
        {
            "ok": True,
            "decision": "REPLAY_COMPLETED" if replayed else "ORIGIN_COMPLETED",
            "replayed": replayed,
            "responseStatus": 200,
            "rawBodyB64Url": _b64url(raw_body),
            "receiptPayload": payload.canonical_dict(),
            "signatureB64Url": signature,
            "signingKeyId": payload.signing_key_id,
            "evidenceId": receipt.digest,
        },
        key,
    )


def test_cliente_valida_envelope_y_no_concede_autoridad() -> None:
    request = _request()
    response, _key = _completed_response(request)
    transport = _Transport(response)
    result = StructuralEdgeGatewayClient(transport).execute(request, bearer_token="oidc-token")

    assert isinstance(result, StructuralEdgeGatewayEvidence)
    assert result.response_status == 200
    assert result.raw_body == _raw_body()
    assert result.receipt.payload.request_kind == "root_total"
    assert result.worker_evidence_id == result.receipt.digest
    assert result.replayed is False
    assert result.cryptographic_signature_verified is False
    assert result.production_authority is False
    assert transport.calls[0][0] == STRUCTURAL_EXECUTE_PATH
    assert transport.calls[0][1] == "oidc-token"
    assert set(transport.calls[0][2]["requestContext"]) == {
        "authorizationId",
        "runId",
        "approvedCommitSha",
        "reservationId",
        "requestId",
        "requestDigest",
        "nonce",
        "requestKind",
    }


def test_salida_del_cliente_verifica_criptograficamente_y_parsea_body() -> None:
    request = _request()
    response, key = _completed_response(request)
    evidence = StructuralEdgeGatewayClient(_Transport(response)).execute(
        request,
        bearer_token="oidc-token",
    )
    assert isinstance(evidence, StructuralEdgeGatewayEvidence)

    public_der = key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    crypto = Ed25519StructuralReceiptVerifier(
        {evidence.receipt.payload.signing_key_id: _b64url(public_der)}
    )
    observation = EdgeStructuralObservationVerifier(crypto).verify(
        source_url=request.origin_url,
        raw_body=evidence.raw_body,
        signed_receipt=evidence.receipt,
    )
    assert observation.cryptographic_signature_verified is True
    assert observation.records_filtered == 321
    assert observation.production_authority is False


def test_hash_de_body_alterado_falla_antes_de_entregar_evidencia() -> None:
    request = _request()
    response, _key = _completed_response(request)
    response["rawBodyB64Url"] = _b64url(b'{"data":{"productSearch":{"recordsFiltered":999}}}')
    with pytest.raises(StructuralEdgeGatewayClientError) as captured:
        StructuralEdgeGatewayClient(_Transport(response)).execute(
            request,
            bearer_token="oidc-token",
        )
    assert captured.value.code == "completed_body_hash_mismatch"


def test_evidence_id_alterado_falla() -> None:
    request = _request()
    response, _key = _completed_response(request)
    response["evidenceId"] = "f" * 64
    with pytest.raises(StructuralEdgeGatewayClientError) as captured:
        StructuralEdgeGatewayClient(_Transport(response)).execute(request, bearer_token="oidc-token")
    assert captured.value.code == "completed_evidence_id_mismatch"


def test_request_kind_y_digest_se_revalidan_antes_del_transporte() -> None:
    root = build_structural_discovery_url("root_total")
    validated = validate_la_colonia_structural_request(root)
    context = StructuralEdgeRequestContext(
        authorization_id="auth-x",
        run_id="32530000000:1",
        approved_commit_sha="a" * 40,
        reservation_id="reservation-x",
        request_id="request-x",
        request_digest=validated.canonical_request_sha256,
        nonce="nonce-x",
        request_kind="category_tree",
    )
    with pytest.raises(StructuralEdgeGatewayClientError) as captured:
        StructuralEdgeExecutionRequest(origin_url=root, context=context)
    assert captured.value.code == "request_kind_origin_mismatch"

    with pytest.raises(StructuralEdgeGatewayClientError) as digest:
        StructuralEdgeExecutionRequest(
            origin_url=root,
            context=StructuralEdgeRequestContext(
                authorization_id="auth-y",
                run_id="32530000000:1",
                approved_commit_sha="a" * 40,
                reservation_id="reservation-y",
                request_id="request-y",
                request_digest="f" * 64,
                nonce="nonce-y",
                request_kind="root_total",
            ),
        )
    assert digest.value.code == "request_digest_origin_mismatch"


def test_wait_y_deny_no_se_disfrazan_como_evidencia() -> None:
    request = _request()
    wait = StructuralEdgeGatewayClient(
        _Transport(
            {
                "ok": True,
                "decision": "WAIT",
                "reason": "pacing",
                "notBeforeMs": 123,
                "inFlightReservationId": None,
            }
        )
    ).execute(request, bearer_token="oidc-token")
    assert isinstance(wait, StructuralEdgeGatewayWait)
    assert wait.not_before_ms == 123

    denied = StructuralEdgeGatewayClient(
        _Transport({"ok": True, "decision": "DENY", "reason": "authorization_consumed"})
    ).execute(request, bearer_token="oidc-token")
    assert isinstance(denied, StructuralEdgeGatewayDenied)
    assert denied.reason == "authorization_consumed"


def test_worker_error_y_campos_extra_fallan_cerrado() -> None:
    request = _request()
    with pytest.raises(StructuralEdgeGatewayClientError) as worker:
        StructuralEdgeGatewayClient(
            _Transport({"ok": False, "error": "authorization_state_missing"})
        ).execute(request, bearer_token="oidc-token")
    assert worker.value.code == "worker_authorization_state_missing"

    response, _key = _completed_response(request)
    response["unexpected"] = True
    with pytest.raises(StructuralEdgeGatewayClientError) as shape:
        StructuralEdgeGatewayClient(_Transport(response)).execute(request, bearer_token="oidc-token")
    assert shape.value.code == "completed_response_shape_invalid"
