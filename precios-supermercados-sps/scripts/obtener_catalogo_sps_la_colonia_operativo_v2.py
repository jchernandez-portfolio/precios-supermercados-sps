#!/usr/bin/env python3
"""Ejecuta el catálogo operativo permitiendo recovery ante huecos de VTEX.

El extractor base conserva su semántica fail-closed. Este entrypoint adapta sólo el
recorrido full-catalog ya autorizado: una página rechazada exclusivamente por
cardinalidad corta puede aportar sus SKU al bucket y una página vacía válida puede
continuar sin aportar datos. En ambos casos la cobertura exacta del bucket sigue
siendo obligatoria contra recordsFiltered y, si hace falta, se recupera en orden
inverso por el runner operativo.

Para el catálogo operativo, ``AvailableQuantity`` es además la evidencia primaria
de disponibilidad: cualquier cantidad positiva implica disponible y una cantidad
explícita igual a cero implica agotado, aunque VTEX conserve un precio publicado.

Un fallo transitorio de transporte/GraphQL puede invalidar un recorrido completo sin
que exista evidencia de cambio estructural. En esos casos este wrapper repite una sola
vez el recorrido desde cero. El primer intento nunca se persiste y el segundo sigue
pasando por los mismos gates fail-closed del extractor base.
"""

from __future__ import annotations

import json
import math
import sys
import time
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

import obtener_catalogo_sps_la_colonia_operativo as operational
from precios_supermercados.enums import AvailabilityStatus
from precios_supermercados.scrapers import la_colonia
from precios_supermercados.scrapers.base import (
    EmptyResponseError,
    ExtractionMetrics,
    ExtractionResult,
)


BaseExtractor = operational.core.LaColoniaExtractor
BASE_AVAILABILITY = la_colonia._availability
RUN_RETRY_DELAY_SECONDS = 15.0
RETRYABLE_RUN_FAILURE_REASONS = frozenset(
    {
        "product_search_graphql_errors",
        "root_product_search_graphql_errors",
        "root_product_search_recheck_graphql_errors",
        "brand_facets_graphql_errors",
        "product_search_http_500",
        "product_search_http_502",
        "product_search_http_503",
        "product_search_http_504",
        "root_product_search_http_500",
        "root_product_search_http_502",
        "root_product_search_http_503",
        "root_product_search_http_504",
        "root_product_search_recheck_http_500",
        "root_product_search_recheck_http_502",
        "root_product_search_recheck_http_503",
        "root_product_search_recheck_http_504",
        "brand_facets_http_500",
        "brand_facets_http_502",
        "brand_facets_http_503",
        "brand_facets_http_504",
    }
)


def _operational_availability(
    current_price: Decimal | None,
    sellers: Sequence[Mapping[str, Any]],
    quantities: Sequence[Decimal],
) -> tuple[AvailabilityStatus, str]:
    """Resuelve cantidad explícita cero como agotado para el catálogo operativo."""

    if sellers and quantities and not any(quantity > 0 for quantity in quantities):
        return AvailabilityStatus.OUT_OF_STOCK, "available_quantity_zero"
    return BASE_AVAILABILITY(current_price, sellers, quantities)


def _partial_page_is_safe_for_bucket_recovery(result: Any) -> bool:
    """Aísla exactamente el rechazo causado por una página corta parseable."""

    metrics = result.metrics
    return (
        not result.accepted
        and "quality:partial_product_page" in result.quality_events
        and metrics.structural_events == 0
        and metrics.errors == 0
        and metrics.skus_extracted > 0
        and metrics.skus_with_price > 0
    )


