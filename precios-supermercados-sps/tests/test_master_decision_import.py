"""Importación de decisiones humanas (CSV) contra productos maestros: vínculos
manuales, rechazos recordados y validaciones fail-closed."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

import _product_master_fixtures as fx
from precios_supermercados import product_master as pm
from precios_supermercados.product_homologation_persistence import NORMALIZATION_VERSION

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import importar_decisiones_maestro as importer  # noqa: E402

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


def _sync(store, members, rows, *, prior, changed, now=NOW):  # type: ignore[no-untyped-def]
    return pm.sync_product_master(
        store,
        members,
        policy=POLICY,
        normalization_version=NORMALIZATION_VERSION,
        now=now,
        prior_profile_digest=pm.profile_state_digest(prior),
        new_profile_digest=pm.profile_state_digest(_profile_state(rows)),
        changed_product_ids=changed,
    )


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    path = tmp_path / "mvp.sqlite"
    fx.build_database(path)
    return path


def _synced(db: Path) -> tuple[sqlite3.Connection, pm.SQLiteMasterStore]:
    con = _open(db)
    store = pm.SQLiteMasterStore(con)
    _, rows, members = _members(con)
    _sync(store, members, rows, prior={}, changed=None)
    return con, store


def test_import_same_creates_manual_link_and_marks_master_dirty(db: Path) -> None:
    con, store = _synced(db)
    dorao = pm.gtin_master_id(fx.DORAO)
    csv_text = f"product_key,master_product_id,decision,reviewer,note\npricesmart:8,{dorao},Mismo,owner,foto empaque\n"
    dry = importer.import_decisions(store, csv_text, now=NOW)
    assert dry["dry_run"] is True and dry["written"] is False and dry["counts"]["same_linked"] == 1
    assert con.execute(f"SELECT COUNT(*) FROM {pm.LINK_TABLE} WHERE product_id=8").fetchone()[0] == 0
    applied = importer.import_decisions(store, csv_text, apply=True, now=NOW)
    assert applied["written"] is True and applied["errors"] == []
    link = con.execute(
        f"SELECT master_product_id,link_method,decided_by,status FROM {pm.LINK_TABLE} WHERE product_id=8"
    ).fetchone()
    assert link == (dorao, "manual_review", "human:owner", "active")
    assert con.execute(f"SELECT master_product_id FROM {pm.DIRTY_TABLE}").fetchall() == [(dorao,)]
    # El refresco siguiente recalcula el maestro (incremental) y limpia dirty.
    _, rows, members = _members(con)
    result = _sync(store, members, rows, prior=_profile_state(rows), changed=[], now="2026-09-21T00:00:00Z")
    assert result["mode"] == "incremental" and result["curated_links"] == 1
    assert con.execute(f"SELECT COUNT(*) FROM {pm.DIRTY_TABLE}").fetchone()[0] == 0
    assert con.execute(f"SELECT COUNT(*) FROM {pm.LINK_TABLE} WHERE product_id=8 AND status='active'").fetchone()[0] == 1
    again = importer.import_decisions(store, csv_text, apply=True, now=NOW)
    assert again["counts"]["same_already_linked"] == 1 and again["written"] is False
    con.close()


def test_import_different_is_remembered_and_never_reproposed(db: Path) -> None:
    from precios_supermercados.matching.master_candidates import generate_master_candidates
    from precios_supermercados.matching.records import MatchRecord
    from precios_supermercados.matching.master_candidates import standardize_records
    from precios_supermercados.matching.config import DEFAULT_CONFIG_PATH, DEFAULT_TAXONOMY_PATH, load_engine_config
    from precios_supermercados.matching.taxonomy import load_source_taxonomy

    con, store = _synced(db)
    dorao = pm.gtin_master_id(fx.DORAO)
    config = load_engine_config(DEFAULT_CONFIG_PATH)
    taxonomy = load_source_taxonomy(DEFAULT_TAXONOMY_PATH)
    records = [
        MatchRecord(source_record_id=f"{s}:{pid}", supermarket_id=s, city="SPS", source_name=n, source_brand=b,
                    source_presentation=pr, source_category=c, barcode=e)
        for pid, s, n, b, pr, c, e in con.execute(
            "SELECT product_id,supermarket_id,name,brand,presentation,category,ean FROM products"
        )
    ]
    links = pm.read_all_active_links(store)
    masters_rows = con.execute(f"SELECT master_product_id FROM {pm.MASTER_TABLE}").fetchall()
    _, rows, members = _members(con)
    golden = pm.build_desired_state(members, policy=POLICY).masters
    assert {row[0] for row in masters_rows} == set(golden)

    def queue(rejections):  # type: ignore[no-untyped-def]
        standardized = standardize_records(records, config=config, taxonomy=taxonomy)
        rows_out, _ = generate_master_candidates(
            standardized, city="SPS", masters=golden,
            product_key_of={record.source_record_id: record.source_record_id for record in records},
            master_of_product={link.key: link.master_product_id for link in links.values()},
            rejections=rejections,
        )
        return {(row["product_key"], row["master_product_id"]) for row in rows_out}

    assert ("pricesmart:8", dorao) in queue(set())
    csv_text = f"product_key,master_product_id,decision,reviewer\npricesmart:8,{dorao},Distinto,owner\n"
    result = importer.import_decisions(store, csv_text, apply=True, now=NOW)
    assert result["counts"]["different_rejected"] == 1 and result["written"] is True
    stored = con.execute(
        f"SELECT product_id,master_product_id,decided_by FROM {pm.REJECTION_TABLE}"
    ).fetchall()
    assert stored == [(8, dorao, "human:owner")]
    rejections = {(f"{s}:{p}", m) for p, s, m in con.execute(f"SELECT product_id,supermarket_id,master_product_id FROM {pm.REJECTION_TABLE}")}
    assert ("pricesmart:8", dorao) not in queue(rejections)
    # Cambiar de opinión: "Mismo" limpia el rechazo y crea el vínculo.
    flip = importer.import_decisions(
        store, f"product_key,master_product_id,decision,reviewer\npricesmart:8,{dorao},Mismo,owner\n", apply=True, now=NOW
    )
    assert flip["counts"]["rejections_cleared"] == 1
    assert con.execute(f"SELECT COUNT(*) FROM {pm.REJECTION_TABLE}").fetchone()[0] == 0
    con.close()


def test_import_validates_and_refuses_unsafe_decisions(db: Path) -> None:
    con, store = _synced(db)
    pepsi = pm.gtin_master_id(fx.PEPSI)
    bimbo = pm.gtin_master_id(fx.BIMBO)
    sula = pm.gtin_master_id(fx.SULA)
    csv_text = "\n".join(
        [
            "product_key,master_product_id,decision,reviewer",
            f"walmart:15,{pepsi},Mismo,owner",  # walmart ya ocupa el maestro Pepsi
            f"walmart:2,{pepsi},Mismo,owner",  # vínculo GTIN de un grupo multi-cadena
            f"walmart:2,{bimbo},Distinto,owner",  # rechazar un GTIN exige revisión de identidad
            f"pricesmart:999,{pepsi},Mismo,owner",  # producto inexistente
            "pricesmart:8,mp_bad,Mismo,owner",  # id inválido
            f"pricesmart:8,{pepsi},quizás,owner",  # decisión inválida
            f"pricesmart:23,{sula},Mismo,",  # sin revisor
            f"colonial:24,{pepsi},pendiente,owner",  # ignorada
        ]
    ) + "\n"
    result = importer.import_decisions(store, csv_text, apply=True, now=NOW)
    errors = sorted(error["error"] for error in result["errors"])
    assert errors == sorted(
        [
            "decision_invalid",
            "gtin_link_requires_identity_review",
            "master_product_id_invalid",
            "product_has_system_link",
            "product_not_found",
            "retailer_slot_taken",
            "reviewer_missing",
        ]
    )
    assert result["written"] is False and result["refused"] == "errors_present"
    assert con.execute(f"SELECT COUNT(*) FROM {pm.LINK_TABLE} WHERE link_method='manual_review'").fetchone()[0] == 0
    conflict = importer.import_decisions(
        store,
        f"product_key,master_product_id,decision,reviewer\npricesmart:8,{pepsi},Mismo,a\npricesmart:8,{sula},Mismo,a\n",
        now=NOW,
    )
    assert [error["error"] for error in conflict["errors"]] == ["product_same_as_two_masters"]
    con.close()


def test_import_resolves_review_ids_and_replaces_single_member_gtin_link(db: Path, tmp_path: Path) -> None:
    con, store = _synced(db)
    walmart_ducal = pm.gtin_master_id(fx.DUCAL)
    paiz_ducal = pm.gtin_master_id(fx.gtin13("742100000999"))
    queue_path = tmp_path / "review-queue.jsonl"
    from precios_supermercados.matching.master_candidates import review_id

    rid = review_id("paiz:16", walmart_ducal)
    queue_path.write_text(
        json.dumps({"_metadata": {"schema": "x"}}) + "\n"
        + json.dumps({"review_id": rid, "product_key": "paiz:16", "master_product_id": walmart_ducal}) + "\n",
        encoding="utf-8",
    )
    result = importer.import_decisions(
        store, f"review_id,decision\n{rid},Mismo\n", queue=importer.load_queue(queue_path),
        default_reviewer="owner", apply=True, now=NOW,
    )
    assert result["errors"] == [] and result["counts"]["gtin_single_member_links_superseded"] == 1
    assert con.execute(
        f"SELECT master_product_id,link_method FROM {pm.LINK_TABLE} WHERE product_id=16 AND status='active'"
    ).fetchone() == (walmart_ducal, "manual_review")
    dirty = {row[0] for row in con.execute(f"SELECT master_product_id FROM {pm.DIRTY_TABLE}")}
    assert dirty == {walmart_ducal, paiz_ducal}
    # El refresco retira el maestro viejo (sin miembros) y no recrea el vínculo GTIN.
    _, rows, members = _members(con)
    sync = _sync(store, members, rows, prior=_profile_state(rows), changed=[], now="2026-09-21T00:00:00Z")
    assert sync["plan"]["masters_retired"] == 1
    assert con.execute(f"SELECT status FROM {pm.MASTER_TABLE} WHERE master_product_id=?", (paiz_ducal,)).fetchone() == ("retired",)
    assert con.execute(f"SELECT COUNT(*) FROM {pm.LINK_TABLE} WHERE product_id=16 AND status='active'").fetchone()[0] == 1
    unknown = importer.import_decisions(store, "review_id,decision,reviewer\nmr_x,Mismo,a\n", queue={}, now=NOW)
    assert unknown["errors"][0]["error"] == "review_id_unknown"
    con.close()
