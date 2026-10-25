from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from precios_supermercados.analytics_quality import (
    QualityRun,
    QualityStatus,
    assess_comparable_market_window,
    assess_freshness,
)
from precios_supermercados.competitive_analytics import analyze_competition
from precios_supermercados.price_analytics import ComparisonScope, CurrentPriceObservation, analyze_current_prices
from precios_supermercados.product_homologation import SourceProductRecord, homologate_products
from precios_supermercados.rpi_data_marts import (
    MartCommercialState,
    MartHistoricalState,
    MartOfferDescriptor,
    RpiDataMartError,
    build_rpi_data_marts,
)

AS_OF = datetime(2026, 9, 8, 12, tzinfo=UTC)
A_CURRENT_AT = datetime(2026, 9, 1, 12, tzinfo=UTC)
B_CURRENT_AT = datetime(2026, 9, 2, 12, tzinfo=UTC)


def inputs(*, stale: bool = False):
    scope = ComparisonScope((("a", "a_sps"), ("b", "b_sps")))
    records = tuple(
        SourceProductRecord(
            source_record_id=f"{retailer}:{index}",
            supermarket_id=retailer,
            source_name="Leche Sula entera 1 L",
            source_brand="Sula",
            source_presentation="1 L",
            source_category="Lácteos",
            barcode="7590002040003",
        )
        for index, retailer in enumerate(("a", "b"), 1)
    )
    prices = {"a:1": 2000, "b:2": 2200}
    analytics = analyze_current_prices(
        homologate_products(records),
        tuple(
            CurrentPriceObservation(record.source_record_id, record.supermarket_id, f"{record.supermarket_id}_sps", prices[record.source_record_id])
            for record in records
        ),
        scope,
    )
    freshness = tuple(
        assess_freshness(
            QualityRun(
                f"{retailer}-run", retailer, f"{retailer}_sps",
                AS_OF - timedelta(hours=30 if stale and retailer == "b" else 2),
                QualityStatus.ACCEPTED,
            ),
            source_id=retailer,
            location_id=f"{retailer}_sps",
            as_of_utc=AS_OF,
            freshness_window=timedelta(hours=24),
        )
        for retailer in ("a", "b")
    )
    competition = analyze_competition(analytics, assess_comparable_market_window(freshness))
    descriptors = tuple(
        MartOfferDescriptor(
            record.source_record_id,
            record.source_name,
            category="Lácteos",
            product_type="Leche",
            brand="Sula",
            variant="Entera",
            presentation="1 L",
        )
        for record in records
    )
    states = (
        MartCommercialState("a:1", 2000, 2500, True, "in_stock", A_CURRENT_AT),
        MartCommercialState("b:2", 2200, None, False, "in_stock", B_CURRENT_AT),
    )
    history = (
        MartHistoricalState("a:1", 2400, None, False, datetime(2026, 5, 1, 12, tzinfo=UTC)),
        MartHistoricalState("a:1", 2200, None, False, datetime(2026, 8, 20, 12, tzinfo=UTC)),
        MartHistoricalState("a:1", 2000, 2500, True, A_CURRENT_AT),
        MartHistoricalState("b:2", 2100, None, False, datetime(2026, 5, 1, 12, tzinfo=UTC)),
        MartHistoricalState("b:2", 2150, None, False, datetime(2026, 8, 20, 12, tzinfo=UTC)),
        MartHistoricalState("b:2", 2200, None, False, B_CURRENT_AT),
    )
    return analytics, competition, descriptors, states, freshness, history


