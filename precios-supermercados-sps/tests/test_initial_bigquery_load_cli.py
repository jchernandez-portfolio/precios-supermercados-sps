from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts/cargar_snapshot_inicial_bigquery.py"


def test_cli_does_not_write_without_explicit_apply(tmp_path: Path) -> None:
    snapshot = tmp_path / "full-catalog.json"
    snapshot.write_text("{}", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--snapshot-json",
            str(snapshot),
            "--project-id",
            "demo-project",
            "--dataset-id",
            "precios_sps",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "la escritura requiere --apply" in result.stderr
