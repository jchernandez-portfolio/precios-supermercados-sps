#!/usr/bin/env python3
"""Importa decisiones humanas "Mismo"/"Distinto" contra productos maestros.

Entrada: CSV simple (UTF-8) con encabezado. Columnas aceptadas:

- ``review_id`` (de ``review-queue.csv``; requiere ``--queue``) **o** el par
  ``product_key`` (``supermarket:product_id``) / ``product_id`` +
  ``master_product_id``;
- ``decision``: ``Mismo`` | ``Distinto`` (también ``same``/``different``,
  ``sí``/``no``); vacío o ``pendiente`` se ignora;
- ``reviewer`` (o ``--reviewer`` por defecto) y ``note`` opcional.

Efectos (sólo con ``--apply``; por defecto es dry-run y no escribe):

- ``Mismo`` → vínculo ``manual_review`` activo (``decided_by = human:<id>``).
  Reemplaza un vínculo manual previo del producto o el vínculo GTIN de su
  propio maestro de un solo miembro (``single_source``); nunca reemplaza un
  vínculo GTIN de un grupo multi-cadena ni uno de registro (eso es un
  conflicto de identidad, no una revisión).
  Respeta ≤ 1 vínculo activo por cadena y maestro. Borra un rechazo previo
  del mismo par.
- ``Distinto`` → fila en ``master_link_rejections`` (el par nunca se vuelve a
  proponer); si existía un vínculo manual activo del par pasa a ``rejected``.

Los maestros afectados se marcan en ``master_sync_dirty`` para que el refresco
diario recalcule su registro golden. Todas las escrituras van en un único lote
atómico. Lecturas: sólo por llave primaria/índice de los productos y maestros
citados en el CSV.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sqlite3
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from precios_supermercados.product_master import (  # noqa: E402
    DIRTY_TABLE,
    GTIN_LINK_METHODS,
    LINK_INSERT_SQL,
    LINK_TABLE,
    REJECTION_TABLE,
    MasterLink,
    MasterStore,
    SQLiteMasterStore,
    ensure_master_schema,
    existing_schema_objects,
    is_master_id,
    read_active_links_by_master,
    read_active_links_by_product,
    read_masters_by_id,
    SCHEMA_OBJECTS,
)

SAME = frozenset({"mismo", "same", "same_product", "si", "sí", "yes", "igual"})
DIFFERENT = frozenset({"distinto", "different", "different_products", "no", "diferente"})
SKIP = frozenset({"", "pendiente", "pending", "skip", "duda", "unsure"})


class DecisionImportError(ValueError):
    """El CSV de decisiones no se puede aplicar."""


def _fold(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").strip().casefold()
    return text


@dataclass(frozen=True, slots=True)
class Decision:
    row_number: int
    product_id: int
    supermarket_id: str | None
    master_product_id: str
    same: bool
    reviewer: str
    note: str | None
    review_id: str | None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_queue(path: Path) -> dict[str, tuple[str, str]]:
    """``review_id`` → (product_key, master_product_id) desde la cola JSONL o CSV."""

    result: dict[str, tuple[str, str]] = {}
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        for line in text.splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if "_metadata" in row:
                continue
            result[str(row["review_id"])] = (str(row["product_key"]), str(row["master_product_id"]))
    else:
        for row in csv.DictReader(io.StringIO(text)):
            result[str(row["review_id"])] = (str(row["product_key"]), str(row["master_product_id"]))
    return result


def parse_decisions(
    text: str,
    *,
    queue: Mapping[str, tuple[str, str]] | None = None,
    default_reviewer: str | None = None,
) -> tuple[list[Decision], list[dict[str, object]]]:
    decisions: list[Decision] = []
    errors: list[dict[str, object]] = []
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if reader.fieldnames is None or "decision" not in {(_fold(name)) for name in reader.fieldnames}:
        raise DecisionImportError("decision_csv_header_missing_decision")
    for number, raw in enumerate(reader, start=2):
        row = {_fold(key): (value or "").strip() for key, value in raw.items() if key is not None}
        decision = _fold(row.get("decision", ""))
        if decision in SKIP:
            continue
        if decision not in SAME | DIFFERENT:
            errors.append({"row": number, "error": "decision_invalid", "value": row.get("decision")})
            continue
        reviewer = row.get("reviewer") or (default_reviewer or "")
        if not reviewer.strip():
            errors.append({"row": number, "error": "reviewer_missing"})
            continue
        product_key = row.get("product_key") or ""
        master_id = row.get("master_product_id") or ""
        review = row.get("review_id") or None
        if review:
            if queue is None or review not in queue:
                errors.append({"row": number, "error": "review_id_unknown", "value": review})
                continue
            queued_key, queued_master = queue[review]
            if (product_key and product_key != queued_key) or (master_id and master_id != queued_master):
                errors.append({"row": number, "error": "review_id_mismatch", "value": review})
                continue
            product_key, master_id = queued_key, queued_master
        supermarket: str | None = None
        product_text = row.get("product_id") or ""
        if product_key:
            supermarket, _, product_text = product_key.partition(":")
        if not product_text.isdigit() or int(product_text) <= 0:
            errors.append({"row": number, "error": "product_id_invalid", "value": product_key or product_text})
            continue
        if not is_master_id(master_id):
            errors.append({"row": number, "error": "master_product_id_invalid", "value": master_id})
            continue
        decisions.append(
            Decision(
                row_number=number,
                product_id=int(product_text),
                supermarket_id=supermarket or (row.get("supermarket_id") or None),
                master_product_id=master_id,
                same=decision in SAME,
                reviewer=reviewer.strip(),
                note=row.get("note") or None,
                review_id=review,
            )
        )
    return decisions, errors


def plan_import(
    store: MasterStore,
    decisions: Sequence[Decision],
    *,
    now: str,
) -> tuple[list[tuple[str, str, tuple[object, ...]]], dict[str, object], list[dict[str, object]]]:
    """Valida contra el estado persistido y arma un único lote atómico."""

    errors: list[dict[str, object]] = []
    by_pair: dict[tuple[int, str], Decision] = {}
    same_by_product: dict[int, Decision] = {}
    for decision in decisions:
        pair = (decision.product_id, decision.master_product_id)
        previous = by_pair.get(pair)
        if previous is not None:
            if previous.same != decision.same:
                errors.append({"row": decision.row_number, "error": "conflicting_decisions_for_pair", "product_id": decision.product_id})
            continue
        if decision.same:
            other = same_by_product.get(decision.product_id)
            if other is not None:
                errors.append({"row": decision.row_number, "error": "product_same_as_two_masters", "product_id": decision.product_id})
                continue
            same_by_product[decision.product_id] = decision
        by_pair[pair] = decision
    if errors:
        return [], {}, errors

    product_ids = sorted({decision.product_id for decision in decisions})
    supermarkets: dict[int, str] = {}
    for start in range(0, len(product_ids), 400):
        chunk = product_ids[start : start + 400]
        placeholders = ",".join("?" for _ in chunk)
        for product_id, supermarket_id in store.query(
            f"SELECT product_id,supermarket_id FROM products WHERE product_id IN ({placeholders})", tuple(chunk)
        ):
            supermarkets[int(product_id)] = str(supermarket_id)  # type: ignore[arg-type]
    master_ids = {decision.master_product_id for decision in decisions}
    masters = read_masters_by_id(store, master_ids)
    links_by_product = read_active_links_by_product(store, product_ids)
    current_masters = {link.master_product_id for link in links_by_product.values()}
    links_by_master = read_active_links_by_master(store, master_ids | current_masters)
    members_per_master: dict[str, int] = {}
    for link in links_by_master.values():
        members_per_master[link.master_product_id] = members_per_master.get(link.master_product_id, 0) + 1
    rejected: set[tuple[int, str]] = set()
    for start in range(0, len(product_ids), 400):
        chunk = product_ids[start : start + 400]
        placeholders = ",".join("?" for _ in chunk)
        for product_id, master_id in store.query(
            f"SELECT product_id,master_product_id FROM {REJECTION_TABLE} WHERE product_id IN ({placeholders})",
            tuple(chunk),
        ):
            rejected.add((int(product_id), str(master_id)))  # type: ignore[arg-type]

    slots: dict[tuple[str, str], int] = {
        (link.master_product_id, link.supermarket_id): link.product_id
        for link in links_by_master.values()
        if link.master_product_id in master_ids
    }
    steps: list[tuple[str, str, tuple[object, ...]]] = [("begin", "BEGIN IMMEDIATE", ())]
    inserts: list[MasterLink] = []
    dirty: set[str] = set()
    counts = {
        "same_linked": 0,
        "same_already_linked": 0,
        "manual_links_superseded": 0,
        "gtin_single_member_links_superseded": 0,
        "different_rejected": 0,
        "links_rejected": 0,
        "rejections_cleared": 0,
    }
    for decision in sorted(by_pair.values(), key=lambda item: item.row_number):
        supermarket = supermarkets.get(decision.product_id)
        if supermarket is None:
            errors.append({"row": decision.row_number, "error": "product_not_found", "product_id": decision.product_id})
            continue
        if decision.supermarket_id is not None and decision.supermarket_id != supermarket:
            errors.append({"row": decision.row_number, "error": "product_supermarket_mismatch", "product_id": decision.product_id})
            continue
        master = masters.get(decision.master_product_id)
        if master is None or master.status != "active":
            errors.append({"row": decision.row_number, "error": "master_not_active", "master_product_id": decision.master_product_id})
            continue
        current = links_by_product.get(decision.product_id)
        decided_by = f"human:{decision.reviewer}"
        evidence = {"source": "csv_review", "review_id": decision.review_id, "note": decision.note}
        if decision.same:
            if current is not None and current.master_product_id == decision.master_product_id:
                counts["same_already_linked"] += 1
                continue
            # Un vínculo GTIN de un maestro de un solo miembro (single_source) sí
            # se reemplaza: el maestro viejo queda sin miembros y se retira.
            single_member_gtin = (
                current is not None
                and current.link_method in GTIN_LINK_METHODS
                and members_per_master.get(current.master_product_id, 0) == 1
            )
            if current is not None and current.link_method not in {"manual_review", "engine_auto"} and not single_member_gtin:
                errors.append(
                    {
                        "row": decision.row_number,
                        "error": "product_has_system_link",
                        "link_method": current.link_method,
                        "master_product_id": current.master_product_id,
                    }
                )
                continue
            holder = slots.get((decision.master_product_id, supermarket))
            if holder is not None and holder != decision.product_id:
                errors.append({"row": decision.row_number, "error": "retailer_slot_taken", "held_by_product_id": holder})
                continue
            if current is not None:
                steps.append(
                    (
                        f"supersede_{decision.row_number}",
                        f"UPDATE {LINK_TABLE} SET status='superseded', status_changed_at_utc=? WHERE link_id=? AND status='active'",
                        (now, current.link_id),
                    )
                )
                slots.pop((current.master_product_id, supermarket), None)
                dirty.add(current.master_product_id)
                counts["gtin_single_member_links_superseded" if single_member_gtin else "manual_links_superseded"] += 1
            if (decision.product_id, decision.master_product_id) in rejected:
                steps.append(
                    (
                        f"clear_rejection_{decision.row_number}",
                        f"DELETE FROM {REJECTION_TABLE} WHERE product_id=? AND master_product_id=?",
                        (decision.product_id, decision.master_product_id),
                    )
                )
                counts["rejections_cleared"] += 1
            slots[(decision.master_product_id, supermarket)] = decision.product_id
            inserts.append(
                MasterLink(
                    product_id=decision.product_id,
                    supermarket_id=supermarket,
                    master_product_id=decision.master_product_id,
                    link_method="manual_review",
                    decided_by=decided_by,
                    evidence=evidence,
                    decided_at_utc=now,
                )
            )
            dirty.add(decision.master_product_id)
            counts["same_linked"] += 1
        else:
            if current is not None and current.master_product_id == decision.master_product_id:
                if current.link_method in GTIN_LINK_METHODS or current.link_method == "reviewed_decision":
                    errors.append(
                        {
                            "row": decision.row_number,
                            "error": "gtin_link_requires_identity_review",
                            "link_method": current.link_method,
                        }
                    )
                    continue
                steps.append(
                    (
                        f"reject_link_{decision.row_number}",
                        f"UPDATE {LINK_TABLE} SET status='rejected', status_changed_at_utc=? WHERE link_id=? AND status='active'",
                        (now, current.link_id),
                    )
                )
                dirty.add(decision.master_product_id)
                counts["links_rejected"] += 1
            steps.append(
                (
                    f"rejection_{decision.row_number}",
                    f"INSERT INTO {REJECTION_TABLE}(product_id,supermarket_id,master_product_id,decided_by,decided_at_utc,note,evidence_json) "
                    "VALUES(?,?,?,?,?,?,?) ON CONFLICT(product_id,master_product_id) DO UPDATE SET "
                    "decided_by=excluded.decided_by,decided_at_utc=excluded.decided_at_utc,note=excluded.note,evidence_json=excluded.evidence_json",
                    (
                        decision.product_id,
                        supermarket,
                        decision.master_product_id,
                        decided_by,
                        now,
                        decision.note,
                        json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    ),
                )
            )
            counts["different_rejected"] += 1
    if inserts:
        payload = [
            {
                "product_id": link.product_id,
                "supermarket_id": link.supermarket_id,
                "master_product_id": link.master_product_id,
                "link_method": link.link_method,
                "confidence": None,
                "evidence_json": json.dumps(dict(link.evidence), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                "decided_by": link.decided_by,
                "decided_at_utc": now,
                "status": "active",
                "status_changed_at_utc": None,
                "link_hash": link.link_hash,
            }
            for link in inserts
        ]
        steps.append(("insert_links", LINK_INSERT_SQL, (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),)))
    for master_id in sorted(dirty):
        steps.append(
            (
                f"dirty_{master_id}",
                f"INSERT INTO {DIRTY_TABLE}(master_product_id,reason,marked_at_utc) VALUES(?,?,?) "
                "ON CONFLICT(master_product_id) DO UPDATE SET reason=excluded.reason, marked_at_utc=excluded.marked_at_utc",
                (master_id, "manual_review_import", now),
            )
        )
    steps.append(("commit", "COMMIT", ()))
    counts["dirty_masters"] = len(dirty)
    return steps, counts, errors


def import_decisions(
    store: MasterStore,
    text: str,
    *,
    queue: Mapping[str, tuple[str, str]] | None = None,
    default_reviewer: str | None = None,
    apply: bool = False,
    skip_invalid: bool = False,
    now: str | None = None,
) -> dict[str, object]:
    now = now or _utc_now()
    decisions, parse_errors = parse_decisions(text, queue=queue, default_reviewer=default_reviewer)
    if not set(SCHEMA_OBJECTS) <= existing_schema_objects(store):
        if not apply:
            raise DecisionImportError("master_schema_missing_run_daily_refresh_first")
        ensure_master_schema(store)
    steps, counts, plan_errors = plan_import(store, decisions, now=now)
    errors = parse_errors + plan_errors
    result: dict[str, object] = {
        "decisions": len(decisions),
        "errors": errors,
        "counts": counts,
        "dry_run": not apply,
        "written": False,
    }
    if errors and not skip_invalid:
        result["refused"] = "errors_present"
        return result
    if apply and len(steps) > 2:
        store.write(steps)
        result["written"] = True
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--sqlite", type=Path)
    source.add_argument("--turso", action="store_true")
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--queue", type=Path, help="review-queue.jsonl/.csv para resolver review_id")
    parser.add_argument("--reviewer", help="revisor por defecto si el CSV no trae columna reviewer")
    parser.add_argument("--apply", action="store_true", help="escribe (por defecto dry-run)")
    parser.add_argument("--skip-invalid", action="store_true", help="aplica las filas válidas aunque haya errores")
    args = parser.parse_args(argv)
    queue = load_queue(args.queue) if args.queue else None
    text = args.csv.read_text(encoding="utf-8")
    if args.sqlite is not None:
        if not args.sqlite.is_file():
            raise SystemExit("sqlite_file_missing")
        connection = sqlite3.connect(args.sqlite, isolation_level=None)
        try:
            result = import_decisions(
                SQLiteMasterStore(connection), text, queue=queue, default_reviewer=args.reviewer,
                apply=args.apply, skip_invalid=args.skip_invalid,
            )
        finally:
            connection.close()
    else:
        from backfill_homologacion_turso import TursoMasterStore  # noqa: PLC0415

        url = os.environ.get("TURSO_DATABASE_URL", "")
        token = os.environ.get("TURSO_AUTH_TOKEN", "")
        if not url.strip() or not token.strip():
            raise SystemExit("turso_credentials_missing")
        result = import_decisions(
            TursoMasterStore(url, token), text, queue=queue, default_reviewer=args.reviewer,
            apply=args.apply, skip_invalid=args.skip_invalid,
        )
    print(json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True))
    return 1 if result.get("refused") else 0


if __name__ == "__main__":
    raise SystemExit(main())
