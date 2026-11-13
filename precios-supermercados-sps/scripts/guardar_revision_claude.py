#!/usr/bin/env python3
"""Guarda una tanda revisada por Claude en sesión, SÓLO con las filas que aprobó una persona.

Lee ``revision-claude/resultados.json`` (lo que propuso Claude por código, con
``aprobado: true`` en las filas que el dueño aprobó), arma el envío
``rpi-size-decisions/v1`` (nota: lo leído por súper + la razón), lo valida con las
mismas reglas que el workflow y, con ``--enviar``, lanza el workflow de decisiones con
``fuente=claude_sesion`` (``gh api``; el workflow sólo acepta la cuenta del dueño y
guarda ``engine:claude_sesion`` con "aprobado por <cuenta>" en la nota). Después marca
todos los códigos de la tanda como vistos (también "no legible" y "a revisión humana")
para no repetirlos.

    python scripts/guardar_revision_claude.py            # sólo valida y muestra
    python scripts/guardar_revision_claude.py --enviar   # tras la aprobación en el chat
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from precios_supermercados.size_decisions import (  # noqa: E402
    MAX_DECISIONS,
    MAX_PAYLOAD_CHARS,
    PAYLOAD_SCHEMA,
    parse_payload,
)
from preparar_revision_claude import BATCH_FILE, OUT_DIR, SEEN_FILE, load_seen, store_name  # noqa: E402

RESULTS_FILE = "resultados.json"
SEND_FILE = "envio.json"
REPOSITORY = "jchernandez-portfolio/precios-supermercados-sps"
WORKFLOW = "precios-supermercados-sps-size-decisions.yml"
PROPOSALS = ("size", "different_products", "no_legible", "revision_humana")
NOTE_CHARS = 165  # el workflow agrega " · aprobado por <cuenta>" hasta 200


def note_for(row: Mapping[str, object]) -> str:
    readings = row.get("lecturas") if isinstance(row.get("lecturas"), Mapping) else {}
    read = "; ".join(f"{store_name(store)} {text}" for store, text in readings.items() if text)
    reason = " ".join(str(row.get("razon") or "").split())
    text = "Claude (sesión): " + " — ".join(part for part in (read, reason) if part)
    return text[:NOTE_CHARS]


def build(results: Mapping[str, object], batch: Mapping[str, object]) -> tuple[dict[str, object], list[str]]:
    """Filas aprobadas → envío; devuelve también todos los códigos revisados (para marcarlos vistos)."""
    in_batch = {str(item.get("gtin")) for item in batch.get("items") or () if isinstance(item, Mapping)}
    decisions: list[dict[str, object]] = []
    reviewed: list[str] = []
    for row in results.get("items") or ():
        if not isinstance(row, Mapping):
            raise SystemExit("resultado_invalido")
        gtin = str(row.get("gtin") or "")
        proposal = row.get("propuesta")
        if gtin not in in_batch or proposal not in PROPOSALS:
            raise SystemExit(f"resultado_invalido:{gtin}")
        reviewed.append(gtin)
        if row.get("aprobado") is not True or proposal not in ("size", "different_products"):
            continue
        decision: dict[str, object] = {"gtin": gtin, "decision": proposal, "note": note_for(row)}
        if proposal == "size":
            decision.update(value=str(row.get("value")), unit=str(row.get("unit")))
        decisions.append(decision)
    if len(decisions) > MAX_DECISIONS:
        raise SystemExit("demasiadas_decisiones")
    return {"schema": PAYLOAD_SCHEMA, "decisions": decisions}, reviewed


def mark_seen(out_dir: Path, results: Mapping[str, object], reviewed: Sequence[str], now: str) -> None:
    seen = load_seen(out_dir / SEEN_FILE)
    by_gtin = {str(row.get("gtin")): row for row in results.get("items") or () if isinstance(row, Mapping)}
    for gtin in reviewed:
        row = by_gtin[gtin]
        state = row.get("propuesta") if row.get("aprobado") is True else "descartado"
        seen[gtin] = {"estado": state, "visto_utc": now}
    (out_dir / SEEN_FILE).write_text(json.dumps({"vistos": dict(sorted(seen.items()))}, ensure_ascii=False,
                                                indent=1) + "\n", encoding="utf-8")


def send(path: Path) -> None:
    subprocess.run(  # noqa: S603 - comando fijo, el JSON va como archivo
        ["gh", "api", "--method", "POST", f"repos/{REPOSITORY}/actions/workflows/{WORKFLOW}/dispatches",
         "-f", "ref=main", "-f", "inputs[fuente]=claude_sesion", "-F", f"inputs[decisions]=@{path}"],
        check=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, default=OUT_DIR)
    parser.add_argument("--enviar", action="store_true", help="lanza el workflow (sólo con aprobación del dueño)")
    args = parser.parse_args(argv)
    results = json.loads((args.dir / RESULTS_FILE).read_text(encoding="utf-8"))
    batch = json.loads((args.dir / BATCH_FILE).read_text(encoding="utf-8"))
    document, reviewed = build(results, batch)
    text = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
    if len(text) > MAX_PAYLOAD_CHARS:
        raise SystemExit("envio_demasiado_grande")
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if document["decisions"]:
        parse_payload(text, decided_by="claude_sesion", decided_at_utc=now, engine=True)  # mismas reglas
    (args.dir / SEND_FILE).write_text(text + "\n", encoding="utf-8")
    print(json.dumps({"aprobadas": len(document["decisions"]), "revisadas": len(reviewed),
                      "caracteres": len(text)}, ensure_ascii=False))
    if args.enviar:
        if document["decisions"]:
            send(args.dir / SEND_FILE)
        mark_seen(args.dir, results, reviewed, now)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
