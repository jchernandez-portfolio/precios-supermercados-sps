from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime, timezone
from pathlib import Path

import pytest

from precios_supermercados.edge_provenance import _iso_z

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLI = PROJECT_ROOT / "edge" / "cloudflare" / "test" / "canonical-time-cli.mjs"


def _node() -> str:
    executable = shutil.which("node")
    assert executable is not None, "Node.js es obligatorio para validar canonicalización edge"
    return executable


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 8, 21, 16, 0, 0, tzinfo=UTC),
        datetime(2026, 8, 21, 16, 0, 0, 123_000, tzinfo=UTC),
        datetime(2026, 8, 21, 16, 0, 59, 999_000, tzinfo=UTC),
    ],
)
def test_edge_timestamp_matches_python_and_javascript(value: datetime) -> None:
    result = subprocess.run(
        [_node(), str(CLI)],
        cwd=PROJECT_ROOT,
        input=json.dumps({"epochMs": int(value.timestamp() * 1000)}),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    parsed = json.loads(result.stdout)
    assert parsed["value"] == _iso_z(value)
