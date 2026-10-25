"""Quality and freshness contracts shared by analytical products.

Source-specific completeness remains upstream.  This module combines that
authoritative decision with non-authoritative health signals and selects the
latest run that may serve commercial data.  Statistical anomalies can degrade
a run, but they cannot accept an incomplete capture or replace a last-known-
good observation.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum


class AnalyticsQualityError(ValueError):
    """The supplied quality/freshness evidence is internally inconsistent."""


class QualityStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    DEGRADED = "DEGRADED"
    REJECTED = "REJECTED"


class FreshnessStatus(StrEnum):
    FRESH = "FRESH"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"


class MarketWindowStatus(StrEnum):
    COMPARABLE = "COMPARABLE"
    INSUFFICIENT_FRESH_COMPARISON = "INSUFFICIENT_FRESH_COMPARISON"


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise AnalyticsQualityError(f"{field_name}_not_timezone_aware")
    return value.astimezone(UTC)


def _clean_reasons(values: Iterable[str], field_name: str) -> tuple[str, ...]:
    supplied = tuple(values)
    if any(not isinstance(value, str) for value in supplied):
        raise AnalyticsQualityError(f"{field_name}_invalid")
    cleaned = tuple(dict.fromkeys(value.strip() for value in supplied if value.strip()))
    return cleaned


@dataclass(frozen=True, slots=True)
class RunQualityAssessment:
    """Result of combining authoritative completeness and advisory health."""

    completeness_passed: bool
    status: QualityStatus
    completeness_failures: tuple[str, ...]
    health_warnings: tuple[str, ...]


def assess_run_quality(
    *,
    completeness_passed: bool,
    completeness_failures: Iterable[str] = (),
    health_warnings: Iterable[str] = (),
) -> RunQualityAssessment:
    """Fail closed on completeness; health anomalies only degrade valid data."""

    if not isinstance(completeness_passed, bool):
        raise AnalyticsQualityError("completeness_passed_invalid")
    failures = _clean_reasons(tuple(completeness_failures), "completeness_failures")
    warnings = _clean_reasons(tuple(health_warnings), "health_warnings")
    if completeness_passed and failures:
        raise AnalyticsQualityError("completeness_decision_conflict")
    if not completeness_passed and not failures:
        raise AnalyticsQualityError("completeness_failure_reason_required")

    status = (
        QualityStatus.REJECTED
        if not completeness_passed
        else QualityStatus.DEGRADED
        if warnings
        else QualityStatus.ACCEPTED
    )
    return RunQualityAssessment(
        completeness_passed=completeness_passed,
        status=status,
        completeness_failures=failures,
        health_warnings=warnings,
    )


@dataclass(frozen=True, slots=True)
class QualityRun:
    """Minimal run evidence used to choose a last-known-good observation."""

    run_id: str
    source_id: str
    location_id: str
    observed_at_utc: datetime
    quality_status: QualityStatus

    def __post_init__(self) -> None:
        for field_name in ("run_id", "source_id", "location_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise AnalyticsQualityError(f"{field_name}_missing")
            object.__setattr__(self, field_name, value.strip())
        object.__setattr__(self, "observed_at_utc", _utc(self.observed_at_utc, "observed_at_utc"))
        try:
            status = QualityStatus(self.quality_status)
        except (TypeError, ValueError) as exc:
            raise AnalyticsQualityError("quality_status_invalid") from exc
        object.__setattr__(self, "quality_status", status)


def select_last_known_good(
    runs: Iterable[QualityRun],
    *,
    source_id: str,
    location_id: str,
    as_of_utc: datetime,
) -> QualityRun | None:
    """Return the newest accepted/degraded run at or before ``as_of_utc``."""

    as_of = _utc(as_of_utc, "as_of_utc")
    candidates = [
        run
        for run in runs
        if run.source_id == source_id
        and run.location_id == location_id
        and run.observed_at_utc <= as_of
        and run.quality_status in {QualityStatus.ACCEPTED, QualityStatus.DEGRADED}
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda run: (run.observed_at_utc, run.run_id))


@dataclass(frozen=True, slots=True)
class FreshnessAssessment:
    source_id: str
    location_id: str
    as_of_utc: datetime
    last_successful_run_id: str | None
    observed_at_utc: datetime | None
    data_age_hours: int | None
    freshness_status: FreshnessStatus
    freshness_window_hours: int


def assess_freshness(
    last_known_good: QualityRun | None,
    *,
    source_id: str,
    location_id: str,
    as_of_utc: datetime,
    freshness_window: timedelta,
) -> FreshnessAssessment:
    """Classify source data age without inventing an observation."""

    as_of = _utc(as_of_utc, "as_of_utc")
    if freshness_window <= timedelta(0):
        raise AnalyticsQualityError("freshness_window_invalid")
    window_hours = int(freshness_window.total_seconds() // 3600)
    if window_hours <= 0:
        raise AnalyticsQualityError("freshness_window_below_one_hour")

    if last_known_good is None:
        return FreshnessAssessment(
            source_id=source_id,
            location_id=location_id,
            as_of_utc=as_of,
            last_successful_run_id=None,
            observed_at_utc=None,
            data_age_hours=None,
            freshness_status=FreshnessStatus.UNAVAILABLE,
            freshness_window_hours=window_hours,
        )
    if last_known_good.source_id != source_id or last_known_good.location_id != location_id:
        raise AnalyticsQualityError("freshness_run_scope_mismatch")
    if last_known_good.observed_at_utc > as_of:
        raise AnalyticsQualityError("freshness_observation_in_future")

    age = as_of - last_known_good.observed_at_utc
    age_hours = int(age.total_seconds() // 3600)
    return FreshnessAssessment(
        source_id=source_id,
        location_id=location_id,
        as_of_utc=as_of,
        last_successful_run_id=last_known_good.run_id,
        observed_at_utc=last_known_good.observed_at_utc,
        data_age_hours=age_hours,
        freshness_status=(
            FreshnessStatus.FRESH if age <= freshness_window else FreshnessStatus.STALE
        ),
        freshness_window_hours=window_hours,
    )


@dataclass(frozen=True, slots=True)
class MarketWindowAssessment:
    status: MarketWindowStatus
    as_of_utc: datetime
    freshness_window_hours: int
    source_count: int
    fresh_source_count: int
    maximum_observation_gap_hours: int | None
    reasons: tuple[str, ...]


def assess_comparable_market_window(
    assessments: Iterable[FreshnessAssessment],
) -> MarketWindowAssessment:
    """Allow direct ranking only when every source is fresh and time-compatible."""

    rows = tuple(assessments)
    if len(rows) < 2:
        raise AnalyticsQualityError("market_window_requires_two_sources")
    as_of_values = {row.as_of_utc for row in rows}
    window_values = {row.freshness_window_hours for row in rows}
    scopes = {(row.source_id, row.location_id) for row in rows}
    if len(as_of_values) != 1:
        raise AnalyticsQualityError("market_window_as_of_mismatch")
    if len(window_values) != 1:
        raise AnalyticsQualityError("market_window_configuration_mismatch")
    if len(scopes) != len(rows):
        raise AnalyticsQualityError("market_window_scope_duplicate")

    reasons: list[str] = []
    unavailable = sum(row.freshness_status is FreshnessStatus.UNAVAILABLE for row in rows)
    stale = sum(row.freshness_status is FreshnessStatus.STALE for row in rows)
    if unavailable:
        reasons.append("source_data_unavailable")
    if stale:
        reasons.append("source_data_stale")

    observed = [row.observed_at_utc for row in rows if row.observed_at_utc is not None]
    gap_hours: int | None = None
    if len(observed) == len(rows):
        gap = max(observed) - min(observed)
        gap_hours = int(gap.total_seconds() // 3600)
        if gap_hours > next(iter(window_values)):
            reasons.append("source_timestamps_incompatible")

    return MarketWindowAssessment(
        status=(
            MarketWindowStatus.COMPARABLE
            if not reasons
            else MarketWindowStatus.INSUFFICIENT_FRESH_COMPARISON
        ),
        as_of_utc=next(iter(as_of_values)),
        freshness_window_hours=next(iter(window_values)),
        source_count=len(rows),
        fresh_source_count=sum(row.freshness_status is FreshnessStatus.FRESH for row in rows),
        maximum_observation_gap_hours=gap_hours,
        reasons=tuple(reasons),
    )
