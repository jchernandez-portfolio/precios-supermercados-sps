#!/usr/bin/env python3
"""Exporta el modelo analítico seguro a JSON/CSV para BI y portafolio.

El script no hace scraping. Lee únicamente el estado comercial ya persistido,
reconstruye la homologación con el motor del proyecto y publica sólo grupos que
``safe_comparator`` autoriza como comparables.

Puede operar sobre SQLite local o sobre Turso usando las credenciales existentes
en variables de entorno. Ninguna credencial ni URL de base de datos se escribe
en los artefactos de salida.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Protocol, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from actualizar_mvp_turso_la_colonia import _execute_rows, _pipeline, _stmt  # noqa: E402
from precios_supermercados.price_analytics import (  # noqa: E402
    ComparisonScope,
    CurrentPriceObservation,
    analyze_current_prices,
)
from precios_supermercados.product_homologation import (  # noqa: E402
    SourceProductRecord,
    homologate_products,
)
from precios_supermercados.publication_dataset import build_publication_dataset  # noqa: E402


class ExportError(ValueError):
    """El estado persistido no puede convertirse en una publicación segura."""


class QueryBackend(Protocol):
    kind: str

    def query(self, sql: str, args: Sequence[object] = ()) -> list[tuple[object, ...]]:
        ...


class SQLiteBackend:
    kind = "sqlite"

    def __init__(self, path: Path) -> None:
        self.path = path
        self.connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)

    def query(self, sql: str, args: Sequence[object] = ()) -> list[tuple[object, ...]]:
        return [tuple(row) for row in self.connection.execute(sql, tuple(args)).fetchall()]

    def close(self) -> None:
        self.connection.close()


class TursoBackend:
    kind = "turso"

    def __init__(self, url: str, token: str) -> None:
        if not url.strip() or not token.strip():
            raise ExportError("turso_credentials_missing")
        self.url = url
        self.token = token

    def query(self, sql: str, args: Sequence[object] = ()) -> list[tuple[object, ...]]:
        data = _pipeline(
            self.url,
            self.token,
            [{"type": "execute", "stmt": _stmt(sql, tuple(args))}, {"type": "close"}],
        )
        results = data.get("results")
        if not isinstance(results, list) or len(results) != 2:
            raise ExportError("turso_query_response_invalid")
        return [tuple(row) for row in _execute_rows(results[0])]

    def close(self) -> None:
        return None


def parse_scope(values: Iterable[str]) -> ComparisonScope:
    pairs: list[tuple[str, str]] = []
    for raw in values:
        supermarket, separator, location = raw.partition("=")
        if separator != "=" or not supermarket.strip() or not location.strip():
            raise ExportError("scope_must_be_supermarket_equals_location")
        pairs.append((supermarket.strip(), location.strip()))
    return ComparisonScope(tuple(pairs))


def _placeholders(count: int) -> str:
    if count <= 0:
        raise ExportError("empty_sql_scope")
    return ",".join("?" for _ in range(count))


def fetch_products(backend: QueryBackend, scope: ComparisonScope) -> tuple[SourceProductRecord, ...]:
    supermarkets = tuple(scope.supermarket_ids)
    placeholders = _placeholders(len(supermarkets))
    cursor = -1
    result: list[SourceProductRecord] = []
    seen: set[str] = set()
    while True:
        rows = backend.query(
            f"""
            SELECT product_id,supermarket_id,name,brand,presentation,category,ean
            FROM products
            WHERE supermarket_id IN ({placeholders}) AND product_id>?
            ORDER BY product_id
            LIMIT 2000
            """,
            (*supermarkets, cursor),
        )
        if not rows:
            break
        for product_id, supermarket_id, name, brand, presentation, category, ean in rows:
            if type(product_id) is not int or not isinstance(supermarket_id, str) or not isinstance(name, str):
                raise ExportError("product_row_invalid")
            source_record_id = f"{supermarket_id}:{product_id}"
            if source_record_id in seen:
                raise ExportError("product_source_identity_duplicate")
            seen.add(source_record_id)
            result.append(
                SourceProductRecord(
                    source_record_id=source_record_id,
                    supermarket_id=supermarket_id,
                    source_name=name,
                    source_brand=brand if isinstance(brand, str) else None,
                    source_presentation=presentation if isinstance(presentation, str) else None,
                    source_category=category if isinstance(category, str) else None,
                    barcode=ean if isinstance(ean, str) else None,
                )
            )
        cursor = int(rows[-1][0])
        if len(rows) < 2000:
            break
    return tuple(result)


def _scope_predicate(scope: ComparisonScope) -> tuple[str, tuple[object, ...]]:
    clauses: list[str] = []
    args: list[object] = []
    for supermarket, location in scope.locations:
        clauses.append("(h.supermarket_id=? AND h.location_id=?)")
        args.extend((supermarket, location))
    return " OR ".join(clauses), tuple(args)


def scope_contexts(scope: object) -> tuple[tuple[str, str], ...]:
    """Contextos ``(supermarket_id, location_id)`` únicos en el orden del scope."""
    return tuple(dict.fromkeys((str(s), str(l)) for s, l in getattr(scope, "locations")))


CURRENT_PAGE_SIZE = 2000


def fetch_current_rows_by_location(
    backend: QueryBackend,
    scope: object,
    *,
    select_sql: str,
    location_index: int,
    invalid_code: str,
    page_size: int,
) -> list[tuple[object, ...]]:
    """Lee filas current por contexto exacto y las devuelve en orden global.

    Cada contexto se pagina con ``location_id=? AND product_id>?`` sobre
    ``idx_price_history_current(location_id, product_id) WHERE valid_to_utc IS NULL``:
    cada fila current del scope se lee una sola vez. El keyset histórico
    ``product_id>? OR (product_id=? AND location_id>?)`` no es un rango indexable y
    hacía que cada página volviera a recorrer el índice desde el inicio.

    ``select_sql`` debe exponer ``h`` como alias de ``price_history`` y devolver
    ``h.product_id`` en la columna 0. El resultado se ordena por
    ``(product_id, location_id)`` para reproducir exactamente el orden anterior.
    """
    collected: list[tuple[object, ...]] = []
    for supermarket_id, location_id in scope_contexts(scope):
        cursor = -1
        while True:
            rows = backend.query(
                f"""
                {select_sql}
                WHERE h.valid_to_utc IS NULL
                  AND h.supermarket_id=? AND h.location_id=?
                  AND h.product_id>?
                ORDER BY h.product_id
                LIMIT {int(page_size)}
                """,
                (supermarket_id, location_id, cursor),
            )
            if not rows:
                break
            for row in rows:
                if type(row[0]) is not int or not isinstance(row[location_index], str):
                    raise ExportError(invalid_code)
            collected.extend(rows)
            cursor = int(rows[-1][0])
            if len(rows) < page_size:
                break
    collected.sort(key=lambda row: (row[0], row[location_index]))
    return collected


def fetch_current_observations(
    backend: QueryBackend,
    scope: ComparisonScope,
) -> tuple[CurrentPriceObservation, ...]:
    result: list[CurrentPriceObservation] = []
    seen: set[tuple[str, str]] = set()
    rows = fetch_current_rows_by_location(
        backend,
        scope,
        select_sql=(
            "SELECT h.product_id,h.supermarket_id,h.location_id,h.current_price_minor,h.availability\n"
            "                FROM price_history AS h"
        ),
        location_index=2,
        invalid_code="current_price_row_identity_invalid",
        page_size=CURRENT_PAGE_SIZE,
    )
    for product_id, supermarket_id, location_id, current_price_minor, availability in rows:
        if type(product_id) is not int or not isinstance(supermarket_id, str) or not isinstance(location_id, str):
            raise ExportError("current_price_row_identity_invalid")
        if current_price_minor is not None and type(current_price_minor) is not int:
            raise ExportError("current_price_minor_invalid")
        source_record_id = f"{supermarket_id}:{product_id}"
        key = (source_record_id, location_id)
        if key in seen:
            raise ExportError("current_price_row_duplicate")
        seen.add(key)
        result.append(
            CurrentPriceObservation(
                source_record_id=source_record_id,
                supermarket_id=supermarket_id,
                location_id=location_id,
                price_minor=current_price_minor,
                availability=availability if isinstance(availability, str) else None,
            )
        )
    return tuple(result)


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="")
    temporary.replace(path)


def _write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def export_dataset(
    backend: QueryBackend,
    scope: ComparisonScope,
    output_directory: Path,
    *,
    require_products: bool = False,
) -> dict[str, object]:
    records = fetch_products(backend, scope)
    observations = fetch_current_observations(backend, scope)
    homologation = homologate_products(records)
    analytics = analyze_current_prices(homologation, observations, scope)
    publication = build_publication_dataset(analytics)
    document = publication.as_json_object()

    if require_products and not publication.products:
        raise ExportError("publication_has_no_comparable_products")

    output_directory.mkdir(parents=True, exist_ok=True)
    _atomic_text(
        output_directory / "publication.json",
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )

    offer_rows = [asdict(row) for row in publication.offers]
    product_rows = [asdict(row) for row in publication.products]
    basket_rows = [asdict(row) for row in publication.common_basket]
    _write_csv(
        output_directory / "offers.csv",
        offer_rows,
        list(offer_rows[0]) if offer_rows else [
            "canonical_product_id", "canonical_gtin", "supermarket_id", "location_id",
            "source_record_id", "current_price", "is_best_price",
        ],
    )
    _write_csv(
        output_directory / "products.csv",
        product_rows,
        list(product_rows[0]) if product_rows else [
            "canonical_product_id", "canonical_gtin", "best_supermarket_id", "best_location_id",
            "best_price", "highest_price", "savings_vs_highest", "savings_vs_highest_pct",
            "supermarket_count",
        ],
    )
    _write_csv(
        output_directory / "common-basket.csv",
        basket_rows,
        list(basket_rows[0]) if basket_rows else [
            "supermarket_id", "location_id", "total", "is_cheapest", "product_count",
            "denominator_definition",
        ],
    )
    exclusion_rows = [
        {"reason": reason, "group_count": count}
        for reason, count in publication.excluded_group_counts.items()
    ]
    _write_csv(output_directory / "excluded-groups.csv", exclusion_rows, ["reason", "group_count"])

    manifest: dict[str, object] = {
        "schema": "precios-sps-publication-manifest/v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_backend": backend.kind,
        "comparison_policy": publication.comparison_policy,
        "scope": [dict(item) for item in publication.scope],
        "source_product_records": len(records),
        "current_price_observations": len(observations),
        "homologation_profiles": len(homologation.profiles),
        "safe_products_published": len(publication.products),
        "safe_offers_published": len(publication.offers),
        "common_basket_product_count": analytics.common_basket.product_count,
        "excluded_group_counts": dict(publication.excluded_group_counts),
        "files": [
            "publication.json",
            "offers.csv",
            "products.csv",
            "common-basket.csv",
            "excluded-groups.csv",
        ],
    }
    _atomic_text(
        output_directory / "manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    source = result.add_mutually_exclusive_group(required=True)
    source.add_argument("--sqlite", type=Path, help="SQLite existente, abierto en modo read-only")
    source.add_argument("--turso", action="store_true", help="Usa TURSO_DATABASE_URL y TURSO_AUTH_TOKEN")
    result.add_argument(
        "--scope",
        action="append",
        required=True,
        metavar="SUPERMARKET=LOCATION",
        help="Una ubicación explícita por supermercado; repetir al menos dos veces",
    )
    result.add_argument("--output-directory", type=Path, required=True)
    result.add_argument("--require-products", action="store_true")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    scope = parse_scope(args.scope)
    backend: SQLiteBackend | TursoBackend
    if args.sqlite is not None:
        if not args.sqlite.is_file():
            raise ExportError("sqlite_file_missing")
        backend = SQLiteBackend(args.sqlite)
    else:
        backend = TursoBackend(
            os.environ.get("TURSO_DATABASE_URL", ""),
            os.environ.get("TURSO_AUTH_TOKEN", ""),
        )
    try:
        manifest = export_dataset(
            backend,
            scope,
            args.output_directory,
            require_products=args.require_products,
        )
    finally:
        backend.close()
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
