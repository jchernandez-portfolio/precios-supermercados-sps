from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from precios_supermercados.product_homologation import SourceProductRecord
from precios_supermercados.product_identity_decisions import (
    DECISION_SCHEMA,
    IDENTITY_POLICY_VERSION,
    ProductIdentityDecisionError,
    ReviewedIdentityDecision,
    assess_product_relation,
    build_reviewed_identity_groups,
    candidate_id,
    load_reviewed_decisions,
    profile_evidence_fingerprint,
)
from precios_supermercados.product_identity_v2 import (
    IDENTITY_NORMALIZATION_VERSION,
    homologate_products_v2,
)


ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "fixtures" / "homologation" / "golden-pairs-v1.jsonl"
REGISTRY = ROOT / "config" / "homologation" / "reviewed-decisions-v1.json"


def _record(value: dict[str, object]) -> SourceProductRecord:
    return SourceProductRecord(
        source_record_id=str(value["source_record_id"]),
        supermarket_id=str(value["supermarket_id"]),
        source_name=str(value["source_name"]),
        source_brand=value.get("source_brand"),
        source_presentation=value.get("source_presentation"),
        barcode=value.get("barcode"),
    )


def _profiles(left: SourceProductRecord, right: SourceProductRecord):
    result = homologate_products_v2((left, right), candidate_threshold=0)
    by_id = {profile.record.source_record_id: profile for profile in result.profiles}
    return by_id[left.source_record_id], by_id[right.source_record_id]


def _approved_equivalence(left, right) -> ReviewedIdentityDecision:
    ordered = sorted((left, right), key=lambda profile: profile.record.source_record_id)
    return ReviewedIdentityDecision(
        candidate_id=candidate_id(
            ordered[0].record.source_record_id,
            ordered[1].record.source_record_id,
        ),
        left_source_record_id=ordered[0].record.source_record_id,
        right_source_record_id=ordered[1].record.source_record_id,
        left_evidence_fingerprint=profile_evidence_fingerprint(ordered[0]),
        right_evidence_fingerprint=profile_evidence_fingerprint(ordered[1]),
        relation="VERIFIED_EQUIVALENT",
        status="approved",
        master_product_id="prod_reviewed_nutri_yema_claras_554g",
        evidence_codes=("package_front", "package_back", "human_verified"),
        evidence_references=("artifact://nutri-yema/front", "artifact://nutri-yema/back"),
        rationale="El frente y reverso confirman marca, tipo y declaración dual de contenido.",
        reviewed_by="catalog-reviewer",
        reviewed_at_utc="2026-09-16T12:00:00Z",
    )


def test_golden_pairs_define_current_fail_closed_truth() -> None:
    rows = [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines()]
    assert len(rows) >= 5
    for row in rows:
        left, right = _profiles(_record(row["left"]), _record(row["right"]))
        assessment = assess_product_relation(left, right)
        assert assessment.relation == row["expected_relation"], row["case_id"]
        assert set(assessment.reasons) == set(row["expected_conflicts"] or assessment.reasons)


def test_current_empty_registry_is_valid_and_versioned() -> None:
    assert load_reviewed_decisions(REGISTRY) == ()


def test_approved_current_equivalence_is_applied_without_using_similarity_as_authority() -> None:
    left, right = _profiles(
        SourceProductRecord(
            "colonial:10593",
            "colonial",
            "Nutri Yema Claras de Huevo 554gr",
            "Nutri Yema",
        ),
        SourceProductRecord(
            "comisariato_los_andes:46232",
            "comisariato_los_andes",
            "Claras de huevo liquidas doy pack 1.2 lb",
            "Marca COMANDES",
            "UN",
        ),
    )
    decision = _approved_equivalence(left, right)
    assessment = assess_product_relation(left, right, decision)
    assert assessment.relation == "VERIFIED_EQUIVALENT"
    assert assessment.decision_state == "approved"
    assert assessment.master_product_id == "prod_reviewed_nutri_yema_claras_554g"


