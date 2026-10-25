from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from precios_supermercados.promotion_analytics import (
    HistoricalPricePosition,
    PromotionAnalyticsError,
    PromotionPriceObservation,
    analyze_promotions,
)

AS_OF = datetime(2026, 9, 10, 12, tzinfo=UTC)


def row(days_old: int, price: int, promoted: bool, regular: int | None = None):
    return PromotionPriceObservation(
        "prod_gtin_07590002040003",
        "walmart",
        "walmart_sps",
        AS_OF - timedelta(days=days_old),
        price,
        promoted,
        regular,
    )


def test_source_promotion_and_historical_reduction_are_separate_signals() -> None:
    analysis = analyze_promotions(
        (
            row(100, 12000, False),
            row(20, 12000, False),
            row(5, 10000, True, 12000),
            row(0, 10000, True, 12000),
        ),
        as_of_utc=AS_OF,
    )

    assert analysis.source_reports_promotion is True
    # The immediately previous observed effective price is unchanged; a regular
    # price label does not manufacture a historical reduction.
    assert analysis.historical_price_reduction is False
    assert analysis.source_discount_depth_pct == Decimal("16.67")
    assert analysis.current_vs_previous_pct == Decimal("0.00")
    assert analysis.promotion_duration_days == Decimal("5.00")
    assert analysis.promotion_event_count == 1
    assert analysis.promotion_share_pct == Decimal("50.00")
    assert analysis.historical_position is HistoricalPricePosition.SOURCE_PROMOTION_WITHOUT_HISTORICAL_REDUCTION


def test_recent_low_uses_observed_history_not_reported_regular_price() -> None:
    analysis = analyze_promotions(
        (
            row(120, 13000, False),
            row(60, 12000, False),
            row(20, 11000, False),
            row(0, 10000, False, 15000),
        ),
        as_of_utc=AS_OF,
    )

    assert analysis.historical_price_reduction is True
    assert analysis.source_reports_promotion is False
    assert analysis.source_discount_depth_pct == Decimal("33.33")
    assert analysis.historical_position is HistoricalPricePosition.NEAR_RECENT_MINIMUM


def test_insufficient_history_is_explicit() -> None:
    analysis = analyze_promotions(
        (row(10, 10000, False), row(0, 10500, False)),
        as_of_utc=AS_OF,
    )
    assert analysis.current_vs_average_30d_pct is None
    assert analysis.current_vs_average_90d_pct is None
    assert analysis.historical_position is HistoricalPricePosition.INSUFFICIENT_HISTORY


def test_invalid_money_timestamp_and_identity_fail_closed() -> None:
    with pytest.raises(PromotionAnalyticsError, match="promotion_current_price_invalid"):
        row(1, 0, False)
    with pytest.raises(PromotionAnalyticsError, match="reported_regular_price_invalid"):
        row(1, 100, True, 0)
    mixed = PromotionPriceObservation(
        "other", "walmart", "walmart_sps", AS_OF, 10000, False
    )
    with pytest.raises(PromotionAnalyticsError, match="promotion_history_mixed_identity"):
        analyze_promotions((row(1, 10000, False), mixed), as_of_utc=AS_OF)
