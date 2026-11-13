#!/usr/bin/env python3
"""Actualiza las fotos de producto de Súper Compras (corrida diaria incremental).

1. Lee el catálogo público ya publicado de cada ciudad (rama ``portfolio-data``,
   ``cities.json``): una fila por producto con sus ofertas ``<súper>:<product_id>``.
2. Resuelve en Turso (sólo lectura) las llaves exactas de cada producto en su súper.
3. Pide a cada súper, por endpoints públicos, las direcciones de sus fotos.
4. Para cada fila pendiente descarga la foto principal de cada súper (la segunda
   sólo si la principal no alcanza), puntúa, elige una y la normaliza a WebP de
   800 y 320 px con fondo blanco y el producto completo.
5. Escribe ``800|320/<id[:2]>/<id>-<huella>.webp`` nuevos, el ``index.json`` completo
   y ``revision.html`` (muestra para revisar a ojo) en ``--output-dir``. Otro
   workflow, sólo con la llave de escritura del bucket, los valida y los sube a
   Cloudflare R2. El nombre lleva la huella del contenido: una foto publicada no
   cambia nunca y puede guardarse en caché un año.

Una fila ya resuelta no se vuelve a descargar mientras sus direcciones de foto
(``sig``) y la versión del proceso no cambien; las filas sin foto aceptable también
quedan anotadas para no reintentarlas cada semana.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from precios_supermercados import product_image_sources as sources  # noqa: E402
from precios_supermercados import product_images as images  # noqa: E402

PIPELINE_VERSION = 2
# Versión de los criterios de puntuación. Cuando sube, las fotos ya elegidas con
# puntaje bajo (< RESCORE_BELOW) se vuelven a evaluar una vez, sin tocar el resto.
# 3 (2026-10-25): fondo no blanco descarta la foto en vez de restar puntos.
# 4 (2026-11-08): una foto es genérica desde 4 productos (antes 2) y se quita el
#   rótulo de color de abajo de Walmart. Las filas sin foto con criterios
#   anteriores también se reevalúan una vez (eran ~27 600, la mayoría fotos buenas
#   de Walmart descartadas como "genérica").
CRITERIA_VERSION = 4
RESCORE_BELOW = 70
INDEX_SCHEMA = "super-compras-product-images/v2"
REPOSITORY_RAW = "https://raw.githubusercontent.com/jchernandez-portfolio/precios-supermercados-sps"
CATALOG_BASE = f"{REPOSITORY_RAW}/portfolio-data/precios-supermercados-sps/published/rpi/v3/"
# Dirección pública del bucket R2 (no es secreta); las fotos viven bajo fotos/v2/.
PUBLIC_BASE = "https://pub-0dcb2204775c484dbe6681e659eee236.r2.dev"
R2_PREFIX = "fotos/v2/"
INDEX_URL = f"{PUBLIC_BASE}/{R2_PREFIX}index.json"
DEFAULT_MAX_PRODUCTS = 12000
# El job tiene 300 min; a los 240 ya no se empiezan filas nuevas y se cierra el artifact.
DEFAULT_TIME_BUDGET_MINUTES = 240
REVIEW_LOWEST = 120
REVIEW_SAMPLE = 80
GENERIC_URL_OWNERS = images.GENERIC_OWNERS
QUERY_BATCH = 400
# Descargas en paralelo desde el propio bucket al heredar fotos por sucursal.
ADOPT_WORKERS = 8
ADOPT_CHUNK = 64
KEY_COLUMNS = ("source_item_id", "source_key", "source_catalog_product_id")


class PhotoUpdateError(RuntimeError):
    """Entrada inválida para la corrida de fotos."""


@dataclass(frozen=True)
class CatalogRow:
    key: str
    offers: tuple[tuple[str, int], ...]
    # Ofertas públicas con sucursal ("walmart:51@walmart_tgu_ffaa") y si la fila es
    # de un producto sin identidad comparable ("source-…"): sirven para heredar la
    # foto de las tarjetas por sucursal cuando Tegucigalpa pasó a una por producto.
    branch_ids: tuple[str, ...] = ()
    source_row: bool = False


@dataclass(frozen=True)
class SourceRef:
    supermarket_id: str
    keys: tuple[str, ...]
    ean: str | None


# --------------------------------------------------------------- catálogo

def row_key(row_id: object) -> str:
    text = str(row_id or "")
    return text.split("-", 1)[-1]


def parse_catalog_rows(document: Mapping[str, object]) -> list[CatalogRow]:
    rows: list[CatalogRow] = []
    for row in document.get("rows") or ():
        if not isinstance(row, Mapping) or not row.get("row_id"):
            continue
        offers: list[tuple[str, int]] = []
        branches: list[str] = []
        for offer in row.get("offers") or ():
            if not isinstance(offer, Mapping):
                continue
            # SPS: "walmart:51"; Tegucigalpa agrega la sucursal: "la_colonia:24@la_colonia_tgu".
            source_id = str(offer.get("source_product_id") or "")
            supermarket, _, product = source_id.partition(":")
            product = product.split("@", 1)[0]
            if supermarket and product.isdigit() and (supermarket, int(product)) not in offers:
                offers.append((supermarket, int(product)))
            if "@" in source_id and source_id not in branches:
                branches.append(source_id)
        if offers:
            rows.append(CatalogRow(row_key(row["row_id"]), tuple(offers), tuple(branches),
                                   str(row["row_id"]).startswith("source-")))
    return rows


def merge_rows(groups: Iterable[Sequence[CatalogRow]]) -> list[CatalogRow]:
    """Une las filas de varias ciudades: el mismo producto suma las ofertas de ambas."""
    merged: dict[str, tuple[list[tuple[str, int]], list[str], list[bool]]] = {}
    for rows in groups:
        for row in rows:
            offers, branches, source = merged.setdefault(row.key, ([], [], []))
            offers.extend(offer for offer in row.offers if offer not in offers)
            branches.extend(item for item in row.branch_ids if item not in branches)
            source.append(row.source_row)
    return [CatalogRow(key, tuple(offers), tuple(branches), all(source))
            for key, (offers, branches, source) in merged.items()]


def load_catalogs(fetch_json: Callable[[str], object], base: str) -> list[CatalogRow]:
    """Catálogo de todas las ciudades publicadas (``cities.json``); sin índice, sólo el base."""
    try:
        index = fetch_json(base + "cities.json")
    except Exception:  # noqa: BLE001 - sin índice de ciudades se usa el manifest base
        index = None
    manifests = ["manifest.json"]
    if isinstance(index, Mapping) and isinstance(index.get("cities"), list):
        manifests = [
            str(city["manifest"]) for city in index["cities"]
            if isinstance(city, Mapping) and isinstance(city.get("manifest"), str)
            and city["manifest"].endswith("manifest.json") and ".." not in city["manifest"]
            and not city["manifest"].startswith("/") and "://" not in city["manifest"]
        ] or manifests
    groups = []
    for manifest in manifests:
        folder = manifest[: -len("manifest.json")]
        groups.append(load_catalog(fetch_json, base + folder))
    return merge_rows(groups)


def load_catalog(fetch_json: Callable[[str], object], base: str) -> list[CatalogRow]:
    manifest = fetch_json(base + "manifest.json")
    if not isinstance(manifest, Mapping) or not isinstance(manifest.get("files"), list):
        raise PhotoUpdateError("catalog_manifest_invalid")
    rows: list[CatalogRow] = []
    for item in manifest["files"]:
        path = str(item.get("path", "")) if isinstance(item, Mapping) else ""
        if path.startswith("catalog/"):
            document = fetch_json(base + path)
            if not isinstance(document, Mapping):
                raise PhotoUpdateError("catalog_partition_invalid")
            rows.extend(parse_catalog_rows(document))
    if not rows:
        raise PhotoUpdateError("catalog_empty")
    return rows


# ------------------------------------------------------------ llaves fuente

def fetch_source_refs(backend, product_ids: Iterable[int]) -> dict[int, SourceRef]:
    ids = sorted(set(product_ids))
    refs: dict[int, SourceRef] = {}
    for start in range(0, len(ids), QUERY_BATCH):
        batch = ids[start:start + QUERY_BATCH]
        marks = ",".join("?" for _ in batch)
        rows = backend.query(
            "SELECT product_id, supermarket_id, source_item_id, source_key, source_catalog_product_id, ean "
            f"FROM products WHERE product_id IN ({marks})",
            batch,
        )
        for product_id, supermarket, *keys, ean in rows:
            values = tuple(dict.fromkeys(str(value).strip() for value in keys if value is not None and str(value).strip()))
            refs[int(product_id)] = SourceRef(str(supermarket), values, str(ean).strip() if ean else None)
    return refs


def needed_vtex_ids(rows: Iterable[CatalogRow], refs: Mapping[int, SourceRef], supermarket: str) -> list[str]:
    ids: set[str] = set()
    for row in rows:
        for store, product in row.offers:
            ref = refs.get(product)
            if store == supermarket and ref:
                ids.update(key for key in ref.keys if key.isdigit())
    return sorted(ids, key=int)


# --------------------------------------------------------------- candidatas

def row_candidates(row: CatalogRow, refs: Mapping[int, SourceRef],
                   maps: Mapping[str, Mapping[str, Sequence[str]]]) -> list[images.Candidate]:
    eans = [refs[product].ean for _, product in row.offers if product in refs and refs[product].ean]
    shared = {ean for ean, count in Counter(eans).items() if count >= 2}
    candidates: list[images.Candidate] = []
    for store, product in row.offers:
        ref = refs.get(product)
        urls = maps.get(store)
        if not ref or urls is None:
            continue
        found: Sequence[str] = ()
        for key in ref.keys:
            if urls.get(key):
                found = urls[key]
                break
        gtin = bool(ref.ean) and (len(row.offers) == 1 or ref.ean in shared)
        for position, url in enumerate(found[:sources.MAX_IMAGES_PER_ITEM]):
            candidates.append(images.Candidate(store, position, url, gtin))
    return candidates


def signature(candidates: Sequence[images.Candidate]) -> str:
    text = "\n".join(sorted(f"{item.supermarket_id} {item.position} {item.url}" for item in candidates))
    return hashlib.sha256(f"{PIPELINE_VERSION}\n{text}".encode()).hexdigest()[:12]


def generic_urls(all_candidates: Mapping[str, Sequence[images.Candidate]]) -> set[str]:
    owners: dict[str, set[str]] = {}
    for key, candidates in all_candidates.items():
        for candidate in candidates:
            owners.setdefault(candidate.url, set()).add(key)
    return {url for url, keys in owners.items() if len(keys) >= GENERIC_URL_OWNERS}


def plan(rows: Sequence[CatalogRow], candidates: Mapping[str, Sequence[images.Candidate]],
         index: Mapping[str, Mapping[str, object]], max_products: int,
         unavailable: set[str] = frozenset()) -> list[str]:
    """Filas nuevas o cambiadas; primero las de más súper (más vistas al comparar).

    Una fila con algún súper que no respondió esta semana se deja para la próxima:
    así no se reemplaza una buena foto por otra peor sólo por un fallo temporal.
    """
    pending = [
        row for row in rows
        if candidates.get(row.key)
        and not any(store in unavailable for store, _ in row.offers)
        and ((index.get(row.key) or {}).get("sig") != signature(candidates[row.key])
             or needs_rescore(index.get(row.key) or {}))
    ]
    pending.sort(key=lambda row: (-len({store for store, _ in row.offers}), row.key))
    return [row.key for row in pending[:max(0, max_products)]]


def needs_rescore(item: Mapping[str, object]) -> bool:
    """Con criterios anteriores: sin foto, o foto con puntaje bajo. Se reevalúa una vez."""
    try:
        if int(item.get("c", 0)) >= CRITERIA_VERSION:  # type: ignore[arg-type]
            return False
        if item.get("none"):
            return True
        return "h" in item and int(item.get("s", 0)) < RESCORE_BELOW  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------- proceso

@dataclass
class Downloaded:
    candidate: images.Candidate
    metrics: images.Metrics | None
    path: Path | None
    error: str | None = None


def _fetch(client, candidate: images.Candidate, folder: Path, number: int) -> Downloaded:
    try:
        data = client.download(sources.download_url(candidate.supermarket_id, candidate.url))
        image = images.load_image(data)
        metrics = images.measure(image, len(data))
    except (sources.ImageSourceError, images.ProductImageError) as error:
        return Downloaded(candidate, None, None, str(error))
    path = folder / f"{number}.bin"
    path.write_bytes(data)
    return Downloaded(candidate, metrics, path)


def download_row(client, candidates: Sequence[images.Candidate], folder: Path, start: int,
                 generic: set[str]) -> list[Downloaded]:
    """Principal de cada súper; la secundaria sólo si la principal no sirve."""
    result: list[Downloaded] = []
    by_store: dict[str, list[images.Candidate]] = {}
    for candidate in candidates:
        if candidate.url not in generic:
            by_store.setdefault(candidate.supermarket_id, []).append(candidate)
    for store_candidates in by_store.values():
        for candidate in sorted(store_candidates, key=lambda item: item.position):
            item = _fetch(client, candidate, folder, start + len(result))
            result.append(item)
            if item.metrics is not None and images.score(candidate, item.metrics)[0] >= images.MIN_SCORE:
                break
    return result


def image_paths(key: str, digest: str) -> dict[int, str]:
    return {size: f"{size}/{key[:2]}/{key}-{digest}.webp" for size in images.OUTPUT_SIZES}


def _review_record(key: str, scored: Sequence[images.Scored], chosen: images.Scored | None,
                   paths: Mapping[int, str] | None) -> dict[str, object]:
    def describe(item: images.Scored) -> dict[str, object]:
        metrics = item.metrics
        return {
            "sm": item.candidate.supermarket_id, "pos": item.candidate.position, "url": item.candidate.url,
            "s": item.score, "flags": list(item.flags),
            "q": metrics.quality if metrics else None, "px": metrics.product_pixels if metrics else None,
            "eff": metrics.effective if metrics else None,
            "size": f"{metrics.width}x{metrics.height}" if metrics else None,
        }
    return {
        "key": key, "chosen": describe(chosen) if chosen else None,
        "file": paths.get(320) if paths else None, "candidates": [describe(item) for item in scored],
    }


def process(keys: Sequence[str], candidates: Mapping[str, Sequence[images.Candidate]], client,
            output_dir: Path, index: dict[str, dict[str, object]], *, log=print,
            review: list[dict[str, object]] | None = None, deadline: float | None = None,
            clock=time.monotonic) -> Counter:
    """Descarga, puntúa y recorta las filas planeadas.

    Se detiene limpiamente (y deja índice y artifact completos) cuando se agota el
    presupuesto de descargas o cuando el reloj pasa ``deadline`` (time.monotonic):
    la corrida de 8 000 productos tomó 3 h 47 min y el job tiene 5 h, así que lo
    que no alcanza queda para la siguiente corrida en vez de perderlo todo.
    """
    stats: Counter = Counter()
    generic = generic_urls(candidates)
    with tempfile.TemporaryDirectory(prefix="fotos-") as temporary:
        folder = Path(temporary)
        downloaded: dict[str, list[Downloaded]] = {}
        count = 0
        for number, key in enumerate(keys, 1):
            if client.requests + 2 * len(candidates[key]) > client.max_requests:
                log(f"presupuesto de descargas agotado tras {number - 1} filas; el resto sigue en la próxima corrida")
                stats["filas_pospuestas"] = len(keys) - number + 1
                break
            if deadline is not None and clock() >= deadline:
                log(f"tiempo agotado tras {number - 1} filas; el resto sigue en la próxima corrida")
                stats["filas_pospuestas"] = len(keys) - number + 1
                stats["tiempo_agotado"] = 1
                break
            downloaded[key] = download_row(client, candidates[key], folder, count, generic)
            count += len(downloaded[key])
            if number % 250 == 0:
                log(f"descargadas {number}/{len(keys)} filas ({count} fotos)")
        duplicates = images.duplicate_keys(
            (key, item.metrics) for key, items in downloaded.items() for item in items if item.metrics is not None
        )
        for key in downloaded:
            sig = signature(candidates[key])
            scored = []
            for item in downloaded[key]:
                duplicate = item.metrics is not None and images.metrics_key(item.metrics) in duplicates
                value, flags = images.score(item.candidate, item.metrics, duplicate=duplicate)
                scored.append(images.Scored(item.candidate, item.metrics, value, flags))
                stats["error_descarga" if item.error else "foto_medida"] += 1
            chosen = images.choose(scored)
            paths: dict[int, str] | None = None
            if chosen is not None:
                path = next(item.path for item in downloaded[key] if item.candidate == chosen.candidate)
                try:
                    rendered = images.render(images.load_image(path.read_bytes()), chosen.metrics.bbox)
                except images.ProductImageError:
                    chosen = None
            if chosen is None:
                index[key] = {"sig": sig, "none": 1, "c": CRITERIA_VERSION}
                stats["sin_foto"] += 1
                if review is not None:
                    review.append(_review_record(key, scored, None, None))
                continue
            digest = hashlib.sha256(rendered[images.OUTPUT_SIZE]).hexdigest()[:10]
            paths = image_paths(key, digest)
            for size, relative in paths.items():
                target = output_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(rendered[size])
                stats[f"bytes_{size}"] += len(rendered[size])
            quality = chosen.metrics.quality
            index[key] = {"sig": sig, "h": digest, "sm": chosen.candidate.supermarket_id, "s": chosen.score,
                          "q": quality, "px": chosen.metrics.product_pixels, "c": CRITERIA_VERSION}
            stats["con_foto"] += 1
            stats[f"calidad_{quality}"] += 1
            stats[f"elegida_{chosen.candidate.supermarket_id}"] += 1
            if review is not None:
                review.append(_review_record(key, scored, chosen, paths))
    return stats


# ------------------------------------------------------ fotos por sucursal

def card_key(public_id: str) -> str:
    """Llave de tarjeta de un producto sin identidad comparable (``source-<llave>``)."""
    return hashlib.sha256(public_id.encode("utf-8")).hexdigest()[:24]


def _number(value: object) -> int:
    try:
        return int(value or 0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _usable(item: object) -> bool:
    return isinstance(item, Mapping) and isinstance(item.get("h"), str) and not item.get("none")


def branch_photo_moves(rows: Sequence[CatalogRow], index: Mapping[str, Mapping[str, object]]) -> list[tuple[str, str]]:
    """``(llave por producto, llave por sucursal con foto)`` que hay que heredar.

    Tegucigalpa publicaba una tarjeta por sucursal (llave de "walmart:51@walmart_tgu_ffaa")
    y ahora una por producto (llave de "walmart:51", la misma que en SPS). Sirve antes
    y después del cambio: la llave nueva se calcula del producto y las viejas de cada
    oferta. Si la llave nueva ya tiene foto no se toca; si no, hereda la mejor de las
    viejas: primero la evaluada con los criterios más nuevos (``c``), luego la de mayor
    puntaje. Son las mismas fotos de los mismos súper: no se vuelve a pedir nada a las
    tiendas.
    """
    donors: dict[str, set[str]] = {}
    for row in rows:
        if not row.source_row or not row.branch_ids:
            continue
        products = {item.split("@", 1)[0] for item in row.branch_ids}
        if len(products) != 1:
            continue
        target = card_key(next(iter(products)))
        if _usable(index.get(target)) or (index.get(target) or {}).get("nh"):
            continue
        donors.setdefault(target, set()).update(
            key for key in (row.key, *(card_key(item) for item in row.branch_ids))
            if key != target and _usable(index.get(key))
        )
    return sorted(
        (target, max(keys, key=lambda key: (_number(index[key].get("c")), _number(index[key].get("s")), key)))
        for target, keys in donors.items() if keys
    )


def adopt_branch_photos(moves: Sequence[tuple[str, str]], index: dict[str, dict[str, object]], output_dir: Path,
                        fetch_bytes: Callable[[str], bytes], public_base: str, *, log=print,
                        workers: int = ADOPT_WORKERS) -> Counter:
    """Copia los WebP publicados de la llave vieja a la nueva y registra la herencia (``de``).

    Lee del propio bucket público (en paralelo: no son las tiendas, y una por una
    4 000 fotos tomaban ~1 h); verifica la huella del archivo de 800 px antes de
    escribirlo, así la subida (que exige huella e índice) recibe lo mismo que ya estaba.
    """

    def download(move: tuple[str, str]) -> dict[int, bytes] | None:
        _, donor = move
        try:
            return {size: fetch_bytes(f"{public_base}/{R2_PREFIX}{relative}")
                    for size, relative in image_paths(donor, str(index[donor]["h"])).items()}
        except Exception:  # noqa: BLE001 - una foto que no baja se deja para otra corrida
            return None

    stats: Counter = Counter()
    def downloaded(pool: ThreadPoolExecutor) -> Iterable[tuple[tuple[str, str], dict[int, bytes] | None]]:
        # De a tandas: así en memoria sólo hay unas decenas de fotos a la vez.
        for start in range(0, len(moves), ADOPT_CHUNK):
            chunk = moves[start:start + ADOPT_CHUNK]
            yield from zip(chunk, pool.map(download, chunk))

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for number, ((target, donor), files) in enumerate(downloaded(pool), 1):
            item = index[donor]
            digest = str(item["h"])
            if files is None:
                stats["herencia_error_descarga"] += 1
                continue
            if hashlib.sha256(files[images.OUTPUT_SIZE]).hexdigest()[:10] != digest:
                stats["herencia_huella_distinta"] += 1
                continue
            for size, relative in image_paths(target, digest).items():
                path = output_dir / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(files[size])
            index[target] = {**{key: value for key, value in item.items() if key != "de"}, "de": donor}
            stats["fotos_heredadas_de_sucursal"] += 1
            if number % 500 == 0:
                log(f"heredadas {number}/{len(moves)} fotos de tarjetas por sucursal")
    return stats


def drop_foreign_inherited(rows: Sequence[CatalogRow], index: dict[str, dict[str, object]],
                           output_dir: Path) -> int:
    """Quita la foto heredada de sucursal cuando es la foto propia de OTRO producto.

    La herencia supone que la tarjeta por sucursal tenía la foto del mismo producto.
    Si esa misma foto (huella) es la elegida, sin herencia, para una fila publicada
    cuyos productos no tienen nada en común con los de la fila que la heredó, la
    herencia está mal (medido el 2026-11-13: "Refrigeradora Durabrand" con la foto de
    "Imán de moscas Terro", "Premezcla Tres Leches" con la de "Detergente Ariel"). La
    fila queda sin foto y marcada ``nh`` (no heredar otra vez): se vuelve a evaluar con
    sus propias fotos en esta misma corrida o en la próxima. Nunca se elige otra foto
    por parecido.
    """
    products = {row.key: set(row.offers) for row in rows}
    owners: dict[str, list[str]] = {}
    for key, item in index.items():
        if key in products and _usable(item) and not item.get("de"):
            owners.setdefault(str(item["h"]), []).append(key)
    dropped = 0
    for key in sorted(index):
        item = index[key]
        if not item.get("de") or not _usable(item) or key not in products:
            continue
        digest = str(item["h"])
        others = [owner for owner in owners.get(digest, ()) if owner not in {key, item.get("de")}]
        if not others or any(products[owner] & products[key] for owner in others):
            continue
        for relative in image_paths(key, digest).values():
            (output_dir / relative).unlink(missing_ok=True)
        index[key] = {"sig": None, "none": 1, "c": CRITERIA_VERSION, "nh": 1}
        dropped += 1
    return dropped


def prune_generic(index: dict[str, dict[str, object]], output_dir: Path,
                  *, min_owners: int = images.GENERIC_OWNERS) -> int:
    """Quita del índice las fotos que comparten huella con ``min_owners`` productos o más.

    La comparación dentro de una corrida sólo ve las filas de esa corrida; ésta mira
    el índice completo, así un "sin imagen" que llega de a pocos también se detecta.
    Las filas quedan sin foto (marca ``gen``) y sus archivos de esta corrida se borran
    del artifact para que la subida no los rechace.
    """
    owners: dict[str, list[str]] = {}
    inherited: dict[str, list[str]] = {}
    for key, item in index.items():
        digest = item.get("h")
        if isinstance(digest, str) and not item.get("none"):
            # Una foto heredada de la tarjeta por sucursal es el mismo producto: no
            # cuenta como otro dueño, pero cae junto con la original si ésta es genérica.
            (inherited if item.get("de") else owners).setdefault(digest, []).append(key)
    pruned = 0
    for digest, keys in owners.items():
        if len(keys) < min_owners:
            continue
        for key in [*keys, *inherited.get(digest, ())]:
            for relative in image_paths(key, digest).values():
                (output_dir / relative).unlink(missing_ok=True)
            index[key] = {"sig": index[key].get("sig"), "none": 1, "c": CRITERIA_VERSION, "gen": 1}
            pruned += 1
    return pruned


# ------------------------------------------------------------------ índice

def parse_index(document: object) -> dict[str, dict[str, object]]:
    if not isinstance(document, Mapping) or document.get("schema") != INDEX_SCHEMA:
        return {}
    items = document.get("items")
    return {str(key): dict(value) for key, value in items.items() if isinstance(value, Mapping)} if isinstance(items, Mapping) else {}


def write_index(output_dir: Path, index: Mapping[str, Mapping[str, object]], stats: Mapping[str, int]) -> Path:
    document = {
        "schema": INDEX_SCHEMA,
        "pipeline": PIPELINE_VERSION,
        "updated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "with_photo": sum(1 for value in index.values() if value.get("h") and not value.get("none")),
        "quality": dict(Counter(str(value.get("q")) for value in index.values() if value.get("h"))),
        "last_run": dict(stats),
        "items": {key: index[key] for key in sorted(index)},
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "index.json"
    path.write_text(json.dumps(document, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return path


def select_review(records: Sequence[Mapping[str, object]], *, lowest: int = REVIEW_LOWEST,
                  sample: int = REVIEW_SAMPLE) -> list[Mapping[str, object]]:
    """Las peores elegidas, las sin foto y una muestra pareja del resto (determinista)."""
    chosen = [item for item in records if item.get("chosen")]
    chosen.sort(key=lambda item: (item["chosen"]["s"], item["key"]))
    worst = chosen[:lowest]
    rest = sorted(chosen[lowest:], key=lambda item: hashlib.sha256(str(item["key"]).encode()).hexdigest())
    missing = [item for item in records if not item.get("chosen")][:40]
    return [*worst, *rest[:sample], *missing]


def write_review(output_dir: Path, records: Sequence[Mapping[str, object]], public_base: str,
                 stats: Mapping[str, int]) -> Path:
    """Página estática para revisar a ojo las fotos de la corrida (sin scripts)."""
    from html import escape

    def card(item: Mapping[str, object]) -> str:
        chosen = item.get("chosen")
        image = (f'<img src="{escape(public_base)}/{R2_PREFIX}{escape(str(item["file"]))}" width="160" height="160" '
                 f'loading="lazy" alt="">') if item.get("file") else '<div class="none">sin foto</div>'
        rows = "".join(
            f'<li class="{"win" if chosen and candidate["url"] == chosen["url"] else ""}">'
            f'<a href="{escape(str(candidate["url"]))}" rel="noreferrer">{escape(str(candidate["sm"]))}'
            f'{" #2" if candidate["pos"] else ""}</a> {candidate["s"]} pts · {escape(str(candidate["q"]))} · '
            f'{candidate["px"]} px · ×{candidate["eff"]} · {escape(str(candidate["size"]))}'
            f'{" · " + escape(", ".join(candidate["flags"])) if candidate["flags"] else ""}</li>'
            for candidate in item.get("candidates") or ()
        )
        return f'<article>{image}<p><b>{escape(str(item["key"]))}</b></p><ul>{rows}</ul></article>'

    summary = " · ".join(f"{escape(str(key))}: {value}" for key, value in sorted(stats.items()))
    html = (
        '<!doctype html><html lang="es"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>Revisión de fotos</title>'
        '<style>body{font:14px system-ui,sans-serif;margin:16px;background:#f4f5f2;color:#1b1f1d}'
        'main{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px}'
        'article{background:#fff;border:1px solid #dde1dc;border-radius:10px;padding:10px}'
        'img,.none{display:block;margin:auto;background:#fff}.none{width:160px;height:160px;line-height:160px;'
        'text-align:center;color:#999;border:1px dashed #ccc}ul{padding-left:16px;margin:6px 0;font-size:12px}'
        'li.win{font-weight:700;color:#0f6b4f}p{margin:6px 0;font-size:12px}</style></head><body>'
        f'<h1>Revisión de fotos</h1><p>{summary}</p><p>Primero las elegidas con menor puntaje, luego una muestra '
        'y al final productos sin foto aceptable.</p><main>'
        + "".join(card(item) for item in select_review(records)) + "</main></body></html>"
    )
    path = output_dir / "revision.html"
    path.write_text(html, encoding="utf-8")
    return path


# ------------------------------------------------------------ autorización

AUTHORIZATION_SCHEMA = "precios-sps-product-images-authorization/v1"


def load_authorization(path: Path, *, api_delay: float, image_delay: float, max_products: int,
                       max_image_requests: int) -> dict[str, object]:
    """Exige la aprobación registrada del responsable y que la corrida quepa en ella."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PhotoUpdateError("authorization_unreadable") from error
    checks = (
        (isinstance(document, dict) and document.get("schema") == AUTHORIZATION_SCHEMA, "authorization_schema_invalid"),
        (isinstance(document, dict) and document.get("live_read_only_authorized") is True, "authorization_revoked"),
        (isinstance(document, dict) and document.get("approved_by") == "project_owner", "authorization_approver_invalid"),
        (isinstance(document, dict) and document.get("concurrency") == 1, "authorization_concurrency_invalid"),
    )
    for ok, code in checks:
        if not ok:
            raise PhotoUpdateError(code)
    limits = (
        (api_delay >= float(document.get("min_api_delay_seconds", 1e9)), "api_delay_below_authorized"),
        (image_delay >= float(document.get("min_image_delay_seconds", 1e9)), "image_delay_below_authorized"),
        (max_products <= int(document.get("max_products_per_run", 0)), "max_products_above_authorized"),
        (max_image_requests <= int(document.get("max_image_requests_per_run", 0)), "image_requests_above_authorized"),
    )
    for ok, code in limits:
        if not ok:
            raise PhotoUpdateError(code)
    origins = set(document.get("allowed_api_origins") or ())
    if not set(sources.STORE_ORIGINS.values()) | {sources.los_andes.API_BASE} <= origins:
        raise PhotoUpdateError("authorization_origins_invalid")
    return document


