from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "precios-supermercados-sps" / "scripts" / "auditar_ramas_historicas.py"


def _load_script() -> ModuleType:
    name = "precios_sps_historical_branch_audit_test_module"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_inspect_only_writes_automatic_snapshot_without_loading_overrides(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_script()
    row = module.BranchAudit(
        branch="feature/precios-sps-example",
        category="UNIQUE_UNMERGED",
        tip_sha="a" * 40,
        unique_patch_count=1,
        open_prs=(),
        closed_unmerged_prs=(),
        merged_prs=(),
        unique_commit_subjects=("example",),
        changed_files=("precios-supermercados-sps/example.py",),
        reason="requires inspection",
    )
    json_output = tmp_path / "audit.json"
    markdown_output = tmp_path / "audit.md"

    monkeypatch.setattr(module, "_git", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "_ref_sha", lambda ref: "b" * 40)
    monkeypatch.setattr(module, "_branches", lambda remote, pattern: [row.branch])
    monkeypatch.setattr(module, "_classify", lambda *args: row)
    monkeypatch.setattr(
        module,
        "_load_overrides",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("overrides must not load")),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--inspect-only",
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
        ],
    )

    assert module.main() == 0
    assert '"category": "UNIQUE_UNMERGED"' in json_output.read_text(encoding="utf-8")
    markdown = markdown_output.read_text(encoding="utf-8")
    assert "Snapshot de `main`: `" + "b" * 40 + "`" in markdown
    assert "Persisten ramas UNIQUE_UNMERGED" in markdown


def test_zero_matching_branches_writes_valid_empty_audit(monkeypatch, tmp_path: Path) -> None:
    """Repositorio separado del monorepo: sin ramas `precios-sps` no es un error."""
    import pytest

    module = _load_script()
    monkeypatch.setattr(module, "_git", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "_ref_sha", lambda ref: "b" * 40)
    monkeypatch.setattr(module, "_branches", lambda remote, pattern: [])
    monkeypatch.setattr(
        module,
        "_classify",
        lambda *args: (_ for _ in ()).throw(AssertionError("nothing to classify")),
    )
    monkeypatch.setattr(
        module,
        "_load_overrides",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("overrides must not load")),
    )
    for mode in (["--inspect-only"], []):
        json_output = tmp_path / f"audit{len(mode)}.json"
        markdown_output = tmp_path / f"audit{len(mode)}.md"
        monkeypatch.setattr(
            sys,
            "argv",
            [str(SCRIPT), *mode, "--json-output", str(json_output), "--markdown-output", str(markdown_output)],
        )
        assert module.main() == 0
        assert json_output.read_text(encoding="utf-8") == "[]\n"
        markdown = markdown_output.read_text(encoding="utf-8")
        assert "Total: **0**" in markdown
        assert module.NO_HISTORICAL_BRANCHES in markdown
        assert "no historical branches in this repository" in markdown
        assert "`precios-sps`" in markdown
        assert "No quedan ramas UNIQUE_UNMERGED" in markdown

    # Con ramas que coinciden, el modo estricto sigue fallando cerrado.
    row = module.BranchAudit(
        branch="feature/precios-sps-example",
        category="UNIQUE_UNMERGED",
        tip_sha="a" * 40,
        unique_patch_count=1,
        open_prs=(),
        closed_unmerged_prs=(),
        merged_prs=(),
        unique_commit_subjects=("example",),
        changed_files=("x.py",),
        reason="requires inspection",
    )
    monkeypatch.setattr(module, "_branches", lambda remote, pattern: [row.branch])
    monkeypatch.setattr(module, "_classify", lambda *args: row)
    monkeypatch.setattr(module, "_load_overrides", lambda *args, **kwargs: {})
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--json-output", str(tmp_path / "s.json")])
    with pytest.raises(module.AuditError, match="UNIQUE_UNMERGED"):
        module.main()
