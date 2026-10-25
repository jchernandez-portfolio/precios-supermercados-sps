"""Readiness técnico del catálogo cuya cadena completa está ligada a SPS.

Esta capa exige un ``VerifiedSpsStructuralContext`` y un provenance físico
producido por el finalizador context-bound. No acepta un discovery suelto ni
permite que el caller aporte ubicación, request limit o autoridad.

El resultado sigue sin conceder aceptación productiva: incluso si cobertura,
receipts v3, contexto SPS y manifest físico son coherentes, permanecen los gates
explícitos de trusted collector y autoridad productiva.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NoReturn

from precios_supermercados.canonical_authenticated_provenance_plan import (
    CANONICAL_CATALOG_REQUEST_LIMIT,
    CanonicalAuthenticatedProvenancePlanError,
    derive_canonical_authenticated_catalog_plan,
)
from precios_supermercados.scrapers.la_colonia_catalog_coverage import (
    CatalogCoverageReport,
    _evaluate_canonical_catalog_coverage,
)
from precios_supermercados.scrapers.la_colonia_context_bound_catalog_finalizer import (
    ContextBoundCatalogProvenanceRun,
)
from precios_supermercados.sps_context_bound_discovery import VerifiedSpsStructuralContext

_TRUST_GATE = "trusted_collector_provenance_unavailable"
_AUTHORITY_BLOCKER = "production_authority_not_established"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class ContextBoundCatalogAcceptanceReadinessError(ValueError):
    """La evidencia context-bound no forma un candidato técnico coherente."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


def _fail(code: str, message: str | None = None) -> NoReturn:
    raise ContextBoundCatalogAcceptanceReadinessError(code, message)


def _sha256(value: object, code: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        _fail(code)
    return value


@dataclass(frozen=True, slots=True)
class ContextBoundCatalogAcceptanceReadiness:
    coverage: CatalogCoverageReport
    discovery_digest: str
    authenticated_plan_digest: str
    provenance_manifest_digest: str
    structural_context_plan_digest: str
    location_id: str
    context_fingerprint: str
    technical_catalog_complete: bool
    ready_for_productive_authority_evidence: bool
    catalog_accepted: bool
    blockers: tuple[str, ...]
    production_authority: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.coverage, CatalogCoverageReport):
            _fail("catalog_context_readiness_coverage_invalid")
        for name in (
            "discovery_digest",
            "authenticated_plan_digest",
            "provenance_manifest_digest",
            "structural_context_plan_digest",
            "context_fingerprint",
        ):
            object.__setattr__(
                self,
                name,
                _sha256(
                    getattr(self, name),
                    f"catalog_context_readiness_{name}_invalid",
                ),
            )
        if self.location_id != "la_colonia_sps":
            _fail("catalog_context_readiness_location_invalid")
        if not isinstance(self.technical_catalog_complete, bool):
            _fail("catalog_context_readiness_technical_complete_invalid")
        if not isinstance(self.ready_for_productive_authority_evidence, bool):
            _fail("catalog_context_readiness_authority_ready_invalid")
        if self.ready_for_productive_authority_evidence != self.technical_catalog_complete:
            _fail("catalog_context_readiness_authority_ready_inconsistent")
        if self.catalog_accepted is not False:
            _fail("catalog_context_readiness_catalog_acceptance_forbidden")
        if self.production_authority is not False:
            _fail("catalog_context_readiness_authority_forbidden")
        if not isinstance(self.blockers, tuple) or any(
            not isinstance(item, str) or not item for item in self.blockers
        ):
            _fail("catalog_context_readiness_blockers_invalid")
        if len(set(self.blockers)) != len(self.blockers):
            _fail("catalog_context_readiness_blockers_duplicate")
        mandatory = {_TRUST_GATE, _AUTHORITY_BLOCKER}
        if not mandatory.issubset(self.blockers):
            _fail("catalog_context_readiness_authority_blocker_missing")
        technical = tuple(item for item in self.blockers if item not in mandatory)
        if self.technical_catalog_complete != (len(technical) == 0):
            _fail("catalog_context_readiness_technical_state_inconsistent")
        if self.coverage.accepted is not False:
            _fail("catalog_context_readiness_coverage_must_remain_unaccepted")
        if _TRUST_GATE not in self.coverage._reasons:
            _fail("catalog_context_readiness_trust_gate_missing")


