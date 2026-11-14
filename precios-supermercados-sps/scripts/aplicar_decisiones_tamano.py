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
- ``size_offer_evidence`` (motor v2.10): si la decisión trae ``evidence`` o
  ``apply``, reemplaza la evidencia de foto de ese GTIN (una fila por súper y,
  con ``apply: "all"``, una fila de alcance ``*``). Toda decisión nueva borra el
  alcance anterior; sin ``evidence`` (panel actual) las lecturas guardadas siguen.

Todo en un único lote atómico. Falla cerrado: un dato inválido o un GTIN que no
existe en los perfiles rechaza el envío completo. El motor aplica la regla en
el siguiente refresco de homologación (el workflow lo lanza al terminar).
"""
from __future__ import annotations

import argparse
import json
import os
import re
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
    APPLY_ALL_STORE,
    APPLY_ALL_TEXT,
    APPROVED_ENGINES,
    ENGINE_ACTORS,
    EVIDENCE_TABLE,
    EVIDENCE_TABLE_SQL,
    MAX_DECISIONS,
    MAX_NOTE_CHARS,
    MAX_PAYLOAD_CHARS,
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
# Una decisión del motor nunca reemplaza una humana (ni aunque llegue al mismo tiempo).
ENGINE_UPSERT_SQL = UPSERT_SQL + f" WHERE {TABLE_NAME}.decided_by NOT LIKE 'human:%'"
# El maestro sólo se toca si la fila guardada es de este mismo autor.
_SAME_AUTHOR = f" AND EXISTS (SELECT 1 FROM {TABLE_NAME} WHERE canonical_gtin=? AND decided_by=?)"
SET_SIZE_SQL = (
    f"UPDATE {MASTER_TABLE} SET human_attributes_json=json_set(human_attributes_json,"
    "'$.net_content_value',json(?),'$.net_content_unit',json(?)) WHERE master_product_id=?" + _SAME_AUTHOR
)
CLEAR_SIZE_SQL = (
    f"UPDATE {MASTER_TABLE} SET human_attributes_json=json_remove(human_attributes_json,"
    "'$.net_content_value','$.net_content_unit') WHERE master_product_id=? "
    f"AND json_extract(human_attributes_json,'$.net_content_value.source')='{TABLE_NAME}'" + _SAME_AUTHOR
)
ENGINE_CHUNK = 300
# Evidencia de foto (v2.10): sólo si la fila guardada de size_decisions es de este mismo autor
# (una decisión de IA que la humana ya reemplazó no toca la evidencia).
_EVIDENCE_AUTHOR = f"EXISTS (SELECT 1 FROM {TABLE_NAME} WHERE canonical_gtin=? AND decided_by=?)"
CLEAR_SCOPE_SQL = (
    f"DELETE FROM {EVIDENCE_TABLE} WHERE canonical_gtin=? AND supermarket_id='{APPLY_ALL_STORE}' AND {_EVIDENCE_AUTHOR}"
)
CLEAR_EVIDENCE_SQL = f"DELETE FROM {EVIDENCE_TABLE} WHERE canonical_gtin=? AND {_EVIDENCE_AUTHOR}"
INSERT_EVIDENCE_SQL = (
    f"INSERT INTO {EVIDENCE_TABLE}(canonical_gtin,supermarket_id,value,unit,source,text,decided_at_utc) "
    f"SELECT ?,?,?,?,?,?,? WHERE {_EVIDENCE_AUTHOR}"
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


def human_decided(store: MasterStore, gtins: Sequence[str]) -> set[str]:
    if not store.query("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (TABLE_NAME,)):
        return set()
    found: set[str] = set()
    for start in range(0, len(gtins), IN_CHUNK):
        chunk = tuple(gtins[start : start + IN_CHUNK])
        placeholders = ",".join("?" for _ in chunk)
        rows = store.query(f"SELECT canonical_gtin FROM {TABLE_NAME} WHERE canonical_gtin IN ({placeholders}) "
                           "AND decided_by LIKE 'human:%'", chunk)
        found.update(str(row[0]) for row in rows)
    return found


def plan(
    store: MasterStore, decisions: Sequence[SizeDecision], *, now: str, engine: bool = False
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
        ("create_size_offer_evidence", EVIDENCE_TABLE_SQL, ()),
    ]
    counts = {"size": 0, "different_products": 0, "masters_updated": 0, "evidence_rows": 0, "apply_all": 0}
    dirty: list[str] = []
    for decision in decisions:
        counts[decision.decision] += 1
        steps.append((
            f"upsert_{decision.canonical_gtin}",
            ENGINE_UPSERT_SQL if engine else UPSERT_SQL,
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
        steps.extend(_evidence_steps(decision, counts))
        master_id = gtin_master_id(decision.canonical_gtin)
        if master_id not in masters:
            continue
        attributes = human_attributes(decision)
        if attributes:
            steps.append((
                f"master_{master_id}",
                SET_SIZE_SQL,
                (_json(attributes["net_content_value"]), _json(attributes["net_content_unit"]), master_id,
                 decision.canonical_gtin, decision.decided_by),
            ))
        else:
            steps.append((f"master_{master_id}", CLEAR_SIZE_SQL,
                          (master_id, decision.canonical_gtin, decision.decided_by)))
        dirty.append(master_id)
    for master_id in sorted(set(dirty)):
        steps.append((f"dirty_{master_id}", DIRTY_SQL, (master_id, DIRTY_REASON, now)))
    steps.append(("commit", "COMMIT", ()))
    counts["masters_updated"] = len(set(dirty))
    return steps, counts, errors


def _evidence_steps(decision: SizeDecision, counts: dict[str, int]) -> list[tuple[str, str, tuple[object, ...]]]:
    """Delta de ``size_offer_evidence`` de un GTIN (≤ 8 sentencias, sin lecturas)."""
    gtin, author = decision.canonical_gtin, decision.decided_by
    if not decision.evidence_given:
        return [(f"evidence_scope_{gtin}", CLEAR_SCOPE_SQL, (gtin, gtin, author))]
    steps: list[tuple[str, str, tuple[object, ...]]] = [(f"evidence_clear_{gtin}", CLEAR_EVIDENCE_SQL, (gtin, gtin, author))]
    for item in decision.evidence:
        steps.append((
            f"evidence_{gtin}_{item.supermarket_id}", INSERT_EVIDENCE_SQL,
            (gtin, item.supermarket_id, format(item.value, "f"), item.unit, item.source, item.text,
             item.decided_at_utc, gtin, author),
        ))
        counts["evidence_rows"] += 1
    if decision.apply == "all":
        source = author.removeprefix("engine:") if author.startswith("engine:") else "human"
        steps.append((
            f"evidence_{gtin}_apply_all", INSERT_EVIDENCE_SQL,
            (gtin, APPLY_ALL_STORE, None, None, source, APPLY_ALL_TEXT, decision.decided_at_utc, gtin, author),
        ))
        counts["apply_all"] += 1
    return steps


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


def apply_engine_decisions(
    store: MasterStore, payload: str, *, engine: str, apply: bool = False, now: str | None = None,
    approved_by: str | None = None,
) -> dict[str, object]:
    """Decisiones de un motor (p. ej. la IA): en lotes de 300, sin tocar las humanas.

    A diferencia del panel, un código que ya no está en los perfiles o que ya tiene
    decisión humana se omite (se informa) en vez de rechazar todo el envío.

    ``claude_sesion`` (Claude en una sesión de Claude Code) exige ``approved_by``: la
    cuenta que aprobó la tanda queda en la nota de cada decisión, y el envío respeta
    los mismos límites que el panel (300 decisiones, 60,000 caracteres).
    """
    now = now or _utc_now()
    if (engine in APPROVED_ENGINES) != bool(approved_by):
        raise SizeDecisionError("size_decisions_approval_invalid")
    if approved_by and not _ACTOR_NAME.fullmatch(approved_by):
        raise SizeDecisionError("size_decisions_actor_invalid")
    if approved_by and len(payload or "") > MAX_PAYLOAD_CHARS:
        raise SizeDecisionError("size_decisions_too_large")
    try:
        document = json.loads(payload or "{}")
    except ValueError as error:
        raise SizeDecisionError("size_decisions_json_invalid") from error
    items = document.get("decisions") if isinstance(document, dict) else None
    if not isinstance(items, list):
        raise SizeDecisionError("size_decisions_schema_invalid")
    if approved_by:
        if len(items) > MAX_DECISIONS:
            raise SizeDecisionError("size_decisions_too_many")
        items = [_with_approval(item, approved_by) for item in items]
    result: dict[str, object] = {"decisions": 0, "skipped_human": [], "skipped_unknown": [], "written": False,
                                 "dry_run": not apply, "batches": 0}
    for chunk in engine_chunks(document.get("schema"), items):
        decisions = parse_payload(chunk, decided_by=engine, decided_at_utc=now, engine=True)
        gtins = [decision.canonical_gtin for decision in decisions]
        human = human_decided(store, gtins)
        known = _present(store, PROFILE_TABLE, "canonical_gtin", gtins)
        keep = [d for d in decisions if d.canonical_gtin not in human and d.canonical_gtin in known]
        result["skipped_human"] += sorted(human)
        result["skipped_unknown"] += sorted(set(gtins) - known)
        if not keep:
            continue
        steps, _, errors = plan(store, keep, now=now, engine=True)
        if errors:
            raise SizeDecisionError("engine_plan_errors")
        result["decisions"] += len(keep)
        result["batches"] += 1
        if apply:
            store.write(steps)
            result["written"] = True
    return result


_ACTOR_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")


def engine_chunks(schema: object, items: Sequence[object]) -> list[str]:
    """Lotes de hasta 300 decisiones y ``MAX_PAYLOAD_CHARS`` caracteres (con la evidencia
    de foto, v2.10, 300 decisiones ya no caben en un solo envío)."""
    chunks: list[str] = []
    current: list[object] = []
    for item in items:
        candidate = [*current, item]
        text = json.dumps({"schema": schema, "decisions": candidate})
        if current and (len(candidate) > ENGINE_CHUNK or len(text) > MAX_PAYLOAD_CHARS):
            chunks.append(json.dumps({"schema": schema, "decisions": current}))
            current = [item]
        else:
            current = candidate
    if current:
        chunks.append(json.dumps({"schema": schema, "decisions": current}))
    return chunks


def _with_approval(item: object, approved_by: str) -> object:
    if not isinstance(item, dict):
        return item  # parse_payload lo rechaza
    suffix = f" · aprobado por {approved_by}"
    note = " ".join(str(item.get("note") or "Claude (sesión)").split())
    return {**item, "note": note[: MAX_NOTE_CHARS - len(suffix)] + suffix}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--sqlite", type=Path)
    target.add_argument("--turso", action="store_true")
    parser.add_argument("--payload-file", type=Path, help="JSON del panel (si no, DECISIONS_JSON)")
    author = parser.add_mutually_exclusive_group(required=True)
    author.add_argument("--actor", help="cuenta de GitHub que envió las decisiones (panel)")
    author.add_argument("--engine", choices=sorted(ENGINE_ACTORS), help="motor automático (p. ej. gemini_auto)")
    parser.add_argument("--approved-by", help="cuenta que aprobó una tanda de Claude en sesión (claude_sesion)")
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
                result = _run(SQLiteMasterStore(connection), payload, args)
            finally:
                connection.close()
        else:
            from backfill_homologacion_turso import TursoMasterStore  # noqa: PLC0415

            url = os.environ.get("TURSO_DATABASE_URL", "")
            token = os.environ.get("TURSO_AUTH_TOKEN", "")
            if not url.strip() or not token.strip():
                raise SystemExit("turso_credentials_missing")
            result = _run(TursoMasterStore(url, token), payload, args)
    except SizeDecisionError as error:
        result = {"refused": str(error), "written": False}
    text = json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True)
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 1 if result.get("refused") else 0


def _run(store: MasterStore, payload: str, args: argparse.Namespace) -> dict[str, object]:
    if args.engine:
        return apply_engine_decisions(store, payload, engine=args.engine, apply=args.apply,
                                      approved_by=args.approved_by)
    if args.approved_by:
        raise SizeDecisionError("size_decisions_approval_invalid")
    return apply_size_decisions(store, payload, actor=args.actor, apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
