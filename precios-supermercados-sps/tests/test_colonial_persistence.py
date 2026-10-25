"""Escenarios sintéticos sobre el SQL productivo, con transporte Turso offline."""
import copy
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import actualizar_mvp_sqlite_la_colonia as local
import actualizar_mvp_turso_la_colonia as remote
from test_actualizar_mvp_turso_la_colonia import product
from test_actualizar_mvp_turso_la_colonia import snapshot as la_colonia_snapshot


def snapshot(hour=1, rows=None):
    rows = rows if rows is not None else [product("100", "10", "100")]
    payload = json.loads(la_colonia_snapshot(f"2026-08-30T{hour:02d}:00:00Z", rows))
    payload.update(supermarket_id="colonial", location_id="colonial_sps", currency="HNL",
                   scope="public_ecommerce_sps_not_physical_branch_inventory",
                   membership_count=payload["catalog_products_reported"],
                   html_cards_count=payload["catalog_products_reported"],
                   availability_counts={v: sum(row["availability"] == v for row in rows)
                                        for v in {row["availability"] for row in rows}})
    return json.dumps(payload).encode()


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / "precios-supermercados.sqlite"
    local.initialize_database(path)

    def pipeline(url, token, requests):
        assert url == "libsql://offline.example" and token == "offline"
        con = sqlite3.connect(path, isolation_level=None)
        con.execute("PRAGMA foreign_keys=ON")
        def execute(stmt):
            cursor = con.execute(stmt["sql"], [remote._scalar(a) for a in stmt["args"]])
            return {"rows": [list(row) for row in cursor.fetchall()], "affected_row_count": max(cursor.rowcount, 0)}
        results = []
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
                        return any(allowed(c) for c in condition["conds"])
                    for step in request["batch"]["steps"]:
                        value, error = None, None
                        if allowed(step.get("condition")):
                            try:
                                value = execute(step["stmt"])
                            except sqlite3.Error as exc:
                                error = {"message": str(exc)}
                        values.append(value); errors.append(error)
                    results.append({"type": "ok", "response": {"type": "batch", "result": {"step_results": values, "step_errors": errors}}})
            return {"results": results}
        finally:
            con.close()
    monkeypatch.setattr(remote, "_pipeline", pipeline)
    return path


def apply(raw, run, market="colonial"):
    return remote.persist_snapshot(raw, run_id=run, supermarket_id=market,
                                   database_url="libsql://offline.example", auth_token="offline")


def dump(path, market=None):
    with sqlite3.connect(path) as con:
        return {table: sorted(con.execute(f"SELECT * FROM {table}" + (" WHERE supermarket_id=?" if market else ""),
                                         (market,) if market else ()).fetchall(), key=repr)
                for table in remote.EXPECTED_TABLES}


def test_new_unchanged_and_exact_replay(database):
    assert apply(snapshot(), "a")["history_opened"] == 1
    second = snapshot(2)
    result = apply(second, "b")
    assert result["history_opened"] == result["history_closed"] == 0
    before = dump(database)
    assert len(before["scrape_runs"]) == 2 and len(before["price_history"]) == 1
    assert apply(second, "b")["replayed"] is True
    assert dump(database) == before
    with pytest.raises(local.SnapshotError, match="run_id_conflict"):
        apply(snapshot(3), "b")
    assert dump(database) == before


@pytest.mark.parametrize("change", [
    {"current_price": "90.00"}, {"reported_regular_price": "120.00"},
    {"is_promotion": True}, {"availability": "out_of_stock"},
])
def test_each_commercial_change_opens_exactly_one_period(database, change):
    apply(snapshot(), "a")
    row = product("100", "10", "100") | change
    result = apply(snapshot(2, [row]), "b")
    assert (result["history_closed"], result["history_opened"]) == (1, 1)
    with sqlite3.connect(database) as con:
        assert con.execute("SELECT COUNT(*) FROM price_history WHERE valid_to_utc IS NULL").fetchone() == (1,)
        assert con.execute("SELECT current_price_minor FROM price_history WHERE valid_to_utc IS NOT NULL").fetchone() == (10000,)


@pytest.mark.parametrize("kind", ["invalid", "incomplete", "duplicate", "counts", "scope", "numeric_true"])
def test_rejected_snapshot_never_changes_accepted_state(database, kind):
    apply(snapshot(), "a")
    bad = json.loads(snapshot(2))
    if kind == "invalid": bad["products"][0]["current_price"] = "NaN"
    elif kind == "incomplete": bad["catalog_complete"] = False
    elif kind == "duplicate":
        bad["products"].append(copy.deepcopy(bad["products"][0]))
        bad["skus_extracted"] = bad["skus_with_price"] = 2
        bad["availability_counts"]["in_stock"] = 2
    elif kind == "counts": bad["membership_count"] = 2
    elif kind == "scope": bad["location_id"] = "la_colonia_sps"
    else: bad["catalog_complete"] = 1
    before = dump(database)
    with pytest.raises(local.SnapshotError): apply(json.dumps(bad).encode(), "bad")
    assert dump(database) == before


def test_new_product_and_disappearance_do_not_mark_missing_oos(database):
    apply(snapshot(), "a")
    rows = [product("100", "10", "100"), product("200", "20", "200")]
    assert apply(snapshot(2, rows), "b")["history_opened"] == 1
    assert apply(snapshot(3, rows[:1]), "c")["history_opened"] == 0
    with sqlite3.connect(database) as con:
        assert con.execute("SELECT availability,COUNT(*) FROM price_history WHERE valid_to_utc IS NULL GROUP BY availability").fetchall() == [("in_stock", 2)]


def test_same_source_ids_are_isolated_both_directions(database):
    row = product("100", "10", "100")
    apply(la_colonia_snapshot("2026-08-30T01:00:00Z", [row]), "lc-a", "la_colonia")
    original = dump(database, "la_colonia")
    apply(snapshot(2), "colonial-a")
    assert dump(database, "la_colonia") == original
    colonial = dump(database, "colonial")
    apply(la_colonia_snapshot("2026-08-30T03:00:00Z", [row | {"current_price": "80.00"}]), "lc-b", "la_colonia")
    assert dump(database, "colonial") == colonial
    with sqlite3.connect(database) as con:
        assert con.execute("SELECT COUNT(*) FROM products").fetchone() == (2,)
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []


def test_out_of_order_change_rolls_back_run_and_metadata(database):
    apply(snapshot(3), "a")
    before = dump(database)
    with pytest.raises(local.SnapshotError, match="out_of_order"):
        apply(snapshot(2, [product("100", "10", "100", price="70.00")]), "bad")
    assert dump(database) == before


def test_wrong_existing_location_is_not_reassigned(database):
    with sqlite3.connect(database) as con:
        con.execute("INSERT INTO locations VALUES('colonial_sps','la_colonia','Otro','HN')")
    before = dump(database)
    with pytest.raises(local.SnapshotError, match="location_mismatch"):
        apply(snapshot(), "a")
    assert dump(database) == before
