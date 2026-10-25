"""Proyección semántica read-only para el futuro dataset de Power BI.

La proyección no persiste datos ni concede autoridad. Consume exclusivamente un
``RehydratedCommercialSnapshot`` ya reconciliado y reutiliza
``evaluate_real_price_reduction`` para que la definición de ahorro real exista en
un solo lugar.

``reported_regular_price`` permanece separado: es una referencia declarada por la
fuente y nunca se usa como baseline de ahorro real.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from .commercial_pricing import CommercialPricingError, evaluate_real_price_reduction
from .tabular_rehydration import RehydratedCommercialSnapshot


class PowerBIProjectionError(ValueError):
    """El snapshot no puede proyectarse sin romper la semántica comercial."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class PriceDirection(StrEnum):
    """Dirección del precio contra el periodo aceptado inmediatamente anterior."""

    NO_BASELINE = "no_baseline"
    DOWN = "down"
    SAME = "same"
    UP = "up"
    UNKNOWN = "unknown"


POWER_BI_OFFER_COLUMNS = (
    "offer_id",
    "supermarket_id",
    "location_id",
    "location_status",
    "location_confidence",
    "source_product_id",
    "product_id",
    "source_key_type",
    "source_key",
    "source_sku",
    "source_name",
    "normalized_name",
    "source_brand",
    "normalized_brand",
    "source_presentation",
    "source_category",
    "barcode",
    "category",
    "subcategory",
    "variant",
    "unit_count",
    "content_per_unit",
    "measurement_unit",
    "total_content",
    "currency",
    "current_price",
    "previous_accepted_price",
    "real_reduction_amount",
    "real_reduction_ratio",
    "is_real_reduction",
    "price_direction",
    "reported_regular_price",
    "source_reports_promotion",
    "availability",
    "review_status",
    "unit_price",
    "unit_price_basis",
    "current_observed_at_utc",
    "previous_observed_at_utc",
    "current_scrape_run_id",
    "previous_scrape_run_id",
    "product_url",
    "image_url",
)


@dataclass(frozen=True, slots=True)
class PowerBIOfferRecord:
    offer_id: str
    supermarket_id: str
    location_id: str
    location_status: str
    location_confidence: Decimal | None
    source_product_id: str
    product_id: str
    source_key_type: str
    source_key: str
    source_sku: str | None
    source_name: str
    normalized_name: str
    source_brand: str | None
    normalized_brand: str | None
    source_presentation: str | None
    source_category: str | None
    barcode: str | None
    category: str | None
    subcategory: str | None
    variant: str | None
    unit_count: int | None
    content_per_unit: Decimal | None
    measurement_unit: str | None
    total_content: Decimal | None
    currency: str
    current_price: Decimal | None
    previous_accepted_price: Decimal | None
    real_reduction_amount: Decimal | None
    real_reduction_ratio: Decimal | None
    is_real_reduction: bool
    price_direction: PriceDirection
    reported_regular_price: Decimal | None
    source_reports_promotion: bool
    availability: str
    review_status: str
    unit_price: Decimal | None
    unit_price_basis: str | None
    current_observed_at_utc: datetime
    previous_observed_at_utc: datetime | None
    current_scrape_run_id: str
    previous_scrape_run_id: str | None
    product_url: str
    image_url: str | None

    def as_row(self) -> Mapping[str, Any]:
        """Devuelve un mapping estable sin convertir Decimal/datetime a float/texto."""

        values = {
            "offer_id": self.offer_id,
            "supermarket_id": self.supermarket_id,
            "location_id": self.location_id,
            "location_status": self.location_status,
            "location_confidence": self.location_confidence,
            "source_product_id": self.source_product_id,
            "product_id": self.product_id,
            "source_key_type": self.source_key_type,
            "source_key": self.source_key,
            "source_sku": self.source_sku,
            "source_name": self.source_name,
            "normalized_name": self.normalized_name,
            "source_brand": self.source_brand,
            "normalized_brand": self.normalized_brand,
            "source_presentation": self.source_presentation,
            "source_category": self.source_category,
            "barcode": self.barcode,
            "category": self.category,
            "subcategory": self.subcategory,
            "variant": self.variant,
            "unit_count": self.unit_count,
            "content_per_unit": self.content_per_unit,
            "measurement_unit": self.measurement_unit,
            "total_content": self.total_content,
            "currency": self.currency,
            "current_price": self.current_price,
            "previous_accepted_price": self.previous_accepted_price,
            "real_reduction_amount": self.real_reduction_amount,
            "real_reduction_ratio": self.real_reduction_ratio,
            "is_real_reduction": self.is_real_reduction,
            "price_direction": self.price_direction.value,
            "reported_regular_price": self.reported_regular_price,
            "source_reports_promotion": self.source_reports_promotion,
            "availability": self.availability,
            "review_status": self.review_status,
            "unit_price": self.unit_price,
            "unit_price_basis": self.unit_price_basis,
            "current_observed_at_utc": self.current_observed_at_utc,
            "previous_observed_at_utc": self.previous_observed_at_utc,
            "current_scrape_run_id": self.current_scrape_run_id,
            "previous_scrape_run_id": self.previous_scrape_run_id,
            "product_url": self.product_url,
            "image_url": self.image_url,
        }
        if tuple(values) != POWER_BI_OFFER_COLUMNS:
            raise PowerBIProjectionError("power_bi_offer_schema_mismatch")
        return MappingProxyType(values)


