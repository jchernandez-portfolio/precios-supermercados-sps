"""Extiende la auditoría histórica de workflows sin degradar sus invariantes."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml


BASE_PATH = Path(__file__).with_name("_workflow_security_audit_base.py")
SPEC = importlib.util.spec_from_file_location("_workflow_security_audit_base", BASE_PATH)
assert SPEC is not None and SPEC.loader is not None
base = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(base)

base.PRODUCTION_OPERATOR_WORKFLOW = "precios-supermercados-sps-production-operator.yml"
base.PRODUCTION_UPDATE_REQUEST = (
    "precios-supermercados-sps/.automation/production-update-request.json"
)
base.SAFE_ANALYTICS_WORKFLOW = "precios-supermercados-sps-safe-analytics-publication.yml"
base.RPI_PUBLICATION_REQUEST = (
    "precios-supermercados-sps/.automation/rpi-publication-request.json"
)
base.TURSO_SCHEMA_MIGRATION_WORKFLOW = "precios-supermercados-sps-turso-schema-migration.yml"
base.TURSO_SCHEMA_MIGRATION_OPERATOR_WORKFLOW = (
    "precios-supermercados-sps-turso-schema-migration-operator.yml"
)
base.TURSO_SCHEMA_MIGRATION_REQUEST = (
    "precios-supermercados-sps/.automation/turso-schema-migration-request.json"
)
base.PORTFOLIO_DATA_SYNC_WORKFLOW = "precios-supermercados-sps-portfolio-data-sync.yml"
base.TURSO_WEEKLY_INTEGRITY_WORKFLOW = "precios-supermercados-sps-turso-weekly-integrity.yml"
base.EXPECTED_PERMISSIONS[base.PRODUCTION_OPERATOR_WORKFLOW] = {
    "actions": "write",
    "contents": "read",
}
base.EXPECTED_TRIGGERS[base.PRODUCTION_OPERATOR_WORKFLOW] = {"push", "schedule"}
base.EXPECTED_PERMISSIONS[base.SAFE_ANALYTICS_WORKFLOW] = {"contents": "read"}
base.EXPECTED_TRIGGERS[base.SAFE_ANALYTICS_WORKFLOW] = {
    "workflow_run", "workflow_dispatch", "push"
}
base.ALLOWED_SECRET_REFERENCES[base.SAFE_ANALYTICS_WORKFLOW] = {
    base.TURSO_DATABASE_URL_SECRET,
    base.TURSO_AUTH_TOKEN_SECRET,
}
base.EXPECTED_PERMISSIONS[base.TURSO_SCHEMA_MIGRATION_WORKFLOW] = {"contents": "read"}
base.EXPECTED_TRIGGERS[base.TURSO_SCHEMA_MIGRATION_WORKFLOW] = {"workflow_dispatch"}
base.ALLOWED_SECRET_REFERENCES[base.TURSO_SCHEMA_MIGRATION_WORKFLOW] = {
    base.TURSO_DATABASE_URL_SECRET,
    base.TURSO_AUTH_TOKEN_SECRET,
}
base.EXPECTED_PERMISSIONS[base.TURSO_SCHEMA_MIGRATION_OPERATOR_WORKFLOW] = {
    "actions": "write",
    "contents": "read",
}
base.EXPECTED_TRIGGERS[base.TURSO_SCHEMA_MIGRATION_OPERATOR_WORKFLOW] = {"push"}
base.EXPECTED_PERMISSIONS[base.PORTFOLIO_DATA_SYNC_WORKFLOW] = {
    "actions": "read",
    "contents": "write",
}
base.EXPECTED_TRIGGERS[base.PORTFOLIO_DATA_SYNC_WORKFLOW] = {"workflow_run"}
base.EXPECTED_TRIGGERS[base.HOMOLOGATION_REFRESH_WORKFLOW] = {
    "workflow_dispatch", "workflow_run", "push"
}
base.EXPECTED_PERMISSIONS[base.TURSO_WEEKLY_INTEGRITY_WORKFLOW] = {"contents": "read"}
base.EXPECTED_TRIGGERS[base.TURSO_WEEKLY_INTEGRITY_WORKFLOW] = {"schedule", "workflow_dispatch"}
base.ALLOWED_SECRET_REFERENCES[base.TURSO_WEEKLY_INTEGRITY_WORKFLOW] = {
    base.TURSO_DATABASE_URL_SECRET,
    base.TURSO_AUTH_TOKEN_SECRET,
}
base.PRICESMART_SPECS_WORKFLOW = "precios-supermercados-sps-pricesmart-specs-weekly.yml"
base.PRICESMART_SPECS_AUTHORIZATION = (
    "precios-supermercados-sps/.automation/pricesmart-specs-capture-authorization.json"
)
base.EXPECTED_PERMISSIONS[base.PRICESMART_SPECS_WORKFLOW] = {
    "actions": "read",
    "contents": "read",
}
base.EXPECTED_TRIGGERS[base.PRICESMART_SPECS_WORKFLOW] = {"schedule", "workflow_dispatch"}
base.ALLOWED_SECRET_REFERENCES[base.PRICESMART_SPECS_WORKFLOW] = {
    base.TURSO_DATABASE_URL_SECRET,
    base.TURSO_AUTH_TOKEN_SECRET,
}


def _checkout_identity_with_derived_workflows() -> None:
    """Conserva el audit histórico y reconoce derivados y publicadores aislados."""

    derived_ref = "${{ github.event_name == 'workflow_run' && github.event.workflow_run.head_sha || github.sha }}"
    for path, workflow in base.workflows():
        if path.name == base.PROBE_WORKFLOW:
            probe_jobs = base.jobs(workflow)
            privileged_checkout = [
                step
                for step in base.job_steps(probe_jobs["controlled-probe"])
                if str(step.get("uses", "")).startswith("actions/checkout@")
            ]
            assert privileged_checkout == [], "El job con OIDC de sonda no debe ejecutar código del repositorio"
            verifier_checkout = [
                step
                for step in base.job_steps(probe_jobs["verify-evidence"])
                if str(step.get("uses", "")).startswith("actions/checkout@")
            ]
            assert len(verifier_checkout) == 1
            assert verifier_checkout[0]["with"] == {
                "ref": "${{ github.sha }}",
                "persist-credentials": "false",
            }
            continue

        checkout_steps = [
            step
            for step in base.steps(workflow)
            if str(step.get("uses", "")).startswith("actions/checkout@")
        ]
        if path.name == base.LOCATION_BINDING_WORKFLOW:
            assert base.all_jobs_blocked(workflow)
            assert checkout_steps == []
            continue
        if path.name == base.PRESERVE_INITIAL_SNAPSHOT_WORKFLOW:
            assert checkout_steps == []
            continue
        if path.name == base.PORTFOLIO_DATA_SYNC_WORKFLOW:
            assert len(checkout_steps) == 1
            assert checkout_steps[0]["uses"] == "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
            assert checkout_steps[0]["with"] == {
                "ref": "portfolio-data",
                "path": "portfolio-data",
                "fetch-depth": "1",
            }
            continue
        if path.name == base.AUDIT_WORKFLOW:
            assert len(checkout_steps) == 1
            assert checkout_steps[0]["with"] == {
                "ref": "${{ github.sha }}",
                "persist-credentials": "false",
                "fetch-depth": "0",
            }
            continue
        if path.name in {base.HOMOLOGATION_REFRESH_WORKFLOW, base.SAFE_ANALYTICS_WORKFLOW}:
            assert len(checkout_steps) == 1
            assert checkout_steps[0]["with"] == {
                "ref": derived_ref,
                "persist-credentials": "false",
            }
            continue

        expected_ref = (
            "${{ github.workflow_sha }}"
            if path.name in {base.COMMAND_WORKFLOW, base.RECOVERY_WORKFLOW}
            else "${{ github.sha }}"
        )
        assert checkout_steps, path.name
        for step in checkout_steps:
            inputs = step.get("with")
            assert isinstance(inputs, dict)
            assert inputs == {"ref": expected_ref, "persist-credentials": "false"}


base.test_checkout_identity_is_immutable_and_credentials_are_not_persisted = (
    _checkout_identity_with_derived_workflows
)

for name, value in vars(base).items():
    if name.startswith("test_"):
        globals()[name] = value


def test_production_operator_is_main_only_closed_and_least_privilege() -> None:
    path = base.WORKFLOW_DIR / base.PRODUCTION_OPERATOR_WORKFLOW
    workflow = base.load_workflow(path)
    assert workflow["permissions"] == {"actions": "write", "contents": "read"}
    assert workflow["concurrency"] == {
        "group": "precios-sps-production-operator",
        "cancel-in-progress": "false",
    }
    assert workflow["on"] == {
        "push": {
            "branches": ["main"],
            "paths": [base.PRODUCTION_UPDATE_REQUEST],
        },
        "schedule": [
            {"cron": "17 14 * * *"},
            {"cron": "17 18 * * *"},
        ],
    }
    operator_jobs = base.jobs(workflow)
    assert set(operator_jobs) == {"dispatch", "recover"}
    dispatch = operator_jobs["dispatch"]
    recover = operator_jobs["recover"]
    assert dispatch["if"] == (
        "${{ github.repository == 'jchernandez-portfolio/precios-supermercados-sps' && "
        "github.ref == 'refs/heads/main' && github.event_name == 'push' }}"
    )
    assert recover["if"] == (
        "${{ github.repository == 'jchernandez-portfolio/precios-supermercados-sps' && "
        "github.ref == 'refs/heads/main' && github.event_name == 'schedule' }}"
    )
    assert dispatch["timeout-minutes"] == "5"
    assert recover["timeout-minutes"] == "5"
    assert "environment" not in dispatch
    assert "environment" not in recover
    assert "permissions" not in dispatch
    assert "permissions" not in recover

    raw = path.read_text(encoding="utf-8")
    assert base.PRODUCTION_UPDATE_REQUEST in raw
    assert "precios-sps-production-update-request/v1" in raw
    assert "daily_all_productive_retailers" in raw
    assert "production_request_schema_closed_set_mismatch" in raw
    assert "production_request_authorization_window_too_long" in raw
    assert "production_request_authorization_not_current" in raw
    assert "production_request_live_authority_missing" in raw
    assert "production_request_persistence_authority_missing" in raw
    assert "createWorkflowDispatch" in raw
    assert "precios-supermercados-sps-la-colonia-mvp-update.yml" in raw
    assert "ref: 'main'" in raw
    assert "live_read_only_authorized: 'true'" in raw
    assert "reRunWorkflowFailedJobs" in raw
    assert "listWorkflowRuns" in raw
    assert "const maxRunAttempt = 3" in raw
    assert "retryableConclusions = new Set(['failure', 'timed_out'])" in raw
    # Corte diario por workflow_dispatch (Worker 01:43) o schedule (histórico);
    # sin corte hoy, el operador lo arranca (respaldo del disparo externo).
    assert "dailyEvents = new Set(['workflow_dispatch', 'schedule'])" in raw
    assert "run.conclusion !== 'cancelled'" in raw
    assert "daily_recovery_dispatched_missing_run" in raw
    assert raw.count("createWorkflowDispatch") == 2
    assert "branch: 'main'" in raw
    assert "daily_recovery_no_scheduled_run_today" in raw
    assert "daily_recovery_run_still_active" in raw
    assert "daily_recovery_attempt_limit_reached" in raw
    assert "secrets." not in raw
    assert "vars." not in raw
    assert "pull_request:" not in raw
    assert "pull_request_target:" not in raw
    assert "issue_comment:" not in raw
    assert "schedule:" in raw
    assert "id-token" not in raw

    parsed = yaml.load(raw, Loader=yaml.BaseLoader)
    dispatch_checkouts = [
        step
        for step in parsed["jobs"]["dispatch"]["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout@")
    ]
    recover_checkouts = [
        step
        for step in parsed["jobs"]["recover"]["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout@")
    ]
    assert len(dispatch_checkouts) == 1
    assert dispatch_checkouts[0]["with"] == {
        "ref": "${{ github.sha }}",
        "persist-credentials": "false",
    }
    assert recover_checkouts == []
    assert len(parsed["jobs"]["recover"]["steps"]) == 1
    assert str(parsed["jobs"]["recover"]["steps"][0]["uses"]).startswith("actions/github-script@")


def test_safe_analytics_publication_is_trusted_read_only_and_fail_closed() -> None:
    path = base.WORKFLOW_DIR / base.SAFE_ANALYTICS_WORKFLOW
    workflow = base.load_workflow(path)
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"] == {
        "group": "precios-sps-safe-analytics-publication",
        "cancel-in-progress": "false",
    }
    assert workflow["on"] == {
        "workflow_run": {
            "workflows": ["Precios SPS - Refrescar homologación derivada"],
            "types": ["completed"],
        },
        "workflow_dispatch": "",
        "push": {
            "branches": ["main"],
            "paths": [base.RPI_PUBLICATION_REQUEST],
        },
    }
    workflow_jobs = base.jobs(workflow)
    assert set(workflow_jobs) == {"publish"}
    publish = workflow_jobs["publish"]
    assert publish["timeout-minutes"] == "20"
    assert "permissions" not in publish
    assert "environment" not in publish
    expected_if = (
        "${{ github.repository == 'jchernandez-portfolio/precios-supermercados-sps' && "
        "(github.event_name == 'workflow_dispatch' || "
        "(github.event_name == 'push' && github.ref == 'refs/heads/main') || "
        "(github.event_name == 'workflow_run' && "
        "github.event.workflow_run.conclusion == 'success' && "
        "github.event.workflow_run.head_branch == 'main')) }}"
    )
    assert " ".join(str(publish["if"]).split()) == " ".join(expected_if.split())

    raw = path.read_text(encoding="utf-8")
    assert base.RPI_PUBLICATION_REQUEST in raw
    assert "precios-sps-rpi-publication-request/v1" in raw
    assert "publish_safe_rpi" in raw
    assert "rpi_publication_request_schema_closed_set_mismatch" in raw
    assert "rpi_publication_request_scope_invalid" in raw
    assert "rpi_publication_request_read_only_required" in raw
    assert "bootstrap_rpi_consumer_publication" in raw
    assert "operator_requested_republication" in raw
    assert "scripts/exportar_rpi_marts.py" in raw
    assert "scripts/exportar_consumer_catalog.py" in raw
    assert "scripts/exportar_modelo_analitico.py" not in raw
    assert "scripts/generar_descriptores_publicacion_segura.py" not in raw
    assert "scripts/generar_muestra_portafolio_segura.py" in raw
    assert "--consumer-mart" in raw
    assert "portfolio-sample.json" in raw
    assert "--scope la_colonia=la_colonia_sps" in raw
    assert "--scope walmart=walmart_sps" in raw
    assert "--scope colonial=colonial_sps" in raw
    assert "--scope pricesmart=pricesmart_sps" in raw
    assert "--scope comisariato_los_andes=comisariato_los_andes_sps" in raw
    assert "rpi-business-mart/v1" in raw
    assert "rpi-consumer-mart/v2" in raw
    assert "rpi-consumer-catalog-manifest/v3" in raw
    assert "rpi-consumer-catalog/v3" in raw
    assert "rpi-consumer-analysis/v1" in raw
    assert "only_persisted_ready_cross_retailer_identity" in raw
    assert "rpi-marts-manifest/v1" in raw
    assert "precios-sps-safe-portfolio-sample/v1" in raw
    assert "business/fact_price_history.csv" in raw
    assert "business/fact_promotion_analysis.csv" in raw
    assert "files_sha256" in raw
    assert "fail_closed_strong_identity_and_commercial_consistency" in raw
    assert "TURSO_DATABASE_URL: ${{ secrets.TURSO_DATABASE_URL }}" in raw
    assert "TURSO_AUTH_TOKEN: ${{ secrets.TURSO_AUTH_TOKEN }}" in raw
    assert "pull_request:" not in raw
    assert "pull_request_target:" not in raw
    assert "issue_comment:" not in raw
    assert "schedule:" not in raw
    assert "id-token" not in raw
    assert "contents: write" not in raw

    parsed = yaml.load(raw, Loader=yaml.BaseLoader)
    checkout = next(
        step
        for step in parsed["jobs"]["publish"]["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert checkout["with"] == {
        "ref": "${{ github.event_name == 'workflow_run' && github.event.workflow_run.head_sha || github.sha }}",
        "persist-credentials": "false",
    }


def test_controlled_rpi_publication_request_is_closed_and_read_only() -> None:
    request_path = base.REPO_ROOT / base.RPI_PUBLICATION_REQUEST
    request = yaml.safe_load(request_path.read_text(encoding="utf-8"))
    assert request == {
        "schema": "precios-sps-rpi-publication-request/v1",
        "action": "publish_safe_rpi",
        "scope": [
            {"supermarket_id": "la_colonia", "location_id": "la_colonia_sps"},
            {"supermarket_id": "colonial", "location_id": "colonial_sps"},
            {"supermarket_id": "walmart", "location_id": "walmart_sps"},
            {"supermarket_id": "pricesmart", "location_id": "pricesmart_sps"},
            {
                "supermarket_id": "comisariato_los_andes",
                "location_id": "comisariato_los_andes_sps",
            },
        ],
        "read_only": True,
        "reason": "operator_requested_republication",
        "sequence": 8,
    }


def test_turso_schema_migration_is_manual_scoped_and_fail_closed() -> None:
    path = base.WORKFLOW_DIR / base.TURSO_SCHEMA_MIGRATION_WORKFLOW
    workflow = base.load_workflow(path)
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["on"]) == {"workflow_dispatch"}
    dispatch = workflow["on"]["workflow_dispatch"]
    assert isinstance(dispatch, dict)
    authorization = dispatch["inputs"]["schema_migration_authorized"]
    assert authorization["required"] == "true"
    assert authorization["default"] == "false"
    assert authorization["type"] == "boolean"
    assert workflow["concurrency"] == {
        "group": "precios-sps-turso-schema-migration",
        "cancel-in-progress": "false",
    }

    workflow_jobs = base.jobs(workflow)
    assert set(workflow_jobs) == {"migrate"}
    migrate = workflow_jobs["migrate"]
    assert migrate["if"] == (
        "${{ github.repository == 'jchernandez-portfolio/precios-supermercados-sps' && "
        "inputs.schema_migration_authorized == true }}"
    )
    assert migrate["timeout-minutes"] == "15"
    assert "permissions" not in migrate
    assert "environment" not in migrate

    raw = path.read_text(encoding="utf-8")
    assert "scripts/migrar_mvp_turso_walmart_pricesmart.py" in raw
    assert "TURSO_DATABASE_URL: ${{ secrets.TURSO_DATABASE_URL }}" in raw
    assert "TURSO_AUTH_TOKEN: ${{ secrets.TURSO_AUTH_TOKEN }}" in raw
    assert "pull_request:" not in raw
    assert "pull_request_target:" not in raw
    assert "issue_comment:" not in raw
    assert "schedule:" not in raw
    assert "id-token" not in raw
    assert "contents: write" not in raw


def test_turso_schema_migration_operator_is_main_only_closed_and_least_privilege() -> None:
    path = base.WORKFLOW_DIR / base.TURSO_SCHEMA_MIGRATION_OPERATOR_WORKFLOW
    workflow = base.load_workflow(path)
    assert workflow["permissions"] == {"actions": "write", "contents": "read"}
    assert workflow["concurrency"] == {
        "group": "precios-sps-turso-schema-migration-operator",
        "cancel-in-progress": "false",
    }
    assert workflow["on"] == {
        "push": {
            "branches": ["main"],
            "paths": [base.TURSO_SCHEMA_MIGRATION_REQUEST],
        }
    }
    workflow_jobs = base.jobs(workflow)
    assert set(workflow_jobs) == {"dispatch"}
    dispatch = workflow_jobs["dispatch"]
    assert dispatch["if"] == (
        "${{ github.repository == 'jchernandez-portfolio/precios-supermercados-sps' && "
        "github.ref == 'refs/heads/main' && github.event_name == 'push' }}"
    )
    assert dispatch["timeout-minutes"] == "5"
    assert "permissions" not in dispatch
    assert "environment" not in dispatch

    raw = path.read_text(encoding="utf-8")
    assert base.TURSO_SCHEMA_MIGRATION_REQUEST in raw
    assert "precios-sps-turso-schema-migration-request/v1" in raw
    assert "migrate_walmart_pricesmart_schema" in raw
    assert "schema_migration_request_schema_closed_set_mismatch" in raw
    assert "schema_migration_request_authorization_window_too_long" in raw
    assert "schema_migration_request_authorization_not_current" in raw
    assert "schema_migration_request_authority_missing" in raw
    assert "createWorkflowDispatch" in raw
    assert "precios-supermercados-sps-turso-schema-migration.yml" in raw
    assert "schema_migration_authorized: 'true'" in raw
    assert "ref: 'main'" in raw
    assert "secrets." not in raw
    assert "vars." not in raw
    assert "pull_request:" not in raw
    assert "pull_request_target:" not in raw
    assert "issue_comment:" not in raw
    assert "schedule:" not in raw
    assert "id-token" not in raw

    parsed = yaml.load(raw, Loader=yaml.BaseLoader)
    checkout = next(
        step
        for step in parsed["jobs"]["dispatch"]["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert checkout["with"] == {
        "ref": "${{ github.sha }}",
        "persist-credentials": "false",
    }


def test_portfolio_data_sync_reuses_safe_artifact_without_turso_reads() -> None:
    path = base.WORKFLOW_DIR / base.PORTFOLIO_DATA_SYNC_WORKFLOW
    workflow = base.load_workflow(path)
    assert workflow["permissions"] == {"actions": "read", "contents": "write"}
    assert workflow["concurrency"] == {
        "group": "precios-sps-portfolio-data-sync",
        "cancel-in-progress": "false",
    }
    assert workflow["on"] == {
        "workflow_run": {
            "workflows": ["Precios SPS - Publicar analítica segura"],
            "types": ["completed"],
        }
    }
    workflow_jobs = base.jobs(workflow)
    assert set(workflow_jobs) == {"sync"}
    sync = workflow_jobs["sync"]
    assert sync["timeout-minutes"] == "10"
    assert "permissions" not in sync
    assert "environment" not in sync
    expected_if = (
        "${{ github.repository == 'jchernandez-portfolio/precios-supermercados-sps' && "
        "github.event.workflow_run.conclusion == 'success' && "
        "github.event.workflow_run.head_branch == 'main' }}"
    )
    assert " ".join(str(sync["if"]).split()) == " ".join(expected_if.split())

    raw = path.read_text(encoding="utf-8")
    assert "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c" in raw
    assert "run-id: ${{ github.event.workflow_run.id }}" in raw
    assert "safe-analytics-la-colonia-walmart-sps-${{ github.event.workflow_run.id }}" in raw
    assert "only('portfolio-sample.json', 'portfolio_sync_sample')" in raw
    assert "rpi-consumer-mart/v2" in raw
    assert "rpi-consumer-catalog-manifest/v3" in raw
    assert "rpi-consumer-catalog/v3" in raw
    assert "rpi-business-mart/v1" in raw
    assert "rpi-marts-manifest/v1" in raw
    assert "portfolio_sync_hash_mismatch" in raw
    assert 'published="$repository/precios-supermercados-sps/published/rpi"' in raw
    assert '"$published/consumer-mart.json"' in raw
    assert '"$published/manifest.json"' in raw
    assert '"$published/v3/"' in raw
    assert "precios-supermercados-sps/published/rpi/business-mart.json" not in raw
    assert "precios-supermercados-sps/portfolio/sample-data.json" in raw
    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1" in raw
    assert "ref: portfolio-data" in raw
    assert "portfolio_sync_symlink_boundary_invalid" in raw
    assert 'rm -rf "$published/v3"' in raw
    assert 'git -C "$repository" add -A --' in raw
    assert 'git -C "$repository" push origin HEAD:portfolio-data' in raw
    assert "createBlob" not in raw
    assert "createOrUpdateFileContents" not in raw
    assert "portfolio_sync_secret_material_detected" in raw
    assert "TURSO_DATABASE_URL: ${{ secrets." not in raw
    assert "TURSO_AUTH_TOKEN: ${{ secrets." not in raw
    assert "scripts/exportar_modelo_analitico.py" not in raw
    assert "scripts/generar_descriptores_publicacion_segura.py" not in raw
    assert "scripts/exportar_rpi_marts.py" not in raw
    assert "pull_request:" not in raw
    assert "pull_request_target:" not in raw
    assert "issue_comment:" not in raw
    assert "schedule:" not in raw
    assert "id-token" not in raw

    frontend = (
        base.REPO_ROOT
        / "precios-supermercados-sps"
        / "portfolio"
        / "precios-portfolio-current-state.js"
    ).read_text(encoding="utf-8")
    assert "raw.githubusercontent.com/jchernandez-portfolio/precios-supermercados-sps/portfolio-data/precios-supermercados-sps/portfolio/sample-data.json" in frontend
    assert "raw.githubusercontent.com/jchernandez-portfolio/precios-supermercados-sps/main/precios-supermercados-sps/portfolio/sample-data.json" not in frontend
    assert "['localhost', '127.0.0.1'].includes(window.location.hostname)" in frontend
    assert "cache: 'no-store'" in frontend
    assert "TURSO_DATABASE_URL" not in frontend
    assert "TURSO_AUTH_TOKEN" not in frontend
    assert "libsql://" not in frontend


def test_turso_weekly_integrity_is_scheduled_read_only_and_owns_full_scans() -> None:
    path = base.WORKFLOW_DIR / base.TURSO_WEEKLY_INTEGRITY_WORKFLOW
    workflow = base.load_workflow(path)
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["on"] == {
        "schedule": [{"cron": "23 10 * * 0"}],
        "workflow_dispatch": "",
    }
    assert workflow["concurrency"] == {
        "group": "precios-sps-turso-weekly-integrity",
        "cancel-in-progress": "false",
    }
    workflow_jobs = base.jobs(workflow)
    assert set(workflow_jobs) == {"integrity"}
    integrity = workflow_jobs["integrity"]
    assert integrity["if"] == (
        "${{ github.repository == 'jchernandez-portfolio/precios-supermercados-sps' }}"
    )
    assert "permissions" not in integrity
    assert "environment" not in integrity

    raw = path.read_text(encoding="utf-8")
    assert "python scripts/verificar_integridad_turso.py" in raw
    assert "TURSO_DATABASE_URL: ${{ secrets.TURSO_DATABASE_URL }}" in raw
    assert "TURSO_AUTH_TOKEN: ${{ secrets.TURSO_AUTH_TOKEN }}" in raw
    assert "pull_request:" not in raw
    assert "pull_request_target:" not in raw
    assert "id-token" not in raw
    assert "contents: write" not in raw

    # Los recorridos completos de la base ya no corren en el ciclo diario.
    daily_paths = (
        base.WORKFLOW_DIR / base.MVP_UPDATE_WORKFLOW,
        base.WORKFLOW_DIR / base.HOMOLOGATION_REFRESH_WORKFLOW,
    )
    for daily in daily_paths:
        daily_raw = daily.read_text(encoding="utf-8")
        assert "PRAGMA integrity_check" not in daily_raw
        assert "pragma_foreign_key_check" not in daily_raw
        assert "SELECT COUNT(*) FROM price_history" not in daily_raw


def test_pricesmart_specs_weekly_is_authorized_bounded_and_least_privilege() -> None:
    import json

    path = base.WORKFLOW_DIR / base.PRICESMART_SPECS_WORKFLOW
    workflow = base.load_workflow(path)
    assert workflow["permissions"] == {"actions": "read", "contents": "read"}
    triggers = workflow["on"]
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert triggers["schedule"] == [{"cron": "37 11 * * 6"}]
    assert triggers["workflow_dispatch"]["inputs"]["force"]["type"] == "boolean"
    assert triggers["workflow_dispatch"]["inputs"]["force"]["default"] == "false"
    assert workflow["concurrency"] == {
        "group": "precios-sps-pricesmart-specs-weekly",
        "cancel-in-progress": "false",
    }
    workflow_jobs = base.jobs(workflow)
    assert set(workflow_jobs) == {"specs"}
    job = workflow_jobs["specs"]
    assert job["if"] == (
        "${{ github.repository == 'jchernandez-portfolio/precios-supermercados-sps' "
        "&& github.ref == 'refs/heads/main' }}"
    )
    assert "permissions" not in job
    assert "environment" not in job
    assert job["timeout-minutes"] == "150"

    raw = path.read_text(encoding="utf-8")
    # Autorización registrada, validada antes de cualquier tráfico.
    assert ".automation/pricesmart-specs-capture-authorization.json" in raw
    assert "load_authorization" in raw
    assert "--live-read-only" in raw
    assert "--delay-seconds 2.0" in raw
    assert "--max-requests 1200" in raw
    assert "--max-items 600" in raw
    # Navegador headless (2026-10-03): versión fijada, transporte explícito.
    assert "python -m pip install --disable-pip-version-check playwright==1.62.0" in raw
    assert "python -m playwright install --with-deps chromium" in raw
    assert "--transport browser" in raw
    # Entrada: sólo el handoff PriceSmart de la última corrida diaria exitosa.
    assert "--workflow precios-supermercados-sps-la-colonia-mvp-update.yml" in raw
    assert "--status success" in raw
    assert '--pattern "daily-acquisition-pricesmart-${run_id}-attempt-*"' in raw
    assert "GH_TOKEN: ${{ github.token }}" in raw
    assert "daily_successful_run_too_old" in raw
    # Persistencia barata y artifact.
    assert "persistir_especificaciones_pricesmart_turso.py state" in raw
    assert "persistir_especificaciones_pricesmart_turso.py apply" in raw
    assert "actions/upload-artifact@" in raw
    # El cron no se cruza con el corte diario ni con la integridad semanal.
    assert "43 7 * * *" not in raw and "23 10 * * 0" not in raw
    for forbidden in (
        "pull_request:", "pull_request_target:", "issue_comment:", "id-token",
        "contents: write", "actions: write", "vars.", "${{ inputs.force }}",
    ):
        assert forbidden not in raw, forbidden

    authorization = json.loads((base.REPO_ROOT / base.PRICESMART_SPECS_AUTHORIZATION).read_text(encoding="utf-8"))
    assert authorization["schema"] == "precios-sps-pricesmart-specs-authorization/v1"
    assert authorization["live_read_only_authorized"] is True
    assert authorization["approved_by"] == "project_owner"
    assert authorization["max_requests_per_run"] >= 1200
    assert authorization["min_delay_seconds"] <= 2.0
    assert authorization["concurrency"] == 1
