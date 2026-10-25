#!/usr/bin/env python3
"""Ejecuta recorridos live manuales, progresivos y sin persistencia comercial."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from precios_supermercados.scrapers.la_colonia_catalog_coverage import (  # noqa: E402
    CanonicalCatalogEvidence,
    build_traversal_evidence,
    raw_page_evidence_from_response,
)
from precios_supermercados.scrapers.la_colonia_catalog_partitions import (  # noqa: E402
    build_structural_discovery_report,
)
from precios_supermercados.scrapers.la_colonia_runner import (  # noqa: E402
    AcceptanceProfile,
    AcceptanceThresholds,
    CatalogRunResult,
    CrawlConfig,
    LaColoniaCatalogRunner,
)

SAFE_PAGE_SIZES = (10, 20, 30, 50)


def _optional_positive(value: str) -> int | None:
    parsed = int(value)
    return parsed if parsed > 0 else None


def _thresholds(args: argparse.Namespace) -> AcceptanceThresholds:
    return AcceptanceThresholds(
        max_missing_price_ratio=args.max_missing_price_ratio,
        max_duplicate_sku_ratio=args.max_duplicate_sku_ratio,
        max_duplicate_product_ratio=args.max_duplicate_product_ratio,
        max_total_change_ratio=args.max_total_change_ratio,
    )


def _resolve_limits(args: argparse.Namespace) -> tuple[int | None, int | None]:
    if args.mode == "smoke":
        return args.max_pages or 2, None
    if args.mode == "staged":
        if args.max_pages is None and args.max_products is None:
            return 10, None
        return args.max_pages, args.max_products
    return None, None


def _markdown(summary: dict[str, Any]) -> str:
    metrics = summary["metrics"]
    rows = [
        ("run_id", metrics["run_id"]),
        ("accepted", metrics["accepted"]),
        ("page_size", metrics["page_size"]),
        ("order_by", metrics["order_by"]),
        ("products_reported_initial", metrics["products_reported_initial"]),
        ("products_reported_final", metrics["products_reported_final"]),
        ("pages_expected", metrics["pages_expected"]),
        ("pages_attempted", metrics["pages_attempted"]),
        ("pages_completed", metrics["pages_completed"]),
        ("page_coverage", metrics["page_coverage"]),
        ("products_returned", metrics["products_returned"]),
        ("products_processed", metrics["products_processed"]),
        ("skus_returned", metrics["skus_returned"]),
        ("skus_extracted", metrics["skus_extracted"]),
        ("skus_with_price", metrics["skus_with_price"]),
        ("skus_without_price", metrics["skus_without_price"]),
        ("promotional_skus", metrics["promotional_skus"]),
        ("weighted_skus", metrics["weighted_skus"]),
        ("duplicate_skus", metrics["duplicate_skus"]),
        ("duplicate_products", metrics["duplicate_products"]),
        ("errors", metrics["errors"]),
        ("structural_events", metrics["structural_events"]),
        ("http_403", metrics["http_403"]),
        ("http_429", metrics["http_429"]),
        ("http_5xx", metrics["http_5xx"]),
        ("retries", metrics["retries"]),
        ("duration_seconds", metrics["duration_seconds"]),
        ("average_response_seconds", metrics["average_response_seconds"]),
        ("average_response_bytes", metrics["average_response_bytes"]),
        ("delay_seconds_applied", metrics["delay_seconds_applied"]),
    ]
    lines = [
        "# La Colonia — resumen de recorrido",
        "",
        f"Modo: `{summary['mode']}`",
        f"Perfil: `{summary['profile']}`",
        "",
        "| Métrica | Valor |",
        "|---|---:|",
    ]
    lines.extend(f"| `{name}` | `{value}` |" for name, value in rows)
    lines.extend(
        [
            "",
            "## Rechazos",
            "",
            *(
                [f"- {reason}" for reason in metrics["rejection_reasons"]]
                or ["- Ninguno"]
            ),
            "",
            "## Advertencias",
            "",
            *([f"- {warning}" for warning in metrics["warnings"]] or ["- Ninguna"]),
            "",
            "## Umbrales propuestos",
            "",
            "```json",
            json.dumps(metrics["proposed_thresholds"], ensure_ascii=False, indent=2),
            "```",
            "",
            "El informe no contiene el catálogo completo ni datos comerciales versionados.",
        ]
    )
    return "\n".join(lines) + "\n"


def _write_summary(
    result: CatalogRunResult,
    *,
    output_dir: Path,
    mode: str,
    profile: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = result.sanitized_summary()
    summary["mode"] = mode
    summary["profile"] = profile
    if extra:
        summary.update(extra)
    (output_dir / "run-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "run-summary.md").write_text(
        _markdown(summary),
        encoding="utf-8",
    )
    return summary


def _run_once(
    *,
    args: argparse.Namespace,
    max_pages: int | None,
    max_products: int | None,
    suffix: str = "",
    canonical_evidence: CanonicalCatalogEvidence | None = None,
) -> CatalogRunResult:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_id = (
        canonical_evidence.run_id
        if canonical_evidence is not None
        else f"live_la_colonia_{args.mode}_{timestamp}{suffix}"
    )
    config = CrawlConfig(
        page_size=args.page_size,
        max_pages=max_pages,
        max_products=max_products,
        delay_seconds=args.delay_seconds,
        max_retries=args.max_retries,
        stop_on_error=True,
        order_by=args.order_by,
        max_duration_seconds=args.max_duration_seconds,
    )
    runner = LaColoniaCatalogRunner()
    return runner.run(
        config,
        run_id=run_id,
        profile=AcceptanceProfile(args.profile),
        thresholds=_thresholds(args),
        canonical_evidence=canonical_evidence,
    )


def _load_canonical_evidence(path: Path) -> CanonicalCatalogEvidence:
    """Carga evidencia cruda offline; recomputa estructura y digests localmente."""

    value = json.loads(path.read_text(encoding="utf-8"))
    required = {"schema_version", "run_id", "root_response", "facets_response", "primary", "reconciliation"}
    if not isinstance(value, dict) or set(value) != required or value["schema_version"] != "1":
        raise ValueError("Evidencia canónica con schema inválido")
    run_id = str(value["run_id"])
    root = value["root_response"]
    facets_response = value["facets_response"]
    if not isinstance(root, dict) or not isinstance(facets_response, dict):
        raise ValueError("Respuestas estructurales inválidas")
    root_total = root.get("recordsFiltered")
    facets = facets_response.get("facets")
    if isinstance(root_total, bool) or not isinstance(root_total, int):
        raise ValueError("Root total inválido")
    if not isinstance(facets, list):
        raise ValueError("Facets inválidas")
    structure = build_structural_discovery_report(
        facets,
        run_id=run_id,
        root_total=root_total,
        sampling=facets_response.get("sampling", True),
    )

    def traversal(raw: Any):
        if not isinstance(raw, dict) or set(raw) != {"traversal_id", "order_by", "pages"}:
            raise ValueError("Traversal inválida")
        traversal_id = str(raw["traversal_id"])
        order_by = str(raw["order_by"])
        pages = []
        if not isinstance(raw["pages"], list):
            raise ValueError("Páginas inválidas")
        for page in raw["pages"]:
            keys = {"partition", "from_index", "to_index", "response", "purpose"}
            if not isinstance(page, dict) or set(page) != keys:
                raise ValueError("Página canónica inválida")
            pages.append(raw_page_evidence_from_response(
                run_id=run_id,
                traversal_id=traversal_id,
                partition=str(page["partition"]),
                order_by=order_by,
                from_index=page["from_index"],
                to_index=page["to_index"],
                response=page["response"],
                purpose=str(page["purpose"]),
            ))
        return build_traversal_evidence(
            run_id=run_id,
            traversal_id=traversal_id,
            tree_digest=structure.tree_digest,
            order_by=order_by,
            pages=tuple(pages),
        )

    return CanonicalCatalogEvidence(
        run_id=run_id,
        root_response=root,
        facets_response=facets_response,
        primary=traversal(value["primary"]),
        reconciliation=traversal(value["reconciliation"]),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("smoke", "staged", "full"), default="smoke")
    parser.add_argument("--page-size", type=int, choices=SAFE_PAGE_SIZES, default=10)
    parser.add_argument("--max-pages", type=_optional_positive)
    parser.add_argument("--max-products", type=_optional_positive)
    parser.add_argument("--delay-seconds", type=float, default=1.5)
    parser.add_argument("--max-retries", type=int, choices=(0,), default=0)
    parser.add_argument(
        "--order-by",
        choices=("OrderByNameASC", "OrderByReleaseDateDESC"),
        default="OrderByNameASC",
    )
    parser.add_argument(
        "--profile",
        choices=("baseline", "validation"),
        default="baseline",
    )
    parser.add_argument("--max-missing-price-ratio", type=float)
    parser.add_argument("--max-duplicate-sku-ratio", type=float)
    parser.add_argument("--max-duplicate-product-ratio", type=float)
    parser.add_argument("--max-total-change-ratio", type=float)
    parser.add_argument("--max-duration-seconds", type=float, default=1_800.0)
    parser.add_argument("--max-full-pages", type=int, default=300)
    parser.add_argument("--allow-full", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "run-artifacts")
    parser.add_argument("--canonical-evidence", type=Path)
    args = parser.parse_args(argv)
    canonical_evidence = (
        _load_canonical_evidence(args.canonical_evidence)
        if args.canonical_evidence is not None
        else None
    )

    if args.delay_seconds < 0:
        parser.error("--delay-seconds no puede ser negativo")
    if args.mode == "full":
        if not args.allow_full:
            parser.error("full requiere --allow-full")
        if args.profile != "validation" or not _thresholds(args).complete:
            parser.error("full requiere perfil validation y los cuatro umbrales")
        preflight = _run_once(args=args, max_pages=1, max_products=None, suffix="_preflight")
        catalog_pages = preflight.metrics.catalog_pages_reported
        # El preflight de una sola página nunca puede declarar catálogo completo.
        # Sólo habilita la colección final cuando esa muestra técnica terminó sin
        # errores/bloqueos; la aceptación final sigue exigiendo evidencia canónica.
        preflight_ready = (
            preflight.metrics.pages_attempted == 1
            and preflight.metrics.pages_completed == 1
            and preflight.metrics.errors == 0
            and preflight.metrics.structural_events == 0
            and preflight.metrics.http_403 == 0
            and preflight.metrics.persistent_http_429 == 0
            and catalog_pages > 0
        )
        if not preflight_ready or catalog_pages > args.max_full_pages:
            summary = _write_summary(
                preflight,
                output_dir=args.output_dir,
                mode=args.mode,
                profile=args.profile,
                extra={
                    "full_started": False,
                    "full_stop_reason": (
                        "preflight_rejected"
                        if not preflight_ready
                        else "catalog_request_count_exceeds_safety_limit"
                    ),
                    "max_full_pages": args.max_full_pages,
                    "recommendation": (
                        "Evaluar recorrido por categorías o sesiones separadas; "
                        "no se forzó el catálogo completo."
                    ),
                },
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 2
        if args.delay_seconds:
            import time

            time.sleep(args.delay_seconds)
        result = _run_once(
            args=args, max_pages=None, max_products=None, suffix="_full",
            canonical_evidence=canonical_evidence,
        )
    else:
        max_pages, max_products = _resolve_limits(args)
        result = _run_once(
            args=args,
            max_pages=max_pages,
            max_products=max_products,
            canonical_evidence=canonical_evidence,
        )

    summary = _write_summary(
        result,
        output_dir=args.output_dir,
        mode=args.mode,
        profile=args.profile,
        extra={"full_started": args.mode == "full"},
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if result.metrics.accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
