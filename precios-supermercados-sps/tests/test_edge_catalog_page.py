from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from typing import Mapping

import pytest

from precios_supermercados.edge_catalog_page import (
    EdgeCatalogPageError,
    EdgeCatalogPageFetcher,
    ValidatedEdgeCatalogPage,
)
from precios_supermercados.edge_gateway_client import (
    EdgeExecutionRequest,
    EdgeGatewayClient,
    EdgeRequestContext,
)
from precios_supermercados.edge_provenance import canonical_json_bytes
from precios_supermercados.la_colonia_edge_request import validate_la_colonia_edge_request
from precios_supermercados.scrapers.la_colonia_graphql import build_product_search_url


SHA = "a" * 40
RUN_ID = "32512282466:1"
SIGNATURE = base64.urlsafe_b64encode(bytes(range(64))).decode("ascii").rstrip("=")


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def request() -> EdgeExecutionRequest:
    url = build_product_search_url(
        page=1,
        page_size=2,
        query="supermercado",
        category_map="category-1",
        order_by="OrderByNameASC",
    )
    validated = validate_la_colonia_edge_request(url)
    return EdgeExecutionRequest(
        origin_url=url,
        context=EdgeRequestContext(
            authorization_id="auth-edge-page-001",
            run_id=RUN_ID,
            approved_commit_sha=SHA,
            reservation_id="reservation-edge-page-001",
            request_id="request-edge-page-001",
            request_digest=validated.canonical_request_sha256,
            nonce="nonce-edge-page-001",
            traversal_role="primary",
            traversal_id="traversal-primary-001",
            partition_id="root",
        ),
    )


def body(*, records_filtered: int = 2, products: list[dict[str, object]] | None = None) -> bytes:
    payload = {
        "data": {
            "productSearch": {
                "recordsFiltered": records_filtered,
                "products": products if products is not None else [{}, {}],
            }
        }
    }
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def receipt_payload(req: EdgeExecutionRequest, raw: bytes) -> dict[str, object]:
    ctx = req.context
    return {
        "approved_commit_sha": ctx.approved_commit_sha,
        "authorization_id": ctx.authorization_id,
        "canonical_request_sha256": ctx.request_digest,
        "collector_code_sha256": "c" * 64,
        "collector_execution": "execution-edge-page-001",
        "collector_principal": "cloudflare-worker:precios-sps-provenance",
        "collector_provider": "cloudflare_workers",
        "collector_release_id": "release-edge-page-001",
        "from_index": 0,
        "github_environment": "la-colonia-live",
        "github_ref": "refs/heads/main",
        "github_repository": "jchernandez-portfolio/precios-supermercados-sps",
        "github_repository_id": "1282475205",
        "github_run_attempt": 1,
        "github_run_id": "32512282466",
        "github_workflow_ref": "jchernandez-portfolio/precios-supermercados-sps/.github/workflows/precios-supermercados-sps-la-colonia-live.yml@refs/heads/main",
        "http_method": "GET",
        "nonce": ctx.nonce,
        "oidc_jti": "oidc-jti-edge-page-001",
        "oidc_subject": "repo:jchernandez-portfolio/precios-supermercados-sps:environment:la-colonia-live",
        "order_by": "OrderByNameASC",
        "partition_id": ctx.partition_id,
        "physical_started_at_utc": "2026-08-21T18:20:00.123000Z",
        "raw_response_sha256": hashlib.sha256(raw).hexdigest(),
        "request_digest": ctx.request_digest,
        "request_id": ctx.request_id,
        "reservation_id": ctx.reservation_id,
        "response_body_bytes": len(raw),
        "response_completed_at_utc": "2026-08-21T18:20:00.456000Z",
        "response_status": 200,
        "run_id": ctx.run_id,
        "schema_version": "2",
        "signing_algorithm": "Ed25519",
        "signing_key_id": "edge-signing-key-001",
        "target_host": "www.lacolonia.com",
        "target_path": "/_v/segment/graphql/v1",
        "target_scheme": "https",
        "to_index": 1,
        "traversal_id": ctx.traversal_id,
        "traversal_role": ctx.traversal_role,
    }


def completed_response(req: EdgeExecutionRequest, raw: bytes) -> dict[str, object]:
    payload = receipt_payload(req, raw)
    evidence_id = hashlib.sha256(
        canonical_json_bytes(payload) + b"\0" + SIGNATURE.encode("ascii")
    ).hexdigest()
    return {
        "ok": True,
        "decision": "ORIGIN_COMPLETED",
        "replayed": False,
        "responseStatus": 200,
        "rawBodyB64Url": b64url(raw),
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
        assert self.responses
        return self.responses.pop(0)


def test_fetch_entrega_payload_inmutable_sin_autoridad_productiva() -> None:
    req = request()
    raw = body()
    transport = FakeTransport([completed_response(req, raw)])
    result = EdgeCatalogPageFetcher(EdgeGatewayClient(transport)).fetch(
        req,
        bearer_token="oidc.synthetic.token",
    )

    assert isinstance(result, ValidatedEdgeCatalogPage)
    assert result.production_authority is False
    assert result.evidence.production_authority is False
    assert result.body.production_authority is False
    assert result.source_url == req.origin_url
    assert result.page_size == 2
    assert result.body.records_filtered == 2
    assert result.body.products_returned == 2
    assert result.payload["data"]["productSearch"]["recordsFiltered"] == 2  # type: ignore[index]
    with pytest.raises(TypeError):
        result.payload["attacker"] = True  # type: ignore[index]


def test_wait_y_deny_no_entregan_payload() -> None:
    req = request()
    client = EdgeGatewayClient(
        FakeTransport(
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
    )
    fetcher = EdgeCatalogPageFetcher(client)

    with pytest.raises(EdgeCatalogPageError) as waiting:
        fetcher.fetch(req, bearer_token="token")
    assert waiting.value.code == "edge_gateway_wait"

    with pytest.raises(EdgeCatalogPageError) as denied:
        fetcher.fetch(req, bearer_token="token")
    assert denied.value.code == "edge_gateway_denied"


def test_body_parcial_falla_en_la_frontera_antes_del_scraper() -> None:
    req = request()
    raw = body(records_filtered=2, products=[{}])
    response = completed_response(req, raw)

    with pytest.raises(EdgeCatalogPageError) as captured:
        EdgeCatalogPageFetcher(EdgeGatewayClient(FakeTransport([response]))).fetch(
            req,
            bearer_token="token",
        )

    assert captured.value.code == "edge_body_graphql_partial_or_overfull_page"


def test_body_con_json_duplicado_falla_en_la_frontera() -> None:
    req = request()
    raw = b'{"data":{"productSearch":{"recordsFiltered":2,"recordsFiltered":2,"products":[{},{}]}}}'
    response = completed_response(req, raw)

    with pytest.raises(EdgeCatalogPageError) as captured:
        EdgeCatalogPageFetcher(EdgeGatewayClient(FakeTransport([response]))).fetch(
            req,
            bearer_token="token",
        )

    assert captured.value.code == "edge_body_json_duplicate_key"


def test_constructor_rechaza_cliente_no_gateway() -> None:
    with pytest.raises(EdgeCatalogPageError) as captured:
        EdgeCatalogPageFetcher(object())  # type: ignore[arg-type]
    assert captured.value.code == "edge_gateway_client_invalid"