def test_business_and_consumer_marts_share_safe_inputs_and_metric_truth() -> None:
    marts = build_rpi_data_marts(*inputs())

    assert marts.business["schema"] == "rpi-business-mart/v1"
    assert marts.consumer["schema"] == "rpi-consumer-mart/v2"
    assert marts.business["comparison_status"] == "COMPARABLE"
    assert marts.consumer["comparison_status"] == "COMPARABLE"
    assert marts.consumer["product_count"] == 1
    assert len(marts.business["facts"]["fact_current_comparison"]) == 2
    assert len(marts.business["facts"]["fact_price_history"]) == 6
    assert len(marts.business["facts"]["fact_promotion_analysis"]) == 2
    assert marts.business["coverage"] == marts.consumer["coverage"]
    assert marts.business["source_freshness"] == marts.consumer["source_freshness"]
    assert {row["pci"] for row in marts.business["facts"]["fact_current_comparison"]} == {"95.24", "104.76"}

    offers = {offer["source_product_id"]: offer for offer in marts.consumer["products"][0]["offers"]}
    assert offers["a:1"]["difference_vs_best_abs"] == "0.00"
    assert offers["a:1"]["difference_vs_best_pct"] == "0.00"
    assert offers["b:2"]["difference_vs_best_abs"] == "2.00"
    assert offers["b:2"]["difference_vs_best_pct"] == "10.00"


def test_business_price_history_fact_keeps_real_periods_and_change_truth() -> None:
    business = build_rpi_data_marts(*inputs()).business
    rows = [
        row for row in business["facts"]["fact_price_history"]
        if row["source_product_id"] == "a:1"
    ]

    assert [row["period_start"] for row in rows] == [
        "2026-05-01T12:00:00Z",
        "2026-08-20T12:00:00Z",
        "2026-09-01T12:00:00Z",
    ]
    assert rows[0]["previous_price"] is None
    assert rows[0]["change_abs"] is None
    assert rows[0]["change_pct"] is None
    assert rows[0]["direction"] == "initial"
    assert rows[1]["current_price"] == "22.00"
    assert rows[1]["previous_price"] == "24.00"
    assert rows[1]["change_abs"] == "-2.00"
    assert rows[1]["change_pct"] == "-8.33"
    assert rows[1]["direction"] == "down"
    assert rows[2]["current_price"] == "20.00"
    assert rows[2]["reported_regular_price"] == "25.00"
    assert rows[2]["is_promotion"] is True
    assert rows[2]["is_current"] is True
    assert rows[2]["freshness_status"] == "FRESH"


def test_business_promotion_fact_separates_source_claim_from_history() -> None:
    business = build_rpi_data_marts(*inputs()).business
    rows = {
        row["source_product_id"]: row
        for row in business["facts"]["fact_promotion_analysis"]
    }
    promoted = rows["a:1"]
    regular = rows["b:2"]

    assert promoted["history_observation_count"] == 3
    assert promoted["source_reports_promotion"] is True
    assert promoted["historical_price_reduction"] is True
    assert promoted["source_discount_depth_pct"] == "20.00"
    assert promoted["current_vs_previous_pct"] == "-9.09"
    assert promoted["current_vs_average_30d_pct"] == "-9.09"
    assert promoted["historical_position"] == "near_recent_minimum"
    assert promoted["promotion_event_count"] == 1
    assert regular["source_reports_promotion"] is False
    assert regular["historical_price_reduction"] is False
    assert regular["source_discount_depth_pct"] is None


def test_consumer_mart_preserves_price_history_and_shopping_descriptors_without_secrets() -> None:
    consumer = build_rpi_data_marts(*inputs()).consumer
    offer = consumer["products"][0]["offers"][0]

    assert offer["current_price"] == "20.00"
    assert offer["reported_regular_price"] == "25.00"
    assert offer["is_promotion"] is True
    assert offer["category"] == "Lácteos"
    assert offer["product_type"] == "Leche"
    assert offer["brand"] == "Sula"
    assert offer["variant"] == "Entera"
    assert offer["presentation"] == "1 L"
    assert offer["rank"] == 1
    assert offer["is_best_price"] is True
    assert offer["difference_vs_best_abs"] == "0.00"
    assert offer["difference_vs_best_pct"] == "0.00"
    history = offer["historical_summary"]
    assert history["observation_count"] == 3
    assert history["previous_price"] == "22.00"
    assert history["current_vs_previous_pct"] == "-9.09"
    assert history["historical_position"] == "near_recent_minimum"
    assert history["source_discount_depth_pct"] == "20.00"
    assert history["windows"]["30d"] == {
        "status": "available",
        "observation_count": 3,
        "average": "22.00",
        "median": "22.00",
        "minimum": "20.00",
        "maximum": "24.00",
        "current_vs_average_pct": "-9.09",
        "current_vs_minimum_pct": "0.00",
    }
    assert history["windows"]["90d"]["status"] == "available"
    assert consumer["products"][0]["recommended_source_product_ids"] == ["a:1"]
    serialized = json.dumps(consumer).casefold()
    for forbidden in ("turso", "database_url", "auth_token", "libsql://"):
        assert forbidden not in serialized


