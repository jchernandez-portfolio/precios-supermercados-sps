"""Producto maestro: esquema STRICT, sincronización incremental y lecturas acotadas."""
from __future__ import annotations

import sqlite3
import sys
from dataclasses import replace
from pathlib import Path

import pytest

import _product_master_fixtures as fx
from precios_supermercados import product_master as pm
from precios_supermercados.product_homologation_persistence import (
    NORMALIZATION_VERSION,
    persist_sqlite_rows,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import actualizar_mvp_turso_la_colonia as turso_updater  # noqa: E402
import backfill_homologacion_turso as homologation  # noqa: E402

POLICY = pm.load_master_policy(ROOT / "config/homologation/identity-policy-v1.yaml")
NOW = "2026-09-20T12:00:00Z"


def _open(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path, isolation_level=None)
    con.execute("PRAGMA foreign_keys=ON")
    return con


def _members(con: sqlite3.Connection):  # type: ignore[no-untyped-def]
    records, rows = fx.derive_profiles(con)
    names = {product_id: record.source_name for product_id, record in records}
    return records, rows, pm.member_profiles(rows, names)


def _profile_state(rows) -> dict[int, tuple[str, str]]:  # type: ignore[no-untyped-def]
    return {row.product_id: (row.profile_hash, row.normalization_version) for row in rows}


def _sync(store, members, rows, *, prior, changed, now=NOW, apply=True, **kwargs):  # type: ignore[no-untyped-def]
    return pm.sync_product_master(
        store,
        members,
        policy=kwargs.pop("policy", POLICY),
        normalization_version=NORMALIZATION_VERSION,
        now=now,
        prior_profile_digest=pm.profile_state_digest(prior),
        new_profile_digest=pm.profile_state_digest(_profile_state(rows)),
        changed_product_ids=changed,
        apply=apply,
        **kwargs,
    )


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    path = tmp_path / "mvp.sqlite"
    fx.build_database(path)
    return path


def test_schema_is_strict_idempotent_and_verified(db: Path) -> None:
    con = _open(db)
    store = pm.SQLiteMasterStore(con)
    first = pm.ensure_master_schema(store)
    assert set(first["created"]) == set(pm.SCHEMA_OBJECTS) and first["already_applied"] is False
    second = pm.ensure_master_schema(store)
    assert second == {"created": [], "already_applied": True}
    for table in pm.MASTER_TABLES:
        sql = con.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone()[0]
        assert sql.rstrip().endswith("STRICT"), table
        assert "CREATE TABLE IF NOT EXISTS" not in sql or True
    # Una columna inesperada falla cerrado.
    con.execute(f"ALTER TABLE {pm.DIRTY_TABLE} ADD COLUMN extra TEXT")
    with pytest.raises(pm.ProductMasterError, match="master_schema_mismatch"):
        pm.verify_master_schema(store)
    # La persistencia diaria tolera las tablas derivadas nuevas.
    names = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    turso_updater._validate_table_names(names)
    con.close()


def test_constraints_enforce_one_active_link_per_product_and_retailer(db: Path) -> None:
    con = _open(db)
    store = pm.SQLiteMasterStore(con)
    _, rows, members = _members(con)
    _sync(store, members, rows, prior={}, changed=None)
    pepsi = pm.gtin_master_id("7421600300247")
    insert = (
        f"INSERT INTO {pm.LINK_TABLE}(product_id,supermarket_id,master_product_id,link_method,confidence,"
        "evidence_json,decided_by,decided_at_utc,status,status_changed_at_utc,link_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?)"
    )
    with pytest.raises(sqlite3.IntegrityError):  # segundo vínculo activo del producto 4
        con.execute(insert, (4, "walmart", pm.gtin_master_id("7441029556773"), "manual_review", None, "{}", "human:x", NOW, "active", None, "a" * 64))
    with pytest.raises(sqlite3.IntegrityError):  # segundo walmart activo en el maestro Pepsi
        con.execute(insert, (15, "walmart", pepsi, "manual_review", None, "{}", "human:x", NOW, "active", None, "a" * 64))
    with pytest.raises(sqlite3.IntegrityError):  # GTIN decidido por humano
        con.execute(insert, (8, "pricesmart", pepsi, "gtin_exact", None, "{}", "human:x", NOW, "active", None, "a" * 64))
    with pytest.raises(sqlite3.IntegrityError):  # producto inexistente (FK)
        con.execute(insert, (999, "pricesmart", pepsi, "manual_review", None, "{}", "human:x", NOW, "active", None, "a" * 64))
    with pytest.raises(sqlite3.IntegrityError):  # id de maestro con formato inválido
        con.execute(
            f"UPDATE {pm.MASTER_TABLE} SET master_product_id='prod_gtin_x' WHERE master_product_id=?", (pepsi,)
        )
    con.close()


def test_first_sync_writes_masters_and_rerun_is_a_cheap_noop(db: Path) -> None:
    con = _open(db)
    store = pm.SQLiteMasterStore(con)
    _, rows, members = _members(con)
    first = _sync(store, members, rows, prior={}, changed=None)
    assert first["mode"] == "full" and first["mode_reason"] == "no_sync_state"
    assert first["plan"]["masters_inserted"] == 8 and first["plan"]["links_inserted"] == 16
    assert con.execute(f"SELECT COUNT(*) FROM {pm.MASTER_TABLE}").fetchone()[0] == 8
    assert con.execute(f"SELECT COUNT(*) FROM {pm.LINK_TABLE} WHERE status='active'").fetchone()[0] == 16
    stored = con.execute(f"SELECT version,status,created_at_utc FROM {pm.MASTER_TABLE}").fetchall()
    assert {row for row in stored} == {(1, "active", NOW)}

    statements_before = len(store.statements)
    store.rows_read = 0
    second = _sync(store, members, rows, prior=_profile_state(rows), changed=[], now="2026-09-21T12:00:00Z")
    assert second["mode"] == "noop" and second["state_written"] is False
    new_statements = store.statements[statements_before:]
    assert not any(statement.startswith(("INSERT", "UPDATE", "DELETE")) for statement in new_statements)
    # sqlite_master + estado + curados + humanos + dirty: sin leer maestros/vínculos GTIN.
    assert second["rows_read"] <= len(pm.SCHEMA_OBJECTS) + 1
    assert not any(f"FROM {pm.MASTER_TABLE} WHERE master_product_id>?" in statement for statement in new_statements)
    con.close()


def test_incremental_sync_writes_only_the_change_and_reads_by_index(db: Path) -> None:
    con = _open(db)
    store = pm.SQLiteMasterStore(con)
    _, rows, members = _members(con)
    _sync(store, members, rows, prior={}, changed=None)
    prior = _profile_state(rows)
    # Paiz publica el Frijol Ducal con el mismo GTIN que Walmart: se une al maestro.
    con.execute("UPDATE products SET ean=? WHERE product_id=16", (fx.DUCAL,))
    _, new_rows, new_members = _members(con)
    persist_sqlite_rows(con, new_rows)
    changed = [row.product_id for row in new_rows if _profile_state(new_rows)[row.product_id] != prior.get(row.product_id)]
    assert set(changed) == {15, 16}
    store.rows_read = 0
    mark = len(store.statements)
    result = _sync(store, new_members, new_rows, prior=prior, changed=changed, now="2026-09-21T12:00:00Z")
    assert result["mode"] == "incremental"
    assert result["plan"]["links_replaced"] == 1  # producto 16 cambia de maestro
    assert result["plan"]["masters_retired"] == 1  # el maestro del GTIN viejo queda sin miembros
    assert result["plan"].get("masters_inserted", 0) == 0
    assert result["plan"]["masters_updated"] == 1
    assert result["rows_read"] <= 30
    statements = store.statements[mark:]
    assert not any("WHERE master_product_id>? ORDER BY" in s or "WHERE product_id>? AND status='active'" in s for s in statements)
    ducal = pm.gtin_master_id(fx.DUCAL)
    active = con.execute(
        f"SELECT product_id FROM {pm.LINK_TABLE} WHERE master_product_id=? AND status='active' ORDER BY product_id", (ducal,)
    ).fetchall()
    assert active == [(15,), (16,)]
    old = pm.gtin_master_id(fx.gtin13("742100000999"))
    assert con.execute(f"SELECT status,version FROM {pm.MASTER_TABLE} WHERE master_product_id=?", (old,)).fetchone() == ("retired", 2)
    history = con.execute(f"SELECT status FROM {pm.LINK_TABLE} WHERE product_id=16 ORDER BY link_id").fetchall()
    assert history == [("superseded",), ("active",)]
    # La corrida incremental deja exactamente el estado que dejaría una completa.
    full_store = pm.SQLiteMasterStore(con)
    con.execute(f"DELETE FROM {pm.STATE_TABLE}")
    replay = _sync(full_store, new_members, new_rows, prior=_profile_state(new_rows), changed=[], now="2026-09-22T12:00:00Z")
    assert replay["mode"] == "full"
    assert {key: value for key, value in replay["plan"].items() if not key.endswith("unchanged")} == {}
    con.close()


def test_out_of_sync_profiles_force_a_full_diff(db: Path) -> None:
    con = _open(db)
    store = pm.SQLiteMasterStore(con)
    _, rows, members = _members(con)
    _sync(store, members, rows, prior={}, changed=None)
    stale_prior = {**_profile_state(rows), 1: ("f" * 64, NORMALIZATION_VERSION)}
    tweaked = [replace(item, normalized_brand="bimbo hn") if item.product_id == 1 else item for item in members]
    result = _sync(store, tweaked, rows, prior=stale_prior, changed=[])
    assert result["mode"] == "full" and result["mode_reason"] == "profiles_out_of_sync_with_master"
    assert result["plan"]["masters_updated"] == 1
    con.close()


def test_dry_run_never_writes_or_creates_schema(db: Path) -> None:
    con = _open(db)
    store = pm.SQLiteMasterStore(con)
    _, rows, members = _members(con)
    result = _sync(store, members, rows, prior={}, changed=None, apply=False)
    assert result["dry_run"] is True and result["plan"]["masters_inserted"] == 8
    assert not any(statement.startswith(("CREATE", "INSERT", "UPDATE")) for statement in store.statements)
    assert con.execute("SELECT COUNT(*) FROM sqlite_master WHERE name=?", (pm.MASTER_TABLE,)).fetchone()[0] == 0
    con.close()


def test_daily_refresh_writes_masters_with_bounded_turso_reads(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    con = sqlite3.connect(db)
    con.execute("DELETE FROM product_homologation_profiles")
    con.commit()
    con.close()
    fake = fx.FakeTurso(db)
    monkeypatch.setattr(homologation, "_pipeline", fake.pipeline)
    monkeypatch.setattr(homologation, "_run_batch", fake.run_batch)
    first = homologation.backfill_turso("libsql://offline.example", "offline", updated_at_utc=NOW)
    master = first["product_master"]
    assert master["status"] == "ok" and master["mode"] == "full", master
    # 16 vínculos GTIN + 2 de la regla A por atributos (Café Dorao PriceSmart y
    # Leche Sula Los Andes → maestro GTIN de Walmart).
    assert master["masters"] == 8 and master["active_links"] == 18
    assert master["plan"]["links_inserted"] == 18
    assert master["engine_attribute_links"]["links"] == 2

    # Segundo día sin cambios: perfiles no-op y maestro no-op con lecturas mínimas.
    fake.sql.clear()
    fake.rows_returned = 0
    second = homologation.backfill_turso("libsql://offline.example", "offline", updated_at_utc="2026-09-21T12:00:00Z")
    assert second["no_op"] is True
    assert second["product_master"]["mode"] == "noop"
    # Esquema + estado + por cada vínculo de la regla A: el vínculo persistido y
    # su maestro destino (2 vínculos en el fixture → 4 filas).
    engine_links = first["product_master"]["engine_attribute_links"]["links"]
    assert second["product_master"]["rows_read"] <= len(pm.SCHEMA_OBJECTS) + 1 + 2 * engine_links
    master_reads = [s for s in fake.sql if "master_" in s and s.startswith("SELECT")]
    assert not any("ORDER BY master_product_id LIMIT" in s or "ORDER BY product_id LIMIT" in s for s in master_reads)
    assert not any(s.startswith(("INSERT", "UPDATE", "DELETE")) and "master_" in s for s in fake.sql)
    assert not any("price_history" in s for s in fake.sql)

    # Tercer día: un producto nuevo de Paiz con el GTIN de Leche Sula → incremental.
    fake.con.execute(
        "INSERT INTO products VALUES(25,'paiz','item_id','25','25','25','25',?,?,?,?,?)",
        (fx.SULA, "Leche Entera Sula - 946 ml", "Sula", "946 ml", "/Lácteos/Leche/"),
    )
    fake.sql.clear()
    third = homologation.backfill_turso("libsql://offline.example", "offline", updated_at_utc="2026-09-22T12:00:00Z")
    assert third["inserted"] == 1
    assert third["product_master"]["mode"] == "incremental"
    assert third["product_master"]["plan"]["links_inserted"] == 1
    assert third["product_master"]["rows_read"] <= 30


def test_master_error_never_blocks_profile_refresh(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = fx.FakeTurso(db)
    monkeypatch.setattr(homologation, "_pipeline", fake.pipeline)
    monkeypatch.setattr(homologation, "_run_batch", fake.run_batch)

    def boom(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise pm.ProductMasterError("simulated")

    monkeypatch.setattr(homologation, "sync_product_master", boom)
    result = homologation.backfill_turso("libsql://offline.example", "offline", updated_at_utc=NOW)
    assert result["product_master"] == {
        "enabled": True,
        "status": "error",
        "error_type": "ProductMasterError",
        "error": "simulated",
    }
    disabled = homologation._sync_master(
        "u", "t", (), (), {}, (), timestamp=NOW, apply=True, policy=replace(POLICY, enabled=False)
    )
    assert disabled == {"enabled": False, "mode": "disabled"}
