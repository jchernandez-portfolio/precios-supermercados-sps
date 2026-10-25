from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timezone
from types import MappingProxyType, SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

import precios_supermercados.scrapers.la_colonia_context_bound_facet_entrypoint as entrypoint_module
from precios_supermercados.la_colonia_edge_structural_request import (
    build_structural_discovery_url,
    validate_la_colonia_structural_request,
)
from precios_supermercados.locations import LocationConfig, LocationGranularity
from precios_supermercados.scrapers.la_colonia_context_bound_facet_entrypoint import (
    ContextBoundFacetEntrypointError,
    FacetEdgeRunContext,
    run_context_bound_facet_entrypoint,
)
from precios_supermercados.scrapers.la_colonia_sps_facet_context import (
    EphemeralSpsRequestContextCollector,
    confirmed_sps_facet_binding,
    fingerprint_context_value,
)
from precios_supermercados.scrapers.la_colonia_sps_structural_plan import (
    build_sps_structural_facet_plan,
)

RAW_REGION = "opaque-region-context-bound-entrypoint-test"
FIXED_TIME = datetime(2026, 8, 24, 18, 50, tzinfo=UTC)
SHA = "a" * 40
RUN_ID = "32765000000"


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _public_key() -> str:
    key = Ed25519PrivateKey.generate().public_key().public_bytes(
        Encoding.DER,
        PublicFormat.SubjectPublicKeyInfo,
    )
    return _b64url(key)


class _ObservedRequest:
    url = "https://www.lacolonia.com/_v/segment/graphql/v1"
    headers = {"X-VTEX-Region": RAW_REGION}
    post_data_json = None


def _plan():
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
    return build_sps_structural_facet_plan(collector.resolve(binding), binding=binding)


def _run_context(**overrides):
    values = {
        "edge_authorization_id": "facet-edge-run-001",
        "github_run_id": RUN_ID,
        "github_run_attempt": 1,
        "approved_commit_sha": SHA,
        "created_at_ms": 1_787_595_000_000,
        "expires_at_ms": 1_787_595_300_000,
    }
    values.update(overrides)
    return FacetEdgeRunContext(**values)


class _InitializeTransport:
    def __init__(self, *, used: int = 0) -> None:
        self.used = used
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def post_json(self, path, *, bearer_token, payload):
        self.calls.append((path, bearer_token, dict(payload)))
        authorization = payload["authorization"]
        remaining = 2 - self.used
        return {
            "ok": True,
            "decision": "INITIALIZED",
            "authorization": {
                "authorizationId": authorization["authorizationId"],
                "runId": authorization["runId"],
                "state": "active",
                "maxRequests": 2,
                "requestsUsed": self.used,
                "remainingRequests": remaining,
                "minStartIntervalMs": 1500,
                "lastPhysicalStartMs": None if self.used == 0 else 1_787_595_001_500,
                "terminalAtMs": None,
                "terminalReason": None,
                "reservationCounts": {
                    "reserved": self.used,
                    "completed": 0,
                    "failed": 0,
                },
            },
        }


def _tree_payload():
    return {
        "recordsFiltered": 100,
        "sampling": False,
        "facets": [
            {
                "type": "CATEGORYTREE",
                "values": [
                    {
                        "key": "category-1",
                        "value": "private-root",
                        "quantity": 100,
                        "children": [
                            {
                                "key": "category-2",
                                "value": "private-leaf",
                                "quantity": 100,
                                "children": [],
                            }
                        ],
                    }
                ],
            }
        ],
    }


class _FakeVerifiedEdgeTransport:
    instances = []
    fail_first = False

    def __init__(
        self,
        _client,
        _verifier,
        *,
        sps_plan,
        context_provider,
        bearer_token_provider,
    ) -> None:
        self.plan = sps_plan
        self.context_provider = context_provider
        self.bearer_token_provider = bearer_token_provider
        self.calls = []
        self._observations = {}
        type(self).instances.append(self)

    @property
    def observations(self):
        return MappingProxyType(dict(self._observations))

    @property
    def complete(self):
        return set(self._observations) == {"root_total", "category_tree"}

    def __call__(self, logical_request):
        validated = validate_la_colonia_structural_request(
            build_structural_discovery_url(logical_request.name)
        )
        context = self.context_provider(logical_request, validated)
        token = self.bearer_token_provider()
        self.calls.append((logical_request, context, token))
        if type(self).fail_first and logical_request.name == "root_total":
            raise RuntimeError("synthetic edge failure")

        planned = self.plan.requests[logical_request.sequence - 1]
        payload = SimpleNamespace(
            wire_request_fingerprint=planned.wire_request_fingerprint,
            context_fingerprint=self.plan.context_fingerprint,
            location_id=self.plan.location_id,
        )
        receipt = SimpleNamespace(
            digest=("1" if logical_request.name == "root_total" else "2") * 64,
            payload=payload,
        )
        verified = SimpleNamespace(
            receipt=receipt,
            public_key_spki_sha256="3" * 64,
        )
        observation = SimpleNamespace(
            verified_receipt=verified,
            raw_body_sha256=("4" if logical_request.name == "root_total" else "5") * 64,
            cryptographic_signature_verified=True,
            structural_body_validated=True,
        )
        self._observations[logical_request.name] = observation
        if logical_request.name == "root_total":
            return {"recordsFiltered": 100}
        return _tree_payload()


@pytest.fixture(autouse=True)
def _fake_verified_transport(monkeypatch):
    _FakeVerifiedEdgeTransport.instances.clear()
    _FakeVerifiedEdgeTransport.fail_first = False
    monkeypatch.setattr(
        entrypoint_module,
        "VerifiedFacetDiscoveryEdgeTransport",
        _FakeVerifiedEdgeTransport,
    )


