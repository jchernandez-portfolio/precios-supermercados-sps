#!/usr/bin/env python3
"""Ejecuta y valida una sola adquisición productiva del corte diario.

Este entrypoint no implementa scraping nuevo: invoca los scrapers operativos ya
aceptados para una única cadena, valida sus snapshots con los contratos existentes
y sólo entonces escribe el marcador que permite reutilizar ese handoff.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SRC = ROOT / "src"
for path in (SCRIPTS, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from actualizar_mvp_sqlite_la_colonia import validate_snapshot_bytes  # noqa: E402
from actualizar_mvp_turso_comisariato_los_andes import (  # noqa: E402
    validate_snapshot_bytes as validate_los_andes,
)
from actualizar_mvp_turso_paiz import validate_snapshot_bytes as validate_paiz  # noqa: E402
from ensamblar_adquisicion_diaria import HANDOFF_FILE, HANDOFF_SCHEMA  # noqa: E402

RETAILERS = (
    "la_colonia",
    "los_andes",
    "paiz",
    "colonial",
    "walmart",
    "pricesmart",
)

COMMANDS: dict[str, tuple[tuple[str, ...], ...]] = {
    "la_colonia": (
        (
            sys.executable,
            "scripts/obtener_catalogo_sps_la_colonia_operativo_v2.py",
            "--live-read-only",
            "--allow-full-catalog",
            "--page-size",
            "50",
            "--delay-seconds",
            "1.5",
            "--output",
            "run-artifacts/sps/full-catalog.json",
            "--csv-output",
            "run-artifacts/sps/full-catalog.csv",
        ),
        (
            sys.executable,
            "scripts/obtener_catalogo_tgu_la_colonia_operativo.py",
            "--live-read-only",
            "--allow-full-catalog",
            "--page-size",
            "50",
            "--delay-seconds",
            "1.5",
            "--output",
            "run-artifacts/tgu/full-catalog.json",
            "--csv-output",
            "run-artifacts/tgu/full-catalog.csv",
        ),
    ),
    "los_andes": (
        (
            sys.executable,
            "scripts/obtener_catalogo_comisariato_los_andes_operativo.py",
            "--live-read-only",
            "--allow-full-catalog",
            "--delay-seconds",
            "1.0",
            "--max-retries",
            "1",
            "--output",
            "run-artifacts/los-andes/full-catalog.json",
            "--raw-directory",
            "run-artifacts/los-andes/raw",
            "--evidence-output",
            "run-artifacts/los-andes/evidence.json",
        ),
    ),
    "paiz": (
        (
            sys.executable,
            "scripts/obtener_catalogo_paiz_operativo.py",
            "--live-read-only",
            "--allow-full-catalog",
            "--delay-seconds",
            "1.0",
            "--max-requests",
            "500",
            "--output-directory",
            "run-artifacts/paiz",
            "--raw-directory",
            "run-artifacts/paiz/raw",
            "--evidence-output",
            "run-artifacts/paiz/evidence.json",
        ),
    ),
    "colonial": (
        (
            sys.executable,
            "scripts/obtener_catalogo_colonial_operativo.py",
            "--live-read-only",
            "--allow-full-catalog",
            "--max-requests",
            "450",
            "--deadline-seconds",
            "1200",
            "--output",
            "run-artifacts/colonial",
        ),
    ),
    "walmart": (
        (
            sys.executable,
            "scripts/obtener_catalogo_walmart_operativo.py",
            "--live-read-only",
            "--allow-full-catalog",
            "--delay-seconds",
            "1.0",
            "--max-requests",
            "700",
            "--output-directory",
            "run-artifacts/walmart",
            "--raw-directory",
            "run-artifacts/walmart/raw",
            "--evidence-output",
            "run-artifacts/walmart/evidence.json",
        ),
    ),
    "pricesmart": (
        (
            sys.executable,
            "scripts/obtener_catalogo_pricesmart_operativo.py",
            "--live-read-only",
            "--allow-full-catalog",
            "--delay-seconds",
            "0.5",
            "--max-requests",
            "80",
            "--output-directory",
            "run-artifacts/pricesmart",
            "--raw-directory",
            "run-artifacts/pricesmart/raw",
            "--evidence-output",
            "run-artifacts/pricesmart/evidence.json",
        ),
    ),
}


class AcquisitionError(RuntimeError):
    pass


def _require_complete(snapshot: dict[str, object], *, exact_count: bool) -> None:
    if snapshot.get("catalog_complete") is not True:
        raise AcquisitionError("catalog_not_complete")
    if snapshot.get("location_verified_same_run") is not True:
        raise AcquisitionError("location_not_verified_same_run")
    if exact_count and snapshot.get("catalog_products_reported") != snapshot.get("unique_products_extracted"):
        raise AcquisitionError("catalog_product_count_mismatch")


def validate_retailer(retailer: str, artifact_root: Path) -> None:
    if retailer == "la_colonia":
        for city in ("sps", "tgu"):
            snapshot = validate_snapshot_bytes((artifact_root / city / "full-catalog.json").read_bytes())
            _require_complete(snapshot, exact_count=False)
        return
    if retailer == "los_andes":
        snapshot = validate_los_andes((artifact_root / "los-andes" / "full-catalog.json").read_bytes())
        _require_complete(snapshot, exact_count=True)
        return
    if retailer == "paiz":
        for location in ("multiplaza", "proceres"):
            snapshot = validate_paiz((artifact_root / "paiz" / f"snapshot-paiz-{location}.json").read_bytes())
            _require_complete(snapshot, exact_count=True)
        return

    files = {
        "colonial": ("colonial/full-catalog.json",),
        "walmart": (
            "walmart/snapshot-walmart-sps.json",
            "walmart/snapshot-walmart-tgu-ffaa.json",
            "walmart/snapshot-walmart-tgu-el-sauce.json",
        ),
        "pricesmart": (
            "pricesmart/snapshot-pricesmart-sps.json",
            "pricesmart/snapshot-pricesmart-tgu.json",
        ),
    }[retailer]
    for relative in files:
        snapshot = validate_snapshot_bytes((artifact_root / relative).read_bytes(), supermarket_id=retailer)
        _require_complete(snapshot, exact_count=True)


def prepare_artifact_root(path: Path) -> None:
    if path.exists():
        if path.is_symlink() or not path.is_dir():
            raise AcquisitionError("run_artifacts_not_safe_directory")
        shutil.rmtree(path)
    path.mkdir(parents=True)


def write_handoff(retailer: str, artifact_root: Path, *, run_id: str, run_attempt: int) -> dict[str, object]:
    if retailer not in RETAILERS:
        raise AcquisitionError("retailer_not_allowed")
    if not run_id.isdigit() or int(run_id) < 1:
        raise AcquisitionError("github_run_id_invalid")
    if run_attempt < 1:
        raise AcquisitionError("github_run_attempt_invalid")
    payload = {
        "schema": HANDOFF_SCHEMA,
        "retailer": retailer,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "accepted": True,
    }
    (artifact_root / HANDOFF_FILE).write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def run_retailer(retailer: str, *, artifact_root: Path, run_id: str, run_attempt: int) -> dict[str, object]:
    if retailer not in RETAILERS:
        raise AcquisitionError("retailer_not_allowed")
    prepare_artifact_root(artifact_root)
    for command in COMMANDS[retailer]:
        subprocess.run(command, cwd=ROOT, check=True)
    validate_retailer(retailer, artifact_root)
    return write_handoff(
        retailer,
        artifact_root,
        run_id=run_id,
        run_attempt=run_attempt,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--retailer", required=True, choices=RETAILERS)
    args = parser.parse_args()
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    raw_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
    try:
        run_attempt = int(raw_attempt)
    except ValueError as exc:
        raise AcquisitionError("github_run_attempt_invalid") from exc
    result = run_retailer(
        args.retailer,
        artifact_root=ROOT / "run-artifacts",
        run_id=run_id,
        run_attempt=run_attempt,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AcquisitionError, OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc)) from exc
