"""Bootstrap seguro del backend Google Sheets.

El bootstrap sólo puede verificar el workbook o materializar las tablas de
configuración. Nunca construye ofertas comerciales ni inicia extracción. Los
fact tables existentes se preservan porque el adapter reconstruye el snapshot
completo antes del único batchUpdate atómico.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .google_sheets_adapter import GoogleSheetsWorkbookAdapter
from .locations import DEFAULT_LOCATION_CATALOG, LocationCatalog
from .tabular_persistence import (
    CFG_LOCATIONS,
    CFG_SUPERMARKETS,
    location_config_rows,
    supermarket_config_rows,
)
from .tabular_store import TabularBatch

MODE_CHECK = "check"
MODE_APPLY_CONFIG = "apply-config"
ALLOWED_BOOTSTRAP_MODES = frozenset({MODE_CHECK, MODE_APPLY_CONFIG})


class GoogleSheetsBootstrapError(ValueError):
    """El modo o dependencia del bootstrap no cumple el contrato."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class GoogleSheetsBootstrapResult:
    mode: str
    wrote: bool
    created: int
    updated: int
    replayed: int
    nonempty_managed_tables: int
    row_counts: Mapping[str, int]
    payload_bytes: int | None = None

    def __post_init__(self) -> None:
        if self.mode not in ALLOWED_BOOTSTRAP_MODES:
            raise GoogleSheetsBootstrapError("bootstrap_mode_invalid")
        if type(self.wrote) is not bool:
            raise GoogleSheetsBootstrapError("bootstrap_wrote_invalid")
        for field_name in (
            "created",
            "updated",
            "replayed",
            "nonempty_managed_tables",
        ):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise GoogleSheetsBootstrapError(f"bootstrap_{field_name}_invalid")
        if self.payload_bytes is not None and (
            type(self.payload_bytes) is not int or self.payload_bytes <= 0
        ):
            raise GoogleSheetsBootstrapError("bootstrap_payload_bytes_invalid")
        object.__setattr__(self, "row_counts", MappingProxyType(dict(self.row_counts)))

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "wrote": self.wrote,
            "created": self.created,
            "updated": self.updated,
            "replayed": self.replayed,
            "nonempty_managed_tables": self.nonempty_managed_tables,
            "row_counts": dict(self.row_counts),
            "payload_bytes": self.payload_bytes,
        }


def build_configuration_batch(
    catalog: LocationCatalog = DEFAULT_LOCATION_CATALOG,
) -> TabularBatch:
    """Construye únicamente cfg_supermarkets + cfg_locations."""

    if not isinstance(catalog, LocationCatalog):
        raise GoogleSheetsBootstrapError("location_catalog_invalid")
    return TabularBatch(
        rows={
            CFG_SUPERMARKETS.name: supermarket_config_rows(catalog),
            CFG_LOCATIONS.name: location_config_rows(catalog),
        }
    )


def run_google_sheets_bootstrap(
    adapter: GoogleSheetsWorkbookAdapter,
    *,
    mode: str,
    catalog: LocationCatalog = DEFAULT_LOCATION_CATALOG,
) -> GoogleSheetsBootstrapResult:
    """Ejecuta verificación read-only o upsert de configuración."""

    if not isinstance(adapter, GoogleSheetsWorkbookAdapter):
        raise GoogleSheetsBootstrapError("google_sheets_adapter_invalid")
    if mode not in ALLOWED_BOOTSTRAP_MODES:
        raise GoogleSheetsBootstrapError("bootstrap_mode_invalid")

    if mode == MODE_CHECK:
        snapshot = adapter.load_snapshot()
        return GoogleSheetsBootstrapResult(
            mode=mode,
            wrote=False,
            created=0,
            updated=0,
            replayed=0,
            nonempty_managed_tables=sum(
                1 for count in snapshot.row_counts.values() if count > 0
            ),
            row_counts=snapshot.row_counts,
            payload_bytes=None,
        )

    batch = build_configuration_batch(catalog)
    result = adapter.apply(batch)
    return GoogleSheetsBootstrapResult(
        mode=mode,
        wrote=True,
        created=result.created,
        updated=result.updated,
        replayed=result.replayed,
        nonempty_managed_tables=sum(
            1 for count in result.initial_row_counts.values() if count > 0
        ),
        row_counts=result.final_row_counts,
        payload_bytes=result.payload_bytes,
    )
