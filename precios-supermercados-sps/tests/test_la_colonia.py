from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timezone
from pathlib import Path

import pytest

from precios_supermercados.enums import AvailabilityStatus, LocationStatus, SourceKeyType
from precios_supermercados.scrapers.base import (
    BlockedResponseError,
    EmptyResponseError,
    HttpResponse,
    HttpStatusError,
    OfflineTestTransport,
    RateLimitedError,
    RobotsPolicyError,
    SafeHttpClient,
    StructureChangedError,
)
from precios_supermercados.scrapers.la_colonia import (
    LOCATION_EVIDENCE,
    LOCATION_ID,
    LaColoniaExtractor,
    decode_search_variables,
)

FIXTURES = Path(__file__).parent / "fixtures" / "la_colonia"
FIXED_TIME = datetime(2026, 8, 5, 4, 30, tzinfo=UTC)


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def build_extractor() -> LaColoniaExtractor:
    return LaColoniaExtractor(clock=lambda: FIXED_TIME)


def parse_fixture(name: str = "product_search_page.json"):
    extractor = build_extractor()
    return extractor.parse_payload(
        load_fixture(name),
        scrape_run_id="run_la_colonia_test",
        source_url=extractor.build_page_url(),
        page_size=5,
    )


def add_sku(
    payload,
    *,
    item_id: str,
    name_complete: str,
    price,
    list_price,
    quantity,
):
    item = deepcopy(payload["data"]["productSearch"]["products"][0]["items"][0])
    item["itemId"] = item_id
    item["name"] = name_complete
    item["nameComplete"] = name_complete
    item["ean"] = f"742100000{item_id}"
    item["referenceId"] = [{"Key": "RefId", "Value": f"SKU-{item_id}"}]
    offer = item["sellers"][0]["commercialOffer"]
    offer["Price"] = price
    offer["ListPrice"] = list_price
    offer["AvailableQuantity"] = quantity
    offer["discountHighlights"] = []
    offer["teasers"] = []
    payload["data"]["productSearch"]["products"][0]["items"].append(item)
    return item


def parse_payload(payload, *, page_size: int = 5):
    extractor = build_extractor()
    return extractor.parse_payload(
        payload,
        scrape_run_id="run_custom",
        source_url=extractor.build_page_url(page_size=page_size),
        page_size=page_size,
    )


def test_parses_five_products_and_uses_raw_product_contract():
    result = parse_fixture()
    assert result.accepted is True
    assert len(result.products) == 5
    assert result.metrics.products_requested == 5
    assert result.metrics.products_returned == 5
    assert result.metrics.skus_returned == 5
    assert result.metrics.skus_extracted == 5
    assert result.metrics.skus_with_price == 3
    assert all(product.supermarket_id == "la_colonia" for product in result.products)


def test_product_with_one_sku_produces_one_raw_product():
    result = parse_fixture()
    product_skus = [
        product for product in result.products if product.raw_values["product_id"] == "1001"
    ]
    assert len(product_skus) == 1
    assert product_skus[0].source_key == "2001"


def test_product_with_multiple_skus_produces_one_raw_product_per_sku():
    payload = load_fixture("product_search_page.json")
    add_sku(
        payload,
        item_id="2010",
        name_complete="Malteada Sula Chocosula 946 Ml",
        price=34.9,
        list_price=34.9,
        quantity=8,
    )
    add_sku(
        payload,
        item_id="2011",
        name_complete="Malteada Sula Chocosula Sin Existencia 1 Lt",
        price=0,
        list_price=0,
        quantity=0,
    )

    result = parse_payload(payload)
    product_skus = [
        product for product in result.products if product.raw_values["product_id"] == "1001"
    ]

    assert len(product_skus) == 3
    assert {product.source_key for product in product_skus} == {"2001", "2010", "2011"}
    assert result.metrics.products_returned == 5
    assert result.metrics.skus_returned == 7
    assert result.metrics.skus_extracted == 7


