"""Frontera offline entre una ejecución técnica y el estado comercial persistible.

No concede autoridad live ni implementa almacenamiento externo. Recibe una
decisión upstream y aplica de forma atómica/idempotente sólo runs comercialmente
aceptados. Una integración productiva DEBE derivar ``catalog_accepted`` del
collector autoritativo; un booleano de caller no sustituye esa evidencia.

Las ofertas ausentes del payload nunca se interpretan aquí como ``not_listed``,
``out_of_stock`` ni eliminación. Esas transiciones requieren evidencia explícita.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from .enums import ChangeType, RunStatus
from .identifiers import (
    canonicalize_text,
    canonicalize_url,
    generate_offer_id,
    generate_source_product_id,
    generate_state_hash,
)
from .models import ValidatedOffer

_STATE_FIELDS = (
    "current_price",
    "reported_regular_price",
    "is_promotion",
    "availability",
    "normalized_brand",
    "category",
    "subcategory",
    "variant",
    "unit_count",
    "content_per_unit",
    "measurement_unit",
    "total_content",
)
_PRODUCT_ATTRIBUTE_FIELDS = frozenset(
    {
        "normalized_brand",
        "category",
        "subcategory",
        "variant",
        "unit_count",
        "content_per_unit",
        "measurement_unit",
        "total_content",
    }
)
_COMMERCIAL_STATUSES = frozenset({RunStatus.SUCCESS, RunStatus.WARNING})


class CommercialStateError(ValueError):
    """Base para errores que deben abortar una transición comercial."""


class CommercialReplayConflict(CommercialStateError):
    """El mismo scrape_run_id reapareció con evidencia o decisión distinta."""


class OutOfOrderCommercialObservation(CommercialStateError):
    """Una observación aceptada intentó reescribir un estado posterior."""


@dataclass(frozen=True, slots=True)
class CommercialRunDecision:
    """Decisión upstream; no es por sí misma una prueba de autoridad live."""

    scrape_run_id: str
    run_status: RunStatus | str
    catalog_accepted: bool
    decided_at_utc: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.scrape_run_id, str) or not self.scrape_run_id.strip():
            raise CommercialStateError("scrape_run_id no puede estar vacío")
        object.__setattr__(self, "scrape_run_id", self.scrape_run_id.strip())
        try:
            status = (
                self.run_status
                if isinstance(self.run_status, RunStatus)
                else RunStatus(self.run_status)
            )
        except (TypeError, ValueError) as exc:
            raise CommercialStateError("run_status no es válido") from exc
        object.__setattr__(self, "run_status", status)
        if not isinstance(self.catalog_accepted, bool):
            raise CommercialStateError("catalog_accepted debe ser booleano")
        _require_utc(self.decided_at_utc, "decided_at_utc")

    @property
    def commercial_update_allowed(self) -> bool:
        """Sólo success/warning + catálogo aceptado puede mutar current/history."""

        return self.catalog_accepted and self.run_status in _COMMERCIAL_STATUSES


@dataclass(frozen=True, slots=True)
class CurrentCommercialOffer:
    validated_offer: ValidatedOffer
    first_observed_at_utc: datetime
    last_observed_at_utc: datetime
    last_scrape_run_id: str


@dataclass(frozen=True, slots=True)
class OfferHistoryPeriod:
    offer_history_id: str
    offer_id: str
    state_hash: str
    change_type: ChangeType
    changed_fields: tuple[str, ...]
    validated_offer: ValidatedOffer
    valid_from_utc: datetime
    valid_to_utc: datetime | None
    opened_by_scrape_run_id: str
    closed_by_scrape_run_id: str | None
    last_confirmed_by_scrape_run_id: str
    last_observed_at_utc: datetime


@dataclass(frozen=True, slots=True)
class ApplyRunResult:
    scrape_run_id: str
    commercial_update_allowed: bool
    replayed: bool
    mutated: bool
    current_created: int
    current_changed: int
    current_confirmed: int
    offers_ignored: int


class InMemoryCommercialState:
    """Motor de transición atómico/idempotente desacoplado del backend."""

    def __init__(self) -> None:
        self._current: dict[str, CurrentCommercialOffer] = {}
        self._history: dict[str, list[OfferHistoryPeriod]] = {}
        self._run_fingerprints: dict[str, str] = {}

    def current(self, offer_id: str) -> CurrentCommercialOffer | None:
        state = self._current.get(offer_id)
        return _snapshot_current(state) if state is not None else None

    def history(self, offer_id: str) -> tuple[OfferHistoryPeriod, ...]:
        return tuple(
            _snapshot_history_period(period)
            for period in self._history.get(offer_id, ())
        )

    @property
    def current_count(self) -> int:
        return len(self._current)

    @property
    def applied_run_count(self) -> int:
        return len(self._run_fingerprints)

    def apply_run(
        self,
        decision: CommercialRunDecision,
        offers: Iterable[ValidatedOffer],
    ) -> ApplyRunResult:
        """Aplica todo el run o nada; jamás infiere estados por ausencia."""

        values = tuple(offers)
        self._validate_run_payload(decision, values)
        fingerprint = _run_fingerprint(decision, values)
        previous_fingerprint = self._run_fingerprints.get(decision.scrape_run_id)
        if previous_fingerprint is not None:
            if previous_fingerprint != fingerprint:
                raise CommercialReplayConflict(
                    "scrape_run_id reutilizado con decisión o evidencia distinta"
                )
            return ApplyRunResult(
                scrape_run_id=decision.scrape_run_id,
                commercial_update_allowed=decision.commercial_update_allowed,
                replayed=True,
                mutated=False,
                current_created=0,
                current_changed=0,
                current_confirmed=0,
                offers_ignored=(0 if decision.commercial_update_allowed else len(values)),
            )

        if not decision.commercial_update_allowed:
            if decision.run_status is not RunStatus.RUNNING:
                self._run_fingerprints[decision.scrape_run_id] = fingerprint
            return ApplyRunResult(
                scrape_run_id=decision.scrape_run_id,
                commercial_update_allowed=False,
                replayed=False,
                mutated=False,
                current_created=0,
                current_changed=0,
                current_confirmed=0,
                offers_ignored=len(values),
            )

        staged_current = dict(self._current)
        staged_history = {key: list(periods) for key, periods in self._history.items()}
        identity_owners, source_keys = _current_identity_indexes(staged_current)
        created = changed = confirmed = 0

        for validated in values:
            offer = validated.offer
            offer_identity = _offer_logical_identity(validated)
            source_product_identity = _source_product_identity(validated)
            source_key_identity = _source_key_identity(validated)

            identity_owner = identity_owners.get(offer_identity)
            if identity_owner is not None and identity_owner != offer.offer_id:
                raise CommercialStateError(
                    "identidad lógica de oferta ya pertenece a otro offer_id"
                )
            known_source_key = source_keys.get(source_product_identity)
            if known_source_key is not None and known_source_key != source_key_identity:
                raise CommercialStateError(
                    "source_product_id cambió su source_key estable"
                )

            existing = staged_current.get(offer.offer_id)
            if existing is None:
                validated_snapshot = _snapshot_validated_offer(validated)
                identity_owners[offer_identity] = offer.offer_id
                source_keys[source_product_identity] = source_key_identity
                observed_at = offer.observed_at_utc
                staged_current[offer.offer_id] = CurrentCommercialOffer(
                    validated_offer=validated_snapshot,
                    first_observed_at_utc=observed_at,
                    last_observed_at_utc=observed_at,
                    last_scrape_run_id=decision.scrape_run_id,
                )
                staged_history[offer.offer_id] = [
                    _new_period(
                        validated_snapshot,
                        decision.scrape_run_id,
                        ChangeType.INITIAL,
                        (),
                    )
                ]
                created += 1
                continue

            if _offer_logical_identity(existing.validated_offer) != offer_identity:
                raise CommercialStateError(
                    f"offer_id {offer.offer_id} cambió su identidad lógica estable"
                )
            if _source_key_identity(existing.validated_offer) != source_key_identity:
                raise CommercialStateError(
                    f"offer_id {offer.offer_id} cambió su source_key estable"
                )
            if existing.validated_offer.offer.currency != offer.currency:
                raise CommercialStateError(
                    f"offer_id {offer.offer_id} cambió su moneda estable"
                )

            observed_at = offer.observed_at_utc
            if validated.state_hash == existing.validated_offer.state_hash:
                if observed_at < existing.last_observed_at_utc:
                    raise OutOfOrderCommercialObservation(
                        f"observación anterior para {offer.offer_id}"
                    )
                validated_snapshot = _snapshot_validated_offer(validated)
                staged_current[offer.offer_id] = replace(
                    existing,
                    validated_offer=validated_snapshot,
                    last_observed_at_utc=max(existing.last_observed_at_utc, observed_at),
                    last_scrape_run_id=decision.scrape_run_id,
                )
                periods = staged_history[offer.offer_id]
                open_period = _require_single_open_period(periods, offer.offer_id)
                periods[-1] = replace(
                    open_period,
                    last_confirmed_by_scrape_run_id=decision.scrape_run_id,
                    last_observed_at_utc=max(open_period.last_observed_at_utc, observed_at),
                )
                confirmed += 1
                continue

            if observed_at <= existing.last_observed_at_utc:
                raise OutOfOrderCommercialObservation(
                    f"cambio no monotónico para {offer.offer_id}"
                )

            changed_fields = _changed_fields(existing.validated_offer, validated)
            if not changed_fields:
                raise CommercialStateError(
                    "state_hash cambió sin diferencias en campos canónicos"
                )
            validated_snapshot = _snapshot_validated_offer(validated)
            change_type = _classify_change(changed_fields)
            periods = staged_history[offer.offer_id]
            open_period = _require_single_open_period(periods, offer.offer_id)
            periods[-1] = replace(
                open_period,
                valid_to_utc=observed_at,
                closed_by_scrape_run_id=decision.scrape_run_id,
            )
            periods.append(
                _new_period(
                    validated_snapshot,
                    decision.scrape_run_id,
                    change_type,
                    changed_fields,
                )
            )
            staged_current[offer.offer_id] = CurrentCommercialOffer(
                validated_offer=validated_snapshot,
                first_observed_at_utc=observed_at,
                last_observed_at_utc=observed_at,
                last_scrape_run_id=decision.scrape_run_id,
            )
            changed += 1

        self._current = staged_current
        self._history = staged_history
        self._run_fingerprints[decision.scrape_run_id] = fingerprint
        return ApplyRunResult(
            scrape_run_id=decision.scrape_run_id,
            commercial_update_allowed=True,
            replayed=False,
            mutated=bool(created or changed or confirmed),
            current_created=created,
            current_changed=changed,
            current_confirmed=confirmed,
            offers_ignored=0,
        )

    @staticmethod
    def _validate_run_payload(
        decision: CommercialRunDecision,
        offers: tuple[ValidatedOffer, ...],
    ) -> None:
        if decision.commercial_update_allowed and not offers:
            raise CommercialStateError(
                "un run comercialmente aceptado no puede aplicar un payload vacío"
            )
        offer_ids: set[str] = set()
        logical_identities: dict[tuple[str | None, ...], str] = {}
        source_keys: dict[tuple[str | None, ...], tuple[str, str]] = {}
        for validated in offers:
            if not isinstance(validated, ValidatedOffer):
                raise CommercialStateError("offers debe contener ValidatedOffer")
            offer = validated.offer
            if offer.scrape_run_id != decision.scrape_run_id:
                raise CommercialStateError(
                    "todas las ofertas deben pertenecer al scrape_run_id de la decisión"
                )
            if offer.offer_id in offer_ids:
                raise CommercialStateError("offer_id duplicado dentro del mismo run")
            offer_ids.add(offer.offer_id)

            expected_source_product_id = generate_source_product_id(
                offer.supermarket_id,
                offer.source_key_type,
                offer.source_key,
            )
            if offer.source_product_id != expected_source_product_id:
                raise CommercialStateError(
                    f"source_product_id no es determinista para {offer.offer_id}"
                )
            expected_offer_id = generate_offer_id(
                offer.supermarket_id,
                offer.location_id,
                expected_source_product_id,
            )
            if offer.offer_id != expected_offer_id:
                raise CommercialStateError(
                    f"offer_id no es determinista para {offer.source_product_id}"
                )

            offer_identity = _offer_logical_identity(validated)
            previous_offer_id = logical_identities.get(offer_identity)
            if previous_offer_id is not None and previous_offer_id != offer.offer_id:
                raise CommercialStateError(
                    "identidad lógica de oferta duplicada bajo offer_id distintos"
                )
            logical_identities[offer_identity] = offer.offer_id

            source_product_identity = _source_product_identity(validated)
            source_key_identity = _source_key_identity(validated)
            previous_source_key = source_keys.get(source_product_identity)
            if previous_source_key is not None and previous_source_key != source_key_identity:
                raise CommercialStateError(
                    "source_product_id asociado a source_key conflictivos dentro del run"
                )
            source_keys[source_product_identity] = source_key_identity

            if generate_state_hash(offer) != validated.state_hash:
                raise CommercialStateError(f"state_hash inválido para {offer.offer_id}")
            if validated.validated_at_utc < offer.observed_at_utc:
                raise CommercialStateError(
                    "validated_at_utc no puede ser anterior a observed_at_utc"
                )
            if validated.validated_at_utc > decision.decided_at_utc:
                raise CommercialStateError(
                    "validated_at_utc no puede ser posterior a decided_at_utc"
                )
            if offer.observed_at_utc > decision.decided_at_utc:
                raise CommercialStateError(
                    "una oferta no puede observarse después de decided_at_utc"
                )


def _require_utc(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise CommercialStateError(f"{field_name} debe incluir zona horaria UTC")
    if value.utcoffset().total_seconds() != 0:
        raise CommercialStateError(f"{field_name} debe expresarse en UTC")


def _clone_audit_value(value: object) -> object:
    """Copia recursivamente evidencia para romper referencias mutables externas."""

    if isinstance(value, Mapping):
        return {key: _clone_audit_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clone_audit_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_clone_audit_value(item) for item in value)
    if isinstance(value, set):
        return {_clone_audit_value(item) for item in value}
    if isinstance(value, frozenset):
        return frozenset(_clone_audit_value(item) for item in value)
    try:
        return deepcopy(value)
    except Exception as exc:  # pragma: no cover - depende de objetos caller-defined
        raise CommercialStateError("raw_values contiene evidencia no copiable") from exc


def _snapshot_validated_offer(validated: ValidatedOffer) -> ValidatedOffer:
    raw_values = _clone_audit_value(validated.offer.raw_values)
    if not isinstance(raw_values, Mapping):  # pragma: no cover - raíz validada por modelo
        raise CommercialStateError("raw_values debe conservar forma de mapping")
    offer = replace(validated.offer, raw_values=raw_values)
    return ValidatedOffer(
        offer=offer,
        state_hash=validated.state_hash,
        validated_at_utc=validated.validated_at_utc,
        quality_events=validated.quality_events,
    )


def _snapshot_current(state: CurrentCommercialOffer) -> CurrentCommercialOffer:
    return replace(
        state,
        validated_offer=_snapshot_validated_offer(state.validated_offer),
    )


def _snapshot_history_period(period: OfferHistoryPeriod) -> OfferHistoryPeriod:
    return replace(
        period,
        validated_offer=_snapshot_validated_offer(period.validated_offer),
    )


def _offer_logical_identity(validated: ValidatedOffer) -> tuple[str | None, ...]:
    """Componentes canónicos que determinan offer_id, sin exigir su hash exacto."""

    offer = validated.offer
    return (
        canonicalize_text(offer.supermarket_id),
        canonicalize_text(offer.location_id),
        canonicalize_text(offer.source_product_id),
    )


def _source_product_identity(validated: ValidatedOffer) -> tuple[str | None, ...]:
    offer = validated.offer
    return (
        canonicalize_text(offer.supermarket_id),
        canonicalize_text(offer.source_product_id),
    )


def _source_key_identity(validated: ValidatedOffer) -> tuple[str, str]:
    offer = validated.offer
    source_key = offer.source_key
    if offer.source_key_type.value == "stable_url":
        source_key = canonicalize_url(source_key)
    return (offer.source_key_type.value, source_key)


def _current_identity_indexes(
    current: dict[str, CurrentCommercialOffer],
) -> tuple[
    dict[tuple[str | None, ...], str],
    dict[tuple[str | None, ...], tuple[str, str]],
]:
    offer_owners: dict[tuple[str | None, ...], str] = {}
    source_keys: dict[tuple[str | None, ...], tuple[str, str]] = {}
    for offer_id, state in current.items():
        validated = state.validated_offer
        offer_identity = _offer_logical_identity(validated)
        previous_owner = offer_owners.get(offer_identity)
        if previous_owner is not None and previous_owner != offer_id:
            raise CommercialStateError(
                "estado current contiene identidad lógica duplicada"
            )
        offer_owners[offer_identity] = offer_id

        source_product_identity = _source_product_identity(validated)
        source_key_identity = _source_key_identity(validated)
        previous_source_key = source_keys.get(source_product_identity)
        if previous_source_key is not None and previous_source_key != source_key_identity:
            raise CommercialStateError(
                "estado current contiene source_key conflictivos para source_product_id"
            )
        source_keys[source_product_identity] = source_key_identity
    return offer_owners, source_keys


def _fingerprint_decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    rendered = format(value.normalize(), "f")
    return "0" if rendered in {"", "-0"} else rendered


def _validated_evidence_payload(item: ValidatedOffer) -> dict[str, object]:
    """Evidencia explícita que debe ser inmutable dentro de un scrape_run_id."""

    offer = item.offer
    return {
        "supermarket_id": offer.supermarket_id,
        "location_id": offer.location_id,
        "source_product_id": offer.source_product_id,
        "source_key_type": offer.source_key_type.value,
        "source_key": offer.source_key,
        "product_id": offer.product_id,
        "offer_id": offer.offer_id,
        "source_name": offer.source_name,
        "product_url": offer.product_url,
        "normalized_name": offer.normalized_name,
        "currency": offer.currency,
        "state_hash": item.state_hash,
        "location_status": offer.location_status.value,
        "observed_at_utc": offer.observed_at_utc.isoformat(),
        "validated_at_utc": item.validated_at_utc.isoformat(),
        "scrape_run_id": offer.scrape_run_id,
        "extractor_version": offer.extractor_version,
        "schema_version": offer.schema_version,
        "source_url": offer.source_url,
        "source_sku": offer.source_sku,
        "source_brand": offer.source_brand,
        "source_presentation": offer.source_presentation,
        "source_category": offer.source_category,
        "image_url": offer.image_url,
        "barcode": offer.barcode,
        "unit_price": _fingerprint_decimal(offer.unit_price),
        "unit_price_basis": offer.unit_price_basis,
        "location_evidence": offer.location_evidence,
        "location_confidence": _fingerprint_decimal(offer.location_confidence),
        "review_status": item.review_status.value,
        "pending_fields": list(offer.pending_fields),
        "quality_events": list(item.quality_events),
    }


def _run_fingerprint(
    decision: CommercialRunDecision,
    offers: tuple[ValidatedOffer, ...],
) -> str:
    payload = {
        "scrape_run_id": decision.scrape_run_id,
        "run_status": decision.run_status.value,
        "catalog_accepted": decision.catalog_accepted,
        "decided_at_utc": decision.decided_at_utc.isoformat(),
        "offers": sorted(
            (_validated_evidence_payload(item) for item in offers),
            key=lambda value: str(value["offer_id"]),
        ),
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _history_id(offer_id: str, state_hash: str, valid_from: datetime) -> str:
    payload = f"{offer_id}\x1f{state_hash}\x1f{valid_from.isoformat()}"
    return "oh_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _new_period(
    validated: ValidatedOffer,
    scrape_run_id: str,
    change_type: ChangeType,
    changed_fields: tuple[str, ...],
) -> OfferHistoryPeriod:
    offer = validated.offer
    observed_at = offer.observed_at_utc
    return OfferHistoryPeriod(
        offer_history_id=_history_id(offer.offer_id, validated.state_hash, observed_at),
        offer_id=offer.offer_id,
        state_hash=validated.state_hash,
        change_type=change_type,
        changed_fields=changed_fields,
        validated_offer=validated,
        valid_from_utc=observed_at,
        valid_to_utc=None,
        opened_by_scrape_run_id=scrape_run_id,
        closed_by_scrape_run_id=None,
        last_confirmed_by_scrape_run_id=scrape_run_id,
        last_observed_at_utc=observed_at,
    )


def _require_single_open_period(
    periods: list[OfferHistoryPeriod],
    offer_id: str,
) -> OfferHistoryPeriod:
    if not periods or periods[-1].valid_to_utc is not None:
        raise CommercialStateError(f"histórico sin periodo abierto para {offer_id}")
    if sum(period.valid_to_utc is None for period in periods) != 1:
        raise CommercialStateError(f"histórico con múltiples periodos abiertos para {offer_id}")
    return periods[-1]


def _comparable(value: object) -> object:
    if hasattr(value, "value"):
        value = value.value
    if isinstance(value, Decimal):
        return value.normalize()
    if isinstance(value, str):
        return canonicalize_text(value)
    return value


def _changed_fields(
    previous: ValidatedOffer,
    current: ValidatedOffer,
) -> tuple[str, ...]:
    before = previous.offer
    after = current.offer
    return tuple(
        field_name
        for field_name in _STATE_FIELDS
        if _comparable(getattr(before, field_name))
        != _comparable(getattr(after, field_name))
    )


def _classify_change(changed_fields: tuple[str, ...]) -> ChangeType:
    categories: set[ChangeType] = set()
    for field_name in changed_fields:
        if field_name == "current_price":
            categories.add(ChangeType.PRICE)
        elif field_name == "reported_regular_price":
            categories.add(ChangeType.REGULAR_PRICE)
        elif field_name == "is_promotion":
            categories.add(ChangeType.PROMOTION)
        elif field_name == "availability":
            categories.add(ChangeType.AVAILABILITY)
        elif field_name in _PRODUCT_ATTRIBUTE_FIELDS:
            categories.add(ChangeType.PRODUCT_ATTRIBUTE)
        else:  # pragma: no cover - _STATE_FIELDS es cerrado
            raise CommercialStateError(f"campo de estado no clasificado: {field_name}")
    if len(categories) == 1:
        return next(iter(categories))
    return ChangeType.MULTIPLE
