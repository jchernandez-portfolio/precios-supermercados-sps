"""Rehidrata el estado comercial desde las tablas durables comunes.

La reconstrucción es fail-closed: no acepta encabezados parciales, hashes que no
correspondan al estado canónico, IDs no deterministas ni cronologías rotas. El
objetivo es que un runner nuevo pueda continuar desde Google Sheets sin depender
de memoria del proceso anterior.

``raw_values`` no se reconstruye: el contrato durable conserva explícitamente
todos los campos que participan en identidad, estado y fingerprint comercial.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any

from .commercial_state import CurrentCommercialOffer, OfferHistoryPeriod
from .enums import ChangeType
from .identifiers import (
    generate_offer_id,
    generate_source_product_id,
    generate_state_hash,
)
from .models import NormalizedOffer, ValidatedOffer
from .tabular_persistence import FACT_OFFER_HISTORY, FACT_OFFERS_CURRENT, TableSpec


class TabularRehydrationError(ValueError):
    """El snapshot durable no puede convertirse en estado comercial confiable."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class RehydratedCommercialSnapshot:
    current: Mapping[str, CurrentCommercialOffer]
    history: Mapping[str, tuple[OfferHistoryPeriod, ...]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "current", MappingProxyType(dict(self.current)))
        object.__setattr__(
            self,
            "history",
            MappingProxyType(
                {offer_id: tuple(periods) for offer_id, periods in self.history.items()}
            ),
        )


def _require_row(row: Mapping[str, Any], spec: TableSpec) -> None:
    if not isinstance(row, Mapping):
        raise TabularRehydrationError("durable_row_invalid")
    if set(row) != set(spec.columns):
        raise TabularRehydrationError("durable_row_schema_mismatch")


