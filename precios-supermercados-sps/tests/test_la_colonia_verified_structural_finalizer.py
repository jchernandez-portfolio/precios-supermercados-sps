from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from precios_supermercados.cloudflare_structural_observability_verifier import (
    CloudflareStructuralObservabilityVerifierClient,
)
from precios_supermercados.cloudflare_structural_trace_evidence import (
    STRUCTURAL_EXECUTION_SPAN_NAME,
)
from precios_supermercados.edge_structural_gateway_client import StructuralEdgeGatewayClient
from precios_supermercados.edge_structural_observation import (
    CryptographicallyVerifiedStructuralObservation,
    EdgeStructuralObservationVerifier,
)
from precios_supermercados.la_colonia_edge_structural_body import (
    validate_la_colonia_structural_body,
)
from precios_supermercados.la_colonia_edge_structural_request import (
    build_structural_discovery_url,
    validate_la_colonia_structural_request,
)
from precios_supermercados.scrapers.la_colonia_verified_facet_transport import (
    VerifiedFacetDiscoveryEdgeTransport,
)
from precios_supermercados.scrapers.la_colonia_verified_structural_finalizer import (
    VerifiedStructuralDiscoveryFinalizationError,
    VerifiedStructuralDiscoveryFinalizer,
)
from precios_supermercados.structural_discovery_manifest import VerifiedStructuralDiscovery
from precios_supermercados.structural_provenance import (
    SignedStructuralReceipt,
    StructuralReceiptPayload,
)
from precios_supermercados.structural_receipt_crypto import (
    Ed25519StructuralReceiptVerifier,
    VerifiedStructuralReceipt,
)

