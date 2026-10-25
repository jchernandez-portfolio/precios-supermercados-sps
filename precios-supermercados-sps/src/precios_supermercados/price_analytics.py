"""Motor analítico conservador para precios homologados.

Este módulo no decide identidad de producto: consume exclusivamente grupos que
``safe_comparator`` marca como ``comparable``. Cualquier grupo ambiguo queda
fuera de ahorros, mejores precios y canastas comunes.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .product_homologation import HomologationResult
from .safe_comparator import COMPARABLE, SafeComparisonGroup, safe_group_decisions

MONEY = Decimal("0.01")
PERCENT = Decimal("0.01")
ALLOWED_AVAILABILITY = frozenset({None, "in_stock", "out_of_stock", "unknown"})


class PriceAnalyticsError(ValueError):
    """Los datos no permiten una comparación analítica segura."""


@dataclass(frozen=True, slots=True)
class CurrentPriceObservation:
    source_record_id: str
    supermarket_id: str
    location_id: str
    price_minor: int | None
    availability: str | None = None

    def __post_init__(self) -> None:
        if not self.source_record_id.strip() or not self.supermarket_id.strip() or not self.location_id.strip():
            raise PriceAnalyticsError("price_observation_identity_missing")
        if self.price_minor is not None and (type(self.price_minor) is not int or self.price_minor < 0):
            raise PriceAnalyticsError("price_minor_invalid")
        if self.availability not in ALLOWED_AVAILABILITY:
            raise PriceAnalyticsError("price_observation_availability_invalid")

    @property
    def priced(self) -> bool:
        """Un precio sólo entra a canasta cuando no está explícitamente agotado."""

        return (
            self.price_minor is not None
            and self.price_minor > 0
            and self.availability != "out_of_stock"
        )


@dataclass(frozen=True, slots=True)
class ComparisonScope:
    """Una ubicación explícita por supermercado evita mezclar plazas."""

    locations: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if len(self.locations) < 2:
            raise PriceAnalyticsError("comparison_scope_requires_two_supermarkets")
        supermarkets = [supermarket for supermarket, _ in self.locations]
        if len(set(supermarkets)) != len(supermarkets):
            raise PriceAnalyticsError("comparison_scope_supermarket_duplicate")
        if any(not supermarket.strip() or not location.strip() for supermarket, location in self.locations):
            raise PriceAnalyticsError("comparison_scope_identity_missing")

    @property
    def supermarket_ids(self) -> tuple[str, ...]:
        return tuple(supermarket for supermarket, _ in self.locations)

    @property
    def location_by_supermarket(self) -> dict[str, str]:
        return dict(self.locations)


@dataclass(frozen=True, slots=True)
class ComparableProductPrice:
    canonical_product_id: str
    canonical_gtin: str
    supermarket_id: str
    location_id: str
    source_record_id: str
    price_minor: int

    @property
    def price(self) -> Decimal:
        return (Decimal(self.price_minor) / Decimal(100)).quantize(MONEY)


@dataclass(frozen=True, slots=True)
class ProductComparison:
    canonical_product_id: str
    canonical_gtin: str
    offers: tuple[ComparableProductPrice, ...]
    best_supermarket_id: str
    best_location_id: str
    best_price_minor: int
    highest_price_minor: int
    savings_vs_highest_minor: int
    savings_vs_highest_pct: Decimal

    @property
    def best_price(self) -> Decimal:
        return (Decimal(self.best_price_minor) / Decimal(100)).quantize(MONEY)

    @property
    def savings_vs_highest(self) -> Decimal:
        return (Decimal(self.savings_vs_highest_minor) / Decimal(100)).quantize(MONEY)


@dataclass(frozen=True, slots=True)
class BasketComparison:
    scope: ComparisonScope
    canonical_product_ids: tuple[str, ...]
    product_count: int
    totals_minor: tuple[tuple[str, int], ...]
    cheapest_supermarket_id: str | None
    cheapest_total_minor: int | None
    highest_total_minor: int | None
    savings_vs_highest_minor: int | None
    savings_vs_highest_pct: Decimal | None
    denominator_definition: str = "products_comparable_and_priced_in_every_supermarket_in_scope"

    @property
    def totals(self) -> tuple[tuple[str, Decimal], ...]:
        return tuple(
            (supermarket, (Decimal(total) / Decimal(100)).quantize(MONEY))
            for supermarket, total in self.totals_minor
        )


@dataclass(frozen=True, slots=True)
class AnalyticsResult:
    scope: ComparisonScope
    products: tuple[ProductComparison, ...]
    common_basket: BasketComparison
    excluded_groups: tuple[tuple[str, str], ...]


def _pct(saving: int, denominator: int) -> Decimal:
    if denominator <= 0:
        return Decimal("0.00")
    return (Decimal(saving) * Decimal(100) / Decimal(denominator)).quantize(
        PERCENT,
        rounding=ROUND_HALF_UP,
    )


def _safe_groups(result: HomologationResult) -> tuple[SafeComparisonGroup, ...]:
    return tuple(group for group in safe_group_decisions(result) if group.status == COMPARABLE)


def analyze_current_prices(
    homologation: HomologationResult,
    observations: Iterable[CurrentPriceObservation],
    scope: ComparisonScope,
) -> AnalyticsResult:
    """Calcula comparaciones sólo sobre la intersección segura del alcance.

    Una canasta común contiene exactamente productos:
    - homologados por identidad fuerte;
    - comercialmente coherentes por ``safe_comparator``;
    - con una sola fila fuente por supermercado del alcance;
    - con precio positivo y no explícitamente agotado en cada ubicación.
    """

    profile_by_id = {profile.record.source_record_id: profile for profile in homologation.profiles}
    observation_index: dict[tuple[str, str], CurrentPriceObservation] = {}
    for observation in observations:
        key = (observation.source_record_id, observation.location_id)
        if key in observation_index:
            raise PriceAnalyticsError("price_observation_duplicate")
        profile = profile_by_id.get(observation.source_record_id)
        if profile is None:
            raise PriceAnalyticsError("price_observation_unknown_source")
        if profile.record.supermarket_id != observation.supermarket_id:
            raise PriceAnalyticsError("price_observation_supermarket_mismatch")
        observation_index[key] = observation

    target_supermarkets = set(scope.supermarket_ids)
    target_locations = scope.location_by_supermarket
    products: list[ProductComparison] = []
    excluded: list[tuple[str, str]] = []

    safe_by_id = {group.canonical_product_id: group for group in safe_group_decisions(homologation)}
    for canonical_id, group in sorted(safe_by_id.items()):
        if group.status != COMPARABLE:
            excluded.append((canonical_id, group.status))
            continue

        by_supermarket: dict[str, list[str]] = {supermarket: [] for supermarket in target_supermarkets}
        for source_id in group.source_record_ids:
            profile = profile_by_id[source_id]
            supermarket = profile.record.supermarket_id
            if supermarket in by_supermarket:
                by_supermarket[supermarket].append(source_id)

        if any(len(source_ids) != 1 for source_ids in by_supermarket.values()):
            excluded.append((canonical_id, "scope_membership_incomplete_or_ambiguous"))
            continue

        offers: list[ComparableProductPrice] = []
        missing_price = False
        for supermarket in scope.supermarket_ids:
            source_id = by_supermarket[supermarket][0]
            location = target_locations[supermarket]
            observation = observation_index.get((source_id, location))
            if observation is None or not observation.priced:
                missing_price = True
                break
            assert observation.price_minor is not None
            offers.append(
                ComparableProductPrice(
                    canonical_product_id=canonical_id,
                    canonical_gtin=group.canonical_gtin,
                    supermarket_id=supermarket,
                    location_id=location,
                    source_record_id=source_id,
                    price_minor=observation.price_minor,
                )
            )
        if missing_price:
            excluded.append((canonical_id, "price_missing_or_unavailable_in_scope"))
            continue

        offers.sort(key=lambda offer: (offer.price_minor, offer.supermarket_id, offer.source_record_id))
        best = offers[0]
        highest = max(offer.price_minor for offer in offers)
        saving = highest - best.price_minor
        products.append(
            ProductComparison(
                canonical_product_id=canonical_id,
                canonical_gtin=group.canonical_gtin,
                offers=tuple(offers),
                best_supermarket_id=best.supermarket_id,
                best_location_id=best.location_id,
                best_price_minor=best.price_minor,
                highest_price_minor=highest,
                savings_vs_highest_minor=saving,
                savings_vs_highest_pct=_pct(saving, highest),
            )
        )

    products.sort(key=lambda product: product.canonical_product_id)
    totals = {supermarket: 0 for supermarket in scope.supermarket_ids}
    for product in products:
        for offer in product.offers:
            totals[offer.supermarket_id] += offer.price_minor

    ordered_totals = tuple((supermarket, totals[supermarket]) for supermarket in scope.supermarket_ids)
    if products:
        cheapest_supermarket, cheapest_total = min(
            ordered_totals,
            key=lambda item: (item[1], item[0]),
        )
        highest_total = max(total for _, total in ordered_totals)
        saving = highest_total - cheapest_total
        savings_pct: Decimal | None = _pct(saving, highest_total)
    else:
        cheapest_supermarket = None
        cheapest_total = None
        highest_total = None
        saving = None
        savings_pct = None

    basket = BasketComparison(
        scope=scope,
        canonical_product_ids=tuple(product.canonical_product_id for product in products),
        product_count=len(products),
        totals_minor=ordered_totals,
        cheapest_supermarket_id=cheapest_supermarket,
        cheapest_total_minor=cheapest_total,
        highest_total_minor=highest_total,
        savings_vs_highest_minor=saving,
        savings_vs_highest_pct=savings_pct,
    )
    return AnalyticsResult(
        scope=scope,
        products=tuple(products),
        common_basket=basket,
        excluded_groups=tuple(sorted(excluded)),
    )


def analyze_subbasket(
    result: AnalyticsResult,
    product_quantities: Mapping[str, int],
) -> BasketComparison:
    """Recalcula una subcanasta explícita sin relajar el universo comparable."""

    if not product_quantities:
        raise PriceAnalyticsError("subbasket_empty")
    if any(type(quantity) is not int or quantity <= 0 for quantity in product_quantities.values()):
        raise PriceAnalyticsError("subbasket_quantity_invalid")

    product_by_id = {product.canonical_product_id: product for product in result.products}
    unknown = set(product_quantities) - set(product_by_id)
    if unknown:
        raise PriceAnalyticsError("subbasket_product_not_in_common_universe")

    totals = {supermarket: 0 for supermarket in result.scope.supermarket_ids}
    selected_ids = tuple(sorted(product_quantities))
    for canonical_id in selected_ids:
        product = product_by_id[canonical_id]
        quantity = product_quantities[canonical_id]
        for offer in product.offers:
            totals[offer.supermarket_id] += offer.price_minor * quantity

    ordered_totals = tuple((supermarket, totals[supermarket]) for supermarket in result.scope.supermarket_ids)
    cheapest_supermarket, cheapest_total = min(ordered_totals, key=lambda item: (item[1], item[0]))
    highest_total = max(total for _, total in ordered_totals)
    saving = highest_total - cheapest_total
    return BasketComparison(
        scope=result.scope,
        canonical_product_ids=selected_ids,
        product_count=sum(product_quantities.values()),
        totals_minor=ordered_totals,
        cheapest_supermarket_id=cheapest_supermarket,
        cheapest_total_minor=cheapest_total,
        highest_total_minor=highest_total,
        savings_vs_highest_minor=saving,
        savings_vs_highest_pct=_pct(saving, highest_total),
        denominator_definition="explicit_quantities_drawn_only_from_current_common_comparable_universe",
    )
