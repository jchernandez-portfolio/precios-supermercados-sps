from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from precios_supermercados.commercial_state import (
    CommercialReplayConflict,
    CommercialRunDecision,
    InMemoryCommercialState,
)
from precios_supermercados.enums import (
    AvailabilityStatus,
    LocationStatus,
    RunStatus,
    SourceKeyType,
)
from precios_supermercados.google_sheets_adapter import (
    GoogleSheetsAdapterError,
    WorkbookSnapshot,
    snapshot_row_counts,
)
from precios_supermercados.google_sheets_plan import parse_spreadsheet_metadata
from precios_supermercados.google_sheets_state_loader import (
    GoogleSheetsStateLoaderError,
    load_commercial_state_from_google_sheets,
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
    FACT_OFFER_HISTORY,
    FACT_OFFERS_CURRENT,
    FACT_SCRAPE_RUNS,
    current_offer_row,
    history_offer_row,
)
from precios_supermercados.tabular_records import ScrapeRunRecord, scrape_run_row
from precios_supermercados.tabular_store import InMemoryTabularStore, TabularBatch

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
        evidence="fixture-fixed-city",
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
        source_name="Producto demo 1 unidad",
        product_url="https://example.invalid/p/001",
        normalized_name="Producto demo",
        currency="HNL",
        current_price=Decimal(price),
        reported_regular_price=Decimal("45"),
        is_promotion=False,
        availability=AvailabilityStatus.IN_STOCK,
        location_status=LocationStatus.CONFIRMED,
        location_evidence="fixture-fixed-city",
        location_confidence=Decimal("1"),
        observed_at_utc=observed_at,
        scrape_run_id=run_id,
        extractor_version="test",
        schema_version="1",
        source_url="https://example.invalid/catalog",
        normalized_brand="Marca",
        category="Demo",
        variant="Base",
        unit_count=1,
        content_per_unit=Decimal("1"),
        measurement_unit="unit",
        total_content=Decimal("1"),
    )
    return ValidatedOffer(
        offer=offer,
        state_hash=generate_state_hash(offer),
        validated_at_utc=observed_at + timedelta(seconds=5),
    )


def decision(item: ValidatedOffer) -> CommercialRunDecision:
    return CommercialRunDecision(
        scrape_run_id=item.offer.scrape_run_id,
        run_status=RunStatus.SUCCESS,
        catalog_accepted=True,
        decided_at_utc=item.validated_at_utc + timedelta(seconds=5),
    )


def run_record(item: ValidatedOffer, *, evidence_digit: str) -> ScrapeRunRecord:
    return ScrapeRunRecord(
        scrape_run_id=item.offer.scrape_run_id,
        supermarket_id="demo",
        location_id="demo_sps",
        run_status=RunStatus.SUCCESS,
        catalog_accepted=True,
        started_at_utc=item.offer.observed_at_utc - timedelta(seconds=5),
        finished_at_utc=item.validated_at_utc + timedelta(seconds=5),
        products_observed=1,
        offers_observed=1,
        current_created=1 if item.offer.scrape_run_id == "run-1" else 0,
        current_changed=0 if item.offer.scrape_run_id == "run-1" else 1,
        current_confirmed=0,
        offers_ignored=0,
        quality_event_count=0,
        run_evidence_id="crev1_" + evidence_digit * 64,
    )


def workbook_snapshot(*, include_runs: bool = True) -> tuple[WorkbookSnapshot, ValidatedOffer]:
    cfg = catalog()
    first = validated(run_id="run-1", observed_at=BASE, price="40")
    second = validated(
        run_id="run-2",
        observed_at=BASE + timedelta(days=1),
        price="38",
    )
    state = InMemoryCommercialState()
    state.apply_run(decision(first), (first,))
    state.apply_run(decision(second), (second,))

    current = state.current(first.offer.offer_id)
    history = state.history(first.offer.offer_id)
    assert current is not None

    rows = {
        FACT_OFFERS_CURRENT.name: (current_offer_row(current, cfg),),
        FACT_OFFER_HISTORY.name: tuple(
            history_offer_row(period, cfg) for period in history
        ),
    }
    if include_runs:
        rows[FACT_SCRAPE_RUNS.name] = (
            scrape_run_row(run_record(first, evidence_digit="1"), cfg),
            scrape_run_row(run_record(second, evidence_digit="2"), cfg),
        )

    store = InMemoryTabularStore()
    store.apply(TabularBatch(rows=rows))
    metadata = parse_spreadsheet_metadata({"sheets": []})
    return (
        WorkbookSnapshot(
            metadata=metadata,
            store=store,
            row_counts=snapshot_row_counts(store),
            requested_ranges=(
                "'fact_offers_current'!A:AX",
                "'fact_offer_history'!A:BE",
                "'fact_scrape_runs'!A:O",
            ),
        ),
        second,
    )


