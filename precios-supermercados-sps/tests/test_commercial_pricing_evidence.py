"""Evidencia temporal y de ejecución para pricing fail-closed."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from precios_supermercados.commercial_pricing import (
    CommercialPricingError,
    evaluate_real_price_reduction,
)
from precios_supermercados.commercial_state import CommercialRunDecision, InMemoryCommercialState
from precios_supermercados.enums import AvailabilityStatus, LocationStatus, RunStatus, SourceKeyType
from precios_supermercados.identifiers import (
    generate_offer_id,
    generate_source_product_id,
    generate_state_hash,
)
from precios_supermercados.models import NormalizedOffer, ValidatedOffer

T0 = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def _validated(run_id: str, observed_at: datetime, price: str) -> ValidatedOffer:
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
        current_price=Decimal(price),
        reported_regular_price=Decimal("35"),
    )
    return ValidatedOffer(
        offer=offer,
        state_hash=generate_state_hash(offer),
        validated_at_utc=observed_at,
    )


def _decision(run_id: str, when: datetime) -> CommercialRunDecision:
    return CommercialRunDecision(
        scrape_run_id=run_id,
        run_status=RunStatus.SUCCESS,
        catalog_accepted=True,
        decided_at_utc=when,
    )


def _store() -> tuple[InMemoryCommercialState, str]:
    store = InMemoryCommercialState()
    first = _validated("run-1", T0, "30")
    store.apply_run(_decision("run-1", T0 + timedelta(minutes=1)), [first])
    t1 = T0 + timedelta(days=1)
    second = _validated("run-2", t1, "25")
    store.apply_run(_decision("run-2", t1 + timedelta(minutes=1)), [second])
    return store, first.offer.offer_id


def test_current_latest_evidence_observation_must_match_current_metadata():
    store, offer_id = _store()
    current = store.current(offer_id)
    assert current is not None
    forged_offer = replace(
        current.validated_offer.offer,
        observed_at_utc=current.last_observed_at_utc + timedelta(seconds=1),
    )
    forged_validated = replace(current.validated_offer, offer=forged_offer)
    forged_current = replace(current, validated_offer=forged_validated)

    with pytest.raises(CommercialPricingError, match="evidencia discrepan en observación"):
        evaluate_real_price_reduction(forged_current, store.history(offer_id))


def test_current_latest_evidence_run_must_match_current_metadata():
    store, offer_id = _store()
    current = store.current(offer_id)
    assert current is not None
    forged_offer = replace(current.validated_offer.offer, scrape_run_id="run-forged")
    forged_validated = replace(current.validated_offer, offer=forged_offer)
    forged_current = replace(current, validated_offer=forged_validated)

    with pytest.raises(CommercialPricingError, match="evidencia discrepan en ejecución"):
        evaluate_real_price_reduction(forged_current, store.history(offer_id))


def test_history_opening_evidence_observation_must_match_valid_from():
    store, offer_id = _store()
    history = list(store.history(offer_id))
    forged_offer = replace(
        history[0].validated_offer.offer,
        observed_at_utc=history[0].valid_from_utc + timedelta(seconds=1),
    )
    forged_validated = replace(history[0].validated_offer, offer=forged_offer)
    history[0] = replace(history[0], validated_offer=forged_validated)

    with pytest.raises(CommercialPricingError, match="evidencia discrepan en apertura"):
        evaluate_real_price_reduction(store.current(offer_id), history)


def test_history_opening_evidence_run_must_match_opened_by_run():
    store, offer_id = _store()
    history = list(store.history(offer_id))
    forged_offer = replace(history[0].validated_offer.offer, scrape_run_id="run-forged")
    forged_validated = replace(history[0].validated_offer, offer=forged_offer)
    history[0] = replace(history[0], validated_offer=forged_validated)

    with pytest.raises(CommercialPricingError, match="ejecución de apertura"):
        evaluate_real_price_reduction(store.current(offer_id), history)
