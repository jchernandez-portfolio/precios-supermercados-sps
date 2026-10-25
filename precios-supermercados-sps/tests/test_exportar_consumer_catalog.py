from __future__ import annotations

import gzip
import importlib.util
import json
import math
import sqlite3
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "exportar_consumer_catalog.py"
SPEC = importlib.util.spec_from_file_location("exportar_consumer_catalog", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
AS_OF = datetime(2026, 9, 9, 12, tzinfo=UTC)
SCOPE = MODULE.parse_scope(tuple(f"{supermarket}={location}" for supermarket, location in MODULE.EXPECTED_SCOPE))


def build_db(path: Path) -> None:
    products = (
        (1, "la_colonia", "Leche entera Sula 1 L", "Sula", "1 L", "Lácteos"),
        (2, "walmart", "Leche Sula entera 1 litro", "Sula", "1 L", "Lácteos"),
        (3, "colonial", "Arroz especial 5 lb", "Progreso", "5 lb", "Granos"),
        (4, "pricesmart", "Café clásico 500 g", "Oro", "500 g", "Bebidas"),
        (5, "comisariato_los_andes", "Café intenso 500 g", "Oro", "500 g", "Bebidas"),
        (6, "la_colonia", "Artículo sin clasificar", None, None, None),
        (7, "pricesmart", "Agua purificada 1 L", "Fuente", "1 L", "Bebidas"),
        (8, "comisariato_los_andes", "Agua Fuente 1 L", "Fuente", "1 L", "Bebidas"),
    )
    locations = {
        "la_colonia": "la_colonia_sps",
        "colonial": "colonial_sps",
        "walmart": "walmart_sps",
        "pricesmart": "pricesmart_sps",
        "comisariato_los_andes": "comisariato_los_andes_sps",
    }
    prices = {1: 1000, 2: 1200, 3: 5500, 4: 3000, 5: 2900, 6: None, 7: 1800, 8: 1800}
    profiles = (
        (1, "la_colonia", "gtin:leche", "Alimentos", "Leche", "volume_ml", "1000", "confirmed", "ready", MODULE.NORMALIZATION_VERSION),
        (2, "walmart", "gtin:leche", "Alimentos", "Leche", "volume_ml", "1000", "confirmed", "ready", MODULE.NORMALIZATION_VERSION),
        (3, "colonial", "gtin:arroz", "Alimentos", "Arroz", "mass_g", "2268", "source_only", "single_source", MODULE.NORMALIZATION_VERSION),
        (4, "pricesmart", "gtin:cafe", "Bebidas", "Café", "mass_g", "500", "confirmed", "review_required", MODULE.NORMALIZATION_VERSION),
        (5, "comisariato_los_andes", "gtin:cafe", "Bebidas", "Café", "mass_g", "500", "confirmed", "review_required", MODULE.NORMALIZATION_VERSION),
        (6, "la_colonia", None, None, None, None, None, "missing", "unmapped", MODULE.NORMALIZATION_VERSION),
        (7, "pricesmart", "gtin:agua", "Bebidas", "Agua", "volume_ml", "1000", "name_only", "ready", MODULE.NORMALIZATION_VERSION),
        (8, "comisariato_los_andes", "gtin:agua", "Bebidas", "Agua", "volume_ml", "1000", "name_only", "ready", MODULE.NORMALIZATION_VERSION),
    )
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE products (
                product_id INTEGER PRIMARY KEY, supermarket_id TEXT NOT NULL,
                name TEXT NOT NULL, brand TEXT, presentation TEXT, category TEXT
            );
            CREATE TABLE price_history (
                product_id INTEGER NOT NULL, supermarket_id TEXT NOT NULL,
                location_id TEXT NOT NULL, current_price_minor INTEGER,
                reported_regular_price_minor INTEGER, is_promotion INTEGER,
                availability TEXT NOT NULL, valid_from_utc TEXT NOT NULL,
                valid_to_utc TEXT
            );
            CREATE TABLE scrape_runs (
                scrape_run_id TEXT PRIMARY KEY, supermarket_id TEXT NOT NULL,
                location_id TEXT NOT NULL, observed_at_utc TEXT NOT NULL,
                run_status TEXT NOT NULL
            );
            CREATE TABLE product_homologation_profiles (
                product_id INTEGER PRIMARY KEY, supermarket_id TEXT NOT NULL,
                canonical_product_id TEXT, category TEXT,
                product_type TEXT, presentation_dimension TEXT,
                presentation_total_base TEXT, presentation_status TEXT NOT NULL,
                comparison_status TEXT NOT NULL, normalization_version TEXT NOT NULL,
                normalized_brand TEXT, display_presentation TEXT
            );
            """
        )
        connection.executemany("INSERT INTO products VALUES(?,?,?,?,?,?)", products)
        connection.executemany(
            "INSERT INTO product_homologation_profiles VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                (*profile, products[index][3].casefold() if products[index][3] else None, products[index][4])
                for index, profile in enumerate(profiles)
            ),
        )
        connection.executemany(
            "INSERT INTO price_history VALUES(?,?,?,?,?,?,?,?,?)",
            (
                (
                    product_id,
                    supermarket_id,
                    locations[supermarket_id],
                    prices[product_id],
                    1300 if product_id == 1 else None,
                    1 if product_id == 1 else None if product_id == 4 else 0,
                    "out_of_stock" if product_id == 6 else "in_stock",
                    "2026-09-09T10:00:00Z",
                    None,
                )
                for product_id, supermarket_id, *_ in products
            ),
        )
        connection.executemany(
            "INSERT INTO price_history VALUES(?,?,?,?,?,?,?,?,?)",
            (
                (1, "la_colonia", "la_colonia_sps", 1200, None, 0, "in_stock", "2026-05-01T10:00:00Z", "2026-08-20T10:00:00Z"),
                (1, "la_colonia", "la_colonia_sps", 1100, None, 0, "in_stock", "2026-08-20T10:00:00Z", "2026-09-09T10:00:00Z"),
            ),
        )
        connection.executemany(
            "INSERT INTO scrape_runs VALUES(?,?,?,?,?)",
            (
                (f"{supermarket}-ok", supermarket, location, "2026-09-09T10:00:00Z", "success")
                for supermarket, location in locations.items()
            ),
        )


def export(path: Path, output: Path) -> dict[str, object]:
    backend = MODULE.SQLiteBackend(path)
    try:
        return MODULE.export_consumer_catalog(
            backend,
            SCOPE,
            output,
            as_of_utc=AS_OF,
            freshness_window=timedelta(hours=24),
            require_products=True,
        )
    finally:
        backend.close()


def all_rows(output: Path, manifest: dict[str, object]) -> list[dict[str, object]]:
    result = []
    for item in manifest["files"]:
        if item["path"].startswith("catalog/"):
            result.extend(json.loads((output / item["path"]).read_text())["rows"])
    return result


def test_exports_partitioned_visible_catalog_with_safe_comparability(tmp_path: Path) -> None:
    database = tmp_path / "source.sqlite"
    output = tmp_path / "public"
    build_db(database)
    before = database.read_bytes()
    manifest = export(database, output)

    assert database.read_bytes() == before
    assert manifest["schema"] == "rpi-consumer-catalog-manifest/v3"
    assert manifest["catalog_schema"] == "rpi-consumer-catalog/v3"
    assert manifest["analysis_file"] == "analysis-sps.json"
    # Compra Inteligente no publica filas sin ninguna oferta comprable.
    assert manifest["visible_rows"] == 5
    assert manifest["source_offers"] == 7
    assert set(manifest["retailer_offer_counts"]) == {item[0] for item in MODULE.EXPECTED_SCOPE}
    assert all(count > 0 for count in manifest["retailer_offer_counts"].values())
    assert manifest["comparability_counts"] == {
        "comparable": 2,
        "individual": 2,
        "single_source": 1,
    }
    assert manifest["offers_with_historical_summary"] == 7
    assert manifest["public_boundary"] == {
        "direct_turso_reads": 0,
        "contains_business_mart": False,
        "contains_raw": False,
        "contains_review_queue": False,
    }
    assert manifest["initial_payload"]["request_count"] == 2
    assert manifest["partition_count"] >= 1
    assert all(item["bytes"] > 0 and item["gzip_bytes"] > 0 for item in manifest["files"])

    rows = all_rows(output, manifest)
    assert all(row["product_name"] != "Artículo sin clasificar" for row in rows)
    milk = next(row for row in rows if row["canonical_product_id"] == "gtin:leche")
    assert milk["comparability"] == "comparable"
    assert [offer["relative_price_state"] for offer in milk["offers"]] == ["best", "highest"]
    assert milk["offers"][0]["current_price"] == "10.00"
    assert milk["offers"][0]["reported_regular_price"] == "13.00"
    assert milk["offers"][0]["is_promotion"] is True
    history = milk["offers"][0]["historical_summary"]
    assert history["previous_price"] == "11.00"
    assert history["historical_position"] == "historically_low"
    assert history["windows"]["30d"] == {
        "status": "available", "observation_count": 3,
        "average": "11.00", "minimum": "10.00", "maximum": "12.00",
    }
    assert milk["offers"][1]["historical_summary"]["historical_position"] == "insufficient_history"
    coffee = next(row for row in rows if row["product_name"] == "Café clásico 500 g")
    assert coffee["offers"][0]["is_promotion"] is None

    water = next(row for row in rows if row["canonical_product_id"] == "gtin:agua")
    assert {offer["relative_price_state"] for offer in water["offers"]} == {"equivalent"}
    review_rows = [row for row in rows if row["product_type"] == "Café"]
    assert len(review_rows) == 2
    assert all(row["comparability"] == "individual" and len(row["offers"]) == 1 for row in review_rows)
    assert len({row["row_id"] for row in rows}) == len(rows)
    assert "review_required" not in json.dumps(manifest) + json.dumps(rows)

    analysis = json.loads((output / "analysis-sps.json").read_text())
    assert analysis["schema"] == "rpi-consumer-analysis/v1"
    assert analysis["summary"] == {
        "visible_products": 5,
        "comparable_products": 2,
        "comparable_products_with_price_spread": 1,
        "active_promotions": 1,
        "observed_unit_savings": "2.00",
        "price_movements": {
            "decreased": 1,
            "increased": 0,
            "unchanged": 0,
            "insufficient_history": 6,
        },
        "recent_low_opportunities": 1,
    }
    assert analysis["opportunities"]["price_drops"][0]["product_name"] == "Leche entera Sula 1 L"
    assert analysis["opportunities"]["promotions"][0]["discount_pct"] == "23.08"
    assert analysis["methodology"]["identity"] == "only_persisted_ready_cross_retailer_identity"
    assert "raw_brand" not in json.dumps(analysis)


def test_facets_exclude_unshoppable_rows_and_type_indexes_point_to_partitions(tmp_path: Path) -> None:
    database = tmp_path / "source.sqlite"
    output = tmp_path / "public"
    build_db(database)
    manifest = export(database, output)
    facets = json.loads((output / "facets-sps.json").read_text())
    assert facets["schema"] == "rpi-consumer-facets/v3"
    assert facets["coverage"]["category"] == {"known": 5, "unknown": 0}
    assert all(item["value"] is not None for item in facets["categories"])
    paths = [
        product_type["index_path"]
        for category in facets["categories"]
        if category["navigation"] == "facets"
        for product_type in category["product_types"]
    ]
    indexes = [json.loads((output / path).read_text()) for path in paths]
    assert sum(index["row_count"] for index in indexes) == manifest["visible_rows"]
    assert all((output / row["partition"]).is_file() for index in indexes for row in index["rows"])
    assert all(
        len({row["partition"] for row in index["rows"]})
        == math.ceil(index["row_count"] / MODULE.MAX_PARTITION_ROWS)
        for index in indexes
    )
    assert "index-sps.json" not in {item["path"] for item in manifest["files"]}
    assert manifest["initial_files"] == ["facets-sps.json"]
    facets_bytes = (output / "facets-sps.json").read_bytes()
    assert manifest["initial_payload"] == {
        "bytes": len(facets_bytes),
        "gzip_bytes": len(gzip.compress(facets_bytes, mtime=0)),
        "request_count": 2,
    }
    hashes = {item["path"]: item["sha256"] for item in manifest["files"]}
    for relative, expected in hashes.items():
        assert MODULE.hashlib.sha256((output / relative).read_bytes()).hexdigest() == expected


def test_rejects_any_scope_other_than_five_productive_sps_contexts(tmp_path: Path) -> None:
    database = tmp_path / "source.sqlite"
    build_db(database)
    backend = MODULE.SQLiteBackend(database)
    try:
        with pytest.raises(MODULE.ExportError, match="consumer_catalog_sps_scope_invalid"):
            MODULE.export_consumer_catalog(
                backend,
                MODULE.parse_scope(("la_colonia=la_colonia_sps", "walmart=walmart_sps")),
                tmp_path / "out",
                as_of_utc=AS_OF,
                freshness_window=timedelta(hours=24),
            )
    finally:
        backend.close()


def test_stale_source_suppresses_ranking_without_hiding_offer(tmp_path: Path) -> None:
    database = tmp_path / "source.sqlite"
    output = tmp_path / "public"
    build_db(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE scrape_runs SET observed_at_utc='2026-09-01T10:00:00Z' WHERE supermarket_id='walmart'"
        )
    manifest = export(database, output)
    milk = next(row for row in all_rows(output, manifest) if row["canonical_product_id"] == "gtin:leche")
    assert {offer["relative_price_state"] for offer in milk["offers"]} == {"neutral"}
    assert next(offer for offer in milk["offers"] if offer["supermarket_id"] == "walmart")["freshness_status"] == "STALE"


def make_offer(number: int, supermarket: str, price: int, *, canonical: str = "gtin:test"):
    location = dict(MODULE.EXPECTED_SCOPE)[supermarket]
    return MODULE.VisibleOffer(
        source_product_id=f"{supermarket}:{number}",
        supermarket_id=supermarket,
        location_id=location,
        product_name=f"Producto {number}",
        brand="Marca",
        presentation="1 L",
        current_price_minor=price,
        reported_regular_price_minor=None,
        is_promotion=False,
        availability="in_stock",
        observed_at="2026-09-09T10:00:00Z",
        canonical_product_id=canonical,
        category="Alimentos",
        product_type="Producto",
        presentation_dimension="volume_ml",
        presentation_total_base="1000",
        presentation_status="confirmed",
        comparison_status="ready",
    )


def test_relative_price_states_cover_intermediates_and_ties() -> None:
    supermarkets = ["la_colonia", "colonial", "walmart", "pricesmart"]
    fresh = {scope: "FRESH" for scope in MODULE.EXPECTED_SCOPE}

    offers = [make_offer(index, supermarket, price) for index, (supermarket, price) in enumerate(zip(supermarkets, (100, 200, 300, 300)), 1)]
    states = MODULE._relative_states("comparable", offers, fresh)
    assert list(states.values()) == ["best", "intermediate", "highest", "highest"]

    tied_best = [make_offer(index, supermarket, price) for index, (supermarket, price) in enumerate(zip(supermarkets[:3], (100, 100, 300)), 1)]
    assert list(MODULE._relative_states("comparable", tied_best, fresh).values()) == ["best", "best", "highest"]

    equal = [make_offer(index, supermarket, 100) for index, supermarket in enumerate(supermarkets[:3], 1)]
    assert set(MODULE._relative_states("comparable", equal, fresh).values()) == {"equivalent"}
    assert set(MODULE._relative_states("individual", offers, fresh).values()) == {"neutral"}
    assert MODULE._relative_states("comparable", offers[:1], fresh)[offers[0].source_product_id] == "neutral"


def test_ready_identity_with_same_retailer_collision_is_not_merged() -> None:
    first = make_offer(1, "walmart", 100)
    second = make_offer(2, "walmart", 110)
    groups = MODULE._identity_groups((first, second))
    assert len(groups) == 2
    assert all(mode == "individual" and len(group) == 1 for mode, group in groups)
