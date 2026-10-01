from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from precios_supermercados.edge_structural_gateway_client import (
    StructuralEdgeGatewayClient,
    StructuralEdgeRequestContext,
)
from precios_supermercados.edge_structural_observation import EdgeStructuralObservationVerifier
from precios_supermercados.la_colonia_edge_structural_request import (
    ValidatedLaColoniaStructuralRequest,
    validate_la_colonia_structural_request,
)
from precios_supermercados.locations import LocationConfig, LocationGranularity
from precios_supermercados.scrapers.la_colonia_facet_discovery import (
    FACET_DISCOVERY_REQUEST_ID,
    FacetDiscoveryRequest,
    get_facet_discovery_plan,
)
from precios_supermercados.scrapers.la_colonia_facet_discovery_runtime import (
    OUTCOME_INCONCLUSIVE,
    OUTCOME_WITHIN_BUDGET,
    FacetDiscoveryRuntime,
)
from precios_supermercados.scrapers.la_colonia_sps_facet_context import (
    EphemeralSpsRequestContextCollector,
    confirmed_sps_facet_binding,
    fingerprint_context_value,
)
from precios_supermercados.scrapers.la_colonia_sps_structural_plan import (
    build_sps_structural_facet_plan,
)
from precios_supermercados.scrapers.la_colonia_verified_facet_transport import (
    VerifiedFacetDiscoveryEdgeTransport,
    VerifiedFacetDiscoveryTransportError,
)
from precios_supermercados.structural_provenance import (
    SignedStructuralReceipt,
    StructuralReceiptPayload,
)
from precios_supermercados.structural_receipt_crypto import Ed25519StructuralReceiptVerifier

FIXED_TIME = datetime(2026, 8, 21, 22, 30, tzinfo=timezone.utc)
GRAPHQL_URL = "https://www.lacolonia.com/_v/segment/graphql/v1"
RAW_REGION = "opaque-region-for-verified-facet-transport-test"


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _node(level: int, value: str, quantity: int, children: list[dict[str, object]]):
    return {
        "key": f"category-{level}",
        "value": value,
        "quantity": quantity,
        "children": children,
    }


def _tree_values():
    return [
        _node(
            1,
            "SECRET-ROOT",
            100,
            [
                _node(2, "SECRET-A", 55, [_node(3, "SECRET-A-LEAF", 55, [])]),
                _node(2, "SECRET-B", 45, [_node(3, "SECRET-B-LEAF", 45, [])]),
            ],
        )
    ]


def _body(kind: str) -> bytes:
    if kind == "root_total":
        payload = {"data": {"productSearch": {"recordsFiltered": 100}}}
    else:
        payload = {
            "data": {
                "productSearch": {"recordsFiltered": 100},
                "facets": {
                    "sampling": False,
                    "facets": [{"type": "CATEGORYTREE", "values": _tree_values()}],
                },
            }
        }
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _command():
    return {
        "request_id": FACET_DISCOVERY_REQUEST_ID,
        "supermarket": "la_colonia",
        "mode": "facet_discovery",
        "discovery_plan": "catalog_categories_v1",
        "delay_seconds": 1.5,
        "allow_full": False,
    }


class _ObservedRequest:
    url = GRAPHQL_URL
    headers = {"X-VTEX-Region": RAW_REGION}
    post_data_json = None


def _sps_plan():
    binding = confirmed_sps_facet_binding(
        LocationConfig(
            location_id="la_colonia_sps",
            supermarket_id="la_colonia",
            city_id="sps",
            city_name="San Pedro Sula",
            granularity=LocationGranularity.CITY,
            is_available=True,
            in_scope=True,
            extraction_enabled=False,
            technical_binding_confirmed=True,
            source_location_key="request:regionid:sha256:" + fingerprint_context_value(RAW_REGION),
            evidence="location_binding_radiography:sha256:" + "c" * 64,
        )
    )
    collector = EphemeralSpsRequestContextCollector()
    collector.observe_request(_ObservedRequest())
    context = collector.resolve(binding)
    return binding, build_sps_structural_facet_plan(context, binding=binding)


