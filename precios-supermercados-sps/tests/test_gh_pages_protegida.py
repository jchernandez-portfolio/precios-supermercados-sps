"""La rama ``gh-pages`` y la configuración de Pages nunca se tocan salvo para publicar.

El 15/16-nov-2026 el sitio quedó en «Site not found» porque Pages perdió su fuente.
Nada del repo debe poder borrar o renombrar ``gh-pages`` ni cambiar Pages: el único
que escribe es «Publicar Súper Compras», y sólo con ``push --force HEAD:gh-pages``.
Las limpiezas de ramas (permiso de Claude Code, #86) se limitan a ``rpi/*``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
SETTINGS = ROOT / ".claude" / "settings.json"
PAGES_WORKFLOW = "precios-supermercados-sps-pages.yml"

# Borrar o renombrar ramas, o cambiar Pages por API/acciones.
FORBIDDEN = (
    re.compile(r"git\s+push\b[^\n]*(--delete|\s-d\s|\s:[\w/.-]+)"),
    re.compile(r"git\s+branch\s+(-D|-d|-m|-M|--delete|--move)\b"),
    re.compile(r"(-X|--method)\s+DELETE\b"),
    re.compile(r"git/refs/heads"),
    re.compile(r"repos/[^\s\"']+/pages\b"),
    re.compile(r"\b(deleteRef|updateRef|createPagesSite|updateInformationAboutPagesSite)\b"),
    re.compile(r"actions/(configure-pages|deploy-pages)@"),
)


def test_workflows_exist() -> None:
    assert len(WORKFLOWS) > 5
    assert (ROOT / ".github" / "workflows" / PAGES_WORKFLOW).exists()


def test_no_workflow_deletes_branches_or_changes_pages() -> None:
    for path in WORKFLOWS:
        text = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN:
            assert not pattern.search(text), (path.name, pattern.pattern)


def test_only_the_pages_workflow_writes_gh_pages_and_only_by_replacing_it() -> None:
    for path in WORKFLOWS:
        text = path.read_text(encoding="utf-8")
        pushes = [line for line in text.splitlines() if re.search(r"git\s+push\b", line) and "gh-pages" in line]
        if path.name == PAGES_WORKFLOW:
            assert len(pushes) == 1, pushes
            assert pushes[0].rstrip().endswith("HEAD:gh-pages"), pushes[0]
        else:
            assert not pushes, (path.name, pushes)


def test_claude_code_may_only_delete_rpi_branches() -> None:
    allow = json.loads(SETTINGS.read_text(encoding="utf-8"))["permissions"]["allow"]
    for rule in allow:
        assert "gh-pages" not in rule and "/pages" not in rule, rule
        if "DELETE" in rule:
            assert rule.endswith("/git/refs/heads/rpi/*)"), rule
