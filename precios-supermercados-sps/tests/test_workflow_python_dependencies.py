"""Cada workflow que ejecuta un script que importa PyYAML debe instalarlo.

Incidente 2026-10-03: el refresco de homologación ejecuta
``backfill_homologacion_turso.py``, que desde el producto maestro importa
``yaml``; el workflow no instalaba dependencias y falló con
``ModuleNotFoundError: No module named 'yaml'`` en la primera corrida real.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT.parent / ".github" / "workflows"
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def _module_path(name: str) -> Path | None:
    if name.startswith("precios_supermercados"):
        base = SRC.joinpath(*name.split("."))
        for candidate in (base.with_suffix(".py"), base / "__init__.py"):
            if candidate.exists():
                return candidate
    candidate = SCRIPTS / f"{name}.py"
    return candidate if candidate.exists() else None


def _needs_yaml(script: Path, seen: set[Path] | None = None) -> bool:
    seen = seen if seen is not None else set()
    if script in seen:
        return False
    seen.add(script)
    imports = _imports(script)
    if "yaml" in imports:
        return True
    return any((path := _module_path(name)) is not None and _needs_yaml(path, seen) for name in imports)


def test_workflows_install_pyyaml_when_their_scripts_need_it():
    missing = []
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        raw = workflow.read_text(encoding="utf-8")
        installs = "pip install -r" in raw or re.search(r"pip install[^\n]*PyYAML==", raw)
        for script_name in sorted(set(re.findall(r"python3? scripts/([A-Za-z0-9_]+)\.py", raw))):
            script = SCRIPTS / f"{script_name}.py"
            if script.exists() and _needs_yaml(script) and not installs:
                missing.append(f"{workflow.name}: {script_name}.py")
    assert missing == []


def test_refresh_workflow_pins_the_same_pyyaml_as_requirements():
    pinned = re.search(r"^PyYAML==(\S+)$", (ROOT / "requirements.txt").read_text(encoding="utf-8"), re.M).group(1)
    raw = (WORKFLOWS / "precios-supermercados-sps-homologation-refresh.yml").read_text(encoding="utf-8")
    assert f"python -m pip install --disable-pip-version-check PyYAML=={pinned}" in raw
