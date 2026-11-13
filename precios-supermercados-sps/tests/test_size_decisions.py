"""Decisiones humanas de tamaño: contrato del panel, motor, guardado y cola."""
from __future__ import annotations

import json
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import aplicar_decisiones_tamano as aplicar  # noqa: E402
import exportar_cola_tamanos as cola  # noqa: E402
from medir_reglas_identidad import build_profiles, resolve_groups  # noqa: E402
from precios_supermercados.product_homologation import PresentationSignature  # noqa: E402
from precios_supermercados.product_homologation_persistence import (  # noqa: E402
    build_homologation_rows,
    records_from_product_rows,
)
from precios_supermercados.product_master import SQLiteMasterStore, ensure_master_schema, gtin_master_id  # noqa: E402
from precios_supermercados.size_decisions import (  # noqa: E402
    SELECT_SQL,
    SizeDecision,
    SizeDecisionError,
    decision_from_row,
    human_attributes,
    index_decisions,
    match_official,
    parse_payload,
)

CHOLULA = "00049733830119"
MCCORMICK = "07411000204139"
ROWS = [
    (3, "colonial", "CHOLULA Hot Chipotle 150 mL", "Cholula", None, "Salsas", "0049733830119"),
    (4, "comisariato_los_andes", "Cholula hot sauce chipotle 5 oz", "Cholula", "UN", "Salsas", "0049733830119"),
    (5, "walmart", "Salsa Cholula picante chipotle - 150 g", "Cholula", None, "Salsas", "0049733830119"),
    (6, "paiz", "Salsa Cholula picante chipotle - 150 g", "Cholula", "150 g", "Salsas", "0049733830119"),
    (11, "colonial", "McCORMICK Caesar 180ml", "McCormick", None, "Aderezos", "7411000204139"),
    (12, "la_colonia", "Aderezo Mccormick Caesar 180 Ml", "McCormick", None, "Aderezos", "7411000204139"),
    (13, "paiz", "Aderezo Mccormick Caesar Dp - 180 g", "McCormick", "180 g", "Aderezos", "7411000204139"),
    (14, "walmart", "Aderezo Mccormick Caesar Dp - 180 g", "McCormick", None, "Aderezos", "7411000204139"),
]
NOW = "2026-11-12T10:00:00Z"


def payload(*decisions: dict[str, object], schema: str = "rpi-size-decisions/v1") -> str:
    return json.dumps({"schema": schema, "decisions": list(decisions)})


def decisions(*items: dict[str, object]) -> dict[str, SizeDecision]:
    return index_decisions(parse_payload(payload(*items), decided_by="Jchernand3z19", decided_at_utc=NOW))


def statuses(size_decisions: dict[str, SizeDecision] | None = None) -> dict[int, object]:
    rows = build_homologation_rows(records_from_product_rows(ROWS), updated_at_utc=NOW, size_decisions=size_decisions)
    return {row.product_id: row for row in rows}


def sig(dimension: str, total: str) -> PresentationSignature:
    return PresentationSignature(dimension=dimension, total_base=Decimal(total), pack_count=1, unit_amount_base=Decimal(total))


# ----------------------------------------------------------------- contrato


def test_payload_is_validated_and_normalized() -> None:
    parsed = parse_payload(
        payload(
            {"gtin": "0049733830119", "decision": "size", "value": "5", "unit": "oz", "note": "  dice 5 fl oz  "},
            {"gtin": "7411000204139", "decision": "size", "value": "1", "unit": "lb"},
            {"gtin": "07501017003075", "decision": "different_products"},
        ),
        decided_by="Jchernand3z19",
        decided_at_utc=NOW,
    )
    cholula, mccormick, costena = parsed
    assert (cholula.canonical_gtin, cholula.net_content_value, cholula.net_content_unit) == (CHOLULA, Decimal("5"), "oz")
    assert cholula.note == "dice 5 fl oz" and cholula.decided_by == "human:Jchernand3z19"
    assert (mccormick.net_content_unit, mccormick.declared_unit) == ("g", "lb")
    assert mccormick.net_content_value == Decimal("453.59237")
    assert costena.decision == "different_products" and costena.official is None


