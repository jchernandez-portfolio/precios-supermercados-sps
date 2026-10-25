"""Contrato tabular común para persistencia y revisión humana.

No escribe en Google Sheets ni en otro backend. Define las columnas canónicas que
compartirán todos los supermercados y serializa producto, mapping, current/history
sin perder identidad fuente ni comercial.

La capa es fail-closed respecto a ubicación: una oferta sólo puede convertirse en
fila comercial cuando la ubicación existe, está disponible, está dentro del
alcance, tiene granularidad demostrada y cumple el binding requerido. El switch
``extraction_enabled`` queda fuera de este gate porque controla tráfico futuro,
no la persistencia de una observación ya obtenida.

``dim_products`` contiene únicamente atributos normalizados/canónicos de productos
ya mapeados. ``map_source_products`` conserva la relación fuente -> producto y es
la cola explícita de revisión cuando ``product_id`` sigue siendo provisional.

Current e histórico conservan además la evidencia estructurada mínima necesaria
para reconstruir un ``ValidatedOffer`` después de reiniciar un runner. No se
persiste ``raw_values`` porque no participa en identidad/hash/transición y puede
contener payload fuente voluminoso; los campos que sí participan en el
fingerprint comercial quedan explícitos y auditables en columnas cerradas.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from .commercial_state import CurrentCommercialOffer, OfferHistoryPeriod
from .enums import LocationStatus
from .identifiers import (
    canonicalize_gtin,
    generate_gtin_product_id,
    generate_pending_product_id,
    generate_source_product_id,
)
from .locations import (
    DEFAULT_LOCATION_CATALOG,
    LocationCatalog,
    LocationConfig,
    LocationConfigError,
    LocationSelectionMode,
)
from .models import NormalizedOffer, ValidatedOffer


class TabularPersistenceError(ValueError):
    """Una fila no cumple el contrato tabular persistible."""


@dataclass(frozen=True, slots=True)
class TableSpec:
    """Esquema cerrado de una tabla lógica compartida."""

    name: str
    columns: tuple[str, ...]
    primary_key: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise TabularPersistenceError("name no puede estar vacío")
        if not self.columns or len(set(self.columns)) != len(self.columns):
            raise TabularPersistenceError("columns debe ser único y no vacío")
        if not self.primary_key or not set(self.primary_key).issubset(self.columns):
            raise TabularPersistenceError("primary_key debe existir en columns")


CFG_SUPERMARKETS = TableSpec(
    name="cfg_supermarkets",
    columns=(
        "supermarket_id",
        "supermarket_name",
        "country_code",
        "location_selection_mode",
        "is_active",
    ),
    primary_key=("supermarket_id",),
)

CFG_LOCATIONS = TableSpec(
    name="cfg_locations",
    columns=(
        "location_id",
        "supermarket_id",
        "city_id",
        "city_name",
        "granularity",
        "source_location_key",
        "is_available",
        "in_scope",
        "extraction_enabled",
        "technical_binding_confirmed",
        "evidence",
    ),
    primary_key=("location_id",),
)

DIM_PRODUCTS = TableSpec(
    name="dim_products",
    columns=(
        "product_id",
        "canonical_gtin",
        "normalized_name",
        "normalized_brand",
        "category",
        "subcategory",
        "variant",
        "unit_count",
        "content_per_unit",
        "measurement_unit",
        "total_content",
        "review_status",
    ),
    primary_key=("product_id",),
)

MAP_SOURCE_PRODUCTS = TableSpec(
    name="map_source_products",
    columns=(
        "source_product_id",
        "supermarket_id",
        "source_key_type",
        "source_key",
        "source_sku",
        "source_name",
        "source_brand",
        "source_presentation",
        "barcode",
        "product_url",
        "product_id",
        "mapping_status",
        "mapping_method",
        "review_reason",
        "normalized_name",
        "normalized_brand",
        "category",
        "subcategory",
        "variant",
        "unit_count",
        "content_per_unit",
        "measurement_unit",
        "total_content",
        "last_observed_at_utc",
        "last_scrape_run_id",
    ),
    primary_key=("source_product_id",),
)

# Estas columnas son deliberadamente explícitas. El Spreadsheet es también el
# snapshot durable que debe permitir continuar el histórico en una ejecución
# posterior, no sólo una vista para BI.
FACT_OFFERS_CURRENT = TableSpec(
    name="fact_offers_current",
    columns=(
        "offer_id",
        "supermarket_id",
        "supermarket_name",
        "location_id",
        "city_id",
        "city_name",
        "location_granularity",
        "location_status",
        "location_evidence",
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
        "reported_regular_price",
        "is_promotion",
        "availability",
        "unit_price",
        "unit_price_basis",
        "state_hash",
        "review_status",
        "quality_events_json",
        "offer_scrape_run_id",
        "extractor_version",
        "schema_version",
        "source_url",
        "observed_at_utc",
        "validated_at_utc",
        "first_observed_at_utc",
        "last_observed_at_utc",
        "last_scrape_run_id",
        "product_url",
        "image_url",
    ),
    primary_key=("offer_id",),
)

FACT_OFFER_HISTORY = TableSpec(
    name="fact_offer_history",
    columns=(
        "offer_history_id",
        "offer_id",
        "supermarket_id",
        "supermarket_name",
        "location_id",
        "city_id",
        "city_name",
        "location_granularity",
        "location_status",
        "location_evidence",
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
        "reported_regular_price",
        "is_promotion",
        "availability",
        "unit_price",
        "unit_price_basis",
        "state_hash",
        "change_type",
        "changed_fields_json",
        "review_status",
        "quality_events_json",
        "offer_scrape_run_id",
        "extractor_version",
        "schema_version",
        "source_url",
        "validated_at_utc",
        "valid_from_utc",
        "valid_to_utc",
        "opened_by_scrape_run_id",
        "closed_by_scrape_run_id",
        "last_confirmed_by_scrape_run_id",
        "last_observed_at_utc",
        "product_url",
        "image_url",
    ),
    primary_key=("offer_history_id",),
)

FACT_SCRAPE_RUNS = TableSpec(
    name="fact_scrape_runs",
    columns=(
        "scrape_run_id",
        "supermarket_id",
        "location_id",
        "run_status",
        "catalog_accepted",
        "started_at_utc",
        "finished_at_utc",
        "products_observed",
        "offers_observed",
        "current_created",
        "current_changed",
        "current_confirmed",
        "offers_ignored",
        "quality_event_count",
        "run_evidence_id",
    ),
    primary_key=("scrape_run_id",),
)

FACT_QUALITY_EVENTS = TableSpec(
    name="fact_quality_events",
    columns=(
        "quality_event_id",
        "scrape_run_id",
        "supermarket_id",
        "location_id",
        "offer_id",
        "category",
        "severity",
        "event_code",
        "observed_at_utc",
    ),
    primary_key=("quality_event_id",),
)

TABLE_SPECS: Mapping[str, TableSpec] = MappingProxyType(
    {
        spec.name: spec
        for spec in (
            CFG_SUPERMARKETS,
            CFG_LOCATIONS,
            DIM_PRODUCTS,
            MAP_SOURCE_PRODUCTS,
            FACT_OFFERS_CURRENT,
            FACT_OFFER_HISTORY,
            FACT_SCRAPE_RUNS,
            FACT_QUALITY_EVENTS,
        )
    }
)


def _enum_text(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    normalized = value.normalize()
    rendered = format(normalized, "f")
    return "0" if rendered in {"", "-0"} else rendered


def _utc_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise TabularPersistenceError("datetime sin zona horaria")
    if value.utcoffset().total_seconds() != 0:
        raise TabularPersistenceError("datetime debe estar en UTC")
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json_string_list(values: tuple[str, ...]) -> str:
    return json.dumps(list(values), ensure_ascii=False, separators=(",", ":"))


def _closed_row(spec: TableSpec, values: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(values).difference(spec.columns)
    missing = set(spec.columns).difference(values)
    if unknown or missing:
        raise TabularPersistenceError(
            f"fila {spec.name} no coincide con esquema; missing={sorted(missing)} "
            f"unknown={sorted(unknown)}"
        )
    row = {column: values[column] for column in spec.columns}
    if any(row[column] in (None, "") for column in spec.primary_key):
        raise TabularPersistenceError(f"primary key vacía en {spec.name}")
    return row


def supermarket_config_rows(
    catalog: LocationCatalog = DEFAULT_LOCATION_CATALOG,
) -> tuple[dict[str, Any], ...]:
    """Serializa una sola tabla de supermercados para todas las fuentes."""

    rows = []
    for supermarket_id in sorted(catalog.supermarkets):
        supermarket = catalog.supermarkets[supermarket_id]
        rows.append(
            _closed_row(
                CFG_SUPERMARKETS,
                {
                    "supermarket_id": supermarket.supermarket_id,
                    "supermarket_name": supermarket.supermarket_name,
                    "country_code": supermarket.country_code,
                    "location_selection_mode": supermarket.location_selection_mode.value,
                    "is_active": supermarket.is_active,
                },
            )
        )
    return tuple(rows)


def location_config_rows(
    catalog: LocationCatalog = DEFAULT_LOCATION_CATALOG,
) -> tuple[dict[str, Any], ...]:
    """Serializa todas las ubicaciones en una tabla común."""

    rows = []
    for location in sorted(catalog.locations, key=lambda value: value.location_id):
        rows.append(
            _closed_row(
                CFG_LOCATIONS,
                {
                    "location_id": location.location_id,
                    "supermarket_id": location.supermarket_id,
                    "city_id": location.city_id,
                    "city_name": location.city_name,
                    "granularity": location.granularity.value,
                    "source_location_key": location.source_location_key,
                    "is_available": location.is_available,
                    "in_scope": location.in_scope,
                    "extraction_enabled": location.extraction_enabled,
                    "technical_binding_confirmed": location.technical_binding_confirmed,
                    "evidence": location.evidence,
                },
            )
        )
    return tuple(rows)


def validate_offer_location_for_persistence(
    offer: NormalizedOffer,
    catalog: LocationCatalog = DEFAULT_LOCATION_CATALOG,
) -> LocationConfig:
    """Impide persistir una oferta bajo una ciudad no demostrada.

    ``extraction_enabled`` se excluye deliberadamente: es un switch de tráfico y
    no cambia la validez comercial de una observación histórica ya capturada.
    """

    if not isinstance(offer, NormalizedOffer):
        raise TabularPersistenceError("offer debe ser NormalizedOffer")
    try:
        location = catalog.location(offer.location_id)
        block_reason = catalog.extraction_block_reason(offer.location_id)
    except LocationConfigError as exc:
        raise TabularPersistenceError(str(exc)) from exc
    if block_reason not in {None, "extraction_disabled"}:
        raise TabularPersistenceError(block_reason)
    if location.supermarket_id != offer.supermarket_id:
        raise TabularPersistenceError(
            "location_id pertenece a otro supermarket_id"
        )
    if offer.location_status is LocationStatus.UNKNOWN:
        raise TabularPersistenceError("location_status_unknown")
    supermarket = catalog.supermarket(offer.supermarket_id)
    if (
        supermarket.location_selection_mode
        is LocationSelectionMode.SOURCE_SELECTION_REQUIRED
        and offer.location_status is not LocationStatus.CONFIRMED
    ):
        raise TabularPersistenceError(
            "multi_location_offer_requires_confirmed_location"
        )
    return location


def _validated_offer(value: ValidatedOffer) -> NormalizedOffer:
    if not isinstance(value, ValidatedOffer):
        raise TabularPersistenceError("validated debe ser ValidatedOffer")
    offer = value.offer
    expected_source_product_id = generate_source_product_id(
        offer.supermarket_id,
        offer.source_key_type,
        offer.source_key,
    )
    if offer.source_product_id != expected_source_product_id:
        raise TabularPersistenceError("source_product_id no es determinista")
    return offer


def _mapping_method(offer: NormalizedOffer) -> tuple[str, str, str | None, str | None]:
    gtin = canonicalize_gtin(offer.barcode)
    if offer.product_id.startswith("prod_pending_"):
        expected_product_id = generate_pending_product_id(offer.source_product_id)
        if offer.product_id != expected_product_id:
            raise TabularPersistenceError(
                "product_id_pending_no_reconcilia_con_source_product_id"
            )
        return "pending", "pending", "pending_product_mapping", gtin
    if offer.product_id.startswith("prod_gtin_"):
        if gtin is None or offer.product_id != generate_gtin_product_id(gtin):
            raise TabularPersistenceError("product_id_gtin_no_reconcilia_con_barcode")
        return "mapped", "gtin", None, gtin
    return "mapped", "explicit", None, gtin


def product_dimension_row(validated: ValidatedOffer) -> dict[str, Any] | None:
    """Materializa sólo productos ya mapeados, sin columnas específicas de fuente."""

    offer = _validated_offer(validated)
    mapping_status, _, _, canonical_gtin = _mapping_method(offer)
    if mapping_status == "pending":
        return None
    return _closed_row(
        DIM_PRODUCTS,
        {
            "product_id": offer.product_id,
            "canonical_gtin": canonical_gtin,
            "normalized_name": offer.normalized_name,
            "normalized_brand": offer.normalized_brand,
            "category": offer.category,
            "subcategory": offer.subcategory,
            "variant": offer.variant,
            "unit_count": offer.unit_count,
            "content_per_unit": _decimal_text(offer.content_per_unit),
            "measurement_unit": offer.measurement_unit,
            "total_content": _decimal_text(offer.total_content),
            "review_status": validated.review_status.value,
        },
    )


def source_product_mapping_row(validated: ValidatedOffer) -> dict[str, Any]:
    """Fila fuente -> producto; los pendientes forman la cola de revisión explícita."""

    offer = _validated_offer(validated)
    mapping_status, mapping_method, review_reason, _ = _mapping_method(offer)
    return _closed_row(
        MAP_SOURCE_PRODUCTS,
        {
            "source_product_id": offer.source_product_id,
            "supermarket_id": offer.supermarket_id,
            "source_key_type": offer.source_key_type.value,
            "source_key": offer.source_key,
            "source_sku": offer.source_sku,
            "source_name": offer.source_name,
            "source_brand": offer.source_brand,
            "source_presentation": offer.source_presentation,
            "barcode": offer.barcode,
            "product_url": offer.product_url,
            "product_id": offer.product_id,
            "mapping_status": mapping_status,
            "mapping_method": mapping_method,
            "review_reason": review_reason,
            "normalized_name": offer.normalized_name,
            "normalized_brand": offer.normalized_brand,
            "category": offer.category,
            "subcategory": offer.subcategory,
            "variant": offer.variant,
            "unit_count": offer.unit_count,
            "content_per_unit": _decimal_text(offer.content_per_unit),
            "measurement_unit": offer.measurement_unit,
            "total_content": _decimal_text(offer.total_content),
            "last_observed_at_utc": _utc_text(offer.observed_at_utc),
            "last_scrape_run_id": offer.scrape_run_id,
        },
    )


def _offer_base_row(
    validated: ValidatedOffer,
    catalog: LocationCatalog,
) -> dict[str, Any]:
    offer = _validated_offer(validated)
    location = validate_offer_location_for_persistence(offer, catalog)
    supermarket = catalog.supermarket(offer.supermarket_id)
    return {
        "offer_id": offer.offer_id,
        "supermarket_id": offer.supermarket_id,
        "supermarket_name": supermarket.supermarket_name,
        "location_id": offer.location_id,
        "city_id": location.city_id,
        "city_name": location.city_name,
        "location_granularity": location.granularity.value,
        "location_status": offer.location_status.value,
        "location_evidence": offer.location_evidence,
        "location_confidence": _decimal_text(offer.location_confidence),
        "source_product_id": offer.source_product_id,
        "product_id": offer.product_id,
        "source_key_type": offer.source_key_type.value,
        "source_key": offer.source_key,
        "source_sku": offer.source_sku,
        "source_name": offer.source_name,
        "normalized_name": offer.normalized_name,
        "source_brand": offer.source_brand,
        "normalized_brand": offer.normalized_brand,
        "source_presentation": offer.source_presentation,
        "source_category": offer.source_category,
        "barcode": offer.barcode,
        "category": offer.category,
        "subcategory": offer.subcategory,
        "variant": offer.variant,
        "unit_count": offer.unit_count,
        "content_per_unit": _decimal_text(offer.content_per_unit),
        "measurement_unit": offer.measurement_unit,
        "total_content": _decimal_text(offer.total_content),
        "currency": offer.currency,
        "current_price": _decimal_text(offer.current_price),
        "reported_regular_price": _decimal_text(offer.reported_regular_price),
        "is_promotion": offer.is_promotion,
        "availability": offer.availability.value,
        "unit_price": _decimal_text(offer.unit_price),
        "unit_price_basis": offer.unit_price_basis,
        "state_hash": validated.state_hash,
        "review_status": validated.review_status.value,
        "quality_events_json": _json_string_list(validated.quality_events),
        "offer_scrape_run_id": offer.scrape_run_id,
        "extractor_version": offer.extractor_version,
        "schema_version": offer.schema_version,
        "source_url": offer.source_url,
        "validated_at_utc": _utc_text(validated.validated_at_utc),
        "product_url": offer.product_url,
        "image_url": offer.image_url,
    }


def current_offer_row(
    current: CurrentCommercialOffer,
    catalog: LocationCatalog = DEFAULT_LOCATION_CATALOG,
) -> dict[str, Any]:
    """Fila única por oferta actual, compartida entre supermercados."""

    if not isinstance(current, CurrentCommercialOffer):
        raise TabularPersistenceError("current debe ser CurrentCommercialOffer")
    validated = current.validated_offer
    offer = validated.offer
    row = _offer_base_row(validated, catalog)
    row.update(
        {
            "observed_at_utc": _utc_text(offer.observed_at_utc),
            "first_observed_at_utc": _utc_text(current.first_observed_at_utc),
            "last_observed_at_utc": _utc_text(current.last_observed_at_utc),
            "last_scrape_run_id": current.last_scrape_run_id,
        }
    )
    return _closed_row(FACT_OFFERS_CURRENT, row)


def history_offer_row(
    period: OfferHistoryPeriod,
    catalog: LocationCatalog = DEFAULT_LOCATION_CATALOG,
) -> dict[str, Any]:
    """Fila inmutable de histórico; un cambio real abre un nuevo periodo."""

    if not isinstance(period, OfferHistoryPeriod):
        raise TabularPersistenceError("period debe ser OfferHistoryPeriod")
    row = _offer_base_row(period.validated_offer, catalog)
    row.pop("offer_id")
    row.update(
        {
            "offer_history_id": period.offer_history_id,
            "offer_id": period.offer_id,
            "change_type": period.change_type.value,
            "changed_fields_json": json.dumps(
                list(period.changed_fields),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "valid_from_utc": _utc_text(period.valid_from_utc),
            "valid_to_utc": _utc_text(period.valid_to_utc),
            "opened_by_scrape_run_id": period.opened_by_scrape_run_id,
            "closed_by_scrape_run_id": period.closed_by_scrape_run_id,
            "last_confirmed_by_scrape_run_id": period.last_confirmed_by_scrape_run_id,
            "last_observed_at_utc": _utc_text(period.last_observed_at_utc),
        }
    )
    return _closed_row(FACT_OFFER_HISTORY, row)
