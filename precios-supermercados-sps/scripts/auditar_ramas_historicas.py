#!/usr/bin/env python3
"""Clasifica ramas históricas `precios-sps` contra `origin/main`.

La clasificación automática es deliberadamente conservadora:

* MERGED_OR_SUBSUMED: el tip es ancestro de main, el tree coincide o todos los
  commits no-merge tienen patch equivalente en main según ``git cherry``.
* OPEN_CURRENT: todavía conserva cambios únicos y existe un PR abierto para el
  head exacto.
* UNIQUE_UNMERGED: conserva cambios únicos y no existe PR abierto.
* CLOSED_SUPERSEDED: un candidato UNIQUE_UNMERGED fue inspeccionado y existe una
  decisión manual versionada que explica por qué no debe recuperarse.

El script no borra, fusiona ni modifica ramas. Usa sólo git local y, cuando se
proporciona GITHUB_TOKEN, la API de GitHub para consultar PRs del head exacto.
Las decisiones manuales se cargan desde un archivo versionado y sólo pueden
reclasificar candidatos UNIQUE_UNMERGED del snapshot exacto de main auditado.

``--inspect-only`` conserva la clasificación automática antes de aplicar
excepciones manuales. Sirve para producir evidencia revisable sobre un snapshot
nuevo de ``main`` sin rebajar el cierre fail-closed del modo normal.

Repositorio separado (2026-09-30): este repositorio se separó del monorepo
Portafolio y las ramas históricas ``precios-sps`` no existen aquí (sólo ``main``,
``portfolio-data`` y ramas ``rpi/*``). Si ningún ref remoto coincide con el
patrón, la auditoría produce un inventario vacío válido (JSON ``[]`` y Markdown
"no historical branches in this repository") y termina en 0: sin ramas no hay
candidatos UNIQUE_UNMERGED ni excepciones que aplicar. Cualquier rama que
coincida vuelve a la clasificación fail-closed habitual.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any


CATEGORIES = {
    "MERGED_OR_SUBSUMED",
    "CLOSED_SUPERSEDED",
    "OPEN_CURRENT",
    "UNIQUE_UNMERGED",
}
OVERRIDE_SCHEMA = "precios-sps-historical-branch-overrides-1"
DEFAULT_OVERRIDES = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "audits"
    / "precios-sps-historical-branch-overrides.json"
)


class AuditError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BranchAudit:
    branch: str
    category: str
    tip_sha: str
    unique_patch_count: int
    open_prs: tuple[int, ...]
    closed_unmerged_prs: tuple[int, ...]
    merged_prs: tuple[int, ...]
    unique_commit_subjects: tuple[str, ...]
    changed_files: tuple[str, ...]
    reason: str


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise AuditError(
            f"git {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}"
        )
    return result


def _ref_sha(ref: str) -> str:
    return _git("rev-parse", ref).stdout.strip()


def _is_ancestor(ancestor: str, descendant: str) -> bool:
    result = _git("merge-base", "--is-ancestor", ancestor, descendant, check=False)
    if result.returncode not in {0, 1}:
        raise AuditError(result.stderr.strip() or "merge-base failed")
    return result.returncode == 0


def _same_tree(left: str, right: str) -> bool:
    return _ref_sha(f"{left}^{{tree}}") == _ref_sha(f"{right}^{{tree}}")


def _unique_patches(main_ref: str, branch_ref: str) -> list[str]:
    result = _git("cherry", main_ref, branch_ref)
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return [line[2:] for line in lines if line.startswith("+ ")]


def _subjects(shas: list[str]) -> tuple[str, ...]:
    result: list[str] = []
    for sha in shas:
        subject = _git("show", "-s", "--format=%s", sha).stdout.strip()
        result.append(subject or "(sin subject)")
    return tuple(result)


def _changed_files(shas: list[str]) -> tuple[str, ...]:
    files: set[str] = set()
    for sha in shas:
        raw = _git(
            "show",
            "--format=",
            "--name-only",
            "--no-renames",
            sha,
        ).stdout.splitlines()
        files.update(path.strip() for path in raw if path.strip())
    return tuple(sorted(files))


def _repository() -> tuple[str, str]:
    value = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if "/" not in value:
        remote = _git("remote", "get-url", "origin").stdout.strip()
        suffix = remote.removesuffix(".git")
        if suffix.startswith("https://github.com/"):
            value = suffix.removeprefix("https://github.com/")
        elif suffix.startswith("git@github.com:"):
            value = suffix.removeprefix("git@github.com:")
    parts = value.split("/", maxsplit=1)
    if len(parts) != 2 or not all(parts):
        raise AuditError("cannot determine GitHub owner/repository")
    return parts[0], parts[1]


def _pull_requests_for_branch(branch: str) -> list[dict[str, Any]]:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        return []
    owner, repo = _repository()
    query = urllib.parse.urlencode(
        {
            "state": "all",
            "head": f"{owner}:{branch}",
            "per_page": "100",
        }
    )
    request = urllib.request.Request(
        f"https://api.github.com/repos/{owner}/{repo}/pulls?{query}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "precios-sps-branch-audit",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except Exception as exc:  # pragma: no cover - sólo red real en workflow
        raise AuditError(f"GitHub PR lookup failed for {branch}: {type(exc).__name__}") from exc
    if not isinstance(payload, list):
        raise AuditError(f"GitHub PR lookup returned invalid payload for {branch}")
    return [item for item in payload if isinstance(item, dict)]


def _row(
    *,
    branch: str,
    category: str,
    tip_sha: str,
    reason: str,
    unique_shas: list[str] | None = None,
    open_prs: tuple[int, ...] = (),
    closed_unmerged_prs: tuple[int, ...] = (),
    merged_prs: tuple[int, ...] = (),
) -> BranchAudit:
    shas = unique_shas or []
    return BranchAudit(
        branch=branch,
        category=category,
        tip_sha=tip_sha,
        unique_patch_count=len(shas),
        open_prs=open_prs,
        closed_unmerged_prs=closed_unmerged_prs,
        merged_prs=merged_prs,
        unique_commit_subjects=_subjects(shas),
        changed_files=_changed_files(shas),
        reason=reason,
    )


def _classify(main_ref: str, remote_prefix: str, branch: str) -> BranchAudit:
    ref = f"{remote_prefix}/{branch}"
    tip_sha = _ref_sha(ref)

    if _is_ancestor(ref, main_ref):
        return _row(
            branch=branch,
            category="MERGED_OR_SUBSUMED",
            tip_sha=tip_sha,
            reason="branch tip is an ancestor of main",
        )
    if _same_tree(ref, main_ref):
        return _row(
            branch=branch,
            category="MERGED_OR_SUBSUMED",
            tip_sha=tip_sha,
            reason="branch tree equals main tree",
        )

    unique_patches = _unique_patches(main_ref, ref)
    if not unique_patches:
        return _row(
            branch=branch,
            category="MERGED_OR_SUBSUMED",
            tip_sha=tip_sha,
            reason="all branch patches are patch-equivalent to main",
        )

    prs = _pull_requests_for_branch(branch)
    open_prs = tuple(
        sorted(int(pr["number"]) for pr in prs if pr.get("state") == "open")
    )
    merged_prs = tuple(
        sorted(int(pr["number"]) for pr in prs if pr.get("merged_at"))
    )
    closed_unmerged_prs = tuple(
        sorted(
            int(pr["number"])
            for pr in prs
            if pr.get("state") == "closed" and not pr.get("merged_at")
        )
    )
    category = "OPEN_CURRENT" if open_prs else "UNIQUE_UNMERGED"
    reason = (
        "unique patches remain and exact head has an open PR"
        if open_prs
        else "unique patches remain; requires manual intent/content inspection"
    )
    return _row(
        branch=branch,
        category=category,
        tip_sha=tip_sha,
        unique_shas=unique_patches,
        open_prs=open_prs,
        closed_unmerged_prs=closed_unmerged_prs,
        merged_prs=merged_prs,
        reason=reason,
    )


def _branches(remote_prefix: str, pattern: str) -> list[str]:
    raw = _git(
        "for-each-ref",
        "--format=%(refname:strip=3)",
        f"refs/remotes/{remote_prefix}/",
    ).stdout.splitlines()
    return sorted(
        branch.strip()
        for branch in raw
        if branch.strip() and branch.strip() != "HEAD" and pattern in branch
    )


def _load_overrides(path: Path, *, expected_main_sha: str) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditError(f"cannot load override file {path}: {type(exc).__name__}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != OVERRIDE_SCHEMA:
        raise AuditError("historical branch override schema invalid")
    if payload.get("as_of_main") != expected_main_sha:
        raise AuditError(
            "historical branch overrides were not inspected against the current main snapshot"
        )
    raw_overrides = payload.get("overrides")
    if not isinstance(raw_overrides, dict) or not raw_overrides:
        raise AuditError("historical branch overrides missing")

    overrides: dict[str, str] = {}
    for branch, raw in raw_overrides.items():
        if not isinstance(branch, str) or not branch or not isinstance(raw, dict):
            raise AuditError("historical branch override entry invalid")
        if raw.get("category") != "CLOSED_SUPERSEDED":
            raise AuditError(f"override category invalid for {branch}")
        reason = raw.get("reason")
        if not isinstance(reason, str) or not reason.strip() or reason.strip() != reason:
            raise AuditError(f"override reason invalid for {branch}")
        overrides[branch] = reason
    return overrides


def _apply_overrides(
    rows: list[BranchAudit],
    overrides: dict[str, str],
) -> list[BranchAudit]:
    by_branch = {row.branch: row for row in rows}
    missing = sorted(set(overrides) - set(by_branch))
    if missing:
        raise AuditError(f"override branches are absent from remote inventory: {missing}")

    result: list[BranchAudit] = []
    used: set[str] = set()
    for row in rows:
        reason = overrides.get(row.branch)
        if reason is None:
            result.append(row)
            continue
        if row.category != "UNIQUE_UNMERGED":
            raise AuditError(
                f"override for {row.branch} is stale: automatic category is {row.category}"
            )
        used.add(row.branch)
        result.append(
            replace(
                row,
                category="CLOSED_SUPERSEDED",
                reason=f"manual inspection: {reason}",
            )
        )
    if used != set(overrides):
        raise AuditError("not all historical branch overrides were applied")
    return result


def _short(values: tuple[str, ...], limit: int = 12) -> str:
    visible = values[:limit]
    rendered = "; ".join(visible)
    if len(values) > limit:
        rendered += f"; … (+{len(values) - limit})"
    return rendered or "—"


NO_HISTORICAL_BRANCHES = "no historical branches in this repository"


def _markdown(rows: list[BranchAudit], *, main_sha: str, pattern: str | None = None) -> str:
    counts = {category: 0 for category in sorted(CATEGORIES)}
    for row in rows:
        counts[row.category] += 1
    lines = [
        "# Inventario de ramas históricas precios-sps",
        "",
        f"Snapshot de `main`: `{main_sha}`",
        f"Total: **{len(rows)}**",
        "",
    ]
    if not rows:
        matched = f" coincide con `{pattern}`" if pattern else " coincide con el patrón"
        lines.extend(
            [
                f"Sin ramas históricas: {NO_HISTORICAL_BRANCHES} (ningún ref remoto{matched};",
                "el repositorio se separó del monorepo Portafolio el 2026-09-30).",
                "",
            ]
        )
    lines.extend(f"- {category}: **{counts[category]}**" for category in sorted(counts))
    candidates = [row for row in rows if row.category != "MERGED_OR_SUBSUMED"]
    lines.extend(["", "## Ramas no clasificadas como merged/subsumed", ""])
    if not candidates:
        lines.append("Ninguna.")
    else:
        lines.append("| rama | categoría | patches únicos | PR abiertos | PR cerrados sin merge |")
        lines.append("|---|---|---:|---|---|")
        for row in candidates:
            open_prs = ", ".join(f"#{number}" for number in row.open_prs) or "—"
            closed_prs = ", ".join(f"#{number}" for number in row.closed_unmerged_prs) or "—"
            lines.append(
                f"| `{row.branch}` | {row.category} | {row.unique_patch_count} | {open_prs} | {closed_prs} |"
            )
        lines.extend(["", "## Evidencia de ramas no merged/subsumed", ""])
        for row in candidates:
            lines.extend(
                [
                    f"### `{row.branch}`",
                    f"- category: `{row.category}`",
                    f"- tip: `{row.tip_sha}`",
                    f"- subjects: {_short(row.unique_commit_subjects)}",
                    f"- files: {_short(row.changed_files, limit=24)}",
                    f"- reason: {row.reason}",
                    "",
                ]
            )
    unresolved = [row.branch for row in rows if row.category == "UNIQUE_UNMERGED"]
    lines.extend(
        [
            "## Cierre",
            "",
            (
                "No quedan ramas UNIQUE_UNMERGED pendientes de inspección."
                if not unresolved
                else "Persisten ramas UNIQUE_UNMERGED: " + ", ".join(f"`{branch}`" for branch in unresolved)
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _write_outputs(
    rows: list[BranchAudit],
    *,
    main_sha: str,
    json_output: Path,
    markdown_output: Path,
    pattern: str | None = None,
) -> None:
    json_output.write_text(
        json.dumps([asdict(row) for row in rows], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown = _markdown(rows, main_sha=main_sha, pattern=pattern)
    markdown_output.write_text(markdown, encoding="utf-8")
    print(markdown)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--main-ref", default="origin/main")
    parser.add_argument("--remote-prefix", default="origin")
    parser.add_argument("--pattern", default="precios-sps")
    parser.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    parser.add_argument("--inspect-only", action="store_true")
    parser.add_argument("--json-output", type=Path, default=Path("branch-audit.json"))
    parser.add_argument("--markdown-output", type=Path, default=Path("branch-audit.md"))
    args = parser.parse_args()

    _git("rev-parse", "--verify", args.main_ref)
    main_sha = _ref_sha(args.main_ref)
    branches = _branches(args.remote_prefix, args.pattern)
    if not branches:
        # Inventario vacío válido: no hay candidatos ni excepciones aplicables.
        _write_outputs(
            [],
            main_sha=main_sha,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
            pattern=args.pattern,
        )
        return 0

    rows = [_classify(args.main_ref, args.remote_prefix, branch) for branch in branches]
    if args.inspect_only:
        _write_outputs(
            rows,
            main_sha=main_sha,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        return 0

    overrides = _load_overrides(args.overrides, expected_main_sha=main_sha)
    rows = _apply_overrides(rows, overrides)
    unresolved = [row.branch for row in rows if row.category == "UNIQUE_UNMERGED"]
    if unresolved:
        raise AuditError(
            "historical branch inventory still has UNIQUE_UNMERGED candidates: "
            + ", ".join(unresolved)
        )

    _write_outputs(
        rows,
        main_sha=main_sha,
        json_output=args.json_output,
        markdown_output=args.markdown_output,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AuditError as exc:
        print(f"branch_audit_error: {exc}", file=sys.stderr)
        raise SystemExit(2)
