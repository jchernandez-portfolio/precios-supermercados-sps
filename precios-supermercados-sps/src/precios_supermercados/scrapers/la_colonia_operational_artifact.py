"""Valida evidencia sanitizada del catálogo operativo de La Colonia SPS.

Este módulo trabaja exclusivamente sobre artifacts ya descargados. No hace red,
no persiste y no concede autoridad comercial. Su objetivo es separar:

- completitud técnica demostrable del catálogo;
- advertencias de calidad que pueden revisarse offline;
- aceptación comercial/productiva, que permanece fuera de esta capa.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from precios_supermercados.enums import AvailabilityStatus, RunStatus, SourceKeyType
from precios_supermercados.unpriced_unavailable import validate_unpriced_unavailable

_EXPECTED_METADATA = {
    "schema_version": "7",
    "catalog_type": "la_colonia_sps_full_read_only",
    "supermarket_id": "la_colonia",
    "location_id": "la_colonia_sps",
    "city": "San Pedro Sula",
    "capture_strategy": "operational_city_url_safe_brand_buckets_recovery_productSearchV3",
    "partition_strategy": "brand_buckets_authoritative_transport_safe_reverse_recovery",
    "location_verification_method": "structural_exact_city_control",
}
_AUTHORITY_FLAGS = (
    "catalog_accepted",
    "commercial_persistence",
    "production_authority",
    "extraction_enabled",
    "raw_context_persisted",
)
_MAX_PRODUCT_REQUESTS = 400
_EXPECTED_PAGE_SIZE = 50


class OperationalCatalogArtifactError(ValueError):
    """El valor recibido ni siquiera forma un artifact evaluable."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class OperationalCatalogAssessment:
    """Resultado offline; nunca equivale a aceptación comercial."""

    run_status: RunStatus
    technical_catalog_complete: bool
    ready_for_normalization: bool
    catalog_accepted: bool
    production_authority: bool
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    sku_rows: int
    unique_products: int
    missing_presentations: int
    unknown_availability: int
    in_stock: int
    out_of_stock: int
    promotion_rows: int
    unpriced_unavailable: int = 0

    def __post_init__(self) -> None:
        if self.catalog_accepted is not False:
            raise OperationalCatalogArtifactError("catalog_acceptance_forbidden")
        if self.production_authority is not False:
            raise OperationalCatalogArtifactError("production_authority_forbidden")
        if self.ready_for_normalization != self.technical_catalog_complete:
            raise OperationalCatalogArtifactError("normalization_readiness_inconsistent")
        if self.technical_catalog_complete != (len(self.blockers) == 0):
            raise OperationalCatalogArtifactError("technical_state_inconsistent")
        expected_status = (
            RunStatus.REJECTED
            if self.blockers
            else RunStatus.WARNING
            if self.warnings
            else RunStatus.SUCCESS
        )
        if self.run_status is not expected_status:
            raise OperationalCatalogArtifactError("run_status_inconsistent")


