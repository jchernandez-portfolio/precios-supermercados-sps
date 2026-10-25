"""Walmart persistence uses the real SQL and existing offline Hrana transport."""
import base64
import copy
import gzip
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import actualizar_mvp_sqlite_la_colonia as local
import actualizar_mvp_turso_la_colonia as remote
import migrar_mvp_walmart as migration
from test_actualizar_mvp_turso_la_colonia import product
from test_actualizar_mvp_turso_la_colonia import snapshot as lc_snapshot
from test_colonial_persistence import apply, database, dump
from test_colonial_persistence import snapshot as colonial_snapshot


def walmart_snapshot(hour=1, rows=None, location="walmart_sps"):
    if rows is None:
        rows = [product("100", "10", "100") | {"reported_regular_price": "100.00"},
                product("200", "20", "200") | {"current_price": None, "reported_regular_price": None,
                    "is_promotion": None, "availability": "out_of_stock"}]
    data = json.loads(lc_snapshot(f"2026-08-31T{hour:02d}:00:00Z", rows))
    seller = local.WALMART_SELLERS[location]
    ids = {r["product_id"] for r in rows}
    data.update(supermarket_id="walmart", location_id=location, city=local.WALMART_LOCATIONS[location],
        currency="HNL", scope="public_ecommerce_selected_store_not_universal_city_price", seller_id=seller,
        region_id=base64.b64encode(("SW#"+seller).encode()).decode(), sales_channel="1",
        membership_count=len(ids), membership_sha256=hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest(),
        skus_with_price=sum(r["current_price"] is not None for r in rows),
        availability_counts={a: sum(r["availability"]==a for r in rows) for a in {r["availability"] for r in rows}},
        source_details={r["source_key"]: {"price_status": "observed" if r["current_price"] is not None else "unavailable_zero_offer",
            "source_price": r["current_price"] or "0.00", "source_list_price": r["reported_regular_price"] or "0.00",
            "available_quantity_signal": 0 if r["availability"] == "out_of_stock" else 10000} for r in rows})
    return json.dumps(data).encode()


def apply_walmart(raw, run):
    return apply(raw, run, "walmart")


def test_null_offer_unchanged_run_and_replay(database):
    assert apply_walmart(walmart_snapshot(), "a")["history_opened"] == 2
    second = walmart_snapshot(2)
    result = apply_walmart(second, "b")
    assert result["history_opened"] == result["history_closed"] == 0
    before = dump(database)
    assert apply_walmart(second, "b")["replayed"] is True
    assert dump(database) == before
    with sqlite3.connect(database) as con:
        assert con.execute("SELECT current_price_minor,is_promotion,availability FROM price_history WHERE current_price_minor IS NULL").fetchall() == [(None, None, "out_of_stock")]
    with pytest.raises(local.SnapshotError, match="run_id_conflict"):
        apply_walmart(walmart_snapshot(3), "b")
    assert dump(database) == before


@pytest.mark.parametrize("change", ["effective", "regular", "availability", "known_to_unknown", "unknown_to_known"])
def test_commercial_transitions_are_null_safe(database, change):
    raw = walmart_snapshot()
    apply_walmart(raw, "a")
    rows = json.loads(raw)["products"]
    if change == "effective": rows[0].update(current_price="90.00", is_promotion=True)
    elif change == "regular": rows[0].update(reported_regular_price="120.00", is_promotion=True)
    elif change == "availability": rows[0]["availability"] = "out_of_stock"
    elif change == "known_to_unknown": rows[0].update(current_price=None, reported_regular_price=None, is_promotion=None, availability="out_of_stock")
    else: rows[1].update(current_price="100.00", reported_regular_price="100.00", is_promotion=False, availability="in_stock")
    result = apply_walmart(walmart_snapshot(2, rows), "b")
    assert (result["history_closed"], result["history_opened"]) == (1, 1)
    with sqlite3.connect(database) as con:
        assert con.execute("SELECT COUNT(*) FROM price_history WHERE valid_to_utc IS NULL").fetchone() == (2,)


