"""Optimización Turso (rows read): equivalencia exacta y lecturas acotadas.

Compara la paginación histórica ``product_id>? OR (product_id=? AND location_id>?)``
contra la paginación nueva por contexto exacto sobre una base SQLite con el esquema
productivo, empates de ``product_id`` entre varias ubicaciones y límites de página.
También verifica que los caminos diarios (migración Paiz, homologación y
verificación del workflow) ya no recorren tablas completas.
"""
from __future__ import annotations

import json
import random
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import actualizar_mvp_sqlite_la_colonia as sqlite_updater  # noqa: E402
import actualizar_mvp_turso_la_colonia as turso_updater  # noqa: E402
import backfill_homologacion_turso as homologation  # noqa: E402
import exportar_consumer_catalog as facade  # noqa: E402
import exportar_consumer_catalog_core as core  # noqa: E402
import exportar_consumer_catalog_tgu as tgu  # noqa: E402
import exportar_modelo_analitico as modelo  # noqa: E402
import migrar_mvp_paiz as paiz  # noqa: E402
import verificar_integridad_turso as integrity  # noqa: E402
from exportar_rpi_marts import _parse_utc  # noqa: E402
from generar_mvp_sqlite_la_colonia import HISTORY_INDEX_NAME, HISTORY_INDEX_SQL, create_schema  # noqa: E402
from precios_supermercados.price_analytics import ComparisonScope, CurrentPriceObservation  # noqa: E402
from precios_supermercados.product_homologation_persistence import (  # noqa: E402
    NORMALIZATION_VERSION,
    SCHEMA_SQL as PROFILE_SCHEMA_SQL,
)

AS_OF = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
CONTEXTS = (
    ("la_colonia", "la_colonia_sps", "San Pedro Sula"),
    ("la_colonia", "la_colonia_tgu", "Tegucigalpa"),
    ("colonial", "colonial_sps", "San Pedro Sula"),
    ("walmart", "walmart_sps", "San Pedro Sula"),
    ("walmart", "walmart_tgu_ffaa", "Tegucigalpa"),
    ("walmart", "walmart_tgu_el_sauce", "Tegucigalpa"),
    ("pricesmart", "pricesmart_sps", "San Pedro Sula"),
    ("pricesmart", "pricesmart_tgu", "Tegucigalpa"),
    ("comisariato_los_andes", "comisariato_los_andes_sps", "San Pedro Sula"),
    ("paiz", "paiz_tgu_multiplaza", "Tegucigalpa Multiplaza"),
    ("paiz", "paiz_tgu_proceres", "Tegucigalpa Proceres"),
)
SUPERMARKETS = tuple(dict.fromkeys(context[0] for context in CONTEXTS))


class MultiContextScope:
    """Scope con varias ubicaciones por cadena (como CityCatalogScope TGU)."""

    def __init__(self, locations: tuple[tuple[str, str], ...]) -> None:
        self.locations = locations

    @property
    def supermarket_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(s for s, _ in self.locations))


SPS_SCOPE = ComparisonScope(tuple(core.EXPECTED_SCOPE))
TGU_LIKE_SCOPE = MultiContextScope(tuple(tgu.TGU_SCOPE))
ALL_SCOPE = MultiContextScope(tuple((s, l) for s, l, _ in CONTEXTS))
SCOPES = {"sps": SPS_SCOPE, "tgu": TGU_LIKE_SCOPE, "all": ALL_SCOPE}


