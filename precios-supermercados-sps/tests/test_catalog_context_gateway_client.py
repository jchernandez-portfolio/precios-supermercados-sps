from __future__ import annotations

import base64
import hashlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from precios_supermercados.catalog_context_gateway_client import (
    CATALOG_CONTEXT_EXECUTE_PATH,
    CatalogContextGatewayClient,
    CatalogContextGatewayClientError,
    ContextBoundCatalogExecutionRequest,
)
from precios_supermercados.catalog_location_context import (
    catalog_edge_location_context_for_request,
)
from precios_supermercados.edge_gateway_client import (
    EdgeGatewayDenied,
    EdgeGatewayEvidence,
    EdgeGatewayWait,
)
from precios_supermercados.edge_provenance import canonical_json_bytes

TESTS = Path(__file__).parent


def _helper(filename: str, module_name: str) -> ModuleType:
    path = TESTS / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


EDGE_HELPER = _helper(
    "test_edge_gateway_client.py",
    "precios_sps_edge_gateway_helper_for_catalog_context",
)
LOCATION_HELPER = _helper(
    "test_catalog_location_context.py",
    "precios_sps_location_helper_for_catalog_gateway",
)


def _request() -> ContextBoundCatalogExecutionRequest:
    proof = LOCATION_HELPER._proof_for(
        LOCATION_HELPER.PLAN_HELPER.FakeRequest(
            headers={
                "X-VTEX-Region": LOCATION_HELPER.PLAN_HELPER.RAW_REGION,
            }
        )
    )
    origin = LOCATION_HELPER._catalog_request()
    context = EDGE_HELPER.context(request_digest=origin.canonical_request_sha256)
    location = catalog_edge_location_context_for_request(proof, origin)
    return ContextBoundCatalogExecutionRequest(
        origin_url=origin.source_url,
        context=context,
        location_context=location,
    )


def _payload(request: ContextBoundCatalogExecutionRequest, **overrides: object) -> dict[str, object]:
    payload = EDGE_HELPER.receipt_payload(request)  # type: ignore[arg-type]
    payload["schema_version"] = "3"
    public = request.location_context.public_dict()
    payload.update(
        {
            "location_id": public["location_id"],
            "binding_source_key": public["binding_source_key"],
            "binding_evidence": public["binding_evidence"],
            "context_fingerprint": public["context_fingerprint"],
            "context_placement": public["placement"],
            "context_wire_key": public["wire_key"],
            "context_value_path": public["value_path"],
            "wire_request_fingerprint": public["wire_request_fingerprint"],
        }
    )
    payload.update(overrides)
    return payload


def _response(request: ContextBoundCatalogExecutionRequest, **overrides: object) -> dict[str, object]:
    payload = _payload(request, **overrides)
    evidence_id = hashlib.sha256(
        canonical_json_bytes(payload) + b"\0" + EDGE_HELPER.SIGNATURE.encode("ascii")
    ).hexdigest()
    return {
        "ok": True,
        "decision": "ORIGIN_COMPLETED",
        "replayed": False,
        "responseStatus": 200,
        "rawBodyB64Url": EDGE_HELPER.b64url(EDGE_HELPER.RAW),
        "receiptPayload": payload,
        "signatureB64Url": EDGE_HELPER.SIGNATURE,
        "signingKeyId": "edge-signing-key-001",
        "evidenceId": evidence_id,
    }


def test_completed_exige_v3_y_contexto_sps_exactamente() -> None:
    request = _request()
    transport = EDGE_HELPER.FakeTransport([_response(request)])

    result = CatalogContextGatewayClient(transport).execute(
        request,
        bearer_token="oidc.synthetic.token",
    )

    assert isinstance(result, EdgeGatewayEvidence)
    assert result.raw_body == EDGE_HELPER.RAW
    assert result.receipt.payload.schema_version == "3"
    assert result.receipt.payload.location_context_bound is True
    assert result.receipt.payload.location_id == "la_colonia_sps"
    assert (
        result.receipt.payload.wire_request_fingerprint
        == request.location_context.wire_request_fingerprint
    )
    assert result.production_authority is False
    assert transport.calls[0][0] == CATALOG_CONTEXT_EXECUTE_PATH
    assert transport.calls[0][2] == request.wire_payload()
    assert (
        transport.calls[0][2]["locationContext"]["rawValue"]
        == LOCATION_HELPER.PLAN_HELPER.RAW_REGION
    )


def test_receipt_v2_legacy_es_downgrade_y_falla_cerrado() -> None:
    request = _request()
    response = _response(request)
    payload = response["receiptPayload"]
    assert isinstance(payload, dict)
    payload["schema_version"] = "2"
    for key in (
        "location_id",
        "binding_source_key",
        "binding_evidence",
        "context_fingerprint",
        "context_placement",
        "context_wire_key",
        "context_value_path",
        "wire_request_fingerprint",
    ):
        payload.pop(key)

    with pytest.raises(CatalogContextGatewayClientError) as captured:
        CatalogContextGatewayClient(EDGE_HELPER.FakeTransport([response])).execute(
            request,
            bearer_token="token",
        )
    assert captured.value.code == "catalog_receipt_catalog_context_receipt_shape_invalid"


def test_location_id_no_puede_cambiar_aunque_worker_recalcule_evidence_id() -> None:
    request = _request()
    response = _response(request, location_id="la_colonia_tgu")

    with pytest.raises(CatalogContextGatewayClientError) as captured:
        CatalogContextGatewayClient(EDGE_HELPER.FakeTransport([response])).execute(
            request,
            bearer_token="token",
        )
    assert captured.value.code == "catalog_receipt_catalog_context_location_id_invalid"


def test_wire_fingerprint_debe_coincidir_con_pagina_exacta() -> None:
    request = _request()
    response = _response(request, wire_request_fingerprint="f" * 64)

    with pytest.raises(CatalogContextGatewayClientError) as captured:
        CatalogContextGatewayClient(EDGE_HELPER.FakeTransport([response])).execute(
            request,
            bearer_token="token",
        )
    assert captured.value.code == "catalog_receipt_wire_request_fingerprint_mismatch"


def test_contexto_publico_no_puede_omitir_evidence_binding() -> None:
    request = _request()
    response = _response(request)
    payload = response["receiptPayload"]
    assert isinstance(payload, dict)
    payload.pop("binding_evidence")

    with pytest.raises(CatalogContextGatewayClientError) as captured:
        CatalogContextGatewayClient(EDGE_HELPER.FakeTransport([response])).execute(
            request,
            bearer_token="token",
        )
    assert captured.value.code == "catalog_receipt_catalog_context_receipt_shape_invalid"


def test_wait_y_deny_no_crean_evidencia_contextual() -> None:
    request = _request()
    transport = EDGE_HELPER.FakeTransport(
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
    client = CatalogContextGatewayClient(transport)

    waiting = client.execute(request, bearer_token="token")
    denied = client.execute(request, bearer_token="token")

    assert isinstance(waiting, EdgeGatewayWait)
    assert isinstance(denied, EdgeGatewayDenied)
    assert waiting.production_authority is False
    assert denied.production_authority is False


def test_raw_region_no_aparece_en_receipt_publico() -> None:
    request = _request()
    payload = _payload(request)
    rendered = str(payload)
    assert LOCATION_HELPER.PLAN_HELPER.RAW_REGION not in rendered
    assert "rawValue" not in payload
    assert "raw_value" not in payload
