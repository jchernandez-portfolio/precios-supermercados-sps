from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from precios_supermercados.commercial_persistence_batch import (
    prepare_new_run_persistence,
)
from precios_supermercados.commercial_run_evidence import (
    derive_bound_run_evidence_id,
    reconcile_bound_durable_run_row,
)
from precios_supermercados.commercial_state import (
    CommercialRunDecision,
    InMemoryCommercialState,
)
from precios_supermercados.commercial_state_restore import restore_commercial_state
from precios_supermercados.enums import LocationStatus, RunStatus
from precios_supermercados.la_colonia_offer_normalization import (
    normalize_and_validate_la_colonia_raw_product,
)
from precios_supermercados.locations import (
    DEFAULT_LOCATION_CATALOG,
    LA_COLONIA_SPS,
    LA_COLONIA_SUPERMARKET,
    LocationCatalog,
    build_location_catalog,
)
from precios_supermercados.models import RawProduct, ValidatedOffer
from precios_supermercados.scrapers.la_colonia import LaColoniaExtractor
from precios_supermercados.tabular_persistence import (
    FACT_OFFER_HISTORY,
    FACT_OFFERS_CURRENT,
    FACT_SCRAPE_RUNS,
)
from precios_supermercados.tabular_rehydration import rehydrate_commercial_snapshot
from precios_supermercados.tabular_store import InMemoryTabularStore

FIXTURES = Path(__file__).parent / "fixtures" / "la_colonia"
BASE_TIME = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
SUPERMARKET_ID = "la_colonia"
LOCATION_ID = "la_colonia_sps"


def _test_catalog() -> LocationCatalog:
    """Habilita SPS sólo dentro del contrato de persistencia simulado del test."""

    sps = replace(LA_COLONIA_SPS, extraction_enabled=True)
    return build_location_catalog((LA_COLONIA_SUPERMARKET,), (sps,))


def _payload() -> dict[str, object]:
    return json.loads(
        (FIXTURES / "product_search_page.json").read_text(encoding="utf-8")
    )


def _bind_sps_after_verified_location_for_test(raw: RawProduct) -> RawProduct:
    assert raw.location_id == "la_colonia_online"
    assert raw.location_status is LocationStatus.UNKNOWN
    return replace(
        raw,
        location_id=LOCATION_ID,
        location_status=LocationStatus.CONFIRMED,
        location_evidence="test:same_run_sps_location_verified",
        location_confidence=Decimal("1"),
    )


def _validated(
    *,
    run_id: str,
    observed_at: datetime,
    price: str | None = None,
) -> ValidatedOffer:
    payload = _payload()
    if price is not None:
        products = payload["data"]["productSearch"]["products"]
        first_item = products[0]["items"][0]
        commercial_offer = first_item["sellers"][0]["commercialOffer"]
        commercial_offer["Price"] = price
        commercial_offer["ListPrice"] = price

    extractor = LaColoniaExtractor(clock=lambda: observed_at)
    result = extractor.parse_payload(
        payload,
        scrape_run_id=run_id,
        source_url=extractor.build_page_url(page_size=5),
        page_size=5,
    )
    assert result.products
    raw = _bind_sps_after_verified_location_for_test(result.products[0])
    return normalize_and_validate_la_colonia_raw_product(
        raw,
        currency="HNL",
        validated_at_utc=observed_at + timedelta(seconds=1),
        registry={},
    ).validated_offer


def _decision(run_id: str, observed_at: datetime) -> CommercialRunDecision:
    return CommercialRunDecision(
        scrape_run_id=run_id,
        run_status=RunStatus.SUCCESS,
        catalog_accepted=True,
        decided_at_utc=observed_at + timedelta(minutes=1),
    )


def _run_window(observed_at: datetime) -> tuple[datetime, datetime]:
    return observed_at - timedelta(minutes=1), observed_at + timedelta(minutes=2)


def _authority_evidence_id(run_id: str) -> str:
    # Marcador sintético sólo para ejercitar el binding durable. No concede autoridad.
    return f"test-only-authority-evidence:{run_id}"