def build_db(path: Path, *, products_per_supermarket: int = 9, history_index: bool = True, seed: int = 7) -> None:
    rng = random.Random(seed)
    con = sqlite3.connect(path)
    try:
        create_schema(con)
        con.executescript(PROFILE_SCHEMA_SQL)
        if history_index:
            con.execute(HISTORY_INDEX_SQL)
        for supermarket in SUPERMARKETS:
            con.execute("INSERT INTO supermarkets VALUES(?,?,'HN')", (supermarket, supermarket.title()))
        for supermarket, location, city in CONTEXTS:
            con.execute("INSERT INTO locations VALUES(?,?,?,'HN')", (location, supermarket, city))
            con.execute(
                "INSERT INTO scrape_runs VALUES(?,?,?,?,'success',1,1,NULL,NULL,NULL)",
                (f"run-{location}", supermarket, location, "2026-09-20T10:00:00Z"),
            )
        by_supermarket = defaultdict(list)
        for supermarket, location, _ in CONTEXTS:
            by_supermarket[supermarket].append(location)
        product_id = 0
        statuses = ("ready", "single_source", "review_required", "unmapped")
        # Round-robin: los product_id intercalan cadenas y cada producto de una
        # cadena con varias sucursales produce empates de product_id entre ubicaciones.
        for index in range(products_per_supermarket):
            for supermarket in SUPERMARKETS:
                product_id += 1
                con.execute(
                    "INSERT INTO products VALUES(?,?,'item_id',?,?,?,NULL,NULL,?,?,?,?)",
                    (
                        product_id, supermarket, str(product_id), str(product_id), str(product_id),
                        f"Producto {index} {supermarket} 1 L", "Marca", "1 L", "Bebidas",
                    ),
                )
                status = statuses[product_id % len(statuses)]
                gtin = None if status == "unmapped" else f"gtin:{product_id % 5}"
                con.execute(
                    """INSERT INTO product_homologation_profiles(
                        product_id,supermarket_id,normalized_name,normalized_brand,canonical_gtin,
                        canonical_product_id,category,product_type,presentation_status,comparison_status,
                        conflict_reasons_json,normalization_version,profile_hash,updated_at_utc,
                        source_brand_role,brand_resolution_source,display_presentation)
                       VALUES(?,?,?,?,?,?,?,?,'missing',?,'[]',?,?,?,'unknown','missing',?)""",
                    (
                        product_id, supermarket, f"producto {index}", "marca", gtin, gtin,
                        "Bebidas", "Agua", status, NORMALIZATION_VERSION, "a" * 64,
                        "2026-09-20T00:00:00Z", "1 L",
                    ),
                )
                for location in by_supermarket[supermarket]:
                    if rng.random() < 0.15:
                        continue
                    periods = rng.randint(1, 4)
                    still_open = rng.random() < 0.9
                    day = rng.randint(1, 5)
                    for period in range(periods):
                        start = f"2026-09-{day:02d}T10:00:00Z"
                        day += rng.randint(1, 4)
                        closed = period < periods - 1 or not still_open
                        end = f"2026-09-{day:02d}T10:00:00Z" if closed else None
                        out_of_stock = supermarket == "walmart" and rng.random() < 0.1
                        price = None if out_of_stock else rng.randint(1, 50) * 100
                        con.execute(
                            "INSERT INTO price_history VALUES(?,?,?,?,?,?,?,'HNL',?,?,?)",
                            (
                                product_id, supermarket, location, price,
                                None if price is None else price + 100,
                                None if price is None else rng.randint(0, 1),
                                "out_of_stock" if out_of_stock else "in_stock",
                                start, end, f"run-{location}",
                            ),
                        )
        con.commit()
    finally:
        con.close()


# --- Implementaciones previas (copia literal del keyset OR) --------------------


def legacy_fetch_visible_offers(backend, scope, *, page_size: int = 2000):
    predicate, scope_args = core._scope_predicate(scope)
    cursor_product = -1
    cursor_location = ""
    result = []
    seen = set()
    while True:
        rows = backend.query(
            f"""
            SELECT p.product_id,p.supermarket_id,p.name,
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
              ON hp.product_id=p.product_id AND hp.supermarket_id=p.supermarket_id
            WHERE h.valid_to_utc IS NULL
              AND ({predicate})
              AND (p.product_id>? OR (p.product_id=? AND h.location_id>?))
            ORDER BY p.product_id,h.location_id
            LIMIT {page_size}
            """,
            (*scope_args, cursor_product, cursor_product, cursor_location),
        )
        if not rows:
            break
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
                raise core.ExportError("consumer_catalog_offer_duplicate")
            seen.add(key)
            if normalization_version != NORMALIZATION_VERSION:
                raise core.ExportError("consumer_catalog_offer_invalid")
            result.append(
                core.VisibleOffer(
                    source_product_id=f"{supermarket_id}:{product_id}",
                    supermarket_id=supermarket_id,
                    location_id=location_id,
                    product_name=core._text(name) or "",
                    brand=core._display_brand(brand),
                    presentation=core._text(presentation),
                    current_price_minor=current_price,
                    reported_regular_price_minor=regular_price,
                    is_promotion=None if is_promotion is None else bool(is_promotion),
                    availability=availability,
                    observed_at=core._text(observed_at) or "",
                    canonical_product_id=core._text(canonical_product_id),
                    category=core._text(category),
                    product_type=core._text(product_type),
                    presentation_dimension=core._text(presentation_dimension),
                    presentation_total_base=core._text(presentation_total_base),
                    presentation_status=str(presentation_status),
                    comparison_status=str(comparison_status),
                )
            )
        cursor_product = int(rows[-1][0])
        cursor_location = str(rows[-1][5])
        if len(rows) < page_size:
            break
    return tuple(result)


