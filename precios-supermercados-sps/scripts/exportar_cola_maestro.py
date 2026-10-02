#!/usr/bin/env python3
"""Cola de revisión producto → producto maestro (shadow, manual, nunca diario).

Fuentes (una):

``--records``  JSONL ``precios-sps-matching-record/v1`` (offline). Reconstruye
               perfiles y maestros en memoria con las mismas reglas del
               refresco diario y además mide completitud de los registros
               golden frente a las filas de cada cadena.
``--sqlite``   base SQLite con ``products`` + tablas del maestro.
``--turso``    Turso (sólo lectura). Lee ``products`` completo, maestros,
               vínculos activos y rechazos: ~P + M + L filas. Con
               ``--with-cities`` lee además las ofertas current por el índice
               parcial ``idx_price_history_current`` (~ofertas vigentes). Usar
               de forma puntual: el plan Starter tiene cuota mensual de lecturas.

Salidas en ``--output-dir``: ``review-queue.csv`` / ``review-queue.jsonl``
(una fila por candidato con ``review_id``, maestro, producto, score y
desglose; columnas vacías ``decision``/``reviewer``/``note`` para el revisor) y
``summary.json``. En modo offline también ``masters.jsonl``.
El CSV revisado se importa con ``scripts/importar_decisiones_maestro.py``.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from precios_supermercados.matching.comparison import ComparisonSettings  # noqa: E402
from precios_supermercados.matching.config import (  # noqa: E402
    DEFAULT_CONFIG_PATH,
    DEFAULT_TAXONOMY_PATH,
    load_engine_config,
)
from precios_supermercados.matching.master_candidates import (  # noqa: E402
    CSV_COLUMNS,
    MASTER_CANDIDATE_SCHEMA,
    MasterCandidateSettings,
    csv_row,
    generate_master_candidates,
    standardize_records,
)
from precios_supermercados.matching.records import MatchRecord, read_records  # noqa: E402
from precios_supermercados.matching.taxonomy import load_source_taxonomy  # noqa: E402
from precios_supermercados.product_homologation import SourceProductRecord  # noqa: E402
from precios_supermercados.product_homologation_persistence import build_homologation_rows  # noqa: E402
from precios_supermercados.product_master import (  # noqa: E402
    DEFAULT_POLICY_PATH,
    MASTER_BUILDER_VERSION,
    MASTER_TABLE,
    LINK_TABLE,
    REJECTION_TABLE,
    GoldenRecord,
    MasterStore,
    SQLiteMasterStore,
    build_desired_state,
    golden_variant_labels,
    load_master_policy,
    member_profiles,
    read_all_active_links,
    source_product_key,
)

_CITY_CODES = {"san pedro sula": "SPS", "tegucigalpa": "TGU"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def write_queue(rows: Sequence[Mapping[str, object]], output_dir: Path, *, metadata: Mapping[str, object]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "review-queue.jsonl").open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({"_metadata": metadata}, ensure_ascii=False, sort_keys=True) + "\n")
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    with (output_dir / "review-queue.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow(csv_row(row))


# --------------------------------------------------------------------------
# Offline: registros reconstruidos → perfiles → maestros
# --------------------------------------------------------------------------


def dedup_records(records: Sequence[MatchRecord]) -> tuple[list[tuple[int, SourceProductRecord]], dict[str, str]]:
    """Un producto por (cadena, evidencia fuente), compartido entre tiendas/ciudades.

    Igual que Turso ``products``: TGU publica un registro por tienda del mismo
    producto. El GTIN usado es el barcode explícito o, si falta, el GTIN
    auxiliar del snapshot (``silver_gtin``: EAN de Walmart/Paiz/La Colonia y el
    ``sku`` GS1 de Colonial que la v2.4+ ya persiste como ``ean``).
    """

    merged: dict[tuple[object, ...], list[MatchRecord]] = {}
    for record in records:
        barcode = record.barcode or record.silver_gtin
        key = (
            record.supermarket_id,
            record.source_name,
            record.source_brand,
            record.source_presentation,
            record.source_category,
            barcode,
        )
        merged.setdefault(key, []).append(record)
    entries: list[tuple[int, SourceProductRecord]] = []
    key_of: dict[str, str] = {}
    ordered = sorted(merged.items(), key=lambda item: min(record.source_record_id for record in item[1]))
    for index, (key, group) in enumerate(ordered, start=1):
        supermarket, name, brand, presentation, category, barcode = key
        entries.append(
            (
                index,
                SourceProductRecord(
                    source_record_id=f"{supermarket}:{index}",
                    supermarket_id=str(supermarket),
                    source_name=str(name),
                    source_brand=brand,  # type: ignore[arg-type]
                    source_presentation=presentation,  # type: ignore[arg-type]
                    source_category=category,  # type: ignore[arg-type]
                    barcode=barcode,  # type: ignore[arg-type]
                ),
            )
        )
        for record in group:
            key_of[f"{record.city}|{record.source_record_id}"] = source_product_key(str(supermarket), index)
    return entries, key_of


def _pct(part: int, total: int) -> float:
    return round(100.0 * part / total, 1) if total else 0.0


def completeness_report(
    *,
    city: str,
    masters: Mapping[str, GoldenRecord],
    member_keys_by_master: Mapping[str, set[str]],
    profiles_by_key: Mapping[str, object],
    names_by_key: Mapping[str, str],
    city_keys: set[str],
) -> dict[str, object]:
    """Completitud golden vs filas fuente (miembros de maestros y catálogo completo)."""

    city_masters = [master_id for master_id, keys in member_keys_by_master.items() if keys & city_keys]
    golden = [masters[master_id] for master_id in city_masters]
    member_keys = sorted({key for master_id in city_masters for key in member_keys_by_master[master_id] & city_keys})

    def row_stats(keys: Iterable[str]) -> dict[str, float | int]:
        keys = list(keys)
        brand = size = ptype = variant = 0
        for key in keys:
            profile = profiles_by_key[key]
            brand += bool(getattr(profile, "normalized_brand"))
            size += bool(
                getattr(profile, "presentation_total_base")
                and getattr(profile, "presentation_status") not in {"conflict", "ambiguous_multipack", "missing"}
            )
            ptype += bool(getattr(profile, "product_type"))
            variant += bool(golden_variant_labels(names_by_key[key]))
        return {
            "rows": len(keys),
            "brand_pct": _pct(brand, len(keys)),
            "size_pct": _pct(size, len(keys)),
            "product_type_pct": _pct(ptype, len(keys)),
            "variant_declared_pct": _pct(variant, len(keys)),
        }

    def enriched(master_ids: Sequence[str]) -> dict[str, float | int]:
        gaps = Counter()
        filled = Counter()
        for master_id in master_ids:
            record = masters[master_id]
            for key in member_keys_by_master[master_id] & city_keys:
                profile = profiles_by_key[key]
                checks = {
                    "brand": (not getattr(profile, "normalized_brand"), bool(record.brand)),
                    "size": (
                        not getattr(profile, "presentation_total_base")
                        or getattr(profile, "presentation_status") in {"conflict", "ambiguous_multipack", "missing"},
                        record.net_content_value is not None,
                    ),
                    "product_type": (not getattr(profile, "product_type"), bool(record.product_type)),
                }
                for name, (missing, golden_has) in checks.items():
                    if missing:
                        gaps[name] += 1
                        filled[name] += golden_has
        return {
            **{f"{name}_gaps": gaps[name] for name in ("brand", "size", "product_type")},
            **{f"{name}_filled": filled[name] for name in ("brand", "size", "product_type")},
        }

    multi_ids = [
        master_id
        for master_id in city_masters
        if len({key.split(":", 1)[0] for key in member_keys_by_master[master_id] & city_keys}) >= 2
    ]
    multi = len(multi_ids)

    def golden_stats(items: Sequence[GoldenRecord]) -> dict[str, float | int]:
        return {
            "rows": len(items),
            "brand_pct": _pct(sum(bool(item.brand) for item in items), len(items)),
            "size_pct": _pct(sum(item.net_content_value is not None for item in items), len(items)),
            "product_type_pct": _pct(sum(bool(item.product_type) for item in items), len(items)),
            "variant_declared_pct": _pct(sum(bool(item.variant) for item in items), len(items)),
            "category_pct": _pct(sum(bool(item.category) for item in items), len(items)),
        }
    return {
        "city": city,
        "masters": len(golden),
        "multi_retailer_masters_in_city": multi,
        "golden": golden_stats(golden),
        "member_rows": row_stats(member_keys),
        "all_retailer_rows": row_stats(sorted(city_keys)),
        # Filas miembro sin el atributo cuyo maestro sí lo tiene en el golden.
        "member_rows_enriched_by_golden": enriched(city_masters),
        # Sólo maestros con ≥2 cadenas en la ciudad (donde el golden combina fuentes).
        "multi_retailer": {
            "golden": golden_stats([masters[m] for m in multi_ids]),
            "member_rows": row_stats(sorted({k for m in multi_ids for k in member_keys_by_master[m] & city_keys})),
            "member_rows_enriched_by_golden": enriched(multi_ids),
        },
    }


def run_offline(
    records_path: Path,
    output_dir: Path,
    *,
    cities: Sequence[str] | None = None,
    settings: MasterCandidateSettings | None = None,
    log=print,  # type: ignore[no-untyped-def]
) -> dict[str, object]:
    started = time.time()
    policy = load_master_policy(DEFAULT_POLICY_PATH)
    config = load_engine_config(DEFAULT_CONFIG_PATH)
    taxonomy = load_source_taxonomy(DEFAULT_TAXONOMY_PATH)
    records = list(read_records(records_path))
    if cities:
        records = [record for record in records if record.city in set(cities)]
    entries, key_of = dedup_records(records)
    log(f"records={len(records)} products={len(entries)}")
    timestamp = _utc_now()
    derived = build_homologation_rows(entries, updated_at_utc=timestamp)
    names = {product_id: record.source_name for product_id, record in entries}
    members = member_profiles(derived, names)
    desired = build_desired_state(members, policy=policy)
    log(f"profiles+masters {time.time() - started:.0f}s masters={len(desired.masters)} links={len(desired.links)}")
    supermarket_of = {product_id: record.supermarket_id for product_id, record in entries}
    master_keys: dict[str, set[str]] = defaultdict(set)
    for product_id, link in desired.links.items():
        master_keys[link.master_product_id].add(source_product_key(supermarket_of[product_id], product_id))
    master_of_key = {key: master_id for master_id, keys in master_keys.items() for key in keys}
    profiles_by_key = {source_product_key(row.supermarket_id, row.product_id): row for row in derived}
    names_by_key = {source_product_key(record.supermarket_id, pid): record.source_name for pid, record in entries}

    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "masters.jsonl").open("w", encoding="utf-8") as handle:
        for master_id, record in sorted(desired.masters.items()):
            handle.write(
                json.dumps(
                    {**record.golden_payload(), "record_hash": record.record_hash, "members": sorted(master_keys[master_id])},
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )

    comparison = ComparisonSettings.from_config(config.section("comparison"), config.implicit_defaults)
    rare_share = float(config.section("comparison").get("rare_token_share", 0.005))
    by_city: dict[str, list[MatchRecord]] = defaultdict(list)
    for record in records:
        by_city[record.city].append(record)
    all_rows: list[dict[str, object]] = []
    city_summaries: dict[str, object] = {}
    completeness: dict[str, object] = {}
    status_by_key = {key: getattr(row, "comparison_status") for key, row in profiles_by_key.items()}
    for city, city_records in sorted(by_city.items()):
        t0 = time.time()
        product_key_of = {record.source_record_id: key_of[f"{city}|{record.source_record_id}"] for record in city_records}
        city_keys = set(product_key_of.values())
        standardized = standardize_records(city_records, config=config, taxonomy=taxonomy, rare_token_share=rare_share)
        rows, summary = generate_master_candidates(
            standardized,
            city=city,
            masters=desired.masters,
            product_key_of=product_key_of,
            master_of_product=master_of_key,
            comparison=comparison,
            settings=settings,
        )
        # No homologados = productos (no registros por tienda) sin vínculo o en un
        # maestro de un solo miembro (single_source).
        unlinked_keys = {
            key for key in city_keys if key not in master_of_key or len(master_keys[master_of_key[key]]) == 1
        }
        with_candidate = {str(row["product_key"]) for row in rows}
        first = {str(row["product_key"]): row for row in rows if row["rank"] == 1}
        by_retailer: dict[str, Counter[str]] = defaultdict(Counter)
        for key in unlinked_keys:
            retailer = key.split(":", 1)[0]
            by_retailer[retailer]["unlinked_products"] += 1
            row = first.get(key)
            if row is not None:
                by_retailer[retailer]["with_candidate"] += 1
                by_retailer[retailer][f"band_{row['band']}"] += 1
                by_retailer[retailer][f"status_{status_by_key[key]}"] += 1
                by_retailer[retailer][f"from_{row['product_link_state']}"] += 1
                if "different_valid_gtin" in row["flags"]:  # type: ignore[operator]
                    by_retailer[retailer]["different_valid_gtin"] += 1
        summary["unlinked_products"] = len(unlinked_keys)
        summary["unlinked_products_with_candidate"] = len(with_candidate)
        summary["unlinked_products_by_retailer"] = {key: dict(sorted(value.items())) for key, value in sorted(by_retailer.items())}
        summary["seconds"] = round(time.time() - t0, 1)
        city_summaries[city] = summary
        completeness[city] = completeness_report(
            city=city,
            masters=desired.masters,
            member_keys_by_master=master_keys,
            profiles_by_key=profiles_by_key,
            names_by_key=names_by_key,
            city_keys=city_keys,
        )
        all_rows.extend(rows)
        log(f"{city}: unlinked={len(unlinked_keys)} with_candidate={len(with_candidate)} rows={len(rows)} {summary['seconds']}s")

    metadata = {
        "schema": MASTER_CANDIDATE_SCHEMA,
        "builder_version": MASTER_BUILDER_VERSION,
        "generated_at_utc": timestamp,
        "source": "offline_records",
        "records": str(records_path),
        "decision_policy": "review_only (engine_auto disabled; different valid GTIN never links)",
    }
    write_queue(all_rows, output_dir, metadata=metadata)
    summary = {
        **metadata,
        "products": len(entries),
        "masters": len(desired.masters),
        "active_links": len(desired.links),
        "diagnostics": desired.diagnostics,
        "link_methods": dict(Counter(link.link_method for link in desired.links.values())),
        "comparison_status": dict(Counter(row.comparison_status for row in derived)),
        "cities": city_summaries,
        "completeness": completeness,
        "seconds": round(time.time() - started, 1),
    }
    _write_json(output_dir / "summary.json", summary)
    return summary


# --------------------------------------------------------------------------
# SQLite / Turso: estado persistido
# --------------------------------------------------------------------------


def _read_products(store: MasterStore) -> list[tuple[int, SourceProductRecord]]:
    result: list[tuple[int, SourceProductRecord]] = []
    cursor = 0
    while True:
        rows = store.query(
            "SELECT product_id,supermarket_id,name,brand,presentation,category,ean FROM products "
            "WHERE product_id>? ORDER BY product_id LIMIT 2000",
            (cursor,),
        )
        for product_id, supermarket_id, name, brand, presentation, category, ean in rows:
            result.append(
                (
                    int(product_id),  # type: ignore[arg-type]
                    SourceProductRecord(
                        source_record_id=f"{supermarket_id}:{product_id}",
                        supermarket_id=str(supermarket_id),
                        source_name=str(name),
                        source_brand=None if brand is None else str(brand),
                        source_presentation=None if presentation is None else str(presentation),
                        source_category=None if category is None else str(category),
                        barcode=None if ean is None else str(ean),
                    ),
                )
            )
        if len(rows) < 2000:
            break
        cursor = int(rows[-1][0])  # type: ignore[arg-type]
    return result


def _read_golden(store: MasterStore) -> dict[str, GoldenRecord]:
    columns = (
        "master_product_id,primary_gtin,origin_method,display_name,brand,manufacturer,product_type,category,"
        "net_content_value,net_content_unit,pack_count,variant,origin,attribute_provenance_json,"
        "human_attributes_json,status,merged_into,builder_version"
    )
    result: dict[str, GoldenRecord] = {}
    cursor = ""
    while True:
        rows = store.query(
            f"SELECT {columns} FROM {MASTER_TABLE} WHERE master_product_id>? AND status='active' "
            "ORDER BY master_product_id LIMIT 2000",
            (cursor,),
        )
        for row in rows:
            values = list(row)
            values[13] = json.loads(str(values[13]))
            values[14] = json.loads(str(values[14]))
            record = GoldenRecord(*values)  # type: ignore[arg-type]
            result[record.master_product_id] = record
        if len(rows) < 2000:
            break
        cursor = str(rows[-1][0])
    return result


def _read_rejections(store: MasterStore) -> set[tuple[str, str]]:
    rows = store.query(f"SELECT product_id,supermarket_id,master_product_id FROM {REJECTION_TABLE}")
    return {(source_product_key(str(supermarket), int(product)), str(master)) for product, supermarket, master in rows}  # type: ignore[arg-type]


def _read_cities(store: MasterStore) -> dict[int, set[str]]:
    city_of = {
        str(location): _CITY_CODES.get(str(city).casefold(), str(city))
        for location, city in store.query("SELECT location_id,city_name FROM locations")
    }
    result: dict[int, set[str]] = defaultdict(set)
    for product_id, location_id in store.query(
        "SELECT product_id,location_id FROM price_history WHERE valid_to_utc IS NULL"
    ):
        result[int(product_id)].add(city_of.get(str(location_id), "unknown"))  # type: ignore[arg-type]
    return result


def run_persisted(
    store: MasterStore,
    output_dir: Path,
    *,
    source: str,
    with_cities: bool,
    settings: MasterCandidateSettings | None = None,
) -> dict[str, object]:
    config = load_engine_config(DEFAULT_CONFIG_PATH)
    taxonomy = load_source_taxonomy(DEFAULT_TAXONOMY_PATH)
    tables = {str(row[0]) for row in store.query("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {MASTER_TABLE, LINK_TABLE, REJECTION_TABLE} <= tables:
        raise SystemExit("master_tables_missing")
    products = _read_products(store)
    masters = _read_golden(store)
    links = read_all_active_links(store)
    rejections = _read_rejections(store)
    cities = _read_cities(store) if with_cities else {}
    master_of_key = {link.key: link.master_product_id for link in links.values()}
    by_city: dict[str, list[MatchRecord]] = defaultdict(list)
    for product_id, record in products:
        for city in sorted(cities.get(product_id, {"ALL"})) if with_cities else ["ALL"]:
            by_city[city].append(
                MatchRecord(
                    source_record_id=record.source_record_id,
                    supermarket_id=record.supermarket_id,
                    city=city,
                    source_name=record.source_name,
                    source_brand=record.source_brand,
                    source_presentation=record.source_presentation,
                    source_category=record.source_category,
                    barcode=record.barcode,
                    fingerprint_authority="turso_products" if source == "turso" else "fixture",
                )
            )
    comparison = ComparisonSettings.from_config(config.section("comparison"), config.implicit_defaults)
    all_rows: list[dict[str, object]] = []
    summaries: dict[str, object] = {}
    for city, records in sorted(by_city.items()):
        product_key_of = {record.source_record_id: record.source_record_id for record in records}
        standardized = standardize_records(records, config=config, taxonomy=taxonomy)
        rows, summary = generate_master_candidates(
            standardized,
            city=city,
            masters=masters,
            product_key_of=product_key_of,
            master_of_product=master_of_key,
            rejections=rejections,
            comparison=comparison,
            settings=settings,
        )
        summaries[city] = summary
        all_rows.extend(rows)
    metadata = {
        "schema": MASTER_CANDIDATE_SCHEMA,
        "builder_version": MASTER_BUILDER_VERSION,
        "generated_at_utc": _utc_now(),
        "source": source,
        "decision_policy": "review_only (engine_auto disabled; different valid GTIN never links)",
    }
    write_queue(all_rows, output_dir, metadata=metadata)
    summary = {
        **metadata,
        "products": len(products),
        "masters": len(masters),
        "active_links": len(links),
        "rejections": len(rejections),
        "cities": summaries,
        "rows_read": getattr(store, "rows_read", None),
    }
    _write_json(output_dir / "summary.json", summary)
    return summary


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = result.add_mutually_exclusive_group(required=True)
    source.add_argument("--records", type=Path)
    source.add_argument("--sqlite", type=Path)
    source.add_argument("--turso", action="store_true")
    result.add_argument("--output-dir", type=Path, required=True)
    result.add_argument("--cities", help="lista separada por comas (sólo --records)")
    result.add_argument("--with-cities", action="store_true", help="separa por ciudad con ofertas current (sqlite/turso)")
    result.add_argument("--top-k", type=int, default=2)
    result.add_argument("--min-score", type=float, default=0.55)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    settings = MasterCandidateSettings(top_k=args.top_k, min_score=args.min_score)
    if args.records is not None:
        summary = run_offline(
            args.records,
            args.output_dir,
            cities=args.cities.split(",") if args.cities else None,
            settings=settings,
            log=lambda message: print(message, file=sys.stderr, flush=True),
        )
    elif args.sqlite is not None:
        import sqlite3

        if not args.sqlite.is_file():
            raise SystemExit("sqlite_file_missing")
        connection = sqlite3.connect(args.sqlite, isolation_level=None)
        try:
            summary = run_persisted(
                SQLiteMasterStore(connection), args.output_dir, source="sqlite", with_cities=args.with_cities, settings=settings
            )
        finally:
            connection.close()
    else:
        from backfill_homologacion_turso import TursoMasterStore  # noqa: PLC0415

        url = os.environ.get("TURSO_DATABASE_URL", "")
        token = os.environ.get("TURSO_AUTH_TOKEN", "")
        if not url.strip() or not token.strip():
            raise SystemExit("turso_credentials_missing")
        summary = run_persisted(
            TursoMasterStore(url, token), args.output_dir, source="turso", with_cities=args.with_cities, settings=settings
        )
    print(json.dumps({key: summary[key] for key in ("masters", "cities") if key in summary}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
