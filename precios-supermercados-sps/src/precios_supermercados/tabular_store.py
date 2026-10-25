"""Store tabular backend-neutral con semántica atómica e idempotente.

Sirve como contrato ejecutable para el futuro adapter de Google Sheets. No hace
red ni I/O externo. Las filas de configuración/current/history son upserts; los
runs y eventos de calidad son registros inmutables y un replay sólo se acepta si
la fila es lógicamente equivalente.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .tabular_persistence import (
    FACT_QUALITY_EVENTS,
    FACT_SCRAPE_RUNS,
    TABLE_SPECS,
    TableSpec,
)


class TabularStoreError(ValueError):
    """Base para errores del contrato de almacenamiento tabular."""


class TabularStoreConflict(TabularStoreError):
    """Una llave inmutable reapareció con contenido diferente."""


_IMMUTABLE_TABLES = frozenset(
    {
        FACT_SCRAPE_RUNS.name,
        FACT_QUALITY_EVENTS.name,
    }
)


def _primary_key(spec: TableSpec, row: Mapping[str, Any]) -> tuple[Any, ...]:
    key = tuple(row[column] for column in spec.primary_key)
    if any(value in (None, "") for value in key):
        raise TabularStoreError(f"primary key vacía en {spec.name}")
    return key


def _validate_cell(value: Any, table_name: str, column: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    raise TabularStoreError(
        f"celda no escalar/finita en {table_name}.{column}: {type(value).__name__}"
    )


def _normalize_row(spec: TableSpec, row: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise TabularStoreError(f"fila de {spec.name} debe ser mapping")
    unknown = set(row).difference(spec.columns)
    missing = set(spec.columns).difference(row)
    if unknown or missing:
        raise TabularStoreError(
            f"fila {spec.name} fuera de esquema; missing={sorted(missing)} "
            f"unknown={sorted(unknown)}"
        )
    normalized = {column: row[column] for column in spec.columns}
    for column, value in normalized.items():
        _validate_cell(value, spec.name, column)
    _primary_key(spec, normalized)
    return normalized


@dataclass(frozen=True, slots=True)
class TabularBatch:
    """Conjunto cerrado de upserts que debe aplicarse todo o nada."""

    rows: Mapping[str, tuple[Mapping[str, Any], ...]]

    def __post_init__(self) -> None:
        if not isinstance(self.rows, Mapping):
            raise TabularStoreError("rows debe ser mapping")
        normalized: dict[str, tuple[Mapping[str, Any], ...]] = {}
        for table_name, source_rows in self.rows.items():
            if table_name not in TABLE_SPECS:
                raise TabularStoreError(f"tabla desconocida: {table_name}")
            if not isinstance(source_rows, tuple):
                raise TabularStoreError(
                    f"rows[{table_name}] debe ser tuple para cerrar el batch"
                )
            spec = TABLE_SPECS[table_name]
            seen: dict[tuple[Any, ...], dict[str, Any]] = {}
            ordered: list[Mapping[str, Any]] = []
            for source_row in source_rows:
                row = _normalize_row(spec, source_row)
                key = _primary_key(spec, row)
                previous = seen.get(key)
                if previous is not None:
                    if previous != row:
                        raise TabularStoreConflict(
                            f"llave duplicada divergente dentro del batch: {table_name}{key}"
                        )
                    continue
                seen[key] = row
                ordered.append(MappingProxyType(row))
            normalized[table_name] = tuple(ordered)
        object.__setattr__(self, "rows", MappingProxyType(normalized))

    @property
    def row_count(self) -> int:
        return sum(len(rows) for rows in self.rows.values())


@dataclass(frozen=True, slots=True)
class TabularApplyResult:
    created: int
    updated: int
    replayed: int
    table_counts: Mapping[str, int]


class InMemoryTabularStore:
    """Referencia de semántica para adapters externos."""

    def __init__(self) -> None:
        self._tables: dict[str, dict[tuple[Any, ...], dict[str, Any]]] = {
            table_name: {} for table_name in TABLE_SPECS
        }

    def apply(self, batch: TabularBatch) -> TabularApplyResult:
        if not isinstance(batch, TabularBatch):
            raise TabularStoreError("batch debe ser TabularBatch")
        staged = {
            table_name: {key: dict(row) for key, row in rows.items()}
            for table_name, rows in self._tables.items()
        }
        created = updated = replayed = 0
        table_counts: dict[str, int] = {}

        for table_name, source_rows in batch.rows.items():
            spec = TABLE_SPECS[table_name]
            table = staged[table_name]
            applied_for_table = 0
            for source_row in source_rows:
                row = dict(source_row)
                key = _primary_key(spec, row)
                previous = table.get(key)
                if previous is None:
                    table[key] = row
                    created += 1
                elif previous == row:
                    replayed += 1
                elif table_name in _IMMUTABLE_TABLES:
                    raise TabularStoreConflict(
                        f"registro inmutable divergente: {table_name}{key}"
                    )
                else:
                    table[key] = row
                    updated += 1
                applied_for_table += 1
            table_counts[table_name] = applied_for_table

        self._tables = staged
        return TabularApplyResult(
            created=created,
            updated=updated,
            replayed=replayed,
            table_counts=MappingProxyType(dict(table_counts)),
        )

    def rows(self, table_name: str) -> tuple[Mapping[str, Any], ...]:
        if table_name not in TABLE_SPECS:
            raise TabularStoreError(f"tabla desconocida: {table_name}")
        table = self._tables[table_name]
        return tuple(
            MappingProxyType(dict(table[key]))
            for key in sorted(table, key=lambda value: tuple(str(item) for item in value))
        )

    def row(self, table_name: str, *primary_key: Any) -> Mapping[str, Any] | None:
        try:
            spec = TABLE_SPECS[table_name]
            table = self._tables[table_name]
        except KeyError as exc:
            raise TabularStoreError(f"tabla desconocida: {table_name}") from exc
        if len(primary_key) != len(spec.primary_key):
            raise TabularStoreError(
                f"{table_name} requiere {len(spec.primary_key)} componentes de llave"
            )
        value = table.get(tuple(primary_key))
        return MappingProxyType(dict(value)) if value is not None else None

    def count(self, table_name: str) -> int:
        if table_name not in TABLE_SPECS:
            raise TabularStoreError(f"tabla desconocida: {table_name}")
        return len(self._tables[table_name])


def build_batch(**tables: tuple[Mapping[str, Any], ...]) -> TabularBatch:
    """Helper explícito para construir una transacción con nombres de tabla."""

    return TabularBatch(rows=tables)