def legacy_fetch_historical_points(backend, scope, *, as_of_utc, page_size: int = 5000):
    predicate, scope_args = core._scope_predicate(scope)
    as_of_text = as_of_utc.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    cursor_product, cursor_location, cursor_observed = -1, "", ""
    grouped = defaultdict(list)
    seen = set()
    while True:
        rows = backend.query(
            f"""
            SELECT h.product_id,h.supermarket_id,h.location_id,h.current_price_minor,h.valid_from_utc
            FROM price_history AS h
            WHERE ({predicate})
              AND current_price_minor IS NOT NULL
              AND current_price_minor > 0
              AND julianday(valid_from_utc)<=julianday(?)
              AND (
                product_id>?
                OR (product_id=? AND location_id>?)
                OR (product_id=? AND location_id=? AND valid_from_utc>?)
              )
            ORDER BY product_id,location_id,valid_from_utc
            LIMIT {page_size}
            """,
            (
                *scope_args, as_of_text, cursor_product, cursor_product, cursor_location,
                cursor_product, cursor_location, cursor_observed,
            ),
        )
        if not rows:
            break
        for product_id, supermarket_id, location_id, current_price, observed_at in rows:
            observed = _parse_utc(observed_at, "consumer_catalog_history_timestamp_invalid")
            identity = (product_id, location_id, observed.isoformat())
            if identity in seen:
                raise core.ExportError("consumer_catalog_history_duplicate")
            seen.add(identity)
            source_id = f"{supermarket_id}:{product_id}"
            grouped[(source_id, location_id)].append(
                core.HistoricalPoint(source_id, supermarket_id, location_id, observed, current_price)
            )
        cursor_product = int(rows[-1][0])
        cursor_location = str(rows[-1][2])
        cursor_observed = str(rows[-1][4])
        if len(rows) < page_size:
            break
    return {key: tuple(value) for key, value in grouped.items()}


def legacy_fetch_current_observations(backend, scope, *, page_size: int = 2000):
    predicate, scope_args = modelo._scope_predicate(scope)
    cursor_product = -1
    cursor_location = ""
    result = []
    while True:
        rows = backend.query(
            f"""
            SELECT h.product_id,h.supermarket_id,h.location_id,h.current_price_minor,h.availability
            FROM price_history AS h
            WHERE h.valid_to_utc IS NULL
              AND ({predicate})
              AND (h.product_id>? OR (h.product_id=? AND h.location_id>?))
            ORDER BY h.product_id,h.location_id
            LIMIT {page_size}
            """,
            (*scope_args, cursor_product, cursor_product, cursor_location),
        )
        if not rows:
            break
        for product_id, supermarket_id, location_id, current_price_minor, availability in rows:
            result.append(
                CurrentPriceObservation(
                    source_record_id=f"{supermarket_id}:{product_id}",
                    supermarket_id=supermarket_id,
                    location_id=location_id,
                    price_minor=current_price_minor,
                    availability=availability if isinstance(availability, str) else None,
                )
            )
        cursor_product = int(rows[-1][0])
        cursor_location = str(rows[-1][2])
        if len(rows) < page_size:
            break
    return tuple(result)


