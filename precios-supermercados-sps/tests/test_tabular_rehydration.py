from __future__ import annotations

from copy import deepcopy
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
from precios_supermercados.locations import (
    LocationConfig,
    LocationGranularity,
    LocationSelectionMode,
    SupermarketConfig,
    build_location_catalog,
)
from precios_supermercados.models import NormalizedOffer, ValidatedOffer
from precios_supermercados.tabular_persistence import (
    current_offer_row,
    history_offer_row,
)
from precios_supermercados.tabular_rehydration import (
    TabularRehydrationError,
    current_from_row,
    history_from_row,
    rehydrate_commercial_snapshot,
)

BASE = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


def catalog():
    supermarket = SupermarketConfig(
        supermarket_id="demo",
        supermarket_name="Super Demo",
        location_selection_mode=LocationSelectionMode.FIXED_SINGLE_CITY,
    )
    location = LocationConfig(
        location_id="demo_sps",
        supermarket_id="demo",
        city_id="sps",
        city_name="San Pedro Sula",
        granularity=LocationGranularity.CITY,
        is_available=True,
        in_scope=True,
        extraction_enabled=True,
        evidence="fixed_city",
    )
    return build_location_catalog((supermarket,), (location,))


def validated(*, run_id: str, observed_at: datetime, price: str) -> ValidatedOffer:
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
        source_name="Producto Fuente 2 x 500 ml",
        product_url="https://example.invalid/p/001",
        normalized_name="Producto Fuente",
        currency="HNL",
        is_promotion=True,
        availability=AvailabilityStatus.IN_STOCK,
        location_status=LocationStatus.INFERRED,
        location_evidence="fixed_city_configuration",
        location_confidence=Decimal("1"),
        observed_at_utc=observed_at,
        scrape_run_id=run_id,
        extractor_version="extractor-7",
        schema_version="2",
        source_url="https://example.invalid/api/catalog",
        normalized_brand="Marca",
        category="Abarrotes",
        subcategory="Prueba",
        variant="Original",
        unit_count=2,
        content_per_unit=Decimal("500"),
        measurement_unit="ml",
        total_content=Decimal("1000"),
        current_price=Decimal(price),
        reported_regular_price=Decimal("45"),
        source_sku="REF-001",
        source_brand="MARCA FUENTE",
        source_presentation="2 x 500ml",
        source_category="Pasillo 3",
        image_url="https://example.invalid/i/001.jpg",
        barcode="742000000001",
        unit_price=Decimal(price),
        unit_price_basis="L",
        raw_values={"large_source_payload": "intentionally_not_persisted"},
    )
    return ValidatedOffer(
        offer=offer,
        state_hash=generate_state_hash(offer),
        validated_at_utc=observed_at + timedelta(seconds=5),
        quality_events=("source_contract_verified",),
    )


def decision(item: ValidatedOffer) -> CommercialRunDecision:
    return CommercialRunDecision(
        scrape_run_id=item.offer.scrape_run_id,
        run_status=RunStatus.SUCCESS,
        catalog_accepted=True,
        decided_at_utc=item.validated_at_utc + timedelta(seconds=5),
    )


def evolved_state():
    state = InMemoryCommercialState()
    first = validated(run_id="run-1", observed_at=BASE, price="40")
    second = validated(
        run_id="run-2",
        observed_at=BASE + timedelta(days=1),
        price="38",
    )
    state.apply_run(decision(first), (first,))
    state.apply_run(decision(second), (second,))
    current = state.current(first.offer.offer_id)
    history = state.history(first.offer.offer_id)
    assert current is not None
    assert len(history) == 2
    return current, history


def test_current_and_history_roundtrip_preserves_durable_continuation_fields():
    cfg = catalog()
    current, history = evolved_state()
    current_row = current_offer_row(current, cfg)
    history_rows = tuple(history_offer_row(period, cfg) for period in history)

    snapshot = rehydrate_commercial_snapshot((current_row,), history_rows)
    restored_current = snapshot.current[current.validated_offer.offer.offer_id]
    restored_history = snapshot.history[current.validated_offer.offer.offer_id]

    assert current_offer_row(restored_current, cfg) == current_row
    assert tuple(history_offer_row(period, cfg) for period in restored_history) == history_rows

    restored_offer = restored_current.validated_offer.offer
    assert restored_offer.source_category == "Pasillo 3"
    assert restored_offer.barcode == "742000000001"
    assert restored_offer.location_status is LocationStatus.INFERRED
    assert restored_offer.location_evidence == "fixed_city_configuration"
    assert restored_offer.location_confidence == Decimal("1")
    assert restored_offer.extractor_version == "extractor-7"
    assert restored_offer.schema_version == "2"
    assert restored_offer.source_url == "https://example.invalid/api/catalog"
    assert restored_current.validated_offer.quality_events == (
        "source_contract_verified",
    )
    assert dict(restored_offer.raw_values) == {}


