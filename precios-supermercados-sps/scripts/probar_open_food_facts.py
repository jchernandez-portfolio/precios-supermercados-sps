#!/usr/bin/env python3
"""Prueba de enriquecimiento con Open Food Facts (sólo lectura; no escribe en la base).

Toma una muestra de GTIN válidos de ``products`` (o de un archivo) y consulta la
API pública de Open Food Facts producto por producto. Mide cuántos existen allí y
cuántos traen datos útiles (nombre, marca, cantidad), en total, por supermercado y
por origen del código (prefijo GS1 de Honduras 742 contra el resto).

Cortesía con la API: una consulta a la vez, ≥ 1 s entre consultas (su límite de
lectura de productos es 100 por minuto), User-Agent que identifica el proyecto y
sólo los campos necesarios. Los datos de Open Food Facts son ODbL: esta prueba
sólo cuenta; un uso masivo futuro exige atribución y compartir igual.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from precios_supermercados.identifiers import canonicalize_gtin  # noqa: E402

API = "https://world.openfoodfacts.org/api/v2/product/{code}.json"
FIELDS = "code,product_name,product_name_es,brands,quantity,product_quantity,product_quantity_unit,image_front_url"
USER_AGENT = "precios-sps-prueba-enriquecimiento/1.0 (+https://github.com/jchernandez-portfolio/precios-supermercados-sps)"
DEFAULT_SAMPLE = 500
DEFAULT_DELAY = 1.0
HONDURAS_PREFIX = "742"
CSV_COLUMNS = ("gtin", "supermercado", "origen", "encontrado", "nombre", "marca", "cantidad", "util", "error")
_READ_ONLY_PREFIX = "SELECT"


class ProbeError(RuntimeError):
    """Entrada inválida para la prueba."""


def off_code(gtin14: str) -> str:
    """GTIN-14 canónico → código que usa Open Food Facts (EAN-13; EAN-8 si cabe)."""
    digits = gtin14.lstrip("0")
    return digits.zfill(8) if len(digits) <= 8 else digits.zfill(13)


def origin(gtin14: str) -> str:
    return "honduras_742" if off_code(gtin14).startswith(HONDURAS_PREFIX) else "otros_prefijos"


def fetch_gtins(db) -> list[tuple[str, str]]:
    """``(gtin14, supermercado)`` de todos los productos con GTIN válido (sólo SELECT)."""
    sql = "SELECT supermarket_id, ean FROM products WHERE ean IS NOT NULL AND trim(ean) <> ''"
    if not sql.startswith(_READ_ONLY_PREFIX):
        raise ProbeError("solo_lectura")
    seen: dict[str, str] = {}
    for supermarket, ean in db.query(sql):
        gtin = canonicalize_gtin(str(ean).strip())
        if gtin and gtin not in seen:
            seen[gtin] = str(supermarket)
    return sorted(seen.items())


def sample(gtins: Sequence[tuple[str, str]], size: int, seed: int) -> list[tuple[str, str]]:
    """Muestra estratificada por supermercado (proporcional), reproducible con ``seed``."""
    if size <= 0 or not gtins:
        raise ProbeError("muestra_vacia")
    by_store: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for item in gtins:
        by_store[item[1]].append(item)
    rng = random.Random(seed)
    total = len(gtins)
    chosen: list[tuple[str, str]] = []
    for _store, items in sorted(by_store.items()):
        quota = max(1, round(size * len(items) / total))
        chosen.extend(rng.sample(items, min(quota, len(items))))
    rng.shuffle(chosen)
    return sorted(chosen[:size])


def classify(document: object) -> dict[str, object]:
    """Respuesta de la API → encontrado y campos útiles."""
    product = document.get("product") if isinstance(document, Mapping) and document.get("status") == 1 else None
    if not isinstance(product, Mapping):
        return {"encontrado": False, "nombre": None, "marca": None, "cantidad": None, "util": False}

    def text(*keys: str) -> str | None:
        for key in keys:
            value = product.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None
    name, brand, quantity = text("product_name_es", "product_name"), text("brands"), text("quantity")
    return {"encontrado": True, "nombre": name, "marca": brand, "cantidad": quantity,
            "util": bool(name and brand and quantity)}


def probe(items: Sequence[tuple[str, str]], get_json: Callable[[str], object], *, delay: float = DEFAULT_DELAY,
          sleep: Callable[[float], None] = time.sleep, log=print) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for number, (gtin, store) in enumerate(items, 1):
        if number > 1:
            sleep(delay)
        row: dict[str, object] = {"gtin": gtin, "supermercado": store, "origen": origin(gtin), "error": None}
        try:
            row |= classify(get_json(API.format(code=off_code(gtin)) + f"?fields={FIELDS}"))
        except Exception as error:  # noqa: BLE001 - un fallo de red cuenta como error, no como "no existe"
            row |= {"encontrado": None, "nombre": None, "marca": None, "cantidad": None, "util": False,
                    "error": type(error).__name__}
        rows.append(row)
        if number % 50 == 0:
            log(f"consultados {number}/{len(items)}")
    return rows


def _rates(rows: Iterable[Mapping[str, object]]) -> dict[str, object]:
    rows = list(rows)
    answered = [row for row in rows if row["error"] is None]
    found = [row for row in answered if row["encontrado"]]

    def pct(count: int) -> float:
        return round(100 * count / len(answered), 1) if answered else 0.0
    counts = {
        "consultados": len(rows),
        "errores_de_red": len(rows) - len(answered),
        "encontrados": len(found),
        "con_nombre": sum(1 for row in found if row["nombre"]),
        "con_marca": sum(1 for row in found if row["marca"]),
        "con_cantidad": sum(1 for row in found if row["cantidad"]),
        "utiles_nombre_marca_y_cantidad": sum(1 for row in found if row["util"]),
    }
    return counts | {f"pct_{key}": pct(value) for key, value in counts.items()
                     if key not in {"consultados", "errores_de_red"}}


def report(rows: Sequence[Mapping[str, object]], *, universe: int, source: str) -> dict[str, object]:
    by_store: dict[str, list] = defaultdict(list)
    by_origin: dict[str, list] = defaultdict(list)
    for row in rows:
        by_store[str(row["supermercado"])].append(row)
        by_origin[str(row["origen"])].append(row)
    return {
        "generado_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fuente_de_gtin": source,
        "gtin_validos_disponibles": universe,
        "total": _rates(rows),
        "por_supermercado": {key: _rates(value) for key, value in sorted(by_store.items())},
        "por_origen": {key: _rates(value) for key, value in sorted(by_origin.items())},
        "muestras_de_cantidad": Counter(str(row["cantidad"]) for row in rows if row.get("cantidad")).most_common(15),
        "licencia": "Open Food Facts: ODbL (atribución y compartir igual si se usa en masa)",
    }


def write_outputs(output: Path, summary: Mapping[str, object], rows: Sequence[Mapping[str, object]]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "open-food-facts.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with (output / "open-food-facts.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _http_get_json(url: str) -> object:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - URL fija https
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return {"status": 0}
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output-dir", type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--turso", action="store_true", help="muestra de products (sólo lectura)")
    source.add_argument("--gtins-file", type=Path, help="CSV con columnas gtin,supermercado")
    parser.add_argument("--sample", type=int, default=DEFAULT_SAMPLE)
    parser.add_argument("--seed", type=int, default=20261112)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    args = parser.parse_args(argv)
    if args.delay < DEFAULT_DELAY:
        raise ProbeError("delay_minimo_1s")
    if args.turso:
        from exportar_modelo_analitico import TursoBackend

        gtins = fetch_gtins(TursoBackend(os.environ.get("TURSO_DATABASE_URL", ""), os.environ.get("TURSO_AUTH_TOKEN", "")))
        label = "products (Turso, muestra estratificada por supermercado)"
    else:
        with args.gtins_file.open(encoding="utf-8") as handle:
            gtins = sorted({(g, s) for g, s in ((canonicalize_gtin(r["gtin"]), r.get("supermercado", "")) for r in csv.DictReader(handle)) if g})
        label = f"archivo {args.gtins_file.name}"
    items = sample(gtins, args.sample, args.seed)
    rows = probe(items, _http_get_json, delay=args.delay)
    summary = report(rows, universe=len(gtins), source=label)
    write_outputs(args.output_dir, summary, rows)
    print(json.dumps(summary["total"], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