def _required_text(row: Mapping[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise TabularRehydrationError(f"durable_{field}_invalid")
    return value


def _optional_text(row: Mapping[str, Any], field: str) -> str | None:
    value = row.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise TabularRehydrationError(f"durable_{field}_invalid")
    return value


def _required_bool(row: Mapping[str, Any], field: str) -> bool:
    value = row.get(field)
    if type(value) is not bool:
        raise TabularRehydrationError(f"durable_{field}_invalid")
    return value


def _optional_positive_int(row: Mapping[str, Any], field: str) -> int | None:
    value = row.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or type(value) is not int or value <= 0:
        raise TabularRehydrationError(f"durable_{field}_invalid")
    return value


def _optional_decimal(row: Mapping[str, Any], field: str) -> Decimal | None:
    value = row.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value or value != value.strip():
        raise TabularRehydrationError(f"durable_{field}_invalid")
    try:
        decimal = Decimal(value)
    except InvalidOperation as exc:
        raise TabularRehydrationError(f"durable_{field}_invalid") from exc
    if not decimal.is_finite():
        raise TabularRehydrationError(f"durable_{field}_invalid")
    return decimal


def _utc(row: Mapping[str, Any], field: str, *, optional: bool = False) -> datetime | None:
    value = row.get(field)
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.endswith("Z"):
        raise TabularRehydrationError(f"durable_{field}_invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise TabularRehydrationError(f"durable_{field}_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise TabularRehydrationError(f"durable_{field}_invalid")
    return parsed


def _json_string_tuple(row: Mapping[str, Any], field: str) -> tuple[str, ...]:
    value = row.get(field)
    if not isinstance(value, str):
        raise TabularRehydrationError(f"durable_{field}_invalid")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise TabularRehydrationError(f"durable_{field}_invalid") from exc
    if not isinstance(decoded, list):
        raise TabularRehydrationError(f"durable_{field}_invalid")
    result: list[str] = []
    for item in decoded:
        if not isinstance(item, str) or not item.strip() or item != item.strip():
            raise TabularRehydrationError(f"durable_{field}_invalid")
        if item in result:
            raise TabularRehydrationError(f"durable_{field}_duplicate")
        result.append(item)
    return tuple(result)


def _validated_offer_from_row(
    row: Mapping[str, Any],
    *,
    observed_at_field: str,
) -> ValidatedOffer:
    observed_at = _utc(row, observed_at_field)
    validated_at = _utc(row, "validated_at_utc")
    assert observed_at is not None and validated_at is not None
    if validated_at < observed_at:
        raise TabularRehydrationError("durable_validation_before_observation")

    try:
        offer = NormalizedOffer(
            supermarket_id=_required_text(row, "supermarket_id"),
            location_id=_required_text(row, "location_id"),
            source_product_id=_required_text(row, "source_product_id"),
            source_key_type=_required_text(row, "source_key_type"),
            source_key=_required_text(row, "source_key"),
            product_id=_required_text(row, "product_id"),
            offer_id=_required_text(row, "offer_id"),
            source_name=_required_text(row, "source_name"),
            product_url=_required_text(row, "product_url"),
            normalized_name=_required_text(row, "normalized_name"),
            currency=_required_text(row, "currency"),
            is_promotion=_required_bool(row, "is_promotion"),
            availability=_required_text(row, "availability"),
            location_status=_required_text(row, "location_status"),
            observed_at_utc=observed_at,
            scrape_run_id=_required_text(row, "offer_scrape_run_id"),
            extractor_version=_required_text(row, "extractor_version"),
            schema_version=_required_text(row, "schema_version"),
            source_url=_required_text(row, "source_url"),
            normalized_brand=_optional_text(row, "normalized_brand"),
            category=_optional_text(row, "category"),
            subcategory=_optional_text(row, "subcategory"),
            variant=_optional_text(row, "variant"),
            unit_count=_optional_positive_int(row, "unit_count"),
            content_per_unit=_optional_decimal(row, "content_per_unit"),
            measurement_unit=_optional_text(row, "measurement_unit"),
            total_content=_optional_decimal(row, "total_content"),
            current_price=_optional_decimal(row, "current_price"),
            source_sku=_optional_text(row, "source_sku"),
            source_brand=_optional_text(row, "source_brand"),
            source_presentation=_optional_text(row, "source_presentation"),
            source_category=_optional_text(row, "source_category"),
            image_url=_optional_text(row, "image_url"),
            barcode=_optional_text(row, "barcode"),
            reported_regular_price=_optional_decimal(row, "reported_regular_price"),
            unit_price=_optional_decimal(row, "unit_price"),
            unit_price_basis=_optional_text(row, "unit_price_basis"),
            location_evidence=_optional_text(row, "location_evidence"),
            location_confidence=_optional_decimal(row, "location_confidence"),
            raw_values={},
        )
        validated = ValidatedOffer(
            offer=offer,
            state_hash=_required_text(row, "state_hash"),
            validated_at_utc=validated_at,
            quality_events=_json_string_tuple(row, "quality_events_json"),
        )
    except TabularRehydrationError:
        raise
    except (TypeError, ValueError) as exc:
        raise TabularRehydrationError("durable_offer_invalid") from exc

    expected_source_product_id = generate_source_product_id(
        offer.supermarket_id,
        offer.source_key_type,
        offer.source_key,
    )
    if offer.source_product_id != expected_source_product_id:
        raise TabularRehydrationError("durable_source_product_id_mismatch")
    expected_offer_id = generate_offer_id(
        offer.supermarket_id,
        offer.location_id,
        expected_source_product_id,
    )
    if offer.offer_id != expected_offer_id:
        raise TabularRehydrationError("durable_offer_id_mismatch")
    if generate_state_hash(offer) != validated.state_hash:
        raise TabularRehydrationError("durable_state_hash_mismatch")
    if _required_text(row, "review_status") != validated.review_status.value:
        raise TabularRehydrationError("durable_review_status_mismatch")
    return validated


def current_from_row(row: Mapping[str, Any]) -> CurrentCommercialOffer:
    """Reconstruye una fila de ``fact_offers_current`` con invariantes fuertes."""

    _require_row(row, FACT_OFFERS_CURRENT)
    validated = _validated_offer_from_row(row, observed_at_field="observed_at_utc")
    first = _utc(row, "first_observed_at_utc")
    last = _utc(row, "last_observed_at_utc")
    assert first is not None and last is not None
    if first > last:
        raise TabularRehydrationError("durable_current_chronology_invalid")
    if validated.offer.observed_at_utc != last:
        raise TabularRehydrationError("durable_current_observation_not_latest")
    last_run = _required_text(row, "last_scrape_run_id")
    if validated.offer.scrape_run_id != last_run:
        raise TabularRehydrationError("durable_current_run_mismatch")
    return CurrentCommercialOffer(
        validated_offer=validated,
        first_observed_at_utc=first,
        last_observed_at_utc=last,
        last_scrape_run_id=last_run,
    )


def history_from_row(row: Mapping[str, Any]) -> OfferHistoryPeriod:
    """Reconstruye una fila de histórico y valida apertura/cierre del periodo."""

    _require_row(row, FACT_OFFER_HISTORY)
    validated = _validated_offer_from_row(row, observed_at_field="valid_from_utc")
    valid_from = _utc(row, "valid_from_utc")
    valid_to = _utc(row, "valid_to_utc", optional=True)
    last_observed = _utc(row, "last_observed_at_utc")
    assert valid_from is not None and last_observed is not None

    opened_by = _required_text(row, "opened_by_scrape_run_id")
    closed_by = _optional_text(row, "closed_by_scrape_run_id")
    if validated.offer.scrape_run_id != opened_by:
        raise TabularRehydrationError("durable_history_open_run_mismatch")
    if valid_to is None and closed_by is not None:
        raise TabularRehydrationError("durable_history_open_period_has_closer")
    if valid_to is not None and closed_by is None:
        raise TabularRehydrationError("durable_history_closed_period_missing_closer")
    if valid_to is not None and valid_to <= valid_from:
        raise TabularRehydrationError("durable_history_chronology_invalid")
    if last_observed < valid_from:
        raise TabularRehydrationError("durable_history_last_observation_invalid")
    if valid_to is not None and last_observed >= valid_to:
        raise TabularRehydrationError("durable_history_last_observation_invalid")

    try:
        change_type = ChangeType(_required_text(row, "change_type"))
    except ValueError as exc:
        raise TabularRehydrationError("durable_change_type_invalid") from exc
    changed_fields = _json_string_tuple(row, "changed_fields_json")
    if change_type is ChangeType.INITIAL and changed_fields:
        raise TabularRehydrationError("durable_initial_changed_fields_invalid")
    if change_type is not ChangeType.INITIAL and not changed_fields:
        raise TabularRehydrationError("durable_changed_fields_missing")

    return OfferHistoryPeriod(
        offer_history_id=_required_text(row, "offer_history_id"),
        offer_id=validated.offer.offer_id,
        state_hash=validated.state_hash,
        change_type=change_type,
        changed_fields=changed_fields,
        validated_offer=validated,
        valid_from_utc=valid_from,
        valid_to_utc=valid_to,
        opened_by_scrape_run_id=opened_by,
        closed_by_scrape_run_id=closed_by,
        last_confirmed_by_scrape_run_id=_required_text(
            row,
            "last_confirmed_by_scrape_run_id",
        ),
        last_observed_at_utc=last_observed,
    )


def rehydrate_commercial_snapshot(
    current_rows: Sequence[Mapping[str, Any]],
    history_rows: Sequence[Mapping[str, Any]],
) -> RehydratedCommercialSnapshot:
    """Reconstruye y cruza current/history para detectar drift durable."""

    if isinstance(current_rows, (str, bytes)) or not isinstance(current_rows, Sequence):
        raise TabularRehydrationError("durable_current_rows_invalid")
    if isinstance(history_rows, (str, bytes)) or not isinstance(history_rows, Sequence):
        raise TabularRehydrationError("durable_history_rows_invalid")

    current: dict[str, CurrentCommercialOffer] = {}
    for row in current_rows:
        item = current_from_row(row)
        offer_id = item.validated_offer.offer.offer_id
        if offer_id in current:
            raise TabularRehydrationError("durable_current_offer_duplicate")
        current[offer_id] = item

    history: dict[str, list[OfferHistoryPeriod]] = {}
    history_ids: set[str] = set()
    for row in history_rows:
        period = history_from_row(row)
        if period.offer_history_id in history_ids:
            raise TabularRehydrationError("durable_history_id_duplicate")
        history_ids.add(period.offer_history_id)
        history.setdefault(period.offer_id, []).append(period)

    if set(current) != set(history):
        raise TabularRehydrationError("durable_current_history_offer_set_mismatch")

    normalized_history: dict[str, tuple[OfferHistoryPeriod, ...]] = {}
    for offer_id, periods in history.items():
        periods.sort(key=lambda item: item.valid_from_utc)
        opens = [period for period in periods if period.valid_to_utc is None]
        if len(opens) != 1 or periods[-1] is not opens[0]:
            raise TabularRehydrationError("durable_history_open_period_invalid")
        for previous, following in zip(periods, periods[1:]):
            if previous.valid_to_utc != following.valid_from_utc:
                raise TabularRehydrationError("durable_history_gap_or_overlap")

        live = current[offer_id]
        open_period = periods[-1]
        if open_period.state_hash != live.validated_offer.state_hash:
            raise TabularRehydrationError("durable_current_history_state_mismatch")
        if open_period.validated_offer.offer.source_product_id != live.validated_offer.offer.source_product_id:
            raise TabularRehydrationError("durable_current_history_identity_mismatch")
        if open_period.last_observed_at_utc != live.last_observed_at_utc:
            raise TabularRehydrationError("durable_current_history_last_observed_mismatch")
        if open_period.last_confirmed_by_scrape_run_id != live.last_scrape_run_id:
            raise TabularRehydrationError("durable_current_history_last_run_mismatch")
        if open_period.valid_from_utc != live.first_observed_at_utc:
            raise TabularRehydrationError("durable_current_history_first_observed_mismatch")
        normalized_history[offer_id] = tuple(periods)

    return RehydratedCommercialSnapshot(
        current=current,
        history=normalized_history,
    )
