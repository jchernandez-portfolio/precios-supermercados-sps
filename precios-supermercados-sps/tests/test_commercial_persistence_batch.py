from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from precios_supermercados.commercial_persistence_batch import (
    CommercialPersistencePreparationError,
    prepare_new_run_persistence,
)
from precios_supermercados.commercial_state import (
    CommercialRunDecision,
    InMemoryCommercialState,
)
from precios_supermercados.enums import (
    AvailabilityStatus,
    LocationStatus,
    QualityEventCategory,
    QualityEventSeverity,
    RunStatus,
    SourceKeyType,
)
from precios_supermercados.identifiers import (
    generate_offer_id,
    generate_source_product_id,
    generate_state_hash,
)
from precios_supermercados.locations import (
    DEFAULT_LOCATION_CATALOG,
    LocationConfig,
    LocationGranularity,
    LocationSelectionMode,
    SupermarketConfig,
    build_location_catalog,
)
from precios_supermercados.models import NormalizedOffer, ValidatedOffer
from precios_supermercados.storage_contract import LEGACY_TABULAR_TABLE_NAMES
from precios_supermercados.tabular_records import QualityEventRecord
from precios_supermercados.tabular_store import InMemoryTabularStore

BASE = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


def demo_catalog():
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
        evidence="fixed_city_test",
    )
    return build_location_catalog((supermarket,), (location,))


def validated(
    *,
    run_id: str,
    observed_at: datetime,
    price: str = "40",
    supermarket_id: str = "demo",
    location_id: str = "demo_sps",
    location_status: LocationStatus = LocationStatus.INFERRED,
) -> ValidatedOffer:
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
        product_id="prod-001",
        offer_id=offer_id,
        source_name="Producto demo 1 unidad",
        product_url="https://example.invalid/p/001",
        normalized_name="Producto demo",
        currency="HNL",
        is_promotion=False,
        availability=AvailabilityStatus.IN_STOCK,
        location_status=location_status,
        location_evidence="fixture",
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
        offer=offer,
        state_hash=generate_state_hash(offer),
        validated_at_utc=observed_at,
    )


def decision(
    run_id: str,
    *,
    status: RunStatus = RunStatus.SUCCESS,
    accepted: bool = True,
    decided_at: datetime = BASE + timedelta(hours=1),
) -> CommercialRunDecision:
    return CommercialRunDecision(
        scrape_run_id=run_id,
        run_status=status,
        catalog_accepted=accepted,
        decided_at_utc=decided_at,
    )


def quality_event(run_id: str, *, location_id: str = "demo_sps"):
    return QualityEventRecord.deterministic(
        scrape_run_id=run_id,
        supermarket_id="demo",
        location_id=location_id,
        category=QualityEventCategory.QUALITY,
        severity=QualityEventSeverity.INFO,
        event_code="catalog_validated",
        observed_at_utc=BASE,
        sequence=0,
    )


def prepare(
    state: InMemoryCommercialState,
    item: ValidatedOffer,
    *,
    catalog=None,
    events=(),
):
    return prepare_new_run_persistence(
        state,
        decision(
            item.offer.scrape_run_id,
            decided_at=item.validated_at_utc + timedelta(minutes=1),
        ),
        (item,),
        supermarket_id=item.offer.supermarket_id,
        location_id=item.offer.location_id,
        started_at_utc=item.offer.observed_at_utc - timedelta(seconds=10),
        finished_at_utc=item.offer.observed_at_utc + timedelta(seconds=10),
        products_observed=1,
        offers_observed=1,
        quality_events=events,
        catalog=catalog or demo_catalog(),
    )


def test_new_accepted_run_builds_legacy_tabular_transition_batch_atomically():
    state = InMemoryCommercialState()
    item = validated(run_id="run-1", observed_at=BASE)
    event = quality_event("run-1")

    prepared = prepare(state, item, events=(event,))

    assert prepared.apply_result.current_created == 1
    assert prepared.apply_result.replayed is False
    assert prepared.affected_offer_ids == (item.offer.offer_id,)
    assert tuple(prepared.table_row_counts) == (
        "cfg_supermarkets",
        "cfg_locations",
        "fact_offers_current",
        "fact_offer_history",
        "fact_scrape_runs",
        "fact_quality_events",
    )
    assert set(prepared.table_row_counts).issubset(LEGACY_TABULAR_TABLE_NAMES)
    assert prepared.table_row_counts == {
        "cfg_supermarkets": 1,
        "cfg_locations": 1,
        "fact_offers_current": 1,
        "fact_offer_history": 1,
        "fact_scrape_runs": 1,
        "fact_quality_events": 1,
    }
    assert prepared.batch.rows["fact_offers_current"][0]["product_id"] == "prod-001"
    assert prepared.batch.rows["fact_offers_current"][0]["source_product_id"] == item.offer.source_product_id
    assert "dim_products" not in prepared.batch.rows
    assert "map_source_products" not in prepared.batch.rows
    assert prepared.run_record.quality_event_count == 1

    store = InMemoryTabularStore()
    applied = store.apply(prepared.batch)
    assert applied.created == 6
    assert store.count("dim_products") == 0
    assert store.count("map_source_products") == 0
    assert store.count("fact_offers_current") == 1
    assert store.count("fact_offer_history") == 1
    assert store.count("fact_scrape_runs") == 1
    assert store.count("fact_quality_events") == 1


