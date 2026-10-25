from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from precios_supermercados.commercial_state import ApplyRunResult
from precios_supermercados.enums import RunStatus
from precios_supermercados.tabular_persistence import TabularPersistenceError
from precios_supermercados.tabular_records import ScrapeRunRecord

START = datetime(2026, 8, 22, 18, 0, tzinfo=UTC)
END = START + timedelta(minutes=1)


def build(result: ApplyRunResult, *, status=RunStatus.SUCCESS, accepted=True):
    return ScrapeRunRecord.from_apply_result(
        supermarket_id="la_colonia",
        location_id="la_colonia_sps",
        run_status=status,
        catalog_accepted=accepted,
        started_at_utc=START,
        finished_at_utc=END,
        products_observed=1,
        offers_observed=1,
        quality_event_count=0,
        apply_result=result,
    )


def test_apply_result_must_match_commercial_update_decision() -> None:
    contradictory = ApplyRunResult(
        scrape_run_id="run-1",
        commercial_update_allowed=False,
        replayed=False,
        mutated=False,
        current_created=0,
        current_changed=0,
        current_confirmed=0,
        offers_ignored=1,
    )
    with pytest.raises(TabularPersistenceError, match="contradice"):
        build(contradictory, status=RunStatus.SUCCESS, accepted=True)


def test_non_commercial_run_cannot_claim_current_mutation() -> None:
    contradictory = ApplyRunResult(
        scrape_run_id="run-2",
        commercial_update_allowed=False,
        replayed=False,
        mutated=True,
        current_created=1,
        current_changed=0,
        current_confirmed=0,
        offers_ignored=0,
    )
    with pytest.raises(TabularPersistenceError, match="no puede reportar"):
        build(contradictory, status=RunStatus.REJECTED, accepted=False)


def test_consistent_non_commercial_run_is_recordable() -> None:
    ignored = ApplyRunResult(
        scrape_run_id="run-3",
        commercial_update_allowed=False,
        replayed=False,
        mutated=False,
        current_created=0,
        current_changed=0,
        current_confirmed=0,
        offers_ignored=1,
    )
    record = build(ignored, status=RunStatus.REJECTED, accepted=False)
    assert record.scrape_run_id == "run-3"
    assert record.offers_ignored == 1
