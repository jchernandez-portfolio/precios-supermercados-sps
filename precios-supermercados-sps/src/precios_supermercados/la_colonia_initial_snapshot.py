"""Carga offline del snapshot inicial aprobado de La Colonia SPS.

Esta pieza es deliberadamente específica: lee el ``full-catalog.json`` del artifact
#9590684834, verifica que sea exactamente el archivo aprobado y lo convierte al
modelo común existente. No descarga nada, no habilita extracción y no introduce
una plataforma genérica de ingestión.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .bigquery_adapter import (
    BigQueryAdapter,
    BigQueryApplyResult,
    BigQueryClientPort,
)
from .bigquery_persistence import BigQueryWritePlan, build_bigquery_write_plan
from .commercial_persistence_guard import (
    LA_COLONIA_INITIAL_SNAPSHOT_APPROVED_AT_UTC,
    LA_COLONIA_INITIAL_SNAPSHOT_LOCATION_ID,
    LA_COLONIA_INITIAL_SNAPSHOT_OFFERS,
    LA_COLONIA_INITIAL_SNAPSHOT_PRODUCTS,
    LA_COLONIA_INITIAL_SNAPSHOT_RUN_ID,
    LA_COLONIA_INITIAL_SNAPSHOT_SUPERMARKET_ID,
    prepare_la_colonia_initial_snapshot_persistence,
)
from .commercial_state import InMemoryCommercialState
from .enums import LocationStatus
from .models import RawProduct, ValidatedOffer
from .offer_normalization import normalize_and_validate_raw_products
from .scrapers.la_colonia import CATALOG_URL, EXTRACTOR_VERSION, SCHEMA_VERSION


class InitialSnapshotError(ValueError):
    """El archivo o su resultado durable no coincide con el snapshot aprobado."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


LA_COLONIA_INITIAL_SNAPSHOT_JSON_SHA256 = (
    "2780eeffa5ef62f2d1c8c2c8365e88da1ca0006622d2f7b1c3529f834c9b5e50"
)
LA_COLONIA_INITIAL_SNAPSHOT_LOCATION_EVIDENCE = (
    "github_actions_artifact:9590684834:location_verified_same_run"
)
_PRODUCT_KEYS = frozenset(
    {
        "availability",
        "brand",
        "category",
        "current_price",
        "ean",
        "is_promotion",
        "item_id",
        "presentation",
        "product_id",
        "reference",
        "reported_regular_price",
        "source_key",
        "source_key_type",
        "source_name",
    }
)


