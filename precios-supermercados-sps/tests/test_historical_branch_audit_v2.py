from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "precios-supermercados-sps" / "scripts"


def _load(name: str, filename: str) -> ModuleType:
    path = SCRIPTS_DIR / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _modules() -> tuple[ModuleType, ModuleType, ModuleType]:
    legacy = _load("auditar_ramas_historicas", "auditar_ramas_historicas.py")
    audit_v2 = _load("auditar_ramas_historicas_v2", "auditar_ramas_historicas_v2.py")
    delta_v2 = _load(
        "resumir_delta_auditoria_ramas_v2",
        "resumir_delta_auditoria_ramas_v2.py",
    )
    return legacy, audit_v2, delta_v2


def _row(
    legacy: ModuleType,
    *,
    branch: str,
    category: str,
    tip: str,
    patches: int,
) -> object:
    return legacy.BranchAudit(
        branch=branch,
        category=category,
        tip_sha=tip,
        unique_patch_count=patches,
        open_prs=(900,) if category == "OPEN_CURRENT" else (),
        closed_unmerged_prs=(),
        merged_prs=(),
        unique_commit_subjects=("subject",) if patches else (),
        changed_files=("precios-supermercados-sps/example.py",) if patches else (),
        reason="automatic",
    )


def test_v2_applies_exact_decision_and_reports_decision_already_subsumed() -> None:
    legacy, audit_v2, _delta_v2 = _modules()
    decision = audit_v2.HistoricalDecision(
        branch="feature/precios-sps-old",
        tip_sha="a" * 40,
        unique_patch_count=2,
        reason="superseded by canonical main",
    )

    rows, no_longer_needed = audit_v2.apply_decisions(
        [
            _row(
                legacy,
                branch=decision.branch,
                category="UNIQUE_UNMERGED",
                tip=decision.tip_sha,
                patches=2,
            )
        ],
        {decision.branch: decision},
    )
    assert rows[0].category == "CLOSED_SUPERSEDED"
    assert rows[0].reason == "reviewed decision v2: superseded by canonical main"
    assert no_longer_needed == ()

    rows, no_longer_needed = audit_v2.apply_decisions(
        [
            _row(
                legacy,
                branch=decision.branch,
                category="MERGED_OR_SUBSUMED",
                tip=decision.tip_sha,
                patches=0,
            )
        ],
        {decision.branch: decision},
    )
    assert rows[0].category == "MERGED_OR_SUBSUMED"
    assert no_longer_needed == (decision.branch,)


def test_v2_fails_closed_on_tip_patch_or_open_pr_drift() -> None:
    legacy, audit_v2, _delta_v2 = _modules()
    decision = audit_v2.HistoricalDecision(
        branch="feature/precios-sps-old",
        tip_sha="a" * 40,
        unique_patch_count=2,
        reason="reviewed",
    )

    with pytest.raises(legacy.AuditError, match="tip drift"):
        audit_v2.apply_decisions(
            [
                _row(
                    legacy,
                    branch=decision.branch,
                    category="UNIQUE_UNMERGED",
                    tip="b" * 40,
                    patches=2,
                )
            ],
            {decision.branch: decision},
        )

    with pytest.raises(legacy.AuditError, match="patch-count drift"):
        audit_v2.apply_decisions(
            [
                _row(
                    legacy,
                    branch=decision.branch,
                    category="UNIQUE_UNMERGED",
                    tip=decision.tip_sha,
                    patches=3,
                )
            ],
            {decision.branch: decision},
        )

    with pytest.raises(legacy.AuditError, match="revived by an open PR"):
        audit_v2.apply_decisions(
            [
                _row(
                    legacy,
                    branch=decision.branch,
                    category="OPEN_CURRENT",
                    tip=decision.tip_sha,
                    patches=2,
                )
            ],
            {decision.branch: decision},
        )


