from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from precios_supermercados.commercial_state import ApplyRunResult
from precios_supermercados.enums import (
    QualityEventCategory,
    QualityEventSeverity,
    RunStatus,
)
from precios_supermercados.tabular_persistence import TABLE_SPECS, TabularPersistenceError
from precios_supermercados.tabular_records import (
    QualityEventRecord,
    ScrapeRunRecord,
    quality_event_row,
    scrape_run_row,
)
from precios_supermercados.tabular_store import (
    InMemoryTabularStore,
    TabularBatch,
    TabularStoreConflict,
    TabularStoreError,
)

START = datetime(2026, 8, 22, 18, 0, tzinfo=UTC)
END = START + timedelta(minutes=3)


def make_run(**overrides) -> ScrapeRunRecord:
    values = {
        "scrape_run_id": "run-001",
        "supermarket_id": "la_colonia",
        "location_id": "la_colonia_sps",
        "run_status": RunStatus.REJECTED,
        "catalog_accepted": False,
        "started_at_utc": START,
        "finished_at_utc": END,
        "products_observed": 0,
        "offers_observed": 0,
        "current_created": 0,
        "current_changed": 0,
        "current_confirmed": 0,
        "offers_ignored": 0,
        "quality_event_count": 1,
        "run_evidence_id": None,
    }
    values.update(overrides)
    return ScrapeRunRecord(**values)


def test_rejected_run_is_persistible_even_when_location_extraction_is_disabled() -> None:
    row = scrape_run_row(make_run())
    assert row["scrape_run_id"] == "run-001"
    assert row["supermarket_id"] == "la_colonia"
    assert row["location_id"] == "la_colonia_sps"
    assert row["run_status"] == "rejected"
    assert row["catalog_accepted"] is False
    assert row["started_at_utc"] == "2026-08-22T18:00:00.000Z"
    assert row["finished_at_utc"] == "2026-08-22T18:03:00.000Z"
    assert tuple(row) == TABLE_SPECS["fact_scrape_runs"].columns


def test_every_final_status_except_running_can_be_recorded() -> None:
    for status in (
        RunStatus.SUCCESS,
        RunStatus.WARNING,
        RunStatus.REJECTED,
        RunStatus.FAILED,
        RunStatus.ABANDONED,
    ):
        accepted = status in {RunStatus.SUCCESS, RunStatus.WARNING}
        record = make_run(run_status=status, catalog_accepted=accepted)
        assert record.run_status is status
    with pytest.raises(TabularPersistenceError, match="estado final"):
        make_run(run_status=RunStatus.RUNNING)


def test_catalog_acceptance_is_impossible_for_failed_or_rejected_run() -> None:
    for status in (RunStatus.REJECTED, RunStatus.FAILED, RunStatus.ABANDONED):
        with pytest.raises(TabularPersistenceError, match="success/warning"):
            make_run(run_status=status, catalog_accepted=True)


def test_run_time_and_counts_are_fail_closed() -> None:
    with pytest.raises(TabularPersistenceError, match="precede"):
        make_run(finished_at_utc=START - timedelta(seconds=1))
    with pytest.raises(TabularPersistenceError, match="entero no negativo"):
        make_run(products_observed=-1)
    with pytest.raises(TabularPersistenceError, match="entero no negativo"):
        make_run(offers_observed=True)


def test_run_can_be_built_from_commercial_apply_result() -> None:
    applied = ApplyRunResult(
        scrape_run_id="run-apply",
        commercial_update_allowed=True,
        replayed=False,
        mutated=True,
        current_created=3,
        current_changed=2,
        current_confirmed=5,
        offers_ignored=0,
    )
    record = ScrapeRunRecord.from_apply_result(
        supermarket_id="la_colonia",
        location_id="la_colonia_sps",
        run_status=RunStatus.SUCCESS,
        catalog_accepted=True,
        started_at_utc=START,
        finished_at_utc=END,
        products_observed=9,
        offers_observed=10,
        quality_event_count=1,
        apply_result=applied,
        run_evidence_id="evidence-001",
    )
    row = scrape_run_row(record)
    assert row["scrape_run_id"] == "run-apply"
    assert row["current_created"] == 3
    assert row["current_changed"] == 2
    assert row["current_confirmed"] == 5
    assert row["offers_observed"] == 10
    assert row["run_evidence_id"] == "evidence-001"


def test_quality_event_id_is_deterministic_and_sequence_distinguishes_duplicates() -> None:
    first = QualityEventRecord.deterministic(
        scrape_run_id="run-001",
        supermarket_id="la_colonia",
        location_id="la_colonia_sps",
        category=QualityEventCategory.QUALITY,
        severity=QualityEventSeverity.WARNING,
        event_code="missing_brand",
        observed_at_utc=END,
        sequence=0,
    )
    replay = QualityEventRecord.deterministic(
        scrape_run_id="run-001",
        supermarket_id="la_colonia",
        location_id="la_colonia_sps",
        category="quality",
        severity="warning",
        event_code="missing_brand",
        observed_at_utc=END,
        sequence=0,
    )
    second = QualityEventRecord.deterministic(
        scrape_run_id="run-001",
        supermarket_id="la_colonia",
        location_id="la_colonia_sps",
        category="quality",
        severity="warning",
        event_code="missing_brand",
        observed_at_utc=END,
        sequence=1,
    )
    assert first.quality_event_id == replay.quality_event_id
    assert first.quality_event_id != second.quality_event_id
    assert first.quality_event_id.startswith("qe_")
    assert len(first.quality_event_id) == 35


