from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from precios_supermercados.commercial_state import (
    CommercialRunDecision,
    InMemoryCommercialState,
)
from precios_supermercados.enums import ChangeType, LocationStatus, RunStatus
from precios_supermercados.la_colonia_offer_normalization import (
    normalize_and_validate_la_colonia_raw_product,
)
from precios_supermercados.models import RawProduct, ValidatedOffer
from precios_supermercados.scrapers.la_colonia import LaColoniaExtractor

FIXTURES = Path(__file__).parent / "fixtures" / "la_colonia"
BASE_TIME = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


def _payload() -> dict[str, object]:
    return json.loads(
        (FIXTURES / "product_search_page.json").read_text(encoding="utf-8")
    )


def _bind_sps_after_verified_location_for_test(raw: RawProduct) -> RawProduct:
    """Simula la frontera ya verificada que cambia contexto fuente por ubicación SPS."""

    assert raw.location_id == "la_colonia_online"
    assert raw.location_status is LocationStatus.UNKNOWN
    return replace(
        raw,
        location_id="la_colonia_sps",
        location_status=LocationStatus.CONFIRMED,
        location_evidence="test:same_run_sps_location_verified",
        location_confidence=Decimal("1"),
    )


def _first_raw(
    *,
    run_id: str,
    observed_at: datetime,
    price: str | None = None,
) -> RawProduct:
    payload = _payload()
    if price is not None:
        products = payload["data"]["productSearch"]["products"]
        first_item = products[0]["items"][0]
        first_offer = first_item["sellers"][0]["commercialOffer"]
        first_offer["Price"] = price
        # El escenario pretende cambiar sólo el precio efectivo. Mantener ListPrice
        # igual evita convertir artificialmente la observación en una promoción.
        first_offer["ListPrice"] = price

    extractor = LaColoniaExtractor(clock=lambda: observed_at)
    result = extractor.parse_payload(
        payload,
        scrape_run_id=run_id,
        source_url=extractor.build_page_url(page_size=5),
        page_size=5,
    )
    assert result.products
    return _bind_sps_after_verified_location_for_test(result.products[0])


def _validated(
    *, run_id: str, observed_at: datetime, price: str | None = None
) -> ValidatedOffer:
    raw = _first_raw(run_id=run_id, observed_at=observed_at, price=price)
    return normalize_and_validate_la_colonia_raw_product(
        raw,
        currency="HNL",
        validated_at_utc=observed_at + timedelta(seconds=1),
        # Los IDs del fixture son sintéticos. El objetivo aquí es probar las reglas
        # generales + el puente + el motor histórico, no reutilizar overrides reales.
        registry={},
    ).validated_offer


def _decision(run_id: str, observed_at: datetime) -> CommercialRunDecision:
    # Aceptación simulada exclusivamente en memoria para probar transiciones. No
    # representa ni concede catalog_accepted al catálogo real ni autoriza persistencia.
    return CommercialRunDecision(
        scrape_run_id=run_id,
        run_status=RunStatus.SUCCESS,
        catalog_accepted=True,
        decided_at_utc=observed_at + timedelta(minutes=1),
    )


def test_la_colonia_identical_second_observation_confirms_without_duplicate_history() -> None:
    store = InMemoryCommercialState()
    first = _validated(run_id="fixture-run-1", observed_at=BASE_TIME)
    second_time = BASE_TIME + timedelta(days=1)
    second = _validated(run_id="fixture-run-2", observed_at=second_time)

    first_result = store.apply_run(
        _decision("fixture-run-1", BASE_TIME),
        (first,),
    )
    second_result = store.apply_run(
        _decision("fixture-run-2", second_time),
        (second,),
    )

    assert first_result.current_created == 1
    assert second_result.current_confirmed == 1
    assert second_result.current_changed == 0
    history = store.history(first.offer.offer_id)
    assert len(history) == 1
    assert history[0].change_type is ChangeType.INITIAL
    assert history[0].valid_from_utc == BASE_TIME
    assert history[0].last_observed_at_utc == second_time
    assert history[0].last_confirmed_by_scrape_run_id == "fixture-run-2"
    assert history[0].valid_to_utc is None


def test_la_colonia_price_change_closes_old_period_and_opens_price_period() -> None:
    store = InMemoryCommercialState()
    first = _validated(run_id="fixture-run-1", observed_at=BASE_TIME)
    second_time = BASE_TIME + timedelta(days=1)
    changed = _validated(
        run_id="fixture-run-2",
        observed_at=second_time,
        price="17.90",
    )
    assert first.offer.current_price != changed.offer.current_price

    store.apply_run(_decision("fixture-run-1", BASE_TIME), (first,))
    result = store.apply_run(
        _decision("fixture-run-2", second_time),
        (changed,),
    )

    assert result.current_changed == 1
    assert result.current_confirmed == 0
    history = store.history(first.offer.offer_id)
    assert len(history) == 2
    assert history[0].change_type is ChangeType.INITIAL
    assert history[0].valid_to_utc == second_time
    assert history[0].closed_by_scrape_run_id == "fixture-run-2"
    assert history[1].change_type is ChangeType.PRICE
    assert history[1].changed_fields == ("current_price",)
    assert history[1].valid_from_utc == second_time
    assert history[1].valid_to_utc is None
    current = store.current(first.offer.offer_id)
    assert current is not None
    assert current.validated_offer.state_hash == changed.state_hash
    assert current.validated_offer.offer.current_price == Decimal("17.90")


def test_replaying_same_la_colonia_run_is_idempotent() -> None:
    store = InMemoryCommercialState()
    item = _validated(run_id="fixture-run-1", observed_at=BASE_TIME)
    decision = _decision("fixture-run-1", BASE_TIME)

    first = store.apply_run(decision, (item,))
    replay = store.apply_run(decision, (item,))

    assert first.mutated is True
    assert replay.mutated is False
    assert replay.replayed is True
    assert store.current_count == 1
    assert len(store.history(item.offer.offer_id)) == 1
