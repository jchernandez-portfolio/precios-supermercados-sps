"""Registros persistibles de ejecución y calidad para las tablas comunes.

Cada ejecución debe quedar registrada aunque no produzca cambios comerciales.
Los eventos de calidad son filas independientes y deterministas dentro de un run.
Este módulo sólo construye filas; no escribe en un backend externo.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .commercial_state import ApplyRunResult
from .enums import QualityEventCategory, QualityEventSeverity, RunStatus
from .locations import DEFAULT_LOCATION_CATALOG, LocationCatalog, LocationConfigError
from .tabular_persistence import (
    FACT_QUALITY_EVENTS,
    FACT_SCRAPE_RUNS,
    TabularPersistenceError,
)


def _required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TabularPersistenceError(f"{field_name} no puede estar vacío")
    return value.strip()


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise TabularPersistenceError(f"{field_name} debe incluir zona horaria UTC")
    if value.utcoffset().total_seconds() != 0:
        raise TabularPersistenceError(f"{field_name} debe expresarse en UTC")
    return value


def _utc_text(value: datetime) -> str:
    return _utc(value, "datetime").isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _non_negative_int(value: int, field_name: str) -> int:
    if isinstance(value, bool) or type(value) is not int or value < 0:
        raise TabularPersistenceError(f"{field_name} debe ser entero no negativo")
    return value


def _closed_row(columns: tuple[str, ...], values: dict[str, Any]) -> dict[str, Any]:
    if set(values) != set(columns):
        missing = sorted(set(columns).difference(values))
        unknown = sorted(set(values).difference(columns))
        raise TabularPersistenceError(
            f"fila fuera de esquema; missing={missing} unknown={unknown}"
        )
    return {column: values[column] for column in columns}


@dataclass(frozen=True, slots=True)
class ScrapeRunRecord:
    """Resumen final de una ejecución, independiente de que haya cambios."""

    scrape_run_id: str
    supermarket_id: str
    location_id: str
    run_status: RunStatus | str
    catalog_accepted: bool
    started_at_utc: datetime
    finished_at_utc: datetime
    products_observed: int
    offers_observed: int
    current_created: int = 0
    current_changed: int = 0
    current_confirmed: int = 0
    offers_ignored: int = 0
    quality_event_count: int = 0
    run_evidence_id: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("scrape_run_id", "supermarket_id", "location_id"):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name),
            )
        try:
            status = (
                self.run_status
                if isinstance(self.run_status, RunStatus)
                else RunStatus(self.run_status)
            )
        except (TypeError, ValueError) as exc:
            raise TabularPersistenceError("run_status no es válido") from exc
        object.__setattr__(self, "run_status", status)
        if status is RunStatus.RUNNING:
            raise TabularPersistenceError("ScrapeRunRecord requiere un estado final")
        if not isinstance(self.catalog_accepted, bool):
            raise TabularPersistenceError("catalog_accepted debe ser booleano")
        if self.catalog_accepted and status not in {RunStatus.SUCCESS, RunStatus.WARNING}:
            raise TabularPersistenceError(
                "catalog_accepted sólo es válido para success/warning"
            )
        _utc(self.started_at_utc, "started_at_utc")
        _utc(self.finished_at_utc, "finished_at_utc")
        if self.finished_at_utc < self.started_at_utc:
            raise TabularPersistenceError("finished_at_utc precede started_at_utc")
        for field_name in (
            "products_observed",
            "offers_observed",
            "current_created",
            "current_changed",
            "current_confirmed",
            "offers_ignored",
            "quality_event_count",
        ):
            _non_negative_int(getattr(self, field_name), field_name)
        object.__setattr__(self, "run_evidence_id", _optional_text(self.run_evidence_id))

    @classmethod
    def from_apply_result(
        cls,
        *,
        supermarket_id: str,
        location_id: str,
        run_status: RunStatus | str,
        catalog_accepted: bool,
        started_at_utc: datetime,
        finished_at_utc: datetime,
        products_observed: int,
        offers_observed: int,
        quality_event_count: int,
        apply_result: ApplyRunResult,
        run_evidence_id: str | None = None,
    ) -> ScrapeRunRecord:
        if not isinstance(apply_result, ApplyRunResult):
            raise TabularPersistenceError("apply_result debe ser ApplyRunResult")
        record = cls(
            scrape_run_id=apply_result.scrape_run_id,
            supermarket_id=supermarket_id,
            location_id=location_id,
            run_status=run_status,
            catalog_accepted=catalog_accepted,
            started_at_utc=started_at_utc,
            finished_at_utc=finished_at_utc,
            products_observed=products_observed,
            offers_observed=offers_observed,
            current_created=apply_result.current_created,
            current_changed=apply_result.current_changed,
            current_confirmed=apply_result.current_confirmed,
            offers_ignored=apply_result.offers_ignored,
            quality_event_count=quality_event_count,
            run_evidence_id=run_evidence_id,
        )
        expected_commercial_update = (
            record.catalog_accepted
            and record.run_status in {RunStatus.SUCCESS, RunStatus.WARNING}
        )
        if apply_result.commercial_update_allowed != expected_commercial_update:
            raise TabularPersistenceError(
                "apply_result contradice la decisión comercial del run"
            )
        if not expected_commercial_update and any(
            (
                apply_result.current_created,
                apply_result.current_changed,
                apply_result.current_confirmed,
            )
        ):
            raise TabularPersistenceError(
                "run no comercial no puede reportar mutaciones/confirmaciones current"
            )
        return record


@dataclass(frozen=True, slots=True)
class QualityEventRecord:
    """Evento auditable dentro de una ejecución."""

    quality_event_id: str
    scrape_run_id: str
    supermarket_id: str
    location_id: str
    category: QualityEventCategory | str
    severity: QualityEventSeverity | str
    event_code: str
    observed_at_utc: datetime
    offer_id: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "quality_event_id",
            "scrape_run_id",
            "supermarket_id",
            "location_id",
            "event_code",
        ):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name),
            )
        object.__setattr__(self, "offer_id", _optional_text(self.offer_id))
        try:
            category = (
                self.category
                if isinstance(self.category, QualityEventCategory)
                else QualityEventCategory(self.category)
            )
            severity = (
                self.severity
                if isinstance(self.severity, QualityEventSeverity)
                else QualityEventSeverity(self.severity)
            )
        except (TypeError, ValueError) as exc:
            raise TabularPersistenceError("category/severity no son válidos") from exc
        object.__setattr__(self, "category", category)
        object.__setattr__(self, "severity", severity)
        _utc(self.observed_at_utc, "observed_at_utc")

    @classmethod
    def deterministic(
        cls,
        *,
        scrape_run_id: str,
        supermarket_id: str,
        location_id: str,
        category: QualityEventCategory | str,
        severity: QualityEventSeverity | str,
        event_code: str,
        observed_at_utc: datetime,
        sequence: int,
        offer_id: str | None = None,
    ) -> QualityEventRecord:
        sequence = _non_negative_int(sequence, "sequence")
        try:
            normalized_category = (
                category
                if isinstance(category, QualityEventCategory)
                else QualityEventCategory(category)
            )
            normalized_severity = (
                severity
                if isinstance(severity, QualityEventSeverity)
                else QualityEventSeverity(severity)
            )
        except (TypeError, ValueError) as exc:
            raise TabularPersistenceError("category/severity no son válidos") from exc
        normalized_run_id = _required_text(scrape_run_id, "scrape_run_id")
        normalized_supermarket = _required_text(supermarket_id, "supermarket_id")
        normalized_location = _required_text(location_id, "location_id")
        normalized_code = _required_text(event_code, "event_code")
        normalized_offer_id = _optional_text(offer_id)
        canonical = {
            "scrape_run_id": normalized_run_id,
            "supermarket_id": normalized_supermarket,
            "location_id": normalized_location,
            "category": normalized_category.value,
            "severity": normalized_severity.value,
            "event_code": normalized_code,
            "observed_at_utc": _utc_text(observed_at_utc),
            "offer_id": normalized_offer_id,
            "sequence": sequence,
        }
        digest = hashlib.sha256(
            json.dumps(
                canonical,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return cls(
            quality_event_id=f"qe_{digest[:32]}",
            scrape_run_id=normalized_run_id,
            supermarket_id=normalized_supermarket,
            location_id=normalized_location,
            category=normalized_category,
            severity=normalized_severity,
            event_code=normalized_code,
            observed_at_utc=observed_at_utc,
            offer_id=normalized_offer_id,
        )


def scrape_run_row(
    record: ScrapeRunRecord,
    catalog: LocationCatalog = DEFAULT_LOCATION_CATALOG,
) -> dict[str, Any]:
    """Convierte un run final en una fila; todo run final puede registrarse.

    A diferencia de una oferta comercial, un run fallido/rechazado debe poder
    persistirse para observabilidad. Por eso sólo exige que supermercado y
    ubicación existan y sean coherentes; no exige ``extraction_enabled``.
    """

    if not isinstance(record, ScrapeRunRecord):
        raise TabularPersistenceError("record debe ser ScrapeRunRecord")
    try:
        location = catalog.location(record.location_id)
        catalog.supermarket(record.supermarket_id)
    except LocationConfigError as exc:
        raise TabularPersistenceError(str(exc)) from exc
    if location.supermarket_id != record.supermarket_id:
        raise TabularPersistenceError("location_id pertenece a otro supermarket_id")
    return _closed_row(
        FACT_SCRAPE_RUNS.columns,
        {
            "scrape_run_id": record.scrape_run_id,
            "supermarket_id": record.supermarket_id,
            "location_id": record.location_id,
            "run_status": record.run_status.value,
            "catalog_accepted": record.catalog_accepted,
            "started_at_utc": _utc_text(record.started_at_utc),
            "finished_at_utc": _utc_text(record.finished_at_utc),
            "products_observed": record.products_observed,
            "offers_observed": record.offers_observed,
            "current_created": record.current_created,
            "current_changed": record.current_changed,
            "current_confirmed": record.current_confirmed,
            "offers_ignored": record.offers_ignored,
            "quality_event_count": record.quality_event_count,
            "run_evidence_id": record.run_evidence_id,
        },
    )


def quality_event_row(
    record: QualityEventRecord,
    catalog: LocationCatalog = DEFAULT_LOCATION_CATALOG,
) -> dict[str, Any]:
    """Convierte un evento en fila común, incluso para runs rechazados/fallidos."""

    if not isinstance(record, QualityEventRecord):
        raise TabularPersistenceError("record debe ser QualityEventRecord")
    try:
        location = catalog.location(record.location_id)
        catalog.supermarket(record.supermarket_id)
    except LocationConfigError as exc:
        raise TabularPersistenceError(str(exc)) from exc
    if location.supermarket_id != record.supermarket_id:
        raise TabularPersistenceError("location_id pertenece a otro supermarket_id")
    return _closed_row(
        FACT_QUALITY_EVENTS.columns,
        {
            "quality_event_id": record.quality_event_id,
            "scrape_run_id": record.scrape_run_id,
            "supermarket_id": record.supermarket_id,
            "location_id": record.location_id,
            "offer_id": record.offer_id,
            "category": record.category.value,
            "severity": record.severity.value,
            "event_code": record.event_code,
            "observed_at_utc": _utc_text(record.observed_at_utc),
        },
    )