def _price_direction(
    current_price: Decimal | None,
    previous_price: Decimal | None,
) -> PriceDirection:
    if current_price is None:
        return PriceDirection.UNKNOWN
    if previous_price is None:
        return PriceDirection.NO_BASELINE
    if current_price < previous_price:
        return PriceDirection.DOWN
    if current_price > previous_price:
        return PriceDirection.UP
    return PriceDirection.SAME


def build_power_bi_offer_records(
    snapshot: RehydratedCommercialSnapshot,
) -> tuple[PowerBIOfferRecord, ...]:
    """Proyecta current + último periodo previo aceptado en orden determinista."""

    if not isinstance(snapshot, RehydratedCommercialSnapshot):
        raise PowerBIProjectionError("rehydrated_commercial_snapshot_required")
    if set(snapshot.current) != set(snapshot.history):
        raise PowerBIProjectionError("power_bi_snapshot_offer_set_mismatch")

    records: list[PowerBIOfferRecord] = []
    for offer_id in sorted(snapshot.current):
        current = snapshot.current[offer_id]
        history = snapshot.history[offer_id]
        try:
            pricing = evaluate_real_price_reduction(current, history)
        except CommercialPricingError as exc:
            raise PowerBIProjectionError("power_bi_pricing_reconciliation_failed") from exc
        if pricing is None:  # pragma: no cover - current existe por construcción
            raise PowerBIProjectionError("power_bi_current_missing")

        validated = current.validated_offer
        offer = validated.offer
        if offer.offer_id != offer_id or pricing.offer_id != offer_id:
            raise PowerBIProjectionError("power_bi_offer_identity_mismatch")

        records.append(
            PowerBIOfferRecord(
                offer_id=offer.offer_id,
                supermarket_id=offer.supermarket_id,
                location_id=offer.location_id,
                location_status=offer.location_status.value,
                location_confidence=offer.location_confidence,
                source_product_id=offer.source_product_id,
                product_id=offer.product_id,
                source_key_type=offer.source_key_type.value,
                source_key=offer.source_key,
                source_sku=offer.source_sku,
                source_name=offer.source_name,
                normalized_name=offer.normalized_name,
                source_brand=offer.source_brand,
                normalized_brand=offer.normalized_brand,
                source_presentation=offer.source_presentation,
                source_category=offer.source_category,
                barcode=offer.barcode,
                category=offer.category,
                subcategory=offer.subcategory,
                variant=offer.variant,
                unit_count=offer.unit_count,
                content_per_unit=offer.content_per_unit,
                measurement_unit=offer.measurement_unit,
                total_content=offer.total_content,
                currency=pricing.currency,
                current_price=pricing.current_price,
                previous_accepted_price=pricing.previous_accepted_price,
                real_reduction_amount=pricing.reduction_amount,
                real_reduction_ratio=pricing.reduction_ratio,
                is_real_reduction=pricing.is_real_reduction,
                price_direction=_price_direction(
                    pricing.current_price,
                    pricing.previous_accepted_price,
                ),
                reported_regular_price=offer.reported_regular_price,
                source_reports_promotion=offer.is_promotion,
                availability=offer.availability.value,
                review_status=validated.review_status.value,
                unit_price=offer.unit_price,
                unit_price_basis=offer.unit_price_basis,
                current_observed_at_utc=pricing.current_observed_at_utc,
                previous_observed_at_utc=pricing.previous_observed_at_utc,
                current_scrape_run_id=pricing.current_scrape_run_id,
                previous_scrape_run_id=pricing.previous_scrape_run_id,
                product_url=offer.product_url,
                image_url=offer.image_url,
            )
        )

    return tuple(records)
