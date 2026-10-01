from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import pytest

from precios_supermercados.cloudflare_observability_verifier import (
    CloudflareObservabilityVerifierClient,
    CloudflareObservabilityVerifierError,
)
from precios_supermercados.cloudflare_trace_evidence import ORIGIN_EXECUTION_SPAN_NAME
from precios_supermercados.edge_crypto_page import CryptographicallyVerifiedEdgeCatalogPage
from precios_supermercados.edge_provenance import EdgeReceiptPayload, SignedEdgeReceipt
from precios_supermercados.edge_receipt_crypto import CryptographicallyVerifiedEdgeReceipt
from precios_supermercados.la_colonia_edge_body import validate_la_colonia_edge_body
from precios_supermercados.la_colonia_edge_request import validate_la_colonia_edge_request
from precios_supermercados.scrapers.la_colonia_graphql import build_product_search_url

ACCOUNT_ID = "0123456789abcdef0123456789abcdef"
TOKEN = "cloudflare-observability-token-test"
SERVICE = "precios-sps-provenance"
TRACE_ID = "trace-verifier-001"
CUSTOM_SPAN_ID = "custom-verifier-001"
VERSION_ID = "cf-version-trace-001"
INVOCATION_ID = "invocation-verifier-001"
START_MS = 1_787_338_800_000


def _signature() -> str:
    return base64.urlsafe_b64encode(bytes(range(64))).decode("ascii").rstrip("=")


def _page() -> CryptographicallyVerifiedEdgeCatalogPage:
    url = build_product_search_url(
        page=1,
        page_size=2,
        query="supermercado",
        category_map="category-1",
        order_by="OrderByNameASC",
    )
    request = validate_la_colonia_edge_request(url)
    raw = json.dumps(
        {"data": {"productSearch": {"recordsFiltered": 2, "products": [{}, {}]}}},
        separators=(",", ":"),
    ).encode()
    body = validate_la_colonia_edge_body(raw, request)
    payload = EdgeReceiptPayload(
        run_id="32519345559:1",
        request_id="request-verifier-001",
        reservation_id="reservation-verifier-001",
        authorization_id="authorization-verifier-001",
        approved_commit_sha="a" * 40,
        request_digest=request.canonical_request_sha256,
        traversal_id="traversal-verifier-001",
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
        physical_started_at_utc=datetime(2026, 8, 21, 19, 0, 0, 100_000, tzinfo=timezone.utc),
        response_completed_at_utc=datetime(2026, 8, 21, 19, 0, 0, 900_000, tzinfo=timezone.utc),
        github_repository="jchernandez-portfolio/precios-supermercados-sps",
        github_repository_id="1282475205",
        github_ref="refs/heads/main",
        github_workflow_ref="jchernandez-portfolio/precios-supermercados-sps/.github/workflows/precios-supermercados-sps-la-colonia-live.yml@refs/heads/main",
        github_environment="la-colonia-live",
        github_run_id="32519345559",
        github_run_attempt=1,
        oidc_subject="repo:jchernandez-portfolio/precios-supermercados-sps:environment:la-colonia-live",
        oidc_jti="oidc-jti-verifier-001",
        collector_provider="cloudflare_workers",
        collector_principal="cloudflare-worker:precios-sps-provenance",
        collector_execution="execution-verifier-001",
        collector_release_id=VERSION_ID,
        collector_code_sha256="d" * 64,
        signing_algorithm="Ed25519",
        signing_key_id="edge-signing-key-001",
        nonce="nonce-verifier-001",
    )
    receipt = SignedEdgeReceipt(payload=payload, signature_b64url=_signature())
    verified = CryptographicallyVerifiedEdgeReceipt(
        receipt=receipt,
        signing_key_id=payload.signing_key_id,
        public_key_spki_sha256="e" * 64,
        receipt_digest=receipt.digest,
    )
    return CryptographicallyVerifiedEdgeCatalogPage(
        request=request,
        body=body,
        verified_receipt=verified,
        worker_evidence_id="f" * 64,
        replayed=False,
    )


