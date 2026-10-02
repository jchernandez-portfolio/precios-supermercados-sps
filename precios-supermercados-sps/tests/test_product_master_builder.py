"""Producto maestro: supervivencia golden, ids, estado deseado y política."""
from __future__ import annotations

import random
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

import _product_master_fixtures as fx
from precios_supermercados import product_master as pm
from precios_supermercados.identifiers import generate_gtin_product_id

POLICY_PATH = Path(__file__).resolve().parents[1] / "config/homologation/identity-policy-v1.yaml"


def member(product_id: int, supermarket: str, name: str, **overrides) -> pm.MemberProfile:  # type: ignore[no-untyped-def]
    values = dict(
        product_id=product_id,
        supermarket_id=supermarket,
        source_name=name,
        canonical_gtin="07441029556773",
        comparison_status="ready",
        normalized_brand="bimbo",
        brand_resolution_source="source",
        product_type="Pan",
        taxonomy_rule_id="pan_name",
        category="Alimentos",
        presentation_dimension="mass_g",
        presentation_total_base="720",
        presentation_pack_count=1,
        presentation_status="confirmed",
    )
    values.update(overrides)
    return pm.MemberProfile(**values)  # type: ignore[arg-type]


MASTER = pm.gtin_master_id("7441029556773")


def test_master_ids_are_deterministic_and_opaque() -> None:
    assert pm.gtin_master_id("7441029556773") == pm.gtin_master_id("07441029556773") == MASTER
    assert pm.is_master_id(MASTER) and MASTER.startswith("mp_") and "7441029556773" not in MASTER
    verified = pm.seeded_master_id("verified", "prod_verified_abc")
    assert pm.is_master_id(verified) and verified != MASTER
    assert pm.public_canonical_id(MASTER, "07441029556773") == generate_gtin_product_id("7441029556773")
    assert pm.public_canonical_id(verified, None) == verified
    with pytest.raises(pm.ProductMasterError):
        pm.gtin_master_id("123")


def test_source_reported_brand_beats_name_derived_majority() -> None:
    members = [
        member(1, "walmart", "Pan Bimbo Blanco", normalized_brand="bimbo", brand_resolution_source="source"),
        member(2, "la_colonia", "Pan Blanco Marinela", normalized_brand="marinela", brand_resolution_source="name_known_brand"),
        member(3, "paiz", "Pan Blanco Marinela", normalized_brand="marinela", brand_resolution_source="name_known_brand"),
    ]
    record = pm.build_golden_record(MASTER, primary_gtin="07441029556773", origin_method="gtin", members=members)
    assert record.brand == "bimbo"
    assert record.attribute_provenance["brand"]["rule"] == "source_reported_brand_majority"
    assert record.attribute_provenance["brand"]["supermarkets"] == ["walmart"]


def test_metric_beats_imperial_and_majority_wins_within_metric() -> None:
    members = [
        member(1, "walmart", "Arroz Progreso 5 lb", presentation_total_base="2267.96185", presentation_status="confirmed"),
        member(2, "la_colonia", "Arroz Progreso 2.27 kg", presentation_total_base="2270"),
        member(3, "paiz", "Arroz Progreso 80 oz", presentation_dimension="ounce", presentation_total_base="80"),
    ]
    record = pm.build_golden_record(MASTER, primary_gtin="07441029556773", origin_method="gtin", members=members)
    assert (record.net_content_value, record.net_content_unit, record.pack_count) == ("2270", "g", 1)
    assert record.attribute_provenance["net_content"]["rule"] == "metric_over_imperial_then_majority"
    assert record.attribute_provenance["net_content"]["alternatives"] == 2
    only_ounce = pm.build_golden_record(MASTER, primary_gtin=None, origin_method="gtin", members=[members[2]])
    assert (only_ounce.net_content_value, only_ounce.net_content_unit) == ("80", "oz")
    conflict = member(4, "walmart", "x", presentation_status="conflict")
    empty = pm.build_golden_record(MASTER, primary_gtin=None, origin_method="gtin", members=[conflict])
    assert empty.net_content_value is None and empty.net_content_unit is None


