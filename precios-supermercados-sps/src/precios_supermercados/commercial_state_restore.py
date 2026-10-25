"""Restauración fail-closed del motor comercial desde persistencia durable.

`tabular_rehydration` reconstruye y valida current/history, pero un runner nuevo
necesita volver a cargar ese estado en `InMemoryCommercialState` antes de
procesar una observación posterior. Esta frontera añade además todos los
`scrape_run_id` terminales persistidos como IDs reservados: un run histórico no
puede reaplicarse por accidente después de reiniciar el proceso.

Los replays exactos deben reconciliarse **antes** de esta frontera mediante el
registro durable correspondiente. Para runs autoritativos, el binding preparado
en `commercial_run_evidence` permite esa reconciliación sin convertir el hash en
autoridad productiva.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .commercial_pricing import CommercialPricingError, evaluate_real_price_reduction
from .commercial_state import (
    CurrentCommercialOffer,
    InMemoryCommercialState,
    OfferHistoryPeriod,
    _snapshot_current,
    _snapshot_history_period,
)
from .enums import RunStatus
from .tabular_persistence import FACT_SCRAPE_RUNS, TabularPersistenceError
from .tabular_records import ScrapeRunRecord
from .tabular_rehydration import RehydratedCommercialSnapshot

_RESTORED_TERMINAL_RUN_SENTINEL = "durable-terminal-run-requires-reconciliation"
_BOUND_EVIDENCE_RE = re.compile(r"^crev1_[0-9a-f]{64}$")
_AUTHORITATIVE_STATUSES = frozenset({RunStatus.SUCCESS, RunStatus.WARNING})


class CommercialStateRestoreError(ValueError):
    """El snapshot durable no puede convertirse en estado operativo confiable."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class RestoredCommercialState:
    """Estado listo para un runner nuevo y métricas de la restauración."""

    state: InMemoryCommercialState
    current_count: int
    history_period_count: int
    terminal_run_count: int


