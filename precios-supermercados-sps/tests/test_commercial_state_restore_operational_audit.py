from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_STATE_ASSIGNMENTS = (
    "._current =",
    "._history =",
    "._run_fingerprints =",
)


def test_private_commercial_state_mutation_is_confined_to_trusted_modules() -> None:
    allowed = {
        PROJECT_ROOT / "src/precios_supermercados/commercial_state.py",
        PROJECT_ROOT / "src/precios_supermercados/commercial_state_restore.py",
    }
    violations: list[str] = []

    for path in sorted((PROJECT_ROOT / "src").rglob("*.py")):
        if path in allowed:
            continue
        raw = path.read_text(encoding="utf-8")
        if any(marker in raw for marker in PRIVATE_STATE_ASSIGNMENTS):
            violations.append(str(path.relative_to(PROJECT_ROOT)))

    assert violations == []
