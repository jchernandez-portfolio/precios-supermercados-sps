from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from precios_supermercados.authenticated_provenance_plan import AuthenticatedCatalogProvenancePlan
from precios_supermercados.catalog_context_provenance import ContextBoundEdgeReceiptPayload
from precios_supermercados.context_bound_query_provenance_run import (
    ContextBoundQueryProvenanceRunError,
    build_context_bound_query_provenance_run_manifest,
)
from precios_supermercados.context_bound_query_trace_evidence import (
    RedactedContextBoundQueryPage,
    RedactedContextBoundQueryTraceEvidence,
)
from precios_supermercados.edge_crypto_page import CryptographicallyVerifiedEdgeCatalogPage
from precios_supermercados.edge_provenance_run import ExpectedProvenancePage

RUN_ID = "32771000000:1"
AUTH = "auth-query-run-001"
SHA = "a" * 40
RELEASE = "worker-release-query-run-001"
CODE_SHA = "b" * 64
SIGNING_KEY = "edge-signing-key-query-001"
CONTEXT_FP = "c" * 64
START = datetime(2026, 8, 24, 20, 0, tzinfo=timezone.utc)


def _expected(index: int, *, role: str, traversal: str, order: str) -> ExpectedProvenancePage:
    return ExpectedProvenancePage(
        traversal_role=role,
        traversal_id=traversal,
        partition_id="root",
        order_by=order,
        from_index=index * 2,
        to_index=index * 2 + 1,
        request_digest=f"{index + 1:x}" * 64,
    )


def _plan(pages: tuple[ExpectedProvenancePage, ...]) -> AuthenticatedCatalogProvenancePlan:
    derived = SimpleNamespace(
        production_authority=False,
        request_count=len(pages),
        pages=pages,
        primary_traversal_id="traversal-primary",
        reconciliation_traversal_id="traversal-reconciliation",
        primary_order_by="OrderByNameASC",
        reconciliation_order_by="OrderByPriceDESC",
    )
    plan = object.__new__(AuthenticatedCatalogProvenancePlan)
    object.__setattr__(plan, "discovery_digest", "d" * 64)
    object.__setattr__(plan, "run_id", RUN_ID)
    object.__setattr__(plan, "authorization_id", AUTH)
    object.__setattr__(plan, "approved_commit_sha", SHA)
    object.__setattr__(plan, "collector_release_id", RELEASE)
    object.__setattr__(plan, "collector_code_sha256", CODE_SHA)
    object.__setattr__(plan, "collector_signing_key_id", SIGNING_KEY)
    object.__setattr__(plan, "plan", derived)
    object.__setattr__(plan, "discovery", SimpleNamespace())
    object.__setattr__(plan, "schema_version", "1")
    object.__setattr__(plan, "production_authority", False)
    return plan


def _payload(expected: ExpectedProvenancePage, index: int) -> ContextBoundEdgeReceiptPayload:
    base = SimpleNamespace(
        authorization_id=AUTH,
        run_id=RUN_ID,
        approved_commit_sha=SHA,
        reservation_id=f"reservation-{index}",
        request_id=f"request-{index}",
        request_digest=expected.request_digest,
        traversal_role=expected.traversal_role,
        traversal_id=expected.traversal_id,
        partition_id=expected.partition_id,
        order_by=expected.order_by,
        from_index=expected.from_index,
        to_index=expected.to_index,
        nonce=f"nonce-{index}",
        response_status=200,
        response_body_bytes=100 + index,
        collector_release_id=RELEASE,
        collector_code_sha256=CODE_SHA,
        collector_provider="cloudflare_workers",
        collector_principal="cloudflare-worker:precios-sps-provenance",
        signing_key_id=SIGNING_KEY,
        raw_response_sha256=f"{index + 4:x}" * 64,
        physical_started_at_utc=START + timedelta(seconds=index),
        response_completed_at_utc=START + timedelta(seconds=index, milliseconds=500),
        github_repository="jchernandez-portfolio/precios-supermercados-sps",
        github_repository_id="1282475205",
        github_ref="refs/heads/main",
        github_workflow_ref=(
            "jchernandez-portfolio/precios-supermercados-sps/.github/workflows/"
            "precios-supermercados-sps-la-colonia-live.yml@refs/heads/main"
        ),
        github_environment="la-colonia-live",
    )
    payload = object.__new__(ContextBoundEdgeReceiptPayload)
    object.__setattr__(payload, "base", base)
    object.__setattr__(payload, "location_id", "la_colonia_sps")
    object.__setattr__(payload, "binding_source_key", f"request:regionid:sha256:{CONTEXT_FP}")
    object.__setattr__(payload, "binding_evidence", "location_binding_radiography:sha256:" + "e" * 64)
    object.__setattr__(payload, "context_fingerprint", CONTEXT_FP)
    object.__setattr__(payload, "context_placement", "query")
    object.__setattr__(payload, "context_wire_key", "regionId")
    object.__setattr__(payload, "context_value_path", ())
    object.__setattr__(payload, "wire_request_fingerprint", f"{index + 6:x}" * 64)
    return payload


def _crypto(payload: ContextBoundEdgeReceiptPayload, index: int) -> CryptographicallyVerifiedEdgeCatalogPage:
    page = object.__new__(CryptographicallyVerifiedEdgeCatalogPage)
    object.__setattr__(page, "request", SimpleNamespace(source_url=f"https://example.invalid/base-{index}"))
    object.__setattr__(page, "body", SimpleNamespace(payload={}))
    object.__setattr__(
        page,
        "verified_receipt",
        SimpleNamespace(
            receipt=SimpleNamespace(payload=payload),
            receipt_digest=f"{index + 8:x}" * 64,
        ),
    )
    object.__setattr__(page, "worker_evidence_id", f"{index + 10:x}" * 64)
    object.__setattr__(page, "replayed", False)
    object.__setattr__(page, "cryptographic_signature_verified", True)
    object.__setattr__(page, "production_authority", False)
    return page