@pytest.mark.parametrize(
    ("text", "error"),
    [
        (payload({"gtin": CHOLULA, "decision": "size", "value": "5", "unit": "oz"}, schema="otro"), "size_decisions_schema_invalid"),
        (payload(), "size_decisions_empty"),
        (payload({"gtin": "123", "decision": "size", "value": "5", "unit": "oz"}), "size_decision_gtin_invalid"),
        (payload({"gtin": CHOLULA, "decision": "size", "value": "5", "unit": "oz"},
                 {"gtin": "0049733830119", "decision": "different_products"}), "size_decision_gtin_duplicate"),
        (payload({"gtin": CHOLULA, "decision": "size", "value": "5", "unit": "kg"}), "size_decision_unit_invalid"),
        (payload({"gtin": CHOLULA, "decision": "size", "value": "-1", "unit": "g"}), "size_decision_value_invalid"),
        (payload({"gtin": CHOLULA, "decision": "size", "value": "1.5", "unit": "unit"}), "size_decision_value_out_of_range"),
        (payload({"gtin": CHOLULA, "decision": "size", "value": "5", "unit": "oz", "sql": "x"}), "size_decision_fields_invalid"),
        (payload({"gtin": CHOLULA, "decision": "different_products", "value": "5"}), "size_decision_fields_invalid"),
        (payload({"gtin": CHOLULA, "decision": "merge"}), "size_decision_kind_invalid"),
        (payload({"gtin": CHOLULA, "decision": "different_products", "note": "a\u0000b"}), "size_decision_note_invalid"),
        ("{no es json", "size_decisions_json_invalid"),
    ],
)
def test_payload_fails_closed(text: str, error: str) -> None:
    with pytest.raises(SizeDecisionError, match=error):
        parse_payload(text, decided_by="Jchernand3z19", decided_at_utc=NOW)


def test_payload_rejects_bad_actor_and_oversize() -> None:
    good = payload({"gtin": CHOLULA, "decision": "different_products"})
    with pytest.raises(SizeDecisionError, match="actor_invalid"):
        parse_payload(good, decided_by="x; rm -rf", decided_at_utc=NOW)
    many = payload(*({"gtin": f"{n:013d}", "decision": "different_products"} for n in range(301)))
    with pytest.raises(SizeDecisionError):
        parse_payload(many, decided_by="Jchernand3z19", decided_at_utc=NOW)


def test_official_match_uses_total_size_tolerance_and_ounce_readings() -> None:
    five_oz = sig("ounce", "5")
    assert match_official(five_oz, sig("volume_ml", "150")) == (True, None)  # 147.9 ml: 1.4 %
    assert match_official(five_oz, sig("mass_g", "150"))[0] is False  # 141.7 g: 5.5 %
    assert match_official(sig("volume_ml", "150"), sig("ounce", "5")) == (True, "fluid")
    assert match_official(sig("mass_g", "800"), sig("mass_g", "820"))[0] is False
    assert match_official(sig("mass_g", "300"), PresentationSignature("mass_g", Decimal("300"), 3, Decimal("100")))[0]
    assert match_official(sig("mass_g", "150"), None) == (True, None)


def test_official_match_accepts_g_ml_only_with_the_v2_9_tolerance() -> None:
    five = Decimal("0.05")
    # Sin la tolerancia (fuera de lácteos/jugos/salsas) g y ml nunca coinciden.
    assert match_official(sig("mass_g", "150"), sig("volume_ml", "150"))[0] is False
    # Con ella: oficial en g contra oferta en ml y viceversa, ≤ 5 %.
    assert match_official(sig("mass_g", "150"), sig("volume_ml", "150"), mass_volume_tolerance=five) == (True, None)
    assert match_official(sig("volume_ml", "400"), sig("mass_g", "382.72"), mass_volume_tolerance=five)[0]
    assert match_official(sig("volume_ml", "1000"), sig("mass_g", "940"), mass_volume_tolerance=five)[0] is False  # 6 %
    # Contenido total: 3 × 100 ml contra 300 g.
    pack = PresentationSignature("volume_ml", Decimal("300"), 3, Decimal("100"))
    assert match_official(sig("mass_g", "300"), pack, mass_volume_tolerance=five)[0]
    # Las onzas no entran en g ≈ ml: 5 oz (147.9 ml) no coincide con 150 g.
    assert match_official(sig("ounce", "5"), sig("mass_g", "150"), mass_volume_tolerance=five)[0] is False
    assert match_official(sig("mass_g", "150"), sig("ounce", "5"), mass_volume_tolerance=five)[0] is False
    # Ni con unidades.
    assert match_official(sig("count", "6"), sig("mass_g", "6"), mass_volume_tolerance=five)[0] is False


