"""Candidatos producto → maestro (shadow): reglas de revisión y exportación de la cola."""
from __future__ import annotations

import csv
import json
import sqlite3
import sys
from pathlib import Path

import pytest

import _product_master_fixtures as fx
from precios_supermercados import product_master as pm
from precios_supermercados.matching.config import DEFAULT_CONFIG_PATH, DEFAULT_TAXONOMY_PATH, load_engine_config
from precios_supermercados.matching.master_candidates import (
    CSV_COLUMNS,
    MasterCandidateSettings,
    generate_master_candidates,
    review_id,
    standardize_records,
)
from precios_supermercados.matching.records import MatchRecord
from precios_supermercados.matching.taxonomy import load_source_taxonomy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import exportar_cola_maestro as queue_cli  # noqa: E402
import importar_decisiones_maestro as importer  # noqa: E402

POLICY = pm.load_master_policy(ROOT / "config/homologation/identity-policy-v1.yaml")
CONFIG = load_engine_config(DEFAULT_CONFIG_PATH)
TAXONOMY = load_source_taxonomy(DEFAULT_TAXONOMY_PATH)


def _state(tmp_path: Path):  # type: ignore[no-untyped-def]
    records, rows = fx.build_database(tmp_path / "db.sqlite")
    names = {pid: record.source_name for pid, record in records}
    desired = pm.build_desired_state(pm.member_profiles(rows, names), policy=POLICY)
    match_records = [
        MatchRecord(
            source_record_id=record.source_record_id,
            supermarket_id=record.supermarket_id,
            city="SPS",
            source_name=record.source_name,
            source_brand=record.source_brand,
            source_presentation=record.source_presentation,
            source_category=record.source_category,
            barcode=record.barcode,
            current_price=str(fx.PRODUCTS[pid - 1][6] / 100),
        )
        for pid, record in records
    ]
    master_of = {link.key: link.master_product_id for link in desired.links.values()}
    return match_records, desired, master_of


def _run(records, desired, master_of, *, rejections=(), extra=()):  # type: ignore[no-untyped-def]
    all_records = [*records, *extra]
    standardized = standardize_records(all_records, config=CONFIG, taxonomy=TAXONOMY)
    return generate_master_candidates(
        standardized,
        city="SPS",
        masters=desired.masters,
        product_key_of={record.source_record_id: record.source_record_id for record in all_records},
        master_of_product=master_of,
        rejections=rejections,
    )


def test_unlinked_products_get_scored_master_candidates(tmp_path: Path) -> None:
    records, desired, master_of = _state(tmp_path)
    rows, summary = _run(records, desired, master_of)
    by_pair = {(row["product_key"], row["master_product_id"]): row for row in rows}
    dorao = pm.gtin_master_id(fx.DORAO)
    sula = pm.gtin_master_id(fx.SULA)
    pricesmart = by_pair[("pricesmart:8", dorao)]
    assert pricesmart["band"] == "high" and pricesmart["rank"] == 1
    assert pricesmart["field_levels"]["brand"] == "exact" and pricesmart["field_levels"]["size"] == "exact"
    assert pricesmart["decision_policy"] == "review_only"
    assert pricesmart["review_id"] == review_id("pricesmart:8", dorao)
    assert set(pricesmart["field_scores"]) == {"brand", "name", "size", "pack", "variant", "type"}
    assert pricesmart["product_link_state"] == "unlinked" and pricesmart["master_supermarkets"] == ["walmart"]
    comisariato = by_pair[("comisariato_los_andes:11", sula)]
    assert comisariato["master_supermarkets"] == ["la_colonia", "walmart"]
    # Productos de maestros multi-cadena nunca son candidatos.
    multi = {key for key, master in master_of.items() if sum(1 for value in master_of.values() if value == master) > 1}
    assert not {row["product_key"] for row in rows} & multi
    assert summary["candidates_from_unlinked"] >= 5


def test_different_valid_gtin_is_review_only_and_single_member_pairs_appear_once(tmp_path: Path) -> None:
    records, desired, master_of = _state(tmp_path)
    rows, summary = _run(records, desired, master_of)
    walmart_ducal = pm.gtin_master_id(fx.DUCAL)
    paiz_ducal = pm.gtin_master_id(fx.gtin13("742100000999"))
    ducal = [row for row in rows if {row["master_product_id"], row["product_master_product_id"]} == {walmart_ducal, paiz_ducal}]
    assert len(ducal) == 1
    row = ducal[0]
    assert row["product_link_state"] == "single_member_master"
    assert "different_valid_gtin" in row["flags"]
    assert row["exact_attribute_match"] is True  # mismos atributos…
    assert row["decision_policy"] == "review_only"  # …y aun así sólo revisión
    assert summary["symmetric_single_member_pair_deduplicated"] >= 1
    assert summary["different_valid_gtin_candidates"] >= 1


