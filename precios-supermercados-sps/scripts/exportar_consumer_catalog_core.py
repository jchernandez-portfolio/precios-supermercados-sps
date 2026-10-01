#!/usr/bin/env python3
"""Exporta el catálogo público B2C SPS v3 desde estado aceptado, sin escrituras."""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Sequence
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from exportar_modelo_analitico import (  # noqa: E402
    ExportError, QueryBackend, SQLiteBackend, TursoBackend, _scope_predicate,
    fetch_current_rows_by_location, parse_scope, scope_contexts,
)
from exportar_rpi_marts import _parse_utc, fetch_freshness  # noqa: E402
from generar_mvp_sqlite_la_colonia import HISTORY_INDEX_NAME  # noqa: E402
from precios_supermercados.price_analytics import ComparisonScope  # noqa: E402
from precios_supermercados.price_history_analytics import (  # noqa: E402
    HistoricalPriceObservation,
    summarize_price_series,
    summarize_price_windows,
)
from precios_supermercados.product_homologation_persistence import (  # noqa: E402
    NORMALIZATION_VERSION,
)
SCHEMA, MANIFEST_SCHEMA = "rpi-consumer-catalog/v3", "rpi-consumer-catalog-manifest/v3"
FACETS_SCHEMA, INDEX_SCHEMA = "rpi-consumer-facets/v3", "rpi-consumer-index/v3"
PARTITION_SCHEMA = "rpi-consumer-catalog-partition/v3"
ANALYSIS_SCHEMA = "rpi-consumer-analysis/v1"
MAX_PARTITION_ROWS = 250
EXPECTED_SCOPE = (
    ("la_colonia", "la_colonia_sps"), ("colonial", "colonial_sps"),
    ("walmart", "walmart_sps"), ("pricesmart", "pricesmart_sps"),
    ("comisariato_los_andes", "comisariato_los_andes_sps"),
)
RETAILER_NAMES = {
    "la_colonia": "La Colonia", "colonial": "Colonial", "walmart": "Walmart",
    "pricesmart": "PriceSmart",
    "comisariato_los_andes": "Los Andes",
}
PUBLIC_COMPARABILITY = {"comparable", "single_source", "individual"}
VISIBLE_OFFERS_PAGE_SIZE = 2000
HISTORY_PAGE_SIZE = 5000
_VISIBLE_OFFERS_SELECT = """SELECT p.product_id,p.supermarket_id,p.name,
                   hp.normalized_brand,hp.display_presentation,
                   h.location_id,h.current_price_minor,h.reported_regular_price_minor,
                   h.is_promotion,h.availability,h.valid_from_utc,
                   hp.canonical_product_id,hp.category,hp.product_type,
                   hp.presentation_dimension,hp.presentation_total_base,
                   hp.presentation_status,hp.comparison_status,hp.normalization_version
            FROM price_history AS h
            JOIN products AS p
              ON p.product_id=h.product_id AND p.supermarket_id=h.supermarket_id
            JOIN product_homologation_profiles AS hp
              ON hp.product_id=p.product_id AND hp.supermarket_id=p.supermarket_id"""


@dataclass(frozen=True, slots=True)
class HistoricalPoint:
    source_product_id: str
    supermarket_id: str
    location_id: str
    observed_at: datetime
    current_price_minor: int


@dataclass(frozen=True, slots=True)
class VisibleOffer:
    source_product_id: str
    supermarket_id: str
    location_id: str
    product_name: str
    brand: str | None
    presentation: str | None
    current_price_minor: int | None
    reported_regular_price_minor: int | None
    is_promotion: bool | None
    availability: str
    observed_at: str
    canonical_product_id: str | None
    category: str | None
    product_type: str | None
    presentation_dimension: str | None
    presentation_total_base: str | None
    presentation_status: str
    comparison_status: str
def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


_BRAND_DISPLAY_OVERRIDES = {
    "norteno": "Norteño",
    "nutri yema": "Nutri Yema",
    "rica yema": "Rica Yema",
    "marketside": "Marketside",
}


def _display_brand(value: object) -> str | None:
    normalized = _text(value)
    if normalized is None:
        return None
    return _BRAND_DISPLAY_OVERRIDES.get(normalized.casefold(), normalized.title())
def _money(minor: int | None) -> str | None:
    if minor is None or minor <= 0:
        return None
    return format(Decimal(minor) / Decimal(100), ".2f")


def _nonnegative_money(minor: int) -> str:
    if not isinstance(minor, int) or minor < 0:
        raise ExportError("consumer_analysis_money_invalid")
    return format(Decimal(minor) / Decimal(100), ".2f")


def _money_minor(value: object) -> int | None:
    if not isinstance(value, str) or not re.fullmatch(r"\d+(?:\.\d{1,2})?", value):
        return None
    amount = Decimal(value)
    minor = amount * 100
    return int(minor) if minor == minor.to_integral_value() and minor > 0 else None