def test_stored_rows_round_trip_to_master_attributes() -> None:
    decision = decisions({"gtin": CHOLULA, "decision": "size", "value": "5", "unit": "oz"})[CHOLULA]
    row = (CHOLULA, "size", "5", "oz", "5", "oz", None, "human:Jchernand3z19", NOW)
    assert decision_from_row(row) == decision
    attributes = human_attributes(decision)
    assert attributes["net_content_value"]["value"] == "5"
    assert attributes["net_content_unit"] == {"value": "oz", "decided_by": "human:Jchernand3z19",
                                              "decided_at_utc": NOW, "source": "size_decisions"}


# -------------------------------------------------------------------- motor


def test_engine_groups_offers_matching_the_official_size_and_isolates_the_rest() -> None:
    before = statuses()
    # v2.9 sin decisiones: g ≈ ml en salsas y aderezos (Colonial 150 ml con los 150 g; McCormick
    # 180 ml ×2 con 180 g ×2); la onza de Los Andes (141.7 g) sigue fuera.
    assert before[3].comparison_status == before[5].comparison_status == "ready"
    assert before[4].comparison_status == "review_required"
    assert before[11].comparison_status == before[13].comparison_status == "ready"

    after = statuses(decisions(
        {"gtin": CHOLULA, "decision": "size", "value": "5", "unit": "oz"},
        {"gtin": MCCORMICK, "decision": "size", "value": "180", "unit": "ml"},
    ))
    assert after[3].comparison_status == after[4].comparison_status == "ready"
    assert after[5].comparison_status == after[6].comparison_status == "review_required"
    assert "human_size_decision_mismatch" in after[5].conflict_reasons_json
    # Oficial 180 ml: desde v2.9 las ofertas en 180 g de aderezo coinciden (g ≈ ml).
    assert {after[pid].comparison_status for pid in (11, 12, 13, 14)} == {"ready"}
    # La onza del grupo se lee como líquido (precio por unidad comparable).
    assert after[4].presentation_dimension == "volume_ml"
    # La decisión cambia la huella: el refresco reescribe esos perfiles.
    assert after[3].profile_hash != before[3].profile_hash


def test_decision_outside_g_ml_categories_keeps_isolating_the_other_unit() -> None:
    rows = [
        (31, "walmart", "Shampoo Sedal Rizos - 400 ml", "Sedal", None, "Cuidado personal", "7501056330217"),
        (32, "paiz", "Shampoo Sedal Rizos - 400 ml", "Sedal", "400 ml", "Cuidado capilar", "7501056330217"),
        (33, "la_colonia", "Shampoo Sedal Rizos 400 g", "Sedal", "400 g", "Cuidado capilar", "7501056330217"),
    ]
    gtin = "07501056330217"
    decided = index_decisions(parse_payload(payload({"gtin": gtin, "decision": "size", "value": "400", "unit": "g"}),
                                            decided_by="Jchernand3z19", decided_at_utc=NOW))
    out = {row.product_id: row for row in build_homologation_rows(
        records_from_product_rows(rows), updated_at_utc=NOW, size_decisions=decided)}
    assert out[33].comparison_status == "review_required"  # sólo una cadena en 400 g: no hay grupo
    assert "human_size_decision_mismatch" in out[31].conflict_reasons_json  # shampoo: g ≠ ml


def test_different_products_never_groups_the_gtin() -> None:
    after = statuses(decisions({"gtin": MCCORMICK, "decision": "different_products"}))
    for product_id in (11, 12, 13, 14):
        assert after[product_id].comparison_status == "review_required"
        assert "human_different_products" in after[product_id].conflict_reasons_json


# ------------------------------------------------------------ guardar (SQLite)


