"""Motor v2.10: "corrige con evidencia" (foto de cada oferta) sobre las decisiones de tamaño."""
from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import aplicar_decisiones_tamano as aplicar  # noqa: E402
import exportar_consumer_catalog_core as core  # noqa: E402
import guardar_revision_claude as guardar  # noqa: E402
import sugerir_tamanos_ia as ia  # noqa: E402
from medir_reglas_identidad import build_profiles, evidence_report  # noqa: E402
from precios_supermercados.product_homologation_persistence import (  # noqa: E402
    build_homologation_rows,
    records_from_product_rows,
)
from precios_supermercados.product_identity_v2 import (  # noqa: E402
    SIZE_EVIDENCE_CONTRADICTED,
    resolve_gtin_group,
)
from precios_supermercados.product_master import SQLiteMasterStore  # noqa: E402
from precios_supermercados.size_decisions import (  # noqa: E402
    EVIDENCE_SELECT_SQL,
    EVIDENCE_TABLE_SQL,
    SELECT_SQL,
    OfferEvidence,
    SizeDecision,
    SizeDecisionError,
    attach_evidence,
    decision_from_row,
    evidence_from_text,
    index_decisions,
    parse_payload,
)

NOW = "2026-11-14T10:00:00Z"
EL_MIGO = "00012656001065"
LACTOLAC = "00787003016091"
ROWS = [
    # El Migo: decidido 240 g (peso neto); Walmart, Paiz y Los Andes publican 148 g (peso escurrido).
    (1, "colonial", "ELMIGO Maiz Dulce Enlatado 8.5oz", "El Migo", None, "Enlatados", "0012656001065"),
    (2, "comisariato_los_andes", "Elmigo maiz dulce granos 148.83 g", "El Migo", None, "Enlatados", "012656001065"),
    (3, "la_colonia", "Maiz Elmigo Dulce En Grano Lata 240 Gr", "El Migo", "240 Gr", "Enlatados", "012656001065"),
    (4, "paiz", "Maíz Dulce En Granos El Migo - 148 g", "El Migo", "148 g", "Enlatados", "0012656001065"),
    (5, "walmart", "Maíz Dulce En Granos El Migo - 148 g", "El Migo", None, "Enlatados", "0012656001065"),
    # Lactolac: decidido 240 g; Colonial y La Colonia publican 230 g, Los Andes 16 rebanadas.
    (11, "colonial", "LACTOLAC Queso Americano Blanco 16 Rebanadas 230g", "Lactolac", None, "Quesos",
     "0787003016091"),
    (12, "comisariato_los_andes", "Queso americano blanco Lactolac 16 unidades", "Lactolac", None, "Quesos",
     "787003016091"),
    (13, "la_colonia", "Queso Lactolac Americano Blanco 16 Rebanadas 230 Gr", "Lactolac", "230 Gr", "Quesos",
     "787003016091"),
    (14, "paiz", "Queso Lactolac americano blanco procesado 16 rebanadas - 240 g", "Lactolac", "240 g", "Quesos",
     "0787003016091"),
    (15, "walmart", "Queso Lactolac americano blanco procesado 16 rebanadas - 240 g", "Lactolac", None, "Quesos",
     "0787003016091"),
]
SIZE_240 = {"decision": "size", "value": "240", "unit": "g"}


def payload(*items: dict[str, object]) -> str:
    return json.dumps({"schema": "rpi-size-decisions/v1", "decisions": list(items)})


def decide(gtin: str, evidence: list[tuple[str, str]] | None = None, *, apply: str | None = None,
           engine: str | None = "claude_sesion") -> dict[str, SizeDecision]:
    item: dict[str, object] = {"gtin": gtin, **SIZE_240}
    if evidence is not None:
        item["evidence"] = [{"store": store, "value": value, "unit": "g", "text": f"{value} g"} for store, value in evidence]
    if apply is not None:
        item["apply"] = apply
    if engine:
        return index_decisions(parse_payload(payload(item), decided_by=engine, decided_at_utc=NOW, engine=True))
    return index_decisions(parse_payload(payload(item), decided_by="Jchernand3z19", decided_at_utc=NOW))


