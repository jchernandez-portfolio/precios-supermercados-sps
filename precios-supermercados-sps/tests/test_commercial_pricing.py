"""Regla comercial: ahorro real usa histórico aceptado, no precio reportado."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from precios_supermercados.commercial_pricing import (
    CommercialPricingError,
    evaluate_real_price_reduction,
)
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

T0 = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def _validated(
    run_id: str,
    observed_at: datetime,
    *,
    current_price: str | None = "30",
    reported_regular_price: str | None = "35",
    is_promotion: bool = False,
    availability: AvailabilityStatus = AvailabilityStatus.IN_STOCK,
) -> ValidatedOffer:
    supermarket_id = "la-colonia"
    location_id = "unknown"
    source_key = "SKU-001"
    source_product_id = generate_source_product_id(
        supermarket_id,
        SourceKeyType.SKU,
        source_key,
    )
    offer_id = generate_offer_id(supermarket_id, location_id, source_product_id)
    offer = NormalizedOffer(
        supermarket_id=supermarket_id,
        location_id=location_id,
        source_product_id=source_product_id,
        source_key_type=SourceKeyType.SKU,
        source_key=source_key,
        product_id="prod_001",
        offer_id=offer_id,
        source_name="Producto 001",
        product_url="https://example.invalid/producto-001",
        normalized_name="producto 001",
        currency="HNL",
        is_promotion=is_promotion,
        availability=availability,
        location_status=LocationStatus.UNKNOWN,
        observed_at_utc=observed_at,
        scrape_run_id=run_id,
        extractor_version="test",
        schema_version="1",
        source_url="https://example.invalid/graphql",
        normalized_brand="Marca",
        category="Categoria",
        subcategory="Subcategoria",
        variant="Base",
        unit_count=1,
        content_per_unit=Decimal("1"),
        measurement_unit="unit",
        total_content=Decimal("1"),
        current_price=(Decimal(current_price) if current_price is not None else None),
        reported_regular_price=(
            Decimal(reported_regular_price)
            if reported_regular_price is not None
            else None
        ),
    )
    return ValidatedOffer(
        offer=offer,
        state_hash=generate_state_hash(offer),
        validated_at_utc=observed_at,
    )


def _decision(
    run_id: str,
    when: datetime,
    *,
    status: RunStatus = RunStatus.SUCCESS,
    accepted: bool = True,
) -> CommercialRunDecision:
    return CommercialRunDecision(
        scrape_run_id=run_id,
        run_status=status,
        catalog_accepted=accepted,
        decided_at_utc=when,
    )


def _evaluate(store: InMemoryCommercialState, offer_id: str):
    return evaluate_real_price_reduction(
        store.current(offer_id),
        store.history(offer_id),
    )


def _store_with_price_change() -> tuple[InMemoryCommercialState, str]:
    store = InMemoryCommercialState()
    first = _validated("run-1", T0, current_price="30")
    store.apply_run(_decision("run-1", T0 + timedelta(minutes=1)), [first])
    t1 = T0 + timedelta(days=1)
    second = _validated("run-2", t1, current_price="25")
    store.apply_run(_decision("run-2", t1 + timedelta(minutes=1)), [second])
    return store, first.offer.offer_id


def test_initial_offer_has_no_real_reduction_even_if_source_reports_large_regular_price():
    store = InMemoryCommercialState()
    first = _validated(
        "run-1",
        T0,
        current_price="30",
        reported_regular_price="100",
        is_promotion=True,
    )
    store.apply_run(_decision("run-1", T0 + timedelta(minutes=1)), [first])

    result = _evaluate(store, first.offer.offer_id)

    assert result is not None
    assert result.current_price == Decimal("30")
    assert result.previous_accepted_price is None
    assert result.reduction_amount is None
    assert result.reduction_ratio is None
    assert result.is_real_reduction is False


def test_real_reduction_uses_previous_accepted_current_price_not_reported_regular_price():
    store = InMemoryCommercialState()
    first = _validated("run-1", T0, current_price="30", reported_regular_price="35")
    store.apply_run(_decision("run-1", T0 + timedelta(minutes=1)), [first])

    t1 = T0 + timedelta(days=1)
    second = _validated(
        "run-2",
        t1,
        current_price="25",
        reported_regular_price="100",
        is_promotion=True,
    )
    store.apply_run(_decision("run-2", t1 + timedelta(minutes=1)), [second])

    result = _evaluate(store, first.offer.offer_id)

    assert result is not None
    assert result.previous_accepted_price == Decimal("30")
    assert result.current_price == Decimal("25")
    assert result.reduction_amount == Decimal("5")
    assert result.reduction_ratio == Decimal("5") / Decimal("30")
    assert result.is_real_reduction is True
    assert result.previous_scrape_run_id == "run-1"
    assert result.current_scrape_run_id == "run-2"


def test_source_promotion_without_historical_price_drop_is_not_real_reduction():
    store = InMemoryCommercialState()
    first = _validated("run-1", T0, current_price="25", reported_regular_price="30")
    store.apply_run(_decision("run-1", T0 + timedelta(minutes=1)), [first])

    t1 = T0 + timedelta(days=1)
    second = _validated(
        "run-2",
        t1,
        current_price="25",
        reported_regular_price="100",
        is_promotion=True,
    )
    store.apply_run(_decision("run-2", t1 + timedelta(minutes=1)), [second])

    result = _evaluate(store, first.offer.offer_id)

    assert result is not None
    assert result.previous_accepted_price == Decimal("25")
    assert result.current_price == Decimal("25")
    assert result.reduction_amount == Decimal("0")
    assert result.reduction_ratio == Decimal("0")
    assert result.is_real_reduction is False


def test_price_increase_is_not_real_reduction_even_with_reported_regular_price():
    store = InMemoryCommercialState()
    first = _validated("run-1", T0, current_price="20")
    store.apply_run(_decision("run-1", T0 + timedelta(minutes=1)), [first])

    t1 = T0 + timedelta(days=1)
    second = _validated(
        "run-2",
        t1,
        current_price="25",
        reported_regular_price="50",
        is_promotion=True,
    )
    store.apply_run(_decision("run-2", t1 + timedelta(minutes=1)), [second])

    result = _evaluate(store, first.offer.offer_id)

    assert result is not None
    assert result.previous_accepted_price == Decimal("20")
    assert result.reduction_amount == Decimal("0")
    assert result.reduction_ratio == Decimal("0")
    assert result.is_real_reduction is False


def test_same_hash_confirmation_keeps_previous_period_as_real_reduction_baseline():
    store = InMemoryCommercialState()
    first = _validated("run-1", T0, current_price="30")
    store.apply_run(_decision("run-1", T0 + timedelta(minutes=1)), [first])

    t1 = T0 + timedelta(days=1)
    dropped = _validated("run-2", t1, current_price="25")
    store.apply_run(_decision("run-2", t1 + timedelta(minutes=1)), [dropped])

    t2 = t1 + timedelta(days=1)
    confirmed = _validated("run-3", t2, current_price="25")
    store.apply_run(_decision("run-3", t2 + timedelta(minutes=1)), [confirmed])

    result = _evaluate(store, first.offer.offer_id)

    assert result is not None
    assert result.previous_accepted_price == Decimal("30")
    assert result.current_price == Decimal("25")
    assert result.reduction_amount == Decimal("5")
    assert result.current_scrape_run_id == "run-3"
    assert result.previous_scrape_run_id == "run-1"


def test_non_price_state_change_with_same_price_becomes_new_immediate_baseline():
    store = InMemoryCommercialState()
    first = _validated("run-1", T0, current_price="30")
    store.apply_run(_decision("run-1", T0 + timedelta(minutes=1)), [first])

    t1 = T0 + timedelta(days=1)
    dropped = _validated("run-2", t1, current_price="25", reported_regular_price="35")
    store.apply_run(_decision("run-2", t1 + timedelta(minutes=1)), [dropped])

    t2 = t1 + timedelta(days=1)
    source_reference_changed = _validated(
        "run-3",
        t2,
        current_price="25",
        reported_regular_price="40",
    )
    store.apply_run(_decision("run-3", t2 + timedelta(minutes=1)), [source_reference_changed])

    result = _evaluate(store, first.offer.offer_id)

    assert result is not None
    assert result.previous_accepted_price == Decimal("25")
    assert result.current_price == Decimal("25")
    assert result.reduction_amount == Decimal("0")
    assert result.is_real_reduction is False


def test_rejected_run_cannot_create_a_fake_real_reduction():
    store = InMemoryCommercialState()
    first = _validated("run-1", T0, current_price="30")
    store.apply_run(_decision("run-1", T0 + timedelta(minutes=1)), [first])

    t1 = T0 + timedelta(days=1)
    rejected = _validated(
        "run-2",
        t1,
        current_price="10",
        reported_regular_price="100",
        is_promotion=True,
    )
    store.apply_run(
        _decision(
            "run-2",
            t1 + timedelta(minutes=1),
            status=RunStatus.REJECTED,
            accepted=False,
        ),
        [rejected],
    )

    result = _evaluate(store, first.offer.offer_id)

    assert result is not None
    assert result.current_price == Decimal("30")
    assert result.previous_accepted_price is None
    assert result.is_real_reduction is False


def test_out_of_stock_current_state_does_not_invent_reduction():
    store = InMemoryCommercialState()
    first = _validated("run-1", T0, current_price="30")
    store.apply_run(_decision("run-1", T0 + timedelta(minutes=1)), [first])

    t1 = T0 + timedelta(days=1)
    unavailable = _validated(
        "run-2",
        t1,
        current_price=None,
        reported_regular_price=None,
        availability=AvailabilityStatus.OUT_OF_STOCK,
    )
    store.apply_run(_decision("run-2", t1 + timedelta(minutes=1)), [unavailable])

    result = _evaluate(store, first.offer.offer_id)

    assert result is not None
    assert result.current_price is None
    assert result.previous_accepted_price == Decimal("30")
    assert result.reduction_amount is None
    assert result.reduction_ratio is None
    assert result.is_real_reduction is False


def test_unknown_offer_has_no_price_comparison():
    assert evaluate_real_price_reduction(None, ()) is None


def test_history_without_current_fails_closed():
    store, offer_id = _store_with_price_change()

    with pytest.raises(CommercialPricingError, match="histórico sin current"):
        evaluate_real_price_reduction(None, store.history(offer_id))


def test_current_without_history_fails_closed():
    store, offer_id = _store_with_price_change()

    with pytest.raises(CommercialPricingError, match="periodo histórico"):
        evaluate_real_price_reduction(store.current(offer_id), ())


def test_mismatched_current_and_history_offer_ids_fail_closed():
    store, offer_id = _store_with_price_change()
    current = store.current(offer_id)
    history = list(store.history(offer_id))
    history[-1] = replace(history[-1], offer_id="of_forged")

    with pytest.raises(CommercialPricingError, match="offer_id distintos"):
        evaluate_real_price_reduction(current, history)


def test_closed_last_history_period_fails_closed():
    store, offer_id = _store_with_price_change()
    current = store.current(offer_id)
    history = list(store.history(offer_id))
    history[-1] = replace(
        history[-1],
        valid_to_utc=history[-1].valid_from_utc + timedelta(minutes=1),
    )

    with pytest.raises(CommercialPricingError, match="no está abierto"):
        evaluate_real_price_reduction(current, history)


def test_current_and_open_period_state_hash_mismatch_fails_closed():
    store, offer_id = _store_with_price_change()
    current = store.current(offer_id)
    history = list(store.history(offer_id))
    history[-1] = replace(history[-1], state_hash="f" * 64)

    with pytest.raises(CommercialPricingError, match="state_hash distinto"):
        evaluate_real_price_reduction(current, history)


def test_current_and_open_period_last_observation_mismatch_fails_closed():
    store, offer_id = _store_with_price_change()
    current = store.current(offer_id)
    assert current is not None
    altered_current = replace(
        current,
        last_observed_at_utc=current.last_observed_at_utc + timedelta(seconds=1),
    )

    with pytest.raises(CommercialPricingError, match="última observación"):
        evaluate_real_price_reduction(altered_current, store.history(offer_id))


def test_noncontiguous_previous_period_fails_closed():
    store, offer_id = _store_with_price_change()
    current = store.current(offer_id)
    history = list(store.history(offer_id))
    history[0] = replace(
        history[0],
        valid_to_utc=history[1].valid_from_utc - timedelta(seconds=1),
    )

    with pytest.raises(CommercialPricingError, match="no son contiguos"):
        evaluate_real_price_reduction(current, history)
