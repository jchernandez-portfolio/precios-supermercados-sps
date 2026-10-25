"""Plan offline del backend Google Sheets retirado.

El resultado representa un único payload para ``spreadsheets.batchUpdate``. Este
módulo se conserva para evidencia/tests de la arquitectura anterior y ya no
consulta el contrato físico activo. Sólo conoce las seis pestañas históricamente
administradas por Sheets mediante ``LEGACY_SHEETS_MANAGED_TABLE_SPECS``.

No autentica, no hace red y no concede autoridad para nuevas escrituras Sheets.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .storage_contract import LEGACY_SHEETS_MANAGED_TABLE_SPECS
from .tabular_persistence import TableSpec
from .tabular_store import InMemoryTabularStore, TabularStoreError


class GoogleSheetsPlanError(ValueError):
    """El snapshot o metadata no permite construir un update seguro."""


@dataclass(frozen=True, slots=True)
class SheetMetadata:
    sheet_id: int
    title: str
    row_count: int
    column_count: int

    def __post_init__(self) -> None:
        if isinstance(self.sheet_id, bool) or type(self.sheet_id) is not int or self.sheet_id < 0:
            raise GoogleSheetsPlanError("sheet_id debe ser entero no negativo")
        if not isinstance(self.title, str) or not self.title.strip():
            raise GoogleSheetsPlanError("title no puede estar vacío")
        if type(self.row_count) is not int or self.row_count < 1:
            raise GoogleSheetsPlanError("row_count debe ser >= 1")
        if type(self.column_count) is not int or self.column_count < 1:
            raise GoogleSheetsPlanError("column_count debe ser >= 1")


@dataclass(frozen=True, slots=True)
class SpreadsheetMetadata:
    sheets: Mapping[str, SheetMetadata]

    def __post_init__(self) -> None:
        if not isinstance(self.sheets, Mapping):
            raise GoogleSheetsPlanError("sheets debe ser mapping")
        normalized: dict[str, SheetMetadata] = {}
        used_ids: set[int] = set()
        for title, sheet in self.sheets.items():
            if not isinstance(sheet, SheetMetadata):
                raise GoogleSheetsPlanError("metadata de sheet inválida")
            if title != sheet.title:
                raise GoogleSheetsPlanError("la llave de sheet no coincide con title")
            if sheet.sheet_id in used_ids:
                raise GoogleSheetsPlanError("sheet_id duplicado")
            used_ids.add(sheet.sheet_id)
            normalized[title] = sheet
        object.__setattr__(self, "sheets", MappingProxyType(normalized))

    @property
    def used_sheet_ids(self) -> frozenset[int]:
        return frozenset(sheet.sheet_id for sheet in self.sheets.values())


@dataclass(frozen=True, slots=True)
class AtomicWorkbookPlan:
    """Payload cerrado; ``payload`` devuelve una copia JSON lista para transporte."""

    payload_json: str
    sheet_ids: Mapping[str, int]
    row_counts: Mapping[str, int]
    payload_bytes: int

    def __post_init__(self) -> None:
        try:
            parsed = json.loads(self.payload_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise GoogleSheetsPlanError("payload_json inválido") from exc
        if not isinstance(parsed, dict) or not isinstance(parsed.get("requests"), list):
            raise GoogleSheetsPlanError("payload_json no es batchUpdate")
        if self.payload_bytes != len(self.payload_json.encode("utf-8")):
            raise GoogleSheetsPlanError("payload_bytes no coincide con payload_json")
        object.__setattr__(self, "sheet_ids", MappingProxyType(dict(self.sheet_ids)))
        object.__setattr__(self, "row_counts", MappingProxyType(dict(self.row_counts)))

    @property
    def payload(self) -> dict[str, Any]:
        return json.loads(self.payload_json)


def parse_spreadsheet_metadata(payload: Mapping[str, Any]) -> SpreadsheetMetadata:
    """Parsea sólo ``sheets.properties`` de ``spreadsheets.get``."""

    if not isinstance(payload, Mapping):
        raise GoogleSheetsPlanError("respuesta spreadsheet inválida")
    raw_sheets = payload.get("sheets", [])
    if not isinstance(raw_sheets, list):
        raise GoogleSheetsPlanError("sheets debe ser lista")
    result: dict[str, SheetMetadata] = {}
    for raw in raw_sheets:
        if not isinstance(raw, Mapping):
            raise GoogleSheetsPlanError("sheet inválido")
        properties = raw.get("properties")
        if not isinstance(properties, Mapping):
            raise GoogleSheetsPlanError("faltan properties de sheet")
        grid = properties.get("gridProperties")
        if not isinstance(grid, Mapping):
            raise GoogleSheetsPlanError("faltan gridProperties")
        try:
            sheet = SheetMetadata(
                sheet_id=properties["sheetId"],
                title=properties["title"],
                row_count=grid["rowCount"],
                column_count=grid["columnCount"],
            )
        except KeyError as exc:
            raise GoogleSheetsPlanError(f"metadata incompleta: {exc.args[0]}") from exc
        if sheet.title in result:
            raise GoogleSheetsPlanError("title de sheet duplicado")
        result[sheet.title] = sheet
    return SpreadsheetMetadata(result)


def _allocate_sheet_id(used: set[int]) -> int:
    candidate = 1000
    while candidate in used:
        candidate += 1
    used.add(candidate)
    return candidate


def _cell(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, bool):
        return {"userEnteredValue": {"boolValue": value}}
    if isinstance(value, int) and not isinstance(value, bool):
        if abs(value) > 9_007_199_254_740_991:
            return {"userEnteredValue": {"stringValue": str(value)}}
        return {"userEnteredValue": {"numberValue": value}}
    if isinstance(value, float):
        return {"userEnteredValue": {"numberValue": value}}
    if isinstance(value, str):
        return {"userEnteredValue": {"stringValue": value}}
    raise GoogleSheetsPlanError(f"tipo de celda no soportado: {type(value).__name__}")


def _matrix_rows(spec: TableSpec, rows: tuple[Mapping[str, Any], ...]) -> list[dict[str, Any]]:
    matrix: list[dict[str, Any]] = [
        {"values": [_cell(column) for column in spec.columns]}
    ]
    for row in rows:
        if set(row) != set(spec.columns):
            raise GoogleSheetsPlanError(f"fila fuera de esquema en {spec.name}")
        matrix.append({"values": [_cell(row[column]) for column in spec.columns]})
    return matrix


def _header_format_request(sheet_id: int, column_count: int) -> dict[str, Any]:
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 0,
                "endRowIndex": 1,
                "startColumnIndex": 0,
                "endColumnIndex": column_count,
            },
            "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
            "fields": "userEnteredFormat.textFormat.bold",
        }
    }


def _update_cells_request(
    sheet_id: int,
    column_count: int,
    matrix: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "updateCells": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 0,
                "endRowIndex": len(matrix),
                "startColumnIndex": 0,
                "endColumnIndex": column_count,
            },
            "rows": matrix,
            "fields": "userEnteredValue",
        }
    }


def _filter_request(sheet_id: int, row_count: int, column_count: int) -> dict[str, Any]:
    return {
        "setBasicFilter": {
            "filter": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": row_count,
                    "startColumnIndex": 0,
                    "endColumnIndex": column_count,
                }
            }
        }
    }


def _existing_sheet_requests(
    sheet: SheetMetadata,
    *,
    required_rows: int,
    required_columns: int,
) -> list[dict[str, Any]]:
    return [
        {"clearBasicFilter": {"sheetId": sheet.sheet_id}},
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet.sheet_id,
                    "gridProperties": {
                        "rowCount": required_rows,
                        "columnCount": required_columns,
                        "frozenRowCount": 1,
                    },
                },
                "fields": (
                    "gridProperties.rowCount,gridProperties.columnCount,"
                    "gridProperties.frozenRowCount"
                ),
            }
        },
    ]


def _new_sheet_request(
    *,
    sheet_id: int,
    title: str,
    required_rows: int,
    required_columns: int,
) -> dict[str, Any]:
    return {
        "addSheet": {
            "properties": {
                "sheetId": sheet_id,
                "title": title,
                "gridProperties": {
                    "rowCount": required_rows,
                    "columnCount": required_columns,
                    "frozenRowCount": 1,
                },
            }
        }
    }


def build_atomic_workbook_plan(
    store: InMemoryTabularStore,
    metadata: SpreadsheetMetadata,
) -> AtomicWorkbookPlan:
    """Materializa las tablas legadas de Sheets en un único batch atómico.

    Esta función permanece para pruebas/evidencia del backend retirado. Tabs ajenos
    o antes diferidos se preservan y nunca se crean implícitamente.
    """

    if not isinstance(store, InMemoryTabularStore):
        raise GoogleSheetsPlanError("store debe ser InMemoryTabularStore")
    if not isinstance(metadata, SpreadsheetMetadata):
        raise GoogleSheetsPlanError("metadata debe ser SpreadsheetMetadata")

    requests: list[dict[str, Any]] = []
    used_ids = set(metadata.used_sheet_ids)
    sheet_ids: dict[str, int] = {}
    row_counts: dict[str, int] = {}

    for table_name, spec in LEGACY_SHEETS_MANAGED_TABLE_SPECS.items():
        try:
            rows = store.rows(table_name)
        except TabularStoreError as exc:
            raise GoogleSheetsPlanError(str(exc)) from exc
        matrix = _matrix_rows(spec, rows)
        required_rows = max(len(matrix), 2)
        required_columns = len(spec.columns)
        row_counts[table_name] = len(rows)

        existing = metadata.sheets.get(table_name)
        if existing is None:
            sheet_id = _allocate_sheet_id(used_ids)
            requests.append(
                _new_sheet_request(
                    sheet_id=sheet_id,
                    title=table_name,
                    required_rows=required_rows,
                    required_columns=required_columns,
                )
            )
        else:
            sheet_id = existing.sheet_id
            requests.extend(
                _existing_sheet_requests(
                    existing,
                    required_rows=required_rows,
                    required_columns=required_columns,
                )
            )
        sheet_ids[table_name] = sheet_id
        requests.append(_update_cells_request(sheet_id, required_columns, matrix))
        requests.append(_header_format_request(sheet_id, required_columns))
        if rows:
            requests.append(_filter_request(sheet_id, required_rows, required_columns))

    payload_dict = {
        "requests": requests,
        "includeSpreadsheetInResponse": False,
    }
    payload_json = json.dumps(
        payload_dict,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return AtomicWorkbookPlan(
        payload_json=payload_json,
        sheet_ids=sheet_ids,
        row_counts=row_counts,
        payload_bytes=len(payload_json.encode("utf-8")),
    )