class RecordingBackend:
    kind = "sqlite"

    def __init__(self, path: Path) -> None:
        self.inner = modelo.SQLiteBackend(path)
        self.connection = self.inner.connection
        self.queries: list[str] = []
        self.vm_steps = 0

    def query(self, sql, args=()):
        self.queries.append(" ".join(sql.split()))

        def progress() -> int:
            self.vm_steps += 1
            return 0

        self.connection.set_progress_handler(progress, 1)
        try:
            return self.inner.query(sql, args)
        finally:
            self.connection.set_progress_handler(None, 1)

    def close(self) -> None:
        self.inner.close()


def _set_page_sizes(monkeypatch: pytest.MonkeyPatch, size: int) -> None:
    monkeypatch.setattr(core, "VISIBLE_OFFERS_PAGE_SIZE", size)
    monkeypatch.setattr(core, "HISTORY_PAGE_SIZE", size)
    monkeypatch.setattr(modelo, "CURRENT_PAGE_SIZE", size)


@pytest.mark.parametrize("history_index", [True, False])
@pytest.mark.parametrize("page_size", [1, 2, 3, 7, 2000])
@pytest.mark.parametrize("scope_name", sorted(SCOPES))
def test_new_pagination_returns_exactly_legacy_rows_in_same_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, history_index: bool, page_size: int, scope_name: str
) -> None:
    db = tmp_path / "rpi.sqlite"
    build_db(db, history_index=history_index)
    scope = SCOPES[scope_name]
    _set_page_sizes(monkeypatch, page_size)
    backend = RecordingBackend(db)
    try:
        legacy_offers = legacy_fetch_visible_offers(backend, scope, page_size=page_size)
        legacy_history = legacy_fetch_historical_points(backend, scope, as_of_utc=AS_OF, page_size=page_size)
        legacy_current = legacy_fetch_current_observations(backend, scope, page_size=page_size)
        backend.queries.clear()

        offers = core.fetch_visible_offers(backend, scope)
        history = core.fetch_historical_points(backend, scope, as_of_utc=AS_OF)
        current = modelo.fetch_current_observations(backend, scope)
    finally:
        backend.close()

    assert len(legacy_offers) > 10
    assert offers == legacy_offers
    assert list(history.items()) == list(legacy_history.items())
    assert current == legacy_current
    # Hay empates de product_id entre varias ubicaciones en los scopes multi-sucursal.
    if scope_name != "sps":
        ids = [offer.source_product_id for offer in offers]
        assert len(ids) != len(set(ids))
    # Ninguna consulta nueva usa el keyset OR no indexable.
    assert not any(" OR (" in query and "product_id=?" in query for query in backend.queries)
    per_location_history = any("(h.product_id,h.valid_from_utc)>(?,?)" in q for q in backend.queries)
    assert per_location_history is history_index


def test_new_queries_use_range_indexes(tmp_path: Path) -> None:
    db = tmp_path / "rpi.sqlite"
    build_db(db, history_index=True)
    con = sqlite3.connect(db)
    try:
        current = f"""{core._VISIBLE_OFFERS_SELECT}
            WHERE h.valid_to_utc IS NULL AND h.supermarket_id=? AND h.location_id=? AND h.product_id>?
            ORDER BY h.product_id LIMIT 2000"""
        plan = str(con.execute("EXPLAIN QUERY PLAN " + current, ("walmart", "walmart_sps", -1)).fetchall())
        assert "idx_price_history_current (location_id=? AND product_id>?)" in plan
        history = f"""{core._HISTORY_SELECT}
            WHERE h.supermarket_id=? AND h.location_id=? AND {core._HISTORY_FILTER}
              AND (h.product_id,h.valid_from_utc)>(?,?)
            ORDER BY h.product_id,h.valid_from_utc LIMIT 5000"""
        plan = str(con.execute("EXPLAIN QUERY PLAN " + history, ("walmart", "walmart_sps", "x", -1, "")).fetchall())
        assert f"{HISTORY_INDEX_NAME} (location_id=? AND (product_id,valid_from_utc)>(?,?))" in plan
        assert "TEMP B-TREE" not in plan
    finally:
        con.close()


