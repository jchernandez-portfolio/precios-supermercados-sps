from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

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

BASE = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


def _offer(*, run_id: str, observed_at: datetime, price: str) -> ValidatedOffer:
    source_key = "SKU-001"
    source_product_id = generate_source_product_id(
        "demo",
        SourceKeyType.SKU,
        source_key,
    )
    offer_id = generate_offer_id("demo", "demo_sps", source_product_id)
    normalized = NormalizedOffer(
        supermarket_id="demo",
        location_id="demo_sps",
        source_product_id=source_product_id,
        source_key_type=SourceKeyType.SKU,
        source_key=source_key,
        product_id="prod-001",
        offer_id=offer_id,
        source_name="Producto demo 1 unidad",
        product_url="https://example.invalid/p/001",
        normalized_name="Producto demo",
        currency="HNL",
        is_promotion=False,
        availability=AvailabilityStatus.IN_STOCK,
        location_status=LocationStatus.CONFIRMED,
        location_evidence="test_fixture",
        location_confidence=Decimal("1"),
        observed_at_utc=observed_at,
        scrape_run_id=run_id,
        extractor_version="test",
        schema_version="1",
        source_url="https://example.invalid/catalog",
        normalized_brand="Marca",
        category="Demo",
        subcategory="Demo",
        variant="Base",
        unit_count=1,
        content_per_unit=Decimal("1"),
        measurement_unit="unit",
        total_content=Decimal("1"),
        current_price=Decimal(price),
    )
    return ValidatedOffer(
        offer=normalized,
        state_hash=generate_state_hash(normalized),
        validated_at_utc=observed_at,
    )


def _decision(item: ValidatedOffer) -> CommercialRunDecision:
    return CommercialRunDecision(
        scrape_run_id=item.offer.scrape_run_id,
        run_status=RunStatus.SUCCESS,
        catalog_accepted=True,
        decided_at_utc=item.validated_at_utc + timedelta(seconds=1),
    )


def test_current_first_observed_is_start_of_open_state_period_after_change():
    state = InMemoryCommercialState()
    first = _offer(run_id="run-1", observed_at=BASE, price="40")
    changed = _offer(
        run_id="run-2",
        observed_at=BASE + timedelta(days=1),
        price="38",
    )

    state.apply_run(_decision(first), (first,))
    state.apply_run(_decision(changed), (changed,))

    current = state.current(first.offer.offer_id)
    history = state.history(first.offer.offer_id)
    assert current is not None
    assert len(history) == 2

    # `first_observed_at_utc` pertenece al estado current, no a toda la vida
    # de la oferta. Por eso coincide con el periodo histórico abierto actual.
    assert current.first_observed_at_utc == history[-1].valid_from_utc
    assert current.first_observed_at_utc != history[0].valid_from_utc
    assert history[0].valid_to_utc == history[-1].valid_from_utc