class _SigningTransport:
    def __init__(self, private_key: Ed25519PrivateKey) -> None:
        self.private_key = private_key
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def post_json(self, path, *, bearer_token, payload):
        self.calls.append((path, bearer_token, dict(payload)))
        origin_url = payload["originUrl"]
        request = validate_la_colonia_structural_request(origin_url)
        context = payload["requestContext"]
        location = payload["locationContext"]
        raw_body = _body(request.request_kind)
        receipt_payload = StructuralReceiptPayload(
            schema_version="2",
            run_id=context["runId"],
            request_kind=request.request_kind,  # type: ignore[arg-type]
            request_id=context["requestId"],
            reservation_id=context["reservationId"],
            authorization_id=context["authorizationId"],
            approved_commit_sha=context["approvedCommitSha"],
            request_digest=context["requestDigest"],
            canonical_request_sha256=request.canonical_request_sha256,
            graphql_query_sha256=request.graphql_query_sha256,
            http_method="GET",
            target_scheme="https",
            target_host="www.lacolonia.com",
            target_path="/_v/segment/graphql/v1",
            raw_response_sha256=hashlib.sha256(raw_body).hexdigest(),
            response_status=200,
            response_body_bytes=len(raw_body),
            physical_started_at_utc=datetime(
                2026, 8, 21, 22, 30, request.request_kind == "category_tree" and 2 or 0,
                tzinfo=timezone.utc,
            ),
            response_completed_at_utc=datetime(
                2026, 8, 21, 22, 30, request.request_kind == "category_tree" and 2 or 0,
                100_000,
                tzinfo=timezone.utc,
            ),
            github_repository="jchernandez-portfolio/precios-supermercados-sps",
            github_repository_id="1282475205",
            github_ref="refs/heads/main",
            github_workflow_ref="jchernandez-portfolio/precios-supermercados-sps/.github/workflows/precios-supermercados-sps-la-colonia-live.yml@refs/heads/main",
            github_environment="la-colonia-live",
            github_run_id="32530000000",
            github_run_attempt=1,
            oidc_subject="repo:jchernandez-portfolio/precios-supermercados-sps:environment:la-colonia-live",
            oidc_jti=f"jti-{request.request_kind}",
            collector_provider="cloudflare_workers",
            collector_principal="cloudflare-worker:precios-sps-provenance",
            collector_execution=f"execution-{request.request_kind}",
            collector_release_id="release-facet-001",
            collector_code_sha256="d" * 64,
            signing_algorithm="Ed25519",
            signing_key_id="cloudflare-ed25519-v1",
            nonce=context["nonce"],
            location_id=location["locationId"],
            binding_source_key=location["bindingSourceKey"],
            binding_evidence=location["bindingEvidence"],
            context_fingerprint=location["contextFingerprint"],
            context_placement=location["placement"],
            context_wire_key=location["wireKey"],
            context_value_path=tuple(location["valuePath"]),
            wire_request_fingerprint=location["wireRequestFingerprint"],
        )
        placeholder = SignedStructuralReceipt(
            payload=receipt_payload,
            signature_b64url=_b64url(b"x" * 64),
        )
        signature = _b64url(self.private_key.sign(placeholder.signing_bytes()))
        receipt = SignedStructuralReceipt(
            payload=receipt_payload,
            signature_b64url=signature,
        )
        return {
            "ok": True,
            "decision": "ORIGIN_COMPLETED",
            "replayed": False,
            "responseStatus": 200,
            "rawBodyB64Url": _b64url(raw_body),
            "receiptPayload": receipt_payload.canonical_dict(),
            "signatureB64Url": signature,
            "signingKeyId": receipt_payload.signing_key_id,
            "evidenceId": receipt.digest,
        }


class _StaticTransport:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls = 0

    def post_json(self, path, *, bearer_token, payload):
        self.calls += 1
        return self.response


def _verifier(public_key: Ed25519PrivateKey) -> EdgeStructuralObservationVerifier:
    public_der = public_key.public_key().public_bytes(
        Encoding.DER,
        PublicFormat.SubjectPublicKeyInfo,
    )
    crypto = Ed25519StructuralReceiptVerifier(
        {"cloudflare-ed25519-v1": _b64url(public_der)}
    )
    return EdgeStructuralObservationVerifier(crypto)


