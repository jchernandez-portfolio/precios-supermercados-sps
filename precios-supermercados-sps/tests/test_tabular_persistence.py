from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from precios_supermercados.commercial_state import (
    CommercialRunDecision,
    InMemoryCommercialState,
)
from precios_supermercados.enums import AvailabilityStatus, LocationStatus, RunStatus, SourceKeyType
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
from precios_supermercados.tabular_persistence import (
    CFG_LOCATIONS,
    CFG_SUPERMARKETS,
    DIM_PRODUCTS,
    FACT_OFFER_HISTORY,
    FACT_OFFERS_CURRENT,
    MAP_SOURCE_PRODUCTS,
    TABLE_SPECS,
    TabularPersistenceError,
    current_offer_row,
    history_offer_row,
    location_config_rows,
    supermarket_config_rows,
    validate_offer_location_for_persistence,
)

BASE_TIME = datetime(2026, 8, 22, 18, 0, tzinfo=UTC)


def fixed_catalog(*, supermarket_id: str = "demo", name: str = "Demo"):
    supermarket = SupermarketConfig(
        supermarket_id=supermarket_id,
        supermarket_name=name,
        location_selection_mode=LocationSelectionMode.FIXED_SINGLE_CITY,
    )
    location = LocationConfig(
        location_id=f"{supermarket_id}_sps",
        supermarket_id=supermarket_id,
        city_id="sps",
        city_name="San Pedro Sula",
        granularity=LocationGranularity.CITY,
        is_available=True,
        in_scope=True,
        extraction_enabled=True,
        evidence="configured_single_city",
    )
    return build_location_catalog((supermarket,), (location,))


def multi_catalog():
    supermarket = SupermarketConfig(
        supermarket_id="multi",
        supermarket_name="Multi",
        location_selection_mode=LocationSelectionMode.SOURCE_SELECTION_REQUIRED,
    )
    location = LocationConfig(
        location_id="multi_sps",
        supermarket_id="multi",
        city_id="sps",
        city_name="San Pedro Sula",
        granularity=LocationGranularity.CITY,
        is_available=True,
        in_scope=True,
        extraction_enabled=True,
        technical_binding_confirmed=True,
        source_location_key="city-source-001",
        evidence="source_binding_test",
    )
    return build_location_catalog((supermarket,), (location,))


def make_validated(
    *,
    run_id: str,
    observed_at: datetime,
    supermarket_id: str = "demo",
    location_id: str = "demo_sps",
    location_status: LocationStatus = LocationStatus.INFERRED,
    price: str = "30.00",
    suffix: str = "001",
) -> ValidatedOffer:
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
        source_name="Coca Cola Original 2 Litros",
        product_url="https://example.invalid/coca-cola-2l",
        normalized_name="Coca-Cola Original",
        currency="HNL",
        is_promotion=True,
        availability=AvailabilityStatus.IN_STOCK,
        location_status=location_status,
        location_evidence="configured_single_city",
        location_confidence=Decimal("1"),
        observed_at_utc=observed_at,
        scrape_run_id=run_id,
        extractor_version="test",
        schema_version="1",
        source_url="https://example.invalid/graphql",
        normalized_brand="Coca-Cola",
        category="Bebidas",
        subcategory="Refrescos",
        variant="Original",
        unit_count=1,
        content_per_unit=Decimal("2000"),
        measurement_unit="ml",
        total_content=Decimal("2000"),
        current_price=Decimal(price),
        reported_regular_price=Decimal("35.00"),
        source_sku=f"REF-{suffix}",
        source_brand="Coca Cola",
        source_presentation="2 Litros",
        image_url="https://example.invalid/coca-cola.jpg",
        unit_price=Decimal(price) / Decimal("2"),
        unit_price_basis="L",
    )
    return ValidatedOffer(
        offer=offer,
        state_hash=generate_state_hash(offer),
        validated_at_utc=observed_at,
    )


