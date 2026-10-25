from __future__ import annotations

from types import SimpleNamespace

import pytest

import precios_supermercados.scrapers.la_colonia_context_bound_catalog_finalizer as module
from precios_supermercados.catalog_context_provenance import ContextBoundEdgeReceiptPayload
from precios_supermercados.cloudflare_observability_verifier import (
    CloudflareObservabilityVerifierClient,
    CloudflareObservabilityVerifierError,
)
from precios_supermercados.cloudflare_trace_evidence import PlatformReconciledEdgePage
from precios_supermercados.edge_provenance_run import EdgeProvenanceRunManifest
from precios_supermercados.scrapers.la_colonia_context_bound_catalog_transport import (
    ContextBoundVerifiedCatalogCollection,
    ContextBoundVerifiedCatalogEdgeCollector,
    ContextBoundVerifiedCatalogPageObservation,
)
from precios_supermercados.scrapers.la_colonia_sps_facet_context import (
    RequestContextPlacement,
)

RUN = "32600000000:1"
PLAN_DIGEST = "1" * 64
DISCOVERY_DIGEST = "2" * 64
STRUCTURAL_PLAN_DIGEST = "3" * 64
CONTEXT_FINGERPRINT = "4" * 64
LOCATION = "la_colonia_sps"


class _NoopTransport:
    def post_json(self, path, *, bearer_token, payload):
        raise AssertionError("el test no debe usar el transporte real")


def _verifier() -> CloudflareObservabilityVerifierClient:
    return CloudflareObservabilityVerifierClient("a" * 32, _NoopTransport())


def _payload(wire_fingerprint: str) -> ContextBoundEdgeReceiptPayload:
    payload = object.__new__(ContextBoundEdgeReceiptPayload)
    object.__setattr__(payload, "base", SimpleNamespace())
    object.__setattr__(payload, "location_id", LOCATION)
    object.__setattr__(payload, "binding_source_key", f"request:regionid:sha256:{CONTEXT_FINGERPRINT}")
    object.__setattr__(payload, "binding_evidence", "location_binding_radiography:sha256:" + "5" * 64)
    object.__setattr__(payload, "context_fingerprint", CONTEXT_FINGERPRINT)
    object.__setattr__(payload, "context_placement", "header")
    object.__setattr__(payload, "context_wire_key", "X-VTEX-Region")
    object.__setattr__(payload, "context_value_path", ())
    object.__setattr__(payload, "wire_request_fingerprint", wire_fingerprint)
    return payload


def _observation(ordinal: int) -> ContextBoundVerifiedCatalogPageObservation:
    expected = SimpleNamespace(identity=("primary", RUN, "root", ordinal))
    payload = _payload(f"{ordinal + 6:x}" * 64)
    page = SimpleNamespace(
        cryptographic_signature_verified=True,
        production_authority=False,
        source_url=f"https://www.lacolonia.com/_v/segment/graphql/v1?page={ordinal}",
        verified_receipt=SimpleNamespace(
            receipt=SimpleNamespace(payload=payload),
        ),
    )
    observation = object.__new__(ContextBoundVerifiedCatalogPageObservation)
    object.__setattr__(observation, "expected", expected)
    object.__setattr__(observation, "page", page)
    object.__setattr__(observation, "raw_evidence", object())
    object.__setattr__(observation, "location_id", LOCATION)
    object.__setattr__(observation, "context_fingerprint", CONTEXT_FINGERPRINT)
    object.__setattr__(observation, "wire_request_fingerprint", payload.wire_request_fingerprint)
    object.__setattr__(observation, "production_authority", False)
    return observation


def _collector(
    *,
    placement: RequestContextPlacement = RequestContextPlacement.HEADER,
    complete: bool = True,
) -> ContextBoundVerifiedCatalogEdgeCollector:
    observations = [_observation(0), _observation(1)]
    if placement is RequestContextPlacement.QUERY:
        for observation in observations:
            observation.page.verified_receipt.receipt.payload.context_placement = "query"
            observation.page.verified_receipt.receipt.payload.context_wire_key = "regionId"
    if not complete:
        observations.pop()
    plan = SimpleNamespace(
        digest=PLAN_DIGEST,
        discovery_digest=DISCOVERY_DIGEST,
        request_count=2,
        pages=tuple(item.expected for item in [_observation(0), _observation(1)]),
    )
    sps_context = SimpleNamespace(
        plan_digest=STRUCTURAL_PLAN_DIGEST,
        location_id=LOCATION,
        context_fingerprint=CONTEXT_FINGERPRINT,
        context_placement=placement,
    )
    collector = object.__new__(ContextBoundVerifiedCatalogEdgeCollector)
    collector._plan = plan
    collector._sps_context = sps_context
    collector._observations = observations
    collector._next_index = len(observations)
    return collector