def test_price_change_writes_current_and_full_affected_history():
    state = InMemoryCommercialState()
    first = validated(run_id="run-1", observed_at=BASE, price="40")
    second = validated(run_id="run-2", observed_at=BASE + timedelta(days=1), price="38")
    prepare(state, first)

    prepared = prepare(state, second)

    assert prepared.apply_result.current_changed == 1
    assert "dim_products" not in prepared.table_row_counts
    assert "map_source_products" not in prepared.table_row_counts
    assert prepared.table_row_counts["fact_offers_current"] == 1
    assert prepared.table_row_counts["fact_offer_history"] == 2
    history_rows = prepared.batch.rows["fact_offer_history"]
    assert history_rows[0]["closed_by_scrape_run_id"] == "run-2"
    assert history_rows[1]["opened_by_scrape_run_id"] == "run-2"
    assert history_rows[1]["valid_to_utc"] is None


def test_rejected_run_records_run_and_quality_without_touching_commercial_tables():
    state = InMemoryCommercialState()
    item = validated(run_id="run-rejected", observed_at=BASE)
    event = quality_event("run-rejected")
    rejected = decision(
        "run-rejected",
        status=RunStatus.REJECTED,
        accepted=False,
        decided_at=BASE + timedelta(minutes=1),
    )

    prepared = prepare_new_run_persistence(
        state,
        rejected,
        (item,),
        supermarket_id="demo",
        location_id="demo_sps",
        started_at_utc=BASE - timedelta(seconds=10),
        finished_at_utc=BASE + timedelta(seconds=10),
        products_observed=1,
        offers_observed=1,
        quality_events=(event,),
        catalog=demo_catalog(),
    )

    assert prepared.apply_result.commercial_update_allowed is False
    assert prepared.apply_result.offers_ignored == 1
    assert "dim_products" not in prepared.table_row_counts
    assert "map_source_products" not in prepared.table_row_counts
    assert prepared.table_row_counts["fact_offers_current"] == 0
    assert prepared.table_row_counts["fact_offer_history"] == 0
    assert prepared.table_row_counts["fact_scrape_runs"] == 1
    assert prepared.table_row_counts["fact_quality_events"] == 1
    assert state.current_count == 0
    assert state.applied_run_count == 1


def test_la_colonia_snapshot_can_persist_while_future_extraction_stays_disabled():
    state = InMemoryCommercialState()
    item = validated(
        run_id="run-lc",
        observed_at=BASE,
        supermarket_id="la_colonia",
        location_id="la_colonia_sps",
        location_status=LocationStatus.CONFIRMED,
    )

    prepared = prepare_new_run_persistence(
        state,
        decision("run-lc", decided_at=BASE + timedelta(minutes=1)),
        (item,),
        supermarket_id="la_colonia",
        location_id="la_colonia_sps",
        started_at_utc=BASE - timedelta(seconds=10),
        finished_at_utc=BASE + timedelta(seconds=10),
        products_observed=1,
        offers_observed=1,
        catalog=DEFAULT_LOCATION_CATALOG,
    )

    assert prepared.apply_result.commercial_update_allowed is True
    assert prepared.table_row_counts["fact_offers_current"] == 1
    locations = prepared.batch.rows["cfg_locations"]
    sps = next(row for row in locations if row["location_id"] == "la_colonia_sps")
    assert sps["extraction_enabled"] is False
    assert DEFAULT_LOCATION_CATALOG.extraction_block_reason("la_colonia_sps") == "extraction_disabled"


def test_quality_event_identity_mismatch_fails_before_state_mutation():
    state = InMemoryCommercialState()
    item = validated(run_id="run-1", observed_at=BASE)
    event = quality_event("other-run")

    with pytest.raises(
        CommercialPersistencePreparationError,
        match="quality_event_run_mismatch",
    ):
        prepare(state, item, events=(event,))

    assert state.current_count == 0
    assert state.applied_run_count == 0


def test_invalid_run_metadata_fails_before_state_mutation():
    state = InMemoryCommercialState()
    item = validated(run_id="run-1", observed_at=BASE)

    with pytest.raises(
        CommercialPersistencePreparationError,
        match="run_metadata_invalid",
    ):
        prepare_new_run_persistence(
            state,
            decision("run-1", decided_at=BASE + timedelta(minutes=1)),
            (item,),
            supermarket_id="demo",
            location_id="demo_sps",
            started_at_utc=BASE + timedelta(minutes=2),
            finished_at_utc=BASE,
            products_observed=1,
            offers_observed=1,
            catalog=demo_catalog(),
        )

    assert state.current_count == 0
    assert state.applied_run_count == 0


def test_running_decision_is_rejected_before_state_mutation():
    state = InMemoryCommercialState()
    item = validated(run_id="run-running", observed_at=BASE)

    with pytest.raises(CommercialPersistencePreparationError, match="run_not_final"):
        prepare_new_run_persistence(
            state,
            decision(
                "run-running",
                status=RunStatus.RUNNING,
                accepted=False,
                decided_at=BASE + timedelta(minutes=1),
            ),
            (item,),
            supermarket_id="demo",
            location_id="demo_sps",
            started_at_utc=BASE - timedelta(seconds=10),
            finished_at_utc=BASE + timedelta(seconds=10),
            products_observed=1,
            offers_observed=1,
            catalog=demo_catalog(),
        )

    assert state.applied_run_count == 0


def test_known_replay_is_not_converted_into_a_second_persistence_batch():
    state = InMemoryCommercialState()
    item = validated(run_id="run-1", observed_at=BASE)
    prepare(state, item)
    before = state.current(item.offer.offer_id)

    with pytest.raises(
        CommercialPersistencePreparationError,
        match="replayed_run_requires_durable_reconciliation",
    ):
        prepare(state, item)

    after = state.current(item.offer.offer_id)
    assert before == after
    assert state.applied_run_count == 1
