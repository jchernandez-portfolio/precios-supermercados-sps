"""Regla B (2026-10-02): una oferta ``out_of_stock`` nunca entra a comparaciones.

Sigue visible como información individual del día, pero queda fuera de ranking,
mejor precio, diferencia, análisis y Mi Compra. Un grupo comparable exige hoy
≥2 cadenas con ofertas no agotadas con precio; ``unknown`` sigue comparable.
"""
from __future__ import annotations

import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import exportar_consumer_catalog as facade  # noqa: E402
import exportar_consumer_catalog_core as core  # noqa: E402
import exportar_consumer_catalog_tgu as tgu  # noqa: E402
from precios_supermercados.price_analytics import CurrentPriceObservation  # noqa: E402
from precios_supermercados.shopping_analytics import ConsumerOffer  # noqa: E402

AS_OF = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
SPS_LOCATIONS = dict(core.EXPECTED_SCOPE)
FRESH_SPS = {scope: "FRESH" for scope in core.EXPECTED_SCOPE}


def offer(supermarket: str, price: int | None, availability: str = "in_stock", *, location: str | None = None, number: int = 1):
    location = location or SPS_LOCATIONS[supermarket]
    return core.VisibleOffer(
        source_product_id=f"{supermarket}:{location}:{number}",
        supermarket_id=supermarket,
        location_id=location,
        product_name="Desodorante Spray Dove 150 Ml",
        brand="Dove",
        presentation="150 Ml",
        current_price_minor=price,
        reported_regular_price_minor=None,
        is_promotion=False if price is not None else None,
        availability=availability,
        observed_at="2026-10-02T08:30:00Z",
        canonical_product_id="gtin:07791293051659",
        category="Cuidado personal",
        product_type="Desodorante",
        presentation_dimension="volume_ml",
        presentation_total_base="150",
        presentation_status="confirmed",
        comparison_status="ready",
    )


def rows_for(offers) -> list[dict[str, object]]:
    return facade.build_rows(offers, FRESH_SPS, {}, as_of_utc=AS_OF)


def states(row: dict[str, object]) -> dict[str, str]:
    return {item["supermarket_id"]: item["relative_price_state"] for item in row["offers"]}


def test_group_with_one_out_of_stock_member_excludes_it_from_ranking() -> None:
    [row] = rows_for([
        offer("la_colonia", 9_000, "out_of_stock"),
        offer("colonial", 10_000),
        offer("walmart", 12_000),
    ])
    assert row["comparability"] == "comparable"
    assert states(row) == {"la_colonia": "neutral", "colonial": "best", "walmart": "highest"}
    # La oferta agotada sigue visible como información individual del día.
    oos = next(item for item in row["offers"] if item["supermarket_id"] == "la_colonia")
    assert oos["availability"] == "out_of_stock" and oos["current_price"] == "90.00"

    analysis = core.build_consumer_analysis([row], as_of_utc=AS_OF)
    retailers = {item["supermarket_id"]: item for item in analysis["retailers"]}
    assert analysis["summary"]["comparable_products"] == 1
    assert analysis["summary"]["observed_unit_savings"] == "20.00"
    assert retailers["la_colonia"]["comparable_products"] == 0
    assert retailers["la_colonia"]["best_price_wins"] == 0
    assert retailers["colonial"]["best_price_wins"] == 1


def test_group_left_with_one_available_retailer_is_not_comparable_today() -> None:
    available = [offer("la_colonia", 9_000), offer("colonial", 10_000)]
    [baseline] = rows_for(available)
    [row] = rows_for([replace(available[0], availability="out_of_stock"), available[1]])

    assert baseline["comparability"] == "comparable"
    assert row["comparability"] == "individual"
    # La identidad pública no cambia: Mi Compra/compartidos siguen resolviendo.
    assert row["row_id"] == baseline["row_id"]
    assert row["canonical_product_id"] == baseline["canonical_product_id"]
    assert set(states(row).values()) == {"neutral"}
    assert core.build_consumer_analysis([row], as_of_utc=AS_OF)["summary"]["comparable_products"] == 0


def test_all_members_out_of_stock_is_not_comparable_and_not_shoppable() -> None:
    group = [offer("la_colonia", 9_000, "out_of_stock"), offer("colonial", 10_000, "out_of_stock")]
    assert core._comparability_today("comparable", group) == "individual"
    # El catálogo B2C ya no indexa filas sin ninguna oferta comprable.
    assert rows_for(group) == []


def test_unpriced_out_of_stock_offer_never_counts_as_available() -> None:
    [row] = rows_for([offer("walmart", None, "out_of_stock"), offer("colonial", 10_000)])
    assert row["comparability"] == "individual"


def test_unknown_availability_stays_comparable() -> None:
    [row] = rows_for([offer("comisariato_los_andes", 9_500, "unknown"), offer("colonial", 10_000)])
    assert row["comparability"] == "comparable"
    assert states(row) == {"comisariato_los_andes": "best", "colonial": "highest"}


def test_offer_back_in_stock_is_comparable_again_automatically() -> None:
    out = [offer("la_colonia", 9_000, "out_of_stock"), offer("colonial", 10_000)]
    back = [replace(out[0], availability="in_stock"), out[1]]
    assert rows_for(out)[0]["comparability"] == "individual"
    assert rows_for(back)[0]["comparability"] == "comparable"
    assert states(rows_for(back)[0]) == {"la_colonia": "best", "colonial": "highest"}


def test_tgu_two_branches_of_one_chain_are_not_a_comparison_when_others_are_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core, "EXPECTED_SCOPE", tgu.TGU_SCOPE)
    group = [
        offer("walmart", 10_000, location="walmart_tgu_ffaa"),
        offer("walmart", 12_000, location="walmart_tgu_el_sauce"),
        offer("la_colonia", 9_000, "out_of_stock", location="la_colonia_tgu"),
    ]
    [(identity_mode, grouped)] = tgu.identity_groups_exact_context(group)
    assert identity_mode == "comparable"
    assert core._comparability_today(identity_mode, grouped) == "individual"
    fresh = {scope: "FRESH" for scope in tgu.TGU_SCOPE}
    assert set(core._relative_states("individual", grouped, fresh).values()) == {"neutral"}

    back = [*group[:2], replace(group[2], availability="in_stock")]
    assert core._comparability_today("comparable", back) == "comparable"


def test_python_analytics_and_scenarios_already_exclude_out_of_stock() -> None:
    """Mart v2/Business Mart y escenarios consumen ``priced``: agotado no participa."""
    observation = CurrentPriceObservation(
        source_record_id="la_colonia:1", supermarket_id="la_colonia",
        location_id="la_colonia_sps", price_minor=9_000, availability="out_of_stock",
    )
    assert observation.priced is False
    assert replace(observation, availability="unknown").priced is True
    scenario_offer = ConsumerOffer(
        canonical_product_id="gtin:07791293051659", source_product_id="la_colonia:1",
        supermarket_id="la_colonia", location_id="la_colonia_sps", category=None,
        product_type=None, product_name="Desodorante", brand="Dove", variant=None,
        presentation="150 Ml", current_price_minor=9_000, reported_regular_price_minor=None,
        is_promotion=False, availability="out_of_stock", observed_at_utc=AS_OF,
        freshness_status="FRESH",
    )
    assert scenario_offer.buyable is False
    assert replace(scenario_offer, availability="unknown").buyable is True