ACCOUNT_ID = "0123456789abcdef0123456789abcdef"
TOKEN = "cloudflare-structural-observability-token-test"
SERVICE = "precios-sps-provenance"
VERSION_ID = "cf-version-finalizer-001"
RUN = "32531000000:1"
AUTH = "authorization-finalizer-001"
BASE = datetime(2026, 8, 21, 22, 30, 0, tzinfo=timezone.utc)


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _body(kind: str) -> bytes:
    if kind == "root_total":
        value: dict[str, object] = {"data": {"productSearch": {"recordsFiltered": 7}}}
    else:
        value = {
            "data": {
                "productSearch": {"recordsFiltered": 7},
                "facets": {
                    "sampling": False,
                    "facets": [
                        {
                            "type": "CATEGORY",
                            "values": [
                                {
                                    "key": "category-1",
                                    "value": "supermercado",
                                    "quantity": 7,
                                    "children": [],
                                }
                            ],
                        }
                    ],
                },
            }
        }
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def _observation(kind: str) -> CryptographicallyVerifiedStructuralObservation:
    source_url = build_structural_discovery_url(kind)
    request = validate_la_colonia_structural_request(source_url)
    raw = _body(kind)
    body = validate_la_colonia_structural_body(raw, request)
    start = BASE if kind == "root_total" else BASE + timedelta(seconds=2)
    completed = start + timedelta(milliseconds=200)
    payload = StructuralReceiptPayload(
        run_id=RUN,
        request_kind=kind,  # type: ignore[arg-type]
        request_id=f"request-{kind}",
        reservation_id=f"reservation-{kind}",
        authorization_id=AUTH,
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
        physical_started_at_utc=start,
        response_completed_at_utc=completed,
        github_repository="jchernandez-portfolio/precios-supermercados-sps",
        github_repository_id="1282475205",
        github_ref="refs/heads/main",
        github_workflow_ref="jchernandez-portfolio/precios-supermercados-sps/.github/workflows/precios-supermercados-sps-la-colonia-live.yml@refs/heads/main",
        github_environment="la-colonia-live",
        github_run_id="32531000000",
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
    signed = SignedStructuralReceipt(
        payload=payload,
        signature_b64url=_b64url(bytes(range(64))),
    )
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


def _base_event(
    *,
    trace_id: str,
    span_id: str,
    span_name: str,
    start: datetime,
    end: datetime,
) -> dict[str, object]:
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    return {
        "$metadata": {
            "service": SERVICE,
            "traceId": trace_id,
            "spanId": span_id,
            "spanName": span_name,
            "startTime": start_ms,
            "endTime": end_ms,
        },
        "$workers": {
            "scriptName": SERVICE,
            "scriptVersion": {"id": VERSION_ID},
            "truncated": False,
        },
        "dataset": "cloudflare-workers",
        "source": {},
        "timestamp": start_ms,
        "cloud.provider": "cloudflare",
        "cloud.platform": "cloudflare.workers",
        "faas.invocation_id": f"invocation-{trace_id}",
        "service.name": SERVICE,
        "cloudflare.script_version.id": VERSION_ID,
    }


def _events(observation: CryptographicallyVerifiedStructuralObservation) -> tuple[dict[str, object], dict[str, object]]:
    payload = observation.verified_receipt.receipt.payload
    suffix = payload.request_kind
    trace_id = f"trace-{suffix}"
    custom_span_id = f"custom-{suffix}"
    start = payload.physical_started_at_utc
    completed = payload.response_completed_at_utc
    custom = _base_event(
        trace_id=trace_id,
        span_id=custom_span_id,
        span_name=STRUCTURAL_EXECUTION_SPAN_NAME,
        start=start,
        end=completed,
    )
    custom.update(
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
    fetch = _base_event(
        trace_id=trace_id,
        span_id=f"fetch-{suffix}",
        span_name="fetch",
        start=start + timedelta(milliseconds=20),
        end=completed - timedelta(milliseconds=20),
    )
    metadata = fetch["$metadata"]
    assert isinstance(metadata, dict)
    metadata["parentSpanId"] = custom_span_id
    fetch.update(
        {
            "url.full": observation.source_url,
            "http.request.method": "GET",
            "http.response.status_code": 200,
            "http.response.body.size": payload.response_body_bytes,
        }
    )
    return custom, fetch


def _response(events: list[dict[str, object]]) -> dict[str, object]:
    return {
        "success": True,
        "errors": [],
        "result": {"events": {"events": events, "count": len(events)}},
    }


class _FakeObservabilityTransport:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def post_json(self, path: str, *, bearer_token: str, payload: Any) -> Any:
        self.calls.append({"path": path, "bearer_token": bearer_token, "payload": payload})
        if not self.responses:
            raise AssertionError("unexpected observability call")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class _UnusedGatewayTransport:
    def post_json(self, path: str, *, bearer_token: str, payload: Mapping[str, object]):
        raise AssertionError("gateway no debe ejecutarse durante finalización")


def _verified_transport(
    root: CryptographicallyVerifiedStructuralObservation | None = None,
    tree: CryptographicallyVerifiedStructuralObservation | None = None,
) -> VerifiedFacetDiscoveryEdgeTransport:
    private = Ed25519PrivateKey.generate()
    public_der = private.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    verifier = EdgeStructuralObservationVerifier(
        Ed25519StructuralReceiptVerifier(
            {"cloudflare-ed25519-v1": _b64url(public_der)}
        )
    )
    transport = VerifiedFacetDiscoveryEdgeTransport(
        StructuralEdgeGatewayClient(_UnusedGatewayTransport()),
        verifier,
        context_provider=lambda *_: (_ for _ in ()).throw(AssertionError("context unused")),
        bearer_token_provider=lambda: (_ for _ in ()).throw(AssertionError("token unused")),
    )
    observations: dict[str, CryptographicallyVerifiedStructuralObservation] = {}
    if root is not None:
        observations["root_total"] = root
    if tree is not None:
        observations["category_tree"] = tree
    transport._observations = observations
    transport._next_sequence = len(observations) + 1
    return transport


def _finalizer_fixture():
    root = _observation("root_total")
    tree = _observation("category_tree")
    root_custom, root_fetch = _events(root)
    tree_custom, tree_fetch = _events(tree)
    obs_transport = _FakeObservabilityTransport(
        [
            _response([root_custom]),
            _response([root_custom, root_fetch]),
            _response([tree_custom]),
            _response([tree_custom, tree_fetch]),
        ]
    )
    client = CloudflareStructuralObservabilityVerifierClient(ACCOUNT_ID, obs_transport)
    token_calls: list[int] = []

    def token_provider() -> str:
        token_calls.append(1)
        return TOKEN

    finalizer = VerifiedStructuralDiscoveryFinalizer(
        client,
        bearer_token_provider=token_provider,
    )
    return finalizer, _verified_transport(root, tree), obs_transport, token_calls


def test_finaliza_root_tree_con_un_token_y_deriva_discovery_verificado() -> None:
    finalizer, transport, obs_transport, token_calls = _finalizer_fixture()

    result = finalizer.finalize(transport)

    assert isinstance(result, VerifiedStructuralDiscovery)
    assert result.structure.valid is True
    assert result.structure.root_total == 7
    assert result.leaf_partitions_count == 1
    assert result.positive_leaf_partitions == 1
    assert result.root_total.request_kind == "root_total"
    assert result.category_tree.request_kind == "category_tree"
    assert result.production_authority is False
    assert finalizer.finalized is True
    assert finalizer.account_id == ACCOUNT_ID
    assert token_calls == [1]
    assert len(obs_transport.calls) == 4
    assert all(call["bearer_token"] == TOKEN for call in obs_transport.calls)


def test_segunda_finalizacion_del_mismo_transporte_es_idempotente_sin_nuevas_queries() -> None:
    finalizer, transport, obs_transport, token_calls = _finalizer_fixture()
    first = finalizer.finalize(transport)
    calls_after_first = len(obs_transport.calls)

    second = finalizer.finalize(transport)

    assert second is first
    assert len(obs_transport.calls) == calls_after_first
    assert token_calls == [1]


def test_incompleto_falla_antes_de_token_y_observability() -> None:
    finalizer, _transport, obs_transport, token_calls = _finalizer_fixture()
    incomplete = _verified_transport(_observation("root_total"), None)

    with pytest.raises(VerifiedStructuralDiscoveryFinalizationError) as captured:
        finalizer.finalize(incomplete)

    assert captured.value.code == "verified_facet_transport_incomplete"
    assert token_calls == []
    assert obs_transport.calls == []


def test_fallo_de_root_detiene_antes_de_tree_y_no_hace_retry() -> None:
    root = _observation("root_total")
    tree = _observation("category_tree")
    obs_transport = _FakeObservabilityTransport(
        [{"success": False, "errors": [{"message": "denied"}], "result": {}}]
    )
    client = CloudflareStructuralObservabilityVerifierClient(ACCOUNT_ID, obs_transport)
    finalizer = VerifiedStructuralDiscoveryFinalizer(
        client,
        bearer_token_provider=lambda: TOKEN,
    )

    with pytest.raises(VerifiedStructuralDiscoveryFinalizationError) as captured:
        finalizer.finalize(_verified_transport(root, tree))

    assert captured.value.code.startswith("root_total_observability_discovery_")
    assert len(obs_transport.calls) == 1


def test_token_invalido_falla_antes_de_observability() -> None:
    root = _observation("root_total")
    tree = _observation("category_tree")
    obs_transport = _FakeObservabilityTransport([])
    client = CloudflareStructuralObservabilityVerifierClient(ACCOUNT_ID, obs_transport)
    finalizer = VerifiedStructuralDiscoveryFinalizer(
        client,
        bearer_token_provider=lambda: "token con espacios",
    )

    with pytest.raises(VerifiedStructuralDiscoveryFinalizationError) as captured:
        finalizer.finalize(_verified_transport(root, tree))

    assert captured.value.code == "observability_bearer_token_invalid"
    assert obs_transport.calls == []


def test_finalizer_no_puede_reusarse_con_otro_transporte() -> None:
    finalizer, transport, _obs_transport, _token_calls = _finalizer_fixture()
    finalizer.finalize(transport)

    with pytest.raises(VerifiedStructuralDiscoveryFinalizationError) as captured:
        finalizer.finalize(
            _verified_transport(_observation("root_total"), _observation("category_tree"))
        )

    assert captured.value.code == "finalizer_already_bound_to_other_transport"
