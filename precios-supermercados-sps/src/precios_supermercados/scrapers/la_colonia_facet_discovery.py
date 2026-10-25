"""Contrato y análisis offline del descubrimiento de facets de La Colonia.

El módulo no realiza HTTP, no conoce GitHub Actions y no publica nombres ni
valores de categorías. El transporte futuro recibirá únicamente dos solicitudes
lógicas predeterminadas del plan ``catalog_categories_v1``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .la_colonia_catalog_coverage import PartitionSpec
from .la_colonia_catalog_partitions import (
    DEFAULT_MAX_PARTITIONS,
    PartitionRequestPlan,
    build_structural_discovery_report,
    estimate_partition_request_plan,
)

FACET_DISCOVERY_SCHEMA_VERSION = "1.0.0"
FACET_DISCOVERY_REQUEST_ID = "la-colonia-facet-discovery-001"
FACET_DISCOVERY_MODE = "facet_discovery"
FACET_DISCOVERY_PLAN_NAME = "catalog_categories_v1"
FACET_DISCOVERY_DELAY_SECONDS = 1.5
FACET_DISCOVERY_PAGE_SIZE = 50
FACET_DISCOVERY_REQUEST_LIMIT = 500
FACET_DISCOVERY_MAX_REQUESTS = 2
FACET_DISCOVERY_CONCURRENCY = 1
FACET_DISCOVERY_MAX_CATEGORY_LEVEL = 8
FACET_DISCOVERY_RECOVERY_REQUESTS = 4
FACET_DISCOVERY_MAX_RECOVERY_PARTITIONS = 5
FACET_DISCOVERY_MAX_RECONCILIATION_PARTITIONS = 2
FACET_DISCOVERY_MAX_ARTIFACT_BYTES = 64 * 1024

_ALLOWED_COMMAND_FIELDS = frozenset(
    {
        "request_id",
        "supermarket",
        "mode",
        "discovery_plan",
        "delay_seconds",
        "allow_full",
    }
)
_FORBIDDEN_PUBLIC_KEYS = frozenset(
    {
        "url",
        "query",
        "selectedFacets",
        "selected_facets",
        "facet_value",
        "facet_values",
        "category_name",
        "category_value",
        "productId",
        "product_id",
        "itemId",
        "item_id",
        "sku",
        "ean",
        "brand",
        "price",
        "payload",
        "products",
    }
)


class FacetDiscoveryError(ValueError):
    """Error cerrado y clasificable del análisis de facets."""


class InvalidFacetQuantitiesError(FacetDiscoveryError):
    pass


class IncompleteFacetTreeError(FacetDiscoveryError):
    pass


class InvalidFacetStructureError(FacetDiscoveryError):
    pass


class FacetPartitionLimitError(FacetDiscoveryError):
    pass


@dataclass(frozen=True, slots=True)
class FacetDiscoveryCommand:
    request_id: str
    supermarket: str
    mode: str
    discovery_plan: str
    delay_seconds: float
    allow_full: bool


@dataclass(frozen=True, slots=True)
class FacetDiscoveryRequest:
    """Solicitud lógica predeterminada; no contiene URL ni query arbitraria."""

    name: str
    sequence: int
    response_kind: str


@dataclass(frozen=True, slots=True)
class FacetDiscoveryPlan:
    name: str
    requests: tuple[FacetDiscoveryRequest, ...]
    page_size: int = FACET_DISCOVERY_PAGE_SIZE
    request_limit: int = FACET_DISCOVERY_REQUEST_LIMIT
    max_requests: int = FACET_DISCOVERY_MAX_REQUESTS
    max_category_level: int = FACET_DISCOVERY_MAX_CATEGORY_LEVEL
    max_partitions: int = DEFAULT_MAX_PARTITIONS

    def __post_init__(self) -> None:
        if self.name != FACET_DISCOVERY_PLAN_NAME:
            raise ValueError("Plan de facets no autorizado")
        if self.requests != (
            FacetDiscoveryRequest("root_total", 1, "root_total"),
            FacetDiscoveryRequest("category_tree", 2, "category_tree"),
        ):
            raise ValueError("El plan debe contener las dos solicitudes cerradas")
        if self.max_requests != len(self.requests):
            raise ValueError("max_requests debe coincidir con el plan cerrado")
        if self.page_size != 50 or self.request_limit != 500:
            raise ValueError("Los límites del plan no pueden modificarse")


CATALOG_CATEGORIES_V1 = FacetDiscoveryPlan(
    name=FACET_DISCOVERY_PLAN_NAME,
    requests=(
        FacetDiscoveryRequest("root_total", 1, "root_total"),
        FacetDiscoveryRequest("category_tree", 2, "category_tree"),
    ),
)


@dataclass(frozen=True, slots=True)
class FacetLeafPartition:
    """Hoja privada; la ruta real nunca se serializa."""

    name: str
    quantity: int
    level: int
    _path: tuple[tuple[str, str], ...] = field(repr=False, compare=False)

    def budget_spec(self) -> PartitionSpec:
        key, value = self._path[-1]
        return PartitionSpec(
            name=self.name,
            facet_key=key,
            facet_value=value,
            expected_products=self.quantity,
            leaf=True,
        )


@dataclass(frozen=True, slots=True)
class FacetTreeAnalysis:
    root_total: int
    sampling: bool
    facet_levels_detected: tuple[str, ...]
    facet_values_count: Mapping[str, int]
    leaf_partitions: tuple[FacetLeafPartition, ...]
    leaf_partitions_count: int
    positive_leaf_partitions: int
    zero_quantity_partitions: int
    leaf_quantity_sum: int
    quality_events: tuple[str, ...]

    def budget_specs(self) -> tuple[PartitionSpec, ...]:
        return tuple(item.budget_spec() for item in self.leaf_partitions)


@dataclass(frozen=True, slots=True)
class FacetBudget:
    primary_requests: int
    probe_requests: int
    recovery_reserve: int
    reconciliation_requests: int
    total_estimated_requests: int
    request_limit: int
    within_request_limit: bool


def parse_facet_discovery_command(value: Mapping[str, Any]) -> FacetDiscoveryCommand:
    """Valida el contrato exacto; cualquier campo adicional queda rechazado."""

    if not isinstance(value, Mapping):
        raise ValueError("El contrato facet_discovery debe ser un objeto")
    keys = set(value)
    missing = _ALLOWED_COMMAND_FIELDS - keys
    extra = keys - _ALLOWED_COMMAND_FIELDS
    if missing:
        raise ValueError(f"Faltan campos requeridos: {sorted(missing)}")
    if extra:
        raise ValueError(f"El contrato contiene campos no permitidos: {sorted(extra)}")

    request_id = value["request_id"]
    supermarket = value["supermarket"]
    mode = value["mode"]
    discovery_plan = value["discovery_plan"]
    delay = value["delay_seconds"]
    allow_full = value["allow_full"]

    if request_id != FACET_DISCOVERY_REQUEST_ID:
        raise ValueError("request_id no autorizado")
    if supermarket != "la_colonia":
        raise ValueError("supermarket no autorizado")
    if mode != FACET_DISCOVERY_MODE:
        raise ValueError("mode no autorizado")
    if discovery_plan != FACET_DISCOVERY_PLAN_NAME:
        raise ValueError("discovery_plan no autorizado")
    if isinstance(delay, bool) or not isinstance(delay, (int, float)):
        raise ValueError("delay_seconds debe ser numérico")
    if float(delay) != FACET_DISCOVERY_DELAY_SECONDS:
        raise ValueError("delay_seconds debe ser exactamente 1.5")
    if allow_full is not False:
        raise ValueError("allow_full debe ser false")

    return FacetDiscoveryCommand(
        request_id=request_id,
        supermarket=supermarket,
        mode=mode,
        discovery_plan=discovery_plan,
        delay_seconds=float(delay),
        allow_full=False,
    )


def get_facet_discovery_plan(name: str) -> FacetDiscoveryPlan:
    if name != FACET_DISCOVERY_PLAN_NAME:
        raise ValueError("Plan de facets desconocido")
    return CATALOG_CATEGORIES_V1


def read_root_total(payload: Mapping[str, Any]) -> int:
    if not isinstance(payload, Mapping):
        raise InvalidFacetStructureError("La respuesta raíz debe ser un objeto")
    value = payload.get("recordsFiltered")
    if isinstance(value, bool):
        raise InvalidFacetQuantitiesError("recordsFiltered debe ser entero")
    try:
        total = int(value)
    except (TypeError, ValueError) as exc:
        raise InvalidFacetQuantitiesError("recordsFiltered debe ser entero") from exc
    if total < 0:
        raise InvalidFacetQuantitiesError("recordsFiltered no puede ser negativo")
    return total


def analyze_category_facets(
    payload: Mapping[str, Any],
    *,
    root_total: int,
    max_category_level: int = FACET_DISCOVERY_MAX_CATEGORY_LEVEL,
    max_partitions: int = DEFAULT_MAX_PARTITIONS,
) -> FacetTreeAnalysis:
    """Identifica las hojas más específicas de un fixture normalizado."""

    if root_total < 0:
        raise InvalidFacetQuantitiesError("root_total no puede ser negativo")
    if not isinstance(payload, Mapping):
        raise InvalidFacetStructureError("La respuesta de facets debe ser un objeto")

    response_total = read_root_total(payload)
    if response_total != root_total:
        raise InvalidFacetStructureError("El total cambió durante facet discovery")

    sampling = payload.get("sampling")
    if not isinstance(sampling, bool):
        raise InvalidFacetStructureError("sampling debe ser booleano")
    if sampling:
        return FacetTreeAnalysis(
            root_total=root_total,
            sampling=True,
            facet_levels_detected=(),
            facet_values_count={},
            leaf_partitions=(),
            leaf_partitions_count=0,
            positive_leaf_partitions=0,
            zero_quantity_partitions=0,
            leaf_quantity_sum=0,
            quality_events=("sampling_detected",),
        )

    facets = payload.get("facets")
    if not isinstance(facets, Sequence) or isinstance(facets, (str, bytes)):
        raise InvalidFacetStructureError("facets debe ser una secuencia")

    report = build_structural_discovery_report(
        facets,
        run_id="facet-discovery-analysis",
        root_total=root_total,
        sampling=False,
        max_partitions=max_partitions,
        max_category_level=max_category_level,
    )
    if not report.valid:
        quantity_errors = {
            "quantity_not_integer",
            "negative_quantity",
            "child_quantity_exceeds_parent",
            "duplicate_structural_node_conflict",
        }
        if "partition_limit_exceeded" in report.errors:
            raise FacetPartitionLimitError("La cantidad de hojas supera el límite")
        if any(reason in quantity_errors for reason in report.errors):
            if "quantity_not_integer" in report.errors:
                raise InvalidFacetQuantitiesError("quantity debe ser entero")
            if "negative_quantity" in report.errors:
                raise InvalidFacetQuantitiesError("quantity no puede ser negativo")
            if "duplicate_structural_node_conflict" in report.errors:
                raise InvalidFacetQuantitiesError(
                    "La misma ruta tiene cantidades o topología incompatibles"
                )
            raise InvalidFacetQuantitiesError("Cantidades estructurales inválidas")
        incomplete_errors = {
            "category_facets_missing",
            "contradictory_topology",
            "children_missing",
            "positive_branch_without_valid_children",
            "positive_leaves_missing",
            "leaf_union_below_root_total",
        }
        if any(reason in incomplete_errors for reason in report.errors):
            if "children_missing" in report.errors:
                raise IncompleteFacetTreeError("Cada nodo debe declarar children")
            if "leaf_union_below_root_total" in report.errors:
                raise IncompleteFacetTreeError(
                    "La suma de hojas positivas no cubre el total raíz"
                )
            if report.errors == ("positive_leaves_missing",):
                raise IncompleteFacetTreeError("No existen particiones hoja positivas")
            raise IncompleteFacetTreeError("El árbol de categorías está incompleto")
        raise InvalidFacetStructureError("Evidencia estructural inválida")

    positive = tuple(leaf for leaf in report.valid_leaves if leaf.expected_products > 0)
    zero_leaves = sum(leaf.expected_products == 0 for leaf in report.valid_leaves)
    leaf_quantity_sum = sum(leaf.expected_products for leaf in positive)
    events: list[str] = []
    if leaf_quantity_sum > root_total:
        events.append("leaf_quantities_exceed_root_total")
    if report.duplicate_structural_nodes:
        events.append("duplicate_structural_nodes")

    partitions = tuple(
        FacetLeafPartition(
            name=leaf.name,
            quantity=leaf.expected_products,
            level=len(leaf._category_path),
            _path=leaf._category_path,
        )
        for leaf in positive
    )
    nodes_by_level: dict[int, set[tuple[tuple[str, str], ...]]] = {}
    for leaf in report.valid_leaves:
        for level in range(1, len(leaf._category_path) + 1):
            nodes_by_level.setdefault(level, set()).add(leaf._category_path[:level])
    counts = {level: len(paths) for level, paths in nodes_by_level.items()}
    level_names = tuple(f"category-{level}" for level in sorted(counts))
    counts_by_name = {f"category-{level}": counts[level] for level in sorted(counts)}
    return FacetTreeAnalysis(
        root_total=root_total,
        sampling=False,
        facet_levels_detected=level_names,
        facet_values_count=counts_by_name,
        leaf_partitions=partitions,
        leaf_partitions_count=len(report.valid_leaves),
        positive_leaf_partitions=len(partitions),
        zero_quantity_partitions=zero_leaves,
        leaf_quantity_sum=leaf_quantity_sum,
        quality_events=tuple(events),
    )


def estimate_facet_discovery_budget(
    analysis: FacetTreeAnalysis,
    *,
    plan: FacetDiscoveryPlan = CATALOG_CATEGORIES_V1,
) -> FacetBudget:
    if analysis.sampling:
        return FacetBudget(0, 0, 0, 0, 0, plan.request_limit, False)
    request_plan: PartitionRequestPlan = estimate_partition_request_plan(
        analysis.budget_specs(),
        page_size=plan.page_size,
        include_boundary_probes=True,
        recovery_windows_per_partition=FACET_DISCOVERY_RECOVERY_REQUESTS,
        max_recovery_partitions=FACET_DISCOVERY_MAX_RECOVERY_PARTITIONS,
        reconcile_with_second_order=True,
        max_reconciliation_partitions=(
            FACET_DISCOVERY_MAX_RECONCILIATION_PARTITIONS
        ),
        request_limit=plan.request_limit,
    )
    return FacetBudget(
        primary_requests=request_plan.primary_requests,
        probe_requests=request_plan.probe_requests,
        recovery_reserve=request_plan.recovery_reserve,
        reconciliation_requests=request_plan.reconciliation_requests,
        total_estimated_requests=request_plan.requests_planned,
        request_limit=request_plan.request_limit,
        within_request_limit=request_plan.within_limit,
    )


def validate_sanitized_facet_summary(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key) in _FORBIDDEN_PUBLIC_KEYS:
                raise ValueError(f"Campo no permitido en resumen sanitizado: {key}")
            validate_sanitized_facet_summary(nested)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for nested in value:
            validate_sanitized_facet_summary(nested)


def serialize_sanitized_facet_summary(
    summary: Mapping[str, Any],
    *,
    max_bytes: int = FACET_DISCOVERY_MAX_ARTIFACT_BYTES,
) -> bytes:
    if max_bytes <= 0:
        raise ValueError("max_bytes debe ser mayor que cero")
    validate_sanitized_facet_summary(summary)
    encoded = json.dumps(
        summary, ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValueError("El artefacto facet_discovery supera 64 KiB")
    return encoded
