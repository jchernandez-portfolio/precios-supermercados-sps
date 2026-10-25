import json
from copy import deepcopy
from datetime import UTC, datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from precios_supermercados.enums import AvailabilityStatus
from precios_supermercados.scrapers.la_colonia import LaColoniaExtractor
from precios_supermercados.scrapers.la_colonia_graphql import (
    PRODUCT_SEARCH_QUERY,
    build_product_search_url,
)

FIXTURE = Path(__file__).parent / "fixtures" / "la_colonia" / "product_search_page.json"
FIXED_TIME = datetime(2026, 8, 5, 4, 30, tzinfo=UTC)


def decoded_variables(url: str):
    return json.loads(parse_qs(urlsplit(url).query)["variables"][0])


def test_live_url_uses_explicit_public_graphql_query_without_persisted_hash():
    url = build_product_search_url(page=1, page_size=10)
    query = parse_qs(urlsplit(url).query)

    assert urlsplit(url).path == "/_v/segment/graphql/v1"
    assert query["operationName"] == ["productSearchV3"]
    assert "productSearch" in query["query"][0]
    assert "persistedQuery" not in query
    assert decoded_variables(url)["to"] == 9


def test_skus_filter_is_all_in_query_and_variables():
    url = build_product_search_url(page=1, page_size=10)
    variables = decoded_variables(url)

    assert "$skusFilter: ItemsFilter = ALL" in PRODUCT_SEARCH_QUERY
    assert "items(filter: $skusFilter)" in PRODUCT_SEARCH_QUERY
    assert variables["skusFilter"] == "ALL"


def test_all_available_is_not_the_default_filter_anywhere():
    url = build_product_search_url(page=1, page_size=10)
    variables = decoded_variables(url)

    assert "ALL_AVAILABLE" not in PRODUCT_SEARCH_QUERY
    assert variables["skusFilter"] != "ALL_AVAILABLE"


def test_hide_unavailable_items_and_skus_filter_have_separate_values():
    variables = decoded_variables(build_product_search_url(page=1, page_size=10))

    assert variables["hideUnavailableItems"] is False
    assert variables["skusFilter"] == "ALL"


def test_directed_full_text_search_removes_category_facet_but_keeps_all_skus():
    variables = decoded_variables(
        build_product_search_url(page=1, page_size=3, full_text="fresa")
    )

    assert variables["query"] == ""
    assert variables["fullText"] == "fresa"
    assert variables["selectedFacets"] == []
    assert variables["skusFilter"] == "ALL"
    assert variables["hideUnavailableItems"] is False


def test_positive_price_with_zero_quantity_is_unknown_not_out_of_stock():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    modified = deepcopy(payload)
    offer = modified["data"]["productSearch"]["products"][0]["items"][0]["sellers"][0]["commercialOffer"]
    offer["Price"] = 19.9
    offer["ListPrice"] = 19.9
    offer["AvailableQuantity"] = 0

    extractor = LaColoniaExtractor(clock=lambda: FIXED_TIME)
    result = extractor.parse_payload(
        modified,
        scrape_run_id="run_conflicting_availability",
        source_url=extractor.build_page_url(),
        page_size=5,
    )

    first = result.products[0]
    assert first.raw_values["availability"] == AvailabilityStatus.UNKNOWN.value
    assert first.raw_values["availability_evidence"] == "price_positive_quantity_zero"
    assert "quality:availability_conflict_price_with_zero_quantity" in result.quality_events