def test_new_pagination_reads_far_fewer_rows_than_legacy_keyset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "rpi.sqlite"
    build_db(db, products_per_supermarket=120, history_index=True)
    _set_page_sizes(monkeypatch, 50)
    backend = RecordingBackend(db)
    try:
        legacy_offers = legacy_fetch_visible_offers(backend, ALL_SCOPE, page_size=50)
        legacy_history = legacy_fetch_historical_points(backend, ALL_SCOPE, as_of_utc=AS_OF, page_size=50)
        legacy_steps = backend.vm_steps
        backend.vm_steps = 0
        offers = core.fetch_visible_offers(backend, ALL_SCOPE)
        history = core.fetch_historical_points(backend, ALL_SCOPE, as_of_utc=AS_OF)
        new_steps = backend.vm_steps
    finally:
        backend.close()
    assert offers == legacy_offers
    assert list(history.items()) == list(legacy_history.items())
    assert new_steps * 3 < legacy_steps


def _tree(path: Path) -> dict[str, bytes]:
    result = {}
    for item in sorted(path.rglob("*")):
        if item.is_file():
            content = item.read_bytes()
            if item.name == "manifest.json":
                document = json.loads(content)
                document.pop("generated_at_utc", None)
                content = json.dumps(document, sort_keys=True).encode()
            result[str(item.relative_to(path))] = content
    return result


@pytest.mark.parametrize("history_index", [True, False])
def test_consumer_catalog_export_is_byte_identical_to_legacy_pagination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, history_index: bool
) -> None:
    db = tmp_path / "rpi.sqlite"
    build_db(db, products_per_supermarket=40, history_index=history_index)
    _set_page_sizes(monkeypatch, 7)
    scope = facade.parse_scope(f"{s}={l}" for s, l in core.EXPECTED_SCOPE)

    def run(output: Path) -> None:
        backend = modelo.SQLiteBackend(db)
        try:
            facade.export_consumer_catalog(
                backend, scope, output, as_of_utc=AS_OF, freshness_window=timedelta(hours=48),
            )
        finally:
            backend.close()

    for name in ("fetch_visible_offers", "fetch_historical_points"):
        monkeypatch.setattr(core, name, getattr(core, name))
    with monkeypatch.context() as patch:
        patch.setattr(facade, "fetch_visible_offers", lambda b, s: legacy_fetch_visible_offers(b, s, page_size=7))
        patch.setattr(
            facade, "fetch_historical_points",
            lambda b, s, *, as_of_utc: legacy_fetch_historical_points(b, s, as_of_utc=as_of_utc, page_size=7),
        )
        run(tmp_path / "legacy")
    run(tmp_path / "new")
    legacy = _tree(tmp_path / "legacy")
    assert len(legacy) > 3
    assert _tree(tmp_path / "new") == legacy


def test_tgu_export_reads_visible_offers_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "rpi.sqlite"
    build_db(db, products_per_supermarket=12)
    base = tgu.base
    # _patch_base_for_tgu muta globals del módulo; restaurarlos al terminar.
    for module in (base, base._core):
        for name in (
            "EXPECTED_SCOPE", "RETAILER_NAMES", "fetch_visible_offers", "fetch_historical_points",
            "_identity_groups", "build_rows", "_derived_presentation",
        ):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, getattr(module, name))
    backend = RecordingBackend(db)
    try:
        manifest = tgu.export_tgu_catalog(
            backend, tmp_path / "tgu", as_of_utc=AS_OF,
            freshness_window=timedelta(hours=48), require_products=True,
        )
    finally:
        backend.close()
    visible_queries = [q for q in backend.queries if "JOIN product_homologation_profiles" in q]
    assert len(visible_queries) == len(tgu.TGU_SCOPE)
    assert all(item["offer_count"] > 0 for item in manifest["context_offer_counts"])
    # La envoltura de reutilización no queda instalada después del export.
    assert base.fetch_visible_offers.__name__ == "fetch_visible"


# --- Fake Hrana: ejecuta el protocolo Turso contra SQLite local -----------------


def _encode(value: object) -> dict[str, object]:
    if value is None:
        return {"type": "null"}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": value}
    return {"type": "text", "value": str(value)}


