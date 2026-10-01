from __future__ import annotations

import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/precios-supermercados-sps-bigquery-first-load.yml"
BOOTSTRAP = ROOT / "precios-supermercados-sps/scripts/preparar_bigquery_gcp.sh"


def _workflow() -> dict[str, object]:
    value = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(value, dict)
    return value


def test_first_load_is_manual_main_only_and_requires_explicit_boolean() -> None:
    workflow = _workflow()
    triggers = workflow["on"]
    assert isinstance(triggers, dict)
    assert set(triggers) == {"workflow_dispatch"}
    dispatch = triggers["workflow_dispatch"]
    assert isinstance(dispatch, dict)
    inputs = dispatch["inputs"]
    assert set(inputs) == {"apply_initial_snapshot"}
    assert inputs["apply_initial_snapshot"] == {
        "description": "Confirma la primera carga durable del snapshot inicial aprobado",
        "required": "true",
        "default": "false",
        "type": "boolean",
    }

    jobs = workflow["jobs"]
    assert set(jobs) == {"first-load"}
    job = jobs["first-load"]
    assert "github.repository == 'jchernandez-portfolio/precios-supermercados-sps'" in job["if"]
    assert "github.ref == 'refs/heads/main'" in job["if"]
    assert "inputs.apply_initial_snapshot == true" in job["if"]
    assert job["permissions"] == {
        "actions": "read",
        "contents": "read",
        "id-token": "write",
    }


def test_first_load_uses_exact_preserved_artifact_and_keyless_wif() -> None:
    raw = WORKFLOW.read_text(encoding="utf-8")

    assert 'SOURCE_ARTIFACT_ID: "9655225996"' in raw
    assert (
        'EXPECTED_ARTIFACT_DIGEST: "0427e88be27df89fd9fcb50ed600ef5c6aef64177bfba92b4af3d2e25756a892"'
        in raw
    )
    assert (
        'EXPECTED_JSON_SHA256: "2780eeffa5ef62f2d1c8c2c8365e88da1ca0006622d2f7b1c3529f834c9b5e50"'
        in raw
    )
    assert "sha256sum --check --strict" in raw
    assert "google-github-actions/auth@7c6bc770dae815cd3e89ee6cdf493a5fab2cc093 # v3" in raw
    assert "workload_identity_provider:" in raw
    assert "service_account:" in raw
    assert "create_credentials_file: true" in raw
    assert "credentials_json" not in raw
    assert "secrets." not in raw
    assert "lacolonia.com" not in raw
    assert "scripts/cargar_snapshot_inicial_bigquery.py" in raw
    assert "--apply" in raw


def test_cloud_bootstrap_is_least_privilege_and_repo_id_bound() -> None:
    raw = BOOTSTRAP.read_text(encoding="utf-8")

    assert ': "${PROJECT_ID:?' in raw
    assert ': "${DATASET_LOCATION:?' in raw
    assert 'GITHUB_REPOSITORY_ID="1282475205"' in raw
    assert 'GITHUB_MAIN_REF="refs/heads/main"' in raw
    assert 'EXPECTED_ISSUER="https://token.actions.githubusercontent.com"' in raw
    assert "https://token.actions.githubusercontent.com/\"" not in raw
    assert "assertion.repository_id=='${GITHUB_REPOSITORY_ID}'" in raw
    assert "assertion.ref=='${GITHUB_MAIN_REF}'" in raw
    assert "attribute.repository_id=assertion.repository_id" in raw
    assert "roles/bigquery.jobUser" in raw
    assert "roles/bigquery.dataEditor" in raw
    assert "ON SCHEMA" in raw
    assert "roles/iam.workloadIdentityUser" in raw
    assert "roles/bigquery.admin" not in raw
    assert "service-accounts keys create" not in raw
    assert "credentials_json" not in raw
    assert "DATASET_LOCATION" in raw
    assert "dataset_location_mismatch" in raw


def test_cloud_bootstrap_has_valid_bash_syntax() -> None:
    result = subprocess.run(
        ["bash", "-n", str(BOOTSTRAP)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