def _required_text(row: Mapping[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise CommercialStateRestoreError(f"durable_run_{field}_invalid")
    return value


def _optional_text(row: Mapping[str, Any], field: str) -> str | None:
    value = row.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise CommercialStateRestoreError(f"durable_run_{field}_invalid")
    return value


def _required_bool(row: Mapping[str, Any], field: str) -> bool:
    value = row.get(field)
    if type(value) is not bool:
        raise CommercialStateRestoreError(f"durable_run_{field}_invalid")
    return value


def _non_negative_int(row: Mapping[str, Any], field: str) -> int:
    value = row.get(field)
    if isinstance(value, bool) or type(value) is not int or value < 0:
        raise CommercialStateRestoreError(f"durable_run_{field}_invalid")
    return value


def _utc(row: Mapping[str, Any], field: str) -> datetime:
    value = row.get(field)
    if not isinstance(value, str) or not value.endswith("Z"):
        raise CommercialStateRestoreError(f"durable_run_{field}_invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise CommercialStateRestoreError(f"durable_run_{field}_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise CommercialStateRestoreError(f"durable_run_{field}_invalid")
    return parsed


def scrape_run_from_durable_row(row: Mapping[str, Any]) -> ScrapeRunRecord:
    """Reconstruye una fila terminal de `fact_scrape_runs` con esquema exacto."""

    if not isinstance(row, Mapping):
        raise CommercialStateRestoreError("durable_run_row_invalid")
    if set(row) != set(FACT_SCRAPE_RUNS.columns):
        raise CommercialStateRestoreError("durable_run_row_schema_mismatch")
    try:
        return ScrapeRunRecord(
            scrape_run_id=_required_text(row, "scrape_run_id"),
            supermarket_id=_required_text(row, "supermarket_id"),
            location_id=_required_text(row, "location_id"),
            run_status=_required_text(row, "run_status"),
            catalog_accepted=_required_bool(row, "catalog_accepted"),
            started_at_utc=_utc(row, "started_at_utc"),
            finished_at_utc=_utc(row, "finished_at_utc"),
            products_observed=_non_negative_int(row, "products_observed"),
            offers_observed=_non_negative_int(row, "offers_observed"),
            current_created=_non_negative_int(row, "current_created"),
            current_changed=_non_negative_int(row, "current_changed"),
            current_confirmed=_non_negative_int(row, "current_confirmed"),
            offers_ignored=_non_negative_int(row, "offers_ignored"),
            quality_event_count=_non_negative_int(row, "quality_event_count"),
            run_evidence_id=_optional_text(row, "run_evidence_id"),
        )
    except CommercialStateRestoreError:
        raise
    except TabularPersistenceError as exc:
        raise CommercialStateRestoreError("durable_run_record_invalid") from exc


def _authoritative_run_for_offer(
    records: Mapping[str, ScrapeRunRecord],
    *,
    scrape_run_id: str,
    supermarket_id: str,
    location_id: str,
) -> ScrapeRunRecord:
    record = records.get(scrape_run_id)
    if record is None:
        raise CommercialStateRestoreError("referenced_terminal_run_missing")
    if not record.catalog_accepted or record.run_status not in _AUTHORITATIVE_STATUSES:
        raise CommercialStateRestoreError("referenced_run_not_authoritative")
    if record.supermarket_id != supermarket_id:
        raise CommercialStateRestoreError("referenced_run_supermarket_mismatch")
    if record.location_id != location_id:
        raise CommercialStateRestoreError("referenced_run_location_mismatch")
    if record.run_evidence_id is None or _BOUND_EVIDENCE_RE.fullmatch(
        record.run_evidence_id
    ) is None:
        raise CommercialStateRestoreError("referenced_run_bound_evidence_missing")
    return record


def _validate_snapshot_and_run_references(
    snapshot: RehydratedCommercialSnapshot,
    records: Mapping[str, ScrapeRunRecord],
) -> None:
    if set(snapshot.current) != set(snapshot.history):
        raise CommercialStateRestoreError("snapshot_offer_set_mismatch")

    for offer_id, current in snapshot.current.items():
        if not isinstance(current, CurrentCommercialOffer):
            raise CommercialStateRestoreError("snapshot_current_type_invalid")
        if current.validated_offer.offer.offer_id != offer_id:
            raise CommercialStateRestoreError("snapshot_current_key_mismatch")
        periods = snapshot.history.get(offer_id)
        if not isinstance(periods, tuple) or not periods:
            raise CommercialStateRestoreError("snapshot_history_missing")
        if any(not isinstance(period, OfferHistoryPeriod) for period in periods):
            raise CommercialStateRestoreError("snapshot_history_type_invalid")

        try:
            # Reutiliza el reconciliador comercial más estricto disponible para
            # detectar identidad/hash/cronología/gaps antes de tocar el state.
            evaluate_real_price_reduction(current, periods)
        except CommercialPricingError as exc:
            raise CommercialStateRestoreError("snapshot_commercial_chain_invalid") from exc

        offer = current.validated_offer.offer
        _authoritative_run_for_offer(
            records,
            scrape_run_id=current.last_scrape_run_id,
            supermarket_id=offer.supermarket_id,
            location_id=offer.location_id,
        )
        for period in periods:
            period_offer = period.validated_offer.offer
            run_ids = {
                period.opened_by_scrape_run_id,
                period.last_confirmed_by_scrape_run_id,
            }
            if period.closed_by_scrape_run_id is not None:
                run_ids.add(period.closed_by_scrape_run_id)
            for run_id in run_ids:
                _authoritative_run_for_offer(
                    records,
                    scrape_run_id=run_id,
                    supermarket_id=period_offer.supermarket_id,
                    location_id=period_offer.location_id,
                )


def restore_commercial_state(
    snapshot: RehydratedCommercialSnapshot,
    scrape_run_rows: Sequence[Mapping[str, Any]],
) -> RestoredCommercialState:
    """Restaura current/history y reserva todos los terminal run IDs persistidos.

    Ningún `scrape_run_id` existente se marca con su fingerprint original porque
    `fact_scrape_runs` no contiene ese material. En su lugar se instala un
    sentinel imposible de coincidir con el SHA-256 normal del motor. Por tanto,
    intentar aplicar de nuevo un run histórico falla como replay conflictivo y
    obliga al caller a reconciliar primero la fila durable exacta.
    """

    if not isinstance(snapshot, RehydratedCommercialSnapshot):
        raise CommercialStateRestoreError("rehydrated_snapshot_required")
    if isinstance(scrape_run_rows, (str, bytes)) or not isinstance(
        scrape_run_rows,
        Sequence,
    ):
        raise CommercialStateRestoreError("durable_run_rows_invalid")

    records: dict[str, ScrapeRunRecord] = {}
    for row in scrape_run_rows:
        record = scrape_run_from_durable_row(row)
        if record.scrape_run_id in records:
            raise CommercialStateRestoreError("durable_run_id_duplicate")
        records[record.scrape_run_id] = record

    _validate_snapshot_and_run_references(snapshot, records)

    state = InMemoryCommercialState()
    # Esta asignación deliberadamente vive sólo en esta frontera: todos los
    # objetos han pasado rehidratación durable + reconciliación comercial arriba.
    state._current = {
        offer_id: _snapshot_current(item)
        for offer_id, item in snapshot.current.items()
    }
    state._history = {
        offer_id: [_snapshot_history_period(period) for period in periods]
        for offer_id, periods in snapshot.history.items()
    }
    state._run_fingerprints = {
        scrape_run_id: _RESTORED_TERMINAL_RUN_SENTINEL
        for scrape_run_id in records
    }

    return RestoredCommercialState(
        state=state,
        current_count=state.current_count,
        history_period_count=sum(len(periods) for periods in state._history.values()),
        terminal_run_count=len(records),
    )
