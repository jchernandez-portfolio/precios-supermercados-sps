from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from precios_supermercados.product_homologation import SourceProductRecord
from precios_supermercados.product_homologation_persistence import (
    LEGACY_PROFILE_COLUMNS,
    PROFILE_COLUMNS,
    TABLE_NAME,
    ProductHomologationPersistenceError,
    build_homologation_rows,
    ensure_sqlite_schema,
    persist_sqlite_rows,
    records_from_product_rows,
)


def source(
    product_id: int,
    supermarket: str,
    name: str,
    *,
    brand: str | None = None,
    presentation: str | None = None,
    category: str | None = None,
    ean: str | None = None,
) -> tuple[int, SourceProductRecord]:
    return (
        product_id,
        SourceProductRecord(
            source_record_id=f"{supermarket}:{product_id}",
            supermarket_id=supermarket,
            source_name=name,
            source_brand=brand,
            source_presentation=presentation,
            source_category=category,
            barcode=ean,
        ),
    )


def create_base_db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(
        """
        CREATE TABLE supermarkets (
            supermarket_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            country_code TEXT NOT NULL
        ) STRICT;
        CREATE TABLE products (
            product_id INTEGER PRIMARY KEY,
            supermarket_id TEXT NOT NULL,
            source_key_type TEXT NOT NULL,
            source_key TEXT NOT NULL,
            source_catalog_product_id TEXT NOT NULL,
            source_item_id TEXT NOT NULL,
            reference TEXT,
            ean TEXT,
            name TEXT NOT NULL,
            brand TEXT,
            presentation TEXT,
            category TEXT,
            FOREIGN KEY (supermarket_id) REFERENCES supermarkets(supermarket_id),
            UNIQUE (product_id, supermarket_id)
        ) STRICT;
        CREATE TABLE price_history (
            product_id INTEGER NOT NULL,
            supermarket_id TEXT NOT NULL,
            location_id TEXT NOT NULL,
            current_price_minor INTEGER,
            valid_from_utc TEXT NOT NULL
        ) STRICT;
        """
    )
    for supermarket in ("la_colonia", "walmart", "pricesmart"):
        con.execute(
            "INSERT INTO supermarkets VALUES(?,?,?)",
            (supermarket, supermarket, "HN"),
        )
    return con


