from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from precios_supermercados.authenticated_provenance_plan import (
    AuthenticatedCatalogProvenancePlan,
    AuthenticatedProvenancePlanError,
    derive_authenticated_catalog_provenance_plan,
)
from precios_supermercados.edge_provenance_plan import derive_catalog_provenance_plan
from precios_supermercados.scrapers.la_colonia_catalog_partitions import (
    build_structural_discovery_report,
)
from precios_supermercados.structural_discovery_manifest import (
    StructuralObservationRecord,
    VerifiedStructuralDiscovery,
)

RUN = "32528000000:1"
AUTH = "authorization-authenticated-plan"
COMMIT = "a" * 40
CODE_SHA = "d" * 64
KEY_ID = "cloudflare-ed25519-v1"
RELEASE = "release-authenticated-plan-001"
NOW = datetime(2026, 8, 21, 21, 0, 0, tzinfo=timezone.utc)


def _structure(*, total: int = 51):
    return build_structural_discovery_report(
        [
            {
                "type": "CATEGORY",
                "values": [
                    {
                        "key": "category-1",
                        "value": "supermercado",
                        "quantity": total,
                        "children": [],
                    }
                ],
            }
        ],
        run_id=RUN,
        root_total=total,
        sampling=False,
        max_partitions=250,
        max_category_level=8,
    )


def _record(kind: str, *, digest_char: str) -> StructuralObservationRecord:
    return StructuralObservationRecord(
        request_kind=kind,
        request_digest=digest_char * 64,
        request_id=f"request-{kind}",
        reservation_id=f"reservation-{kind}",
        nonce=f"nonce-{kind}",
        receipt_digest=("1" if kind == "root_total" else "2") * 64,
        public_key_spki_sha256="e" * 64,
        physical_evidence_id=("3" if kind == "root_total" else "4") * 64,
        trace_id=f"trace-{kind}",
        custom_span_id=f"custom-{kind}",
        fetch_span_id=f"fetch-{kind}",
        raw_response_sha256=("5" if kind == "root_total" else "6") * 64,
        response_body_bytes=100 if kind == "root_total" else 200,
        records_filtered=51,
        physical_started_at_utc=NOW,
        response_completed_at_utc=NOW,
    )


def _discovery() -> VerifiedStructuralDiscovery:
    structure = _structure()
    return VerifiedStructuralDiscovery(
        run_id=RUN,
        authorization_id=AUTH,
        approved_commit_sha=COMMIT,
        github_repository="jchernandez-portfolio/precios-supermercados-sps",
        github_repository_id="1282475205",
        github_ref="refs/heads/main",
        github_workflow_ref="jchernandez-portfolio/precios-supermercados-sps/.github/workflows/precios-supermercados-sps-la-colonia-live.yml@refs/heads/main",
        github_environment="la-colonia-live",
        github_run_id="32528000000",
        github_run_attempt=1,
        oidc_subject="repo:jchernandez-portfolio/precios-supermercados-sps:environment:la-colonia-live",
        collector_provider="cloudflare_workers",
        collector_principal="cloudflare-worker:precios-sps-provenance",
        collector_release_id=RELEASE,
        collector_code_sha256=CODE_SHA,
        collector_signing_key_id=KEY_ID,
        root_total=_record("root_total", digest_char="7"),
        category_tree=_record("category_tree", digest_char="8"),
        tree_digest=structure.tree_digest,
        leaf_partitions_count=1,
        positive_leaf_partitions=1,
        structure=structure,
    )


def _derive(discovery: VerifiedStructuralDiscovery | None = None):
    return derive_authenticated_catalog_provenance_plan(
        discovery or _discovery(),
        page_size=50,
        primary_traversal_id="traversal-primary-authenticated",
        reconciliation_traversal_id="traversal-reconciliation-authenticated",
        primary_order_by="OrderByNameASC",
        reconciliation_order_by="OrderByPriceDESC",
    )


def test_deriva_todas_las_paginas_desde_discovery_autenticado() -> None:
    discovery = _discovery()
    result = _derive(discovery)

    assert isinstance(result, AuthenticatedCatalogProvenancePlan)
    assert result.discovery is discovery
    assert result.discovery_digest == discovery.digest
    assert result.plan.run_id == discovery.run_id
    assert result.plan.tree_digest == discovery.tree_digest
    assert result.request_count == 4
    assert len(result.primary_pages) == 2
    assert len(result.reconciliation_pages) == 2
    assert result.production_authority is False
    assert len(result.digest) == 64


