from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from precios_supermercados.cloudflare_trace_evidence import (
    CLOUD_PLATFORM,
    CLOUD_PROVIDER,
    ORIGIN_EXECUTION_SPAN_NAME,
    TRACE_CONTRACT_VERSION,
    CloudflareOriginTraceEvidence,
    CloudflareTraceEvidenceError,
    PlatformReconciledEdgePage,
    assert_distinct_primary_reconciliation_evidence,
    reconcile_cloudflare_origin_trace,
)
from precios_supermercados.edge_crypto_page import CryptographicallyVerifiedEdgeCatalogPage
from precios_supermercados.edge_provenance import EdgeReceiptPayload, SignedEdgeReceipt
from precios_supermercados.edge_receipt_crypto import CryptographicallyVerifiedEdgeReceipt
from precios_supermercados.la_colonia_edge_body import validate_la_colonia_edge_body
from precios_supermercados.la_colonia_edge_request import validate_la_colonia_edge_request
from precios_supermercados.scrapers.la_colonia_graphql import build_product_search_url


def _signature() -> str:
    return base64.urlsafe_b64encode(bytes(range(64))).decode("ascii").rstrip("=")


def _page(*, role: str = "primary", traversal_id: str = "traversal-primary-001", reservation_id: str = "reservation-primary-001") -> CryptographicallyVerifiedEdgeCatalogPage:
    url = build_product_search_url(
        page=1,
        page_size=2,
        query="supermercado",
        category_map="category-1",
        order_by="OrderByNameASC",
    )
    request = validate_la_colonia_edge_request(url)
    raw = json.dumps(
        {"data": {"productSearch": {"recordsFiltered": 2, "products": [{}, {}]}}},
        separators=(",", ":"),
    ).encode()
    body = validate_la_colonia_edge_body(raw, request)
    payload = EdgeReceiptPayload(
        run_id="32516206820:1",
        request_id=f"request-{role}-001",
        reservation_id=reservation_id,
        authorization_id="authorization-trace-001",
        approved_commit_sha="a" * 40,
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
        physical_started_at_utc=datetime(2026, 8, 21, 19, 0, 0, 100_000, tzinfo=timezone.utc),
        response_completed_at_utc=datetime(2026, 8, 21, 19, 0, 0, 900_000, tzinfo=timezone.utc),
        github_repository="jchernandez-portfolio/precios-supermercados-sps",
        github_repository_id="1282475205",
        github_ref="refs/heads/main",
        github_workflow_ref="jchernandez-portfolio/precios-supermercados-sps/.github/workflows/precios-supermercados-sps-la-colonia-live.yml@refs/heads/main",
        github_environment="la-colonia-live",
        github_run_id="32516206820",
        github_run_attempt=1,
        oidc_subject="repo:jchernandez-portfolio/precios-supermercados-sps:environment:la-colonia-live",
        oidc_jti=f"oidc-jti-{role}-001",
        collector_provider="cloudflare_workers",
        collector_principal="cloudflare-worker:precios-sps-provenance",
        collector_execution=f"execution-{role}-001",
        collector_release_id="cf-version-trace-001",
        collector_code_sha256="d" * 64,
        signing_algorithm="Ed25519",
        signing_key_id="edge-signing-key-001",
        nonce=f"nonce-{role}-001",
    )
    receipt = SignedEdgeReceipt(payload=payload, signature_b64url=_signature())
    verified = CryptographicallyVerifiedEdgeReceipt(
        receipt=receipt,
        signing_key_id=payload.signing_key_id,
        public_key_spki_sha256="e" * 64,
        receipt_digest=receipt.digest,
    )
    return CryptographicallyVerifiedEdgeCatalogPage(
        request=request,
        body=body,
        verified_receipt=verified,
        worker_evidence_id="f" * 64,
        replayed=False,
    )


