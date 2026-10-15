#!/usr/bin/env python3
"""Exporta catálogo B2C con normalización pública de marca y presentación.

El núcleo v3 se conserva en ``exportar_consumer_catalog_core.py``. Esta fachada
mantiene su API (incluidos los hooks usados por TGU) y aplica únicamente una
normalización de lectura antes de escribir archivos públicos.
"""
from __future__ import annotations

import json
import math
import re
import sys
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import exportar_consumer_catalog_core as _core  # noqa: E402
from precios_supermercados.consumer_catalog_display import (  # noqa: E402
    brand_from_prefix,
    canonical_brand,
    canonical_egg_size,
    canonical_presentation,
)
from precios_supermercados import master_taxonomy  # noqa: E402
from precios_supermercados.matching import size_variants  # noqa: E402

# Conserva la API histórica del script, incluidos helpers privados consumidos por
# el exportador TGU y por las pruebas del contrato v3.
for _name in dir(_core):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_core, _name)

_ORIGINAL_BUILD_ROWS = _core.build_rows
_ORIGINAL_EXPORT = _core.export_consumer_catalog
_ORIGINAL_MAIN = _core.main
# Tope de "otras presentaciones" por fila (las de tamaño más cercano).
MAX_OTHER_PRESENTATIONS = 12
# Onza de peso (avoirdupois) en gramos.
GRAMS_PER_OUNCE = Decimal("28.349523125")
# Subcategorías sin atributo ``ounce`` en el árbol cuyos productos se venden por
# peso: el huevo líquido (Egg Beaters) declara "NET WT 16 OZ (1 LB) 454 g".
EXPORT_OUNCE_MASS_SUBCATEGORIES = frozenset({"Huevos"})
# Productos pesados en tienda (código de barras de medida variable "2…"): el súper
# publica el precio por libra aunque el nombre diga el peso del paquete
# ("Pollo Entero Norteño ... - 5 lb" a L 42.00). Se llena en export_consumer_catalog.
WEIGHED_BY_POUND: set[str] = set()
WEIGHED_TYPE_WORDS = frozenset({
    "res", "cerdo", "pollo", "pavo", "carne", "carnes", "pescado", "pescados", "mariscos", "embutidos",
    "queso", "quesos", "jamon", "frutas", "verduras", "hierbas",
})
# Walmart lo trae como ean; Colonial (Shopify) como sku = reference ("2063710000000"
# "NORTEÑO Pollo en Piezas 1/12" a L 46.99 la libra).
_VARIABLE_CODE = "((length({c})=13 AND substr({c},1,1)='2') OR (length({c})=14 AND substr({c},1,2)='02'))"
WEIGHED_SQL = (
    "SELECT supermarket_id, product_id FROM products WHERE supermarket_id IN ('walmart','colonial') AND ("
    + _VARIABLE_CODE.format(c="ean") + " OR " + _VARIABLE_CODE.format(c="reference") + ")"
)
# El súper a veces aclara lo contrario en el nombre ("Ayote Sazon ... - Precio Por Unidad").
# (o "COLONIAL Encurtido Rojo und": termina en unidad).
_PER_UNIT_NAME = re.compile(r"precio\s+por\s+unidad|\s(?:und|unds|unid|unidad|un|c/u)\.?\s*$", re.IGNORECASE)
_AEROSOL_RE = re.compile(r"(?<![a-z])(?:spray|aerosol|atomizador)(?![a-z])", re.IGNORECASE)


def _fold_public(value: object) -> str:
    """Normaliza texto sólo para reglas públicas explícitas y auditables."""
    if not isinstance(value, str):
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_text = "".join(char for char in decomposed if not unicodedata.combining(char))
    normalized = re.sub(r"[^0-9a-zA-Z]+", " ", ascii_text).casefold()
    return " ".join(normalized.split())


def _normalize_public_taxonomy(row: dict[str, object]) -> None:
    """Completa taxonomía pública cuando el nombre aporta evidencia inequívoca.

    No toca identidad canónica ni comparabilidad. ``Egg Beaters`` es una marca
    explícita y suficientemente distintiva para evitar que sus productos terminen
    en ``Sin categoría`` cuando la fuente trae ``RMS`` y un nombre en inglés.
    """
    name = _fold_public(row.get("product_name"))
    if re.search(r"(?<!\w)egg\s+beaters(?!\w)", name):
        row["brand"] = "Egg Beaters"
        if not row.get("category"):
            row["category"] = "Alimentos"
        if not row.get("product_type"):
            row["product_type"] = "Huevo"