class SnapshotAdapter:
    def __init__(self, snapshot: WorkbookSnapshot):
        self.snapshot = snapshot
        self.load_calls = 0
        self.write_calls = 0

    def load_snapshot(self):
        self.load_calls += 1
        return self.snapshot

    def apply(self, _batch):  # pragma: no cover - no debe invocarse
        self.write_calls += 1
        raise AssertionError("read-only loader attempted write")


class FailingAdapter:
    def load_snapshot(self):
        raise GoogleSheetsAdapterError("managed_sheet_header_mismatch")


def test_loader_restores_durable_state_and_never_writes() -> None:
    snapshot, second = workbook_snapshot()
    adapter = SnapshotAdapter(snapshot)

    loaded = load_commercial_state_from_google_sheets(adapter)

    assert adapter.load_calls == 1
    assert adapter.write_calls == 0
    assert loaded.current_count == 1
    assert loaded.history_period_count == 2
    assert loaded.terminal_run_count == 2
    assert loaded.row_counts[FACT_OFFERS_CURRENT.name] == 1
    restored_current = loaded.state.current(second.offer.offer_id)
    assert restored_current is not None
    assert restored_current.validated_offer.offer.current_price == Decimal("38")


def test_restored_state_reserves_old_run_ids_but_accepts_new_run() -> None:
    snapshot, second = workbook_snapshot()
    loaded = load_commercial_state_from_google_sheets(SnapshotAdapter(snapshot))

    with pytest.raises(CommercialReplayConflict):
        loaded.state.apply_run(decision(second), (second,))

    third = validated(
        run_id="run-3",
        observed_at=BASE + timedelta(days=2),
        price="37",
    )
    result = loaded.state.apply_run(decision(third), (third,))

    assert result.current_changed == 1
    assert result.replayed is False
    assert loaded.state.current_count == 1
    assert len(loaded.state.history(third.offer.offer_id)) == 3


def test_loader_fails_closed_when_referenced_runs_are_missing() -> None:
    snapshot, _second = workbook_snapshot(include_runs=False)

    with pytest.raises(
        GoogleSheetsStateLoaderError,
        match="commercial_state_restore_failed",
    ):
        load_commercial_state_from_google_sheets(SnapshotAdapter(snapshot))


def test_loader_accepts_empty_initialized_state() -> None:
    store = InMemoryTabularStore()
    snapshot = WorkbookSnapshot(
        metadata=parse_spreadsheet_metadata({"sheets": []}),
        store=store,
        row_counts=snapshot_row_counts(store),
        requested_ranges=(),
    )

    loaded = load_commercial_state_from_google_sheets(SnapshotAdapter(snapshot))

    assert loaded.current_count == 0
    assert loaded.history_period_count == 0
    assert loaded.terminal_run_count == 0
    assert loaded.state.current_count == 0


def test_loader_sanitizes_adapter_read_failures() -> None:
    with pytest.raises(
        GoogleSheetsStateLoaderError,
        match="workbook_snapshot_read_failed",
    ) as exc_info:
        load_commercial_state_from_google_sheets(FailingAdapter())

    assert "managed_sheet_header_mismatch" not in str(exc_info.value)


def test_loader_rejects_objects_without_snapshot_capability() -> None:
    with pytest.raises(
        GoogleSheetsStateLoaderError,
        match="workbook_snapshot_loader_invalid",
    ):
        load_commercial_state_from_google_sheets(object())