def test_display_name_prefers_shared_name_then_explicit_barcode_retailer() -> None:
    members = [
        member(1, "colonial", "BIMBO Pan Blanco Grande Familiar Rebanado 720g"),
        member(2, "walmart", "Pan Bimbo Blanco - 720 g"),
        member(3, "la_colonia", "Pan Blanco Bimbo Grande 720 g"),
    ]
    record = pm.build_golden_record(MASTER, primary_gtin="07441029556773", origin_method="gtin", members=members)
    assert record.display_name == "Pan Blanco Bimbo Grande 720 g"
    shared = [
        member(1, "walmart", "Café Maya Original - 400 g"),
        member(2, "paiz", "Café Maya Original - 400 g"),
        member(3, "la_colonia", "Café Maya Molido Original Tostado 400 g"),
    ]
    assert (
        pm.build_golden_record(MASTER, primary_gtin=None, origin_method="gtin", members=shared).display_name
        == "Café Maya Original - 400 g"
    )


def test_type_variant_and_category_survivorship() -> None:
    members = [
        member(1, "walmart", "Jugo Sula Naranja 1 L", product_type="Jugo", taxonomy_rule_id="jugo"),
        member(2, "la_colonia", "Jugo Sula Naranja 1 L", product_type="Bebida", taxonomy_rule_id="source_category_keyword"),
        member(3, "colonial", "SULA Jugo 1L", product_type=None, category=None),
    ]
    record = pm.build_golden_record(MASTER, primary_gtin=None, origin_method="gtin", members=members)
    assert record.product_type == "Jugo"
    assert record.attribute_provenance["product_type"]["rule"] == "name_rule_majority"
    assert record.variant == "naranja"
    assert record.attribute_provenance["variant"]["support"] == 2
    assert record.category == "Alimentos"
    assert pm.golden_variant_labels("Leche Sula Descremada Sin Azúcar") >= {"descremada", "sin azucar"}
    assert pm.golden_variant_labels("Café Maya Original") == frozenset()


def test_human_attribute_is_never_overwritten() -> None:
    members = [member(1, "walmart", "Pan Bimbo Blanco - 720 g"), member(2, "paiz", "Pan Bimbo Blanco - 720 g")]
    human = {
        "brand": {"value": "Bimbo Honduras", "decided_by": "human:owner", "decided_at_utc": "2026-10-01T00:00:00Z"},
        "manufacturer": {"value": "Grupo Bimbo", "decided_by": "human:owner", "decided_at_utc": "2026-10-01T00:00:00Z"},
    }
    record = pm.build_golden_record(MASTER, primary_gtin=None, origin_method="gtin", members=members, human_attributes=human)
    assert record.brand == "Bimbo Honduras" and record.manufacturer == "Grupo Bimbo"
    assert record.attribute_provenance["brand"] == {
        "rule": "human_set",
        "decided_by": "human:owner",
        "decided_at_utc": "2026-10-01T00:00:00Z",
    }
    with pytest.raises(pm.ProductMasterError):
        pm.build_golden_record(MASTER, primary_gtin=None, origin_method="gtin", members=members, human_attributes={"color": {"value": "x"}})


def test_golden_record_is_deterministic_regardless_of_member_order() -> None:
    members = [
        member(1, "walmart", "Pan Bimbo Blanco - 720 g"),
        member(2, "paiz", "Pan Bimbo Blanco - 720 g", normalized_brand=None, brand_resolution_source="missing"),
        member(3, "la_colonia", "Pan Blanco Bimbo 720 g", presentation_total_base="720.0"),
    ]
    expected = pm.build_golden_record(MASTER, primary_gtin="07441029556773", origin_method="gtin", members=members)
    for seed in range(5):
        shuffled = list(members)
        random.Random(seed).shuffle(shuffled)
        again = pm.build_golden_record(MASTER, primary_gtin="07441029556773", origin_method="gtin", members=shuffled)
        assert again == expected and again.record_hash == expected.record_hash


def _fixture_state(tmp_path: Path, policy: pm.MasterPolicy | None = None):  # type: ignore[no-untyped-def]
    records, rows = fx.build_database(tmp_path / "db.sqlite")
    names = {product_id: record.source_name for product_id, record in records}
    members = pm.member_profiles(rows, names)
    return rows, members, pm.build_desired_state(members, policy=policy or pm.load_master_policy(POLICY_PATH))