def rows(decisions: dict[str, SizeDecision] | None) -> dict[int, object]:
    built = build_homologation_rows(records_from_product_rows(ROWS), updated_at_utc=NOW, size_decisions=decisions)
    return {row.product_id: row for row in built}


def members(gtin: str):
    profiles = build_profiles([record for _, record in records_from_product_rows(ROWS)])
    return [profile for profile in profiles if profile.canonical_gtin == gtin]


def status(built: dict[int, object], *ids: int) -> set[str]:
    return {built[product_id].comparison_status for product_id in ids}


def reasons(built: dict[int, object], product_id: int) -> list[str]:
    return json.loads(built[product_id].conflict_reasons_json)


def published(row) -> str | None:
    return core.published_size(
        row.comparison_status, row.presentation_dimension, row.presentation_total_base, row.normalized_quantity,
        row.normalized_unit, row.normalized_pack_count, row.canonical_total,
    )


# ------------------------------------------------------------------- motor


def test_without_evidence_the_decision_works_like_v2_9() -> None:
    built = rows(decide(LACTOLAC))
    assert status(built, 14, 15) == {"ready"}
    for product_id in (11, 12, 13):
        assert built[product_id].comparison_status == "review_required"
        assert "human_size_decision_mismatch" in reasons(built, product_id)
    # El panel sin evidencia da exactamente lo mismo que un envío de Claude sin evidencia.
    assert rows(decide(LACTOLAC, engine=None)) == built
    assert all(published(row) is None for row in built.values())


def test_el_migo_majority_of_photos_corrects_every_offer_to_240_g() -> None:
    built = rows(decide(EL_MIGO, [("colonial", "240"), ("la_colonia", "240"), ("walmart", "240")]))
    assert status(built, 1, 2, 3, 4, 5) == {"ready"}
    for product_id, was in ((2, "148.83 g"), (4, "148 g"), (5, "148 g")):
        row = built[product_id]
        assert (row.presentation_dimension, row.presentation_total_base, row.display_presentation) == (
            "mass_g", "240", "240 g")
        # Lo que publica el súper se conserva en los campos canónicos → ``published_as``.
        assert published(row) == was
    assert published(built[3]) is None  # La Colonia ya publicaba 240 g


def test_lactolac_majority_corrects_all_even_the_offer_whose_photo_says_230() -> None:
    evidence = [("colonial", "240"), ("comisariato_los_andes", "240"), ("paiz", "240"), ("la_colonia", "230")]
    built = rows(decide(LACTOLAC, evidence))
    assert status(built, 11, 12, 13, 14, 15) == {"ready"}
    assert {built[i].display_presentation for i in (11, 12, 13, 14, 15)} == {"240 g"}
    assert published(built[13]) == "230 g" and published(built[11]) == "230 g"
    assert published(built[12]) == "16 unidades" and published(built[15]) is None
    details: dict[str, object] = {}
    resolve_gtin_group(LACTOLAC, members(LACTOLAC), decide(LACTOLAC, evidence)[LACTOLAC], details=details)
    assert details["mode"] == "mayoria" and details["contra"] == frozenset({"la_colonia"})
    assert not details["contradicted"] and not details["fallback_v2_9"]


def test_one_confirming_photo_corrects_only_its_offer_unless_apply_all() -> None:
    built = rows(decide(LACTOLAC, [("colonial", "240")]))
    assert status(built, 11, 14, 15) == {"ready"} and published(built[11]) == "230 g"
    assert "human_size_decision_mismatch" in reasons(built, 12)  # sin evidencia: como hoy
    assert "human_size_decision_mismatch" in reasons(built, 13)
    everything = rows(decide(LACTOLAC, [("colonial", "240")], apply="all"))
    assert status(everything, 11, 12, 13, 14, 15) == {"ready"}


def test_no_evidence_with_apply_all_corrects_every_offer() -> None:
    built = rows(decide(LACTOLAC, apply="all"))
    assert status(built, 11, 12, 13, 14, 15) == {"ready"}
    assert published(built[13]) == "230 g"


