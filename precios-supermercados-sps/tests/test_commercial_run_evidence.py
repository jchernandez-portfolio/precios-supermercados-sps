from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from precios_supermercados.commercial_run_evidence import (
    CommercialRunEvidenceError,
    derive_bound_run_evidence_id,
    reconcile_bound_durable_run_row,
)
from precios_supermercados.commercial_state import CommercialRunDecision
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
from precios_supermercados.models import NormalizedOffer, ValidatedOffer
from precios_supermercados.tabular_records import QualityEventRecord

BASE = datetime(2026, 8, 22, 20, 0, tzinfo=UTC)
AUTHORITY_EVIDENCE_ID = "verified-authority-evidence-001"


def decision(
    *,
    accepted: bool = True,
    status: RunStatus = RunStatus.SUCCESS,
    decided_at: datetime = BASE + timedelta(minutes=2),
) -> CommercialRunDecision:
    return CommercialRunDecision(
        scrape_run_id="run-authoritative-001",
        run_status=status,
        catalog_accepted=accepted,
        decided_at_utc=decided_at,
    )


def validated_offer(*, price: str = "40") -> ValidatedOffer:
    supermarket_id = "demo"
    location_id = "demo_sps"
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
        current_price=Decimal(price),
        is_promotion=False,
        availability=AvailabilityStatus.IN_STOCK,
        location_status=LocationStatus.CONFIRMED,
        location_evidence="fixture-confirmed",
        location_confidence=Decimal("1"),
        observed_at_utc=BASE + timedelta(seconds=30),
        scrape_run_id="run-authoritative-001",
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
    )
    return ValidatedOffer(
        offer=offer,
        state_hash=generate_state_hash(offer),
        validated_at_utc=BASE + timedelta(seconds=31),
    )


def quality_event(*, code: str = "catalog_verified") -> QualityEventRecord:
    return QualityEventRecord.deterministic(
        scrape_run_id="run-authoritative-001",
        supermarket_id="demo",
        location_id="demo_sps",
        category=QualityEventCategory.QUALITY,
        severity=QualityEventSeverity.INFO,
        event_code=code,
        observed_at_utc=BASE + timedelta(seconds=40),
        sequence=0,
    )


def binding(
    *,
    authority_evidence_id: str = AUTHORITY_EVIDENCE_ID,
    run_decision: CommercialRunDecision | None = None,
    offers: tuple[ValidatedOffer, ...] | None = None,
    events: tuple[QualityEventRecord, ...] | None = None,
) -> str:
    offer_values = offers if offers is not None else (validated_offer(),)
    event_values = events if events is not None else (quality_event(),)
    return derive_bound_run_evidence_id(
        authority_evidence_id=authority_evidence_id,
        decision=run_decision or decision(),
        offers=offer_values,
        supermarket_id="demo",
        location_id="demo_sps",
        started_at_utc=BASE,
        finished_at_utc=BASE + timedelta(minutes=1),
        products_observed=1,
        offers_observed=1,
        quality_events=event_values,
    )


def durable_row(*, run_evidence_id: str | None = None) -> dict[str, object]:
    return {
        "scrape_run_id": "run-authoritative-001",
        "supermarket_id": "demo",
        "location_id": "demo_sps",
        "run_status": "success",
        "catalog_accepted": True,
        "started_at_utc": "2026-08-22T20:00:00.000Z",
        "finished_at_utc": "2026-08-22T20:01:00.000Z",
        "products_observed": 1,
        "offers_observed": 1,
        "current_created": 1,
        "current_changed": 0,
        "current_confirmed": 0,
        "offers_ignored": 0,
        "quality_event_count": 1,
        "run_evidence_id": run_evidence_id or binding(),
    }


def test_binding_is_deterministic_opaque_and_versioned() -> None:
    first = binding()
    second = binding()

    assert first == second
    assert first.startswith("crev1_")
    assert len(first) == len("crev1_") + 64
    assert all(char in "0123456789abcdef" for char in first.removeprefix("crev1_"))
    assert AUTHORITY_EVIDENCE_ID not in first


def test_authority_evidence_id_is_bound_without_being_exposed() -> None:
    first = binding(authority_evidence_id="verified-authority-evidence-001")
    second = binding(authority_evidence_id="verified-authority-evidence-002")

    assert first != second
    assert "verified-authority" not in first
    assert "verified-authority" not in second


def test_decision_timestamp_and_offer_evidence_are_bound() -> None:
    original = binding()
    later_decision = binding(
        run_decision=decision(decided_at=BASE + timedelta(minutes=3))
    )
    changed_price = binding(offers=(validated_offer(price="39"),))

    assert later_decision != original
    assert changed_price != original


def test_quality_event_content_is_bound() -> None:
    original = binding()
    changed = binding(events=(quality_event(code="catalog_warning"),))

    assert changed != original