def test_quality_event_row_is_common_and_allows_run_level_event() -> None:
    event = QualityEventRecord.deterministic(
        scrape_run_id="run-001",
        supermarket_id="la_colonia",
        location_id="la_colonia_sps",
        category=QualityEventCategory.STRUCTURE,
        severity=QualityEventSeverity.ERROR,
        event_code="catalog_rejected",
        observed_at_utc=END,
        sequence=0,
        offer_id=None,
    )
    row = quality_event_row(event)
    assert tuple(row) == TABLE_SPECS["fact_quality_events"].columns
    assert row["offer_id"] is None
    assert row["category"] == "structure"
    assert row["severity"] == "error"
    assert row["event_code"] == "catalog_rejected"


def test_run_and_quality_rows_reject_unknown_location() -> None:
    with pytest.raises(TabularPersistenceError, match="location_id desconocido"):
        scrape_run_row(make_run(location_id="la_colonia_unknown"))
    event = QualityEventRecord.deterministic(
        scrape_run_id="run-001",
        supermarket_id="la_colonia",
        location_id="la_colonia_unknown",
        category="quality",
        severity="warning",
        event_code="test",
        observed_at_utc=END,
        sequence=0,
    )
    with pytest.raises(TabularPersistenceError, match="location_id desconocido"):
        quality_event_row(event)


def blank_row(table_name: str, key_value: str, **values):
    spec = TABLE_SPECS[table_name]
    row = {column: None for column in spec.columns}
    assert len(spec.primary_key) == 1
    row[spec.primary_key[0]] = key_value
    row.update(values)
    return row


def test_batch_rejects_unknown_schema_missing_columns_and_divergent_duplicate() -> None:
    with pytest.raises(TabularStoreError, match="tabla desconocida"):
        TabularBatch({"made_up": ()})
    with pytest.raises(TabularStoreError, match="fuera de esquema"):
        TabularBatch({"fact_scrape_runs": ({"scrape_run_id": "x"},)})

    first = blank_row("fact_scrape_runs", "run-x", run_status="failed")
    second = dict(first)
    second["run_status"] = "rejected"
    with pytest.raises(TabularStoreConflict, match="duplicada divergente"):
        TabularBatch({"fact_scrape_runs": (first, second)})


def test_store_accepts_exact_replay_of_immutable_run_but_rejects_divergence() -> None:
    store = InMemoryTabularStore()
    run = scrape_run_row(make_run())
    first = store.apply(TabularBatch({"fact_scrape_runs": (run,)}))
    replay = store.apply(TabularBatch({"fact_scrape_runs": (run,)}))
    assert first.created == 1
    assert replay.replayed == 1
    assert store.count("fact_scrape_runs") == 1

    divergent = dict(run)
    divergent["quality_event_count"] = 99
    with pytest.raises(TabularStoreConflict, match="inmutable divergente"):
        store.apply(TabularBatch({"fact_scrape_runs": (divergent,)}))
    assert store.row("fact_scrape_runs", "run-001")["quality_event_count"] == 1


def test_quality_events_are_immutable() -> None:
    store = InMemoryTabularStore()
    event = QualityEventRecord.deterministic(
        scrape_run_id="run-001",
        supermarket_id="la_colonia",
        location_id="la_colonia_sps",
        category="quality",
        severity="warning",
        event_code="missing_brand",
        observed_at_utc=END,
        sequence=0,
    )
    row = quality_event_row(event)
    store.apply(TabularBatch({"fact_quality_events": (row,)}))
    divergent = dict(row)
    divergent["severity"] = "error"
    with pytest.raises(TabularStoreConflict, match="inmutable divergente"):
        store.apply(TabularBatch({"fact_quality_events": (divergent,)}))


def test_current_and_history_are_upsert_tables() -> None:
    store = InMemoryTabularStore()
    current = blank_row(
        "fact_offers_current",
        "offer-1",
        current_price="30",
        last_scrape_run_id="run-1",
    )
    history = blank_row(
        "fact_offer_history",
        "history-1",
        offer_id="offer-1",
        current_price="30",
        valid_to_utc=None,
    )
    store.apply(
        TabularBatch(
            {
                "fact_offers_current": (current,),
                "fact_offer_history": (history,),
            }
        )
    )

    updated_current = dict(current)
    updated_current["current_price"] = "28"
    updated_current["last_scrape_run_id"] = "run-2"
    closed_history = dict(history)
    closed_history["valid_to_utc"] = "2026-08-23T18:00:00.000Z"
    result = store.apply(
        TabularBatch(
            {
                "fact_offers_current": (updated_current,),
                "fact_offer_history": (closed_history,),
            }
        )
    )
    assert result.updated == 2
    assert store.row("fact_offers_current", "offer-1")["current_price"] == "28"
    assert store.row("fact_offer_history", "history-1")["valid_to_utc"] is not None


def test_store_apply_is_atomic_when_late_immutable_conflict_occurs() -> None:
    store = InMemoryTabularStore()
    original_run = scrape_run_row(make_run())
    store.apply(TabularBatch({"fact_scrape_runs": (original_run,)}))

    current = blank_row(
        "fact_offers_current",
        "offer-atomic",
        current_price="30",
    )
    conflicting_run = dict(original_run)
    conflicting_run["offers_observed"] = 999

    with pytest.raises(TabularStoreConflict):
        store.apply(
            TabularBatch(
                {
                    "fact_offers_current": (current,),
                    "fact_scrape_runs": (conflicting_run,),
                }
            )
        )
    assert store.row("fact_offers_current", "offer-atomic") is None
    assert store.row("fact_scrape_runs", "run-001")["offers_observed"] == 0


def test_batch_deduplicates_identical_rows_with_same_primary_key() -> None:
    row = blank_row("fact_quality_events", "qe-1", event_code="x")
    batch = TabularBatch({"fact_quality_events": (row, dict(row))})
    assert batch.row_count == 1
