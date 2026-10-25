"""Adapter preservado del backend Google Sheets retirado.

La hoja se trata como un snapshot materializado, no como una colección de
``append``. Este código permanece para compatibilidad/evidencia y está ligado de
forma explícita a ``LEGACY_SHEETS_MANAGED_TABLE_SPECS``; nunca consulta el
contrato físico activo BigQuery.

El workflow que podía ejecutar este adapter está neutralizado. Este módulo no
concede por sí solo autorización para nuevas escrituras Sheets.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol

from .google_sheets_plan import (
    AtomicWorkbookPlan,
    SpreadsheetMetadata,
    build_atomic_workbook_plan,
    parse_spreadsheet_metadata,
)
from .storage_contract import LEGACY_SHEETS_MANAGED_TABLE_SPECS
from .tabular_persistence import TABLE_SPECS, TableSpec
from .tabular_store import (
    InMemoryTabularStore,
    TabularApplyResult,
    TabularBatch,
    TabularStoreError,
)


class GoogleSheetsAdapterError(RuntimeError):
    """Error cerrado del adapter; ``code`` no contiene valores de celdas."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class GoogleSheetsTransportLike(Protocol):
    @property
    def spreadsheet_id(self) -> str: ...

    def get_spreadsheet_metadata(self) -> dict[str, Any]: ...

    def batch_get_values(self, ranges: Sequence[str]) -> dict[str, Any]: ...

    def batch_update(self, payload: Mapping[str, Any]) -> dict[str, Any]: ...


_BOOLEAN_COLUMNS = frozenset(
    {
        "is_active",
        "is_available",
        "in_scope",
        "extraction_enabled",
        "technical_binding_confirmed",
        "is_promotion",
        "catalog_accepted",
    }
)

_INTEGER_COLUMNS = frozenset(
    {
        "unit_count",
        "products_observed",
        "offers_observed",
        "current_created",
        "current_changed",
        "current_confirmed",
        "offers_ignored",
        "quality_event_count",
    }
)


@dataclass(frozen=True, slots=True)
class WorkbookSnapshot:
    """Estado validado leído del Spreadsheet legado antes de una escritura."""

    metadata: SpreadsheetMetadata
    store: InMemoryTabularStore
    row_counts: Mapping[str, int]
    requested_ranges: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "row_counts", MappingProxyType(dict(self.row_counts)))


@dataclass(frozen=True, slots=True)
class GoogleSheetsApplyResult:
    """Resumen sanitizado de una materialización del backend retirado."""

    created: int
    updated: int
    replayed: int
    initial_row_counts: Mapping[str, int]
    final_row_counts: Mapping[str, int]
    payload_bytes: int
    managed_sheet_count: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "initial_row_counts",
            MappingProxyType(dict(self.initial_row_counts)),
        )
        object.__setattr__(
            self,
            "final_row_counts",
            MappingProxyType(dict(self.final_row_counts)),
        )


def _column_letter(index_one_based: int) -> str:
    if isinstance(index_one_based, bool) or type(index_one_based) is not int:
        raise GoogleSheetsAdapterError("column_index_invalid")
    if index_one_based < 1 or index_one_based > 18_278:  # ZZZ
        raise GoogleSheetsAdapterError("column_index_invalid")
    result = ""
    value = index_one_based
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _a1_range(spec: TableSpec) -> str:
    title = spec.name.replace("'", "''")
    end_column = _column_letter(len(spec.columns))
    return f"'{title}'!A:{end_column}"


def managed_existing_ranges(
    metadata: SpreadsheetMetadata,
) -> tuple[tuple[str, str], ...]:
    """Devuelve rangos sólo de pestañas del backend Sheets legado."""

    if not isinstance(metadata, SpreadsheetMetadata):
        raise GoogleSheetsAdapterError("spreadsheet_metadata_invalid")
    return tuple(
        (table_name, _a1_range(spec))
        for table_name, spec in LEGACY_SHEETS_MANAGED_TABLE_SPECS.items()
        if table_name in metadata.sheets
    )


def _returned_range_title(value: str) -> str:
    if not isinstance(value, str) or "!" not in value:
        raise GoogleSheetsAdapterError("value_range_name_invalid")
    title = value.split("!", 1)[0]
    if len(title) >= 2 and title[0] == "'" and title[-1] == "'":
        title = title[1:-1].replace("''", "'")
    if not title:
        raise GoogleSheetsAdapterError("value_range_name_invalid")
    return title