def _workers() -> dict[str, object]:
    return {
        "scriptName": SERVICE,
        "scriptVersion": {"id": VERSION_ID},
        "truncated": False,
    }


def _base_event(*, span_id: str, span_name: str, start_ms: int, end_ms: int) -> dict[str, object]:
    return {
        "$metadata": {
            "service": SERVICE,
            "traceId": TRACE_ID,
            "spanId": span_id,
            "spanName": span_name,
            "startTime": start_ms,
            "endTime": end_ms,
        },
        "$workers": _workers(),
        "dataset": "cloudflare-workers",
        "source": {},
        "timestamp": start_ms,
        "cloud.provider": "cloudflare",
        "cloud.platform": "cloudflare.workers",
        "faas.invocation_id": INVOCATION_ID,
        "service.name": SERVICE,
        "cloudflare.script_version.id": VERSION_ID,
    }


def _custom_event(page: CryptographicallyVerifiedEdgeCatalogPage) -> dict[str, object]:
    payload = page.verified_receipt.receipt.payload
    event = _base_event(
        span_id=CUSTOM_SPAN_ID,
        span_name=ORIGIN_EXECUTION_SPAN_NAME,
        start_ms=START_MS,
        end_ms=START_MS + 1000,
    )
    event.update(
        {
            "precios.trace_contract_version": "1",
            "precios.collector_provider": "cloudflare_workers",
            "precios.authorization_id": payload.authorization_id,
            "precios.run_id": payload.run_id,
            "precios.approved_commit_sha": payload.approved_commit_sha,
            "precios.reservation_id": payload.reservation_id,
            "precios.request_id": payload.request_id,
            "precios.request_digest": payload.request_digest,
            "precios.traversal_role": payload.traversal_role,
            "precios.traversal_id": payload.traversal_id,
            "precios.partition_id": payload.partition_id,
        }
    )
    return event


def _fetch_event(page: CryptographicallyVerifiedEdgeCatalogPage) -> dict[str, object]:
    payload = page.verified_receipt.receipt.payload
    event = _base_event(
        span_id="fetch-verifier-001",
        span_name="fetch",
        start_ms=START_MS + 150,
        end_ms=START_MS + 500,
    )
    metadata = event["$metadata"]
    assert isinstance(metadata, dict)
    metadata["parentSpanId"] = CUSTOM_SPAN_ID
    event.update(
        {
            "url.full": page.source_url,
            "http.request.method": "GET",
            "http.response.status_code": 200,
            "http.response.body.size": payload.response_body_bytes,
        }
    )
    return event


def _response(events: list[dict[str, object]]) -> dict[str, object]:
    return {
        "success": True,
        "errors": [],
        "result": {"events": {"events": events, "count": len(events)}},
    }


class FakeTransport:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def post_json(self, path: str, *, bearer_token: str, payload: Any) -> Any:
        self.calls.append({"path": path, "bearer_token": bearer_token, "payload": payload})
        if not self.responses:
            raise AssertionError("unexpected transport call")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _transport_for(page: CryptographicallyVerifiedEdgeCatalogPage) -> FakeTransport:
    return FakeTransport(
        [
            _response([_custom_event(page)]),
            _response([_custom_event(page), _fetch_event(page)]),
        ]
    )


def test_reconcile_page_hace_discovery_detail_y_reconcilia_sin_autoridad_productiva() -> None:
    page = _page()
    transport = _transport_for(page)
    client = CloudflareObservabilityVerifierClient(ACCOUNT_ID, transport)

    result = client.reconcile_page(page, bearer_token=TOKEN)

    assert result.platform_evidence_reconciled is True
    assert result.production_authority is False
    assert result.page is page
    assert result.trace_evidence.trace_id == TRACE_ID
    assert len(transport.calls) == 2
    assert {call["path"] for call in transport.calls} == {
        f"/accounts/{ACCOUNT_ID}/workers/observability/telemetry/query"
    }
    assert all(call["bearer_token"] == TOKEN for call in transport.calls)

    discovery = transport.calls[0]["payload"]
    detail = transport.calls[1]["payload"]
    assert isinstance(discovery, dict)
    assert isinstance(detail, dict)
    assert discovery["timeframe"] == {
        "from": START_MS - 30_000 + 100,
        "to": START_MS + 30_000 + 900,
    }
    detail_filters = detail["parameters"]["filters"]  # type: ignore[index]
    assert any(item["key"] == "$metadata.traceId" and item["value"] == TRACE_ID for item in detail_filters)