def insert_product(
    con: sqlite3.Connection,
    product_id: int,
    supermarket: str,
    name: str,
    *,
    brand: str | None = None,
    presentation: str | None = None,
    category: str | None = None,
    ean: str | None = None,
) -> None:
    con.execute(
        """
        INSERT INTO products (
            product_id,supermarket_id,source_key_type,source_key,
            source_catalog_product_id,source_item_id,reference,ean,name,brand,
            presentation,category
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            product_id,
            supermarket,
            "sku",
            f"sku-{product_id}",
            f"catalog-{product_id}",
            f"item-{product_id}",
            None,
            ean,
            name,
            brand,
            presentation,
            category,
        ),
    )


def fetch_source_rows(con: sqlite3.Connection) -> tuple[tuple[int, SourceProductRecord], ...]:
    return records_from_product_rows(
        con.execute(
            "SELECT product_id,supermarket_id,name,brand,presentation,category,ean "
            "FROM products ORDER BY product_id"
        ).fetchall()
    )


def test_schema_is_separate_from_products_and_price_history(tmp_path: Path) -> None:
    con = create_base_db(tmp_path / "mvp.sqlite")
    try:
        ensure_sqlite_schema(con)
        columns = {
            row[1]
            for row in con.execute(f"PRAGMA table_info({TABLE_NAME})").fetchall()
        }
        assert {
            "product_id",
            "canonical_product_id",
            "category",
            "subcategory",
            "product_type",
            "comparison_status",
            "normalization_version",
        } <= columns
        assert "product_type" not in {
            row[1] for row in con.execute("PRAGMA table_info(price_history)").fetchall()
        }
        assert con.execute("SELECT COUNT(*) FROM price_history").fetchone() == (0,)
    finally:
        con.close()


def test_legacy_profile_schema_migrates_without_rewriting_source_tables(tmp_path: Path) -> None:
    con = create_base_db(tmp_path / "legacy.sqlite")
    try:
        definitions = [
            f"{column} {'INTEGER PRIMARY KEY' if column == 'product_id' else 'TEXT'}"
            for column in LEGACY_PROFILE_COLUMNS
        ]
        con.execute(f"CREATE TABLE {TABLE_NAME} ({','.join(definitions)})")
        ensure_sqlite_schema(con)

        columns = tuple(
            row[1] for row in con.execute(f"PRAGMA table_info({TABLE_NAME})")
        )
        assert columns == PROFILE_COLUMNS
        assert con.execute("SELECT COUNT(*) FROM products").fetchone() == (0,)
        assert con.execute("SELECT COUNT(*) FROM price_history").fetchone() == (0,)
    finally:
        con.close()


def test_exact_gtin_group_persists_ready_and_unmapped_states() -> None:
    rows = build_homologation_rows(
        (
            source(
                1,
                "la_colonia",
                "Suavizante Downy Pureza 800 ml",
                brand="Downy",
                presentation="800 ml",
                ean="7590002040003",
            ),
            source(
                2,
                "walmart",
                "Downy Suavizante Pureza 800 ML",
                brand="Downy",
                presentation="800 ml",
                ean="07590002040003",
            ),
            source(
                3,
                "pricesmart",
                "Arroz Progreso Blanco 25 lb",
                brand="Progreso",
                presentation="25 lb",
            ),
        ),
        updated_at_utc="2026-09-04T20:00:00Z",
    )

    assert [row.comparison_status for row in rows] == ["ready", "ready", "unmapped"]
    assert rows[0].canonical_product_id == rows[1].canonical_product_id
    assert rows[2].canonical_product_id is None
    assert rows[0].product_type == "Suavizante"


def test_valid_gtin_seen_in_one_supermarket_is_single_source() -> None:
    rows = build_homologation_rows(
        (
            source(
                1,
                "la_colonia",
                "Suavizante Downy Pureza 800 ml",
                brand="Downy",
                presentation="800 ml",
                ean="7590002040003",
            ),
        ),
        updated_at_utc="2026-09-04T20:00:00Z",
    )
    assert rows[0].comparison_status == "single_source"
    assert rows[0].canonical_gtin == "07590002040003"


def test_v23_persists_raw_and_canonical_brand_and_presentation_fields() -> None:
    rows = build_homologation_rows(
        (
            source(
                1,
                "la_colonia",
                "Bonovo Huevo G 30 und",
                brand="RMS",
                presentation="30Un",
            ),
        ),
        updated_at_utc="2026-09-04T20:00:00Z",
    )

    row = rows[0]
    assert row.normalization_version == "product-homologation-v2.5"
    assert row.raw_brand == "RMS"
    assert row.source_brand_role == "retailer_placeholder"
    assert row.normalized_brand == "bonovo"
    assert row.brand_resolution_source == "name_known_brand"
    assert row.raw_presentation == "30Un"
    assert row.normalized_quantity == "30"
    assert row.normalized_unit == "unit"
    assert row.normalized_pack_count == 1
    assert row.canonical_total == "30"
    assert row.display_presentation == "30 unidades"


def test_review_candidate_without_gtin_remains_unmapped_in_persistence() -> None:
    rows = build_homologation_rows(
        (
            source(
                1,
                "colonial",
                "Nutri Yema Claras De Huevo 554gr",
                brand="Nutri Yema",
                presentation="554gr",
            ),
            source(
                2,
                "comisariato_los_andes",
                "Claras de huevo liquidas Nutri Yema doy pack 1.2 lb",
                brand="COMANDES",
                presentation="1.2 lb",
            ),
        ),
        updated_at_utc="2026-09-04T20:00:00Z",
    )

    assert {row.comparison_status for row in rows} == {"unmapped"}
    assert all(row.canonical_product_id is None for row in rows)


def test_same_gtin_with_presentation_conflict_persists_review_reason() -> None:
    rows = build_homologation_rows(
        (
            source(
                1,
                "la_colonia",
                "Maiz El Migo Dulce En Grano 240 g",
                brand="El Migo",
                presentation="240 g",
                ean="012656001065",
            ),
            source(
                2,
                "walmart",
                "Maíz Dulce En Granos El Migo 148 g",
                brand="El Migo",
                presentation="148 g",
                ean="012656001065",
            ),
        ),
        updated_at_utc="2026-09-04T20:00:00Z",
    )
    assert {row.comparison_status for row in rows} == {"review_required"}
    for row in rows:
        assert "cross_source_presentation_conflict" in json.loads(
            row.conflict_reasons_json
        )


def test_full_backfill_is_idempotent_and_does_not_touch_price_history(tmp_path: Path) -> None:
    con = create_base_db(tmp_path / "mvp.sqlite")
    try:
        insert_product(
            con,
            1,
            "la_colonia",
            "Suavizante Downy Pureza 800 ml",
            brand="Downy",
            presentation="800 ml",
            ean="7590002040003",
        )
        insert_product(
            con,
            2,
            "walmart",
            "Downy Suavizante Pureza 800 ML",
            brand="Downy",
            presentation="800 ml",
            ean="07590002040003",
        )
        con.execute(
            "INSERT INTO price_history VALUES(?,?,?,?,?)",
            (1, "la_colonia", "la_colonia_sps", 10000, "2026-09-04T00:00:00Z"),
        )
        before_history = con.execute("SELECT * FROM price_history").fetchall()

        first_rows = build_homologation_rows(
            fetch_source_rows(con), updated_at_utc="2026-09-04T20:00:00Z"
        )
        first = persist_sqlite_rows(con, first_rows)
        con.commit()
        second_rows = build_homologation_rows(
            fetch_source_rows(con), updated_at_utc="2026-09-04T21:00:00Z"
        )
        second = persist_sqlite_rows(con, second_rows)
        con.commit()

        assert first == {
            "processed": 2,
            "inserted": 2,
            "updated": 0,
            "unchanged": 0,
            "stored": 2,
        }
        assert second == {
            "processed": 2,
            "inserted": 0,
            "updated": 0,
            "unchanged": 2,
            "stored": 2,
        }
        assert con.execute("SELECT * FROM price_history").fetchall() == before_history
        assert con.execute(
            f"SELECT DISTINCT updated_at_utc FROM {TABLE_NAME}"
        ).fetchall() == [("2026-09-04T20:00:00Z",)]
    finally:
        con.close()


def test_metadata_change_updates_profile_without_commercial_history_change(tmp_path: Path) -> None:
    con = create_base_db(tmp_path / "mvp.sqlite")
    try:
        insert_product(
            con,
            1,
            "la_colonia",
            "Arroz Progreso Blanco 1 lb",
            brand="Progreso",
            presentation="1 lb",
        )
        rows = build_homologation_rows(
            fetch_source_rows(con), updated_at_utc="2026-09-04T20:00:00Z"
        )
        persist_sqlite_rows(con, rows)
        con.commit()

        con.execute("UPDATE products SET name=? WHERE product_id=1", ("Arroz Progreso Blanco Premium 1 lb",))
        before_history = con.execute("SELECT * FROM price_history").fetchall()
        changed = build_homologation_rows(
            fetch_source_rows(con), updated_at_utc="2026-09-04T21:00:00Z"
        )
        result = persist_sqlite_rows(con, changed)
        con.commit()

        assert result["updated"] == 1
        assert con.execute("SELECT * FROM price_history").fetchall() == before_history
        stored_name, stored_time = con.execute(
            f"SELECT normalized_name,updated_at_utc FROM {TABLE_NAME} WHERE product_id=1"
        ).fetchone()
        assert stored_name == "arroz progreso blanco premium 1 lb"
        assert stored_time == "2026-09-04T21:00:00Z"
    finally:
        con.close()


def test_partial_initial_backfill_fails_closed(tmp_path: Path) -> None:
    con = create_base_db(tmp_path / "mvp.sqlite")
    try:
        insert_product(con, 1, "la_colonia", "Arroz Uno 1 lb")
        insert_product(con, 2, "walmart", "Arroz Dos 1 lb")
        rows = build_homologation_rows(
            (source(1, "la_colonia", "Arroz Uno 1 lb"),),
            updated_at_utc="2026-09-04T20:00:00Z",
        )
        with pytest.raises(
            ProductHomologationPersistenceError,
            match="profile_product_count_mismatch",
        ):
            persist_sqlite_rows(con, rows)
        con.rollback()
    finally:
        con.close()


def test_records_from_product_rows_rejects_bad_shape() -> None:
    with pytest.raises(
        ProductHomologationPersistenceError,
        match="product_row_shape_invalid",
    ):
        records_from_product_rows(((1, "la_colonia"),))


def test_partial_recalculation_fails_even_with_preexisting_full_profiles(tmp_path: Path) -> None:
    con = create_base_db(tmp_path / "mvp.sqlite")
    try:
        insert_product(con, 1, "la_colonia", "Arroz Uno 1 lb")
        insert_product(con, 2, "walmart", "Arroz Dos 1 lb")
        full = build_homologation_rows(
            fetch_source_rows(con), updated_at_utc="2026-09-04T20:00:00Z"
        )
        persist_sqlite_rows(con, full)
        con.commit()

        partial = build_homologation_rows(
            (source(1, "la_colonia", "Arroz Uno 1 lb"),),
            updated_at_utc="2026-09-04T21:00:00Z",
        )
        with pytest.raises(
            ProductHomologationPersistenceError,
            match="profile_product_count_mismatch",
        ):
            persist_sqlite_rows(con, partial)
        assert con.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}").fetchone() == (2,)
    finally:
        con.close()
