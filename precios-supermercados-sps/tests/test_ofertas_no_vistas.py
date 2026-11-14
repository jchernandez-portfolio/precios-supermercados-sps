"""Ofertas no vistas en la última captura aceptada ("ofertas fantasma").

Cubre el interruptor, el formato de la membresía, la persistencia (una fila por
contexto, sólo con el interruptor) y el exportador público SPS/TGU: la oferta que
desaparece se oculta, vuelve sola, un snapshot rechazado o una cadena sin captura
hoy no cambian nada y los duplicados del mismo súper se limpian.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

import _product_master_fixtures as fx
from precios_supermercados import capture_membership as cm

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import actualizar_mvp_sqlite_la_colonia as sqlite_updater  # noqa: E402
import actualizar_mvp_turso_la_colonia as turso_updater  # noqa: E402
import exportar_consumer_catalog_core as core  # noqa: E402
import exportar_consumer_catalog_tgu as tgu  # noqa: E402
import verificar_integridad_turso as integrity  # noqa: E402
from test_actualizar_mvp_turso_la_colonia import dump, product, snapshot  # noqa: E402
from test_product_master_exporter_parity import AS_OF, _export_all, _rows, _tree  # noqa: E402

HIDE = cm.UnseenOffersPolicy(record_capture_membership=True, hide_unseen_offers=True, max_unseen_fraction=0.5)
MEASURE = replace(HIDE, hide_unseen_offers=False)
RUN_AT = "2026-09-20T10:00:00Z"


# --------------------------------------------------------------------- política


def test_versioned_policy_ships_disabled() -> None:
    policy = cm.load_policy()
    assert policy == cm.UnseenOffersPolicy(False, False, 0.5)
    assert turso_updater.membership_keys_if_enabled(json.loads(snapshot(RUN_AT, [product()]))) is None


def test_missing_policy_file_means_everything_off(tmp_path: Path) -> None:
    assert cm.load_policy(tmp_path / "nada.json") == cm.UnseenOffersPolicy()


@pytest.mark.parametrize(
    "document",
    [
        {"schema": "otra", "record_capture_membership": False, "hide_unseen_offers": False, "max_unseen_fraction": 0.5},
        {"schema": cm.POLICY_SCHEMA, "record_capture_membership": 1, "hide_unseen_offers": False, "max_unseen_fraction": 0.5},
        {"schema": cm.POLICY_SCHEMA, "record_capture_membership": True, "hide_unseen_offers": False, "max_unseen_fraction": 1},
        {"schema": cm.POLICY_SCHEMA, "record_capture_membership": True, "hide_unseen_offers": False, "max_unseen_fraction": 0},
        {"schema": cm.POLICY_SCHEMA, "record_capture_membership": True, "hide_unseen_offers": False,
         "max_unseen_fraction": 0.5, "extra": 1},
    ],
)
def test_invalid_policy_fails_closed(tmp_path: Path, document: dict[str, object]) -> None:
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(cm.CaptureMembershipError, match="policy_invalid"):
        cm.load_policy(path)


def test_policy_switches_are_read(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({
        "schema": cm.POLICY_SCHEMA, "record_capture_membership": True,
        "hide_unseen_offers": True, "max_unseen_fraction": 0.25, "notes": "x",
    }), encoding="utf-8")
    assert cm.load_policy(path) == cm.UnseenOffersPolicy(True, True, 0.25)


# -------------------------------------------------------------------- formato


def test_membership_round_trip_is_order_and_duplicate_insensitive() -> None:
    keys = [cm.capture_key("item_id", str(n)) for n in (3, 1, 2, 2)]
    encoded = cm.encode_keys(keys)
    assert encoded.key_count == 3
    assert encoded == cm.encode_keys(sorted(set(keys)))
    assert cm.decode_keys(encoded.key_count, encoded.keys_sha256, cm.CODEC, encoded.keys_payload) == frozenset(keys)


def test_corrupt_membership_decodes_to_none() -> None:
    encoded = cm.encode_keys(["item_id\x1f1", "item_id\x1f2"])
    good = (encoded.key_count, encoded.keys_sha256, cm.CODEC, encoded.keys_payload)
    assert cm.decode_keys(*good) is not None
    assert cm.decode_keys(3, *good[1:]) is None
    assert cm.decode_keys(good[0], "0" * 64, *good[2:]) is None
    assert cm.decode_keys(*good[:2], "otro", good[3]) is None
    assert cm.decode_keys(*good[:3], "%%%no-base64") is None
    assert cm.decode_keys(*good[:3], cm.encode_keys(["item_id\x1f9"]).keys_payload) is None


def test_membership_payload_is_compact_for_a_large_catalog() -> None:
    encoded = cm.encode_keys(cm.capture_key("item_id", str(100_000 + n)) for n in range(25_000))
    assert encoded.key_count == 25_000
    assert len(encoded.keys_payload) < 200_000


# ---------------------------------------------------------------- persistencia


def _steps(raw: bytes, run_id: str, *, keys: bool) -> list[tuple[str, str, tuple[object, ...]]]:
    snap = sqlite_updater.validate_snapshot_bytes(raw)
    return turso_updater._mutation_steps(
        turso_updater._normalised_json(snap),
        location_id=str(snap["location_id"]),
        observed_at=str(snap["observed_at_utc"]),
        run_id=run_id,
        sku_count=len(snap["products"]),
        catalog_count=int(snap["catalog_products_reported"]),
        artifact_id="artifact",
        digest=hashlib.sha256(raw).hexdigest(),
        membership_keys=cm.snapshot_keys(snap) if keys else None,
    )


def _apply(path: Path, raw: bytes, run_id: str, *, keys: bool) -> None:
    con = sqlite3.connect(path, isolation_level=None)
    try:
        for _, sql, args in _steps(raw, run_id, keys=keys):
            con.execute(sql, args)
    finally:
        con.close()


def _membership(path: Path) -> dict[tuple[str, str], tuple[str, frozenset[str] | None]]:
    con = sqlite3.connect(path)
    try:
        rows = con.execute(
            "SELECT supermarket_id,location_id,scrape_run_id,key_count,keys_sha256,keys_codec,keys_payload "
            f"FROM {cm.TABLE_NAME}"
        ).fetchall()
    finally:
        con.close()
    return {(s, loc): (run, cm.decode_keys(n, sha, codec, payload)) for s, loc, run, n, sha, codec, payload in rows}


def test_switch_off_keeps_the_daily_batch_identical() -> None:
    raw = snapshot(RUN_AT, [product()])
    names = [name for name, _, _ in _steps(raw, "run-1", keys=False)]
    assert not any("capture_membership" in name for name in names)
    snap = sqlite_updater.validate_snapshot_bytes(raw)
    legacy = turso_updater._mutation_steps(
        turso_updater._normalised_json(snap), location_id="la_colonia_sps", observed_at=RUN_AT,
        run_id="run-1", sku_count=1, catalog_count=1, artifact_id="artifact",
        digest=hashlib.sha256(raw).hexdigest(),
    )
    assert legacy == _steps(raw, "run-1", keys=False)


def test_membership_steps_run_inside_the_transaction_after_the_run_insert() -> None:
    names = [name for name, _, _ in _steps(snapshot(RUN_AT, [product()]), "run-1", keys=True)]
    begin, commit = names.index("begin"), names.index("commit")
    positions = [names.index(name) for name in (
        "capture_membership_table", "upsert_capture_membership", "guard_capture_membership")]
    assert begin < names.index("insert_run") < positions[0] < positions[1] < positions[2] < commit


def test_persistence_writes_one_row_per_context_and_overwrites_it(tmp_path: Path) -> None:
    with_membership = tmp_path / "with.db"
    reference = tmp_path / "reference.db"
    for path in (with_membership, reference):
        sqlite_updater.initialize_database(path)
    observations = [
        ("run-1", snapshot("2026-09-01T10:00:00Z", [product(), product("sku-2", "product-2", "item-2")])),
        ("run-2", snapshot("2026-09-02T10:00:00Z", [product()])),  # sku-2 deja de salir
        ("run-3", snapshot("2026-09-02T10:05:00Z", [product("sku-2", "product-2", "item-2")], "la_colonia_tgu")),
    ]
    for run_id, raw in observations:
        _apply(with_membership, raw, run_id, keys=True)
        _apply(reference, raw, run_id, keys=False)
    # La verdad comercial no cambia: mismo estado en las cinco tablas base y el
    # periodo de sku-2 en SPS sigue abierto (ausencia no es agotado).
    assert dump(with_membership) == dump(reference)
    assert _membership(with_membership) == {
        ("la_colonia", "la_colonia_sps"): ("run-2", frozenset({"item_id\x1fsku-1"})),
        ("la_colonia", "la_colonia_tgu"): ("run-3", frozenset({"item_id\x1fsku-2"})),
    }
    con = sqlite3.connect(with_membership)
    try:
        assert con.execute(f"SELECT COUNT(*) FROM {cm.TABLE_NAME}").fetchone() == (2,)
        open_periods = con.execute(
            "SELECT COUNT(*) FROM price_history WHERE location_id='la_colonia_sps' AND valid_to_utc IS NULL"
        ).fetchone()
    finally:
        con.close()
    assert open_periods == (2,)


def test_membership_guard_rolls_back_when_the_row_is_not_this_run(tmp_path: Path) -> None:
    steps = _steps(snapshot(RUN_AT, [product()]), "run-1", keys=True)
    index = next(i for i, step in enumerate(steps) if step[0] == "guard_capture_membership")
    name, sql, args = steps[index]
    steps[index] = (name, sql, (*args[:2], "otra-corrida", *args[3:]))
    path = tmp_path / "mvp.db"
    sqlite_updater.initialize_database(path)
    con = sqlite3.connect(path, isolation_level=None)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            for _, statement, values in steps:
                con.execute(statement, values)
        con.execute("ROLLBACK")
        assert con.execute("SELECT COUNT(*) FROM scrape_runs").fetchone() == (0,)
    finally:
        con.close()


def test_persist_snapshot_records_membership_only_with_the_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[list[tuple[str, str, tuple[object, ...]]]] = []
    monkeypatch.setattr(turso_updater, "_preflight", lambda *a, **k: None)

    def run_batch(_url: str, _token: str, steps):  # type: ignore[no-untyped-def]
        captured.append(steps)
        return [{"affected_row_count": 0} for _ in steps]

    monkeypatch.setattr(turso_updater, "_run_batch", run_batch)
    raw = snapshot(RUN_AT, [product(), product("sku-2", "product-2", "item-2")])
    off = turso_updater.persist_snapshot(raw, database_url="https://x", auth_token="t", run_id="r1")
    assert "capture_membership_keys" not in off
    monkeypatch.setattr(cm, "load_policy", lambda *a, **k: MEASURE)
    on = turso_updater.persist_snapshot(raw, database_url="https://x", auth_token="t", run_id="r2")
    assert on["capture_membership_keys"] == 2
    assert not any(name == "upsert_capture_membership" for name, _, _ in captured[0])
    assert any(name == "upsert_capture_membership" for name, _, _ in captured[1])


def test_rejected_snapshot_never_reaches_turso(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cm, "load_policy", lambda *a, **k: HIDE)

    def forbidden(*_a, **_k):  # type: ignore[no-untyped-def]
        raise AssertionError("un snapshot rechazado no debe tocar Turso")

    monkeypatch.setattr(turso_updater, "_pipeline", forbidden)
    monkeypatch.setattr(turso_updater, "_run_batch", forbidden)
    document = json.loads(snapshot(RUN_AT, [product()]))
    document["catalog_complete"] = False
    with pytest.raises(sqlite_updater.SnapshotError):
        turso_updater.persist_snapshot(
            json.dumps(document).encode(), database_url="https://x", auth_token="t", run_id="r1"
        )


def test_every_turso_persistor_wires_the_membership() -> None:
    for filename in ("actualizar_mvp_turso_paiz.py", "actualizar_mvp_turso_comisariato_los_andes.py"):
        source = (ROOT / "scripts" / filename).read_text(encoding="utf-8")
        assert "membership_keys = membership_keys_if_enabled(snapshot)" in source
        assert "membership_keys=membership_keys," in source
        assert "**membership_summary(membership_keys)" in source


def test_schema_guards_tolerate_the_new_derived_table() -> None:
    assert cm.TABLE_NAME in turso_updater.OPTIONAL_DERIVED_TABLES
    assert cm.TABLE_NAME in integrity.OPTIONAL_COUNTED_TABLES
    turso_updater._validate_table_names(turso_updater.EXPECTED_TABLES | {cm.TABLE_NAME})


# ------------------------------------------------------------------ exportador


def _record(db: Path, location: str, keys: list[str], *, run_id: str | None = None) -> None:
    supermarket = next(s for s, loc, _ in fx.CONTEXTS if loc == location)
    encoded = cm.encode_keys(keys)
    con = sqlite3.connect(db)
    try:
        con.execute(cm.TABLE_SQL)
        con.execute(
            f"INSERT OR REPLACE INTO {cm.TABLE_NAME} VALUES(?,?,?,?,?,?,?,?)",
            (supermarket, location, run_id or f"run-{location}", RUN_AT, encoded.key_count,
             encoded.keys_sha256, cm.CODEC, encoded.keys_payload),
        )
        con.commit()
    finally:
        con.close()


def _keys(db: Path, supermarket: str, *, without: tuple[int, ...] = ()) -> list[str]:
    con = sqlite3.connect(db)
    try:
        rows = con.execute(
            "SELECT product_id,source_key_type,source_key FROM products WHERE supermarket_id=?", (supermarket,)
        ).fetchall()
    finally:
        con.close()
    return [cm.capture_key(kind, key) for pid, kind, key in rows if pid not in without]


def _record_all(db: Path, *, without: tuple[int, ...] = ()) -> None:
    for supermarket, location, _ in fx.CONTEXTS:
        _record(db, location, _keys(db, supermarket, without=without))


def _offers(output: Path, city: str) -> set[str]:
    return {str(offer["source_product_id"]) for row in _rows(output, city) for offer in row["offers"]}


def _manifest(output: Path, city: str) -> dict[str, object]:
    return json.loads((output / city / "manifest.json").read_text(encoding="utf-8"))


def _policy(monkeypatch: pytest.MonkeyPatch, policy: cm.UnseenOffersPolicy) -> None:
    monkeypatch.setattr(cm, "load_policy", lambda *a, **k: policy)


def _db(tmp_path: Path, products=fx.PRODUCTS) -> Path:  # type: ignore[no-untyped-def]
    db = tmp_path / "rpi.sqlite"
    fx.build_database(db, products)
    return db


def _walmart_dorao(db: Path) -> int:
    con = sqlite3.connect(db)
    try:
        return con.execute("SELECT product_id FROM products WHERE name LIKE 'Café Molido Dorao%'").fetchone()[0]
    finally:
        con.close()


def test_without_membership_table_exports_are_byte_identical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = _db(tmp_path)
    _export_all(db, tmp_path / "a")
    _policy(monkeypatch, HIDE)
    backend = _export_all(db, tmp_path / "b")
    assert _tree(tmp_path / "a") == _tree(tmp_path / "b")
    assert "unseen_offers" not in _manifest(tmp_path / "b", "sps")
    assert not any("source_key" in query for query in backend.queries)


def test_offer_that_disappears_is_hidden_and_comes_back_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _db(tmp_path)
    _export_all(db, tmp_path / "baseline")
    baseline = _offers(tmp_path / "baseline", "sps")
    dorao = _walmart_dorao(db)
    assert f"walmart:{dorao}" in baseline
    _policy(monkeypatch, HIDE)

    # Walmart dejó de publicar el café Dorao en sus tres sucursales.
    _record_all(db, without=(dorao,))
    _export_all(db, tmp_path / "hidden")
    assert _offers(tmp_path / "hidden", "sps") == baseline - {f"walmart:{dorao}"}
    assert not any(item.startswith(f"walmart:{dorao}@") for item in _offers(tmp_path / "hidden", "tgu"))
    report = _manifest(tmp_path / "hidden", "sps")["unseen_offers"]
    assert report["policy"] == "hide_offers_absent_from_latest_accepted_capture"
    assert report["hidden_offers"] == 1
    walmart = next(item for item in report["contexts"] if item["location_id"] == "walmart_sps")
    assert walmart["status"] == "hidden" and walmart["unseen_offers"] == 1 and walmart["capture_run"] == "run-walmart_sps"
    assert all(item["status"] == "hidden" for item in report["contexts"])
    tgu_manifest = _manifest(tmp_path / "hidden", "tgu")
    counts = {item["location_id"]: item["offer_count"] for item in tgu_manifest["context_offer_counts"]}
    baseline_counts = {item["location_id"]: item["offer_count"]
                       for item in _manifest(tmp_path / "baseline", "tgu")["context_offer_counts"]}
    assert counts["walmart_tgu_ffaa"] == baseline_counts["walmart_tgu_ffaa"] - 1
    assert counts["paiz_tgu_proceres"] == baseline_counts["paiz_tgu_proceres"]

    # La disponibilidad y el precio no se tocan en Turso: sólo deja de publicarse.
    con = sqlite3.connect(db)
    try:
        assert con.execute(
            "SELECT availability FROM price_history WHERE product_id=? AND valid_to_utc IS NULL", (dorao,)
        ).fetchall() == [("in_stock",)] * 3
        # Nueva captura aceptada (otro run) que vuelve a traerlo.
        con.execute(
            "INSERT INTO scrape_runs VALUES('run-2-walmart_sps','walmart','walmart_sps',"
            "'2026-09-20T11:00:00Z','success',1,1,NULL,NULL,NULL)"
        )
        con.commit()
    finally:
        con.close()
    _record(db, "walmart_sps", _keys(db, "walmart"), run_id="run-2-walmart_sps")
    _export_all(db, tmp_path / "back")
    assert _offers(tmp_path / "back", "sps") == baseline


def test_measure_only_reports_without_hiding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = _db(tmp_path)
    _export_all(db, tmp_path / "baseline")
    dorao = _walmart_dorao(db)
    _record_all(db, without=(dorao,))
    _policy(monkeypatch, MEASURE)
    _export_all(db, tmp_path / "measured")
    assert _offers(tmp_path / "measured", "sps") == _offers(tmp_path / "baseline", "sps")
    report = _manifest(tmp_path / "measured", "sps")["unseen_offers"]
    assert report["policy"] == "measure_only" and report["hidden_offers"] == 0
    walmart = next(item for item in report["contexts"] if item["location_id"] == "walmart_sps")
    assert walmart["status"] == "measured" and walmart["unseen_offers"] == 1


def test_rejected_capture_or_chain_without_capture_today_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _db(tmp_path)
    dorao = _walmart_dorao(db)
    _record_all(db, without=(dorao,))
    _policy(monkeypatch, HIDE)
    _export_all(db, tmp_path / "yesterday")
    expected = _offers(tmp_path / "yesterday", "sps")

    # Hoy: Walmart intentó capturar y su snapshot fue rechazado (nunca se acepta
    # como 'success'); las demás cadenas no capturaron. La membresía no cambia.
    con = sqlite3.connect(db)
    try:
        con.execute(
            "INSERT INTO scrape_runs VALUES('run-rejected','walmart','walmart_sps',"
            "'2026-09-21T10:00:00Z','rejected',1,1,NULL,NULL,'catalog_incomplete')"
        )
        con.commit()
    finally:
        con.close()
    _export_all(db, tmp_path / "today")
    assert _offers(tmp_path / "today", "sps") == expected
    assert _rows(tmp_path / "today", "sps") == _rows(tmp_path / "yesterday", "sps")

    # Tres días sin captura (STALE): se conserva lo de la última captura aceptada.
    import test_product_master_exporter_parity as parity

    monkeypatch.setattr(parity, "AS_OF", AS_OF + timedelta(days=3))
    _export_all(db, tmp_path / "stale")
    assert _offers(tmp_path / "stale", "sps") == expected


def test_newer_accepted_run_without_membership_hides_nothing_in_that_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Una corrida aceptada por código sin membresía (p. ej. un re-run viejo) no
    puede ocultar productos nuevos con la membresía de ayer."""
    db = _db(tmp_path)
    _export_all(db, tmp_path / "baseline")
    dorao = _walmart_dorao(db)
    _record_all(db, without=(dorao,))
    con = sqlite3.connect(db)
    try:
        con.execute(
            "INSERT INTO scrape_runs VALUES('run-new','walmart','walmart_sps',"
            "'2026-09-20T11:00:00Z','success',1,1,NULL,NULL,NULL)"
        )
        con.commit()
    finally:
        con.close()
    _policy(monkeypatch, HIDE)
    _export_all(db, tmp_path / "out")
    assert f"walmart:{dorao}" in _offers(tmp_path / "out", "sps")
    report = _manifest(tmp_path / "out", "sps")["unseen_offers"]
    walmart = next(item for item in report["contexts"] if item["location_id"] == "walmart_sps")
    assert walmart["status"] == "run_mismatch" and walmart["unseen_offers"] is None
    # TGU sí aplica: sus sucursales Walmart siguen en su última captura aceptada.
    assert not any(item.startswith(f"walmart:{dorao}@") for item in _offers(tmp_path / "out", "tgu"))


