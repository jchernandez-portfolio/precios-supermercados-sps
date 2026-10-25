from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from precios_supermercados.bigquery_adapter import (
    BigQueryAdapter,
    BigQueryAdapterError,
    BigQueryReplayConflict,
    FakeBigQueryClient,
)
from precios_supermercados.bigquery_contract import (
    BIGQUERY_TABLE_NAMES,
    BIGQUERY_TABLE_SPECS,
    INVENTARIO_HISTORICO,
    PRECIOS_HISTORICOS,
    PRODUCTOS,
    QUALITY_EVENTS,
    SCRAPE_RUNS,
)
from precios_supermercados.bigquery_persistence import (
    BigQueryWritePlan,
    build_bigquery_write_plan,
)
from precios_supermercados.commercial_persistence_batch import prepare_new_run_persistence
from precios_supermercados.commercial_state import CommercialRunDecision, InMemoryCommercialState
from precios_supermercados.enums import (
    AvailabilityStatus,
    LocationStatus,
    QualityEventCategory,
    QualityEventSeverity,
    RunStatus,
    SourceKeyType,
)
from precios_supermercados.google_bigquery_client import GoogleCloudBigQueryClient
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
from precios_supermercados.storage_contract import (
    ACTIVE_STORAGE_BACKEND,
    ACTIVE_STORAGE_TABLE_NAMES,
)
from precios_supermercados.tabular_records import QualityEventRecord

BASE = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
EVIDENCE_ID = "crev1_" + "a" * 64


def catalog():
    return build_location_catalog(
        (
            SupermarketConfig(
                supermarket_id="demo",
                supermarket_name="Super Demo",
                location_selection_mode=LocationSelectionMode.FIXED_SINGLE_CITY,
            ),
        ),
        (
            LocationConfig(
                location_id="demo_sps",
                supermarket_id="demo",
                city_id="sps",
                city_name="San Pedro Sula",
                granularity=LocationGranularity.CITY,
                is_available=True,
                in_scope=True,
                extraction_enabled=True,
                evidence="bigquery_test",
            ),
        ),
    )


def validated(
    *,
    run_id: str,
    observed_at: datetime,
    price: str | None = "40",
    availability: AvailabilityStatus = AvailabilityStatus.IN_STOCK,
    brand: str = "Marca",
) -> ValidatedOffer:
    source_key = "SKU-001"
    source_product_id = generate_source_product_id("demo", SourceKeyType.SKU, source_key)
    offer = NormalizedOffer(
        supermarket_id="demo",
        location_id="demo_sps",
        source_product_id=source_product_id,
        source_key_type=SourceKeyType.SKU,
        source_key=source_key,
        product_id=f"prod_pending_{source_product_id}",
        offer_id=generate_offer_id("demo", "demo_sps", source_product_id),
        source_name="Producto demo 1 unidad",
        product_url="https://example.invalid/p/001",
        normalized_name="Producto demo",
        currency="HNL",
        is_promotion=False,
        availability=availability,
        location_status=LocationStatus.INFERRED,
        location_evidence="fixture",
        location_confidence=Decimal("1"),
        observed_at_utc=observed_at,
        scrape_run_id=run_id,
        extractor_version="test",
        schema_version="1",
        source_url="https://example.invalid/catalog",
        normalized_brand=brand,
        category="Demo",
        subcategory="Demo",
        variant="Base",
        unit_count=1,
        content_per_unit=Decimal("1"),
        measurement_unit="unit",
        total_content=Decimal("1"),
        current_price=None if price is None else Decimal(price),
    )
    return ValidatedOffer(
        offer=offer,
        state_hash=generate_state_hash(offer),
        validated_at_utc=observed_at,
    )


def quality_event(run_id: str) -> QualityEventRecord:
    return QualityEventRecord.deterministic(
        scrape_run_id=run_id,
        supermarket_id="demo",
        location_id="demo_sps",
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
    accepted: bool = True,
    status: RunStatus = RunStatus.SUCCESS,
    events: tuple[QualityEventRecord, ...] = (),
    run_evidence_id: str | None = EVIDENCE_ID,
):
    return prepare_new_run_persistence(
        state,
        CommercialRunDecision(
            scrape_run_id=item.offer.scrape_run_id,
            run_status=status,
            catalog_accepted=accepted,
            decided_at_utc=item.validated_at_utc + timedelta(minutes=1),
        ),
        (item,),
        supermarket_id="demo",
        location_id="demo_sps",
        started_at_utc=item.offer.observed_at_utc - timedelta(seconds=10),
        finished_at_utc=item.offer.observed_at_utc + timedelta(seconds=10),
        products_observed=1,
        offers_observed=1,
        quality_events=events,
        run_evidence_id=run_evidence_id if accepted else None,
        catalog=catalog(),
    )


