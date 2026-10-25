"""Puente offline entre la normalización revisada de La Colonia y la oferta común.

El extractor conserva la evidencia fuente en ``RawProduct``. Este módulo aplica la
normalización de producto/presentación específica de La Colonia sobre una oferta
común ya construida, sin inventar provenance, ubicación, precio o disponibilidad.

No hace red, no acepta catálogos y no persiste. Una excepción versionada sólo se
usa cuando la firma de los campos fuente coincide; si un producto conocido cambia,
el puente falla cerrado y exige revisión en vez de reutilizar una corrección vieja.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .la_colonia_product_normalization import (
    ProductNormalizationError,
    load_override_registry,
    normalize_product_row,
)
from .models import NormalizedOffer, RawProduct, ValidatedOffer
from .offer_normalization import (
    ProductIdResolver,
    normalize_raw_product,
    validate_normalized_offer,
)

SUPERMARKET_ID = "la_colonia"
DEFAULT_REGISTRY_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "supermercados"
    / "la-colonia-product-normalization-overrides"
)


class LaColoniaOfferNormalizationError(ValueError):
    """La observación no puede cruzar de forma segura al esquema común."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class LaColoniaProductNormalizationEvidence:
    """Semántica revisada que el ``NormalizedOffer`` actual aún no modela completa."""

    presentation_normalized: str
    presentation_kind: str
    declared_content: Decimal | None
    content_scope: str
    normalization_method: str
    field_normalization_method: str


@dataclass(frozen=True, slots=True)
class LaColoniaOfferNormalizationResult:
    """Oferta canónica más evidencia de presentación específica, sin perder el raw."""

    offer: NormalizedOffer
    product_normalization: LaColoniaProductNormalizationEvidence


@dataclass(frozen=True, slots=True)
class LaColoniaValidatedOfferResult:
    """Oferta sellada después de aplicar la semántica revisada de producto."""

    validated_offer: ValidatedOffer
    product_normalization: LaColoniaProductNormalizationEvidence


def load_default_registry() -> Mapping[str, Mapping[str, object]]:
    """Carga el registro versionado aprobado para el snapshot de La Colonia."""

    return load_override_registry(DEFAULT_REGISTRY_PATH)


def _active_registry(
    registry: Mapping[str, Mapping[str, object]] | None,
) -> Mapping[str, Mapping[str, object]]:
    if registry is not None:
        return registry
    try:
        return load_default_registry()
    except (OSError, ValueError) as exc:
        raise LaColoniaOfferNormalizationError(
            "normalization_registry_invalid"
        ) from exc


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _required_text(value: object, field_name: str) -> str:
    cleaned = _optional_text(value)
    if cleaned is None:
        raise LaColoniaOfferNormalizationError(f"{field_name}_missing")
    return cleaned


def _positive_int(value: object, field_name: str) -> int | None:
    cleaned = _optional_text(value)
    if cleaned is None:
        return None
    try:
        parsed = int(cleaned)
    except ValueError as exc:
        raise LaColoniaOfferNormalizationError(f"{field_name}_invalid") from exc
    if parsed <= 0 or str(parsed) != cleaned:
        raise LaColoniaOfferNormalizationError(f"{field_name}_invalid")
    return parsed


def _positive_decimal(value: object, field_name: str) -> Decimal | None:
    cleaned = _optional_text(value)
    if cleaned is None:
        return None
    try:
        parsed = Decimal(cleaned)
    except InvalidOperation as exc:
        raise LaColoniaOfferNormalizationError(f"{field_name}_invalid") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise LaColoniaOfferNormalizationError(f"{field_name}_invalid")
    return parsed


def _normalization_row(raw: RawProduct) -> dict[str, object]:
    values = raw.raw_values
    return {
        "source_key_type": raw.source_key_type.value,
        "source_key": raw.source_key,
        "source_name": raw.source_name,
        "brand": raw.source_brand,
        "presentation": raw.source_presentation,
        "category": raw.source_category,
        "product_id": values.get("product_id"),
        "item_id": values.get("item_id"),
        "reference": raw.source_sku or values.get("reference"),
        "ean": values.get("ean"),
    }