def _change_pct(current: int, baseline: int) -> str | None:
    if current <= 0 or baseline <= 0:
        return None
    value = (Decimal(current - baseline) * 100 / Decimal(baseline)).quantize(Decimal("0.01"))
    return format(value, ".2f")


def _discount_pct(current: int, regular: int) -> str | None:
    if current <= 0 or regular <= current:
        return None
    value = (Decimal(regular - current) * 100 / Decimal(regular)).quantize(Decimal("0.01"))
    return format(value, ".2f")
def _slug(value: str | None) -> str:
    raw = "sin-categoria" if value is None else value
    folded = unicodedata.normalize("NFKD", raw)
    ascii_text = "".join(char for char in folded if not unicodedata.combining(char))
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.casefold()).strip("-")
    return slug or "sin-categoria"
def _format_decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")
def _derived_presentation(offer: VisibleOffer) -> str | None:
    source = _text(offer.presentation)
    if source:
        return source
    if offer.presentation_status not in {"confirmed", "name_only"}:
        return None
    if offer.presentation_dimension is None or offer.presentation_total_base is None:
        return None
    try:
        total = Decimal(offer.presentation_total_base)
    except Exception as exc:  # pragma: no cover - defensive against remote schema drift
        raise ExportError("consumer_catalog_presentation_invalid") from exc
    dimension = offer.presentation_dimension
    if dimension == "volume_ml":
        value, unit = (total / 1000, "L") if total >= 1000 and total % 1000 == 0 else (total, "ml")
    elif dimension == "mass_g":
        value, unit = (total / 1000, "kg") if total >= 1000 and total % 1000 == 0 else (total, "g")
    elif dimension == "ounce":
        value, unit = total, "oz"
    elif dimension == "count":
        value, unit = total, "unidades"
    else:
        return None
    return f"{_format_decimal(value)} {unit}"
def fetch_visible_offers(
    backend: QueryBackend,
    scope: ComparisonScope,
) -> tuple[VisibleOffer, ...]:
    """Lee una fila current por identidad fuente dentro del scope explícito.

    La lectura se pagina por contexto exacto sobre ``idx_price_history_current`` y
    se reordena por ``(product_id, location_id)``: mismo resultado que el keyset
    global anterior, pero cada fila current se lee una sola vez en Turso.
    """
    result: list[VisibleOffer] = []
    seen: set[tuple[int, str]] = set()
    rows = fetch_current_rows_by_location(
        backend,
        scope,
        select_sql=_VISIBLE_OFFERS_SELECT,
        location_index=5,
        invalid_code="consumer_catalog_offer_invalid",
        page_size=VISIBLE_OFFERS_PAGE_SIZE,
    )
    for row in rows:
        (
            product_id, supermarket_id, name, brand, presentation,
            location_id, current_price, regular_price, is_promotion, availability,
            observed_at, canonical_product_id, category, product_type,
            presentation_dimension, presentation_total_base, presentation_status,
            comparison_status, normalization_version,
        ) = row
        key = (product_id, str(location_id))
        if key in seen:
            raise ExportError("consumer_catalog_offer_duplicate")
        seen.add(key)
        if (
            type(product_id) is not int
            or not isinstance(supermarket_id, str)
            or not isinstance(location_id, str)
            or not _text(name)
            or (current_price is not None and type(current_price) is not int)
            or (regular_price is not None and type(regular_price) is not int)
            or (is_promotion is not None and (type(is_promotion) is not int or is_promotion not in {0, 1}))
            or availability not in {"in_stock", "out_of_stock", "unknown"}
            or not _text(observed_at)
            or comparison_status not in {"ready", "review_required", "single_source", "unmapped"}
            or normalization_version != NORMALIZATION_VERSION
        ):
            raise ExportError("consumer_catalog_offer_invalid")
        result.append(
            VisibleOffer(
                source_product_id=f"{supermarket_id}:{product_id}",
                supermarket_id=supermarket_id,
                location_id=location_id,
                product_name=_text(name) or "",
                brand=_display_brand(brand),
                presentation=_text(presentation),
                current_price_minor=current_price,
                reported_regular_price_minor=regular_price,
                is_promotion=None if is_promotion is None else bool(is_promotion),
                availability=availability,
                observed_at=_text(observed_at) or "",
                canonical_product_id=_text(canonical_product_id),
                category=_text(category),
                product_type=_text(product_type),
                presentation_dimension=_text(presentation_dimension),
                presentation_total_base=_text(presentation_total_base),
                presentation_status=str(presentation_status),
                comparison_status=str(comparison_status),
            )
        )
    return tuple(result)


# `idx_ph_loc_hist` lo crea `migrar_mvp_paiz.py`; si todavía no existe, el
# histórico usa el keyset de fila sobre la PK (lineal, sin el índice nuevo).
def _history_index_available(backend: QueryBackend) -> bool:
    rows = backend.query(
        "SELECT name FROM sqlite_master WHERE type='index' AND name=? AND tbl_name='price_history'",
        (HISTORY_INDEX_NAME,),
    )
    return bool(rows)


