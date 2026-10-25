"""Pruebas adversariales de la frontera entre run técnico y estado comercial."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from precios_supermercados.commercial_state import (
    CommercialReplayConflict,
    CommercialRunDecision,
    CommercialStateError,
    InMemoryCommercialState,
    OutOfOrderCommercialObservation,
)
from precios_supermercados.enums import (
    AvailabilityStatus,
    ChangeType,
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

BASE_TIME = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def make_validated(
    *,
    run_id: str,
    observed_at: datetime,
    suffix: str = "001",
    current_price: str = "30.00",
    reported_regular_price: str | None = "35.00",
    is_promotion: bool = False,
    availability: AvailabilityStatus = AvailabilityStatus.IN_STOCK,
    brand: str = "Marca Demo",
    category: str = "Abarrotes",
) -> ValidatedOffer:
    supermarket_id = "la-colonia"
    location_id = "unknown"
    source_key = f"SKU-{suffix}"
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
        product_id=f"prod_{suffix}",
        offer_id=offer_id,
        source_name=f"Producto {suffix}",
        product_url=f"https://example.invalid/producto-{suffix}",
        normalized_name=f"producto {suffix}",
        currency="HNL",
        is_promotion=is_promotion,
        availability=availability,
        location_status=LocationStatus.UNKNOWN,
        observed_at_utc=observed_at,
        scrape_run_id=run_id,
        extractor_version="test",
        schema_version="1",
        source_url="https://example.invalid/graphql",
        normalized_brand=brand,
        category=category,
        subcategory="General",
        variant="Base",
        unit_count=1,
        content_per_unit=Decimal("1"),
        measurement_unit="unit",
        total_content=Decimal("1"),
        current_price=(
            Decimal(current_price) if current_price is not None else None
        ),
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


def decision(
    run_id: str,
    *,
    status: RunStatus = RunStatus.SUCCESS,
    accepted: bool = True,
    decided_at: datetime = BASE_TIME + timedelta(hours=1),
) -> CommercialRunDecision:
    return CommercialRunDecision(
        scrape_run_id=run_id,
        run_status=status,
        catalog_accepted=accepted,
        decided_at_utc=decided_at,
    )


@pytest.mark.parametrize(
    "status",
    [RunStatus.RUNNING, RunStatus.REJECTED, RunStatus.FAILED, RunStatus.ABANDONED],
)
def test_non_commercial_status_never_mutates_current_or_history(status: RunStatus):
    store = InMemoryCommercialState()
    item = make_validated(run_id="run-1", observed_at=BASE_TIME)

    result = store.apply_run(decision("run-1", status=status), [item])

    assert result.commercial_update_allowed is False
    assert result.mutated is False
    assert result.offers_ignored == 1
    assert store.current_count == 0
    assert store.history(item.offer.offer_id) == ()


def test_success_without_catalog_acceptance_never_mutates():
    store = InMemoryCommercialState()
    item = make_validated(run_id="run-1", observed_at=BASE_TIME)

    result = store.apply_run(decision("run-1", accepted=False), [item])

    assert result.commercial_update_allowed is False
    assert store.current(item.offer.offer_id) is None


def test_initial_accepted_offer_creates_current_and_one_open_period():
    store = InMemoryCommercialState()
    item = make_validated(run_id="run-1", observed_at=BASE_TIME)

    result = store.apply_run(decision("run-1"), [item])

    assert result.current_created == 1
    assert result.current_changed == 0
    current = store.current(item.offer.offer_id)
    assert current is not None
    assert current.validated_offer == item
    assert current.first_observed_at_utc == BASE_TIME
    history = store.history(item.offer.offer_id)
    assert len(history) == 1
    assert history[0].change_type is ChangeType.INITIAL
    assert history[0].valid_to_utc is None


def test_warning_can_update_only_when_catalog_is_accepted():
    store = InMemoryCommercialState()
    item = make_validated(run_id="run-1", observed_at=BASE_TIME)

    result = store.apply_run(
        decision("run-1", status=RunStatus.WARNING, accepted=True),
        [item],
    )

    assert result.commercial_update_allowed is True
    assert result.current_created == 1


def test_same_state_confirms_open_period_without_duplicate_history():
    store = InMemoryCommercialState()
    first = make_validated(run_id="run-1", observed_at=BASE_TIME)
    second_time = BASE_TIME + timedelta(days=1)
    second = make_validated(run_id="run-2", observed_at=second_time)
    store.apply_run(decision("run-1"), [first])

    result = store.apply_run(
        decision("run-2", decided_at=second_time + timedelta(minutes=1)),
        [second],
    )

    assert result.current_confirmed == 1
    assert result.current_changed == 0
    history = store.history(first.offer.offer_id)
    assert len(history) == 1
    assert history[0].last_confirmed_by_scrape_run_id == "run-2"
    assert history[0].last_observed_at_utc == second_time
    current = store.current(first.offer.offer_id)
    assert current is not None
    assert current.first_observed_at_utc == BASE_TIME
    assert current.last_observed_at_utc == second_time


def test_price_change_closes_period_and_opens_price_period():
    store = InMemoryCommercialState()
    first = make_validated(run_id="run-1", observed_at=BASE_TIME, current_price="30")
    second_time = BASE_TIME + timedelta(days=1)
    second = make_validated(run_id="run-2", observed_at=second_time, current_price="27")
    store.apply_run(decision("run-1"), [first])

    result = store.apply_run(
        decision("run-2", decided_at=second_time + timedelta(minutes=1)),
        [second],
    )

    assert result.current_changed == 1
    history = store.history(first.offer.offer_id)
    assert len(history) == 2
    assert history[0].valid_to_utc == second_time
    assert history[0].closed_by_scrape_run_id == "run-2"
    assert history[1].change_type is ChangeType.PRICE
    assert history[1].changed_fields == ("current_price",)
    assert history[1].valid_to_utc is None
    assert history[0].validated_offer.offer.current_price == Decimal("30")
    assert history[1].validated_offer.offer.current_price == Decimal("27")


def test_reported_regular_price_change_is_not_classified_as_real_price_change():
    store = InMemoryCommercialState()
    first = make_validated(
        run_id="run-1",
        observed_at=BASE_TIME,
        current_price="30",
        reported_regular_price="35",
    )
    second_time = BASE_TIME + timedelta(days=1)
    second = make_validated(
        run_id="run-2",
        observed_at=second_time,
        current_price="30",
        reported_regular_price="40",
    )
    store.apply_run(decision("run-1"), [first])
    store.apply_run(
        decision("run-2", decided_at=second_time + timedelta(minutes=1)),
        [second],
    )

    history = store.history(first.offer.offer_id)
    assert history[1].change_type is ChangeType.REGULAR_PRICE
    assert history[1].changed_fields == ("reported_regular_price",)
    assert history[0].validated_offer.offer.current_price == Decimal("30")
    assert history[1].validated_offer.offer.current_price == Decimal("30")


def test_multiple_categories_of_change_are_classified_as_multiple():
    store = InMemoryCommercialState()
    first = make_validated(run_id="run-1", observed_at=BASE_TIME)
    second_time = BASE_TIME + timedelta(days=1)
    second = make_validated(
        run_id="run-2",
        observed_at=second_time,
        current_price="28",
        is_promotion=True,
        brand="Marca Corregida",
    )
    store.apply_run(decision("run-1"), [first])
    store.apply_run(
        decision("run-2", decided_at=second_time + timedelta(minutes=1)),
        [second],
    )

    period = store.history(first.offer.offer_id)[1]
    assert period.change_type is ChangeType.MULTIPLE
    assert period.changed_fields == (
        "current_price",
        "is_promotion",
        "normalized_brand",
    )


def test_exact_run_replay_is_noop_and_does_not_duplicate_history():
    store = InMemoryCommercialState()
    item = make_validated(run_id="run-1", observed_at=BASE_TIME)
    first = store.apply_run(decision("run-1"), [item])

    replay = store.apply_run(decision("run-1"), [item])

    assert first.replayed is False
    assert replay.replayed is True
    assert replay.mutated is False
    assert len(store.history(item.offer.offer_id)) == 1
    assert store.applied_run_count == 1


def test_same_run_id_with_different_content_is_replay_conflict():
    store = InMemoryCommercialState()
    first = make_validated(run_id="run-1", observed_at=BASE_TIME, current_price="30")
    conflicting = make_validated(
        run_id="run-1",
        observed_at=BASE_TIME,
        current_price="29",
    )
    store.apply_run(decision("run-1"), [first])

    with pytest.raises(CommercialReplayConflict):
        store.apply_run(decision("run-1"), [conflicting])

    assert len(store.history(first.offer.offer_id)) == 1
    assert store.current(first.offer.offer_id).validated_offer == first


def test_out_of_order_change_is_rejected_without_mutation():
    store = InMemoryCommercialState()
    later = BASE_TIME + timedelta(days=2)
    first = make_validated(run_id="run-1", observed_at=later, current_price="30")
    backdated = make_validated(
        run_id="run-2",
        observed_at=BASE_TIME,
        current_price="25",
    )
    store.apply_run(decision("run-1", decided_at=later + timedelta(minutes=1)), [first])

    with pytest.raises(OutOfOrderCommercialObservation):
        store.apply_run(decision("run-2"), [backdated])

    assert len(store.history(first.offer.offer_id)) == 1
    assert store.current(first.offer.offer_id).validated_offer == first
    assert store.applied_run_count == 1


def test_run_application_is_atomic_when_later_offer_is_invalid_by_chronology():
    store = InMemoryCommercialState()
    later = BASE_TIME + timedelta(days=2)
    existing = make_validated(
        run_id="run-1",
        observed_at=later,
        suffix="001",
        current_price="30",
    )
    store.apply_run(decision("run-1", decided_at=later + timedelta(minutes=1)), [existing])

    new_offer = make_validated(
        run_id="run-2",
        observed_at=later + timedelta(days=1),
        suffix="002",
    )
    invalid_existing = make_validated(
        run_id="run-2",
        observed_at=BASE_TIME,
        suffix="001",
        current_price="20",
    )

    with pytest.raises(OutOfOrderCommercialObservation):
        store.apply_run(
            decision("run-2", decided_at=later + timedelta(days=2)),
            [new_offer, invalid_existing],
        )

    assert store.current(new_offer.offer.offer_id) is None
    assert store.current_count == 1
    assert store.applied_run_count == 1


def test_duplicate_offer_in_same_run_is_rejected():
    store = InMemoryCommercialState()
    first = make_validated(run_id="run-1", observed_at=BASE_TIME)
    duplicate = make_validated(run_id="run-1", observed_at=BASE_TIME)

    with pytest.raises(CommercialStateError, match="duplicado"):
        store.apply_run(decision("run-1"), [first, duplicate])

    assert store.current_count == 0


def test_offer_run_id_must_match_decision():
    store = InMemoryCommercialState()
    item = make_validated(run_id="other-run", observed_at=BASE_TIME)

    with pytest.raises(CommercialStateError, match="scrape_run_id"):
        store.apply_run(decision("run-1"), [item])


def test_state_hash_is_revalidated_before_commercial_mutation():
    store = InMemoryCommercialState()
    item = make_validated(run_id="run-1", observed_at=BASE_TIME)
    forged = ValidatedOffer(
        offer=item.offer,
        state_hash="0" * 64,
        validated_at_utc=BASE_TIME,
    )

    with pytest.raises(CommercialStateError, match="state_hash"):
        store.apply_run(decision("run-1"), [forged])

    assert store.current_count == 0


def test_accepted_empty_run_is_rejected_but_nonaccepted_empty_run_is_safe_noop():
    store = InMemoryCommercialState()

    with pytest.raises(CommercialStateError, match="payload vacío"):
        store.apply_run(decision("run-accepted"), [])

    result = store.apply_run(decision("run-rejected", accepted=False), [])
    assert result.mutated is False
    assert result.offers_ignored == 0
    assert store.current_count == 0