def _utc(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise InitialSnapshotError("snapshot_observed_at_invalid")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise InitialSnapshotError("snapshot_observed_at_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InitialSnapshotError("snapshot_observed_at_invalid")
    return parsed.astimezone(UTC)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise InitialSnapshotError(code)


def _subcategory(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    parts = tuple(part.strip() for part in value.split(">") if part.strip())
    return parts[-1] if len(parts) > 1 else None


def _read_exact_json(path: Path) -> Mapping[str, Any]:
    if not isinstance(path, Path):
        path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise InitialSnapshotError("snapshot_file_unreadable") from exc
    digest = hashlib.sha256(raw).hexdigest()
    _require(
        digest == LA_COLONIA_INITIAL_SNAPSHOT_JSON_SHA256,
        "snapshot_json_digest_mismatch",
    )
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InitialSnapshotError("snapshot_json_invalid") from exc
    if not isinstance(value, Mapping):
        raise InitialSnapshotError("snapshot_root_invalid")
    return value


def _validate_metadata(value: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    expected = {
        "result": "success",
        "supermarket_id": LA_COLONIA_INITIAL_SNAPSHOT_SUPERMARKET_ID,
        "location_id": LA_COLONIA_INITIAL_SNAPSHOT_LOCATION_ID,
        "catalog_complete": True,
        "validation_passed": True,
        "location_verified_same_run": True,
        "catalog_products_reported": LA_COLONIA_INITIAL_SNAPSHOT_PRODUCTS,
        "unique_products_extracted": LA_COLONIA_INITIAL_SNAPSHOT_PRODUCTS,
        "skus_extracted": LA_COLONIA_INITIAL_SNAPSHOT_OFFERS,
        "skus_with_price": LA_COLONIA_INITIAL_SNAPSHOT_OFFERS,
        "extraction_enabled": False,
    }
    for field_name, expected_value in expected.items():
        _require(
            value.get(field_name) == expected_value,
            f"snapshot_metadata_mismatch:{field_name}",
        )
    _require(value.get("catalog_product_coverage") == 1.0, "snapshot_coverage_mismatch")
    products = value.get("products")
    _require(
        isinstance(products, Sequence) and not isinstance(products, (str, bytes)),
        "snapshot_products_invalid",
    )
    typed = tuple(item for item in products if isinstance(item, Mapping))
    _require(len(typed) == len(products), "snapshot_product_row_invalid")
    _require(len(typed) == LA_COLONIA_INITIAL_SNAPSHOT_OFFERS, "snapshot_offer_count_mismatch")
    return typed


def _raw_product(row: Mapping[str, Any], *, observed_at_utc: datetime) -> RawProduct:
    _require(set(row) == _PRODUCT_KEYS, "snapshot_product_schema_mismatch")
    return RawProduct(
        supermarket_id=LA_COLONIA_INITIAL_SNAPSHOT_SUPERMARKET_ID,
        location_id=LA_COLONIA_INITIAL_SNAPSHOT_LOCATION_ID,
        source_key_type=row["source_key_type"],
        source_key=str(row["source_key"]),
        source_name=str(row["source_name"]),
        product_url=None,
        observed_at_utc=observed_at_utc,
        scrape_run_id=LA_COLONIA_INITIAL_SNAPSHOT_RUN_ID,
        extractor_version=EXTRACTOR_VERSION,
        schema_version=SCHEMA_VERSION,
        source_url=CATALOG_URL,
        source_sku=row.get("reference"),
        source_brand=row.get("brand"),
        source_presentation=row.get("presentation"),
        source_category=row.get("category"),
        image_url=None,
        location_status=LocationStatus.CONFIRMED,
        location_evidence=LA_COLONIA_INITIAL_SNAPSHOT_LOCATION_EVIDENCE,
        location_confidence=Decimal("1"),
        raw_values={
            "product_id": row.get("product_id"),
            "item_id": row.get("item_id"),
            "reference": row.get("reference"),
            "ean": row.get("ean"),
            "current_price": row.get("current_price"),
            "reported_regular_price": row.get("reported_regular_price"),
            "is_promotion": row.get("is_promotion"),
            "availability": row.get("availability"),
            "subcategory": _subcategory(row.get("category")),
        },
    )


def load_la_colonia_initial_snapshot(path: Path) -> tuple[ValidatedOffer, ...]:
    """Convierte el JSON aprobado en 9,439 ``ValidatedOffer`` deterministas."""

    value = _read_exact_json(path)
    products = _validate_metadata(value)
    observed_at_utc = _utc(value.get("observed_at_utc"))
    raw_products = tuple(
        _raw_product(row, observed_at_utc=observed_at_utc) for row in products
    )
    identities = {(item.source_key_type.value, item.source_key) for item in raw_products}
    _require(
        len(identities) == LA_COLONIA_INITIAL_SNAPSHOT_OFFERS,
        "snapshot_source_identity_duplicate",
    )
    product_ids = {str(row.get("product_id")) for row in products}
    _require(
        len(product_ids) == LA_COLONIA_INITIAL_SNAPSHOT_PRODUCTS,
        "snapshot_product_identity_count_mismatch",
    )
    offers = normalize_and_validate_raw_products(
        raw_products,
        currency="HNL",
        validated_at_utc=LA_COLONIA_INITIAL_SNAPSHOT_APPROVED_AT_UTC,
    )
    _require(len(offers) == LA_COLONIA_INITIAL_SNAPSHOT_OFFERS, "snapshot_normalized_count_mismatch")
    return offers


def build_la_colonia_initial_snapshot_bigquery_plan(path: Path) -> BigQueryWritePlan:
    """Construye el plan BigQuery completo del snapshot sin hacer I/O cloud."""

    offers = load_la_colonia_initial_snapshot(path)
    prepared = prepare_la_colonia_initial_snapshot_persistence(
        InMemoryCommercialState(),
        offers,
    )
    return build_bigquery_write_plan(
        prepared,
        catalog_products_reported=LA_COLONIA_INITIAL_SNAPSHOT_PRODUCTS,
        unique_products_extracted=LA_COLONIA_INITIAL_SNAPSHOT_PRODUCTS,
        skus_extracted=LA_COLONIA_INITIAL_SNAPSHOT_OFFERS,
        skus_with_price=LA_COLONIA_INITIAL_SNAPSHOT_OFFERS,
        catalog_product_coverage=Decimal("1"),
    )


def apply_la_colonia_initial_snapshot_bigquery(
    path: Path,
    *,
    client: BigQueryClientPort,
    dataset_id: str,
) -> BigQueryApplyResult:
    """Bootstrap + carga + reconciliación usando el port BigQuery ya existente."""

    plan = build_la_colonia_initial_snapshot_bigquery_plan(path)
    adapter = BigQueryAdapter(client, dataset_id=dataset_id)
    adapter.bootstrap()
    result = adapter.apply(plan)
    read_back = adapter.read_back(
        supermarket_id=LA_COLONIA_INITIAL_SNAPSHOT_SUPERMARKET_ID,
        location_id=LA_COLONIA_INITIAL_SNAPSHOT_LOCATION_ID,
    )
    _require(
        len(read_back.products) == LA_COLONIA_INITIAL_SNAPSHOT_OFFERS,
        "durable_product_count_mismatch",
    )
    _require(
        len(read_back.latest_prices) == LA_COLONIA_INITIAL_SNAPSHOT_OFFERS,
        "durable_price_count_mismatch",
    )
    _require(
        len(read_back.latest_inventory) == LA_COLONIA_INITIAL_SNAPSHOT_OFFERS,
        "durable_inventory_count_mismatch",
    )
    matching_runs = tuple(
        row for row in read_back.runs
        if row["scrape_run_id"] == LA_COLONIA_INITIAL_SNAPSHOT_RUN_ID
    )
    _require(len(matching_runs) == 1, "durable_run_missing")
    _require(matching_runs[0]["run_fingerprint"] == plan.run_fingerprint, "durable_run_fingerprint_mismatch")
    return result
