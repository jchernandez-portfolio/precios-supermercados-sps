"""Persistencia de especificaciones PriceSmart y su uso mínimo en homologación.

Todo corre sobre SQLite local (mismas sentencias que Turso/libSQL) y un Turso
falso por pipeline; nada consulta red.
"""
from __future__ import annotations

import copy
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import actualizar_mvp_turso_la_colonia as turso_updater  # noqa: E402
import backfill_homologacion_turso as homologation  # noqa: E402
import persistir_especificaciones_pricesmart_turso as persist_script  # noqa: E402
import verificar_integridad_turso as integrity  # noqa: E402
from generar_mvp_sqlite_la_colonia import create_schema  # noqa: E402
from precios_supermercados.pricesmart_specs_persistence import (  # noqa: E402
    EXISTING_SQL,
    PROFILE_ATTRIBUTES_SQL,
    STATE_SQL,
    TABLE_NAME,
    VERIFY_SQL,
    PriceSmartSpecsPersistenceError,
    SqliteExecutor,
    apply_rows,
    fetch_profile_attributes,
    read_state,
    rows_from_artifact,
)
from precios_supermercados.product_homologation import SourceProductRecord  # noqa: E402
from precios_supermercados.product_homologation_persistence import (  # noqa: E402
    apply_source_spec_attributes,
    build_homologation_rows,
)
from precios_supermercados.scrapers.pricesmart_specs import (  # noqa: E402
    parse_product_page,
    spec_fingerprint,
)
from datetime import datetime, timezone  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "pricesmart_specs" / "SYNTHETIC-breadco-415586.html"


def synthetic_specs(pid: str, net: str = "0.5000 kg", brand: str = "Sintética") -> dict[str, Any]:
    html = (
        f"<html><body><h1>Producto {pid}</h1><div>Número de ítem {pid}</div>"
        f"<h3>Especificaciones</h3><div><div><span>Peso neto</span><span>{net}</span></div>"
        f"<div><span>Marca</span><span>{brand}</span></div></div></body></html>"
    )
    result = parse_product_page(html, pid)
    assert result.status == "parsed"
    return result.specs


def item(pid: str, specs: dict[str, Any] | None, fetched: str = "2026-10-03T11:40:00Z", status: str = "parsed") -> dict[str, Any]:
    return {
        "product_id": pid,
        "url": f"https://www.pricesmart.com/es-hn/producto/p-{pid}/{pid}",
        "status": status,
        "http_status": 200,
        "attempts": 1,
        "fetched_at_utc": fetched,
        "extraction_method": "dom_structured" if specs else None,
        "error": None,
        "specs": specs,
        "spec_sha256": spec_fingerprint(specs) if specs else None,
    }


def artifact(items: list[dict[str, Any]], run_id: str = "900") -> dict[str, Any]:
    return {
        "schema": "precios-sps-pricesmart-specs/v1",
        "result": "success",
        "generated_at_utc": "2026-10-03T12:30:00Z",
        "run": {"github_run_id": run_id},
        "items": items,
    }


@pytest.fixture()
def executor() -> SqliteExecutor:
    con = sqlite3.connect(":memory:", isolation_level=None)
    create_schema(con)
    return SqliteExecutor(con)


def table_rows(executor: SqliteExecutor) -> list[tuple]:
    return executor.connection.execute(
        f"SELECT product_id,spec_sha256,first_captured_at_utc,verified_at_utc,changed_at_utc,"
        f"capture_run_id,net_weight_g,brand,presentation_hint FROM {TABLE_NAME} ORDER BY product_id"
    ).fetchall()


def test_breadco_row_shape_from_artifact() -> None:
    specs = parse_product_page(FIXTURE.read_text(encoding="utf-8"), "415586").specs
    rows = rows_from_artifact(artifact([item("415586", specs)]))
    assert len(rows) == 1
    values = rows[0].values
    assert values["net_weight_g"] == "1450"
    assert values["unit_weight_g"] == "90.63"
    assert values["pack_count"] == 16
    assert values["brand"] == "Breadco"
    assert values["category_path"] == "Alimentos > Panadería y repostería"
    assert values["imported_or_national"] == "nacional"
    assert values["origin_country"] == "Honduras"
    assert json.loads(values["allergens_json"]) == ["Leche", "Huevos", "Trigo"]
    assert values["trans_fat_free"] == 0
    assert values["gtin"] is None
    assert values["presentation_hint"] == "16 x 90.63 g"
    assert values["capture_run_id"] == "900"