# -------------------------------------------------------------------- main

def fetch_maps(api, rows: Sequence[CatalogRow], refs: Mapping[int, SourceRef], stores: set[str],
               *, log=print) -> dict[str, dict[str, list[str]]]:
    maps: dict[str, dict[str, list[str]]] = {}
    for store in sorted(stores):
        started = time.monotonic()
        try:
            if store in sources.VTEX_STORES:
                maps[store] = api.vtex(store, needed_vtex_ids(rows, refs, store))
            elif store == "colonial":
                maps[store] = api.colonial()
            elif store == "comisariato_los_andes":
                maps[store] = api.los_andes()
            elif store == "pricesmart":
                maps[store] = api.pricesmart()
        except sources.ImageSourceError as error:
            log(f"{store}: sin fotos esta semana ({error})")
            continue
        if store in maps:
            log(f"{store}: {len(maps[store])} referencias con foto en {time.monotonic() - started:.0f}s")
    return maps


def _http_json(session, missing_ok: bool = False):
    def fetch(url: str) -> object:
        response = session.get(url, timeout=60)
        if missing_ok and response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()
    return fetch


def main(argv: Sequence[str] | None = None) -> int:
    started = time.monotonic()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--authorization", type=Path,
                        default=ROOT / ".automation" / "product-images-capture-authorization.json")
    parser.add_argument("--catalog-base", default=CATALOG_BASE)
    parser.add_argument("--index-url", default=INDEX_URL)
    parser.add_argument("--public-base", default=PUBLIC_BASE)
    parser.add_argument("--max-products", type=int, default=DEFAULT_MAX_PRODUCTS)
    parser.add_argument("--api-delay", type=float, default=1.0)
    parser.add_argument("--image-delay", type=float, default=0.15)
    parser.add_argument("--max-image-requests", type=int, default=12000)
    parser.add_argument("--time-budget-minutes", type=float, default=DEFAULT_TIME_BUDGET_MINUTES,
                        help="minutos desde el arranque tras los cuales ya no se empiezan filas nuevas (0 = sin límite)")
    args = parser.parse_args(argv)
    authorization = load_authorization(
        args.authorization, api_delay=args.api_delay, image_delay=args.image_delay,
        max_products=args.max_products, max_image_requests=args.max_image_requests,
    )
    print(f"autorización: {authorization['authorization_id']}")

    import requests

    from exportar_modelo_analitico import TursoBackend

    public = requests.Session()
    public.headers["User-Agent"] = sources.USER_AGENT
    rows = load_catalogs(_http_json(public), args.catalog_base)
    index = parse_index(_http_json(public, missing_ok=True)(args.index_url))
    print(f"catálogo: {len(rows)} filas; índice previo: {len(index)} filas")
    moves = branch_photo_moves(rows, index)

    def fetch_bytes(url: str) -> bytes:
        response = public.get(url, timeout=60)
        response.raise_for_status()
        return response.content

    inherited = adopt_branch_photos(moves, index, args.output_dir, fetch_bytes, args.public_base.rstrip("/"))
    print(f"fotos por sucursal heredadas: {dict(inherited)} de {len(moves)}")
    foreign = drop_foreign_inherited(rows, index, args.output_dir)
    if foreign:
        inherited["herencia_de_otro_producto"] = foreign
    print(f"fotos heredadas que eran de otro producto (quedan para reevaluar): {foreign}")

    backend = TursoBackend(os.environ.get("TURSO_DATABASE_URL", ""), os.environ.get("TURSO_AUTH_TOKEN", ""))
    refs = fetch_source_refs(backend, (product for row in rows for _, product in row.offers))
    stores = {store for row in rows for store, _ in row.offers} & set(images.RETAILER_PRIORITY)
    api = sources.ImageSourceClient(requests.Session(), delay=args.api_delay)
    maps = fetch_maps(api, rows, refs, stores)
    candidates = {row.key: row_candidates(row, refs, maps) for row in rows}
    keys = plan(rows, candidates, index, args.max_products, unavailable=stores - set(maps))
    print(f"pendientes esta corrida: {len(keys)}")

    downloader = sources.ImageSourceClient(requests.Session(), delay=args.image_delay,
                                           max_requests=args.max_image_requests)
    review: list[dict[str, object]] = []
    deadline = started + args.time_budget_minutes * 60 if args.time_budget_minutes > 0 else None
    stats = process(keys, candidates, downloader, args.output_dir, index, review=review, deadline=deadline)
    stats.update(inherited)
    stats.update({f"error_{name}": 1 for name in api.errors})
    pruned = prune_generic(index, args.output_dir)
    if pruned:
        stats["genericas_indice"] = pruned
    path = write_index(args.output_dir, index, stats)
    write_review(args.output_dir, review, args.public_base.rstrip("/"), stats)
    print(json.dumps({"stats": dict(stats), "index": str(path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
