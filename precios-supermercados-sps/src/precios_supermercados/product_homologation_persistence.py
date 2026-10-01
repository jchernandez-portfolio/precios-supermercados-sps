"""Persistencia derivada para la homologación de productos.

La capa vive separada del histórico comercial. Puede recalcularse cuando mejoran
las reglas de taxonomía o presentación sin abrir/cerrar periodos de precio.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable

from .product_homologation import SourceProductRecord
from .product_identity_v2 import (
    IDENTITY_NORMALIZATION_VERSION,
    build_brand_lexicon,
    canonical_presentation_fields,
    homologate_products_v2,
    resolve_brand,
    source_brand_role,
)

NORMALIZATION_VERSION = IDENTITY_NORMALIZATION_VERSION
TABLE_NAME = "product_homologation_profiles"
COMPARISON_STATUSES = frozenset({"ready", "review_required", "single_source", "unmapped"})
PROFILE_COLUMNS = (
    "product_id",
    "supermarket_id",
    "normalized_name",
    "normalized_brand",
    "canonical_gtin",
    "canonical_product_id",
    "category",
    "subcategory",
    "product_type",
    "taxonomy_rule_id",
    "presentation_dimension",
    "presentation_total_base",
    "presentation_pack_count",
    "presentation_unit_amount_base",
    "presentation_status",
    "comparison_status",
    "conflict_reasons_json",
    "normalization_version",
    "profile_hash",
    "updated_at_utc",
    "raw_brand",
    "source_brand_role",
    "brand_resolution_source",
    "raw_presentation",
    "normalized_quantity",
    "normalized_unit",
    "normalized_pack_count",
    "canonical_total",
    "display_presentation",
)
LEGACY_PROFILE_COLUMNS = PROFILE_COLUMNS[:-9]
SQLITE_MIGRATION_STEPS = (
    f"ALTER TABLE {TABLE_NAME} ADD COLUMN raw_brand TEXT",
    f"ALTER TABLE {TABLE_NAME} ADD COLUMN source_brand_role TEXT NOT NULL DEFAULT 'unknown' "
    "CHECK (source_brand_role IN ('unknown','retailer_placeholder','retailer_reported_brand'))",
    f"ALTER TABLE {TABLE_NAME} ADD COLUMN brand_resolution_source TEXT NOT NULL DEFAULT 'missing' "
    "CHECK (brand_resolution_source IN ('source','name_known_brand','source_conflict','missing'))",
    f"ALTER TABLE {TABLE_NAME} ADD COLUMN raw_presentation TEXT",
    f"ALTER TABLE {TABLE_NAME} ADD COLUMN normalized_quantity TEXT",
    f"ALTER TABLE {TABLE_NAME} ADD COLUMN normalized_unit TEXT "
    "CHECK (normalized_unit IS NULL OR normalized_unit IN ('g','ml','unit','oz'))",
    f"ALTER TABLE {TABLE_NAME} ADD COLUMN normalized_pack_count INTEGER "
    "CHECK (normalized_pack_count IS NULL OR normalized_pack_count > 0)",
    f"ALTER TABLE {TABLE_NAME} ADD COLUMN canonical_total TEXT",
    f"ALTER TABLE {TABLE_NAME} ADD COLUMN display_presentation TEXT",
)


class ProductHomologationPersistenceError(ValueError):
    """El estado derivado no cumple el contrato persistente."""


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    normalized = value.normalize()
    rendered = format(normalized, "f")
    return "0" if rendered in {"", "-0"} else rendered


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class ProductHomologationRow:
    product_id: int
    supermarket_id: str
    normalized_name: str
    normalized_brand: str | None
    canonical_gtin: str | None
    canonical_product_id: str | None
    category: str | None
    subcategory: str | None
    product_type: str | None
    taxonomy_rule_id: str | None
    presentation_dimension: str | None
    presentation_total_base: str | None
    presentation_pack_count: int | None
    presentation_unit_amount_base: str | None
    presentation_status: str
    comparison_status: str
    conflict_reasons_json: str
    normalization_version: str
    profile_hash: str
    updated_at_utc: str
    raw_brand: str | None = None
    source_brand_role: str = "unknown"
    brand_resolution_source: str = "missing"
    raw_presentation: str | None = None
    normalized_quantity: str | None = None
    normalized_unit: str | None = None
    normalized_pack_count: int | None = None
    canonical_total: str | None = None
    display_presentation: str | None = None

    def __post_init__(self) -> None:
        if type(self.product_id) is not int or self.product_id <= 0:
            raise ProductHomologationPersistenceError("product_id_invalid")
        if not self.supermarket_id.strip() or not self.normalized_name.strip():
            raise ProductHomologationPersistenceError("required_text_missing")
        if self.comparison_status not in COMPARISON_STATUSES:
            raise ProductHomologationPersistenceError("comparison_status_invalid")
        try:
            reasons = json.loads(self.conflict_reasons_json)
        except json.JSONDecodeError as exc:
            raise ProductHomologationPersistenceError("conflict_reasons_invalid") from exc
        if not isinstance(reasons, list) or any(not isinstance(item, str) for item in reasons):
            raise ProductHomologationPersistenceError("conflict_reasons_invalid")
        if self.comparison_status == "review_required" and not reasons:
            raise ProductHomologationPersistenceError("review_requires_reason")
        if self.comparison_status != "review_required" and reasons:
            raise ProductHomologationPersistenceError("unexpected_conflict_reason")
        if (self.canonical_gtin is None) != (self.canonical_product_id is None):
            raise ProductHomologationPersistenceError("canonical_identity_partial")
        if self.comparison_status in {"ready", "review_required", "single_source"} and self.canonical_gtin is None:
            raise ProductHomologationPersistenceError("canonical_identity_required")
        if self.comparison_status == "unmapped" and self.canonical_gtin is not None:
            raise ProductHomologationPersistenceError("unmapped_with_gtin")
        if not self.normalization_version.strip() or len(self.profile_hash) != 64:
            raise ProductHomologationPersistenceError("version_or_hash_invalid")
        if not self.updated_at_utc.endswith("Z"):
            raise ProductHomologationPersistenceError("updated_at_invalid")
        if self.source_brand_role not in {
            "unknown",
            "retailer_placeholder",
            "retailer_reported_brand",
        }:
            raise ProductHomologationPersistenceError("source_brand_role_invalid")
        if self.brand_resolution_source not in {
            "source",
            "name_known_brand",
            "source_conflict",
            "missing",
        }:
            raise ProductHomologationPersistenceError("brand_resolution_source_invalid")
        if self.normalized_unit not in {None, "g", "ml", "unit", "oz"}:
            raise ProductHomologationPersistenceError("normalized_unit_invalid")


def _row_hash(payload: dict[str, object]) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def build_homologation_rows(
    products: Iterable[tuple[int, SourceProductRecord]],
    *,
    updated_at_utc: str | None = None,
    normalization_version: str = NORMALIZATION_VERSION,
) -> tuple[ProductHomologationRow, ...]:
    """Convierte productos fuente en filas derivadas deterministas."""

    entries = tuple(products)
    if not entries:
        return ()
    if len({product_id for product_id, _ in entries}) != len(entries):
        raise ProductHomologationPersistenceError("product_id_duplicate")
    if len({record.source_record_id for _, record in entries}) != len(entries):
        raise ProductHomologationPersistenceError("source_record_id_duplicate")

    timestamp = updated_at_utc or _utc_now()
    if not timestamp.endswith("Z"):
        raise ProductHomologationPersistenceError("updated_at_invalid")
    if not isinstance(normalization_version, str) or not normalization_version.strip():
        raise ProductHomologationPersistenceError("normalization_version_invalid")

    records = tuple(record for _, record in entries)
    result = homologate_products_v2(records)
    brand_lexicon = build_brand_lexicon(records)
    profile_by_source = {profile.record.source_record_id: profile for profile in result.profiles}
    group_by_source: dict[str, tuple[str, tuple[str, ...]]] = {}
    for group in result.exact_gtin_groups:
        for source_record_id in group.source_record_ids:
            group_by_source[source_record_id] = (
                group.comparison_status,
                group.conflict_reasons,
            )
        # Un miembro excluido no bloquea al resto del grupo: queda en revisión
        # con sus motivos y nunca entra al grupo comparable de su GTIN.
        for source_record_id, reasons in group.excluded_members:
            group_by_source[source_record_id] = (
                "review_required",
                tuple(sorted({"member_excluded_from_ready_group", *reasons})),
            )

    rows: list[ProductHomologationRow] = []
    for product_id, record in sorted(entries, key=lambda item: item[0]):
        profile = profile_by_source[record.source_record_id]
        if profile.canonical_gtin is None:
            comparison_status = "unmapped"
            conflict_reasons: tuple[str, ...] = ()
        elif record.source_record_id in group_by_source:
            comparison_status, conflict_reasons = group_by_source[record.source_record_id]
        else:
            comparison_status = "single_source"
            conflict_reasons = ()

        presentation = profile.presentation
        canonical_presentation = canonical_presentation_fields(record, profile.taxonomy)
        brand_resolution = resolve_brand(record, brand_lexicon=brand_lexicon)
        payload: dict[str, object] = {
            "product_id": product_id,
            "supermarket_id": record.supermarket_id,
            "normalized_name": profile.normalized_name,
            "normalized_brand": profile.normalized_brand,
            "canonical_gtin": profile.canonical_gtin,
            "canonical_product_id": profile.canonical_product_id,
            "category": profile.taxonomy.category,
            "subcategory": profile.taxonomy.subcategory,
            "product_type": profile.taxonomy.product_type,
            "taxonomy_rule_id": profile.taxonomy.rule_id,
            "presentation_dimension": None if presentation is None else presentation.dimension,
            "presentation_total_base": None if presentation is None else _decimal_text(presentation.total_base),
            "presentation_pack_count": None if presentation is None else presentation.pack_count,
            "presentation_unit_amount_base": None if presentation is None else _decimal_text(presentation.unit_amount_base),
            "presentation_status": profile.presentation_status,
            "comparison_status": comparison_status,
            "conflict_reasons_json": json.dumps(list(conflict_reasons), ensure_ascii=False, separators=(",", ":")),
            "normalization_version": normalization_version,
            "raw_brand": record.source_brand,
            "source_brand_role": source_brand_role(record.source_brand),
            "brand_resolution_source": brand_resolution.source,
            "raw_presentation": canonical_presentation.raw_presentation,
            "normalized_quantity": _decimal_text(canonical_presentation.normalized_quantity),
            "normalized_unit": canonical_presentation.normalized_unit,
            "normalized_pack_count": canonical_presentation.normalized_pack_count,
            "canonical_total": _decimal_text(canonical_presentation.canonical_total),
            "display_presentation": canonical_presentation.display_presentation,
        }
        rows.append(
            ProductHomologationRow(
                **payload,
                profile_hash=_row_hash(payload),
                updated_at_utc=timestamp,
            )
        )
    return tuple(rows)


SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    product_id INTEGER PRIMARY KEY,
    supermarket_id TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    normalized_brand TEXT,
    canonical_gtin TEXT,
    canonical_product_id TEXT,
    category TEXT,
    subcategory TEXT,
    product_type TEXT,
    taxonomy_rule_id TEXT,
    presentation_dimension TEXT CHECK (
        presentation_dimension IS NULL OR
        presentation_dimension IN ('mass_g','volume_ml','count','ounce')
    ),
    presentation_total_base TEXT,
    presentation_pack_count INTEGER CHECK (
        presentation_pack_count IS NULL OR presentation_pack_count > 0
    ),
    presentation_unit_amount_base TEXT,
    presentation_status TEXT NOT NULL CHECK (
        presentation_status IN (
            'confirmed','name_only','source_only','name_preferred_source_conflict',
            'ambiguous_multipack','conflict','missing'
        )
    ),
    comparison_status TEXT NOT NULL CHECK (
        comparison_status IN ('ready','review_required','single_source','unmapped')
    ),
    conflict_reasons_json TEXT NOT NULL,
    normalization_version TEXT NOT NULL,
    profile_hash TEXT NOT NULL CHECK (length(profile_hash) = 64),
    updated_at_utc TEXT NOT NULL,
    raw_brand TEXT,
    source_brand_role TEXT NOT NULL CHECK (
        source_brand_role IN ('unknown','retailer_placeholder','retailer_reported_brand')
    ),
    brand_resolution_source TEXT NOT NULL CHECK (
        brand_resolution_source IN ('source','name_known_brand','source_conflict','missing')
    ),
    raw_presentation TEXT,
    normalized_quantity TEXT,
    normalized_unit TEXT CHECK (
        normalized_unit IS NULL OR normalized_unit IN ('g','ml','unit','oz')
    ),
    normalized_pack_count INTEGER CHECK (
        normalized_pack_count IS NULL OR normalized_pack_count > 0
    ),
    canonical_total TEXT,
    display_presentation TEXT,
    FOREIGN KEY (product_id, supermarket_id)
        REFERENCES products(product_id, supermarket_id),
    CHECK ((canonical_gtin IS NULL) = (canonical_product_id IS NULL)),
    CHECK (
        (comparison_status = 'unmapped' AND canonical_gtin IS NULL)
        OR (comparison_status != 'unmapped' AND canonical_gtin IS NOT NULL)
    )
) STRICT;

CREATE INDEX IF NOT EXISTS idx_product_homologation_canonical
    ON {TABLE_NAME}(canonical_product_id)
    WHERE canonical_product_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_product_homologation_type
    ON {TABLE_NAME}(product_type)
    WHERE product_type IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_product_homologation_brand
    ON {TABLE_NAME}(normalized_brand)
    WHERE normalized_brand IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_product_homologation_comparison
    ON {TABLE_NAME}(comparison_status, canonical_product_id);
"""


