#!/usr/bin/env python3
"""Auditoría del catálogo público de Súper Compras tal como se publica (sólo lectura).

Complementa ``auditar_calidad_catalogo.mjs`` (que audita lo que la app muestra
después de sus correcciones) con lo que viene del pipeline Python: títulos,
presentación frente al título, precio por libra entre ciudades, precio por unidad
fuera de rango, tamaños distintos dentro de una fila comparable, frescura,
disponibilidad, filas duplicadas del mismo súper y fotos (faltantes, de otro súper,
heredadas de sucursal y la misma foto en productos distintos).

    python scripts/auditar_catalogo_publico.py --data DIR [--photos-index index.json] [--out resumen.json]

``DIR`` es una copia local de ``published/rpi/v3`` (con ``cities.json``). Sólo lee
archivos públicos ya publicados: no consulta Turso ni a los supermercados.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path

CAMEL = re.compile(r"[a-záéíóúñ][A-ZÁÉÍÓÚÑ][a-záéíóúñ]")
BARE_NUMBER = re.compile(r"\s\d+(?:[.,]\d+)?\s*$")
PER_POUND_NAME = re.compile(r"precio\s+(?:indicado\s+)?por\s+(?:libra|lb)", re.IGNORECASE)
MEASURE = re.compile(
    r"(?<![\w.,/])(\d{1,3}(?:,\d{3})+(?!\d)|\d+(?:[.,]\d+)?)\s*-?\s*(kgs?|kilos?|grms?|grs?|gramos|gr|g|mls?|lts?|litros?|l|onzas|onz|oz|lbs?|libras?)"
    r"(?![a-záéíóúñ])",
    re.IGNORECASE,
)
UNIT_FACTOR = {
    "kg": ("g", 1000.0), "kgs": ("g", 1000.0), "kilo": ("g", 1000.0), "kilos": ("g", 1000.0),
    "g": ("g", 1.0), "gr": ("g", 1.0), "grs": ("g", 1.0), "gramos": ("g", 1.0), "grm": ("g", 1.0), "grms": ("g", 1.0),
    "ml": ("ml", 1.0), "mls": ("ml", 1.0), "l": ("ml", 1000.0), "lt": ("ml", 1000.0), "lts": ("ml", 1000.0),
    "litro": ("ml", 1000.0), "litros": ("ml", 1000.0),
    "oz": ("oz", 1.0), "onz": ("oz", 1.0), "onzas": ("oz", 1.0),
    "lb": ("g", 453.59237), "lbs": ("g", 453.59237), "libra": ("g", 453.59237), "libras": ("g", 453.59237),
}
PRESENTATION = re.compile(r"^(?:(\d+)\s*×\s*)?(\d+(?:\.\d+)?)\s*(kg|g|ml|L|oz)$")
PER_BASE = {"100 g": 100.0, "kg": 1000.0, "100 ml": 100.0, "L": 1000.0, "unidad": 1.0}
AGE_BUCKETS = ((2, "<2 d"), (7, "2-6 d"), (30, "7-29 d"), (60, "30-59 d"))


def fold(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(char for char in text if not unicodedata.combining(char))


def title_flags(name: str, presentation: object) -> list[str]:
    """Señales de título raro: palabras pegadas, todo en mayúsculas, número sin unidad, doble título."""
    flags = []
    letters = [char for char in name if char.isalpha()]
    if CAMEL.search(name):
        flags.append("palabras_pegadas")
    if len(letters) > 5 and all(char.isupper() for char in letters):
        flags.append("mayusculas")
    if BARE_NUMBER.search(name) and not presentation:
        flags.append("numero_sin_unidad")
    sizes = name_sizes(name)
    if len(name) > 45 and len(sizes) >= 2 and len({(base, round(value, 1)) for base, value in sizes}) == 1:
        flags.append("titulo_duplicado")
    return flags


def name_sizes(name: str) -> list[tuple[str, float]]:
    out = []
    for match in MEASURE.finditer(name or ""):
        base, factor = UNIT_FACTOR[match.group(2).casefold()]
        number = match.group(1)
        # "1,400 ml" es mil cuatrocientos; "1,5 kg" es uno y medio. "1/2 Libra" no se lee.
        number = number.replace(",", "") if re.fullmatch(r"\d{1,3}(?:,\d{3})+", number) else number.replace(",", ".")
        out.append((base, float(number) * factor))
    return out


def presentation_total(presentation: object) -> tuple[str, float, int] | None:
    match = PRESENTATION.match(str(presentation or "").strip())
    if not match:
        return None
    count = int(match.group(1) or 1)
    base, factor = {"kg": ("g", 1000.0), "g": ("g", 1.0), "ml": ("ml", 1.0), "L": ("ml", 1000.0),
                    "oz": ("oz", 1.0)}[match.group(3)]
    return base, float(match.group(2)) * factor * count, count


def _comparable_forms(base: str, value: float) -> list[tuple[str, float]]:
    if base == "oz":
        return [("g", value * 28.349523125), ("ml", value * 29.5735295625), ("oz", value)]
    return [(base, value)]


def title_contradicts_presentation(name: str, presentation: object) -> bool:
    """El título trae un tamaño y ninguno cuadra con la presentación (total o por unidad, ±3 %)."""
    sizes = name_sizes(name)
    parsed = presentation_total(presentation)
    if not sizes or parsed is None:
        return False
    base, total, count = parsed
    targets = _comparable_forms(base, total) + (_comparable_forms(base, total / count) if count > 1 else [])
    for size_base, size_value in sizes:
        for left_base, left in _comparable_forms(size_base, size_value):
            for right_base, right in targets:
                if left_base == right_base and abs(left - right) <= max(1.0, 0.03 * max(left, right)):
                    return False
    return True


def implied_total(offer: Mapping[str, object]) -> tuple[str, float] | None:
    """Tamaño que usó el pipeline para el precio por unidad de la oferta."""
    unit = offer.get("unit_price")
    if not isinstance(unit, Mapping) or unit.get("per") not in PER_BASE:
        return None
    try:
        amount, price = float(str(unit.get("amount"))), float(str(offer.get("current_price")))
    except ValueError:
        return None
    if amount <= 0 or price <= 0:
        return None
    return str(unit["per"]), price * PER_BASE[str(unit["per"])] / amount


def _retailer_key(row: Mapping[str, object]) -> str:
    retailers = {str(offer.get("supermarket_id")) for offer in row.get("offers") or ()}
    return next(iter(retailers)) if len(retailers) == 1 else "varias"


def _age_bucket(days: float) -> str:
    return next((label for limit, label in AGE_BUCKETS if days < limit), ">=60 d")


def audit_city(rows: list[dict[str, object]], as_of: str) -> dict[str, object]:
    as_of_dt = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    result: dict[str, Counter] = defaultdict(Counter)
    examples: dict[str, list[object]] = defaultdict(list)

    def note(kind: str, key: object, example: object = None) -> None:
        result[kind][str(key)] += 1
        if example is not None and len(examples[kind]) < 8:
            examples[kind].append(example)

    duplicates: dict[tuple[str, object, str], list[str]] = defaultdict(list)
    unit_groups: dict[tuple[object, str], list[tuple[float, dict[str, object]]]] = defaultdict(list)
    for row in rows:
        name = str(row.get("product_name") or "")
        retailer = _retailer_key(row)
        offers = [offer for offer in row.get("offers") or () if isinstance(offer, dict)]
        note("filas", retailer)
        note("comparabilidad", row.get("comparability"))
        if not name.strip():
            note("titulo_vacio", retailer, row.get("row_id"))
        for flag in title_flags(name, row.get("presentation")):
            note(f"titulo_{flag}", retailer, name)
        if not row.get("presentation"):
            note("sin_presentacion", retailer)
        elif title_contradicts_presentation(name, row.get("presentation")):
            note("titulo_contradice_presentacion", retailer, (row.get("row_id"), name, row.get("presentation")))
        if PER_POUND_NAME.search(name) and row.get("presentation") != "por libra":
            note("dice_precio_por_libra_y_no_va_por_libra", retailer, (name, row.get("presentation")))
        totals = {}
        for offer in offers:
            supermarket = str(offer.get("supermarket_id"))
            note("ofertas", supermarket)
            note("disponibilidad", f"{supermarket}:{offer.get('availability')}")
            note("frescura", f"{supermarket}:{offer.get('freshness_status')}")
            observed = str(offer.get("observed_at") or "")
            if observed:
                days = (as_of_dt - datetime.fromisoformat(observed.replace("Z", "+00:00"))).total_seconds() / 86400
                note("precio_sin_cambio_desde", f"{supermarket}:{_age_bucket(days)}")
            unit = offer.get("unit_price") if isinstance(offer.get("unit_price"), dict) else {}
            if unit.get("per") == "libra":
                note("ofertas_por_libra", supermarket)
            implied = implied_total(offer)
            if implied is not None:
                totals[supermarket] = implied
                if implied[0] != "unidad":
                    unit_groups[(row.get("product_type"), implied[0])].append((float(unit["amount"]), row))
            duplicates[(supermarket, fold(name), row.get("presentation"), str(len(offers) > 1))].append(
                str(row.get("row_id")))
        if row.get("comparability") == "comparable" and len(totals) >= 2:
            pers = {per for per, _ in totals.values()}
            values = [value for _, value in totals.values()]
            if len(pers) > 1 or max(values) / min(values) > 1.05:
                note("comparable_con_tamanos_distintos", retailer,
                     (row.get("row_id"), name, {key: round(value, 1) for key, (_, value) in totals.items()}))
    for (_, per), values in unit_groups.items():
        if len(values) < 8 or not per:
            continue
        median = statistics.median(amount for amount, _ in values)
        for amount, row in values:
            ratio = amount / median if median else None
            if ratio is not None and (ratio >= 100 or ratio <= 1 / 20):
                note("precio_unidad_fuera_de_rango", _retailer_key(row),
                     (row.get("product_name"), row.get("presentation"), round(ratio, 3)))
    for (supermarket, _, _, _), ids in duplicates.items():
        if len(set(ids)) > 1:
            note("filas_duplicadas_mismo_super", supermarket, sorted(set(ids)))
    return {"conteos": {key: dict(value) for key, value in sorted(result.items())},
            "ejemplos": dict(sorted(examples.items()))}


def audit_photos(rows_by_city: Mapping[str, list[dict[str, object]]], search_by_city: Mapping[str, Mapping[str, object]],
                 photos: Mapping[str, Mapping[str, object]]) -> dict[str, object]:
    counts: dict[str, Counter] = defaultdict(Counter)
    products: dict[str, set[str]] = defaultdict(set)
    for rows in rows_by_city.values():
        for row in rows:
            key = str(row["row_id"]).split("-", 1)[-1]
            products[key].update(str(offer["source_product_id"]).split("@", 1)[0] for offer in row.get("offers") or ())
    for city, search in search_by_city.items():
        columns = {name: index for index, name in enumerate(search["columns"])}
        retailers = search["retailers"]
        for entry in search["rows"]:
            stores = [retailers[index] for index in entry[columns["retailers"]]]
            group = f"{city}:{stores[0] if len(stores) == 1 else 'varias'}:{entry[columns['comparability']]}"
            counts["filas"][group] += 1
            if not entry[columns["photo"]]:
                counts["sin_foto"][group] += 1
                continue
            item = photos.get(entry[0]) or {}
            if item.get("sm") and item["sm"] not in stores:
                counts["foto_de_un_super_que_no_esta_en_la_fila"][f"{city}:{item['sm']}"] += 1
            if item.get("de"):
                counts["foto_heredada_de_sucursal"][city] += 1
    owners: dict[str, list[str]] = defaultdict(list)
    for key, item in photos.items():
        if item.get("h") and not item.get("none") and not item.get("de") and key in products:
            owners[str(item["h"])].append(key)
    for key, item in photos.items():
        if not item.get("de") or item.get("none") or key not in products:
            continue
        others = [owner for owner in owners.get(str(item.get("h")), ()) if owner not in {key, item.get("de")}]
        if others and not any(products[owner] & products[key] for owner in others):
            counts["foto_heredada_que_es_de_otro_producto"]["total"] += 1
    return {key: dict(value) for key, value in sorted(counts.items())}


def load_city_rows(root: Path, manifest_path: str) -> tuple[dict[str, object], list[dict[str, object]], dict[str, object]]:
    manifest = json.loads((root / manifest_path).read_text(encoding="utf-8"))
    prefix = manifest_path.rsplit("/", 1)[0] + "/" if "/" in manifest_path else ""
    rows: list[dict[str, object]] = []
    for item in manifest["files"]:
        if "/part-" in item["path"]:
            rows.extend(json.loads((root / (prefix + item["path"])).read_text(encoding="utf-8"))["rows"])
    search = json.loads((root / (prefix + manifest["search_file"])).read_text(encoding="utf-8"))
    return manifest, rows, search


def run(root: Path, photos_index: Path | None) -> dict[str, object]:
    cities = json.loads((root / "cities.json").read_text(encoding="utf-8"))["cities"]
    report: dict[str, object] = {"ciudades": {}}
    rows_by_city, search_by_city = {}, {}
    for city in cities:
        manifest, rows, search = load_city_rows(root, city["manifest"])
        rows_by_city[city["slug"]], search_by_city[city["slug"]] = rows, search
        report["ciudades"][city["slug"]] = {"as_of": manifest["as_of"], **audit_city(rows, str(manifest["as_of"]))}
    if photos_index is not None:
        photos = json.loads(photos_index.read_text(encoding="utf-8")).get("items") or {}
        report["fotos"] = audit_photos(rows_by_city, search_by_city, photos)
    return report


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, required=True, help="copia local de published/rpi/v3")
    parser.add_argument("--photos-index", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)
    report = run(args.data, args.photos_index)
    text = json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
