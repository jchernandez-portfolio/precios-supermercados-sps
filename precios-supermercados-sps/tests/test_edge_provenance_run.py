from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from precios_supermercados.cloudflare_trace_evidence import (
    CLOUD_PLATFORM,
    CLOUD_PROVIDER,
    ORIGIN_EXECUTION_SPAN_NAME,
    TRACE_CONTRACT_VERSION,
    CloudflareOriginTraceEvidence,
    PlatformReconciledEdgePage,
    reconcile_cloudflare_origin_trace,
)
from precios_supermercados.edge_crypto_page import CryptographicallyVerifiedEdgeCatalogPage
from precios_supermercados.edge_provenance import EdgeReceiptPayload, SignedEdgeReceipt
from precios_supermercados.edge_provenance_run import (
    EdgeProvenanceRunError,
    ExpectedProvenancePage,
    build_edge_provenance_run_manifest,
)
from precios_supermercados.edge_receipt_crypto import CryptographicallyVerifiedEdgeReceipt
from precios_supermercados.la_colonia_edge_body import validate_la_colonia_edge_body
from precios_supermercados.la_colonia_edge_request import validate_la_colonia_edge_request
from precios_supermercados.scrapers.la_colonia_graphql import build_product_search_url

RUN_ID = "32520000000:1"
AUTHORIZATION_ID = "authorization-run-manifest-001"
COMMIT_SHA = "a" * 40
COLLECTOR_RELEASE = "cf-version-run-manifest-001"
COLLECTOR_KEY = "edge-signing-key-run-manifest-001"


