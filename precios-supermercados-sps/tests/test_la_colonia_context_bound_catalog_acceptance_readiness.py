from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

import precios_supermercados.scrapers.la_colonia_context_bound_catalog_acceptance_readiness as module
from precios_supermercados.canonical_authenticated_provenance_plan import (
    CANONICAL_CATALOG_REQUEST_LIMIT,
)
from precios_supermercados.scrapers.la_colonia_catalog_coverage import CatalogCoverageReport
from precios_supermercados.scrapers.la_colonia_context_bound_catalog_finalizer import (
    ContextBoundCatalogProvenanceRun,
)

RUN = "32610000000:1"
AUTH = "authorization-context-readiness"
COMMIT = "a" * 40
RELEASE = "release-context-readiness-001"
CODE_SHA = "d" * 64
KEY_ID = "cloudflare-ed25519-context-readiness-v1"
DISCOVERY_DIGEST = "1" * 64
PLAN_DIGEST = "2" * 64
MANIFEST_DIGEST = "3" * 64
TREE_DIGEST = "4" * 64
STRUCTURAL_PLAN_DIGEST = "5" * 64
CONTEXT_FINGERPRINT = "6" * 64
LOCATION = "la_colonia_sps"
PRIMARY_ID = f"{RUN}:primary"
RECONCILIATION_ID = f"{RUN}:reconciliation"
TRUST_GATE = "trusted_collector_provenance_unavailable"
AUTHORITY_BLOCKER = "production_authority_not_established"


class _FakeDiscovery:
    def __init__(self) -> None:
        self.run_id = RUN
        self.authorization_id = AUTH
        self.approved_commit_sha = COMMIT
        self.tree_digest = TREE_DIGEST
        self.structure = object()
        self.production_authority = False
        self.digest = DISCOVERY_DIGEST


class _FakeSpsContext:
    def __init__(self) -> None:
        self.discovery = _FakeDiscovery()
        self.plan_digest = STRUCTURAL_PLAN_DIGEST
        self.location_id = LOCATION
        self.context_fingerprint = CONTEXT_FINGERPRINT
        self.production_authority = False
        self.catalog_accepted = False
        self.extraction_enabled = False


def _plan():
    pages = (
        SimpleNamespace(identity=("primary", PRIMARY_ID, "root", "OrderByNameASC", 0, 49, "7" * 64)),
        SimpleNamespace(identity=("reconciliation", RECONCILIATION_ID, "root", "OrderByNameDESC", 0, 49, "8" * 64)),
    )
    return SimpleNamespace(
        digest=PLAN_DIGEST,
        discovery_digest=DISCOVERY_DIGEST,
        run_id=RUN,
        authorization_id=AUTH,
        approved_commit_sha=COMMIT,
        collector_release_id=RELEASE,
        collector_code_sha256=CODE_SHA,
        collector_signing_key_id=KEY_ID,
        request_count=len(pages),
        pages=pages,
        plan=SimpleNamespace(
            primary_traversal_id=PRIMARY_ID,
            reconciliation_traversal_id=RECONCILIATION_ID,
            primary_order_by="OrderByNameASC",
            reconciliation_order_by="OrderByNameDESC",
        ),
    )


def _coverage(*reasons: str) -> CatalogCoverageReport:
    values = tuple(reasons)
    complete = values == (TRUST_GATE,)
    return CatalogCoverageReport(
        partitions_discovered=1,
        partitions_attempted=1,
        partitions_completed=1 if complete else 0,
        pages_expected=1,
        pages_attempted=2,
        pages_completed=1 if complete else 0,
        products_reported=1,
        products_received=1,
        products_unique=1,
        duplicate_occurrences=0,
        repeated_page_sets=0,
        unexpected_overlaps=0,
        missing_coverage_events=len(values),
        total_changes=0,
        uncategorized_products=0,
        request_limit=CANONICAL_CATALOG_REQUEST_LIMIT,
        coverage_demonstrated=False,
        coverage_reason=";".join(values),
        accepted=False,
        run_id=RUN,
        tree_digest=TREE_DIGEST,
        primary_plan_digest="9" * 64,
        reconciliation_plan_digest="a" * 64,
        _reasons=values,
    )


