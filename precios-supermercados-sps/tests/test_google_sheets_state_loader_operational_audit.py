from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOADER = ROOT / "src" / "precios_supermercados" / "google_sheets_state_loader.py"


def test_google_sheets_state_loader_remains_strictly_read_only() -> None:
    source = LOADER.read_text(encoding="utf-8")

    assert "batch_update(" not in source
    assert ".apply(" not in source
    assert "prepare_new_run_persistence" not in source
    assert "catalog_accepted=True" not in source
    assert "catalog_accepted = True" not in source
    assert "load_snapshot" in source
    assert "rehydrate_commercial_snapshot" in source
    assert "restore_commercial_state" in source