def _decode(arg: dict[str, Any]) -> object:
    if arg["type"] == "null":
        return None
    if arg["type"] == "integer":
        return int(arg["value"])
    return arg["value"]


class FakeTurso:
    def __init__(self, path: Path) -> None:
        self.con = sqlite3.connect(path, isolation_level=None)
        self.sql: list[str] = []

    def pipeline(self, _url: str, _token: str, requests: list[dict[str, Any]]) -> dict[str, Any]:
        results = []
        for request in requests:
            if request["type"] == "close":
                results.append({"type": "ok", "response": {"type": "close"}})
                continue
            stmt = request["stmt"]
            self.sql.append(" ".join(stmt["sql"].split()))
            rows = self.con.execute(stmt["sql"], [_decode(arg) for arg in stmt["args"]]).fetchall()
            results.append({
                "type": "ok",
                "response": {"type": "execute", "result": {"rows": [[_encode(v) for v in row] for row in rows]}},
            })
        return {"results": results}

    def run_batch(self, _url: str, _token: str, steps):
        out = []
        for _, sql, args in steps:
            self.sql.append(" ".join(sql.split()))
            cursor = self.con.execute(sql, args)
            out.append({"affected_row_count": cursor.rowcount})
        return out


FULL_SCAN_MARKERS = (
    "PRAGMA integrity_check",
    "FROM pragma_foreign_key_check",
    "COUNT(*) FROM price_history",
    "GROUP BY product_id,location_id HAVING",
)


def _assert_no_full_scans(statements: list[str]) -> None:
    for statement in statements:
        for marker in FULL_SCAN_MARKERS:
            if marker == "FROM pragma_foreign_key_check" and "pragma_foreign_key_check('" in statement:
                continue
            assert marker not in statement, statement


def test_paiz_migration_is_cheap_noop_once_applied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "mvp.sqlite"
    con = sqlite3.connect(db)
    create_schema(con)
    con.commit()
    con.close()
    first = paiz.migrate_sqlite(db)
    assert first["migrated"] is True and first["history_index_created"] is True

    fake = FakeTurso(db)
    monkeypatch.setattr(paiz, "_pipeline", fake.pipeline)
    monkeypatch.setattr(paiz, "_run_batch", fake.run_batch)
    result = paiz.migrate_turso("libsql://offline.example", "offline")
    assert result == {
        "migrated": False,
        "price_history_migrated": False,
        "locations_index_migrated": False,
        "history_index_created": False,
        "already_applied": True,
        "locations": ["paiz_tgu_multiplaza", "paiz_tgu_proceres"],
    }
    assert all(statement.startswith("SELECT") for statement in fake.sql)
    assert not any("COUNT(" in statement for statement in fake.sql)
    _assert_no_full_scans(fake.sql)

    # Primera corrida tras el despliegue: sólo crea el índice histórico.
    fake.con.execute(f"DROP INDEX {HISTORY_INDEX_NAME}")
    fake.sql.clear()
    result = paiz.migrate_turso("libsql://offline.example", "offline")
    assert result["history_index_created"] is True and result["already_applied"] is True
    assert HISTORY_INDEX_SQL in fake.sql
    _assert_no_full_scans(fake.sql)
    assert fake.con.execute(
        "SELECT sql FROM sqlite_master WHERE name=?", (HISTORY_INDEX_NAME,)
    ).fetchone()[0].endswith("ON price_history(location_id, product_id, valid_from_utc)")


def test_paiz_structural_turso_migration_still_verifies_and_creates_history_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "mvp.sqlite"
    sqlite_updater.initialize_database(db)
    fake = FakeTurso(db)
    monkeypatch.setattr(paiz, "_pipeline", fake.pipeline)
    monkeypatch.setattr(paiz, "_run_batch", fake.run_batch)
    result = paiz.migrate_turso("libsql://offline.example", "offline")
    assert result["migrated"] is True
    assert result["price_history_migrated"] is True
    assert result["history_index_created"] is True
    assert result["integrity_check"] == "ok"
    assert "PRAGMA integrity_check" in fake.sql
    assert fake.con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (HISTORY_INDEX_NAME,)
    ).fetchone() == (1,)


