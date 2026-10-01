#!/usr/bin/env python3
"""Exporta Consumer Catalog v3 para Tegucigalpa sin alterar el contrato SPS.

El exportador base nació con un único contexto por cadena. Tegucigalpa tiene dos
Walmart y dos Paiz productivos, por lo que esta capa conserva la identidad exacta
``supermarket_id + location_id`` y hace único el ``source_product_id`` público por
ubicación. El proceso es sólo lectura.
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence

import exportar_consumer_catalog as base
from exportar_modelo_analitico import ExportError, SQLiteBackend, TursoBackend


TGU_SCOPE = (
    ("la_colonia", "la_colonia_tgu"),
    ("walmart", "walmart_tgu_ffaa"),
    ("walmart", "walmart_tgu_el_sauce"),
    ("pricesmart", "pricesmart_tgu"),
    ("paiz", "paiz_tgu_multiplaza"),
    ("paiz", "paiz_tgu_proceres"),
)
TGU_RETAILERS = (
    ("la_colonia", "la_colonia_tgu", "La Colonia"),
    ("walmart", "walmart_tgu_ffaa", "Walmart FFAA"),
    ("walmart", "walmart_tgu_el_sauce", "Walmart El Sauce"),
    ("pricesmart", "pricesmart_tgu", "PriceSmart Florencia"),
    ("paiz", "paiz_tgu_multiplaza", "Paiz Multiplaza"),
    ("paiz", "paiz_tgu_proceres", "Paiz Próceres"),
)
FACETS_FILE = "facets-tgu.json"
CITY = {"city": "Tegucigalpa", "country_code": "HN"}


@dataclass(frozen=True, slots=True)
class CityCatalogScope:
    """Scope B2C por contexto exacto; una cadena puede tener varias sucursales."""

    locations: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not self.locations:
            raise ExportError("consumer_catalog_tgu_scope_empty")
        normalized: list[tuple[str, str]] = []
        for supermarket_id, location_id in self.locations:
            supermarket = supermarket_id.strip() if isinstance(supermarket_id, str) else ""
            location = location_id.strip() if isinstance(location_id, str) else ""
            if not supermarket or not location:
                raise ExportError("consumer_catalog_tgu_scope_context_invalid")
            normalized.append((supermarket, location))
        if len(set(normalized)) != len(normalized):
            raise ExportError("consumer_catalog_tgu_scope_context_duplicate")
        object.__setattr__(self, "locations", tuple(normalized))

    @property
    def supermarket_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(supermarket_id for supermarket_id, _ in self.locations))


def public_source_id(source_product_id: str, location_id: str) -> str:
    """Hace inequívoca una oferta cuando un producto fuente existe en dos tiendas."""
    return f"{source_product_id}@{location_id}"


def identity_groups_exact_context(
    offers: Iterable[base.VisibleOffer],
) -> list[tuple[str, list[base.VisibleOffer]]]:
    """Agrupa sólo identidades fuertes sin ambigüedad dentro de una sucursal.

    Una identidad canónica sólo se publica como comparable cuando participa más
    de una cadena y existe exactamente una oferta fuente por contexto físico. Si
    una sucursal aporta dos filas a la misma identidad, no se elige ni se fusiona
    ninguna: todo el grupo se degrada a filas individuales, igual que el contrato
    SPS degrada las colisiones por supermercado.
    """
    values = tuple(offers)
    candidates: dict[str, list[base.VisibleOffer]] = {}
    individual: list[base.VisibleOffer] = []
    for offer in values:
        if offer.comparison_status == "ready" and offer.canonical_product_id:
            candidates.setdefault(offer.canonical_product_id, []).append(offer)
        else:
            individual.append(offer)

    groups: list[tuple[str, list[base.VisibleOffer]]] = []
    for grouped in candidates.values():
        contexts = [(offer.supermarket_id, offer.location_id) for offer in grouped]
        chains = {offer.supermarket_id for offer in grouped}
        unambiguous_contexts = len(contexts) == len(set(contexts))
        if len(grouped) >= 2 and len(chains) >= 2 and unambiguous_contexts:
            groups.append(("comparable", grouped))
        else:
            individual.extend(grouped)

    groups.extend(
        (
            "single_source" if offer.comparison_status == "single_source" else "individual",
            [offer],
        )
        for offer in individual
    )
    if sum(len(group) for _, group in groups) != len(values):
        raise ExportError("consumer_catalog_tgu_identity_partition_invalid")
    return groups


def _patch_base_for_tgu() -> None:
    """Acota el exportador base al contrato TGU durante este proceso."""
    original_fetch_visible = base.fetch_visible_offers
    original_fetch_history = base.fetch_historical_points

    def fetch_visible(backend, scope):
        offers = original_fetch_visible(backend, scope)
        return tuple(
            replace(
                offer,
                source_product_id=public_source_id(offer.source_product_id, offer.location_id),
            )
            for offer in offers
        )

    def fetch_history(backend, scope, *, as_of_utc):
        grouped = original_fetch_history(backend, scope, as_of_utc=as_of_utc)
        result = {}
        for (_, location_id), points in grouped.items():
            transformed = tuple(
                replace(
                    point,
                    source_product_id=public_source_id(point.source_product_id, point.location_id),
                )
                for point in points
            )
            if not transformed:
                continue
            key = (transformed[0].source_product_id, location_id)
            if key in result:
                raise ExportError("consumer_catalog_tgu_history_collision")
            result[key] = transformed
        return result

    base.EXPECTED_SCOPE = TGU_SCOPE
    base.RETAILER_NAMES = {supermarket_id: supermarket_id for supermarket_id, _ in TGU_SCOPE}
    base.fetch_visible_offers = fetch_visible
    base.fetch_historical_points = fetch_history
    base._identity_groups = identity_groups_exact_context


def _context_counts(backend, scope) -> dict[tuple[str, str], int]:
    return _context_counts_from_offers(base.fetch_visible_offers(backend, scope))


def _context_counts_from_offers(offers: Iterable[base.VisibleOffer]) -> dict[tuple[str, str], int]:
    counts = {context: 0 for context in TGU_SCOPE}
    for offer in offers:
        context = (offer.supermarket_id, offer.location_id)
        if context in counts:
            counts[context] += 1
    return counts


def _rewrite_public_metadata(output: Path, manifest: dict[str, object], counts: dict[tuple[str, str], int]) -> dict[str, object]:
    old_facets = output / "facets-sps.json"
    if not old_facets.is_file():
        raise ExportError("consumer_catalog_tgu_facets_missing")
    facets = json.loads(old_facets.read_text(encoding="utf-8"))
    facets["location"] = CITY
    base._atomic_bytes(output / FACETS_FILE, base._json_bytes(facets))
    old_facets.unlink()

    files = []
    for item in manifest.get("files", []):
        if not isinstance(item, dict):
            raise ExportError("consumer_catalog_tgu_manifest_files_invalid")
        path = item.get("path")
        if path == "facets-sps.json":
            files.append(base._file_metadata(output, FACETS_FILE))
        else:
            files.append(item)

    manifest["location"] = CITY
    manifest["scope"] = [
        {"supermarket_id": supermarket_id, "location_id": location_id}
        for supermarket_id, location_id in TGU_SCOPE
    ]
    manifest["retailers"] = [
        {"supermarket_id": supermarket_id, "location_id": location_id, "name": name}
        for supermarket_id, location_id, name in TGU_RETAILERS
    ]
    manifest["context_offer_counts"] = [
        {
            "supermarket_id": supermarket_id,
            "location_id": location_id,
            "offer_count": counts[(supermarket_id, location_id)],
        }
        for supermarket_id, location_id in TGU_SCOPE
    ]
    manifest["visibility_policy"] = "accepted_current_source_offers_in_city_scope"
    manifest["initial_files"] = [FACETS_FILE]
    manifest["files"] = files
    facets_meta = next(item for item in files if item.get("path") == FACETS_FILE)
    manifest["initial_payload"] = {
        "bytes": facets_meta["bytes"],
        "gzip_bytes": facets_meta["gzip_bytes"],
        "request_count": 2,
    }
    base._atomic_bytes(output / "manifest.json", base._json_bytes(manifest))
    return manifest


def export_tgu_catalog(
    backend,
    output: Path,
    *,
    as_of_utc: datetime,
    freshness_window: timedelta,
    require_products: bool,
) -> dict[str, object]:
    _patch_base_for_tgu()
    scope = CityCatalogScope(TGU_SCOPE)
    # Una sola lectura de ofertas current: los conteos por contexto y el export
    # reutilizan exactamente la misma tupla en vez de releer Turso.
    fetch_visible = base.fetch_visible_offers
    offers = fetch_visible(backend, scope)
    counts = _context_counts_from_offers(offers)
    if require_products and any(count <= 0 for count in counts.values()):
        missing = [f"{s}:{l}" for (s, l), count in counts.items() if count <= 0]
        raise ExportError(f"consumer_catalog_tgu_missing_context:{','.join(missing)}")

    def reuse_visible(current_backend, current_scope):
        if current_backend is backend and current_scope is scope:
            return offers
        return fetch_visible(current_backend, current_scope)

    base.fetch_visible_offers = reuse_visible
    try:
        manifest = base.export_consumer_catalog(
            backend,
            scope,
            output,
            as_of_utc=as_of_utc,
            freshness_window=freshness_window,
            require_products=False,
        )
    finally:
        base.fetch_visible_offers = fetch_visible
        base._core.fetch_visible_offers = fetch_visible
    return _rewrite_public_metadata(output, manifest, counts)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    source = result.add_mutually_exclusive_group(required=True)
    source.add_argument("--sqlite", type=Path)
    source.add_argument("--turso", action="store_true")
    result.add_argument("--output-directory", type=Path, required=True)
    result.add_argument("--as-of-utc")
    result.add_argument("--freshness-hours", type=int, default=48)
    result.add_argument("--require-products", action="store_true")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.freshness_hours <= 0:
        raise ExportError("consumer_catalog_tgu_freshness_hours_invalid")
    as_of = (
        datetime.now(timezone.utc)
        if args.as_of_utc is None
        else base._parse_utc(args.as_of_utc, "consumer_catalog_tgu_as_of_invalid")
    )
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
        manifest = export_tgu_catalog(
            backend,
            args.output_directory,
            as_of_utc=as_of,
            freshness_window=timedelta(hours=args.freshness_hours),
            require_products=args.require_products,
        )
    finally:
        backend.close()
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