def _canonical_sheet_cell(table_name: str, column: str, value: Any) -> Any:
    """Normaliza sólo tipos que Google puede devolver sin perder identidad."""

    if value is None:
        return None

    if column in _BOOLEAN_COLUMNS:
        if type(value) is not bool:
            raise GoogleSheetsAdapterError("managed_sheet_boolean_type_invalid")
        return value

    if column in _INTEGER_COLUMNS:
        if type(value) is int:
            return value
        if isinstance(value, float) and math.isfinite(value) and value.is_integer():
            integer = int(value)
            if abs(integer) <= 9_007_199_254_740_991:
                return integer
        raise GoogleSheetsAdapterError("managed_sheet_integer_type_invalid")

    if not isinstance(value, str):
        raise GoogleSheetsAdapterError("managed_sheet_text_type_invalid")
    if "\x00" in value:
        raise GoogleSheetsAdapterError("managed_sheet_text_invalid")
    return value


def _parse_table_values(
    table_name: str,
    values: Any,
) -> tuple[Mapping[str, Any], ...]:
    try:
        spec = LEGACY_SHEETS_MANAGED_TABLE_SPECS[table_name]
    except KeyError as exc:
        raise GoogleSheetsAdapterError("managed_table_unknown") from exc

    if values is None:
        values = []
    if not isinstance(values, list):
        raise GoogleSheetsAdapterError("managed_sheet_values_invalid")
    if not values:
        return ()

    header = values[0]
    if not isinstance(header, list) or header != list(spec.columns):
        raise GoogleSheetsAdapterError("managed_sheet_header_mismatch")

    parsed: list[Mapping[str, Any]] = []
    seen_keys: set[tuple[Any, ...]] = set()
    for raw_row in values[1:]:
        if not isinstance(raw_row, list):
            raise GoogleSheetsAdapterError("managed_sheet_row_invalid")
        if len(raw_row) > len(spec.columns):
            raise GoogleSheetsAdapterError("managed_sheet_row_too_wide")
        if not raw_row or all(value in (None, "") for value in raw_row):
            raise GoogleSheetsAdapterError("managed_sheet_blank_row")

        padded = raw_row + [None] * (len(spec.columns) - len(raw_row))
        row = {
            column: _canonical_sheet_cell(table_name, column, value)
            for column, value in zip(spec.columns, padded, strict=True)
        }
        key = tuple(row[column] for column in spec.primary_key)
        if any(value in (None, "") for value in key):
            raise GoogleSheetsAdapterError("managed_sheet_primary_key_empty")
        if key in seen_keys:
            raise GoogleSheetsAdapterError("managed_sheet_primary_key_duplicate")
        seen_keys.add(key)
        parsed.append(row)
    return tuple(parsed)


def parse_managed_values_payload(
    table_names: Sequence[str],
    payload: Mapping[str, Any],
    *,
    expected_spreadsheet_id: str | None = None,
) -> Mapping[str, tuple[Mapping[str, Any], ...]]:
    """Valida la respuesta de ``values:batchGet`` de tabs legados."""

    if isinstance(table_names, (str, bytes)) or not isinstance(table_names, Sequence):
        raise GoogleSheetsAdapterError("managed_table_names_invalid")
    if not isinstance(payload, Mapping):
        raise GoogleSheetsAdapterError("batch_get_payload_invalid")

    spreadsheet_id = payload.get("spreadsheetId")
    if (
        expected_spreadsheet_id is not None
        and spreadsheet_id is not None
        and spreadsheet_id != expected_spreadsheet_id
    ):
        raise GoogleSheetsAdapterError("batch_get_spreadsheet_id_mismatch")

    raw_ranges = payload.get("valueRanges", [])
    if not isinstance(raw_ranges, list) or len(raw_ranges) != len(table_names):
        raise GoogleSheetsAdapterError("batch_get_value_ranges_mismatch")

    result: dict[str, tuple[Mapping[str, Any], ...]] = {}
    for table_name, raw_range in zip(table_names, raw_ranges, strict=True):
        if table_name not in LEGACY_SHEETS_MANAGED_TABLE_SPECS:
            raise GoogleSheetsAdapterError("managed_table_unknown")
        if table_name in result:
            raise GoogleSheetsAdapterError("managed_table_duplicate")
        if not isinstance(raw_range, Mapping):
            raise GoogleSheetsAdapterError("value_range_invalid")
        returned_range = raw_range.get("range")
        if _returned_range_title(returned_range) != table_name:
            raise GoogleSheetsAdapterError("value_range_table_mismatch")
        result[table_name] = _parse_table_values(
            table_name,
            raw_range.get("values", []),
        )
    return MappingProxyType(result)


