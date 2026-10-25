"""Direcciones de fotos de producto publicadas por cada supermercado.

Se usan las mismas referencias exactas que ya guarda la captura diaria
(``products.source_item_id`` / ``source_catalog_product_id``) y sólo endpoints
públicos de lectura, a ritmo bajo:

* Walmart y La Colonia (VTEX): ``catalog_system`` por ``skuId`` en lotes de 50;
* Colonial (Shopify): ``/products.json`` paginado (variante → fotos);
* Comisariato Los Andes: catálogo paginado (código → fotos);
* PriceSmart: búsqueda pública por categoría (pid/sku → foto).

Las funciones ``parse_*`` son puras y se prueban con fixtures; ``ImageSourceClient``
hace el tráfico con un ``requests.Session`` inyectable.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from precios_supermercados.scrapers import comisariato_los_andes as los_andes

USER_AGENT = "Mozilla/5.0 (compatible; PreciosSupermercadosSPS-Fotos/1.0; read-only)"
VTEX_BATCH = 50
SHOPIFY_PAGE = 250
SHOPIFY_MAX_PAGES = 200
PRICESMART_ROWS = 200
PRICESMART_MAX_PAGES = 60
MAX_IMAGES_PER_ITEM = 2
MAX_IMAGE_BYTES = 6_000_000
# VTEX agranda si se le pide más que el original (La Colonia guarda 1000 px y Walmart
# 3000 px): 1000 px da detalle real para la versión de 800 sin bajar fotos de 700 KB.
SOURCE_SIZE = 1000
SHOPIFY_WIDTH = 1200  # Shopify nunca agranda; Colonial guarda 1040 px.

STORE_ORIGINS = {
    "walmart": "https://www.walmart.com.hn",
    "la_colonia": "https://www.lacolonia.com",
    "colonial": "https://supercolonial.com",
    "pricesmart": "https://www.pricesmart.com",
    # Paiz comparte catálogo VTEX y fotos (walmarthn.vteximg.com.br) con Walmart.
    "paiz": "https://www.paiz.com.hn",
}
VTEX_STORES = frozenset({"walmart", "la_colonia", "paiz"})
PRICESMART_ENDPOINT = "https://www.pricesmart.com/api/br_discovery/getProductsByKeyword"
PRICESMART_PUBLIC = {
    "account_id": "7024",
    "auth_key": "ev7libhybjg5h1d1",
    "domain_key": "pricesmart_bloomreach_io_es",
    "view_id": "HN",
}
PRICESMART_ROOTS = (
    ("S10D45", "Productos de temporada"), ("G10D03", "Alimentos"), ("H30D22", "Hogar"),
    ("H20D09", "Salud y belleza"), ("G10D08014", "Licor, cerveza y vino"), ("P10D51", "Mascotas"),
    ("B10D27", "Bebé"), ("H10D21", "Ferretería y mejoras al hogar"), ("S30D26", "Deportes y fitness"),
    ("O20D30", "Exteriores"), ("E10D24", "Electrónicos"), ("S20D23", "Electrodomésticos"),
    ("C10D29", "Computadoras, tablets y accesorios"), ("M10D43", "Línea blanca"),
    ("F10D40", "Moda y accesorios"), ("F20D27", "Muebles"), ("O10D25", "Oficina"),
    ("R10D22", "Suministros para restaurantes"), ("A10D20", "Automotriz"), ("T10D46", "Juguetes y juegos"),
    ("L10D22", "Equipaje"), ("U10D72", "Óptica"), ("U11D13", "Audiología"),
    ("T20D42", "Películas, música y libros"), ("J10D44", "Joyería y relojes"),
)
ALLOWED_IMAGE_HOSTS = (
    "walmarthn.vteximg.com.br", "walmarthn.vtexassets.com", "lacolonia.vteximg.com.br",
    "lacolonia.vtexassets.com", "cdn.shopify.com", "comisariatolosandesfiles.s3.us-east-2.amazonaws.com",
    "d31f1ehqijlcua.cloudfront.net",
)


class ImageSourceError(ValueError):
    """Respuesta pública inesperada al buscar fotos."""


def _https(url: object) -> str | None:
    if not isinstance(url, str) or not url.strip():
        return None
    value = url.strip()
    if value.startswith("//"):
        value = "https:" + value
    parts = urlsplit(value)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_IMAGE_HOSTS:
        return None
    return value


def _dedupe(urls: Iterable[str | None]) -> list[str]:
    result: list[str] = []
    for url in urls:
        if url and url not in result:
            result.append(url)
    return result[:MAX_IMAGES_PER_ITEM]


# ------------------------------------------------------------------- tamaños

_VTEX_IDS = re.compile(r"(/arquivos/ids/\d+)(?:-\d+-\d+)?/")


def vtex_resized(url: str, size: int = SOURCE_SIZE) -> str:
    """VTEX entrega la foto ya reducida con ``/ids/<id>-<w>-<h>/``."""
    return _VTEX_IDS.sub(lambda match: f"{match.group(1)}-{size}-{size}/", url, count=1)


def shopify_resized(url: str, width: int = SHOPIFY_WIDTH) -> str:
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query) if key != "width"]
    query.append(("width", str(width)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def download_url(supermarket_id: str, url: str) -> str:
    if supermarket_id in VTEX_STORES:
        return vtex_resized(url)
    if supermarket_id == "colonial":
        return shopify_resized(url)
    return url


# ------------------------------------------------------------------- parsers

def parse_vtex_search(payload: object) -> dict[str, list[str]]:
    if not isinstance(payload, list):
        raise ImageSourceError("vtex_payload_invalid")
    result: dict[str, list[str]] = {}
    for product in payload:
        if not isinstance(product, Mapping):
            continue
        for item in product.get("items") or ():
            if not isinstance(item, Mapping):
                continue
            item_id = str(item.get("itemId") or "").strip()
            if not item_id.isdigit():
                continue
            images = [image.get("imageUrl") for image in item.get("images") or () if isinstance(image, Mapping)]
            result[item_id] = _dedupe(_https(url) for url in images)
    return result


def parse_shopify_products(payload: object) -> dict[str, list[str]]:
    products = payload.get("products") if isinstance(payload, Mapping) else None
    if not isinstance(products, list):
        raise ImageSourceError("shopify_payload_invalid")
    result: dict[str, list[str]] = {}
    for product in products:
        if not isinstance(product, Mapping):
            continue
        images = sorted(
            (image for image in product.get("images") or () if isinstance(image, Mapping)),
            key=lambda image: image.get("position") or 0,
        )
        for variant in product.get("variants") or ():
            if not isinstance(variant, Mapping) or variant.get("id") is None:
                continue
            variant_id = str(variant["id"])
            own = [image.get("src") for image in images if int(variant["id"]) in (image.get("variant_ids") or ())]
            rest = [image.get("src") for image in images]
            result[variant_id] = _dedupe(_https(url) for url in [*own, *rest])
    return result


def parse_los_andes_page(payload: object) -> tuple[dict[str, list[str]], int]:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), list):
        raise ImageSourceError("los_andes_payload_invalid")
    result: dict[str, list[str]] = {}
    for product in payload["data"]:
        if not isinstance(product, Mapping) or not isinstance(product.get("code"), str):
            continue
        images = [image for image in product.get("images") or () if isinstance(image, Mapping) and image.get("fileName")]
        images.sort(key=lambda image: (image.get("active") != "1", image.get("position") or 0))
        urls = [los_andes.IMAGE_BASE + str(image["fileName"]).strip().lstrip("/") for image in images]
        result[product["code"].strip()] = _dedupe(_https(url) for url in urls)
    total = payload.get("totalItems")
    return result, total if isinstance(total, int) else 0


def parse_pricesmart_docs(payload: object) -> tuple[dict[str, list[str]], int]:
    response = payload.get("response") if isinstance(payload, Mapping) else None
    if not isinstance(response, Mapping) or not isinstance(response.get("docs"), list):
        raise ImageSourceError("pricesmart_payload_invalid")
    result: dict[str, list[str]] = {}
    for doc in response["docs"]:
        if not isinstance(doc, Mapping):
            continue
        url = _https(doc.get("thumb_image"))
        if not url:
            continue
        keys = {str(doc.get("pid") or ""), str(doc.get("skuid") or "")}
        keys.update(str(variant.get("skuid") or "") for variant in doc.get("variants") or () if isinstance(variant, Mapping))
        for key in keys - {""}:
            result[key] = [url]
    found = response.get("numFound")
    return result, found if isinstance(found, int) else 0


# -------------------------------------------------------------------- cliente

def _ascii_slug(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "-", ascii_name).strip("-")


class ImageSourceClient:
    """Tráfico público y secuencial con pausa fija entre solicitudes."""

    def __init__(self, session: Any, *, delay: float = 1.0, sleep: Callable[[float], None] = time.sleep,
                 max_requests: int = 3000) -> None:
        self.session = session
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.delay = delay
        self.sleep = sleep
        self.max_requests = max_requests
        self.requests = 0
        self.errors: list[str] = []

    def _send(self, method: str, url: str, **kwargs: Any) -> Any:
        for attempt in range(3):
            if self.requests >= self.max_requests:
                raise ImageSourceError("request_budget_exhausted")
            self.requests += 1
            if self.requests > 1:
                self.sleep(self.delay)
            try:
                response = self.session.request(method, url, timeout=30, **kwargs)
            except Exception as error:  # noqa: BLE001 - red pública: se reintenta y se informa
                if attempt == 2:
                    raise ImageSourceError(f"request_failed:{type(error).__name__}") from error
                self.sleep(5 * (attempt + 1))
                continue
            if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                self.sleep(10 * (attempt + 1))
                continue
            if response.status_code >= 400:
                raise ImageSourceError(f"http_{response.status_code}")
            return response
        raise ImageSourceError("request_retries_exhausted")

    def _json(self, method: str, url: str, **kwargs: Any) -> object:
        response = self._send(method, url, **kwargs)
        try:
            return response.json()
        except ValueError as error:
            raise ImageSourceError("json_invalid") from error

    def vtex(self, supermarket_id: str, item_ids: Sequence[str]) -> dict[str, list[str]]:
        origin = STORE_ORIGINS[supermarket_id]
        result: dict[str, list[str]] = {}
        ids = sorted({str(item) for item in item_ids if str(item).isdigit()}, key=int)
        for start in range(0, len(ids), VTEX_BATCH):
            batch = ids[start:start + VTEX_BATCH]
            query = "&".join(f"fq=skuId:{item}" for item in batch)
            url = f"{origin}/api/catalog_system/pub/products/search?{query}&_from=0&_to={VTEX_BATCH - 1}"
            try:
                result.update(parse_vtex_search(self._json("GET", url)))
            except ImageSourceError as error:
                self.errors.append(f"{supermarket_id}:{error}")
        return result

    def colonial(self) -> dict[str, list[str]]:
        origin = STORE_ORIGINS["colonial"]
        result: dict[str, list[str]] = {}
        for page in range(1, SHOPIFY_MAX_PAGES + 1):
            payload = self._json("GET", f"{origin}/products.json?limit={SHOPIFY_PAGE}&page={page}")
            parsed = parse_shopify_products(payload)
            if not payload.get("products"):  # type: ignore[union-attr]
                break
            result.update(parsed)
        return result

    def los_andes(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        skip = 0
        while True:
            url, body = los_andes.build_catalog_request(skip)
            payload = self._json("POST", url, data=json.dumps(body), headers={"Content-Type": "application/json"})
            page, total = parse_los_andes_page(payload)
            result.update(page)
            skip += los_andes.PAGE_SIZE
            if not page or skip >= total:
                break
        return result

    def pricesmart(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for key, name in PRICESMART_ROOTS:
            for page in range(PRICESMART_MAX_PAGES):
                query = {
                    "url": f"https://www.pricesmart.com/es-hn/categoria/{_ascii_slug(name)}-{key}/{key}",
                    "start": page * PRICESMART_ROWS, "q": key, "fq": [], "search_type": "category",
                    "rows": PRICESMART_ROWS, "request_id": int(time.time() * 1000),
                    "fl": "pid,title,thumb_image,skuid", **PRICESMART_PUBLIC,
                }
                try:
                    payload = self._json("POST", PRICESMART_ENDPOINT, data=json.dumps([query]),
                                         headers={"Content-Type": "application/json"})
                    docs, found = parse_pricesmart_docs(payload)
                except ImageSourceError as error:
                    self.errors.append(f"pricesmart:{key}:{error}")
                    break
                result.update(docs)
                if (page + 1) * PRICESMART_ROWS >= found or not docs:
                    break
        return result

    def download(self, url: str) -> bytes:
        if not _https(url):
            raise ImageSourceError("image_host_not_allowed")
        response = self._send("GET", url, stream=True)
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > MAX_IMAGE_BYTES:
                raise ImageSourceError("image_too_large")
            chunks.append(chunk)
        return b"".join(chunks)