def test_own_photo_contradicting_the_official_isolates_it_as_another_presentation() -> None:
    built = rows(decide(LACTOLAC, [("la_colonia", "230")], apply="all"))
    assert status(built, 11, 12, 14, 15) == {"ready"}  # sin foto + apply=all → corregidas
    assert built[13].comparison_status == "review_required"
    assert SIZE_EVIDENCE_CONTRADICTED in reasons(built, 13)
    assert core.contradicted_by_photo(built[13].comparison_status, built[13].conflict_reasons_json)
    assert not core.contradicted_by_photo(built[11].comparison_status, built[11].conflict_reasons_json)


@pytest.mark.parametrize("evidence", [
    [("colonial", "240"), ("la_colonia", "230")],
    [("colonial", "240"), ("paiz", "240"), ("la_colonia", "230"), ("comisariato_los_andes", "230")],
])
def test_tied_photos_correct_nothing(evidence: list[tuple[str, str]]) -> None:
    for apply in (None, "all"):
        built = rows(decide(LACTOLAC, evidence, apply=apply))
        assert rows(decide(LACTOLAC)) == built  # como v2.9: queda para revisión humana
    details: dict[str, object] = {}
    resolve_gtin_group(LACTOLAC, members(LACTOLAC), decide(LACTOLAC, evidence)[LACTOLAC], details=details)
    assert details["mode"] == "empate" and not details["corrections"]


def test_never_worse_keeps_v2_9_when_a_contradicting_photo_would_block_the_group() -> None:
    # Sólo Paiz y Walmart coinciden por título; la foto de Paiz dice 230 → aislarla deja 1 cadena.
    decision = decide(LACTOLAC, [("paiz", "230")])
    assert rows(decision) == rows(decide(LACTOLAC))
    details: dict[str, object] = {}
    resolve_gtin_group(LACTOLAC, members(LACTOLAC), decision[LACTOLAC], details=details)
    assert details["fallback_v2_9"] is True and details["contradicted"] == frozenset()


def test_evidence_of_stores_without_offers_is_ignored() -> None:
    built = rows(decide(LACTOLAC, [("pricesmart", "240")]))
    assert built == rows(decide(LACTOLAC))


def test_human_and_ai_decisions_with_the_same_evidence_resolve_the_same() -> None:
    evidence = [("colonial", "240"), ("comisariato_los_andes", "240"), ("paiz", "240"), ("la_colonia", "230")]
    assert rows(decide(LACTOLAC, evidence, engine=None)) == rows(decide(LACTOLAC, evidence, engine="gemini_auto"))


def test_different_products_never_takes_evidence() -> None:
    with pytest.raises(SizeDecisionError, match="size_decision_fields_invalid"):
        parse_payload(payload({"gtin": LACTOLAC, "decision": "different_products", "apply": "all"}),
                      decided_by="Jchernand3z19", decided_at_utc=NOW)
    with pytest.raises(SizeDecisionError, match="size_decision_fields_invalid"):
        parse_payload(payload({"gtin": LACTOLAC, "decision": "different_products",
                               "evidence": [{"store": "paiz", "value": "240", "unit": "g"}]}),
                      decided_by="Jchernand3z19", decided_at_utc=NOW)
    built = rows(index_decisions(parse_payload(payload({"gtin": LACTOLAC, "decision": "different_products"}),
                                               decided_by="Jchernand3z19", decided_at_utc=NOW)))
    assert status(built, 11, 12, 13, 14, 15) == {"review_required"}


