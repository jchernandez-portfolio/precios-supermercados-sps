"""Decisiones auditables para identidad de productos sin GTIN compartido.

Este módulo no genera equivalencias por similitud. Recibe perfiles producidos por
el motor de identidad, clasifica la evidencia automática y, opcionalmente, aplica
una decisión revisada sólo cuando su huella todavía coincide con la evidencia
fuente actual.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping

from .gtin_policy import restricted_gtin_shared_master_ok
from .product_homologation import ProductProfile, fold_text
from .product_identity_v2 import IDENTITY_NORMALIZATION_VERSION, explain_candidate


DECISION_SCHEMA = "precios-sps-product-identity-decisions/v1"
IDENTITY_POLICY_VERSION = "precios-sps-product-identity-policy/v1"

RELATIONS = frozenset(
    {
        "EXACT_TRADE_ITEM",
        "VERIFIED_EQUIVALENT",
        "PRODUCT_VARIANT",
        "COMPARABLE_ALTERNATIVE",
        "UNRESOLVED",
        "CONFLICT",
    }
)
DECISION_STATUSES = frozenset({"approved", "pending", "retired"})
IDENTITY_RELATIONS = frozenset({"EXACT_TRADE_ITEM", "VERIFIED_EQUIVALENT"})
ALLOWED_EVIDENCE_CODES = frozenset(
    {
        "same_valid_gtin",
        "manufacturer_catalog",
        "barcode_visible",
        "package_front",
        "package_back",
        "explicit_unit_conversion",
        "retailer_detail_page",
        "source_title",
        "human_verified",
    }
)


class ProductIdentityDecisionError(ValueError):
    """Una decisión no cumple el contrato de identidad."""


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(payload: object) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def profile_evidence_fingerprint(profile: ProductProfile) -> str:
    """Huella sólo de evidencia fuente/material; no incluye score heurístico."""

    record = profile.record
    presentation = profile.presentation
    return _sha256(
        {
            "source_record_id": record.source_record_id,
            "supermarket_id": record.supermarket_id,
            "source_name": record.source_name,
            "source_brand": record.source_brand,
            "source_presentation": record.source_presentation,
            "source_category": record.source_category,
            "barcode": record.barcode,
            "canonical_gtin": profile.canonical_gtin,
            "presentation": None
            if presentation is None
            else {
                "dimension": presentation.dimension,
                "total_base": format(presentation.total_base.normalize(), "f"),
                "pack_count": presentation.pack_count,
                "unit_amount_base": None
                if presentation.unit_amount_base is None
                else format(presentation.unit_amount_base.normalize(), "f"),
            },
        }
    )


def candidate_id(left_source_record_id: str, right_source_record_id: str) -> str:
    left, right = sorted((left_source_record_id, right_source_record_id))
    if not left or not right or left == right:
        raise ProductIdentityDecisionError("candidate_pair_invalid")
    return f"candidate_{_sha256({'left': left, 'right': right})[:24]}"


@dataclass(frozen=True, slots=True)
class ReviewedIdentityDecision:
    candidate_id: str
    left_source_record_id: str
    right_source_record_id: str
    left_evidence_fingerprint: str
    right_evidence_fingerprint: str
    relation: str
    status: str
    master_product_id: str | None
    evidence_codes: tuple[str, ...]
    evidence_references: tuple[str, ...]
    rationale: str
    reviewed_by: str
    reviewed_at_utc: str
    policy_version: str = IDENTITY_POLICY_VERSION
    engine_version: str = IDENTITY_NORMALIZATION_VERSION

    def __post_init__(self) -> None:
        left, right = sorted((self.left_source_record_id, self.right_source_record_id))
        if (left, right) != (self.left_source_record_id, self.right_source_record_id):
            raise ProductIdentityDecisionError("decision_pair_not_canonical")
        if self.candidate_id != candidate_id(left, right):
            raise ProductIdentityDecisionError("decision_candidate_id_invalid")
        if any(len(value) != 64 for value in (self.left_evidence_fingerprint, self.right_evidence_fingerprint)):
            raise ProductIdentityDecisionError("decision_fingerprint_invalid")
        if self.relation not in RELATIONS:
            raise ProductIdentityDecisionError("decision_relation_invalid")
        if self.status not in DECISION_STATUSES:
            raise ProductIdentityDecisionError("decision_status_invalid")
        if self.status == "pending" and self.relation != "UNRESOLVED":
            raise ProductIdentityDecisionError("pending_decision_must_be_unresolved")
        if self.status == "approved" and self.relation == "UNRESOLVED":
            raise ProductIdentityDecisionError("approved_decision_cannot_be_unresolved")
        if (self.relation in IDENTITY_RELATIONS) != (self.master_product_id is not None):
            raise ProductIdentityDecisionError("decision_master_identity_mismatch")
        if self.master_product_id is not None and not self.master_product_id.startswith("prod_"):
            raise ProductIdentityDecisionError("decision_master_product_id_invalid")
        if not self.evidence_codes or any(code not in ALLOWED_EVIDENCE_CODES for code in self.evidence_codes):
            raise ProductIdentityDecisionError("decision_evidence_codes_invalid")
        if self.status == "approved" and self.relation in IDENTITY_RELATIONS:
            codes = set(self.evidence_codes)
            strong_evidence = bool(
                codes & {"same_valid_gtin", "manufacturer_catalog", "barcode_visible"}
            ) or {"package_front", "package_back"} <= codes
            if "human_verified" not in codes or not strong_evidence:
                raise ProductIdentityDecisionError("approved_identity_evidence_insufficient")
        if self.relation == "EXACT_TRADE_ITEM" and "same_valid_gtin" not in self.evidence_codes:
            raise ProductIdentityDecisionError("exact_trade_item_requires_gtin_evidence")
        if not self.rationale.strip() or not self.reviewed_by.strip():
            raise ProductIdentityDecisionError("decision_audit_text_missing")
        try:
            reviewed = datetime.fromisoformat(self.reviewed_at_utc.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ProductIdentityDecisionError("decision_reviewed_at_invalid") from exc
        if reviewed.tzinfo is None or not self.reviewed_at_utc.endswith("Z"):
            raise ProductIdentityDecisionError("decision_reviewed_at_invalid")
        if self.policy_version != IDENTITY_POLICY_VERSION:
            raise ProductIdentityDecisionError("decision_policy_version_invalid")

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "ReviewedIdentityDecision":
        expected = {
            "candidate_id",
            "left_source_record_id",
            "right_source_record_id",
            "left_evidence_fingerprint",
            "right_evidence_fingerprint",
            "relation",
            "status",
            "master_product_id",
            "evidence_codes",
            "evidence_references",
            "rationale",
            "reviewed_by",
            "reviewed_at_utc",
            "policy_version",
            "engine_version",
        }
        if set(value) != expected:
            raise ProductIdentityDecisionError("decision_fields_invalid")
        sequence_fields = ("evidence_codes", "evidence_references")
        if any(
            not isinstance(value[field], list)
            or any(not isinstance(item, str) for item in value[field])
            for field in sequence_fields
        ):
            raise ProductIdentityDecisionError("decision_evidence_shape_invalid")
        scalar_fields = expected - {"master_product_id", *sequence_fields}
        if any(not isinstance(value[field], str) for field in scalar_fields):
            raise ProductIdentityDecisionError("decision_text_field_invalid")
        master_product_id = value["master_product_id"]
        if master_product_id is not None and not isinstance(master_product_id, str):
            raise ProductIdentityDecisionError("decision_master_product_id_invalid")
        return cls(
            candidate_id=str(value["candidate_id"]),
            left_source_record_id=str(value["left_source_record_id"]),
            right_source_record_id=str(value["right_source_record_id"]),
            left_evidence_fingerprint=str(value["left_evidence_fingerprint"]),
            right_evidence_fingerprint=str(value["right_evidence_fingerprint"]),
            relation=str(value["relation"]),
            status=str(value["status"]),
            master_product_id=master_product_id,
            evidence_codes=tuple(value["evidence_codes"]),
            evidence_references=tuple(value["evidence_references"]),
            rationale=str(value["rationale"]),
            reviewed_by=str(value["reviewed_by"]),
            reviewed_at_utc=str(value["reviewed_at_utc"]),
            policy_version=str(value["policy_version"]),
            engine_version=str(value["engine_version"]),
        )


@dataclass(frozen=True, slots=True)
class ProductRelationAssessment:
    candidate_id: str
    relation: str
    decision_state: str
    master_product_id: str | None
    evidence_codes: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReviewedIdentityGroup:
    master_product_id: str
    source_record_ids: tuple[str, ...]
    supermarket_ids: tuple[str, ...]
    relation: str


def _policy_conflicts(left: ProductProfile, right: ProductProfile) -> tuple[str, ...]:
    """Conflictos del contrato shadow aún no desplegados en el motor v2.3."""

    formulation_groups = (
        frozenset({"classic", "clasico", "orig", "original", "normal", "regular"}),
        frozenset({"zero", "sin azucar"}),
        frozenset({"light", "diet", "bajo en azucar"}),
    )

    def declared_group(profile: ProductProfile) -> int | None:
        text = fold_text(profile.record.source_name) or ""
        for index, group in enumerate(formulation_groups):
            if any(re.search(rf"(?<!\w){re.escape(label)}(?!\w)", text) for label in group):
                return index
        return None

    left_group = declared_group(left)
    right_group = declared_group(right)
    if left_group is not None and right_group is not None and left_group != right_group:
        return ("variant_conflict",)
    return ()


def _restricted_gtin_pair_ok(left: ProductProfile, right: ProductProfile) -> bool:
    """Un GTIN de circulación restringida sólo es identidad dentro de un maestro."""

    return restricted_gtin_shared_master_ok(
        left.canonical_gtin,
        (left.record.supermarket_id, right.record.supermarket_id),
    )


def _orient_decision(
    left: ProductProfile,
    right: ProductProfile,
    decision: ReviewedIdentityDecision,
) -> tuple[str, str]:
    if left.record.source_record_id == decision.left_source_record_id:
        return decision.left_evidence_fingerprint, decision.right_evidence_fingerprint
    return decision.right_evidence_fingerprint, decision.left_evidence_fingerprint


def assess_product_relation(
    left: ProductProfile,
    right: ProductProfile,
    decision: ReviewedIdentityDecision | None = None,
) -> ProductRelationAssessment:
    """Clasifica una pareja sin convertir similitud en identidad."""

    pair_id = candidate_id(left.record.source_record_id, right.record.source_record_id)
    evidence = explain_candidate(left, right)
    conflicts = tuple(sorted(set(evidence.conflict_signals) | set(_policy_conflicts(left, right))))
    if conflicts:
        return ProductRelationAssessment(
            pair_id,
            "CONFLICT",
            "blocked",
            None,
            (),
            conflicts,
        )
    if decision is not None:
        if decision.candidate_id != pair_id:
            raise ProductIdentityDecisionError("decision_candidate_mismatch")
        expected_left, expected_right = _orient_decision(left, right, decision)
        if (
            expected_left != profile_evidence_fingerprint(left)
            or expected_right != profile_evidence_fingerprint(right)
            or decision.engine_version != IDENTITY_NORMALIZATION_VERSION
        ):
            return ProductRelationAssessment(
                pair_id,
                "UNRESOLVED",
                "stale_decision",
                None,
                decision.evidence_codes,
                ("source_evidence_changed",),
            )
        if decision.status == "retired":
            return ProductRelationAssessment(pair_id, "UNRESOLVED", "retired", None, (), ("decision_retired",))
        if decision.status == "pending":
            return ProductRelationAssessment(pair_id, "UNRESOLVED", "pending", None, decision.evidence_codes, ("human_review_pending",))
        if decision.relation == "EXACT_TRADE_ITEM" and not (
            left.canonical_gtin is not None
            and left.canonical_gtin == right.canonical_gtin
            and decision.master_product_id == left.canonical_product_id
        ):
            raise ProductIdentityDecisionError("exact_trade_item_gtin_mismatch")
        if decision.relation == "EXACT_TRADE_ITEM" and not _restricted_gtin_pair_ok(left, right):
            raise ProductIdentityDecisionError("exact_trade_item_restricted_gtin")
        return ProductRelationAssessment(
            pair_id,
            decision.relation,
            "approved",
            decision.master_product_id,
            decision.evidence_codes,
            (),
        )
    if left.canonical_gtin is not None and left.canonical_gtin == right.canonical_gtin:
        if not _restricted_gtin_pair_ok(left, right):
            return ProductRelationAssessment(
                pair_id,
                "UNRESOLVED",
                "review_required",
                None,
                (),
                ("restricted_gtin_outside_shared_master",),
            )
        return ProductRelationAssessment(
            pair_id,
            "EXACT_TRADE_ITEM",
            "rule_verified",
            left.canonical_product_id,
            ("same_valid_gtin",),
            (),
        )
    reasons = tuple(evidence.matching_signals) or ("insufficient_identity_evidence",)
    return ProductRelationAssessment(pair_id, "UNRESOLVED", "review_required", None, (), reasons)


def load_reviewed_decisions(path: Path) -> tuple[ReviewedIdentityDecision, ...]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProductIdentityDecisionError("decision_registry_unreadable") from exc
    if not isinstance(payload, dict) or set(payload) != {"schema", "policy_version", "decisions"}:
        raise ProductIdentityDecisionError("decision_registry_shape_invalid")
    if payload["schema"] != DECISION_SCHEMA or payload["policy_version"] != IDENTITY_POLICY_VERSION:
        raise ProductIdentityDecisionError("decision_registry_version_invalid")
    if not isinstance(payload["decisions"], list):
        raise ProductIdentityDecisionError("decision_registry_rows_invalid")
    decisions = tuple(ReviewedIdentityDecision.from_mapping(row) for row in payload["decisions"])
    ids = [decision.candidate_id for decision in decisions]
    if len(ids) != len(set(ids)):
        raise ProductIdentityDecisionError("decision_candidate_duplicate")
    return decisions


def index_reviewed_decisions(
    decisions: Iterable[ReviewedIdentityDecision],
) -> dict[str, ReviewedIdentityDecision]:
    result: dict[str, ReviewedIdentityDecision] = {}
    for decision in decisions:
        if decision.candidate_id in result:
            raise ProductIdentityDecisionError("decision_candidate_duplicate")
        result[decision.candidate_id] = decision
    return result


def build_reviewed_identity_groups(
    profiles: Iterable[ProductProfile],
    decisions: Iterable[ReviewedIdentityDecision],
) -> tuple[ReviewedIdentityGroup, ...]:
    """Forma grupos revisados sólo cuando todas las parejas están respaldadas.

    Una cadena A-B y B-C no autoriza A-C. El grupo requiere un clique de
    decisiones vigentes (o una pareja exacta por GTIN) y como máximo un registro
    por supermercado.
    """

    profile_index = {profile.record.source_record_id: profile for profile in profiles}
    decision_index = index_reviewed_decisions(decisions)
    members_by_master: dict[str, set[str]] = {}
    membership_owner: dict[str, str] = {}

    for decision in decision_index.values():
        if decision.status != "approved" or decision.relation not in IDENTITY_RELATIONS:
            continue
        if (
            decision.left_source_record_id not in profile_index
            or decision.right_source_record_id not in profile_index
        ):
            raise ProductIdentityDecisionError("decision_source_record_missing")
        left = profile_index[decision.left_source_record_id]
        right = profile_index[decision.right_source_record_id]
        if left.record.supermarket_id == right.record.supermarket_id:
            raise ProductIdentityDecisionError("reviewed_identity_same_retailer_pair")
        assessment = assess_product_relation(left, right, decision)
        if assessment.relation not in IDENTITY_RELATIONS or assessment.decision_state != "approved":
            raise ProductIdentityDecisionError("reviewed_identity_decision_not_applicable")
        master = decision.master_product_id
        assert master is not None
        for source_id in (decision.left_source_record_id, decision.right_source_record_id):
            previous = membership_owner.get(source_id)
            if previous is not None and previous != master:
                raise ProductIdentityDecisionError("reviewed_identity_membership_collision")
            membership_owner[source_id] = master
            members_by_master.setdefault(master, set()).add(source_id)

    groups: list[ReviewedIdentityGroup] = []
    for master_product_id, member_ids in sorted(members_by_master.items()):
        ordered_ids = tuple(sorted(member_ids))
        member_profiles = [profile_index[source_id] for source_id in ordered_ids]
        retailers = [profile.record.supermarket_id for profile in member_profiles]
        if len(retailers) != len(set(retailers)):
            raise ProductIdentityDecisionError("reviewed_identity_retailer_collision")

        observed_relations: set[str] = set()
        for left_index, left in enumerate(member_profiles):
            for right in member_profiles[left_index + 1 :]:
                pair = candidate_id(left.record.source_record_id, right.record.source_record_id)
                direct = decision_index.get(pair)
                if direct is not None and direct.master_product_id != master_product_id:
                    direct = None
                assessment = assess_product_relation(left, right, direct)
                if assessment.relation not in IDENTITY_RELATIONS:
                    reason = (
                        "reviewed_identity_cluster_conflict"
                        if assessment.relation == "CONFLICT"
                        else "reviewed_identity_cluster_missing_pair_decision"
                    )
                    raise ProductIdentityDecisionError(reason)
                if assessment.master_product_id not in {master_product_id, left.canonical_product_id}:
                    raise ProductIdentityDecisionError("reviewed_identity_cluster_master_mismatch")
                observed_relations.add(assessment.relation)
        relation = (
            "EXACT_TRADE_ITEM"
            if observed_relations == {"EXACT_TRADE_ITEM"}
            else "VERIFIED_EQUIVALENT"
        )
        groups.append(
            ReviewedIdentityGroup(
                master_product_id=master_product_id,
                source_record_ids=ordered_ids,
                supermarket_ids=tuple(sorted(retailers)),
                relation=relation,
            )
        )
    return tuple(groups)