def test_five_products_with_first_multisku_do_not_omit_later_products():
    payload = load_fixture("product_search_page.json")
    add_sku(
        payload,
        item_id="2010",
        name_complete="Malteada Sula Chocosula 946 Ml",
        price=34.9,
        list_price=34.9,
        quantity=8,
    )
    add_sku(
        payload,
        item_id="2011",
        name_complete="Malteada Sula Chocosula Sin Precio 1 Lt",
        price=0,
        list_price=0,
        quantity=None,
    )

    result = parse_payload(payload)
    product_ids = {product.raw_values["product_id"] for product in result.products}

    assert product_ids == {"1001", "1002", "1003", "1004", "1005"}
    assert result.products[-1].raw_values["product_id"] == "1005"
    assert len(result.products) == 7


def test_available_no_price_and_exhausted_skus_are_all_preserved():
    payload = load_fixture("product_search_page.json")
    add_sku(
        payload,
        item_id="2010",
        name_complete="Malteada Sula Chocosula Sin Precio 946 Ml",
        price=0,
        list_price=0,
        quantity=None,
    )
    add_sku(
        payload,
        item_id="2011",
        name_complete="Malteada Sula Chocosula Agotada 1 Lt",
        price=0,
        list_price=0,
        quantity=0,
    )

    result = parse_payload(payload)
    by_key = {product.source_key: product for product in result.products}

    assert by_key["2001"].raw_values["availability"] == AvailabilityStatus.IN_STOCK.value
    assert by_key["2010"].raw_values["current_price"] is None
    assert by_key["2010"].raw_values["availability"] == AvailabilityStatus.UNKNOWN.value
    assert by_key["2011"].raw_values["current_price"] is None
    assert by_key["2011"].raw_values["availability"] == AvailabilityStatus.OUT_OF_STOCK.value
    assert AvailabilityStatus.NOT_LISTED.value not in {
        product.raw_values["availability"] for product in result.products
    }


def test_packaged_product_has_price_brand_category_and_presentation():
    product = parse_fixture().products[0]
    assert product.source_name == "Malteada Sula Chocosula 473 Ml"
    assert product.source_brand == "Sula"
    assert product.source_presentation == "473 Ml"
    assert product.source_category.endswith("Leches saborizadas")
    assert product.raw_values["current_price"] == "19.9"
    assert product.raw_values["availability"] == AvailabilityStatus.IN_STOCK.value


def test_weighted_product_preserves_original_unit_without_conversion():
    product = parse_fixture().products[1]
    assert product.source_name == "Fresa X Lb"
    assert product.source_presentation == "X Lb"
    assert product.raw_values["measurement_unit"] == "lb"
    assert product.raw_values["unit_multiplier"] == "1"
    assert product.raw_values["weighted_product"] is True
    assert product.raw_values["current_price"] == "94.9"


def test_promotional_product_separates_current_and_reported_regular_price():
    product = parse_fixture().products[2]
    assert product.raw_values["current_price"] == "59.9"
    assert product.raw_values["reported_regular_price"] == "99.9"
    assert product.raw_values["is_promotion"] is True
    assert product.raw_values["promotion_evidence"] == ["40%"]


def test_equal_list_price_is_not_reported_as_tachado_or_promotion():
    product = parse_fixture().products[0]
    assert product.raw_values["source_list_price"] == "19.9"
    assert product.raw_values["reported_regular_price"] is None
    assert product.raw_values["is_promotion"] is False


def test_explicit_zero_quantity_is_out_of_stock():
    product = parse_fixture().products[3]
    assert product.raw_values["current_price"] is None
    assert product.raw_values["availability"] == AvailabilityStatus.OUT_OF_STOCK.value