_HISTORY_SELECT = """SELECT h.product_id,h.supermarket_id,h.location_id,h.current_price_minor,h.valid_from_utc
            FROM price_history AS h"""
_HISTORY_FILTER = """current_price_minor IS NOT NULL
              AND current_price_minor > 0
              AND julianday(valid_from_utc)<=julianday(?)"""


def _validate_history_key(row: Sequence[object]) -> None:
    """Valida antes de ordenar, con los mismos códigos y precedencia del bucle final."""
    product_id, supermarket_id, location_id, current_price, observed_at = row
    if (
        type(product_id) is not int
        or not isinstance(supermarket_id, str)
        or not isinstance(location_id, str)
        or type(current_price) is not int
        or current_price <= 0
    ):
        raise ExportError("consumer_catalog_history_invalid")
    if not isinstance(observed_at, str):
        raise ExportError("consumer_catalog_history_timestamp_invalid")


def _history_rows_by_location(
    backend: QueryBackend,
    scope: ComparisonScope,
    as_of_text: str,
) -> list[tuple[object, ...]]:
    """Pagina cada contexto sobre ``idx_ph_loc_hist(location_id,product_id,valid_from_utc)``."""
    collected: list[tuple[object, ...]] = []
    for supermarket_id, location_id in scope_contexts(scope):
        cursor_product, cursor_observed = -1, ""
        while True:
            rows = backend.query(
                f"""
            {_HISTORY_SELECT}
            WHERE h.supermarket_id=? AND h.location_id=?
              AND {_HISTORY_FILTER}
              AND (h.product_id,h.valid_from_utc)>(?,?)
            ORDER BY h.product_id,h.valid_from_utc
            LIMIT {int(HISTORY_PAGE_SIZE)}
            """,
                (supermarket_id, location_id, as_of_text, cursor_product, cursor_observed),
            )
            if not rows:
                break
            for row in rows:
                _validate_history_key(row)
            collected.extend(rows)
            cursor_product, cursor_observed = int(rows[-1][0]), str(rows[-1][4])
            if len(rows) < HISTORY_PAGE_SIZE:
                break
    collected.sort(key=lambda row: (row[0], row[2], row[4]))
    return collected


def _history_rows_global(
    backend: QueryBackend,
    scope: ComparisonScope,
    as_of_text: str,
) -> list[tuple[object, ...]]:
    """Fallback sin índice nuevo: rango de fila sobre la PK, una pasada lineal."""
    predicate, scope_args = _scope_predicate(scope)
    cursor: tuple[object, object, object] = (-1, "", "")
    collected: list[tuple[object, ...]] = []
    while True:
        rows = backend.query(
            f"""
            {_HISTORY_SELECT}
            WHERE ({predicate})
              AND {_HISTORY_FILTER}
              AND (h.product_id,h.location_id,h.valid_from_utc)>(?,?,?)
            ORDER BY h.product_id,h.location_id,h.valid_from_utc
            LIMIT {int(HISTORY_PAGE_SIZE)}
            """,
            (*scope_args, as_of_text, *cursor),
        )
        if not rows:
            break
        for row in rows:
            _validate_history_key(row)
        collected.extend(rows)
        cursor = (int(rows[-1][0]), str(rows[-1][2]), str(rows[-1][4]))
        if len(rows) < HISTORY_PAGE_SIZE:
            break
    return collected


def fetch_historical_points(
    backend: QueryBackend,
    scope: ComparisonScope,
    *,
    as_of_utc: datetime,
) -> dict[tuple[str, str], tuple[HistoricalPoint, ...]]:
    """Lee periodos con precio mediante keyset pagination, sin queries por producto."""
    as_of_text = as_of_utc.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    grouped: dict[tuple[str, str], list[HistoricalPoint]] = defaultdict(list)
    seen: set[tuple[int, str, str]] = set()
    if _history_index_available(backend):
        rows = _history_rows_by_location(backend, scope, as_of_text)
    else:
        rows = _history_rows_global(backend, scope, as_of_text)
    for product_id, supermarket_id, location_id, current_price, observed_at in rows:
        if (
            type(product_id) is not int
            or not isinstance(supermarket_id, str)
            or not isinstance(location_id, str)
            or type(current_price) is not int
            or current_price <= 0
        ):
            raise ExportError("consumer_catalog_history_invalid")
        observed = _parse_utc(observed_at, "consumer_catalog_history_timestamp_invalid")
        identity = (product_id, location_id, observed.isoformat())
        if identity in seen:
            raise ExportError("consumer_catalog_history_duplicate")
        seen.add(identity)
        source_id = f"{supermarket_id}:{product_id}"
        grouped[(source_id, location_id)].append(
            HistoricalPoint(source_id, supermarket_id, location_id, observed, current_price)
        )
    return {key: tuple(value) for key, value in grouped.items()}


