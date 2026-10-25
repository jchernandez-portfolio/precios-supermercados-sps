from __future__ import annotations

import pytest

from precios_supermercados.edge_gateway_client import (
    MAX_AUTHORIZATION_LIFETIME_MS,
    MAX_REQUESTS,
    EdgeAuthorizationRequest,
    EdgeGatewayClientError,
)


def test_authorization_rechaza_vida_y_presupuesto_sobre_limites_edge() -> None:
    base = 2_000_000_000_000
    with pytest.raises(EdgeGatewayClientError) as lifetime_error:
        EdgeAuthorizationRequest(
            authorization_id="auth-edge-001",
            run_id="32509541437:1",
            approved_commit_sha="a" * 40,
            created_at_ms=base,
            expires_at_ms=base + MAX_AUTHORIZATION_LIFETIME_MS + 1,
            max_requests=1,
        )
    assert lifetime_error.value.code == "authorization_lifetime_above_limit"

    with pytest.raises(EdgeGatewayClientError) as budget_error:
        EdgeAuthorizationRequest(
            authorization_id="auth-edge-001",
            run_id="32509541437:1",
            approved_commit_sha="a" * 40,
            created_at_ms=base,
            expires_at_ms=base + 1000,
            max_requests=MAX_REQUESTS + 1,
        )
    assert budget_error.value.code == "max_requests_invalid"
