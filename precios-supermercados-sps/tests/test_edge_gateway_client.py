from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field
from typing import Mapping

import pytest

from precios_supermercados.edge_gateway_client import (
    EXECUTE_PATH,
    INITIALIZE_PATH,
    EdgeAuthorizationRequest,
    EdgeExecutionRequest,
    EdgeGatewayClient,
    EdgeGatewayClientError,
    EdgeGatewayDenied,
    EdgeGatewayEvidence,
    EdgeGatewayWait,
    EdgeRequestContext,
)
from precios_supermercados.edge_provenance import canonical_json_bytes
from precios_supermercados.la_colonia_edge_request import validate_la_colonia_edge_request
from precios_supermercados.scrapers.la_colonia_graphql import build_product_search_url


SHA = "a" * 40
RUN_ID = "32509541437:1"
RAW = b'{"data":{"productSearch":{"recordsFiltered":0}}}'
SIGNATURE = base64.urlsafe_b64encode(bytes(range(64))).decode("ascii").rstrip("=")


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def context(**overrides: object) -> EdgeRequestContext:
    values: dict[str, object] = {
        "authorization_id": "auth-edge-001",
        "run_id": RUN_ID,
        "approved_commit_sha": SHA,
        "reservation_id": "reservation-001",
        "request_id": "request-001",
        "request_digest": "b" * 64,
        "nonce": "nonce-001",
        "traversal_role": "primary",
        "traversal_id": "traversal-primary-001",
        "partition_id": "root",
    }
    values.update(overrides)
    return EdgeRequestContext(**values)  # type: ignore[arg-type]


def execution(**context_overrides: object) -> EdgeExecutionRequest:
    origin_url = build_product_search_url(
        page=1,
        page_size=50,
        order_by="OrderByNameASC",
    )
    digest = validate_la_colonia_edge_request(origin_url).canonical_request_sha256
    effective_overrides = {"request_digest": digest, **context_overrides}
    return EdgeExecutionRequest(
        origin_url=origin_url,
        context=context(**effective_overrides),
    )


def receipt_payload(request: EdgeExecutionRequest, **overrides: object) -> dict[str, object]:
    ctx = request.context
    origin = request.validated_origin
    payload: dict[str, object] = {
        "approved_commit_sha": ctx.approved_commit_sha,
        "authorization_id": ctx.authorization_id,
        "canonical_request_sha256": origin.canonical_request_sha256,
        "collector_code_sha256": "c" * 64,
        "collector_execution": "execution-edge-001",
        "collector_principal": "cloudflare-worker:precios-sps-provenance",
        "collector_provider": "cloudflare_workers",
        "collector_release_id": "release-edge-001",
        "from_index": origin.from_index,
        "github_environment": "la-colonia-live",
        "github_ref": "refs/heads/main",
        "github_repository": "jchernandez-portfolio/precios-supermercados-sps",
        "github_repository_id": "1282475205",
        "github_run_attempt": 1,
        "github_run_id": "32509541437",
        "github_workflow_ref": "jchernandez-portfolio/precios-supermercados-sps/.github/workflows/precios-supermercados-sps-la-colonia-live.yml@refs/heads/main",
        "http_method": "GET",
        "nonce": ctx.nonce,
        "oidc_jti": "oidc-jti-001",
        "oidc_subject": "repo:jchernandez-portfolio/precios-supermercados-sps:environment:la-colonia-live",
        "order_by": origin.order_by,
        "partition_id": ctx.partition_id,
        "physical_started_at_utc": "2026-08-21T17:40:00.123000Z",
        "raw_response_sha256": hashlib.sha256(RAW).hexdigest(),
        "request_digest": ctx.request_digest,
        "request_id": ctx.request_id,
        "reservation_id": ctx.reservation_id,
        "response_body_bytes": len(RAW),
        "response_completed_at_utc": "2026-08-21T17:40:00.456000Z",
        "response_status": 200,
        "run_id": ctx.run_id,
        "schema_version": "2",
        "signing_algorithm": "Ed25519",
        "signing_key_id": "edge-signing-key-001",
        "target_host": "www.lacolonia.com",
        "target_path": "/_v/segment/graphql/v1",
        "target_scheme": "https",
        "to_index": origin.to_index,
        "traversal_id": ctx.traversal_id,
        "traversal_role": ctx.traversal_role,
    }
    payload.update(overrides)
    return payload