def test_current_row_contains_latest_offer_run_and_validation_timestamp():
    cfg = catalog()
    current, _history = evolved_state()

    row = current_offer_row(current, cfg)

    assert row["offer_scrape_run_id"] == "run-2"
    assert row["last_scrape_run_id"] == "run-2"
    assert row["validated_at_utc"] == "2026-08-23T12:00:05.000Z"
    assert row["quality_events_json"] == '["source_contract_verified"]'
    assert row["source_category"] == "Pasillo 3"
    assert row["barcode"] == "742000000001"


def test_history_row_preserves_original_offer_run_after_period_closes():
    cfg = catalog()
    _current, history = evolved_state()

    closed = history_offer_row(history[0], cfg)
    opened = history_offer_row(history[1], cfg)

    assert closed["offer_scrape_run_id"] == "run-1"
    assert closed["opened_by_scrape_run_id"] == "run-1"
    assert closed["closed_by_scrape_run_id"] == "run-2"
    assert opened["offer_scrape_run_id"] == "run-2"
    assert opened["opened_by_scrape_run_id"] == "run-2"


def test_state_hash_corruption_is_rejected():
    cfg = catalog()
    current, _history = evolved_state()
    row = current_offer_row(current, cfg)
    row["state_hash"] = "0" * 64

    with pytest.raises(TabularRehydrationError, match="durable_state_hash_mismatch"):
        current_from_row(row)


def test_source_identity_corruption_is_rejected():
    cfg = catalog()
    current, _history = evolved_state()
    row = current_offer_row(current, cfg)
    row["source_key"] = "OTHER-SKU"

    with pytest.raises(
        TabularRehydrationError,
        match="durable_source_product_id_mismatch",
    ):
        current_from_row(row)


def test_current_run_must_match_latest_validated_offer_run():
    cfg = catalog()
    current, _history = evolved_state()
    row = current_offer_row(current, cfg)
    row["last_scrape_run_id"] = "run-other"

    with pytest.raises(TabularRehydrationError, match="durable_current_run_mismatch"):
        current_from_row(row)


def test_history_open_run_must_match_validated_offer_run():
    cfg = catalog()
    _current, history = evolved_state()
    row = history_offer_row(history[0], cfg)
    row["opened_by_scrape_run_id"] = "run-other"

    with pytest.raises(
        TabularRehydrationError,
        match="durable_history_open_run_mismatch",
    ):
        history_from_row(row)


def test_snapshot_rejects_history_gap_even_if_each_row_is_locally_valid():
    cfg = catalog()
    current, history = evolved_state()
    current_row = current_offer_row(current, cfg)
    rows = [history_offer_row(period, cfg) for period in history]
    rows[0]["valid_to_utc"] = "2026-08-23T11:59:59.000Z"

    with pytest.raises(TabularRehydrationError, match="durable_history_gap_or_overlap"):
        rehydrate_commercial_snapshot((current_row,), rows)


def test_snapshot_requires_same_offer_set_in_current_and_history():
    cfg = catalog()
    current, _history = evolved_state()
    current_row = current_offer_row(current, cfg)

    with pytest.raises(
        TabularRehydrationError,
        match="durable_current_history_offer_set_mismatch",
    ):
        rehydrate_commercial_snapshot((current_row,), ())


def test_quality_events_json_rejects_duplicate_or_non_string_entries():
    cfg = catalog()
    current, _history = evolved_state()
    row = current_offer_row(current, cfg)

    duplicate = deepcopy(row)
    duplicate["quality_events_json"] = '["same","same"]'
    with pytest.raises(TabularRehydrationError, match="quality_events_json_duplicate"):
        current_from_row(duplicate)

    invalid = deepcopy(row)
    invalid["quality_events_json"] = '["ok",1]'
    with pytest.raises(TabularRehydrationError, match="quality_events_json_invalid"):
        current_from_row(invalid)
