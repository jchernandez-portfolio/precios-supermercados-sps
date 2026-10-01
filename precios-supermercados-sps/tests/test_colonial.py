from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from precios_supermercados.scrapers.colonial import (
    ORIGIN, ColonialError, parse_cards, parse_products, price, reconcile, sitemap_urls,
)

FIXTURES = Path(__file__).parent / "fixtures" / "colonial"


def test_source_sample_preserves_identity_price_vendor_and_unknown_stock():
    rows = parse_products((FIXTURES / "products-40.json").read_bytes())
    assert len(rows) == 40
    assert sum(row["is_promotion"] for row in rows) == 10
    assert rows[0]["item_id"] == "56173138444580"
    assert rows[0]["source_name"] == "CHOBANI Non-Fat Vanilla 32oz"
    assert rows[0]["current_price"] == "234.99"
    assert rows[0]["brand"] == "RMS"  # vendor fuente, no inferir marca desde nombre
    assert rows[0]["reference"] == "894700010144"
    assert rows[0]["ean"] is None  # SKU numérico no demuestra barcode
    assert all(row["availability"] == "unknown" for row in rows)


def test_source_cards_use_button_instead_of_hidden_in_stock_label():
    total, cards = parse_cards((FIXTURES / "collection-section.html").read_bytes())
    assert total == 9199 and len(cards) == 24
    assert sum(card["availability"] == "out_of_stock" for card in cards) == 4
    assert sum(card["availability"] == "in_stock" for card in cards) == 20
    assert cards[0]["item_id"] == "52017827610916"
    assert cards[0]["current_price"] == "26.99"


@pytest.mark.parametrize("value", [None, True, 0, "-1", "NaN", "Infinity", "1.001", " 1.00"])
def test_invalid_price_rejected(value):
    with pytest.raises(ColonialError):
        price(value)


@pytest.mark.parametrize("mutation", ["duplicate_product", "duplicate_variant", "wrong_parent", "missing_price", "missing_variants"])
def test_invalid_source_records_rejected(mutation):
    data = json.loads((FIXTURES / "products-40.json").read_bytes())
    product = data["products"][0]
    if mutation == "duplicate_product":
        data["products"].append(copy.deepcopy(product))
    elif mutation == "duplicate_variant":
        product["variants"].append(copy.deepcopy(product["variants"][0]))
    elif mutation == "wrong_parent":
        product["variants"][0]["product_id"] += 1
    elif mutation == "missing_price":
        del product["variants"][0]["price"]
    else:
        product["variants"] = []
    with pytest.raises(ColonialError):
        parse_products(json.dumps(data).encode())


def sample():
    rows = parse_products((FIXTURES / "products-40.json").read_bytes())[:2]
    cards = [{k: row[k] for k in ("item_id", "handle", "current_price", "reported_regular_price")} | {"availability": "out_of_stock"} for row in rows]
    members = {ORIGIN + "/products/" + row["handle"] for row in rows}
    return rows, cards, members


def test_complete_snapshot_reconciles_three_sources():
    rows, cards, members = sample()
    accepted = reconcile(rows, cards, members, 2)
    assert all(p["availability"] == "out_of_stock" for p in accepted)
    assert all("handle" not in p for p in accepted)


def test_card_minimum_price_does_not_overwrite_other_variant_or_infer_its_stock():
    rows, cards, _ = sample()
    rows[1].update(product_id=rows[0]["product_id"], handle=rows[0]["handle"],
                   current_price="199.00", reported_regular_price="234.99", is_promotion=True)
    cards = [cards[0] | {"current_price": "199.00", "reported_regular_price": "234.99"}]
    accepted = reconcile(rows, cards, {ORIGIN + "/products/" + rows[0]["handle"]}, 1)
    assert [(row["current_price"], row["availability"]) for row in accepted] == [
        ("234.99", "out_of_stock"), ("199.00", "unknown")]


def test_unclosed_product_grid_is_rejected():
    raw = (FIXTURES / "collection-section.html").read_bytes()
    grid_end = raw.index(b"</ul>", raw.index(b'id="product-grid"'))
    with pytest.raises(ColonialError):
        parse_cards(raw[:grid_end])


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "identity_swap", "price", "total", "wrong_variant"])
def test_incomplete_or_inconsistent_catalog_rejected(mutation):
    rows, cards, members = sample()
    total = 2
    if mutation == "missing":
        cards.pop()
    elif mutation == "duplicate":
        rows[1] = copy.deepcopy(rows[0])
    elif mutation == "identity_swap":
        members.pop(); members.add(ORIGIN + "/products/other")
    elif mutation == "price":
        cards[0]["current_price"] = "0.00"
    elif mutation == "total":
        total = 3
    else:
        cards[0]["item_id"] = "999"
    with pytest.raises(ColonialError):
        reconcile(rows, cards, members, total)


def test_sitemap_ignores_home_and_rejects_other_origin():
    xml = b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://supercolonial.com/</loc></url><url><loc>https://supercolonial.com/products/x</loc></url></urlset>'
    assert sitemap_urls(xml) == {ORIGIN + "/products/x"}
    with pytest.raises(ColonialError):
        sitemap_urls(xml.replace(b"supercolonial.com/products", b"example.org/products"))


def test_missing_grid_or_total_is_not_empty_catalog():
    with pytest.raises(ColonialError):
        parse_cards(b"<html>Access denied</html>")
    with pytest.raises(ColonialError):
        parse_cards(b"9199 productos")


def test_cards_markup_2026_09_30_add_to_cart_and_sold_out_buttons():
    # Desde 2026-09-24 el botón disponible ya no usa `addtocart-btn` y el regular
    # se publica en <s class="lc-pcard__price-was">; antes cada tarjeta fallaba
    # con card_shape_invalid y la cadena completa quedaba sin handoff.
    total, cards = parse_cards((FIXTURES / "collection-section-2026-09-30.html").read_bytes())
    assert total == 9137
    assert [c["availability"] for c in cards] == ["in_stock", "in_stock", "in_stock", "out_of_stock", "out_of_stock"]
    assert [c["current_price"] for c in cards] == ["26.99", "129.99", "1029.50", "9.99", "34.50"]
    assert [c["reported_regular_price"] for c in cards] == [None, "169.99", None, "15.99", None]
    assert cards[0] == {"item_id": "52017827610916", "handle": "123-deterg-maxi-efecto-425g",
                        "availability": "in_stock", "current_price": "26.99", "reported_regular_price": None}


@pytest.mark.parametrize("mutation", ["no_button", "two_buttons", "sold_out_not_disabled"])
def test_cards_markup_2026_09_30_still_fail_closed(mutation):
    raw = (FIXTURES / "collection-section-2026-09-30.html").read_text()
    button = 'class="lc-btn lc-btn--primary ripple-btn add_to_cart_btn_cls notifi-cart"'
    if mutation == "no_button":
        raw = raw.replace(button, 'class="lc-btn lc-btn--primary"', 1)
    elif mutation == "two_buttons":
        raw = raw.replace("</form>", '<button class="cp-sold-out" disabled aria-disabled="true"></button></form>', 1)
    else:
        raw = raw.replace('class="lc-btn lc-btn--disabled cp-sold-out" disabled', 'class="lc-btn lc-btn--disabled cp-sold-out"', 1)
    if mutation == "sold_out_not_disabled":
        # Agotado sin `disabled`: no se infiere stock.
        _, cards = parse_cards(raw.encode())
        assert cards[3]["availability"] == "unknown"
        return
    with pytest.raises(ColonialError, match="card_shape_invalid"):
        parse_cards(raw.encode())
