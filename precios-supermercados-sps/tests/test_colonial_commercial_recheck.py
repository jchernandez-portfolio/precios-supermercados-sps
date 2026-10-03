"""Re-verificación acotada de precios Colonial que cambian a media captura.

Incidentes 2026-10-02 y 2026-10-03: el JSON se lee al inicio y las tarjetas HTML
durante ~8 minutos; un solo cambio de precio en ese intervalo rechazaba los más
de 3,000 productos con ``commercial_sources_disagree``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from obtener_catalogo_colonial import html_page_url, recheck_commercial_disagreements  # noqa: E402
from precios_supermercados.scrapers.colonial import (  # noqa: E402
    MAX_COMMERCIAL_RECHECKS, ORIGIN, ColonialError, apply_commercial_recheck,
    commercial_disagreements, parse_cards, parse_product_detail, reconcile,
)

FIXTURES = Path(__file__).parent / "fixtures" / "colonial"
PAGE = (FIXTURES / "collection-section-2026-09-30.html").read_bytes()
TOTAL, CARDS = parse_cards(PAGE)


def _row(card: dict, index: int, **changes) -> dict:
    current = changes.get("current_price", card["current_price"])
    regular = changes.get("reported_regular_price", card["reported_regular_price"])
    return {
        "product_id": str(1000 + index), "item_id": card["item_id"], "source_key_type": "item_id",
        "source_key": card["item_id"], "source_name": card["handle"], "reference": None, "ean": None,
        "brand": None, "category": None, "presentation": None,
        "current_price": current, "reported_regular_price": regular,
        "is_promotion": regular is not None and float(regular) > float(current),
        "availability": "unknown", "handle": card["handle"],
    }


def _detail(card: dict, index: int, price: str, regular: str | None, *, variant_id: str | None = None) -> bytes:
    return json.dumps({"product": {
        "id": 1000 + index, "handle": card["handle"], "title": card["handle"], "vendor": "", "product_type": "",
        "variants": [{"id": int(variant_id or card["item_id"]), "product_id": 1000 + index, "price": price,
                      "compare_at_price": regular, "sku": None, "barcode": None, "title": "Default Title"}],
    }}).encode()


class FakeGet:
    def __init__(self, responses: dict[str, bytes]):
        self.responses, self.urls = responses, []

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        return self.responses[url]


def _stale_rows(index: int = 1, price: str = "139.99") -> list[dict]:
    """El JSON (leído al inicio) aún tenía el precio anterior de una tarjeta."""
    return [_row(card, i, current_price=price) if i == index else _row(card, i) for i, card in enumerate(CARDS)]


def _page_of() -> dict[str, int]:
    return {card["handle"]: 7 for card in CARDS}


def test_disagreements_list_only_products_whose_card_matches_no_variant():
    assert commercial_disagreements([_row(c, i) for i, c in enumerate(CARDS)], CARDS) == []
    assert commercial_disagreements(_stale_rows(), CARDS) == [CARDS[1]["handle"]]


def test_price_changed_mid_capture_is_resolved_with_fresh_reads():
    card = CARDS[1]
    get = FakeGet({
        f"{ORIGIN}/products/{card['handle']}.json": _detail(card, 1, card["current_price"], card["reported_regular_price"]),
        html_page_url(7, recheck=True): PAGE,
    })
    rows, cards, recheck = recheck_commercial_disagreements(get, _stale_rows(), list(CARDS), _page_of(), TOTAL)
    assert recheck == {"disagreements": 1, "handles": [card["handle"]], "resolved": 1}
    assert get.urls == [f"{ORIGIN}/products/{card['handle']}.json",
                        f"{ORIGIN}/collections/all?section_id=template--25869947109668__banner&page=7&recheck=1"]
    members = {ORIGIN + "/products/" + c["handle"] for c in CARDS}
    accepted = reconcile(rows, cards, members, len(CARDS))
    fixed = next(p for p in accepted if p["item_id"] == card["item_id"])
    assert (fixed["current_price"], fixed["reported_regular_price"], fixed["availability"]) == ("129.99", "169.99", "in_stock")


def test_no_disagreement_makes_no_extra_request():
    get = FakeGet({})
    rows = [_row(c, i) for i, c in enumerate(CARDS)]
    assert recheck_commercial_disagreements(get, rows, list(CARDS), _page_of(), TOTAL)[2] == {"disagreements": 0, "handles": []}
    assert get.urls == []


def test_sources_that_still_disagree_after_recheck_keep_failing_closed():
    card = CARDS[1]
    get = FakeGet({
        f"{ORIGIN}/products/{card['handle']}.json": _detail(card, 1, "139.99", card["reported_regular_price"]),
        html_page_url(7, recheck=True): PAGE,
    })
    rows, cards, recheck = recheck_commercial_disagreements(get, _stale_rows(), list(CARDS), _page_of(), TOTAL)
    assert recheck["resolved"] == 0
    with pytest.raises(ColonialError, match="commercial_sources_disagree"):
        reconcile(rows, cards, {ORIGIN + "/products/" + c["handle"] for c in CARDS}, len(CARDS))


def test_mass_disagreement_is_not_a_price_change_and_fails_without_requests():
    cards = [dict(CARDS[0], item_id=str(9000 + i), handle=f"p-{i}") for i in range(MAX_COMMERCIAL_RECHECKS + 1)]
    rows = [_row(card, i, current_price="1.00") for i, card in enumerate(cards)]
    get = FakeGet({})
    with pytest.raises(ColonialError, match="commercial_sources_disagree"):
        recheck_commercial_disagreements(get, rows, cards, {c["handle"]: 2 for c in cards}, TOTAL)
    assert get.urls == []


def test_identity_change_during_recheck_is_rejected():
    card = CARDS[1]
    get = FakeGet({
        f"{ORIGIN}/products/{card['handle']}.json": _detail(card, 1, "129.99", "169.99", variant_id="123456"),
        html_page_url(7, recheck=True): PAGE,
    })
    with pytest.raises(ColonialError, match="commercial_recheck_identity_changed"):
        recheck_commercial_disagreements(get, _stale_rows(), list(CARDS), _page_of(), TOTAL)


def test_card_that_moved_to_another_page_is_rejected():
    card = CARDS[1]
    moved = PAGE.replace(f'/products/{card["handle"]}'.encode(), b"/products/otro-producto")
    get = FakeGet({
        f"{ORIGIN}/products/{card['handle']}.json": _detail(card, 1, "129.99", "169.99"),
        html_page_url(7, recheck=True): moved,
    })
    with pytest.raises(ColonialError, match="commercial_recheck_card_moved"):
        recheck_commercial_disagreements(get, _stale_rows(), list(CARDS), _page_of(), TOTAL)


def test_product_detail_must_be_the_requested_handle():
    card = CARDS[0]
    with pytest.raises(ColonialError, match="product_detail_identity_invalid"):
        parse_product_detail(_detail(card, 0, "26.99", None), "otro-handle")
    with pytest.raises(ColonialError, match="product_detail_shape_invalid"):
        parse_product_detail(b'{"products": []}', card["handle"])


def test_apply_recheck_never_touches_other_products():
    rows = _stale_rows()
    card = CARDS[1]
    fresh = parse_product_detail(_detail(card, 1, "129.99", "169.99"), card["handle"])
    new_rows, new_cards = apply_commercial_recheck(rows, list(CARDS), card["handle"], fresh, CARDS[1])
    assert [r for r in new_rows if r["handle"] != card["handle"]] == [r for r in rows if r["handle"] != card["handle"]]
    assert new_cards == list(CARDS)


def test_first_page_recheck_url_uses_full_collection():
    assert html_page_url(1) == ORIGIN + "/collections/all"
    assert html_page_url(1, recheck=True) == ORIGIN + "/collections/all?recheck=1"