def _redacted(expected: ExpectedProvenancePage, index: int) -> RedactedContextBoundQueryPage:
    payload = _payload(expected, index)
    crypto = _crypto(payload, index)
    trace = RedactedContextBoundQueryTraceEvidence(
        trace_id=f"trace-{index}",
        custom_span_id=f"custom-{index}",
        fetch_span_id=f"fetch-{index}",
        faas_invocation_id=f"invocation-{index}",
        service_name="precios-sps-provenance",
        script_version_id=RELEASE,
        authorization_id=AUTH,
        run_id=RUN_ID,
        approved_commit_sha=SHA,
        reservation_id=payload.reservation_id,
        request_id=payload.request_id,
        request_digest=payload.request_digest,
        traversal_role=payload.traversal_role,
        traversal_id=payload.traversal_id,
        partition_id=payload.partition_id,
        context_fingerprint=CONTEXT_FP,
        wire_request_fingerprint=payload.wire_request_fingerprint,
        base_fetch_url_sha256=f"{index + 12:x}" * 64,
        raw_fetch_url_sha256=format((index + 14) % 16, "x") * 64,
        raw_trace_evidence_sha256=f"{index + 1:x}" * 64,
        fetch_status=200,
        fetch_response_body_size=payload.response_body_bytes,
        custom_started_at_utc=payload.physical_started_at_utc - timedelta(milliseconds=100),
        custom_completed_at_utc=payload.response_completed_at_utc + timedelta(milliseconds=100),
        fetch_started_at_utc=payload.physical_started_at_utc + timedelta(milliseconds=10),
        fetch_completed_at_utc=payload.response_completed_at_utc - timedelta(milliseconds=10),
    )
    return RedactedContextBoundQueryPage(page=crypto, trace_evidence=trace)


def _fixture():
    pages = (
        _expected(0, role="primary", traversal="traversal-primary", order="OrderByNameASC"),
        _expected(1, role="reconciliation", traversal="traversal-reconciliation", order="OrderByPriceDESC"),
    )
    return _plan(pages), tuple(_redacted(page, index) for index, page in enumerate(pages))


def test_manifest_query_redactado_conserva_shape_legacy_sin_url_fisico() -> None:
    plan, pages = _fixture()
    manifest = build_context_bound_query_provenance_run_manifest(
        authenticated_plan=plan,
        reconciled_pages=pages,
    )

    assert manifest.run_id == RUN_ID
    assert manifest.authorization_id == AUTH
    assert manifest.approved_commit_sha == SHA
    assert manifest.request_count == 2
    assert manifest.production_authority is False
    assert tuple(record.expected for record in manifest.pages) == plan.pages
    assert {record.physical_evidence_id for record in manifest.pages} == {
        page.physical_evidence_id for page in pages
    }
    rendered = str(manifest.canonical_dict())
    assert "fetch_url" not in rendered
    assert "regionId" not in rendered
    assert "raw_fetch_url_sha256" not in rendered


def test_input_fuera_del_plan_falla_cerrado() -> None:
    plan, pages = _fixture()
    foreign_expected = _expected(
        2,
        role="reconciliation",
        traversal="traversal-reconciliation",
        order="OrderByPriceDESC",
    )
    with pytest.raises(ContextBoundQueryProvenanceRunError) as captured:
        build_context_bound_query_provenance_run_manifest(
            authenticated_plan=plan,
            reconciled_pages=(pages[0], _redacted(foreign_expected, 2)),
        )
    assert captured.value.code == "query_run_expected_pages_mismatch"


def test_receipt_reutilizado_se_rechaza() -> None:
    plan, pages = _fixture()
    pages[1].page.verified_receipt.receipt_digest = pages[0].page.verified_receipt.receipt_digest
    with pytest.raises(ContextBoundQueryProvenanceRunError) as captured:
        build_context_bound_query_provenance_run_manifest(
            authenticated_plan=plan,
            reconciled_pages=pages,
        )
    assert captured.value.code == "query_run_receipt_reused"


def test_fingerprint_de_contexto_no_puede_cambiar_entre_paginas() -> None:
    plan, pages = _fixture()
    pages[1].page.verified_receipt.receipt.payload.context_fingerprint = "f" * 64
    object.__setattr__(pages[1].trace_evidence, "context_fingerprint", "f" * 64)
    with pytest.raises(ContextBoundQueryProvenanceRunError) as captured:
        build_context_bound_query_provenance_run_manifest(
            authenticated_plan=plan,
            reconciled_pages=pages,
        )
    assert captured.value.code == "query_run_context_context_fingerprint_mismatch"


def test_contexto_header_no_puede_usar_builder_query() -> None:
    plan, pages = _fixture()
    pages[0].page.verified_receipt.receipt.payload.context_placement = "header"
    with pytest.raises(ContextBoundQueryProvenanceRunError) as captured:
        build_context_bound_query_provenance_run_manifest(
            authenticated_plan=plan,
            reconciled_pages=pages,
        )
    assert captured.value.code == "query_run_query_placement_required"


def test_plan_y_receipt_deben_compartir_release_y_firma() -> None:
    plan, pages = _fixture()
    object.__setattr__(plan, "collector_release_id", "different-release")
    with pytest.raises(ContextBoundQueryProvenanceRunError) as captured:
        build_context_bound_query_provenance_run_manifest(
            authenticated_plan=plan,
            reconciled_pages=pages,
        )
    assert captured.value.code == "query_run_plan_collector_release_id_mismatch"