def apply_initial(validated: ValidatedOffer):
    state = InMemoryCommercialState()
    decision = CommercialRunDecision(
        scrape_run_id=validated.offer.scrape_run_id,
        run_status=RunStatus.SUCCESS,
        catalog_accepted=True,
        decided_at_utc=validated.offer.observed_at_utc + timedelta(minutes=1),
    )
    state.apply_run(decision, (validated,))
    current = state.current(validated.offer.offer_id)
    history = state.history(validated.offer.offer_id)
    assert current is not None
    assert len(history) == 1
    return state, current, history[0]


def test_table_specs_are_shared_not_one_table_per_supermarket() -> None:
    assert set(TABLE_SPECS) == {
        "cfg_supermarkets",
        "cfg_locations",
        "dim_products",
        "map_source_products",
        "fact_offers_current",
        "fact_offer_history",
        "fact_scrape_runs",
        "fact_quality_events",
    }
    assert DIM_PRODUCTS.primary_key == ("product_id",)
    assert MAP_SOURCE_PRODUCTS.primary_key == ("source_product_id",)
    assert FACT_OFFERS_CURRENT.primary_key == ("offer_id",)
    assert FACT_OFFER_HISTORY.primary_key == ("offer_history_id",)
    assert all("la_colonia" not in name for name in TABLE_SPECS)


def test_configuration_rows_put_all_locations_in_common_tables() -> None:
    supermarkets = supermarket_config_rows(DEFAULT_LOCATION_CATALOG)
    locations = location_config_rows(DEFAULT_LOCATION_CATALOG)

    assert len(supermarkets) == 1
    assert supermarkets[0]["supermarket_id"] == "la_colonia"
    assert set(supermarkets[0]) == set(CFG_SUPERMARKETS.columns)

    assert {row["location_id"] for row in locations} == {
        "la_colonia_sps",
        "la_colonia_tgu",
    }
    by_id = {row["location_id"]: row for row in locations}
    assert by_id["la_colonia_sps"]["in_scope"] is True
    assert by_id["la_colonia_tgu"]["in_scope"] is False
    assert all(row["extraction_enabled"] is False for row in locations)
    assert all(set(row) == set(CFG_LOCATIONS.columns) for row in locations)


def test_current_row_is_human_readable_and_preserves_source_plus_normalized_fields() -> None:
    catalog = fixed_catalog(name="Super Demo")
    validated = make_validated(run_id="run-1", observed_at=BASE_TIME)
    _state, current, _period = apply_initial(validated)

    row = current_offer_row(current, catalog)

    assert tuple(row) == FACT_OFFERS_CURRENT.columns
    assert row["supermarket_id"] == "demo"
    assert row["supermarket_name"] == "Super Demo"
    assert row["location_id"] == "demo_sps"
    assert row["city_id"] == "sps"
    assert row["city_name"] == "San Pedro Sula"
    assert row["source_name"] == "Coca Cola Original 2 Litros"
    assert row["normalized_name"] == "Coca-Cola Original"
    assert row["source_brand"] == "Coca Cola"
    assert row["normalized_brand"] == "Coca-Cola"
    assert row["source_presentation"] == "2 Litros"
    assert row["total_content"] == "2000"
    assert row["measurement_unit"] == "ml"
    assert row["current_price"] == "30"
    assert row["reported_regular_price"] == "35"
    assert row["is_promotion"] is True
    assert row["last_scrape_run_id"] == "run-1"
    assert row["observed_at_utc"] == "2026-08-22T18:00:00.000Z"


def test_history_row_opens_new_period_only_for_real_state_change() -> None:
    catalog = fixed_catalog()
    first = make_validated(run_id="run-1", observed_at=BASE_TIME, price="30")
    state, _current, first_period = apply_initial(first)
    assert history_offer_row(first_period, catalog)["change_type"] == "initial"

    second = make_validated(
        run_id="run-2",
        observed_at=BASE_TIME + timedelta(days=1),
        price="28",
    )
    state.apply_run(
        CommercialRunDecision(
            scrape_run_id="run-2",
            run_status=RunStatus.SUCCESS,
            catalog_accepted=True,
            decided_at_utc=second.offer.observed_at_utc + timedelta(minutes=1),
        ),
        (second,),
    )
    periods = state.history(second.offer.offer_id)
    assert len(periods) == 2

    closed = history_offer_row(periods[0], catalog)
    opened = history_offer_row(periods[1], catalog)
    assert tuple(opened) == FACT_OFFER_HISTORY.columns
    assert closed["valid_to_utc"] == "2026-08-23T18:00:00.000Z"
    assert opened["current_price"] == "28"
    assert opened["change_type"] == "price"
    assert opened["changed_fields_json"] == '["current_price"]'
    assert opened["valid_to_utc"] is None