def test_ambiguous_multipack_is_lifted_only_for_corrected_offers() -> None:
    gtin = "00027000419137"
    pudding = [
        (21, "colonial", "Snack Pack Pudding Chocolate 4 pack 368 g", "Snack Pack", None, "Postres", "0027000419137"),
        (22, "paiz", "4 Pack Pudding Snack Pack Chocolate - 92 g", "Snack Pack", None, "Postres", "0027000419137"),
    ]
    records = records_from_product_rows(pudding)
    item = {"gtin": gtin, "decision": "size", "value": "368", "unit": "g"}
    plain = index_decisions(parse_payload(payload(item), decided_by="claude_sesion", decided_at_utc=NOW, engine=True))
    corrected = index_decisions(parse_payload(payload({**item, "evidence": [
        {"store": "colonial", "value": "368", "unit": "g"}, {"store": "paiz", "value": "368", "unit": "g"}]}),
        decided_by="claude_sesion", decided_at_utc=NOW, engine=True))
    before = {row.product_id: row for row in build_homologation_rows(records, updated_at_utc=NOW, size_decisions=plain)}
    after = {row.product_id: row for row in build_homologation_rows(records, updated_at_utc=NOW,
                                                                     size_decisions=corrected)}
    assert after[21].comparison_status == after[22].comparison_status == "ready"
    assert after[22].display_presentation == "368 g"
    for product_id in (21, 22):  # sin evidencia: como v2.9
        assert before[product_id].comparison_status == "review_required"
        assert "ambiguous_multipack_presentation" in json.loads(before[product_id].conflict_reasons_json)


# ----------------------------------------------------------------- contrato


def test_payload_evidence_is_validated_and_normalized() -> None:
    decision = parse_payload(payload({"gtin": LACTOLAC, **SIZE_240, "apply": "all", "evidence": [
        {"store": "paiz", "value": "8.5", "unit": "oz", "text": " 8.5  oz (240 g) "},
        {"store": "walmart", "value": "0.5", "unit": "lb"}]}),
        decided_by="claude_sesion", decided_at_utc=NOW, engine=True)[0]
    assert decision.apply == "all" and decision.evidence_given
    paiz, walmart = decision.evidence
    assert (paiz.supermarket_id, paiz.value, paiz.unit, paiz.text, paiz.source) == (
        "paiz", Decimal("8.5"), "oz", "8.5 oz (240 g)", "claude_sesion")
    assert (walmart.unit, walmart.value.quantize(Decimal("0.01"))) == ("g", Decimal("226.80"))
    human = parse_payload(payload({"gtin": LACTOLAC, **SIZE_240, "evidence": []}),
                          decided_by="Jchernand3z19", decided_at_utc=NOW)[0]
    assert human.evidence == () and human.evidence_given and human.apply == "evidence"
    old_panel = parse_payload(payload({"gtin": LACTOLAC, **SIZE_240}), decided_by="Jchernand3z19",
                              decided_at_utc=NOW)[0]
    assert not old_panel.evidence_given


@pytest.mark.parametrize(("extra", "error"), [
    ({"apply": "todo"}, "size_decision_apply_invalid"),
    ({"evidence": {"store": "paiz"}}, "size_evidence_invalid"),
    ({"evidence": [{"store": "maxi", "value": "1", "unit": "g"}]}, "size_evidence_store_invalid"),
    ({"evidence": [{"store": "paiz", "value": "1", "unit": "kg"}]}, "size_evidence_unit_invalid"),
    ({"evidence": [{"store": "paiz", "value": "0", "unit": "g"}]}, "size_decision_value_invalid"),
    ({"evidence": [{"store": "paiz", "value": "200000", "unit": "g"}]}, "size_evidence_value_out_of_range"),
    ({"evidence": [{"store": "paiz", "value": "1.5", "unit": "unit"}]}, "size_evidence_value_out_of_range"),
    ({"evidence": [{"store": "paiz", "value": "1", "unit": "g", "text": "x" * 61}]}, "size_evidence_text_too_long"),
    ({"evidence": [{"store": "paiz", "value": "1", "unit": "g", "text": "a\u0007"}]}, "size_evidence_text_invalid"),
    ({"evidence": [{"store": "paiz", "value": "1", "unit": "g", "foto": "x"}]}, "size_evidence_invalid"),
    ({"evidence": [{"store": "paiz", "unit": "g"}]}, "size_evidence_invalid"),
    ({"evidence": [{"store": "paiz", "value": "1", "unit": "g"}] * 2}, "size_evidence_store_duplicate"),
    ({"evidence": [{"store": s, "value": "1", "unit": "g"} for s in
                   ("paiz", "walmart", "colonial", "la_colonia", "pricesmart", "comisariato_los_andes", "paiz")]},
     "size_evidence_invalid"),
])
def test_payload_evidence_fails_closed(extra: dict[str, object], error: str) -> None:
    with pytest.raises(SizeDecisionError, match=error):
        parse_payload(payload({"gtin": LACTOLAC, **SIZE_240, **extra}), decided_by="Jchernand3z19", decided_at_utc=NOW)