def database(with_master: bool = True) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("CREATE TABLE product_homologation_profiles (product_id INTEGER PRIMARY KEY, canonical_gtin TEXT)")
    connection.executemany("INSERT INTO product_homologation_profiles VALUES (?,?)", [(1, CHOLULA), (2, MCCORMICK)])
    if with_master:
        ensure_master_schema(SQLiteMasterStore(connection))
        connection.execute(
            "INSERT INTO master_products(master_product_id,primary_gtin,origin_method,display_name,"
            "attribute_provenance_json,human_attributes_json,status,record_hash,builder_version,created_at_utc,"
            "updated_at_utc,version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (gtin_master_id(CHOLULA), CHOLULA, "gtin", "Salsa Cholula", "{}", '{"brand":{"value":"Cholula"}}',
             "active", "0" * 64, "test", NOW, NOW, 1),
        )
    return connection


def test_apply_saves_decisions_updates_master_and_marks_it_dirty() -> None:
    connection = database()
    store = SQLiteMasterStore(connection)
    text = payload({"gtin": CHOLULA, "decision": "size", "value": "5", "unit": "oz", "note": "etiqueta 5 fl oz"},
                   {"gtin": MCCORMICK, "decision": "size", "value": "180", "unit": "ml"})

    dry = aplicar.apply_size_decisions(store, text, actor="Jchernand3z19", now=NOW)
    assert dry["written"] is False and dry["counts"] == {"size": 2, "different_products": 0, "masters_updated": 1}
    assert not connection.execute("SELECT name FROM sqlite_master WHERE name='size_decisions'").fetchall()

    result = aplicar.apply_size_decisions(store, text, actor="Jchernand3z19", apply=True, now=NOW)
    assert result["written"] is True
    stored = index_decisions(decision_from_row(row) for row in connection.execute(SELECT_SQL).fetchall())
    assert stored[CHOLULA].note == "etiqueta 5 fl oz" and stored[MCCORMICK].net_content_unit == "ml"
    human = json.loads(connection.execute(
        "SELECT human_attributes_json FROM master_products WHERE master_product_id=?", (gtin_master_id(CHOLULA),)
    ).fetchone()[0])
    assert human["brand"] == {"value": "Cholula"}  # lo demás se conserva
    assert human["net_content_value"]["value"] == "5" and human["net_content_unit"]["value"] == "oz"
    assert connection.execute("SELECT master_product_id, reason FROM master_sync_dirty").fetchall() == [
        (gtin_master_id(CHOLULA), "size_decision")]

    # Cambiar de opinión: "no es el mismo producto" reemplaza la fila y quita el tamaño del maestro.
    aplicar.apply_size_decisions(store, payload({"gtin": CHOLULA, "decision": "different_products"}),
                                 actor="Jchernand3z19", apply=True, now=NOW)
    assert connection.execute("SELECT decision FROM size_decisions WHERE canonical_gtin=?", (CHOLULA,)).fetchone() == (
        "different_products",)
    human = json.loads(connection.execute("SELECT human_attributes_json FROM master_products").fetchone()[0])
    assert human == {"brand": {"value": "Cholula"}}


def test_apply_refuses_unknown_gtins_without_writing() -> None:
    connection = database(with_master=False)
    result = aplicar.apply_size_decisions(
        SQLiteMasterStore(connection),
        payload({"gtin": CHOLULA, "decision": "size", "value": "5", "unit": "oz"},
                {"gtin": "07501017003075", "decision": "different_products"}),
        actor="Jchernand3z19", apply=True, now=NOW,
    )
    assert result["refused"] == "errors_present" and result["written"] is False
    assert result["errors"] == ["gtin_sin_perfiles:07501017003075"]
    assert not connection.execute("SELECT name FROM sqlite_master WHERE name='size_decisions'").fetchall()


def test_apply_cli_reads_payload_from_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "db.sqlite"
    disk = sqlite3.connect(path)
    database(with_master=False).backup(disk)
    disk.close()
    monkeypatch.setenv("DECISIONS_JSON", payload({"gtin": MCCORMICK, "decision": "size", "value": "180", "unit": "ml"}))
    summary = tmp_path / "out" / "resultado.json"
    assert aplicar.main(["--sqlite", str(path), "--actor", "Jchernand3z19", "--apply", "--summary", str(summary)]) == 0
    assert json.loads(summary.read_text())["written"] is True
    monkeypatch.setenv("DECISIONS_JSON", "{}")
    assert aplicar.main(["--sqlite", str(path), "--actor", "Jchernand3z19"]) == 1


# --------------------------------------------------------------------- cola


