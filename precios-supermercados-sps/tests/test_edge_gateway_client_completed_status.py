from __future__ import annotations

import hashlib

import pytest

from precios_supermercados.edge_gateway_client import EdgeGatewayClient, EdgeGatewayClientError
from precios_supermercados.edge_provenance import canonical_json_bytes
from test_edge_gateway_client import SIGNATURE, FakeTransport, completed_response, execution


def test_completed_no_acepta_status_distinto_de_200_aunque_envelope_sea_consistente() -> None:
    request = execution()
    response = completed_response(request)
    response["responseStatus"] = 500
    payload = response["receiptPayload"]
    assert isinstance(payload, dict)
    payload["response_status"] = 500
    response["evidenceId"] = hashlib.sha256(
        canonical_json_bytes(payload) + b"\0" + SIGNATURE.encode("ascii")
    ).hexdigest()

    with pytest.raises(EdgeGatewayClientError) as captured:
        EdgeGatewayClient(FakeTransport([response])).execute(request, bearer_token="token")

    assert captured.value.code == "evidence_status_not_success"