class _UnpricedBlocker(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code.removeprefix("snapshot_")


def _text(value: object) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _positive_decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() and result > 0 else None


def _non_negative_int(value: object) -> int | None:
    if type(value) is not int or value < 0:
        return None
    return value


def _exact_coverage(value: object) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        return Decimal(str(value)) == Decimal("1")
    except (InvalidOperation, TypeError, ValueError):
        return False


def assess_operational_catalog_artifact(
    artifact: Mapping[str, Any],
) -> OperationalCatalogAssessment:
    """Evalúa un full-catalog sanitizado sin convertirlo en autoridad comercial.

    Los problemas de identidad, cobertura, precio, presupuesto o contexto son
    blockers. Campos interpretativos como presentación faltante o disponibilidad
    ``unknown`` se conservan como warnings: no se inventa información.
    """

    if not isinstance(artifact, Mapping):
        raise OperationalCatalogArtifactError("artifact_mapping_required")

    blockers: list[str] = []
    warnings: list[str] = []

    for field_name, expected in _EXPECTED_METADATA.items():
        if artifact.get(field_name) != expected:
            blockers.append(f"{field_name}_mismatch")

    for field_name in _AUTHORITY_FLAGS:
        if artifact.get(field_name) is not False:
            blockers.append(f"{field_name}_must_be_false")

    if artifact.get("result") != "success":
        blockers.append("result_not_success")
    if artifact.get("catalog_complete") is not True:
        blockers.append("catalog_not_complete")
    if artifact.get("validation_passed") is not True:
        blockers.append("validation_not_passed")
    if artifact.get("location_verified_same_run") is not True:
        blockers.append("location_not_verified_same_run")
    if artifact.get("page_size") != _EXPECTED_PAGE_SIZE:
        blockers.append("page_size_mismatch")
    if not _exact_coverage(artifact.get("catalog_product_coverage")):
        blockers.append("catalog_product_coverage_not_exact")

    planned_requests = _non_negative_int(artifact.get("planned_product_requests"))
    completed_requests = _non_negative_int(artifact.get("product_requests_completed"))
    if planned_requests is None or planned_requests > _MAX_PRODUCT_REQUESTS:
        blockers.append("planned_product_request_budget_invalid")
    if completed_requests is None or completed_requests > _MAX_PRODUCT_REQUESTS:
        blockers.append("completed_product_request_budget_invalid")

    raw_products = artifact.get("products")
    if (
        not isinstance(raw_products, Sequence)
        or isinstance(raw_products, (str, bytes))
        or len(raw_products) == 0
    ):
        blockers.append("products_invalid")
        products: tuple[Mapping[str, Any], ...] = ()
    else:
        product_values: list[Mapping[str, Any]] = []
        for row in raw_products:
            if not isinstance(row, Mapping):
                blockers.append("product_row_invalid")
                continue
            product_values.append(row)
        products = tuple(product_values)

    identities: set[tuple[str, str]] = set()
    product_ids: set[str] = set()
    missing_presentations = 0
    unknown_availability = 0
    in_stock = 0
    out_of_stock = 0
    promotion_rows = 0

    allowed_source_keys = {item.value for item in SourceKeyType}
    allowed_availability = {item.value for item in AvailabilityStatus}

    for product in products:
        source_key_type = _text(product.get("source_key_type"))
        source_key = _text(product.get("source_key"))
        if source_key_type not in allowed_source_keys or source_key is None:
            blockers.append("source_identity_invalid")
        else:
            identity = (source_key_type, source_key)
            if identity in identities:
                blockers.append("duplicate_source_identity")
            else:
                identities.add(identity)

        product_id = _text(product.get("product_id"))
        if product_id is None:
            blockers.append("product_id_missing")
        else:
            product_ids.add(product_id)

        if _text(product.get("source_name")) is None:
            blockers.append("source_name_missing")
        if _text(product.get("brand")) is None:
            blockers.append("brand_missing")
        if _text(product.get("category")) is None:
            blockers.append("category_missing")

        current_price = _positive_decimal(product.get("current_price"))
        if current_price is None:
            blockers.append("current_price_invalid")

        availability = _text(product.get("availability"))
        if availability not in allowed_availability:
            blockers.append("availability_invalid")
        elif availability == AvailabilityStatus.UNKNOWN.value:
            unknown_availability += 1
        elif availability == AvailabilityStatus.IN_STOCK.value:
            in_stock += 1
        elif availability == AvailabilityStatus.OUT_OF_STOCK.value:
            out_of_stock += 1

        if _text(product.get("presentation")) is None:
            missing_presentations += 1

        promotion = product.get("is_promotion")
        if not isinstance(promotion, bool):
            blockers.append("is_promotion_invalid")
        elif promotion:
            promotion_rows += 1

        regular_price_raw = product.get("reported_regular_price")
        if regular_price_raw is not None:
            regular_price = _positive_decimal(regular_price_raw)
            if regular_price is None:
                blockers.append("reported_regular_price_invalid")
            elif current_price is not None and regular_price <= current_price:
                blockers.append("reported_regular_price_not_greater")
            if promotion is False:
                blockers.append("regular_price_without_promotion")
        elif promotion is True:
            # Una promoción puede provenir de teaser/discountHighlights sin precio
            # regular comparable; se conserva como advertencia, no se inventa uno.
            warnings.append("promotion_without_reported_regular_price")

    # SKU listados sin precio y agotados viajan fuera de ``products``: cuentan
    # para la completitud del catálogo, no como filas con precio.
    unpriced_entries: list[Mapping[str, Any]] = []
    try:
        unpriced_entries = validate_unpriced_unavailable(
            artifact, priced_rows=products, error=_UnpricedBlocker
        )
    except _UnpricedBlocker as exc:
        blockers.append(exc.code)
    for entry in unpriced_entries:
        product_ids.add(str(entry["product_id"]).strip())
    if unpriced_entries:
        warnings.append("unpriced_unavailable_present")

    sku_rows = len(products)
    skus_extracted = _non_negative_int(artifact.get("skus_extracted"))
    skus_with_price = _non_negative_int(artifact.get("skus_with_price"))
    skus_without_price = _non_negative_int(artifact.get("skus_without_price"))
    unique_products = _non_negative_int(artifact.get("unique_products_extracted"))
    products_reported = _non_negative_int(artifact.get("catalog_products_reported"))
    partitions_completed = _non_negative_int(artifact.get("partitions_completed"))
    partitions_detected = _non_negative_int(artifact.get("partitions_detected"))
    partition_observed_total = _non_negative_int(
        artifact.get("partition_observed_total_sum")
    )
    duplicates = _non_negative_int(artifact.get("duplicate_skus_across_partitions"))

    if skus_extracted != sku_rows:
        blockers.append("skus_extracted_mismatch")
    if skus_with_price != sku_rows:
        blockers.append("skus_with_price_mismatch")
    if skus_without_price != 0:
        blockers.append("skus_without_price_nonzero")
    if unique_products != len(product_ids):
        blockers.append("unique_products_extracted_mismatch")
    if products_reported != len(product_ids):
        blockers.append("catalog_products_reported_mismatch")
    if partition_observed_total != products_reported:
        blockers.append("partition_observed_total_mismatch")
    if (
        partitions_completed is None
        or partitions_detected is None
        or partitions_completed != partitions_detected
    ):
        blockers.append("partitions_incomplete")
    if duplicates != 0:
        blockers.append("duplicate_skus_across_partitions_nonzero")

    if missing_presentations:
        warnings.append("presentation_missing")
    if unknown_availability:
        warnings.append("availability_unknown")

    blocker_values = tuple(dict.fromkeys(blockers))
    warning_values = tuple(dict.fromkeys(warnings))
    technical_complete = len(blocker_values) == 0
    run_status = (
        RunStatus.REJECTED
        if blocker_values
        else RunStatus.WARNING
        if warning_values
        else RunStatus.SUCCESS
    )

    return OperationalCatalogAssessment(
        run_status=run_status,
        technical_catalog_complete=technical_complete,
        ready_for_normalization=technical_complete,
        catalog_accepted=False,
        production_authority=False,
        blockers=blocker_values,
        warnings=warning_values,
        sku_rows=sku_rows,
        unique_products=len(product_ids),
        missing_presentations=missing_presentations,
        unknown_availability=unknown_availability,
        in_stock=in_stock,
        out_of_stock=out_of_stock,
        promotion_rows=promotion_rows,
        unpriced_unavailable=len(unpriced_entries),
    )
