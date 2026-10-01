"""Extractor controlado del catálogo público de Supermercados La Colonia."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import parse_qs, urlsplit

from precios_supermercados.enums import AvailabilityStatus, LocationStatus
from precios_supermercados.identifiers import select_source_key
from precios_supermercados.models import RawProduct

from .base import (
    EmptyResponseError,
    ExtractionMetrics,
    ExtractionResult,
    SafeHttpClient,
    StructureChangedError,
)
from .la_colonia_graphql import GRAPHQL_ENDPOINT, build_product_search_url

SUPERMARKET_ID = "la_colonia"
LOCATION_ID = "la_colonia_online"
LOCATION_EVIDENCE = (
    "Catálogo público en línea sin selección obligatoria de ciudad o sucursal."
)
BASE_URL = "https://www.lacolonia.com"
CATALOG_URL = f"{BASE_URL}/supermercado"
GRAPHQL_URL = GRAPHQL_ENDPOINT
ROBOTS_URL = f"{BASE_URL}/robots.txt"
EXTRACTOR_VERSION = "0.3.1"
SCHEMA_VERSION = "1.0.0"
USER_AGENT = (
    "PreciosSupermercadosSPS-LaColonia/0.3 "
    "(+https://github.com/jchernandez-portfolio/precios-supermercados-sps)"
)
FORBIDDEN_PATH_PREFIXES = (
    "/img",
    "/account",
    "/login",
    "/checkout",
    "/busca",
    "/quick-view",
    "/espiar",
    "/buscapagina",
    "/site/track.aspx",
    "/api",
    "/register.js",
)

_PRESENTATION_PATTERN = re.compile(
    r"(?i)(?:\bx\s*)?(?:(\d+(?:[.,]\d+)?)\s*)?"
    r"(lb|lbs|libra|libras|kg|g|gr|gramos|ml|l|lt|litros|oz|"
    r"un|und|uds|unidad|unidades|ft)\s*$"
)


class LaColoniaExtractor:
    """Obtiene una página de productos y produce un ``RawProduct`` por SKU."""

    def __init__(
        self,
        client: SafeHttpClient | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.client = client or SafeHttpClient(
            allowed_hosts={"www.lacolonia.com"},
            forbidden_path_prefixes=FORBIDDEN_PATH_PREFIXES,
            user_agent=USER_AGENT,
        )
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def build_page_url(
        self,
        *,
        page: int = 1,
        page_size: int = 5,
        query: str = "supermercado",
        category_map: str = "category-1",
        full_text: str = "",
    ) -> str:
        return build_product_search_url(
            page=page,
            page_size=page_size,
            query=query,
            category_map=category_map,
            full_text=full_text,
        )

    def extract_page(
        self,
        *,
        scrape_run_id: str,
        page: int = 1,
        page_size: int = 5,
        query: str = "supermercado",
        category_map: str = "category-1",
        full_text: str = "",
    ) -> ExtractionResult:
        source_url = self.build_page_url(
            page=page,
            page_size=page_size,
            query=query,
            category_map=category_map,
            full_text=full_text,
        )
        payload = self.client.get_json(source_url)
        return self.parse_payload(
            payload,
            scrape_run_id=scrape_run_id,
            source_url=source_url,
            page_size=page_size,
        )

    def parse_payload(
        self,
        payload: Mapping[str, Any],
        *,
        scrape_run_id: str,
        source_url: str,
        page_size: int = 5,
    ) -> ExtractionResult:
        products_payload, total_products = _read_product_search(payload)
        if not products_payload:
            raise EmptyResponseError("La página controlada no devolvió productos")

        metrics = ExtractionMetrics(
            products_discovered=total_products,
            products_requested=page_size,
            products_returned=len(products_payload),
            pages_discovered=max(math.ceil(total_products / page_size), 1),
            pages_processed=1,
        )
        metrics.page_coverage = metrics.pages_processed / metrics.pages_discovered
        events: list[str] = []
        observed_at = self.clock()
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("clock debe devolver una fecha con zona horaria")
        observed_at = observed_at.astimezone(timezone.utc)

        raw_products: list[RawProduct] = []
        seen_keys: set[tuple[str, str]] = set()

        for product in products_payload:
            if not isinstance(product, Mapping):
                metrics.errors += 1
                events.append("quality:product_not_mapping")
                continue

            items = product.get("items")
            if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
                metrics.errors += 1
                metrics.structural_events += 1
                events.append("structure:missing_items")
                continue

            metrics.skus_returned += len(items)
            for item in items:
                if not isinstance(item, Mapping):
                    metrics.errors += 1
                    events.append("quality:item_not_mapping")
                    continue
                try:
                    raw = self._parse_item(
                        product,
                        item,
                        observed_at=observed_at,
                        scrape_run_id=scrape_run_id,
                        source_url=source_url,
                    )
                except (KeyError, ValueError) as exc:
                    metrics.errors += 1
                    events.append(f"quality:skipped_item:{type(exc).__name__}")
                    continue

                identity = (raw.source_key_type.value, raw.source_key)
                if identity in seen_keys:
                    metrics.duplicate_skus += 1
                    events.append("quality:duplicate_source_key")
                    continue
                seen_keys.add(identity)
                raw_products.append(raw)

                values = raw.raw_values
                if values.get("current_price") is not None:
                    metrics.skus_with_price += 1
                if values.get("availability_evidence") == "price_positive_quantity_zero":
                    events.append("quality:availability_conflict_price_with_zero_quantity")
                if any(
                    values.get(field_name) is None
                    for field_name in (
                        "current_price",
                        "brand",
                        "category",
                        "presentation",
                    )
                ) or values.get("availability") == AvailabilityStatus.UNKNOWN.value:
                    metrics.skus_pending_review += 1

        metrics.skus_extracted = len(raw_products)
        if not raw_products:
            raise StructureChangedError("No se pudo interpretar ningún SKU de la respuesta")

        if metrics.skus_with_price == 0:
            events.append("quality:missing_all_prices")

        expected_products = _expected_products_for_page(
            total_products=total_products,
            products_requested=page_size,
            source_url=source_url,
        )
        product_page_complete = metrics.products_returned == expected_products
        if metrics.products_returned < expected_products:
            events.append("quality:partial_product_page")
        elif metrics.products_returned > expected_products:
            metrics.structural_events += 1
            events.append("structure:unexpected_product_count")

        accepted = (
            metrics.skus_with_price > 0
            and metrics.skus_extracted > 0
            and metrics.structural_events == 0
            and metrics.errors == 0
            and product_page_complete
        )
        return ExtractionResult(
            products=tuple(raw_products),
            metrics=metrics,
            quality_events=tuple(dict.fromkeys(events)),
            accepted=accepted,
            source_url=source_url,
        )

    def _parse_item(
        self,
        product: Mapping[str, Any],
        item: Mapping[str, Any],
        *,
        observed_at: datetime,
        scrape_run_id: str,
        source_url: str,
    ) -> RawProduct:
        product_name = _text(product.get("productName"))
        source_name = _text(item.get("nameComplete")) or product_name
        if source_name is None:
            raise ValueError("SKU sin nombre")

        product_url = _product_url(_text(product.get("linkText")))
        product_id = _text(product.get("productId"))
        item_id = _text(item.get("itemId"))
        source_sku = _reference_value(item.get("referenceId")) or _text(
            product.get("productReference")
        )
        barcode = _text(item.get("ean"))
        source_key_type, source_key = select_source_key(
            internal_id=item_id,
            sku=source_sku,
            barcode=barcode,
            api_id=product_id,
            stable_url=product_url,
        )

        sellers = _mapping_sequence(item.get("sellers"))
        selected_seller = _select_seller(sellers)
        offer = _commercial_offer(selected_seller)
        current_price = _positive_decimal(offer.get("Price"))
        list_price = _positive_decimal(offer.get("ListPrice"))
        quantities = [
            _non_negative_decimal(_commercial_offer(seller).get("AvailableQuantity"))
            for seller in sellers
        ]
        quantities = [value for value in quantities if value is not None]
        availability, availability_evidence = _availability(
            current_price,
            sellers,
            quantities,
        )
        promotion_evidence = _promotion_evidence(offer)
        is_promotion = bool(
            (current_price is not None and list_price is not None and list_price > current_price)
            or promotion_evidence
        )
        reported_regular_price = (
            list_price
            if current_price is not None
            and list_price is not None
            and list_price > current_price
            else None
        )

        category_tree = _mapping_sequence(product.get("categoryTree"))
        category_names = tuple(
            name
            for entry in category_tree
            if (name := _text(entry.get("name"))) is not None
        )
        source_category = " > ".join(category_names) or _first_category(
            product.get("categories")
        )
        subcategory = category_names[-1] if len(category_names) > 1 else _last_category(
            source_category
        )
        source_brand = _text(product.get("brand"))
        presentation_source = (
            _text(item.get("nameComplete"))
            or _text(item.get("name"))
            or product_name
        )
        source_presentation = _presentation(presentation_source)
        images = _mapping_sequence(item.get("images"))
        image_url = next(
            (
                value
                for image in images
                if (value := _text(image.get("imageUrl")))
                and value.startswith("https://")
            ),
            None,
        )
        measurement_unit = _text(item.get("measurementUnit"))
        unit_multiplier = _positive_decimal(item.get("unitMultiplier"))

        raw_values = {
            "product_id": product_id,
            "product_name": product_name,
            "item_id": item_id,
            "item_name": _text(item.get("name")),
            "item_name_complete": _text(item.get("nameComplete")),
            "reference": source_sku,
            "ean": barcode,
            "brand": source_brand,
            "category": source_category,
            "subcategory": subcategory,
            "presentation": source_presentation,
            "categories": list(product.get("categories") or []),
            "category_tree": [dict(entry) for entry in category_tree],
            "current_price": _decimal_text(current_price),
            "reported_regular_price": _decimal_text(reported_regular_price),
            "source_list_price": _decimal_text(list_price),
            "is_promotion": is_promotion,
            "promotion_evidence": promotion_evidence,
            "availability": availability.value,
            "availability_evidence": availability_evidence,
            "available_quantity": _decimal_text(quantities[0]) if quantities else None,
            "seller_id": _text(selected_seller.get("sellerId")) if selected_seller else None,
            "measurement_unit": measurement_unit,
            "unit_multiplier": _decimal_text(unit_multiplier),
            "weighted_product": _is_weighted(source_name, measurement_unit),
        }

        return RawProduct(
            supermarket_id=SUPERMARKET_ID,
            location_id=LOCATION_ID,
            source_key_type=source_key_type,
            source_key=source_key,
            source_name=source_name,
            product_url=product_url,
            observed_at_utc=observed_at,
            scrape_run_id=scrape_run_id,
            extractor_version=EXTRACTOR_VERSION,
            schema_version=SCHEMA_VERSION,
            source_url=source_url,
            source_sku=source_sku,
            source_brand=source_brand,
            source_presentation=source_presentation,
            source_category=source_category,
            image_url=image_url,
            location_status=LocationStatus.UNKNOWN,
            location_evidence=LOCATION_EVIDENCE,
            location_confidence=None,
            raw_values=raw_values,
        )


def decode_search_variables(url: str) -> Mapping[str, Any]:
    """Decodifica las variables de una URL creada por el extractor."""

    parsed = urlsplit(url)
    variables_raw = parse_qs(parsed.query).get("variables")
    if not variables_raw:
        raise ValueError("La URL no contiene variables")
    value = json.loads(variables_raw[0])
    if not isinstance(value, Mapping):
        raise ValueError("Las variables no son un objeto")
    return value


def _read_product_search(payload: Mapping[str, Any]) -> tuple[Sequence[Any], int]:
    errors = payload.get("errors")
    data = payload.get("data")
    if not isinstance(data, Mapping):
        if errors:
            raise StructureChangedError(f"GraphQL devolvió errores: {errors}")
        raise StructureChangedError("Falta data en la respuesta GraphQL")
    product_search = data.get("productSearch")
    if not isinstance(product_search, Mapping):
        if errors:
            raise StructureChangedError(f"GraphQL devolvió errores: {errors}")
        raise StructureChangedError("Falta data.productSearch")
    products = product_search.get("products")
    if not isinstance(products, Sequence) or isinstance(products, (str, bytes)):
        raise StructureChangedError("Falta data.productSearch.products")
    total_value = product_search.get("recordsFiltered", len(products))
    try:
        total = max(int(total_value), len(products))
    except (TypeError, ValueError):
        total = len(products)
    return products, total


def _expected_products_for_page(
    *,
    total_products: int,
    products_requested: int,
    source_url: str,
) -> int:
    try:
        variables = decode_search_variables(source_url)
        from_index = max(int(variables.get("from", 0)), 0)
    except (TypeError, ValueError, json.JSONDecodeError):
        from_index = 0
    remaining = max(total_products - from_index, 0)
    return min(products_requested, remaining)


def _mapping_sequence(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [entry for entry in value if isinstance(entry, Mapping)]


def _text(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _positive_decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() and result > 0 else None


def _non_negative_decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() and result >= 0 else None


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    rendered = format(value.normalize(), "f")
    return "0" if rendered in {"", "-0"} else rendered


def _product_url(link_text: str | None) -> str:
    if link_text is None:
        raise ValueError("Producto sin linkText")
    cleaned = link_text.strip("/")
    if not cleaned:
        raise ValueError("linkText vacío")
    return f"{BASE_URL}/{cleaned}/p"


def _reference_value(value: Any) -> str | None:
    for entry in _mapping_sequence(value):
        candidate = _text(entry.get("Value") or entry.get("value"))
        if candidate:
            return candidate
    return None


def _select_seller(sellers: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    if not sellers:
        return {}
    return next((seller for seller in sellers if seller.get("sellerDefault") is True), sellers[0])


def _commercial_offer(seller: Mapping[str, Any]) -> Mapping[str, Any]:
    if not seller:
        return {}
    offer = seller.get("commercialOffer") or seller.get("commertialOffer")
    return offer if isinstance(offer, Mapping) else {}


def _availability(
    current_price: Decimal | None,
    sellers: Sequence[Mapping[str, Any]],
    quantities: Sequence[Decimal],
) -> tuple[AvailabilityStatus, str]:
    if current_price is not None and any(quantity > 0 for quantity in quantities):
        return AvailabilityStatus.IN_STOCK, "price_positive_quantity_positive"
    if current_price is not None and quantities and all(quantity == 0 for quantity in quantities):
        return AvailabilityStatus.UNKNOWN, "price_positive_quantity_zero"
    if current_price is None and sellers and quantities and all(
        quantity == 0 for quantity in quantities
    ):
        return AvailabilityStatus.OUT_OF_STOCK, "price_absent_quantity_zero"
    return AvailabilityStatus.UNKNOWN, "insufficient_evidence"


def _promotion_evidence(offer: Mapping[str, Any]) -> list[str]:
    evidence: list[str] = []
    for field_name in ("discountHighlights", "teasers"):
        for entry in _mapping_sequence(offer.get(field_name)):
            text = _text(entry.get("name") or entry.get("Name"))
            evidence.append(text or field_name)
    return list(dict.fromkeys(evidence))


def _first_category(value: Any) -> str | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return None
    for item in value:
        text = _text(item)
        if text:
            parts = [part.strip() for part in text.split("/") if part.strip()]
            return " > ".join(parts) if parts else text
    return None


def _last_category(value: str | None) -> str | None:
    if value is None:
        return None
    parts = [part.strip() for part in value.split(">") if part.strip()]
    return parts[-1] if len(parts) > 1 else None


def _presentation(value: str | None) -> str | None:
    if value is None:
        return None
    match = _PRESENTATION_PATTERN.search(value)
    if not match:
        return None
    number, _unit = match.groups()
    matched = match.group(0).strip()
    if number is None and not matched.casefold().startswith("x"):
        return None
    return matched


def _is_weighted(name: str, measurement_unit: str | None) -> bool:
    normalized_unit = (measurement_unit or "").casefold()
    if normalized_unit in {"kg", "g", "lb", "lbs", "libra", "libras"}:
        return True
    return bool(re.search(r"(?i)\bx\s*(lb|kg|g)\b", name))