@pytest.mark.parametrize(("text", "expected"), [
    ("8.5 oz (240 g)", ("240", "g")),
    ("13.5 fl oz (400 mL)", ("400", "ml")),
    ("230 g (2 × 115 g)", ("230", "g")),
    ("5 sobres × 10 g", ("50", "g")),
    ("4 × 3.25 oz · 13 oz (368 g)", ("368", "g")),
    ("cont. neto 142 g / esc. 100 g", ("142", "g")),
    ("10 × 6 fl oz (177 ml) · 60 fl oz (1.77 L)", ("1770", "ml")),
    ("0.23 fl oz · 6.8 ml", ("6.8", "ml")),
    ("4.0 fl oz (119 g)", ("119", "g")),
    ("6 pack × 200 ml", ("1200", "ml")),
    ("1 litro", ("1000", "ml")),
    ("5 oz", ("5", "oz")),
    ("110 g (parcial)", None),
    ("100 g (banner)", None),
    ("10 g (1 sobre)", None),
    ("200 ml c/u", None),
    ("946 ml (+Calcio Defensas, otro diseño)", None),
    ("60 g (50 sobres de 1.2 g)", None),
    ("1", None),
    (None, None),
])
def test_photo_text_becomes_evidence_only_when_unambiguous(text: object, expected) -> None:
    parsed = evidence_from_text(text)
    assert (None if parsed is None else (format(parsed[0], "f"), parsed[1])) == expected


# ----------------------------------------------------------------- guardado


def profiles_db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.execute("CREATE TABLE product_homologation_profiles (product_id INTEGER PRIMARY KEY, canonical_gtin TEXT)")
    connection.executemany("INSERT INTO product_homologation_profiles VALUES (?,?)", [(1, EL_MIGO), (2, LACTOLAC)])
    return connection


def stored(connection: sqlite3.Connection) -> dict[str, SizeDecision]:
    decisions = index_decisions(decision_from_row(row) for row in connection.execute(SELECT_SQL).fetchall())
    return attach_evidence(decisions, connection.execute(EVIDENCE_SELECT_SQL).fetchall())


def test_saving_replaces_the_gtin_evidence_and_its_scope_as_a_delta() -> None:
    connection = profiles_db()
    store = SQLiteMasterStore(connection)
    first = payload({"gtin": LACTOLAC, **SIZE_240, "apply": "all", "evidence": [
        {"store": "paiz", "value": "240", "unit": "g", "text": "240 g"},
        {"store": "la_colonia", "value": "230", "unit": "g"}]})
    result = aplicar.apply_engine_decisions(store, first, engine="claude_sesion", approved_by="Jchernand3z19",
                                            apply=True, now=NOW)
    assert result["written"] is True
    saved = stored(connection)[LACTOLAC]
    assert saved.apply == "all" and [(e.supermarket_id, e.source) for e in saved.evidence] == [
        ("la_colonia", "claude_sesion"), ("paiz", "claude_sesion")]
    # El panel actual (sin evidencia) reemplaza la decisión: borra el alcance, conserva las lecturas.
    aplicar.apply_size_decisions(store, payload({"gtin": LACTOLAC, **SIZE_240}), actor="Jchernand3z19",
                                 apply=True, now=NOW)
    saved = stored(connection)[LACTOLAC]
    assert saved.apply == "evidence" and len(saved.evidence) == 2 and saved.decided_by == "human:Jchernand3z19"
    # Una humana con evidencia la reemplaza entera.
    aplicar.apply_size_decisions(store, payload({"gtin": LACTOLAC, **SIZE_240, "evidence": [
        {"store": "walmart", "value": "240", "unit": "g"}]}), actor="Jchernand3z19", apply=True, now=NOW)
    assert [(e.supermarket_id, e.source) for e in stored(connection)[LACTOLAC].evidence] == [("walmart", "human")]