def completed_response(request: EdgeExecutionRequest, **payload_overrides: object) -> dict[str, object]:
    payload = receipt_payload(request, **payload_overrides)
    evidence_id = hashlib.sha256(
        canonical_json_bytes(payload) + b"\0" + SIGNATURE.encode("ascii")
    ).hexdigest()
    return {
        "ok": True,
        "decision": "ORIGIN_COMPLETED",
        "replayed": False,
        "responseStatus": 200,
        "rawBodyB64Url": b64url(RAW),
        "receiptPayload": payload,
        "signatureB64Url": SIGNATURE,
        "signingKeyId": "edge-signing-key-001",
        "evidenceId": evidence_id,
    }


@dataclass
class FakeTransport:
    responses: list[Mapping[str, object]]
    calls: list[tuple[str, str, Mapping[str, object]]] = field(default_factory=list)

    def post_json(
        self,
        path: str,
        *,
        bearer_token: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        self.calls.append((path, bearer_token, payload))
        assert self.responses, "respuesta fake no configurada"
        return self.responses.pop(0)


def expect_code(code: str):
    def check(error: BaseException) -> bool:
        return isinstance(error, EdgeGatewayClientError) and error.code == code

    return check


def test_completed_reconcilia_body_receipt_contexto_y_evidence_id_sin_conceder_autoridad() -> None:
    request = execution()
    transport = FakeTransport([completed_response(request)])
    result = EdgeGatewayClient(transport).execute(request, bearer_token="oidc.synthetic.token")

    assert isinstance(result, EdgeGatewayEvidence)
    assert result.raw_body == RAW
    assert result.response_status == 200
    assert result.receipt.payload.request_id == request.context.request_id
    assert result.receipt.payload.run_id == RUN_ID
    assert result.replayed is False
    assert result.cryptographic_signature_verified is False
    assert result.production_authority is False
    assert len(result.receipt.digest) == 64
    assert transport.calls[0][0] == EXECUTE_PATH
    assert transport.calls[0][1] == "oidc.synthetic.token"
    assert transport.calls[0][2] == request.wire_payload()


def test_replay_completed_exige_coherencia_entre_decision_y_flag() -> None:
    request = execution()
    response = completed_response(request)
    response["decision"] = "REPLAY_COMPLETED"
    response["replayed"] = True
    result = EdgeGatewayClient(FakeTransport([response])).execute(request, bearer_token="token")
    assert isinstance(result, EdgeGatewayEvidence)
    assert result.replayed is True


def test_body_corrupto_mismo_tamano_falla_por_sha256() -> None:
    request = execution()
    response = completed_response(request)
    corrupted = bytearray(RAW)
    corrupted[-2] ^= 1
    response["rawBodyB64Url"] = b64url(bytes(corrupted))
    with pytest.raises(EdgeGatewayClientError, match="receipt_raw_body_hash_mismatch") as captured:
        EdgeGatewayClient(FakeTransport([response])).execute(request, bearer_token="token")
    assert captured.value.code == "receipt_raw_body_hash_mismatch"


def test_evidence_id_se_recalcula_y_no_se_confia_en_el_worker() -> None:
    request = execution()
    response = completed_response(request)
    response["evidenceId"] = "f" * 64
    with pytest.raises(EdgeGatewayClientError) as captured:
        EdgeGatewayClient(FakeTransport([response])).execute(request, bearer_token="token")
    assert captured.value.code == "evidence_id_mismatch"


def test_receipt_no_puede_cambiar_request_id_aunque_recalcule_evidence_id() -> None:
    request = execution()
    response = completed_response(request, request_id="request-attacker")
    with pytest.raises(EdgeGatewayClientError) as captured:
        EdgeGatewayClient(FakeTransport([response])).execute(request, bearer_token="token")
    assert captured.value.code == "receipt_request_id_context_mismatch"


def test_run_id_debe_reconciliar_con_run_id_y_attempt_del_oidc() -> None:
    request = execution()
    response = completed_response(request, github_run_id="999999")
    with pytest.raises(EdgeGatewayClientError) as captured:
        EdgeGatewayClient(FakeTransport([response])).execute(request, bearer_token="token")
    assert captured.value.code == "receipt_github_run_fence_mismatch"


def test_receipt_extra_field_falla_cerrado() -> None:
    request = execution()
    response = completed_response(request)
    response["receiptPayload"]["attacker"] = "ignored"  # type: ignore[index]
    with pytest.raises(EdgeGatewayClientError) as captured:
        EdgeGatewayClient(FakeTransport([response])).execute(request, bearer_token="token")
    assert captured.value.code == "receipt_payload_shape_invalid"


def test_envelope_extra_field_falla_cerrado() -> None:
    request = execution()
    response = completed_response(request)
    response["debug"] = "should-not-be-accepted"
    with pytest.raises(EdgeGatewayClientError) as captured:
        EdgeGatewayClient(FakeTransport([response])).execute(request, bearer_token="token")
    assert captured.value.code == "evidence_response_shape_invalid"


def test_wait_y_deny_nunca_crean_evidencia() -> None:
    request = execution()
    transport = FakeTransport(
        [
            {
                "ok": True,
                "decision": "WAIT",
                "reason": "pacing_interval",
                "notBeforeMs": 2_000_000_001_500,
                "inFlightReservationId": None,
            },
            {"ok": True, "decision": "DENY", "reason": "authorization_rejected"},
        ]
    )
    client = EdgeGatewayClient(transport)
    waiting = client.execute(request, bearer_token="token")
    denied = client.execute(request, bearer_token="token")
    assert isinstance(waiting, EdgeGatewayWait)
    assert waiting.production_authority is False
    assert isinstance(denied, EdgeGatewayDenied)
    assert denied.production_authority is False


def test_error_del_worker_se_propaga_como_codigo_fail_closed() -> None:
    request = execution()
    client = EdgeGatewayClient(FakeTransport([{"ok": False, "error": "jwt_signature_invalid"}]))
    with pytest.raises(EdgeGatewayClientError) as captured:
        client.execute(request, bearer_token="token")
    assert captured.value.code == "gateway_jwt_signature_invalid"


def test_initialize_construye_wire_contract_y_reconcilia_presupuesto() -> None:
    authorization = EdgeAuthorizationRequest(
        authorization_id="auth-edge-001",
        run_id=RUN_ID,
        approved_commit_sha=SHA,
        created_at_ms=2_000_000_000_000,
        expires_at_ms=2_000_000_900_000,
        max_requests=12,
    )
    response = {
        "ok": True,
        "decision": "INITIALIZED",
        "authorization": {
            "authorizationId": authorization.authorization_id,
            "runId": authorization.run_id,
            "state": "active",
            "maxRequests": 12,
            "requestsUsed": 0,
            "remainingRequests": 12,
            "minStartIntervalMs": 1500,
            "lastPhysicalStartMs": None,
            "terminalAtMs": None,
            "terminalReason": None,
            "reservationCounts": {"reserved": 0, "completed": 0, "failed": 0},
        },
    }
    transport = FakeTransport([response])
    result = EdgeGatewayClient(transport).initialize(authorization, bearer_token="token")
    assert result.authorization_id == authorization.authorization_id
    assert result.production_authority is False
    assert transport.calls == [(INITIALIZE_PATH, "token", authorization.wire_payload())]


def test_no_hay_transporte_implicito_y_run_id_sintetico_es_rechazado() -> None:
    with pytest.raises(EdgeGatewayClientError) as transport_error:
        EdgeGatewayClient(None)  # type: ignore[arg-type]
    assert transport_error.value.code == "transport_invalid"

    with pytest.raises(EdgeGatewayClientError) as run_error:
        context(run_id="run-001")
    assert run_error.value.code == "run_id_invalid"
