#!/usr/bin/env python3
"""Auditoría de identidad de producto (sólo lectura): códigos de barra, atributos y maestro.

Responde con números exactos, sobre el catálogo público vigente de cada ciudad:

* cuántos productos fuente (oferta de un súper) tienen código de barras, cuántos
  son GTIN válidos y de dónde sale (campo del súper, SKU de Colonial, código de
  Los Andes);
* cuántos tienen marca y contenido neto separados, y con qué estado de evidencia;
* cuántas tarjetas del catálogo agrupan varios súper y cuántos GTIN quedaron
  repartidos en varias tarjetas (duplicados sin agrupar);
* el estado del producto maestro (``master_products`` / ``master_product_links``);
* el esquema real de la base (tablas, columnas, índices y filas).

Deja ``resumen.json``, ``esquema.json`` y ``muestra-productos.csv`` (100 productos
con los casos revisados: maíz, margarina, café, conflictos de presentación y GTIN
repartidos). Sólo ejecuta SELECT; cualquier otra sentencia se rechaza.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import unicodedata
import urllib.request
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from precios_supermercados.identifiers import canonicalize_gtin  # noqa: E402

CATALOG_BASE = (
    "https://raw.githubusercontent.com/jchernandez-portfolio/precios-supermercados-sps/"
    "portfolio-data/precios-supermercados-sps/published/rpi/v3/"
)
PHOTO_INDEX_URL = "https://pub-0dcb2204775c484dbe6681e659eee236.r2.dev/fotos/v2/index.json"
PHOTO_BASE = "https://pub-0dcb2204775c484dbe6681e659eee236.r2.dev/fotos/v2/800/"
BATCH = 400
SAMPLE_SIZE = 100
SAMPLE_GROUPS = (  # (etiqueta, palabras del nombre, cupo)
    ("maiz", ("maiz", "elote"), 18),
    ("margarina", ("margarina",), 14),
    ("cafe", ("cafe",), 18),
)
SAMPLE_CONFLICTS = 15
SAMPLE_SPLIT_GTIN = 15
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_READ_ONLY = re.compile(r"\s*(SELECT|PRAGMA\s+table_info)\b", re.IGNORECASE)
CSV_COLUMNS = (
    "id", "ciudad", "supermercado", "product_id", "nombre_original", "marca", "marca_normalizada",
    "presentacion_fuente", "peso_neto", "unidad", "unidades_paquete", "estado_presentacion",
    "codigo_barras", "gtin_valido", "origen_codigo", "producto_maestro", "metodo_maestro",
    "supermercados_en_tarjeta", "motivo_muestra", "url_imagen",
)


class AuditError(RuntimeError):
    """Entrada inválida o respuesta inesperada."""


class ReadOnly:
    """Envuelve el backend: sólo deja pasar SELECT y PRAGMA table_info."""

    def __init__(self, backend) -> None:
        self.backend = backend
        self.statements = 0

    def query(self, sql: str, args: Sequence[object] = ()) -> list[tuple[object, ...]]:
        if not _READ_ONLY.match(sql) or ";" in sql.strip().rstrip(";"):
            raise AuditError("solo_lectura")
        self.statements += 1
        return self.backend.query(sql, args)


def fold(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def _http_json(url: str) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": "precios-sps-auditoria/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - URLs fijas https
        return json.loads(response.read().decode("utf-8"))


# ------------------------------------------------------------------- catálogo

@dataclass
class CatalogRow:
    city: str
    key: str
    name: str
    offers: list[tuple[str, int]] = field(default_factory=list)


def offer_product(offer: Mapping[str, object]) -> tuple[str, int] | None:
    """``walmart:19098`` o ``la_colonia:24@la_colonia_tgu`` → (súper, product_id)."""
    store, _, rest = str(offer.get("source_product_id") or "").partition(":")
    product = rest.split("@", 1)[0]
    return (store, int(product)) if store and product.isdigit() else None


def load_city(fetch: Callable[[str], object], base: str, city: str) -> list[CatalogRow]:
    manifest = fetch(base + "manifest.json")
    if not isinstance(manifest, Mapping) or not isinstance(manifest.get("files"), list):
        raise AuditError("manifest_invalido")
    rows: list[CatalogRow] = []
    for item in manifest["files"]:
        path = str(item.get("path", "")) if isinstance(item, Mapping) else ""
        if not path.startswith("catalog/"):
            continue
        document = fetch(base + path)
        for row in document.get("rows", []) if isinstance(document, Mapping) else ():
            key = str(row.get("row_id", "")).split("-", 1)[-1]
            entry = CatalogRow(city, key, str(row.get("product_name") or ""))
            for offer in row.get("offers") or ():
                parsed = offer_product(offer) if isinstance(offer, Mapping) else None
                if parsed and parsed not in entry.offers:
                    entry.offers.append(parsed)
            rows.append(entry)
    if not rows:
        raise AuditError("catalogo_vacio")
    return rows


def load_cities(fetch: Callable[[str], object], base: str) -> dict[str, list[CatalogRow]]:
    index = fetch(base + "cities.json")
    cities = index.get("cities") if isinstance(index, Mapping) else None
    if not isinstance(cities, list) or not cities:
        return {"san-pedro-sula": load_city(fetch, base, "san-pedro-sula")}
    result = {}
    for city in cities:
        manifest = str(city.get("manifest", ""))
        if not manifest.endswith("manifest.json") or ".." in manifest or "://" in manifest:
            raise AuditError("ciudad_invalida")
        result[str(city["slug"])] = load_city(fetch, base + manifest[: -len("manifest.json")], str(city["slug"]))
    return result


# ---------------------------------------------------------------------- base

def _batches(values: Sequence[int]) -> Iterable[Sequence[int]]:
    for start in range(0, len(values), BATCH):
        yield values[start:start + BATCH]


def table_columns(db: ReadOnly, table: str) -> list[str]:
    if not _IDENTIFIER.fullmatch(table):
        raise AuditError("tabla_invalida")
    return [str(row[1]) for row in db.query(f"PRAGMA table_info({table})")]


def schema(db: ReadOnly) -> dict[str, object]:
    objects = db.query("SELECT type, name, tbl_name, sql FROM sqlite_master WHERE type IN ('table','index','view') "
                       "AND name NOT LIKE 'sqlite_%' ORDER BY type, name")
    tables = {}
    for kind, name, _, sql in objects:
        if kind != "table" or not _IDENTIFIER.fullmatch(str(name)):
            continue
        columns = db.query(f"PRAGMA table_info({name})")
        count = db.query(f"SELECT COUNT(*) FROM {name}")[0][0]
        tables[str(name)] = {
            "filas": count,
            "columnas": [{"nombre": c[1], "tipo": c[2], "no_nulo": bool(c[3]), "pk": bool(c[5])} for c in columns],
            "sql": sql,
        }
    indexes = {str(name): {"tabla": tbl, "sql": sql} for kind, name, tbl, sql in objects if kind == "index"}
    return {"tablas": tables, "indices": indexes}


def fetch_products(db: ReadOnly, ids: Sequence[int]) -> dict[int, dict[str, object]]:
    products: dict[int, dict[str, object]] = {}
    for batch in _batches(sorted(set(ids))):
        marks = ",".join("?" for _ in batch)
        for row in db.query(
            "SELECT product_id, supermarket_id, name, brand, presentation, category, ean, reference "
            f"FROM products WHERE product_id IN ({marks})", batch,
        ):
            keys = ("product_id", "supermarket_id", "name", "brand", "presentation", "category", "ean", "reference")
            products[int(row[0])] = dict(zip(keys, row, strict=True))
    return products


PROFILE_COLUMNS = (
    "canonical_gtin", "normalized_brand", "raw_brand", "normalized_quantity", "normalized_unit",
    "normalized_pack_count", "presentation_status", "comparison_status", "display_presentation",
    "raw_presentation", "product_type",
)


def fetch_profiles(db: ReadOnly, ids: Sequence[int]) -> dict[int, dict[str, object]]:
    available = set(table_columns(db, "product_homologation_profiles"))
    columns = [column for column in PROFILE_COLUMNS if column in available]
    profiles: dict[int, dict[str, object]] = {}
    for batch in _batches(sorted(set(ids))):
        marks = ",".join("?" for _ in batch)
        for row in db.query(
            f"SELECT product_id, {', '.join(columns)} FROM product_homologation_profiles WHERE product_id IN ({marks})",
            batch,
        ):
            profiles[int(row[0])] = dict(zip(columns, row[1:], strict=True))
    return profiles


def fetch_links(db: ReadOnly, ids: Sequence[int]) -> dict[int, tuple[str, str]]:
    if "master_product_links" not in {row[0] for row in db.query(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='master_product_links'")}:
        return {}
    links: dict[int, tuple[str, str]] = {}
    for batch in _batches(sorted(set(ids))):
        marks = ",".join("?" for _ in batch)
        for product_id, master, method in db.query(
            "SELECT product_id, master_product_id, link_method FROM master_product_links "
            f"WHERE status='active' AND product_id IN ({marks})", batch,
        ):
            links[int(product_id)] = (str(master), str(method))
    return links


def database_totals(db: ReadOnly) -> dict[str, object]:
    by_store = {
        str(store): {"productos": total, "con_ean": with_ean}
        for store, total, with_ean in db.query(
            "SELECT supermarket_id, COUNT(*), SUM(CASE WHEN ean IS NOT NULL AND trim(ean) <> '' THEN 1 ELSE 0 END) "
            "FROM products GROUP BY supermarket_id ORDER BY supermarket_id")
    }
    tables = {row[0] for row in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    master: dict[str, object] = {"existe": "master_products" in tables}
    if master["existe"]:
        master["maestros"] = {f"{origin}/{status}": count for origin, status, count in db.query(
            "SELECT origin_method, status, COUNT(*) FROM master_products GROUP BY origin_method, status")}
        master["vinculos"] = {f"{method}/{status}": count for method, status, count in db.query(
            "SELECT link_method, status, COUNT(*) FROM master_product_links GROUP BY link_method, status")}
        master["maestros_con_2_o_mas_supermercados"] = db.query(
            "SELECT COUNT(*) FROM (SELECT master_product_id FROM master_product_links WHERE status='active' "
            "GROUP BY master_product_id HAVING COUNT(DISTINCT supermarket_id) >= 2)")[0][0]
        master["rechazos_humanos"] = db.query("SELECT COUNT(*) FROM master_link_rejections")[0][0] \
            if "master_link_rejections" in tables else 0
    return {"productos_por_supermercado": by_store, "producto_maestro": master}


# ------------------------------------------------------------------- métricas

def code_origin(product: Mapping[str, object]) -> str | None:
    """De dónde sale el código: campo del súper o reconstrucción documentada."""
    ean = str(product.get("ean") or "").strip()
    if not ean:
        return None
    if product.get("supermarket_id") == "colonial" and ean == str(product.get("reference") or "").strip():
        return "sku_colonial_gs1"
    if product.get("supermarket_id") == "comisariato_los_andes":
        return "codigo_los_andes_reconstruido"
    return "campo_ean_del_super"


def city_metrics(rows: Sequence[CatalogRow], products: Mapping[int, Mapping[str, object]],
                 profiles: Mapping[int, Mapping[str, object]], links: Mapping[int, tuple[str, str]]) -> dict[str, object]:
    stores: dict[str, Counter] = defaultdict(Counter)
    statuses: Counter = Counter()
    gtin_rows: dict[str, set[str]] = defaultdict(set)
    seen: set[int] = set()
    multi = rows_with_gtin = 0
    for row in rows:
        if len({store for store, _ in row.offers}) >= 2:
            multi += 1
        row_has_gtin = False
        for store, product_id in row.offers:
            product = products.get(product_id) or {}
            profile = profiles.get(product_id) or {}
            gtin = canonicalize_gtin(str(product.get("ean") or "").strip() or None)
            if gtin:
                row_has_gtin = True
                gtin_rows[gtin].add(row.key)
            if product_id in seen:
                continue
            seen.add(product_id)
            counter = stores[store]
            counter["productos"] += 1
            counter["en_base"] += bool(product)
            counter["con_codigo"] += bool(str(product.get("ean") or "").strip())
            counter["gtin_valido"] += bool(gtin)
            origin = code_origin(product)
            if origin:
                counter[f"origen:{origin}"] += 1
            counter["con_marca_fuente"] += bool(str(product.get("brand") or "").strip())
            counter["con_marca_normalizada"] += bool(profile.get("normalized_brand"))
            counter["con_presentacion_fuente"] += bool(str(product.get("presentation") or "").strip())
            counter["con_contenido_neto"] += bool(profile.get("normalized_quantity"))
            counter["con_producto_maestro"] += product_id in links
            statuses[str(profile.get("presentation_status") or "sin_perfil")] += 1
        rows_with_gtin += row_has_gtin
    split = {gtin: keys for gtin, keys in gtin_rows.items() if len(keys) >= 2}
    total = Counter()
    for counter in stores.values():
        total.update(counter)
    return {
        "tarjetas": len(rows),
        "tarjetas_con_2_o_mas_supermercados": multi,
        "tarjetas_con_algun_gtin": rows_with_gtin,
        "gtin_repartidos_en_varias_tarjetas": len(split),
        "tarjetas_afectadas_por_gtin_repartido": len({key for keys in split.values() for key in keys}),
        "productos_fuente": dict(total),
        "por_supermercado": {store: dict(counter) for store, counter in sorted(stores.items())},
        "estado_presentacion": dict(statuses.most_common()),
        "_gtin_repartidos": split,
    }


# --------------------------------------------------------------------- muestra

def _stable(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def select_sample(rows_by_city: Mapping[str, Sequence[CatalogRow]], products: Mapping[int, Mapping[str, object]],
                  profiles: Mapping[int, Mapping[str, object]], split: Mapping[str, Mapping[str, set[str]]],
                  size: int = SAMPLE_SIZE) -> list[tuple[str, CatalogRow, str, int]]:
    """(motivo, tarjeta, súper, product_id): casos pedidos primero, después una muestra pareja."""
    chosen: list[tuple[str, CatalogRow, str, int]] = []
    taken: set[int] = set()
    every = [(row, store, pid) for city in sorted(rows_by_city) for row in rows_by_city[city] for store, pid in row.offers]
    every.sort(key=lambda item: _stable(f"{item[0].city}:{item[2]}"))

    def take(reason: str, candidates: Iterable[tuple[CatalogRow, str, int]], limit: int) -> None:
        count = 0
        for row, store, pid in candidates:
            if count >= limit or len(chosen) >= size:
                return
            if pid in taken:
                continue
            taken.add(pid)
            chosen.append((reason, row, store, pid))
            count += 1

    for label, words, limit in SAMPLE_GROUPS:
        take(label, ((r, s, p) for r, s, p in every
                     if any(re.search(rf"\b{word}\b", fold((products.get(p) or {}).get("name") or r.name)) for word in words)),
             limit)
    take("conflicto_presentacion", ((r, s, p) for r, s, p in every
                                    if (profiles.get(p) or {}).get("presentation_status") in {"conflict", "name_preferred_source_conflict"}),
         SAMPLE_CONFLICTS)
    split_keys = {(city, key) for city, groups in split.items() for keys in groups.values() for key in keys}
    take("gtin_repartido", ((r, s, p) for r, s, p in every if (r.city, r.key) in split_keys), SAMPLE_SPLIT_GTIN)
    take("aleatoria", every, size)
    return chosen


def sample_rows(sample, products, profiles, links, photos: Mapping[str, Mapping[str, object]]) -> list[dict[str, object]]:
    lines = []
    for reason, row, store, pid in sample:
        product = products.get(pid) or {}
        profile = profiles.get(pid) or {}
        ean = str(product.get("ean") or "").strip()
        photo = photos.get(row.key) or {}
        digest = photo.get("h") if isinstance(photo.get("h"), str) and not photo.get("none") else None
        master, method = links.get(pid, (None, None))
        lines.append({
            "id": row.key, "ciudad": row.city, "supermercado": store, "product_id": pid,
            "nombre_original": product.get("name") or row.name, "marca": product.get("brand"),
            "marca_normalizada": profile.get("normalized_brand"), "presentacion_fuente": product.get("presentation"),
            "peso_neto": profile.get("normalized_quantity"), "unidad": profile.get("normalized_unit"),
            "unidades_paquete": profile.get("normalized_pack_count"),
            "estado_presentacion": profile.get("presentation_status"), "codigo_barras": ean or None,
            "gtin_valido": canonicalize_gtin(ean or None), "origen_codigo": code_origin(product),
            "producto_maestro": master, "metodo_maestro": method,
            "supermercados_en_tarjeta": "+".join(sorted({s for s, _ in row.offers})), "motivo_muestra": reason,
            "url_imagen": f"{PHOTO_BASE}{row.key[:2]}/{row.key}-{digest}.webp" if digest else None,
        })
    return lines


# ------------------------------------------------------------------------ main

def audit(db, fetch: Callable[[str], object], base: str = CATALOG_BASE,
          photo_index: Mapping[str, object] | None = None) -> tuple[dict[str, object], dict[str, object], list[dict[str, object]]]:
    reader = ReadOnly(db)
    rows_by_city = load_cities(fetch, base)
    ids = sorted({pid for rows in rows_by_city.values() for row in rows for _, pid in row.offers})
    products = fetch_products(reader, ids)
    profiles = fetch_profiles(reader, ids)
    links = fetch_links(reader, ids)
    cities = {city: city_metrics(rows, products, profiles, links) for city, rows in rows_by_city.items()}
    split = {city: metrics.pop("_gtin_repartidos") for city, metrics in cities.items()}
    examples = {city: [{"gtin": gtin, "tarjetas": sorted(keys)} for gtin, keys in sorted(groups.items())[:20]]
                for city, groups in split.items()}
    items = (photo_index or {}).get("items") if isinstance(photo_index, Mapping) else None
    sample = select_sample(rows_by_city, products, profiles, split)
    csv_rows = sample_rows(sample, products, profiles, links, items if isinstance(items, Mapping) else {})
    summary = {
        "generado_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "alcance": "productos fuente con oferta en el catálogo público vigente de cada ciudad",
        "productos_fuente_distintos_en_catalogo": len(ids),
        "productos_encontrados_en_base": len(products),
        "ciudades": cities,
        "ejemplos_gtin_repartidos": examples,
        "base_completa": database_totals(reader),
        "sentencias_ejecutadas": reader.statements,
    }
    return summary, schema(reader), csv_rows


def write_outputs(output: Path, summary, schema_doc, csv_rows) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "resumen.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "esquema.json").write_text(json.dumps(schema_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    with (output / "muestra-productos.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(csv_rows)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--catalog-base", default=CATALOG_BASE)
    args = parser.parse_args(argv)
    from exportar_modelo_analitico import TursoBackend

    backend = TursoBackend(os.environ.get("TURSO_DATABASE_URL", ""), os.environ.get("TURSO_AUTH_TOKEN", ""))
    try:
        photo_index = _http_json(PHOTO_INDEX_URL)
    except Exception:  # noqa: BLE001 - sin índice la muestra sale sin url de imagen
        photo_index = None
    summary, schema_doc, csv_rows = audit(backend, _http_json, args.catalog_base, photo_index)
    write_outputs(args.output_dir, summary, schema_doc, csv_rows)
    printable = {key: value for key, value in summary.items() if key != "ejemplos_gtin_repartidos"}
    print(json.dumps(printable, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