def _signature(seed: int) -> str:
    raw = bytes((index + seed) % 256 for index in range(64))
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _platform_page(
    *,
    role: str,
    traversal_id: str,
    order_by: str,
    suffix: str,
    page_number: int = 1,
) -> PlatformReconciledEdgePage:
    url = build_product_search_url(
        page=page_number,
        page_size=2,
        query="supermercado",
        category_map="category-1",
        order_by=order_by,
    )
    request = validate_la_colonia_edge_request(url)
    raw = json.dumps(
        {"data": {"productSearch": {"recordsFiltered": 4, "products": [{}, {}]}}},
        separators=(",", ":"),
    ).encode()
    body = validate_la_colonia_edge_body(raw, request)
    payload = EdgeReceiptPayload(
        run_id=RUN_ID,
        request_id=f"request-{suffix}",
        reservation_id=f"reservation-{suffix}",
        authorization_id=AUTHORIZATION_ID,
        approved_commit_sha=COMMIT_SHA,
        request_digest=request.canonical_request_sha256,
        traversal_id=traversal_id,
        traversal_role=role,
        order_by=request.order_by,
        partition_id="root",
        from_index=request.from_index,
        to_index=request.to_index,
        http_method="GET",
        target_scheme="https",
        target_host="www.lacolonia.com",
        target_path="/_v/segment/graphql/v1",
        canonical_request_sha256=request.canonical_request_sha256,
        raw_response_sha256=hashlib.sha256(raw).hexdigest(),
        response_status=200,
        response_body_bytes=len(raw),
        physical_started_at_utc=datetime(2026, 8, 21, 19, 45, page_number, 100_000, tzinfo=timezone.utc),
        response_completed_at_utc=datetime(2026, 8, 21, 19, 45, page_number, 900_000, tzinfo=timezone.utc),
        github_repository="jchernandez-portfolio/precios-supermercados-sps",
        github_repository_id="1282475205",
        github_ref="refs/heads/main",
        github_workflow_ref="jchernandez-portfolio/precios-supermercados-sps/.github/workflows/precios-supermercados-sps-la-colonia-live.yml@refs/heads/main",
        github_environment="la-colonia-live",
        github_run_id="32520000000",
        github_run_attempt=1,
        oidc_subject="repo:jchernandez-portfolio/precios-supermercados-sps:environment:la-colonia-live",
        oidc_jti=f"oidc-jti-{suffix}",
        collector_provider="cloudflare_workers",
        collector_principal="cloudflare-worker:precios-sps-provenance",
        collector_execution=f"execution-{suffix}",
        collector_release_id=COLLECTOR_RELEASE,
        collector_code_sha256="d" * 64,
        signing_algorithm="Ed25519",
        signing_key_id=COLLECTOR_KEY,
        nonce=f"nonce-{suffix}",
    )
    receipt = SignedEdgeReceipt(payload=payload, signature_b64url=_signature(sum(map(ord, suffix)) % 191))
    verified_receipt = CryptographicallyVerifiedEdgeReceipt(
        receipt=receipt,
        signing_key_id=payload.signing_key_id,
        public_key_spki_sha256="e" * 64,
        receipt_digest=receipt.digest,
    )
    crypto_page = CryptographicallyVerifiedEdgeCatalogPage(
        request=request,
        body=body,
        verified_receipt=verified_receipt,
        worker_evidence_id=hashlib.sha256(f"worker:{suffix}".encode()).hexdigest(),
        replayed=False,
    )
    trace = CloudflareOriginTraceEvidence(
        trace_id=f"trace-{suffix}",
        custom_span_id=f"custom-{suffix}",
        fetch_span_id=f"fetch-{suffix}",
        fetch_parent_span_id=f"custom-{suffix}",
        faas_invocation_id=f"invocation-{suffix}",
        service_name="precios-sps-provenance",
        script_version_id=payload.collector_release_id,
        custom_span_name=ORIGIN_EXECUTION_SPAN_NAME,
        trace_contract_version=TRACE_CONTRACT_VERSION,
        cloud_provider=CLOUD_PROVIDER,
        cloud_platform=CLOUD_PLATFORM,
        collector_provider="cloudflare_workers",
        authorization_id=payload.authorization_id,
        run_id=payload.run_id,
        approved_commit_sha=payload.approved_commit_sha,
        reservation_id=payload.reservation_id,
        request_id=payload.request_id,
        request_digest=payload.request_digest,
        traversal_role=payload.traversal_role,
        traversal_id=payload.traversal_id,
        partition_id=payload.partition_id,
        fetch_url=crypto_page.source_url,
        fetch_method="GET",
        fetch_status=200,
        fetch_response_body_size=payload.response_body_bytes,
        custom_started_at_utc=datetime(2026, 8, 21, 19, 45, page_number, 0, tzinfo=timezone.utc),
        custom_completed_at_utc=datetime(2026, 8, 21, 19, 45, page_number + 1, 0, tzinfo=timezone.utc),
        fetch_started_at_utc=datetime(2026, 8, 21, 19, 45, page_number, 150_000, tzinfo=timezone.utc),
        fetch_completed_at_utc=datetime(2026, 8, 21, 19, 45, page_number, 500_000, tzinfo=timezone.utc),
    )
    return reconcile_cloudflare_origin_trace(crypto_page, [trace])


def _set() -> tuple[list[ExpectedProvenancePage], list[PlatformReconciledEdgePage]]:
    pages = [
        _platform_page(
            role="primary",
            traversal_id="traversal-primary-001",
            order_by="OrderByNameASC",
            suffix="primary-1",
        ),
        _platform_page(
            role="reconciliation",
            traversal_id="traversal-reconciliation-001",
            order_by="OrderByNameDESC",
            suffix="reconciliation-1",
        ),
    ]
    expected = []
    for page in pages:
        payload = page.page.verified_receipt.receipt.payload
        expected.append(
            ExpectedProvenancePage(
                traversal_role=payload.traversal_role,
                traversal_id=payload.traversal_id,
                partition_id=payload.partition_id,
                order_by=payload.order_by,
                from_index=payload.from_index,
                to_index=payload.to_index,
                request_digest=payload.request_digest,
            )
        )
    return expected, pages


def test_manifest_cierra_plan_exacto_y_es_determinista_independiente_del_orden() -> None:
    expected, pages = _set()
    direct = build_edge_provenance_run_manifest(expected_pages=expected, reconciled_pages=pages)
    reversed_manifest = build_edge_provenance_run_manifest(
        expected_pages=list(reversed(expected)),
        reconciled_pages=list(reversed(pages)),
    )

    assert direct.digest == reversed_manifest.digest
    assert direct.request_count == 2
    assert direct.production_authority is False
    assert direct.run_id == RUN_ID
    assert direct.authorization_id == AUTHORIZATION_ID
    assert direct.primary_traversal_id == "traversal-primary-001"
    assert direct.reconciliation_traversal_id == "traversal-reconciliation-001"
    assert len({record.physical_evidence_id for record in direct.pages}) == 2


