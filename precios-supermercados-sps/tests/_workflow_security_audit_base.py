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

TEST_WORKFLOW = "precios-supermercados-sps-tests.yml"
AUDIT_WORKFLOW = "precios-supermercados-sps-historical-branch-audit.yml"
MVP_UPDATE_WORKFLOW = "precios-supermercados-sps-la-colonia-mvp-update.yml"
HOMOLOGATION_REFRESH_WORKFLOW = "precios-supermercados-sps-homologation-refresh.yml"
TURSO_DATABASE_URL_SECRET = "TURSO_DATABASE_URL"
TURSO_AUTH_TOKEN_SECRET = "TURSO_AUTH_TOKEN"

# Workflows retirados el 2026-10-09: nunca corrieron en este repo y no podían
# correr (sin environments `cloudflare-probe`/`la-colonia-live` ni secrets).
# Su historial queda en git; no deben reaparecer sin una revisión explícita.
REMOVED_WORKFLOWS = frozenset({
    "cloudflare-controlled-probe-evidence-verify.yml",
    "precios-supermercados-sps-cloudflare-probe.yml",
    "precios-supermercados-sps-la-colonia-command.yml",
    "precios-supermercados-sps-la-colonia-diagnostic.yml",
    "precios-supermercados-sps-la-colonia-dispatch-recovery.yml",
    "precios-supermercados-sps-la-colonia-facet-discovery.yml",
    "precios-supermercados-sps-la-colonia-live.yml",
    "precios-supermercados-sps-la-colonia-location-binding.yml",
    "precios-supermercados-sps-preserve-initial-snapshot.yml",
    "precios-supermercados-sps-turso-schema-migration.yml",
    "precios-supermercados-sps-turso-schema-migration-operator.yml",
})

EXPECTED_PERMISSIONS = {
    AUDIT_WORKFLOW: {"contents": "read", "pull-requests": "read"},
    MVP_UPDATE_WORKFLOW: {"contents": "read"},
    HOMOLOGATION_REFRESH_WORKFLOW: {"contents": "read"},
    TEST_WORKFLOW: {"contents": "read"},
}

ALLOWED_JOB_PERMISSIONS = {
    # Lectura de jobs del corte para publicar persistencias parciales (2026-10-09).
    HOMOLOGATION_REFRESH_WORKFLOW: {
        "daily-gate": {"actions": "read"},
    },
}

EXPECTED_TRIGGERS = {
    AUDIT_WORKFLOW: {"workflow_dispatch", "push"},
    # Desde 2026-10-09 lo dispara el Worker edge/daily-trigger (05:17) y, de
    # respaldo, el operador productivo; sin cron propio (llegaba 5–9 h tarde).
    MVP_UPDATE_WORKFLOW: {"workflow_dispatch"},
    HOMOLOGATION_REFRESH_WORKFLOW: {"workflow_dispatch", "workflow_run"},
    TEST_WORKFLOW: {"workflow_dispatch", "pull_request", "push"},
}

ALLOWED_SECRET_REFERENCES = {
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
ALLOWED_VAR_REFERENCES: dict[str, set[str]] = {}

# Scripts con tráfico live manual de La Colonia: ningún workflow los ejecuta.
NETWORK_CAPABLE_SCRIPTS = (
    "scripts/probar_la_colonia.py",
    "scripts/probar_muestra_sps_la_colonia.py",
    "scripts/diagnosticar_ventanas_la_colonia.py",
    "scripts/descubrir_facets_la_colonia.py",
    "scripts/diagnosticar_binding_ubicacion_la_colonia.py",
    "scripts/ejecutar_facets_context_bound_la_colonia.py",
)

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
        checkout_steps = [
            step
            for step in steps(workflow)
            if str(step.get("uses", "")).startswith("actions/checkout@")
        ]
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

        assert checkout_steps, path.name
        for step in checkout_steps:
            inputs = step.get("with")
            assert isinstance(inputs, dict)
            assert inputs == {"ref": "${{ github.sha }}", "persist-credentials": "false"}


def test_removed_live_and_probe_entrypoints_stay_removed():
    names = {path.name for path in ALL_WORKFLOW_FILES}
    assert not names & REMOVED_WORKFLOWS
    assert not (WORKFLOW_DIR / "requests").exists()


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


def test_no_workflow_requests_oidc_tokens():
    for path, workflow in workflows():
        assert "id-token" not in workflow["permissions"], path.name
        for name, job in jobs(workflow).items():
            assert "id-token" not in job.get("permissions", {}), (path.name, name)


def test_trigger_sets_are_closed_without_issue_comment_authority():
    for path, workflow in workflows():
        triggers = workflow["on"]
        assert isinstance(triggers, dict)
        assert set(triggers) == EXPECTED_TRIGGERS[path.name]
        assert "issue_comment" not in triggers


def test_no_workflow_uses_pull_request_target():
    for path in ALL_WORKFLOW_FILES:
        workflow = load_workflow(path)
        assert "pull_request_target" not in workflow["on"], path.name


def test_network_capable_scripts_are_not_wired_into_any_workflow() -> None:
    for path in ALL_WORKFLOW_FILES:
        raw = path.read_text(encoding="utf-8")
        for command in NETWORK_CAPABLE_SCRIPTS:
            assert command not in raw, (path.name, command)


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