def _trace(page: CryptographicallyVerifiedEdgeCatalogPage, **overrides: object) -> CloudflareOriginTraceEvidence:
    payload = page.verified_receipt.receipt.payload
    values: dict[str, object] = {
        "trace_id": f"trace-{payload.traversal_role}-001",
        "custom_span_id": f"custom-{payload.traversal_role}-001",
        "fetch_span_id": f"fetch-{payload.traversal_role}-001",
        "fetch_parent_span_id": f"custom-{payload.traversal_role}-001",
        "faas_invocation_id": f"invocation-{payload.traversal_role}-001",
        "service_name": "precios-sps-provenance",
        "script_version_id": payload.collector_release_id,
        "custom_span_name": ORIGIN_EXECUTION_SPAN_NAME,
        "trace_contract_version": TRACE_CONTRACT_VERSION,
        "cloud_provider": CLOUD_PROVIDER,
        "cloud_platform": CLOUD_PLATFORM,
        "collector_provider": "cloudflare_workers",
        "authorization_id": payload.authorization_id,
        "run_id": payload.run_id,
        "approved_commit_sha": payload.approved_commit_sha,
        "reservation_id": payload.reservation_id,
        "request_id": payload.request_id,
        "request_digest": payload.request_digest,
        "traversal_role": payload.traversal_role,
        "traversal_id": payload.traversal_id,
        "partition_id": payload.partition_id,
        "fetch_url": page.source_url,
        "fetch_method": "GET",
        "fetch_status": 200,
        "fetch_response_body_size": payload.response_body_bytes,
        "custom_started_at_utc": datetime(2026, 8, 21, 18, 59, 59, 900_000, tzinfo=timezone.utc),
        "custom_completed_at_utc": datetime(2026, 8, 21, 19, 0, 1, 100_000, tzinfo=timezone.utc),
        "fetch_started_at_utc": datetime(2026, 8, 21, 19, 0, 0, 150_000, tzinfo=timezone.utc),
        "fetch_completed_at_utc": datetime(2026, 8, 21, 19, 0, 0, 500_000, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return CloudflareOriginTraceEvidence(**values)  # type: ignore[arg-type]


def test_evidencia_normalizada_deriva_id_determinista_y_no_autoridad() -> None:
    page = _page()
    evidence = _trace(page)
    same = _trace(page)
    changed = _trace(page, faas_invocation_id="invocation-primary-002")

    assert evidence.physical_evidence_id == same.physical_evidence_id
    assert evidence.physical_evidence_id != changed.physical_evidence_id
    assert len(evidence.physical_evidence_id) == 64
    assert evidence.production_authority is False
    assert evidence.canonical_dict()["platform_provider"] == "cloudflare_workers_tracing"


def test_reconciliacion_exige_unico_fetch_coherente() -> None:
    page = _page()
    evidence = _trace(page)
    result = reconcile_cloudflare_origin_trace(page, [evidence])

    assert isinstance(result, PlatformReconciledEdgePage)
    assert result.platform_evidence_reconciled is True
    assert result.production_authority is False
    assert result.physical_evidence_id == evidence.physical_evidence_id

    with pytest.raises(CloudflareTraceEvidenceError) as missing:
        reconcile_cloudflare_origin_trace(page, [])
    assert missing.value.code == "matching_trace_missing"

    duplicate = replace(evidence, fetch_span_id="fetch-primary-duplicate")
    with pytest.raises(CloudflareTraceEvidenceError) as non_unique:
        reconcile_cloudflare_origin_trace(page, [evidence, duplicate])
    assert non_unique.value.code == "matching_trace_not_unique"


def test_candidato_de_otro_request_no_cuenta_como_match() -> None:
    page = _page()
    other = _trace(page, request_id="request-attacker")
    with pytest.raises(CloudflareTraceEvidenceError) as captured:
        reconcile_cloudflare_origin_trace(page, [other])
    assert captured.value.code == "matching_trace_missing"


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"fetch_url": "https://www.lacolonia.com/otro"}, "trace_fetch_url_mismatch"),
        ({"fetch_status": 201}, "trace_fetch_status_mismatch"),
        ({"fetch_response_body_size": 1}, "trace_fetch_body_size_mismatch"),
        ({"script_version_id": "cf-version-wrong"}, "trace_script_version_mismatch"),
        (
            {"fetch_started_at_utc": datetime(2026, 8, 21, 18, 59, 40, tzinfo=timezone.utc)},
            "fetch_span_outside_custom_span",
        ),
    ],
)
def test_reconciliacion_rechaza_evidencia_fisica_incoherente(overrides: dict[str, object], code: str) -> None:
    page = _page()
    try:
        evidence = _trace(page, **overrides)
    except CloudflareTraceEvidenceError as exc:
        assert exc.code == code
        return
    with pytest.raises(CloudflareTraceEvidenceError) as captured:
        reconcile_cloudflare_origin_trace(page, [evidence])
    assert captured.value.code == code


def test_tiempo_de_fetch_fuera_de_ventana_del_receipt_falla() -> None:
    page = _page()
    evidence = _trace(
        page,
        custom_started_at_utc=datetime(2026, 8, 21, 19, 0, 15, tzinfo=timezone.utc),
        custom_completed_at_utc=datetime(2026, 8, 21, 19, 0, 17, tzinfo=timezone.utc),
        fetch_started_at_utc=datetime(2026, 8, 21, 19, 0, 15, 100_000, tzinfo=timezone.utc),
        fetch_completed_at_utc=datetime(2026, 8, 21, 19, 0, 15, 500_000, tzinfo=timezone.utc),
    )
    with pytest.raises(CloudflareTraceEvidenceError) as captured:
        reconcile_cloudflare_origin_trace(page, [evidence])
    assert captured.value.code == "trace_fetch_started_too_late"


def test_parent_span_y_plataforma_son_fail_closed() -> None:
    page = _page()
    with pytest.raises(CloudflareTraceEvidenceError) as parent:
        _trace(page, fetch_parent_span_id="otro-parent")
    assert parent.value.code == "fetch_parent_span_mismatch"

    with pytest.raises(CloudflareTraceEvidenceError) as platform:
        _trace(page, cloud_platform="otro")
    assert platform.value.code == "cloud_platform_invalid"


def test_primary_y_reconciliation_requieren_evidencia_fisica_distinta() -> None:
    primary_page = _page()
    recon_page = _page(
        role="reconciliation",
        traversal_id="traversal-reconciliation-001",
        reservation_id="reservation-reconciliation-001",
    )
    primary = reconcile_cloudflare_origin_trace(primary_page, [_trace(primary_page)])
    reconciliation = reconcile_cloudflare_origin_trace(recon_page, [_trace(recon_page)])

    assert_distinct_primary_reconciliation_evidence(primary, reconciliation)

    reused_trace = replace(
        reconciliation.trace_evidence,
        trace_id=primary.trace_evidence.trace_id,
        custom_span_id=primary.trace_evidence.custom_span_id,
        fetch_parent_span_id=primary.trace_evidence.custom_span_id,
    )
    reused = PlatformReconciledEdgePage(page=recon_page, trace_evidence=reused_trace)
    with pytest.raises(CloudflareTraceEvidenceError) as captured:
        assert_distinct_primary_reconciliation_evidence(primary, reused)
    assert captured.value.code == "custom_trace_span_reused"


def test_production_authority_no_puede_ser_declarada_en_evidencia() -> None:
    page = _page()
    with pytest.raises(CloudflareTraceEvidenceError) as captured:
        _trace(page, production_authority=True)
    assert captured.value.code == "production_authority_forbidden"
