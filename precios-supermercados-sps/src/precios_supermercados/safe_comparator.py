"""Capa fail-closed para comparaciones directas de precio.

La homologación descriptiva puede conservar candidatos para revisión, pero una
comparación de ahorro sólo se autoriza cuando la identidad comercial está
suficientemente demostrada. En particular, marca + presentación nunca bastan.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from itertools import combinations

from .product_homologation import (
    HomologationResult,
    ProductProfile,
    SourceProductRecord,
    homologate_products,
    presentations_compatible,
)

COMPARABLE = "comparable"
REVIEW_REQUIRED = "review_required"
NOT_COMPARABLE = "not_comparable"
SAFE_COMPARISON_STATUSES = frozenset({COMPARABLE, REVIEW_REQUIRED, NOT_COMPARABLE})


@dataclass(frozen=True, slots=True)
class SafeComparisonDecision:
    """Decisión pairwise usada por datasets, analítica y dashboards."""

    left_source_record_id: str
    right_source_record_id: str
    status: str
    reasons: tuple[str, ...]
    canonical_product_id: str | None

    def __post_init__(self) -> None:
        if self.status not in SAFE_COMPARISON_STATUSES:
            raise ValueError("safe_comparison_status_invalid")
        if self.status == COMPARABLE and self.reasons:
            raise ValueError("comparable_with_reasons")
        if self.status != COMPARABLE and not self.reasons:
            raise ValueError("blocked_comparison_requires_reason")

    @property
    def automatic_comparable(self) -> bool:
        return self.status == COMPARABLE


@dataclass(frozen=True, slots=True)
class SafeComparisonGroup:
    """Resumen conservador de un grupo de identidad fuerte por GTIN."""

    canonical_gtin: str
    canonical_product_id: str
    source_record_ids: tuple[str, ...]
    supermarket_ids: tuple[str, ...]
    status: str
    reasons: tuple[str, ...]

    @property
    def automatic_comparable(self) -> bool:
        return self.status == COMPARABLE


def _identity_tokens(profile: ProductProfile) -> frozenset[str]:
    """Descriptores comerciales remanentes tras quitar marca/tipo/presentación."""

    return frozenset(profile.matching_tokens)


def compare_profiles(left: ProductProfile, right: ProductProfile) -> SafeComparisonDecision:
    """Decide si dos perfiles pueden alimentar una comparación directa.

    Orden de precedencia:
    1. contradicciones duras -> ``not_comparable``;
    2. evidencia incompleta -> ``review_required``;
    3. sólo identidad fuerte y comercialmente coherente -> ``comparable``.
    """

    reasons: set[str] = set()
    if left.record.supermarket_id == right.record.supermarket_id:
        return SafeComparisonDecision(
            left.record.source_record_id,
            right.record.source_record_id,
            NOT_COMPARABLE,
            ("same_supermarket",),
            None,
        )

    left_gtin = left.canonical_gtin
    right_gtin = right.canonical_gtin
    if left_gtin is not None and right_gtin is not None and left_gtin != right_gtin:
        return SafeComparisonDecision(
            left.record.source_record_id,
            right.record.source_record_id,
            NOT_COMPARABLE,
            ("different_gtin",),
            None,
        )
    if left_gtin is None or right_gtin is None:
        return SafeComparisonDecision(
            left.record.source_record_id,
            right.record.source_record_id,
            REVIEW_REQUIRED,
            ("strong_identity_missing",),
            None,
        )

    canonical_product_id = left.canonical_product_id
    if canonical_product_id is None or right.canonical_product_id != canonical_product_id:
        return SafeComparisonDecision(
            left.record.source_record_id,
            right.record.source_record_id,
            NOT_COMPARABLE,
            ("canonical_identity_conflict",),
            None,
        )

    if left.normalized_brand is None or right.normalized_brand is None:
        reasons.add("brand_identity_incomplete")
    elif left.normalized_brand != right.normalized_brand:
        return SafeComparisonDecision(
            left.record.source_record_id,
            right.record.source_record_id,
            NOT_COMPARABLE,
            ("brand_identity_conflict",),
            canonical_product_id,
        )

    left_type = left.taxonomy.product_type
    right_type = right.taxonomy.product_type
    if left_type is None or right_type is None:
        reasons.add("product_type_incomplete")
    elif left_type != right_type:
        return SafeComparisonDecision(
            left.record.source_record_id,
            right.record.source_record_id,
            NOT_COMPARABLE,
            ("product_type_conflict",),
            canonical_product_id,
        )

    if left.presentation is None or right.presentation is None:
        reasons.add("presentation_incomplete")
    elif not presentations_compatible(left.presentation, right.presentation):
        return SafeComparisonDecision(
            left.record.source_record_id,
            right.record.source_record_id,
            NOT_COMPARABLE,
            ("presentation_conflict",),
            canonical_product_id,
        )

    if left.presentation_status in {"conflict", "ambiguous_multipack"} or right.presentation_status in {
        "conflict",
        "ambiguous_multipack",
    }:
        return SafeComparisonDecision(
            left.record.source_record_id,
            right.record.source_record_id,
            NOT_COMPARABLE,
            ("presentation_evidence_conflict",),
            canonical_product_id,
        )

    left_tokens = _identity_tokens(left)
    right_tokens = _identity_tokens(right)
    if left_tokens != right_tokens:
        if left_tokens and right_tokens and left_tokens.isdisjoint(right_tokens):
            return SafeComparisonDecision(
                left.record.source_record_id,
                right.record.source_record_id,
                NOT_COMPARABLE,
                ("commercial_identity_conflict",),
                canonical_product_id,
            )
        reasons.add("commercial_identity_incomplete")

    if reasons:
        return SafeComparisonDecision(
            left.record.source_record_id,
            right.record.source_record_id,
            REVIEW_REQUIRED,
            tuple(sorted(reasons)),
            canonical_product_id,
        )
    return SafeComparisonDecision(
        left.record.source_record_id,
        right.record.source_record_id,
        COMPARABLE,
        (),
        canonical_product_id,
    )


def safe_group_decisions(result: HomologationResult) -> tuple[SafeComparisonGroup, ...]:
    """Convierte grupos GTIN del motor en grupos aptos/no aptos para precio."""

    profile_by_id = {profile.record.source_record_id: profile for profile in result.profiles}
    groups: list[SafeComparisonGroup] = []
    for group in result.exact_gtin_groups:
        members = [profile_by_id[source_id] for source_id in group.source_record_ids]
        decisions = [
            compare_profiles(left, right)
            for left, right in combinations(members, 2)
            if left.record.supermarket_id != right.record.supermarket_id
        ]
        reasons = set(group.conflict_reasons)
        for decision in decisions:
            reasons.update(decision.reasons)

        if not decisions:
            status = REVIEW_REQUIRED
            reasons.add("cross_supermarket_pair_missing")
        elif any(decision.status == NOT_COMPARABLE for decision in decisions):
            status = NOT_COMPARABLE
        elif any(decision.status == REVIEW_REQUIRED for decision in decisions):
            status = REVIEW_REQUIRED
        else:
            status = COMPARABLE
            reasons.clear()

        groups.append(
            SafeComparisonGroup(
                canonical_gtin=group.canonical_gtin,
                canonical_product_id=group.canonical_product_id,
                source_record_ids=group.source_record_ids,
                supermarket_ids=group.supermarket_ids,
                status=status,
                reasons=tuple(sorted(reasons)),
            )
        )
    return tuple(groups)


def build_safe_comparison(
    records: Iterable[SourceProductRecord],
) -> tuple[HomologationResult, tuple[SafeComparisonGroup, ...]]:
    """Entrada única para consumidores que necesitan comparación fail-closed."""

    result = homologate_products(records)
    return result, safe_group_decisions(result)