def _context_provider(
    logical_request: FacetDiscoveryRequest,
    validated: ValidatedLaColoniaStructuralRequest,
) -> StructuralEdgeRequestContext:
    return StructuralEdgeRequestContext(
        authorization_id="auth-facet-runtime-001",
        run_id="32530000000:1",
        approved_commit_sha="a" * 40,
        reservation_id=f"reservation-{logical_request.name}",
        request_id=f"request-{logical_request.name}",
        request_digest=validated.canonical_request_sha256,
        nonce=f"nonce-{logical_request.name}",
        request_kind=logical_request.name,
    )


def _adapter(monkeypatch, private_key: Ed25519PrivateKey | None = None, transport=None):
    binding, plan = _sps_plan()
    monkeypatch.setattr(
        "precios_supermercados.scrapers.la_colonia_verified_facet_transport.confirmed_sps_facet_binding",
        lambda: binding,
    )
    key = private_key or Ed25519PrivateKey.generate()
    wire_transport = transport or _SigningTransport(key)
    adapter = VerifiedFacetDiscoveryEdgeTransport(
        StructuralEdgeGatewayClient(wire_transport),
        _verifier(key),
        sps_plan=plan,
        context_provider=_context_provider,
        bearer_token_provider=lambda: "oidc-token",
    )
    return adapter, wire_transport, key


def test_runtime_completo_solo_recibe_payloads_despues_de_ed25519_y_contexto_sps(monkeypatch) -> None:
    adapter, transport, _key = _adapter(monkeypatch)
    sleeps: list[float] = []
    runtime = FacetDiscoveryRuntime(
        adapter,
        sleeper=sleeps.append,
        clock=lambda: FIXED_TIME,
    )
    result = runtime.run(_command())

    assert result.summary["discovery_outcome"] == OUTCOME_WITHIN_BUDGET
    assert result.summary["root_total"] == 100
    assert result.summary["positive_leaf_partitions"] == 2
    assert sleeps == [1.5]
    assert len(transport.calls) == 2
    assert adapter.requests_completed == 2
    assert adapter.complete is True
    assert set(adapter.observations) == {"root_total", "category_tree"}
    assert all(call[2]["locationContext"]["locationId"] == "la_colonia_sps" for call in transport.calls)
    assert all(call[2]["locationContext"]["rawValue"] == RAW_REGION for call in transport.calls)
    assert all(
        observation.cryptographic_signature_verified is True
        and observation.structural_body_validated is True
        and observation.production_authority is False
        and observation.verified_receipt.receipt.payload.location_context_bound is True
        for observation in adapter.observations.values()
    )
    rendered_receipts = json.dumps(
        [item.verified_receipt.receipt.payload.canonical_dict() for item in adapter.observations.values()],
        sort_keys=True,
    )
    assert RAW_REGION not in rendered_receipts


def test_adapter_exige_secuencia_root_luego_tree_y_no_admite_tercer_request(monkeypatch) -> None:
    adapter, _transport, _key = _adapter(monkeypatch)
    plan = get_facet_discovery_plan("catalog_categories_v1")
    with pytest.raises(VerifiedFacetDiscoveryTransportError) as first:
        adapter(plan.requests[1])
    assert first.value.code == "facet_request_sequence_invalid"

    adapter(plan.requests[0])
    adapter(plan.requests[1])
    with pytest.raises(VerifiedFacetDiscoveryTransportError) as third:
        adapter(plan.requests[0])
    assert third.value.code == "facet_request_count_exceeded"


def test_wait_y_deny_detienen_runtime_sin_retry_oculto(monkeypatch) -> None:
    for response, expected_code in (
        (
            {
                "ok": True,
                "decision": "WAIT",
                "reason": "pacing",
                "notBeforeMs": 123,
                "inFlightReservationId": None,
            },
            "structural_gateway_wait_pacing",
        ),
        (
            {"ok": True, "decision": "DENY", "reason": "authorization_consumed"},
            "structural_gateway_denied_authorization_consumed",
        ),
    ):
        key = Ed25519PrivateKey.generate()
        transport = _StaticTransport(response)
        adapter, _wire, _key = _adapter(monkeypatch, key, transport)
        with pytest.raises(VerifiedFacetDiscoveryTransportError) as captured:
            adapter(get_facet_discovery_plan("catalog_categories_v1").requests[0])
        assert captured.value.code == expected_code
        assert transport.calls == 1


