from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from precios_supermercados.cloudflare_live_preflight import CloudflareDeploymentEvidence
from precios_supermercados.cloudflare_preflight_settings_binding import (
    CloudflareSettingsBindingError,
    bind_script_settings_to_preflight,
)
from precios_supermercados.cloudflare_script_settings import parse_cloudflare_script_settings
from precios_supermercados.edge_provenance_run import (
    EdgeProvenanceRunManifest,
    ExpectedProvenancePage,
    ProvenancePageRecord,
)


def _record(*, role: str, traversal_id: str, order_by: str, suffix: str) -> ProvenancePageRecord:
    return ProvenancePageRecord(
        expected=ExpectedProvenancePage(
            traversal_role=role,
            traversal_id=traversal_id,
            partition_id="root",
            order_by=order_by,
            from_index=0,
            to_index=1,
            request_digest=("a" if role == "primary" else "b") * 64,
        ),
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
        physical_started_at_utc=datetime(2026, 8, 21, 20, 0, tzinfo=timezone.utc),
        response_completed_at_utc=datetime(2026, 8, 21, 20, 0, 1, tzinfo=timezone.utc),
    )


def _manifest() -> EdgeProvenanceRunManifest:
    return EdgeProvenanceRunManifest(
        run_id="run-settings-binding-001",
        authorization_id="auth-settings-binding-001",
        approved_commit_sha="c" * 40,
        github_repository="jchernandez-portfolio/precios-supermercados-sps",
        github_repository_id="1282475205",
        github_ref="refs/heads/main",
        github_workflow_ref="jchernandez-portfolio/precios-supermercados-sps/.github/workflows/precios-supermercados-sps-la-colonia-live.yml@refs/heads/main",
        github_environment="la-colonia-live",
        collector_provider="cloudflare_workers",
        collector_principal="cloudflare-worker:precios-sps-provenance",
        collector_release_id="cf-version-settings-001",
        collector_code_sha256="d" * 64,
        collector_signing_key_id="edge-signing-key-settings-001",
        primary_traversal_id="traversal-primary-settings",
        reconciliation_traversal_id="traversal-reconciliation-settings",
        primary_order_by="OrderByNameASC",
        reconciliation_order_by="OrderByNameDESC",
        pages=(
            _record(role="primary", traversal_id="traversal-primary-settings", order_by="OrderByNameASC", suffix="primary"),
            _record(role="reconciliation", traversal_id="traversal-reconciliation-settings", order_by="OrderByNameDESC", suffix="reconciliation"),
        ),
    )


def _deployment() -> CloudflareDeploymentEvidence:
    return CloudflareDeploymentEvidence(
        account_id_sha256="9" * 64,
        script_name="precios-sps-provenance",
        deployment_id="deployment-settings-001",
        script_version_id="cf-version-settings-001",
        deployed_code_sha256="d" * 64,
        signing_key_id="edge-signing-key-settings-001",
        signing_public_key_spki_sha256="e" * 64,
        tracing_enabled=True,
        tracing_sampling_rate_ppm=1_000_000,
        observability_enabled=True,
        observability_query_roundtrip_succeeded=True,
        egress_allowlist_enforced=True,
        allowed_origin_host="www.lacolonia.com",
        observed_at_utc=datetime(2026, 8, 21, 20, 5, tzinfo=timezone.utc),
    )


def _settings(*, observability: bool = True, traces: bool = True, rate: float = 1.0):
    return parse_cloudflare_script_settings(
        {
            "success": True,
            "errors": [],
            "result": {
                "observability": {
                    "enabled": observability,
                    "head_sampling_rate": 1,
                    "traces": {
                        "enabled": traces,
                        "head_sampling_rate": rate,
                        "persist": True,
                        "propagation_policy": "authenticated",
                    },
                }
            },
        }
    )


def test_binding_completo_solo_habilita_pedir_autorizacion_humana() -> None:
    assessment = bind_script_settings_to_preflight(
        manifest=_manifest(), deployment=_deployment(), settings=_settings()
    )

    assert assessment.technical_prerequisites_satisfied is True
    assert assessment.ready_for_human_live_authorization_request is True
    assert assessment.production_authority is False
    assert assessment.blockers == (
        "human_live_authorization_required",
        "sps_context_unconfirmed",
        "production_authority_not_established",
    )
    assert len(assessment.digest) == 64


def test_settings_reales_pueden_desmentir_flags_deployment() -> None:
    assessment = bind_script_settings_to_preflight(
        manifest=_manifest(), deployment=_deployment(), settings=_settings(traces=False)
    )

    assert assessment.technical_prerequisites_satisfied is False
    assert "settings_tracing_disabled" in assessment.blockers
    assert "deployment_settings_tracing_mismatch" in assessment.blockers


def test_sampling_parcial_en_settings_bloquea_aunque_deployment_diga_cien() -> None:
    assessment = bind_script_settings_to_preflight(
        manifest=_manifest(), deployment=_deployment(), settings=_settings(rate=0.5)
    )

    assert "settings_tracing_sampling_not_full" in assessment.blockers
    assert "deployment_settings_sampling_mismatch" in assessment.blockers
    assert assessment.ready_for_human_live_authorization_request is False


def test_mismatch_observability_es_explicito() -> None:
    assessment = bind_script_settings_to_preflight(
        manifest=_manifest(), deployment=_deployment(), settings=_settings(observability=False)
    )

    assert "settings_observability_disabled" in assessment.blockers
    assert "deployment_settings_observability_mismatch" in assessment.blockers


def test_blockers_tecnicos_base_se_preservan() -> None:
    assessment = bind_script_settings_to_preflight(
        manifest=_manifest(),
        deployment=replace(_deployment(), egress_allowlist_enforced=False),
        settings=_settings(),
    )

    assert "egress_allowlist_unverified" in assessment.blockers
    assert assessment.technical_prerequisites_satisfied is False


def test_rechaza_objeto_settings_no_normalizado() -> None:
    with pytest.raises(CloudflareSettingsBindingError) as captured:
        bind_script_settings_to_preflight(
            manifest=_manifest(), deployment=_deployment(), settings={}  # type: ignore[arg-type]
        )
    assert captured.value.code == "settings_evidence_invalid"