def bootstrapped():
    client = FakeBigQueryClient()
    adapter = BigQueryAdapter(client, dataset_id="precios_sps")
    adapter.bootstrap()
    return client, adapter


def test_bigquery_is_the_only_active_physical_contract():
    assert ACTIVE_STORAGE_BACKEND == "bigquery"
    assert ACTIVE_STORAGE_TABLE_NAMES == BIGQUERY_TABLE_NAMES
    assert len(BIGQUERY_TABLE_SPECS) == 9
    assert PRECIOS_HISTORICOS.partition_field == "observed_at_utc"
    assert INVENTARIO_HISTORICO.partition_field == "observed_at_utc"
    assert SCRAPE_RUNS.partition_field == "started_at_utc"
    assert PRECIOS_HISTORICOS.clustering_fields == (
        "supermarket_id",
        "location_id",
        "source_product_id",
    )


def test_bootstrap_creates_all_tables_offline_with_exact_specs():
    client, _ = bootstrapped()
    assert client.datasets == {"precios_sps"}
    assert {
        table for dataset, table in client.table_specs if dataset == "precios_sps"
    } == set(BIGQUERY_TABLE_NAMES)
    for spec in BIGQUERY_TABLE_SPECS:
        assert client.table_specs[("precios_sps", spec.name)] == spec


def test_first_accepted_load_materializes_observations_mapping_run_and_quality():
    state = InMemoryCommercialState()
    item = validated(run_id="run-1", observed_at=BASE)
    prepared = prepare(state, item, events=(quality_event("run-1"),))
    plan = build_bigquery_write_plan(prepared)
    client, adapter = bootstrapped()

    result = adapter.apply(plan)

    assert result.exact_run_replay is False
    assert client.count("precios_sps", PRODUCTOS.name) == 1
    assert client.count("precios_sps", PRECIOS_HISTORICOS.name) == 1
    assert client.count("precios_sps", INVENTARIO_HISTORICO.name) == 1
    assert client.count("precios_sps", SCRAPE_RUNS.name) == 1
    assert client.count("precios_sps", QUALITY_EVENTS.name) == 1
    assert client.count("precios_sps", "product_mapping") == 1
    price = client.read_rows("precios_sps", PRECIOS_HISTORICOS.name)[0]
    assert price["current_price"] == "40"
    assert price["reported_regular_price"] is None
    assert "previous_price" not in price


def test_commercial_write_requires_bound_authority_evidence():
    state = InMemoryCommercialState()
    prepared = prepare(
        state,
        validated(run_id="run-no-evidence", observed_at=BASE),
        run_evidence_id=None,
    )
    plan = build_bigquery_write_plan(prepared)
    _, adapter = bootstrapped()

    with pytest.raises(BigQueryAdapterError, match="authoritative_run_evidence_required"):
        adapter.apply(plan)


def test_commercial_write_rejects_non_bound_evidence_shape():
    state = InMemoryCommercialState()
    prepared = prepare(
        state,
        validated(run_id="run-bad-evidence", observed_at=BASE),
        run_evidence_id="manual-boolean-is-not-evidence",
    )
    plan = build_bigquery_write_plan(prepared)
    _, adapter = bootstrapped()

    with pytest.raises(BigQueryAdapterError, match="authoritative_run_evidence_required"):
        adapter.apply(plan)