@pytest.mark.parametrize("kind", ["incomplete", "duplicate", "region", "null_available", "zero", "promotion", "unpriced_proof", "counts"])
def test_invalid_snapshots_leave_all_chains_unchanged(database, kind):
    apply_walmart(walmart_snapshot(), "a")
    bad = json.loads(walmart_snapshot(2))
    if kind == "incomplete": bad["catalog_complete"] = False
    elif kind == "duplicate": bad["products"].append(copy.deepcopy(bad["products"][0]))
    elif kind == "region": bad["region_id"] = "other"
    elif kind == "null_available": bad["products"][1]["availability"] = "in_stock"
    elif kind == "zero": bad["products"][0]["current_price"] = "0.00"
    elif kind == "promotion": bad["products"][0]["is_promotion"] = 1
    elif kind == "unpriced_proof": bad["source_details"]["200"]["available_quantity_signal"] = 10
    else: bad["membership_count"] += 1
    before = dump(database)
    with pytest.raises(local.SnapshotError): apply_walmart(json.dumps(bad).encode(), "bad")
    assert dump(database) == before


def test_two_tgu_contexts_share_city_but_never_state(database):
    a = walmart_snapshot(location="walmart_tgu_ffaa")
    rows = json.loads(a)["products"]
    rows[0].update(current_price="90.00", is_promotion=True)
    b = walmart_snapshot(rows=rows, location="walmart_tgu_el_sauce")
    apply_walmart(a, "ffaa")
    apply_walmart(b, "sauce")
    with sqlite3.connect(database) as con:
        assert con.execute("SELECT city_name,COUNT(*) FROM locations WHERE supermarket_id='walmart' GROUP BY city_name").fetchall() == [("Tegucigalpa", 2)]
        assert con.execute("SELECT location_id,current_price_minor FROM price_history WHERE product_id=(SELECT product_id FROM products WHERE supermarket_id='walmart' AND source_key='100') ORDER BY location_id").fetchall() == [("walmart_tgu_el_sauce", 9000), ("walmart_tgu_ffaa", 10000)]


def test_same_ids_are_isolated_in_both_directions_for_all_chains(database):
    row = product("100", "10", "100")
    apply(lc_snapshot("2026-08-30T01:00:00Z", [row]), "lc", "la_colonia")
    apply(colonial_snapshot(rows=[row]), "colonial", "colonial")
    lc, colonial = dump(database, "la_colonia"), dump(database, "colonial")
    apply_walmart(walmart_snapshot(), "wal")
    assert dump(database, "la_colonia") == lc and dump(database, "colonial") == colonial
    walmart = dump(database, "walmart")
    changed = row | {"current_price": "90.00"}
    apply(lc_snapshot("2026-08-30T02:00:00Z", [changed]), "lc2", "la_colonia")
    apply(colonial_snapshot(2, [changed]), "colonial2", "colonial")
    assert dump(database, "walmart") == walmart
    lc, colonial = dump(database, "la_colonia"), dump(database, "colonial")
    apply_walmart(walmart_snapshot(2), "wal2")
    assert dump(database, "la_colonia") == lc and dump(database, "colonial") == colonial


def test_new_product_disappearance_and_rollback(database):
    raw = walmart_snapshot(3)
    apply_walmart(raw, "a")
    rows = json.loads(raw)["products"]
    rows.append(product("300", "30", "300") | {"reported_regular_price": "100.00"})
    assert apply_walmart(walmart_snapshot(4, rows), "b")["history_opened"] == 1
    assert apply_walmart(walmart_snapshot(5, rows[:1]), "c")["history_opened"] == 0
    before = dump(database)
    rows[0].update(brand="Must roll back", current_price="80.00", is_promotion=True)
    with pytest.raises(local.SnapshotError, match="out_of_order"):
        apply_walmart(walmart_snapshot(2, rows), "bad")
    assert dump(database) == before
    with sqlite3.connect(database) as con:
        assert con.execute("SELECT COUNT(*) FROM price_history WHERE valid_to_utc IS NULL").fetchone() == (3,)
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []


def legacy_database(path):
    con = sqlite3.connect(path)
    con.executescript((Path(__file__).parent / "fixtures/mvp_schema_before_walmart.sql").read_text())
    con.execute("INSERT INTO supermarkets VALUES('la_colonia','La Colonia','HN')")
    con.executemany("INSERT INTO locations VALUES(?,'la_colonia',?,'HN')", local.LOCATIONS.items())
    con.commit(); con.close()
    local.apply_snapshot(path, lc_snapshot("2026-08-30T01:00:00Z", [product()]), run_id="legacy")


def test_migration_preserves_data_and_is_one_time(tmp_path):
    path = tmp_path / "legacy.db"
    legacy_database(path)
    before = dump(path)
    assert migration.migrate_database(path)["migrated"] is True
    assert dump(path) == before
    assert migration.migrate_database(path)["migrated"] is False
    with sqlite3.connect(path) as con:
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        assert len(con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()) == 5
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO locations VALUES('duplicate','la_colonia','San Pedro Sula','HN')")
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("UPDATE price_history SET current_price_minor=NULL,is_promotion=NULL")


