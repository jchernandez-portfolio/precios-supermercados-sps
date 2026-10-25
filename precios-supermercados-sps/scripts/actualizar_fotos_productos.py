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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from precios_supermercados import product_image_sources as sources  # noqa: E402
from precios_supermercados import product_images as images  # noqa: E402

PIPELINE_VERSION = 2
INDEX_SCHEMA = "super-compras-product-images/v2"
REPOSITORY_RAW = "https://raw.githubusercontent.com/jchernandez-portfolio/precios-supermercados-sps"
CATALOG_BASE = f"{REPOSITORY_RAW}/portfolio-data/precios-supermercados-sps/published/rpi/v3/"
# Dirección pública del bucket R2 (no es secreta); las fotos viven bajo fotos/v2/.
PUBLIC_BASE = "https://pub-0dcb2204775c484dbe6681e659eee236.r2.dev"
R2_PREFIX = "fotos/v2/"
INDEX_URL = f"{PUBLIC_BASE}/{R2_PREFIX}index.json"
DEFAULT_MAX_PRODUCTS = 8000
REVIEW_LOWEST = 120
REVIEW_SAMPLE = 80
GENERIC_URL_OWNERS = 3
QUERY_BATCH = 400
KEY_COLUMNS = ("source_item_id", "source_key", "source_catalog_product_id")


class PhotoUpdateError(RuntimeError):
    """Entrada inválida para la corrida de fotos."""


@dataclass(frozen=True)
class CatalogRow:
    key: str
    offers: tuple[tuple[str, int], ...]


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
        for offer in row.get("offers") or ():
            if not isinstance(offer, Mapping):
                continue
            # SPS: "walmart:51"; Tegucigalpa agrega la sucursal: "la_colonia:24@la_colonia_tgu".
            supermarket, _, product = str(offer.get("source_product_id") or "").partition(":")
            product = product.split("@", 1)[0]
            if supermarket and product.isdigit() and (supermarket, int(product)) not in offers:
                offers.append((supermarket, int(product)))
        if offers:
            rows.append(CatalogRow(row_key(row["row_id"]), tuple(offers)))
    return rows


def merge_rows(groups: Iterable[Sequence[CatalogRow]]) -> list[CatalogRow]:
    """Une las filas de varias ciudades: el mismo producto suma las ofertas de ambas."""
    merged: dict[str, list[tuple[str, int]]] = {}
    for rows in groups:
        for row in rows:
            offers = merged.setdefault(row.key, [])
            offers.extend(offer for offer in row.offers if offer not in offers)
    return [CatalogRow(key, tuple(offers)) for key, offers in merged.items()]


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
        and (index.get(row.key) or {}).get("sig") != signature(candidates[row.key])
    ]
    pending.sort(key=lambda row: (-len({store for store, _ in row.offers}), row.key))
    return [row.key for row in pending[:max(0, max_products)]]


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
            review: list[dict[str, object]] | None = None) -> Counter:
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
                index[key] = {"sig": sig, "none": 1}
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
                          "q": quality, "px": chosen.metrics.product_pixels}
            stats["con_foto"] += 1
            stats[f"calidad_{quality}"] += 1
            if review is not None:
                review.append(_review_record(key, scored, chosen, paths))
    return stats


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
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
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
    stats = process(keys, candidates, downloader, args.output_dir, index, review=review)
    stats.update({f"error_{name}": 1 for name in api.errors})
    path = write_index(args.output_dir, index, stats)
    write_review(args.output_dir, review, args.public_base.rstrip("/"), stats)
    print(json.dumps({"stats": dict(stats), "index": str(path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