def test_ai_never_overwrites_a_human_decision_or_its_evidence() -> None:
    connection = profiles_db()
    store = SQLiteMasterStore(connection)
    aplicar.apply_size_decisions(store, payload({"gtin": LACTOLAC, **SIZE_240, "evidence": [
        {"store": "paiz", "value": "240", "unit": "g"}]}), actor="Jchernand3z19", apply=True, now=NOW)
    engine = payload({"gtin": LACTOLAC, "decision": "size", "value": "230", "unit": "g", "apply": "all",
                      "evidence": [{"store": "la_colonia", "value": "230", "unit": "g"}]})
    result = aplicar.apply_engine_decisions(store, engine, engine="gemini_auto", apply=True, now=NOW)
    assert result["skipped_human"] == [LACTOLAC]
    saved = stored(connection)[LACTOLAC]
    assert saved.net_content_value == Decimal("240") and saved.apply == "evidence"
    assert [(e.supermarket_id, e.source) for e in saved.evidence] == [("paiz", "human")]
    # Guarda SQL: aunque el plan del motor corriera, la evidencia exige que la fila sea de ese autor.
    steps, _, _ = aplicar.plan(store, parse_payload(engine, decided_by="gemini_auto", decided_at_utc=NOW, engine=True),
                               now=NOW, engine=True)
    store.write(steps)
    assert [(e.supermarket_id, e.source) for e in stored(connection)[LACTOLAC].evidence] == [("paiz", "human")]


@pytest.mark.parametrize("with_evidence_table", [True, False])
def test_refresh_reads_decisions_with_their_evidence(monkeypatch: pytest.MonkeyPatch, with_evidence_table: bool) -> None:
    import backfill_homologacion_turso as turso

    connection = profiles_db()
    store = SQLiteMasterStore(connection)
    aplicar.apply_size_decisions(store, payload({"gtin": LACTOLAC, **SIZE_240, "apply": "all", "evidence": [
        {"store": "paiz", "value": "240", "unit": "g"}]}), actor="Jchernand3z19", apply=True, now=NOW)
    if not with_evidence_table:
        connection.execute("DROP TABLE size_offer_evidence")
    sql: list[str] = []

    def query(_url: str, _token: str, statement: str, args: tuple[object, ...] = ()) -> list[list[object]]:
        sql.append(statement)
        return [list(row) for row in connection.execute(statement, args).fetchall()]

    monkeypatch.setattr(turso, "_query", query)
    decision = turso._fetch_size_decisions("https://x", "t")[LACTOLAC]
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in sql)
    if with_evidence_table:
        assert decision.apply == "all" and [e.supermarket_id for e in decision.evidence] == ["paiz"]
    else:
        assert decision.apply == "evidence" and decision.evidence == ()


def test_schema_guard_tolerates_the_evidence_table() -> None:
    import actualizar_mvp_turso_la_colonia as turso_updater
    import verificar_integridad_turso as integrity

    assert "size_offer_evidence" in turso_updater.OPTIONAL_DERIVED_TABLES
    assert "size_offer_evidence" in integrity.OPTIONAL_COUNTED_TABLES
    turso_updater._validate_table_names([*turso_updater.EXPECTED_TABLES, "size_decisions", "size_offer_evidence"])


def test_evidence_table_rejects_bad_rows() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute(EVIDENCE_TABLE_SQL)
    insert = "INSERT INTO size_offer_evidence VALUES (?,?,?,?,?,?,?)"
    connection.execute(insert, (LACTOLAC, "*", None, None, "human", "apply:all", NOW))
    for bad in ((LACTOLAC, "maxi", "1", "g", "human", None, NOW), (LACTOLAC, "paiz", None, None, "human", None, NOW),
                (LACTOLAC, "paiz", "1", "g", "openai", None, NOW), (LACTOLAC, "paiz", "1", "g", "human", "x" * 61, NOW)):
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(insert, bad)