def _assert_context_binding(
    sps_context: VerifiedSpsStructuralContext,
    plan,
    provenance: ContextBoundCatalogProvenanceRun,
) -> None:
    discovery = sps_context.discovery
    collection = provenance.collection
    manifest = provenance.manifest

    if collection.plan_digest != plan.digest:
        _fail("catalog_context_readiness_collection_plan_digest_mismatch")
    if collection.discovery_digest != discovery.digest:
        _fail("catalog_context_readiness_collection_discovery_digest_mismatch")
    if collection.structural_context_plan_digest != sps_context.plan_digest:
        _fail("catalog_context_readiness_collection_structural_plan_digest_mismatch")
    if provenance.structural_context_plan_digest != sps_context.plan_digest:
        _fail("catalog_context_readiness_provenance_structural_plan_digest_mismatch")
    if collection.location_id != sps_context.location_id or provenance.location_id != sps_context.location_id:
        _fail("catalog_context_readiness_location_mismatch")
    if (
        collection.context_fingerprint != sps_context.context_fingerprint
        or provenance.context_fingerprint != sps_context.context_fingerprint
    ):
        _fail("catalog_context_readiness_context_fingerprint_mismatch")

    expected_manifest = {
        "run_id": plan.run_id,
        "authorization_id": plan.authorization_id,
        "approved_commit_sha": plan.approved_commit_sha,
        "collector_release_id": plan.collector_release_id,
        "collector_code_sha256": plan.collector_code_sha256,
        "collector_signing_key_id": plan.collector_signing_key_id,
        "primary_traversal_id": plan.plan.primary_traversal_id,
        "reconciliation_traversal_id": plan.plan.reconciliation_traversal_id,
        "primary_order_by": plan.plan.primary_order_by,
        "reconciliation_order_by": plan.plan.reconciliation_order_by,
    }
    for name, expected in expected_manifest.items():
        if getattr(manifest, name) != expected:
            _fail(f"catalog_context_readiness_manifest_{name}_mismatch")
    if manifest.request_count != plan.request_count:
        _fail("catalog_context_readiness_manifest_request_count_mismatch")

    expected_pages = {page.identity for page in plan.pages}
    observed_pages = {record.expected.identity for record in manifest.pages}
    if observed_pages != expected_pages or len(manifest.pages) != len(expected_pages):
        _fail("catalog_context_readiness_manifest_page_set_mismatch")

    for traversal, traversal_id, order_by, label in (
        (
            collection.primary,
            plan.plan.primary_traversal_id,
            plan.plan.primary_order_by,
            "primary",
        ),
        (
            collection.reconciliation,
            plan.plan.reconciliation_traversal_id,
            plan.plan.reconciliation_order_by,
            "reconciliation",
        ),
    ):
        if traversal.run_id != plan.run_id:
            _fail(f"catalog_context_readiness_{label}_run_id_mismatch")
        if traversal.tree_digest != discovery.tree_digest:
            _fail(f"catalog_context_readiness_{label}_tree_digest_mismatch")
        if traversal.traversal_id != traversal_id:
            _fail(f"catalog_context_readiness_{label}_traversal_id_mismatch")
        if traversal.order_by != order_by:
            _fail(f"catalog_context_readiness_{label}_order_by_mismatch")

    if provenance.production_authority is not False:
        _fail("catalog_context_readiness_provenance_authority_forbidden")
    if collection.production_authority is not False:
        _fail("catalog_context_readiness_collection_authority_forbidden")
    if manifest.production_authority is not False:
        _fail("catalog_context_readiness_manifest_authority_forbidden")


def assess_context_bound_catalog_acceptance_readiness(
    *,
    sps_context: VerifiedSpsStructuralContext,
    provenance: ContextBoundCatalogProvenanceRun,
) -> ContextBoundCatalogAcceptanceReadiness:
    """Evalúa completitud técnica sin permitir autoridad caller-controlled."""

    if not isinstance(sps_context, VerifiedSpsStructuralContext):
        _fail("verified_sps_structural_context_required")
    if (
        sps_context.production_authority is not False
        or sps_context.catalog_accepted is not False
        or sps_context.extraction_enabled is not False
    ):
        _fail("verified_sps_structural_context_authority_invalid")
    if not isinstance(provenance, ContextBoundCatalogProvenanceRun):
        _fail("catalog_context_readiness_provenance_invalid")

    discovery = sps_context.discovery
    if discovery.production_authority is not False:
        _fail("catalog_context_readiness_discovery_authority_forbidden")
    try:
        plan = derive_canonical_authenticated_catalog_plan(discovery)
    except CanonicalAuthenticatedProvenancePlanError as exc:
        raise ContextBoundCatalogAcceptanceReadinessError(
            f"catalog_context_readiness_plan_{exc.code}"
        ) from exc

    _assert_context_binding(sps_context, plan, provenance)

    coverage = _evaluate_canonical_catalog_coverage(
        discovery.structure,
        provenance.collection.primary,
        provenance.collection.reconciliation,
        request_limit=CANONICAL_CATALOG_REQUEST_LIMIT,
    )
    reasons = tuple(coverage._reasons)
    if _TRUST_GATE not in reasons:
        _fail("catalog_context_readiness_trust_gate_missing")
    technical_reasons = tuple(reason for reason in reasons if reason != _TRUST_GATE)
    technical_complete = len(technical_reasons) == 0
    blockers = tuple(
        dict.fromkeys(
            (
                *technical_reasons,
                _TRUST_GATE,
                _AUTHORITY_BLOCKER,
            )
        )
    )
    return ContextBoundCatalogAcceptanceReadiness(
        coverage=coverage,
        discovery_digest=discovery.digest,
        authenticated_plan_digest=plan.digest,
        provenance_manifest_digest=provenance.manifest.digest,
        structural_context_plan_digest=sps_context.plan_digest,
        location_id=sps_context.location_id,
        context_fingerprint=sps_context.context_fingerprint,
        technical_catalog_complete=technical_complete,
        ready_for_productive_authority_evidence=technical_complete,
        catalog_accepted=False,
        blockers=blockers,
    )
