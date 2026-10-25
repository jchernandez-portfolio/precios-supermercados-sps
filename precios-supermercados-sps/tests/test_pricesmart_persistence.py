import copy
import gzip
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import actualizar_mvp_sqlite_la_colonia as local
import actualizar_mvp_turso_la_colonia as remote
import migrar_mvp_pricesmart as migration
from test_actualizar_mvp_turso_la_colonia import product
from test_actualizar_mvp_turso_la_colonia import snapshot as lc_snapshot
from test_colonial_persistence import apply, database, dump
from test_colonial_persistence import snapshot as colonial_snapshot
from test_walmart_persistence import walmart_snapshot

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/pricesmart/2026-09-01-full"


def snapshot(location):
    return gzip.decompress((REPORT / f"{location}.json.gz").read_bytes())


def apply_pricesmart(raw, run):
    return apply(raw, run, "pricesmart")


def install_offline_pipeline(monkeypatch, path):
    def pipeline(url, token, requests):
        assert url == "libsql://offline.example" and token == "offline"
        con = sqlite3.connect(path, isolation_level=None)
        con.execute("PRAGMA foreign_keys=ON")
        results = []

        def execute(statement):
            cursor = con.execute(statement["sql"], [remote._scalar(arg) for arg in statement["args"]])
            return {"rows": [list(row) for row in cursor.fetchall()], "affected_row_count": max(cursor.rowcount, 0)}

        try:
            for request in requests:
                if request["type"] == "close":
                    results.append({"type": "ok", "response": {"type": "close"}})
                elif request["type"] == "execute":
                    results.append({"type": "ok", "response": {"type": "execute", "result": execute(request["stmt"])}})
                else:
                    values, errors = [], []

                    def allowed(condition):
                        if not condition:
                            return True
                        if condition["type"] == "ok":
                            return values[condition["step"]] is not None
                        if condition["type"] == "error":
                            return errors[condition["step"]] is not None
                        return any(allowed(item) for item in condition["conds"])

                    for step in request["batch"]["steps"]:
                        value = error = None
                        if allowed(step.get("condition")):
                            try:
                                value = execute(step["stmt"])
                            except sqlite3.Error as exc:
                                error = {"message": str(exc)}
                        values.append(value)
                        errors.append(error)
                    results.append({"type": "ok", "response": {"type": "batch", "result": {"step_results": values, "step_errors": errors}}})
            return {"results": results}
        finally:
            con.close()

    monkeypatch.setattr(remote, "_pipeline", pipeline)


def test_two_full_snapshots_persist_offline_and_replay(database):
    sps, tgu = snapshot("pricesmart_sps"), snapshot("pricesmart_tgu")
    first = apply_pricesmart(sps, "pricesmart-sps-20260901")
    second = apply_pricesmart(tgu, "pricesmart-tgu-20260901")
    assert first["products_processed"] == second["products_processed"] == 1127
    assert first["history_opened"] == second["history_opened"] == 1127
    before = dump(database)
    assert apply_pricesmart(sps, "pricesmart-sps-20260901")["replayed"] is True
    assert dump(database) == before
    with sqlite3.connect(database) as con:
        assert con.execute("SELECT COUNT(*) FROM products WHERE supermarket_id='pricesmart'").fetchone() == (1127,)
        assert con.execute("SELECT COUNT(*) FROM price_history WHERE supermarket_id='pricesmart' AND valid_to_utc IS NULL").fetchone() == (2254,)
        assert con.execute("SELECT COUNT(*) FROM price_history WHERE supermarket_id='pricesmart' AND current_price_minor IS NULL AND availability='out_of_stock'").fetchone() == (100,)
        assert con.execute("SELECT location_id,COUNT(*) FROM price_history WHERE supermarket_id='pricesmart' AND is_promotion=1 GROUP BY location_id ORDER BY location_id").fetchall() == [("pricesmart_sps", 18), ("pricesmart_tgu", 17)]
        assert con.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("kind", ["scope", "club", "duplicate", "counts", "promotion", "unpriced"])
def test_invalid_pricesmart_snapshot_rolls_back_every_table(database, kind):
    good = snapshot("pricesmart_sps")
    apply_pricesmart(good, "accepted")
    bad = json.loads(good)
    if kind == "scope":
        bad["scope"] = "country"
    elif kind == "club":
        bad["club_id"] = "6604"
    elif kind == "duplicate":
        bad["products"].append(copy.deepcopy(bad["products"][0]))
        bad["skus_extracted"] += 1
    elif kind == "counts":
        bad["membership_count"] += 1
    elif kind == "promotion":
        row = next(row for row in bad["products"] if row["is_promotion"] is True)
        row["reported_regular_price"] = row["current_price"]
    else:
        row = next(row for row in bad["products"] if row["current_price"] is None)
        row["availability"] = "in_stock"
    before = dump(database)
    with pytest.raises(local.SnapshotError):
        apply_pricesmart((json.dumps(bad) + "\n").encode(), "rejected")
    assert dump(database) == before