def _collection() -> ContextBoundVerifiedCatalogCollection:
    value = object.__new__(ContextBoundVerifiedCatalogCollection)
    object.__setattr__(value, "plan_digest", PLAN_DIGEST)
    object.__setattr__(value, "discovery_digest", DISCOVERY_DIGEST)
    object.__setattr__(value, "structural_context_plan_digest", STRUCTURAL_PLAN_DIGEST)
    object.__setattr__(value, "location_id", LOCATION)
    object.__setattr__(value, "context_fingerprint", CONTEXT_FINGERPRINT)
    object.__setattr__(value, "primary", object())
    object.__setattr__(value, "reconciliation", object())
    object.__setattr__(value, "observations", (_observation(0), _observation(1)))
    object.__setattr__(value, "production_authority", False)
    return value


def _manifest(count: int = 2) -> EdgeProvenanceRunManifest:
    value = object.__new__(EdgeProvenanceRunManifest)
    object.__setattr__(value, "pages", tuple(object() for _ in range(count)))
    object.__setattr__(value, "production_authority", False)
    return value


def _reconciled(page, ordinal: int) -> PlatformReconciledEdgePage:
    trace = SimpleNamespace(
        fetch_url=page.source_url,
        fetch_span_id=f"fetch-{ordinal}",
        physical_evidence_id=f"{ordinal + 8:x}" * 64,
    )
    value = object.__new__(PlatformReconciledEdgePage)
    object.__setattr__(value, "page", page)
    object.__setattr__(value, "trace_evidence", trace)
    object.__setattr__(value, "platform_evidence_reconciled", True)
    object.__setattr__(value, "production_authority", False)
    return value


def _patch_collection(monkeypatch, collector):
    collection = _collection()
    monkeypatch.setattr(
        ContextBoundVerifiedCatalogEdgeCollector,
        "build_collection",
        lambda self: collection,
    )
    return collection


def test_header_reconcilia_en_orden_y_resultado_no_retiene_trazas_raw(monkeypatch) -> None:
    collector = _collector()
    collection = _patch_collection(monkeypatch, collector)
    verifier = _verifier()
    calls: list[tuple[object, str]] = []

    def fake_reconcile(self, page, *, bearer_token):
        calls.append((page, bearer_token))
        return _reconciled(page, len(calls) - 1)

    monkeypatch.setattr(CloudflareObservabilityVerifierClient, "reconcile_page", fake_reconcile)
    captured: dict[str, object] = {}
    manifest = _manifest()

    def fake_manifest_builder(*, authenticated_plan, reconciled_pages):
        captured["authenticated_plan"] = authenticated_plan
        captured["reconciled_pages"] = reconciled_pages
        return manifest

    monkeypatch.setattr(module, "build_authenticated_edge_provenance_run_manifest", fake_manifest_builder)
    token_calls = 0

    def token_provider() -> str:
        nonlocal token_calls
        token_calls += 1
        return "observability-token"

    finalizer = module.ContextBoundCatalogProvenanceFinalizer(
        verifier,
        bearer_token_provider=token_provider,
    )
    result = finalizer.finalize(collector)

    assert result.collection is collection
    assert result.manifest is manifest
    assert result.structural_context_plan_digest == STRUCTURAL_PLAN_DIGEST
    assert result.location_id == LOCATION
    assert result.context_fingerprint == CONTEXT_FINGERPRINT
    assert result.production_authority is False
    assert not hasattr(result, "reconciled_pages")
    assert [page for page, _ in calls] == [item.page for item in collector.observations]
    assert {token for _, token in calls} == {"observability-token"}
    assert token_calls == 1
    assert captured["authenticated_plan"] is collector.authenticated_plan
    assert tuple(captured["reconciled_pages"]) == tuple(
        _reconciled(item.page, index)
        for index, item in enumerate(collector.observations)
    )
    assert finalizer.finalized is True