def test_compone_initialize_y_exactamente_root_tree_con_un_solo_contexto_edge() -> None:
    transport = _InitializeTransport()
    sleeps: list[float] = []
    result = run_context_bound_facet_entrypoint(
        plan=_plan(),
        run=_run_context(),
        transport=transport,
        bearer_token="oidc-token-fixed",
        trusted_public_key_spki_b64url=_public_key(),
        sleeper=sleeps.append,
        clock=lambda: FIXED_TIME,
    )

    assert len(transport.calls) == 1
    path, token, payload = transport.calls[0]
    assert path == "/v1/initialize"
    assert token == "oidc-token-fixed"
    assert payload["authorization"]["maxRequests"] == 2
    assert payload["authorization"]["runId"] == f"{RUN_ID}:1"

    edge = _FakeVerifiedEdgeTransport.instances[-1]
    assert [call[0].name for call in edge.calls] == ["root_total", "category_tree"]
    assert [call[0].sequence for call in edge.calls] == [1, 2]
    assert sleeps == [1.5]
    assert all(call[2] == "oidc-token-fixed" for call in edge.calls)

    root_context = edge.calls[0][1]
    tree_context = edge.calls[1][1]
    assert root_context.authorization_id == tree_context.authorization_id == "facet-edge-run-001"
    assert root_context.run_id == tree_context.run_id == f"{RUN_ID}:1"
    assert root_context.approved_commit_sha == tree_context.approved_commit_sha == SHA
    assert root_context.request_kind == "root_total"
    assert tree_context.request_kind == "category_tree"
    assert root_context.request_id != tree_context.request_id
    assert root_context.reservation_id != tree_context.reservation_id
    assert root_context.nonce != tree_context.nonce

    assert result.discovery_summary["requests_attempted"] == 2
    assert result.discovery_summary["requests_completed"] == 2
    assert result.network_executed is True
    assert result.production_authority is False
    assert result.catalog_accepted is False
    assert result.extraction_enabled is False


def test_artifact_es_sanitizado_acotado_y_con_invariantes_de_presupuesto() -> None:
    result = run_context_bound_facet_entrypoint(
        plan=_plan(),
        run=_run_context(),
        transport=_InitializeTransport(),
        bearer_token="oidc-token-fixed",
        trusted_public_key_spki_b64url=_public_key(),
        sleeper=lambda _: None,
        clock=lambda: FIXED_TIME,
    )
    artifact = dict(result.artifact)
    assert artifact["requests_planned"] == ["root_total", "category_tree"]
    assert artifact["max_requests"] == 2
    assert artifact["concurrency"] == 1
    assert artifact["max_retries"] == 0
    assert artifact["delay_seconds"] == 1.5
    assert artifact["raw_values_exposed"] is False
    assert artifact["production_authority"] is False
    assert artifact["catalog_accepted"] is False
    assert artifact["extraction_enabled"] is False
    assert set(artifact["evidence"]) == {"root_total", "category_tree"}
    rendered = result.artifact_bytes().decode("utf-8")
    assert RAW_REGION not in rendered
    assert "oidc-token-fixed" not in rendered
    assert "private-root" not in rendered
    assert "private-leaf" not in rendered
    assert len(result.artifact_bytes()) < 64 * 1024
    json.loads(rendered)


def test_initialize_no_fresco_falla_antes_del_primer_request_structural() -> None:
    with pytest.raises(ContextBoundFacetEntrypointError) as captured:
        run_context_bound_facet_entrypoint(
            plan=_plan(),
            run=_run_context(),
            transport=_InitializeTransport(used=1),
            bearer_token="oidc-token-fixed",
            trusted_public_key_spki_b64url=_public_key(),
            sleeper=lambda _: None,
            clock=lambda: FIXED_TIME,
        )
    assert captured.value.code == "facet_edge_initialization_not_fresh"
    assert _FakeVerifiedEdgeTransport.instances == []


def test_fallo_primario_no_reintenta_ni_intenta_category_tree() -> None:
    _FakeVerifiedEdgeTransport.fail_first = True
    result = run_context_bound_facet_entrypoint(
        plan=_plan(),
        run=_run_context(),
        transport=_InitializeTransport(),
        bearer_token="oidc-token-fixed",
        trusted_public_key_spki_b64url=_public_key(),
        sleeper=lambda _: None,
        clock=lambda: FIXED_TIME,
    )
    edge = _FakeVerifiedEdgeTransport.instances[-1]
    assert len(edge.calls) == 1
    assert edge.calls[0][0].name == "root_total"
    assert result.discovery_summary["requests_attempted"] == 1
    assert result.discovery_summary["requests_completed"] == 0
    assert result.discovery_summary["stop_reason"] == "transport_failure"
    assert result.artifact["evidence"] == {}


def test_contexto_de_run_rechaza_vida_mayor_a_diez_minutos() -> None:
    with pytest.raises(ContextBoundFacetEntrypointError) as captured:
        _run_context(
            created_at_ms=1_787_595_000_000,
            expires_at_ms=1_787_595_600_001,
        )
    assert captured.value.code == "facet_authorization_lifetime_above_limit"


def test_bearer_invalido_falla_antes_de_initialize() -> None:
    transport = _InitializeTransport()
    with pytest.raises(ContextBoundFacetEntrypointError) as captured:
        run_context_bound_facet_entrypoint(
            plan=_plan(),
            run=_run_context(),
            transport=transport,
            bearer_token="token con espacios",
            trusted_public_key_spki_b64url=_public_key(),
            sleeper=lambda _: None,
            clock=lambda: FIXED_TIME,
        )
    assert captured.value.code == "facet_edge_bearer_invalid"
    assert transport.calls == []