SEDAL = "07501056330217"
SHAMPOO_ROWS = [  # fuera de lácteos/jugos/salsas: 400 ml contra 400 g sigue siendo un empate
    (31, "walmart", "Shampoo Sedal Rizos - 400 ml", "Sedal", None, "Cuidado personal", "7501056330217"),
    (32, "paiz", "Shampoo Sedal Rizos - 400 g", "Sedal", "400 g", "Cuidado capilar", "7501056330217"),
]


def test_queue_lists_ties_and_outliers_and_skips_decided_gtins() -> None:
    rows = ROWS + SHAMPOO_ROWS
    groups = resolve_groups(build_profiles([record for _, record in records_from_product_rows(rows)]))
    items = cola.queue_items(groups, skip=set())
    by_gtin = {item["gtin"]: item for item in items}
    assert by_gtin[SEDAL]["kind"] == "empate"
    assert MCCORMICK not in by_gtin  # v2.9: 180 ml ≈ 180 g en aderezos, ya comparable sin revisión
    assert by_gtin[CHOLULA]["kind"] == "atipico"  # v2.9: Colonial, Walmart y Paiz comparan; Los Andes fuera
    cholula = {offer["store"]: offer for offer in by_gtin[CHOLULA]["offers"]}
    assert cholula["colonial"]["excluded"] is False and cholula["comisariato_los_andes"]["excluded"] is True
    assert (cholula["comisariato_los_andes"]["value"], cholula["comisariato_los_andes"]["unit"]) == ("5", "oz")
    assert cholula["paiz"]["size"] == "150 g"
    assert cholula["paiz"]["code"] == "0049733830119"  # forma publicada por el súper
    assert cholula["paiz"]["ref"] == "paiz:6"  # para hallar la foto de su tarjeta
    assert [item["gtin"] for item in cola.queue_items(groups, skip={SEDAL})] == [CHOLULA]


def test_queue_sizes_are_rounded_to_two_decimals() -> None:
    rows = [
        (21, "colonial", "Harina Pan 2.2 lb", "PAN", None, "Harinas", "0739907000010"),
        (22, "la_colonia", "Harina Pan De Maiz Blanco 2 Lb", "PAN", None, "Harinas", "0739907000010"),
    ]
    groups = resolve_groups(build_profiles([record for _, record in records_from_product_rows(rows)]))
    sizes = {offer["store"]: (offer["value"], offer["size"]) for item in cola.queue_items(groups, skip=set())
             for offer in item["offers"]}
    assert sizes == {"colonial": ("997.9", "997.9 g"), "la_colonia": ("907.18", "907.18 g")}


# ------------------------------------------------------------ motor (IA)


def test_engine_decisions_never_replace_a_human_one_and_humans_replace_the_engine() -> None:
    connection = database()
    store = SQLiteMasterStore(connection)
    aplicar.apply_size_decisions(store, payload({"gtin": CHOLULA, "decision": "size", "value": "5", "unit": "oz"}),
                                 actor="Jchernand3z19", apply=True, now=NOW)
    engine = json.dumps({"schema": "rpi-size-decisions/v1", "decisions": [
        {"gtin": CHOLULA, "decision": "size", "value": "150", "unit": "g", "note": "Gemini: 150 g"},
        {"gtin": MCCORMICK, "decision": "size", "value": "180", "unit": "ml", "note": "Gemini: Cont. neto 180 ml"},
        {"gtin": "07501017003075", "decision": "size", "value": "820", "unit": "g"},
    ]})
    result = aplicar.apply_engine_decisions(store, engine, engine="gemini_auto", apply=True, now=NOW)
    assert result["decisions"] == 1 and result["skipped_human"] == [CHOLULA]
    assert result["skipped_unknown"] == ["07501017003075"]
    rows = dict(connection.execute("SELECT canonical_gtin, decided_by FROM size_decisions").fetchall())
    assert rows == {CHOLULA: "human:Jchernand3z19", MCCORMICK: "engine:gemini_auto"}
    human = json.loads(connection.execute("SELECT human_attributes_json FROM master_products").fetchone()[0])
    assert human["net_content_unit"]["value"] == "oz"  # la IA no tocó el maestro decidido por una persona

    # Una persona corrige a la IA: su decisión reemplaza la del motor.
    aplicar.apply_size_decisions(store, payload({"gtin": MCCORMICK, "decision": "different_products"}),
                                 actor="Jchernand3z19", apply=True, now=NOW)
    assert connection.execute("SELECT decided_by FROM size_decisions WHERE canonical_gtin=?", (MCCORMICK,)).fetchone() == (
        "human:Jchernand3z19",)


