"""Cronología fail-closed de observación, validación y decisión comercial."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from precios_supermercados.commercial_state import (
    CommercialRunDecision,
    CommercialStateError,
    InMemoryCommercialState,
)
from precios_supermercados.enums import AvailabilityStatus, LocationStatus, RunStatus, SourceKeyType
from precios_supermercados.identifiers import (
    generate_offer_id,
    generate_source_product_id,
    generate_state_hash,
)
from precios_supermercados.models import NormalizedOffer, ValidatedOffer

T0 = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def offer(run_id: str, observed_at: datetime) -> NormalizedOffer:
    supermarket_id = "la-colonia"
    location_id = "unknown"
    source_key = "SKU-001"
    source_product_id = generate_source_product_id(
        supermarket_id,
        SourceKeyType.SKU,
        source_key,
    )
    offer_id = generate_offer_id(supermarket_id, location_id, source_product_id)
    return NormalizedOffer(
        supermarket_id=supermarket_id,
        location_id=location_id,
        source_product_id=source_product_id,
        source_key_type=SourceKeyType.SKU,
        source_key=source_key,
        product_id="prod_001",
        offer_id=offer_id,
        source_name="Producto",
        product_url="https://example.invalid/producto",
        normalized_name="producto",
        currency="HNL",
        is_promotion=False,
        availability=AvailabilityStatus.IN_STOCK,
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
        current_price=Decimal("30"),
        reported_regular_price=Decimal("35"),
    )


def decision(run_id: str, decided_at: datetime) -> CommercialRunDecision:
    return CommercialRunDecision(
        scrape_run_id=run_id,
        run_status=RunStatus.SUCCESS,
        catalog_accepted=True,
        decided_at_utc=decided_at,
    )


def test_validation_cannot_precede_source_observation():
    store = InMemoryCommercialState()
    observed_at = T0 + timedelta(minutes=10)
    value = offer("run-1", observed_at)
    validated = ValidatedOffer(
        offer=value,
        state_hash=generate_state_hash(value),
        validated_at_utc=T0,
    )

    with pytest.raises(CommercialStateError, match="anterior a observed_at_utc"):
        store.apply_run(decision("run-1", T0 + timedelta(hours=1)), [validated])

    assert store.current_count == 0
    assert store.applied_run_count == 0


def test_run_decision_cannot_precede_offer_validation():
    store = InMemoryCommercialState()
    value = offer("run-1", T0)
    validated = ValidatedOffer(
        offer=value,
        state_hash=generate_state_hash(value),
        validated_at_utc=T0 + timedelta(hours=2),
    )

    with pytest.raises(CommercialStateError, match="posterior a decided_at_utc"):
        store.apply_run(decision("run-1", T0 + timedelta(hours=1)), [validated])

    assert store.current_count == 0
    assert store.applied_run_count == 0


def test_equal_boundaries_are_valid_and_remain_idempotent():
    store = InMemoryCommercialState()
    value = offer("run-1", T0)
    validated = ValidatedOffer(
        offer=value,
        state_hash=generate_state_hash(value),
        validated_at_utc=T0,
    )
    run_decision = decision("run-1", T0)

    first = store.apply_run(run_decision, [validated])
    replay = store.apply_run(run_decision, [validated])

    assert first.current_created == 1
    assert replay.replayed is True
    assert len(store.history(value.offer_id)) == 1
