from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timezone
from pathlib import Path

from precios_supermercados.scrapers.la_colonia import LaColoniaExtractor

FIXTURE = Path(__file__).parent / "fixtures" / "la_colonia" / "product_search_page.json"
FIXED_TIME = datetime(2026, 8, 5, 14, 0, tzinfo=UTC)


def _base_product():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return payload["data"]["productSearch"]["products"][0]


def _payload(*, products_returned: int, total_products: int):
    template = _base_product()
    products = []
    for index in range(products_returned):
        product = deepcopy(template)
        product_id = str(3000 + index)
        item_id = str(4000 + index)
        product["productId"] = product_id
        product["productName"] = f"Producto controlado {index + 1} 473 Ml"
        product["productReference"] = f"PROD-{product_id}"
        product["linkText"] = f"producto-controlado-{index + 1}-473-ml"
        item = product["items"][0]
        item["itemId"] = item_id
        item["name"] = "473 Ml"
        item["nameComplete"] = f"Producto controlado {index + 1} 473 Ml"
        item["ean"] = f"74210000{item_id}"
        item["referenceId"] = [{"Key": "RefId", "Value": f"SKU-{item_id}"}]
        products.append(product)
    return {
        "data": {
            "productSearch": {
                "recordsFiltered": total_products,
                "products": products,
            }
        }
    }


def _parse(payload, *, page: int, page_size: int):
    extractor = LaColoniaExtractor(clock=lambda: FIXED_TIME)
    return extractor.parse_payload(
        payload,
        scrape_run_id="run_acceptance_test",
        source_url=extractor.build_page_url(page=page, page_size=page_size),
        page_size=page_size,
    )


def test_complete_intermediate_page_is_accepted():
    result = _parse(
        _payload(products_returned=10, total_products=25),
        page=1,
        page_size=10,
    )

    assert result.metrics.products_requested == 10
    assert result.metrics.products_returned == 10
    assert result.metrics.errors == 0
    assert result.metrics.structural_events == 0
    assert result.accepted is True
    assert "quality:partial_product_page" not in result.quality_events


def test_partial_intermediate_page_is_rejected():
    result = _parse(
        _payload(products_returned=4, total_products=25),
        page=1,
        page_size=10,
    )

    assert result.metrics.products_requested == 10
    assert result.metrics.products_returned == 4
    assert result.metrics.skus_with_price == 4
    assert result.accepted is False
    assert "quality:partial_product_page" in result.quality_events


def test_legitimate_last_page_with_four_remaining_products_is_accepted():
    result = _parse(
        _payload(products_returned=4, total_products=14),
        page=2,
        page_size=10,
    )

    assert result.metrics.products_requested == 10
    assert result.metrics.products_returned == 4
    assert result.metrics.errors == 0
    assert result.accepted is True
    assert "quality:partial_product_page" not in result.quality_events


def test_discarded_invalid_sku_rejects_page_even_when_other_skus_have_price():
    payload = _payload(products_returned=10, total_products=25)
    payload["data"]["productSearch"]["products"][0]["items"][0] = "invalid-sku"

    result = _parse(payload, page=1, page_size=10)

    assert result.metrics.products_returned == 10
    assert result.metrics.skus_with_price == 9
    assert result.metrics.skus_extracted == 9
    assert result.metrics.errors == 1
    assert result.accepted is False
    assert "quality:item_not_mapping" in result.quality_events


def test_structural_event_rejects_page():
    payload = _payload(products_returned=10, total_products=25)
    payload["data"]["productSearch"]["products"][0]["items"] = None

    result = _parse(payload, page=1, page_size=10)

    assert result.metrics.skus_with_price == 9
    assert result.metrics.structural_events == 1
    assert result.metrics.errors == 1
    assert result.accepted is False
    assert "structure:missing_items" in result.quality_events


def test_duplicate_sku_is_deduplicated_without_rejecting_valid_complete_page():
    payload = _payload(products_returned=10, total_products=25)
    first_items = payload["data"]["productSearch"]["products"][0]["items"]
    first_items.append(deepcopy(first_items[0]))

    result = _parse(payload, page=1, page_size=10)

    assert result.metrics.products_returned == 10
    assert result.metrics.skus_returned == 11
    assert result.metrics.skus_extracted == 10
    assert result.metrics.duplicate_skus == 1
    assert result.metrics.errors == 0
    assert result.accepted is True
    assert "quality:duplicate_source_key" in result.quality_events