def _historical_summary(
    offer: VisibleOffer,
    history: Sequence[HistoricalPoint],
    *,
    as_of_utc: datetime,
) -> dict[str, object] | None:
    if offer.current_price_minor is None or offer.current_price_minor <= 0 or not history:
        return None
    observations = tuple(
        HistoricalPriceObservation(
            point.source_product_id,
            point.supermarket_id,
            point.location_id,
            point.observed_at,
            point.current_price_minor,
        )
        for point in history
    )
    if observations[-1].price_minor != offer.current_price_minor:
        return None
    series = summarize_price_series(observations)
    windows = {
        item.window_days: item
        for item in summarize_price_windows(observations, as_of_utc=as_of_utc, windows=(30, 90))
    }

    def window_payload(days: int) -> dict[str, object]:
        window = windows[days]
        return {
            "status": "available" if window.sufficient_history else "insufficient_history",
            "observation_count": window.observation_count,
            "average": _money(window.mean_price_minor),
            "minimum": _money(window.minimum_price_minor),
            "maximum": _money(window.maximum_price_minor),
        }

    window_90 = windows[90]
    position = "insufficient_history"
    if window_90.sufficient_history:
        if window_90.current_price_minor == window_90.minimum_price_minor:
            position = "historically_low"
        elif window_90.current_vs_average_pct is not None and window_90.current_vs_average_pct < 0:
            position = "below_recent_average"
        elif window_90.current_vs_average_pct is not None and window_90.current_vs_average_pct > 0:
            position = "above_recent_average"
        else:
            position = "normal_range"
    return {
        "observation_count": series.observation_count,
        "previous_price": _money(observations[-2].price_minor) if len(observations) > 1 else None,
        "observed_minimum": _money(series.minimum_price_minor),
        "observed_maximum": _money(series.maximum_price_minor),
        "historical_position": position,
        "windows": {"30d": window_payload(30), "90d": window_payload(90)},
    }
def _row_id(prefix: str, value: str) -> str:
    return f"{prefix}-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]}"
def _category_key(value: str | None) -> str:
    identity = "__unknown__" if value is None else value
    return f"{_slug(value)}-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:8]}"
def _search_prefix(value: object) -> str:
    folded = unicodedata.normalize("NFKD", str(value or ""))
    normalized = re.sub(r"[^a-z0-9]+", "", "".join(char for char in folded if not unicodedata.combining(char)).casefold())
    return normalized[:2] or "__"
def _identity_groups(offers: Iterable[VisibleOffer]) -> list[tuple[str, list[VisibleOffer]]]:
    """Agrupa sólo IDs canónicos ready sin colisiones por supermercado."""
    values = tuple(offers)
    candidates: dict[str, list[VisibleOffer]] = defaultdict(list)
    individual: list[VisibleOffer] = []
    for offer in values:
        if offer.comparison_status == "ready" and offer.canonical_product_id:
            candidates[offer.canonical_product_id].append(offer)
        else:
            individual.append(offer)
    groups: list[tuple[str, list[VisibleOffer]]] = []
    for canonical_id, grouped in candidates.items():
        retailers = [offer.supermarket_id for offer in grouped]
        if len(grouped) >= 2 and len(retailers) == len(set(retailers)):
            groups.append(("comparable", grouped))
        else:
            individual.extend(grouped)
    groups.extend(("single_source" if offer.comparison_status == "single_source" else "individual", [offer]) for offer in individual)
    if sum(len(group) for _, group in groups) != len(values):
        raise ExportError("consumer_catalog_identity_partition_invalid")
    return groups
def _relative_states(
    mode: str,
    offers: Sequence[VisibleOffer],
    freshness_by_scope: dict[tuple[str, str], str],
) -> dict[str, str]:
    result = {offer.source_product_id: "neutral" for offer in offers}
    valid = [
        offer for offer in offers
        if offer.current_price_minor is not None
        and offer.current_price_minor > 0
        and offer.availability != "out_of_stock"
    ]
    if (
        mode != "comparable"
        or len(valid) < 2
        or any(freshness_by_scope[(offer.supermarket_id, offer.location_id)] != "FRESH" for offer in valid)
    ):
        return result
    prices = [offer.current_price_minor for offer in valid]
    if len(set(prices)) == 1:
        for offer in valid:
            result[offer.source_product_id] = "equivalent"
        return result
    minimum, maximum = min(prices), max(prices)
    for offer in valid:
        price = offer.current_price_minor
        result[offer.source_product_id] = "best" if price == minimum else "highest" if price == maximum else "intermediate"
    return result
def _representative(group: Sequence[VisibleOffer]) -> VisibleOffer:
    return sorted(
        group,
        key=lambda offer: (
            offer.product_type is None,
            offer.category is None,
            offer.brand is None,
            _derived_presentation(offer) is None,
            len(offer.product_name),
            offer.supermarket_id,
        ),
    )[0]
