"""Cambios entre ejecuciones del motor de precios seguros."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .price_analytics import MONEY, PERCENT, AnalyticsResult, PriceAnalyticsError


@dataclass(frozen=True, slots=True)
class OfferChange:
    canonical_product_id: str
    supermarket_id: str
    location_id: str
    previous_price_minor: int
    current_price_minor: int
    change_minor: int
    change_pct: Decimal

    @property
    def change(self) -> Decimal:
        return (Decimal(self.change_minor) / Decimal(100)).quantize(MONEY)


@dataclass(frozen=True, slots=True)
class BasketTotalChange:
    supermarket_id: str
    previous_total_minor: int
    current_total_minor: int
    change_minor: int
    change_pct: Decimal


@dataclass(frozen=True, slots=True)
class RunChangeSummary:
    comparable_products_previous: int
    comparable_products_current: int
    common_products_both_runs: int
    added_to_common_universe: tuple[str, ...]
    removed_from_common_universe: tuple[str, ...]
    changed_offers: tuple[OfferChange, ...]
    basket_changes: tuple[BasketTotalChange, ...]
    cheapest_supermarket_previous: str | None
    cheapest_supermarket_current: str | None


def _pct(change: int, previous: int) -> Decimal:
    if previous <= 0:
        return Decimal("0.00")
    return (Decimal(change) * Decimal(100) / Decimal(previous)).quantize(
        PERCENT,
        rounding=ROUND_HALF_UP,
    )


def compare_runs(previous: AnalyticsResult, current: AnalyticsResult) -> RunChangeSummary:
    """Explica qué cambió sin comparar universos o plazas incompatibles."""

    if previous.scope != current.scope:
        raise PriceAnalyticsError("run_comparison_scope_mismatch")

    previous_products = {item.canonical_product_id: item for item in previous.products}
    current_products = {item.canonical_product_id: item for item in current.products}
    previous_ids = set(previous_products)
    current_ids = set(current_products)
    common_ids = previous_ids & current_ids

    changed_offers: list[OfferChange] = []
    for canonical_id in sorted(common_ids):
        previous_offers = {
            (offer.supermarket_id, offer.location_id): offer
            for offer in previous_products[canonical_id].offers
        }
        current_offers = {
            (offer.supermarket_id, offer.location_id): offer
            for offer in current_products[canonical_id].offers
        }
        if set(previous_offers) != set(current_offers):
            raise PriceAnalyticsError("run_comparison_offer_scope_mismatch")
        for key in sorted(previous_offers):
            old = previous_offers[key]
            new = current_offers[key]
            if old.price_minor == new.price_minor:
                continue
            change = new.price_minor - old.price_minor
            changed_offers.append(
                OfferChange(
                    canonical_product_id=canonical_id,
                    supermarket_id=key[0],
                    location_id=key[1],
                    previous_price_minor=old.price_minor,
                    current_price_minor=new.price_minor,
                    change_minor=change,
                    change_pct=_pct(change, old.price_minor),
                )
            )

    # Totales de canasta sólo son comparables si ambos runs contienen el mismo
    # universo no vacío. Comparar dos sumas de conjunto vacío produciría un delta
    # de cero técnicamente correcto pero comercialmente engañoso.
    basket_changes: list[BasketTotalChange] = []
    if previous_ids and previous_ids == current_ids:
        old_totals = dict(previous.common_basket.totals_minor)
        new_totals = dict(current.common_basket.totals_minor)
        for supermarket in previous.scope.supermarket_ids:
            old = old_totals[supermarket]
            new = new_totals[supermarket]
            change = new - old
            basket_changes.append(
                BasketTotalChange(
                    supermarket_id=supermarket,
                    previous_total_minor=old,
                    current_total_minor=new,
                    change_minor=change,
                    change_pct=_pct(change, old),
                )
            )

    return RunChangeSummary(
        comparable_products_previous=len(previous_ids),
        comparable_products_current=len(current_ids),
        common_products_both_runs=len(common_ids),
        added_to_common_universe=tuple(sorted(current_ids - previous_ids)),
        removed_from_common_universe=tuple(sorted(previous_ids - current_ids)),
        changed_offers=tuple(changed_offers),
        basket_changes=tuple(basket_changes),
        cheapest_supermarket_previous=previous.common_basket.cheapest_supermarket_id,
        cheapest_supermarket_current=current.common_basket.cheapest_supermarket_id,
    )
