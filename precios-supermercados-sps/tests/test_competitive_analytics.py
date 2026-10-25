from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

from precios_supermercados.analytics_quality import (
    MarketWindowStatus,
    QualityRun,
    QualityStatus,
    assess_comparable_market_window,
    assess_freshness,
)
from precios_supermercados.competitive_analytics import PciReference, analyze_competition
from precios_supermercados.price_analytics import (
    ComparisonScope,
    CurrentPriceObservation,
    analyze_current_prices,
)
from precios_supermercados.product_homologation import SourceProductRecord, homologate_products

AS_OF = datetime(2026, 9, 8, 12, tzinfo=UTC)
SCOPE = ComparisonScope((("a", "a_sps"), ("b", "b_sps")))


def product(source_id: str, supermarket_id: str, gtin: str = "7590002040003") -> SourceProductRecord:
    return SourceProductRecord(
        source_record_id=source_id,
        supermarket_id=supermarket_id,
        source_name="Leche Sula entera 1 L",
        source_brand="Sula",
        source_presentation="1 L",
        source_category="Lácteos",
        barcode=gtin,
    )


def analytics(*, include_second_price: bool = True):
    observations = [CurrentPriceObservation("a:1", "a", "a_sps", 10000)]
    if include_second_price:
        observations.append(CurrentPriceObservation("b:2", "b", "b_sps", 12000))
    return analyze_current_prices(
        homologate_products((product("a:1", "a"), product("b:2", "b"))),
        observations,
        SCOPE,
    )


def window(hours_a: int = 2, hours_b: int = 3, *, limit: int = 24):
    rows = []
    for source_id, location_id, age in (("a", "a_sps", hours_a), ("b", "b_sps", hours_b)):
        quality_run = QualityRun(
            f"{source_id}-run",
            source_id,
            location_id,
            AS_OF - timedelta(hours=age),
            QualityStatus.ACCEPTED,
        )
        rows.append(
            assess_freshness(
                quality_run,
                source_id=source_id,
                location_id=location_id,
                as_of_utc=AS_OF,
                freshness_window=timedelta(hours=limit),
            )
        )
    return assess_comparable_market_window(rows)


def test_pci_market_metrics_rank_and_coverage_use_decimal_math() -> None:
    result = analyze_competition(analytics(), window(), pci_reference=PciReference.MEDIAN)

    assert result.comparison_status is MarketWindowStatus.COMPARABLE
    assert result.coverage.comparable_count == 1
    assert result.coverage.valid_price_count == 1
    assert result.coverage.excluded_count == 0
    assert result.coverage.coverage_pct == Decimal("100.00")
    metric = result.products[0]
    assert metric.market_min_minor == 10000
    assert metric.market_max_minor == 12000
    assert metric.market_mean_minor == Decimal("11000.00")
    assert metric.market_median_minor == Decimal("11000.00")
    assert metric.spread_abs_minor == 2000
    assert metric.spread_pct == Decimal("20.00")
    assert metric.cheapest_retailer_ids == ("a",)
    assert [offer.rank for offer in metric.offers] == [1, 2]
    assert [offer.pci for offer in metric.offers] == [Decimal("90.91"), Decimal("109.09")]


def test_pci_can_use_market_minimum_reference() -> None:
    metric = analyze_competition(
        analytics(),
        window(),
        pci_reference="market_minimum",
    ).products[0]
    assert [offer.pci for offer in metric.offers] == [Decimal("100.00"), Decimal("120.00")]


def test_stale_market_blocks_rankings_but_keeps_auditable_coverage() -> None:
    result = analyze_competition(analytics(), window(hours_b=30))

    assert result.comparison_status is MarketWindowStatus.INSUFFICIENT_FRESH_COMPARISON
    assert result.products == ()
    assert result.coverage.valid_price_count == 1
    assert result.blocked_reasons == ("source_data_stale", "source_timestamps_incompatible")


def test_missing_price_reduces_coverage_without_imputing_zero() -> None:
    result = analyze_competition(analytics(include_second_price=False), window())

    assert result.products == ()
    assert result.coverage.comparable_count == 1
    assert result.coverage.valid_price_count == 0
    assert result.coverage.excluded_count == 1
    assert result.coverage.coverage_pct == Decimal("0.00")