def test_missing_price_and_seller_remain_unknown():
    product = parse_fixture().products[4]
    assert product.raw_values["current_price"] is None
    assert product.raw_values["availability"] == AvailabilityStatus.UNKNOWN.value


def test_missing_brand_category_and_presentation_are_not_invented():
    product = parse_fixture().products[4]
    assert product.source_brand is None
    assert product.source_category is None
    assert product.source_presentation is None
    assert product.image_url is None


def test_source_identity_prefers_internal_item_id():
    product = parse_fixture().products[0]
    assert product.source_key_type is SourceKeyType.INTERNAL_ID
    assert product.source_key == "2001"
    assert product.source_sku == "161729"


def test_product_has_stable_individual_url_and_source_image():
    product = parse_fixture().products[1]
    assert product.product_url == "https://www.lacolonia.com/fresa-x-lb-ref536/p"
    assert product.image_url.startswith("https://lacolonia.vtexassets.com/")


def test_uses_approved_unknown_online_location_values():
    product = parse_fixture().products[0]
    assert product.location_id == LOCATION_ID == "la_colonia_online"
    assert product.location_status is LocationStatus.UNKNOWN
    assert product.location_evidence == LOCATION_EVIDENCE
    assert product.location_confidence is None


def test_pagination_uses_non_overlapping_product_indices():
    extractor = build_extractor()
    first = decode_search_variables(extractor.build_page_url(page=1, page_size=10))
    second = decode_search_variables(extractor.build_page_url(page=2, page_size=10))
    assert (first["from"], first["to"]) == (0, 9)
    assert (second["from"], second["to"]) == (10, 19)


def test_metrics_separate_products_and_skus():
    payload = load_fixture("product_search_page.json")
    add_sku(
        payload,
        item_id="2010",
        name_complete="Malteada Sula Chocosula 946 Ml",
        price=34.9,
        list_price=34.9,
        quantity=8,
    )
    add_sku(
        payload,
        item_id="2011",
        name_complete="Malteada Sula Chocosula Agotada 1 Lt",
        price=0,
        list_price=0,
        quantity=0,
    )

    result = parse_payload(payload)
    metrics = result.metrics

    assert metrics.products_discovered == 25
    assert metrics.products_requested == 5
    assert metrics.products_returned == 5
    assert metrics.skus_returned == 7
    assert metrics.skus_extracted == 7
    assert metrics.skus_with_price == 4
    assert metrics.skus_pending_review == 3
    assert metrics.duplicate_skus == 0
    assert metrics.pages_discovered == 5
    assert metrics.pages_processed == 1
    assert metrics.page_coverage == pytest.approx(0.2)


def test_duplicate_sku_is_deduplicated_by_stable_source_key():
    payload = load_fixture("product_search_page.json")
    duplicate = deepcopy(payload["data"]["productSearch"]["products"][0]["items"][0])
    payload["data"]["productSearch"]["products"][0]["items"].append(duplicate)
    result = parse_payload(payload)

    assert len(result.products) == 5
    assert result.metrics.products_returned == 5
    assert result.metrics.skus_returned == 6
    assert result.metrics.skus_extracted == 5
    assert result.metrics.duplicate_skus == 1
    assert "quality:duplicate_source_key" in result.quality_events


def test_empty_response_is_rejected():
    with pytest.raises(EmptyResponseError):
        parse_fixture("empty_response.json")


def test_structural_change_is_rejected():
    with pytest.raises(StructureChangedError, match="productSearch"):
        parse_fixture("structure_changed.json")


def test_graphql_errors_are_reported_as_structural_change():
    payload = {"data": {}, "errors": [{"message": "PersistedQueryNotFound"}]}
    with pytest.raises(StructureChangedError, match="GraphQL"):
        parse_payload(payload)


