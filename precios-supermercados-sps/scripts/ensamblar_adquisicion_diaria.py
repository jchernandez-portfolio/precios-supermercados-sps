#!/usr/bin/env python3
"""Ensambla handoffs aceptados de adquisición por supermercado.

Cada job publica un artifact único por ``run_attempt``. Al reintentar sólo jobs
fallidos pueden coexistir intentos distintos dentro del mismo workflow run. Este
módulo selecciona, para cada supermercado, el intento aceptado más alto y
reconstruye un único ``run-artifacts/`` para las validaciones y persistencia ya
existentes.

Con ``--allow-missing`` (persistencia parcial, aprobada 2026-10-09) una cadena
sin handoff aceptado no bloquea a las demás: se lista en ``missing_retailers``
y sólo se ensamblan las aceptadas (al menos una). La cadena faltante conserva
su último precio en Turso y el catálogo la marca como no fresca (``STALE``).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path


HANDOFF_SCHEMA = "precios-sps-daily-acquisition-handoff/v1"
HANDOFF_FILE = "acquisition-handoff.json"
ARTIFACT_PREFIX = "daily-acquisition"

EXPECTED = {
    "la_colonia": (
        "sps/full-catalog.json",
        "tgu/full-catalog.json",
    ),
    "los_andes": (
        "los-andes/full-catalog.json",
    ),
    "paiz": (
        "paiz/snapshot-paiz-multiplaza.json",
        "paiz/snapshot-paiz-proceres.json",
    ),
    "colonial": (
        "colonial/full-catalog.json",
    ),
    "walmart": (
        "walmart/snapshot-walmart-sps.json",
        "walmart/snapshot-walmart-tgu-ffaa.json",
        "walmart/snapshot-walmart-tgu-el-sauce.json",
    ),
    "pricesmart": (
        "pricesmart/snapshot-pricesmart-sps.json",
        "pricesmart/snapshot-pricesmart-tgu.json",
    ),
}

OWNED_TOP_LEVEL = {
    "la_colonia": {"sps", "tgu"},
    "los_andes": {"los-andes"},
    "paiz": {"paiz"},
    "colonial": {"colonial"},
    "walmart": {"walmart"},
    "pricesmart": {"pricesmart"},
}


class AssemblyError(RuntimeError):
    pass


def _read_handoff(path: Path, *, retailer: str, run_id: str, attempt: int) -> bool:
    marker = path / HANDOFF_FILE
    if not marker.is_file() or marker.is_symlink():
        return False
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssemblyError(f"retailer_handoff_marker_invalid:{retailer}:{attempt}") from exc
    expected = {
        "schema": HANDOFF_SCHEMA,
        "retailer": retailer,
        "run_id": run_id,
        "run_attempt": attempt,
        "accepted": True,
    }
    if payload != expected:
        raise AssemblyError(f"retailer_handoff_marker_mismatch:{retailer}:{attempt}")
    return True


def _attempts(root: Path, retailer: str, run_id: str) -> list[tuple[int, Path]]:
    pattern = re.compile(
        rf"^{re.escape(ARTIFACT_PREFIX)}-{re.escape(retailer)}-{re.escape(run_id)}-attempt-([1-9][0-9]*)$"
    )
    result: list[tuple[int, Path]] = []
    if not root.is_dir() or root.is_symlink():
        return result
    for child in root.iterdir():
        if child.is_symlink() or not child.is_dir():
            continue
        match = pattern.fullmatch(child.name)
        if not match:
            continue
        attempt = int(match.group(1))
        if _read_handoff(child, retailer=retailer, run_id=run_id, attempt=attempt):
            result.append((attempt, child))
    return sorted(result)


def _complete(path: Path, required: tuple[str, ...]) -> bool:
    return all(
        (path / relative).is_file() and not (path / relative).is_symlink()
        for relative in required
    )


def select_handoffs(
    root: Path,
    *,
    run_id: str,
    allow_missing: bool = False,
) -> dict[str, tuple[int, Path]]:
    if not run_id.isdigit() or int(run_id) < 1:
        raise AssemblyError("daily_acquisition_run_id_invalid")
    selected: dict[str, tuple[int, Path]] = {}
    for retailer, required in EXPECTED.items():
        valid = [
            (attempt, path)
            for attempt, path in _attempts(root, retailer, run_id)
            if _complete(path, required)
        ]
        if not valid:
            if allow_missing:
                continue
            raise AssemblyError(f"accepted_retailer_handoff_missing:{retailer}")
        selected[retailer] = valid[-1]
    if not selected:
        raise AssemblyError("accepted_retailer_handoff_missing:all")
    return selected


def assemble(root: Path, output: Path, *, run_id: str, allow_missing: bool = False) -> dict[str, object]:
    selected = select_handoffs(root, run_id=run_id, allow_missing=allow_missing)
    if output.exists():
        if output.is_symlink() or not output.is_dir():
            raise AssemblyError("daily_acquisition_output_not_safe_directory")
        shutil.rmtree(output)
    output.mkdir(parents=True)
    for retailer, (attempt, source) in selected.items():
        allowed = OWNED_TOP_LEVEL[retailer]
        actual = {
            child.name
            for child in source.iterdir()
            if child.is_dir() and not child.is_symlink()
        }
        unexpected = actual - allowed
        if unexpected:
            raise AssemblyError(
                f"retailer_handoff_unexpected_directory:{retailer}:{sorted(unexpected)}"
            )
        for top_level in sorted(allowed):
            item = source / top_level
            if not item.is_dir() or item.is_symlink():
                raise AssemblyError(
                    f"retailer_handoff_directory_missing:{retailer}:{top_level}"
                )
            destination = output / top_level
            if destination.exists():
                raise AssemblyError(f"retailer_handoff_collision:{top_level}")
            shutil.copytree(item, destination, symlinks=False)
    for retailer, required in EXPECTED.items():
        if retailer not in selected:
            continue
        for relative in required:
            if not (output / relative).is_file():
                raise AssemblyError(
                    f"assembled_required_file_missing:{retailer}:{relative}"
                )
    return {
        "schema": "precios-sps-daily-acquisition-assembly/v1",
        "run_id": run_id,
        "retailers": {
            retailer: {
                "run_attempt": attempt,
                "required_files": list(EXPECTED[retailer]),
            }
            for retailer, (attempt, _) in sorted(selected.items())
        },
        "missing_retailers": sorted(set(EXPECTED) - set(selected)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--allow-missing", action="store_true", help="persistencia parcial: ensambla sólo cadenas aceptadas")
    args = parser.parse_args()
    result = assemble(args.input, args.output, run_id=args.run_id, allow_missing=args.allow_missing)
    text = json.dumps(result, ensure_ascii=False, sort_keys=True)
    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssemblyError as exc:
        raise SystemExit(str(exc)) from exc
