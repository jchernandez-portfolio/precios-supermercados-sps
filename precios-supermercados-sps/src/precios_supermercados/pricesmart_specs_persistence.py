"""Persistencia barata de especificaciones PriceSmart (tabla derivada STRICT).

``pricesmart_product_specs`` vive al margen del histórico comercial: no abre ni
cierra periodos de precio y no crea identidad. Está indexada por el ``pid``
público de PriceSmart (``products.source_catalog_product_id``), nunca por un GTIN.

Coste Turso (rows read), según la regla READ NECESSARY SCOPE → COMPARE ONCE →
WRITE CHANGES ONLY → VERIFY AFFECTED SCOPE:

- estado incremental: sólo las llaves verificadas dentro de la ventana, por el
  índice cubriente ``(verified_at_utc, product_id)`` (``INDEXED BY``, falla si
  el índice no existe en vez de degradar a un scan);
- comparación: búsquedas por PK sólo de los ``pid`` capturados en la corrida;
- escritura: upsert completo sólo de filas nuevas o con huella distinta; las
  filas re-verificadas sin cambios sólo actualizan ``verified_at_utc``;
- verificación: búsquedas por PK sólo de las filas afectadas.

Las sentencias son SQLite estándar: las mismas corren en Turso (libSQL) y en el
SQLite en memoria de las pruebas.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Protocol

TABLE_NAME = "pricesmart_product_specs"
VERIFIED_INDEX = "idx_pricesmart_product_specs_verified"
ARTIFACT_SCHEMA = "precios-sps-pricesmart-specs/v1"
STATE_SCHEMA = "precios-sps-pricesmart-specs-state/v1"
DEFAULT_STALE_DAYS = 28
CHUNK_SIZE = 400
_PID_RE = re.compile(r"[1-9][0-9]{0,11}")
_UTC_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")

TABLE_SQL = f"""CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    product_id TEXT PRIMARY KEY CHECK (length(product_id) BETWEEN 1 AND 12 AND product_id NOT GLOB '*[^0-9]*'),
    item_number TEXT,
    product_url TEXT NOT NULL,
    title TEXT,
    brand TEXT,
    category_path TEXT,
    net_weight_g TEXT,
    net_volume_ml TEXT,
    unit_weight_g TEXT,
    pack_count INTEGER CHECK (pack_count IS NULL OR pack_count > 0),
    imported_or_national TEXT CHECK (imported_or_national IS NULL OR imported_or_national IN ('importado','nacional')),
    origin_country TEXT,
    storage TEXT,
    allergens_json TEXT NOT NULL,
    trans_fat_free INTEGER CHECK (trans_fat_free IS NULL OR trans_fat_free IN (0,1)),
    gtin TEXT CHECK (gtin IS NULL OR (length(gtin) = 14 AND gtin NOT GLOB '*[^0-9]*')),
    presentation_hint TEXT,
    conflicts_json TEXT NOT NULL,
    extraction_method TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    spec_json TEXT NOT NULL,
    spec_sha256 TEXT NOT NULL CHECK (length(spec_sha256) = 64),
    capture_run_id TEXT,
    first_captured_at_utc TEXT NOT NULL,
    verified_at_utc TEXT NOT NULL,
    changed_at_utc TEXT NOT NULL
) STRICT"""
INDEX_SQL = (
    f"CREATE INDEX IF NOT EXISTS {VERIFIED_INDEX} "
    f"ON {TABLE_NAME}(verified_at_utc, product_id)"
)
COLUMNS = (
    "product_id", "item_number", "product_url", "title", "brand", "category_path",
    "net_weight_g", "net_volume_ml", "unit_weight_g", "pack_count",
    "imported_or_national", "origin_country", "storage", "allergens_json",
    "trans_fat_free", "gtin", "presentation_hint", "conflicts_json",
    "extraction_method", "parser_version", "spec_json", "spec_sha256",
    "capture_run_id", "first_captured_at_utc", "verified_at_utc", "changed_at_utc",
)
_INTEGER_COLUMNS = frozenset({"pack_count", "trans_fat_free"})

TABLE_EXISTS_SQL = "SELECT name FROM sqlite_master WHERE type='table' AND name=?"
STATE_SQL = (
    f"SELECT product_id,verified_at_utc FROM {TABLE_NAME} INDEXED BY {VERIFIED_INDEX} "
    "WHERE verified_at_utc>=? ORDER BY verified_at_utc,product_id"
)
EXISTING_SQL = (
    f"SELECT product_id,spec_sha256 FROM {TABLE_NAME} "
    "WHERE product_id IN (SELECT value FROM json_each(?))"
)
VERIFY_SQL = (
    f"SELECT product_id,spec_sha256,verified_at_utc FROM {TABLE_NAME} "
    "WHERE product_id IN (SELECT value FROM json_each(?))"
)
_EXTRACT = ",".join(
    f"CAST(json_extract(value,'$.{column}') AS INTEGER)" if column in _INTEGER_COLUMNS
    else f"json_extract(value,'$.{column}')"
    for column in COLUMNS
)
UPSERT_SQL = f"""INSERT INTO {TABLE_NAME} ({','.join(COLUMNS)})
SELECT {_EXTRACT} FROM json_each(?) WHERE 1
ON CONFLICT(product_id) DO UPDATE SET
{','.join(f'{column}=excluded.{column}' for column in COLUMNS if column not in {'product_id', 'first_captured_at_utc'})}
WHERE {TABLE_NAME}.spec_sha256 IS NOT excluded.spec_sha256"""
TOUCH_SQL = (
    f"UPDATE {TABLE_NAME} SET verified_at_utc=?,capture_run_id=? "
    "WHERE product_id IN (SELECT value FROM json_each(?)) AND verified_at_utc<?"
)
# Para el refresco diario de homologación: recorre sólo los products PriceSmart
# por el índice UNIQUE(supermarket_id,source_key_type,source_key) y busca cada
# especificación por PK. CROSS JOIN fija ese orden en SQLite/libSQL.
PROFILE_ATTRIBUTES_SQL = (
    "SELECT p.product_id,s.brand,s.presentation_hint "
    f"FROM products AS p CROSS JOIN {TABLE_NAME} AS s "
    "ON s.product_id=p.source_catalog_product_id "
    "WHERE p.supermarket_id='pricesmart' "
    "AND (s.brand IS NOT NULL OR s.presentation_hint IS NOT NULL)"
)


class PriceSmartSpecsPersistenceError(ValueError):
    pass


class Executor(Protocol):
    def query(self, sql: str, args: tuple[object, ...] = ()) -> list[list[object]]: ...

    def batch(self, steps: list[tuple[str, str, tuple[object, ...]]]) -> list[int | None]: ...


def _require(condition: object, reason: str) -> None:
    if not condition:
        raise PriceSmartSpecsPersistenceError(reason)


def _utc(value: object, reason: str) -> datetime:
    _require(isinstance(value, str) and _UTC_RE.fullmatch(value), reason)
    return datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _chunks(values: list[Any], size: int = CHUNK_SIZE) -> Iterable[list[Any]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


@dataclass(frozen=True)
class SpecRow:
    values: dict[str, object]

    @property
    def product_id(self) -> str:
        return str(self.values["product_id"])

    @property
    def spec_sha256(self) -> str:
        return str(self.values["spec_sha256"])


def _text_or_none(value: object) -> str | None:
    return None if value is None else str(value)


def rows_from_artifact(artifact: dict[str, Any]) -> list[SpecRow]:
    """Filas sólo de ítems ``parsed`` de un artifact exitoso; valida el contrato."""

    _require(isinstance(artifact, dict) and artifact.get("schema") == ARTIFACT_SCHEMA, "artifact_schema_invalid")
    _require(artifact.get("result") == "success", "artifact_not_successful")
    generated = artifact.get("generated_at_utc")
    _utc(generated, "artifact_generated_at_invalid")
    items = artifact.get("items")
    _require(isinstance(items, list), "artifact_items_invalid")
    run_id = artifact.get("run", {}).get("github_run_id") if isinstance(artifact.get("run"), dict) else None
    rows: list[SpecRow] = []
    seen: set[str] = set()
    for item in items:
        _require(isinstance(item, dict), "artifact_item_invalid")
        pid = item.get("product_id")
        _require(isinstance(pid, str) and _PID_RE.fullmatch(pid), "artifact_product_id_invalid")
        _require(pid not in seen, "artifact_product_id_duplicate")
        seen.add(pid)
        if item.get("status") != "parsed":
            _require(item.get("specs") is None, "artifact_unparsed_item_has_specs")
            continue
        specs = item.get("specs")
        _require(isinstance(specs, dict) and specs.get("product_id") == pid, "artifact_specs_invalid")
        digest = item.get("spec_sha256")
        _require(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest), "artifact_spec_hash_invalid")
        fetched = item.get("fetched_at_utc")
        _utc(fetched, "artifact_fetched_at_invalid")
        net = specs.get("net_weight") or {}
        volume = specs.get("net_volume") or {}
        unit = specs.get("unit_weight") or {}
        trans = specs.get("trans_fat_free")
        category = specs.get("category_path") or []
        _require(isinstance(category, list), "artifact_category_path_invalid")
        values: dict[str, object] = {
            "product_id": pid,
            "item_number": _text_or_none(specs.get("item_number")),
            "product_url": str(item.get("url") or ""),
            "title": _text_or_none(specs.get("title")),
            "brand": _text_or_none(specs.get("brand")),
            "category_path": " > ".join(str(entry) for entry in category) or None,
            "net_weight_g": _text_or_none(net.get("value_g")),
            "net_volume_ml": _text_or_none(volume.get("value_ml")),
            "unit_weight_g": _text_or_none(unit.get("value_g")),
            "pack_count": specs.get("pack_count"),
            "imported_or_national": specs.get("imported_or_national"),
            "origin_country": _text_or_none(specs.get("origin_country")),
            "storage": _text_or_none(specs.get("storage")),
            "allergens_json": json.dumps(specs.get("allergens") or [], ensure_ascii=False, separators=(",", ":")),
            "trans_fat_free": None if trans is None else int(bool(trans)),
            "gtin": specs.get("gtin"),
            "presentation_hint": _text_or_none(specs.get("presentation_hint")),
            "conflicts_json": json.dumps(specs.get("conflicts") or [], ensure_ascii=False, separators=(",", ":")),
            "extraction_method": str(item.get("extraction_method") or ""),
            "parser_version": str(specs.get("parser_version") or ""),
            "spec_json": json.dumps(specs, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            "spec_sha256": digest,
            "capture_run_id": _text_or_none(run_id),
            "first_captured_at_utc": fetched,
            "verified_at_utc": fetched,
            "changed_at_utc": fetched,
        }
        _require(values["product_url"], "artifact_url_missing")
        _require(values["extraction_method"] and values["parser_version"], "artifact_method_missing")
        _require(values["pack_count"] is None or (type(values["pack_count"]) is int and values["pack_count"] > 0), "artifact_pack_count_invalid")
        rows.append(SpecRow(values))
    return rows


def table_exists(executor: Executor) -> bool:
    return bool(executor.query(TABLE_EXISTS_SQL, (TABLE_NAME,)))


def read_state(executor: Executor, *, now: datetime, stale_days: int = DEFAULT_STALE_DAYS) -> dict[str, Any]:
    """Llaves frescas (``verified_at >= now - stale_days``) para la captura incremental."""

    _require(1 <= stale_days <= 365, "stale_days_invalid")
    cutoff = _utc_text(now - timedelta(days=stale_days))
    fresh: dict[str, str] = {}
    exists = table_exists(executor)
    if exists:
        for product_id, verified in executor.query(STATE_SQL, (cutoff,)):
            _require(isinstance(product_id, str) and _PID_RE.fullmatch(product_id), "state_product_id_invalid")
            _utc(verified, "state_verified_at_invalid")
            fresh[product_id] = str(verified)
    return {
        "schema": STATE_SCHEMA,
        "as_of_utc": _utc_text(now),
        "stale_days": stale_days,
        "cutoff_utc": cutoff,
        "table_exists": exists,
        "fresh": dict(sorted(fresh.items())),
    }


def apply_rows(executor: Executor, rows: list[SpecRow]) -> dict[str, Any]:
    """Upsert de filas cambiadas + touch de re-verificadas; verifica sólo lo afectado."""

    if not rows:
        return {"incoming": 0, "inserted": 0, "changed": 0, "reverified_unchanged": 0, "no_op": True}
    ids = [row.product_id for row in rows]
    _require(len(set(ids)) == len(ids), "rows_product_id_duplicate")
    verified_values = {str(row.values["verified_at_utc"]) for row in rows}
    run_ids = {row.values["capture_run_id"] for row in rows}
    _require(len(run_ids) == 1, "rows_capture_run_id_mixed")
    run_id = next(iter(run_ids))

    executor.batch([
        ("begin", "BEGIN IMMEDIATE", ()),
        ("create_table", TABLE_SQL, ()),
        ("create_index", INDEX_SQL, ()),
        ("commit", "COMMIT", ()),
    ])
    existing: dict[str, str] = {}
    for chunk in _chunks(ids):
        for product_id, digest in executor.query(EXISTING_SQL, (json.dumps(chunk),)):
            existing[str(product_id)] = str(digest)
    changed = [row for row in rows if existing.get(row.product_id) != row.spec_sha256]
    unchanged = [row for row in rows if existing.get(row.product_id) == row.spec_sha256]
    inserted = sum(row.product_id not in existing for row in changed)

    steps: list[tuple[str, str, tuple[object, ...]]] = [
        ("drop_guard", "DROP TABLE IF EXISTS temp.pricesmart_specs_guard", ()),
        ("guard_table", "CREATE TEMP TABLE pricesmart_specs_guard(value INTEGER NOT NULL CHECK(value=0)) STRICT", ()),
        ("begin", "BEGIN IMMEDIATE", ()),
    ]
    for index, chunk in enumerate(_chunks(changed)):
        payload = json.dumps([row.values for row in chunk], ensure_ascii=False, separators=(",", ":"))
        steps.append((f"upsert_{index}", UPSERT_SQL, (payload,)))
        steps.append((
            f"guard_upsert_{index}",
            "INSERT INTO pricesmart_specs_guard SELECT CASE WHEN changes()=? THEN 0 ELSE 1 END",
            (len(chunk),),
        ))
    # Re-verificadas sin cambios: sólo ``verified_at_utc`` (agrupadas por instante).
    touch_index = 0
    for verified in sorted(verified_values):
        group = [row.product_id for row in unchanged if row.values["verified_at_utc"] == verified]
        for chunk in _chunks(group):
            steps.append((f"touch_{touch_index}", TOUCH_SQL, (verified, run_id, json.dumps(chunk), verified)))
            touch_index += 1
    steps.append(("commit", "COMMIT", ()))
    if len(steps) > 4:
        executor.batch(steps)

    verified_rows: dict[str, tuple[str, str]] = {}
    for chunk in _chunks(ids):
        for product_id, digest, verified in executor.query(VERIFY_SQL, (json.dumps(chunk),)):
            verified_rows[str(product_id)] = (str(digest), str(verified))
    for row in rows:
        actual = verified_rows.get(row.product_id)
        _require(actual is not None, f"postflight_row_missing:{row.product_id}")
        _require(actual[0] == row.spec_sha256, f"postflight_hash_mismatch:{row.product_id}")
        _require(actual[1] >= str(row.values["verified_at_utc"]), f"postflight_verified_at_stale:{row.product_id}")
    return {
        "incoming": len(rows),
        "inserted": inserted,
        "changed": len(changed) - inserted,
        "reverified_unchanged": len(unchanged),
        "no_op": False,
        "verified_rows": len(verified_rows),
    }


def fetch_profile_attributes(executor: Executor) -> dict[int, tuple[str | None, str | None]]:
    """``products.product_id`` → (marca, presentación) desde especificaciones PriceSmart."""

    if not table_exists(executor):
        return {}
    result: dict[int, tuple[str | None, str | None]] = {}
    for product_id, brand, hint in executor.query(PROFILE_ATTRIBUTES_SQL):
        _require(type(product_id) is int and product_id > 0, "profile_attribute_product_id_invalid")
        result[int(product_id)] = (
            None if brand is None else str(brand),
            None if hint is None else str(hint),
        )
    return result


class SqliteExecutor:
    """Ejecutor local (pruebas y SQLite MVP) con la misma semántica transaccional."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection
        self.statements: list[str] = []

    def query(self, sql: str, args: tuple[object, ...] = ()) -> list[list[object]]:
        self.statements.append(" ".join(sql.split()))
        return [list(row) for row in self.connection.execute(sql, args).fetchall()]

    def batch(self, steps: list[tuple[str, str, tuple[object, ...]]]) -> list[int | None]:
        counts: list[int | None] = []
        in_transaction = False
        try:
            for name, sql, args in steps:
                self.statements.append(" ".join(sql.split()))
                cursor = self.connection.execute(sql, args)
                if name == "begin":
                    in_transaction = True
                if name == "commit":
                    in_transaction = False
                counts.append(cursor.rowcount)
        except Exception:
            if in_transaction:
                self.connection.execute("ROLLBACK")
            raise
        return counts
