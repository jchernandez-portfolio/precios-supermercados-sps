from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from google.api_core.exceptions import Conflict, ServiceUnavailable

from precios_supermercados.bigquery_adapter import BigQueryAdapterError
from precios_supermercados.bigquery_contract import BigQueryField, BigQueryTableSpec
from precios_supermercados.google_bigquery_client import GoogleCloudBigQueryClient

UTC = UTC


def staging_spec() -> BigQueryTableSpec:
    return BigQueryTableSpec(
        name="staging_serialization_test",
        fields=(
            BigQueryField("id", "STRING", True),
            BigQueryField("observed_at_utc", "TIMESTAMP", True),
            BigQueryField("amount", "NUMERIC"),
            BigQueryField("business_date", "DATE"),
        ),
        logical_key=("id",),
    )


def test_staging_row_converts_python_temporal_and_decimal_values_to_json():
    spec = staging_spec()
    source = {
        "id": "row-1",
        "observed_at_utc": datetime(2026, 8, 27, 3, 30, 1, 123456, tzinfo=UTC),
        "amount": Decimal("40.5000"),
        "business_date": date(2026, 8, 27),
    }

    normalized = GoogleCloudBigQueryClient._json_compatible_row(spec, source)

    assert normalized == {
        "id": "row-1",
        "observed_at_utc": "2026-08-27T03:30:01.123456Z",
        "amount": "40.5000",
        "business_date": "2026-08-27",
    }
    assert json.loads(json.dumps(normalized)) == normalized


def test_staging_timestamp_rejects_naive_and_non_utc_datetime():
    spec = staging_spec()
    base = {
        "id": "row-1",
        "amount": None,
        "business_date": None,
    }

    with pytest.raises(BigQueryAdapterError, match="timestamp_timezone_required"):
        GoogleCloudBigQueryClient._json_compatible_row(
            spec,
            {**base, "observed_at_utc": datetime(2026, 8, 27, 3, 30)},
        )

    with pytest.raises(BigQueryAdapterError, match="timestamp_must_be_utc"):
        GoogleCloudBigQueryClient._json_compatible_row(
            spec,
            {
                **base,
                "observed_at_utc": datetime(
                    2026,
                    8,
                    26,
                    21,
                    30,
                    tzinfo=timezone(-timedelta(hours=6)),
                ),
            },
        )


def test_staging_row_rejects_schema_drift_before_sdk_load():
    spec = staging_spec()
    with pytest.raises(BigQueryAdapterError, match="staging_row_schema_mismatch"):
        GoogleCloudBigQueryClient._json_compatible_row(
            spec,
            {
                "id": "row-1",
                "observed_at_utc": "2026-08-27T03:30:00Z",
                "amount": "40",
            },
        )


class _SuccessfulJob:
    def result(self):
        return ()


class _CapturingQueryClient:
    project = "test-project"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def query(self, sql: str, **kwargs):
        self.calls.append((sql, kwargs))
        return _SuccessfulJob()


def test_atomic_script_uses_explicit_idempotent_job_id_and_disables_job_retry():
    low_level = _CapturingQueryClient()
    client = GoogleCloudBigQueryClient(low_level)

    reused = client._run_atomic_script(
        dataset_id="precios_sps",
        run_id="run-1",
        script="SELECT 1",
        location="US",
    )

    assert reused is False
    assert len(low_level.calls) == 1
    sql, kwargs = low_level.calls[0]
    assert sql == "SELECT 1"
    assert kwargs["job_id"] == client._atomic_job_id("precios_sps", "run-1", 0)
    assert kwargs["location"] == "US"
    assert kwargs["job_retry"] is None


class _ConflictThenReuseClient:
    project = "test-project"

    def __init__(self) -> None:
        self.requested_job_id: str | None = None
        self.get_job_args: tuple[str, str | None] | None = None

    def query(self, sql: str, **kwargs):
        del sql
        self.requested_job_id = str(kwargs["job_id"])
        raise Conflict("job already exists")

    def get_job(self, job_id: str, *, location: str | None = None):
        self.get_job_args = (job_id, location)
        return _SuccessfulJob()


def test_atomic_script_reuses_existing_same_run_job_on_insert_conflict():
    low_level = _ConflictThenReuseClient()
    client = GoogleCloudBigQueryClient(low_level)

    reused = client._run_atomic_script(
        dataset_id="precios_sps",
        run_id="run-1",
        script="SELECT 1",
        location="US",
    )

    assert reused is True
    assert low_level.requested_job_id is not None
    assert low_level.get_job_args == (low_level.requested_job_id, "US")


class _ResultFailsOnceClient:
    project = "test-project"

    def __init__(self) -> None:
        self.job_ids: list[str] = []

    def query(self, sql: str, **kwargs):
        del sql
        self.job_ids.append(str(kwargs["job_id"]))
        position = len(self.job_ids)

        class Job:
            def result(self_inner):
                if position == 1:
                    raise ServiceUnavailable("transaction aborted due to concurrent update")
                return ()

        return Job()


def test_atomic_script_advances_deterministically_after_transient_failure():
    low_level = _ResultFailsOnceClient()
    client = GoogleCloudBigQueryClient(low_level)

    assert client._run_atomic_script(
        dataset_id="precios_sps",
        run_id="run-1",
        script="SELECT 1",
        location="US",
    ) is False
    assert low_level.job_ids == [
        client._atomic_job_id("precios_sps", "run-1", 0),
        client._atomic_job_id("precios_sps", "run-1", 1),
    ]