def test_runtime_convierte_wait_en_transport_failure_sin_segunda_llamada(monkeypatch) -> None:
    transport = _StaticTransport(
        {
            "ok": True,
            "decision": "WAIT",
            "reason": "pacing",
            "notBeforeMs": 123,
            "inFlightReservationId": None,
        }
    )
    adapter, _wire, _key = _adapter(monkeypatch, Ed25519PrivateKey.generate(), transport)
    runtime = FacetDiscoveryRuntime(adapter, sleeper=lambda _: None, clock=lambda: FIXED_TIME)
    result = runtime.run(_command())
    assert result.summary["discovery_outcome"] == OUTCOME_INCONCLUSIVE
    assert result.summary["stop_reason"] == "transport_failure"
    assert result.summary["requests_attempted"] == 1
    assert transport.calls == 1


def test_firma_de_otra_clave_falla_antes_de_exponer_payload(monkeypatch) -> None:
    signer = Ed25519PrivateKey.generate()
    trusted = Ed25519PrivateKey.generate()
    binding, plan = _sps_plan()
    monkeypatch.setattr(
        "precios_supermercados.scrapers.la_colonia_verified_facet_transport.confirmed_sps_facet_binding",
        lambda: binding,
    )
    transport = _SigningTransport(signer)
    adapter = VerifiedFacetDiscoveryEdgeTransport(
        StructuralEdgeGatewayClient(transport),
        _verifier(trusted),
        sps_plan=plan,
        context_provider=_context_provider,
        bearer_token_provider=lambda: "oidc-token",
    )
    with pytest.raises(VerifiedFacetDiscoveryTransportError) as captured:
        adapter(get_facet_discovery_plan("catalog_categories_v1").requests[0])
    assert captured.value.code.startswith("structural_observation_structural_receipt_crypto_")
    assert adapter.requests_completed == 0


def test_contexto_de_ejecucion_no_puede_cambiar_entre_root_y_tree(monkeypatch) -> None:
    adapter, _transport, _key = _adapter(monkeypatch)
    plan = get_facet_discovery_plan("catalog_categories_v1")
    adapter(plan.requests[0])

    def changed_context(logical, validated):
        base = _context_provider(logical, validated)
        return StructuralEdgeRequestContext(
            authorization_id=base.authorization_id,
            run_id="32530000001:1",
            approved_commit_sha=base.approved_commit_sha,
            reservation_id=base.reservation_id,
            request_id=base.request_id,
            request_digest=base.request_digest,
            nonce=base.nonce,
            request_kind=base.request_kind,
        )

    adapter._context_provider = changed_context  # prueba adversarial sobre dependencia inyectada
    with pytest.raises(VerifiedFacetDiscoveryTransportError) as captured:
        adapter(plan.requests[1])
    assert captured.value.code == "structural_execution_context_changed"
    assert adapter.requests_completed == 1


def test_plan_con_binding_distinto_al_confirmado_se_rechaza_antes_de_red(monkeypatch) -> None:
    _binding, plan = _sps_plan()
    other_binding = confirmed_sps_facet_binding(
        LocationConfig(
            location_id="la_colonia_sps",
            supermarket_id="la_colonia",
            city_id="sps",
            city_name="San Pedro Sula",
            granularity=LocationGranularity.CITY,
            is_available=True,
            in_scope=True,
            extraction_enabled=False,
            technical_binding_confirmed=True,
            source_location_key="request:regionid:sha256:" + fingerprint_context_value("other"),
            evidence="location_binding_radiography:sha256:" + "d" * 64,
        )
    )
    monkeypatch.setattr(
        "precios_supermercados.scrapers.la_colonia_verified_facet_transport.confirmed_sps_facet_binding",
        lambda: other_binding,
    )
    with pytest.raises(VerifiedFacetDiscoveryTransportError) as captured:
        VerifiedFacetDiscoveryEdgeTransport(
            StructuralEdgeGatewayClient(_StaticTransport({})),
            _verifier(Ed25519PrivateKey.generate()),
            sps_plan=plan,
            context_provider=_context_provider,
            bearer_token_provider=lambda: "oidc-token",
        )
    assert captured.value.code == "sps_structural_plan_binding_mismatch"
