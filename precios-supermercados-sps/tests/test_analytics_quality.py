from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from precios_supermercados.analytics_quality import (
    AnalyticsQualityError,
    FreshnessStatus,
    MarketWindowStatus,
    QualityRun,
    QualityStatus,
    assess_comparable_market_window,
    assess_freshness,
    assess_run_quality,
    select_last_known_good,
)

AS_OF = datetime(2026, 9, 8, 12, tzinfo=UTC)


def run(run_id: str, hours_old: int, status: QualityStatus) -> QualityRun:
    return QualityRun(
        run_id=run_id,
        source_id="walmart",
        location_id="walmart_sps",
        observed_at_utc=AS_OF - timedelta(hours=hours_old),
        quality_status=status,
    )


def test_completeness_is_authoritative_and_health_only_degrades() -> None:
    accepted = assess_run_quality(completeness_passed=True)
    degraded = assess_run_quality(
        completeness_passed=True,
        health_warnings=("promotion_share_above_baseline",),
    )
    rejected = assess_run_quality(
        completeness_passed=False,
        completeness_failures=("missing_partition",),
        health_warnings=("low_volume",),
    )

    assert accepted.status is QualityStatus.ACCEPTED
    assert degraded.status is QualityStatus.DEGRADED
    assert rejected.status is QualityStatus.REJECTED
    assert rejected.health_warnings == ("low_volume",)


def test_inconsistent_completeness_evidence_fails_closed() -> None:
    with pytest.raises(AnalyticsQualityError, match="completeness_failure_reason_required"):
        assess_run_quality(completeness_passed=False)
    with pytest.raises(AnalyticsQualityError, match="completeness_decision_conflict"):
        assess_run_quality(completeness_passed=True, completeness_failures=("missing_page",))


def test_last_known_good_ignores_newer_rejected_run() -> None:
    runs = (
        run("monday", 54, QualityStatus.ACCEPTED),
        run("tuesday", 30, QualityStatus.DEGRADED),
        run("wednesday", 2, QualityStatus.REJECTED),
    )
    selected = select_last_known_good(
        runs,
        source_id="walmart",
        location_id="walmart_sps",
        as_of_utc=AS_OF,
    )

    assert selected is not None
    assert selected.run_id == "tuesday"
    assert selected.quality_status is QualityStatus.DEGRADED


def test_fresh_stale_and_unavailable_are_explicit() -> None:
    fresh = assess_freshness(
        run("fresh", 5, QualityStatus.ACCEPTED),
        source_id="walmart",
        location_id="walmart_sps",
        as_of_utc=AS_OF,
        freshness_window=timedelta(hours=24),
    )
    stale = assess_freshness(
        run("stale", 25, QualityStatus.ACCEPTED),
        source_id="walmart",
        location_id="walmart_sps",
        as_of_utc=AS_OF,
        freshness_window=timedelta(hours=24),
    )
    unavailable = assess_freshness(
        None,
        source_id="pricesmart",
        location_id="pricesmart_sps_6603",
        as_of_utc=AS_OF,
        freshness_window=timedelta(hours=24),
    )

    assert fresh.freshness_status is FreshnessStatus.FRESH
    assert fresh.data_age_hours == 5
    assert stale.freshness_status is FreshnessStatus.STALE
    assert stale.data_age_hours == 25
    assert unavailable.freshness_status is FreshnessStatus.UNAVAILABLE
    assert unavailable.data_age_hours is None


def test_market_window_rejects_stale_or_incompatible_source_timestamps() -> None:
    fresh_a = assess_freshness(
        QualityRun("a", "a", "a_sps", AS_OF - timedelta(hours=1), QualityStatus.ACCEPTED),
        source_id="a",
        location_id="a_sps",
        as_of_utc=AS_OF,
        freshness_window=timedelta(hours=24),
    )
    stale_b = assess_freshness(
        QualityRun("b", "b", "b_sps", AS_OF - timedelta(hours=30), QualityStatus.ACCEPTED),
        source_id="b",
        location_id="b_sps",
        as_of_utc=AS_OF,
        freshness_window=timedelta(hours=24),
    )

    result = assess_comparable_market_window((fresh_a, stale_b))
    assert result.status is MarketWindowStatus.INSUFFICIENT_FRESH_COMPARISON
    assert result.reasons == ("source_data_stale", "source_timestamps_incompatible")
    assert result.maximum_observation_gap_hours == 29


def test_market_window_accepts_all_fresh_sources_and_rejects_mixed_as_of() -> None:
    first = assess_freshness(
        QualityRun("a", "a", "a_sps", AS_OF - timedelta(hours=2), QualityStatus.ACCEPTED),
        source_id="a",
        location_id="a_sps",
        as_of_utc=AS_OF,
        freshness_window=timedelta(hours=24),
    )
    second = assess_freshness(
        QualityRun("b", "b", "b_sps", AS_OF - timedelta(hours=4), QualityStatus.DEGRADED),
        source_id="b",
        location_id="b_sps",
        as_of_utc=AS_OF,
        freshness_window=timedelta(hours=24),
    )
    result = assess_comparable_market_window((first, second))
    assert result.status is MarketWindowStatus.COMPARABLE
    assert result.fresh_source_count == 2
    assert result.maximum_observation_gap_hours == 2

    incompatible = assess_freshness(
        QualityRun("c", "c", "c_sps", AS_OF - timedelta(hours=1), QualityStatus.ACCEPTED),
        source_id="c",
        location_id="c_sps",
        as_of_utc=AS_OF + timedelta(hours=1),
        freshness_window=timedelta(hours=24),
    )
    with pytest.raises(AnalyticsQualityError, match="market_window_as_of_mismatch"):
        assess_comparable_market_window((first, incompatible))
