from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from precios_supermercados.edge_structural_gateway_client import (
    StructuralEdgeExecutionRequest,
    StructuralEdgeGatewayClient,
    StructuralEdgeGatewayClientError,
    StructuralEdgeRequestContext,
)
from precios_supermercados.la_colonia_edge_structural_request import (
    build_structural_discovery_url,
    validate_la_colonia_structural_request,
)
from precios_supermercados.scrapers.la_colonia_sps_facet_context import RequestContextPlacement
from precios_supermercados.structural_location_context import StructuralEdgeLocationContext
from precios_supermercados.structural_provenance import (
    SignedStructuralReceipt,
    StructuralReceiptPayload,
)

RAW_REGION = "opaque-sps-context-v2"
CONTEXT_FP = hashlib.sha256(json.dumps(RAW_REGION, separators=(",", ":")).encode()).hexdigest()
WIRE_FP = "9" * 64
EVIDENCE = "location_binding_radiography:sha256:" + "8" * 64
SOURCE_KEY = "request:regionid:sha256:" + CONTEXT_FP


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _location() -> StructuralEdgeLocationContext:
    return StructuralEdgeLocationContext(
        location_id="la_colonia_sps",
        binding_source_key=SOURCE_KEY,
        binding_evidence=EVIDENCE,
        context_fingerprint=CONTEXT_FP,
        placement=RequestContextPlacement.HEADER,
        wire_key="X-VTEX-Region",
        value_path=(),
        wire_request_fingerprint=WIRE_FP,
        _raw_value=RAW_REGION,
    )


def _request(*, location=True) -> StructuralEdgeExecutionRequest:
    url = build_structural_discovery_url("root_total")
    validated = validate_la_colonia_structural_request(url)
    return StructuralEdgeExecutionRequest(
        origin_url=url,
        context=StructuralEdgeRequestContext(
            authorization_id="auth-context-v2",
            run_id="32590000000:1",
            approved_commit_sha="a" * 40,
            reservation_id="reservation-context-v2",
            request_id="request-context-v2",
            request_digest=validated.canonical_request_sha256,
            nonce="nonce-context-v2",
            request_kind="root_total",
        ),
        location_context=_location() if location else None,
    )


def _raw_body() -> bytes:
    return b'{"data":{"productSearch":{"recordsFiltered":27}}}'


def _payload(request: StructuralEdgeExecutionRequest, *, with_location: bool) -> StructuralReceiptPayload:
    raw = _raw_body()
    validated = validate_la_colonia_structural_request(request.origin_url)
    kwargs = dict(
        run_id=request.context.run_id,
        request_kind="root_total",
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
        raw_response_sha256=hashlib.sha256(raw).hexdigest(),
        response_status=200,
        response_body_bytes=len(raw),
        physical_started_at_utc=datetime(2026, 8, 24, 2, 5, 0, tzinfo=timezone.utc),
        response_completed_at_utc=datetime(2026, 8, 24, 2, 5, 0, 100_000, tzinfo=timezone.utc),
        github_repository="jchernandez-portfolio/precios-supermercados-sps",
        github_repository_id="1282475205",
        github_ref="refs/heads/main",
        github_workflow_ref="repo/workflow@refs/heads/main",
        github_environment="la-colonia-live",
        github_run_id="32590000000",
        github_run_attempt=1,
        oidc_subject="repo:jchernandez-portfolio/precios-supermercados-sps:environment:la-colonia-live",
        oidc_jti="jti-context-v2",
        collector_provider="cloudflare_workers",
        collector_principal="cloudflare-worker:precios-sps-provenance",
        collector_execution="execution-context-v2",
        collector_release_id="release-context-v2",
        collector_code_sha256="d" * 64,
        signing_algorithm="Ed25519",
        signing_key_id="cloudflare-ed25519-v1",
        nonce=request.context.nonce,
    )
    if with_location:
        kwargs.update(
            schema_version="2",
            location_id="la_colonia_sps",
            binding_source_key=SOURCE_KEY,
            binding_evidence=EVIDENCE,
            context_fingerprint=CONTEXT_FP,
            context_placement="header",
            context_wire_key="X-VTEX-Region",
            context_value_path=(),
            wire_request_fingerprint=WIRE_FP,
        )
    return StructuralReceiptPayload(**kwargs)