UPSERT_SQL = f"""
INSERT INTO {TABLE_NAME} (
    product_id,supermarket_id,normalized_name,normalized_brand,canonical_gtin,
    canonical_product_id,category,subcategory,product_type,taxonomy_rule_id,
    presentation_dimension,presentation_total_base,presentation_pack_count,
    presentation_unit_amount_base,presentation_status,comparison_status,
    conflict_reasons_json,normalization_version,profile_hash,updated_at_utc,
    raw_brand,source_brand_role,brand_resolution_source,raw_presentation,
    normalized_quantity,normalized_unit,normalized_pack_count,canonical_total,
    display_presentation
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(product_id) DO UPDATE SET
    supermarket_id=excluded.supermarket_id,
    normalized_name=excluded.normalized_name,
    normalized_brand=excluded.normalized_brand,
    canonical_gtin=excluded.canonical_gtin,
    canonical_product_id=excluded.canonical_product_id,
    category=excluded.category,
    subcategory=excluded.subcategory,
    product_type=excluded.product_type,
    taxonomy_rule_id=excluded.taxonomy_rule_id,
    presentation_dimension=excluded.presentation_dimension,
    presentation_total_base=excluded.presentation_total_base,
    presentation_pack_count=excluded.presentation_pack_count,
    presentation_unit_amount_base=excluded.presentation_unit_amount_base,
    presentation_status=excluded.presentation_status,
    comparison_status=excluded.comparison_status,
    conflict_reasons_json=excluded.conflict_reasons_json,
    normalization_version=excluded.normalization_version,
    profile_hash=excluded.profile_hash,
    updated_at_utc=excluded.updated_at_utc,
    raw_brand=excluded.raw_brand,
    source_brand_role=excluded.source_brand_role,
    brand_resolution_source=excluded.brand_resolution_source,
    raw_presentation=excluded.raw_presentation,
    normalized_quantity=excluded.normalized_quantity,
    normalized_unit=excluded.normalized_unit,
    normalized_pack_count=excluded.normalized_pack_count,
    canonical_total=excluded.canonical_total,
    display_presentation=excluded.display_presentation
WHERE {TABLE_NAME}.profile_hash <> excluded.profile_hash
   OR {TABLE_NAME}.normalization_version <> excluded.normalization_version
"""


