from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/precios-supermercados-sps-bigquery-first-load.yml"
BOOTSTRAP = ROOT / "precios-supermercados-sps/scripts/preparar_bigquery_gcp.sh"


def test_legacy_first_load_workflow_is_removed() -> None:
    # La carga BigQuery pertenecía a otro proyecto; RPI persiste en Turso.
    assert not WORKFLOW.exists()


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
