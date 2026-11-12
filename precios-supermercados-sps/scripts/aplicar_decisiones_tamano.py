#!/usr/bin/env python3
"""Guarda las decisiones de tamaño del panel de revisión (``#/admin/revision``).

Entrada: el JSON que envía el panel (``rpi-size-decisions/v1``), por la variable
de entorno ``DECISIONS_JSON`` (el workflow nunca lo pega dentro del comando) o
por ``--payload-file``. ``--actor`` es la cuenta de GitHub que lanzó el envío.

Efectos (sólo con ``--apply``; por defecto es dry-run y no escribe):

- ``size_decisions``: una fila por GTIN (se reemplaza si ya existía).
- ``master_products.human_attributes_json``: con ``size`` fija
  ``net_content_value``/``net_content_unit`` del maestro de ese GTIN; con
  ``different_products`` quita esos dos atributos si venían de este panel.
- ``master_sync_dirty``: marca esos maestros para que el refresco los recalcule.

Todo en un único lote atómico. Falla cerrado: un dato inválido o un GTIN que no
existe en los perfiles rechaza el envío completo. El motor aplica la regla en
el siguiente refresco de homologación (el workflow lo lanza al terminar).
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from precios_supermercados.product_homologation_persistence import (  # noqa: E402
    TABLE_NAME as PROFILE_TABLE,
)
from precios_supermercados.product_master import (  # noqa: E402
    DIRTY_TABLE,
    MASTER_TABLE,
    MasterStore,
    SQLiteMasterStore,
    gtin_master_id,
)
from precios_supermercados.size_decisions import (  # noqa: E402
    TABLE_NAME,
    TABLE_SQL,
    SizeDecision,
    SizeDecisionError,
    human_attributes,
    parse_payload,
)

IN_CHUNK = 200
DIRTY_REASON = "size_decision"
UPSERT_SQL = (
    f"INSERT INTO {TABLE_NAME}(canonical_gtin,decision,net_content_value,net_content_unit,declared_value,"
    "declared_unit,note,decided_by,decided_at_utc) VALUES(?,?,?,?,?,?,?,?,?) "
    "ON CONFLICT(canonical_gtin) DO UPDATE SET decision=excluded.decision,"
    "net_content_value=excluded.net_content_value,net_content_unit=excluded.net_content_unit,"
    "declared_value=excluded.declared_value,declared_unit=excluded.declared_unit,note=excluded.note,"
    "decided_by=excluded.decided_by,decided_at_utc=excluded.decided_at_utc"
)
SET_SIZE_SQL = (
    f"UPDATE {MASTER_TABLE} SET human_attributes_json=json_set(human_attributes_json,"
    "'$.net_content_value',json(?),'$.net_content_unit',json(?)) WHERE master_product_id=?"
)
CLEAR_SIZE_SQL = (
    f"UPDATE {MASTER_TABLE} SET human_attributes_json=json_remove(human_attributes_json,"
    "'$.net_content_value','$.net_content_unit') WHERE master_product_id=? "
    f"AND json_extract(human_attributes_json,'$.net_content_value.source')='{TABLE_NAME}'"
)
DIRTY_SQL = (
    f"INSERT INTO {DIRTY_TABLE}(master_product_id,reason,marked_at_utc) VALUES(?,?,?) "
    "ON CONFLICT(master_product_id) DO UPDATE SET reason=excluded.reason, marked_at_utc=excluded.marked_at_utc"
)


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _tables(store: MasterStore) -> set[str]:
    rows = store.query(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?,?,?)",
        (PROFILE_TABLE, MASTER_TABLE, DIRTY_TABLE),
    )
    return {str(row[0]) for row in rows}


def _present(store: MasterStore, table: str, column: str, values: Sequence[str]) -> set[str]:
    found: set[str] = set()
    for start in range(0, len(values), IN_CHUNK):
        chunk = tuple(values[start : start + IN_CHUNK])
        placeholders = ",".join("?" for _ in chunk)
        rows = store.query(f"SELECT DISTINCT {column} FROM {table} WHERE {column} IN ({placeholders})", chunk)
        found.update(str(row[0]) for row in rows)
    return found


def plan(
    store: MasterStore, decisions: Sequence[SizeDecision], *, now: str
) -> tuple[list[tuple[str, str, tuple[object, ...]]], dict[str, int], list[str]]:
    tables = _tables(store)
    if PROFILE_TABLE not in tables:
        raise SizeDecisionError("homologation_profiles_missing")
    gtins = [decision.canonical_gtin for decision in decisions]
    known = _present(store, PROFILE_TABLE, "canonical_gtin", gtins)
    errors = [f"gtin_sin_perfiles:{gtin}" for gtin in gtins if gtin not in known]
    masters: set[str] = set()
    if {MASTER_TABLE, DIRTY_TABLE} <= tables:
        masters = _present(store, MASTER_TABLE, "master_product_id", [gtin_master_id(gtin) for gtin in gtins])

    steps: list[tuple[str, str, tuple[object, ...]]] = [
        ("begin", "BEGIN IMMEDIATE", ()),
        ("create_size_decisions", TABLE_SQL, ()),
    ]
    counts = {"size": 0, "different_products": 0, "masters_updated": 0}
    dirty: list[str] = []
    for decision in decisions:
        counts[decision.decision] += 1
        steps.append((
            f"upsert_{decision.canonical_gtin}",
            UPSERT_SQL,
            (
                decision.canonical_gtin,
                decision.decision,
                None if decision.net_content_value is None else format(decision.net_content_value, "f"),
                decision.net_content_unit,
                None if decision.declared_value is None else format(decision.declared_value, "f"),
                decision.declared_unit,
                decision.note,
                decision.decided_by,
                decision.decided_at_utc,
            ),
        ))
        master_id = gtin_master_id(decision.canonical_gtin)
        if master_id not in masters:
            continue
        attributes = human_attributes(decision)
        if attributes:
            steps.append((
                f"master_{master_id}",
                SET_SIZE_SQL,
                (_json(attributes["net_content_value"]), _json(attributes["net_content_unit"]), master_id),
            ))
        else:
            steps.append((f"master_{master_id}", CLEAR_SIZE_SQL, (master_id,)))
        dirty.append(master_id)
    for master_id in sorted(set(dirty)):
        steps.append((f"dirty_{master_id}", DIRTY_SQL, (master_id, DIRTY_REASON, now)))
    steps.append(("commit", "COMMIT", ()))
    counts["masters_updated"] = len(set(dirty))
    return steps, counts, errors


def apply_size_decisions(
    store: MasterStore, payload: str, *, actor: str, apply: bool = False, now: str | None = None
) -> dict[str, object]:
    now = now or _utc_now()
    decisions = parse_payload(payload, decided_by=actor, decided_at_utc=now)
    steps, counts, errors = plan(store, decisions, now=now)
    result: dict[str, object] = {
        "decisions": len(decisions),
        "gtins": [decision.canonical_gtin for decision in decisions],
        "counts": counts,
        "errors": errors,
        "dry_run": not apply,
        "written": False,
    }
    if errors:
        result["refused"] = "errors_present"
        return result
    if apply:
        store.write(steps)
        result["written"] = True
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--sqlite", type=Path)
    target.add_argument("--turso", action="store_true")
    parser.add_argument("--payload-file", type=Path, help="JSON del panel (si no, DECISIONS_JSON)")
    parser.add_argument("--actor", required=True, help="cuenta de GitHub que envió las decisiones")
    parser.add_argument("--summary", type=Path, help="escribe el resultado en este archivo JSON")
    parser.add_argument("--apply", action="store_true", help="escribe (por defecto dry-run)")
    args = parser.parse_args(argv)
    payload = args.payload_file.read_text(encoding="utf-8") if args.payload_file else os.environ.get("DECISIONS_JSON", "")
    try:
        if args.sqlite is not None:
            if not args.sqlite.is_file():
                raise SystemExit("sqlite_file_missing")
            connection = sqlite3.connect(args.sqlite, isolation_level=None)
            try:
                result = apply_size_decisions(SQLiteMasterStore(connection), payload, actor=args.actor, apply=args.apply)
            finally:
                connection.close()
        else:
            from backfill_homologacion_turso import TursoMasterStore  # noqa: PLC0415

            url = os.environ.get("TURSO_DATABASE_URL", "")
            token = os.environ.get("TURSO_AUTH_TOKEN", "")
            if not url.strip() or not token.strip():
                raise SystemExit("turso_credentials_missing")
            result = apply_size_decisions(TursoMasterStore(url, token), payload, actor=args.actor, apply=args.apply)
    except SizeDecisionError as error:
        result = {"refused": str(error), "written": False}
    text = json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True)
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 1 if result.get("refused") else 0


if __name__ == "__main__":
    raise SystemExit(main())
