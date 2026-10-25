#!/usr/bin/env python3
"""Resume diferencias entre clasificación automática y overrides históricos.

No aplica ni modifica decisiones. Consume el JSON sanitizado producido por
``auditar_ramas_historicas.py --inspect-only`` y el archivo versionado de
overrides para hacer visible qué decisiones pueden conservarse, cuáles quedaron
obsoletas y qué candidatos UNIQUE_UNMERGED son nuevos en el snapshot actual.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

OVERRIDE_SCHEMA = "precios-sps-historical-branch-overrides-1"
ALLOWED_CATEGORIES = {
    "MERGED_OR_SUBSUMED",
    "CLOSED_SUPERSEDED",
    "OPEN_CURRENT",
    "UNIQUE_UNMERGED",
}


class AuditDeltaError(ValueError):
    pass


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditDeltaError(f"invalid_json:{path}") from exc


def _automatic_rows(path: Path) -> dict[str, dict[str, Any]]:
    payload = _load_json(path)
    if not isinstance(payload, list) or not payload:
        raise AuditDeltaError("automatic_audit_invalid")
    rows: dict[str, dict[str, Any]] = {}
    for raw in payload:
        if not isinstance(raw, dict):
            raise AuditDeltaError("automatic_row_invalid")
        branch = raw.get("branch")
        category = raw.get("category")
        if not isinstance(branch, str) or not branch or branch in rows:
            raise AuditDeltaError("automatic_branch_invalid")
        if category not in ALLOWED_CATEGORIES:
            raise AuditDeltaError("automatic_category_invalid")
        rows[branch] = raw
    return rows


def _previous_overrides(path: Path) -> tuple[str, dict[str, str]]:
    payload = _load_json(path)
    if not isinstance(payload, dict) or payload.get("schema") != OVERRIDE_SCHEMA:
        raise AuditDeltaError("override_schema_invalid")
    as_of_main = payload.get("as_of_main")
    raw_overrides = payload.get("overrides")
    if not isinstance(as_of_main, str) or len(as_of_main) != 40:
        raise AuditDeltaError("override_main_invalid")
    if not isinstance(raw_overrides, dict):
        raise AuditDeltaError("overrides_invalid")
    result: dict[str, str] = {}
    for branch, raw in raw_overrides.items():
        if not isinstance(branch, str) or not branch or not isinstance(raw, dict):
            raise AuditDeltaError("override_entry_invalid")
        if raw.get("category") != "CLOSED_SUPERSEDED":
            raise AuditDeltaError("override_category_invalid")
        reason = raw.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise AuditDeltaError("override_reason_invalid")
        result[branch] = reason.strip()
    return as_of_main, result


def render_delta(
    rows: dict[str, dict[str, Any]],
    previous_main: str,
    overrides: dict[str, str],
    current_main: str,
) -> str:
    if len(current_main) != 40:
        raise AuditDeltaError("current_main_invalid")
    unique = {branch for branch, row in rows.items() if row["category"] == "UNIQUE_UNMERGED"}
    open_current = {branch for branch, row in rows.items() if row["category"] == "OPEN_CURRENT"}
    previous = set(overrides)
    carry = sorted(unique & previous)
    new = sorted(unique - previous)
    stale = sorted(previous - unique)

    lines = [
        "# Delta de auditoría histórica precios-sps",
        "",
        f"Snapshot anterior de overrides: `{previous_main}`",
        f"Snapshot automático actual: `{current_main}`",
        "",
        f"- decisiones previas todavía UNIQUE_UNMERGED: **{len(carry)}**",
        f"- candidatos UNIQUE_UNMERGED nuevos: **{len(new)}**",
        f"- overrides previos que ya no son UNIQUE_UNMERGED: **{len(stale)}**",
        f"- ramas OPEN_CURRENT: **{len(open_current)}**",
        "",
        "## Candidatos nuevos que requieren inspección manual",
        "",
    ]
    if new:
        for branch in new:
            row = rows[branch]
            closed = ", ".join(f"#{value}" for value in row.get("closed_unmerged_prs", [])) or "—"
            subjects = "; ".join(row.get("unique_commit_subjects", [])[:6]) or "—"
            files = "; ".join(row.get("changed_files", [])[:12]) or "—"
            lines.extend(
                [
                    f"### `{branch}`",
                    f"- tip: `{row.get('tip_sha')}`",
                    f"- patches únicos: {row.get('unique_patch_count')}",
                    f"- PR cerrados sin merge: {closed}",
                    f"- subjects: {subjects}",
                    f"- files: {files}",
                    "",
                ]
            )
    else:
        lines.extend(["Ninguno.", ""])

    lines.extend(["## Overrides previos que deben retirarse o revalidarse", ""])
    if stale:
        for branch in stale:
            category = rows.get(branch, {}).get("category", "ABSENT_FROM_REMOTE_INVENTORY")
            lines.append(f"- `{branch}` -> `{category}`")
    else:
        lines.append("Ninguno.")

    lines.extend(["", "## Ramas abiertas actuales", ""])
    if open_current:
        for branch in sorted(open_current):
            prs = ", ".join(f"#{value}" for value in rows[branch].get("open_prs", [])) or "—"
            lines.append(f"- `{branch}` -> {prs}")
    else:
        lines.append("Ninguna.")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--automatic-audit", type=Path, required=True)
    parser.add_argument("--overrides", type=Path, required=True)
    parser.add_argument("--current-main", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    rows = _automatic_rows(args.automatic_audit)
    previous_main, overrides = _previous_overrides(args.overrides)
    rendered = render_delta(rows, previous_main, overrides, args.current_main)
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