def test_engine_chunks_respect_the_payload_limit() -> None:
    items = [{"gtin": LACTOLAC, **SIZE_240, "note": "x" * 190, "evidence": [
        {"store": s, "value": "240", "unit": "g", "text": "y" * 60}
        for s in ("paiz", "walmart", "colonial", "la_colonia", "pricesmart", "comisariato_los_andes")]}] * 300
    chunks = aplicar.engine_chunks("rpi-size-decisions/v1", items)
    assert len(chunks) > 1 and all(len(chunk) <= 60_000 for chunk in chunks)
    assert sum(len(json.loads(chunk)["decisions"]) for chunk in chunks) == 300


# ------------------------------------------------------- Claude sesión / IA


def test_session_save_sends_own_photo_evidence_only() -> None:
    batch = {"items": [{"gtin": EL_MIGO, "offers": [
        {"store": "colonial", "foto": "a", "foto_de": "colonial"},
        {"store": "comisariato_los_andes", "foto": "w", "foto_de": "walmart"},
        {"store": "la_colonia", "foto": "b", "foto_de": "la_colonia"},
        {"store": "walmart", "foto": "w", "foto_de": "walmart"},
        {"store": "paiz", "foto": None, "foto_de": None}]}]}
    results = {"items": [{"gtin": EL_MIGO, "propuesta": "size", "value": "240", "unit": "g", "aprobado": True,
                          "lecturas": {"colonial": "8.5 oz (240 g)", "comisariato_los_andes": "8.5 oz (240 g)",
                                       "la_colonia": "110 g (parcial)", "walmart": "8.5 oz (240 g)",
                                       "paiz": "240 g"}}]}
    document, _ = guardar.build(results, batch)
    decision = document["decisions"][0]
    assert decision["evidence"] == [
        {"store": "colonial", "value": "240", "unit": "g", "text": "8.5 oz (240 g)"},
        {"store": "walmart", "value": "240", "unit": "g", "text": "8.5 oz (240 g)"}]
    assert "apply" not in decision
    parse_payload(json.dumps(document), decided_by="claude_sesion", decided_at_utc=NOW, engine=True)
    results["items"][0]["apply"] = "all"
    assert guardar.build(results, batch)[0]["decisions"][0]["apply"] == "all"


def test_ai_auto_decision_carries_the_reading_of_each_own_photo() -> None:
    item = {"gtin": LACTOLAC, "ia": {"status": "auto", "value": "240", "unit": "g", "text": "240 g", "fuente": "claude"},
            "offers": [
                {"store": "paiz", "foto_de": "paiz", "ia": {"status": "leido", "value": "240", "unit": "g",
                                                            "text": "Peso neto 240 g"}},
                {"store": "walmart", "foto_de": "paiz", "ia": {"status": "leido", "value": "240", "unit": "g"}},
                {"store": "la_colonia", "foto_de": "la_colonia", "ia": {"status": "leido", "value": "230", "unit": "g"}},
                {"store": "colonial", "foto_de": "colonial", "ia": {"status": "no_legible"}},
                {"store": "comisariato_los_andes", "foto_de": "comisariato_los_andes",
                 "ia": {"status": "leido", "value": "999999", "unit": "g"}}]}
    decision = ia.auto_decision(item)
    assert decision["evidence"] == [
        {"store": "la_colonia", "value": "230", "unit": "g"},
        {"store": "paiz", "value": "240", "unit": "g", "text": "Peso neto 240 g"}]
    parsed = parse_payload(payload(decision), decided_by="claude_auto", decided_at_utc=NOW, engine=True)[0]
    assert {e.source for e in parsed.evidence} == {"claude_auto"}


# ------------------------------------------------------------- catálogo público


def visible(supermarket: str, *, status: str = "ready", published_as: str | None = None,
            other: bool = False, canonical: str | None = "prod_gtin_00787003016091"):
    return core.VisibleOffer(
        source_product_id=f"{supermarket}:1", supermarket_id=supermarket,
        location_id=dict(core.EXPECTED_SCOPE)[supermarket], product_name="Queso Lactolac 16 rebanadas",
        brand="Lactolac", presentation="240 g", current_price_minor=9_000, reported_regular_price_minor=None,
        is_promotion=False, availability="in_stock", observed_at="2026-11-14T08:00:00Z",
        canonical_product_id=canonical, category="Lácteos", product_type="Queso",
        presentation_dimension="mass_g", presentation_total_base="240", presentation_status="confirmed",
        comparison_status=status, published_as=published_as, other_presentation=other,
    )