def test_gtin_links_materialize_exactly_the_persisted_identity(tmp_path: Path) -> None:
    rows, _, state = _fixture_state(tmp_path)
    by_id = {row.product_id: row for row in rows}
    for row in rows:
        link = state.links.get(row.product_id)
        if row.comparison_status in {"ready", "single_source"} and row.product_id not in {17, 18}:
            assert link is not None, row.product_id
            assert state.masters[link.master_product_id].public_id == row.canonical_product_id
        else:
            assert link is None, row.product_id
    # Mismo maestro ⇔ mismo canonical_product_id ready (la identidad vigente).
    ready_groups: dict[str, set[int]] = {}
    for row in rows:
        if row.comparison_status == "ready":
            ready_groups.setdefault(str(row.canonical_product_id), set()).add(row.product_id)
    for members in ready_groups.values():
        assert len({state.links[pid].master_product_id for pid in members}) == 1
    # Colonial/Comisariato: GTIN derivado de SKU.
    methods = {pid: link.link_method for pid, link in state.links.items()}
    assert methods[3] == "gtin_sku_derived" and methods[21] == "gtin_sku_derived" and methods[2] == "gtin_exact"
    # Dos single_source del mismo supermercado y GTIN: ninguno se vincula.
    assert by_id[17].comparison_status == by_id[18].comparison_status == "single_source"
    assert state.diagnostics["single_source_same_retailer_collision"] == 2
    # Grupo en revisión (conflicto de presentación): sin maestro.
    assert by_id[19].comparison_status == "review_required" and 19 not in state.links
    assert state.diagnostics["masters"] == len(state.masters) == 8


def test_curated_link_has_priority_and_respects_one_link_per_retailer(tmp_path: Path) -> None:
    _, members, state = _fixture_state(tmp_path)
    dorao = state.links[7].master_product_id
    manual = pm.MasterLink(8, "pricesmart", dorao, "manual_review", "human:owner", {"note": "misma bolsa"})
    curated = pm.build_desired_state(
        members, policy=pm.load_master_policy(POLICY_PATH), curated_links=[manual]
    )
    assert curated.links[8].link_method == "manual_review"
    assert {link.supermarket_id for link in curated.links.values() if link.master_product_id == dorao} == {
        "walmart",
        "pricesmart",
    }
    # Un curado del mismo supermercado que un miembro GTIN: gana el curado.
    pepsi = state.links[4].master_product_id
    clash = pm.MasterLink(15, "walmart", pepsi, "manual_review", "human:owner")
    resolved = pm.build_desired_state(members, policy=pm.load_master_policy(POLICY_PATH), curated_links=[clash])
    assert resolved.links[15].master_product_id == pepsi and 4 not in resolved.links
    assert resolved.diagnostics["retailer_slot_conflict_dropped"] == 1
    # Un maestro curado desconocido falla cerrado.
    unknown = pm.MasterLink(8, "pricesmart", pm.seeded_master_id("manual", "x"), "manual_review", "human:owner")
    with pytest.raises(pm.ProductMasterError, match="target_unknown"):
        pm.build_desired_state(members, policy=pm.load_master_policy(POLICY_PATH), curated_links=[unknown])


def test_engine_auto_links_are_disabled_by_policy(tmp_path: Path) -> None:
    _, members, state = _fixture_state(tmp_path)
    policy = pm.load_master_policy(POLICY_PATH)
    assert not policy.method_active("engine_auto")
    assert "engine_auto" not in policy.serving_methods
    engine = pm.MasterLink(8, "pricesmart", state.links[7].master_product_id, "engine_auto", "engine:matching-engine-v1", confidence=0.99)
    result = pm.build_desired_state(members, policy=policy, curated_links=[engine])
    assert 8 not in result.links
    assert result.diagnostics["curated_link_inactive_engine_auto"] == 1


def test_policy_yaml_section_is_closed_and_gtin_only_reproduces_today() -> None:
    raw = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    policy = pm.MasterPolicy.from_mapping(raw)
    assert policy.enabled and policy.gtin_links_enabled
    assert raw["product_master"]["link_methods"]["engine_auto"] == "disabled"
    assert policy.sku_derived_supermarkets == frozenset(raw["restricted_gtin"]["sku_derived_gtin_supermarkets"])
    without = dict(raw)
    without.pop("product_master")
    fallback = pm.MasterPolicy.from_mapping(without)
    assert fallback.active_methods == pm.GTIN_LINK_METHODS and not fallback.serving_methods
    broken = {**raw, "product_master": {**raw["product_master"], "serving_link_methods": ["gtin_exact"]}}
    with pytest.raises(pm.ProductMasterError):
        pm.MasterPolicy.from_mapping(broken)
    enabling_engine = {
        **raw,
        "product_master": {**raw["product_master"], "serving_link_methods": ["engine_auto"]},
    }
    with pytest.raises(pm.ProductMasterError, match="serving_method_inactive"):
        pm.MasterPolicy.from_mapping(enabling_engine)
    with pytest.raises(pm.ProductMasterError):
        pm.MasterPolicy.from_mapping({**raw, "product_master": {**raw["product_master"], "different_valid_gtin": "auto"}})


