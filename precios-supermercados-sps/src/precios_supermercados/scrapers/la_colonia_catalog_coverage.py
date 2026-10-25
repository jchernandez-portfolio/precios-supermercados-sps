"""Contrato offline y sanitizado de cobertura para La Colonia.

Este módulo no realiza solicitudes HTTP y no persiste datos comerciales. Su
evaluador canónico está diseñado para ser consumido por runner y CLI. Las identidades se mantienen únicamente
en memoria para demostrar cobertura, detectar repeticiones y reconciliar
particiones u órdenes de búsqueda.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from types import MappingProxyType
from typing import Any

from .la_colonia_graphql import ALLOWED_ORDER_BY, MAX_CATALOG_PAGE_SIZE

COVERAGE_SCHEMA_VERSION = "1.0.0"
DEFAULT_MAX_COVERAGE_REQUESTS = 500
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
_RAW_COLLECTOR_ISSUER = object()


@dataclass(frozen=True, slots=True)
class PartitionSpec:
    """Partición lógica cerrada, normalmente una categoría hoja."""

    name: str
    facet_key: str
    facet_value: str
    expected_products: int
    leaf: bool = True
    _category_path: tuple[tuple[str, str], ...] = field(
        default=(), repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("name no puede estar vacío")
        if (
            not isinstance(self.facet_key, str)
            or not isinstance(self.facet_value, str)
            or not self.facet_key.strip()
            or not self.facet_value.strip()
        ):
            raise ValueError("facet_key y facet_value no pueden estar vacíos")
        if (
            isinstance(self.expected_products, bool)
            or not isinstance(self.expected_products, int)
            or self.expected_products < 0
        ):
            raise ValueError("expected_products no puede ser negativo")
        if not self.facet_key.startswith("category-"):
            raise ValueError("La partición debe usar una facet de categoría")


@dataclass(frozen=True, slots=True)
class StructuralDiscoveryReport:
    """Universo estructural autoritativo derivado del árbol original."""

    run_id: str
    tree_digest: str
    nodes_seen: int
    positive_nodes: int
    valid_leaves: tuple[PartitionSpec, ...]
    invalid_positive_leaves: int
    duplicate_structural_nodes: int
    discovered_leaf_identities: tuple[str, ...]
    errors: tuple[str, ...]
    structural_status: str
    root_total: int

    @property
    def valid(self) -> bool:
        return self.structural_status == "VALID" and not self.errors


@dataclass(frozen=True, slots=True)
class RawProductEvidence:
    """Evidencia mínima para identidad y membership, antes de deduplicar."""

    product_id: str | None
    product_reference: str | None
    link_text: str | None
    item_ids: tuple[str, ...]
    category_paths: tuple[tuple[tuple[str, tuple[str, ...]], ...], ...]

    @property
    def identity(self) -> str | None:
        for label, value in (
            ("productId", self.product_id),
            ("productReference", self.product_reference),
            ("linkText", self.link_text),
        ):
            if (text := _optional_text(value)) is not None:
                return f"{label}:{text}"
        return None

    @property
    def sku_identities(self) -> tuple[str, ...]:
        return tuple(f"itemId:{value}" for value in self.item_ids)


@dataclass(frozen=True, slots=True)
class RawPageEvidence:
    run_id: str
    traversal_id: str
    partition: str
    order_by: str
    from_index: int
    to_index: int
    records_filtered: int
    products: tuple[RawProductEvidence, ...]
    response_digest: str
    purpose: str = "PRIMARY"
    _collector_issuer: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        for label, value in (
            ("run_id", self.run_id),
            ("traversal_id", self.traversal_id),
            ("partition", self.partition),
            ("response_digest", self.response_digest),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} debe ser texto no vacío")
        if self.order_by not in ALLOWED_ORDER_BY:
            raise ValueError("order_by de página no permitido")
        if (
            isinstance(self.from_index, bool)
            or not isinstance(self.from_index, int)
            or isinstance(self.to_index, bool)
            or not isinstance(self.to_index, int)
            or self.from_index < 0
            or self.to_index < self.from_index
        ):
            raise ValueError("Rango de página inválido")
        _closed_nonnegative_int(self.records_filtered)
        if self.purpose not in {"PRIMARY", "RECOVERY"}:
            raise ValueError("purpose de evidencia no permitido")


@dataclass(frozen=True, slots=True)
class TraversalEvidence:
    """Una travesía completa con lineage inequívoco."""

    run_id: str
    traversal_id: str
    tree_digest: str
    plan_digest: str
    order_by: str
    pages: tuple[RawPageEvidence, ...]

    def __post_init__(self) -> None:
        if not self.traversal_id.strip() or not self.plan_digest.strip():
            raise ValueError("La traversal debe tener identidad y plan digest")
        if self.order_by not in ALLOWED_ORDER_BY:
            raise ValueError("order_by de traversal no permitido")
        if any(page.traversal_id != self.traversal_id for page in self.pages):
            raise ValueError("La evidencia mezcla identidades de traversal")
        if any(page.run_id != self.run_id for page in self.pages):
            raise ValueError("La evidencia mezcla run_id")
        if any(page.order_by != self.order_by for page in self.pages):
            raise ValueError("La evidencia mezcla order_by")


@dataclass(frozen=True, slots=True)
class CanonicalCatalogEvidence:
    """Entrada cruda autoritativa; totales y estructura se derivan internamente."""

    run_id: str
    root_response: Mapping[str, Any]
    facets_response: Mapping[str, Any]
    primary: TraversalEvidence
    reconciliation: TraversalEvidence | None

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id canónico no puede estar vacío")
        object.__setattr__(self, "root_response", _deep_freeze(self.root_response))
        object.__setattr__(self, "facets_response", _deep_freeze(self.facets_response))


@dataclass(frozen=True, slots=True)
class CoveragePageObservation:
    """Respuesta agregada; las identidades privadas nunca se serializan."""

    partition: str
    order_by: str
    from_index: int
    to_index: int
    records_filtered: int
    products_received: int
    products_expected: int
    complete: bool
    sequence_signature: str
    set_signature: str
    quality_events: tuple[str, ...] = ()
    _product_keys: tuple[str, ...] = field(default=(), repr=False, compare=False)

    @property
    def width(self) -> int:
        return self.to_index - self.from_index + 1


@dataclass(frozen=True, slots=True)
class PartitionCoverageResult:
    """Cobertura demostrada o rechazada para una partición."""

    partition: str
    pages_expected: int
    pages_attempted: int
    pages_completed: int
    products_reported: int
    products_received: int
    products_unique: int
    duplicate_occurrences: int
    repeated_page_sets: int
    unexpected_overlaps: int
    missing_coverage_events: int
    total_changes: int
    orderings_attempted: int
    orderings_reconciled: bool
    coverage_demonstrated: bool
    coverage_reason: str
    _product_keys: tuple[str, ...] = field(default=(), repr=False, compare=False)
    _sku_keys: tuple[str, ...] = field(default=(), repr=False, compare=False)
    _product_sku_pairs: tuple[tuple[str, str], ...] = field(
        default=(), repr=False, compare=False
    )
    _reasons: tuple[str, ...] = field(default=(), repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class CatalogCoverageReport:
    """Resumen agregado sin productos, categorías ni identificadores."""

    partitions_discovered: int
    partitions_attempted: int
    partitions_completed: int
    pages_expected: int
    pages_attempted: int
    pages_completed: int
    products_reported: int
    products_received: int
    products_unique: int
    duplicate_occurrences: int
    repeated_page_sets: int
    unexpected_overlaps: int
    missing_coverage_events: int
    total_changes: int
    uncategorized_products: int
    request_limit: int
    coverage_demonstrated: bool
    coverage_reason: str
    accepted: bool
    run_id: str
    tree_digest: str
    primary_plan_digest: str
    reconciliation_plan_digest: str
    _reasons: tuple[str, ...] = field(default=(), repr=False, compare=False)
    _product_keys: tuple[str, ...] = field(default=(), repr=False, compare=False)
    _sku_keys: tuple[str, ...] = field(default=(), repr=False, compare=False)
    _product_sku_pairs: tuple[tuple[str, str], ...] = field(
        default=(), repr=False, compare=False
    )

    def sanitized_summary(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("_reasons", None)
        value.pop("_product_keys", None)
        value.pop("_sku_keys", None)
        value.pop("_product_sku_pairs", None)
        summary = {"schema_version": COVERAGE_SCHEMA_VERSION, **value}
        validate_sanitized_coverage_summary(summary)
        return summary


def raw_page_evidence_from_response(
    *,
    run_id: str,
    traversal_id: str,
    partition: str,
    order_by: str,
    from_index: int,
    to_index: int,
    response: Mapping[str, Any],
    purpose: str = "PRIMARY",
) -> RawPageEvidence:
    """Parsea payload GraphQL crudo; totals/products no son argumentos libres."""

    data = response.get("data")
    if not isinstance(data, Mapping):
        raise ValueError("La evidencia de página no contiene data")
    product_search = data.get("productSearch")
    if not isinstance(product_search, Mapping):
        raise ValueError("La evidencia de página no contiene productSearch")
    records_filtered = _closed_nonnegative_int(product_search.get("recordsFiltered"))
    products = product_search.get("products")
    if not isinstance(products, Sequence) or isinstance(products, (str, bytes)):
        raise ValueError("La evidencia de página no contiene products")
    return _raw_page_evidence_from_values(
        run_id=run_id,
        traversal_id=traversal_id,
        partition=partition,
        order_by=order_by,
        from_index=from_index,
        to_index=to_index,
        records_filtered=records_filtered,
        products=products,
        response_digest=_mapping_digest(product_search),
        purpose=purpose,
    )


def _raw_page_evidence_from_values(
    *,
    run_id: str,
    traversal_id: str,
    partition: str,
    order_by: str,
    from_index: int,
    to_index: int,
    records_filtered: int,
    products: Sequence[Mapping[str, Any]],
    response_digest: str,
    purpose: str,
) -> RawPageEvidence:

    if purpose not in {"PRIMARY", "RECOVERY"}:
        raise ValueError("purpose de evidencia no permitido")
    evidence: list[RawProductEvidence] = []
    for product in products:
        if not isinstance(product, Mapping):
            raise ValueError("Cada producto de evidencia debe ser un objeto")
        for identity_field in ("productId", "productReference", "linkText"):
            identity_value = product.get(identity_field)
            if identity_value is not None and not isinstance(identity_value, str):
                raise ValueError(f"{identity_field} debe ser texto")
        tree = product.get("categoryTree")
        candidates_by_level: list[tuple[str, tuple[str, ...]]] = []
        if isinstance(tree, Sequence) and not isinstance(tree, (str, bytes)):
            for level, node in enumerate(tree, start=1):
                if not isinstance(node, Mapping):
                    candidates_by_level = []
                    break
                candidates = tuple(
                    dict.fromkeys(
                        value
                        for raw in (node.get("id"), node.get("value"), node.get("name"))
                        if (value := _optional_text(raw)) is not None
                    )
                )
                if not candidates:
                    candidates_by_level = []
                    break
                candidates_by_level.append((f"category-{level}", candidates))
        candidate_paths: list[tuple[tuple[str, tuple[str, ...]], ...]] = []
        if candidates_by_level:
            candidate_paths.append(tuple(candidates_by_level))
        categories = product.get("categories")
        if isinstance(categories, Sequence) and not isinstance(categories, (str, bytes)):
            for raw_category in categories:
                if (text := _optional_text(raw_category)) is None:
                    continue
                segments = [segment.strip() for segment in text.split("/") if segment.strip()]
                if not segments:
                    continue
                path = tuple(
                    (f"category-{index}", (segment,))
                    for index, segment in enumerate(segments, start=1)
                )
                represented = any(
                    len(existing) == len(path)
                    and all(
                        key == existing_key and value[0] in existing_values
                        for (key, value), (existing_key, existing_values) in zip(
                            path, existing
                        )
                    )
                    for existing in candidate_paths
                )
                if not represented and path not in candidate_paths:
                    candidate_paths.append(path)
        raw_items = product.get("items")
        if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
            raise ValueError("Cada producto debe contener items verificables")
        item_ids: list[str] = []
        for item in raw_items:
            if not isinstance(item, Mapping):
                raise ValueError("Cada SKU de evidencia debe ser un objeto")
            item_id = _optional_text(item.get("itemId"))
            if item_id is None:
                raise ValueError("Cada SKU de evidencia requiere itemId")
            item_ids.append(item_id)
        evidence.append(
            RawProductEvidence(
                product_id=_optional_text(product.get("productId")),
                product_reference=_optional_text(product.get("productReference")),
                link_text=_optional_text(product.get("linkText")),
                item_ids=tuple(item_ids),
                category_paths=tuple(candidate_paths),
            )
        )
    return RawPageEvidence(
        run_id=run_id,
        traversal_id=traversal_id,
        partition=partition,
        order_by=order_by,
        from_index=from_index,
        to_index=to_index,
        records_filtered=records_filtered,
        products=tuple(evidence),
        response_digest=response_digest,
        purpose=purpose,
        _collector_issuer=_RAW_COLLECTOR_ISSUER,
    )


def build_traversal_evidence(
    *,
    run_id: str,
    traversal_id: str,
    tree_digest: str,
    order_by: str,
    pages: Sequence[RawPageEvidence],
) -> TraversalEvidence:
    values = tuple(pages)
    plan_digest = _traversal_plan_digest(
        run_id=run_id,
        traversal_id=traversal_id,
        tree_digest=tree_digest,
        order_by=order_by,
        pages=values,
    )
    return TraversalEvidence(
        run_id=run_id,
        traversal_id=traversal_id,
        tree_digest=tree_digest,
        plan_digest=plan_digest,
        order_by=order_by,
        pages=values,
    )


def evaluate_canonical_catalog_coverage(
    evidence: CanonicalCatalogEvidence,
    *,
    request_limit: int = DEFAULT_MAX_COVERAGE_REQUESTS,
) -> CatalogCoverageReport:
    """Deriva total y estructura desde respuestas crudas antes de decidir."""

    from .la_colonia_catalog_partitions import build_structural_discovery_report

    structural_errors: list[str] = []
    try:
        root_total = _closed_nonnegative_int(
            evidence.root_response.get("recordsFiltered")
        )
    except ValueError:
        root_total = 0
        structural_errors.append("invalid_root_response_total")
    try:
        facets_total = _closed_nonnegative_int(
            evidence.facets_response.get("recordsFiltered")
        )
        if facets_total != root_total:
            structural_errors.append("facet_total_differs_from_root")
    except ValueError:
        structural_errors.append("invalid_facets_response_total")
    sampling = evidence.facets_response.get("sampling")
    if not isinstance(sampling, bool):
        structural_errors.append("invalid_sampling_evidence")
        sampling = True
    facets = evidence.facets_response.get("facets")
    if not isinstance(facets, Sequence) or isinstance(facets, (str, bytes)):
        structural_errors.append("invalid_facets_evidence")
        facets = ()
    structure = build_structural_discovery_report(
        facets,
        run_id=evidence.run_id,
        root_total=root_total,
        sampling=sampling,
    )
    if structural_errors:
        structure = StructuralDiscoveryReport(
            run_id=structure.run_id,
            tree_digest=structure.tree_digest,
            nodes_seen=structure.nodes_seen,
            positive_nodes=structure.positive_nodes,
            valid_leaves=structure.valid_leaves,
            invalid_positive_leaves=structure.invalid_positive_leaves,
            duplicate_structural_nodes=structure.duplicate_structural_nodes,
            discovered_leaf_identities=structure.discovered_leaf_identities,
            errors=tuple((*structural_errors, *structure.errors)),
            structural_status="INVALID",
            root_total=structure.root_total,
        )
    return _evaluate_canonical_catalog_coverage(
        structure,
        evidence.primary,
        evidence.reconciliation,
        request_limit=request_limit,
    )


def _evaluate_canonical_catalog_coverage(
    structure: StructuralDiscoveryReport,
    primary: TraversalEvidence,
    reconciliation: TraversalEvidence | None,
    *,
    request_limit: int = DEFAULT_MAX_COVERAGE_REQUESTS,
) -> CatalogCoverageReport:
    """Única decisión autoritativa de completitud del catálogo."""

    if request_limit <= 0:
        raise ValueError("request_limit debe ser mayor que cero")
    reasons: list[str] = []
    _append_unique(reasons, "trusted_collector_provenance_unavailable")
    if not structure.valid:
        _append_unique(reasons, "invalid_structural_evidence")
    for traversal in (primary, reconciliation):
        if traversal is None:
            continue
        if traversal.run_id != structure.run_id:
            _append_unique(reasons, "traversal_run_id_mismatch")
        if traversal.tree_digest != structure.tree_digest:
            _append_unique(reasons, "traversal_tree_digest_mismatch")
        expected_digest = _traversal_plan_digest(
            run_id=traversal.run_id,
            traversal_id=traversal.traversal_id,
            tree_digest=traversal.tree_digest,
            order_by=traversal.order_by,
            pages=traversal.pages,
        )
        if traversal.plan_digest != expected_digest:
            _append_unique(reasons, "traversal_plan_digest_invalid")
    if reconciliation is None:
        _append_unique(reasons, "independent_reconciliation_missing")
    elif reconciliation.traversal_id == primary.traversal_id:
        _append_unique(reasons, "self_reconciliation_forbidden")
    elif reconciliation.order_by == primary.order_by:
        _append_unique(reasons, "reconciliation_order_not_independent")
    if not primary.pages:
        _append_unique(reasons, "primary_collector_observation_missing")
    if any(page._collector_issuer is not _RAW_COLLECTOR_ISSUER for page in primary.pages):
        _append_unique(reasons, "primary_collector_provenance_invalid")
    if reconciliation is not None:
        if not reconciliation.pages:
            _append_unique(reasons, "reconciliation_collector_observation_missing")
        if any(
            page._collector_issuer is not _RAW_COLLECTOR_ISSUER
            for page in reconciliation.pages
        ):
            _append_unique(reasons, "reconciliation_collector_provenance_invalid")
        primary_responses = {page.response_digest for page in primary.pages}
        secondary_responses = {page.response_digest for page in reconciliation.pages}
        if primary_responses.intersection(secondary_responses):
            _append_unique(reasons, "reconciliation_response_reused")

    primary_results = _evaluate_raw_traversal(structure, primary)
    secondary_results = (
        _evaluate_raw_traversal(structure, reconciliation)
        if reconciliation is not None
        else {}
    )
    partition_results: list[PartitionCoverageResult] = []
    global_primary: set[str] = set()
    global_secondary: set[str] = set()
    global_primary_skus: set[str] = set()
    global_secondary_skus: set[str] = set()
    global_primary_pairs: set[tuple[str, str]] = set()
    global_secondary_pairs: set[tuple[str, str]] = set()
    for leaf in structure.valid_leaves:
        first = primary_results.get(leaf.name)
        second = secondary_results.get(leaf.name)
        if first is None or not first.coverage_demonstrated:
            _append_unique(reasons, f"primary_incomplete:{leaf.name}")
        if second is None or not second.coverage_demonstrated:
            _append_unique(reasons, f"reconciliation_incomplete:{leaf.name}")
        if first is not None:
            global_primary.update(first._product_keys)
            global_primary_skus.update(first._sku_keys)
            global_primary_pairs.update(first._product_sku_pairs)
            partition_results.append(first)
        if second is not None:
            global_secondary.update(second._product_keys)
            global_secondary_skus.update(second._sku_keys)
            global_secondary_pairs.update(second._product_sku_pairs)
        if first is not None and second is not None:
            if first._product_keys != second._product_keys:
                _append_unique(reasons, f"reconciliation_union_mismatch:{leaf.name}")
            if first._sku_keys != second._sku_keys:
                _append_unique(reasons, f"reconciliation_sku_union_mismatch:{leaf.name}")
            if first._product_sku_pairs != second._product_sku_pairs:
                _append_unique(reasons, f"reconciliation_product_sku_mapping_mismatch:{leaf.name}")

    expected_names = {leaf.name for leaf in structure.valid_leaves}
    observed_primary = {page.partition for page in primary.pages}
    observed_secondary = (
        {page.partition for page in reconciliation.pages}
        if reconciliation is not None
        else set()
    )
    if observed_primary - expected_names or observed_secondary - expected_names:
        _append_unique(reasons, "unknown_partition_observed")
    if global_primary != global_secondary:
        _append_unique(reasons, "global_reconciliation_mismatch")
    if global_primary_skus != global_secondary_skus:
        _append_unique(reasons, "global_sku_reconciliation_mismatch")
    if global_primary_pairs != global_secondary_pairs:
        _append_unique(reasons, "global_product_sku_mapping_mismatch")
    for pairs, label in (
        (global_primary_pairs, "primary"),
        (global_secondary_pairs, "reconciliation"),
    ):
        owners: dict[str, str] = {}
        for product_key, sku_key in pairs:
            previous_owner = owners.get(sku_key)
            if previous_owner is not None and previous_owner != product_key:
                _append_unique(reasons, f"global_sku_owner_conflict:{label}")
            owners[sku_key] = product_key
    if len(global_primary) != structure.root_total:
        _append_unique(reasons, "global_union_differs_from_structural_root")

    pages_attempted = len(primary.pages) + (
        len(reconciliation.pages) if reconciliation is not None else 0
    )
    if pages_attempted > request_limit:
        _append_unique(reasons, "request_limit_exceeded")
    inherited = (
        reason
        for result in (*primary_results.values(), *secondary_results.values())
        for reason in result._reasons
    )
    _extend_unique(reasons, inherited)

    occurrences = sum(len(result._product_keys) for result in partition_results)
    cross_partition_duplicates = occurrences - len(global_primary)
    accepted = not reasons
    return CatalogCoverageReport(
        partitions_discovered=len(structure.valid_leaves),
        partitions_attempted=len(primary_results),
        partitions_completed=sum(
            result.coverage_demonstrated for result in primary_results.values()
        ),
        pages_expected=sum(result.pages_expected for result in primary_results.values()),
        pages_attempted=pages_attempted,
        pages_completed=sum(result.pages_completed for result in primary_results.values()),
        products_reported=structure.root_total,
        products_received=sum(
            len(page.products)
            for page in primary.pages
        ),
        products_unique=len(global_primary),
        duplicate_occurrences=(
            sum(result.duplicate_occurrences for result in primary_results.values())
            + cross_partition_duplicates
        ),
        repeated_page_sets=sum(
            result.repeated_page_sets for result in primary_results.values()
        ),
        unexpected_overlaps=sum(
            result.unexpected_overlaps for result in primary_results.values()
        ),
        missing_coverage_events=len(reasons),
        total_changes=sum(result.total_changes for result in primary_results.values()),
        uncategorized_products=0,
        request_limit=request_limit,
        coverage_demonstrated=accepted,
        coverage_reason="coverage_demonstrated" if accepted else ";".join(reasons),
        accepted=accepted,
        run_id=structure.run_id,
        tree_digest=structure.tree_digest,
        primary_plan_digest=primary.plan_digest,
        reconciliation_plan_digest=(
            reconciliation.plan_digest if reconciliation is not None else ""
        ),
        _reasons=tuple(reasons),
        _product_keys=tuple(sorted(global_primary)),
        _sku_keys=tuple(sorted(global_primary_skus)),
        _product_sku_pairs=tuple(sorted(global_primary_pairs)),
    )


@dataclass(frozen=True, slots=True)
class RecoveryCoverageResult:
    """Resultado fail-closed de recuperar una omisión con ventanas solapadas."""

    partition: str
    status: str
    products_reported: int
    products_unique: int
    products_recovered: int
    residual: int
    reasons: tuple[str, ...]
    deterministic_union: tuple[str, ...] = field(repr=False)

    @property
    def accepted(self) -> bool:
        # Compatibilidad de métricas: sólo el evaluador canónico concede accepted.
        return False


def evaluate_overlap_recovery(
    partition: PartitionSpec,
    primary_pages: Sequence[CoveragePageObservation],
    recovery_pages: Sequence[CoveragePageObservation],
    *,
    reconciliation_pages: Sequence[CoveragePageObservation] | None,
) -> RecoveryCoverageResult:
    """Acepta recuperación solo cuando otra travesía confirma la unión exacta."""

    primary = tuple(primary_pages)
    recovery = tuple(recovery_pages)
    values = primary + recovery
    reasons: list[str] = []
    if not primary:
        reasons.append("primary_traversal_missing")
    if not recovery:
        reasons.append("recovery_windows_missing")
    if any(item.partition != partition.name for item in values):
        reasons.append("partition_mismatch")
    totals = {item.records_filtered for item in values}
    if totals != {partition.expected_products}:
        reasons.append("total_changed_or_differs_from_discovery")
    if any(not item.complete for item in recovery):
        reasons.append("recovery_response_truncated")
    if any("duplicate_within_page" in item.quality_events for item in values):
        reasons.append("duplicate_within_page")

    positions: set[int] = set()
    for item in primary:
        positions.update(
            range(
                item.from_index,
                min(item.to_index, partition.expected_products - 1) + 1,
            )
        )
    if positions != set(range(partition.expected_products)):
        reasons.append("logical_positions_not_fully_planned")

    primary_keys = {key for item in primary for key in item._product_keys}
    union = {key for item in values for key in item._product_keys}
    recovered = union - primary_keys
    residual = max(partition.expected_products - len(union), 0)
    if len(union) != partition.expected_products:
        reasons.append("residual_unknown")
    if any(not item.complete for item in primary) and not recovered:
        reasons.append("partial_page_not_recovered")

    if reconciliation_pages is None:
        reasons.append("reconciliation_missing")
    else:
        reconciliation = tuple(reconciliation_pages)
        if any(item.partition != partition.name for item in reconciliation):
            reasons.append("reconciliation_partition_mismatch")
        else:
            traversal = _evaluate_traversal(partition, reconciliation)
            if not traversal.coverage_demonstrated:
                reasons.append("reconciliation_incomplete")
            elif traversal.product_keys != union:
                reasons.append("reordering_not_reconciled")
        if not reconciliation:
            reasons.append("reordering_not_reconciled")

    status = "LEGACY_EVIDENCE_CONSISTENT" if not reasons else "INCOMPLETE"
    return RecoveryCoverageResult(
        partition=partition.name,
        status=status,
        products_reported=partition.expected_products,
        products_unique=len(union),
        products_recovered=len(recovered),
        residual=residual,
        reasons=tuple(dict.fromkeys(reasons)),
        deterministic_union=tuple(sorted(union)),
    )


def observe_coverage_page(
    *,
    partition: str,
    order_by: str,
    from_index: int,
    to_index: int,
    records_filtered: int,
    product_keys: Sequence[str],
) -> CoveragePageObservation:
    """Crea una observación offline a partir de identidades privadas."""

    if not partition.strip():
        raise ValueError("partition no puede estar vacía")
    if order_by not in ALLOWED_ORDER_BY:
        raise ValueError(f"order_by no permitido: {order_by}")
    if from_index < 0 or to_index < from_index:
        raise ValueError("Rango de página inválido")
    width = to_index - from_index + 1
    if width > MAX_CATALOG_PAGE_SIZE:
        raise ValueError(
            f"La página no puede superar {MAX_CATALOG_PAGE_SIZE} posiciones"
        )
    if records_filtered < 0:
        raise ValueError("records_filtered no puede ser negativo")

    keys = tuple(str(key).strip() for key in product_keys)
    if any(not key for key in keys):
        raise ValueError("Las identidades privadas no pueden estar vacías")
    expected = min(width, max(records_filtered - from_index, 0))
    events: list[str] = []
    if len(keys) < expected:
        events.append("partial_page")
    elif len(keys) > expected:
        events.append("more_products_than_expected")
    if len(set(keys)) != len(keys):
        events.append("duplicate_within_page")

    return CoveragePageObservation(
        partition=partition,
        order_by=order_by,
        from_index=from_index,
        to_index=to_index,
        records_filtered=records_filtered,
        products_received=len(keys),
        products_expected=expected,
        complete=len(keys) == expected,
        sequence_signature=_signature(keys),
        set_signature=_signature(sorted(set(keys))),
        quality_events=tuple(events),
        _product_keys=keys,
    )


def evaluate_partition_coverage(
    partition: PartitionSpec,
    observations: Sequence[CoveragePageObservation],
    *,
    primary_order_by: str = "OrderByNameASC",
) -> PartitionCoverageResult:
    """Evalúa una partición y reconcilia órdenes adicionales si existen."""

    values = tuple(observations)
    if not values:
        return PartitionCoverageResult(
            partition=partition.name,
            pages_expected=0,
            pages_attempted=0,
            pages_completed=0,
            products_reported=partition.expected_products,
            products_received=0,
            products_unique=0,
            duplicate_occurrences=0,
            repeated_page_sets=0,
            unexpected_overlaps=0,
            missing_coverage_events=1,
            total_changes=0,
            orderings_attempted=0,
            orderings_reconciled=False,
            coverage_demonstrated=False,
            coverage_reason="partition_not_attempted",
            _reasons=("partition_not_attempted",),
        )
    if primary_order_by not in ALLOWED_ORDER_BY:
        raise ValueError("primary_order_by no permitido")
    if any(item.partition != partition.name for item in values):
        raise ValueError("Todas las observaciones deben pertenecer a la partición")

    grouped: dict[str, list[CoveragePageObservation]] = {}
    for item in values:
        grouped.setdefault(item.order_by, []).append(item)
    if primary_order_by not in grouped:
        raise ValueError("Falta el order_by primario")

    traversals = {
        order_by: _evaluate_traversal(partition, pages)
        for order_by, pages in grouped.items()
    }
    primary = traversals[primary_order_by]
    reasons: list[str] = []
    for traversal in traversals.values():
        _extend_unique(reasons, traversal.reasons)

    reconciled = True
    for order_by, traversal in traversals.items():
        if order_by == primary_order_by:
            continue
        if not traversal.coverage_demonstrated:
            reconciled = False
            _append_unique(reasons, "order_reconciliation_incomplete")
        elif traversal.product_keys != primary.product_keys:
            reconciled = False
            _append_unique(reasons, "order_reconciliation_failed")

    coverage_demonstrated = primary.coverage_demonstrated and reconciled
    reason = "coverage_demonstrated" if coverage_demonstrated else ";".join(reasons)

    return PartitionCoverageResult(
        partition=partition.name,
        pages_expected=sum(item.pages_expected for item in traversals.values()),
        pages_attempted=sum(item.pages_attempted for item in traversals.values()),
        pages_completed=sum(item.pages_completed for item in traversals.values()),
        products_reported=primary.products_reported,
        products_received=sum(item.products_received for item in traversals.values()),
        products_unique=len(primary.product_keys),
        duplicate_occurrences=sum(
            item.duplicate_occurrences for item in traversals.values()
        ),
        repeated_page_sets=sum(item.repeated_page_sets for item in traversals.values()),
        unexpected_overlaps=sum(
            item.unexpected_overlaps for item in traversals.values()
        ),
        missing_coverage_events=len(reasons),
        total_changes=sum(item.total_changes for item in traversals.values()),
        orderings_attempted=len(traversals),
        orderings_reconciled=reconciled,
        coverage_demonstrated=coverage_demonstrated,
        coverage_reason=reason or "coverage_not_demonstrated",
        _product_keys=tuple(sorted(primary.product_keys)),
        _reasons=tuple(reasons),
    )


def evaluate_catalog_coverage(
    partitions: Sequence[PartitionCoverageResult],
    *,
    partitions_discovered: int,
    products_reported: int,
    uncategorized_products: int = 0,
    request_limit: int = DEFAULT_MAX_COVERAGE_REQUESTS,
) -> CatalogCoverageReport:
    """Evalúa la unión global sin asumir que deduplicar demuestra completitud."""

    if partitions_discovered < 0 or products_reported < 0:
        raise ValueError("Los totales globales no pueden ser negativos")
    if uncategorized_products < 0:
        raise ValueError("uncategorized_products no puede ser negativo")
    if request_limit <= 0:
        raise ValueError("request_limit debe ser mayor que cero")

    values = tuple(partitions)
    reasons: list[str] = []
    if partitions_discovered != len(values):
        _append_unique(reasons, "partition_count_mismatch")
    if any(not item.coverage_demonstrated for item in values):
        _append_unique(reasons, "partitions_incomplete")
    if uncategorized_products:
        _append_unique(reasons, "uncategorized_products_present")

    canonical_occurrences = sum(len(item._product_keys) for item in values)
    unique_keys = {key for item in values for key in item._product_keys}
    cross_partition_duplicates = canonical_occurrences - len(unique_keys)
    if len(unique_keys) < products_reported:
        _append_unique(reasons, "global_union_below_reported_total")
    elif len(unique_keys) > products_reported:
        _append_unique(reasons, "global_union_above_reported_total")

    pages_attempted = sum(item.pages_attempted for item in values)
    if pages_attempted > request_limit:
        _append_unique(reasons, "request_limit_exceeded")

    inherited_reasons = (
        reason
        for item in values
        for reason in item._reasons
        if reason != "coverage_demonstrated"
    )
    _extend_unique(reasons, inherited_reasons)

    _append_unique(reasons, "legacy_evidence_non_authoritative")
    coverage_demonstrated = False
    return CatalogCoverageReport(
        partitions_discovered=partitions_discovered,
        partitions_attempted=len(values),
        partitions_completed=sum(item.coverage_demonstrated for item in values),
        pages_expected=sum(item.pages_expected for item in values),
        pages_attempted=pages_attempted,
        pages_completed=sum(item.pages_completed for item in values),
        products_reported=products_reported,
        products_received=sum(item.products_received for item in values),
        products_unique=len(unique_keys),
        duplicate_occurrences=(
            sum(item.duplicate_occurrences for item in values)
            + cross_partition_duplicates
        ),
        repeated_page_sets=sum(item.repeated_page_sets for item in values),
        unexpected_overlaps=sum(item.unexpected_overlaps for item in values),
        missing_coverage_events=len(reasons),
        total_changes=sum(item.total_changes for item in values),
        uncategorized_products=uncategorized_products,
        request_limit=request_limit,
        coverage_demonstrated=coverage_demonstrated,
        coverage_reason=(
            "coverage_demonstrated" if coverage_demonstrated else ";".join(reasons)
        ),
        accepted=coverage_demonstrated,
        run_id="legacy-non-authoritative",
        tree_digest="",
        primary_plan_digest="",
        reconciliation_plan_digest="",
        _reasons=tuple(reasons),
    )


def validate_sanitized_coverage_summary(value: Any) -> None:
    """Impide publicar identificadores o datos comerciales."""

    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key) in _FORBIDDEN_PUBLIC_KEYS:
                raise ValueError(f"Campo no permitido en resumen sanitizado: {key}")
            validate_sanitized_coverage_summary(nested)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for nested in value:
            validate_sanitized_coverage_summary(nested)


@dataclass(frozen=True, slots=True)
class _TraversalResult:
    pages_expected: int
    pages_attempted: int
    pages_completed: int
    products_reported: int
    products_received: int
    duplicate_occurrences: int
    repeated_page_sets: int
    unexpected_overlaps: int
    total_changes: int
    coverage_demonstrated: bool
    reasons: tuple[str, ...]
    product_keys: frozenset[str]


def _evaluate_traversal(
    partition: PartitionSpec,
    pages: Sequence[CoveragePageObservation],
) -> _TraversalResult:
    ordered = tuple(sorted(pages, key=lambda item: (item.from_index, item.to_index)))
    reasons: list[str] = []
    totals = [item.records_filtered for item in ordered]
    total_changes = sum(left != right for left, right in zip(totals, totals[1:]))
    if total_changes:
        _append_unique(reasons, "partition_total_changed")
    total = totals[0]
    if total != partition.expected_products:
        _append_unique(reasons, "partition_total_differs_from_discovery")

    page_width = max(item.width for item in ordered)
    pages_expected = math.ceil(total / page_width) if total else 0
    pages_completed = 0
    all_keys: list[str] = []
    positions: set[int] = set()
    for item in ordered:
        all_keys.extend(item._product_keys)
        if total:
            positions.update(
                range(
                    max(item.from_index, 0),
                    min(item.to_index, total - 1) + 1,
                )
            )
        if item.complete and not item.quality_events:
            pages_completed += 1
        _extend_unique(reasons, item.quality_events)

    repeated_page_sets = 0
    unexpected_overlaps = 0
    for index, left in enumerate(ordered):
        left_keys = set(left._product_keys)
        for right in ordered[index + 1 :]:
            if (
                left.set_signature == right.set_signature
                and (left.from_index, left.to_index)
                != (right.from_index, right.to_index)
            ):
                repeated_page_sets += 1
            expected_overlap = _expected_overlap(left, right)
            observed_overlap = len(left_keys.intersection(right._product_keys))
            if expected_overlap != observed_overlap:
                unexpected_overlaps += 1

    if repeated_page_sets:
        _append_unique(reasons, "repeated_page_set")
    if unexpected_overlaps:
        _append_unique(reasons, "unexpected_overlap")
    if len(positions) != total:
        _append_unique(reasons, "logical_positions_not_fully_covered")
    if pages_completed < pages_expected:
        _append_unique(reasons, "pages_incomplete")

    unique_keys = frozenset(all_keys)
    if len(unique_keys) < total:
        _append_unique(reasons, "unique_products_below_partition_total")
    elif len(unique_keys) > total:
        _append_unique(reasons, "unique_products_above_partition_total")

    duplicate_occurrences = len(all_keys) - len(unique_keys)
    coverage_demonstrated = not reasons
    return _TraversalResult(
        pages_expected=pages_expected,
        pages_attempted=len(ordered),
        pages_completed=pages_completed,
        products_reported=total,
        products_received=len(all_keys),
        duplicate_occurrences=duplicate_occurrences,
        repeated_page_sets=repeated_page_sets,
        unexpected_overlaps=unexpected_overlaps,
        total_changes=total_changes,
        coverage_demonstrated=coverage_demonstrated,
        reasons=tuple(reasons),
        product_keys=unique_keys,
    )


def _evaluate_raw_traversal(
    structure: StructuralDiscoveryReport,
    traversal: TraversalEvidence,
) -> dict[str, PartitionCoverageResult]:
    grouped: dict[str, list[RawPageEvidence]] = {}
    for page in traversal.pages:
        grouped.setdefault(page.partition, []).append(page)

    results: dict[str, PartitionCoverageResult] = {}
    for leaf in structure.valid_leaves:
        pages = tuple(
            sorted(
                grouped.get(leaf.name, ()),
                key=lambda page: (page.from_index, page.to_index),
            )
        )
        reasons: list[str] = []
        if leaf.expected_products == 0:
            if pages:
                _append_unique(reasons, "empty_leaf_was_requested")
            results[leaf.name] = PartitionCoverageResult(
                partition=leaf.name,
                pages_expected=0,
                pages_attempted=len(pages),
                pages_completed=0,
                products_reported=0,
                products_received=sum(len(page.products) for page in pages),
                products_unique=0,
                duplicate_occurrences=0,
                repeated_page_sets=0,
                unexpected_overlaps=0,
                missing_coverage_events=len(reasons),
                total_changes=0,
                orderings_attempted=1,
                orderings_reconciled=False,
                coverage_demonstrated=not reasons,
                coverage_reason="coverage_demonstrated" if not reasons else ";".join(reasons),
                _product_keys=(),
                _reasons=tuple(reasons),
            )
            continue
        if not pages:
            _append_unique(reasons, "partition_not_attempted")

        positions: dict[int, str] = {}
        primary_planned_positions: set[int] = set()
        occurrences: list[str] = []
        sku_occurrences: list[str] = []
        product_sku_pairs: list[tuple[str, str]] = []
        sku_owners: dict[str, str] = {}
        completed_pages = 0
        total_changes = 0
        totals: list[int] = []
        page_sets: set[frozenset[str]] = set()
        repeated_page_sets = 0
        overlap_conflicts = 0
        for page in pages:
            totals.append(page.records_filtered)
            if page.records_filtered != leaf.expected_products:
                _append_unique(reasons, "partition_total_differs_from_discovery")
            if page.from_index < 0 or page.to_index < page.from_index:
                _append_unique(reasons, "invalid_page_range")
                continue
            if page.to_index - page.from_index + 1 > MAX_CATALOG_PAGE_SIZE:
                _append_unique(reasons, "page_range_above_limit")
            expected = min(
                page.to_index - page.from_index + 1,
                max(leaf.expected_products - page.from_index, 0),
            )
            if page.purpose == "PRIMARY":
                primary_planned_positions.update(
                    range(page.from_index, min(page.to_index, leaf.expected_products - 1) + 1)
                )
            if len(page.products) == expected:
                completed_pages += 1
            elif page.purpose == "RECOVERY":
                _append_unique(reasons, "recovery_response_truncated")
            keys_on_page: list[str] = []
            for offset, product in enumerate(page.products):
                identity = product.identity
                if identity is None:
                    _append_unique(reasons, "uninterpretable_product_identity")
                    continue
                if not _membership_matches(product, leaf._category_path):
                    _append_unique(reasons, "partition_membership_invalid")
                position = page.from_index + offset
                if position > page.to_index or position >= leaf.expected_products:
                    _append_unique(reasons, "product_outside_requested_range")
                    continue
                previous = positions.get(position)
                if previous is not None and previous != identity:
                    overlap_conflicts += 1
                    _append_unique(reasons, "overlap_position_conflict")
                else:
                    positions[position] = identity
                occurrences.append(identity)
                keys_on_page.append(identity)
                sku_keys = product.sku_identities
                if not sku_keys:
                    _append_unique(reasons, "uninterpretable_sku_identity")
                if len(set(sku_keys)) != len(sku_keys):
                    _append_unique(reasons, "duplicate_sku_identity")
                for sku_key in sku_keys:
                    owner = sku_owners.get(sku_key)
                    if owner is not None and owner != identity:
                        _append_unique(reasons, "duplicate_sku_identity")
                    else:
                        sku_owners[sku_key] = identity
                    product_sku_pairs.append((identity, sku_key))
                sku_occurrences.extend(sku_keys)
            page_set = frozenset(keys_on_page)
            if page_set in page_sets and page_set:
                repeated_page_sets += 1
                _append_unique(reasons, "repeated_page_set")
            page_sets.add(page_set)

        total_changes = sum(left != right for left, right in zip(totals, totals[1:]))
        if total_changes:
            _append_unique(reasons, "partition_total_changed")
        if set(positions) != set(range(leaf.expected_products)):
            _append_unique(reasons, "logical_positions_not_fully_covered")
        if primary_planned_positions != set(range(leaf.expected_products)):
            _append_unique(reasons, "primary_plan_does_not_cover_leaf")
        unique = frozenset(occurrences)
        if len(unique) != leaf.expected_products:
            _append_unique(reasons, "unique_products_differ_from_partition_total")
        position_identities = list(positions.values())
        if len(set(position_identities)) != len(position_identities):
            _append_unique(reasons, "duplicate_compensates_omission")
        unique_skus = frozenset(sku_occurrences)

        duplicate_occurrences = len(occurrences) - len(unique)
        page_width = max(
            (page.to_index - page.from_index + 1 for page in pages),
            default=MAX_CATALOG_PAGE_SIZE,
        )
        expected_pages = math.ceil(leaf.expected_products / page_width)
        accepted = not reasons
        results[leaf.name] = PartitionCoverageResult(
            partition=leaf.name,
            pages_expected=expected_pages,
            pages_attempted=len(pages),
            pages_completed=completed_pages,
            products_reported=leaf.expected_products,
            products_received=len(occurrences),
            products_unique=len(unique),
            duplicate_occurrences=duplicate_occurrences,
            repeated_page_sets=repeated_page_sets,
            unexpected_overlaps=overlap_conflicts,
            missing_coverage_events=len(reasons),
            total_changes=total_changes,
            orderings_attempted=1,
            orderings_reconciled=False,
            coverage_demonstrated=accepted,
            coverage_reason="coverage_demonstrated" if accepted else ";".join(reasons),
            _product_keys=tuple(sorted(unique)),
            _sku_keys=tuple(sorted(unique_skus)),
            _product_sku_pairs=tuple(sorted(set(product_sku_pairs))),
            _reasons=tuple(reasons),
        )
    return results


def _expected_overlap(
    left: CoveragePageObservation,
    right: CoveragePageObservation,
) -> int:
    start = max(left.from_index, right.from_index)
    end = min(left.to_index, right.to_index)
    return max(end - start + 1, 0)


def _signature(values: Sequence[str]) -> str:
    encoded = json.dumps(list(values), separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _first_nonempty(*values: Any) -> str | None:
    for value in values:
        if (text := _optional_text(value)) is not None:
            return text
    return None


def _membership_matches(
    product: RawProductEvidence,
    expected_path: tuple[tuple[str, str], ...],
) -> bool:
    matches = 0
    for path in product.category_paths:
        if len(path) != len(expected_path):
            continue
        if all(
            observed_key == expected_key and expected_value in candidates
            for (observed_key, candidates), (expected_key, expected_value) in zip(
                path, expected_path
            )
        ):
            matches += 1
    return matches == 1


def _closed_nonnegative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("El total debe ser un entero no negativo")
    return value


def _traversal_plan_digest(
    *,
    run_id: str,
    traversal_id: str,
    tree_digest: str,
    order_by: str,
    pages: Sequence[RawPageEvidence],
) -> str:
    plan = {
        "run_id": run_id,
        "traversal_id": traversal_id,
        "tree_digest": tree_digest,
        "order_by": order_by,
        "pages": [
            {
                "ordinal": index,
                "partition": page.partition,
                "from": page.from_index,
                "to": page.to_index,
                "purpose": page.purpose,
                "response_digest": page.response_digest,
            }
            for index, page in enumerate(pages)
        ],
    }
    return hashlib.sha256(
        json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _mapping_digest(value: Mapping[str, Any]) -> str:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("La respuesta no es JSON canónico verificable") from exc
    return hashlib.sha256(encoded).hexdigest()


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _append_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


def _extend_unique(values: list[str], additions: Any) -> None:
    for value in additions:
        _append_unique(values, str(value))