def test_missing_corrupt_or_implausible_membership_hides_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _db(tmp_path)
    _export_all(db, tmp_path / "baseline")
    baseline = _offers(tmp_path / "baseline", "sps")
    _policy(monkeypatch, HIDE)
    # Sólo La Colonia SPS tiene membresía, y vacía de casi todo: > 50 % no vistas.
    first = _keys(db, "la_colonia")[:1]
    _record(db, "la_colonia_sps", first)
    # Colonial con fila corrupta.
    _record(db, "colonial_sps", _keys(db, "colonial"))
    con = sqlite3.connect(db)
    try:
        con.execute(f"UPDATE {cm.TABLE_NAME} SET keys_sha256=? WHERE location_id='colonial_sps'", ("0" * 64,))
        con.commit()
    finally:
        con.close()
    _export_all(db, tmp_path / "out")
    assert _offers(tmp_path / "out", "sps") == baseline
    statuses = {item["location_id"]: item["status"] for item in _manifest(tmp_path / "out", "sps")["unseen_offers"]["contexts"]}
    assert statuses == {
        "la_colonia_sps": "above_limit",
        "colonial_sps": "invalid_membership",
        "walmart_sps": "no_membership",
        "pricesmart_sps": "no_membership",
        "comisariato_los_andes_sps": "no_membership",
    }


