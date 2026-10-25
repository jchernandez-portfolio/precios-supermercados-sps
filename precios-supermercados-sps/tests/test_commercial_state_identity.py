"""Regresiones para evitar contaminación de identidad entre ofertas/runs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from precios_supermercados.commercial_state import (
    CommercialRunDecision,
    CommercialStateError,
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
    offer_id: str | None = None,
    supermarket_id: str = "la-colonia",
    location_id: str = "unknown",
    source_product_id: str | None = None,
    source_key: str = "SKU-001",
    product_id: str = "prod_001",
    currency: str = "HNL",
) -> ValidatedOffer:
    canonical_source_product_id = generate_source_product_id(
        supermarket_id,
        SourceKeyType.SKU,
        source_key,
    )
    effective_source_product_id = source_product_id or canonical_source_product_id
    effective_offer_id = offer_id or generate_offer_id(
        supermarket_id,
        location_id,
        effective_source_product_id,
    )
    offer = NormalizedOffer(
        supermarket_id=supermarket_id,
        location_id=location_id,
        source_product_id=effective_source_product_id,
        source_key_type=SourceKeyType.SKU,
        source_key=source_key,
        product_id=product_id,
        offer_id=effective_offer_id,
        source_name="Producto 001",
        product_url="https://example.invalid/producto-001",
        normalized_name="producto 001",
        currency=currency,
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
    return ValidatedOffer(
        offer=offer,
        state_hash=generate_state_hash(offer),
        validated_at_utc=observed_at,
    )


def _accepted(run_id: str, when: datetime) -> CommercialRunDecision:
    return CommercialRunDecision(
        scrape_run_id=run_id,
        run_status=RunStatus.SUCCESS,
        catalog_accepted=True,
        decided_at_utc=when,
    )


def test_forged_offer_id_cannot_move_existing_offer_to_another_location():
    store = InMemoryCommercialState()
    first = _validated("run-1", T0)
    store.apply_run(_accepted("run-1", T0 + timedelta(minutes=1)), [first])

    t1 = T0 + timedelta(days=1)
    moved = _validated(
        "run-2",
        t1,
        location_id="otra-ubicacion",
        offer_id=first.offer.offer_id,
    )

    with pytest.raises(CommercialStateError, match="offer_id no es determinista"):
        store.apply_run(_accepted("run-2", t1 + timedelta(minutes=1)), [moved])

    assert store.current(first.offer.offer_id).validated_offer == first
    assert len(store.history(first.offer.offer_id)) == 1
    assert store.applied_run_count == 1


def test_forged_source_product_id_is_rejected_before_commercial_mutation():
    store = InMemoryCommercialState()
    forged = _validated(
        "run-1",
        T0,
        source_product_id="sp_forged",
        source_key="SKU-999",
    )

    with pytest.raises(CommercialStateError, match="source_product_id no es determinista"):
        store.apply_run(
            _accepted("run-1", T0 + timedelta(minutes=1)),
            [forged],
        )

    assert store.current_count == 0
    assert store.applied_run_count == 0


def test_existing_source_product_cannot_keep_id_while_source_key_changes():
    store = InMemoryCommercialState()
    first = _validated("run-1", T0)
    store.apply_run(_accepted("run-1", T0 + timedelta(minutes=1)), [first])

    t1 = T0 + timedelta(days=1)
    drifted_key = _validated(
        "run-2",
        t1,
        source_key="SKU-999",
        source_product_id=first.offer.source_product_id,
        offer_id=first.offer.offer_id,
    )

    with pytest.raises(CommercialStateError, match="source_product_id no es determinista"):
        store.apply_run(
            _accepted("run-2", t1 + timedelta(minutes=1)),
            [drifted_key],
        )

    assert store.current(first.offer.offer_id).validated_offer == first


def test_same_logical_offer_identity_cannot_gain_forged_second_offer_id():
    store = InMemoryCommercialState()
    first = _validated("run-1", T0)
    store.apply_run(_accepted("run-1", T0 + timedelta(minutes=1)), [first])

    t1 = T0 + timedelta(days=1)
    duplicate_identity = _validated("run-2", t1, offer_id="of_forged")

    with pytest.raises(CommercialStateError, match="offer_id no es determinista"):
        store.apply_run(
            _accepted("run-2", t1 + timedelta(minutes=1)),
            [duplicate_identity],
        )

    assert store.current_count == 1


def test_same_source_product_across_locations_is_allowed_with_canonical_offer_ids():
    store = InMemoryCommercialState()
    first = _validated(
        "run-1",
        T0,
        location_id="location-a",
    )
    store.apply_run(_accepted("run-1", T0 + timedelta(minutes=1)), [first])

    t1 = T0 + timedelta(days=1)
    second_location = _validated(
        "run-2",
        t1,
        location_id="location-b",
    )
    result = store.apply_run(
        _accepted("run-2", t1 + timedelta(minutes=1)),
        [second_location],
    )

    assert first.offer.source_product_id == second_location.offer.source_product_id
    assert first.offer.offer_id != second_location.offer.offer_id
    assert result.current_created == 1
    assert store.current_count == 2


def test_product_mapping_can_change_without_changing_source_identity():
    store = InMemoryCommercialState()
    first = _validated("run-1", T0, product_id="prod_pending")
    store.apply_run(_accepted("run-1", T0 + timedelta(minutes=1)), [first])

    t1 = T0 + timedelta(days=1)
    remapped = _validated("run-2", t1, product_id="prod_reviewed")
    result = store.apply_run(
        _accepted("run-2", t1 + timedelta(minutes=1)),
        [remapped],
    )

    assert result.current_confirmed == 1
    current = store.current(first.offer.offer_id)
    assert current is not None
    assert current.validated_offer.offer.product_id == "prod_reviewed"
    assert len(store.history(first.offer.offer_id)) == 1


def test_currency_is_stable_for_the_same_offer_identity():
    store = InMemoryCommercialState()
    first = _validated("run-1", T0, currency="HNL")
    store.apply_run(_accepted("run-1", T0 + timedelta(minutes=1)), [first])

    t1 = T0 + timedelta(days=1)
    changed_currency = _validated("run-2", t1, currency="USD")

    with pytest.raises(CommercialStateError, match="moneda estable"):
        store.apply_run(
            _accepted("run-2", t1 + timedelta(minutes=1)),
            [changed_currency],
        )

    assert store.current(first.offer.offer_id).validated_offer.offer.currency == "HNL"
