"""Regresiones de coherencia entre current, histórico y state_hash canónico."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

from precios_supermercados.commercial_state import (
    CommercialRunDecision,
    InMemoryCommercialState,
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


def _validated(
    run_id: str,
    observed_at: datetime,
    *,
    current_price: str = "30",
    brand: str = "Marca Demo",
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
        is_promotion=False,
        availability=AvailabilityStatus.IN_STOCK,
        location_status=LocationStatus.UNKNOWN,
        observed_at_utc=observed_at,
        scrape_run_id=run_id,
        extractor_version=f"extractor-{run_id}",
        schema_version="1",
        source_url=f"https://example.invalid/graphql?run={run_id}",
        normalized_brand=brand,
        category="Abarrotes",
        subcategory="General",
        variant="Base",
        unit_count=1,
        content_per_unit=Decimal("1"),
        measurement_unit="unit",
        total_content=Decimal("1"),
        current_price=Decimal(current_price),
        reported_regular_price=Decimal("35"),
    )
    return ValidatedOffer(
        offer=offer,
        state_hash=generate_state_hash(offer),
        validated_at_utc=observed_at,
    )


def _decision(run_id: str, decided_at: datetime) -> CommercialRunDecision:
    return CommercialRunDecision(
        scrape_run_id=run_id,
        run_status=RunStatus.SUCCESS,
        catalog_accepted=True,
        decided_at_utc=decided_at,
    )


def test_same_hash_refreshes_current_evidence_but_keeps_single_history_period():
    store = InMemoryCommercialState()
    first = _validated("run-1", BASE_TIME)
    second_time = BASE_TIME + timedelta(days=1)
    second = _validated("run-2", second_time)

    store.apply_run(_decision("run-1", BASE_TIME + timedelta(minutes=1)), [first])
    result = store.apply_run(
        _decision("run-2", second_time + timedelta(minutes=1)),
        [second],
    )

    assert result.current_confirmed == 1
    current = store.current(first.offer.offer_id)
    assert current is not None
    assert current.validated_offer == second
    assert current.last_scrape_run_id == "run-2"
    assert current.validated_offer.offer.scrape_run_id == current.last_scrape_run_id
    assert current.first_observed_at_utc == BASE_TIME
    assert current.last_observed_at_utc == second_time

    history = store.history(first.offer.offer_id)
    assert len(history) == 1
    assert history[0].validated_offer == first
    assert history[0].last_confirmed_by_scrape_run_id == "run-2"
    assert history[0].last_observed_at_utc == second_time


def test_changed_fields_uses_same_text_canonicalization_as_state_hash():
    store = InMemoryCommercialState()
    first = _validated("run-1", BASE_TIME, current_price="30", brand="Marca Demo")
    second_time = BASE_TIME + timedelta(days=1)
    second = _validated(
        "run-2",
        second_time,
        current_price="29",
        brand="  MARCA   DEMO  ",
    )

    store.apply_run(_decision("run-1", BASE_TIME + timedelta(minutes=1)), [first])
    store.apply_run(
        _decision("run-2", second_time + timedelta(minutes=1)),
        [second],
    )

    history = store.history(first.offer.offer_id)
    assert len(history) == 2
    assert history[1].change_type is ChangeType.PRICE
    assert history[1].changed_fields == ("current_price",)