def _row_is_shoppable(row: dict[str, object]) -> bool:
    """Compra Inteligente sólo indexa filas con al menos una oferta comprable."""
    offers = row.get("offers")
    if not isinstance(offers, list):
        return False
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        if offer.get("availability") == "out_of_stock" or offer.get("freshness_status") == "UNAVAILABLE":
            continue
        try:
            price = Decimal(str(offer.get("current_price")))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if price.is_finite() and price > 0:
            return True
    return False


def _derived_presentation(offer: VisibleOffer) -> str | None:
    return canonical_presentation(
        source_presentation=offer.presentation,
        product_name=offer.product_name,
        product_type=offer.product_type,
        presentation_dimension=offer.presentation_dimension,
        presentation_total_base=offer.presentation_total_base,
        presentation_status=offer.presentation_status,
    )


def ounce_as_mass(offer: VisibleOffer) -> VisibleOffer:
    """Onza sin convertir → gramos cuando la subcategoría pública es de peso.

    El motor sólo convierte la onza cuando la categoría del súper lleva a una
    subcategoría ``ounce: mass``; la asignación pública usa además el nombre y
    la categoría, y suele llegar a ella (Colonial "EGG Beaters 16 oz" → Huevos).
    Sólo cambia la lectura pública: presentación en g y precio por kg/100 g. No
    toca identidad, perfiles ni comparabilidad. Aerosoles quedan sin convertir.
    """
    if offer.presentation_dimension != "ounce" or _AEROSOL_RE.search(offer.product_name or ""):
        return offer
    try:
        ounces = Decimal(str(offer.presentation_total_base))
    except (InvalidOperation, TypeError, ValueError):
        return offer
    if not ounces.is_finite() or ounces <= 0:
        return offer
    node = master_taxonomy.assign_offer(
        offer.supermarket_id, offer.source_category, offer.product_type, offer.category,
        product_name=offer.product_name,
    ).node
    if node is not None and node.subcategory is not None:
        mass = master_taxonomy.node_attribute(node, "ounce") == "mass" or node.subcategory in EXPORT_OUNCE_MASS_SUBCATEGORIES
    else:
        # Misma evidencia explícita que _normalize_public_taxonomy: Egg Beaters es huevo.
        mass = re.search(r"(?<!\w)egg\s+beaters(?!\w)", _fold_public(offer.product_name)) is not None
    if not mass:
        return offer
    grams = (ounces * GRAMS_PER_OUNCE).quantize(Decimal("0.00001"))
    return _core.replace(offer, presentation_dimension="mass_g", presentation_total_base=format(grams.normalize(), "f"))


def build_rows(
    offers: Iterable[VisibleOffer],
    freshness_by_scope: dict[tuple[str, str], str],
    history_by_offer: dict[tuple[str, str], tuple[HistoricalPoint, ...]] | None = None,
    *,
    as_of_utc=None,
) -> list[dict[str, object]]:
    """Construye filas v3, limpia atributos públicos y añade otras presentaciones."""
    _core._derived_presentation = _derived_presentation
    offers = tuple(ounce_as_mass(offer) for offer in offers)
    offers_by_key = {(offer.source_product_id, offer.location_id): offer for offer in offers}
    rows = _ORIGINAL_BUILD_ROWS(
        offers,
        freshness_by_scope,
        history_by_offer,
        as_of_utc=as_of_utc,
    )
    visible: list[dict[str, object]] = []
    for row in rows:
        row["brand"] = canonical_brand(row.get("brand"), row.get("product_name"))
        if not row["brand"]:
            row["brand"] = _prefix_brand(row, offers_by_key)
        _normalize_public_taxonomy(row)
        size = canonical_egg_size(row.get("product_name"), row.get("product_type"))
        if size is not None:
            # Campo aditivo: la presentación sigue siendo el conteo; el tamaño de
            # huevo no vuelve a fragmentar el selector de presentación.
            row["variant"] = size
        if _row_is_shoppable(row):
            visible.append(row)
    for row in visible:
        _price_per_pound(row)
    attach_other_presentations(visible, offers_by_key)
    return visible


