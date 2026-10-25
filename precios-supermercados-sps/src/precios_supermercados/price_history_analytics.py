"""Variabilidad y tendencia de series históricas aceptadas.

Las series deben pertenecer a una identidad canónica ya considerada segura por
la capa de comparación. Este módulo no inventa periodos ni interpola precios.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal


class PriceHistoryAnalyticsError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class HistoricalPriceObservation:
    canonical_product_id: str
    supermarket_id: str
    location_id: str
    observed_at_utc: datetime
    price_minor: int

    def __post_init__(self) -> None:
        if not self.canonical_product_id.strip() or not self.supermarket_id.strip() or not self.location_id.strip():
            raise PriceHistoryAnalyticsError("history_identity_missing")
        if self.observed_at_utc.tzinfo is None or self.observed_at_utc.utcoffset() is None:
            raise PriceHistoryAnalyticsError("history_timestamp_not_timezone_aware")
        if type(self.price_minor) is not int or self.price_minor <= 0:
            raise PriceHistoryAnalyticsError("history_price_invalid")


@dataclass(frozen=True, slots=True)
class PriceSeriesSummary:
    canonical_product_id: str
    supermarket_id: str
    location_id: str
    observation_count: int
    first_observed_at_utc: datetime
    last_observed_at_utc: datetime
    first_price_minor: int
    last_price_minor: int
    minimum_price_minor: int
    maximum_price_minor: int
    mean_price_minor: int
    median_price_minor: Decimal
    absolute_change_minor: int
    change_pct: Decimal
    range_minor: int
    range_pct_of_min: Decimal
    distinct_price_count: int
    price_change_count: int
    observation_span_days: Decimal
    mean_price_duration_days: Decimal
    change_frequency_per_30d: Decimal
    volatility_pct: Decimal
    days_since_last_change: Decimal | None
    direction: str


@dataclass(frozen=True, slots=True)
class PriceWindowSummary:
    window_days: int
    window_start_utc: datetime
    as_of_utc: datetime
    sufficient_history: bool
    reason: str | None
    observation_count: int
    minimum_price_minor: int | None
    maximum_price_minor: int | None
    mean_price_minor: int | None
    median_price_minor: Decimal | None
    current_price_minor: int | None
    current_vs_average_pct: Decimal | None
    current_vs_minimum_pct: Decimal | None


def _pct(numerator: int, denominator: int) -> Decimal:
    if denominator <= 0:
        return Decimal("0.00")
    return (Decimal(numerator) * Decimal(100) / Decimal(denominator)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def _mean_minor(prices: list[int]) -> int:
    return int(
        (Decimal(sum(prices)) / Decimal(len(prices))).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )


def _median_minor(prices: list[int]) -> Decimal:
    ordered = sorted(prices)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return Decimal(ordered[middle]).quantize(Decimal("0.01"))
    return (
        (Decimal(ordered[middle - 1]) + Decimal(ordered[middle])) / Decimal(2)
    ).quantize(Decimal("0.01"))


def _days(delta: timedelta) -> Decimal:
    return (Decimal(str(delta.total_seconds())) / Decimal(86400)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def _volatility_pct(prices: list[int]) -> Decimal:
    mean = Decimal(sum(prices)) / Decimal(len(prices))
    if mean <= 0:
        return Decimal("0.00")
    variance = sum((Decimal(price) - mean) ** 2 for price in prices) / Decimal(len(prices))
    return (variance.sqrt() * Decimal(100) / mean).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def summarize_price_series(observations: Iterable[HistoricalPriceObservation]) -> PriceSeriesSummary:
    rows = sorted(observations, key=lambda item: item.observed_at_utc)
    if not rows:
        raise PriceHistoryAnalyticsError("history_empty")
    identity = (rows[0].canonical_product_id, rows[0].supermarket_id, rows[0].location_id)
    if any((row.canonical_product_id, row.supermarket_id, row.location_id) != identity for row in rows):
        raise PriceHistoryAnalyticsError("history_mixed_identity")
    timestamps = [row.observed_at_utc for row in rows]
    if len(set(timestamps)) != len(timestamps):
        raise PriceHistoryAnalyticsError("history_duplicate_timestamp")

    prices = [row.price_minor for row in rows]
    first = prices[0]
    last = prices[-1]
    change = last - first
    direction = "same"
    if change < 0:
        direction = "down"
    elif change > 0:
        direction = "up"
    price_change_count = sum(current != previous for previous, current in zip(prices, prices[1:]))
    minimum = min(prices)
    maximum = max(prices)
    span = rows[-1].observed_at_utc - rows[0].observed_at_utc
    span_days = _days(span)
    periods = max(price_change_count + 1, 1)
    mean_duration = (span_days / Decimal(periods)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    frequency = (
        Decimal("0.00")
        if span.total_seconds() <= 0
        else (Decimal(price_change_count) * Decimal(30) / (Decimal(str(span.total_seconds())) / Decimal(86400))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    )
    current_price_started = rows[-1].observed_at_utc
    if len(rows) > 1:
        current_price_started = rows[0].observed_at_utc
        for previous, current in zip(rows, rows[1:]):
            if previous.price_minor != current.price_minor:
                current_price_started = current.observed_at_utc
    days_since_last_change = (
        None if len(rows) == 1 else _days(rows[-1].observed_at_utc - current_price_started)
    )
    return PriceSeriesSummary(
        canonical_product_id=identity[0],
        supermarket_id=identity[1],
        location_id=identity[2],
        observation_count=len(rows),
        first_observed_at_utc=rows[0].observed_at_utc,
        last_observed_at_utc=rows[-1].observed_at_utc,
        first_price_minor=first,
        last_price_minor=last,
        minimum_price_minor=minimum,
        maximum_price_minor=maximum,
        mean_price_minor=_mean_minor(prices),
        median_price_minor=_median_minor(prices),
        absolute_change_minor=change,
        change_pct=_pct(change, first),
        range_minor=maximum - minimum,
        range_pct_of_min=_pct(maximum - minimum, minimum),
        distinct_price_count=len(set(prices)),
        price_change_count=price_change_count,
        observation_span_days=span_days,
        mean_price_duration_days=mean_duration,
        change_frequency_per_30d=frequency,
        volatility_pct=_volatility_pct(prices),
        days_since_last_change=days_since_last_change,
        direction=direction,
    )


def summarize_price_windows(
    observations: Iterable[HistoricalPriceObservation],
    *,
    as_of_utc: datetime,
    windows: tuple[int, ...] = (7, 30, 60, 90, 365),
) -> tuple[PriceWindowSummary, ...]:
    """Summarize configured windows only when an observed baseline covers them.

    The last observation at or before the window start is the opening state. No
    value is interpolated before the first real observation and no requested
    window is silently shortened.
    """

    if as_of_utc.tzinfo is None or as_of_utc.utcoffset() is None:
        raise PriceHistoryAnalyticsError("history_as_of_not_timezone_aware")
    as_of = as_of_utc.astimezone(UTC)
    if not windows or any(type(value) is not int or value <= 0 for value in windows):
        raise PriceHistoryAnalyticsError("history_windows_invalid")
    if len(set(windows)) != len(windows):
        raise PriceHistoryAnalyticsError("history_windows_duplicate")

    rows = sorted(observations, key=lambda item: item.observed_at_utc)
    if not rows:
        raise PriceHistoryAnalyticsError("history_empty")
    summarize_price_series(rows)  # validates identity and duplicate timestamps
    if rows[-1].observed_at_utc > as_of:
        raise PriceHistoryAnalyticsError("history_observation_after_as_of")

    summaries: list[PriceWindowSummary] = []
    for window_days in windows:
        start = as_of - timedelta(days=window_days)
        baseline = [row for row in rows if row.observed_at_utc <= start]
        if not baseline:
            summaries.append(
                PriceWindowSummary(
                    window_days=window_days,
                    window_start_utc=start,
                    as_of_utc=as_of,
                    sufficient_history=False,
                    reason="insufficient_history",
                    observation_count=sum(start < row.observed_at_utc <= as_of for row in rows),
                    minimum_price_minor=None,
                    maximum_price_minor=None,
                    mean_price_minor=None,
                    median_price_minor=None,
                    current_price_minor=rows[-1].price_minor,
                    current_vs_average_pct=None,
                    current_vs_minimum_pct=None,
                )
            )
            continue

        window_rows = [baseline[-1], *(row for row in rows if start < row.observed_at_utc <= as_of)]
        prices = [row.price_minor for row in window_rows]
        current = prices[-1]
        mean = _mean_minor(prices)
        minimum = min(prices)
        summaries.append(
            PriceWindowSummary(
                window_days=window_days,
                window_start_utc=start,
                as_of_utc=as_of,
                sufficient_history=True,
                reason=None,
                observation_count=len(window_rows),
                minimum_price_minor=minimum,
                maximum_price_minor=max(prices),
                mean_price_minor=mean,
                median_price_minor=_median_minor(prices),
                current_price_minor=current,
                current_vs_average_pct=_pct(current - mean, mean),
                current_vs_minimum_pct=_pct(current - minimum, minimum),
            )
        )
    return tuple(summaries)
