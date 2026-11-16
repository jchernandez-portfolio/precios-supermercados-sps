"""Catálogo v3: ``history_30d`` (puntos de cambio de precio de 30 días, aditivo)."""
from __future__ import annotations

import json
from datetime import timedelta, timezone
from pathlib import Path

from test_exportar_consumer_catalog import AS_OF, MODULE, all_rows, build_db, export

core = MODULE  # la fachada reexporta el núcleo (history_30d, VisibleOffer, HistoricalPoint)


def offer(price: int | None = 1000) -> object:
    return core.VisibleOffer(
        source_product_id="la_colonia:1", supermarket_id="la_colonia", location_id="la_colonia_sps",
        product_name="Leche", brand="Sula", presentation="1 L", current_price_minor=price,
        reported_regular_price_minor=None, is_promotion=False, availability="in_stock",
        observed_at="2026-09-09T10:00:00Z", canonical_product_id=None, category=None, product_type=None,
        presentation_dimension=None, presentation_total_base=None, presentation_status="confirmed",
        comparison_status="ready",
    )


def periods(*items: tuple[timedelta, int]) -> tuple[object, ...]:
    """Periodos ``(hace cuánto empezó, precio)`` relativos a ``AS_OF``."""
    return tuple(
        core.HistoricalPoint("la_colonia:1", "la_colonia", "la_colonia_sps", AS_OF - ago, price)
        for ago, price in items
    )


def days(value: float) -> timedelta:
    return timedelta(days=value)


def test_points_go_oldest_to_newest_and_end_at_the_current_price() -> None:
    history = periods((days(120), 1300), (days(45), 1200), (days(20.1), 1100), (days(3.5), 1000))
    assert core.history_30d(offer(), history, as_of_utc=AS_OF) == [
        [30, "12.00"], [20, "11.00"], [3, "10.00"],
    ]


def test_without_a_price_change_in_30_days_the_field_is_omitted() -> None:
    # Cambió hace 45 días; después sólo cambió la promoción/disponibilidad (mismo precio).
    history = periods((days(60), 1200), (days(45), 1000), (days(10), 1000), (days(2), 1000))
    assert core.history_30d(offer(), history, as_of_utc=AS_OF) is None
    assert core.history_30d(offer(), periods((days(5), 1000)), as_of_utc=AS_OF) is None
    assert core.history_30d(offer(), (), as_of_utc=AS_OF) is None


def test_only_the_last_30_days_count() -> None:
    history = periods((days(80), 900), (days(50), 1500), (days(31), 1200), (days(1), 1000))
    points = core.history_30d(offer(), history, as_of_utc=AS_OF)
    assert points == [[30, "12.00"], [1, "10.00"]]
    assert all(0 <= day <= 30 for day, _ in points)


def test_a_product_newer_than_30_days_starts_at_its_first_period() -> None:
    history = periods((days(12.9), 1250), (days(0.2), 1000))
    assert core.history_30d(offer(), history, as_of_utc=AS_OF) == [[12, "12.50"], [0, "10.00"]]


def test_two_changes_the_same_day_keep_the_last_and_a_return_collapses() -> None:
    same_day = periods((days(40), 1200), (days(6.8), 1100), (days(6.2), 1000))
    assert core.history_30d(offer(), same_day, as_of_utc=AS_OF) == [[30, "12.00"], [6, "10.00"]]
    back = periods((days(40), 1000), (days(6.8), 1100), (days(6.2), 1000))
    assert core.history_30d(offer(), back, as_of_utc=AS_OF) is None
    up_down = periods((days(40), 1000), (days(9), 1100), (days(2), 1000))
    assert core.history_30d(offer(), up_down, as_of_utc=AS_OF) == [[30, "10.00"], [9, "11.00"], [2, "10.00"]]


def test_history_that_does_not_end_at_the_current_price_is_not_published() -> None:
    history = periods((days(40), 1200), (days(5), 1100))
    assert core.history_30d(offer(1000), history, as_of_utc=AS_OF) is None
    assert core.history_30d(offer(None), history, as_of_utc=AS_OF) is None


def test_exported_catalog_carries_history_30d_only_on_offers_that_changed(tmp_path: Path) -> None:
    database = tmp_path / "source.sqlite"
    output = tmp_path / "public"
    build_db(database)
    manifest = export(database, output)
    rows = all_rows(output, manifest)
    milk = next(row for row in rows if row["canonical_product_id"] == "gtin:leche")
    la_colonia, walmart = milk["offers"]
    # 12.00 desde mayo (vigente al abrir la ventana), 11.00 el 20-ago, 10.00 hoy.
    assert la_colonia["history_30d"] == [[30, "12.00"], [20, "11.00"], [0, "10.00"]]
    assert la_colonia["history_30d"][-1][1] == la_colonia["current_price"]
    assert "history_30d" not in walmart  # un solo periodo: sin cambio, sin campo
    with_field = [o for row in rows for o in row["offers"] if "history_30d" in o]
    assert len(with_field) == manifest["offers_with_history_30d"] == 1
    assert "history_30d" not in json.dumps(json.loads((output / "search.json").read_text()))


def test_as_of_with_another_offset_gives_the_same_points() -> None:
    history = periods((days(40), 1200), (days(2), 1000))
    honduras = AS_OF.astimezone(timezone(timedelta(hours=-6)))
    assert core.history_30d(offer(), history, as_of_utc=honduras) == [[30, "12.00"], [2, "10.00"]]
