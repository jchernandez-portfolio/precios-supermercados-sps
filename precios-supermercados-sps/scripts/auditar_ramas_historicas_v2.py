#!/usr/bin/env python3
"""Auditoría histórica v2 sin dependencia autorreferente del SHA de ``main``.

La clasificación automática sigue viviendo en ``auditar_ramas_historicas.py``.
Esta capa endurece únicamente la frontera de decisiones manuales:

* ``reviewed_main`` debe ser un commit ancestro del ``main`` auditado;
* cada decisión queda ligada al tip SHA y al número de patches únicos revisados;
* un tip o patch-count cambiado obliga a una revisión nueva;
* una rama ya ``MERGED_OR_SUBSUMED`` deja de necesitar la excepción manual;
* una rama decidida que reaparece con PR abierto falla cerrado;
* las razones históricas v1 se conservan por referencia a su archivo versionado.

Sin ramas históricas que coincidan con el patrón (repositorio separado del
monorepo el 2026-09-30) el resultado es un inventario vacío válido: en modo
estricto el archivo de decisiones se sigue validando, pero no hay ramas a las
que aplicarlo y el cierre termina en 0.

No borra, fusiona ni modifica ramas y no realiza tráfico hacia supermercados.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import auditar_ramas_historicas as legacy

DECISION_SCHEMA = "precios-sps-historical-branch-decisions-2"
LEGACY_SCHEMA = "precios-sps-historical-branch-overrides-1"
_SHA40 = re.compile(r"[0-9a-f]{40}\Z")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DECISIONS = (
    PROJECT_ROOT / "docs" / "audits" / "precios-sps-historical-branch-decisions-v2.json"
)
DEFAULT_LEGACY_OVERRIDES = (
    PROJECT_ROOT / "docs" / "audits" / "precios-sps-historical-branch-overrides.json"
)


@dataclass(frozen=True, slots=True)
class HistoricalDecision:
    branch: str
    tip_sha: str
    unique_patch_count: int
    reason: str


def _load_json(path: Path, code: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise legacy.AuditError(f"{code}: {type(exc).__name__}") from exc


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise legacy.AuditError(code)
    return value


def _sha40(value: object, code: str) -> str:
    text = _text(value, code)
    if _SHA40.fullmatch(text) is None:
        raise legacy.AuditError(code)
    return text


def _legacy_reasons(
    path: Path,
    *,
    expected_schema: str,
    expected_main: str,
) -> dict[str, str]:
    payload = _load_json(path, "cannot load legacy historical decisions")
    if not isinstance(payload, dict) or set(payload) != {"schema", "as_of_main", "overrides"}:
        raise legacy.AuditError("legacy historical decision shape invalid")
    if payload.get("schema") != expected_schema or expected_schema != LEGACY_SCHEMA:
        raise legacy.AuditError("legacy historical decision schema invalid")
    if payload.get("as_of_main") != expected_main:
        raise legacy.AuditError("legacy historical decision baseline mismatch")
    raw = payload.get("overrides")
    if not isinstance(raw, dict) or not raw:
        raise legacy.AuditError("legacy historical decisions missing")
    result: dict[str, str] = {}
    for branch, entry in raw.items():
        if not isinstance(branch, str) or not branch or not isinstance(entry, dict):
            raise legacy.AuditError("legacy historical decision entry invalid")
        if set(entry) != {"category", "reason"} or entry.get("category") != "CLOSED_SUPERSEDED":
            raise legacy.AuditError(f"legacy historical decision invalid for {branch}")
        result[branch] = _text(entry.get("reason"), f"legacy reason invalid for {branch}")
    return result


def load_decisions(
    path: Path,
    *,
    legacy_overrides: Path,
    main_ref: str,
) -> tuple[str, dict[str, HistoricalDecision]]:
    payload = _load_json(path, "cannot load historical decisions v2")
    expected_root = {"schema", "reviewed_main", "legacy_reason_source", "decisions"}
    if not isinstance(payload, dict) or set(payload) != expected_root:
        raise legacy.AuditError("historical decision v2 shape invalid")
    if payload.get("schema") != DECISION_SCHEMA:
        raise legacy.AuditError("historical decision v2 schema invalid")

    reviewed_main = _sha40(payload.get("reviewed_main"), "reviewed_main invalid")
    verify = legacy._git("cat-file", "-e", f"{reviewed_main}^{{commit}}", check=False)
    if verify.returncode != 0:
        raise legacy.AuditError("reviewed_main commit unavailable")
    if not legacy._is_ancestor(reviewed_main, main_ref):
        raise legacy.AuditError("reviewed_main is not an ancestor of current main")

    source = payload.get("legacy_reason_source")
    expected_source_keys = {"path", "schema", "as_of_main"}
    if not isinstance(source, dict) or set(source) != expected_source_keys:
        raise legacy.AuditError("legacy reason source invalid")
    source_path = _text(source.get("path"), "legacy reason source path invalid")
    if source_path != "precios-supermercados-sps/docs/audits/precios-sps-historical-branch-overrides.json":
        raise legacy.AuditError("legacy reason source path unexpected")
    source_schema = _text(source.get("schema"), "legacy reason source schema invalid")
    source_main = _sha40(source.get("as_of_main"), "legacy reason source main invalid")
    reasons = _legacy_reasons(
        legacy_overrides,
        expected_schema=source_schema,
        expected_main=source_main,
    )

    raw_decisions = payload.get("decisions")
    if not isinstance(raw_decisions, dict) or not raw_decisions:
        raise legacy.AuditError("historical decisions v2 missing")
    result: dict[str, HistoricalDecision] = {}
    for branch, raw in raw_decisions.items():
        if not isinstance(branch, str) or not branch or not isinstance(raw, dict):
            raise legacy.AuditError("historical decision v2 entry invalid")
        if raw.get("category") != "CLOSED_SUPERSEDED":
            raise legacy.AuditError(f"historical decision category invalid for {branch}")
        tip_sha = _sha40(raw.get("tip_sha"), f"historical decision tip invalid for {branch}")
        count = raw.get("unique_patch_count")
        if type(count) is not int or count <= 0:
            raise legacy.AuditError(f"historical decision patch count invalid for {branch}")
        has_reason = "reason" in raw
        has_legacy = "legacy_reason_key" in raw
        expected_keys = {"category", "tip_sha", "unique_patch_count"} | (
            {"reason"} if has_reason else {"legacy_reason_key"}
        )
        if has_reason == has_legacy or set(raw) != expected_keys:
            raise legacy.AuditError(f"historical decision reason shape invalid for {branch}")
        if has_reason:
            reason = _text(raw.get("reason"), f"historical decision reason invalid for {branch}")
        else:
            key = _text(raw.get("legacy_reason_key"), f"legacy reason key invalid for {branch}")
            if key != branch or key not in reasons:
                raise legacy.AuditError(f"legacy reason key unresolved for {branch}")
            reason = reasons[key]
        result[branch] = HistoricalDecision(
            branch=branch,
            tip_sha=tip_sha,
            unique_patch_count=count,
            reason=reason,
        )
    return reviewed_main, result


def apply_decisions(
    rows: list[legacy.BranchAudit],
    decisions: dict[str, HistoricalDecision],
) -> tuple[list[legacy.BranchAudit], tuple[str, ...]]:
    by_branch = {row.branch: row for row in rows}
    missing = sorted(set(decisions) - set(by_branch))
    if missing:
        raise legacy.AuditError(f"historical decision branches absent from remote inventory: {missing}")

    result: list[legacy.BranchAudit] = []
    no_longer_needed: list[str] = []
    for row in rows:
        decision = decisions.get(row.branch)
        if decision is None:
            result.append(row)
            continue
        if row.category == "MERGED_OR_SUBSUMED":
            no_longer_needed.append(row.branch)
            result.append(row)
            continue
        if row.category == "OPEN_CURRENT":
            raise legacy.AuditError(f"historical decision for {row.branch} was revived by an open PR")
        if row.category != "UNIQUE_UNMERGED":
            raise legacy.AuditError(
                f"historical decision for {row.branch} has unexpected category {row.category}"
            )
        if row.tip_sha != decision.tip_sha:
            raise legacy.AuditError(f"historical decision tip drift for {row.branch}")
        if row.unique_patch_count != decision.unique_patch_count:
            raise legacy.AuditError(f"historical decision patch-count drift for {row.branch}")
        result.append(
            replace(
                row,
                category="CLOSED_SUPERSEDED",
                reason=f"reviewed decision v2: {decision.reason}",
            )
        )
    return result, tuple(no_longer_needed)


def audit(
    *,
    main_ref: str,
    remote_prefix: str,
    pattern: str,
    decisions_path: Path,
    legacy_overrides: Path,
    inspect_only: bool,
) -> tuple[str, str | None, list[legacy.BranchAudit], tuple[str, ...]]:
    legacy._git("rev-parse", "--verify", main_ref)
    main_sha = legacy._ref_sha(main_ref)
    branches = legacy._branches(remote_prefix, pattern)
    if not branches:
        if inspect_only:
            return main_sha, None, [], ()
        # Se valida el archivo versionado aunque no haya ramas a las que aplicarlo.
        reviewed_main, _decisions = load_decisions(
            decisions_path,
            legacy_overrides=legacy_overrides,
            main_ref=main_ref,
        )
        return main_sha, reviewed_main, [], ()
    rows = [legacy._classify(main_ref, remote_prefix, branch) for branch in branches]
    if inspect_only:
        return main_sha, None, rows, ()

    reviewed_main, decisions = load_decisions(
        decisions_path,
        legacy_overrides=legacy_overrides,
        main_ref=main_ref,
    )
    rows, no_longer_needed = apply_decisions(rows, decisions)
    unresolved = [row.branch for row in rows if row.category == "UNIQUE_UNMERGED"]
    if unresolved:
        raise legacy.AuditError(
            "historical branch inventory still has UNIQUE_UNMERGED candidates: "
            + ", ".join(unresolved)
        )
    return main_sha, reviewed_main, rows, no_longer_needed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--main-ref", default="origin/main")
    parser.add_argument("--remote-prefix", default="origin")
    parser.add_argument("--pattern", default="precios-sps")
    parser.add_argument("--decisions", type=Path, default=DEFAULT_DECISIONS)
    parser.add_argument("--legacy-overrides", type=Path, default=DEFAULT_LEGACY_OVERRIDES)
    parser.add_argument("--inspect-only", action="store_true")
    parser.add_argument("--json-output", type=Path, default=Path("branch-audit-v2.json"))
    parser.add_argument("--markdown-output", type=Path, default=Path("branch-audit-v2.md"))
    args = parser.parse_args()

    main_sha, reviewed_main, rows, no_longer_needed = audit(
        main_ref=args.main_ref,
        remote_prefix=args.remote_prefix,
        pattern=args.pattern,
        decisions_path=args.decisions,
        legacy_overrides=args.legacy_overrides,
        inspect_only=args.inspect_only,
    )
    legacy._write_outputs(
        rows,
        main_sha=main_sha,
        json_output=args.json_output,
        markdown_output=args.markdown_output,
        pattern=args.pattern,
    )
    if reviewed_main is not None:
        print(f"reviewed_main={reviewed_main}")
        print(f"decisions_no_longer_needed={len(no_longer_needed)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except legacy.AuditError as exc:
        print(f"branch_audit_v2_error: {exc}", file=sys.stderr)
        raise SystemExit(2)