def test_page_without_any_price_is_not_accepted():
    payload = load_fixture("product_search_page.json")
    for product in payload["data"]["productSearch"]["products"]:
        for item in product["items"]:
            for seller in item["sellers"]:
                seller["commercialOffer"]["Price"] = 0
                seller["commercialOffer"]["ListPrice"] = 0
                seller["commercialOffer"]["AvailableQuantity"] = 0
    result = parse_payload(payload)
    assert result.accepted is False
    assert result.metrics.skus_with_price == 0
    assert "quality:missing_all_prices" in result.quality_events


def test_client_blocks_robots_excluded_routes_before_transport():
    calls = []

    def transport(url, headers, timeout):
        calls.append(url)
        raise AssertionError("No debe ejecutarse el transporte")

    client = SafeHttpClient(
        allowed_hosts={"www.lacolonia.com"},
        forbidden_path_prefixes=("/api", "/busca", "/buscapagina"),
        user_agent="test",
        transport=OfflineTestTransport(transport),
    )
    for url in (
        "https://www.lacolonia.com/api/catalog_system/pub/products/search",
        "https://www.lacolonia.com/buscapagina?PageNumber=1",
        "https://www.lacolonia.com/busca?q=arroz",
    ):
        with pytest.raises(RobotsPolicyError):
            client.get(url)
    assert calls == []


def test_client_blocks_mobile_domain():
    client = SafeHttpClient(
        allowed_hosts={"www.lacolonia.com"},
        forbidden_path_prefixes=("/api",),
        user_agent="test",
        transport=OfflineTestTransport(lambda *_: None),
    )
    with pytest.raises(RobotsPolicyError, match="Host"):
        client.get("https://mobile.lacolonia.com/supermercado")


def test_client_stops_on_403_and_captcha():
    forbidden = SafeHttpClient(
        allowed_hosts={"www.lacolonia.com"},
        forbidden_path_prefixes=("/api",),
        user_agent="test",
        transport=OfflineTestTransport(
            lambda url, headers, timeout: HttpResponse(403, url, {}, b"blocked")
        ),
    )
    with pytest.raises(BlockedResponseError):
        forbidden.get("https://www.lacolonia.com/_v/segment/graphql/v1")
    captcha = SafeHttpClient(
        allowed_hosts={"www.lacolonia.com"},
        forbidden_path_prefixes=("/api",),
        user_agent="test",
        transport=OfflineTestTransport(
            lambda url, headers, timeout: HttpResponse(200, url, {}, b"CAPTCHA")
        ),
    )
    with pytest.raises(BlockedResponseError):
        captcha.get("https://www.lacolonia.com/_v/segment/graphql/v1")


def test_client_rejects_retry_configuration_and_never_hides_second_attempt():
    calls = []

    def transport(url, headers, timeout):
        calls.append(url)
        return HttpResponse(500, url, {}, b"error")

    with pytest.raises(ValueError, match="max_retries debe ser 0"):
        SafeHttpClient(
            allowed_hosts={"www.lacolonia.com"},
            forbidden_path_prefixes=("/api",),
            user_agent="test",
            max_retries=1,
            retry_delay_seconds=0,
            transport=OfflineTestTransport(transport),
            sleeper=lambda _: None,
        )
    assert calls == []


def test_client_respects_429_and_then_stops():
    sleeps = []
    client = SafeHttpClient(
        allowed_hosts={"www.lacolonia.com"},
        forbidden_path_prefixes=("/api",),
        user_agent="test",
        max_retries=0,
        retry_delay_seconds=0,
        transport=OfflineTestTransport(
            lambda url, headers, timeout: HttpResponse(
                429, url, {"Retry-After": "2"}, b"rate limited"
            )
        ),
        sleeper=sleeps.append,
    )
    with pytest.raises(RateLimitedError):
        client.get("https://www.lacolonia.com/_v/segment/graphql/v1")
    assert sleeps == []


def test_live_limit_cannot_exceed_ten_products():
    with pytest.raises(ValueError, match="entre 1 y 10"):
        build_extractor().build_page_url(page_size=11)