def test_v2_accepts_ancestor_baseline_and_resolves_versioned_legacy_reason(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    legacy, audit_v2, _delta_v2 = _modules()
    reviewed_main = "a" * 40
    legacy_main = "b" * 40
    branch = "feature/precios-sps-old"
    decisions_path = tmp_path / "decisions.json"
    legacy_path = tmp_path / "legacy.json"

    legacy_path.write_text(
        json.dumps(
            {
                "schema": audit_v2.LEGACY_SCHEMA,
                "as_of_main": legacy_main,
                "overrides": {
                    branch: {
                        "category": "CLOSED_SUPERSEDED",
                        "reason": "reviewed in the original inventory",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    decisions_path.write_text(
        json.dumps(
            {
                "schema": audit_v2.DECISION_SCHEMA,
                "reviewed_main": reviewed_main,
                "legacy_reason_source": {
                    "path": "precios-supermercados-sps/docs/audits/precios-sps-historical-branch-overrides.json",
                    "schema": audit_v2.LEGACY_SCHEMA,
                    "as_of_main": legacy_main,
                },
                "decisions": {
                    branch: {
                        "category": "CLOSED_SUPERSEDED",
                        "tip_sha": "c" * 40,
                        "unique_patch_count": 4,
                        "legacy_reason_key": branch,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        legacy,
        "_git",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(legacy, "_is_ancestor", lambda ancestor, descendant: True)

    loaded_main, decisions = audit_v2.load_decisions(
        decisions_path,
        legacy_overrides=legacy_path,
        main_ref="origin/main",
    )
    assert loaded_main == reviewed_main
    assert decisions[branch].tip_sha == "c" * 40
    assert decisions[branch].unique_patch_count == 4
    assert decisions[branch].reason == "reviewed in the original inventory"


def test_delta_v2_distinguishes_carried_subsumed_drift_revived_and_new() -> None:
    legacy, audit_v2, delta_v2 = _modules()
    decisions = {
        name: audit_v2.HistoricalDecision(
            branch=name,
            tip_sha=tip,
            unique_patch_count=patches,
            reason="reviewed",
        )
        for name, tip, patches in (
            ("a", "a" * 40, 1),
            ("b", "b" * 40, 2),
            ("c", "c" * 40, 3),
            ("d", "d" * 40, 4),
            ("e", "e" * 40, 5),
        )
    }
    rows = [
        _row(legacy, branch="a", category="UNIQUE_UNMERGED", tip="a" * 40, patches=1),
        _row(legacy, branch="b", category="MERGED_OR_SUBSUMED", tip="b" * 40, patches=0),
        _row(legacy, branch="c", category="UNIQUE_UNMERGED", tip="c" * 40, patches=99),
        _row(legacy, branch="d", category="OPEN_CURRENT", tip="d" * 40, patches=4),
        _row(legacy, branch="new", category="UNIQUE_UNMERGED", tip="f" * 40, patches=1),
        _row(legacy, branch="open", category="OPEN_CURRENT", tip="0" * 40, patches=1),
    ]

    groups = delta_v2.classify_delta(rows, decisions)
    assert groups == {
        "CARRIED_EXACT": ("a",),
        "SUBSUMED": ("b",),
        "DRIFT": ("c",),
        "REVIVED_OPEN": ("d",),
        "MISSING": ("e",),
        "NEW_UNIQUE": ("new",),
        "OPEN_CURRENT": ("open",),
    }


def test_v2_zero_matching_branches_is_valid_empty_audit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    legacy, audit_v2, delta_v2 = _modules()
    monkeypatch.setattr(legacy, "_git", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(legacy, "_ref_sha", lambda ref: "f" * 40)
    monkeypatch.setattr(legacy, "_branches", lambda remote, pattern: [])
    loaded: list[Path] = []

    def fake_load(path, *, legacy_overrides, main_ref):
        loaded.append(path)
        return "a" * 40, {"feature/precios-sps-old": audit_v2.HistoricalDecision(
            branch="feature/precios-sps-old", tip_sha="c" * 40, unique_patch_count=1, reason="old"
        )}

    monkeypatch.setattr(audit_v2, "load_decisions", fake_load)
    common = {
        "main_ref": "origin/main",
        "remote_prefix": "origin",
        "pattern": "precios-sps",
        "decisions_path": tmp_path / "decisions.json",
        "legacy_overrides": tmp_path / "legacy.json",
    }

    assert audit_v2.audit(**common, inspect_only=True) == ("f" * 40, None, [], ())
    assert loaded == []
    # Estricto: valida el archivo de decisiones, pero no hay ramas a las que aplicarlo.
    assert audit_v2.audit(**common, inspect_only=False) == ("f" * 40, "a" * 40, [], ())
    assert loaded == [tmp_path / "decisions.json"]

    # Un archivo de decisiones inválido sigue fallando cerrado sin ramas.
    monkeypatch.setattr(
        audit_v2,
        "load_decisions",
        lambda *args, **kwargs: (_ for _ in ()).throw(legacy.AuditError("historical decision v2 shape invalid")),
    )
    with pytest.raises(legacy.AuditError, match="shape invalid"):
        audit_v2.audit(**common, inspect_only=False)

    groups = delta_v2.classify_delta([], fake_load(None, legacy_overrides=None, main_ref=None)[1])
    assert groups["MISSING"] == ("feature/precios-sps-old",)
    assert not any(values for name, values in groups.items() if name != "MISSING")
    rendered = delta_v2._markdown(groups, reviewed_main="a" * 40, current_main="f" * 40, empty_inventory=True)
    assert legacy.NO_HISTORICAL_BRANCHES in rendered