def test_upsert_is_idempotent_and_writes_only_changed_rows(executor: SqliteExecutor) -> None:
    first = artifact([item("100", synthetic_specs("100")), item("200", synthetic_specs("200")),
                      item("300", None, status="no_specifications")])
    result = apply_rows(executor, rows_from_artifact(first))
    assert result == {"incoming": 2, "inserted": 2, "changed": 0, "reverified_unchanged": 0,
                      "no_op": False, "verified_rows": 2}
    snapshot = table_rows(executor)
    assert [row[0] for row in snapshot] == ["100", "200"]  # sin specs no se escribe

    again = apply_rows(executor, rows_from_artifact(first))
    assert again["inserted"] == 0 and again["changed"] == 0 and again["reverified_unchanged"] == 2
    assert table_rows(executor) == snapshot  # re-aplicar el mismo artifact no cambia nada

    later = "2026-10-10T11:40:00Z"
    second = artifact([
        item("100", synthetic_specs("100", net="0.7500 kg"), fetched=later),
        item("200", synthetic_specs("200"), fetched=later),
    ], run_id="901")
    result = apply_rows(executor, rows_from_artifact(second))
    assert result["inserted"] == 0 and result["changed"] == 1 and result["reverified_unchanged"] == 1
    rows = {row[0]: row for row in table_rows(executor)}
    assert rows["100"][2] == "2026-10-03T11:40:00Z"  # first_captured conservado
    assert rows["100"][3] == later and rows["100"][4] == later  # verificado y cambiado
    assert rows["100"][6] == "750"
    assert rows["200"][3] == later  # re-verificado
    assert rows["200"][4] == "2026-10-03T11:40:00Z"  # sin cambio de contenido
    assert rows["200"][5] == "901"


def test_rows_read_budget_reads_only_indexed_keys(executor: SqliteExecutor) -> None:
    apply_rows(executor, rows_from_artifact(artifact([item(str(pid), synthetic_specs(str(pid))) for pid in range(100, 110)])))
    con = executor.connection

    def plan(sql: str, args: tuple) -> str:
        return " | ".join(row[-1] for row in con.execute(f"EXPLAIN QUERY PLAN {sql}", args).fetchall())

    state_plan = plan(STATE_SQL, ("2026-09-01T00:00:00Z",))
    assert "COVERING INDEX idx_pricesmart_product_specs_verified (verified_at_utc>?)" in state_plan
    assert "SCAN" not in state_plan
    for sql in (EXISTING_SQL, VERIFY_SQL):
        lookup = plan(sql, ('["100","101"]',))
        assert f"SEARCH {TABLE_NAME} USING INDEX sqlite_autoindex_{TABLE_NAME}_1 (product_id=?)" in lookup
        assert f"SCAN {TABLE_NAME}" not in lookup
    profile_plan = plan(PROFILE_ATTRIBUTES_SQL, ())
    assert "SEARCH p USING INDEX sqlite_autoindex_products_1 (supermarket_id=?)" in profile_plan
    assert f"SEARCH s USING INDEX sqlite_autoindex_{TABLE_NAME}_1 (product_id=?)" in profile_plan
    assert "SCAN" not in profile_plan

    # Una corrida que re-verifica 2 filas sólo lee esas 2 llaves (comparación y
    # verificación) más el estado fresco: nunca la tabla completa.
    executor.statements.clear()
    apply_rows(executor, rows_from_artifact(artifact([
        item("100", synthetic_specs("100"), fetched="2026-10-10T11:40:00Z"),
        item("101", synthetic_specs("101"), fetched="2026-10-10T11:40:00Z"),
    ])))
    reads = [statement for statement in executor.statements if statement.startswith("SELECT")]
    assert reads == [" ".join(EXISTING_SQL.split()), " ".join(VERIFY_SQL.split())]
    assert not any("COUNT(" in statement for statement in executor.statements)


def test_state_reads_only_fresh_keys_and_tolerates_missing_table(executor: SqliteExecutor) -> None:
    now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    state = read_state(executor, now=now)
    assert state["table_exists"] is False and state["fresh"] == {}
    apply_rows(executor, rows_from_artifact(artifact([
        item("100", synthetic_specs("100"), fetched="2026-10-03T11:40:00Z"),
    ])))
    apply_rows(executor, rows_from_artifact(artifact([
        item("200", synthetic_specs("200"), fetched="2026-08-01T11:40:00Z"),
    ], run_id="800")))
    state = read_state(executor, now=now)
    assert state["fresh"] == {"100": "2026-10-03T11:40:00Z"}
    assert state["cutoff_utc"] == "2026-09-12T12:00:00Z"


