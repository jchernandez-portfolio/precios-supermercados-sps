"""PriceSmart: variantes con el mismo título salen en una sola tarjeta.

PriceSmart se captura una fila por SKU (``26181``, ``26181-0132029951206``…). Las
ofertas sin identidad comparable con el mismo ``pid`` (lo que va antes del ``-``),
la misma ubicación y el mismo título normalizado se publican como una sola fila con
la oferta comprable más barata y ``variants: N`` (campo aditivo). Títulos distintos
no se juntan y las ofertas comparables por GTIN no se tocan.
"""
from __future__ import annotations

import sqlite3
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import _product_master_fixtures as fx
from precios_supermercados import capture_membership as cm

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import exportar_consumer_catalog as facade  # noqa: E402
import exportar_consumer_catalog_core as core  # noqa: E402
from test_product_master_exporter_parity import _export_all, _rows  # noqa: E402

AS_OF = datetime(2026, 11, 16, 12, tzinfo=UTC)
FRESH_SPS = {scope: "FRESH" for scope in core.EXPECTED_SCOPE}
DELAFAILLE = "Delafaille Bombones de Chocolates Belgas Finos 2 Unidades / 200 g"


def offer(number: int, sku: str | None, name: str = DELAFAILLE, price: int | None = 49_995, **changes):  # type: ignore[no-untyped-def]
    base = core.VisibleOffer(
        source_product_id=f"pricesmart:{number}",
        supermarket_id="pricesmart",
        location_id="pricesmart_sps",
        product_name=name,
        brand="Delafaille",
        presentation=None,
        current_price_minor=price,
        reported_regular_price_minor=None,
        is_promotion=False,
        availability="in_stock",
        observed_at="2026-11-16T08:30:00Z",
        canonical_product_id=None,
        category="Alimentos",
        product_type="Chocolates",
        presentation_dimension="mass_g",
        presentation_total_base="200",
        presentation_status="name_only",
        comparison_status="unmapped",
        source_key=sku,
    )
    changed = replace(base, **changes)
    return replace(changed, source_product_id=f"{changed.supermarket_id}:{number}")


def rows_for(offers) -> list[dict[str, object]]:  # type: ignore[no-untyped-def]
    return facade.build_rows(tuple(offers), FRESH_SPS, {}, as_of_utc=AS_OF)


def test_delafaille_variants_with_the_same_title_are_one_row() -> None:
    [row] = rows_for([
        offer(1, "26181-0132029959318"),
        offer(2, "26181-0132029951206"),
    ])
    [published] = row["offers"]
    assert published["current_price"] == "499.95"
    assert published["variants"] == 2
    # Empate de precio: SKU menor, estable entre corridas.
    assert published["source_product_id"] == "pricesmart:2"
    assert row["comparability"] == "individual"


def test_cheapest_buyable_variant_wins_and_the_base_sku_breaks_ties() -> None:
    [row] = rows_for([
        offer(1, "26181-0132029951206", price=52_995),
        offer(2, "26181-0132029959318", price=49_995),
        offer(3, "26181-0132029900000", price=39_995, availability="out_of_stock"),
    ])
    [published] = row["offers"]
    assert (published["source_product_id"], published["current_price"]) == ("pricesmart:2", "499.95")
    assert published["variants"] == 2  # la agotada no cuenta como variante comprable

    [tie] = rows_for([offer(4, "26181-0132029951206"), offer(5, "26181")])
    assert tie["offers"][0]["source_product_id"] == "pricesmart:5"


def test_variants_with_different_titles_stay_separate() -> None:
    rows = rows_for([
        offer(1, "51234-0132029951206", name="Member's Mark Camisa Hombre Talla M"),
        offer(2, "51234-0132029959318", name="Member's Mark Camisa Hombre Talla L"),
    ])
    assert len(rows) == 2
    assert all("variants" not in item for row in rows for item in row["offers"])


def test_title_normalization_and_scope_of_the_key() -> None:
    # Acentos, mayúsculas, espacios y puntuación no separan; otro pid u otra
    # ubicación sí, y sin source_key (sin membresía) no se une nada.
    [same] = rows_for([offer(1, "26181-1"), offer(2, "26181-2", name="DELAFAILLE  bombones de chocolates belgas finos 2 unidades 200 G")])
    assert same["offers"][0]["variants"] == 2
    assert len(rows_for([offer(1, "26181-1"), offer(2, "99999-2")])) == 2
    kept, variants = facade.collapse_same_title_variants([offer(1, "26181-1"), offer(2, "26181-2", location_id="pricesmart_tgu")])
    assert len(kept) == 2 and variants == {}
    assert len(rows_for([offer(1, None), offer(2, None)])) == 2


def test_offers_with_a_comparable_identity_are_untouched() -> None:
    gtin = {"canonical_product_id": "gtin:07400000000001", "comparison_status": "ready"}
    walmart = offer(9, "77", supermarket_id="walmart", location_id="walmart_sps", **gtin)
    rows = rows_for([offer(1, "26181-1", **gtin), offer(2, "26181-2"), walmart])
    assert len(rows) == 2
    comparable = next(row for row in rows if row["comparability"] == "comparable")
    assert {item["source_product_id"] for item in comparable["offers"]} == {"pricesmart:1", "walmart:9"}
    assert all("variants" not in item for row in rows for item in row["offers"])
    # Otro súper con dos SKU del mismo título no se junta (sólo PriceSmart).
    others = [offer(n, f"5-{n}", supermarket_id="walmart", location_id="walmart_sps") for n in (1, 2)]
    assert len(rows_for(others)) == 2


def test_exporter_reads_the_sku_and_publishes_one_card_in_both_cities(tmp_path: Path) -> None:
    products = (
        *fx.PRODUCTS,
        ("pricesmart", DELAFAILLE, "Delafaille", None, "Alimentos", None, 49_995),
        ("pricesmart", DELAFAILLE, "Delafaille", None, "Alimentos", None, 49_995),
        ("pricesmart", "Delafaille Bombones Surtidos 400 g", "Delafaille", None, "Alimentos", None, 79_995),
    )
    db = tmp_path / "rpi.sqlite"
    fx.build_database(db, products)
    first = len(fx.PRODUCTS) + 1
    con = sqlite3.connect(db)
    try:
        for product_id, sku in zip(range(first, first + 3), ("26181-0132029951206", "26181-0132029959318", "26181"), strict=True):
            con.execute("UPDATE products SET source_key=? WHERE product_id=?", (sku, product_id))
        con.execute(cm.TABLE_SQL)  # la llave fuente se lee con la membresía (activa en producción)
        con.commit()
    finally:
        con.close()
    _export_all(db, tmp_path / "out")
    for city in ("sps", "tgu"):
        delafaille = [row for row in _rows(tmp_path / "out", city) if str(row["product_name"]).startswith("Delafaille")]
        assert sorted(str(row["product_name"]) for row in delafaille) == sorted([DELAFAILLE, "Delafaille Bombones Surtidos 400 g"])
        same = next(row for row in delafaille if row["product_name"] == DELAFAILLE)
        assert [item["variants"] for item in same["offers"]] == [2]
