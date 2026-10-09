#!/usr/bin/env python3
"""Valida todos los destinos Turso de una corrida diaria antes de escribir.

Con ``--assembly`` (evidencia de ``ensamblar_adquisicion_diaria.py``) sólo se
validan las cadenas aceptadas ese día (persistencia parcial, 2026-10-09).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import actualizar_mvp_turso_comisariato_los_andes as los_andes
import actualizar_mvp_turso_la_colonia as generic
import actualizar_mvp_turso_paiz as paiz
from actualizar_mvp_sqlite_la_colonia import SnapshotError, validate_snapshot_bytes


def _accept_previous(
    previous: dict[str, object] | None,
    *,
    location_id: str,
    digest: str,
) -> str:
    if previous is None:
        return "new"
    if previous == {
        "location_id": location_id,
        "run_status": "success",
        "sha": digest,
    }:
        return "exact_replay"
    raise SnapshotError(f"daily_preflight_run_conflict:{location_id}")


# Contextos (ubicaciones) que aporta cada cadena: 11 en total.
LOCATIONS_BY_RETAILER = {
    "la_colonia": 2,
    "los_andes": 1,
    "paiz": 2,
    "colonial": 1,
    "walmart": 3,
    "pricesmart": 2,
}
_GENERIC_RETAILER = {"la_colonia": "la_colonia", "colonial": "colonial", "walmart": "walmart", "pricesmart": "pricesmart"}


def accepted_retailers(assembly: Path | None) -> frozenset[str]:
    if assembly is None:
        return frozenset(LOCATIONS_BY_RETAILER)
    payload = json.loads(assembly.read_text(encoding="utf-8"))
    retailers = frozenset(payload.get("retailers", {}))
    if not retailers or not retailers <= set(LOCATIONS_BY_RETAILER):
        raise SnapshotError("daily_preflight_assembly_invalid")
    return retailers


def preflight(
    root: Path,
    *,
    database_url: str,
    auth_token: str,
    run_id: str,
    retailers: frozenset[str] = frozenset(LOCATIONS_BY_RETAILER),
) -> dict:
    if not database_url.strip() or not auth_token.strip():
        raise SnapshotError("turso_credentials_missing")
    if not run_id.strip():
        raise SnapshotError("run_id_missing")

    results: list[dict[str, str]] = []
    generic_inputs = (
        ("sps/full-catalog.json", "la_colonia", "sps"),
        ("tgu/full-catalog.json", "la_colonia", "tgu"),
        ("colonial/full-catalog.json", "colonial", "colonial"),
        ("walmart/snapshot-walmart-sps.json", "walmart", "walmart-sps"),
        ("walmart/snapshot-walmart-tgu-ffaa.json", "walmart", "walmart-tgu-ffaa"),
        (
            "walmart/snapshot-walmart-tgu-el-sauce.json",
            "walmart",
            "walmart-tgu-el-sauce",
        ),
        ("pricesmart/snapshot-pricesmart-sps.json", "pricesmart", "pricesmart-sps"),
        ("pricesmart/snapshot-pricesmart-tgu.json", "pricesmart", "pricesmart-tgu"),
    )
    for relative, supermarket, suffix in generic_inputs:
        if _GENERIC_RETAILER[supermarket] not in retailers:
            continue
        raw = (root / relative).read_bytes()
        snapshot = validate_snapshot_bytes(raw, supermarket_id=supermarket)
        location_id = str(snapshot["location_id"])
        digest = hashlib.sha256(raw).hexdigest()
        previous = generic._preflight(
            database_url,
            auth_token,
            location_id=location_id,
            run_id=f"{run_id}-{suffix}",
            supermarket_id=supermarket,
        )
        results.append(
            {
                "location_id": location_id,
                "run_id": f"{run_id}-{suffix}",
                "status": _accept_previous(
                    previous, location_id=location_id, digest=digest
                ),
            }
        )

    if "los_andes" in retailers:
        raw = (root / "los-andes/full-catalog.json").read_bytes()
        snapshot = los_andes.validate_snapshot_bytes(raw)
        location_id = str(snapshot["location_id"])
        digest = hashlib.sha256(raw).hexdigest()
        previous = los_andes._preflight(
            database_url, auth_token, run_id=f"{run_id}-los-andes"
        )
        results.append(
            {
                "location_id": location_id,
                "run_id": f"{run_id}-los-andes",
                "status": _accept_previous(
                    previous, location_id=location_id, digest=digest
                ),
            }
        )

    for name in (("multiplaza", "proceres") if "paiz" in retailers else ()):
        raw = (root / f"paiz/snapshot-paiz-{name}.json").read_bytes()
        snapshot = paiz.validate_snapshot_bytes(raw)
        location_id = str(snapshot["location_id"])
        digest = hashlib.sha256(raw).hexdigest()
        current_run_id = f"{run_id}-paiz-{name}"
        previous = paiz._preflight(
            database_url,
            auth_token,
            location_id=location_id,
            run_id=current_run_id,
        )
        results.append(
            {
                "location_id": location_id,
                "run_id": current_run_id,
                "status": _accept_previous(
                    previous, location_id=location_id, digest=digest
                ),
            }
        )

    expected = sum(LOCATIONS_BY_RETAILER[retailer] for retailer in retailers)
    if len(results) != expected or len({item["location_id"] for item in results}) != expected:
        raise SnapshotError("daily_preflight_location_set_invalid")
    return {
        "result": "ready",
        "location_count": len(results),
        "locations": results,
        "retailers": sorted(retailers),
        "missing_retailers": sorted(set(LOCATIONS_BY_RETAILER) - retailers),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--assembly", type=Path, help="evidencia del ensamblado: valida sólo cadenas aceptadas")
    args = parser.parse_args()
    try:
        result = preflight(
            args.artifact_root,
            database_url=os.environ.get("TURSO_DATABASE_URL", ""),
            auth_token=os.environ.get("TURSO_AUTH_TOKEN", ""),
            run_id=args.run_id,
            retailers=accepted_retailers(args.assembly),
        )
    except (OSError, SnapshotError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