def _values(row: ProductHomologationRow) -> tuple[object, ...]:
    return (
        row.product_id,
        row.supermarket_id,
        row.normalized_name,
        row.normalized_brand,
        row.canonical_gtin,
        row.canonical_product_id,
        row.category,
        row.subcategory,
        row.product_type,
        row.taxonomy_rule_id,
        row.presentation_dimension,
        row.presentation_total_base,
        row.presentation_pack_count,
        row.presentation_unit_amount_base,
        row.presentation_status,
        row.comparison_status,
        row.conflict_reasons_json,
        row.normalization_version,
        row.profile_hash,
        row.updated_at_utc,
        row.raw_brand,
        row.source_brand_role,
        row.brand_resolution_source,
        row.raw_presentation,
        row.normalized_quantity,
        row.normalized_unit,
        row.normalized_pack_count,
        row.canonical_total,
        row.display_presentation,
    )


def ensure_sqlite_schema(con: sqlite3.Connection) -> None:
    exists = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (TABLE_NAME,),
    ).fetchone()
    if exists:
        columns = tuple(row[1] for row in con.execute(f"PRAGMA table_info({TABLE_NAME})"))
        if columns == LEGACY_PROFILE_COLUMNS:
            for statement in SQLITE_MIGRATION_STEPS:
                con.execute(statement)
        elif columns != PROFILE_COLUMNS:
            raise ProductHomologationPersistenceError("profile_schema_mismatch")
    con.executescript(SCHEMA_SQL)
    columns = tuple(row[1] for row in con.execute(f"PRAGMA table_info({TABLE_NAME})"))
    if columns != PROFILE_COLUMNS:
        raise ProductHomologationPersistenceError("profile_schema_mismatch")