def test_artifact_contract_is_fail_closed() -> None:
    good = artifact([item("100", synthetic_specs("100"))])
    for mutate, reason in (
        (lambda doc: doc.update(result="failed"), "artifact_not_successful"),
        (lambda doc: doc.update(schema="x"), "artifact_schema_invalid"),
        (lambda doc: doc["items"].append(copy.deepcopy(doc["items"][0])), "artifact_product_id_duplicate"),
        (lambda doc: doc["items"][0].update(status="no_specifications"), "artifact_unparsed_item_has_specs"),
        (lambda doc: doc["items"][0].update(spec_sha256="bad"), "artifact_spec_hash_invalid"),
        (lambda doc: doc["items"][0].update(product_id="01"), "artifact_product_id_invalid"),
    ):
        broken = copy.deepcopy(good)
        mutate(broken)
        with pytest.raises(PriceSmartSpecsPersistenceError, match=reason):
            rows_from_artifact(broken)


def test_shared_turso_guards_accept_the_new_optional_table() -> None:
    turso_updater._validate_table_names(turso_updater.EXPECTED_TABLES | {TABLE_NAME})
    turso_updater._validate_table_names(
        turso_updater.EXPECTED_TABLES | {TABLE_NAME, "product_homologation_profiles"}
    )
    assert TABLE_NAME in integrity.OPTIONAL_COUNTED_TABLES


class FakeTurso:
    def __init__(self, con: sqlite3.Connection) -> None:
        self.con = con
        self.sql: list[str] = []

    @staticmethod
    def _decode(arg: dict[str, str]) -> object:
        if arg["type"] == "null":
            return None
        return int(arg["value"]) if arg["type"] == "integer" else arg["value"]

    @staticmethod
    def _encode(value: object) -> dict[str, str]:
        if value is None:
            return {"type": "null"}
        if isinstance(value, int):
            return {"type": "integer", "value": str(value)}
        return {"type": "text", "value": str(value)}

    def pipeline(self, _url: str, _token: str, requests: list[dict[str, Any]]) -> dict[str, Any]:
        results = []
        for request in requests:
            if request["type"] == "close":
                results.append({"type": "ok", "response": {"type": "close"}})
                continue
            stmt = request["stmt"]
            self.sql.append(" ".join(stmt["sql"].split()))
            rows = self.con.execute(stmt["sql"], [self._decode(arg) for arg in stmt["args"]]).fetchall()
            results.append({"type": "ok", "response": {"type": "execute", "result": {
                "rows": [[self._encode(value) for value in row] for row in rows],
            }}})
        return {"results": results}

    def run_batch(self, _url: str, _token: str, steps):
        out = []
        for _, sql, args in steps:
            self.sql.append(" ".join(sql.split()))
            out.append({"affected_row_count": self.con.execute(sql, args).rowcount})
        return out


def test_turso_script_state_and_apply_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    con = sqlite3.connect(":memory:", isolation_level=None)
    create_schema(con)
    fake = FakeTurso(con)
    monkeypatch.setattr(persist_script, "_pipeline", fake.pipeline)
    monkeypatch.setattr(persist_script, "_run_batch", fake.run_batch)
    executor = persist_script.TursoExecutor("libsql://offline.example", "offline")
    now = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)

    summary = persist_script.command_state(executor, tmp_path / "state.json", stale_days=28, now=now)
    assert summary["table_exists"] is False and summary["fresh_keys"] == 0

    path = tmp_path / "specs.json"
    path.write_text(json.dumps(artifact([item("100", synthetic_specs("100"))])), encoding="utf-8")
    result = persist_script.command_apply(executor, path)
    assert result["inserted"] == 1
    summary = persist_script.command_state(executor, tmp_path / "state.json", stale_days=28, now=now)
    assert summary["fresh_keys"] == 1
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["fresh"] == {"100": "2026-10-03T11:40:00Z"}
    assert not any("products" in statement or "price_history" in statement for statement in fake.sql)
    with pytest.raises(PriceSmartSpecsPersistenceError, match="turso_credentials_missing"):
        persist_script.TursoExecutor("", "")


# ---------------------------------------------------------------------------
# Homologación: sólo completa lo que falta y nunca crea comparables
# ---------------------------------------------------------------------------


def record(pid: int, name: str, *, brand: str | None = None, supermarket: str = "pricesmart") -> tuple[int, SourceProductRecord]:
    return pid, SourceProductRecord(
        source_record_id=f"{supermarket}:{pid}",
        supermarket_id=supermarket,
        source_name=name,
        source_brand=brand,
    )