def test_homologation_refresh_never_scans_price_history_or_whole_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "mvp.sqlite"
    sqlite_updater.initialize_database(db)
    con = sqlite3.connect(db)
    con.executemany(
        "INSERT INTO products VALUES(?,'la_colonia','item_id',?,?,?,NULL,?,?,?,?,?)",
        [
            (1, "1", "1", "1", "7401000000017", "Leche entera Sula 1 L", "Sula", "1 L", "Lácteos"),
            (2, "2", "2", "2", None, "Arroz Progreso 5 lb", "Progreso", "5 lb", "Granos"),
        ],
    )
    con.commit()
    con.close()
    fake = FakeTurso(db)
    monkeypatch.setattr(homologation, "_pipeline", fake.pipeline)
    monkeypatch.setattr(homologation, "_run_batch", fake.run_batch)

    first = homologation.backfill_turso("libsql://offline.example", "offline", updated_at_utc="2026-09-30T00:00:00Z")
    assert first["no_op"] is False and first["inserted"] == 2
    assert first["foreign_key_violations"] == 0
    second = homologation.backfill_turso("libsql://offline.example", "offline", updated_at_utc="2026-09-30T00:00:00Z")
    assert second["no_op"] is True and second["unchanged"] == 2
    assert not any("price_history" in statement for statement in fake.sql)
    _assert_no_full_scans(fake.sql)
    assert any("pragma_foreign_key_check('product_homologation_profiles')" in s for s in fake.sql)


def test_weekly_integrity_script_owns_full_checks_and_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "rpi.sqlite"
    build_db(db, products_per_supermarket=3)
    fake = FakeTurso(db)
    monkeypatch.setattr(integrity, "_pipeline", fake.pipeline)
    result = integrity.verify_turso("libsql://offline.example", "offline")
    assert result["integrity_check"] == "ok"
    assert result["duplicate_open_periods"] == 0
    assert result["table_counts"]["product_homologation_profiles"] == 3 * len(SUPERMARKETS)
    assert "PRAGMA integrity_check" in fake.sql

    row = fake.con.execute(
        "SELECT product_id,supermarket_id,location_id FROM price_history WHERE valid_to_utc IS NULL LIMIT 1"
    ).fetchone()
    fake.con.execute(
        "INSERT INTO price_history VALUES(?,?,?,100,NULL,0,'in_stock','HNL','2099-01-01T00:00:00Z',NULL,?)",
        (*row, f"run-{row[2]}"),
    )
    with pytest.raises(integrity.SnapshotError, match="turso_integrity_failed"):
        integrity.verify_turso("libsql://offline.example", "offline")


def _mutation_steps(raw_rows: int, *, sku_count: int | None = None):
    rows = [{
        "availability": "in_stock", "brand": "Marca", "category": "Cat", "current_price": "10.00",
        "ean": None, "is_promotion": False, "item_id": f"i{index}", "presentation": None,
        "product_id": f"p{index}", "reference": None, "reported_regular_price": None,
        "source_key": f"k{index}", "source_key_type": "item_id", "source_name": f"Producto {index}",
    } for index in range(raw_rows)]
    snapshot = {
        "supermarket_id": "la_colonia", "location_id": "la_colonia_sps", "products": rows,
    }
    return turso_updater._mutation_steps(
        turso_updater._normalised_json(snapshot), location_id="la_colonia_sps",
        observed_at="2026-09-30T01:00:00Z", run_id="run-x",
        sku_count=raw_rows if sku_count is None else sku_count,
        catalog_count=raw_rows, artifact_id=None, digest="d" * 64,
    )


def test_cardinality_guards_follow_their_loads_and_still_fail_closed(tmp_path: Path) -> None:
    names = [name for name, _, _ in _mutation_steps(3)]
    assert names.index("guard_incoming") == names.index("incoming_load") + 1
    assert names.index("guard_delta") == names.index("delta_load") + 1

    db = tmp_path / "mvp.sqlite"
    sqlite_updater.initialize_database(db)
    con = sqlite3.connect(db, isolation_level=None)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            for name, sql, args in _mutation_steps(3, sku_count=4):
                con.execute(sql, args)
        assert con.in_transaction is False
    finally:
        con.close()
