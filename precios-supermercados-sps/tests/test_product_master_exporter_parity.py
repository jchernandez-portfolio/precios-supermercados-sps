"""Paridad de exportadores con el producto maestro.

Con sólo vínculos GTIN (o sin tablas maestras) los catálogos SPS y TGU son
byte a byte idénticos a los vigentes. Un vínculo curado servible (decisión
humana "Mismo") vuelve comparable la fila; la lectura extra es sólo
``sqlite_master`` + vínculos curados por índice.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import _product_master_fixtures as fx
from precios_supermercados import product_master as pm
from precios_supermercados.product_homologation_persistence import NORMALIZATION_VERSION

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import exportar_consumer_catalog as facade  # noqa: E402
import exportar_consumer_catalog_core as core  # noqa: E402
import exportar_consumer_catalog_tgu as tgu  # noqa: E402
import exportar_modelo_analitico as modelo  # noqa: E402
import importar_decisiones_maestro as importer  # noqa: E402

AS_OF = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
POLICY = pm.load_master_policy(ROOT / "config/homologation/identity-policy-v1.yaml")


class CountingBackend:
    kind = "sqlite"

    def __init__(self, path: Path) -> None:
        self.inner = modelo.SQLiteBackend(path)
        self.queries: list[str] = []
        self.rows = 0

    def query(self, sql, args=()):  # type: ignore[no-untyped-def]
        self.queries.append(" ".join(sql.split()))
        result = self.inner.query(sql, args)
        self.rows += len(result)
        return result

    def close(self) -> None:
        self.inner.close()


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


_HOOKS = (
    "EXPECTED_SCOPE", "RETAILER_NAMES", "fetch_visible_offers", "fetch_historical_points",
    "_identity_groups", "build_rows", "_derived_presentation",
)


@contextmanager
def preserved_exporter_globals():  # type: ignore[no-untyped-def]
    """``_patch_base_for_tgu`` muta globals del módulo base; restaurarlos siempre."""
    base = tgu.base
    saved = [(module, name, getattr(module, name)) for module in (base, base._core) for name in _HOOKS if hasattr(module, name)]
    try:
        yield
    finally:
        for module, name, value in saved:
            setattr(module, name, value)


def _export_all(db: Path, output: Path) -> CountingBackend:
    scope = facade.parse_scope(f"{s}={l}" for s, l in core.EXPECTED_SCOPE)
    backend = CountingBackend(db)
    try:
        facade.export_consumer_catalog(
            backend, scope, output / "sps", as_of_utc=AS_OF, freshness_window=timedelta(hours=48),
        )
    finally:
        backend.close()
    tgu_backend = CountingBackend(db)
    try:
        with preserved_exporter_globals():
            tgu.export_tgu_catalog(
                tgu_backend, output / "tgu", as_of_utc=AS_OF, freshness_window=timedelta(hours=48), require_products=True,
            )
    finally:
        tgu_backend.close()
    return backend


def _is_master_query(query: str) -> bool:
    return "master_product_links" in query or query.startswith(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?,?)"
    )


def _rows(output: Path, city: str) -> list[dict[str, object]]:
    rows = []
    for part in sorted((output / city / "catalog").rglob("part-*.json")):
        rows.extend(json.loads(part.read_text(encoding="utf-8"))["rows"])
    return rows


def _sync(db: Path) -> None:
    con = sqlite3.connect(db, isolation_level=None)
    try:
        records, rows = fx.derive_profiles(con)
        names = {pid: record.source_name for pid, record in records}
        state = {row.product_id: (row.profile_hash, row.normalization_version) for row in rows}
        result = pm.sync_product_master(
            pm.SQLiteMasterStore(con),
            pm.member_profiles(rows, names),
            policy=POLICY,
            normalization_version=NORMALIZATION_VERSION,
            now="2026-09-20T11:30:00Z",
            prior_profile_digest=pm.profile_state_digest(state),
            new_profile_digest=pm.profile_state_digest(state),
            changed_product_ids=None,
        )
        assert result["mode"] == "full" and result["active_links"] == 16
    finally:
        con.close()


def test_gtin_only_master_links_keep_catalog_exports_byte_identical(tmp_path: Path) -> None:
    db = tmp_path / "rpi.sqlite"
    fx.build_database(db)
    _export_all(db, tmp_path / "baseline")
    _sync(db)
    _export_all(db, tmp_path / "masters")
    baseline = _tree(tmp_path / "baseline")
    assert len(baseline) > 6
    assert _tree(tmp_path / "masters") == baseline
    sps = _rows(tmp_path / "baseline", "sps")
    assert {row["comparability"] for row in sps} == {"comparable", "single_source", "individual"}
    assert sum(row["comparability"] == "comparable" for row in sps) >= 4


def test_manual_link_makes_row_comparable_and_reads_only_curated_links(tmp_path: Path) -> None:
    db = tmp_path / "rpi.sqlite"
    fx.build_database(db)
    _sync(db)
    _export_all(db, tmp_path / "before")

    dorao = pm.gtin_master_id(fx.DORAO)
    sula = pm.gtin_master_id(fx.SULA)
    con = sqlite3.connect(db, isolation_level=None)
    try:
        result = importer.import_decisions(
            pm.SQLiteMasterStore(con),
            "product_key,master_product_id,decision,reviewer\n"
            f"pricesmart:8,{dorao},Mismo,owner\n"
            f"comisariato_los_andes:11,{sula},Mismo,owner\n",
            apply=True,
            now="2026-09-20T11:45:00Z",
        )
        assert result["written"] is True, result
    finally:
        con.close()
    backend = _export_all(db, tmp_path / "after")

    before = _rows(tmp_path / "before", "sps")
    after = _rows(tmp_path / "after", "sps")
    dorao_row = next(row for row in after if row["canonical_product_id"] == "prod_gtin_07421000001010")
    assert dorao_row["comparability"] == "comparable"
    assert sorted(offer["supermarket_id"] for offer in dorao_row["offers"]) == ["pricesmart", "walmart"]
    sula_row = next(row for row in after if row["canonical_product_id"] == "prod_gtin_07421000002024")
    assert sorted(offer["supermarket_id"] for offer in sula_row["offers"]) == ["comisariato_los_andes", "la_colonia", "walmart"]
    assert len(after) == len(before) - 2
    counts_before = json.loads((tmp_path / "before/sps/manifest.json").read_text())["comparability_counts"]
    counts_after = json.loads((tmp_path / "after/sps/manifest.json").read_text())["comparability_counts"]
    assert counts_after["comparable"] == counts_before["comparable"] + 1
    assert counts_after["single_source"] == counts_before["single_source"] - 1
    assert counts_after["individual"] == counts_before["individual"] - 2
    tgu_after = _rows(tmp_path / "after", "tgu")
    tgu_dorao = next(row for row in tgu_after if row["canonical_product_id"] == "prod_gtin_07421000001010")
    assert tgu_dorao["comparability"] == "comparable"
    # Lectura extra: sqlite_master + vínculos curados por índice, nunca GTIN.
    master_queries = [q for q in backend.queries if _is_master_query(q)]
    assert len(master_queries) == 2
    assert "link_method IN (?,?,?)" in master_queries[1]
    con = sqlite3.connect(db)
    try:
        plan = str(con.execute("EXPLAIN QUERY PLAN " + master_queries[1].replace("(?,?,?)", "('engine_auto','manual_review','reviewed_decision')")).fetchall())
    finally:
        con.close()
    assert "idx_master_links_method" in plan


def test_serving_disabled_or_no_tables_keeps_current_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "rpi.sqlite"
    fx.build_database(db)
    backend = CountingBackend(db)
    try:
        assert core.fetch_master_link_overrides(backend) == {}
    finally:
        backend.close()
    assert len([q for q in backend.queries if _is_master_query(q)]) == 1  # sólo sqlite_master
    _sync(db)
    con = sqlite3.connect(db, isolation_level=None)
    try:
        importer.import_decisions(
            pm.SQLiteMasterStore(con),
            f"product_key,master_product_id,decision,reviewer\npricesmart:8,{pm.gtin_master_id(fx.DORAO)},Mismo,owner\n",
            apply=True,
        )
    finally:
        con.close()
    monkeypatch.setattr(core, "load_master_policy", lambda _path: replace(POLICY, serving_methods=frozenset()))
    backend = CountingBackend(db)
    try:
        assert core.fetch_master_link_overrides(backend) == {}
    finally:
        backend.close()
    assert not any(_is_master_query(q) for q in backend.queries)
    monkeypatch.setattr(core, "load_master_policy", lambda _path: POLICY)
    backend = CountingBackend(db)
    try:
        assert core.fetch_master_link_overrides(backend) == {"pricesmart:8": "prod_gtin_07421000001010"}
    finally:
        backend.close()


def test_group_key_rules_without_overrides_match_legacy_grouping() -> None:
    offer = core.VisibleOffer(
        source_product_id="walmart:7", supermarket_id="walmart", location_id="walmart_sps", product_name="x",
        brand=None, presentation=None, current_price_minor=100, reported_regular_price_minor=None,
        is_promotion=None, availability="in_stock", observed_at="2026-09-20T10:00:00Z",
        canonical_product_id="prod_gtin_07421000001010", category=None, product_type=None,
        presentation_dimension=None, presentation_total_base=None, presentation_status="missing",
        comparison_status="single_source",
    )
    assert core.master_group_key(offer, set()) is None
    assert core.master_group_key(offer, {"prod_gtin_07421000001010"}) == "prod_gtin_07421000001010"
    assert core.master_group_key(replace(offer, comparison_status="ready"), set()) == "prod_gtin_07421000001010"
    assert core.master_group_key(replace(offer, comparison_status="review_required"), {"prod_gtin_07421000001010"}) is None
    overridden = core.apply_master_link_overrides((offer,), {"walmart:7": "mp_" + "a" * 20})
    assert overridden[0].master_group_key == overridden[0].canonical_product_id == "mp_" + "a" * 20
    assert core.apply_master_link_overrides((offer,), {}) == (offer,)
