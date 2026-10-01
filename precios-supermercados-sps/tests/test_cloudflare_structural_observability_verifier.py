from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import pytest

from precios_supermercados.cloudflare_structural_observability_verifier import (
    CloudflareStructuralObservabilityVerifierClient,
    CloudflareStructuralObservabilityVerifierError,
)
from precios_supermercados.cloudflare_structural_trace_evidence import (
    STRUCTURAL_EXECUTION_SPAN_NAME,
)
from precios_supermercados.edge_structural_observation import (
    CryptographicallyVerifiedStructuralObservation,
)
from precios_supermercados.la_colonia_edge_structural_body import (
    validate_la_colonia_structural_body,
)
from precios_supermercados.la_colonia_edge_structural_request import (
    build_structural_discovery_url,
    validate_la_colonia_structural_request,
)
from precios_supermercados.structural_provenance import (
    SignedStructuralReceipt,
    StructuralReceiptPayload,
)
from precios_supermercados.structural_receipt_crypto import VerifiedStructuralReceipt

ACCOUNT_ID = "0123456789abcdef0123456789abcdef"
TOKEN = "cloudflare-structural-observability-token-test"
SERVICE = "precios-sps-provenance"
TRACE_ID = "trace-structural-verifier-001"
CUSTOM_SPAN_ID = "custom-structural-verifier-001"
VERSION_ID = "cf-version-structural-001"
INVOCATION_ID = "invocation-structural-verifier-001"
START = datetime(2026, 8, 21, 20, 30, 0, tzinfo=timezone.utc)
START_MS = int(START.timestamp() * 1000)


def _signature() -> str:
    return base64.urlsafe_b64encode(bytes(range(64))).decode("ascii").rstrip("=")


def _raw_body(kind: str = "root_total") -> bytes:
    if kind == "root_total":
        value = {"data": {"productSearch": {"recordsFiltered": 7}}}
    else:
        value = {
            "data": {
                "productSearch": {"recordsFiltered": 7},
                "facets": {"sampling": False, "facets": []},
            }
        }
    return json.dumps(value, separators=(",", ":")).encode()


def _observation(kind: str = "root_total") -> CryptographicallyVerifiedStructuralObservation:
    source_url = build_structural_discovery_url(kind)
    request = validate_la_colonia_structural_request(source_url)
    raw = _raw_body(kind)
    body = validate_la_colonia_structural_body(raw, request)
    payload = StructuralReceiptPayload(
        run_id="32525000000:1",
        request_kind=kind,  # type: ignore[arg-type]
        request_id=f"request-{kind}",
        reservation_id=f"reservation-{kind}",
        authorization_id="authorization-structural-verifier",
        approved_commit_sha="a" * 40,
        request_digest=request.canonical_request_sha256,
        canonical_request_sha256=request.canonical_request_sha256,
        graphql_query_sha256=request.graphql_query_sha256,
        http_method="GET",
        target_scheme="https",
        target_host="www.lacolonia.com",
        target_path="/_v/segment/graphql/v1",
        raw_response_sha256=hashlib.sha256(raw).hexdigest(),
        response_status=200,
        response_body_bytes=len(raw),
        physical_started_at_utc=datetime(2026, 8, 21, 20, 30, 0, 100_000, tzinfo=timezone.utc),
        response_completed_at_utc=datetime(2026, 8, 21, 20, 30, 0, 900_000, tzinfo=timezone.utc),
        github_repository="jchernandez-portfolio/precios-supermercados-sps",
        github_repository_id="1282475205",
        github_ref="refs/heads/main",
        github_workflow_ref="jchernandez-portfolio/precios-supermercados-sps/.github/workflows/precios-supermercados-sps-la-colonia-live.yml@refs/heads/main",
        github_environment="la-colonia-live",
        github_run_id="32525000000",
        github_run_attempt=1,
        oidc_subject="repo:jchernandez-portfolio/precios-supermercados-sps:environment:la-colonia-live",
        oidc_jti=f"oidc-jti-{kind}",
        collector_provider="cloudflare_workers",
        collector_principal="cloudflare-worker:precios-sps-provenance",
        collector_execution=f"execution-{kind}",
        collector_release_id=VERSION_ID,
        collector_code_sha256="d" * 64,
        signing_algorithm="Ed25519",
        signing_key_id="cloudflare-ed25519-v1",
        nonce=f"nonce-{kind}",
    )
    signed = SignedStructuralReceipt(payload=payload, signature_b64url=_signature())
    verified = VerifiedStructuralReceipt(
        receipt=signed,
        signing_key_id=payload.signing_key_id,
        public_key_spki_sha256="e" * 64,
        receipt_digest=signed.digest,
    )
    return CryptographicallyVerifiedStructuralObservation(
        request=request,
        body=body,
        verified_receipt=verified,
        raw_body_sha256=hashlib.sha256(raw).hexdigest(),
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


def _custom_event(observation: CryptographicallyVerifiedStructuralObservation) -> dict[str, object]:
    payload = observation.verified_receipt.receipt.payload
    event = _base_event(
        span_id=CUSTOM_SPAN_ID,
        span_name=STRUCTURAL_EXECUTION_SPAN_NAME,
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
            "precios.request_kind": payload.request_kind,
        }
    )
    return event


