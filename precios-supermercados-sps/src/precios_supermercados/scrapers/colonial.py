"""Contrato público Colonial: JSON para identidad/precios y tarjetas para stock.

Shopify `available` no representa el botón Agotado de este comercio. Nunca se
usa como sustituto de la evidencia de disponibilidad del catálogo HTML.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

from ..identifiers import canonicalize_gtin

ORIGIN = "https://supercolonial.com"
SECTION = "template--25869947109668__banner"


class ColonialError(ValueError):
    pass


def price(value: object, *, nullable: bool = False) -> str | None:
    if nullable and value in (None, ""):
        return None
    if not isinstance(value, str) or value != value.strip():
        raise ColonialError("price_invalid")
    try:
        number = Decimal(value)
        if not number.is_finite() or number < 0 or number * 100 != (number * 100).to_integral_value():
            raise ColonialError("price_invalid")
    except InvalidOperation as exc:
        raise ColonialError("price_invalid") from exc
    return format(number, ".2f")


EAN_SOURCES = ("barcode", "sku_gs1_valid")


def declared_barcode(value: object) -> str | None:
    """Conserva sólo un barcode Shopify explícito que supera el check digit GS1.

    Un barcode con espacios externos se recorta; uno no numérico, de longitud no
    GS1 o con check digit inválido queda en ``None``. Los ceros a la izquierda se
    conservan tal cual vienen de la fuente.
    """
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or canonicalize_gtin(candidate) is None:
        return None
    return candidate


def gtin_from_sku(value: object) -> str | None:
    """SKU Colonial aceptado como GTIN (aprobado 2026-10-01) sólo si es GS1 válido.

    La captura completa 2026-08-30 tiene ``barcode=null`` en las 9,205 variantes y
    el código UPC/EAN vive en ``sku``: 8,501 SKU pasan el check digit (al azar
    pasaría ~10%). Se acepta sólo un SKU recortado, todo dígitos, de longitud
    8/12/13/14 y con check digit válido; cualquier otro SKU nunca produce GTIN.
    """
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate.isdigit() or len(candidate) not in {8, 12, 13, 14}:
        return None
    return candidate if canonicalize_gtin(candidate) is not None else None


def ean_with_source(barcode: object, sku: object) -> tuple[str | None, str | None]:
    """Devuelve ``(ean, ean_source)``; el barcode explícito tiene prioridad."""
    explicit = declared_barcode(barcode)
    if explicit is not None:
        return explicit, "barcode"
    derived = gtin_from_sku(sku)
    if derived is not None:
        return derived, "sku_gs1_valid"
    return None, None


def ean_source(row: dict) -> str | None:
    """Procedencia de ``ean`` reconstruida desde una fila del snapshot.

    El contrato de snapshot/persistencia tiene un conjunto cerrado de llaves, así
    que la procedencia no viaja como columna: en Colonial ``ean == reference`` (el
    SKU recortado) identifica un GTIN derivado del SKU.
    """
    ean = row.get("ean")
    if ean is None:
        return None
    reference = row.get("reference")
    if isinstance(reference, str) and reference.strip() == ean:
        return "sku_gs1_valid"
    return "barcode"


def _id(value: object) -> str:
    if type(value) is not int or value <= 0:
        raise ColonialError("source_id_invalid")
    return str(value)


def parse_products(raw: bytes) -> list[dict]:
    try:
        payload = json.loads(raw)
        products = payload["products"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ColonialError("products_shape_invalid") from exc
    if not isinstance(products, list):
        raise ColonialError("products_shape_invalid")
    result, ids, variants = [], set(), set()
    for product in products:
        if not isinstance(product, dict):
            raise ColonialError("product_invalid")
        pid = _id(product.get("id"))
        handle, name = product.get("handle"), product.get("title")
        if pid in ids or not isinstance(handle, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", handle):
            raise ColonialError("product_identity_invalid")
        if not isinstance(name, str) or not name.strip():
            raise ColonialError("product_name_invalid")
        ids.add(pid)
        items = product.get("variants")
        if not isinstance(items, list) or not items:
            raise ColonialError("variants_missing")
        for item in items:
            if not isinstance(item, dict):
                raise ColonialError("variant_invalid")
            vid = _id(item.get("id"))
            if vid in variants or _id(item.get("product_id")) != pid:
                raise ColonialError("variant_identity_invalid")
            variants.add(vid)
            current = price(item.get("price"))
            regular = price(item.get("compare_at_price"), nullable=True)
            for value in (item.get("sku"), item.get("barcode"), product.get("vendor"), product.get("product_type")):
                if value is not None and not isinstance(value, str):
                    raise ColonialError("description_invalid")
            title = item.get("title")
            if not isinstance(title, str):
                raise ColonialError("variant_title_invalid")
            result.append({
                "product_id": pid, "item_id": vid, "source_key_type": "item_id",
                "source_key": vid, "source_name": name,
                "reference": item.get("sku") or None, "ean": ean_with_source(item.get("barcode"), item.get("sku"))[0],
                "brand": product.get("vendor") or None,
                "category": product.get("product_type") or None,
                "presentation": None,  # opciones numéricas observadas no prueban presentación
                "current_price": current, "reported_regular_price": regular,
                "is_promotion": regular is not None and Decimal(regular) > Decimal(current),
                "availability": "unknown", "handle": handle,
            })
    return result


class _Cards(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.grid = 0
        self.depth = 0
        self.card = None
        self.capture = None
        self.cards = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if tag == "ul" and (a.get("id") == "product-grid" or self.grid):
            self.grid += 1
        if not self.grid:
            return
        if tag == "div":
            if self.card is not None:
                self.depth += 1
            elif "cp_product_box" in classes:
                self.card = {"ids": [], "urls": set(), "price": "", "regular": "", "buttons": []}
                self.depth = 1
        if self.card is None:
            return
        if tag == "a" and (a.get("href") or "").startswith("/products/"):
            self.card["urls"].add(a["href"])
        if tag == "input" and a.get("name") == "id":
            self.card["ids"].append(a.get("value"))
        if "cp_product_price" in classes:
            self.capture = (tag, "price")
        if tag == "del":
            self.capture = (tag, "regular")
        if tag == "button" and "addtocart-btn" in classes:
            self.card["buttons"].append(("cp-sold-out" in classes, "disabled" in a, a.get("aria-disabled")))

    def handle_data(self, data: str) -> None:
        if self.card is not None and self.capture:
            self.card[self.capture[1]] += data

    def handle_endtag(self, tag: str) -> None:
        if self.capture and self.capture[0] == tag:
            self.capture = None
        if tag == "div" and self.card is not None:
            self.depth -= 1
            if self.depth == 0:
                self.cards.append(self.card)
                self.card = None
        if tag == "ul" and self.grid:
            self.grid -= 1


def parse_cards(raw: bytes) -> tuple[int, list[dict]]:
    html = raw.decode("utf-8")
    totals = set(re.findall(r"\b(\d+) productos\b", html))
    if len(totals) != 1 or int(next(iter(totals))) <= 0:
        raise ColonialError("collection_total_invalid")
    parser = _Cards()
    parser.feed(html)
    if parser.card is not None or parser.grid or not parser.cards:
        raise ColonialError("cards_missing_or_truncated")
    result, seen = [], set()
    for card in parser.cards:
        if len(card["ids"]) != 1 or len(card["urls"]) != 1 or len(card["buttons"]) != 1:
            raise ColonialError("card_shape_invalid")
        vid = card["ids"][0]
        url = next(iter(card["urls"]))
        if not isinstance(vid, str) or not vid.isdigit() or vid in seen or not re.fullmatch(r"/products/[a-z0-9][a-z0-9-]*", url):
            raise ColonialError("card_identity_invalid")
        seen.add(vid)
        sold, disabled, aria = card["buttons"][0]
        availability = "out_of_stock" if sold and disabled else "in_stock" if not sold and not disabled and aria != "true" else "unknown"
        def money(text: str, nullable: bool = False) -> str | None:
            value = text.strip()
            if not value and nullable:
                return None
            if not value.startswith("L "):
                raise ColonialError("card_currency_invalid")
            return price(value[2:].replace(",", ""))
        result.append({"item_id": vid, "handle": url.removeprefix("/products/"),
                       "availability": availability, "current_price": money(card["price"]),
                       "reported_regular_price": money(card["regular"], True)})
    return int(next(iter(totals))), result


def sitemap_urls(raw: bytes, *, index: bool = False) -> set[str]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ColonialError("sitemap_invalid") from exc
    ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
    if root.tag != ns + ("sitemapindex" if index else "urlset"):
        raise ColonialError("sitemap_shape_invalid")
    urls = set()
    for element in root.findall(f"{ns}{'sitemap' if index else 'url'}/{ns}loc"):
        value = element.text or ""
        u = urlparse(value)
        if u.scheme != "https" or u.netloc != "supercolonial.com":
            raise ColonialError("sitemap_origin_invalid")
        if (index and u.path.startswith("/sitemap_products_")) or (not index and u.path.startswith("/products/")):
            if value in urls:
                raise ColonialError("sitemap_duplicate")
            urls.add(value)
    if not urls:
        raise ColonialError("sitemap_empty")
    return urls


def reconcile(rows: list[dict], cards: list[dict], membership: set[str], expected: int) -> list[dict]:
    by_variant = {row["item_id"]: row for row in rows}
    by_handle = {}
    for row in rows:
        by_handle.setdefault(row["handle"], []).append(row)
    handles = {row["handle"] for row in rows}
    card_handles = {row["handle"] for row in cards}
    if len(by_variant) != len(rows) or len({c["item_id"] for c in cards}) != len(cards):
        raise ColonialError("duplicate_identity")
    expected_handles = {url.removeprefix(ORIGIN + "/products/") for url in membership}
    if len({row["product_id"] for row in rows}) != expected or len(handles) != expected:
        raise ColonialError("catalog_count_mismatch")
    if handles != expected_handles or handles != card_handles or len(cards) != expected:
        raise ColonialError("catalog_membership_mismatch")
    for card in cards:
        row = by_variant.get(card["item_id"])
        if row is None or row["handle"] != card["handle"]:
            raise ColonialError("card_variant_mismatch")
        # La tarjeta puede anunciar el precio de otra variante (mínimo del
        # producto) aunque su botón apunte a la primera. No copiar ese precio
        # sobre la variante del botón ni propagar su stock a variantes no expuestas.
        if not any(all(option[key] == card[key] for key in ("current_price", "reported_regular_price"))
                   for option in by_handle[card["handle"]]):
            raise ColonialError("commercial_sources_disagree")
        row["availability"] = card["availability"]
    return [{key: value for key, value in row.items() if key != "handle"} for row in rows]