def _normalize_product_evidence(
    raw: RawProduct,
    registry: Mapping[str, Mapping[str, object]],
) -> tuple[dict[str, str], LaColoniaProductNormalizationEvidence]:
    try:
        normalized = normalize_product_row(_normalization_row(raw), registry=registry)
    except ProductNormalizationError as exc:
        raise LaColoniaOfferNormalizationError("product_normalization_invalid") from exc

    status = normalized["normalization_status"]
    if status != "ready":
        raise LaColoniaOfferNormalizationError(f"product_normalization_{status}")

    evidence = LaColoniaProductNormalizationEvidence(
        presentation_normalized=_required_text(
            normalized.get("presentation_normalized"),
            "presentation_normalized",
        ),
        presentation_kind=_required_text(
            normalized.get("presentation_kind"),
            "presentation_kind",
        ),
        declared_content=_positive_decimal(
            normalized.get("declared_content"),
            "declared_content",
        ),
        content_scope=_required_text(normalized.get("content_scope"), "content_scope"),
        normalization_method=_required_text(
            normalized.get("normalization_method"),
            "normalization_method",
        ),
        field_normalization_method=_required_text(
            normalized.get("field_normalization_method"),
            "field_normalization_method",
        ),
    )
    return normalized, evidence


def normalize_la_colonia_raw_product(
    raw: RawProduct,
    *,
    currency: str,
    registry: Mapping[str, Mapping[str, object]] | None = None,
    product_id_resolver: ProductIdResolver | None = None,
) -> LaColoniaOfferNormalizationResult:
    """Aplica la normalización revisada sin alterar evidencia comercial/provenance."""

    if not isinstance(raw, RawProduct):
        raise LaColoniaOfferNormalizationError("raw_product_invalid")
    if raw.supermarket_id != SUPERMARKET_ID:
        raise LaColoniaOfferNormalizationError("supermarket_mismatch")

    active_registry = _active_registry(registry)
    normalized, evidence = _normalize_product_evidence(raw, active_registry)
    base_offer = normalize_raw_product(
        raw,
        currency=currency,
        product_id_resolver=product_id_resolver,
    )

    offer = replace(
        base_offer,
        normalized_brand=_optional_text(normalized.get("normalized_brand")),
        category=_optional_text(normalized.get("category")),
        subcategory=_optional_text(normalized.get("subcategory")),
        unit_count=_positive_int(normalized.get("unit_count"), "unit_count"),
        content_per_unit=_positive_decimal(
            normalized.get("content_per_unit"),
            "content_per_unit",
        ),
        measurement_unit=_optional_text(normalized.get("measurement_unit")),
        total_content=_positive_decimal(normalized.get("total_content"), "total_content"),
    )
    return LaColoniaOfferNormalizationResult(
        offer=offer,
        product_normalization=evidence,
    )


def normalize_and_validate_la_colonia_raw_product(
    raw: RawProduct,
    *,
    currency: str,
    validated_at_utc: datetime,
    registry: Mapping[str, Mapping[str, object]] | None = None,
    product_id_resolver: ProductIdResolver | None = None,
    quality_events: Sequence[str] = (),
) -> LaColoniaValidatedOfferResult:
    """Normaliza y sella hash sólo después de aplicar el overlay de La Colonia."""

    normalized = normalize_la_colonia_raw_product(
        raw,
        currency=currency,
        registry=registry,
        product_id_resolver=product_id_resolver,
    )
    validated = validate_normalized_offer(
        normalized.offer,
        validated_at_utc=validated_at_utc,
        quality_events=quality_events,
    )
    return LaColoniaValidatedOfferResult(
        validated_offer=validated,
        product_normalization=normalized.product_normalization,
    )


def normalize_and_validate_la_colonia_raw_products(
    raw_products: Sequence[RawProduct],
    *,
    currency: str,
    validated_at_utc: datetime,
    registry: Mapping[str, Mapping[str, object]] | None = None,
    product_id_resolver: ProductIdResolver | None = None,
    quality_events: Sequence[str] = (),
) -> tuple[LaColoniaValidatedOfferResult, ...]:
    """Batch offline; cada fila conserva su propia provenance y falla cerrado."""

    if isinstance(raw_products, (str, bytes)) or not isinstance(raw_products, Sequence):
        raise LaColoniaOfferNormalizationError("raw_products_sequence_invalid")
    active_registry = _active_registry(registry)
    return tuple(
        normalize_and_validate_la_colonia_raw_product(
            raw,
            currency=currency,
            validated_at_utc=validated_at_utc,
            registry=active_registry,
            product_id_resolver=product_id_resolver,
            quality_events=quality_events,
        )
        for raw in raw_products
    )
