"""Deterministic shopping-list calculations for the B2C product.

Totals use the accepted effective price exactly as observed.  This module does
not model tax, delivery, service, shipping, membership, or checkout fees.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from .analytics_quality import FreshnessStatus


class ShoppingAnalyticsError(ValueError):
    pass


class BasketStatus(StrEnum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"


@dataclass(frozen=True, slots=True)
class ConsumerOffer:
    canonical_product_id: str
    source_product_id: str
    supermarket_id: str
    location_id: str
    category: str | None
    product_type: str | None
    product_name: str
    brand: str | None
    variant: str | None
    presentation: str | None
    current_price_minor: int | None
    reported_regular_price_minor: int | None
    is_promotion: bool | None
    availability: str
    observed_at_utc: datetime
    freshness_status: FreshnessStatus | str

    def __post_init__(self) -> None:
        for name in (
            "canonical_product_id",
            "source_product_id",
            "supermarket_id",
            "location_id",
            "product_name",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ShoppingAnalyticsError(f"{name}_missing")
            object.__setattr__(self, name, value.strip())
        for name in ("category", "product_type", "brand", "variant", "presentation"):
            value = getattr(self, name)
            if value is not None:
                if not isinstance(value, str):
                    raise ShoppingAnalyticsError(f"{name}_invalid")
                object.__setattr__(self, name, value.strip() or None)
        if self.current_price_minor is not None and (
            type(self.current_price_minor) is not int or self.current_price_minor <= 0
        ):
            raise ShoppingAnalyticsError("current_price_minor_invalid")
        if self.reported_regular_price_minor is not None and (
            type(self.reported_regular_price_minor) is not int
            or self.reported_regular_price_minor <= 0
        ):
            raise ShoppingAnalyticsError("reported_regular_price_minor_invalid")
        if self.is_promotion is not None and not isinstance(self.is_promotion, bool):
            raise ShoppingAnalyticsError("is_promotion_invalid")
        if self.availability not in {"in_stock", "out_of_stock", "unknown"}:
            raise ShoppingAnalyticsError("availability_invalid")
        if self.observed_at_utc.tzinfo is None or self.observed_at_utc.utcoffset() is None:
            raise ShoppingAnalyticsError("observed_at_not_timezone_aware")
        object.__setattr__(self, "observed_at_utc", self.observed_at_utc.astimezone(UTC))
        try:
            freshness = FreshnessStatus(self.freshness_status)
        except (TypeError, ValueError) as exc:
            raise ShoppingAnalyticsError("freshness_status_invalid") from exc
        object.__setattr__(self, "freshness_status", freshness)
        if self.current_price_minor is None and self.is_promotion is not None:
            raise ShoppingAnalyticsError("unpriced_offer_promotion_must_be_unknown")

    @property
    def buyable(self) -> bool:
        return (
            self.current_price_minor is not None
            and self.availability != "out_of_stock"
            and self.freshness_status is not FreshnessStatus.UNAVAILABLE
        )


@dataclass(frozen=True, slots=True)
class ShoppingSelection:
    canonical_product_id: str
    source_product_id: str
    quantity: int

    def __post_init__(self) -> None:
        if not self.canonical_product_id.strip() or not self.source_product_id.strip():
            raise ShoppingAnalyticsError("selection_identity_missing")
        if type(self.quantity) is not int or self.quantity <= 0:
            raise ShoppingAnalyticsError("selection_quantity_invalid")


@dataclass(frozen=True, slots=True)
class ShoppingLine:
    canonical_product_id: str
    source_product_id: str
    supermarket_id: str
    location_id: str
    category: str | None
    product_type: str | None
    product_name: str
    brand: str | None
    variant: str | None
    presentation: str | None
    quantity: int
    unit_price_minor: int | None
    reported_regular_price_minor: int | None
    is_promotion: bool | None
    line_total_minor: int | None
    availability: str
    observed_at_utc: datetime
    freshness_status: FreshnessStatus


@dataclass(frozen=True, slots=True)
class RetailerShoppingGroup:
    supermarket_id: str
    location_id: str
    lines: tuple[ShoppingLine, ...]
    subtotal_minor: int | None
    status: BasketStatus


@dataclass(frozen=True, slots=True)
class ShoppingBasket:
    groups: tuple[RetailerShoppingGroup, ...]
    grand_total_minor: int | None
    status: BasketStatus
    distinct_product_count: int
    total_quantity: int
    retailer_count: int
    missing_count: int
    stale_item_count: int


@dataclass(frozen=True, slots=True)
class RetailerBasketOption:
    supermarket_id: str
    location_id: str
    requested_product_count: int
    covered_product_count: int
    coverage_pct_minor: int
    total_minor: int | None
    status: BasketStatus


def _offer_index(offers: Iterable[ConsumerOffer]) -> dict[str, ConsumerOffer]:
    result: dict[str, ConsumerOffer] = {}
    for offer in offers:
        if offer.source_product_id in result:
            raise ShoppingAnalyticsError("consumer_offer_duplicate")
        result[offer.source_product_id] = offer
    return result


def build_manual_basket(
    offers: Iterable[ConsumerOffer],
    selections: Iterable[ShoppingSelection],
) -> ShoppingBasket:
    """Honor exact user retailer selections and calculate observed-price totals."""

    index = _offer_index(offers)
    selected = tuple(selections)
    if not selected:
        return ShoppingBasket((), 0, BasketStatus.COMPLETE, 0, 0, 0, 0, 0)
    canonical_ids = [selection.canonical_product_id for selection in selected]
    if len(set(canonical_ids)) != len(canonical_ids):
        raise ShoppingAnalyticsError("selection_canonical_product_duplicate")

    lines_by_scope: dict[tuple[str, str], list[ShoppingLine]] = {}
    for selection in selected:
        offer = index.get(selection.source_product_id)
        if offer is None:
            raise ShoppingAnalyticsError("selection_offer_unknown")
        if offer.canonical_product_id != selection.canonical_product_id:
            raise ShoppingAnalyticsError("selection_product_offer_mismatch")
        unit_price = offer.current_price_minor if offer.buyable else None
        line = ShoppingLine(
            canonical_product_id=offer.canonical_product_id,
            source_product_id=offer.source_product_id,
            supermarket_id=offer.supermarket_id,
            location_id=offer.location_id,
            category=offer.category,
            product_type=offer.product_type,
            product_name=offer.product_name,
            brand=offer.brand,
            variant=offer.variant,
            presentation=offer.presentation,
            quantity=selection.quantity,
            unit_price_minor=unit_price,
            reported_regular_price_minor=offer.reported_regular_price_minor,
            is_promotion=offer.is_promotion,
            line_total_minor=None if unit_price is None else unit_price * selection.quantity,
            availability=offer.availability,
            observed_at_utc=offer.observed_at_utc,
            freshness_status=offer.freshness_status,
        )
        lines_by_scope.setdefault((offer.supermarket_id, offer.location_id), []).append(line)

    groups: list[RetailerShoppingGroup] = []
    for (supermarket_id, location_id), group_lines in sorted(lines_by_scope.items()):
        ordered = tuple(sorted(group_lines, key=lambda line: (line.product_name.casefold(), line.source_product_id)))
        complete = all(line.line_total_minor is not None for line in ordered)
        groups.append(
            RetailerShoppingGroup(
                supermarket_id=supermarket_id,
                location_id=location_id,
                lines=ordered,
                subtotal_minor=(
                    sum(line.line_total_minor for line in ordered if line.line_total_minor is not None)
                    if complete
                    else None
                ),
                status=BasketStatus.COMPLETE if complete else BasketStatus.INCOMPLETE,
            )
        )

    complete = all(group.status is BasketStatus.COMPLETE for group in groups)
    missing_count = sum(line.line_total_minor is None for group in groups for line in group.lines)
    return ShoppingBasket(
        groups=tuple(groups),
        grand_total_minor=(
            sum(group.subtotal_minor for group in groups if group.subtotal_minor is not None)
            if complete
            else None
        ),
        status=BasketStatus.COMPLETE if complete else BasketStatus.INCOMPLETE,
        distinct_product_count=len(selected),
        total_quantity=sum(selection.quantity for selection in selected),
        retailer_count=len(groups),
        missing_count=missing_count,
        stale_item_count=sum(
            line.freshness_status is FreshnessStatus.STALE
            for group in groups
            for line in group.lines
        ),
    )


def evaluate_single_retailer_baskets(
    offers: Iterable[ConsumerOffer],
    quantities: Mapping[str, int],
) -> tuple[RetailerBasketOption, ...]:
    """Price full baskets only; missing products remain explicit coverage gaps."""

    if not quantities or any(type(value) is not int or value <= 0 for value in quantities.values()):
        raise ShoppingAnalyticsError("basket_quantities_invalid")
    by_scope: dict[tuple[str, str], dict[str, ConsumerOffer]] = {}
    for offer in offers:
        scope = (offer.supermarket_id, offer.location_id)
        previous = by_scope.setdefault(scope, {}).setdefault(offer.canonical_product_id, offer)
        if previous.source_product_id != offer.source_product_id:
            raise ShoppingAnalyticsError("basket_offer_scope_ambiguous")

    options: list[RetailerBasketOption] = []
    requested = len(quantities)
    for (supermarket_id, location_id), product_offers in sorted(by_scope.items()):
        usable = {
            product_id: product_offers[product_id]
            for product_id in quantities
            if product_id in product_offers and product_offers[product_id].buyable
        }
        covered = len(usable)
        complete = covered == requested
        options.append(
            RetailerBasketOption(
                supermarket_id=supermarket_id,
                location_id=location_id,
                requested_product_count=requested,
                covered_product_count=covered,
                coverage_pct_minor=(covered * 10000) // requested,
                total_minor=(
                    sum(offer.current_price_minor * quantities[product_id] for product_id, offer in usable.items())
                    if complete
                    else None
                ),
                status=BasketStatus.COMPLETE if complete else BasketStatus.INCOMPLETE,
            )
        )
    return tuple(options)


def optimize_split_basket(
    offers: Iterable[ConsumerOffer],
    quantities: Mapping[str, int],
) -> ShoppingBasket:
    """Choose the cheapest valid offer per product with deterministic ties."""

    if not quantities or any(type(value) is not int or value <= 0 for value in quantities.values()):
        raise ShoppingAnalyticsError("basket_quantities_invalid")
    by_product: dict[str, list[ConsumerOffer]] = {product_id: [] for product_id in quantities}
    all_offers = tuple(offers)
    for offer in all_offers:
        if offer.canonical_product_id in by_product and offer.buyable:
            by_product[offer.canonical_product_id].append(offer)
    missing = [product_id for product_id, choices in by_product.items() if not choices]
    if missing:
        raise ShoppingAnalyticsError("optimized_basket_product_unavailable")

    selected = []
    for product_id, choices in sorted(by_product.items()):
        winner = min(
            choices,
            key=lambda offer: (
                offer.current_price_minor,
                offer.supermarket_id,
                offer.location_id,
                offer.source_product_id,
            ),
        )
        selected.append(ShoppingSelection(product_id, winner.source_product_id, quantities[product_id]))
    return build_manual_basket(all_offers, selected)