def _weighed_type(row: dict[str, object]) -> bool:
    words = set(_fold_public(" ".join(str(row.get(key) or "") for key in ("category", "product_type"))).split())
    return bool(words & WEIGHED_TYPE_WORDS)


def _price_per_pound(row: dict[str, object]) -> None:
    """Ofertas de producto pesado en tienda: precio por libra, no por paquete."""
    offers = [offer for offer in row.get("offers") or () if isinstance(offer, dict)]
    weighed = [offer for offer in offers if str(offer.get("source_product_id")) in WEIGHED_BY_POUND]
    if not weighed or not _weighed_type(row) or _PER_UNIT_NAME.search(str(row.get("product_name") or "")):
        return
    for offer in weighed:
        price = offer.get("current_price")
        offer["unit_price"] = {"amount": price, "per": "libra"} if price else None
    if len(weighed) == len(offers):
        row["presentation"] = "por libra"


def _prefix_brand(row: dict[str, object], offers_by_key: dict[tuple[str, str], VisibleOffer]) -> str | None:
    """Marca curada por prefijo del nombre (p. ej. Colonial) para filas sin marca."""
    for offer in row.get("offers") or ():
        source = offers_by_key.get((str(offer.get("source_product_id")), str(offer.get("location_id"))))
        if source is not None:
            brand = brand_from_prefix(source.supermarket_id, source.product_name)
            if brand:
                return brand
    return None


def _eligible_prices(row: dict[str, object]) -> list[tuple[Decimal, dict[str, object]]]:
    prices: list[tuple[Decimal, dict[str, object]]] = []
    for offer in row.get("offers") or ():
        if not isinstance(offer, dict) or offer.get("availability") == "out_of_stock":
            continue
        price = size_variants.parse_price(offer.get("current_price"))
        if price is not None:
            prices.append((price, offer))
    return prices


def _presentation_entry(row: dict[str, object], *, large_size: bool) -> dict[str, object]:
    prices = _eligible_prices(row)
    best_price, best_offer = min(prices, key=lambda item: item[0]) if prices else (None, {})
    return {
        "row_id": row["row_id"],
        "product_name": row["product_name"],
        "presentation": row["presentation"],
        "best_price": None if best_price is None else format(best_price, "f"),
        "best_unit_price": best_offer.get("unit_price"),
        "large_size": large_size,
    }