def test_query_usa_candidatos_y_builder_redactado_sin_reconcile_legacy(monkeypatch) -> None:
    collector = _collector(placement=RequestContextPlacement.QUERY)
    collection = _patch_collection(monkeypatch, collector)
    verifier = _verifier()
    token_calls = 0
    candidate_calls: list[tuple[object, str]] = []
    redaction_calls: list[tuple[object, object]] = []
    manifest = _manifest()
    captured: dict[str, object] = {}

    def token_provider() -> str:
        nonlocal token_calls
        token_calls += 1
        return "observability-token"

    def candidates(self, page, *, bearer_token):
        candidate_calls.append((page, bearer_token))
        return (SimpleNamespace(marker=f"candidate-{len(candidate_calls)}"),)

    def legacy(*_args, **_kwargs):
        raise AssertionError("placement query no debe usar reconcile_page legacy")

    def redact(observation, raw_candidates):
        redaction_calls.append((observation, raw_candidates))
        return SimpleNamespace(
            page=observation.page,
            platform_evidence_reconciled=True,
            production_authority=False,
        )

    def query_builder(*, authenticated_plan, reconciled_pages):
        captured["authenticated_plan"] = authenticated_plan
        captured["reconciled_pages"] = reconciled_pages
        return manifest

    monkeypatch.setattr(CloudflareObservabilityVerifierClient, "trace_candidates", candidates)
    monkeypatch.setattr(CloudflareObservabilityVerifierClient, "reconcile_page", legacy)
    monkeypatch.setattr(module, "reconcile_context_bound_query_trace", redact)
    monkeypatch.setattr(module, "build_context_bound_query_provenance_run_manifest", query_builder)

    finalizer = module.ContextBoundCatalogProvenanceFinalizer(
        verifier,
        bearer_token_provider=token_provider,
    )
    result = finalizer.finalize(collector)

    assert result.collection is collection
    assert result.manifest is manifest
    assert result.production_authority is False
    assert token_calls == 1
    assert [page for page, _ in candidate_calls] == [item.page for item in collector.observations]
    assert {token for _, token in candidate_calls} == {"observability-token"}
    assert [item for item, _ in redaction_calls] == list(collector.observations)
    assert captured["authenticated_plan"] is collector.authenticated_plan
    assert len(captured["reconciled_pages"]) == 2
    assert finalizer.finalized is True


def test_collector_incompleto_falla_antes_de_token(monkeypatch) -> None:
    collector = _collector(complete=False)
    verifier = _verifier()
    token_calls = 0

    def token_provider() -> str:
        nonlocal token_calls
        token_calls += 1
        return "observability-token"

    finalizer = module.ContextBoundCatalogProvenanceFinalizer(
        verifier,
        bearer_token_provider=token_provider,
    )
    with pytest.raises(module.ContextBoundCatalogFinalizationError) as captured:
        finalizer.finalize(collector)
    assert captured.value.code == "context_bound_catalog_collector_incomplete"
    assert token_calls == 0


def test_downgrade_v2_se_rechaza_antes_de_observability(monkeypatch) -> None:
    collector = _collector()
    collector._observations[0].page.verified_receipt.receipt.payload = SimpleNamespace(
        schema_version="2",
        location_id=LOCATION,
        context_fingerprint=CONTEXT_FINGERPRINT,
        wire_request_fingerprint=collector._observations[0].wire_request_fingerprint,
    )
    verifier = _verifier()

    with pytest.raises(module.ContextBoundCatalogFinalizationError) as captured:
        module.ContextBoundCatalogProvenanceFinalizer(
            verifier,
            bearer_token_provider=lambda: "observability-token",
        ).finalize(collector)
    assert captured.value.code == "context_bound_catalog_receipt_downgrade"


def test_fallo_observability_corta_el_run(monkeypatch) -> None:
    collector = _collector()
    _patch_collection(monkeypatch, collector)
    verifier = _verifier()
    calls = 0

    def fail_first(self, page, *, bearer_token):
        nonlocal calls
        calls += 1
        raise CloudflareObservabilityVerifierError("matching_trace_missing")

    monkeypatch.setattr(CloudflareObservabilityVerifierClient, "reconcile_page", fail_first)
    finalizer = module.ContextBoundCatalogProvenanceFinalizer(
        verifier,
        bearer_token_provider=lambda: "observability-token",
    )
    with pytest.raises(module.ContextBoundCatalogFinalizationError) as captured:
        finalizer.finalize(collector)
    assert captured.value.code == "catalog_context_page_0_observability_matching_trace_missing"
    assert calls == 1
    assert finalizer.finalized is False


def test_segunda_llamada_mismo_collector_es_idempotente(monkeypatch) -> None:
    collector = _collector()
    _patch_collection(monkeypatch, collector)
    verifier = _verifier()
    calls = 0

    def fake_reconcile(self, page, *, bearer_token):
        nonlocal calls
        ordinal = calls
        calls += 1
        return _reconciled(page, ordinal)

    monkeypatch.setattr(CloudflareObservabilityVerifierClient, "reconcile_page", fake_reconcile)
    monkeypatch.setattr(
        module,
        "build_authenticated_edge_provenance_run_manifest",
        lambda **_: _manifest(),
    )
    finalizer = module.ContextBoundCatalogProvenanceFinalizer(
        verifier,
        bearer_token_provider=lambda: "observability-token",
    )

    first = finalizer.finalize(collector)
    second = finalizer.finalize(collector)
    assert second is first
    assert calls == 2