def _prepare(
    state: InMemoryCommercialState,
    item: ValidatedOffer,
    *,
    catalog: LocationCatalog,
):
    observed_at = item.offer.observed_at_utc
    decision = _decision(item.offer.scrape_run_id, observed_at)
    started_at, finished_at = _run_window(observed_at)
    run_evidence_id = derive_bound_run_evidence_id(
        authority_evidence_id=_authority_evidence_id(decision.scrape_run_id),
        decision=decision,
        offers=(item,),
        supermarket_id=SUPERMARKET_ID,
        location_id=LOCATION_ID,
        started_at_utc=started_at,
        finished_at_utc=finished_at,
        products_observed=1,
        offers_observed=1,
    )
    prepared = prepare_new_run_persistence(
        state,
        decision,
        (item,),
        supermarket_id=SUPERMARKET_ID,
        location_id=LOCATION_ID,
        started_at_utc=started_at,
        finished_at_utc=finished_at,
        products_observed=1,
        offers_observed=1,
        run_evidence_id=run_evidence_id,
        catalog=catalog,
    )
    return prepared, decision, started_at, finished_at


def _restore(store: InMemoryTabularStore) -> InMemoryCommercialState:
    snapshot = rehydrate_commercial_snapshot(
        store.rows(FACT_OFFERS_CURRENT.name),
        store.rows(FACT_OFFER_HISTORY.name),
    )
    restored = restore_commercial_state(
        snapshot,
        store.rows(FACT_SCRAPE_RUNS.name),
    )
    return restored.state


def test_simulated_catalog_does_not_enable_production_location_catalog() -> None:
    simulated = _test_catalog()

    assert DEFAULT_LOCATION_CATALOG.location(LOCATION_ID).extraction_enabled is False
    assert simulated.location(LOCATION_ID).extraction_enabled is True
    assert simulated.location(LOCATION_ID).technical_binding_confirmed is True


def test_la_colonia_first_tabular_load_preserves_source_and_normalized_offer() -> None:
    catalog = _test_catalog()
    state = InMemoryCommercialState()
    store = InMemoryTabularStore()
    item = _validated(run_id="lc-tabular-1", observed_at=BASE_TIME)

    prepared, _decision_value, _started, _finished = _prepare(
        state,
        item,
        catalog=catalog,
    )
    applied = store.apply(prepared.batch)

    assert applied.created > 0
    assert store.count(FACT_OFFERS_CURRENT.name) == 1
    assert store.count(FACT_OFFER_HISTORY.name) == 1
    assert store.count(FACT_SCRAPE_RUNS.name) == 1

    current = store.rows(FACT_OFFERS_CURRENT.name)[0]
    assert current["supermarket_id"] == SUPERMARKET_ID
    assert current["location_id"] == LOCATION_ID
    assert current["city_name"] == "San Pedro Sula"
    assert current["source_name"] == "Malteada Sula Chocosula 473 Ml"
    assert current["source_presentation"] == "473 Ml"
    assert current["normalized_brand"] == "Sula"
    assert current["measurement_unit"] == "ml"
    assert current["total_content"] == "473"
    assert current["current_price"] == "19.9"
    assert current["offer_scrape_run_id"] == "lc-tabular-1"
    assert current["location_status"] == "confirmed"


def test_la_colonia_identical_second_tabular_run_rehydrates_without_duplicate_history() -> None:
    catalog = _test_catalog()
    store = InMemoryTabularStore()

    first = _validated(run_id="lc-tabular-1", observed_at=BASE_TIME)
    first_state = InMemoryCommercialState()
    first_prepared, *_ = _prepare(first_state, first, catalog=catalog)
    store.apply(first_prepared.batch)

    second_time = BASE_TIME + timedelta(days=1)
    second = _validated(run_id="lc-tabular-2", observed_at=second_time)
    restored = _restore(store)
    second_prepared, *_ = _prepare(restored, second, catalog=catalog)
    store.apply(second_prepared.batch)

    assert second_prepared.apply_result.current_confirmed == 1
    assert second_prepared.apply_result.current_changed == 0
    assert store.count(FACT_OFFERS_CURRENT.name) == 1
    assert store.count(FACT_OFFER_HISTORY.name) == 1
    assert store.count(FACT_SCRAPE_RUNS.name) == 2

    current = store.rows(FACT_OFFERS_CURRENT.name)[0]
    history = store.rows(FACT_OFFER_HISTORY.name)[0]
    assert current["last_scrape_run_id"] == "lc-tabular-2"
    assert current["last_observed_at_utc"] == "2026-08-27T12:00:00.000Z"
    assert history["last_confirmed_by_scrape_run_id"] == "lc-tabular-2"
    assert history["last_observed_at_utc"] == "2026-08-27T12:00:00.000Z"
    assert history["valid_to_utc"] is None