def test_link_contract_rejects_inconsistent_authors() -> None:
    with pytest.raises(pm.ProductMasterError):
        pm.MasterLink(1, "walmart", MASTER, "gtin_exact", "human:owner")
    with pytest.raises(pm.ProductMasterError):
        pm.MasterLink(1, "walmart", MASTER, "manual_review", "system")
    with pytest.raises(pm.ProductMasterError):
        pm.MasterLink(1, "walmart", MASTER, "engine_auto", "human:owner")
    with pytest.raises(pm.ProductMasterError):
        pm.MasterLink(1, "walmart", "prod_gtin_07441029556773", "manual_review", "human:owner")
    link = pm.MasterLink(1, "walmart", MASTER, "gtin_exact", "system", {"canonical_gtin": "07441029556773"})
    assert link.link_hash == pm.MasterLink(1, "walmart", MASTER, "gtin_exact", "system", {"canonical_gtin": "07441029556773"}, decided_at_utc="x").link_hash


def test_reviewed_registry_decisions_become_links_only_when_fresh(tmp_path: Path) -> None:
    from precios_supermercados.product_identity_decisions import (
        ReviewedIdentityDecision,
        candidate_id,
        profile_evidence_fingerprint,
    )
    from precios_supermercados.product_identity_v2 import build_brand_lexicon, profile_product_v2

    db = tmp_path / "db.sqlite"
    records, _ = fx.build_database(db)
    lexicon = build_brand_lexicon(record for _, record in records)
    by_id = dict(records)
    left, right = sorted((by_id[7], by_id[8]), key=lambda record: record.source_record_id)
    left_print = profile_evidence_fingerprint(profile_product_v2(left, brand_lexicon=lexicon))
    right_print = profile_evidence_fingerprint(profile_product_v2(right, brand_lexicon=lexicon))
    decision = ReviewedIdentityDecision(
        candidate_id=candidate_id(left.source_record_id, right.source_record_id),
        left_source_record_id=left.source_record_id,
        right_source_record_id=right.source_record_id,
        left_evidence_fingerprint=left_print,
        right_evidence_fingerprint=right_print,
        relation="VERIFIED_EQUIVALENT",
        status="approved",
        master_product_id="prod_gtin_07421000001010",
        evidence_codes=("human_verified", "barcode_visible"),
        evidence_references=("foto-1",),
        rationale="Mismo código de barras en el empaque",
        reviewed_by="owner",
        reviewed_at_utc="2026-10-01T00:00:00Z",
    )
    policy = pm.load_master_policy(POLICY_PATH)
    links, diagnostics = pm.reviewed_decision_links([decision], records, policy=policy)
    assert diagnostics == {"reviewed_decision_applied": 1}
    assert {link.product_id for link in links} == {7, 8}
    assert {link.master_product_id for link in links} == {pm.gtin_master_id("07421000001010")}
    # Si cambia la evidencia fuente de un lado, la decisión queda obsoleta.
    changed = [
        (pid, replace(record, source_name="Café Dorao Clásico 454 g") if pid == 8 else record)
        for pid, record in records
    ]
    links, diagnostics = pm.reviewed_decision_links([decision], changed, policy=policy)
    assert links == () and diagnostics == {"reviewed_decision_stale_decision": 1}
    assert pm.reviewed_decision_links([], records, policy=policy) == ((), {})


def test_profile_digest_changes_with_any_profile_hash() -> None:
    state = {1: ("a" * 64, "v"), 2: ("b" * 64, "v")}
    assert pm.profile_state_digest(state) == pm.profile_state_digest(dict(reversed(list(state.items()))))
    assert pm.profile_state_digest(state) != pm.profile_state_digest({1: ("a" * 64, "v"), 2: ("c" * 64, "v")})


def test_fixture_database_is_consistent(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    fx.build_database(db)
    con = sqlite3.connect(db)
    try:
        assert con.execute("SELECT COUNT(*) FROM products").fetchone()[0] == len(fx.PRODUCTS)
        assert con.execute("SELECT COUNT(*) FROM product_homologation_profiles").fetchone()[0] == len(fx.PRODUCTS)
    finally:
        con.close()
