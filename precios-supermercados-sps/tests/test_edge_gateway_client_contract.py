from __future__ import annotations

import pytest

from precios_supermercados.edge_gateway_client import (
    EdgeAuthorizationRequest,
    EdgeGatewayClient,
    EdgeGatewayClientError,
)
from test_edge_gateway_client import RUN_ID, SHA, FakeTransport


def authorization() -> EdgeAuthorizationRequest:
    return EdgeAuthorizationRequest(
        authorization_id="auth-edge-001",
        run_id=RUN_ID,
        approved_commit_sha=SHA,
        created_at_ms=2_000_000_000_000,
        expires_at_ms=2_000_000_900_000,
        max_requests=12,
    )


def summary_response(**overrides: object) -> dict[str, object]:
    auth = authorization()
    summary: dict[str, object] = {
        "authorizationId": auth.authorization_id,
        "runId": auth.run_id,
        "state": "active",
        "maxRequests": 12,
        "requestsUsed": 0,
        "remainingRequests": 12,
        "minStartIntervalMs": 1500,
        "lastPhysicalStartMs": None,
        "terminalAtMs": None,
        "terminalReason": None,
        "reservationCounts": {"reserved": 0, "completed": 0, "failed": 0},
    }
    summary.update(overrides)
    return {"ok": True, "decision": "INITIALIZED", "authorization": summary}


def test_initialize_no_acepta_presupuesto_inconsistente() -> None:
    auth = authorization()
    client = EdgeGatewayClient(FakeTransport([summary_response(remainingRequests=11)]))
    with pytest.raises(EdgeGatewayClientError) as captured:
        client.initialize(auth, bearer_token="token")
    assert captured.value.code == "initialize_budget_accounting_invalid"


def test_initialize_no_acepta_contexto_de_otra_autorizacion() -> None:
    auth = authorization()
    client = EdgeGatewayClient(FakeTransport([summary_response(authorizationId="auth-other")]))
    with pytest.raises(EdgeGatewayClientError) as captured:
        client.initialize(auth, bearer_token="token")
    assert captured.value.code == "initialize_context_mismatch"
