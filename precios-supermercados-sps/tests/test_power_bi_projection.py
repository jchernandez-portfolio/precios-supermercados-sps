from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from precios_supermercados.commercial_state import (
    CommercialRunDecision,
    InMemoryCommercialState,
)
from precios_supermercados.enums import (
    AvailabilityStatus,
    LocationStatus,
    RunStatus,
    SourceKeyType,
)
from precios_supermercados.identifiers import (
    generate_offer_id,
    generate_source_product_id,
    generate_state_hash,
)
from precios_supermercados.models import NormalizedOffer, ValidatedOffer
from precios_supermercados.power_bi_projection import (
    POWER_BI_OFFER_COLUMNS,
    PowerBIProjectionError,
    PriceDirection,
    build_power_bi_offer_records,
)
from precios_supermercados.tabular_rehydration import RehydratedCommercialSnapshot

BASE = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


def validated(
    *,
    run_id: str,
    observed_at: datetime,
    price: str | None,
    regular: str | None = "45",
) -> ValidatedOffer:
    source_key = "SKU-001"
    source_product_id = generate_source_product_id(
        "demo",
        SourceKeyType.SKU,
        source_key,
    )
    offer_id = generate_offer_id("demo", "demo_sps", source_product_id)
    offer = NormalizedOffer(
        supermarket_id="demo",
        location_id="demo_sps",
        source_product_id=source_product_id,
        source_key_type=SourceKeyType.SKU,
        source_key=source_key,
        product_id="prod-001",
        offer_id=offer_id,
        source_name="Producto fuente 2 x 500 ml",
        product_url="https://example.invalid/p/001",
        normalized_name="Producto fuente",
        currency="HNL",
        current_price=Decimal(price) if price is not None else None,
        reported_regular_price=Decimal(regular) if regular is not None else None,
        is_promotion=True,
        availability=(
            AvailabilityStatus.IN_STOCK
            if price is not None
            else AvailabilityStatus.UNKNOWN
        ),
        location_status=LocationStatus.CONFIRMED,
        location_evidence="fixture",
        location_confidence=Decimal("1"),
        observed_at_utc=observed_at,
        scrape_run_id=run_id,
        extractor_version="test",
        schema_version="1",
        source_url="https://example.invalid/catalog",
        source_sku="REF-001",
        source_brand="MARCA FUENTE",
        normalized_brand="Marca Fuente",
        source_presentation="2 x 500ml",
        source_category="Pasillo",
        barcode="742000000001",
        category="Abarrotes",
        subcategory="Demo",
        variant="Original",
        unit_count=2,
        content_per_unit=Decimal("500"),
        measurement_unit="ml",
        total_content=Decimal("1000"),
        unit_price=Decimal(price) if price is not None else None,
        unit_price_basis="L",
        image_url="https://example.invalid/i/001.jpg",
    )
    return ValidatedOffer(
        offer=offer,
        state_hash=generate_state_hash(offer),
        validated_at_utc=observed_at + timedelta(seconds=5),
    )


def decision(item: ValidatedOffer) -> CommercialRunDecision:
    return CommercialRunDecision(
        scrape_run_id=item.offer.scrape_run_id,
        run_status=RunStatus.SUCCESS,
        catalog_accepted=True,
        decided_at_utc=item.validated_at_utc + timedelta(seconds=5),
    )


def snapshot_for(*items: ValidatedOffer) -> RehydratedCommercialSnapshot:
    state = InMemoryCommercialState()
    for item in items:
        state.apply_run(decision(item), (item,))
    offer_id = items[-1].offer.offer_id
    current = state.current(offer_id)
    assert current is not None
    return RehydratedCommercialSnapshot(
        current={offer_id: current},
        history={offer_id: state.history(offer_id)},
    )


def test_projection_uses_previous_accepted_price_not_reported_regular_price() -> None:
    first = validated(
        run_id="run-1",
        observed_at=BASE,
        price="40",
        regular="100",
    )
    second = validated(
        run_id="run-2",
        observed_at=BASE + timedelta(days=1),
        price="38",
        regular="500",
    )

    record = build_power_bi_offer_records(snapshot_for(first, second))[0]

    assert record.current_price == Decimal("38")
    assert record.previous_accepted_price == Decimal("40")
    assert record.real_reduction_amount == Decimal("2")
    assert record.real_reduction_ratio == Decimal("0.05")
    assert record.is_real_reduction is True
    assert record.price_direction is PriceDirection.DOWN
    assert record.reported_regular_price == Decimal("500")
    assert record.source_reports_promotion is True


def test_projection_marks_price_increase_without_negative_savings() -> None:
    first = validated(run_id="run-1", observed_at=BASE, price="40")
    second = validated(
        run_id="run-2",
        observed_at=BASE + timedelta(days=1),
        price="42",
    )

    record = build_power_bi_offer_records(snapshot_for(first, second))[0]

    assert record.previous_accepted_price == Decimal("40")
    assert record.real_reduction_amount == Decimal("0")
    assert record.real_reduction_ratio == Decimal("0")
    assert record.is_real_reduction is False
    assert record.price_direction is PriceDirection.UP


def test_projection_does_not_invent_baseline_for_first_observation() -> None:
    first = validated(run_id="run-1", observed_at=BASE, price="40")

    record = build_power_bi_offer_records(snapshot_for(first))[0]

    assert record.previous_accepted_price is None
    assert record.real_reduction_amount is None
    assert record.real_reduction_ratio is None
    assert record.is_real_reduction is False
    assert record.price_direction is PriceDirection.NO_BASELINE
    assert record.previous_observed_at_utc is None
    assert record.previous_scrape_run_id is None


def test_projection_marks_missing_current_price_as_unknown() -> None:
    first = validated(run_id="run-1", observed_at=BASE, price=None)

    record = build_power_bi_offer_records(snapshot_for(first))[0]

    assert record.current_price is None
    assert record.price_direction is PriceDirection.UNKNOWN
    assert record.is_real_reduction is False


def test_projection_row_contract_is_exact_and_preserves_numeric_types() -> None:
    first = validated(run_id="run-1", observed_at=BASE, price="40")
    second = validated(
        run_id="run-2",
        observed_at=BASE + timedelta(days=1),
        price="38",
    )

    row = build_power_bi_offer_records(snapshot_for(first, second))[0].as_row()

    assert tuple(row) == POWER_BI_OFFER_COLUMNS
    assert isinstance(row["current_price"], Decimal)
    assert isinstance(row["current_observed_at_utc"], datetime)
    assert row["price_direction"] == "down"
    assert row["availability"] == "in_stock"


def test_projection_fails_closed_on_broken_current_history_chain() -> None:
    first = validated(run_id="run-1", observed_at=BASE, price="40")
    valid = snapshot_for(first)
    offer_id = first.offer.offer_id
    broken = RehydratedCommercialSnapshot(
        current=valid.current,
        history={offer_id: ()},
    )

    with pytest.raises(
        PowerBIProjectionError,
        match="power_bi_pricing_reconciliation_failed",
    ):
        build_power_bi_offer_records(broken)


def test_projection_requires_rehydrated_snapshot() -> None:
    with pytest.raises(
        PowerBIProjectionError,
        match="rehydrated_commercial_snapshot_required",
    ):
        build_power_bi_offer_records(object())  # type: ignore[arg-type]
