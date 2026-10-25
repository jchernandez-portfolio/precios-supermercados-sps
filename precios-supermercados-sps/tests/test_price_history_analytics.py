from __future__ import annotations

from datetime import UTC, datetime, timezone
from decimal import Decimal

import pytest

from precios_supermercados.price_history_analytics import (
    HistoricalPriceObservation,
    PriceHistoryAnalyticsError,
    summarize_price_series,
    summarize_price_windows,
)


def observation(day: int, price: int, *, location: str = "walmart_sps") -> HistoricalPriceObservation:
    return HistoricalPriceObservation(
        canonical_product_id="prod_gtin_07590002040003",
        supermarket_id="walmart",
        location_id=location,
        observed_at_utc=datetime(2026, 9, day, 12, tzinfo=UTC),
        price_minor=price,
    )


def test_summarize_price_series_reports_variability_and_trend_without_interpolation() -> None:
    summary = summarize_price_series(
        (
            observation(3, 12000),
            observation(1, 10000),
            observation(2, 10000),
            observation(4, 11000),
        )
    )
    assert summary.observation_count == 4
    assert summary.first_price_minor == 10000
    assert summary.last_price_minor == 11000
    assert summary.minimum_price_minor == 10000
    assert summary.maximum_price_minor == 12000
    assert summary.mean_price_minor == 10750
    assert summary.median_price_minor == Decimal("10500.00")
    assert summary.absolute_change_minor == 1000
    assert summary.change_pct == Decimal("10.00")
    assert summary.range_minor == 2000
    assert summary.range_pct_of_min == Decimal("20.00")
    assert summary.distinct_price_count == 3
    assert summary.price_change_count == 2
    assert summary.observation_span_days == Decimal("3.00")
    assert summary.mean_price_duration_days == Decimal("1.00")
    assert summary.change_frequency_per_30d == Decimal("20.00")
    assert summary.volatility_pct == Decimal("7.71")
    assert summary.days_since_last_change == Decimal("0.00")
    assert summary.direction == "up"


def test_history_rejects_mixed_locations_duplicate_timestamps_and_invalid_prices() -> None:
    with pytest.raises(PriceHistoryAnalyticsError, match="history_mixed_identity"):
        summarize_price_series((observation(1, 10000), observation(2, 11000, location="walmart_tgu")))
    with pytest.raises(PriceHistoryAnalyticsError, match="history_duplicate_timestamp"):
        summarize_price_series((observation(1, 10000), observation(1, 11000)))
    with pytest.raises(PriceHistoryAnalyticsError, match="history_price_invalid"):
        observation(1, 0)


def test_history_requires_timezone_aware_timestamp() -> None:
    with pytest.raises(PriceHistoryAnalyticsError, match="history_timestamp_not_timezone_aware"):
        HistoricalPriceObservation(
            canonical_product_id="prod_gtin_07590002040003",
            supermarket_id="walmart",
            location_id="walmart_sps",
            observed_at_utc=datetime(2026, 9, 1, 12),
            price_minor=10000,
        )


def test_windows_require_a_real_baseline_and_never_shorten_silently() -> None:
    as_of = datetime(2026, 9, 10, 12, tzinfo=UTC)
    rows = (
        HistoricalPriceObservation(
            "prod_gtin_07590002040003", "walmart", "walmart_sps",
            datetime(2026, 8, 1, 12, tzinfo=UTC), 10000,
        ),
        HistoricalPriceObservation(
            "prod_gtin_07590002040003", "walmart", "walmart_sps",
            datetime(2026, 9, 5, 12, tzinfo=UTC), 9000,
        ),
        HistoricalPriceObservation(
            "prod_gtin_07590002040003", "walmart", "walmart_sps",
            datetime(2026, 9, 9, 12, tzinfo=UTC), 9500,
        ),
    )

    windows = summarize_price_windows(rows, as_of_utc=as_of, windows=(7, 30, 90))
    seven, thirty, ninety = windows
    assert seven.sufficient_history is True
    assert seven.observation_count == 3
    assert seven.minimum_price_minor == 9000
    assert seven.maximum_price_minor == 10000
    assert seven.mean_price_minor == 9500
    assert seven.median_price_minor == Decimal("9500.00")
    assert seven.current_vs_average_pct == Decimal("0.00")
    assert seven.current_vs_minimum_pct == Decimal("5.56")
    assert thirty.sufficient_history is True
    assert ninety.sufficient_history is False
    assert ninety.reason == "insufficient_history"
    assert ninety.minimum_price_minor is None


def test_windows_reject_invalid_configuration_and_future_observations() -> None:
    as_of = datetime(2026, 9, 2, 12, tzinfo=UTC)
    with pytest.raises(PriceHistoryAnalyticsError, match="history_windows_duplicate"):
        summarize_price_windows((observation(1, 10000),), as_of_utc=as_of, windows=(7, 7))
    with pytest.raises(PriceHistoryAnalyticsError, match="history_observation_after_as_of"):
        summarize_price_windows((observation(3, 10000),), as_of_utc=as_of)
