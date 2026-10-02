#!/usr/bin/env python3
"""Resume el delta de un snapshot automático contra decisiones históricas v2.

El resumen es diagnóstico y no rebaja el modo estricto. Distingue decisiones
exactas, decisiones ya subsumidas, drift real, ramas nuevas y PRs abiertos.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import auditar_ramas_historicas as legacy
import auditar_ramas_historicas_v2 as audit_v2


def _load_rows(path: Path) -> list[legacy.BranchAudit]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise legacy.AuditError(f"cannot load automatic audit: {type(exc).__name__}") from exc
    if not isinstance(payload, list):
        raise legacy.AuditError("automatic audit shape invalid")
    rows: list[legacy.BranchAudit] = []
    for raw in payload:
        if not isinstance(raw, dict):
            raise legacy.AuditError("automatic audit row invalid")
        try:
            rows.append(
                legacy.BranchAudit(
                    branch=raw["branch"],
                    category=raw["category"],
                    tip_sha=raw["tip_sha"],
                    unique_patch_count=raw["unique_patch_count"],
                    open_prs=tuple(raw["open_prs"]),
                    closed_unmerged_prs=tuple(raw["closed_unmerged_prs"]),
                    merged_prs=tuple(raw["merged_prs"]),
                    unique_commit_subjects=tuple(raw["unique_commit_subjects"]),
                    changed_files=tuple(raw["changed_files"]),
                    reason=raw["reason"],
                )
            )
        except (KeyError, TypeError) as exc:
            raise legacy.AuditError("automatic audit row invalid") from exc
    return rows


def classify_delta(
    rows: list[legacy.BranchAudit],
    decisions: dict[str, audit_v2.HistoricalDecision],
) -> dict[str, tuple[str, ...]]:
    by_branch = {row.branch: row for row in rows}
    carried: list[str] = []
    subsumed: list[str] = []
    drift: list[str] = []
    revived_open: list[str] = []
    missing: list[str] = []
    new_unique: list[str] = []
    open_current: list[str] = []

    for branch, decision in decisions.items():
        row = by_branch.get(branch)
        if row is None:
            missing.append(branch)
        elif row.category == "MERGED_OR_SUBSUMED":
            subsumed.append(branch)
        elif row.category == "OPEN_CURRENT":
            revived_open.append(branch)
        elif row.category != "UNIQUE_UNMERGED":
            drift.append(branch)
        elif row.tip_sha == decision.tip_sha and row.unique_patch_count == decision.unique_patch_count:
            carried.append(branch)
        else:
            drift.append(branch)

    for row in rows:
        if row.branch in decisions:
            continue
        if row.category == "UNIQUE_UNMERGED":
            new_unique.append(row.branch)
        elif row.category == "OPEN_CURRENT":
            open_current.append(row.branch)

    return {
        "CARRIED_EXACT": tuple(sorted(carried)),
        "SUBSUMED": tuple(sorted(subsumed)),
        "DRIFT": tuple(sorted(drift)),
        "REVIVED_OPEN": tuple(sorted(revived_open)),
        "MISSING": tuple(sorted(missing)),
        "NEW_UNIQUE": tuple(sorted(new_unique)),
        "OPEN_CURRENT": tuple(sorted(open_current)),
    }


def _markdown(
    groups: dict[str, tuple[str, ...]],
    *,
    reviewed_main: str,
    current_main: str,
    empty_inventory: bool = False,
) -> str:
    lines = [
        "# Delta de auditoría histórica v2",
        "",
        f"Baseline revisado ancestro: `{reviewed_main}`",
        f"Main actual: `{current_main}`",
        "",
    ]
    if empty_inventory:
        # Repositorio separado del monorepo (2026-09-30): las decisiones quedan MISSING.
        lines.extend([f"Inventario vacío: {legacy.NO_HISTORICAL_BRANCHES}.", ""])
    for name in (
        "CARRIED_EXACT",
        "SUBSUMED",
        "DRIFT",
        "REVIVED_OPEN",
        "MISSING",
        "NEW_UNIQUE",
        "OPEN_CURRENT",
    ):
        values = groups[name]
        lines.append(f"## {name} ({len(values)})")
        lines.append("")
        if values:
            lines.extend(f"- `{branch}`" for branch in values)
        else:
            lines.append("Ninguna.")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--automatic-audit", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, default=audit_v2.DEFAULT_DECISIONS)
    parser.add_argument("--legacy-overrides", type=Path, default=audit_v2.DEFAULT_LEGACY_OVERRIDES)
    parser.add_argument("--main-ref", default="origin/main")
    parser.add_argument("--current-main", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    reviewed_main, decisions = audit_v2.load_decisions(
        args.decisions,
        legacy_overrides=args.legacy_overrides,
        main_ref=args.main_ref,
    )
    current_main = audit_v2._sha40(args.current_main, "current main invalid")
    if current_main != legacy._ref_sha(args.main_ref):
        raise legacy.AuditError("current main argument does not match main ref")
    rows = _load_rows(args.automatic_audit)
    groups = classify_delta(rows, decisions)
    rendered = _markdown(
        groups,
        reviewed_main=reviewed_main,
        current_main=current_main,
        empty_inventory=not rows,
    )
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except legacy.AuditError as exc:
        print(f"branch_audit_delta_v2_error: {exc}", file=sys.stderr)
        raise SystemExit(2)