def build_rows(
    offers: Iterable[VisibleOffer],
    freshness_by_scope: dict[tuple[str, str], str],
    history_by_offer: dict[tuple[str, str], tuple[HistoricalPoint, ...]] | None = None,
    *,
    as_of_utc: datetime | None = None,
) -> list[dict[str, object]]:
    history_by_offer = history_by_offer or {}
    rows: list[dict[str, object]] = []
    for mode, group in _identity_groups(offers):
        representative = _representative(group)
        canonical_id = representative.canonical_product_id if mode == "comparable" else None
        identity_value = canonical_id or representative.source_product_id
        states = _relative_states(mode, group, freshness_by_scope)
        public_offers = []
        for offer in sorted(group, key=lambda item: EXPECTED_SCOPE.index((item.supermarket_id, item.location_id))):
            freshness = freshness_by_scope[(offer.supermarket_id, offer.location_id)]
            public_offers.append(
                {
                    "source_product_id": offer.source_product_id,
                    "supermarket_id": offer.supermarket_id,
                    "location_id": offer.location_id,
                    "current_price": _money(offer.current_price_minor),
                    "reported_regular_price": _money(offer.reported_regular_price_minor),
                    "is_promotion": offer.is_promotion,
                    "availability": offer.availability,
                    "observed_at": offer.observed_at,
                    "freshness_status": freshness,
                    "relative_price_state": states[offer.source_product_id],
                    "historical_summary": _historical_summary(
                        offer,
                        history_by_offer.get((offer.source_product_id, offer.location_id), ()),
                        as_of_utc=as_of_utc or _parse_utc(offer.observed_at, "consumer_catalog_offer_timestamp_invalid"),
                    ),
                }
            )
        category = representative.category
        row = {
            "row_id": _row_id("product" if canonical_id else "source", identity_value),
            "canonical_product_id": canonical_id,
            "comparability": mode,
            "category": category,
            "product_type": representative.product_type,
            "product_name": representative.product_name,
            "brand": representative.brand,
            "presentation": _derived_presentation(representative),
            "offers": public_offers,
        }
        if mode not in PUBLIC_COMPARABILITY:
            raise ExportError("consumer_catalog_public_comparability_invalid")
        rows.append(row)
    rows.sort(
        key=lambda row: tuple((str(row.get(key) or "")).casefold() for key in ("category", "product_type", "brand", "presentation", "product_name", "row_id"))
    )
    if len({str(row["row_id"]) for row in rows}) != len(rows):
        raise ExportError("consumer_catalog_row_id_collision")
    return rows


