"""Binding durable de evidencia para futuros runs comerciales autoritativos.

Un ``scrape_run_id`` persistido no basta para demostrar que un retry posterior
corresponde exactamente a la misma decisión, ofertas, metadata y evidencia de
autoridad. Este módulo deriva un identificador opaco que liga todos esos inputs y
permite reconciliar un registro durable sin volver a aplicar la transición.

IMPORTANTE: el binding **no concede autoridad productiva**. El
``authority_evidence_id`` debe provenir, en una integración futura, de un
verificador productivo real. Este módulo sólo evita que una evidencia ya
verificada pueda reutilizarse con un payload comercial distinto.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .commercial_state import (
    CommercialRunDecision,
    CommercialStateError,
    InMemoryCommercialState,
    _run_fingerprint,
)
from .enums import RunStatus
from .models import ValidatedOffer
from .tabular_persistence import FACT_SCRAPE_RUNS
from .tabular_records import QualityEventRecord

_BINDING_SCHEMA = "commercial-run-evidence-1"
_BINDING_PREFIX = "crev1_"
_MAX_AUTHORITY_EVIDENCE_ID = 512


class CommercialRunEvidenceError(ValueError):
    """La evidencia de run no puede ligarse o reconciliarse de forma segura."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class DurableCommercialReplay:
    """Resultado de reconocer exactamente un run ya persistido."""

    scrape_run_id: str
    run_evidence_id: str
    current_created: int
    current_changed: int
    current_confirmed: int
    offers_ignored: int
    quality_event_count: int


