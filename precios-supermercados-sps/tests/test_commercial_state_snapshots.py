"""Regresiones para snapshots defensivos de evidencia raw anidada."""

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

T0 = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def _validated(
    run_id: str,
    observed_at: datetime,
    raw_values: dict[str, object],
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
        raw_values=raw_values,
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


def test_mutating_original_nested_raw_values_after_apply_does_not_change_current_or_history():
    nested = {"prices": ["30"], "metadata": {"seller": "A"}}
    raw_values = {"nested": nested}
    validated = _validated("run-1", T0, raw_values)
    store = InMemoryCommercialState()

    store.apply_run(_decision("run-1", T0 + timedelta(minutes=1)), [validated])

    nested["prices"][0] = "999"
    nested["metadata"]["seller"] = "MUTATED"

    current = store.current(validated.offer.offer_id)
    history = store.history(validated.offer.offer_id)
    assert current is not None
    assert current.validated_offer.offer.raw_values["nested"]["prices"] == ["30"]
    assert current.validated_offer.offer.raw_values["nested"]["metadata"]["seller"] == "A"
    assert history[0].validated_offer.offer.raw_values["nested"]["prices"] == ["30"]
    assert history[0].validated_offer.offer.raw_values["nested"]["metadata"]["seller"] == "A"


def test_mutating_current_view_does_not_change_internal_state():
    validated = _validated(
        "run-1",
        T0,
        {"nested": {"prices": ["30"]}},
    )
    store = InMemoryCommercialState()
    store.apply_run(_decision("run-1", T0 + timedelta(minutes=1)), [validated])

    first_view = store.current(validated.offer.offer_id)
    assert first_view is not None
    first_view.validated_offer.offer.raw_values["nested"]["prices"].append("999")

    second_view = store.current(validated.offer.offer_id)
    assert second_view is not None
    assert second_view.validated_offer.offer.raw_values["nested"]["prices"] == ["30"]


def test_mutating_history_view_does_not_change_internal_history():
    validated = _validated(
        "run-1",
        T0,
        {"nested": {"events": ["opened"]}},
    )
    store = InMemoryCommercialState()
    store.apply_run(_decision("run-1", T0 + timedelta(minutes=1)), [validated])

    first_history = store.history(validated.offer.offer_id)
    first_history[0].validated_offer.offer.raw_values["nested"]["events"].append("tampered")

    second_history = store.history(validated.offer.offer_id)
    assert second_history[0].validated_offer.offer.raw_values["nested"]["events"] == ["opened"]


def test_same_hash_confirmation_snapshots_latest_current_evidence_independently():
    first_raw = {"nested": {"seller": ["A"]}}
    first = _validated("run-1", T0, first_raw)
    store = InMemoryCommercialState()
    store.apply_run(_decision("run-1", T0 + timedelta(minutes=1)), [first])

    t1 = T0 + timedelta(days=1)
    second_raw = {"nested": {"seller": ["B"]}}
    second = _validated("run-2", t1, second_raw)
    store.apply_run(_decision("run-2", t1 + timedelta(minutes=1)), [second])

    second_raw["nested"]["seller"][0] = "MUTATED"

    current = store.current(first.offer.offer_id)
    history = store.history(first.offer.offer_id)
    assert current is not None
    assert current.validated_offer.offer.raw_values["nested"]["seller"] == ["B"]
    assert history[0].validated_offer.offer.raw_values["nested"]["seller"] == ["A"]
