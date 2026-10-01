from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from precios_supermercados.cloudflare_live_preflight import (
    CloudflareDeploymentEvidence,
    CloudflareLivePreflightError,
    assess_cloudflare_live_preflight,
)
from precios_supermercados.edge_provenance_run import (
    EdgeProvenanceRunManifest,
    ExpectedProvenancePage,
    ProvenancePageRecord,
)


def _record(*, role: str, traversal_id: str, order_by: str, suffix: str) -> ProvenancePageRecord:
    expected = ExpectedProvenancePage(
        traversal_role=role,
        traversal_id=traversal_id,
        partition_id="root",
        order_by=order_by,
        from_index=0,
        to_index=1,
        request_digest=("a" if role == "primary" else "b") * 64,
    )
    return ProvenancePageRecord(
        expected=expected,
        request_id=f"request-{suffix}",
        reservation_id=f"reservation-{suffix}",
        nonce=f"nonce-{suffix}",
        receipt_digest=("1" if role == "primary" else "2") * 64,
        worker_evidence_id=("3" if role == "primary" else "4") * 64,
        physical_evidence_id=("5" if role == "primary" else "6") * 64,
        trace_id=f"trace-{suffix}",
        custom_span_id=f"custom-{suffix}",
        fetch_span_id=f"fetch-{suffix}",
        raw_response_sha256=("7" if role == "primary" else "8") * 64,
        physical_started_at_utc=datetime(2026, 8, 21, 20, 0, 0, tzinfo=timezone.utc),
        response_completed_at_utc=datetime(2026, 8, 21, 20, 0, 1, tzinfo=timezone.utc),
    )


def _manifest() -> EdgeProvenanceRunManifest:
    return EdgeProvenanceRunManifest(
        run_id="32521000000:1",
        authorization_id="authorization-preflight-001",
        approved_commit_sha="c" * 40,
        github_repository="jchernandez-portfolio/precios-supermercados-sps",
        github_repository_id="1282475205",
        github_ref="refs/heads/main",
        github_workflow_ref="jchernandez-portfolio/precios-supermercados-sps/.github/workflows/precios-supermercados-sps-la-colonia-live.yml@refs/heads/main",
        github_environment="la-colonia-live",
        collector_provider="cloudflare_workers",
        collector_principal="cloudflare-worker:precios-sps-provenance",
        collector_release_id="cf-version-preflight-001",
        collector_code_sha256="d" * 64,
        collector_signing_key_id="edge-signing-key-preflight-001",
        primary_traversal_id="traversal-primary-001",
        reconciliation_traversal_id="traversal-reconciliation-001",
        primary_order_by="OrderByNameASC",
        reconciliation_order_by="OrderByNameDESC",
        pages=(
            _record(
                role="primary",
                traversal_id="traversal-primary-001",
                order_by="OrderByNameASC",
                suffix="primary",
            ),
            _record(
                role="reconciliation",
                traversal_id="traversal-reconciliation-001",
                order_by="OrderByNameDESC",
                suffix="reconciliation",
            ),
        ),
    )


def _deployment() -> CloudflareDeploymentEvidence:
    return CloudflareDeploymentEvidence(
        account_id_sha256="9" * 64,
        script_name="precios-sps-provenance",
        deployment_id="deployment-preflight-001",
        script_version_id="cf-version-preflight-001",
        deployed_code_sha256="d" * 64,
        signing_key_id="edge-signing-key-preflight-001",
        signing_public_key_spki_sha256="e" * 64,
        tracing_enabled=True,
        tracing_sampling_rate_ppm=1_000_000,
        observability_enabled=True,
        observability_query_roundtrip_succeeded=True,
        egress_allowlist_enforced=True,
        allowed_origin_host="www.lacolonia.com",
        observed_at_utc=datetime(2026, 8, 21, 20, 5, tzinfo=timezone.utc),
    )


def test_preflight_tecnico_completo_solo_habilita_pedir_autorizacion_humana() -> None:
    assessment = assess_cloudflare_live_preflight(_manifest(), _deployment())

    assert assessment.technical_prerequisites_satisfied is True
    assert assessment.ready_for_human_live_authorization_request is True
    assert assessment.production_authority is False
    assert assessment.blockers == (
        "human_live_authorization_required",
        "sps_context_unconfirmed",
        "production_authority_not_established",
    )
    assert len(assessment.digest) == 64


def test_mismatch_de_release_y_codigo_falla_cerrado() -> None:
    deployment = replace(
        _deployment(),
        script_version_id="cf-version-other",
        deployed_code_sha256="f" * 64,
    )
    assessment = assess_cloudflare_live_preflight(_manifest(), deployment)

    assert assessment.technical_prerequisites_satisfied is False
    assert assessment.ready_for_human_live_authorization_request is False
    assert "deployment_release_mismatch" in assessment.blockers
    assert "deployment_code_mismatch" in assessment.blockers


def test_tracing_debe_estar_habilitado_y_muestreado_al_cien_por_ciento() -> None:
    disabled = assess_cloudflare_live_preflight(
        _manifest(),
        replace(_deployment(), tracing_enabled=False),
    )
    partial = assess_cloudflare_live_preflight(
        _manifest(),
        replace(_deployment(), tracing_sampling_rate_ppm=500_000),
    )

    assert "tracing_disabled" in disabled.blockers
    assert "tracing_sampling_not_full" in partial.blockers


def test_observability_y_egress_requieren_evidencia_explicita() -> None:
    assessment = assess_cloudflare_live_preflight(
        _manifest(),
        replace(
            _deployment(),
            observability_enabled=False,
            observability_query_roundtrip_succeeded=False,
            egress_allowlist_enforced=False,
        ),
    )

    assert assessment.technical_prerequisites_satisfied is False
    assert "observability_disabled" in assessment.blockers
    assert "observability_roundtrip_unverified" in assessment.blockers
    assert "egress_allowlist_unverified" in assessment.blockers


def test_deployment_evidence_digest_es_determinista_en_utc() -> None:
    utc = _deployment()
    honduras = replace(
        utc,
        observed_at_utc=utc.observed_at_utc.astimezone(timezone(timedelta(hours=-6))),
    )
    assert utc.digest == honduras.digest


def test_snapshot_no_admite_fuente_host_o_autoridad_fabricados() -> None:
    with pytest.raises(CloudflareLivePreflightError) as source:
        replace(_deployment(), source="manual")
    assert source.value.code == "deployment_source_invalid"

    with pytest.raises(CloudflareLivePreflightError) as host:
        replace(_deployment(), allowed_origin_host="example.com")
    assert host.value.code == "deployment_allowed_origin_host_invalid"

    with pytest.raises(CloudflareLivePreflightError) as authority:
        replace(_deployment(), production_authority=True)
    assert authority.value.code == "deployment_production_authority_forbidden"


def test_assessment_no_admite_autoridad_productiva_declarada() -> None:
    assessment = assess_cloudflare_live_preflight(_manifest(), _deployment())
    with pytest.raises(CloudflareLivePreflightError) as captured:
        replace(assessment, production_authority=True)
    assert captured.value.code == "assessment_production_authority_forbidden"
