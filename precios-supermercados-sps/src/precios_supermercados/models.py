"""Contratos de datos comunes para extracción, normalización y validación."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any, TypeVar
from urllib.parse import urlsplit

from .enums import (
    AvailabilityStatus,
    LocationStatus,
    ReviewStatus,
    SourceKeyType,
)

EnumT = TypeVar("EnumT", SourceKeyType, AvailabilityStatus, LocationStatus)

_REVIEWABLE_NORMALIZED_FIELDS = (
    "normalized_brand",
    "category",
    "subcategory",
    "unit_count",
    "content_per_unit",
    "measurement_unit",
    "total_content",
)


def _non_empty(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} no puede estar vacío")
    return value.strip()


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _enum_value(value: EnumT | str, enum_type: type[EnumT], field_name: str) -> EnumT:
    try:
        return value if isinstance(value, enum_type) else enum_type(value)
    except (TypeError, ValueError) as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise ValueError(f"{field_name} debe ser uno de: {allowed}") from exc


def _decimal_value(
    value: Decimal | int | float | str | None,
    field_name: str,
    *,
    required: bool = False,
    allow_zero: bool = False,
) -> Decimal | None:
    if value is None:
        if required:
            raise ValueError(f"{field_name} es obligatorio")
        return None
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field_name} debe ser decimal") from exc
    if not decimal_value.is_finite():
        raise ValueError(f"{field_name} debe ser finito")
    minimum_valid = decimal_value >= 0 if allow_zero else decimal_value > 0
    if not minimum_valid:
        comparator = "mayor o igual que cero" if allow_zero else "mayor que cero"
        raise ValueError(f"{field_name} debe ser {comparator}")
    return decimal_value


def _optional_positive_int(value: int | None, field_name: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field_name} debe ser un entero mayor que cero")
    return value


def _utc_datetime(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} debe incluir zona horaria UTC")
    if value.utcoffset().total_seconds() != 0:
        raise ValueError(f"{field_name} debe expresarse en UTC")
    return value


def _absolute_url(value: str | None, field_name: str, *, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ValueError(f"{field_name} es obligatorio")
        return None
    cleaned = value.strip()
    parsed = urlsplit(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name} debe ser una URL absoluta http/https")
    return cleaned


def _confidence(value: Decimal | int | float | str | None) -> Decimal | None:
    if value is None:
        return None
    result = _decimal_value(value, "location_confidence", allow_zero=True)
    assert result is not None
    if result > 1:
        raise ValueError("location_confidence debe estar entre 0 y 1")
    return result


def _mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("raw_values debe ser un mapping")
    return MappingProxyType(dict(value))


@dataclass(frozen=True, slots=True)
class RawProduct:
    """Observación fiel a la fuente, antes de normalizar nombres o cantidades."""

    supermarket_id: str
    location_id: str
    source_key_type: SourceKeyType | str
    source_key: str
    source_name: str
    product_url: str | None
    observed_at_utc: datetime
    scrape_run_id: str
    extractor_version: str
    schema_version: str
    source_url: str
    source_sku: str | None = None
    source_brand: str | None = None
    source_presentation: str | None = None
    source_category: str | None = None
    image_url: str | None = None
    location_status: LocationStatus | str = LocationStatus.UNKNOWN
    location_evidence: str | None = None
    location_confidence: Decimal | int | float | str | None = None
    raw_values: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "supermarket_id", _non_empty(self.supermarket_id, "supermarket_id"))
        object.__setattr__(self, "location_id", _non_empty(self.location_id, "location_id"))
        object.__setattr__(self, "source_key_type", _enum_value(self.source_key_type, SourceKeyType, "source_key_type"))
        object.__setattr__(self, "source_key", _non_empty(self.source_key, "source_key"))
        object.__setattr__(self, "source_name", _non_empty(self.source_name, "source_name"))
        object.__setattr__(self, "product_url", _absolute_url(self.product_url, "product_url"))
        object.__setattr__(self, "source_url", _absolute_url(self.source_url, "source_url", required=True))
        object.__setattr__(self, "image_url", _absolute_url(self.image_url, "image_url"))
        object.__setattr__(self, "observed_at_utc", _utc_datetime(self.observed_at_utc, "observed_at_utc"))
        object.__setattr__(self, "scrape_run_id", _non_empty(self.scrape_run_id, "scrape_run_id"))
        object.__setattr__(self, "extractor_version", _non_empty(self.extractor_version, "extractor_version"))
        object.__setattr__(self, "schema_version", _non_empty(self.schema_version, "schema_version"))
        object.__setattr__(self, "source_sku", _optional_text(self.source_sku))
        object.__setattr__(self, "source_brand", _optional_text(self.source_brand))
        object.__setattr__(self, "source_presentation", _optional_text(self.source_presentation))
        object.__setattr__(self, "source_category", _optional_text(self.source_category))
        object.__setattr__(self, "location_status", _enum_value(self.location_status, LocationStatus, "location_status"))
        object.__setattr__(self, "location_evidence", _optional_text(self.location_evidence))
        object.__setattr__(self, "location_confidence", _confidence(self.location_confidence))
        object.__setattr__(self, "raw_values", _mapping(self.raw_values))

        if self.location_status in {LocationStatus.CONFIRMED, LocationStatus.INFERRED}:
            if self.location_evidence is None or self.location_confidence is None:
                raise ValueError("Una ubicación confirmed o inferred requiere evidencia y confianza")


@dataclass(frozen=True, slots=True)
class NormalizedOffer:
    """Oferta común; puede conservar interpretación parcial con revisión pendiente."""

    supermarket_id: str
    location_id: str
    source_product_id: str
    source_key_type: SourceKeyType | str
    source_key: str
    product_id: str
    offer_id: str
    source_name: str
    product_url: str | None
    normalized_name: str
    currency: str
    is_promotion: bool
    availability: AvailabilityStatus | str
    location_status: LocationStatus | str
    observed_at_utc: datetime
    scrape_run_id: str
    extractor_version: str
    schema_version: str
    source_url: str
    normalized_brand: str | None = None
    category: str | None = None
    subcategory: str | None = None
    variant: str | None = None
    unit_count: int | None = None
    content_per_unit: Decimal | int | float | str | None = None
    measurement_unit: str | None = None
    total_content: Decimal | int | float | str | None = None
    current_price: Decimal | int | float | str | None = None
    source_sku: str | None = None
    source_brand: str | None = None
    source_presentation: str | None = None
    source_category: str | None = None
    image_url: str | None = None
    barcode: str | None = None
    reported_regular_price: Decimal | int | float | str | None = None
    unit_price: Decimal | int | float | str | None = None
    unit_price_basis: str | None = None
    location_evidence: str | None = None
    location_confidence: Decimal | int | float | str | None = None
    raw_values: Mapping[str, Any] = field(default_factory=dict)
    review_status: ReviewStatus = field(init=False)
    pending_fields: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        for field_name in (
            "supermarket_id",
            "location_id",
            "source_product_id",
            "source_key",
            "product_id",
            "offer_id",
            "source_name",
            "normalized_name",
            "scrape_run_id",
            "extractor_version",
            "schema_version",
        ):
            object.__setattr__(self, field_name, _non_empty(getattr(self, field_name), field_name))

        object.__setattr__(self, "source_key_type", _enum_value(self.source_key_type, SourceKeyType, "source_key_type"))
        object.__setattr__(self, "availability", _enum_value(self.availability, AvailabilityStatus, "availability"))
        object.__setattr__(self, "location_status", _enum_value(self.location_status, LocationStatus, "location_status"))
        object.__setattr__(self, "product_url", _absolute_url(self.product_url, "product_url"))
        object.__setattr__(self, "source_url", _absolute_url(self.source_url, "source_url", required=True))
        object.__setattr__(self, "image_url", _absolute_url(self.image_url, "image_url"))
        object.__setattr__(self, "observed_at_utc", _utc_datetime(self.observed_at_utc, "observed_at_utc"))

        if not isinstance(self.is_promotion, bool):
            raise ValueError("is_promotion debe ser booleano")

        object.__setattr__(self, "unit_count", _optional_positive_int(self.unit_count, "unit_count"))
        object.__setattr__(self, "content_per_unit", _decimal_value(self.content_per_unit, "content_per_unit"))
        object.__setattr__(self, "total_content", _decimal_value(self.total_content, "total_content"))
        object.__setattr__(self, "current_price", _decimal_value(self.current_price, "current_price"))
        object.__setattr__(self, "reported_regular_price", _decimal_value(self.reported_regular_price, "reported_regular_price"))
        object.__setattr__(self, "unit_price", _decimal_value(self.unit_price, "unit_price"))

        if self.availability is AvailabilityStatus.IN_STOCK and self.current_price is None:
            raise ValueError("current_price es obligatorio y mayor que cero cuando availability es in_stock")

        currency = _non_empty(self.currency, "currency").upper()
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError("currency debe ser un código ISO de tres letras")
        object.__setattr__(self, "currency", currency)

        for field_name in (
            "normalized_brand",
            "category",
            "subcategory",
            "variant",
            "measurement_unit",
            "source_sku",
            "source_brand",
            "source_presentation",
            "source_category",
            "barcode",
            "unit_price_basis",
            "location_evidence",
        ):
            object.__setattr__(self, field_name, _optional_text(getattr(self, field_name)))

        object.__setattr__(self, "location_confidence", _confidence(self.location_confidence))
        object.__setattr__(self, "raw_values", _mapping(self.raw_values))

        if self.location_status in {LocationStatus.CONFIRMED, LocationStatus.INFERRED}:
            if self.location_evidence is None or self.location_confidence is None:
                raise ValueError("Una ubicación confirmed o inferred requiere evidencia y confianza")

        pending_fields = tuple(
            field_name
            for field_name in _REVIEWABLE_NORMALIZED_FIELDS
            if getattr(self, field_name) is None
        )
        object.__setattr__(self, "pending_fields", pending_fields)
        object.__setattr__(
            self,
            "review_status",
            ReviewStatus.NEEDS_REVIEW if pending_fields else ReviewStatus.READY,
        )


@dataclass(frozen=True, slots=True)
class ValidatedOffer:
    """Oferta validada estructuralmente, con trazabilidad de revisión y calidad."""

    offer: NormalizedOffer
    state_hash: str
    validated_at_utc: datetime
    quality_events: tuple[str, ...] = ()
    review_status: ReviewStatus = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.offer, NormalizedOffer):
            raise ValueError("offer debe ser una instancia de NormalizedOffer")
        state_hash = _non_empty(self.state_hash, "state_hash").lower()
        if len(state_hash) != 64 or any(character not in "0123456789abcdef" for character in state_hash):
            raise ValueError("state_hash debe ser un SHA-256 hexadecimal de 64 caracteres")
        object.__setattr__(self, "state_hash", state_hash)
        object.__setattr__(self, "validated_at_utc", _utc_datetime(self.validated_at_utc, "validated_at_utc"))

        supplied_events = tuple(_non_empty(event, "quality_event") for event in self.quality_events)
        generated_events = tuple(
            f"pending_normalization:{field_name}"
            for field_name in self.offer.pending_fields
        )
        merged_events = tuple(dict.fromkeys((*supplied_events, *generated_events)))
        object.__setattr__(self, "quality_events", merged_events)
        object.__setattr__(self, "review_status", self.offer.review_status)