def hydrate_store_from_managed_rows(
    rows_by_table: Mapping[str, tuple[Mapping[str, Any], ...]],
) -> InMemoryTabularStore:
    """Reconstruye el store desde las tablas del backend Sheets legado."""

    if not isinstance(rows_by_table, Mapping):
        raise GoogleSheetsAdapterError("managed_rows_invalid")
    unexpected = set(rows_by_table).difference(LEGACY_SHEETS_MANAGED_TABLE_SPECS)
    if unexpected:
        raise GoogleSheetsAdapterError("managed_table_unknown")

    complete_rows = {
        table_name: tuple(rows_by_table.get(table_name, ()))
        for table_name in TABLE_SPECS
    }
    try:
        batch = TabularBatch(rows=complete_rows)
        store = InMemoryTabularStore()
        store.apply(batch)
    except TabularStoreError as exc:
        raise GoogleSheetsAdapterError("managed_sheet_store_invalid") from exc
    return store


def snapshot_row_counts(store: InMemoryTabularStore) -> Mapping[str, int]:
    if not isinstance(store, InMemoryTabularStore):
        raise GoogleSheetsAdapterError("tabular_store_invalid")
    return MappingProxyType(
        {
            table_name: store.count(table_name)
            for table_name in LEGACY_SHEETS_MANAGED_TABLE_SPECS
        }
    )


class GoogleSheetsWorkbookAdapter:
    """Lee/valida el snapshot del backend Sheets preservado."""

    def __init__(self, transport: GoogleSheetsTransportLike) -> None:
        required = (
            "get_spreadsheet_metadata",
            "batch_get_values",
            "batch_update",
        )
        if any(not callable(getattr(transport, name, None)) for name in required):
            raise GoogleSheetsAdapterError("google_sheets_transport_invalid")
        spreadsheet_id = getattr(transport, "spreadsheet_id", None)
        if not isinstance(spreadsheet_id, str) or not spreadsheet_id:
            raise GoogleSheetsAdapterError("google_sheets_transport_invalid")
        self._transport = transport

    def load_snapshot(self) -> WorkbookSnapshot:
        try:
            metadata_payload = self._transport.get_spreadsheet_metadata()
            metadata = parse_spreadsheet_metadata(metadata_payload)
        except GoogleSheetsAdapterError:
            raise
        except Exception as exc:
            raise GoogleSheetsAdapterError("spreadsheet_metadata_read_failed") from exc

        existing = managed_existing_ranges(metadata)
        table_names = tuple(table_name for table_name, _ in existing)
        ranges = tuple(a1_range for _, a1_range in existing)

        if ranges:
            try:
                values_payload = self._transport.batch_get_values(ranges)
                rows_by_table = parse_managed_values_payload(
                    table_names,
                    values_payload,
                    expected_spreadsheet_id=self._transport.spreadsheet_id,
                )
            except GoogleSheetsAdapterError:
                raise
            except Exception as exc:
                raise GoogleSheetsAdapterError("managed_values_read_failed") from exc
        else:
            rows_by_table = MappingProxyType({})

        store = hydrate_store_from_managed_rows(rows_by_table)
        return WorkbookSnapshot(
            metadata=metadata,
            store=store,
            row_counts=snapshot_row_counts(store),
            requested_ranges=ranges,
        )

    def apply(self, batch: TabularBatch) -> GoogleSheetsApplyResult:
        if not isinstance(batch, TabularBatch):
            raise GoogleSheetsAdapterError("tabular_batch_invalid")
        if set(batch.rows).difference(LEGACY_SHEETS_MANAGED_TABLE_SPECS):
            raise GoogleSheetsAdapterError("tabular_batch_contains_deferred_table")

        snapshot = self.load_snapshot()
        try:
            local_result: TabularApplyResult = snapshot.store.apply(batch)
            plan: AtomicWorkbookPlan = build_atomic_workbook_plan(
                snapshot.store,
                snapshot.metadata,
            )
        except TabularStoreError as exc:
            raise GoogleSheetsAdapterError("tabular_batch_conflict") from exc
        except Exception as exc:
            raise GoogleSheetsAdapterError("workbook_plan_failed") from exc

        try:
            self._transport.batch_update(plan.payload)
        except Exception as exc:
            raise GoogleSheetsAdapterError("workbook_batch_update_failed") from exc

        final_counts = snapshot_row_counts(snapshot.store)
        return GoogleSheetsApplyResult(
            created=local_result.created,
            updated=local_result.updated,
            replayed=local_result.replayed,
            initial_row_counts=snapshot.row_counts,
            final_row_counts=final_counts,
            payload_bytes=plan.payload_bytes,
            managed_sheet_count=len(LEGACY_SHEETS_MANAGED_TABLE_SPECS),
        )