def test_canonical_publico_no_serializa_valores_privados_del_arbol() -> None:
    result = _derive()
    public = json.dumps(result.canonical_dict(), sort_keys=True)
    assert "supermercado" not in public
    assert "category-1" not in public
    assert "facet_value" not in public
    assert "facet_key" not in public
    assert result.discovery.structure.valid_leaves[0].facet_value == "supermercado"


def test_digest_es_reproducible_y_liga_discovery_y_plan() -> None:
    discovery = _discovery()
    first = _derive(discovery)
    second = _derive(discovery)
    assert first.digest == second.digest
    assert first.discovery_digest == second.discovery_digest
    assert first.plan.digest == second.plan.digest


def test_rechaza_objeto_que_no_es_verified_structural_discovery() -> None:
    with pytest.raises(AuthenticatedProvenancePlanError) as captured:
        derive_authenticated_catalog_provenance_plan(  # type: ignore[arg-type]
            object(),
            page_size=50,
            primary_traversal_id="primary",
            reconciliation_traversal_id="reconciliation",
            primary_order_by="OrderByNameASC",
            reconciliation_order_by="OrderByPriceDESC",
        )
    assert captured.value.code == "authenticated_plan_discovery_invalid"


def test_no_permite_mismo_traversal_ni_mismo_order() -> None:
    discovery = _discovery()
    with pytest.raises(AuthenticatedProvenancePlanError) as traversal:
        derive_authenticated_catalog_provenance_plan(
            discovery,
            page_size=50,
            primary_traversal_id="same",
            reconciliation_traversal_id="same",
            primary_order_by="OrderByNameASC",
            reconciliation_order_by="OrderByPriceDESC",
        )
    assert traversal.value.code == "derived_derived_plan_traversal_ids_not_distinct"

    with pytest.raises(AuthenticatedProvenancePlanError) as order:
        derive_authenticated_catalog_provenance_plan(
            discovery,
            page_size=50,
            primary_traversal_id="primary",
            reconciliation_traversal_id="reconciliation",
            primary_order_by="OrderByNameASC",
            reconciliation_order_by="OrderByNameASC",
        )
    assert order.value.code == "derived_derived_plan_orders_not_distinct"


def test_constructor_rechaza_discovery_digest_fabricado() -> None:
    valid = _derive()
    with pytest.raises(AuthenticatedProvenancePlanError) as captured:
        replace(valid, discovery_digest="f" * 64)
    assert captured.value.code == "authenticated_plan_discovery_digest_mismatch"


def test_constructor_rechaza_plan_con_tree_digest_distinto() -> None:
    discovery = _discovery()
    raw_plan = derive_catalog_provenance_plan(
        discovery.structure,
        page_size=50,
        primary_traversal_id="primary",
        reconciliation_traversal_id="reconciliation",
        primary_order_by="OrderByNameASC",
        reconciliation_order_by="OrderByPriceDESC",
    )
    mismatched = replace(raw_plan, tree_digest="f" * 64)
    with pytest.raises(AuthenticatedProvenancePlanError) as captured:
        AuthenticatedCatalogProvenancePlan(
            discovery_digest=discovery.digest,
            run_id=discovery.run_id,
            authorization_id=discovery.authorization_id,
            approved_commit_sha=discovery.approved_commit_sha,
            collector_release_id=discovery.collector_release_id,
            collector_code_sha256=discovery.collector_code_sha256,
            collector_signing_key_id=discovery.collector_signing_key_id,
            plan=mismatched,
            discovery=discovery,
        )
    assert captured.value.code == "authenticated_plan_tree_digest_mismatch"


def test_constructor_rechaza_contexto_que_no_coincide_con_discovery() -> None:
    valid = _derive()
    with pytest.raises(AuthenticatedProvenancePlanError) as captured:
        replace(valid, authorization_id="authorization-attacker")
    assert captured.value.code == "authenticated_plan_authorization_id_mismatch"
