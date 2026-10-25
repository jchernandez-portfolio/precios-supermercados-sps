"""Entradas operativas mínimas delante del preparador comercial backend-neutral.

La ruta normal conserva el guard para runs no autoritativos. Para el MVP existe
una sola excepción explícita y versionada: el catálogo completo de La Colonia SPS
ya obtenido en GitHub Actions y aprobado por el usuario como snapshot inicial.

No hay keyrings, firmas, PKI ni servicios de autoridad. La aprobación identifica
exactamente el run/artifact conocido y produce un ``run_evidence_id`` estable para
replay/auditoría. ``extraction_enabled`` sigue en ``False`` y no se genera tráfico.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from .commercial_persistence_batch import (
    PreparedCommercialPersistence,
    prepare_new_run_persistence,
)
from .commercial_state import CommercialRunDecision, InMemoryCommercialState
from .enums import RunStatus
from .locations import DEFAULT_LOCATION_CATALOG, LocationCatalog
from .models import ValidatedOffer
from .tabular_records import QualityEventRecord


class CommercialPersistenceGuardError(ValueError):
    """La decisión no puede cruzar la frontera operativa de persistencia."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class NonAuthoritativePersistenceDecision:
    """Decisión final explícitamente incapaz de mutar estado comercial."""

    decision: CommercialRunDecision
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.decision, CommercialRunDecision):
            raise CommercialPersistenceGuardError("commercial_decision_invalid")
        if self.decision.run_status is RunStatus.RUNNING:
            raise CommercialPersistenceGuardError("run_not_final")
        if self.decision.catalog_accepted or self.decision.commercial_update_allowed:
            raise CommercialPersistenceGuardError("approved_snapshot_required")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise CommercialPersistenceGuardError("non_authoritative_reason_required")
        object.__setattr__(self, "reason", self.reason.strip())

    @property
    def scrape_run_id(self) -> str:
        return self.decision.scrape_run_id


# Aprobación humana versionada del snapshot inicial conocido.
LA_COLONIA_INITIAL_SNAPSHOT_RUN_ID = "32922877781"
LA_COLONIA_INITIAL_SNAPSHOT_ARTIFACT_ID = 9590684834
LA_COLONIA_INITIAL_SNAPSHOT_ARTIFACT_DIGEST = (
    "sha256:0427e88be27df89fd9fcb50ed600ef5c6aef64177bfba92b4af3d2e25756a892"
)
LA_COLONIA_INITIAL_SNAPSHOT_HEAD_SHA = "589b694fdc75fd97d47fcc5259062fb026cf7ee4"
LA_COLONIA_INITIAL_SNAPSHOT_SUPERMARKET_ID = "la_colonia"
LA_COLONIA_INITIAL_SNAPSHOT_LOCATION_ID = "la_colonia_sps"
LA_COLONIA_INITIAL_SNAPSHOT_PRODUCTS = 9437
LA_COLONIA_INITIAL_SNAPSHOT_OFFERS = 9439
LA_COLONIA_INITIAL_SNAPSHOT_STARTED_AT_UTC = datetime(
    2026, 8, 26, 2, 28, 59, tzinfo=UTC
)
LA_COLONIA_INITIAL_SNAPSHOT_FINISHED_AT_UTC = datetime(
    2026, 8, 26, 2, 41, 3, tzinfo=UTC
)
LA_COLONIA_INITIAL_SNAPSHOT_APPROVED_AT_UTC = datetime(
    2026, 8, 27, 4, 14, 1, tzinfo=UTC
)


def _initial_snapshot_evidence_id() -> str:
    material = "|".join(
        (
            LA_COLONIA_INITIAL_SNAPSHOT_RUN_ID,
            str(LA_COLONIA_INITIAL_SNAPSHOT_ARTIFACT_ID),
            LA_COLONIA_INITIAL_SNAPSHOT_ARTIFACT_DIGEST,
            LA_COLONIA_INITIAL_SNAPSHOT_HEAD_SHA,
            LA_COLONIA_INITIAL_SNAPSHOT_LOCATION_ID,
            str(LA_COLONIA_INITIAL_SNAPSHOT_OFFERS),
        )
    )
    return "crev1_" + hashlib.sha256(material.encode("utf-8")).hexdigest()


