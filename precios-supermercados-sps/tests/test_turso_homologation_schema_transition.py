from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import actualizar_mvp_turso_la_colonia as updater  # noqa: E402
from actualizar_mvp_sqlite_la_colonia import SnapshotError  # noqa: E402


def test_preflight_accepts_base_schema_and_optional_homologation_table_only() -> None:
    updater._validate_table_names(updater.EXPECTED_TABLES)
    updater._validate_table_names(
        updater.EXPECTED_TABLES | {"product_homologation_profiles"}
    )


def test_preflight_still_rejects_missing_or_unknown_tables() -> None:
    with pytest.raises(SnapshotError, match="turso_schema_mismatch"):
        updater._validate_table_names(updater.EXPECTED_TABLES - {"price_history"})
    with pytest.raises(SnapshotError, match="turso_schema_mismatch"):
        updater._validate_table_names(updater.EXPECTED_TABLES | {"unexpected_table"})


def test_all_turso_persistors_share_optional_derived_table_guard() -> None:
    for filename in (
        "actualizar_mvp_turso_comisariato_los_andes.py",
        "actualizar_mvp_turso_paiz.py",
    ):
        source = (ROOT / "scripts" / filename).read_text(encoding="utf-8")
        assert "_validate_table_names(str(row[0]) for row in _execute_rows(results[0]))" in source
        assert "!= EXPECTED_TABLES" not in source


def test_every_derived_table_the_project_creates_is_tolerated_by_the_daily_guard() -> None:
    """La tabla del panel de tamaños rompió el refresco del 12-nov por faltar aquí."""
    from precios_supermercados.product_homologation_persistence import TABLE_NAME as PROFILES
    from precios_supermercados.product_master import MASTER_TABLES
    from precios_supermercados.size_decisions import TABLE_NAME as SIZE_DECISIONS

    for table in (PROFILES, SIZE_DECISIONS, *MASTER_TABLES):
        assert table in updater.OPTIONAL_DERIVED_TABLES, table
    updater._validate_table_names(updater.EXPECTED_TABLES | {SIZE_DECISIONS})