def test_plan_con_pagina_faltante_falla() -> None:
    expected, pages = _set()
    with pytest.raises(EdgeProvenanceRunError) as captured:
        build_edge_provenance_run_manifest(expected_pages=expected, reconciled_pages=pages[:1])
    assert captured.value.code == "missing_provenance_page"


def test_pagina_no_planeada_falla() -> None:
    expected, pages = _set()
    extra = _platform_page(
        role="primary",
        traversal_id="traversal-primary-001",
        order_by="OrderByNameASC",
        suffix="primary-2",
        page_number=2,
    )
    with pytest.raises(EdgeProvenanceRunError) as captured:
        build_edge_provenance_run_manifest(expected_pages=expected, reconciled_pages=[*pages, extra])
    assert captured.value.code == "unexpected_provenance_page"


def test_plan_duplicado_falla_antes_de_consumir_evidencia() -> None:
    expected, pages = _set()
    with pytest.raises(EdgeProvenanceRunError) as captured:
        build_edge_provenance_run_manifest(
            expected_pages=[expected[0], expected[0], expected[1]],
            reconciled_pages=pages,
        )
    assert captured.value.code == "expected_page_duplicate"


def test_reutilizar_request_id_entre_traversals_falla() -> None:
    expected, pages = _set()
    second = pages[1]
    payload = second.page.verified_receipt.receipt.payload
    forged_payload = replace(
        payload,
        request_id=pages[0].page.verified_receipt.receipt.payload.request_id,
    )
    forged_receipt = replace(second.page.verified_receipt.receipt, payload=forged_payload)
    forged_verified = replace(
        second.page.verified_receipt,
        receipt=forged_receipt,
        receipt_digest=forged_receipt.digest,
    )
    forged_crypto = replace(second.page, verified_receipt=forged_verified)
    forged_trace = replace(second.trace_evidence, request_id=forged_payload.request_id)
    forged = reconcile_cloudflare_origin_trace(forged_crypto, [forged_trace])

    with pytest.raises(EdgeProvenanceRunError) as captured:
        build_edge_provenance_run_manifest(expected_pages=expected, reconciled_pages=[pages[0], forged])
    assert captured.value.code in {"unexpected_provenance_page", "request_id_reused"}


def test_contexto_run_distinto_falla() -> None:
    expected, pages = _set()
    second = pages[1]
    payload = second.page.verified_receipt.receipt.payload
    forged_payload = replace(payload, authorization_id="authorization-other")
    forged_receipt = replace(second.page.verified_receipt.receipt, payload=forged_payload)
    forged_verified = replace(
        second.page.verified_receipt,
        receipt=forged_receipt,
        receipt_digest=forged_receipt.digest,
    )
    forged_crypto = replace(second.page, verified_receipt=forged_verified)
    forged_trace = replace(second.trace_evidence, authorization_id="authorization-other")
    forged = reconcile_cloudflare_origin_trace(forged_crypto, [forged_trace])

    with pytest.raises(EdgeProvenanceRunError) as captured:
        build_edge_provenance_run_manifest(expected_pages=expected, reconciled_pages=[pages[0], forged])
    assert captured.value.code == "run_context_authorization_id_mismatch"


def test_wrapper_platform_fabricado_se_revalida_y_falla() -> None:
    expected, pages = _set()
    second = pages[1]
    forged = PlatformReconciledEdgePage(
        page=second.page,
        trace_evidence=pages[0].trace_evidence,
    )
    with pytest.raises(Exception):
        build_edge_provenance_run_manifest(expected_pages=expected, reconciled_pages=[pages[0], forged])


def test_manifest_no_admite_autoridad_productiva_declarada() -> None:
    expected, pages = _set()
    manifest = build_edge_provenance_run_manifest(expected_pages=expected, reconciled_pages=pages)
    with pytest.raises(EdgeProvenanceRunError) as captured:
        replace(manifest, production_authority=True)
    assert captured.value.code == "run_manifest_production_authority_forbidden"
