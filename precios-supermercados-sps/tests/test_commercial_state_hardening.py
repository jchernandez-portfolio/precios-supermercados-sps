"""Counterexamples adicionales para replay y ausencia de ofertas."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from precios_supermercados.commercial_state import (
    CommercialReplayConflict,
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


def validated(
    run_id: str,
    suffix: str,
    observed_at: datetime,
    *,
    price: str | None = "30",
    availability: AvailabilityStatus = AvailabilityStatus.IN_STOCK,
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
        product_url=f"https://example.invalid/{suffix}",
        normalized_name=f"producto {suffix}",
        currency="HNL",
        is_promotion=False,
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
        current_price=Decimal(price) if price is not None else None,
        reported_regular_price=Decimal("35"),
    )
    return ValidatedOffer(
        offer=offer,
        state_hash=generate_state_hash(offer),
        validated_at_utc=observed_at,
    )


def accepted(run_id: str, decided_at: datetime) -> CommercialRunDecision:
    return CommercialRunDecision(
        scrape_run_id=run_id,
        run_status=RunStatus.SUCCESS,
        catalog_accepted=True,
        decided_at_utc=decided_at,
    )


def test_replay_same_state_with_different_observation_timestamp_is_conflict():
    store = InMemoryCommercialState()
    first = validated("run-1", "001", T0)
    store.apply_run(accepted("run-1", T0 + timedelta(hours=1)), [first])

    forged_replay = validated("run-1", "001", T0 + timedelta(minutes=1))
    with pytest.raises(CommercialReplayConflict):
        store.apply_run(
            accepted("run-1", T0 + timedelta(hours=1)),
            [forged_replay],
        )

    assert store.current(first.offer.offer_id).last_observed_at_utc == T0
    assert len(store.history(first.offer.offer_id)) == 1


def test_replay_same_payload_with_different_decision_timestamp_is_conflict():
    store = InMemoryCommercialState()
    item = validated("run-1", "001", T0)
    store.apply_run(accepted("run-1", T0 + timedelta(hours=1)), [item])

    with pytest.raises(CommercialReplayConflict):
        store.apply_run(
            accepted("run-1", T0 + timedelta(hours=2)),
            [item],
        )


def test_replay_same_state_and_timestamps_with_different_source_url_is_conflict():
    store = InMemoryCommercialState()
    item = validated("run-1", "001", T0)
    decision = accepted("run-1", T0 + timedelta(hours=1))
    store.apply_run(decision, [item])

    altered_offer = replace(
        item.offer,
        source_url="https://example.invalid/graphql-v2",
    )
    altered = ValidatedOffer(
        offer=altered_offer,
        state_hash=generate_state_hash(altered_offer),
        validated_at_utc=item.validated_at_utc,
    )

    with pytest.raises(CommercialReplayConflict):
        store.apply_run(decision, [altered])


def test_replay_same_state_and_timestamps_with_different_quality_evidence_is_conflict():
    store = InMemoryCommercialState()
    item = validated("run-1", "001", T0)
    decision = accepted("run-1", T0 + timedelta(hours=1))
    store.apply_run(decision, [item])

    altered = ValidatedOffer(
        offer=item.offer,
        state_hash=item.state_hash,
        validated_at_utc=item.validated_at_utc,
        quality_events=("quality:late_evidence",),
    )

    with pytest.raises(CommercialReplayConflict):
        store.apply_run(decision, [altered])


def test_running_status_does_not_consume_run_id_before_terminal_success():
    store = InMemoryCommercialState()
    item = validated("run-1", "001", T0)

    running = CommercialRunDecision(
        scrape_run_id="run-1",
        run_status=RunStatus.RUNNING,
        catalog_accepted=False,
        decided_at_utc=T0 + timedelta(minutes=30),
    )
    first_result = store.apply_run(running, [item])

    assert first_result.commercial_update_allowed is False
    assert first_result.mutated is False
    assert store.applied_run_count == 0

    final_result = store.apply_run(
        accepted("run-1", T0 + timedelta(hours=1)),
        [item],
    )

    assert final_result.current_created == 1
    assert final_result.replayed is False
    assert store.applied_run_count == 1
    assert store.current(item.offer.offer_id) is not None


def test_offer_omitted_from_later_payload_is_not_inferred_as_deleted_or_out_of_stock():
    store = InMemoryCommercialState()
    first_a = validated("run-1", "001", T0)
    first_b = validated("run-1", "002", T0)
    store.apply_run(
        accepted("run-1", T0 + timedelta(hours=1)),
        [first_a, first_b],
    )

    t1 = T0 + timedelta(days=1)
    second_a = validated("run-2", "001", t1)
    store.apply_run(accepted("run-2", t1 + timedelta(hours=1)), [second_a])

    untouched = store.current(first_b.offer.offer_id)
    assert untouched is not None
    assert untouched.validated_offer.offer.availability is AvailabilityStatus.IN_STOCK
    assert untouched.last_scrape_run_id == "run-1"
    assert len(store.history(first_b.offer.offer_id)) == 1


def test_explicit_out_of_stock_observation_can_change_state_without_price():
    store = InMemoryCommercialState()
    initial = validated("run-1", "001", T0)
    store.apply_run(accepted("run-1", T0 + timedelta(hours=1)), [initial])

    t1 = T0 + timedelta(days=1)
    explicit = validated(
        "run-2",
        "001",
        t1,
        price=None,
        availability=AvailabilityStatus.OUT_OF_STOCK,
    )
    store.apply_run(accepted("run-2", t1 + timedelta(hours=1)), [explicit])

    current = store.current(initial.offer.offer_id)
    assert current is not None
    assert current.validated_offer.offer.availability is AvailabilityStatus.OUT_OF_STOCK
    assert current.validated_offer.offer.current_price is None