def test_exact_run_replay_is_noop_and_conflicting_replay_fails_closed():
    state = InMemoryCommercialState()
    plan = build_bigquery_write_plan(
        prepare(state, validated(run_id="run-1", observed_at=BASE))
    )
    client, adapter = bootstrapped()
    adapter.apply(plan)
    before = {name: client.count("precios_sps", name) for name in BIGQUERY_TABLE_NAMES}

    replay = adapter.apply(plan)
    assert replay.exact_run_replay is True
    assert {name: client.count("precios_sps", name) for name in BIGQUERY_TABLE_NAMES} == before

    conflicting_rows = {name: tuple(rows) for name, rows in plan.rows.items()}
    changed_run = dict(conflicting_rows[SCRAPE_RUNS.name][0])
    changed_run["run_fingerprint"] = "f" * 64
    conflicting_rows[SCRAPE_RUNS.name] = (changed_run,)
    conflict = BigQueryWritePlan(
        scrape_run_id=plan.scrape_run_id,
        run_fingerprint="f" * 64,
        rows=conflicting_rows,
    )
    with pytest.raises(BigQueryReplayConflict, match="conflicting_run_replay"):
        adapter.apply(conflict)


def test_post_write_fingerprint_detects_concurrent_conflicting_winner():
    class ConcurrentWinnerClient(FakeBigQueryClient):
        def apply_atomic(self, dataset_id, rows, *, immutable_tables):
            run = dict(rows[SCRAPE_RUNS.name][0])
            run["run_fingerprint"] = "f" * 64
            spec = self._spec(dataset_id, SCRAPE_RUNS.name)
            key = self._key(spec, run)
            self._rows[(dataset_id, SCRAPE_RUNS.name)][key] = run
            return 0, 0, sum(len(values) for values in rows.values())

    state = InMemoryCommercialState()
    plan = build_bigquery_write_plan(
        prepare(state, validated(run_id="run-race", observed_at=BASE))
    )
    client = ConcurrentWinnerClient()
    adapter = BigQueryAdapter(client, dataset_id="precios_sps")
    adapter.bootstrap()

    with pytest.raises(BigQueryReplayConflict, match="conflicting_run_replay"):
        adapter.apply(plan)


def test_google_client_immutable_sql_treats_identical_key_as_replay():
    spec = SCRAPE_RUNS
    join = GoogleCloudBigQueryClient._join_predicate(spec, "T", "S")
    guard = GoogleCloudBigQueryClient._immutable_guard_sql(
        spec=spec,
        target="project.dataset.scrape_runs",
        staging_ref="project.dataset._stg_scrape_runs",
        join=join,
    )
    insert = GoogleCloudBigQueryClient._immutable_insert_sql(
        spec=spec,
        target="project.dataset.scrape_runs",
        staging_ref="project.dataset._stg_scrape_runs",
        join=join,
    )

    assert "IS DISTINCT FROM" in guard
    assert "immutable_conflict:scrape_runs" in guard
    assert "WHERE NOT EXISTS" in insert


def test_google_client_atomic_job_id_serializes_same_run_per_attempt_slot():
    first = GoogleCloudBigQueryClient._atomic_job_id("precios_sps", "run-1", 0)
    assert first == GoogleCloudBigQueryClient._atomic_job_id("precios_sps", "run-1", 0)
    assert first != GoogleCloudBigQueryClient._atomic_job_id("precios_sps", "run-1", 1)
    assert first != GoogleCloudBigQueryClient._atomic_job_id("precios_sps", "run-2", 0)
    assert first != GoogleCloudBigQueryClient._atomic_job_id("otro_dataset", "run-1", 0)


def test_partial_failure_rolls_back_every_target_table():
    state = InMemoryCommercialState()
    plan = build_bigquery_write_plan(
        prepare(
            state,
            validated(run_id="run-1", observed_at=BASE),
            events=(quality_event("run-1"),),
        )
    )
    client, adapter = bootstrapped()
    client.fail_after_mutations = 3

    with pytest.raises(BigQueryAdapterError, match="injected_partial_failure"):
        adapter.apply(plan)

    assert all(client.count("precios_sps", name) == 0 for name in BIGQUERY_TABLE_NAMES)


