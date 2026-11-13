#!/usr/bin/env python3
"""Cola pública del panel de revisión de tamaños (``cola.json``; sólo lectura).

Recalcula los grupos GTIN con la resolución de producción (igual que
``medir_reglas_identidad.py``) y lista los que necesitan que una persona fije el
tamaño oficial:

- ``empate``: el grupo quedó bloqueado (``review_required``) por tamaños
  distintos entre cadenas;
- ``atipico``: el grupo compara, pero una o más ofertas quedaron fuera por
  tamaño.

Omite los GTIN que ya tienen fila en ``size_decisions`` (y publica cuáles decidió
la IA, para auditarlos, con sus ofertas en ``resueltas_motor``). Cada oferta lleva la foto de su tarjeta en el bucket R2
(``foto``: la misma que lee Gemini) y de qué súper vino esa foto. Sólo publica
datos que ya son públicos en el catálogo.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from auditar_identidad_productos import CATALOG_BASE, ReadOnly, load_cities  # noqa: E402
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
PHOTO_BASE = "https://pub-0dcb2204775c484dbe6681e659eee236.r2.dev/fotos/v2/"
PHOTO_INDEX_SCHEMA = "super-compras-product-images/v2"
_PHOTO_NAME = re.compile(r"[0-9a-f]{24}-[0-9a-f]{6,64}")


def _amount(value: Decimal) -> str:
    """2 decimales como máximo y sin ceros de sobra: 997.903214 → 997.9; 150.00 → 150."""
    rounded = value.quantize(_CENT, rounding=ROUND_HALF_UP).normalize()
    return format(rounded, "f")


def source_code(profile: ProductProfile) -> str | None:
    """El código de barras tal como lo publica el súper (cada súper busca por su forma)."""
    digits = "".join(char for char in str(profile.record.barcode or "") if char.isdigit())
    return digits if 8 <= len(digits) <= 14 else None


def decided_gtins(db: ReadOnly) -> dict[str, str]:
    """GTIN ya decidido → quién lo decidió (``human:<cuenta>`` o ``engine:gemini_auto`` / ``engine:claude_auto``)."""
    present = db.query("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (DECISIONS_TABLE,))
    if not present:
        return {}
    return {str(gtin): str(by) for gtin, by in db.query(f"SELECT canonical_gtin, decided_by FROM {DECISIONS_TABLE}")}


ENGINE_SOURCE = {"engine:claude_sesion": "claude_sesion", "engine:claude_auto": "claude", "engine:gemini_auto": "gemini"}


def engine_rows(db: ReadOnly) -> dict[str, dict[str, object]]:
    """Decisiones de motores de IA (sólo lectura) para mostrarlas en "Resueltos por IA"."""
    present = db.query("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (DECISIONS_TABLE,))
    if not present:
        return {}
    rows = db.query(
        f"SELECT canonical_gtin, decision, net_content_value, net_content_unit, note, decided_by, decided_at_utc "
        f"FROM {DECISIONS_TABLE} WHERE decided_by LIKE 'engine:%'"
    )
    return {str(gtin): {"decision": decision, "value": value, "unit": unit, "note": note, "by": by, "at": at}
            for gtin, decision, value, unit, note, by, at in rows}


# --------------------------------------------------------------------- fotos


def photo_names(cities: Mapping[str, Sequence[object]], index: Mapping[str, Mapping[str, object]]
                ) -> dict[str, tuple[str, str | None]]:
    """``"walmart:51"`` → (``<tarjeta>-<huella>``, súper de donde salió la foto)."""
    result: dict[str, tuple[str, str | None]] = {}
    for rows in cities.values():
        for row in rows:
            entry = index.get(row.key)
            digest = entry.get("h") if isinstance(entry, Mapping) else None
            name = f"{row.key}-{digest}"
            if not isinstance(digest, str) or entry.get("none") or not _PHOTO_NAME.fullmatch(name):
                continue
            store = entry.get("sm") if isinstance(entry.get("sm"), str) else None
            for supermarket, product_id in row.offers:
                result.setdefault(f"{supermarket}:{product_id}", (name, store))
    return result


def photo_url(name: str, size: int = 800) -> str:
    return f"{PHOTO_BASE}{size}/{name[:2]}/{name}.webp"


def attach_photos(items: Sequence[dict[str, object]], photos: Mapping[str, tuple[str, str | None]]) -> int:
    attached = 0
    for item in items:
        for offer in item["offers"]:
            found = photos.get(str(offer.get("ref")))
            if found:
                offer["foto"], offer["foto_de"] = found
                attached += 1
    return attached


def load_photos(fetch: Callable[[str], object]) -> dict[str, tuple[str, str | None]]:
    cities = load_cities(fetch, CATALOG_BASE)
    document = fetch(PHOTO_BASE + "index.json")
    index = document.get("items") if isinstance(document, Mapping) and document.get("schema") == PHOTO_INDEX_SCHEMA else None
    return photo_names(cities, index if isinstance(index, Mapping) else {})


def _fetch_json(url: str) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": "precios-sps-cola-tamanos/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - URLs fijas https
        return json.loads(response.read().decode("utf-8"))


def offer_size(profile: ProductProfile) -> dict[str, object] | None:
    value = profile.presentation
    if value is None or value.dimension not in UNIT:
        return None
    unit = UNIT[value.dimension]
    total = _amount(value.total_base)
    unit_amount = _amount(value.unit_amount_base)
    text = f"{value.pack_count}×{unit_amount} {unit}" if value.pack_count > 1 else f"{total} {unit}"
    return {"value": total, "unit": unit, "text": text}


def group_offers(group: Mapping[str, object], size_excluded: set[str]
                 ) -> tuple[Sequence[ProductProfile], list[dict[str, object]]]:
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
            "ref": member.record.source_record_id,
            "size": None if size is None else size["text"],
            "value": None if size is None else size["value"],
            "unit": None if size is None else size["unit"],
            "excluded": member.record.source_record_id in size_excluded,
        })
    return members, offers


def engine_items(groups: Sequence[Mapping[str, object]], rows: Mapping[str, Mapping[str, object]]
                 ) -> list[dict[str, object]]:
    """Códigos que resolvió un motor de IA, con sus ofertas, para auditarlos en el panel."""
    items: list[dict[str, object]] = []
    for group in groups:
        gtin = str(group["gtin"])
        row = rows.get(gtin)
        source = ENGINE_SOURCE.get(str(row.get("by"))) if row else None
        if source is None:
            continue
        members, offers = group_offers(group, set())
        title = max(members, key=lambda member: len(member.record.source_name or "")).record.source_name
        ia: dict[str, object] = {"status": "auto" if row["decision"] == "size" else "otro_producto", "fuente": source,
                                 "text": str(row.get("note") or "")[:200]}
        if row["decision"] == "size":
            ia.update(value=str(row["value"]), unit=str(row["unit"]))
        items.append({"gtin": gtin, "kind": "resuelto_ia", "name": title, "offers": offers, "ia": ia,
                      "resuelto_utc": row.get("at")})
    items.sort(key=lambda item: str(item.get("resuelto_utc") or ""), reverse=True)
    return items[:MAX_ITEMS]


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
        members, offers = group_offers(group, size_excluded)
        kept = [member for member in members if member.record.source_record_id not in size_excluded]
        title = max((kept or members), key=lambda member: len(member.record.source_name or "")).record.source_name
        items.append({"gtin": gtin, "kind": kind, "name": title, "offers": offers})
    items.sort(key=lambda item: (item["kind"] != "empate", -len(item["offers"]), item["gtin"]))
    return items[:MAX_ITEMS]


def build_queue(db, fetch: Callable[[str], object] | None = None) -> dict[str, object]:
    reader = ReadOnly(db)
    products = fetch_all_products(reader)
    specs = fetch_spec_attributes(reader)
    if specs is not None:
        products, _ = apply_source_spec_attributes(products, specs)
    profiles = build_profiles([record for _, record in products])
    decided = decided_gtins(reader)
    groups = resolve_groups(profiles)
    items = queue_items(groups, set(decided))
    by_engine = engine_items(groups, engine_rows(reader))
    photos_error = None
    try:  # sin fotos la cola se publica igual
        photos = load_photos(fetch or _fetch_json)
        attach_photos(items, photos)
        attach_photos(by_engine, photos)
    except Exception as error:  # noqa: BLE001
        photos_error = type(error).__name__
    return {
        "schema": QUEUE_SCHEMA,
        "generated_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "decided": len(decided),
        "decididas_ia": sorted(gtin for gtin, by in decided.items() if by.startswith("engine:")),
        "fotos_error": photos_error,
        "counts": {
            "empate": sum(item["kind"] == "empate" for item in items),
            "atipico": sum(item["kind"] == "atipico" for item in items),
        },
        "items": items,
        "resueltas_motor": by_engine,
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
