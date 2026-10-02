"""SKU listados sin precio y agotados: fuera del snapshot con precio, sin bloquear.

Regla (incidente La Colonia 2026-10-02): un retailer puede listar productos
nuevos o agotados sin precio publicado ("NO DISPONIBLE"). Esos SKU no son un
fallo de extracción cuando la fuente los declara explícitamente agotados:

- se separan de ``products`` (filas con precio) en ``unpriced_unavailable``;
- no crean ``products`` ni ``price_history`` (no hay precio que historizar);
- se aceptan sólo si TODOS están ``out_of_stock`` y su cantidad no supera
  ``MAX_UNPRICED_UNAVAILABLE_RATIO`` del catálogo de SKU observado;
- un SKU sin precio ``in_stock``/``unknown`` sigue fallando cerrado.

El módulo es puro: no hace red ni persiste. Lo consumen la adquisición, los
validadores de snapshot y la persistencia Turso.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Callable, Iterable, Mapping, Sequence

# 2 % del catálogo de SKU (con precio + sin precio agotados). Configurable aquí;
# por encima de este umbral la ausencia de precios deja de ser plausible como
# lanzamientos/agotados y se trata como problema de extracción.
MAX_UNPRICED_UNAVAILABLE_RATIO = Decimal("0.02")

SNAPSHOT_LIST_KEY = "unpriced_unavailable"
SNAPSHOT_COUNT_KEY = "skus_unpriced_unavailable"

# Conjunto cerrado de claves por entrada de ``unpriced_unavailable``.
ENTRY_KEYS = frozenset(
    {
        "availability",
        "brand",
        "category",
        "ean",
        "item_id",
        "observed_at_utc",
        "presentation",
        "product_id",
        "reference",
        "source_key",
        "source_key_type",
        "source_name",
    }
)
_OPTIONAL_TEXT_KEYS = ("brand", "category", "ean", "presentation", "reference")

UNPRICED_OBSERVATIONS_TABLE = "catalog_unpriced_observations"
UNPRICED_OBSERVATIONS_PENDING_INDEX = "idx_catalog_unpriced_pending"
UNPRICED_OBSERVATIONS_TABLE_SQL = """CREATE TABLE IF NOT EXISTS catalog_unpriced_observations (
            supermarket_id TEXT NOT NULL,
            location_id TEXT NOT NULL,
            source_key_type TEXT NOT NULL,
            source_key TEXT NOT NULL,
            source_catalog_product_id TEXT NOT NULL,
            name TEXT NOT NULL,
            brand TEXT,
            category TEXT,
            presentation TEXT,
            ean TEXT,
            first_seen_utc TEXT NOT NULL,
            last_seen_utc TEXT NOT NULL,
            priced_since_utc TEXT,
            PRIMARY KEY (supermarket_id, location_id, source_key_type, source_key),
            FOREIGN KEY (location_id, supermarket_id)
                REFERENCES locations(location_id, supermarket_id)
        ) STRICT"""
# Parcial: la marca ``priced_since_utc`` sólo recorre observaciones pendientes
# (sin precio todavía) del contexto, nunca la tabla completa.
UNPRICED_OBSERVATIONS_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_catalog_unpriced_pending "
    "ON catalog_unpriced_observations(supermarket_id, location_id) "
    "WHERE priced_since_utc IS NULL"
)


def is_unpriced_unavailable(row: Mapping[str, Any]) -> bool:
    """Fila de catálogo sin precio publicado y declarada agotada por la fuente."""

    return row.get("current_price") is None and row.get("availability") == "out_of_stock"


def unpriced_unavailable_limit(total_skus: int) -> int:
    """Máximo de SKU sin precio agotados admitidos para ``total_skus`` observados."""

    if type(total_skus) is not int or total_skus < 0:
        raise ValueError("total_skus_invalid")
    return int(Decimal(total_skus) * MAX_UNPRICED_UNAVAILABLE_RATIO)


def unpriced_entry(row: Mapping[str, Any], *, observed_at_utc: str) -> dict[str, Any]:
    """Proyecta una fila sin precio agotada a la entrada cerrada del snapshot."""

    return {
        "availability": row.get("availability"),
        "brand": row.get("brand"),
        "category": row.get("category"),
        "ean": row.get("ean"),
        "item_id": row.get("item_id"),
        "observed_at_utc": observed_at_utc,
        "presentation": row.get("presentation"),
        "product_id": row.get("product_id"),
        "reference": row.get("reference"),
        "source_key": row.get("source_key"),
        "source_key_type": row.get("source_key_type"),
        "source_name": row.get("source_name"),
    }


def split_unpriced_unavailable(
    rows: Iterable[Mapping[str, Any]],
    *,
    observed_at_utc: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Separa SKU sin precio agotados; el resto (incluido sin precio no agotado) queda.

    Las filas sin precio ``in_stock``/``unknown`` permanecen en ``products`` para
    que la validación del snapshot falle cerrado sobre ellas.
    """

    kept: list[dict[str, Any]] = []
    unpriced: list[dict[str, Any]] = []
    for row in rows:
        if is_unpriced_unavailable(row):
            unpriced.append(unpriced_entry(row, observed_at_utc=observed_at_utc))
        else:
            kept.append(dict(row))
    return kept, unpriced