class _Transport:
    def __init__(self, response):
        self.response = response
        self.payload = None

    def post_json(self, _path, *, bearer_token, payload):
        assert bearer_token == "oidc-token"
        self.payload = payload
        return self.response


def _response(request: StructuralEdgeExecutionRequest, *, with_location: bool):
    payload = _payload(request, with_location=with_location)
    key = Ed25519PrivateKey.generate()
    unsigned = SignedStructuralReceipt(payload=payload, signature_b64url=_b64url(b"x" * 64))
    signature = _b64url(key.sign(unsigned.signing_bytes()))
    receipt = SignedStructuralReceipt(payload=payload, signature_b64url=signature)
    return {
        "ok": True,
        "decision": "ORIGIN_COMPLETED",
        "replayed": False,
        "responseStatus": 200,
        "rawBodyB64Url": _b64url(_raw_body()),
        "receiptPayload": payload.canonical_dict(),
        "signatureB64Url": signature,
        "signingKeyId": payload.signing_key_id,
        "evidenceId": receipt.digest,
    }


def test_contexto_raw_solo_sale_en_wire_hacia_collector() -> None:
    context = _location()
    assert RAW_REGION not in repr(context)
    assert RAW_REGION not in json.dumps(context.public_dict(), sort_keys=True)
    assert context.wire_dict()["rawValue"] == RAW_REGION
    assert context.public_dict()["raw_values_exposed"] is False


def test_request_contextual_exige_receipt_v2_con_misma_evidencia() -> None:
    request = _request()
    transport = _Transport(_response(request, with_location=True))
    result = StructuralEdgeGatewayClient(transport).execute(request, bearer_token="oidc-token")

    assert result.receipt.payload.location_context_bound is True
    assert result.receipt.payload.location_id == "la_colonia_sps"
    assert result.receipt.payload.context_fingerprint == CONTEXT_FP
    assert result.receipt.payload.wire_request_fingerprint == WIRE_FP
    assert transport.payload["locationContext"]["rawValue"] == RAW_REGION
    serialized_receipt = json.dumps(result.receipt.payload.canonical_dict(), sort_keys=True)
    assert RAW_REGION not in serialized_receipt


def test_request_contextual_rechaza_receipt_legacy_aunque_el_body_sea_valido() -> None:
    request = _request()
    with pytest.raises(StructuralEdgeGatewayClientError) as captured:
        StructuralEdgeGatewayClient(_Transport(_response(request, with_location=False))).execute(
            request,
            bearer_token="oidc-token",
        )
    assert captured.value.code == "completed_location_context_missing"


def test_request_legacy_rechaza_receipt_contextual_no_solicitado() -> None:
    request = _request(location=False)
    with pytest.raises(StructuralEdgeGatewayClientError) as captured:
        StructuralEdgeGatewayClient(_Transport(_response(request, with_location=True))).execute(
            request,
            bearer_token="oidc-token",
        )
    assert captured.value.code == "completed_unrequested_location_context"


def test_receipt_v2_no_permite_binding_source_con_otro_fingerprint() -> None:
    request = _request()
    kwargs = _payload(request, with_location=True).canonical_dict()
    kwargs["physical_started_at_utc"] = datetime(2026, 8, 24, 2, 5, 0, tzinfo=timezone.utc)
    kwargs["response_completed_at_utc"] = datetime(2026, 8, 24, 2, 5, 0, 100_000, tzinfo=timezone.utc)
    kwargs["context_value_path"] = ()
    kwargs["binding_source_key"] = "request:regionid:sha256:" + "0" * 64
    with pytest.raises(Exception, match="structural_receipt_binding_context_fingerprint_mismatch"):
        StructuralReceiptPayload(**kwargs)