def test_same_fact_table_accepts_rows_from_different_supermarkets() -> None:
    catalog_a = fixed_catalog(supermarket_id="a", name="Super A")
    catalog_b = fixed_catalog(supermarket_id="b", name="Super B")
    first = make_validated(
        run_id="run-a",
        observed_at=BASE_TIME,
        supermarket_id="a",
        location_id="a_sps",
        suffix="101",
    )
    second = make_validated(
        run_id="run-b",
        observed_at=BASE_TIME,
        supermarket_id="b",
        location_id="b_sps",
        suffix="202",
    )
    _, current_a, _ = apply_initial(first)
    _, current_b, _ = apply_initial(second)

    row_a = current_offer_row(current_a, catalog_a)
    row_b = current_offer_row(current_b, catalog_b)
    assert tuple(row_a) == tuple(row_b) == FACT_OFFERS_CURRENT.columns
    assert row_a["supermarket_id"] == "a"
    assert row_b["supermarket_id"] == "b"


def test_la_colonia_sps_is_persistible_while_future_extraction_stays_disabled() -> None:
    offer = make_validated(
        run_id="run-lc",
        observed_at=BASE_TIME,
        supermarket_id="la_colonia",
        location_id="la_colonia_sps",
        location_status=LocationStatus.CONFIRMED,
    ).offer

    location = validate_offer_location_for_persistence(
        offer,
        DEFAULT_LOCATION_CATALOG,
    )

    assert location.location_id == "la_colonia_sps"
    assert location.extraction_enabled is False
    assert DEFAULT_LOCATION_CATALOG.extraction_block_reason("la_colonia_sps") == "extraction_disabled"


def test_disabled_extraction_does_not_bypass_out_of_scope_location_gate() -> None:
    offer = make_validated(
        run_id="run-tgu",
        observed_at=BASE_TIME,
        supermarket_id="la_colonia",
        location_id="la_colonia_tgu",
        location_status=LocationStatus.CONFIRMED,
    ).offer

    with pytest.raises(TabularPersistenceError, match="location_out_of_scope"):
        validate_offer_location_for_persistence(offer, DEFAULT_LOCATION_CATALOG)


def test_unknown_or_mismatched_location_is_never_persistible() -> None:
    catalog = fixed_catalog()
    unknown = make_validated(
        run_id="run-unknown",
        observed_at=BASE_TIME,
        location_status=LocationStatus.UNKNOWN,
    ).offer
    with pytest.raises(TabularPersistenceError, match="location_status_unknown"):
        validate_offer_location_for_persistence(unknown, catalog)

    other_supermarket = make_validated(
        run_id="run-other",
        observed_at=BASE_TIME,
        supermarket_id="other",
        location_id="demo_sps",
    ).offer
    with pytest.raises(TabularPersistenceError, match="otro supermarket_id"):
        validate_offer_location_for_persistence(other_supermarket, catalog)


def test_multi_city_source_requires_confirmed_observation_even_with_known_source_key() -> None:
    catalog = multi_catalog()
    inferred = make_validated(
        run_id="run-multi-1",
        observed_at=BASE_TIME,
        supermarket_id="multi",
        location_id="multi_sps",
        location_status=LocationStatus.INFERRED,
    ).offer
    with pytest.raises(
        TabularPersistenceError,
        match="multi_location_offer_requires_confirmed_location",
    ):
        validate_offer_location_for_persistence(inferred, catalog)

    confirmed = make_validated(
        run_id="run-multi-2",
        observed_at=BASE_TIME,
        supermarket_id="multi",
        location_id="multi_sps",
        location_status=LocationStatus.CONFIRMED,
    ).offer
    assert validate_offer_location_for_persistence(confirmed, catalog).city_id == "sps"
