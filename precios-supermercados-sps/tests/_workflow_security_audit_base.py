"""Auditoría fail-closed del YAML ejecutable de los workflows SPS."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
ALL_WORKFLOW_FILES = tuple(
    sorted((*WORKFLOW_DIR.glob("*.yml"), *WORKFLOW_DIR.glob("*.yaml")))
)


def is_sps_workflow(path: Path) -> bool:
    """Clasifica por nombre o contenido para que renombrar no evada la auditoría."""

    raw = path.read_text(encoding="utf-8").casefold()
    # El nombre del repo es identidad, no un marcador de contenido SPS.
    raw = raw.replace("jchernandez-portfolio/precios-supermercados-sps", "")
    return any(
        marker in path.name.casefold() or marker in raw
        for marker in (
            "precios-supermercados-sps",
            "la-colonia",
            "la_colonia",
            "la colonia",
            "lacolonia",
        )
    )


WORKFLOWS = tuple(path for path in ALL_WORKFLOW_FILES if is_sps_workflow(path))

PINNED_ACTIONS = {
    "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
    "actions/setup-python": "5fda3b95a4ea91299a34e894583c3862153e4b97",
    "actions/github-script": "3a2844b7e9c422d3c10d287c895573f7108da1b3",
    "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
    "actions/download-artifact": "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
}

PROBE_WORKFLOW = "precios-supermercados-sps-cloudflare-probe.yml"
TEST_WORKFLOW = "precios-supermercados-sps-tests.yml"
AUDIT_WORKFLOW = "precios-supermercados-sps-historical-branch-audit.yml"
COMMAND_WORKFLOW = "precios-supermercados-sps-la-colonia-command.yml"
RECOVERY_WORKFLOW = "precios-supermercados-sps-la-colonia-dispatch-recovery.yml"
LA_DIAGNOSTIC_WORKFLOW = "precios-supermercados-sps-la-colonia-diagnostic.yml"
FACET_WORKFLOW = "precios-supermercados-sps-la-colonia-facet-discovery.yml"
LIVE_WORKFLOW = "precios-supermercados-sps-la-colonia-live.yml"
MVP_UPDATE_WORKFLOW = "precios-supermercados-sps-la-colonia-mvp-update.yml"
LIVE_MVP_JOB = "mvp-sample"
LIVE_FACET_JOB = "context-bound-facet-entrypoint"
LOCATION_BINDING_WORKFLOW = "precios-supermercados-sps-la-colonia-location-binding.yml"
PRESERVE_INITIAL_SNAPSHOT_WORKFLOW = (
    "precios-supermercados-sps-preserve-initial-snapshot.yml"
)
HOMOLOGATION_REFRESH_WORKFLOW = "precios-supermercados-sps-homologation-refresh.yml"
LIVE_MVP_REQUEST = (
    "precios-supermercados-sps/.automation/la-colonia-mvp-live-request.json"
)
PROBE_GATEWAY_SECRET = "CLOUDFLARE_PROBE_GATEWAY_URL"
PROBE_OBSERVABILITY_SECRET = "CLOUDFLARE_PROBE_OBSERVABILITY_TOKEN"
PROBE_PUBLIC_KEY_VAR = "CLOUDFLARE_PROBE_PUBLIC_KEY_SPKI_B64URL"
CLOUDFLARE_ACCOUNT_VAR = "CLOUDFLARE_ACCOUNT_ID"
EDGE_GATEWAY_VAR = "CLOUDFLARE_EDGE_GATEWAY_URL"
EDGE_PUBLIC_KEY_VAR = "CLOUDFLARE_EDGE_RECEIPT_PUBLIC_KEY_SPKI_B64URL"
TURSO_DATABASE_URL_SECRET = "TURSO_DATABASE_URL"
TURSO_AUTH_TOKEN_SECRET = "TURSO_AUTH_TOKEN"

EXPECTED_PERMISSIONS = {
    PROBE_WORKFLOW: {"contents": "read"},
    AUDIT_WORKFLOW: {"contents": "read", "pull-requests": "read"},
    COMMAND_WORKFLOW: {"contents": "read", "pull-requests": "read"},
    RECOVERY_WORKFLOW: {"actions": "read", "contents": "read"},
    LA_DIAGNOSTIC_WORKFLOW: {"contents": "read"},
    FACET_WORKFLOW: {"contents": "read"},
    LIVE_WORKFLOW: {"contents": "read"},
    MVP_UPDATE_WORKFLOW: {"contents": "read"},
    LOCATION_BINDING_WORKFLOW: {"contents": "read"},
    PRESERVE_INITIAL_SNAPSHOT_WORKFLOW: {"actions": "read", "contents": "read"},
    HOMOLOGATION_REFRESH_WORKFLOW: {"contents": "read"},
    TEST_WORKFLOW: {"contents": "read"},
}

ALLOWED_JOB_PERMISSIONS = {
    PROBE_WORKFLOW: {
        "controlled-probe": {"contents": "read", "id-token": "write"},
    },
    LIVE_WORKFLOW: {
        LIVE_FACET_JOB: {"contents": "read", "id-token": "write"},
    },
    # Lectura de jobs del corte para publicar persistencias parciales (2026-10-09).
    HOMOLOGATION_REFRESH_WORKFLOW: {
        "daily-gate": {"actions": "read"},
    },
}

EXPECTED_TRIGGERS = {
    PROBE_WORKFLOW: {"workflow_dispatch"},
    AUDIT_WORKFLOW: {"workflow_dispatch", "push"},
    COMMAND_WORKFLOW: {"pull_request_target"},
    LA_DIAGNOSTIC_WORKFLOW: {"workflow_dispatch"},
    RECOVERY_WORKFLOW: {"workflow_run"},
    FACET_WORKFLOW: {"workflow_dispatch"},
    LIVE_WORKFLOW: {"workflow_dispatch", "push"},
    # Desde 2026-10-09 lo dispara el Worker edge/daily-trigger (05:17) y, de
    # respaldo, el operador productivo; sin cron propio (llegaba 5–9 h tarde).
    MVP_UPDATE_WORKFLOW: {"workflow_dispatch"},
    LOCATION_BINDING_WORKFLOW: {"workflow_dispatch"},
    PRESERVE_INITIAL_SNAPSHOT_WORKFLOW: {"workflow_dispatch", "push"},
    HOMOLOGATION_REFRESH_WORKFLOW: {"workflow_dispatch", "workflow_run"},
    TEST_WORKFLOW: {"workflow_dispatch", "pull_request", "push"},
}

BLOCKED_ENTRYPOINTS = {
    COMMAND_WORKFLOW,
    LA_DIAGNOSTIC_WORKFLOW,
    RECOVERY_WORKFLOW,
    FACET_WORKFLOW,
    LOCATION_BINDING_WORKFLOW,
}

ALLOWED_SECRET_REFERENCES = {
    PROBE_WORKFLOW: {PROBE_GATEWAY_SECRET, PROBE_OBSERVABILITY_SECRET},
    MVP_UPDATE_WORKFLOW: {TURSO_DATABASE_URL_SECRET, TURSO_AUTH_TOKEN_SECRET},
    HOMOLOGATION_REFRESH_WORKFLOW: {TURSO_DATABASE_URL_SECRET, TURSO_AUTH_TOKEN_SECRET},
}
# Referencias permitidas sólo para capacidades opcionales que conservan el
# contrato de solo lectura. Las referencias requeridas siguen siendo exactas.
OPTIONAL_SECRET_REFERENCE_GROUPS = {
    AUDIT_WORKFLOW: (
        {TURSO_DATABASE_URL_SECRET, TURSO_AUTH_TOKEN_SECRET},
    ),
}
ALLOWED_VAR_REFERENCES = {
    PROBE_WORKFLOW: {PROBE_PUBLIC_KEY_VAR, CLOUDFLARE_ACCOUNT_VAR},
    LIVE_WORKFLOW: {EDGE_GATEWAY_VAR, EDGE_PUBLIC_KEY_VAR},
}


def load_workflow(path: Path) -> dict[str, Any]:
    """Parsea sin constructores de objetos y conserva `on` como string YAML."""

    value = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(value, dict), f"{path.name}: raíz YAML inválida"
    assert set(value).issuperset({"name", "on", "permissions", "jobs"}), path.name
    return value


def workflows() -> tuple[tuple[Path, dict[str, Any]], ...]:
    assert {path.name for path in WORKFLOWS} == set(EXPECTED_PERMISSIONS)
    return tuple((path, load_workflow(path)) for path in WORKFLOWS)


def jobs(value: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = value["jobs"]
    assert isinstance(result, dict) and result
    assert all(isinstance(job, dict) for job in result.values())
    return result


def job_steps(job: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    current = job.get("steps", [])
    assert isinstance(current, list)
    assert all(isinstance(step, dict) for step in current)
    return tuple(current)


def steps(value: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    all_steps: list[dict[str, Any]] = []
    for job in jobs(value).values():
        all_steps.extend(job_steps(job))
    return tuple(all_steps)


def action_references(value: dict[str, Any]) -> tuple[str, ...]:
    references = [
        str(step["uses"])
        for step in steps(value)
        if step.get("uses") is not None
    ]
    references.extend(
        str(job["uses"])
        for job in jobs(value).values()
        if job.get("uses") is not None
    )
    return tuple(references)


def secret_references(path: Path) -> set[str]:
    raw = path.read_text(encoding="utf-8")
    return set(re.findall(r"\$\{\{\s*secrets\.([A-Za-z0-9_]+)\s*\}\}", raw))


def variable_references(path: Path) -> set[str]:
    raw = path.read_text(encoding="utf-8")
    return set(re.findall(r"\$\{\{\s*vars\.([A-Za-z0-9_]+)\s*\}\}", raw))


def all_jobs_blocked(value: dict[str, Any]) -> bool:
    return all(job.get("if") == "${{ false }}" for job in jobs(value).values())


def test_all_external_actions_are_pinned_to_verified_full_shas():
    for path, workflow in workflows():
        for reference in action_references(workflow):
            action, separator, revision = reference.partition("@")
            assert separator and re.fullmatch(r"[0-9a-f]{40}", revision), (path.name, reference)
            assert PINNED_ACTIONS.get(action) == revision, (path.name, reference)


def test_permissions_are_exact_with_only_explicit_job_overrides_and_references():
    for path, workflow in workflows():
        assert workflow["permissions"] == EXPECTED_PERMISSIONS[path.name]
        actual_overrides = {
            name: job["permissions"]
            for name, job in jobs(workflow).items()
            if "permissions" in job
        }
        assert actual_overrides == ALLOWED_JOB_PERMISSIONS.get(path.name, {})
        required_secrets = ALLOWED_SECRET_REFERENCES.get(path.name, set())
        actual_secrets = secret_references(path)
        assert required_secrets <= actual_secrets
        optional_groups = OPTIONAL_SECRET_REFERENCE_GROUPS.get(path.name, ())
        assert actual_secrets - required_secrets in (set(), *optional_groups)
        assert variable_references(path) == ALLOWED_VAR_REFERENCES.get(path.name, set())


def test_checkout_identity_is_immutable_and_credentials_are_not_persisted():
    for path, workflow in workflows():
        if path.name == PROBE_WORKFLOW:
            probe_jobs = jobs(workflow)
            privileged_checkout = [
                step
                for step in job_steps(probe_jobs["controlled-probe"])
                if str(step.get("uses", "")).startswith("actions/checkout@")
            ]
            assert privileged_checkout == [], "El job con OIDC de sonda no debe ejecutar código del repositorio"
            verifier_checkout = [
                step
                for step in job_steps(probe_jobs["verify-evidence"])
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
            for step in steps(workflow)
            if str(step.get("uses", "")).startswith("actions/checkout@")
        ]
        if path.name == LOCATION_BINDING_WORKFLOW:
            assert all_jobs_blocked(workflow)
            assert checkout_steps == []
            continue
        if path.name == PRESERVE_INITIAL_SNAPSHOT_WORKFLOW:
            assert checkout_steps == []
            continue
        if path.name == AUDIT_WORKFLOW:
            assert len(checkout_steps) == 1
            assert checkout_steps[0]["with"] == {
                "ref": "${{ github.sha }}",
                "persist-credentials": "false",
                "fetch-depth": "0",
            }
            continue

        if path.name == HOMOLOGATION_REFRESH_WORKFLOW:
            assert len(checkout_steps) == 1
            assert checkout_steps[0]["with"] == {
                "ref": "${{ github.event_name == 'workflow_run' && github.event.workflow_run.head_sha || github.sha }}",
                "persist-credentials": "false",
            }
            continue

        expected_ref = (
            "${{ github.workflow_sha }}"
            if path.name in {COMMAND_WORKFLOW, RECOVERY_WORKFLOW}
            else "${{ github.sha }}"
        )
        assert checkout_steps, path.name
        for step in checkout_steps:
            inputs = step.get("with")
            assert isinstance(inputs, dict)
            assert inputs == {"ref": expected_ref, "persist-credentials": "false"}


def test_privileged_and_mutating_entrypoints_are_globally_blocked():
    by_name = {path.name: workflow for path, workflow in workflows()}
    for name in BLOCKED_ENTRYPOINTS:
        assert all_jobs_blocked(by_name[name]), name

    command = by_name[COMMAND_WORKFLOW]
    assert "actions" not in command["permissions"]
    controller = (
        REPO_ROOT
        / "precios-supermercados-sps"
        / "scripts"
        / "controlar_solicitud_archivo_la_colonia.js"
    ).read_text(encoding="utf-8")
    assert "GLOBAL LIVE BLOCKED" in controller
    assert "/actions/workflows/{workflow_id}/dispatches" not in controller
    assert "github.request(" not in controller


def test_mvp_sample_is_the_only_nonprivileged_live_catalog_path() -> None:
    path = WORKFLOW_DIR / LIVE_WORKFLOW
    workflow = load_workflow(path)
    live_jobs = jobs(workflow)
    assert set(live_jobs) == {LIVE_MVP_JOB, "live-crawl", LIVE_FACET_JOB}

    sample = live_jobs[LIVE_MVP_JOB]
    assert sample["if"] == (
        "${{ (github.event_name == 'workflow_dispatch' && inputs.mode == 'mvp_sample' && "
        "inputs.mvp_read_only_authorized == true) || (github.event_name == 'push' && "
        "github.ref == 'refs/heads/main') }}"
    )
    assert sample["timeout-minutes"] == "15"
    assert "environment" not in sample
    assert "permissions" not in sample
    assert sample["env"] == {
        "PYTHONPATH": "${{ github.workspace }}/precios-supermercados-sps/src"
    }
    assert live_jobs["live-crawl"]["if"] == "${{ false }}"
    assert live_jobs[LIVE_FACET_JOB]["if"] == "${{ false }}"

    triggers = workflow["on"]
    dispatch = triggers["workflow_dispatch"]
    assert isinstance(dispatch, dict)
    inputs = dispatch["inputs"]
    assert inputs["mode"]["default"] == "mvp_sample"
    assert inputs["mode"]["options"] == ["mvp_sample", "smoke", "staged", "full"]
    assert inputs["mvp_sample_size"] == {
        "description": "SKUs máximos conservados en la muestra MVP",
        "required": "true",
        "default": "10",
        "type": "choice",
        "options": ["5", "10"],
    }
    assert inputs["mvp_read_only_authorized"]["required"] == "true"
    assert inputs["mvp_read_only_authorized"]["default"] == "false"
    assert inputs["mvp_read_only_authorized"]["type"] == "boolean"

    assert triggers["push"] == {
        "branches": ["main"],
        "paths": [LIVE_MVP_REQUEST],
    }

    sample_raw = "\n".join(str(step) for step in job_steps(sample))
    assert "scripts/probar_muestra_sps_la_colonia.py" in sample_raw
    assert "--live-read-only" in sample_raw
    assert "--sample-size" in sample_raw
    assert "actions/upload-artifact@" in sample_raw
    assert "CLOUDFLARE" not in sample_raw
    assert "id-token" not in sample_raw
    assert "secrets." not in sample_raw
    assert "vars." not in sample_raw
    assert "GITHUB_EVENT_PATH" in sample_raw
    assert "precios-sps-la-colonia-mvp-live-request/v1" in sample_raw
    assert "trigger_pr_number" in sample_raw
    assert "2026-08-25T01:20:22Z" in sample_raw
    assert "Merge pull request #277 from" in sample_raw
    assert "jchernandez-portfolio/feature/precios-sps-mvp-live-trigger" in sample_raw
    assert "commercial_persistence" in sample_raw
    assert "production_authority" in sample_raw


def test_location_binding_entrypoint_is_manual_and_fail_closed() -> None:
    path = WORKFLOW_DIR / LOCATION_BINDING_WORKFLOW
    workflow = load_workflow(path)
    assert workflow["permissions"] == {"contents": "read"}
    assert set(jobs(workflow)) == {"binding"}
    binding = jobs(workflow)["binding"]
    assert binding["if"] == "${{ false }}"
    assert binding["timeout-minutes"] == "1"
    assert "environment" not in binding
    assert "permissions" not in binding
    assert workflow["concurrency"] == {
        "group": "la-colonia-location-binding-read-only",
        "cancel-in-progress": "false",
    }
    assert set(workflow["on"]) == {"workflow_dispatch"}

    raw = path.read_text(encoding="utf-8")
    assert "--standing-public-read-only" not in raw
    assert "--authorization-id" not in raw
    assert "standing-request" not in raw
    assert "createCommitStatus" not in raw
    assert "scripts/diagnosticar_binding_ubicacion_la_colonia.py" not in raw
    assert "secrets." not in raw
    assert "vars." not in raw
    assert "id-token" not in raw
    assert "actions: write" not in raw
    assert "statuses: write" not in raw
    assert "pull_request_target" not in raw
    assert "issue_comment" not in raw
    assert "schedule:" not in raw


def test_historical_branch_audit_is_read_only_reproducible_and_not_live() -> None:
    path = WORKFLOW_DIR / AUDIT_WORKFLOW
    workflow = load_workflow(path)
    assert workflow["permissions"] == {"contents": "read", "pull-requests": "read"}
    assert workflow["concurrency"] == {
        "group": "precios-sps-historical-branch-audit",
        "cancel-in-progress": "false",
    }
    triggers = workflow["on"]
    assert set(triggers) == {"workflow_dispatch", "push"}
    assert triggers["push"]["branches"] == ["main"]
    assert set(jobs(workflow)) == {"audit"}
    audit = jobs(workflow)["audit"]
    assert audit["timeout-minutes"] == "20"
    assert "environment" not in audit
    assert "permissions" not in audit

    raw = path.read_text(encoding="utf-8")
    assert "git fetch --no-tags --prune origin '+refs/heads/*:refs/remotes/origin/*'" in raw
    assert "--inspect-only" in raw
    assert "GITHUB_TOKEN: ${{ github.token }}" in raw
    assert "actions/upload-artifact@" in raw
    assert "pull_request:" not in raw
    assert "pull_request_target:" not in raw
    assert "issue_comment:" not in raw
    assert "schedule:" not in raw
    assert "id-token" not in raw
    assert secret_references(path) in (
        set(),
        {TURSO_DATABASE_URL_SECRET, TURSO_AUTH_TOKEN_SECRET},
    )
    assert "vars." not in raw
    assert "scripts/probar_la_colonia.py" not in raw
    assert "scripts/diagnosticar_ventanas_la_colonia.py" not in raw
    assert "scripts/descubrir_facets_la_colonia.py" not in raw
    assert "scripts/diagnosticar_binding_ubicacion_la_colonia.py" not in raw
    assert ".workers.dev" not in raw


def test_controlled_probe_is_manual_isolated_and_verified_outside_oidc_job():
    path = WORKFLOW_DIR / PROBE_WORKFLOW
    workflow = load_workflow(path)
    triggers = workflow["on"]
    assert isinstance(triggers, dict)
    assert set(triggers) == {"workflow_dispatch"}
    assert triggers["workflow_dispatch"] in ("", None)

    probe_jobs = jobs(workflow)
    assert set(probe_jobs) == {"controlled-probe", "verify-evidence"}
    privileged = probe_jobs["controlled-probe"]
    verifier = probe_jobs["verify-evidence"]
    assert privileged.get("environment") == "cloudflare-probe"
    assert verifier.get("environment") == "cloudflare-probe"
    assert privileged.get("if") != "${{ false }}"
    assert verifier.get("needs") == "controlled-probe"
    assert privileged["permissions"] == {"contents": "read", "id-token": "write"}
    assert "permissions" not in verifier
    assert workflow["permissions"] == {"contents": "read"}

    privileged_raw = "\n".join(str(step) for step in job_steps(privileged))
    verifier_raw = "\n".join(str(step) for step in job_steps(verifier))
    raw = path.read_text(encoding="utf-8")
    assert "actions/checkout@" not in privileged_raw
    assert "CLOUDFLARE_PROBE_OBSERVABILITY_TOKEN" not in privileged_raw
    assert "ACTIONS_ID_TOKEN_REQUEST_TOKEN" not in verifier_raw
    assert "ACTIONS_ID_TOKEN_REQUEST_URL" not in verifier_raw
    assert "cloudflare_controlled_probe_verifier" in verifier_raw
    assert "cloudflare_controlled_probe_observability" in verifier_raw
    assert "CloudflareObservabilityHttpTransport" in verifier_raw
    assert "PROBE_OBSERVABILITY_TOKEN" in verifier_raw
    assert "actions/download-artifact@" in verifier_raw
    assert "${{ inputs." not in raw
    assert "github.event.inputs" not in raw
    assert "originUrl" not in raw
    assert "scripts/probar_la_colonia.py" not in raw
    assert "scripts/diagnosticar_ventanas_la_colonia.py" not in raw
    assert "scripts/descubrir_facets_la_colonia.py" not in raw
    assert "urn:precios-sps:cloudflare:probe:v1" in raw
    assert "environment: cloudflare-probe" in raw
    assert ".workers.dev" in raw
    assert "--retry 0" in raw
    assert "${PROBE_GATEWAY_URL%/}/v1/probe" in raw


def test_only_explicit_oidc_jobs_can_request_write_permission():
    allowed = {
        (PROBE_WORKFLOW, "controlled-probe"),
        (LIVE_WORKFLOW, LIVE_FACET_JOB),
    }
    observed: set[tuple[str, str]] = set()
    for path, workflow in workflows():
        assert "id-token" not in workflow["permissions"]
        for name, job in jobs(workflow).items():
            permissions = job.get("permissions", {})
            if permissions.get("id-token") == "write":
                observed.add((path.name, name))
                assert (path.name, name) in allowed
            else:
                assert "id-token" not in permissions
    assert observed == allowed


def test_context_bound_facet_job_has_exact_privilege_and_stays_inert() -> None:
    path = WORKFLOW_DIR / LIVE_WORKFLOW
    workflow = load_workflow(path)
    live_jobs = jobs(workflow)
    assert set(live_jobs) == {LIVE_MVP_JOB, "live-crawl", LIVE_FACET_JOB}
    facet = live_jobs[LIVE_FACET_JOB]
    assert facet["if"] == "${{ false }}"
    assert facet["environment"] == "la-colonia-live"
    assert facet["permissions"] == {"contents": "read", "id-token": "write"}
    assert facet["timeout-minutes"] == "15"
    assert facet["env"] == {
        "PYTHONPATH": "${{ github.workspace }}/precios-supermercados-sps/src",
        EDGE_GATEWAY_VAR: "${{ vars.CLOUDFLARE_EDGE_GATEWAY_URL }}",
        EDGE_PUBLIC_KEY_VAR: "${{ vars.CLOUDFLARE_EDGE_RECEIPT_PUBLIC_KEY_SPKI_B64URL }}",
    }
    raw = path.read_text(encoding="utf-8")
    assert "facet_authorization_id:" in raw
    assert "ejecutar_facets_context_bound_la_colonia.py" in raw
    assert "--authorization-id \"${{ inputs.facet_authorization_id }}\"" in raw
    assert "secrets." not in raw
    assert "schedule:" not in raw
    assert "pull_request:" not in raw
    assert "pull_request_target:" not in raw
    assert "issue_comment:" not in raw


def test_trigger_sets_are_closed_without_issue_comment_authority():
    for path, workflow in workflows():
        triggers = workflow["on"]
        assert isinstance(triggers, dict)
        assert set(triggers) == EXPECTED_TRIGGERS[path.name]
        assert "issue_comment" not in triggers


def test_pull_request_target_never_checks_out_untrusted_pr_code():
    for path, workflow in workflows():
        triggers = workflow["on"]
        if "pull_request_target" not in triggers:
            continue
        raw = path.read_text(encoding="utf-8")
        assert path.name in BLOCKED_ENTRYPOINTS
        assert all_jobs_blocked(workflow)
        assert "actions: write" not in raw
        assert "github.head_ref" not in raw
        assert "github.event.pull_request.head" not in raw
        checkout = [
            step
            for step in steps(workflow)
            if str(step.get("uses", "")).startswith("actions/checkout@")
        ]
        assert all(step["with"]["ref"] == "${{ github.workflow_sha }}" for step in checkout)


def test_network_capable_scripts_are_blocked_without_current_live_authority() -> None:
    blocked_commands = {
        "scripts/probar_la_colonia.py",
        "scripts/probar_muestra_sps_la_colonia.py",
        "scripts/diagnosticar_ventanas_la_colonia.py",
        "scripts/descubrir_facets_la_colonia.py",
        "scripts/diagnosticar_binding_ubicacion_la_colonia.py",
        "scripts/ejecutar_facets_context_bound_la_colonia.py",
    }
    for path, workflow in workflows():
        commands = "\n".join(str(step.get("run", "")) for step in steps(workflow))
        if any(command in commands for command in blocked_commands):
            if path.name == LIVE_WORKFLOW:
                live_jobs = jobs(workflow)
                assert live_jobs["live-crawl"]["if"] == "${{ false }}"
                assert live_jobs[LIVE_FACET_JOB]["if"] == "${{ false }}"
                sample_raw = "\n".join(str(step) for step in job_steps(live_jobs[LIVE_MVP_JOB]))
                assert "mvp_live_request_mismatch" in sample_raw
                assert "mvp_live_merge_identity_mismatch" in sample_raw
                continue
            assert path.name in BLOCKED_ENTRYPOINTS
            assert all_jobs_blocked(workflow)


def test_legacy_google_sheets_and_bigquery_workflows_are_removed():
    """Este proyecto persiste en Turso; esos workflows eran de otro proyecto."""
    names = {path.name for path in ALL_WORKFLOW_FILES}
    assert "precios-supermercados-sps-google-sheets-storage.yml" not in names
    assert "precios-supermercados-sps-bigquery-first-load.yml" not in names
    for path in ALL_WORKFLOW_FILES:
        raw = path.read_text(encoding="utf-8")
        assert "google-github-actions/" not in raw, path.name
        assert "PRECIOS_SPS_GOOGLE_SERVICE_ACCOUNT_JSON" not in raw, path.name
        assert "PRECIOS_SPS_GCP_" not in raw, path.name


def test_ci_paths_are_isolated_to_rpi_project_and_its_own_ci_workflow():
    workflow = load_workflow(WORKFLOW_DIR / TEST_WORKFLOW)
    expected_paths = {
        "precios-supermercados-sps/**",
        ".github/workflows/precios-supermercados-sps-tests.yml",
    }
    pull_request = workflow["on"]["pull_request"]
    push = workflow["on"]["push"]
    assert set(pull_request["paths"]) == expected_paths
    assert set(push["paths"]) == expected_paths
    assert push["branches"] == ["main"]


def test_la_colonia_daily_verifier_counts_only_its_locations_in_shared_database():
    import ast
    import sqlite3

    workflow = load_workflow(WORKFLOW_DIR / MVP_UPDATE_WORKFLOW)
    verification = next(step for job in workflow["jobs"].values() for step in job_steps(job)
                        if step.get("name") == "Verificar commits exactos en Turso")
    script = verification["run"].split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    statements = [node for node in ast.walk(ast.parse(script))
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                  and node.func.id == "_stmt" and isinstance(node.args[0], ast.Constant)
                  and str(node.args[0].value).startswith("SELECT location_id,COUNT(*)")]
    assert len(statements) == 1
    statement = statements[0]
    args = ast.literal_eval(statement.args[1]) if len(statement.args) > 1 else ()
    with sqlite3.connect(":memory:") as con:
        con.execute("CREATE TABLE price_history(supermarket_id TEXT, location_id TEXT, valid_to_utc TEXT)")
        con.executemany("INSERT INTO price_history VALUES(?,?,?)", [
            ("la_colonia", "la_colonia_sps", None), ("la_colonia", "la_colonia_sps", None),
            ("la_colonia", "la_colonia_tgu", None), ("la_colonia", "la_colonia_sps", "closed"),
            ("colonial", "colonial_sps", None),
        ])
        assert con.execute(statement.args[0].value, args).fetchall() == [
            ("la_colonia_sps", 2), ("la_colonia_tgu", 1),
        ]


@pytest.mark.parametrize("suffix", [".yml", ".yaml"])
def test_renamed_sps_workflow_is_still_classified(tmp_path: Path, suffix: str):
    fake = tmp_path / f"backdoor{suffix}"
    fake.write_text(
        "name: alternate\non:\n  workflow_dispatch:\npermissions:\n  contents: read\n"
        "jobs:\n  bypass:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: python precios-supermercados-sps/scripts/probar_la_colonia.py\n",
        encoding="utf-8",
    )
    assert is_sps_workflow(fake)


def test_job_level_reusable_workflow_reference_cannot_evade_pin_audit():
    workflow = {"jobs": {"bypass": {"uses": "attacker/example/.github/workflows/live.yml@main"}}}
    assert action_references(workflow) == ("attacker/example/.github/workflows/live.yml@main",)
    reference = action_references(workflow)[0]
    action, separator, revision = reference.partition("@")
    assert separator
    assert not re.fullmatch(r"[0-9a-f]{40}", revision)
    assert action not in PINNED_ACTIONS


def test_yaml_comments_cannot_satisfy_a_security_field(tmp_path: Path):
    fake = tmp_path / "fake.yml"
    fake.write_text(
        "name: fake\non:\n  workflow_dispatch:\n# permissions:\n#   contents: read\n"
        "jobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
        f"      # - uses: actions/checkout@{PINNED_ACTIONS['actions/checkout']}\n",
        encoding="utf-8",
    )
    with pytest.raises(AssertionError):
        load_workflow(fake)
