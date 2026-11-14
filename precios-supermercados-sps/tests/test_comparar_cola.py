"""La cola sólo se publica si cambia algo más que la fecha."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import comparar_cola  # noqa: E402

BASE = {"schema": "rpi-size-queue/v1", "generated_utc": "2026-11-13T21:00:00Z", "items": [{"gtin": "1"}],
        "ia": {"auto": 1, "consultas": 5, "actualizado_utc": "2026-11-13T21:10:00Z"}}


def write(path: Path, document: dict) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_only_new_dates_is_not_a_change(tmp_path: Path) -> None:
    before = write(tmp_path / "a.json", BASE)
    after = write(tmp_path / "b.json", {**BASE, "generated_utc": "2026-11-14T11:00:00Z",
                                        "ia": {**BASE["ia"], "actualizado_utc": "2026-11-14T11:30:00Z"}})
    assert comparar_cola.changed(before, after) is False
    assert comparar_cola.main(["--anterior", str(before), "--nueva", str(after)]) == 1


def test_items_or_ai_summary_changes_are_published(tmp_path: Path) -> None:
    before = write(tmp_path / "a.json", BASE)
    assert comparar_cola.changed(before, write(tmp_path / "b.json", {**BASE, "items": []})) is True
    assert comparar_cola.changed(before, write(tmp_path / "c.json", {**BASE, "ia": {**BASE["ia"], "auto": 2}})) is True


def test_missing_or_broken_previous_queue_counts_as_change(tmp_path: Path) -> None:
    after = write(tmp_path / "b.json", BASE)
    assert comparar_cola.changed(tmp_path / "no-existe.json", after) is True
    (tmp_path / "roto.json").write_text("{", encoding="utf-8")
    assert comparar_cola.main(["--anterior", str(tmp_path / "roto.json"), "--nueva", str(after)]) == 0