def test_engine_upsert_guard_protects_a_human_row_written_in_between() -> None:
    connection = database()
    store = SQLiteMasterStore(connection)
    decisions = parse_payload(payload({"gtin": CHOLULA, "decision": "size", "value": "150", "unit": "g"}),
                              decided_by="gemini_auto", decided_at_utc=NOW, engine=True)
    steps, _, _ = aplicar.plan(store, decisions, now=NOW, engine=True)
    aplicar.apply_size_decisions(store, payload({"gtin": CHOLULA, "decision": "size", "value": "5", "unit": "oz"}),
                                 actor="Jchernand3z19", apply=True, now=NOW)
    store.write(steps)  # llega tarde: la fila humana ya existe
    assert connection.execute("SELECT decided_by FROM size_decisions").fetchone() == ("human:Jchernand3z19",)
    human = json.loads(connection.execute("SELECT human_attributes_json FROM master_products").fetchone()[0])
    assert human["net_content_value"]["value"] == "5"


def test_engine_actor_is_an_allowlist_and_humans_cannot_pose_as_the_engine() -> None:
    text = payload({"gtin": CHOLULA, "decision": "different_products"})
    with pytest.raises(SizeDecisionError, match="actor_invalid"):
        parse_payload(text, decided_by="otra_ia", decided_at_utc=NOW, engine=True)
    assert parse_payload(text, decided_by="engine", decided_at_utc=NOW)[0].decided_by == "human:engine"
    with pytest.raises(SystemExit):
        aplicar.main(["--sqlite", "x.db", "--engine", "otra_ia"])


def test_queue_offers_carry_their_card_photo_and_who_it_came_from() -> None:
    from auditar_identidad_productos import CatalogRow

    key_a, key_b = "a" * 24, "b" * 24
    cities = {"sps": [CatalogRow("sps", key_a, "x", [("colonial", 3), ("los_andes", 4)]),
                      CatalogRow("sps", key_b, "y", [("walmart", 5)]), CatalogRow("sps", "c" * 24, "z", [("paiz", 6)])]}
    index = {key_a: {"h": "1111111111", "sm": "comisariato_los_andes"}, key_b: {"h": "2222222222", "sm": "walmart"},
             "c" * 24: {"none": 1, "h": "3333333333"}}
    photos = cola.photo_names(cities, index)
    assert photos == {"colonial:3": (f"{key_a}-1111111111", "comisariato_los_andes"),
                      "los_andes:4": (f"{key_a}-1111111111", "comisariato_los_andes"),
                      "walmart:5": (f"{key_b}-2222222222", "walmart")}
    items = [{"offers": [{"ref": "colonial:3"}, {"ref": "paiz:6"}]}]
    assert cola.attach_photos(items, photos) == 1
    assert items[0]["offers"][0]["foto_de"] == "comisariato_los_andes" and "foto" not in items[0]["offers"][1]
    assert cola.photo_url(f"{key_b}-2222222222", 320).endswith(f"/320/bb/{key_b}-2222222222.webp")


def test_claude_engine_also_never_replaces_a_human_decision() -> None:
    connection = database()
    store = SQLiteMasterStore(connection)
    aplicar.apply_size_decisions(store, payload({"gtin": CHOLULA, "decision": "size", "value": "5", "unit": "oz"}),
                                 actor="Jchernand3z19", apply=True, now=NOW)
    engine = json.dumps({"schema": "rpi-size-decisions/v1", "decisions": [
        {"gtin": CHOLULA, "decision": "size", "value": "150", "unit": "g", "note": "Claude: 150 g"},
        {"gtin": MCCORMICK, "decision": "size", "value": "180", "unit": "ml", "note": "Claude: 180 ml"},
    ]})
    result = aplicar.apply_engine_decisions(store, engine, engine="claude_auto", apply=True, now=NOW)
    assert result["skipped_human"] == [CHOLULA]
    rows = dict(connection.execute("SELECT canonical_gtin, decided_by FROM size_decisions").fetchall())
    assert rows == {CHOLULA: "human:Jchernand3z19", MCCORMICK: "engine:claude_auto"}


