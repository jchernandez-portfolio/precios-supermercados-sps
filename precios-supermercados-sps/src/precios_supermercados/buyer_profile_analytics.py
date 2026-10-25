"""Canastas por perfil de comprador sobre el universo comparable actual."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .price_analytics import AnalyticsResult, BasketComparison, PriceAnalyticsError, analyze_subbasket


@dataclass(frozen=True, slots=True)
class BuyerProfile:
    profile_id: str
    label: str
    product_quantities: Mapping[str, int]

    def __post_init__(self) -> None:
        if not self.profile_id.strip() or not self.label.strip():
            raise PriceAnalyticsError("buyer_profile_identity_missing")
        quantities = dict(self.product_quantities)
        if not quantities:
            raise PriceAnalyticsError("buyer_profile_empty")
        if any(not isinstance(product_id, str) or not product_id.strip() for product_id in quantities):
            raise PriceAnalyticsError("buyer_profile_product_identity_invalid")
        if any(type(quantity) is not int or quantity <= 0 for quantity in quantities.values()):
            raise PriceAnalyticsError("buyer_profile_quantity_invalid")
        object.__setattr__(self, "product_quantities", MappingProxyType(quantities))


@dataclass(frozen=True, slots=True)
class BuyerProfileResult:
    profile_id: str
    label: str
    basket: BasketComparison


def analyze_buyer_profile(result: AnalyticsResult, profile: BuyerProfile) -> BuyerProfileResult:
    """Evalúa un perfil sin sustituir productos ausentes ni relajar comparabilidad."""

    try:
        basket = analyze_subbasket(result, profile.product_quantities)
    except PriceAnalyticsError as exc:
        if str(exc) == "subbasket_product_not_in_common_universe":
            raise PriceAnalyticsError("buyer_profile_not_fully_comparable") from exc
        raise
    return BuyerProfileResult(profile_id=profile.profile_id, label=profile.label, basket=basket)
