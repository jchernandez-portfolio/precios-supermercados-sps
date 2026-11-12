#!/usr/bin/env python3
"""Cola pública del panel de revisión de tamaños (``cola.json``; sólo lectura).

Recalcula los grupos GTIN con la resolución de producción (igual que
``medir_reglas_identidad.py``) y lista los que necesitan que una persona fije el
tamaño oficial:

- ``empate``: el grupo quedó bloqueado (``review_required``) por tamaños
  distintos entre cadenas;
- ``atipico``: el grupo compara, pero una o más ofertas quedaron fuera por
  tamaño.

Omite los GTIN que ya tienen fila en ``size_decisions``. Sólo publica datos que
ya son públicos en el catálogo (cadena, nombre y tamaño publicados).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from auditar_identidad_productos import ReadOnly  # noqa: E402
from medir_reglas_identidad import (  # noqa: E402
    SIZE_REASONS,
    build_profiles,
    fetch_all_products,
    fetch_spec_attributes,
    resolve_groups,
)
from precios_supermercados.product_homologation import ProductProfile  # noqa: E402
from precios_supermercados.product_homologation_persistence import apply_source_spec_attributes  # noqa: E402
from precios_supermercados.size_decisions import TABLE_NAME as DECISIONS_TABLE  # noqa: E402

QUEUE_SCHEMA = "rpi-size-queue/v1"
QUEUE_FILE = "cola.json"
MAX_ITEMS = 1500
UNIT = {"mass_g": "g", "volume_ml": "ml", "ounce": "oz", "count": "unit"}
_CENT = Decimal("0.01")


def _amount(value: Decimal) -> str:
    """2 decimales como máximo y sin ceros de sobra: 997.903214 → 997.9; 150.00 → 150."""
    rounded = value.quantize(_CENT, rounding=ROUND_HALF_UP).normalize()
    return format(rounded, "f")


def source_code(profile: ProductProfile) -> str | None:
    """El código de barras tal como lo publica el súper (cada súper busca por su forma)."""
    digits = "".join(char for char in str(profile.record.barcode or "") if char.isdigit())
    return digits if 8 <= len(digits) <= 14 else None


def decided_gtins(db: ReadOnly) -> set[str]:
    present = db.query("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (DECISIONS_TABLE,))
    if not present:
        return set()
    return {str(row[0]) for row in db.query(f"SELECT canonical_gtin FROM {DECISIONS_TABLE}")}


def offer_size(profile: ProductProfile) -> dict[str, object] | None:
    value = profile.presentation
    if value is None or value.dimension not in UNIT:
        return None
    unit = UNIT[value.dimension]
    total = _amount(value.total_base)
    unit_amount = _amount(value.unit_amount_base)
    text = f"{value.pack_count}×{unit_amount} {unit}" if value.pack_count > 1 else f"{total} {unit}"
    return {"value": total, "unit": unit, "text": text}


def queue_items(groups: Sequence[Mapping[str, object]], skip: set[str]) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for group in groups:
        gtin = str(group["gtin"])
        if gtin in skip:
            continue
        excluded: Mapping[str, frozenset[str]] = group["excluded"]
        size_excluded = {source for source, reasons in excluded.items() if reasons & SIZE_REASONS}
        if group["status"] == "review_required" and group["group_reasons"] & SIZE_REASONS:
            kind = "empate"
        elif group["status"] == "ready" and size_excluded:
            kind = "atipico"
        else:
            continue
        members: Sequence[ProductProfile] = sorted(
            group["members"], key=lambda member: (member.record.supermarket_id, member.record.source_record_id)
        )
        offers = []
        for member in members:
            size = offer_size(member)
            offers.append({
                "store": member.record.supermarket_id,
                "name": member.record.source_name,
                "code": source_code(member),
                "size": None if size is None else size["text"],
                "value": None if size is None else size["value"],
                "unit": None if size is None else size["unit"],
                "excluded": member.record.source_record_id in size_excluded,
            })
        kept = [member for member in members if member.record.source_record_id not in size_excluded]
        title = max((kept or members), key=lambda member: len(member.record.source_name or "")).record.source_name
        items.append({"gtin": gtin, "kind": kind, "name": title, "offers": offers})
    items.sort(key=lambda item: (item["kind"] != "empate", -len(item["offers"]), item["gtin"]))
    return items[:MAX_ITEMS]


def build_queue(db) -> dict[str, object]:
    reader = ReadOnly(db)
    products = fetch_all_products(reader)
    specs = fetch_spec_attributes(reader)
    if specs is not None:
        products, _ = apply_source_spec_attributes(products, specs)
    profiles = build_profiles([record for _, record in products])
    skip = decided_gtins(reader)
    items = queue_items(resolve_groups(profiles), skip)
    return {
        "schema": QUEUE_SCHEMA,
        "generated_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "decided": len(skip),
        "counts": {
            "empate": sum(item["kind"] == "empate" for item in items),
            "atipico": sum(item["kind"] == "atipico" for item in items),
        },
        "items": items,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    from exportar_modelo_analitico import TursoBackend  # noqa: PLC0415

    url = os.environ.get("TURSO_DATABASE_URL", "")
    token = os.environ.get("TURSO_AUTH_TOKEN", "")
    if not url.strip() or not token.strip():
        raise SystemExit("turso_credentials_missing")
    queue = build_queue(TursoBackend(url, token))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / QUEUE_FILE).write_text(
        json.dumps(queue, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    print(json.dumps({"decided": queue["decided"], **queue["counts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