def _provenance(plan=None) -> ContextBoundCatalogProvenanceRun:
    plan = plan or _plan()
    primary = SimpleNamespace(
        run_id=RUN,
        tree_digest=TREE_DIGEST,
        traversal_id=PRIMARY_ID,
        order_by="OrderByNameASC",
    )
    reconciliation = SimpleNamespace(
        run_id=RUN,
        tree_digest=TREE_DIGEST,
        traversal_id=RECONCILIATION_ID,
        order_by="OrderByNameDESC",
    )
    collection = SimpleNamespace(
        plan_digest=PLAN_DIGEST,
        discovery_digest=DISCOVERY_DIGEST,
        structural_context_plan_digest=STRUCTURAL_PLAN_DIGEST,
        location_id=LOCATION,
        context_fingerprint=CONTEXT_FINGERPRINT,
        primary=primary,
        reconciliation=reconciliation,
        production_authority=False,
    )
    manifest = SimpleNamespace(
        run_id=RUN,
        authorization_id=AUTH,
        approved_commit_sha=COMMIT,
        collector_release_id=RELEASE,
        collector_code_sha256=CODE_SHA,
        collector_signing_key_id=KEY_ID,
        primary_traversal_id=PRIMARY_ID,
        reconciliation_traversal_id=RECONCILIATION_ID,
        primary_order_by="OrderByNameASC",
        reconciliation_order_by="OrderByNameDESC",
        request_count=plan.request_count,
        pages=tuple(SimpleNamespace(expected=page) for page in plan.pages),
        digest=MANIFEST_DIGEST,
        production_authority=False,
    )
    value = object.__new__(ContextBoundCatalogProvenanceRun)
    object.__setattr__(value, "collection", collection)
    object.__setattr__(value, "manifest", manifest)
    object.__setattr__(value, "structural_context_plan_digest", STRUCTURAL_PLAN_DIGEST)
    object.__setattr__(value, "location_id", LOCATION)
    object.__setattr__(value, "context_fingerprint", CONTEXT_FINGERPRINT)
    object.__setattr__(value, "production_authority", False)
    return value


def _prepare(monkeypatch, *, reasons=(TRUST_GATE,)):
    monkeypatch.setattr(module, "VerifiedSpsStructuralContext", _FakeSpsContext)
    sps_context = _FakeSpsContext()
    plan = _plan()
    provenance = _provenance(plan)
    monkeypatch.setattr(
        module,
        "derive_canonical_authenticated_catalog_plan",
        lambda discovery: plan,
    )
    captured: dict[str, object] = {}

    def evaluate(structure, primary, reconciliation, *, request_limit):
        captured["structure"] = structure
        captured["primary"] = primary
        captured["reconciliation"] = reconciliation
        captured["request_limit"] = request_limit
        return _coverage(*reasons)

    monkeypatch.setattr(module, "_evaluate_canonical_catalog_coverage", evaluate)
    return sps_context, provenance, plan, captured


def test_unico_gate_trusted_collector_deja_candidato_context_bound_completo(monkeypatch) -> None:
    sps_context, provenance, plan, captured = _prepare(monkeypatch)

    result = module.assess_context_bound_catalog_acceptance_readiness(
        sps_context=sps_context,
        provenance=provenance,
    )

    assert result.technical_catalog_complete is True
    assert result.ready_for_productive_authority_evidence is True
    assert result.catalog_accepted is False
    assert result.production_authority is False
    assert result.blockers == (TRUST_GATE, AUTHORITY_BLOCKER)
    assert result.discovery_digest == DISCOVERY_DIGEST
    assert result.authenticated_plan_digest == plan.digest
    assert result.provenance_manifest_digest == MANIFEST_DIGEST
    assert result.structural_context_plan_digest == STRUCTURAL_PLAN_DIGEST
    assert result.location_id == LOCATION
    assert result.context_fingerprint == CONTEXT_FINGERPRINT
    assert captured["request_limit"] == CANONICAL_CATALOG_REQUEST_LIMIT
    assert captured["structure"] is sps_context.discovery.structure


