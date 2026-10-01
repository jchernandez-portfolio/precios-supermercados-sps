from __future__ import annotations

from pathlib import Path

from precios_supermercados.bigquery_contract import (
    BIGQUERY_TABLE_NAMES,
    PRECIOS_HISTORICOS,
)
from precios_supermercados.google_bigquery_client import GoogleCloudBigQueryClient
from precios_supermercados.storage_contract import (
    ACTIVE_STORAGE_BACKEND,
    ACTIVE_STORAGE_TABLE_NAMES,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SHEETS_WORKFLOW = (
    REPO_ROOT
    / ".github"
    / "workflows"
    / "precios-supermercados-sps-google-sheets-storage.yml"
)


def test_bigquery_contract_is_active_and_retired_sheets_workflow_is_removed():
    assert ACTIVE_STORAGE_BACKEND == "bigquery"
    assert ACTIVE_STORAGE_TABLE_NAMES == BIGQUERY_TABLE_NAMES

    # El workflow de Google Sheets era de otro proyecto y se eliminó.
    assert not SHEETS_WORKFLOW.exists()


def test_real_client_immutable_guard_uses_transaction_supported_query_statement():
    sql = GoogleCloudBigQueryClient._immutable_guard_sql(
        spec=PRECIOS_HISTORICOS,
        target="project.dataset.precios_historicos",
        staging_ref="project.dataset._stg_precios_historicos_deadbeef",
        join="T.`price_observation_id` = S.`price_observation_id`",
    )

    assert sql.startswith("SELECT IF(COUNTIF(")
    assert "IS DISTINCT FROM" in sql
    assert "ASSERT" not in sql
    assert "immutable_conflict:precios_historicos" in sql
    assert sql.endswith(";")
