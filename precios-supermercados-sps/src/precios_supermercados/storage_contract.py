"""Contrato físico activo de almacenamiento.

BigQuery es el único backend productivo objetivo. El contrato tabular histórico
(current + period history) continúa existiendo como motor/backend-neutral de
transición y rehidratación, pero ya no se presenta como contrato físico activo ni
como Google Sheets.

El planner/adapter de Google Sheets se conserva únicamente como evidencia legada.
Sus seis pestañas históricamente administradas quedan nombradas de forma explícita
para que ese código no vuelva a importar por accidente el contrato activo.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from .bigquery_contract import BIGQUERY_TABLE_SPECS, BigQueryTableSpec
from .tabular_persistence import (
    CFG_LOCATIONS,
    CFG_SUPERMARKETS,
    DIM_PRODUCTS,
    FACT_OFFER_HISTORY,
    FACT_OFFERS_CURRENT,
    FACT_QUALITY_EVENTS,
    FACT_SCRAPE_RUNS,
    MAP_SOURCE_PRODUCTS,
    TableSpec,
)
from .tabular_persistence import (
    TABLE_SPECS as LEGACY_TABLE_SPECS,
)

ACTIVE_STORAGE_BACKEND = "bigquery"
ACTIVE_STORAGE_TABLE_SPECS: Mapping[str, BigQueryTableSpec] = MappingProxyType(
    {spec.name: spec for spec in BIGQUERY_TABLE_SPECS}
)
ACTIVE_STORAGE_TABLE_NAMES = tuple(ACTIVE_STORAGE_TABLE_SPECS)

# Contrato del motor backend-neutral anterior. No es un segundo backend activo.
LEGACY_TABULAR_TABLE_SPECS: Mapping[str, TableSpec] = MappingProxyType(
    dict(LEGACY_TABLE_SPECS)
)
LEGACY_TABULAR_TABLE_NAMES = tuple(LEGACY_TABULAR_TABLE_SPECS)

# Superficie exacta que el backend Sheets retirado administraba. Mantener este
# nombre separado evita que código legado siga a ACTIVE_STORAGE_TABLE_SPECS y
# convierta accidentalmente las tablas BigQuery en pestañas de un Spreadsheet.
LEGACY_SHEETS_MANAGED_TABLE_SPECS: Mapping[str, TableSpec] = MappingProxyType(
    {
        spec.name: spec
        for spec in (
            CFG_SUPERMARKETS,
            CFG_LOCATIONS,
            FACT_OFFERS_CURRENT,
            FACT_OFFER_HISTORY,
            FACT_SCRAPE_RUNS,
            FACT_QUALITY_EVENTS,
        )
    }
)
LEGACY_SHEETS_MANAGED_TABLE_NAMES = tuple(LEGACY_SHEETS_MANAGED_TABLE_SPECS)
LEGACY_SHEETS_DEFERRED_TABLE_NAMES = (
    DIM_PRODUCTS.name,
    MAP_SOURCE_PRODUCTS.name,
)


def is_active_storage_table(table_name: str) -> bool:
    return table_name in ACTIVE_STORAGE_TABLE_SPECS


def is_legacy_tabular_table(table_name: str) -> bool:
    return table_name in LEGACY_TABULAR_TABLE_SPECS


def is_legacy_sheets_managed_table(table_name: str) -> bool:
    return table_name in LEGACY_SHEETS_MANAGED_TABLE_SPECS
