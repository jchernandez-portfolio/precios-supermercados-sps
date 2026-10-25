from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from precios_supermercados.commercial_state import (
    CommercialReplayConflict,
    CommercialRunDecision,
    InMemoryCommercialState,
)
from precios_supermercados.commercial_state_restore import (
    CommercialStateRestoreError,
    restore_commercial_state,
    scrape_run_from_durable_row,
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
from precios_supermercados.tabular_persistence import current_offer_row, history_offer_row
from precios_supermercados.tabular_records import ScrapeRunRecord, scrape_run_row
from precios_supermercados.tabular_rehydration import rehydrate_commercial_snapshot

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
        source_name="Producto Fuente",
        product_url="https://example.invalid/p/001",
        normalized_name="Producto Fuente",
        currency="HNL",
        current_price=Decimal(price),
        reported_regular_price=Decimal("45"),
        is_promotion=True,
        availability=AvailabilityStatus.IN_STOCK,
        location_status=LocationStatus.CONFIRMED,
        location_evidence="fixed_city",
        location_confidence=Decimal("1"),
        observed_at_utc=observed_at,
        scrape_run_id=run_id,
        extractor_version="test",
        schema_version="1",
        source_url="https://example.invalid/catalog",
        normalized_brand="Marca",
        category="Abarrotes",
        subcategory="Prueba",
        variant="Original",
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


def decision(item: ValidatedOffer, *, accepted: bool = True) -> CommercialRunDecision:
    return CommercialRunDecision(
        scrape_run_id=item.offer.scrape_run_id,
        run_status=RunStatus.SUCCESS if accepted else RunStatus.REJECTED,
        catalog_accepted=accepted,
        decided_at_utc=item.validated_at_utc + timedelta(seconds=5),
    )


def evidence(char: str) -> str:
    return "crev1_" + char * 64


def run_row(
    run_id: str,
    *,
    day: int,
    created: int = 0,
    changed: int = 0,
    confirmed: int = 0,
    accepted: bool = True,
    status: RunStatus = RunStatus.SUCCESS,
    evidence_id: str | None = None,
) -> dict[str, object]:
    record = ScrapeRunRecord(
        scrape_run_id=run_id,
        supermarket_id="demo",
        location_id="demo_sps",
        run_status=status,
        catalog_accepted=accepted,
        started_at_utc=BASE + timedelta(days=day),
        finished_at_utc=BASE + timedelta(days=day, minutes=1),
        products_observed=1,
        offers_observed=1,
        current_created=created,
        current_changed=changed,
        current_confirmed=confirmed,
        offers_ignored=0 if accepted else 1,
        quality_event_count=0,
        run_evidence_id=(
            evidence_id
            if evidence_id is not None
            else (evidence(str(day + 1)) if accepted else None)
        ),
    )
    return scrape_run_row(record, catalog())


def durable_snapshot():
    cfg = catalog()
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
    assert current is not None and len(history) == 2
    snapshot = rehydrate_commercial_snapshot(
        (current_offer_row(current, cfg),),
        tuple(history_offer_row(period, cfg) for period in history),
    )
    rows = (
        run_row("run-1", day=0, created=1),
        run_row("run-2", day=1, changed=1),
    )
    return snapshot, rows, first, second


def test_scrape_run_row_roundtrip_is_strict() -> None:
    row = run_row("run-1", day=0, created=1)

    restored = scrape_run_from_durable_row(row)

    assert restored.scrape_run_id == "run-1"
    assert restored.catalog_accepted is True
    assert restored.run_evidence_id == evidence("1")


def test_restore_rebuilds_state_and_can_continue_with_new_run() -> None:
    snapshot, rows, first, _second = durable_snapshot()

    restored = restore_commercial_state(snapshot, rows)

    assert restored.current_count == 1
    assert restored.history_period_count == 2
    assert restored.terminal_run_count == 2
    assert restored.state.applied_run_count == 2
    current = restored.state.current(first.offer.offer_id)
    assert current is not None
    assert current.validated_offer.offer.current_price == Decimal("38")

    third = validated(
        run_id="run-3",
        observed_at=BASE + timedelta(days=2),
        price="35",
    )
    result = restored.state.apply_run(decision(third), (third,))

    assert result.replayed is False
    assert result.current_changed == 1
    assert restored.state.current(first.offer.offer_id).validated_offer.offer.current_price == Decimal("35")  # type: ignore[union-attr]
    assert len(restored.state.history(first.offer.offer_id)) == 3


def test_restore_reserves_historical_run_ids_instead_of_guessing_fingerprint() -> None:
    snapshot, rows, _first, second = durable_snapshot()
    restored = restore_commercial_state(snapshot, rows)

    with pytest.raises(CommercialReplayConflict):
        restored.state.apply_run(decision(second), (second,))


def test_restore_requires_every_referenced_authoritative_run() -> None:
    snapshot, rows, _first, _second = durable_snapshot()

    with pytest.raises(
        CommercialStateRestoreError,
        match="referenced_terminal_run_missing",
    ):
        restore_commercial_state(snapshot, rows[1:])


def test_restore_rejects_referenced_run_without_authority_or_bound_evidence() -> None:
    snapshot, rows, _first, _second = durable_snapshot()

    rejected = [deepcopy(row) for row in rows]
    rejected[0]["catalog_accepted"] = False
    with pytest.raises(
        CommercialStateRestoreError,
        match="referenced_run_not_authoritative",
    ):
        restore_commercial_state(snapshot, rejected)

    missing_evidence = [deepcopy(row) for row in rows]
    missing_evidence[0]["run_evidence_id"] = None
    with pytest.raises(
        CommercialStateRestoreError,
        match="referenced_run_bound_evidence_missing",
    ):
        restore_commercial_state(snapshot, missing_evidence)


def test_restore_rejects_run_location_drift() -> None:
    snapshot, rows, _first, _second = durable_snapshot()
    drifted = [deepcopy(row) for row in rows]
    drifted[0]["location_id"] = "other_location"

    with pytest.raises(
        CommercialStateRestoreError,
        match="referenced_run_location_mismatch",
    ):
        restore_commercial_state(snapshot, drifted)


def test_extra_non_authoritative_terminal_run_is_reserved_too() -> None:
    snapshot, rows, _first, _second = durable_snapshot()
    rejected_row = run_row(
        "run-rejected",
        day=2,
        accepted=False,
        status=RunStatus.REJECTED,
    )
    restored = restore_commercial_state(snapshot, (*rows, rejected_row))

    rejected_offer = validated(
        run_id="run-rejected",
        observed_at=BASE + timedelta(days=2),
        price="37",
    )
    rejected_decision = CommercialRunDecision(
        scrape_run_id="run-rejected",
        run_status=RunStatus.REJECTED,
        catalog_accepted=False,
        decided_at_utc=rejected_offer.validated_at_utc + timedelta(seconds=5),
    )

    assert restored.terminal_run_count == 3
    with pytest.raises(CommercialReplayConflict):
        restored.state.apply_run(rejected_decision, (rejected_offer,))


def test_restore_rejects_duplicate_or_malformed_terminal_rows() -> None:
    snapshot, rows, _first, _second = durable_snapshot()

    with pytest.raises(CommercialStateRestoreError, match="durable_run_id_duplicate"):
        restore_commercial_state(snapshot, (*rows, deepcopy(rows[0])))

    malformed = deepcopy(rows[0])
    malformed.pop("run_evidence_id")
    with pytest.raises(
        CommercialStateRestoreError,
        match="durable_run_row_schema_mismatch",
    ):
        restore_commercial_state(snapshot, (malformed, rows[1]))