def test_consumer_history_keeps_explicit_insufficient_history_windows() -> None:
    analytics, competition, descriptors, states, freshness, history = inputs()
    current_only = tuple(row for row in history if row.observed_at_utc in {A_CURRENT_AT, B_CURRENT_AT})
    marts = build_rpi_data_marts(
        analytics,
        competition,
        descriptors,
        states,
        freshness,
        current_only,
    )
    consumer = marts.consumer

    for offer in consumer["products"][0]["offers"]:
        assert offer["historical_summary"]["windows"]["30d"]["status"] == "insufficient_history"
        assert offer["historical_summary"]["windows"]["30d"]["average"] is None
        assert offer["historical_summary"]["windows"]["90d"]["status"] == "insufficient_history"
        assert offer["historical_summary"]["windows"]["90d"]["minimum"] is None
    assert len(marts.business["facts"]["fact_price_history"]) == 2
    assert all(
        row["history_observation_count"] == 1
        for row in marts.business["facts"]["fact_promotion_analysis"]
    )


def test_stale_market_keeps_lkg_offers_visible_but_removes_rank_and_pci() -> None:
    marts = build_rpi_data_marts(*inputs(stale=True))
    facts = marts.business["facts"]["fact_current_comparison"]

    assert marts.consumer["comparison_status"] == "INSUFFICIENT_FRESH_COMPARISON"
    assert marts.consumer["product_count"] == 1
    assert "source_data_stale" in marts.consumer["blocked_reasons"]
    assert all(row["rank"] is None and row["pci"] is None for row in facts)
    assert all(
        offer["rank"] is None
        and offer["is_best_price"] is False
        and offer["difference_vs_best_abs"] is None
        and offer["difference_vs_best_pct"] is None
        for product in marts.consumer["products"]
        for offer in product["offers"]
    )
    assert marts.consumer["products"][0]["recommended_source_product_ids"] == []
    assert any(row["freshness_status"] == "STALE" for row in facts)
    assert any(
        row["freshness_status"] == "STALE"
        for row in marts.business["facts"]["fact_price_history"]
    )
    assert any(
        row["freshness_status"] == "STALE"
        for row in marts.business["facts"]["fact_promotion_analysis"]
    )


def test_mart_rejects_descriptor_price_or_history_drift_from_safe_universe() -> None:
    analytics, competition, descriptors, states, freshness, history = inputs()
    with pytest.raises(RpiDataMartError, match="mart_offer_inputs_not_exact_safe_universe"):
        build_rpi_data_marts(analytics, competition, descriptors[:-1], states, freshness, history)

    bad_states = (
        MartCommercialState("a:1", 9999, 2500, True, "in_stock", A_CURRENT_AT),
        states[1],
    )
    with pytest.raises(RpiDataMartError, match="mart_current_price_mismatch"):
        build_rpi_data_marts(analytics, competition, descriptors, bad_states, freshness, history)

    bad_history = (*history[:-1], MartHistoricalState("b:2", 2199, None, False, B_CURRENT_AT))
    with pytest.raises(RpiDataMartError, match="mart_history_current_state_mismatch"):
        build_rpi_data_marts(analytics, competition, descriptors, states, freshness, bad_history)

    with pytest.raises(RpiDataMartError, match="mart_history_not_safe_universe"):
        build_rpi_data_marts(
            analytics,
            competition,
            descriptors,
            states,
            freshness,
            (*history, MartHistoricalState("x:9", 1000, None, False, AS_OF)),
        )