def validate_unpriced_unavailable(
    data: Mapping[str, Any],
    *,
    priced_rows: Sequence[Mapping[str, Any]],
    error: Callable[[str], Exception],
) -> list[dict[str, Any]]:
    """Valida la sección ``unpriced_unavailable`` de un snapshot (fail-closed).

    Ausente equivale a lista vacía (snapshots anteriores a la regla). Si existe,
    debe venir con su contador, entradas de esquema cerrado, todas
    ``out_of_stock``, con ``observed_at_utc`` del snapshot, identidad no vacía,
    sin duplicados ni solapamiento con las filas con precio, y dentro del umbral.
    """

    if SNAPSHOT_LIST_KEY not in data and SNAPSHOT_COUNT_KEY not in data:
        return []
    entries = data.get(SNAPSHOT_LIST_KEY)
    count = data.get(SNAPSHOT_COUNT_KEY)
    if not isinstance(entries, list) or type(count) is not int or count != len(entries):
        raise error("snapshot_unpriced_unavailable_count_mismatch")
    observed_at = data.get("observed_at_utc")
    priced_identities = {
        (str(row.get("source_key_type")), str(row.get("source_key"))) for row in priced_rows
    }
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != ENTRY_KEYS:
            raise error("snapshot_unpriced_unavailable_schema_invalid")
        if entry["availability"] != "out_of_stock":
            # Sin precio y no agotado = problema real de extracción.
            raise error("snapshot_unpriced_not_unavailable")
        if entry["observed_at_utc"] != observed_at:
            raise error("snapshot_unpriced_unavailable_observed_at_invalid")
        identity_values = (
            entry["source_key_type"], entry["source_key"], entry["source_name"],
            entry["product_id"], entry["item_id"],
        )
        if not all(isinstance(value, str) and value.strip() for value in identity_values):
            raise error("snapshot_unpriced_unavailable_identity_invalid")
        if any(entry[key] is not None and not isinstance(entry[key], str) for key in _OPTIONAL_TEXT_KEYS):
            raise error("snapshot_unpriced_unavailable_schema_invalid")
        identity = (entry["source_key_type"], entry["source_key"])
        if identity in seen or identity in priced_identities:
            raise error("snapshot_unpriced_unavailable_identity_duplicate")
        seen.add(identity)
    if len(entries) > unpriced_unavailable_limit(len(priced_rows) + len(entries)):
        raise error("snapshot_unpriced_unavailable_above_threshold")
    return entries
