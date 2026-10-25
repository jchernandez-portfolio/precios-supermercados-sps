from __future__ import annotations

from datetime import UTC, datetime, timezone

import pytest

from precios_supermercados.analytics_quality import FreshnessStatus
from precios_supermercados.shopping_analytics import (
    BasketStatus,
    ConsumerOffer,
    ShoppingAnalyticsError,
    ShoppingSelection,
    build_manual_basket,
    evaluate_single_retailer_baskets,
    optimize_split_basket,
)

OBSERVED = datetime(2026, 9, 8, 11, tzinfo=UTC)


def offer(
    product: str,
    source: str,
    retailer: str,
    price: int | None,
    *,
    regular: int | None = None,
    promotion: bool | None = False,
    availability: str = "in_stock",
) -> ConsumerOffer:
    return ConsumerOffer(
        canonical_product_id=product,
        source_product_id=source,
        supermarket_id=retailer,
        location_id=f"{retailer}_sps",
        category="Lácteos",
        product_type="Leche",
        product_name=f"Leche {product}",
        brand="Sula",
        variant="Entera",
        presentation="1 L",
        current_price_minor=price,
        reported_regular_price_minor=regular,
        is_promotion=promotion,
        availability=availability,
        observed_at_utc=OBSERVED,
        freshness_status=FreshnessStatus.FRESH,
    )


def test_manual_basket_uses_current_promotion_price_and_preserves_descriptors() -> None:
    promoted = offer("milk", "walmart:1", "walmart", 2000, regular=3000, promotion=True)
    other = offer("yogurt", "colonia:2", "colonia", 1500)
    basket = build_manual_basket(
        (promoted, other),
        (ShoppingSelection("milk", "walmart:1", 2), ShoppingSelection("yogurt", "colonia:2", 3)),
    )

    assert basket.status is BasketStatus.COMPLETE
    assert basket.grand_total_minor == 8500
    assert basket.total_quantity == 5
    assert basket.retailer_count == 2
    walmart_line = next(group.lines[0] for group in basket.groups if group.supermarket_id == "walmart")
    assert walmart_line.unit_price_minor == 2000
    assert walmart_line.reported_regular_price_minor == 3000
    assert walmart_line.line_total_minor == 4000
    assert walmart_line.category == "Lácteos"
    assert walmart_line.product_type == "Leche"
    assert walmart_line.brand == "Sula"
    assert walmart_line.variant == "Entera"
    assert walmart_line.presentation == "1 L"
    # Exact observed-price arithmetic: neither reported regular price nor 15%
    # is added to a line, retailer subtotal, or grand total.
    assert basket.grand_total_minor != 9775


def test_retailer_subtotals_are_exact_sums_of_their_lines() -> None:
    offers = (
        offer("milk", "walmart:1", "walmart", 2001),
        offer("yogurt", "walmart:2", "walmart", 333),
    )
    basket = build_manual_basket(
        offers,
        (ShoppingSelection("milk", "walmart:1", 3), ShoppingSelection("yogurt", "walmart:2", 2)),
    )
    assert [line.line_total_minor for line in basket.groups[0].lines] == [6003, 666]
    assert basket.groups[0].subtotal_minor == 6669
    assert basket.grand_total_minor == 6669


def test_unavailable_item_makes_totals_incomplete_instead_of_zero() -> None:
    unavailable = offer(
        "milk", "walmart:1", "walmart", None,
        promotion=None, availability="out_of_stock",
    )
    basket = build_manual_basket(
        (unavailable,),
        (ShoppingSelection("milk", "walmart:1", 1),),
    )
    assert basket.status is BasketStatus.INCOMPLETE
    assert basket.groups[0].subtotal_minor is None
    assert basket.grand_total_minor is None
    assert basket.missing_count == 1


def test_single_retailer_total_requires_complete_coverage() -> None:
    offers = (
        offer("milk", "a:1", "a", 1000),
        offer("bread", "a:2", "a", 2000),
        offer("milk", "b:1", "b", 900),
    )
    options = evaluate_single_retailer_baskets(offers, {"milk": 2, "bread": 1})
    by_retailer = {option.supermarket_id: option for option in options}
    assert by_retailer["a"].status is BasketStatus.COMPLETE
    assert by_retailer["a"].total_minor == 4000
    assert by_retailer["a"].coverage_pct_minor == 10000
    assert by_retailer["b"].status is BasketStatus.INCOMPLETE
    assert by_retailer["b"].total_minor is None
    assert by_retailer["b"].coverage_pct_minor == 5000


def test_split_basket_selects_cheapest_offer_and_reports_retailer_count() -> None:
    offers = (
        offer("milk", "a:1", "a", 1000),
        offer("milk", "b:1", "b", 900),
        offer("bread", "a:2", "a", 1800),
        offer("bread", "b:2", "b", 2000),
    )
    basket = optimize_split_basket(offers, {"milk": 2, "bread": 1})
    assert basket.grand_total_minor == 3600
    assert basket.retailer_count == 2
    assert {line.source_product_id for group in basket.groups for line in group.lines} == {"b:1", "a:2"}


def test_manual_selection_is_never_replaced_by_a_cheaper_retailer() -> None:
    offers = (offer("milk", "a:1", "a", 1000), offer("milk", "b:1", "b", 900))
    basket = build_manual_basket(offers, (ShoppingSelection("milk", "a:1", 1),))
    assert basket.groups[0].supermarket_id == "a"
    assert basket.grand_total_minor == 1000


def test_shipping_or_service_fee_cannot_enter_the_product_contract() -> None:
    with pytest.raises(TypeError):
        ConsumerOffer(  # type: ignore[call-arg]
            canonical_product_id="milk",
            source_product_id="a:1",
            supermarket_id="a",
            location_id="a_sps",
            category=None,
            product_type=None,
            product_name="Milk",
            brand=None,
            variant=None,
            presentation=None,
            current_price_minor=1000,
            reported_regular_price_minor=None,
            is_promotion=False,
            availability="in_stock",
            observed_at_utc=OBSERVED,
            freshness_status="FRESH",
            shipping_fee_minor=500,
        )


def test_quantity_and_identity_validation_fail_closed() -> None:
    with pytest.raises(ShoppingAnalyticsError, match="selection_quantity_invalid"):
        ShoppingSelection("milk", "a:1", 0)
    with pytest.raises(ShoppingAnalyticsError, match="selection_offer_unknown"):
        build_manual_basket((offer("milk", "a:1", "a", 1000),), (ShoppingSelection("milk", "x:1", 1),))
