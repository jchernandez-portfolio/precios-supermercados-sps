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
    canonical_brand,
    canonical_egg_size,
    canonical_presentation,
)
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


def build_rows(
    offers: Iterable[VisibleOffer],
    freshness_by_scope: dict[tuple[str, str], str],
    history_by_offer: dict[tuple[str, str], tuple[HistoricalPoint, ...]] | None = None,
    *,
    as_of_utc=None,
) -> list[dict[str, object]]:
    """Construye filas v3, limpia atributos públicos y añade otras presentaciones."""
    _core._derived_presentation = _derived_presentation
    offers = tuple(offers)
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
        _normalize_public_taxonomy(row)
        size = canonical_egg_size(row.get("product_name"), row.get("product_type"))
        if size is not None:
            # Campo aditivo: la presentación sigue siendo el conteo; el tamaño de
            # huevo no vuelve a fragmentar el selector de presentación.
            row["variant"] = size
        if _row_is_shoppable(row):
            visible.append(row)
    attach_other_presentations(visible, offers_by_key)
    return visible


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
                    brand=representative.brand,
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
    return _ORIGINAL_EXPORT(
        backend,
        scope,
        output_directory,
        as_of_utc=as_of_utc,
        freshness_window=freshness_window,
        require_products=require_products,
    )


def main(argv: Sequence[str] | None = None) -> int:
    _sync_core()
    previous_export = _core.export_consumer_catalog
    _core.export_consumer_catalog = export_consumer_catalog
    try:
        return _ORIGINAL_MAIN(argv)
    finally:
        _core.export_consumer_catalog = previous_export


if __name__ == "__main__":
    raise SystemExit(main())
