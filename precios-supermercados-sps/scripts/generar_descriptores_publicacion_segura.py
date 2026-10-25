#!/usr/bin/env python3
"""Genera descriptores publicables únicamente para ofertas ya autorizadas por el comparador seguro.

La entrada es ``precios-sps-publication/v1``. El script vuelve a consultar la
fila fuente persistida y exige que el GTIN canónico coincida antes de exponer
nombre, marca, presentación o categoría. No hace scraping ni relaja identidad.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from exportar_modelo_analitico import ExportError, SQLiteBackend, TursoBackend  # noqa: E402
from precios_supermercados.identifiers import canonicalize_gtin  # noqa: E402

SCHEMA = "precios-sps-safe-source-descriptors/v1"
PUBLICATION_SCHEMA = "precios-sps-publication/v1"
POLICY = "fail_closed_strong_identity_and_commercial_consistency"


def _load_publication(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExportError("publication_input_invalid") from exc
    if not isinstance(value, dict):
        raise ExportError("publication_input_not_object")
    if value.get("schema") != PUBLICATION_SCHEMA:
        raise ExportError("publication_schema_invalid")
    if value.get("comparison_policy") != POLICY:
        raise ExportError("publication_policy_invalid")
    if not isinstance(value.get("offers"), list):
        raise ExportError("publication_offers_invalid")
    return value


def _offer_identity(offer: object) -> tuple[int, str, str, str, str]:
    if not isinstance(offer, dict):
        raise ExportError("publication_offer_not_object")
    source_record_id = offer.get("source_record_id")
    supermarket_id = offer.get("supermarket_id")
    canonical_product_id = offer.get("canonical_product_id")
    canonical_gtin = offer.get("canonical_gtin")
    if not all(isinstance(value, str) and value.strip() for value in (
        source_record_id,
        supermarket_id,
        canonical_product_id,
        canonical_gtin,
    )):
        raise ExportError("publication_offer_identity_invalid")
    prefix, separator, product_text = source_record_id.partition(":")
    if separator != ":" or prefix != supermarket_id or not product_text.isdigit():
        raise ExportError("publication_source_record_id_invalid")
    product_id = int(product_text)
    if product_id <= 0:
        raise ExportError("publication_product_id_invalid")
    return product_id, supermarket_id, canonical_product_id, canonical_gtin, source_record_id


def build_descriptors(backend, publication_path: Path) -> dict[str, object]:
    publication = _load_publication(publication_path)
    requested: dict[int, tuple[str, str, str, str]] = {}
    for raw_offer in publication["offers"]:
        product_id, supermarket_id, canonical_product_id, canonical_gtin, source_record_id = _offer_identity(raw_offer)
        value = (supermarket_id, canonical_product_id, canonical_gtin, source_record_id)
        previous = requested.setdefault(product_id, value)
        if previous != value:
            raise ExportError("publication_product_identity_conflict")

    rows: list[dict[str, object]] = []
    found: set[int] = set()
    product_ids = sorted(requested)
    for start in range(0, len(product_ids), 400):
        chunk = product_ids[start : start + 400]
        if not chunk:
            continue
        placeholders = ",".join("?" for _ in chunk)
        source_rows = backend.query(
            f"""
            SELECT product_id,supermarket_id,name,brand,presentation,category,ean
            FROM products
            WHERE product_id IN ({placeholders})
            ORDER BY product_id
            """,
            tuple(chunk),
        )
        for product_id, supermarket_id, name, brand, presentation, category, ean in source_rows:
            if type(product_id) is not int or product_id not in requested:
                raise ExportError("descriptor_product_identity_invalid")
            if product_id in found:
                raise ExportError("descriptor_product_duplicate")
            found.add(product_id)
            expected_supermarket, canonical_product_id, canonical_gtin, source_record_id = requested[product_id]
            if supermarket_id != expected_supermarket or not isinstance(name, str) or not name.strip():
                raise ExportError("descriptor_source_row_invalid")
            if not isinstance(ean, str) or canonicalize_gtin(ean) != canonical_gtin:
                raise ExportError("descriptor_gtin_mismatch")
            rows.append(
                {
                    "canonical_product_id": canonical_product_id,
                    "canonical_gtin": canonical_gtin,
                    "source_record_id": source_record_id,
                    "supermarket_id": supermarket_id,
                    "source_name": name.strip(),
                    "source_brand": brand.strip() if isinstance(brand, str) and brand.strip() else None,
                    "source_presentation": presentation.strip() if isinstance(presentation, str) and presentation.strip() else None,
                    "source_category": category.strip() if isinstance(category, str) and category.strip() else None,
                }
            )

    if found != set(product_ids):
        raise ExportError("descriptor_source_rows_missing")

    rows.sort(key=lambda row: (str(row["canonical_product_id"]), str(row["supermarket_id"]), str(row["source_record_id"])))
    return {
        "schema": SCHEMA,
        "comparison_policy": POLICY,
        "source_backend": backend.kind,
        "row_count": len(rows),
        "canonical_product_count": len({row["canonical_product_id"] for row in rows}),
        "rows": rows,
    }


def _atomic_json(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    source = result.add_mutually_exclusive_group(required=True)
    source.add_argument("--sqlite", type=Path)
    source.add_argument("--turso", action="store_true")
    result.add_argument("--publication", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not args.publication.is_file():
        raise ExportError("publication_file_missing")
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
        document = build_descriptors(backend, args.publication)
    finally:
        backend.close()
    _atomic_json(args.output, document)
    print(json.dumps({
        "schema": document["schema"],
        "row_count": document["row_count"],
        "canonical_product_count": document["canonical_product_count"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