def test_duplicate_offer_of_the_same_retailer_disappears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Caso PriceSmart 35606/63198 o Colonial 13211/62703: código viejo y nuevo.

    El súper ya sólo publica el código nuevo; el viejo conserva su periodo
    abierto y salía como otra tarjeta con el precio viejo. Al ocultarlo queda una
    sola oferta del súper para ese producto."""
    old_code = ("walmart", "Leche Entera Sula - 946 ml", "Sula", "946 ml", "/Lácteos/Leche/", fx.SULA, 3400)
    db = _db(tmp_path, (*fx.PRODUCTS, old_code))
    old_id = len(fx.PRODUCTS) + 1
    _export_all(db, tmp_path / "before")

    def walmart_sula(output: Path, city: str) -> set[str]:
        return {
            str(offer["source_product_id"]).split("@", 1)[0]
            for row in _rows(output, city) for offer in row["offers"]
            if str(offer["source_product_id"]).split("@", 1)[0] in {f"walmart:{old_id}", "walmart:9"}
        }

    assert walmart_sula(tmp_path / "before", "sps") == {"walmart:9", f"walmart:{old_id}"}
    assert walmart_sula(tmp_path / "before", "tgu") == {"walmart:9", f"walmart:{old_id}"}
    _record_all(db, without=(old_id,))
    _policy(monkeypatch, HIDE)
    _export_all(db, tmp_path / "after")
    assert walmart_sula(tmp_path / "after", "sps") == {"walmart:9"}
    assert walmart_sula(tmp_path / "after", "tgu") == {"walmart:9"}


def test_hiding_before_grouping_lets_a_ready_group_compare_again() -> None:
    """Si la identidad del grupo es ``ready`` (p. ej. vínculo curado), quitar la
    oferta vieja del mismo súper antes de agrupar devuelve la comparación; la
    llave de la tarjeta es la del producto canónico, no cambia."""
    base = core.VisibleOffer(
        source_product_id="walmart:9", supermarket_id="walmart", location_id="walmart_sps",
        product_name="Leche Sula 946 ml", brand="Sula", presentation="946 ml", current_price_minor=3200,
        reported_regular_price_minor=None, is_promotion=False, availability="in_stock",
        observed_at="2026-09-20T10:00:00Z", canonical_product_id="prod_gtin_sula", category=None,
        product_type=None, presentation_dimension="volume_ml", presentation_total_base="946",
        presentation_status="confirmed", comparison_status="ready", capture_key="item_id\x1f9",
    )
    old = replace(base, source_product_id="walmart:30", current_price_minor=3400, capture_key="item_id\x1f30")
    colonia = replace(base, source_product_id="la_colonia:10", supermarket_id="la_colonia",
                      location_id="la_colonia_sps", current_price_minor=3150, capture_key="item_id\x1f10")
    offers = (base, old, colonia)
    assert sorted(mode for mode, _ in core._identity_groups(offers)) == ["individual"] * 3
    memberships = {
        ("walmart", "walmart_sps"): cm.ContextMembership("walmart", "walmart_sps", "r-w", frozenset({"item_id\x1f9"})),
        ("la_colonia", "la_colonia_sps"): cm.ContextMembership(
            "la_colonia", "la_colonia_sps", "r-c", frozenset({"item_id\x1f10"})),
    }
    kept, _ = cm.apply_unseen_policy(
        offers, contexts=list(memberships), memberships=memberships,
        latest_accepted_runs={("walmart", "walmart_sps"): "r-w", ("la_colonia", "la_colonia_sps"): "r-c"},
        policy=replace(HIDE, max_unseen_fraction=0.9),
        context_of=lambda o: (o.supermarket_id, o.location_id), key_of=lambda o: o.capture_key,
    )
    assert [mode for mode, _ in core._identity_groups(kept)] == ["comparable"]


def test_exporter_reads_at_most_one_membership_row_per_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _db(tmp_path)
    _record_all(db)
    _policy(monkeypatch, HIDE)
    backend = _export_all(db, tmp_path / "out")
    membership_queries = [query for query in backend.queries if f"FROM {cm.TABLE_NAME}" in query]
    assert len(membership_queries) == 1  # SPS (la exportación TGU usa su propio backend)
    assert not any("scrape_runs" in query and cm.TABLE_NAME in query for query in backend.queries)


def test_apply_unseen_policy_treats_offers_without_key_as_seen() -> None:
    membership = cm.ContextMembership("walmart", "walmart_sps", "run-1", frozenset({"item_id\x1f1"}))
    offers = [("walmart", "walmart_sps", "item_id\x1f1"), ("walmart", "walmart_sps", None),
              ("walmart", "walmart_sps", "item_id\x1f2"), ("walmart", "walmart_sps", "item_id\x1f3")]
    kept, report = cm.apply_unseen_policy(
        offers, contexts=[("walmart", "walmart_sps")], memberships={("walmart", "walmart_sps"): membership},
        latest_accepted_runs={("walmart", "walmart_sps"): "run-1"}, policy=HIDE,
        context_of=lambda o: (o[0], o[1]), key_of=lambda o: o[2],
    )
    assert kept == offers[:2]
    assert report[0]["unseen_offers"] == 2 and report[0]["status"] == "hidden"


def test_tgu_counts_only_subtract_hidden_contexts() -> None:
    counts = {("walmart", "walmart_tgu_ffaa"): 10, ("paiz", "paiz_tgu_proceres"): 5}
    manifest = {"unseen_offers": {"contexts": [
        {"supermarket_id": "walmart", "location_id": "walmart_tgu_ffaa", "status": "hidden", "unseen_offers": 3},
        {"supermarket_id": "paiz", "location_id": "paiz_tgu_proceres", "status": "measured", "unseen_offers": 2},
    ]}}
    assert tgu._published_counts(counts, manifest) == {
        ("walmart", "walmart_tgu_ffaa"): 7, ("paiz", "paiz_tgu_proceres"): 5,
    }
    assert tgu._published_counts(counts, {}) is counts


def test_visible_offer_select_with_keys_reads_the_same_rows() -> None:
    assert core._VISIBLE_OFFERS_SELECT_WITH_KEYS.replace(",p.source_key_type,p.source_key", "") == core._VISIBLE_OFFERS_SELECT
