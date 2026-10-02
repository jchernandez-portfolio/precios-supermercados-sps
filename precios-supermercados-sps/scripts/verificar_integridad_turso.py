#!/usr/bin/env python3
"""Verificación semanal de integridad completa de la base Turso (sólo lectura).

Concentra los chequeos que recorren toda la base y que antes corrían a diario
(actualización MVP, migración Paiz y refresco de homologación):

- ``PRAGMA integrity_check``;
- ``pragma_foreign_key_check`` global;
- periodos abiertos duplicados por ``(product_id, location_id)``;
- conteos por tabla (evidencia, sin umbral).

No escribe nada. Falla cerrado ante cualquier violación.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from actualizar_mvp_sqlite_la_colonia import SnapshotError  # noqa: E402
from actualizar_mvp_turso_la_colonia import (  # noqa: E402
    _execute_rows,
    _pipeline,
    _stmt,
    _validate_table_names,
)

COUNTED_TABLES = ("supermarkets", "locations", "products", "scrape_runs", "price_history")
OPTIONAL_COUNTED_TABLES = (
    "product_homologation_profiles",
    "master_products",
    "master_product_links",
    "master_link_rejections",
    "catalog_unpriced_observations",
    "pricesmart_product_specs",
)
DUPLICATE_OPEN_PERIODS_SQL = (
    "SELECT COUNT(*) FROM (SELECT product_id,location_id FROM price_history "
    "WHERE valid_to_utc IS NULL GROUP BY product_id,location_id HAVING COUNT(*)>1)"
)


def integrity_requests(tables: tuple[str, ...]) -> list[dict[str, Any]]:
    return [
        {"type": "execute", "stmt": _stmt("PRAGMA integrity_check")},
        {"type": "execute", "stmt": _stmt("SELECT COUNT(*) FROM pragma_foreign_key_check")},
        {"type": "execute", "stmt": _stmt(DUPLICATE_OPEN_PERIODS_SQL)},
        *[
            {"type": "execute", "stmt": _stmt(f"SELECT COUNT(*) FROM {table}")}
            for table in tables
        ],
        {"type": "close"},
    ]


def verify_turso(url: str, token: str) -> dict[str, object]:
    if not url.strip() or not token.strip():
        raise SnapshotError("turso_credentials_missing")
    data = _pipeline(
        url,
        token,
        [
            {
                "type": "execute",
                "stmt": _stmt(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ),
            },
            {"type": "close"},
        ],
    )
    results = data.get("results")
    if not isinstance(results, list) or not results:
        raise SnapshotError("turso_integrity_schema_invalid")
    names = [str(row[0]) for row in _execute_rows(results[0])]
    _validate_table_names(names)
    tables = COUNTED_TABLES + tuple(name for name in OPTIONAL_COUNTED_TABLES if name in names)

    data = _pipeline(url, token, integrity_requests(tables))
    results = data.get("results")
    if not isinstance(results, list) or len(results) < 3 + len(tables):
        raise SnapshotError("turso_integrity_response_invalid")
    integrity = _execute_rows(results[0])
    foreign_keys = _execute_rows(results[1])
    duplicates = _execute_rows(results[2])
    counts: dict[str, int] = {}
    for offset, table in enumerate(tables, start=3):
        rows = _execute_rows(results[offset])
        if len(rows) != 1 or len(rows[0]) != 1 or type(rows[0][0]) is not int:
            raise SnapshotError(f"turso_integrity_count_invalid:{table}")
        counts[table] = int(rows[0][0])
    if integrity != [["ok"]] or foreign_keys != [[0]] or duplicates != [[0]]:
        raise SnapshotError(
            f"turso_integrity_failed:{integrity[:5]}:{foreign_keys}:{duplicates}"
        )
    return {
        "integrity_check": "ok",
        "foreign_key_violations": 0,
        "duplicate_open_periods": 0,
        "table_counts": counts,
    }


def main() -> None:
    try:
        result = verify_turso(
            os.environ.get("TURSO_DATABASE_URL", ""),
            os.environ.get("TURSO_AUTH_TOKEN", ""),
        )
    except SnapshotError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
