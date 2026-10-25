"""Diagnóstico sanitizado de ventanas solapadas para La Colonia.

El módulo no cambia el recorrido normal, no acepta páginas parciales y no
persiste datos comerciales. Las identidades de producto se mantienen solo en
memoria para calcular firmas, uniones y solapamientos agregados.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from .la_colonia_graphql import (
    ALLOWED_ORDER_BY,
    GRAPHQL_ENDPOINT,
    MAX_CATALOG_PAGE_SIZE,
    PRODUCT_SEARCH_QUERY,
)

DEFAULT_MAX_ARTIFACT_BYTES = 64 * 1024
_FORBIDDEN_PUBLIC_KEYS = frozenset(
    {
        "productId",
        "product_id",
        "itemId",
        "item_id",
        "sku",
        "ean",
        "productName",
        "product_name",
        "brand",
        "price",
        "url",
        "source_key",
        "source_key_hash",
        "product_key",
        "product_keys",
    }
)


@dataclass(frozen=True, slots=True)
class WindowSpec:
    """Ventana inclusiva y limitada a cincuenta posiciones."""

    name: str
    from_index: int
    to_index: int
    order_by: str = "OrderByNameASC"

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("name no puede estar vacío")
        if self.from_index < 0:
            raise ValueError("from_index no puede ser negativo")
        if self.to_index < self.from_index:
            raise ValueError("to_index debe ser mayor o igual que from_index")
        if self.width > MAX_CATALOG_PAGE_SIZE:
            raise ValueError(
                f"La ventana no puede superar {MAX_CATALOG_PAGE_SIZE} posiciones"
            )
        if self.order_by not in ALLOWED_ORDER_BY:
            raise ValueError(f"order_by no permitido: {self.order_by}")

    @property
    def width(self) -> int:
        return self.to_index - self.from_index + 1


@dataclass(frozen=True, slots=True)
class WindowObservation:
    """Observación de una respuesta; las claves privadas nunca se serializan."""

    window: WindowSpec
    records_filtered: int
    products_returned: int
    skus_returned: int
    response_bytes: int
    signature: str
    quality_events: tuple[str, ...] = ()
    _product_keys: tuple[str, ...] = field(default=(), repr=False, compare=False)

    def sanitized_dict(self) -> dict[str, Any]:
        return {
            "window": self.window.name,
            "from": self.window.from_index,
            "to": self.window.to_index,
            "order_by": self.window.order_by,
            "products_expected": self.window.width,
            "products_returned": self.products_returned,
            "skus_returned": self.skus_returned,
            "records_filtered": self.records_filtered,
            "response_bytes": self.response_bytes,
            "signature": self.signature,
            "quality_events": list(self.quality_events),
        }


@dataclass(frozen=True, slots=True)
class OverlapObservation:
    left: str
    right: str
    expected: int
    observed: int

    @property
    def delta(self) -> int:
        return self.observed - self.expected

    def as_dict(self) -> dict[str, Any]:
        return {
            "left": self.left,
            "right": self.right,
            "expected": self.expected,
            "observed": self.observed,
            "delta": self.delta,
        }


@dataclass(frozen=True, slots=True)
class WindowDiagnosticReport:
    observations: tuple[WindowObservation, ...]
    overlaps: tuple[OverlapObservation, ...]
    expected_unique_positions: int
    products_unique_in_union: int
    repeated_occurrences: int
    duplicates_within_windows: int
    total_initial: int
    total_final: int
    quality_events: tuple[str, ...]

    @property
    def union_delta(self) -> int:
        return self.products_unique_in_union - self.expected_unique_positions

    def sanitized_summary(self) -> dict[str, Any]:
        summary = {
            "schema_version": "1.0.0",
            "windows": [item.sanitized_dict() for item in self.observations],
            "overlaps": [item.as_dict() for item in self.overlaps],
            "expected_unique_positions": self.expected_unique_positions,
            "products_unique_in_union": self.products_unique_in_union,
            "union_delta": self.union_delta,
            "repeated_occurrences": self.repeated_occurrences,
            "duplicates_within_windows": self.duplicates_within_windows,
            "total_initial": self.total_initial,
            "total_final": self.total_final,
            "quality_events": list(self.quality_events),
        }
        validate_sanitized_summary(summary)
        return summary


def build_window_url(
    window: WindowSpec,
    *,
    query: str = "supermercado",
    category_map: str = "category-1",
    full_text: str = "",
) -> str:
    """Construye una consulta GET para una ventana inclusiva arbitraria."""

    if full_text.strip():
        query_value = ""
        selected_facets: list[dict[str, str]] = []
    else:
        if not query.strip() or not category_map.strip():
            raise ValueError("query y category_map no pueden estar vacíos")
        query_value = query
        selected_facets = [{"key": category_map, "value": query}]

    variables: Mapping[str, Any] = {
        "query": query_value,
        "fullText": full_text.strip(),
        "selectedFacets": selected_facets,
        "orderBy": window.order_by,
        "from": window.from_index,
        "to": window.to_index,
        "hideUnavailableItems": False,
        "skusFilter": "ALL",
    }
    params = {
        "workspace": "master",
        "maxAge": "short",
        "appsEtag": "remove",
        "domain": "store",
        "locale": "es-HN",
        "operationName": "productSearchV3",
        "query": PRODUCT_SEARCH_QUERY,
        "variables": json.dumps(variables, separators=(",", ":")),
    }
    return f"{GRAPHQL_ENDPOINT}?{urlencode(params)}"


def observe_window_payload(
    window: WindowSpec,
    payload: Mapping[str, Any],
    *,
    response_bytes: int | None = None,
) -> WindowObservation:
    """Obtiene métricas agregadas antes de cualquier parsing de SKU."""

    product_search = _product_search(payload)
    products_value = product_search.get("products")
    if not isinstance(products_value, Sequence) or isinstance(
        products_value, (str, bytes)
    ):
        raise ValueError("Falta data.productSearch.products")
    if not all(isinstance(product, Mapping) for product in products_value):
        raise ValueError("La ventana contiene productos no materializados como objetos")
    products = list(products_value)

    records_filtered = product_search.get("recordsFiltered")
    if (
        isinstance(records_filtered, bool)
        or not isinstance(records_filtered, int)
        or records_filtered < 0
    ):
        raise ValueError("recordsFiltered es inválido")

    events: list[str] = []
    if len(products) < window.width:
        events.append("quality:partial_window")
    elif len(products) > window.width:
        events.append("structure:more_products_than_window")

    skus_returned = 0
    for product in products:
        items = product.get("items")
        if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
            events.append("structure:missing_items")
            continue
        skus_returned += len(items)

    keys = tuple(_product_key(product, index) for index, product in enumerate(products))
    signature = _signature(keys)
    if len(set(keys)) != len(keys):
        events.append("quality:duplicate_product_within_window")

    if response_bytes is None:
        response_bytes = len(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        )
    if (
        isinstance(response_bytes, bool)
        or not isinstance(response_bytes, int)
        or response_bytes < 0
    ):
        raise ValueError("response_bytes no puede ser negativo")

    return WindowObservation(
        window=window,
        records_filtered=records_filtered,
        products_returned=len(products),
        skus_returned=skus_returned,
        response_bytes=response_bytes,
        signature=signature,
        quality_events=tuple(dict.fromkeys(events)),
        _product_keys=keys,
    )


def diagnose_windows(
    observations: Sequence[WindowObservation],
) -> WindowDiagnosticReport:
    """Compara ventanas sin inferir automáticamente una causa raíz."""

    values = tuple(observations)
    if not values:
        raise ValueError("Se requiere al menos una ventana")
    names = [value.window.name for value in values]
    if len(set(names)) != len(names):
        raise ValueError("Los nombres de ventana deben ser únicos")
    orderings = {value.window.order_by for value in values}
    if len(orderings) != 1:
        raise ValueError("Todas las ventanas deben usar el mismo order_by")

    overlaps: list[OverlapObservation] = []
    for left_index, left in enumerate(values):
        left_keys = set(left._product_keys)
        for right in values[left_index + 1 :]:
            expected = _expected_overlap(left.window, right.window)
            observed = len(left_keys.intersection(right._product_keys))
            if expected or observed:
                overlaps.append(
                    OverlapObservation(
                        left=left.window.name,
                        right=right.window.name,
                        expected=expected,
                        observed=observed,
                    )
                )

    all_keys = [key for value in values for key in value._product_keys]
    unique_keys = set(all_keys)
    expected_positions = _expected_union_size([value.window for value in values])
    duplicates_within = sum(
        len(value._product_keys) - len(set(value._product_keys)) for value in values
    )

    events = [event for value in values for event in value.quality_events]
    if values[0].records_filtered != values[-1].records_filtered:
        events.append("quality:catalog_total_changed")
    if any(item.delta != 0 for item in overlaps):
        events.append("quality:unexpected_overlap")
    union_delta = len(unique_keys) - expected_positions
    if union_delta < 0:
        events.append("quality:union_unique_below_expected")
    elif union_delta > 0:
        events.append("quality:union_unique_above_expected")

    return WindowDiagnosticReport(
        observations=values,
        overlaps=tuple(overlaps),
        expected_unique_positions=expected_positions,
        products_unique_in_union=len(unique_keys),
        repeated_occurrences=len(all_keys) - len(unique_keys),
        duplicates_within_windows=duplicates_within,
        total_initial=values[0].records_filtered,
        total_final=values[-1].records_filtered,
        quality_events=tuple(dict.fromkeys(events)),
    )


def serialize_sanitized_report(
    report: WindowDiagnosticReport,
    *,
    max_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
) -> bytes:
    if max_bytes <= 0:
        raise ValueError("max_bytes debe ser mayor que cero")
    encoded = json.dumps(
        report.sanitized_summary(), ensure_ascii=False, indent=2
    ).encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValueError("El artefacto diagnóstico supera el tamaño permitido")
    return encoded


def write_sanitized_report(
    report: WindowDiagnosticReport,
    path: Path,
    *,
    max_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(serialize_sanitized_report(report, max_bytes=max_bytes))


def validate_sanitized_summary(value: Any) -> None:
    """Impide que el artefacto publique identificadores o datos comerciales."""

    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key) in _FORBIDDEN_PUBLIC_KEYS:
                raise ValueError(f"Campo no permitido en resumen sanitizado: {key}")
            validate_sanitized_summary(nested)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for nested in value:
            validate_sanitized_summary(nested)


def _product_search(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise ValueError("Falta data en la respuesta GraphQL")
    product_search = data.get("productSearch")
    if not isinstance(product_search, Mapping):
        raise ValueError("Falta data.productSearch")
    return product_search


def _product_key(product: Mapping[str, Any], index: int) -> str:
    for field_name in (
        "productId",
        "productReference",
        "linkText",
        "productName",
    ):
        value = product.get(field_name)
        if value is not None and str(value).strip():
            return f"{field_name}:{str(value).strip()}"
    return f"missing:{index}"


def _signature(keys: Sequence[str]) -> str:
    encoded = json.dumps(list(keys), separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _expected_overlap(left: WindowSpec, right: WindowSpec) -> int:
    start = max(left.from_index, right.from_index)
    end = min(left.to_index, right.to_index)
    return max(end - start + 1, 0)


def _expected_union_size(windows: Sequence[WindowSpec]) -> int:
    positions: set[int] = set()
    for window in windows:
        positions.update(range(window.from_index, window.to_index + 1))
    return len(positions)
