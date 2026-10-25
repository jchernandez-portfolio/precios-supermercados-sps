"""Competitive metrics over the already authorized comparable universe."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from .analytics_quality import MarketWindowAssessment, MarketWindowStatus
from .price_analytics import PERCENT, AnalyticsResult


class CompetitiveAnalyticsError(ValueError):
    pass


class PciReference(StrEnum):
    MEAN = "mean"
    MEDIAN = "median"
    MARKET_MINIMUM = "market_minimum"


@dataclass(frozen=True, slots=True)
class MetricCoverage:
    comparable_count: int
    valid_price_count: int
    excluded_count: int
    coverage_pct: Decimal
    as_of: str
    freshness_window_hours: int


@dataclass(frozen=True, slots=True)
class CompetitiveOfferMetric:
    supermarket_id: str
    location_id: str
    source_record_id: str
    current_price_minor: int
    rank: int
    pci: Decimal


@dataclass(frozen=True, slots=True)
class CompetitiveProductMetric:
    canonical_product_id: str
    canonical_gtin: str
    market_min_minor: int
    market_max_minor: int
    market_mean_minor: Decimal
    market_median_minor: Decimal
    spread_abs_minor: int
    spread_pct: Decimal
    cheapest_retailer_ids: tuple[str, ...]
    comparable_count: int
    coverage_pct: Decimal
    offers: tuple[CompetitiveOfferMetric, ...]


@dataclass(frozen=True, slots=True)
class CompetitiveAnalyticsResult:
    comparison_status: MarketWindowStatus
    pci_reference: PciReference
    coverage: MetricCoverage
    products: tuple[CompetitiveProductMetric, ...]
    blocked_reasons: tuple[str, ...]


def _pct(numerator: Decimal | int, denominator: Decimal | int) -> Decimal:
    denominator_value = Decimal(denominator)
    if denominator_value <= 0:
        return Decimal("0.00")
    return (Decimal(numerator) * Decimal(100) / denominator_value).quantize(
        PERCENT,
        rounding=ROUND_HALF_UP,
    )


def _median(values: list[int]) -> Decimal:
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return Decimal(ordered[midpoint])
    return (Decimal(ordered[midpoint - 1]) + Decimal(ordered[midpoint])) / Decimal(2)


def _identity_comparable_count(result: AnalyticsResult) -> int:
    price_exclusions = {
        "price_missing_or_unavailable_in_scope",
        "price_missing_in_scope",
    }
    return len(result.products) + sum(
        reason in price_exclusions for _, reason in result.excluded_groups
    )


def analyze_competition(
    result: AnalyticsResult,
    market_window: MarketWindowAssessment,
    *,
    pci_reference: PciReference | str = PciReference.MEDIAN,
) -> CompetitiveAnalyticsResult:
    """Compute PCI/ranks only when source timestamps are safe to compare."""

    try:
        reference_kind = PciReference(pci_reference)
    except (TypeError, ValueError) as exc:
        raise CompetitiveAnalyticsError("pci_reference_invalid") from exc
    if market_window.source_count != len(result.scope.locations):
        raise CompetitiveAnalyticsError("market_window_scope_cardinality_mismatch")

    identity_comparable = _identity_comparable_count(result)
    valid_prices = len(result.products)
    coverage_pct = _pct(valid_prices, identity_comparable)
    excluded_count = len(result.excluded_groups)
    coverage = MetricCoverage(
        comparable_count=identity_comparable,
        valid_price_count=valid_prices,
        excluded_count=excluded_count,
        coverage_pct=coverage_pct,
        as_of=market_window.as_of_utc.isoformat().replace("+00:00", "Z"),
        freshness_window_hours=market_window.freshness_window_hours,
    )
    if market_window.status is not MarketWindowStatus.COMPARABLE:
        return CompetitiveAnalyticsResult(
            comparison_status=market_window.status,
            pci_reference=reference_kind,
            coverage=coverage,
            products=(),
            blocked_reasons=market_window.reasons,
        )

    product_metrics: list[CompetitiveProductMetric] = []
    scope_size = len(result.scope.locations)
    for product in result.products:
        prices = [offer.price_minor for offer in product.offers]
        market_min = min(prices)
        market_max = max(prices)
        mean = Decimal(sum(prices)) / Decimal(len(prices))
        median = _median(prices)
        reference = {
            PciReference.MEAN: mean,
            PciReference.MEDIAN: median,
            PciReference.MARKET_MINIMUM: Decimal(market_min),
        }[reference_kind]

        ordered_prices = sorted(set(prices))
        rank_by_price = {price: index + 1 for index, price in enumerate(ordered_prices)}
        offers = tuple(
            CompetitiveOfferMetric(
                supermarket_id=offer.supermarket_id,
                location_id=offer.location_id,
                source_record_id=offer.source_record_id,
                current_price_minor=offer.price_minor,
                rank=rank_by_price[offer.price_minor],
                pci=_pct(offer.price_minor, reference),
            )
            for offer in sorted(
                product.offers,
                key=lambda item: (item.price_minor, item.supermarket_id, item.location_id),
            )
        )
        product_metrics.append(
            CompetitiveProductMetric(
                canonical_product_id=product.canonical_product_id,
                canonical_gtin=product.canonical_gtin,
                market_min_minor=market_min,
                market_max_minor=market_max,
                market_mean_minor=mean.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                market_median_minor=median.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                spread_abs_minor=market_max - market_min,
                spread_pct=_pct(market_max - market_min, market_min),
                cheapest_retailer_ids=tuple(
                    sorted(offer.supermarket_id for offer in product.offers if offer.price_minor == market_min)
                ),
                comparable_count=len(offers),
                coverage_pct=_pct(len(offers), scope_size),
                offers=offers,
            )
        )

    return CompetitiveAnalyticsResult(
        comparison_status=MarketWindowStatus.COMPARABLE,
        pci_reference=reference_kind,
        coverage=coverage,
        products=tuple(sorted(product_metrics, key=lambda item: item.canonical_product_id)),
        blocked_reasons=(),
    )
