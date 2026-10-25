"""Proyección offline del batch comercial al modelo físico BigQuery.

El motor Python de current/history sigue siendo la autoridad de transición. Esta
capa no intenta convertir sus periodos SCD en el histórico analítico: para cada
run comercial aceptado materializa una observación de precio e inventario por
oferta observada. Así ``precio igual observado hoy`` queda distinguible de
``no hubo observación hoy``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any

from .bigquery_contract import (
    BIGQUERY_TABLE_BY_NAME,
    BIGQUERY_TABLE_NAMES,
    INVENTARIO_HISTORICO,
    LOCATIONS,
    NORMALIZATION_OVERRIDES,
    PRECIOS_HISTORICOS,
    PRODUCT_MAPPING,
    PRODUCTOS,
    QUALITY_EVENTS,
    SCRAPE_RUNS,
    SUPERMARKETS,
)
from .commercial_persistence_batch import PreparedCommercialPersistence
from .identifiers import canonicalize_gtin, generate_gtin_product_id
from .tabular_persistence import (
    CFG_LOCATIONS,
    CFG_SUPERMARKETS,
    FACT_OFFERS_CURRENT,
    FACT_QUALITY_EVENTS,
)


class BigQueryPersistenceError(ValueError):
    """El batch no puede proyectarse sin violar el contrato BigQuery."""


@dataclass(frozen=True, slots=True)
class BigQueryWritePlan:
    scrape_run_id: str
    run_fingerprint: str
    rows: Mapping[str, tuple[Mapping[str, Any], ...]]

    def __post_init__(self) -> None:
        if not self.scrape_run_id or not self.scrape_run_id.strip():
            raise BigQueryPersistenceError("scrape_run_id_invalid")
        if len(self.run_fingerprint) != 64 or any(
            char not in "0123456789abcdef" for char in self.run_fingerprint
        ):
            raise BigQueryPersistenceError("run_fingerprint_invalid")
        if set(self.rows) != set(BIGQUERY_TABLE_NAMES):
            raise BigQueryPersistenceError("plan_tables_mismatch")
        normalized: dict[str, tuple[Mapping[str, Any], ...]] = {}
        for table_name in BIGQUERY_TABLE_NAMES:
            spec = BIGQUERY_TABLE_BY_NAME[table_name]
            source_rows = self.rows[table_name]
            if not isinstance(source_rows, tuple):
                raise BigQueryPersistenceError("plan_rows_not_tuple")
            seen: set[tuple[Any, ...]] = set()
            closed_rows: list[Mapping[str, Any]] = []
            for source_row in source_rows:
                if not isinstance(source_row, Mapping):
                    raise BigQueryPersistenceError("plan_row_not_mapping")
                if set(source_row) != set(spec.columns):
                    raise BigQueryPersistenceError(
                        f"row_schema_mismatch:{table_name}"
                    )
                row = {column: source_row[column] for column in spec.columns}
                for field in spec.fields:
                    if field.required and row[field.name] is None:
                        raise BigQueryPersistenceError(
                            f"required_value_missing:{table_name}.{field.name}"
                        )
                key = tuple(row[column] for column in spec.logical_key)
                if any(value in (None, "") for value in key):
                    raise BigQueryPersistenceError(
                        f"logical_key_empty:{table_name}"
                    )
                if key in seen:
                    raise BigQueryPersistenceError(
                        f"logical_key_duplicate:{table_name}"
                    )
                seen.add(key)
                closed_rows.append(MappingProxyType(row))
            normalized[table_name] = tuple(closed_rows)
        run_rows = normalized[SCRAPE_RUNS.name]
        if len(run_rows) != 1:
            raise BigQueryPersistenceError("exactly_one_scrape_run_required")
        if run_rows[0]["scrape_run_id"] != self.scrape_run_id:
            raise BigQueryPersistenceError("scrape_run_identity_mismatch")
        if run_rows[0]["run_fingerprint"] != self.run_fingerprint:
            raise BigQueryPersistenceError("scrape_run_fingerprint_mismatch")
        object.__setattr__(self, "rows", MappingProxyType(normalized))

    @property
    def row_counts(self) -> Mapping[str, int]:
        return MappingProxyType(
            {table: len(rows) for table, rows in self.rows.items()}
        )


def _stable_id(prefix: str, *parts: object) -> str:
    raw = json.dumps(parts, ensure_ascii=False, separators=(",", ":"), default=str)
    return prefix + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def _canonical(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Mapping):
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    return value


def _fingerprint(rows: Mapping[str, Sequence[Mapping[str, Any]]]) -> str:
    payload = {
        table_name: [_canonical(dict(row)) for row in rows[table_name]]
        for table_name in sorted(rows)
    }
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _closed(table_name: str, **values: Any) -> dict[str, Any]:
    spec = BIGQUERY_TABLE_BY_NAME[table_name]
    if set(values) != set(spec.columns):
        raise BigQueryPersistenceError(f"builder_schema_mismatch:{table_name}")
    return {column: values[column] for column in spec.columns}


def _optional_count(value: int | None, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BigQueryPersistenceError(f"{field_name}_invalid")
    return value


def _optional_coverage(
    value: Decimal | int | float | str | None,
) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise BigQueryPersistenceError("catalog_product_coverage_invalid") from exc
    if not result.is_finite() or result < 0 or result > 1:
        raise BigQueryPersistenceError("catalog_product_coverage_invalid")
    return result


def _product_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return _closed(
        PRODUCTOS.name,
        supermarket_id=row["supermarket_id"],
        source_product_id=row["source_product_id"],
        product_id=row["product_id"],
        source_key_type=row["source_key_type"],
        source_key=row["source_key"],
        source_sku=row["source_sku"],
        source_name=row["source_name"],
        normalized_name=row["normalized_name"],
        source_brand=row["source_brand"],
        normalized_brand=row["normalized_brand"],
        source_presentation=row["source_presentation"],
        source_category=row["source_category"],
        category=row["category"],
        subcategory=row["subcategory"],
        variant=row["variant"],
        unit_count=row["unit_count"],
        content_per_unit=row["content_per_unit"],
        measurement_unit=row["measurement_unit"],
        total_content=row["total_content"],
        barcode=row["barcode"],
        product_url=row["product_url"],
        image_url=row["image_url"],
        review_status=row["review_status"],
        first_seen_at_utc=row["first_observed_at_utc"],
        last_seen_at_utc=row["last_observed_at_utc"],
        last_scrape_run_id=row["last_scrape_run_id"],
    )


def _price_row(row: Mapping[str, Any], run_id: str) -> dict[str, Any]:
    return _closed(
        PRECIOS_HISTORICOS.name,
        price_observation_id=_stable_id(
            "price_", run_id, row["location_id"], row["source_product_id"]
        ),
        supermarket_id=row["supermarket_id"],
        location_id=row["location_id"],
        source_product_id=row["source_product_id"],
        product_id=row["product_id"],
        currency=row["currency"],
        current_price=row["current_price"],
        reported_regular_price=row["reported_regular_price"],
        is_promotion=row["is_promotion"],
        promotion_evidence=None,
        observed_at_utc=row["observed_at_utc"],
        scrape_run_id=run_id,
        extractor_version=row["extractor_version"],
        schema_version=row["schema_version"],
    )


def _inventory_row(row: Mapping[str, Any], run_id: str) -> dict[str, Any]:
    # El snapshot actual no preserva cantidad/seller/evidencia de inventario.
    # Se conserva la clasificación observada y UNKNOWN permanece UNKNOWN.
    return _closed(
        INVENTARIO_HISTORICO.name,
        inventory_observation_id=_stable_id(
            "inventory_", run_id, row["location_id"], row["source_product_id"], None
        ),
        supermarket_id=row["supermarket_id"],
        location_id=row["location_id"],
        source_product_id=row["source_product_id"],
        product_id=row["product_id"],
        seller_id=None,
        available_quantity_observed=None,
        availability=row["availability"],
        availability_evidence=None,
        quantity_is_exact=False,
        observed_at_utc=row["observed_at_utc"],
        scrape_run_id=run_id,
        extractor_version=row["extractor_version"],
        schema_version=row["schema_version"],
    )


def _mapping_row(row: Mapping[str, Any]) -> dict[str, Any]:
    canonical_gtin = canonicalize_gtin(row["barcode"])
    expected_product_id = (
        generate_gtin_product_id(canonical_gtin) if canonical_gtin is not None else None
    )
    mapped = expected_product_id is not None and row["product_id"] == expected_product_id
    return _closed(
        PRODUCT_MAPPING.name,
        supermarket_id=row["supermarket_id"],
        source_product_id=row["source_product_id"],
        product_id=row["product_id"],
        mapping_status="mapped" if mapped else "pending",
        mapping_method="gtin" if mapped else "source_singleton",
        canonical_gtin=canonical_gtin if mapped else None,
        review_reason=None if mapped else "canonical_identity_pending",
        last_observed_at_utc=row["last_observed_at_utc"],
        last_scrape_run_id=row["last_scrape_run_id"],
    )


def build_bigquery_write_plan(
    prepared: PreparedCommercialPersistence,
    *,
    normalization_overrides: Sequence[Mapping[str, Any]] = (),
    catalog_products_reported: int | None = None,
    unique_products_extracted: int | None = None,
    skus_extracted: int | None = None,
    skus_with_price: int | None = None,
    catalog_product_coverage: Decimal | int | float | str | None = None,
) -> BigQueryWritePlan:
    """Convierte un run ya validado/aplicado en memoria a una escritura durable.

    Un run rechazado conserva ledger y quality events, pero no contamina productos,
    precios, inventario ni mapping. Los overrides sólo se materializan cuando el
    caller entrega filas explícitas con ``source_signature``; nunca se sintetizan.
    Las métricas de catálogo son opcionales porque no todos los runners las conocen;
    cuando se entregan quedan ligadas al fingerprint inmutable del run.
    """

    if not isinstance(prepared, PreparedCommercialPersistence):
        raise BigQueryPersistenceError("prepared_persistence_invalid")
    batch = prepared.batch.rows
    current_rows = tuple(batch.get(FACT_OFFERS_CURRENT.name, ()))
    accepted = prepared.apply_result.commercial_update_allowed
    if not accepted and current_rows:
        raise BigQueryPersistenceError("rejected_run_contains_current_rows")

    catalog_products_reported = _optional_count(
        catalog_products_reported,
        "catalog_products_reported",
    )
    unique_products_extracted = _optional_count(
        unique_products_extracted,
        "unique_products_extracted",
    )
    skus_extracted = _optional_count(skus_extracted, "skus_extracted")
    skus_with_price = _optional_count(skus_with_price, "skus_with_price")
    coverage = _optional_coverage(catalog_product_coverage)
    if (
        skus_extracted is not None
        and skus_with_price is not None
        and skus_with_price > skus_extracted
    ):
        raise BigQueryPersistenceError("skus_with_price_exceeds_skus_extracted")

    supermarkets = tuple(
        _closed(
            SUPERMARKETS.name,
            supermarket_id=row["supermarket_id"],
            supermarket_name=row["supermarket_name"],
            country_code=row["country_code"],
            location_selection_mode=row["location_selection_mode"],
            is_active=row["is_active"],
        )
        for row in batch[CFG_SUPERMARKETS.name]
    )
    locations = tuple(
        _closed(
            LOCATIONS.name,
            location_id=row["location_id"],
            supermarket_id=row["supermarket_id"],
            city_id=row["city_id"],
            city_name=row["city_name"],
            granularity=row["granularity"],
            source_location_key=row["source_location_key"],
            is_available=row["is_available"],
            in_scope=row["in_scope"],
            extraction_enabled=row["extraction_enabled"],
            technical_binding_confirmed=row["technical_binding_confirmed"],
            evidence=row["evidence"],
        )
        for row in batch[CFG_LOCATIONS.name]
    )

    products = tuple(_product_row(row) for row in current_rows) if accepted else ()
    prices = (
        tuple(_price_row(row, prepared.run_record.scrape_run_id) for row in current_rows)
        if accepted
        else ()
    )
    inventory = (
        tuple(
            _inventory_row(row, prepared.run_record.scrape_run_id)
            for row in current_rows
        )
        if accepted
        else ()
    )
    mappings = tuple(_mapping_row(row) for row in current_rows) if accepted else ()

    source_by_offer = {
        row["offer_id"]: row["source_product_id"] for row in current_rows
    }
    quality = tuple(
        _closed(
            QUALITY_EVENTS.name,
            quality_event_id=row["quality_event_id"],
            scrape_run_id=row["scrape_run_id"],
            supermarket_id=row["supermarket_id"],
            location_id=row["location_id"],
            source_product_id=source_by_offer.get(row["offer_id"]),
            offer_id=row["offer_id"],
            category=row["category"],
            severity=row["severity"],
            event_code=row["event_code"],
            observed_at_utc=row["observed_at_utc"],
        )
        for row in batch[FACT_QUALITY_EVENTS.name]
    )

    override_rows = tuple(dict(row) for row in normalization_overrides)
    for row in override_rows:
        if set(row) != set(NORMALIZATION_OVERRIDES.columns):
            raise BigQueryPersistenceError("normalization_override_schema_mismatch")

    record = prepared.run_record
    run_base = {
        "scrape_run_id": record.scrape_run_id,
        "run_evidence_id": record.run_evidence_id,
        "supermarket_id": record.supermarket_id,
        "location_id": record.location_id,
        "run_status": record.run_status.value,
        "catalog_accepted": record.catalog_accepted,
        "commercial_update_allowed": prepared.apply_result.commercial_update_allowed,
        "started_at_utc": record.started_at_utc,
        "finished_at_utc": record.finished_at_utc,
        "products_observed": record.products_observed,
        "offers_observed": record.offers_observed,
        "quality_event_count": record.quality_event_count,
        "current_created": record.current_created,
        "current_changed": record.current_changed,
        "current_confirmed": record.current_confirmed,
        "offers_ignored": record.offers_ignored,
        "catalog_products_reported": catalog_products_reported,
        "unique_products_extracted": unique_products_extracted,
        "skus_extracted": skus_extracted,
        "skus_with_price": skus_with_price,
        "catalog_product_coverage": coverage,
        "extractor_version": current_rows[0]["extractor_version"] if current_rows else None,
        "schema_version": current_rows[0]["schema_version"] if current_rows else None,
    }

    rows_without_run = {
        SUPERMARKETS.name: supermarkets,
        LOCATIONS.name: locations,
        PRODUCTOS.name: products,
        PRECIOS_HISTORICOS.name: prices,
        INVENTARIO_HISTORICO.name: inventory,
        QUALITY_EVENTS.name: quality,
        NORMALIZATION_OVERRIDES.name: override_rows,
        PRODUCT_MAPPING.name: mappings,
    }
    fingerprint = _fingerprint(
        {**rows_without_run, "scrape_run_base": (run_base,)}
    )
    run_row = _closed(
        SCRAPE_RUNS.name,
        run_fingerprint=fingerprint,
        **run_base,
    )
    all_rows = {
        SUPERMARKETS.name: supermarkets,
        LOCATIONS.name: locations,
        PRODUCTOS.name: products,
        PRECIOS_HISTORICOS.name: prices,
        INVENTARIO_HISTORICO.name: inventory,
        SCRAPE_RUNS.name: (run_row,),
        QUALITY_EVENTS.name: quality,
        NORMALIZATION_OVERRIDES.name: override_rows,
        PRODUCT_MAPPING.name: mappings,
    }
    return BigQueryWritePlan(
        scrape_run_id=record.scrape_run_id,
        run_fingerprint=fingerprint,
        rows=all_rows,
    )