LA_COLONIA_INITIAL_SNAPSHOT_EVIDENCE_ID = _initial_snapshot_evidence_id()


def guard_non_authoritative_decision(
    decision: CommercialRunDecision,
    *,
    reason: str,
) -> NonAuthoritativePersistenceDecision:
    """Sella una decisión final únicamente cuando no puede actualizar current/history."""

    return NonAuthoritativePersistenceDecision(decision=decision, reason=reason)


def prepare_non_authoritative_run_persistence(
    state: InMemoryCommercialState,
    guarded_decision: NonAuthoritativePersistenceDecision,
    offers: Sequence[ValidatedOffer],
    *,
    supermarket_id: str,
    location_id: str,
    started_at_utc: datetime,
    finished_at_utc: datetime,
    products_observed: int,
    offers_observed: int,
    quality_events: Sequence[QualityEventRecord] = (),
    run_evidence_id: str | None = None,
    catalog: LocationCatalog = DEFAULT_LOCATION_CATALOG,
) -> PreparedCommercialPersistence:
    """Construye un batch durable que sólo registra un run no autoritativo."""

    if not isinstance(guarded_decision, NonAuthoritativePersistenceDecision):
        raise CommercialPersistenceGuardError("guarded_decision_required")
    decision = guarded_decision.decision
    if decision.catalog_accepted or decision.commercial_update_allowed:
        raise CommercialPersistenceGuardError("approved_snapshot_required")

    return prepare_new_run_persistence(
        state,
        decision,
        offers,
        supermarket_id=supermarket_id,
        location_id=location_id,
        started_at_utc=started_at_utc,
        finished_at_utc=finished_at_utc,
        products_observed=products_observed,
        offers_observed=offers_observed,
        quality_events=quality_events,
        run_evidence_id=run_evidence_id,
        catalog=catalog,
    )


def prepare_la_colonia_initial_snapshot_persistence(
    state: InMemoryCommercialState,
    offers: Sequence[ValidatedOffer],
    *,
    quality_events: Sequence[QualityEventRecord] = (),
    catalog: LocationCatalog = DEFAULT_LOCATION_CATALOG,
) -> PreparedCommercialPersistence:
    """Prepara exclusivamente el artifact #9590684834 como primer estado aceptado.

    La función no recibe run ID, artifact ID, digest, ubicación, conteos ni fecha de
    aprobación del caller. Todo eso está versionado arriba. Un cambio de snapshot
    requiere modificar explícitamente esta aprobación mediante PR.
    """

    if isinstance(offers, (str, bytes)) or not isinstance(offers, Sequence):
        raise CommercialPersistenceGuardError("initial_snapshot_offers_invalid")
    if len(offers) != LA_COLONIA_INITIAL_SNAPSHOT_OFFERS:
        raise CommercialPersistenceGuardError("initial_snapshot_offer_count_mismatch")

    decision = CommercialRunDecision(
        scrape_run_id=LA_COLONIA_INITIAL_SNAPSHOT_RUN_ID,
        run_status=RunStatus.SUCCESS,
        catalog_accepted=True,
        decided_at_utc=LA_COLONIA_INITIAL_SNAPSHOT_APPROVED_AT_UTC,
    )
    return prepare_new_run_persistence(
        state,
        decision,
        offers,
        supermarket_id=LA_COLONIA_INITIAL_SNAPSHOT_SUPERMARKET_ID,
        location_id=LA_COLONIA_INITIAL_SNAPSHOT_LOCATION_ID,
        started_at_utc=LA_COLONIA_INITIAL_SNAPSHOT_STARTED_AT_UTC,
        finished_at_utc=LA_COLONIA_INITIAL_SNAPSHOT_FINISHED_AT_UTC,
        products_observed=LA_COLONIA_INITIAL_SNAPSHOT_PRODUCTS,
        offers_observed=LA_COLONIA_INITIAL_SNAPSHOT_OFFERS,
        quality_events=quality_events,
        run_evidence_id=LA_COLONIA_INITIAL_SNAPSHOT_EVIDENCE_ID,
        catalog=catalog,
    )
