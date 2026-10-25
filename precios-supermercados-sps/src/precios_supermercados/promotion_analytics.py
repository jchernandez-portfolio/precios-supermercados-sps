"""Promotion intelligence that keeps source claims separate from price history."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from .price_history_analytics import HistoricalPriceObservation, summarize_price_windows


class PromotionAnalyticsError(ValueError):
    pass


class HistoricalPricePosition(StrEnum):
    BELOW_RECENT_AVERAGE = "below_recent_average"
    NEAR_RECENT_MINIMUM = "near_recent_minimum"
    NORMAL_RANGE = "normal_range"
    ABOVE_RECENT_AVERAGE = "above_recent_average"
    SOURCE_PROMOTION_WITHOUT_HISTORICAL_REDUCTION = (
        "source_promotion_without_historical_reduction"
    )
    INSUFFICIENT_HISTORY = "insufficient_history"


@dataclass(frozen=True, slots=True)
class PromotionPriceObservation:
    canonical_product_id: str
    supermarket_id: str
    location_id: str
    observed_at_utc: datetime
    current_price_minor: int
    source_reports_promotion: bool
    reported_regular_price_minor: int | None = None

    def __post_init__(self) -> None:
        for name in ("canonical_product_id", "supermarket_id", "location_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise PromotionAnalyticsError(f"{name}_missing")
        if self.observed_at_utc.tzinfo is None or self.observed_at_utc.utcoffset() is None:
            raise PromotionAnalyticsError("promotion_timestamp_not_timezone_aware")
        object.__setattr__(self, "observed_at_utc", self.observed_at_utc.astimezone(UTC))
        if type(self.current_price_minor) is not int or self.current_price_minor <= 0:
            raise PromotionAnalyticsError("promotion_current_price_invalid")
        if not isinstance(self.source_reports_promotion, bool):
            raise PromotionAnalyticsError("source_reports_promotion_invalid")
        if self.reported_regular_price_minor is not None and (
            type(self.reported_regular_price_minor) is not int
            or self.reported_regular_price_minor <= 0
        ):
            raise PromotionAnalyticsError("reported_regular_price_invalid")


@dataclass(frozen=True, slots=True)
class PromotionAnalysis:
    canonical_product_id: str
    supermarket_id: str
    location_id: str
    as_of_utc: datetime
    current_price_minor: int
    previous_price_minor: int | None
    reported_regular_price_minor: int | None
    source_reports_promotion: bool
    historical_price_reduction: bool | None
    source_discount_depth_pct: Decimal | None
    current_vs_previous_pct: Decimal | None
    current_vs_average_30d_pct: Decimal | None
    current_vs_average_90d_pct: Decimal | None
    current_vs_minimum_90d_pct: Decimal | None
    promotion_duration_days: Decimal | None
    promotion_event_count: int
    promotion_share_pct: Decimal
    historical_position: HistoricalPricePosition


def _pct(numerator: int, denominator: int) -> Decimal:
    if denominator <= 0:
        return Decimal("0.00")
    return (Decimal(numerator) * Decimal(100) / Decimal(denominator)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def _promotion_events(rows: tuple[PromotionPriceObservation, ...]) -> int:
    return sum(
        row.source_reports_promotion
        and (index == 0 or not rows[index - 1].source_reports_promotion)
        for index, row in enumerate(rows)
    )


def _promotion_duration(rows: tuple[PromotionPriceObservation, ...]) -> Decimal | None:
    if not rows[-1].source_reports_promotion:
        return None
    start = rows[-1].observed_at_utc
    for row in reversed(rows[:-1]):
        if not row.source_reports_promotion:
            break
        start = row.observed_at_utc
    seconds = Decimal(str((rows[-1].observed_at_utc - start).total_seconds()))
    return (seconds / Decimal(86400)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def analyze_promotions(
    observations: Iterable[PromotionPriceObservation],
    *,
    as_of_utc: datetime,
    near_minimum_threshold_pct: Decimal = Decimal("2.00"),
) -> PromotionAnalysis:
    """Explain a source promotion and its independently observed price context."""

    if as_of_utc.tzinfo is None or as_of_utc.utcoffset() is None:
        raise PromotionAnalyticsError("promotion_as_of_not_timezone_aware")
    as_of = as_of_utc.astimezone(UTC)
    if (
        not isinstance(near_minimum_threshold_pct, Decimal)
        or not near_minimum_threshold_pct.is_finite()
        or near_minimum_threshold_pct < 0
    ):
        raise PromotionAnalyticsError("near_minimum_threshold_invalid")
    rows = tuple(sorted(observations, key=lambda row: row.observed_at_utc))
    if not rows:
        raise PromotionAnalyticsError("promotion_history_empty")
    identity = (rows[0].canonical_product_id, rows[0].supermarket_id, rows[0].location_id)
    if any(
        (row.canonical_product_id, row.supermarket_id, row.location_id) != identity
        for row in rows
    ):
        raise PromotionAnalyticsError("promotion_history_mixed_identity")
    if len({row.observed_at_utc for row in rows}) != len(rows):
        raise PromotionAnalyticsError("promotion_history_duplicate_timestamp")
    if rows[-1].observed_at_utc > as_of:
        raise PromotionAnalyticsError("promotion_observation_after_as_of")

    history_rows = tuple(
        HistoricalPriceObservation(
            row.canonical_product_id,
            row.supermarket_id,
            row.location_id,
            row.observed_at_utc,
            row.current_price_minor,
        )
        for row in rows
    )
    windows = {
        summary.window_days: summary
        for summary in summarize_price_windows(
            history_rows,
            as_of_utc=as_of,
            windows=(30, 90),
        )
    }
    current = rows[-1]
    previous = rows[-2] if len(rows) > 1 else None
    historical_reduction = (
        None if previous is None else current.current_price_minor < previous.current_price_minor
    )
    regular_discount = (
        None
        if current.reported_regular_price_minor is None
        else _pct(
            max(current.reported_regular_price_minor - current.current_price_minor, 0),
            current.reported_regular_price_minor,
        )
    )
    window_30 = windows[30]
    window_90 = windows[90]

    if current.source_reports_promotion and historical_reduction is not True:
        position = HistoricalPricePosition.SOURCE_PROMOTION_WITHOUT_HISTORICAL_REDUCTION
    elif not window_90.sufficient_history:
        position = HistoricalPricePosition.INSUFFICIENT_HISTORY
    elif (
        window_90.current_vs_minimum_pct is not None
        and window_90.current_vs_minimum_pct <= near_minimum_threshold_pct
    ):
        position = HistoricalPricePosition.NEAR_RECENT_MINIMUM
    elif window_90.current_vs_average_pct is not None and window_90.current_vs_average_pct < 0:
        position = HistoricalPricePosition.BELOW_RECENT_AVERAGE
    elif window_90.current_vs_average_pct is not None and window_90.current_vs_average_pct > 0:
        position = HistoricalPricePosition.ABOVE_RECENT_AVERAGE
    else:
        position = HistoricalPricePosition.NORMAL_RANGE

    return PromotionAnalysis(
        canonical_product_id=identity[0],
        supermarket_id=identity[1],
        location_id=identity[2],
        as_of_utc=as_of,
        current_price_minor=current.current_price_minor,
        previous_price_minor=None if previous is None else previous.current_price_minor,
        reported_regular_price_minor=current.reported_regular_price_minor,
        source_reports_promotion=current.source_reports_promotion,
        historical_price_reduction=historical_reduction,
        source_discount_depth_pct=regular_discount,
        current_vs_previous_pct=(
            None
            if previous is None
            else _pct(current.current_price_minor - previous.current_price_minor, previous.current_price_minor)
        ),
        current_vs_average_30d_pct=window_30.current_vs_average_pct,
        current_vs_average_90d_pct=window_90.current_vs_average_pct,
        current_vs_minimum_90d_pct=window_90.current_vs_minimum_pct,
        promotion_duration_days=_promotion_duration(rows),
        promotion_event_count=_promotion_events(rows),
        promotion_share_pct=_pct(sum(row.source_reports_promotion for row in rows), len(rows)),
        historical_position=position,
    )