def test_fallo_de_coverage_impide_readiness_productiva(monkeypatch) -> None:
    sps_context, provenance, _plan_value, _captured = _prepare(
        monkeypatch,
        reasons=(TRUST_GATE, "global_reconciliation_mismatch"),
    )
    result = module.assess_context_bound_catalog_acceptance_readiness(
        sps_context=sps_context,
        provenance=provenance,
    )
    assert result.technical_catalog_complete is False
    assert result.ready_for_productive_authority_evidence is False
    assert result.blockers == (
        "global_reconciliation_mismatch",
        TRUST_GATE,
        AUTHORITY_BLOCKER,
    )


def test_provenance_no_puede_cambiar_contexto_sps(monkeypatch) -> None:
    sps_context, provenance, _plan_value, _captured = _prepare(monkeypatch)
    provenance.collection.context_fingerprint = "f" * 64

    with pytest.raises(module.ContextBoundCatalogAcceptanceReadinessError) as captured:
        module.assess_context_bound_catalog_acceptance_readiness(
            sps_context=sps_context,
            provenance=provenance,
        )
    assert captured.value.code == "catalog_context_readiness_context_fingerprint_mismatch"


def test_collection_no_puede_pertenecer_a_otro_structural_plan(monkeypatch) -> None:
    sps_context, provenance, _plan_value, _captured = _prepare(monkeypatch)
    provenance.collection.structural_context_plan_digest = "e" * 64

    with pytest.raises(module.ContextBoundCatalogAcceptanceReadinessError) as captured:
        module.assess_context_bound_catalog_acceptance_readiness(
            sps_context=sps_context,
            provenance=provenance,
        )
    assert captured.value.code == "catalog_context_readiness_collection_structural_plan_digest_mismatch"


def test_manifest_no_puede_sustituir_pagina_del_plan(monkeypatch) -> None:
    sps_context, provenance, _plan_value, _captured = _prepare(monkeypatch)
    forged = SimpleNamespace(identity=("primary", PRIMARY_ID, "root", "OrderByNameASC", 50, 99, "0" * 64))
    provenance.manifest.pages = (
        SimpleNamespace(expected=forged),
        provenance.manifest.pages[1],
    )

    with pytest.raises(module.ContextBoundCatalogAcceptanceReadinessError) as captured:
        module.assess_context_bound_catalog_acceptance_readiness(
            sps_context=sps_context,
            provenance=provenance,
        )
    assert captured.value.code == "catalog_context_readiness_manifest_page_set_mismatch"


def test_autoridad_fabricada_en_provenance_se_rechaza(monkeypatch) -> None:
    sps_context, provenance, _plan_value, _captured = _prepare(monkeypatch)
    object.__setattr__(provenance, "production_authority", True)

    with pytest.raises(module.ContextBoundCatalogAcceptanceReadinessError) as captured:
        module.assess_context_bound_catalog_acceptance_readiness(
            sps_context=sps_context,
            provenance=provenance,
        )
    assert captured.value.code == "catalog_context_readiness_provenance_authority_forbidden"


def test_resultado_no_permite_catalog_accepted_true() -> None:
    with pytest.raises(module.ContextBoundCatalogAcceptanceReadinessError) as captured:
        module.ContextBoundCatalogAcceptanceReadiness(
            coverage=_coverage(TRUST_GATE),
            discovery_digest=DISCOVERY_DIGEST,
            authenticated_plan_digest=PLAN_DIGEST,
            provenance_manifest_digest=MANIFEST_DIGEST,
            structural_context_plan_digest=STRUCTURAL_PLAN_DIGEST,
            location_id=LOCATION,
            context_fingerprint=CONTEXT_FINGERPRINT,
            technical_catalog_complete=True,
            ready_for_productive_authority_evidence=True,
            catalog_accepted=True,
            blockers=(TRUST_GATE, AUTHORITY_BLOCKER),
        )
    assert captured.value.code == "catalog_context_readiness_catalog_acceptance_forbidden"


def test_api_no_expone_overrides_de_contexto_authority_ni_request_limit() -> None:
    parameters = inspect.signature(
        module.assess_context_bound_catalog_acceptance_readiness
    ).parameters
    assert tuple(parameters) == ("sps_context", "provenance")
    for forbidden in (
        "catalog_accepted",
        "production_authority",
        "request_limit",
        "location_id",
        "context_fingerprint",
    ):
        assert forbidden not in parameters