def test_pricesmart_isolated_from_la_colonia_colonial_and_walmart(database):
    row = product("100", "10", "100")
    apply(lc_snapshot("2026-08-30T01:00:00Z", [row]), "lc", "la_colonia")
    apply(colonial_snapshot(rows=[row]), "colonial", "colonial")
    apply(walmart_snapshot(rows=[row | {"reported_regular_price": "100.00"}]), "walmart", "walmart")
    previous = {market: dump(database, market) for market in ("la_colonia", "colonial", "walmart")}
    apply_pricesmart(snapshot("pricesmart_sps"), "pricesmart-sps")
    apply_pricesmart(snapshot("pricesmart_tgu"), "pricesmart-tgu")
    assert all(dump(database, market) == state for market, state in previous.items())
    pricesmart = dump(database, "pricesmart")
    changed = row | {"current_price": "90.00"}
    apply(lc_snapshot("2026-08-30T02:00:00Z", [changed]), "lc2", "la_colonia")
    apply(colonial_snapshot(2, [changed]), "colonial2", "colonial")
    apply(walmart_snapshot(2, [changed | {"reported_regular_price": "100.00", "is_promotion": True}]), "walmart2", "walmart")
    assert dump(database, "pricesmart") == pricesmart


def pre_pricesmart_database(path):
    con = sqlite3.connect(path)
    target = dict(migration.target_schema())
    target["price_history"] = target["price_history"].replace(
        "supermarket_id IN ('walmart', 'pricesmart')", "supermarket_id = 'walmart'"
    )
    for _name, sql in target.items():
        if not sql.startswith("CREATE TABLE"):
            continue
        con.execute(sql)
    for _name, sql in target.items():
        if sql.startswith("CREATE TABLE"):
            continue
        con.execute(sql)
    con.execute("INSERT INTO supermarkets VALUES('walmart','Walmart Honduras','HN')")
    con.execute("INSERT INTO locations VALUES('walmart_sps','walmart','San Pedro Sula','HN')")
    con.execute("INSERT INTO products VALUES(1,'walmart','item_id','legacy','legacy','legacy',NULL,NULL,'Legacy',NULL,NULL,NULL)")
    con.execute("INSERT INTO scrape_runs VALUES('legacy','walmart','walmart_sps','2026-08-31T00:00:00Z','success',1,1,NULL,'legacy',NULL)")
    con.execute("INSERT INTO price_history VALUES(1,'walmart','walmart_sps',NULL,NULL,NULL,'out_of_stock','HNL','2026-08-31T00:00:00Z',NULL,'legacy')")
    con.commit()
    con.close()
    with sqlite3.connect(path) as check:
        assert migration.fingerprint(check.execute(migration.SCHEMA_QUERY).fetchall()) == migration.PRE_PRICESMART_FINGERPRINT


def test_one_time_migration_is_required_preserves_state_and_then_allows_first_load(tmp_path, monkeypatch):
    path = tmp_path / "pre-pricesmart.sqlite"
    pre_pricesmart_database(path)
    before = dump(path)
    install_offline_pipeline(monkeypatch, path)
    with pytest.raises(local.SnapshotError, match="pricesmart_schema_migration_required"):
        apply_pricesmart(snapshot("pricesmart_sps"), "pricesmart-sps")
    assert migration.migrate_database(path)["migrated"] is True
    assert migration.migrate_database(path)["migrated"] is False
    assert dump(path) == before
    assert apply_pricesmart(snapshot("pricesmart_sps"), "pricesmart-sps")["history_opened"] == 1127
    with sqlite3.connect(path) as con:
        assert migration.fingerprint(con.execute(migration.SCHEMA_QUERY).fetchall()) == migration.fingerprint(migration.target_schema())
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []


def test_migration_rolls_back_after_ddl_failure(tmp_path, monkeypatch):
    path = tmp_path / "pre-pricesmart.sqlite"
    pre_pricesmart_database(path)
    original = migration.migration_steps()
    at = next(index for index, step in enumerate(original) if step[0] == "drop_price_history") + 1
    broken = original[:at] + [("injected_failure", "INSERT INTO migration_guard VALUES(1)", ())] + original[at:]
    monkeypatch.setattr(migration, "migration_steps", lambda: broken)
    with pytest.raises(sqlite3.IntegrityError):
        migration.migrate_database(path)
    with sqlite3.connect(path) as con:
        assert migration.fingerprint(con.execute(migration.SCHEMA_QUERY).fetchall()) == migration.PRE_PRICESMART_FINGERPRINT
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []


def test_migration_cleans_guard_left_by_reused_turso_session():
    steps = migration.migration_steps()
    cleanup = next(index for index, step in enumerate(steps) if step[0] == "drop_guard_table")
    guard = next(index for index, step in enumerate(steps) if step[0] == "guard_table")
    assert cleanup < guard