def attach_other_presentations(
    rows: list[dict[str, object]],
    offers_by_key: dict[tuple[str, str], VisibleOffer],
    *,
    links_function=None,
) -> dict[str, object]:
    """Añade ``other_presentations`` (regla V) a las filas con otro tamaño publicado.

    Campo aditivo y opcional: sólo aparece en filas con al menos un vínculo. No
    toca identidad, ofertas ni comparabilidad. Un fallo de la regla nunca bloquea
    la publicación: se informa por stderr y el catálogo sale sin el campo.
    """

    links_function = links_function or size_variants.size_variant_links
    try:
        items = []
        totals: dict[str, float] = {}
        for row in rows:
            group = [
                offers_by_key[(str(offer["source_product_id"]), str(offer["location_id"]))]
                for offer in row["offers"]  # type: ignore[union-attr]
            ]
            representative = _core._representative(group)
            prices = [price for price, _ in _eligible_prices(row)]
            items.append(
                size_variants.SizeVariantItem(
                    key=str(row["row_id"]),
                    supermarket_id=representative.supermarket_id,
                    name=str(row["product_name"]),
                    brand=_text(row.get("brand")) or representative.brand,
                    presentation=representative.presentation or _text(row.get("presentation")),
                    category=representative.source_category,
                    price=min(prices) if prices else None,
                )
            )
        links, diagnostics = links_function(items)
        by_id = {str(row["row_id"]): row for row in rows}
        related: dict[str, list[tuple[float, str, bool]]] = {}
        for link in links:
            for this, other, this_total, other_total in (
                (link.key_a, link.key_b, link.total_a, link.total_b),
                (link.key_b, link.key_a, link.total_b, link.total_a),
            ):
                totals[other] = other_total
                closeness = abs(math.log(other_total / this_total)) if this_total > 0 and other_total > 0 else math.inf
                related.setdefault(this, []).append(
                    (closeness, other, other_total >= size_variants.LARGE_SIZE_RATIO * this_total)
                )
        for row_id, entries in related.items():
            chosen = sorted(entries, key=lambda entry: (entry[0], entry[1]))[:MAX_OTHER_PRESENTATIONS]
            chosen.sort(key=lambda entry: (totals[entry[1]], entry[1]))
            by_id[row_id]["other_presentations"] = [
                _presentation_entry(by_id[other], large_size=large) for _, other, large in chosen
            ]
        summary = {
            "rule": f"{size_variants.RULE_ID}@{size_variants.RULE_VERSION}",
            "links": len(links),
            "rows_with_other_presentations": len(related),
            "diagnostics": diagnostics,
        }
    except Exception as exc:  # noqa: BLE001 - la relación nunca bloquea el catálogo
        for row in rows:
            row.pop("other_presentations", None)
        summary = {"status": "error", "error_type": type(exc).__name__, "error": str(exc)[:300]}
    print(json.dumps({"other_presentations": summary}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
    return summary


def _sync_core() -> None:
    """Sincroniza hooks mutables para preservar el contrato del exportador TGU."""
    for name in (
        "EXPECTED_SCOPE",
        "RETAILER_NAMES",
        "MAX_PARTITION_ROWS",
        "PUBLIC_COMPARABILITY",
        "fetch_visible_offers",
        "fetch_historical_points",
        "_identity_groups",
    ):
        setattr(_core, name, globals()[name])
    _core._derived_presentation = _derived_presentation
    _core.build_rows = build_rows


def export_consumer_catalog(
    backend: QueryBackend,
    scope: ComparisonScope,
    output_directory: Path,
    *,
    as_of_utc,
    freshness_window,
    require_products: bool = False,
) -> dict[str, object]:
    _sync_core()
    WEIGHED_BY_POUND.clear()
    columns = {str(row[0]) for row in backend.query("SELECT name FROM pragma_table_info('products')")}
    if {"ean", "reference"} <= columns:  # bases mínimas de prueba no traen ean/reference
        WEIGHED_BY_POUND.update(f"{row[0]}:{row[1]}" for row in backend.query(WEIGHED_SQL))
    manifest = _ORIGINAL_EXPORT(
        backend,
        scope,
        output_directory,
        as_of_utc=as_of_utc,
        freshness_window=freshness_window,
        require_products=require_products,
    )
    return attach_search_index(output_directory, manifest)


SEARCH_SCHEMA = "rpi-consumer-search/v1"
SEARCH_FILE = "search.json"
SEARCH_COLUMNS = (
    "id", "product_name", "brand", "presentation", "category", "product_type",
    "partition", "best_price", "unit_amount", "unit_per", "retailers", "promo",
    "other_presentations", "comparability", "photo",
)
# id = hash del row_id sin prefijo; comparabilidad en una letra; photo = versión
# de la foto en la rama ``product-images`` (0 = sin foto, la app dibuja la ilustración).
PHOTO_INDEX_SCHEMA = "super-compras-product-images/v1"
_PHOTO_VERSIONS: dict[str, int] = {}
_COMPARABILITY_CODES = {"comparable": "c", "single_source": "s", "individual": "i"}


def load_photo_versions(path: Path | None) -> dict[str, int]:
    """Lee ``index.json`` de fotos; si falta o no cuadra, el catálogo sale sin fotos."""
    if path is None or not path.is_file():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    items = document.get("items") if isinstance(document, dict) and document.get("schema") == PHOTO_INDEX_SCHEMA else None
    if not isinstance(items, dict):
        return {}
    return {
        str(key): value["v"]
        for key, value in items.items()
        if isinstance(value, dict) and not value.get("none") and type(value.get("v")) is int and value["v"] > 0
    }


def _search_entry(row: dict[str, object], partition: int, retailer_index: dict[str, int]) -> list[object]:
    prices = _eligible_prices(row)
    best_price, best_offer = min(prices, key=lambda item: item[0]) if prices else (None, {})
    unit = best_offer.get("unit_price") if isinstance(best_offer, dict) else None
    retailers = sorted({
        retailer_index[str(offer["supermarket_id"])]
        for _, offer in prices
        if str(offer.get("supermarket_id")) in retailer_index
    })
    promo = any(
        offer.get("is_promotion") is True
        or (
            size_variants.parse_price(offer.get("reported_regular_price")) is not None
            and size_variants.parse_price(offer.get("reported_regular_price")) > price
        )
        for price, offer in prices
    )
    key = str(row["row_id"]).split("-", 1)[-1]
    return [
        key, row["product_name"], row.get("brand"), row.get("presentation"),
        row.get("category"), row.get("product_type"), partition,
        None if best_price is None else format(best_price, "f"),
        unit.get("amount") if isinstance(unit, dict) else None,
        unit.get("per") if isinstance(unit, dict) else None,
        retailers, 1 if promo else 0, len(row.get("other_presentations") or ()),
        _COMPARABILITY_CODES.get(str(row.get("comparability")), "i"),
        _PHOTO_VERSIONS.get(key, 0),
    ]


def attach_search_index(output_directory: Path, manifest: dict[str, object]) -> dict[str, object]:
    """Índice compacto de búsqueda para Súper Compras (``search.json``).

    Una fila por producto público con lo que necesita una tarjeta de resultados
    (nombre, marca, presentación, categoría, mejor precio, precio por unidad,
    súper con oferta, oferta y otras presentaciones) y la partición donde está
    la ficha completa. Se deriva de las particiones ya escritas: no cambia el
    contrato v3 ni la identidad. Queda listado con SHA-256 en el manifest.
    """
    partitions = [
        str(item["path"])
        for item in manifest.get("files", [])  # type: ignore[union-attr]
        if isinstance(item, dict) and str(item.get("path", "")).startswith("catalog/")
    ]
    retailers: list[str] = []
    for item in manifest.get("scope", []):  # type: ignore[union-attr]
        supermarket = str(item["supermarket_id"])
        if supermarket not in retailers:
            retailers.append(supermarket)
    retailer_index = {supermarket: index for index, supermarket in enumerate(retailers)}
    entries: list[list[object]] = []
    for number, relative in enumerate(partitions):
        document = json.loads((output_directory / relative).read_text(encoding="utf-8"))
        for row in document.get("rows", []):
            entries.append(_search_entry(row, number, retailer_index))
    if len(entries) != manifest.get("visible_rows"):
        raise ExportError("consumer_search_row_count_mismatch")
    document = {
        "schema": SEARCH_SCHEMA,
        "as_of": manifest.get("as_of"),
        "columns": list(SEARCH_COLUMNS),
        "retailers": retailers,
        "partitions": partitions,
        "row_count": len(entries),
        "rows": entries,
    }
    content = json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=False).encode("utf-8")
    _core._atomic_bytes(output_directory / SEARCH_FILE, content)
    files = [item for item in manifest.get("files", []) if not (isinstance(item, dict) and item.get("path") == SEARCH_FILE)]  # type: ignore[union-attr]
    files.append(_core._file_metadata(output_directory, SEARCH_FILE))
    manifest["files"] = files
    manifest["search_file"] = SEARCH_FILE
    _core._atomic_bytes(output_directory / "manifest.json", _core._json_bytes(manifest))
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    # ``--images-index`` es opcional y propio de este envoltorio: el núcleo no lo ve.
    arguments = list(sys.argv[1:] if argv is None else argv)
    images_index = None
    if "--images-index" in arguments:
        position = arguments.index("--images-index")
        if position + 1 >= len(arguments):
            raise SystemExit("--images-index requiere una ruta")
        images_index = Path(arguments[position + 1])
        del arguments[position:position + 2]
    _PHOTO_VERSIONS.clear()
    _PHOTO_VERSIONS.update(load_photo_versions(images_index))
    argv = arguments
    _sync_core()
    previous_export = _core.export_consumer_catalog
    _core.export_consumer_catalog = export_consumer_catalog
    try:
        return _ORIGINAL_MAIN(argv)
    finally:
        _core.export_consumer_catalog = previous_export


if __name__ == "__main__":
    raise SystemExit(main())
