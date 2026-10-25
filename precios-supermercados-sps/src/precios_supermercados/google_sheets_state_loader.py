"""Carga read-only de estado comercial durable desde un workbook gestionado.

Esta frontera une tres capas ya validadas sin introducir una ruta de escritura:

1. ``GoogleSheetsWorkbookAdapter.load_snapshot`` lee y valida el workbook;
2. ``rehydrate_commercial_snapshot`` reconstruye current/history;
3. ``restore_commercial_state`` reinstala el motor y reserva los run IDs terminales.

El loader no concede autoridad productiva, no acepta decisiones de catálogo y no
materializa ningún batch. Su único efecto externo posible es la lectura realizada
por el adapter recibido.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

from .commercial_state import InMemoryCommercialState
from .commercial_state_restore import (
    CommercialStateRestoreError,
    restore_commercial_state,
)
from .google_sheets_adapter import (
    GoogleSheetsAdapterError,
    WorkbookSnapshot,
    snapshot_row_counts,
)
from .tabular_persistence import (
    FACT_OFFER_HISTORY,
    FACT_OFFERS_CURRENT,
    FACT_SCRAPE_RUNS,
)
from .tabular_rehydration import (
    TabularRehydrationError,
    rehydrate_commercial_snapshot,
)
from .tabular_store import InMemoryTabularStore, TabularStoreError


class WorkbookSnapshotLoaderLike(Protocol):
    """Capacidad mínima necesaria; el adapter productivo satisface este contrato."""

    def load_snapshot(self) -> WorkbookSnapshot: ...


class GoogleSheetsStateLoaderError(RuntimeError):
    """Fallo sanitizado de la frontera read-only."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class LoadedCommercialState:
    """Estado restaurado y métricas no sensibles de la lectura."""

    state: InMemoryCommercialState
    row_counts: Mapping[str, int]
    requested_ranges: tuple[str, ...]
    current_count: int
    history_period_count: int
    terminal_run_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.state, InMemoryCommercialState):
            raise GoogleSheetsStateLoaderError("restored_state_invalid")
        object.__setattr__(self, "row_counts", MappingProxyType(dict(self.row_counts)))
        object.__setattr__(self, "requested_ranges", tuple(self.requested_ranges))


def load_commercial_state_from_google_sheets(
    adapter: WorkbookSnapshotLoaderLike,
) -> LoadedCommercialState:
    """Lee un snapshot gestionado y devuelve un motor comercial restaurado.

    No se llama ``apply`` ni ``batch_update``. Los replays históricos siguen
    requiriendo reconciliación explícita; restaurar el workbook no crea una nueva
    decisión autoritativa ni habilita ``catalog_accepted``.
    """

    load_snapshot = getattr(adapter, "load_snapshot", None)
    if not callable(load_snapshot):
        raise GoogleSheetsStateLoaderError("workbook_snapshot_loader_invalid")

    try:
        workbook = load_snapshot()
    except GoogleSheetsAdapterError as exc:
        raise GoogleSheetsStateLoaderError("workbook_snapshot_read_failed") from exc
    except Exception as exc:
        raise GoogleSheetsStateLoaderError("workbook_snapshot_read_failed") from exc

    if not isinstance(workbook, WorkbookSnapshot):
        raise GoogleSheetsStateLoaderError("workbook_snapshot_invalid")
    if not isinstance(workbook.store, InMemoryTabularStore):
        raise GoogleSheetsStateLoaderError("workbook_store_invalid")
    if not isinstance(workbook.requested_ranges, tuple) or any(
        not isinstance(value, str) or not value
        for value in workbook.requested_ranges
    ):
        raise GoogleSheetsStateLoaderError("workbook_requested_ranges_invalid")

    try:
        actual_row_counts = snapshot_row_counts(workbook.store)
        if dict(workbook.row_counts) != dict(actual_row_counts):
            raise GoogleSheetsStateLoaderError("workbook_row_counts_mismatch")
        current_rows = workbook.store.rows(FACT_OFFERS_CURRENT.name)
        history_rows = workbook.store.rows(FACT_OFFER_HISTORY.name)
        scrape_run_rows = workbook.store.rows(FACT_SCRAPE_RUNS.name)
    except GoogleSheetsStateLoaderError:
        raise
    except TabularStoreError as exc:
        raise GoogleSheetsStateLoaderError("workbook_store_read_failed") from exc

    try:
        commercial_snapshot = rehydrate_commercial_snapshot(
            current_rows,
            history_rows,
        )
    except TabularRehydrationError as exc:
        raise GoogleSheetsStateLoaderError("commercial_snapshot_rehydration_failed") from exc

    try:
        restored = restore_commercial_state(commercial_snapshot, scrape_run_rows)
    except CommercialStateRestoreError as exc:
        raise GoogleSheetsStateLoaderError("commercial_state_restore_failed") from exc

    return LoadedCommercialState(
        state=restored.state,
        row_counts=actual_row_counts,
        requested_ranges=workbook.requested_ranges,
        current_count=restored.current_count,
        history_period_count=restored.history_period_count,
        terminal_run_count=restored.terminal_run_count,
    )