def _required_text(value: object, code: str, *, max_length: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CommercialRunEvidenceError(code)
    cleaned = value.strip()
    if max_length is not None and len(cleaned) > max_length:
        raise CommercialRunEvidenceError(code)
    if any(ord(char) < 32 or ord(char) == 127 for char in cleaned):
        raise CommercialRunEvidenceError(code)
    return cleaned


def _utc_text(value: object, code: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise CommercialRunEvidenceError(code)
    if value.utcoffset().total_seconds() != 0:
        raise CommercialRunEvidenceError(code)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _non_negative_int(value: object, code: str) -> int:
    if isinstance(value, bool) or type(value) is not int or value < 0:
        raise CommercialRunEvidenceError(code)
    return value


def _quality_event_payload(event: QualityEventRecord) -> Mapping[str, object]:
    if not isinstance(event, QualityEventRecord):
        raise CommercialRunEvidenceError("quality_event_type_invalid")
    return {
        "quality_event_id": event.quality_event_id,
        "scrape_run_id": event.scrape_run_id,
        "supermarket_id": event.supermarket_id,
        "location_id": event.location_id,
        "offer_id": event.offer_id,
        "category": event.category.value,
        "severity": event.severity.value,
        "event_code": event.event_code,
        "observed_at_utc": _utc_text(
            event.observed_at_utc,
            "quality_event_observed_at_invalid",
        ),
    }


def _validate_bound_context(
    *,
    decision: CommercialRunDecision,
    offers: tuple[ValidatedOffer, ...],
    quality_events: tuple[QualityEventRecord, ...],
    supermarket_id: str,
    location_id: str,
) -> None:
    """Reaplica invariantes que un replay no volverá a ejecutar en el state engine."""

    try:
        InMemoryCommercialState._validate_run_payload(decision, offers)
    except CommercialStateError as exc:
        raise CommercialRunEvidenceError("commercial_run_payload_invalid") from exc

    for item in offers:
        offer = item.offer
        if offer.supermarket_id != supermarket_id:
            raise CommercialRunEvidenceError("offer_supermarket_mismatch")
        if offer.location_id != location_id:
            raise CommercialRunEvidenceError("offer_location_mismatch")

    event_ids: set[str] = set()
    for event in quality_events:
        if not isinstance(event, QualityEventRecord):
            raise CommercialRunEvidenceError("quality_event_type_invalid")
        if event.quality_event_id in event_ids:
            raise CommercialRunEvidenceError("quality_event_id_duplicate")
        event_ids.add(event.quality_event_id)
        if event.scrape_run_id != decision.scrape_run_id:
            raise CommercialRunEvidenceError("quality_event_run_mismatch")
        if event.supermarket_id != supermarket_id:
            raise CommercialRunEvidenceError("quality_event_supermarket_mismatch")
        if event.location_id != location_id:
            raise CommercialRunEvidenceError("quality_event_location_mismatch")


def derive_bound_run_evidence_id(
    *,
    authority_evidence_id: str,
    decision: CommercialRunDecision,
    offers: Sequence[ValidatedOffer],
    supermarket_id: str,
    location_id: str,
    started_at_utc: datetime,
    finished_at_utc: datetime,
    products_observed: int,
    offers_observed: int,
    quality_events: Sequence[QualityEventRecord] = (),
) -> str:
    """Deriva un ID opaco que liga evidencia de autoridad con todo el input del run.

    El valor fuente de ``authority_evidence_id`` no sale en el resultado; sólo
    participa dentro del SHA-256. El fingerprint comercial reutiliza exactamente
    la semántica de replay de ``commercial_state`` para decisión + ofertas.
    """

    if not isinstance(decision, CommercialRunDecision):
        raise CommercialRunEvidenceError("commercial_decision_invalid")
    if isinstance(offers, (str, bytes)) or not isinstance(offers, Sequence):
        raise CommercialRunEvidenceError("offers_sequence_invalid")
    if isinstance(quality_events, (str, bytes)) or not isinstance(
        quality_events,
        Sequence,
    ):
        raise CommercialRunEvidenceError("quality_events_sequence_invalid")

    authority_id = _required_text(
        authority_evidence_id,
        "authority_evidence_id_invalid",
        max_length=_MAX_AUTHORITY_EVIDENCE_ID,
    )
    supermarket = _required_text(supermarket_id, "supermarket_id_invalid")
    location = _required_text(location_id, "location_id_invalid")
    started = _utc_text(started_at_utc, "started_at_utc_invalid")
    finished = _utc_text(finished_at_utc, "finished_at_utc_invalid")
    if finished_at_utc < started_at_utc:
        raise CommercialRunEvidenceError("run_time_order_invalid")
    products_count = _non_negative_int(products_observed, "products_observed_invalid")
    offers_count = _non_negative_int(offers_observed, "offers_observed_invalid")

    offer_values = tuple(offers)
    if any(not isinstance(item, ValidatedOffer) for item in offer_values):
        raise CommercialRunEvidenceError("validated_offer_type_invalid")
    event_values = tuple(quality_events)
    _validate_bound_context(
        decision=decision,
        offers=offer_values,
        quality_events=event_values,
        supermarket_id=supermarket,
        location_id=location,
    )
    event_payloads = tuple(_quality_event_payload(item) for item in event_values)

    try:
        commercial_fingerprint = _run_fingerprint(decision, offer_values)
    except Exception as exc:
        raise CommercialRunEvidenceError("commercial_run_fingerprint_failed") from exc

    payload = {
        "schema": _BINDING_SCHEMA,
        "authority_evidence_id": authority_id,
        "commercial_run_fingerprint": commercial_fingerprint,
        "supermarket_id": supermarket,
        "location_id": location,
        "started_at_utc": started,
        "finished_at_utc": finished,
        "products_observed": products_count,
        "offers_observed": offers_count,
        "quality_events": sorted(
            event_payloads,
            key=lambda value: str(value["quality_event_id"]),
        ),
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _BINDING_PREFIX + hashlib.sha256(serialized).hexdigest()


def reconcile_bound_durable_run_row(
    row: Mapping[str, Any],
    *,
    authority_evidence_id: str,
    decision: CommercialRunDecision,
    offers: Sequence[ValidatedOffer],
    supermarket_id: str,
    location_id: str,
    started_at_utc: datetime,
    finished_at_utc: datetime,
    products_observed: int,
    offers_observed: int,
    quality_events: Sequence[QualityEventRecord] = (),
) -> DurableCommercialReplay:
    """Reconoce un run autoritativo existente o falla ante cualquier divergencia.

    Esta función no crea un batch ni toca ``InMemoryCommercialState``. Su uso
    esperado es antes de reintentar una transición después de rehidratar un
    backend durable. El registro existente aporta los contadores de resultado; el
    binding demuestra que los inputs son exactamente los mismos.
    """

    if not isinstance(row, Mapping):
        raise CommercialRunEvidenceError("durable_run_row_invalid")
    if set(row) != set(FACT_SCRAPE_RUNS.columns):
        raise CommercialRunEvidenceError("durable_run_row_schema_mismatch")
    if not isinstance(decision, CommercialRunDecision):
        raise CommercialRunEvidenceError("commercial_decision_invalid")
    if not decision.catalog_accepted or not decision.commercial_update_allowed:
        raise CommercialRunEvidenceError("authoritative_commercial_decision_required")
    if decision.run_status not in {RunStatus.SUCCESS, RunStatus.WARNING}:
        raise CommercialRunEvidenceError("authoritative_commercial_decision_required")

    expected_evidence_id = derive_bound_run_evidence_id(
        authority_evidence_id=authority_evidence_id,
        decision=decision,
        offers=offers,
        supermarket_id=supermarket_id,
        location_id=location_id,
        started_at_utc=started_at_utc,
        finished_at_utc=finished_at_utc,
        products_observed=products_observed,
        offers_observed=offers_observed,
        quality_events=quality_events,
    )

    expected = {
        "scrape_run_id": decision.scrape_run_id,
        "supermarket_id": supermarket_id.strip(),
        "location_id": location_id.strip(),
        "run_status": decision.run_status.value,
        "catalog_accepted": True,
        "started_at_utc": _utc_text(started_at_utc, "started_at_utc_invalid"),
        "finished_at_utc": _utc_text(finished_at_utc, "finished_at_utc_invalid"),
        "products_observed": _non_negative_int(
            products_observed,
            "products_observed_invalid",
        ),
        "offers_observed": _non_negative_int(
            offers_observed,
            "offers_observed_invalid",
        ),
        "quality_event_count": len(tuple(quality_events)),
        "run_evidence_id": expected_evidence_id,
    }
    for field_name, expected_value in expected.items():
        if row.get(field_name) != expected_value:
            raise CommercialRunEvidenceError(f"durable_run_{field_name}_mismatch")

    return DurableCommercialReplay(
        scrape_run_id=decision.scrape_run_id,
        run_evidence_id=expected_evidence_id,
        current_created=_non_negative_int(
            row.get("current_created"),
            "durable_run_current_created_invalid",
        ),
        current_changed=_non_negative_int(
            row.get("current_changed"),
            "durable_run_current_changed_invalid",
        ),
        current_confirmed=_non_negative_int(
            row.get("current_confirmed"),
            "durable_run_current_confirmed_invalid",
        ),
        offers_ignored=_non_negative_int(
            row.get("offers_ignored"),
            "durable_run_offers_ignored_invalid",
        ),
        quality_event_count=_non_negative_int(
            row.get("quality_event_count"),
            "durable_run_quality_event_count_invalid",
        ),
    )
