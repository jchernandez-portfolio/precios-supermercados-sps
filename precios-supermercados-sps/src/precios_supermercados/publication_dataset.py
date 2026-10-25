"""Dataset público/BI derivado exclusivamente de comparaciones seguras.

No contiene credenciales, URLs internas ni datos que no hayan superado el gate
``safe_comparator``. Los consumidores reciben también la definición explícita
del denominador de la canasta para evitar métricas engañosas.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from .price_analytics import AnalyticsResult


@dataclass(frozen=True, slots=True)
class PublishedOfferRow:
    canonical_product_id: str
    canonical_gtin: str
    supermarket_id: str
    location_id: str
    source_record_id: str
    current_price: str
    is_best_price: bool


@dataclass(frozen=True, slots=True)
class PublishedProductRow:
    canonical_product_id: str
    canonical_gtin: str
    best_supermarket_id: str
    best_location_id: str
    best_price: str
    highest_price: str
    savings_vs_highest: str
    savings_vs_highest_pct: str
    supermarket_count: int


@dataclass(frozen=True, slots=True)
class PublishedBasketRow:
    supermarket_id: str
    location_id: str
    total: str
    is_cheapest: bool
    product_count: int
    denominator_definition: str


@dataclass(frozen=True, slots=True)
class PublicationDataset:
    schema: str
    comparison_policy: str
    currency: str
    scope: tuple[Mapping[str, str], ...]
    offers: tuple[PublishedOfferRow, ...]
    products: tuple[PublishedProductRow, ...]
    common_basket: tuple[PublishedBasketRow, ...]
    excluded_group_counts: Mapping[str, int]

    def as_json_object(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "comparison_policy": self.comparison_policy,
            "currency": self.currency,
            "scope": [dict(item) for item in self.scope],
            "offers": [asdict(item) for item in self.offers],
            "products": [asdict(item) for item in self.products],
            "common_basket": [asdict(item) for item in self.common_basket],
            "excluded_group_counts": dict(self.excluded_group_counts),
        }


def _money(minor: int) -> str:
    return format((Decimal(minor) / Decimal(100)).quantize(Decimal("0.01")), "f")


def build_publication_dataset(result: AnalyticsResult, *, currency: str = "HNL") -> PublicationDataset:
    """Proyecta la salida analítica a tablas estables y auditables."""

    if not currency.strip():
        raise ValueError("publication_currency_missing")

    offers: list[PublishedOfferRow] = []
    products: list[PublishedProductRow] = []
    for product in result.products:
        products.append(
            PublishedProductRow(
                canonical_product_id=product.canonical_product_id,
                canonical_gtin=product.canonical_gtin,
                best_supermarket_id=product.best_supermarket_id,
                best_location_id=product.best_location_id,
                best_price=_money(product.best_price_minor),
                highest_price=_money(product.highest_price_minor),
                savings_vs_highest=_money(product.savings_vs_highest_minor),
                savings_vs_highest_pct=format(product.savings_vs_highest_pct, "f"),
                supermarket_count=len(product.offers),
            )
        )
        offers.extend(
            PublishedOfferRow(
                canonical_product_id=product.canonical_product_id,
                canonical_gtin=product.canonical_gtin,
                supermarket_id=offer.supermarket_id,
                location_id=offer.location_id,
                source_record_id=offer.source_record_id,
                current_price=_money(offer.price_minor),
                # Todos los mínimos son ganadores. El campo singular de Products
                # sólo conserva un desempate determinista para consumidores que
                # necesitan una fila representativa.
                is_best_price=offer.price_minor == product.best_price_minor,
            )
            for offer in product.offers
        )

    location_by_supermarket = result.scope.location_by_supermarket
    basket_minimum = result.common_basket.cheapest_total_minor
    basket_has_products = result.common_basket.product_count > 0 and basket_minimum is not None
    basket = tuple(
        PublishedBasketRow(
            supermarket_id=supermarket,
            location_id=location_by_supermarket[supermarket],
            total=_money(total_minor),
            # Un universo vacío nunca produce un ganador. En empate se marcan
            # todas las cadenas con el mismo total mínimo.
            is_cheapest=basket_has_products and total_minor == basket_minimum,
            product_count=result.common_basket.product_count,
            denominator_definition=result.common_basket.denominator_definition,
        )
        for supermarket, total_minor in result.common_basket.totals_minor
    )

    excluded: dict[str, int] = {}
    for _, reason in result.excluded_groups:
        excluded[reason] = excluded.get(reason, 0) + 1

    scope = tuple(
        {"supermarket_id": supermarket, "location_id": location}
        for supermarket, location in result.scope.locations
    )
    return PublicationDataset(
        schema="precios-sps-publication/v1",
        comparison_policy="fail_closed_strong_identity_and_commercial_consistency",
        currency=currency,
        scope=scope,
        offers=tuple(sorted(offers, key=lambda row: (row.canonical_product_id, row.supermarket_id, row.location_id))),
        products=tuple(sorted(products, key=lambda row: row.canonical_product_id)),
        common_basket=basket,
        excluded_group_counts=dict(sorted(excluded.items())),
    )