def test_claude_session_needs_approval_and_leaves_it_in_the_note() -> None:
    connection = database()
    store = SQLiteMasterStore(connection)
    aplicar.apply_size_decisions(store, payload({"gtin": CHOLULA, "decision": "size", "value": "5", "unit": "oz"}),
                                 actor="Jchernand3z19", apply=True, now=NOW)
    text = json.dumps({"schema": "rpi-size-decisions/v1", "decisions": [
        {"gtin": MCCORMICK, "decision": "size", "value": "180", "unit": "ml", "note": "La Colonia 180 ml · Walmart 180 ml"},
        {"gtin": CHOLULA, "decision": "size", "value": "150", "unit": "g"},
    ]})
    with pytest.raises(SizeDecisionError, match="approval_invalid"):
        aplicar.apply_engine_decisions(store, text, engine="claude_sesion", apply=True, now=NOW)
    with pytest.raises(SizeDecisionError, match="approval_invalid"):
        aplicar.apply_engine_decisions(store, text, engine="claude_auto", apply=True, now=NOW, approved_by="Jchernand3z19")
    with pytest.raises(SizeDecisionError, match="actor_invalid"):
        aplicar.apply_engine_decisions(store, text, engine="claude_sesion", apply=True, now=NOW, approved_by="a b")
    result = aplicar.apply_engine_decisions(store, text, engine="claude_sesion", apply=True, now=NOW,
                                            approved_by="Jchernand3z19")
    assert result["skipped_human"] == [CHOLULA]  # una decisión humana nunca se reemplaza
    row = connection.execute("SELECT decided_by, note FROM size_decisions WHERE canonical_gtin=?", (MCCORMICK,)).fetchone()
    assert row == ("engine:claude_sesion", "La Colonia 180 ml · Walmart 180 ml · aprobado por Jchernand3z19")


def test_claude_session_respects_the_panel_limits() -> None:
    store = SQLiteMasterStore(database())
    many = json.dumps({"schema": "rpi-size-decisions/v1", "decisions": [
        {"gtin": f"{n:014d}", "decision": "different_products"} for n in range(1, 302)]})
    with pytest.raises(SizeDecisionError, match="too_many"):
        aplicar.apply_engine_decisions(store, many, engine="claude_sesion", now=NOW, approved_by="Jchernand3z19")
    with pytest.raises(SizeDecisionError, match="too_large"):
        aplicar.apply_engine_decisions(store, "x" * 60_001, engine="claude_sesion", now=NOW, approved_by="Jchernand3z19")


def test_engine_items_publish_ai_decisions_with_their_offers_for_the_ai_tab() -> None:
    groups = resolve_groups(build_profiles([record for _, record in records_from_product_rows(ROWS)]))
    rows = {
        MCCORMICK: {"decision": "size", "value": "180", "unit": "ml", "note": "Claude (sesión): 180 ml · aprobado por X",
                    "by": "engine:claude_sesion", "at": "2026-11-13T10:00:00Z"},
        CHOLULA: {"decision": "size", "value": "5", "unit": "oz", "note": "x", "by": "human:Jchernand3z19", "at": "T"},
    }
    items = cola.engine_items(groups, rows)
    assert [item["gtin"] for item in items] == [MCCORMICK]  # las humanas no son de la IA
    assert items[0]["ia"] == {"status": "auto", "fuente": "claude_sesion", "value": "180", "unit": "ml",
                              "text": "Claude (sesión): 180 ml · aprobado por X"}
    assert items[0]["offers"] and items[0]["kind"] == "resuelto_ia"


def test_engine_rows_reads_only_engine_decisions() -> None:
    connection = database()
    store = SQLiteMasterStore(connection)
    aplicar.apply_size_decisions(store, payload({"gtin": CHOLULA, "decision": "size", "value": "5", "unit": "oz"}),
                                 actor="Jchernand3z19", apply=True, now=NOW)
    aplicar.apply_engine_decisions(store, payload({"gtin": MCCORMICK, "decision": "different_products"}),
                                   engine="claude_auto", apply=True, now=NOW)

    class Reader:
        def query(self, sql, params=()):
            return connection.execute(sql, params).fetchall()

    rows = cola.engine_rows(Reader())
    assert list(rows) == [MCCORMICK] and rows[MCCORMICK]["by"] == "engine:claude_auto"