def _unpriced_unavailable_page_is_safe(result: Any) -> bool:
    """Página completa cuyos SKU están todos sin precio y agotados ("NO DISPONIBLE").

    El extractor base rechaza una página sin ningún precio. En el catálogo
    operativo esa página es válida si cada SKU está explícitamente agotado: la
    regla 2026-10-02 los separa del snapshot con precio y la validación global
    limita su volumen (≤2 %). Sin precio ``in_stock``/``unknown`` sigue fallando.
    """

    metrics = result.metrics
    products = tuple(result.products)
    return (
        not result.accepted
        and "quality:partial_product_page" not in result.quality_events
        and metrics.structural_events == 0
        and metrics.errors == 0
        and metrics.skus_with_price == 0
        and metrics.skus_extracted > 0
        and len(products) == metrics.skus_extracted
        and all(
            product.raw_values.get("current_price") is None
            and product.raw_values.get("availability") == AvailabilityStatus.OUT_OF_STOCK.value
            for product in products
        )
    )


def _empty_page_for_bucket_recovery(
    payload: Any,
    *,
    source_url: str,
    page_size: int,
) -> ExtractionResult:
    """Representa un hueco vacío sin aceptar cobertura incompleta del bucket."""

    total_products, products_returned = operational.core._read_shape(payload)
    if total_products <= 0 or products_returned != 0:
        raise EmptyResponseError("La página controlada no devolvió productos")
    metrics = ExtractionMetrics(
        products_discovered=total_products,
        products_requested=page_size,
        products_returned=0,
        pages_discovered=max(math.ceil(total_products / page_size), 1),
        pages_processed=1,
    )
    metrics.page_coverage = metrics.pages_processed / metrics.pages_discovered
    return ExtractionResult(
        products=(),
        metrics=metrics,
        quality_events=("quality:empty_product_page_recovery",),
        accepted=True,
        source_url=source_url,
    )


class RecoveryAwareLaColoniaExtractor(BaseExtractor):
    """Relaja sólo gates de página; la cobertura del bucket sigue fail-closed."""

    def parse_payload(self, *args: Any, **kwargs: Any) -> Any:
        try:
            result = super().parse_payload(*args, **kwargs)
        except EmptyResponseError:
            payload = args[0] if args else kwargs.get("payload")
            return _empty_page_for_bucket_recovery(
                payload,
                source_url=str(kwargs.get("source_url") or ""),
                page_size=int(kwargs.get("page_size") or 5),
            )
        if _partial_page_is_safe_for_bucket_recovery(result):
            return replace(result, accepted=True)
        if _unpriced_unavailable_page_is_safe(result):
            return replace(result, accepted=True)
        return result


def _output_path(argv: list[str] | None) -> Path:
    values = list(sys.argv[1:] if argv is None else argv)
    for index, value in enumerate(values):
        if value == "--output" and index + 1 < len(values):
            return Path(values[index + 1])
        if value.startswith("--output="):
            return Path(value.split("=", 1)[1])
    return operational.ROOT / "run-artifacts" / "full-catalog.json"


def _failure_reason(path: Path) -> str | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    reason = payload.get("reason") if isinstance(payload, Mapping) else None
    return reason if isinstance(reason, str) and reason else None


def main(argv: list[str] | None = None) -> int:
    # El runner operativo resuelve la clase desde ``core`` al iniciar el catálogo.
    # El ajuste de disponibilidad se limita al mismo recorrido operativo.
    original_extractor = operational.core.LaColoniaExtractor
    original_availability = la_colonia._availability
    operational.core.LaColoniaExtractor = RecoveryAwareLaColoniaExtractor
    la_colonia._availability = _operational_availability
    try:
        result = operational.main(argv)
        if result != 3:
            return result

        reason = _failure_reason(_output_path(argv))
        if reason not in RETRYABLE_RUN_FAILURE_REASONS:
            return result

        print(f"sps_operational_catalog_retrying:{reason}", file=sys.stderr)
        time.sleep(RUN_RETRY_DELAY_SECONDS)
        return operational.main(argv)
    finally:
        la_colonia._availability = original_availability
        operational.core.LaColoniaExtractor = original_extractor


if __name__ == "__main__":
    raise SystemExit(main())
