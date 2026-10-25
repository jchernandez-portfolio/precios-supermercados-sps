#!/usr/bin/env python3
"""Export the shared RPI business and consumer marts without scraping/writes."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from exportar_modelo_analitico import (  # noqa: E402
    ExportError,
    QueryBackend,
    SQLiteBackend,
    TursoBackend,
    fetch_current_observations,
    fetch_products,
    parse_scope,
)
from precios_supermercados.analytics_quality import (  # noqa: E402
    QualityRun,
    QualityStatus,
    assess_comparable_market_window,
    assess_freshness,
)
from precios_supermercados.competitive_analytics import analyze_competition  # noqa: E402
from precios_supermercados.price_analytics import ComparisonScope, analyze_current_prices  # noqa: E402
from precios_supermercados.product_homologation import homologate_products  # noqa: E402
from precios_supermercados.rpi_data_marts import (  # noqa: E402
    MartCommercialState,
    MartHistoricalState,
    MartOfferDescriptor,
    build_rpi_data_marts,
)


def _parse_utc(value: object, error: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ExportError(error)
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExportError(error) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ExportError(error)
    return parsed.astimezone(UTC)


def fetch_freshness(
    backend: QueryBackend,
    scope: ComparisonScope,
    *,
    as_of_utc: datetime,
    freshness_window: timedelta,
):
    assessments = []
    as_of_text = as_of_utc.isoformat().replace("+00:00", "Z")
    for supermarket_id, location_id in scope.locations:
        rows = backend.query(
            """
            SELECT scrape_run_id,observed_at_utc
            FROM scrape_runs
            WHERE supermarket_id=? AND location_id=? AND run_status='success'
              AND julianday(observed_at_utc)<=julianday(?)
            ORDER BY observed_at_utc DESC,scrape_run_id DESC
            LIMIT 1
            """,
            (supermarket_id, location_id, as_of_text),
        )
        last_good = None
        if rows:
            run_id, observed_at = rows[0]
            if not isinstance(run_id, str):
                raise ExportError("rpi_latest_run_identity_invalid")
            last_good = QualityRun(
                run_id,
                supermarket_id,
                location_id,
                _parse_utc(observed_at, "rpi_latest_run_timestamp_invalid"),
                QualityStatus.ACCEPTED,
            )
        assessments.append(
            assess_freshness(
                last_good,
                source_id=supermarket_id,
                location_id=location_id,
                as_of_utc=as_of_utc,
                freshness_window=freshness_window,
            )
        )
    return tuple(assessments)


def _source_product_ids(source_record_ids: Sequence[str]) -> dict[int, str]:
    product_ids: dict[int, str] = {}
    for source_record_id in source_record_ids:
        _, separator, product_text = source_record_id.partition(":")
        if separator != ":" or not product_text.isdigit() or int(product_text) <= 0:
            raise ExportError("rpi_source_record_id_invalid")
        if int(product_text) in product_ids:
            raise ExportError("rpi_product_id_duplicate")
        product_ids[int(product_text)] = source_record_id
    return product_ids


def fetch_commercial_states(
    backend: QueryBackend,
    source_record_ids: Sequence[str],
    scope: ComparisonScope,
) -> tuple[MartCommercialState, ...]:
    product_ids = _source_product_ids(source_record_ids)
    scope_set = set(scope.locations)
    found: dict[str, MartCommercialState] = {}
    ids = sorted(product_ids)
    for start in range(0, len(ids), 400):
        chunk = ids[start : start + 400]
        placeholders = ",".join("?" for _ in chunk)
        rows = backend.query(
            f"""
            SELECT product_id,supermarket_id,location_id,current_price_minor,
                   reported_regular_price_minor,is_promotion,availability,valid_from_utc
            FROM price_history
            WHERE valid_to_utc IS NULL AND product_id IN ({placeholders})
            ORDER BY product_id,location_id
            """,
            tuple(chunk),
        )
        for row in rows:
            (
                product_id,
                supermarket_id,
                location_id,
                current_price,
                regular_price,
                is_promotion,
                availability,
                observed_at,
            ) = row
            if type(product_id) is not int or product_id not in product_ids:
                raise ExportError("rpi_commercial_product_invalid")
            if (supermarket_id, location_id) not in scope_set:
                continue
            source_record_id = product_ids[product_id]
            if source_record_id in found:
                raise ExportError("rpi_commercial_state_duplicate")
            if (
                type(current_price) is not int
                or (regular_price is not None and type(regular_price) is not int)
                or type(is_promotion) is not int
                or is_promotion not in {0, 1}
                or not isinstance(availability, str)
            ):
                raise ExportError("rpi_commercial_state_invalid")
            found[source_record_id] = MartCommercialState(
                source_record_id=source_record_id,
                current_price_minor=current_price,
                reported_regular_price_minor=regular_price,
                is_promotion=bool(is_promotion),
                availability=availability,
                observed_at_utc=_parse_utc(observed_at, "rpi_commercial_timestamp_invalid"),
            )
    if set(found) != set(source_record_ids):
        raise ExportError("rpi_commercial_states_missing")
    return tuple(found[key] for key in sorted(found))


def fetch_historical_states(
    backend: QueryBackend,
    source_record_ids: Sequence[str],
    scope: ComparisonScope,
    *,
    as_of_utc: datetime,
) -> tuple[MartHistoricalState, ...]:
    """Read accepted compact price periods for only the already-safe offer universe."""

    product_ids = _source_product_ids(source_record_ids)
    scope_set = set(scope.locations)
    found: dict[str, list[MartHistoricalState]] = {source_id: [] for source_id in source_record_ids}
    ids = sorted(product_ids)
    as_of_text = as_of_utc.isoformat().replace("+00:00", "Z")
    for start in range(0, len(ids), 400):
        chunk = ids[start : start + 400]
        placeholders = ",".join("?" for _ in chunk)
        rows = backend.query(
            f"""
            SELECT product_id,supermarket_id,location_id,current_price_minor,
                   reported_regular_price_minor,is_promotion,valid_from_utc
            FROM price_history
            WHERE product_id IN ({placeholders})
              AND current_price_minor IS NOT NULL
              AND current_price_minor > 0
              AND julianday(valid_from_utc)<=julianday(?)
            ORDER BY product_id,location_id,valid_from_utc
            """,
            (*chunk, as_of_text),
        )
        for row in rows:
            (
                product_id,
                supermarket_id,
                location_id,
                current_price,
                regular_price,
                is_promotion,
                observed_at,
            ) = row
            if type(product_id) is not int or product_id not in product_ids:
                raise ExportError("rpi_history_product_invalid")
            if (supermarket_id, location_id) not in scope_set:
                continue
            if (
                type(current_price) is not int
                or current_price <= 0
                or (regular_price is not None and type(regular_price) is not int)
                or type(is_promotion) is not int
                or is_promotion not in {0, 1}
            ):
                raise ExportError("rpi_history_state_invalid")
            source_record_id = product_ids[product_id]
            found[source_record_id].append(
                MartHistoricalState(
                    source_record_id=source_record_id,
                    current_price_minor=current_price,
                    reported_regular_price_minor=regular_price,
                    is_promotion=bool(is_promotion),
                    observed_at_utc=_parse_utc(observed_at, "rpi_history_timestamp_invalid"),
                )
            )
    if any(not rows for rows in found.values()):
        raise ExportError("rpi_history_states_missing")
    return tuple(
        row
        for source_record_id in sorted(found)
        for row in sorted(found[source_record_id], key=lambda item: item.observed_at_utc)
    )


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fields, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def export_rpi_marts(
    backend: QueryBackend,
    scope: ComparisonScope,
    output_directory: Path,
    *,
    as_of_utc: datetime,
    freshness_window: timedelta,
    require_products: bool = False,
) -> dict[str, object]:
    records = fetch_products(backend, scope)
    observations = fetch_current_observations(backend, scope)
    homologation = homologate_products(records)
    analytics = analyze_current_prices(homologation, observations, scope)
    freshness = fetch_freshness(
        backend,
        scope,
        as_of_utc=as_of_utc,
        freshness_window=freshness_window,
    )
    competition = analyze_competition(
        analytics,
        assess_comparable_market_window(freshness),
    )
    if require_products and not analytics.products:
        raise ExportError("rpi_mart_has_no_safe_products")

    profile_by_id = {profile.record.source_record_id: profile for profile in homologation.profiles}
    source_ids = tuple(
        offer.source_record_id for product in analytics.products for offer in product.offers
    )
    descriptors = tuple(
        MartOfferDescriptor(
            source_record_id=source_id,
            product_name=profile_by_id[source_id].record.source_name,
            category=(
                profile_by_id[source_id].taxonomy.category
                or profile_by_id[source_id].record.source_category
            ),
            product_type=profile_by_id[source_id].taxonomy.product_type,
            brand=profile_by_id[source_id].record.source_brand,
            variant=None,
            presentation=profile_by_id[source_id].record.source_presentation,
        )
        for source_id in source_ids
    )
    states = fetch_commercial_states(backend, source_ids, scope)
    history = fetch_historical_states(
        backend,
        source_ids,
        scope,
        as_of_utc=as_of_utc,
    )
    marts = build_rpi_data_marts(
        analytics,
        competition,
        descriptors,
        states,
        freshness,
        historical_states=history,
    )

    output_directory.mkdir(parents=True, exist_ok=True)
    files: dict[str, object] = {
        "business-mart.json": marts.business,
        "consumer-mart.json": marts.consumer,
    }
    for filename, document in files.items():
        _atomic_json(output_directory / filename, document)
    dimension_fields = {
        "dim_product": ["canonical_product_id", "canonical_gtin"],
        "dim_retailer": ["supermarket_id"],
        "dim_location": ["supermarket_id", "location_id"],
        "dim_category": ["category"],
        "dim_brand": ["brand"],
    }
    fact_identity_fields = [
        "canonical_product_id", "canonical_gtin", "source_product_id",
        "supermarket_id", "location_id", "category", "product_type",
        "product_name", "brand", "variant", "presentation",
    ]
    fact_fields = {
        "fact_current_comparison": [
            *fact_identity_fields, "current_price", "reported_regular_price",
            "is_promotion", "availability", "observed_at", "last_successful_run",
            "source_last_successful_at", "data_age_hours", "freshness_status", "rank",
            "pci", "market_min", "market_max", "market_mean", "market_median",
            "spread_abs", "spread_pct",
        ],
        "fact_price_history": [
            *fact_identity_fields, "period_start", "current_price",
            "reported_regular_price", "is_promotion", "previous_price", "change_abs",
            "change_pct", "direction", "is_current", "source_last_successful_at",
            "freshness_status",
        ],
        "fact_promotion_analysis": [
            *fact_identity_fields, "as_of", "history_observation_count", "current_price",
            "previous_price", "reported_regular_price", "source_reports_promotion",
            "historical_price_reduction", "source_discount_depth_pct",
            "current_vs_previous_pct", "current_vs_average_30d_pct",
            "current_vs_average_90d_pct", "current_vs_minimum_90d_pct",
            "promotion_duration_days", "promotion_event_count", "promotion_share_pct",
            "historical_position", "source_last_successful_at", "freshness_status",
        ],
        "fact_basket_cost": [
            "supermarket_id", "location_id", "total", "product_count", "is_complete",
        ],
        "fact_metric_coverage": [
            "comparable_count", "valid_price_count", "excluded_count", "coverage_pct",
            "as_of", "freshness_window_hours",
        ],
    }
    for table, rows in marts.business["dimensions"].items():
        _write_csv(output_directory / "business" / f"{table}.csv", rows, dimension_fields[table])
    for table, rows in marts.business["facts"].items():
        _write_csv(output_directory / "business" / f"{table}.csv", rows, fact_fields[table])

    generated_files = sorted(path for path in output_directory.rglob("*") if path.is_file())
    hashes = {
        str(path.relative_to(output_directory)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in generated_files
    }
    manifest = {
        "schema": "rpi-marts-manifest/v1",
        "generated_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "as_of": as_of_utc.isoformat().replace("+00:00", "Z"),
        "source_backend": backend.kind,
        "scope": [
            {"supermarket_id": supermarket, "location_id": location}
            for supermarket, location in scope.locations
        ],
        "comparison_status": competition.comparison_status.value,
        "business_schema": marts.business["schema"],
        "consumer_schema": marts.consumer["schema"],
        "safe_products": len(analytics.products),
        "safe_offers": len(source_ids),
        "files_sha256": hashes,
    }
    _atomic_json(output_directory / "manifest.json", manifest)
    return manifest


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    source = result.add_mutually_exclusive_group(required=True)
    source.add_argument("--sqlite", type=Path)
    source.add_argument("--turso", action="store_true")
    result.add_argument("--scope", action="append", required=True)
    result.add_argument("--output-directory", type=Path, required=True)
    result.add_argument("--as-of-utc")
    result.add_argument("--freshness-hours", type=int, default=48)
    result.add_argument("--require-products", action="store_true")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.freshness_hours <= 0:
        raise ExportError("rpi_freshness_hours_invalid")
    as_of = (
        datetime.now(UTC)
        if args.as_of_utc is None
        else _parse_utc(args.as_of_utc, "rpi_as_of_invalid")
    )
    scope = parse_scope(args.scope)
    if args.sqlite is not None:
        if not args.sqlite.is_file():
            raise ExportError("sqlite_file_missing")
        backend = SQLiteBackend(args.sqlite)
    else:
        backend = TursoBackend(
            os.environ.get("TURSO_DATABASE_URL", ""),
            os.environ.get("TURSO_AUTH_TOKEN", ""),
        )
    manifest = export_rpi_marts(
        backend,
        scope,
        args.output_directory,
        as_of_utc=as_of,
        freshness_window=timedelta(hours=args.freshness_hours),
        require_products=args.require_products,
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