def build_consumer_analysis(
    rows: Sequence[dict[str, object]],
    *,
    as_of_utc: datetime,
) -> dict[str, object]:
    """Resume señales B2C ya validadas sin recalcular identidad en frontend."""

    retailers = {
        supermarket_id: {
            "supermarket_id": supermarket_id,
            "name": RETAILER_NAMES[supermarket_id],
            "visible_offers": 0,
            "active_promotions": 0,
            "comparable_products": 0,
            "best_price_wins": 0,
            "price_decreases": 0,
            "price_increases": 0,
            "unchanged_prices": 0,
        }
        for supermarket_id, _ in EXPECTED_SCOPE
    }
    categories: dict[str, dict[str, object]] = {}
    price_drops: list[dict[str, object]] = []
    price_increases: list[dict[str, object]] = []
    promotions: list[dict[str, object]] = []
    recent_lows: list[dict[str, object]] = []
    comparable_products = 0
    comparable_with_spread = 0
    unit_savings_minor = 0
    promotion_count = 0
    movements = {"decreased": 0, "increased": 0, "unchanged": 0, "insufficient_history": 0}

    def item(
        row: dict[str, object],
        offer: dict[str, object],
        current: int,
        **extra: object,
    ) -> dict[str, object]:
        return {
            "row_id": row["row_id"],
            "product_name": row["product_name"],
            "brand": row.get("brand"),
            "presentation": row.get("presentation"),
            "category": row.get("category"),
            "supermarket_id": offer["supermarket_id"],
            "retailer_name": RETAILER_NAMES[str(offer["supermarket_id"])],
            "current_price": _money(current),
            **extra,
        }

    for row in rows:
        offers = [offer for offer in row.get("offers", []) if isinstance(offer, dict)]
        fresh_prices: list[tuple[dict[str, object], int]] = []
        for offer in offers:
            supermarket_id = offer.get("supermarket_id")
            if supermarket_id not in retailers:
                raise ExportError("consumer_analysis_retailer_invalid")
            current = _money_minor(offer.get("current_price"))
            if current is None or offer.get("availability") == "out_of_stock":
                continue
            retailer = retailers[str(supermarket_id)]
            retailer["visible_offers"] = int(retailer["visible_offers"]) + 1
            if offer.get("freshness_status") == "FRESH":
                fresh_prices.append((offer, current))
            if offer.get("is_promotion") is True:
                promotion_count += 1
                retailer["active_promotions"] = int(retailer["active_promotions"]) + 1
                regular = _money_minor(offer.get("reported_regular_price"))
                promotions.append(
                    item(
                        row,
                        offer,
                        current,
                        reported_regular_price=_money(regular),
                        discount_pct=_discount_pct(current, regular) if regular is not None else None,
                    )
                )

            history = offer.get("historical_summary")
            if not isinstance(history, dict):
                movements["insufficient_history"] += 1
                continue
            previous = _money_minor(history.get("previous_price"))
            if history.get("historical_position") == "historically_low":
                recent_lows.append(item(row, offer, current))
            if previous is None:
                movements["insufficient_history"] += 1
                continue
            movement_item = item(
                row,
                offer,
                current,
                previous_price=_money(previous),
                change_pct=_change_pct(current, previous),
            )
            if current < previous:
                movements["decreased"] += 1
                retailer["price_decreases"] = int(retailer["price_decreases"]) + 1
                price_drops.append(movement_item)
            elif current > previous:
                movements["increased"] += 1
                retailer["price_increases"] = int(retailer["price_increases"]) + 1
                price_increases.append(movement_item)
            else:
                movements["unchanged"] += 1
                retailer["unchanged_prices"] = int(retailer["unchanged_prices"]) + 1

        if row.get("comparability") != "comparable" or len(fresh_prices) < 2:
            continue
        comparable_products += 1
        for offer, _ in fresh_prices:
            retailer = retailers[str(offer["supermarket_id"])]
            retailer["comparable_products"] = int(retailer["comparable_products"]) + 1
        prices = [price for _, price in fresh_prices]
        minimum, maximum = min(prices), max(prices)
        winners = sorted(
            str(offer["supermarket_id"])
            for offer, price in fresh_prices
            if price == minimum
        )
        for supermarket_id in winners:
            retailer = retailers[supermarket_id]
            retailer["best_price_wins"] = int(retailer["best_price_wins"]) + 1
        spread = maximum - minimum
        if spread > 0:
            comparable_with_spread += 1
            unit_savings_minor += spread
        category_name = row.get("category") if isinstance(row.get("category"), str) else "Sin categoría normalizada"
        category = categories.setdefault(
            category_name,
            {"category": category_name, "comparable_products": 0, "unit_savings_minor": 0, "wins": defaultdict(int)},
        )
        category["comparable_products"] = int(category["comparable_products"]) + 1
        category["unit_savings_minor"] = int(category["unit_savings_minor"]) + spread
        for supermarket_id in winners:
            category["wins"][supermarket_id] += 1

    retailer_rows = []
    for retailer in retailers.values():
        visible = int(retailer["visible_offers"])
        comparable = int(retailer["comparable_products"])
        retailer_rows.append(
            {
                **retailer,
                "promotion_rate_pct": (
                    format((Decimal(int(retailer["active_promotions"])) * 100 / Decimal(visible)).quantize(Decimal("0.01")), ".2f")
                    if visible
                    else None
                ),
                "best_price_rate_pct": (
                    format((Decimal(int(retailer["best_price_wins"])) * 100 / Decimal(comparable)).quantize(Decimal("0.01")), ".2f")
                    if comparable
                    else None
                ),
            }
        )

    category_rows = []
    for category in categories.values():
        wins = dict(sorted(category["wins"].items()))
        maximum_wins = max(wins.values(), default=0)
        leaders = [RETAILER_NAMES[key] for key, value in wins.items() if value == maximum_wins]
        category_rows.append(
            {
                "category": category["category"],
                "comparable_products": category["comparable_products"],
                "unit_savings": _nonnegative_money(int(category["unit_savings_minor"])),
                "best_price_leaders": leaders,
                "best_price_wins": wins,
            }
        )
    category_rows.sort(key=lambda value: (-int(value["comparable_products"]), str(value["category"])))

    price_drops.sort(key=lambda value: (Decimal(str(value["change_pct"])), str(value["product_name"])))
    price_increases.sort(key=lambda value: (-Decimal(str(value["change_pct"])), str(value["product_name"])))
    promotions.sort(
        key=lambda value: (
            value["discount_pct"] is None,
            -Decimal(str(value["discount_pct"] or "0")),
            str(value["product_name"]),
        )
    )
    recent_lows.sort(key=lambda value: (_money_minor(value["current_price"]) or 0, str(value["product_name"])))
    return {
        "schema": ANALYSIS_SCHEMA,
        "as_of": as_of_utc.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "location": {"city": "San Pedro Sula", "country_code": "HN"},
        "summary": {
            "visible_products": len(rows),
            "comparable_products": comparable_products,
            "comparable_products_with_price_spread": comparable_with_spread,
            "active_promotions": promotion_count,
            "observed_unit_savings": _nonnegative_money(unit_savings_minor),
            "price_movements": movements,
            "recent_low_opportunities": len(recent_lows),
        },
        "retailers": retailer_rows,
        "categories": category_rows,
        "opportunities": {
            "price_drops": price_drops[:12],
            "price_increases": price_increases[:12],
            "promotions": promotions[:12],
            "recent_lows": recent_lows[:12],
        },
        "methodology": {
            "identity": "only_persisted_ready_cross_retailer_identity",
            "freshness": "competitive_metrics_require_fresh_offers",
            "savings": "sum_of_current_unit_price_spreads_not_a_household_basket",
            "promotions": "retailer_reported_active_promotions",
            "movements": "current_price_vs_previous_accepted_observation",
        },
    }