def test_rejections_slots_and_hard_conflicts_are_never_proposed(tmp_path: Path) -> None:
    records, desired, master_of = _state(tmp_path)
    dorao = pm.gtin_master_id(fx.DORAO)
    pepsi = pm.gtin_master_id(fx.PEPSI)
    extra = [
        MatchRecord(source_record_id="walmart:99", supermarket_id="walmart", city="SPS",
                    source_name="Refresco Pepsi Botella Retornable - 2 L", source_brand="Pepsi", source_presentation="2 L"),
        MatchRecord(source_record_id="pricesmart:98", supermarket_id="pricesmart", city="SPS",
                    source_name="Café Dorao Molido Tradicional 1 kg", source_brand="Dorao"),
    ]
    rows, summary = _run(records, desired, master_of, rejections={("pricesmart:8", dorao)}, extra=extra)
    pairs = {(row["product_key"], row["master_product_id"]) for row in rows}
    assert ("pricesmart:8", dorao) not in pairs
    assert summary["rejected_pairs_skipped"] == 1
    assert ("walmart:99", pepsi) not in pairs and summary["retailer_slot_taken_skipped"] >= 1
    assert ("pricesmart:98", dorao) not in pairs  # 1 kg vs 454 g: conflicto duro


def test_name_floor_blocks_brand_type_size_only_candidates() -> None:
    settings = MasterCandidateSettings()
    assert settings.band(0.9) == "high" and settings.band(0.75) == "medium" and settings.band(0.6) == "low"
    assert settings.band(0.5) is None
    assert settings.min_name_score >= CONFIG.section("comparison")["name_levels"][-1]


def _write_records(path: Path, records: list[MatchRecord]) -> None:
    path.write_text("".join(json.dumps(record.to_json(), ensure_ascii=False) + "\n" for record in records), encoding="utf-8")


def test_offline_queue_export_writes_reviewable_csv_and_summary(tmp_path: Path) -> None:
    records, _, _ = _state(tmp_path)
    tgu = [
        MatchRecord(
            source_record_id=record.source_record_id,
            supermarket_id=record.supermarket_id,
            city="TGU",
            source_name=record.source_name,
            source_brand=record.source_brand,
            source_presentation=record.source_presentation,
            source_category=record.source_category,
            barcode=record.barcode,
        )
        for record in records
        if record.supermarket_id in {"walmart", "paiz", "la_colonia", "pricesmart"}
    ]
    jsonl = tmp_path / "records.jsonl"
    _write_records(jsonl, [*records, *tgu])
    out = tmp_path / "out"
    summary = queue_cli.run_offline(jsonl, out, log=lambda _message: None)
    assert summary["masters"] == 8 and set(summary["cities"]) == {"SPS", "TGU"}
    assert summary["completeness"]["SPS"]["golden"]["brand_pct"] == 100.0
    assert summary["cities"]["SPS"]["unlinked_products_with_candidate"] >= 3
    with (out / "review-queue.csv").open(encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert tuple(reader.fieldnames or ()) == CSV_COLUMNS
        rows = list(reader)
    assert rows and all(row["decision"] == "" for row in rows)
    queue = importer.load_queue(out / "review-queue.jsonl")
    assert set(queue) == {row["review_id"] for row in rows}
    assert importer.load_queue(out / "review-queue.csv") == queue
    masters = [json.loads(line) for line in (out / "masters.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(masters) == 8 and all(line["record_hash"] for line in masters)


def test_persisted_queue_export_from_sqlite_honours_rejections(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    fx.build_database(db)
    con = sqlite3.connect(db, isolation_level=None)
    try:
        records, rows = fx.derive_profiles(con)
        names = {pid: record.source_name for pid, record in records}
        state = {row.product_id: (row.profile_hash, row.normalization_version) for row in rows}
        store = pm.SQLiteMasterStore(con)
        pm.sync_product_master(
            store, pm.member_profiles(rows, names), policy=POLICY, normalization_version=rows[0].normalization_version,
            now="2026-09-20T12:00:00Z", prior_profile_digest=pm.profile_state_digest(state),
            new_profile_digest=pm.profile_state_digest(state), changed_product_ids=None,
        )
        dorao = pm.gtin_master_id(fx.DORAO)
        importer.import_decisions(
            store, f"product_key,master_product_id,decision,reviewer\npricesmart:8,{dorao},Distinto,owner\n", apply=True
        )
        summary = queue_cli.run_persisted(store, tmp_path / "out", source="sqlite", with_cities=True)
    finally:
        con.close()
    assert summary["masters"] == 8 and summary["rejections"] == 1
    queued = [json.loads(line) for line in (tmp_path / "out/review-queue.jsonl").read_text(encoding="utf-8").splitlines()[1:]]
    assert not any(row["product_key"] == "pricesmart:8" and row["master_product_id"] == dorao for row in queued)
    assert set(summary["cities"]) <= {"SPS", "TGU", "Tegucigalpa Multiplaza", "Tegucigalpa Proceres"}


@pytest.mark.parametrize("decision", ["Mismo", "mismo", "SÍ", "same"])
def test_decision_vocabulary(decision: str) -> None:
    parsed, errors = importer.parse_decisions(
        f"product_key,master_product_id,decision,reviewer\npricesmart:8,{'mp_' + 'a' * 20},{decision},owner\n"
    )
    assert errors == [] and parsed[0].same is True