def test_source_change_invalidates_prior_decision() -> None:
    original_left, original_right = _profiles(
        SourceProductRecord("a:1", "a", "Nutri Yema Claras 554 g", "Nutri Yema"),
        SourceProductRecord("b:2", "b", "Nutri Yema Claras 1.2 lb", "Nutri Yema"),
    )
    decision = _approved_equivalence(original_left, original_right)
    changed_left, changed_right = _profiles(
        SourceProductRecord("a:1", "a", "Nutri Yema Claras Light 554 g", "Nutri Yema"),
        original_right.record,
    )
    assessment = assess_product_relation(changed_left, changed_right, decision)
    assert assessment.relation == "UNRESOLVED"
    assert assessment.decision_state == "stale_decision"
    assert assessment.reasons == ("source_evidence_changed",)


def test_hard_conflict_blocks_even_an_approved_equivalence() -> None:
    left, right = _profiles(
        SourceProductRecord("a:1", "a", "Kraft Ranch Classic 237 ml", "Kraft"),
        SourceProductRecord("b:2", "b", "Kraft Ranch Light 237 ml", "Kraft"),
    )
    decision = _approved_equivalence(left, right)
    assessment = assess_product_relation(left, right, decision)
    assert assessment.relation == "CONFLICT"
    assert "variant_conflict" in assessment.reasons


def test_registry_rejects_unknown_fields_and_duplicate_candidates(tmp_path: Path) -> None:
    left, right = _profiles(
        SourceProductRecord("a:1", "a", "Producto Demo 500 g", "Demo"),
        SourceProductRecord("b:2", "b", "Producto Demo 500 g", "Demo"),
    )
    decision = _approved_equivalence(left, right)
    row = asdict(decision)
    payload = {
        "schema": DECISION_SCHEMA,
        "policy_version": IDENTITY_POLICY_VERSION,
        "decisions": [row, row],
    }
    path = tmp_path / "decisions.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ProductIdentityDecisionError, match="decision_candidate_duplicate"):
        load_reviewed_decisions(path)


def test_decision_requires_current_contract_versions() -> None:
    assert IDENTITY_NORMALIZATION_VERSION == "product-homologation-v2.5"
    assert IDENTITY_POLICY_VERSION == "precios-sps-product-identity-policy/v1"


def test_approved_identity_rejects_title_or_ai_style_assertion_without_strong_evidence() -> None:
    left, right = _profiles(
        SourceProductRecord("a:1", "a", "Demo Producto 500 g", "Demo"),
        SourceProductRecord("b:2", "b", "Demo Producto 500 g", "Demo"),
    )
    valid = _approved_equivalence(left, right)
    payload = asdict(valid)
    payload["evidence_codes"] = ("source_title", "human_verified")
    with pytest.raises(
        ProductIdentityDecisionError,
        match="approved_identity_evidence_insufficient",
    ):
        ReviewedIdentityDecision(**payload)


def test_reviewed_pair_builds_one_shadow_identity_group() -> None:
    left, right = _profiles(
        SourceProductRecord("a:1", "a", "Nutri Yema Claras 554 g", "Nutri Yema"),
        SourceProductRecord("b:2", "b", "Nutri Yema Claras 1.2 lb", "Nutri Yema"),
    )
    decision = _approved_equivalence(left, right)
    groups = build_reviewed_identity_groups((left, right), (decision,))
    assert len(groups) == 1
    assert groups[0].master_product_id == decision.master_product_id
    assert groups[0].relation == "VERIFIED_EQUIVALENT"
    assert groups[0].source_record_ids == ("a:1", "b:2")


def test_reviewed_groups_do_not_assume_transitivity() -> None:
    records = (
        SourceProductRecord("a:1", "a", "Demo Producto 500 g", "Demo"),
        SourceProductRecord("b:2", "b", "Demo Producto 500 g", "Demo"),
        SourceProductRecord("c:3", "c", "Demo Producto 500 g", "Demo"),
    )
    result = homologate_products_v2(records, candidate_threshold=0)
    profiles = {profile.record.source_record_id: profile for profile in result.profiles}
    ab = _approved_equivalence(profiles["a:1"], profiles["b:2"])
    bc = _approved_equivalence(profiles["b:2"], profiles["c:3"])
    with pytest.raises(
        ProductIdentityDecisionError,
        match="reviewed_identity_cluster_missing_pair_decision",
    ):
        build_reviewed_identity_groups(result.profiles, (ab, bc))