def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
def _atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(path)
def _file_metadata(root: Path, relative: str) -> dict[str, object]:
    content = (root / relative).read_bytes()
    return {
        "path": relative,
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
        "gzip_bytes": len(gzip.compress(content, mtime=0)),
    }
def export_consumer_catalog(
    backend: QueryBackend,
    scope: ComparisonScope,
    output_directory: Path,
    *,
    as_of_utc: datetime,
    freshness_window: timedelta,
    require_products: bool = False,
) -> dict[str, object]:
    if tuple(scope.locations) != EXPECTED_SCOPE:
        raise ExportError("consumer_catalog_sps_scope_invalid")
    offers = fetch_visible_offers(backend, scope)
    retailer_offer_counts = {
        supermarket_id: sum(offer.supermarket_id == supermarket_id for offer in offers)
        for supermarket_id, _ in scope.locations
    }
    if require_products and (not offers or any(count == 0 for count in retailer_offer_counts.values())):
        raise ExportError("consumer_catalog_missing_visible_retailer")
    freshness = fetch_freshness(
        backend,
        scope,
        as_of_utc=as_of_utc,
        freshness_window=freshness_window,
    )
    freshness_by_scope = {
        (item.source_id, item.location_id): item.freshness_status.value for item in freshness
    }
    history = fetch_historical_points(backend, scope, as_of_utc=as_of_utc)
    rows = build_rows(
        offers,
        freshness_by_scope,
        history,
        as_of_utc=as_of_utc,
    )
    analysis = build_consumer_analysis(rows, as_of_utc=as_of_utc)
    output_directory.mkdir(parents=True, exist_ok=True)
    categories: dict[str | None, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        categories[row["category"] if isinstance(row["category"], str) else None].append(row)
    partition_paths: list[str] = []
    for category, category_rows in sorted(categories.items(), key=lambda item: (item[0] is None, str(item[0]).casefold())):
        category_key = _category_key(category)
        navigation_groups: dict[str | None, list[dict[str, object]]] = defaultdict(list)
        for row in category_rows:
            key = _search_prefix(row["product_name"]) if category is None else row["product_type"]
            navigation_groups[key if isinstance(key, str) else None].append(row)
        for navigation, group in sorted(navigation_groups.items(), key=lambda item: (item[0] is None, str(item[0]).casefold())):
            navigation_key = f"search-{navigation}" if category is None else _category_key(navigation)
            for number, start in enumerate(range(0, len(group), MAX_PARTITION_ROWS), start=1):
                chunk = group[start : start + MAX_PARTITION_ROWS]
                relative = f"catalog/{category_key}/{navigation_key}/part-{number:03d}.json"
                _atomic_bytes(
                    output_directory / relative,
                    _json_bytes({"schema": PARTITION_SCHEMA, "partition": relative, "row_count": len(chunk), "rows": chunk}),
                )
                partition_paths.append(relative)
                for row in chunk:
                    row["_partition"] = relative
    index_paths: list[str] = []
    category_facets: list[dict[str, object]] = []
    for category, category_rows in sorted(categories.items(), key=lambda item: (item[0] is None, str(item[0]).casefold())):
        category_key = _category_key(category)
        if category is None:
            prefix_groups: dict[str, list[dict[str, object]]] = defaultdict(list)
            for row in category_rows:
                prefix_groups[_search_prefix(row["product_name"])].append(row)
            search_indexes = []
            for prefix, group in sorted(prefix_groups.items()):
                relative = f"index/{category_key}/search-{prefix}.json"
                entries = [{key: row[key] for key in ("row_id", "product_name", "brand", "presentation")} | {"partition": row["_partition"]} for row in group]
                _atomic_bytes(output_directory / relative, _json_bytes({"schema": INDEX_SCHEMA, "category": None, "search_prefix": prefix, "row_count": len(entries), "rows": entries}))
                index_paths.append(relative)
                search_indexes.append({"prefix": prefix, "path": relative, "row_count": len(entries)})
            category_facets.append({"value": None, "label": "Sin categoría normalizada", "row_count": len(category_rows), "navigation": "search", "search_indexes": search_indexes})
            continue
        types: dict[str | None, list[dict[str, object]]] = defaultdict(list)
        for row in category_rows:
            types[row["product_type"] if isinstance(row["product_type"], str) else None].append(row)
        type_facets = []
        for product_type, group in sorted(types.items(), key=lambda item: (item[0] is None, str(item[0]).casefold())):
            relative = f"index/{category_key}/{_category_key(product_type)}.json"
            entries = [{key: row[key] for key in ("row_id", "product_name", "brand", "presentation")} | {"partition": row["_partition"]} for row in group]
            _atomic_bytes(output_directory / relative, _json_bytes({"schema": INDEX_SCHEMA, "category": category, "product_type": product_type, "row_count": len(entries), "rows": entries}))
            index_paths.append(relative)
            type_facets.append({"value": product_type, "label": product_type or "Sin tipo normalizado", "row_count": len(entries), "index_path": relative})
        category_facets.append({"value": category, "label": category, "row_count": len(category_rows), "navigation": "facets", "product_types": type_facets})
    for row in rows:
        row.pop("_partition", None)
    def covered(field: str) -> int:
        return sum(bool(row.get(field)) for row in rows)
    facets = {
        "schema": FACETS_SCHEMA,
        "location": {"city": "San Pedro Sula", "country_code": "HN"},
        "row_count": len(rows),
        "coverage": {
            field: {"known": covered(field), "unknown": len(rows) - covered(field)}
            for field in ("category", "product_type", "brand", "presentation")
        },
        "categories": category_facets,
    }
    _atomic_bytes(output_directory / "facets-sps.json", _json_bytes(facets))
    _atomic_bytes(output_directory / "analysis-sps.json", _json_bytes(analysis))
    data_files = ["facets-sps.json", "analysis-sps.json", *index_paths, *partition_paths]
    metadata = [_file_metadata(output_directory, relative) for relative in data_files]
    offer_count = sum(len(row["offers"]) for row in rows)
    mode_counts = {
        mode: sum(row["comparability"] == mode for row in rows)
        for mode in sorted(PUBLIC_COMPARABILITY)
    }
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "catalog_schema": SCHEMA,
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "as_of": as_of_utc.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_backend": backend.kind,
        "location": {"city": "San Pedro Sula", "country_code": "HN"},
        "scope": [
            {"supermarket_id": supermarket_id, "location_id": location_id}
            for supermarket_id, location_id in scope.locations
        ],
        "retailers": [
            {"supermarket_id": supermarket_id, "location_id": location_id, "name": RETAILER_NAMES[supermarket_id]}
            for supermarket_id, location_id in scope.locations
        ],
        "visibility_policy": "accepted_current_source_offers_in_sps_scope",
        "comparison_policy": "persisted_ready_identity_without_retailer_collision_and_fresh_prices",
        "visible_rows": len(rows),
        "source_offers": offer_count,
        "offers_with_historical_summary": sum(
            offer["historical_summary"] is not None
            for row in rows
            for offer in row["offers"]
        ),
        "retailer_offer_counts": retailer_offer_counts,
        "comparability_counts": mode_counts,
        "partition_count": len(partition_paths),
        "max_partition_rows": MAX_PARTITION_ROWS,
        "initial_files": ["facets-sps.json"],
        "analysis_file": "analysis-sps.json",
        "files": metadata,
        "initial_payload": {
            "bytes": next(item["bytes"] for item in metadata if item["path"] == "facets-sps.json"),
            "gzip_bytes": next(item["gzip_bytes"] for item in metadata if item["path"] == "facets-sps.json"),
            "request_count": 2,
        },
        "public_boundary": {
            "direct_turso_reads": 0,
            "contains_business_mart": False,
            "contains_raw": False,
            "contains_review_queue": False,
        },
        "source_freshness": [
            {
                "supermarket_id": item.source_id,
                "location_id": item.location_id,
                "status": item.freshness_status.value,
                "last_successful_run": item.last_successful_run_id,
                "last_successful_at": None if item.observed_at_utc is None else item.observed_at_utc.isoformat().replace("+00:00", "Z"),
                "data_age_hours": item.data_age_hours,
            }
            for item in freshness
        ],
    }
    _atomic_bytes(output_directory / "manifest.json", _json_bytes(manifest))
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
        raise ExportError("consumer_catalog_freshness_hours_invalid")
    as_of = datetime.now(timezone.utc) if args.as_of_utc is None else _parse_utc(args.as_of_utc, "consumer_catalog_as_of_invalid")
    scope = parse_scope(args.scope)
    if args.sqlite is not None:
        if not args.sqlite.is_file():
            raise ExportError("sqlite_file_missing")
        backend = SQLiteBackend(args.sqlite)
    else:
        import os
        backend = TursoBackend(os.environ.get("TURSO_DATABASE_URL", ""), os.environ.get("TURSO_AUTH_TOKEN", ""))
    try:
        manifest = export_consumer_catalog(
            backend,
            scope,
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