def test_rejected_run_keeps_ledger_and_quality_without_commercial_contamination():
    state = InMemoryCommercialState()
    item = validated(run_id="run-rejected", observed_at=BASE)
    prepared = prepare(
        state,
        item,
        accepted=False,
        status=RunStatus.REJECTED,
        events=(quality_event("run-rejected"),),
    )
    plan = build_bigquery_write_plan(prepared)
    client, adapter = bootstrapped()
    adapter.apply(plan)

    assert client.count("precios_sps", PRODUCTOS.name) == 0
    assert client.count("precios_sps", PRECIOS_HISTORICOS.name) == 0
    assert client.count("precios_sps", INVENTARIO_HISTORICO.name) == 0
    assert client.count("precios_sps", "product_mapping") == 0
    assert client.count("precios_sps", SCRAPE_RUNS.name) == 1
    assert client.count("precios_sps", QUALITY_EVENTS.name) == 1
    run = client.read_rows("precios_sps", SCRAPE_RUNS.name)[0]
    assert run["catalog_accepted"] is False
    assert run["commercial_update_allowed"] is False


def test_unknown_inventory_stays_unknown_without_invented_quantity_or_seller():
    state = InMemoryCommercialState()
    item = validated(
        run_id="run-unknown",
        observed_at=BASE,
        price=None,
        availability=AvailabilityStatus.UNKNOWN,
    )
    plan = build_bigquery_write_plan(prepare(state, item))
    client, adapter = bootstrapped()
    adapter.apply(plan)

    inventory = client.read_rows("precios_sps", INVENTARIO_HISTORICO.name)[0]
    assert inventory["availability"] == "unknown"
    assert inventory["available_quantity_observed"] is None
    assert inventory["availability_evidence"] is None
    assert inventory["seller_id"] is None
    assert inventory["quantity_is_exact"] is False


def test_observational_history_reconciles_with_period_engine_and_read_back():
    state = InMemoryCommercialState()
    client, adapter = bootstrapped()

    first = validated(run_id="run-1", observed_at=BASE, price="40")
    prepared1 = prepare(state, first)
    adapter.apply(build_bigquery_write_plan(prepared1))

    same = validated(run_id="run-2", observed_at=BASE + timedelta(days=1), price="40")
    prepared2 = prepare(state, same)
    assert prepared2.apply_result.current_confirmed == 1
    adapter.apply(build_bigquery_write_plan(prepared2))
    assert len(state.history(first.offer.offer_id)) == 1
    assert client.count("precios_sps", PRECIOS_HISTORICOS.name) == 2

    changed = validated(run_id="run-3", observed_at=BASE + timedelta(days=2), price="38")
    prepared3 = prepare(state, changed)
    assert prepared3.apply_result.current_changed == 1
    adapter.apply(build_bigquery_write_plan(prepared3))
    assert len(state.history(first.offer.offer_id)) == 2
    assert client.count("precios_sps", PRECIOS_HISTORICOS.name) == 3

    restored = adapter.read_back(supermarket_id="demo", location_id="demo_sps")
    assert len(restored.products) == 1
    assert len(restored.latest_prices) == 1
    assert restored.latest_prices[0]["current_price"] == "38"
    assert len(restored.latest_inventory) == 1
    assert [row["scrape_run_id"] for row in restored.runs] == ["run-1", "run-2", "run-3"]


def test_product_upsert_and_explicit_normalization_override_are_idempotent():
    state = InMemoryCommercialState()
    client, adapter = bootstrapped()
    override = {
        "override_id": "ovr-1",
        "supermarket_id": "demo",
        "source_product_id": validated(run_id="tmp", observed_at=BASE).offer.source_product_id,
        "source_signature": "sig-v1",
        "field_name": "normalized_brand",
        "source_value": "Mrc",
        "override_value": "Marca",
        "reason": "manual_verified",
        "status": "active",
        "created_at_utc": BASE,
        "updated_at_utc": BASE,
    }
    first = prepare(state, validated(run_id="run-1", observed_at=BASE, brand="Marca"))
    plan1 = build_bigquery_write_plan(first, normalization_overrides=(override,))
    adapter.apply(plan1)
    adapter.apply(plan1)
    assert client.count("precios_sps", "normalization_overrides") == 1

    second = prepare(
        state,
        validated(
            run_id="run-2",
            observed_at=BASE + timedelta(days=1),
            brand="Marca Corregida",
        ),
    )
    adapter.apply(build_bigquery_write_plan(second))
    product = client.read_rows("precios_sps", PRODUCTOS.name)[0]
    assert product["normalized_brand"] == "Marca Corregida"
    assert client.count("precios_sps", PRODUCTOS.name) == 1