def test_token_invalido_falla_antes_del_transporte() -> None:
    page = _page()
    transport = _transport_for(page)
    client = CloudflareObservabilityVerifierClient(ACCOUNT_ID, transport)
    with pytest.raises(CloudflareObservabilityVerifierError) as captured:
        client.reconcile_page(page, bearer_token="token con espacios")
    assert captured.value.code == "bearer_token_invalid"
    assert transport.calls == []


def test_account_id_invalido_falla_en_constructor() -> None:
    with pytest.raises(CloudflareObservabilityVerifierError) as captured:
        CloudflareObservabilityVerifierClient("not-an-account", FakeTransport([]))
    assert captured.value.code == "account_id_invalid"


def test_no_acepta_transporte_implicito_o_invalido() -> None:
    with pytest.raises(CloudflareObservabilityVerifierError) as captured:
        CloudflareObservabilityVerifierClient(ACCOUNT_ID, object())  # type: ignore[arg-type]
    assert captured.value.code == "transport_invalid"


def test_error_de_transporte_se_cierra_sin_retry_oculto() -> None:
    page = _page()
    transport = FakeTransport([RuntimeError("network unavailable")])
    client = CloudflareObservabilityVerifierClient(ACCOUNT_ID, transport)
    with pytest.raises(CloudflareObservabilityVerifierError) as captured:
        client.reconcile_page(page, bearer_token=TOKEN)
    assert captured.value.code == "observability_transport_error"
    assert len(transport.calls) == 1


def test_discovery_vacio_no_inventa_evidencia_y_no_hace_detail() -> None:
    page = _page()
    transport = FakeTransport([_response([])])
    client = CloudflareObservabilityVerifierClient(ACCOUNT_ID, transport)
    with pytest.raises(CloudflareObservabilityVerifierError) as captured:
        client.reconcile_page(page, bearer_token=TOKEN)
    assert captured.value.code == "reconciliation_matching_trace_missing"
    assert len(transport.calls) == 1


def test_contexto_custom_distinto_del_receipt_no_cuenta_como_evidencia() -> None:
    page = _page()
    custom = _custom_event(page)
    custom["precios.request_id"] = "request-other"
    transport = FakeTransport(
        [
            _response([_custom_event(page)]),
            _response([custom, _fetch_event(page)]),
        ]
    )
    client = CloudflareObservabilityVerifierClient(ACCOUNT_ID, transport)
    with pytest.raises(CloudflareObservabilityVerifierError) as captured:
        client.reconcile_page(page, bearer_token=TOKEN)
    assert captured.value.code == "reconciliation_matching_trace_missing"


def test_detail_mezclado_con_otro_trace_falla_antes_de_reconciliar() -> None:
    page = _page()
    other = _fetch_event(page)
    metadata = other["$metadata"]
    assert isinstance(metadata, dict)
    metadata["traceId"] = "trace-attacker"
    transport = FakeTransport(
        [
            _response([_custom_event(page)]),
            _response([_custom_event(page), other]),
        ]
    )
    client = CloudflareObservabilityVerifierClient(ACCOUNT_ID, transport)
    with pytest.raises(CloudflareObservabilityVerifierError) as captured:
        client.reconcile_page(page, bearer_token=TOKEN)
    assert captured.value.code == "detail_detail_trace_id_mismatch"


def test_token_no_se_almacena_en_estado_del_cliente() -> None:
    page = _page()
    transport = _transport_for(page)
    client = CloudflareObservabilityVerifierClient(ACCOUNT_ID, transport)
    client.reconcile_page(page, bearer_token=TOKEN)
    assert TOKEN not in repr(vars(client))
