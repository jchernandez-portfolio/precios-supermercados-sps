"""Modelo offline fail-closed para una futura frontera live de La Colonia.

No abre sockets ni concede autoridad productiva. La implementación linealizable
permite probar las invariantes que un broker/enforcer real deberá preservar.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import math
import re
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

MINIMUM_PHYSICAL_START_DELAY_SECONDS = 1.5
REQUEST_SCHEMA_VERSION = "1"
_REQUEST_DOMAIN = b"precios-sps/la-colonia/live-request/v1\0"
_SHA = re.compile(r"[0-9a-f]{40}\Z")


def _finite_number(value: object, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} debe ser numérico y no booleano")
    numeric = float(value)
    if not math.isfinite(numeric) or (positive and numeric <= 0):
        qualifier = "finito y positivo" if positive else "finito"
        raise ValueError(f"{label} debe ser {qualifier}")
    return numeric


def _exact_int(value: object, expected: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value != expected:
        raise ValueError(f"{label} debe ser exactamente {expected}")


class SafetyViolation(RuntimeError):
    """Una transición solicitada viola una invariante fail-closed."""


class SpsTechnicalContextStatus(StrEnum):
    CONFIRMED = "confirmed"
    UNCONFIRMED = "unconfirmed"
    UNAVAILABLE = "unavailable"


class GrantState(StrEnum):
    ACTIVE = "active"
    CONSUMED = "consumed"
    REVOKED = "revoked"


class ReservationState(StrEnum):
    RESERVED = "reserved"
    ACTIVATED = "activated"
    CLOSING = "closing"
    CLOSED = "closed"
    UNCERTAIN = "uncertain"
    FENCING_REQUIRED = "fencing_required"
    FENCED = "fenced"


class StopReason(StrEnum):
    CONTINUE = "continue"
    HTTP_403 = "http_403"
    HTTP_429 = "http_429"
    CAPTCHA = "captcha_or_antibot"
    AUTH_OR_LOCATION = "auth_address_or_gps_required"
    EXCESSIVE_LOAD = "excessive_load"
    REDIRECT = "redirect"
    UNKNOWN = "unknown"


def classify_stop(*, status: int, body: str = "", load_excessive: bool = False) -> StopReason:
    """Política cerrada: cualquier señal ambigua o bloqueante detiene."""

    lowered = body.casefold()
    if load_excessive:
        return StopReason.EXCESSIVE_LOAD
    if status == 403:
        return StopReason.HTTP_403
    if status == 429:
        return StopReason.HTTP_429
    if 300 <= status < 400:
        return StopReason.REDIRECT
    if any(marker in lowered for marker in ("captcha", "antibot", "robot check")):
        return StopReason.CAPTCHA
    if any(marker in lowered for marker in ("iniciar sesión", "login required", "dirección requerida", "gps required")):
        return StopReason.AUTH_OR_LOCATION
    if 200 <= status < 300:
        return StopReason.CONTINUE
    return StopReason.UNKNOWN


@dataclass(frozen=True, slots=True)
class ClosedBudget:
    max_connections: int = 1
    max_http_requests: int = 1
    max_final_responses: int = 1
    max_retries: int = 0
    activation_deadline_seconds: float = 5.0
    connect_deadline_seconds: float = 10.0
    tls_deadline_seconds: float = 10.0
    first_byte_deadline_seconds: float = 20.0
    response_deadline_seconds: float = 30.0
    hard_reservation_deadline_seconds: float = 45.0

    def __post_init__(self) -> None:
        _exact_int(self.max_connections, 1, "max_connections")
        _exact_int(self.max_http_requests, 1, "max_http_requests")
        _exact_int(self.max_final_responses, 1, "max_final_responses")
        _exact_int(self.max_retries, 0, "max_retries")
        for label in (
            "activation_deadline_seconds",
            "connect_deadline_seconds",
            "tls_deadline_seconds",
            "first_byte_deadline_seconds",
            "response_deadline_seconds",
            "hard_reservation_deadline_seconds",
        ):
            _finite_number(getattr(self, label), label, positive=True)

    def canonical(self) -> dict[str, int | float]:
        return {
            "activation_deadline_seconds": float(self.activation_deadline_seconds),
            "connect_deadline_seconds": float(self.connect_deadline_seconds),
            "first_byte_deadline_seconds": float(self.first_byte_deadline_seconds),
            "hard_reservation_deadline_seconds": float(self.hard_reservation_deadline_seconds),
            "max_connections": self.max_connections,
            "max_final_responses": self.max_final_responses,
            "max_http_requests": self.max_http_requests,
            "max_retries": self.max_retries,
            "response_deadline_seconds": float(self.response_deadline_seconds),
            "tls_deadline_seconds": float(self.tls_deadline_seconds),
        }


@dataclass(frozen=True, slots=True)
class ImmutableLiveRequest:
    request_id: str
    approved_commit_sha: str
    plan: str
    epoch: int
    budget: ClosedBudget = field(default_factory=ClosedBudget)
    schema_version: str = REQUEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != REQUEST_SCHEMA_VERSION:
            raise ValueError("schema_version no soportada")
        if not self.request_id.strip() or not self.plan.strip():
            raise ValueError("request_id y plan son obligatorios")
        if not _SHA.fullmatch(self.approved_commit_sha):
            raise ValueError("approved_commit_sha debe ser SHA-1 hexadecimal inmutable")
        if isinstance(self.epoch, bool) or not isinstance(self.epoch, int) or self.epoch < 0:
            raise ValueError("epoch no puede ser negativo")

    def canonical_bytes(self) -> bytes:
        payload = {
            "approved_commit_sha": self.approved_commit_sha,
            "budget": self.budget.canonical(),
            "epoch": self.epoch,
            "plan": self.plan,
            "request_id": self.request_id,
            "schema_version": self.schema_version,
        }
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    @property
    def digest(self) -> str:
        return hashlib.sha256(_REQUEST_DOMAIN + self.canonical_bytes()).hexdigest()


@dataclass(frozen=True, slots=True)
class Grant:
    grant_id: str
    request_digest: str
    epoch: int
    state: GrantState = GrantState.ACTIVE


@dataclass(frozen=True, slots=True)
class Reservation:
    reservation_id: str
    grant_id: str
    request_digest: str
    epoch: int
    budget: ClosedBudget
    reserved_at: float
    last_transition_at: float
    state: ReservationState = ReservationState.RESERVED
    physical_started_at: float | None = None
    connected_at: float | None = None
    tls_completed_at: float | None = None
    request_sent_at: float | None = None
    first_byte_at: float | None = None
    start_observed: bool = False
    connections: int = 0
    requests: int = 0
    final_responses: int = 0
    release_kind: str | None = None


@dataclass(slots=True)
class _GrantRecord:
    grant_id: str
    request_digest: str
    epoch: int
    state: GrantState = GrantState.ACTIVE

    def snapshot(self) -> Grant:
        return Grant(self.grant_id, self.request_digest, self.epoch, self.state)


@dataclass(slots=True)
class _ReservationRecord:
    reservation_id: str
    grant_id: str
    request_digest: str
    epoch: int
    budget: ClosedBudget
    reserved_at: float
    last_transition_at: float
    state: ReservationState = ReservationState.RESERVED
    physical_started_at: float | None = None
    connected_at: float | None = None
    tls_completed_at: float | None = None
    request_sent_at: float | None = None
    first_byte_at: float | None = None
    start_observed: bool = False
    connections: int = 0
    requests: int = 0
    final_responses: int = 0
    release_kind: str | None = None

    def snapshot(self) -> Reservation:
        return Reservation(**{name: getattr(self, name) for name in Reservation.__dataclass_fields__})


@dataclass(frozen=True, slots=True)
class FencingEvidence:
    reservation_id: str
    epoch: int
    request_digest: str
    phase: str
    physical_closed_at: float
    source: str
    _issuer: object = field(repr=False, compare=False)
    _seal: str = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class PhysicalStartEvidence:
    reservation_id: str
    epoch: int
    request_digest: str
    physical_started_at: float
    _issuer: object = field(repr=False, compare=False)
    _seal: str = field(repr=False, compare=False)


class IndependentFencingObserver:
    """Simulador inyectado de una fuente externa de cierre físico.

    No es evidencia productiva y nunca puede cerrar GATE-17. El nombre histórico
    se conserva para no romper imports, pero la API explicita que sólo simula una
    observación de cierre para pruebas offline.
    """

    def __init__(self) -> None:
        self._issuer = object()
        self._seal_key = secrets.token_bytes(32)

    def _seal(self, kind: str, *values: object) -> str:
        payload = json.dumps(
            [kind, *values], separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        return hmac.new(self._seal_key, payload, hashlib.sha256).hexdigest()

    def _valid_start(self, evidence: PhysicalStartEvidence) -> bool:
        try:
            if not isinstance(evidence._seal, str):
                return False
            expected = self._seal(
                "start",
                evidence.reservation_id,
                evidence.epoch,
                evidence.request_digest,
                evidence.physical_started_at,
            )
            return hmac.compare_digest(evidence._seal, expected)
        except (TypeError, ValueError):
            return False

    def _valid_closed(self, evidence: FencingEvidence) -> bool:
        try:
            if not isinstance(evidence._seal, str):
                return False
            expected = self._seal(
                "closed",
                evidence.reservation_id,
                evidence.epoch,
                evidence.request_digest,
                evidence.phase,
                evidence.physical_closed_at,
                evidence.source,
            )
            return hmac.compare_digest(evidence._seal, expected)
        except (TypeError, ValueError):
            return False

    def observe_closed_for_offline_test(
        self,
        reservation_id: str,
        *,
        epoch: int,
        request_digest: str,
        phase: str,
        observed_at: float,
        source: str,
    ) -> FencingEvidence:
        if (
            not reservation_id
            or isinstance(epoch, bool)
            or not isinstance(epoch, int)
            or epoch < 0
            or not re.fullmatch(r"[0-9a-f]{64}", request_digest)
            or phase not in {"normal", "fencing"}
            or not source.strip()
        ):
            raise ValueError("Evidencia de fencing inválida")
        try:
            timestamp = _finite_number(observed_at, "observed_at")
        except ValueError as exc:
            raise ValueError("Evidencia de fencing inválida") from exc
        if timestamp < 0:
            raise ValueError("Evidencia de fencing inválida")
        values = (
            reservation_id,
            epoch,
            request_digest,
            phase,
            timestamp,
            source.strip(),
            self._issuer,
        )
        return FencingEvidence(
            *values,
            self._seal("closed", *values[:-1]),
        )

    def observe_started_for_offline_test(
        self,
        reservation_id: str,
        *,
        epoch: int,
        request_digest: str,
        observed_at: float,
    ) -> PhysicalStartEvidence:
        if (
            not reservation_id
            or isinstance(epoch, bool)
            or not isinstance(epoch, int)
            or epoch < 0
            or not re.fullmatch(r"[0-9a-f]{64}", request_digest)
        ):
            raise ValueError("Evidencia de inicio físico inválida")
        try:
            timestamp = _finite_number(observed_at, "observed_at")
        except ValueError as exc:
            raise ValueError("Evidencia de inicio físico inválida") from exc
        if timestamp < 0:
            raise ValueError("Evidencia de inicio físico inválida")
        values = (
            reservation_id,
            epoch,
            request_digest,
            timestamp,
            self._issuer,
        )
        return PhysicalStartEvidence(
            *values,
            self._seal("start", *values[:-1]),
        )


class LinearizableAuthority:
    """Repositorio en memoria con una única sección crítica para transiciones."""

    def __init__(
        self,
        fencing_observer: IndependentFencingObserver | None = None,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._grants: dict[str, _GrantRecord] = {}
        self._issued_request_digests: set[str] = set()
        self._reservations: dict[str, _ReservationRecord] = {}
        self._epoch = 0
        self._active_reservation_id: str | None = None
        self._physical_in_flight = 0
        self._last_physical_start: float | None = None
        self._next_start_not_before: float = 0.0
        self._last_observed_at: float = 0.0
        self._raw_monotonic = time.monotonic
        self._clock_offset: float | None = None
        self._uses_default_clock = clock is None
        self._clock = clock or self._default_monitor_clock
        self._fencing_issuer = (
            fencing_observer._issuer if fencing_observer is not None else object()
        )
        self._fencing_observer = fencing_observer

    @property
    def epoch(self) -> int:
        with self._lock:
            self._guard_active_deadline()
            return self._epoch

    @property
    def active_reservation_id(self) -> str | None:
        with self._lock:
            self._guard_active_deadline()
            return self._active_reservation_id

    @property
    def physical_in_flight(self) -> int:
        with self._lock:
            self._guard_active_deadline()
            return self._physical_in_flight

    @property
    def last_physical_start(self) -> float | None:
        with self._lock:
            self._guard_active_deadline()
            return self._last_physical_start

    @property
    def next_start_not_before(self) -> float:
        with self._lock:
            self._guard_active_deadline()
            return self._next_start_not_before

    def issue_for_offline_test(self, grant_id: str, request: ImmutableLiveRequest) -> Grant:
        """Crea autoridad sintética; no está conectado a ningún runtime real."""

        with self._lock:
            self._guard_active_deadline()
            if type(request) is not ImmutableLiveRequest:
                raise SafetyViolation("Request tipado obligatorio")
            request_digest = request.digest
            if (
                not grant_id
                or request.epoch != self._epoch
                or grant_id in self._grants
                or request_digest in self._issued_request_digests
            ):
                raise SafetyViolation("Grant duplicado o epoch stale")
            grant = _GrantRecord(grant_id, request_digest, request.epoch)
            self._grants[grant_id] = grant
            self._issued_request_digests.add(request_digest)
            return grant.snapshot()

    def reserve(
        self,
        grant_id: str,
        request: ImmutableLiveRequest,
        reservation_id: str,
        *,
        now: float,
    ) -> Reservation:
        with self._lock:
            self._guard_active_deadline()
            if type(request) is not ImmutableLiveRequest:
                raise SafetyViolation("Request tipado obligatorio")
            self._validate_initial_time(now)
            grant = self._grants.get(grant_id)
            if (
                grant is None
                or grant.state is not GrantState.ACTIVE
                or grant.epoch != self._epoch
                or request.epoch != self._epoch
                or grant.request_digest != request.digest
                or self._active_reservation_id is not None
                or not reservation_id
                or reservation_id in self._reservations
            ):
                raise SafetyViolation("Reserva denegada")
            grant.state = GrantState.CONSUMED
            reservation = _ReservationRecord(
                reservation_id,
                grant_id,
                request.digest,
                self._epoch,
                request.budget,
                now,
                now,
            )
            self._reservations[reservation_id] = reservation
            self._active_reservation_id = reservation_id
            if self._uses_default_clock:
                self._clock_offset = now - self._raw_monotonic()
            self._last_observed_at = now
            return reservation.snapshot()

    def activate(
        self,
        reservation_id: str,
        *,
        now: float,
        evidence: PhysicalStartEvidence | None,
    ) -> None:
        with self._lock:
            self._guard_active_deadline()
            reservation = self._reservation(reservation_id, ReservationState.RESERVED)
            self._validate_transition_time(reservation, now)
            self._enforce_deadline(
                reservation,
                now,
                reservation.reserved_at + reservation.budget.activation_deadline_seconds,
                "activation_deadline",
            )
            if reservation.epoch != self._epoch or now < self._next_start_not_before:
                raise SafetyViolation("Activación stale o viola pacing")
            if self._last_physical_start is not None and now - self._last_physical_start < MINIMUM_PHYSICAL_START_DELAY_SECONDS:
                raise SafetyViolation("Inicio físico antes de 1.5 segundos")
            self._physical_in_flight = 1
            if evidence is None:
                reservation.state = ReservationState.UNCERTAIN
                reservation.start_observed = False
                self._record_time(reservation, now)
                return
            if (
                type(evidence) is not PhysicalStartEvidence
                or evidence._issuer is not self._fencing_issuer
                or self._fencing_observer is None
                or not self._fencing_observer._valid_start(evidence)
                or evidence.reservation_id != reservation.reservation_id
                or evidence.epoch != reservation.epoch
                or evidence.request_digest != reservation.request_digest
                or evidence.physical_started_at != now
            ):
                reservation.state = ReservationState.UNCERTAIN
                self._record_time(reservation, now)
                raise SafetyViolation("Inicio exige evidencia del simulador inyectado")
            reservation.state = ReservationState.ACTIVATED
            reservation.start_observed = True
            reservation.physical_started_at = now
            self._record_time(reservation, now)
            self._last_physical_start = now

    def open_connection(self, reservation_id: str, *, now: float) -> None:
        with self._lock:
            self._guard_active_deadline()
            reservation = self._reservation(reservation_id, ReservationState.ACTIVATED)
            self._validate_transition_time(reservation, now)
            assert reservation.physical_started_at is not None
            self._enforce_deadline(
                reservation,
                now,
                reservation.physical_started_at + reservation.budget.connect_deadline_seconds,
                "connect_deadline",
            )
            if reservation.connections != 0:
                raise SafetyViolation("Una reserva no puede abrir segunda conexión")
            reservation.connections = 1
            reservation.connected_at = now
            self._record_time(reservation, now)

    def complete_tls(self, reservation_id: str, *, now: float) -> None:
        with self._lock:
            self._guard_active_deadline()
            reservation = self._reservation(reservation_id, ReservationState.ACTIVATED)
            self._validate_transition_time(reservation, now)
            if reservation.connections != 1 or reservation.connected_at is None:
                raise SafetyViolation("TLS sin conexión única")
            self._enforce_deadline(
                reservation,
                now,
                reservation.connected_at + reservation.budget.tls_deadline_seconds,
                "tls_deadline",
            )
            if reservation.tls_completed_at is not None:
                raise SafetyViolation("Segundo handshake TLS")
            reservation.tls_completed_at = now
            self._record_time(reservation, now)

    def send_http_request(
        self,
        reservation_id: str,
        *,
        method: str = "GET",
        redirect: bool = False,
        upgrade: bool = False,
        now: float,
    ) -> None:
        with self._lock:
            self._guard_active_deadline()
            reservation = self._reservation(reservation_id, ReservationState.ACTIVATED)
            self._validate_transition_time(reservation, now)
            self._enforce_hard_deadline(reservation, now)
            if (
                reservation.connections != 1
                or reservation.tls_completed_at is None
                or reservation.requests != 0
            ):
                raise SafetyViolation("Request sin conexión única o segundo request")
            if method != "GET" or redirect or upgrade:
                raise SafetyViolation("Intercambio HTTP no permitido")
            reservation.requests = 1
            reservation.request_sent_at = now
            self._record_time(reservation, now)

    def receive_first_byte(self, reservation_id: str, *, now: float) -> None:
        with self._lock:
            self._guard_active_deadline()
            reservation = self._reservation(reservation_id, ReservationState.ACTIVATED)
            self._validate_transition_time(reservation, now)
            if reservation.requests != 1 or reservation.request_sent_at is None:
                raise SafetyViolation("First byte sin request")
            self._enforce_deadline(
                reservation,
                now,
                reservation.request_sent_at + reservation.budget.first_byte_deadline_seconds,
                "first_byte_deadline",
            )
            if reservation.first_byte_at is not None:
                raise SafetyViolation("First byte duplicado")
            reservation.first_byte_at = now
            self._record_time(reservation, now)

    def receive_final_response(
        self, reservation_id: str, *, now: float, final: bool = True
    ) -> None:
        with self._lock:
            self._guard_active_deadline()
            reservation = self._reservation(reservation_id, ReservationState.ACTIVATED)
            self._validate_transition_time(reservation, now)
            if not final or reservation.first_byte_at is None or reservation.final_responses != 0:
                raise SafetyViolation("Respuesta no final o duplicada")
            self._enforce_deadline(
                reservation,
                now,
                reservation.first_byte_at + reservation.budget.response_deadline_seconds,
                "response_deadline",
            )
            reservation.final_responses = 1
            self._record_time(reservation, now)

    def begin_closing(self, reservation_id: str, *, now: float) -> None:
        with self._lock:
            self._guard_active_deadline()
            reservation = self._reservation(reservation_id, ReservationState.ACTIVATED)
            self._validate_transition_time(reservation, now)
            self._enforce_hard_deadline(reservation, now)
            if (reservation.connections, reservation.requests, reservation.final_responses) != (1, 1, 1):
                raise SafetyViolation("Intercambio físico incompleto")
            reservation.state = ReservationState.CLOSING
            self._record_time(reservation, now)

    def confirm_physical_closed(
        self, reservation_id: str, *, evidence: FencingEvidence
    ) -> None:
        with self._lock:
            self._guard_active_deadline()
            reservation = self._reservation(reservation_id, ReservationState.CLOSING)
            self._validate_closure_evidence(reservation, evidence, expected_phase="normal")
            self._guard_current_deadline(reservation, evidence.physical_closed_at)
            self._record_time(reservation, evidence.physical_closed_at)
            self._release(reservation, ReservationState.CLOSED, "normal")

    def expire(self, reservation_id: str, *, now: float) -> bool:
        """Monitoriza una reserva sin depender de que llegue otra transición."""

        with self._lock:
            self._guard_active_deadline()
            existing = self._reservations.get(reservation_id)
            if existing is not None and existing.state is ReservationState.UNCERTAIN:
                return True
            reservation = self._reservation(
                reservation_id,
                ReservationState.RESERVED,
                ReservationState.ACTIVATED,
                ReservationState.CLOSING,
            )
            self._validate_transition_time(reservation, now)
            deadline, label = self._current_deadline(reservation)
            if now <= deadline:
                return False
            reservation.state = ReservationState.UNCERTAIN
            self._record_time(reservation, now)
            return True

    def deadline_expired(self, reservation_id: str, *, now: float) -> None:
        """Alias fail-closed: sólo acepta expiración demostrada por reloj."""

        if not self.expire(reservation_id, now=now):
            raise SafetyViolation("Deadline todavía vigente")

    def require_fencing(self, reservation_id: str) -> None:
        with self._lock:
            self._guard_active_deadline()
            reservation = self._reservation(reservation_id, ReservationState.UNCERTAIN)
            reservation.state = ReservationState.FENCING_REQUIRED

    def confirm_fenced_closed(
        self, reservation_id: str, *, evidence: FencingEvidence
    ) -> None:
        with self._lock:
            self._guard_active_deadline()
            reservation = self._reservation(reservation_id, ReservationState.FENCING_REQUIRED)
            self._validate_closure_evidence(reservation, evidence, expected_phase="fencing")
            if not reservation.start_observed:
                self._next_start_not_before = max(
                    self._next_start_not_before,
                    evidence.physical_closed_at + MINIMUM_PHYSICAL_START_DELAY_SECONDS,
                )
            self._record_time(reservation, evidence.physical_closed_at)
            self._release(reservation, ReservationState.FENCED, "fencing")

    def revoke(self) -> int:
        with self._lock:
            # Emergency revocation deliberately does not consult the clock: even
            # a throwing clock backend must not preserve authority or ACTIVE grants.
            self._epoch += 1
            for grant in self._grants.values():
                if grant.state is GrantState.ACTIVE:
                    grant.state = GrantState.REVOKED
            if self._active_reservation_id is not None:
                reservation = self._reservations[self._active_reservation_id]
                if reservation.state in {ReservationState.RESERVED, ReservationState.ACTIVATED, ReservationState.CLOSING}:
                    reservation.state = ReservationState.UNCERTAIN
            return self._epoch

    def snapshot(self, reservation_id: str) -> Mapping[str, Any]:
        with self._lock:
            self._guard_active_deadline()
            reservation = self._reservations[reservation_id]
            return MappingProxyType({
                "state": reservation.state.value,
                "physical_in_flight": self._physical_in_flight,
                "connections": reservation.connections,
                "requests": reservation.requests,
                "final_responses": reservation.final_responses,
                "release_kind": reservation.release_kind,
            })

    def grant_snapshot(self, grant_id: str) -> Grant:
        with self._lock:
            self._guard_active_deadline()
            try:
                return self._grants[grant_id].snapshot()
            except KeyError as exc:
                raise SafetyViolation("Grant desconocido") from exc

    def reservation_snapshot(self, reservation_id: str) -> Reservation:
        with self._lock:
            self._guard_active_deadline()
            try:
                return self._reservations[reservation_id].snapshot()
            except KeyError as exc:
                raise SafetyViolation("Reserva desconocida") from exc

    def _reservation(self, reservation_id: str, *states: ReservationState) -> _ReservationRecord:
        reservation = self._reservations.get(reservation_id)
        if reservation is None or reservation.state not in states:
            raise SafetyViolation("Transición de reserva inválida")
        return reservation

    def _release(self, reservation: _ReservationRecord, target: ReservationState, kind: str) -> None:
        allowed_physical_states = {1} if kind == "normal" else {0, 1}
        if (
            self._physical_in_flight not in allowed_physical_states
            or self._active_reservation_id != reservation.reservation_id
        ):
            raise SafetyViolation("Liberación duplicada o sin autoridad física")
        reservation.state = target
        reservation.release_kind = kind
        self._physical_in_flight = 0
        self._active_reservation_id = None

    def _enforce_hard_deadline(self, reservation: _ReservationRecord, now: float) -> None:
        self._enforce_deadline(
            reservation,
            now,
            reservation.reserved_at + reservation.budget.hard_reservation_deadline_seconds,
            "hard_reservation_deadline",
        )

    def _validate_initial_time(self, now: float) -> None:
        if (
            isinstance(now, bool)
            or not isinstance(now, (int, float))
            or not math.isfinite(float(now))
            or now < self._last_observed_at
        ):
            raise SafetyViolation("Tiempo de reserva inválido")

    def _validate_transition_time(self, reservation: _ReservationRecord, now: float) -> None:
        if (
            isinstance(now, bool)
            or not isinstance(now, (int, float))
            or not math.isfinite(float(now))
            or now < reservation.last_transition_at
            or now < self._last_observed_at
        ):
            raise SafetyViolation("Tiempo no finito o no monotónico")

    def _default_monitor_clock(self) -> float:
        if self._clock_offset is None:
            return self._last_observed_at
        return self._raw_monotonic() + self._clock_offset

    def _record_time(self, reservation: _ReservationRecord, now: float) -> None:
        reservation.last_transition_at = now
        self._last_observed_at = now
        if self._uses_default_clock:
            candidate = now - self._raw_monotonic()
            self._clock_offset = (
                candidate
                if self._clock_offset is None
                else max(self._clock_offset, candidate)
            )

    def _enforce_deadline(
        self, reservation: _ReservationRecord, now: float, deadline: float, label: str
    ) -> None:
        hard = reservation.reserved_at + reservation.budget.hard_reservation_deadline_seconds
        if now > min(deadline, hard):
            reservation.state = ReservationState.UNCERTAIN
            self._record_time(reservation, now)
            raise SafetyViolation(f"{label} excedido; fencing requerido")

    def _current_deadline(self, reservation: _ReservationRecord) -> tuple[float, str]:
        hard = reservation.reserved_at + reservation.budget.hard_reservation_deadline_seconds
        if reservation.state is ReservationState.RESERVED:
            return min(hard, reservation.reserved_at + reservation.budget.activation_deadline_seconds), "activation_deadline"
        if reservation.state is ReservationState.CLOSING:
            return hard, "hard_reservation_deadline"
        if reservation.connections == 0:
            assert reservation.physical_started_at is not None
            return min(hard, reservation.physical_started_at + reservation.budget.connect_deadline_seconds), "connect_deadline"
        if reservation.tls_completed_at is None:
            assert reservation.connected_at is not None
            return min(hard, reservation.connected_at + reservation.budget.tls_deadline_seconds), "tls_deadline"
        if reservation.requests == 0:
            return hard, "hard_reservation_deadline"
        if reservation.first_byte_at is None:
            assert reservation.request_sent_at is not None
            return min(hard, reservation.request_sent_at + reservation.budget.first_byte_deadline_seconds), "first_byte_deadline"
        if reservation.final_responses == 0:
            return min(hard, reservation.first_byte_at + reservation.budget.response_deadline_seconds), "response_deadline"
        return hard, "hard_reservation_deadline"

    def _guard_current_deadline(self, reservation: _ReservationRecord, now: float) -> None:
        self._validate_transition_time(reservation, now)
        deadline, label = self._current_deadline(reservation)
        self._enforce_deadline(reservation, now, deadline, label)

    def _guard_active_deadline(self) -> None:
        if self._active_reservation_id is None:
            return
        reservation = self._reservations[self._active_reservation_id]
        if reservation.state not in {
            ReservationState.RESERVED,
            ReservationState.ACTIVATED,
            ReservationState.CLOSING,
        }:
            return
        try:
            now = self._clock()
        except Exception as exc:
            reservation.state = ReservationState.UNCERTAIN
            raise SafetyViolation("Clock del monitor no disponible") from exc
        if (
            isinstance(now, bool)
            or not isinstance(now, (int, float))
            or not math.isfinite(float(now))
            or now < reservation.last_transition_at
            or now < self._last_observed_at
        ):
            reservation.state = ReservationState.UNCERTAIN
            raise SafetyViolation("Clock del monitor no finito o no monotónico")
        deadline, _ = self._current_deadline(reservation)
        if now > deadline:
            reservation.state = ReservationState.UNCERTAIN
            self._record_time(reservation, float(now))

    def _validate_closure_evidence(
        self,
        reservation: _ReservationRecord,
        evidence: FencingEvidence,
        *,
        expected_phase: str,
    ) -> None:
        if (
            type(evidence) is not FencingEvidence
            or evidence._issuer is not self._fencing_issuer
            or self._fencing_observer is None
            or not self._fencing_observer._valid_closed(evidence)
            or evidence.reservation_id != reservation.reservation_id
            or evidence.epoch != reservation.epoch
            or evidence.request_digest != reservation.request_digest
            or evidence.phase != expected_phase
            or not math.isfinite(evidence.physical_closed_at)
            or evidence.physical_closed_at < reservation.last_transition_at
            or evidence.physical_closed_at < reservation.reserved_at
            or evidence.physical_closed_at < self._last_observed_at
        ):
            raise SafetyViolation("Cierre exige evidencia del simulador inyectado")


NETWORK_POLICY_SCHEMA_VERSION = "1"
_NETWORK_BOOLEAN_CONTROLS = frozenset(
    {
        "dns_fallback_disabled",
        "happy_eyeballs_disabled",
        "proxy_disabled",
        "environment_proxy_disabled",
        "redirects_disabled",
        "connection_pooling_disabled",
        "connection_reuse_disabled",
        "http2_disabled",
        "http3_disabled",
        "quic_disabled",
        "online_aia_disabled",
        "online_ocsp_disabled",
        "online_crl_disabled",
        "online_ct_disabled",
        "certificate_url_fetch_disabled",
        "alt_svc_disabled",
        "speculative_connect_disabled",
        "preconnect_disabled",
        "auxiliary_network_disabled",
        "hostname_verification",
        "certificate_verification",
        "local_revocation_only",
    }
)
_NETWORK_ZERO_RETRY_CONTROLS = frozenset(
    {"connect_retries", "tls_retries", "http_retries"}
)
_NETWORK_POLICY_FIELDS = frozenset(
    {
        "schema_version",
        "host",
        "port",
        "resolved_addresses",
        "selected_peer",
        "max_dns_resolutions",
        "sni",
        "host_header",
        *_NETWORK_BOOLEAN_CONTROLS,
        *_NETWORK_ZERO_RETRY_CONTROLS,
    }
)
_ENFORCEMENT_CAPABILITIES = frozenset(
    {"exact_peer_binding", "exact_sni", "exact_host_header", *_NETWORK_BOOLEAN_CONTROLS, *_NETWORK_ZERO_RETRY_CONTROLS, "max_dns_resolutions"}
)


def _canonical_policy_bytes(policy: Mapping[str, Any]) -> bytes:
    serializable = dict(policy)
    serializable["resolved_addresses"] = list(policy["resolved_addresses"])
    return json.dumps(
        serializable, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class NetworkEnforcementCapabilities:
    """Atestación tipada producida por el simulador de enforcer offline."""

    schema_version: str
    policy_digest: str
    producer: str
    capabilities: frozenset[str]
    _issuer: object = field(repr=False, compare=False)


class OfflineNetworkEnforcer:
    """Simula capability negotiation; no abre red ni prueba enforcement real."""

    def __init__(self, producer: str = "offline-network-enforcer") -> None:
        if not isinstance(producer, str) or not producer.strip():
            raise ValueError("producer es obligatorio")
        self._producer = producer.strip()
        self._issuer = object()

    def attest(self, policy: Mapping[str, Any]) -> NetworkEnforcementCapabilities:
        _validate_network_policy_shape(policy)
        return NetworkEnforcementCapabilities(
            NETWORK_POLICY_SCHEMA_VERSION,
            hashlib.sha256(_canonical_policy_bytes(policy)).hexdigest(),
            self._producer,
            _ENFORCEMENT_CAPABILITIES,
            self._issuer,
        )

    def validate(
        self, policy: Mapping[str, Any], capabilities: NetworkEnforcementCapabilities
    ) -> None:
        _validate_network_policy_shape(policy)
        digest = hashlib.sha256(_canonical_policy_bytes(policy)).hexdigest()
        if (
            type(capabilities) is not NetworkEnforcementCapabilities
            or capabilities._issuer is not self._issuer
            or capabilities.schema_version != NETWORK_POLICY_SCHEMA_VERSION
            or type(capabilities.producer) is not str
            or not capabilities.producer
            or capabilities.policy_digest != digest
            or type(capabilities.capabilities) is not frozenset
            or capabilities.capabilities != _ENFORCEMENT_CAPABILITIES
        ):
            raise SafetyViolation(
                "Atestación de capacidades incompleta, stale o no confiable"
            )


def _validate_network_policy_shape(policy: Mapping[str, Any]) -> None:
    if not isinstance(policy, Mapping) or set(policy) != _NETWORK_POLICY_FIELDS:
        raise SafetyViolation("Policy DNS/TLS incompleta o con campos desconocidos")
    if policy["schema_version"] != NETWORK_POLICY_SCHEMA_VERSION:
        raise SafetyViolation("Versión de policy no soportada")
    for name in _NETWORK_BOOLEAN_CONTROLS:
        if policy[name] is not True:
            raise SafetyViolation(f"Capability requerido no aplicado: {name}")
    for name in _NETWORK_ZERO_RETRY_CONTROLS:
        if isinstance(policy[name], bool) or type(policy[name]) is not int or policy[name] != 0:
            raise SafetyViolation(f"Retry oculto no permitido: {name}")
    if (
        isinstance(policy["port"], bool)
        or type(policy["port"]) is not int
        or policy["port"] != 443
        or isinstance(policy["max_dns_resolutions"], bool)
        or type(policy["max_dns_resolutions"]) is not int
        or policy["max_dns_resolutions"] != 1
    ):
        raise SafetyViolation("Puerto o presupuesto DNS no es exacto")
    if not all(
        type(policy[name]) is str and policy[name] == "www.lacolonia.com"
        for name in ("host", "sni", "host_header")
    ):
        raise SafetyViolation("Host/SNI/Host header no son exactos")
    addresses = policy["resolved_addresses"]
    selected = policy["selected_peer"]
    if (
        type(addresses) is not tuple
        or not addresses
        or any(type(value) is not str for value in addresses)
        or type(selected) is not str
        or addresses.count(selected) != 1
    ):
        raise SafetyViolation("Peer seleccionado no pertenece exactamente a la resolución")
    try:
        parsed_addresses = tuple(ipaddress.ip_address(value) for value in addresses)
        parsed_selected = ipaddress.ip_address(selected)
    except ValueError as exc:
        raise SafetyViolation("Resolución contiene una dirección inválida") from exc
    if any(
        not address.is_global or address.is_multicast
        for address in parsed_addresses
    ) or not parsed_selected.is_global or parsed_selected.is_multicast:
        raise SafetyViolation("Resolución contiene una dirección prohibida")