@pytest.mark.parametrize("facade_name", ["exportar_consumer_catalog", "exportar_consumer_catalog_core"])
def test_catalog_offers_carry_published_as_and_other_presentation(facade_name: str) -> None:
    facade = __import__(facade_name)
    fresh = {scope: "FRESH" for scope in core.EXPECTED_SCOPE}
    as_of = core.datetime(2026, 11, 14, 12, tzinfo=core.UTC)
    rows = facade.build_rows([
        visible("la_colonia", published_as="230 g"), visible("walmart"),
        visible("colonial", status="review_required", other=True, canonical=None),
    ], fresh, {}, as_of_utc=as_of)
    offers = {offer["supermarket_id"]: offer for row in rows for offer in row["offers"]}
    assert offers["la_colonia"]["published_as"] == "230 g" and "other_presentation" not in offers["la_colonia"]
    assert "published_as" not in offers["walmart"] and "other_presentation" not in offers["walmart"]
    assert offers["colonial"]["other_presentation"] is True


def test_published_size_labels() -> None:
    assert core.published_size("ready", "mass_g", "240", "148", "g", 1, "148") == "148 g"
    assert core.published_size("ready", "mass_g", "240", "8.5", "oz", 1, "8.5") == "8.5 oz"
    assert core.published_size("ready", "mass_g", "240", "16", "unit", 1, "16") == "16 unidades"
    assert core.published_size("ready", "mass_g", "50", "10", "g", 5, "50") is None  # mismo total
    assert core.published_size("ready", "mass_g", "240", "60", "g", 2, "120") == "2 × 60 g"
    assert core.published_size("review_required", "mass_g", "240", "148", "g", 1, "148") is None
    assert core.published_size("ready", "mass_g", "240", None, None, None, None) is None


# ----------------------------------------------------------------- medición


def test_measurement_lists_the_cards_that_change_by_gtin() -> None:
    decisions = {
        **decide(EL_MIGO, [("colonial", "240"), ("la_colonia", "240"), ("walmart", "240")]),
        **decide(LACTOLAC),
    }
    profiles = build_profiles([record for _, record in records_from_product_rows(ROWS)])
    product_of = {record.source_record_id: product_id for product_id, record in records_from_product_rows(ROWS)}
    summary, lines = evidence_report(profiles, decisions, product_of)
    assert summary["fichas_que_cambian"] == 1 and summary["modo_mayoria"] == 1
    assert summary["ofertas_corregidas"] == 3 and summary["fichas_que_cambiarian_con_apply_all"] == 2
    cards = {card["gtin"]: card for card in summary["fichas"]}
    assert cards[EL_MIGO]["cambia"] is True and cards[EL_MIGO]["fotos_confirman"] == ["colonial", "la_colonia",
                                                                                     "walmart"]
    assert cards[LACTOLAC]["cambia"] is False  # sin evidencia: aparece por sus ofertas aisladas
    walmart = next(line for line in lines if line["gtin"] == EL_MIGO and line["supermercado"] == "walmart")
    assert (walmart["publicado"], walmart["rol_sin_evidencia"], walmart["rol_con_evidencia"], walmart["foto"]) == (
        "148 g", "aislada_por_tamano", "corregida", "240 g")
    lactolac = {line["supermercado"]: line for line in lines if line["gtin"] == LACTOLAC}
    assert lactolac["la_colonia"]["rol_con_apply_all"] == "corregida"


def test_offer_evidence_dataclass_rejects_bad_values() -> None:
    with pytest.raises(SizeDecisionError):
        OfferEvidence("paiz", Decimal("1"), "kg", "human")
    with pytest.raises(SizeDecisionError):
        replace(decide(LACTOLAC)[LACTOLAC], decision="different_products", net_content_value=None,
                net_content_unit=None, apply="all")