def test_la_colonia_price_change_survives_tabular_rehydrate_restore_cycle() -> None:
    catalog = _test_catalog()
    store = InMemoryTabularStore()

    first = _validated(run_id="lc-tabular-1", observed_at=BASE_TIME)
    state = InMemoryCommercialState()
    first_prepared, *_ = _prepare(state, first, catalog=catalog)
    store.apply(first_prepared.batch)

    second_time = BASE_TIME + timedelta(days=1)
    second = _validated(run_id="lc-tabular-2", observed_at=second_time)
    state = _restore(store)
    second_prepared, *_ = _prepare(state, second, catalog=catalog)
    store.apply(second_prepared.batch)

    third_time = BASE_TIME + timedelta(days=2)
    changed = _validated(
        run_id="lc-tabular-3",
        observed_at=third_time,
        price="17.90",
    )
    state = _restore(store)
    third_prepared, *_ = _prepare(state, changed, catalog=catalog)
    store.apply(third_prepared.batch)

    assert third_prepared.apply_result.current_changed == 1
    assert third_prepared.apply_result.current_confirmed == 0
    assert store.count(FACT_OFFERS_CURRENT.name) == 1
    assert store.count(FACT_OFFER_HISTORY.name) == 2
    assert store.count(FACT_SCRAPE_RUNS.name) == 3

    current = store.rows(FACT_OFFERS_CURRENT.name)[0]
    history = sorted(
        store.rows(FACT_OFFER_HISTORY.name),
        key=lambda row: row["valid_from_utc"],
    )
    assert current["current_price"] == "17.9"
    assert current["last_scrape_run_id"] == "lc-tabular-3"
    assert history[0]["change_type"] == "initial"
    assert history[0]["valid_to_utc"] == "2026-08-28T12:00:00.000Z"
    assert history[0]["closed_by_scrape_run_id"] == "lc-tabular-3"
    assert history[1]["change_type"] == "price"
    assert history[1]["changed_fields_json"] == '["current_price"]'
    assert history[1]["valid_to_utc"] is None

    # El snapshot final debe poder restaurarse otra vez sin drift durable.
    final_state = _restore(store)
    final_current = final_state.current(changed.offer.offer_id)
    assert final_current is not None
    assert final_current.validated_offer.offer.current_price == Decimal("17.90")
    assert len(final_state.history(changed.offer.offer_id)) == 2


def test_exact_durable_replay_is_reconciled_without_second_transition() -> None:
    catalog = _test_catalog()
    store = InMemoryTabularStore()
    item = _validated(run_id="lc-tabular-1", observed_at=BASE_TIME)
    state = InMemoryCommercialState()
    prepared, decision, started_at, finished_at = _prepare(
        state,
        item,
        catalog=catalog,
    )
    store.apply(prepared.batch)
    durable_row = store.rows(FACT_SCRAPE_RUNS.name)[0]

    replay = reconcile_bound_durable_run_row(
        durable_row,
        authority_evidence_id=_authority_evidence_id(decision.scrape_run_id),
        decision=decision,
        offers=(item,),
        supermarket_id=SUPERMARKET_ID,
        location_id=LOCATION_ID,
        started_at_utc=started_at,
        finished_at_utc=finished_at,
        products_observed=1,
        offers_observed=1,
    )

    assert replay.scrape_run_id == "lc-tabular-1"
    assert replay.run_evidence_id == durable_row["run_evidence_id"]
    assert replay.current_created == 1
    assert replay.current_changed == 0
    assert replay.current_confirmed == 0
    assert store.count(FACT_OFFERS_CURRENT.name) == 1
    assert store.count(FACT_OFFER_HISTORY.name) == 1
    assert store.count(FACT_SCRAPE_RUNS.name) == 1
