#!/usr/bin/env python3
"""Recorre el catálogo SPS por marca usando el contrato VTEX validado.

Cada producto VTEX tiene una sola marca. La dimensión marca ofrece particiones
disjuntas para evitar la ventana máxima de búsqueda. El límite de particiones
queda alineado con el presupuesto global de peticiones; el control real de carga
sigue siendo el cálculo de páginas antes de iniciar la descarga comercial.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import obtener_catalogo_sps_la_colonia_particionado_v2 as frontier  # noqa: E402

BRAND_PARTITION_LIMIT = frontier.base.MAX_PLANNED_PRODUCT_REQUESTS


def _brand_values(normalized: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    facets = normalized.get("facets")
    if not isinstance(facets, Sequence) or isinstance(facets, (str, bytes)):
        raise ValueError("facets_not_sequence")

    candidates: list[list[Mapping[str, Any]]] = []
    for facet in facets:
        if not isinstance(facet, Mapping):
            continue
        values = facet.get("values")
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            continue
        matching = [
            item
            for item in values
            if isinstance(item, Mapping)
            and str(item.get("key") or "").strip().lower() == "brand"
            and isinstance(item.get("value"), str)
            and str(item.get("value")).strip()
            and isinstance(item.get("quantity"), int)
            and not isinstance(item.get("quantity"), bool)
            and int(item.get("quantity")) > 0
        ]
        facet_type = str(facet.get("type") or "").strip().upper()
        if matching and (facet_type == "BRAND" or len(matching) == len([
            item for item in values if isinstance(item, Mapping)
        ])):
            candidates.append(matching)

    if not candidates:
        raise ValueError("brand_values_missing")
    return max(candidates, key=lambda items: sum(int(item["quantity"]) for item in items))


def _build_brand_frontier(
    values: Sequence[Mapping[str, Any]],
    *,
    search_window: int = frontier.base.SEARCH_WINDOW_MAX_PRODUCTS,
    max_partitions: int = BRAND_PARTITION_LIMIT,
) -> tuple[frontier.FrontierPartition, ...]:
    if search_window <= 0 or max_partitions <= 0:
        raise ValueError("invalid_brand_limits")
    by_value: dict[str, int] = {}
    for item in values:
        key = str(item.get("key") or "").strip().lower()
        value = str(item.get("value") or "").strip()
        quantity = item.get("quantity")
        if key != "brand" or not value:
            raise ValueError("invalid_brand_identity")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise ValueError("invalid_brand_quantity")
        if quantity > search_window:
            raise ValueError("brand_partition_exceeds_search_window")
        previous = by_value.get(value)
        if previous is not None and previous != quantity:
            raise ValueError("duplicate_brand_quantity_conflict")
        by_value[value] = int(quantity)
    if not by_value:
        raise ValueError("brand_partitions_missing")
    if len(by_value) > max_partitions:
        raise ValueError("partition_limit_exceeded")
    return tuple(
        frontier.FrontierPartition(path=(("brand", value),), quantity=quantity)
        for value, quantity in by_value.items()
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-read-only", action="store_true")
    parser.add_argument("--allow-full-catalog", action="store_true")
    parser.add_argument("--page-size", type=int, choices=(50,), default=50)
    parser.add_argument("--delay-seconds", type=float, default=1.5)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "run-artifacts" / "full-catalog.json",
    )
    parser.add_argument(
        "--csv-output",
        type=Path,
        default=ROOT / "run-artifacts" / "full-catalog.csv",
    )
    args = parser.parse_args(argv)
    if not args.live_read_only:
        parser.error("el catálogo live requiere --live-read-only")
    if not args.allow_full_catalog:
        parser.error("el catálogo completo requiere --allow-full-catalog")
    if args.delay_seconds < 1.0:
        parser.error("--delay-seconds debe ser al menos 1.0")

    old_values = frontier._category_values
    old_builder = frontier._build_frontier
    frontier._category_values = _brand_values
    frontier._build_frontier = _build_brand_frontier
    try:
        artifact = frontier._run_partitioned_catalog(
            page_size=args.page_size,
            delay_seconds=args.delay_seconds,
        )
        artifact["capture_strategy"] = (
            "radiography_same_context_brand_partitions_productSearchV3"
        )
        artifact["partition_strategy"] = "brand"
        frontier.base.full.passive._validate_artifact_shape(artifact)
        frontier.base.full._write_json(artifact, args.output)
        frontier.base.full._write_csv(list(artifact["products"]), args.csv_output)
    except frontier.base.full.FullCatalogError as exc:
        failure = frontier.base.full._safe_failure(exc.reason, exc.diagnostic)
        failure["capture_strategy"] = (
            "radiography_same_context_brand_partitions_productSearchV3"
        )
        failure["partition_strategy"] = "brand"
        if "frontier_error" in exc.diagnostic:
            failure["frontier_error"] = str(exc.diagnostic["frontier_error"])
        for key in (
            "partitions_detected",
            "partitions_completed",
            "partition_quantity_sum",
            "planned_product_requests",
            "unique_products_extracted",
            "partition_expected_total",
            "partition_observed_total",
            "expected_products_on_page",
            "observed_products_on_page",
        ):
            if key in exc.diagnostic:
                failure[key] = int(exc.diagnostic[key])
        frontier.base.full._write_json(failure, args.output)
        print(f"sps_brand_catalog_stopped:{exc.reason}", file=sys.stderr)
        return 3
    finally:
        frontier._category_values = old_values
        frontier._build_frontier = old_builder

    print(json.dumps({
        "result": "success",
        "catalog_products_reported": artifact["catalog_products_reported"],
        "unique_products_extracted": artifact["unique_products_extracted"],
        "partitions_completed": artifact["partitions_completed"],
        "skus_extracted": artifact["skus_extracted"],
        "skus_with_price": artifact["skus_with_price"],
        "catalog_complete": artifact["catalog_complete"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
