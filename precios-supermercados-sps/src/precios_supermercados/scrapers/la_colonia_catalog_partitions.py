"""Planificación offline de particiones de catálogo mediante facets de categoría.

El módulo recibe fixtures equivalentes a una respuesta de facets ya obtenida. No
realiza HTTP y no publica nombres, valores ni identificadores de categorías.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .la_colonia_catalog_coverage import PartitionSpec, StructuralDiscoveryReport
from .la_colonia_graphql import MAX_CATALOG_PAGE_SIZE

DEFAULT_MAX_PARTITIONS = 250
DEFAULT_MAX_PARTITION_REQUESTS = 500
_CATEGORY_KEY = re.compile(r"category-(\d+)\Z")


@dataclass(frozen=True, slots=True)
class PartitionRequestPlan:
    partitions: tuple[PartitionSpec, ...]
    primary_requests: int
    probe_requests: int
    recovery_reserve: int
    reconciliation_requests: int
    requests_planned: int
    request_limit: int
    within_limit: bool


def discover_leaf_category_partitions(
    facets: Sequence[Mapping[str, Any]],
    *,
    sampling: bool = False,
    max_partitions: int = DEFAULT_MAX_PARTITIONS,
) -> tuple[PartitionSpec, ...]:
    """Extrae categorías hoja con cantidad positiva de un fixture de facets."""

    if sampling:
        raise ValueError("Facets muestreadas no demuestran todas las particiones")
    if max_partitions <= 0:
        raise ValueError("max_partitions debe ser mayor que cero")

    report = build_structural_discovery_report(
        facets,
        run_id="legacy-offline-discovery",
        root_total=None,
        sampling=sampling,
        max_partitions=max_partitions,
    )
    if not report.valid:
        if "partition_limit_exceeded" in report.errors:
            raise ValueError("La cantidad de particiones supera el límite permitido")
        if "duplicate_structural_node_conflict" in report.errors:
            raise ValueError("La misma categoría hoja tiene cantidades incompatibles")
        raise ValueError(";".join(report.errors))
    return tuple(leaf for leaf in report.valid_leaves if leaf.expected_products > 0)


def build_structural_discovery_report(
    facets: Sequence[Mapping[str, Any]],
    *,
    run_id: str,
    root_total: int | None,
    sampling: bool = False,
    max_partitions: int = DEFAULT_MAX_PARTITIONS,
    max_category_level: int = 8,
) -> StructuralDiscoveryReport:
    """Valida el árbol original antes de deduplicar y conserva su universo."""

    errors: list[str] = []
    if not isinstance(run_id, str) or not run_id.strip():
        errors.append("invalid_run_id")
    nodes_seen = 0
    positive_nodes = 0
    invalid_positive = 0
    duplicates = 0
    leaf_nodes: dict[tuple[tuple[str, str], ...], int] = {}
    seen_nodes: dict[tuple[tuple[str, str], ...], str] = {}
    normalized_roots: list[dict[str, Any]] = []

    if sampling:
        errors.append("facets_sampled")
    if max_partitions <= 0:
        errors.append("invalid_partition_limit")
    if max_category_level <= 0:
        errors.append("invalid_category_level_limit")
    if (
        root_total is not None
        and (isinstance(root_total, bool) or not isinstance(root_total, int) or root_total < 0)
    ):
        errors.append("invalid_root_total")
    if not isinstance(facets, Sequence) or isinstance(facets, (str, bytes)):
        facets = ()
        errors.append("facets_not_sequence")

    def add_error(reason: str) -> None:
        if reason not in errors:
            errors.append(reason)

    def parse_nodes(
        values: Any,
        *,
        parent_path: tuple[tuple[str, str], ...],
        expected_level: int,
        parent_quantity: int | None,
    ) -> list[dict[str, Any]]:
        nonlocal nodes_seen, positive_nodes, invalid_positive, duplicates
        normalized: list[dict[str, Any]] = []
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            add_error("children_not_sequence")
            return normalized
        for node in values:
            nodes_seen += 1
            if not isinstance(node, Mapping):
                add_error("node_not_object")
                continue
            quantity_raw = node.get("quantity")
            if isinstance(quantity_raw, bool) or not isinstance(quantity_raw, int):
                add_error("quantity_not_integer")
                continue
            quantity = quantity_raw
            if quantity > 0:
                positive_nodes += 1
            if quantity < 0:
                add_error("negative_quantity")
                continue

            raw_key = node.get("key")
            raw_value = node.get("value")
            key = raw_key.strip() if isinstance(raw_key, str) else ""
            value = raw_value.strip() if isinstance(raw_value, str) else ""
            match = _CATEGORY_KEY.fullmatch(key)
            malformed_identity = not match or not value
            if malformed_identity:
                if quantity > 0:
                    invalid_positive += 1
                add_error("invalid_positive_leaf" if quantity > 0 else "invalid_node_identity")
                continue
            level = int(match.group(1))
            if level > max_category_level:
                if quantity > 0:
                    invalid_positive += 1
                add_error("category_level_above_limit")
            if level != expected_level:
                if quantity > 0:
                    invalid_positive += 1
                add_error("contradictory_topology")
            if parent_quantity is not None and quantity > parent_quantity:
                add_error("child_quantity_exceeds_parent")
            if "children" not in node:
                if quantity > 0:
                    invalid_positive += 1
                add_error("children_missing")
                continue
            children = node.get("children")
            if not isinstance(children, Sequence) or isinstance(children, (str, bytes)):
                if quantity > 0:
                    invalid_positive += 1
                add_error("children_not_sequence")
                continue

            path = (*parent_path, (key, value))
            normalized_children = parse_nodes(
                children,
                parent_path=path,
                expected_level=level + 1,
                parent_quantity=quantity,
            )
            normalized_node = {
                "key": key,
                "value": value,
                "quantity": quantity,
                "children": sorted(
                    normalized_children,
                    key=lambda item: json.dumps(item, sort_keys=True),
                ),
            }
            fingerprint = hashlib.sha256(
                json.dumps(normalized_node, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            previous = seen_nodes.get(path)
            if previous is not None:
                duplicates += 1
                if previous != fingerprint:
                    add_error("duplicate_structural_node_conflict")
                normalized.append(normalized_node)
                continue
            seen_nodes[path] = fingerprint
            if children:
                if not normalized_children:
                    add_error("positive_branch_without_valid_children")
            else:
                leaf_nodes[path] = quantity
            normalized.append(normalized_node)
        return normalized

    category_facets = 0
    for facet in facets:
        if not isinstance(facet, Mapping):
            add_error("facet_not_object")
            continue
        facet_type = str(facet.get("type") or "").strip().upper()
        if facet_type not in {"CATEGORYTREE", "CATEGORY"}:
            candidate_values = facet.get("values")
            if (
                isinstance(candidate_values, Sequence)
                and not isinstance(candidate_values, (str, bytes))
                and any(
                    isinstance(node, Mapping)
                    and str(node.get("key") or "").startswith("category-")
                    for node in candidate_values
                )
            ):
                add_error("unknown_category_facet_schema")
            continue
        category_facets += 1
        normalized_roots.extend(
            parse_nodes(
                facet.get("values"),
                parent_path=(),
                expected_level=1,
                parent_quantity=None,
            )
        )
    if category_facets == 0:
        add_error("category_facets_missing")

    ordered_leaves = sorted(leaf_nodes.items())
    if len(ordered_leaves) > max_partitions:
        add_error("partition_limit_exceeded")
    positive_sum = sum(quantity for _, quantity in ordered_leaves if quantity > 0)
    if root_total is not None:
        if root_total > 0 and not any(quantity > 0 for _, quantity in ordered_leaves):
            add_error("positive_leaves_missing")
        if positive_sum < root_total:
            add_error("leaf_union_below_root_total")

    leaves = tuple(
        PartitionSpec(
            name=f"partition-{index:04d}",
            facet_key=path[-1][0],
            facet_value=path[-1][1],
            expected_products=quantity,
            leaf=True,
            _category_path=path,
        )
        for index, (path, quantity) in enumerate(ordered_leaves, start=1)
    )
    canonical_tree = sorted(
        normalized_roots,
        key=lambda item: json.dumps(item, sort_keys=True),
    )
    tree_digest = hashlib.sha256(
        json.dumps(canonical_tree, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    identities = tuple(
        hashlib.sha256(
            json.dumps(path, separators=(",", ":")).encode()
        ).hexdigest()
        for path, _ in ordered_leaves
    )
    return StructuralDiscoveryReport(
        run_id=run_id,
        tree_digest=tree_digest,
        nodes_seen=nodes_seen,
        positive_nodes=positive_nodes,
        valid_leaves=leaves,
        invalid_positive_leaves=invalid_positive,
        duplicate_structural_nodes=duplicates,
        discovered_leaf_identities=identities,
        errors=tuple(errors),
        structural_status="VALID" if not errors else "INVALID",
        root_total=root_total if root_total is not None else positive_sum,
    )


def estimate_partition_request_plan(
    partitions: Sequence[PartitionSpec],
    *,
    page_size: int,
    include_boundary_probes: bool = False,
    recovery_windows_per_partition: int = 0,
    max_recovery_partitions: int | None = None,
    reconcile_with_second_order: bool = False,
    max_reconciliation_partitions: int | None = None,
    request_limit: int = DEFAULT_MAX_PARTITION_REQUESTS,
) -> PartitionRequestPlan:
    """Calcula un límite previo sin realizar solicitudes.

    Los defaults conservan el comportamiento histórico. Cuando se activan sondas,
    se reserva una por cada frontera interna. La recuperación y reconciliación
    pueden limitarse a un número cerrado de particiones anómalas.
    """

    values = tuple(partitions)
    if not 1 <= page_size <= MAX_CATALOG_PAGE_SIZE:
        raise ValueError(
            f"page_size debe estar entre 1 y {MAX_CATALOG_PAGE_SIZE}"
        )
    if recovery_windows_per_partition < 0:
        raise ValueError("recovery_windows_per_partition no puede ser negativo")
    if max_recovery_partitions is not None and max_recovery_partitions < 0:
        raise ValueError("max_recovery_partitions no puede ser negativo")
    if (
        max_reconciliation_partitions is not None
        and max_reconciliation_partitions < 0
    ):
        raise ValueError("max_reconciliation_partitions no puede ser negativo")
    if request_limit <= 0:
        raise ValueError("request_limit debe ser mayor que cero")

    page_counts = [
        math.ceil(partition.expected_products / page_size)
        if partition.expected_products
        else 0
        for partition in values
    ]
    primary = sum(page_counts)
    probes = (
        sum(max(pages - 1, 0) for pages in page_counts)
        if include_boundary_probes
        else 0
    )

    recovery_partition_count = len(values)
    if max_recovery_partitions is not None:
        recovery_partition_count = min(
            recovery_partition_count, max_recovery_partitions
        )
    recovery = recovery_partition_count * recovery_windows_per_partition

    reconciliation = 0
    if reconcile_with_second_order:
        ordered_pages = sorted(page_counts, reverse=True)
        if max_reconciliation_partitions is not None:
            ordered_pages = ordered_pages[:max_reconciliation_partitions]
        reconciliation = sum(ordered_pages)

    planned = primary + probes + recovery + reconciliation
    return PartitionRequestPlan(
        partitions=values,
        primary_requests=primary,
        probe_requests=probes,
        recovery_reserve=recovery,
        reconciliation_requests=reconciliation,
        requests_planned=planned,
        request_limit=request_limit,
        within_limit=planned <= request_limit,
    )
