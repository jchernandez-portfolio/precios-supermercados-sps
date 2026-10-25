from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MONOREPO_ROOT = PROJECT_ROOT.parent
PROTECTED_SYMBOLS = (
    "derive_bound_run_evidence_id",
    "reconcile_bound_durable_run_row",
)


def test_durable_replay_binding_is_not_an_operational_authority_entrypoint() -> None:
    """El binding sólo puede permanecer en su módulo hasta existir verifier real.

    Un SHA-256 de evidencia + payload demuestra igualdad/replay; no demuestra que
    ``authority_evidence_id`` haya sido emitido por una autoridad productiva. Por
    eso scripts, workflows y otros módulos de runtime no pueden consumir todavía
    estas funciones. La futura integración autoritativa deberá modificar este
    test de forma explícita junto con su verifier y revisión de seguridad.
    """

    allowed = {
        PROJECT_ROOT / "src/precios_supermercados/commercial_run_evidence.py",
    }
    candidates = [
        *sorted((PROJECT_ROOT / "src").rglob("*.py")),
        *sorted((PROJECT_ROOT / "scripts").rglob("*.py")),
        *sorted((MONOREPO_ROOT / ".github/workflows").glob("*.yml")),
        *sorted((MONOREPO_ROOT / ".github/workflows").glob("*.yaml")),
    ]

    violations: list[str] = []
    for path in candidates:
        if path in allowed:
            continue
        raw = path.read_text(encoding="utf-8")
        if any(symbol in raw for symbol in PROTECTED_SYMBOLS):
            violations.append(str(path.relative_to(MONOREPO_ROOT)))

    assert violations == []