def test_spec_attributes_fill_only_missing_brand_and_presentation() -> None:
    products = (
        record(1, "Pastelito de Piña"),  # sin marca ni presentación
        record(2, "Leche Entera 946 ml", brand="Sula"),  # el nombre ya trae presentación
        record(3, "Arroz Blanco", brand="Member's Selection"),
        record(4, "Leche Entera", supermarket="walmart"),  # otra cadena: intacta
    )
    attributes = {
        1: ("Breadco", "16 x 90.63 g"),
        2: ("Otra", "1000 ml"),
        3: (None, "2268 g"),
        4: ("Sula", "946 ml"),
    }
    enriched, counts = apply_source_spec_attributes(products, attributes)
    by_id = dict(enriched)
    assert counts == {"brand": 1, "presentation": 2}
    assert by_id[1].source_brand == "Breadco" and by_id[1].source_presentation == "16 x 90.63 g"
    assert by_id[2] is products[1][1]  # nada que completar: mismo registro
    assert by_id[3].source_brand == "Member's Selection" and by_id[3].source_presentation == "2268 g"
    assert by_id[4] is products[3][1]
    assert all(item.barcode is None for item in by_id.values())

    rows = {row.product_id: row for row in build_homologation_rows(enriched, updated_at_utc="2026-10-03T12:00:00Z")}
    assert rows[1].presentation_status == "source_only"
    assert rows[1].presentation_pack_count == 16
    assert rows[1].raw_brand == "Breadco"
    assert rows[2].presentation_status == "name_only"  # comportamiento previo intacto
    assert rows[3].presentation_status == "source_only"
    assert all(row.comparison_status == "unmapped" for row in rows.values())  # sin GTIN no hay comparables
    baseline = {row.product_id: row for row in build_homologation_rows(products, updated_at_utc="2026-10-03T12:00:00Z")}
    assert rows[2] == baseline[2] and rows[4] == baseline[4]


def test_homologation_refresh_reads_specs_cheaply_when_table_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "rpi.sqlite"
    con = sqlite3.connect(db, isolation_level=None)
    create_schema(con)
    con.executemany("INSERT INTO supermarkets VALUES(?,?,?)", [("pricesmart", "PriceSmart", "HN"), ("la_colonia", "La Colonia", "HN")])
    con.executemany(
        "INSERT INTO products VALUES(?,?,'item_id',?,?,?,NULL,NULL,?,?,NULL,?)",
        [
            (1, "pricesmart", "415586", "415586", "415586", "Breadco Pastelito de Piña", None, "Alimentos"),
            (2, "pricesmart", "100187", "100187", "100187", "Member's Selection Chuleta de Cerdo 1.2 kg", "Member's Selection", "Alimentos"),
            (3, "la_colonia", "9", "9", "9", "Leche Sula 1 L", "Sula", "Lácteos"),
        ],
    )
    fake = FakeTurso(con)
    monkeypatch.setattr(homologation, "_pipeline", fake.pipeline)
    monkeypatch.setattr(homologation, "_run_batch", fake.run_batch)

    # Sin tabla de especificaciones: comportamiento previo, ninguna lectura extra.
    first = homologation.backfill_turso("libsql://offline.example", "offline", updated_at_utc="2026-10-03T12:00:00Z")
    assert first["pricesmart_spec_enrichment"] == {"brand": 0, "presentation": 0}
    assert not any(TABLE_NAME in statement for statement in fake.sql)
    status = dict(con.execute("SELECT product_id,presentation_status FROM product_homologation_profiles").fetchall())
    assert status[1] == "missing"

    specs = parse_product_page(FIXTURE.read_text(encoding="utf-8"), "415586").specs
    executor = SqliteExecutor(con)
    apply_rows(executor, rows_from_artifact(artifact([item("415586", specs)])))
    assert fetch_profile_attributes(executor) == {1: ("Breadco", "16 x 90.63 g")}

    fake.sql.clear()
    second = homologation.backfill_turso("libsql://offline.example", "offline", updated_at_utc="2026-10-03T12:00:00Z")
    assert second["pricesmart_spec_enrichment"] == {"brand": 1, "presentation": 1}
    assert second["updated"] == 1
    profile = con.execute(
        "SELECT presentation_status,presentation_pack_count,raw_brand,comparison_status "
        "FROM product_homologation_profiles WHERE product_id=1"
    ).fetchone()
    assert profile == ("source_only", 16, "Breadco", "unmapped")
    spec_reads = [statement for statement in fake.sql if TABLE_NAME in statement]
    assert spec_reads == [" ".join(PROFILE_ATTRIBUTES_SQL.split())]
    assert not any("price_history" in statement for statement in fake.sql)
    other = con.execute(
        "SELECT presentation_status FROM product_homologation_profiles WHERE product_id IN (2,3) ORDER BY product_id"
    ).fetchall()
    assert other == [("name_only",), ("name_only",)]