def persist_sqlite_rows(
    con: sqlite3.Connection,
    rows: Iterable[ProductHomologationRow],
) -> dict[str, int]:
    """Upsert idempotente de cobertura total; no toca estado comercial."""

    rows = tuple(rows)
    source_ids = {int(product_id) for (product_id,) in con.execute("SELECT product_id FROM products")}
    row_ids = {row.product_id for row in rows}
    if len(rows) != len(source_ids) or row_ids != source_ids:
        raise ProductHomologationPersistenceError(
            f"profile_product_count_mismatch:{len(rows)}:{len(source_ids)}"
        )

    ensure_sqlite_schema(con)
    existing = {
        int(product_id): (str(profile_hash), str(version))
        for product_id, profile_hash, version in con.execute(
            f"SELECT product_id,profile_hash,normalization_version FROM {TABLE_NAME}"
        )
    }
    inserted = 0
    updated = 0
    unchanged = 0
    for row in rows:
        prior = existing.get(row.product_id)
        if prior is None:
            inserted += 1
        elif prior == (row.profile_hash, row.normalization_version):
            unchanged += 1
        else:
            updated += 1
        con.execute(UPSERT_SQL, _values(row))

    stored = con.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}").fetchone()[0]
    product_count = con.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    if stored != product_count:
        raise ProductHomologationPersistenceError(
            f"profile_product_count_mismatch:{stored}:{product_count}"
        )
    fk = con.execute("PRAGMA foreign_key_check").fetchall()
    if fk:
        raise ProductHomologationPersistenceError("foreign_key_check_failed")
    return {
        "processed": len(rows),
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "stored": int(stored),
    }


def records_from_product_rows(rows: Iterable[tuple[object, ...]]) -> tuple[tuple[int, SourceProductRecord], ...]:
    """Adapta SELECT estable de products a registros del motor."""

    result: list[tuple[int, SourceProductRecord]] = []
    for raw in rows:
        if len(raw) != 7:
            raise ProductHomologationPersistenceError("product_row_shape_invalid")
        product_id, supermarket_id, name, brand, presentation, category, ean = raw
        if type(product_id) is not int or product_id <= 0:
            raise ProductHomologationPersistenceError("product_id_invalid")
        supermarket = str(supermarket_id).strip()
        source_name = str(name).strip()
        if not supermarket or not source_name:
            raise ProductHomologationPersistenceError("product_row_required_text_missing")
        result.append(
            (
                product_id,
                SourceProductRecord(
                    source_record_id=f"{supermarket}:{product_id}",
                    supermarket_id=supermarket,
                    source_name=source_name,
                    source_brand=None if brand is None else str(brand),
                    source_presentation=None if presentation is None else str(presentation),
                    source_category=None if category is None else str(category),
                    barcode=None if ean is None else str(ean),
                ),
            )
        )
    return tuple(result)