def test_exact_durable_row_reconciles_without_mutation_contract() -> None:
    run_decision = decision()
    offers = (validated_offer(),)
    events = (quality_event(),)
    evidence_id = binding(
        run_decision=run_decision,
        offers=offers,
        events=events,
    )
    row = durable_row(run_evidence_id=evidence_id)

    replay = reconcile_bound_durable_run_row(
        row,
        authority_evidence_id=AUTHORITY_EVIDENCE_ID,
        decision=run_decision,
        offers=offers,
        supermarket_id="demo",
        location_id="demo_sps",
        started_at_utc=BASE,
        finished_at_utc=BASE + timedelta(minutes=1),
        products_observed=1,
        offers_observed=1,
        quality_events=events,
    )

    assert replay.scrape_run_id == "run-authoritative-001"
    assert replay.run_evidence_id == evidence_id
    assert replay.current_created == 1
    assert replay.current_changed == 0
    assert replay.current_confirmed == 0
    assert replay.offers_ignored == 0
    assert replay.quality_event_count == 1


def test_divergent_evidence_id_fails_closed() -> None:
    row = durable_row()
    row["run_evidence_id"] = "crev1_" + "0" * 64

    with pytest.raises(
        CommercialRunEvidenceError,
        match="durable_run_run_evidence_id_mismatch",
    ):
        reconcile_bound_durable_run_row(
            row,
            authority_evidence_id=AUTHORITY_EVIDENCE_ID,
            decision=decision(),
            offers=(validated_offer(),),
            supermarket_id="demo",
            location_id="demo_sps",
            started_at_utc=BASE,
            finished_at_utc=BASE + timedelta(minutes=1),
            products_observed=1,
            offers_observed=1,
            quality_events=(quality_event(),),
        )


def test_durable_metadata_divergence_fails_before_returning_replay() -> None:
    row = durable_row()
    row["offers_observed"] = 2

    with pytest.raises(
        CommercialRunEvidenceError,
        match="durable_run_offers_observed_mismatch",
    ):
        reconcile_bound_durable_run_row(
            row,
            authority_evidence_id=AUTHORITY_EVIDENCE_ID,
            decision=decision(),
            offers=(validated_offer(),),
            supermarket_id="demo",
            location_id="demo_sps",
            started_at_utc=BASE,
            finished_at_utc=BASE + timedelta(minutes=1),
            products_observed=1,
            offers_observed=1,
            quality_events=(quality_event(),),
        )


def test_reconciliation_refuses_non_authoritative_decision() -> None:
    non_authoritative = decision(accepted=False)

    with pytest.raises(
        CommercialRunEvidenceError,
        match="authoritative_commercial_decision_required",
    ):
        reconcile_bound_durable_run_row(
            durable_row(),
            authority_evidence_id=AUTHORITY_EVIDENCE_ID,
            decision=non_authoritative,
            offers=(validated_offer(),),
            supermarket_id="demo",
            location_id="demo_sps",
            started_at_utc=BASE,
            finished_at_utc=BASE + timedelta(minutes=1),
            products_observed=1,
            offers_observed=1,
            quality_events=(quality_event(),),
        )


def test_binding_rejects_invalid_time_order_timezone_and_counts() -> None:
    common = {
        "authority_evidence_id": AUTHORITY_EVIDENCE_ID,
        "decision": decision(),
        "offers": (validated_offer(),),
        "supermarket_id": "demo",
        "location_id": "demo_sps",
        "quality_events": (quality_event(),),
    }

    with pytest.raises(CommercialRunEvidenceError, match="run_time_order_invalid"):
        derive_bound_run_evidence_id(
            **common,
            started_at_utc=BASE + timedelta(minutes=1),
            finished_at_utc=BASE,
            products_observed=1,
            offers_observed=1,
        )

    with pytest.raises(CommercialRunEvidenceError, match="started_at_utc_invalid"):
        derive_bound_run_evidence_id(
            **common,
            started_at_utc=BASE.replace(tzinfo=None),
            finished_at_utc=BASE + timedelta(minutes=1),
            products_observed=1,
            offers_observed=1,
        )

    with pytest.raises(CommercialRunEvidenceError, match="products_observed_invalid"):
        derive_bound_run_evidence_id(
            **common,
            started_at_utc=BASE,
            finished_at_utc=BASE + timedelta(minutes=1),
            products_observed=-1,
            offers_observed=1,
        )


def test_binding_itself_does_not_require_or_create_productive_authority() -> None:
    non_authoritative = decision(accepted=False, status=RunStatus.REJECTED)

    evidence_id = binding(run_decision=non_authoritative)

    assert evidence_id.startswith("crev1_")
    assert non_authoritative.catalog_accepted is False
    assert non_authoritative.commercial_update_allowed is False