def test_migration_target_is_frozen_before_pricesmart():
    assert migration.fingerprint(migration.target_schema()) == migration.TARGET_FINGERPRINT
    assert migration.TARGET_FINGERPRINT == (
        "f09ea1cf63f3de159c87872f842babcc42e5d14f8e2c33067782dd272c1a36f4"
    )


def test_migration_cleans_guard_left_by_reused_turso_session():
    steps = migration.migration_steps()
    cleanup = next(i for i, step in enumerate(steps) if step[0] == "drop_guard_table")
    guard = next(i for i, step in enumerate(steps) if step[0] == "guard_table")
    assert cleanup < guard


def test_migration_rolls_back_ddl_and_rejects_unrecognized_schema(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    legacy_database(path)
    before = dump(path)
    original = migration.migration_steps()
    position = next(i for i, s in enumerate(original) if s[0] == "drop_locations") + 1
    broken = original[:position] + [("injected_failure", "INSERT INTO migration_guard VALUES(1)", ())] + original[position:]
    monkeypatch.setattr(migration, "migration_steps", lambda: broken)
    with pytest.raises(sqlite3.IntegrityError): migration.migrate_database(path)
    assert dump(path) == before
    with sqlite3.connect(path) as con:
        assert migration.fingerprint(con.execute(migration.SCHEMA_QUERY).fetchall()) == migration.LEGACY_FINGERPRINT
        con.execute("CREATE TABLE unexpected(value TEXT)")
    with pytest.raises(ValueError, match="unrecognized_schema"):
        migration.migrate_database(path)


def test_walmart_requires_migration_before_any_write(database):
    database.unlink()
    legacy_database(database)
    before = dump(database)
    with pytest.raises(local.SnapshotError, match="schema_migration_required"):
        apply_walmart(walmart_snapshot(), "wal")
    assert dump(database) == before


def test_wrong_existing_location_is_not_reassigned(database):
    with sqlite3.connect(database) as con:
        con.execute("INSERT INTO locations VALUES('walmart_sps','la_colonia','Otro','HN')")
    before = dump(database)
    with pytest.raises(local.SnapshotError, match="location_mismatch"):
        apply_walmart(walmart_snapshot(), "wal")
    assert dump(database) == before


@pytest.mark.parametrize("fail", [False, True])
def test_migration_uses_existing_conditional_batch_and_rolls_back(database, fail):
    database.unlink()
    legacy_database(database)
    before = dump(database)
    steps = migration.migration_steps()
    if fail:
        at = next(i for i, s in enumerate(steps) if s[0] == "drop_price_history") + 1
        steps.insert(at, ("guard_injected", "INSERT INTO migration_guard VALUES(1)", ()))
        with pytest.raises(local.SnapshotError, match="guard_injected"):
            remote._run_batch("libsql://offline.example", "offline", steps)
    else:
        remote._run_batch("libsql://offline.example", "offline", steps)
        apply_walmart(walmart_snapshot(), "wal")
    assert dump(database, "la_colonia") == {t: [r for r in before[t]] for t in before}
    with sqlite3.connect(database) as con:
        expected = migration.LEGACY_FINGERPRINT if fail else migration.fingerprint(migration.target_schema())
        assert migration.fingerprint(con.execute(migration.SCHEMA_QUERY).fetchall()) == expected
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []


def mutation_steps(raw, run):
    snap = local.validate_snapshot_bytes(raw, supermarket_id="walmart")
    return remote._mutation_steps(remote._normalised_json(snap),
        location_id=snap["location_id"], observed_at=snap["observed_at_utc"], run_id=run,
        sku_count=len(snap["products"]), catalog_count=snap["catalog_products_reported"],
        artifact_id=None, digest=hashlib.sha256(raw).hexdigest(), supermarket_id="walmart")


def measure_mutation(path, raw, run):
    instructions, changes, plans = 0, {}, {}
    def progress():
        nonlocal instructions
        instructions += 100
        return 0
    with sqlite3.connect(path, isolation_level=None) as con:
        con.set_progress_handler(progress, 100)
        for name, sql, args in mutation_steps(raw, run):
            if name in {"guard_out_of_order", "delta_load", "close_history", "guard_current"}:
                plans[name] = con.execute("EXPLAIN QUERY PLAN " + sql, args).fetchall()
            con.execute(sql, args)
            if name in {"insert_run", "upsert_products", "close_history", "open_history"}:
                changes[name] = con.execute("SELECT changes()").fetchone()[0]
        con.set_progress_handler(None, 0)
    return {"vm_instructions": instructions, "changes": changes, "plans": plans}


def test_null_and_priced_unchanged_write_only_run_metadata_writes_one(database):
    raw = walmart_snapshot()
    apply_walmart(raw, "a")
    result = measure_mutation(database, walmart_snapshot(2), "b")
    assert result["changes"] == {"insert_run": 1, "upsert_products": 0, "close_history": 0, "open_history": 0}
    rows = json.loads(raw)["products"]
    rows[1]["brand"] = "Metadata sin precio"
    result = measure_mutation(database, walmart_snapshot(3, rows), "c")
    assert result["changes"] == {"insert_run": 1, "upsert_products": 1, "close_history": 0, "open_history": 0}


def benchmark(path, count, closed_history=0):
    local.initialize_database(path)
    rows = [product(str(i), str(i), str(i)) | {"reported_regular_price": "100.00"}
            for i in range(1, count+1)]
    for row in rows[::4]:
        row.update(current_price=None, reported_regular_price=None, is_promotion=None, availability="out_of_stock")
    measure_mutation(path, walmart_snapshot(1, rows), "a")
    if closed_history:
        with sqlite3.connect(path) as con:
            con.executemany("""INSERT INTO price_history(product_id,supermarket_id,location_id,current_price_minor,
                reported_regular_price_minor,is_promotion,availability,currency,valid_from_utc,valid_to_utc,scrape_run_id)
                SELECT product_id,supermarket_id,location_id,current_price_minor,reported_regular_price_minor,
                is_promotion,availability,currency,?,'2026-07-02T00:00:00Z',scrape_run_id
                FROM price_history WHERE rowid=1""", [(f"2026-07-01T00:00:00.{i:06d}Z",) for i in range(closed_history)])
    return measure_mutation(path, walmart_snapshot(2, rows), "b")


def test_walmart_cost_is_linear_and_independent_of_closed_history(tmp_path):
    work = [benchmark(tmp_path/f"{n}.db", n)["vm_instructions"] for n in (128,256,512)]
    assert 0 < work[0] and work[1] < work[0]*3 and work[2] < work[1]*3
    polluted = benchmark(tmp_path/"history.db", 128, 10000)
    assert polluted["vm_instructions"] < work[0]*1.1
    assert "idx_price_history_current" in str(polluted["plans"])


def assert_snapshot_state(path, snap):
    expected = [(r["source_key"], r["product_id"], r["source_name"],
                 local._minor(r["current_price"]), local._minor(r["reported_regular_price"]),
                 None if r["is_promotion"] is None else int(r["is_promotion"]), r["availability"])
                for r in snap["products"]]
    with sqlite3.connect(path) as con:
        actual = con.execute("""SELECT p.source_key,p.source_catalog_product_id,p.name,
            ph.current_price_minor,ph.reported_regular_price_minor,ph.is_promotion,ph.availability
            FROM price_history ph JOIN products p ON ph.product_id=p.product_id
            WHERE ph.supermarket_id='walmart' AND ph.location_id=? AND ph.valid_to_utc IS NULL""",
            (snap["location_id"],)).fetchall()
    assert sorted(actual) == sorted(expected)


def test_full_catalogs_use_productive_sql_with_other_chains_and_exact_replay(database):
    report = Path(__file__).resolve().parents[1]/"reports"
    database.unlink()
    legacy_database(database)
    apply(gzip.decompress((report/"colonial/2026-08-30/full-catalog.json.gz").read_bytes()), "colonial-full", "colonial")
    previous = {market: dump(database, market) for market in ["la_colonia", "colonial"]}
    migration.migrate_database(database)
    for path in sorted((report/"walmart/2026-08-31-full").glob("walmart_*.json.gz")):
        raw = gzip.decompress(path.read_bytes()); snap = json.loads(raw)
        result = apply_walmart(raw, path.stem)
        assert result["history_opened"] == len(snap["products"])
        assert result["history_closed"] == 0
        assert_snapshot_state(database, snap)
        assert apply_walmart(raw, path.stem)["replayed"] is True
    for market, before in previous.items():
        assert dump(database, market) == before
    with sqlite3.connect(database) as con:
        assert con.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        assert con.execute("SELECT COUNT(*),SUM(current_price_minor IS NULL) FROM price_history WHERE supermarket_id='walmart'").fetchone() == (41752,1597)
        assert con.execute("SELECT COUNT(*) FROM (SELECT product_id,location_id FROM price_history WHERE valid_to_utc IS NULL GROUP BY 1,2 HAVING COUNT(*)>1)").fetchone() == (0,)