def _fetch_event(observation: CryptographicallyVerifiedStructuralObservation) -> dict[str, object]:
    payload = observation.verified_receipt.receipt.payload
    event = _base_event(
        span_id="fetch-structural-verifier-001",
        span_name="fetch",
        start_ms=START_MS + 150,
        end_ms=START_MS + 500,
    )
    metadata = event["$metadata"]
    assert isinstance(metadata, dict)
    metadata["parentSpanId"] = CUSTOM_SPAN_ID
    event.update(
        {
            "url.full": observation.source_url,
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


def _transport_for(observation: CryptographicallyVerifiedStructuralObservation) -> FakeTransport:
    return FakeTransport(
        [
            _response([_custom_event(observation)]),
            _response([_custom_event(observation), _fetch_event(observation)]),
        ]
    )


def test_reconcile_observation_hace_discovery_detail_y_reconcilia() -> None:
    observation = _observation()
    transport = _transport_for(observation)
    client = CloudflareStructuralObservabilityVerifierClient(ACCOUNT_ID, transport)

    result = client.reconcile_observation(observation, bearer_token=TOKEN)

    assert result.platform_evidence_reconciled is True
    assert result.production_authority is False
    assert result.observation is observation
    assert result.trace_evidence.trace_id == TRACE_ID
    assert result.trace_evidence.request_kind == "root_total"
    assert len(transport.calls) == 2
    assert all(call["bearer_token"] == TOKEN for call in transport.calls)
    assert {call["path"] for call in transport.calls} == {
        f"/accounts/{ACCOUNT_ID}/workers/observability/telemetry/query"
    }

    discovery = transport.calls[0]["payload"]
    assert isinstance(discovery, dict)
    filters = discovery["parameters"]["filters"]  # type: ignore[index]
    assert any(
        item["key"] == "precios.request_kind" and item["value"] == "root_total"
        for item in filters
    )
    assert discovery["timeframe"] == {
        "from": START_MS - 30_000 + 100,
        "to": START_MS + 30_000 + 900,
    }


def test_category_tree_usa_kind_independiente() -> None:
    observation = _observation("category_tree")
    transport = _transport_for(observation)
    result = CloudflareStructuralObservabilityVerifierClient(
        ACCOUNT_ID,
        transport,
    ).reconcile_observation(observation, bearer_token=TOKEN)
    assert result.trace_evidence.request_kind == "category_tree"


def test_token_invalido_falla_antes_del_transporte() -> None:
    observation = _observation()
    transport = _transport_for(observation)
    client = CloudflareStructuralObservabilityVerifierClient(ACCOUNT_ID, transport)
    with pytest.raises(CloudflareStructuralObservabilityVerifierError) as captured:
        client.reconcile_observation(observation, bearer_token="token con espacios")
    assert captured.value.code == "bearer_token_invalid"
    assert transport.calls == []


def test_account_y_transporte_invalidos_fallan_en_constructor() -> None:
    with pytest.raises(CloudflareStructuralObservabilityVerifierError) as account:
        CloudflareStructuralObservabilityVerifierClient("not-account", FakeTransport([]))
    assert account.value.code == "account_id_invalid"

    with pytest.raises(CloudflareStructuralObservabilityVerifierError) as transport:
        CloudflareStructuralObservabilityVerifierClient(ACCOUNT_ID, object())  # type: ignore[arg-type]
    assert transport.value.code == "transport_invalid"


def test_error_de_transporte_no_hace_retry_oculto() -> None:
    observation = _observation()
    transport = FakeTransport([RuntimeError("network unavailable")])
    client = CloudflareStructuralObservabilityVerifierClient(ACCOUNT_ID, transport)
    with pytest.raises(CloudflareStructuralObservabilityVerifierError) as captured:
        client.reconcile_observation(observation, bearer_token=TOKEN)
    assert captured.value.code == "observability_transport_error"
    assert len(transport.calls) == 1


def test_discovery_vacio_no_inventa_evidencia() -> None:
    observation = _observation()
    transport = FakeTransport([_response([])])
    client = CloudflareStructuralObservabilityVerifierClient(ACCOUNT_ID, transport)
    with pytest.raises(CloudflareStructuralObservabilityVerifierError) as captured:
        client.reconcile_observation(observation, bearer_token=TOKEN)
    assert captured.value.code == "reconciliation_matching_trace_missing"
    assert len(transport.calls) == 1


def test_contexto_custom_distinto_no_reconcilia() -> None:
    observation = _observation()
    custom = _custom_event(observation)
    custom["precios.request_id"] = "request-attacker"
    transport = FakeTransport(
        [
            _response([_custom_event(observation)]),
            _response([custom, _fetch_event(observation)]),
        ]
    )
    client = CloudflareStructuralObservabilityVerifierClient(ACCOUNT_ID, transport)
    with pytest.raises(CloudflareStructuralObservabilityVerifierError) as captured:
        client.reconcile_observation(observation, bearer_token=TOKEN)
    assert captured.value.code == "reconciliation_matching_trace_missing"


def test_kind_reetiquetado_en_detail_falla_en_reconciliacion() -> None:
    observation = _observation()
    custom = _custom_event(observation)
    custom["precios.request_kind"] = "category_tree"
    transport = FakeTransport(
        [
            _response([_custom_event(observation)]),
            _response([custom, _fetch_event(observation)]),
        ]
    )
    client = CloudflareStructuralObservabilityVerifierClient(ACCOUNT_ID, transport)
    with pytest.raises(CloudflareStructuralObservabilityVerifierError) as captured:
        client.reconcile_observation(observation, bearer_token=TOKEN)
    assert captured.value.code == "reconciliation_matching_trace_missing"


def test_token_no_se_almacena_en_cliente() -> None:
    observation = _observation()
    transport = _transport_for(observation)
    client = CloudflareStructuralObservabilityVerifierClient(ACCOUNT_ID, transport)
    client.reconcile_observation(observation, bearer_token=TOKEN)
    assert TOKEN not in repr(vars(client))
