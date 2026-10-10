from __future__ import annotations

import builtins
import json
import threading
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

import precios_supermercados.live_safety as live_safety
import precios_supermercados.diagnostics.la_colonia_sps_context_diagnostic as sps_diag
from precios_supermercados.live_safety import (
    ClosedBudget,
    ImmutableLiveRequest,
    IndependentFencingObserver,
    LinearizableAuthority,
    OfflineNetworkEnforcer,
    ReservationState,
    SafetyViolation,
    SpsTechnicalContextStatus,
    StopReason,
    classify_stop,
)
from precios_supermercados.scrapers.base import (
    ExternalNetworkDeniedError,
    HttpResponse,
    HttpStatusError,
    OfflineTestTransport,
    SafeHttpClient,
)
from precios_supermercados.scrapers.la_colonia import FORBIDDEN_PATH_PREFIXES, USER_AGENT
from precios_supermercados.scrapers.la_colonia_facet_discovery import CATALOG_CATEGORIES_V1
from precios_supermercados.scrapers.la_colonia_facet_discovery_adapter import (
    FacetDiscoveryTransportError,
    LaColoniaFacetDiscoveryAdapter,
)

SHA = "a" * 40


class ManualClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def exact_network_policy() -> dict[str, object]:
    return {
        "schema_version": "1",
        "host": "www.lacolonia.com",
        "port": 443,
        "resolved_addresses": ("1.1.1.1",),
        "selected_peer": "1.1.1.1",
        "max_dns_resolutions": 1,
        "sni": "www.lacolonia.com",
        "host_header": "www.lacolonia.com",
        "dns_fallback_disabled": True,
        "happy_eyeballs_disabled": True,
        "proxy_disabled": True,
        "environment_proxy_disabled": True,
        "redirects_disabled": True,
        "connection_pooling_disabled": True,
        "connection_reuse_disabled": True,
        "http2_disabled": True,
        "http3_disabled": True,
        "quic_disabled": True,
        "connect_retries": 0,
        "tls_retries": 0,
        "http_retries": 0,
        "online_aia_disabled": True,
        "online_ocsp_disabled": True,
        "online_crl_disabled": True,
        "online_ct_disabled": True,
        "certificate_url_fetch_disabled": True,
        "alt_svc_disabled": True,
        "speculative_connect_disabled": True,
        "preconnect_disabled": True,
        "auxiliary_network_disabled": True,
        "hostname_verification": True,
        "certificate_verification": True,
        "local_revocation_only": True,
    }


def request(
    store: LinearizableAuthority,
    *,
    sha: str = SHA,
    request_id: str = "offline-test-001",
) -> ImmutableLiveRequest:
    return ImmutableLiveRequest(request_id, sha, "one_exchange", store.epoch)


def reserved() -> tuple[LinearizableAuthority, ImmutableLiveRequest, IndependentFencingObserver]:
    observer = IndependentFencingObserver()
    store = LinearizableAuthority(observer)
    value = request(store)
    store.issue_for_offline_test("grant-1", value)
    store.reserve("grant-1", value, "reservation-1", now=9)
    return store, value, observer


def closure_evidence(
    store: LinearizableAuthority,
    observer: IndependentFencingObserver,
    *,
    observed_at: float,
    phase: str,
    reservation_id: str = "reservation-1",
):
    reservation = store.reservation_snapshot(reservation_id)
    return observer.observe_closed_for_offline_test(
        reservation_id,
        epoch=reservation.epoch,
        request_digest=reservation.request_digest,
        phase=phase,
        observed_at=observed_at,
        source="offline-simulator",
    )


def start_evidence(
    store: LinearizableAuthority,
    observer: IndependentFencingObserver,
    *,
    observed_at: float,
    reservation_id: str = "reservation-1",
):
    reservation = store.reservation_snapshot(reservation_id)
    return observer.observe_started_for_offline_test(
        reservation_id,
        epoch=reservation.epoch,
        request_digest=reservation.request_digest,
        observed_at=observed_at,
    )


def complete_exchange(
    store: LinearizableAuthority,
    observer: IndependentFencingObserver,
    *,
    now: float = 10.0,
    step: float = 1.0,
) -> None:
    store.activate(
        "reservation-1",
        now=now,
        evidence=start_evidence(store, observer, observed_at=now),
    )
    store.open_connection("reservation-1", now=now + step)
    store.complete_tls("reservation-1", now=now + 2 * step)
    store.send_http_request("reservation-1", now=now + 3 * step)
    store.receive_first_byte("reservation-1", now=now + 4 * step)
    store.receive_final_response("reservation-1", now=now + 5 * step)
    store.begin_closing("reservation-1", now=now + 6 * step)
    store.confirm_physical_closed(
        "reservation-1",
        evidence=closure_evidence(
            store, observer, observed_at=now + 7 * step, phase="normal"
        ),
    )


def test_canonical_identity_is_deterministic_and_binds_sha_budget_and_epoch():
    store = LinearizableAuthority()
    first = request(store)
    assert first.digest == request(store).digest
    assert first.digest != request(store, sha="b" * 40).digest
    assert first.digest != ImmutableLiveRequest(
        first.request_id, SHA, first.plan, first.epoch, ClosedBudget(first_byte_deadline_seconds=19)
    ).digest
    store.revoke()
    assert first.digest != request(store).digest


def test_unknown_fields_cannot_enter_typed_request_and_invalid_values_deny():
    with pytest.raises(TypeError):
        ImmutableLiveRequest("x", SHA, "p", 0, unknown=True)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        ImmutableLiveRequest("x", "mutable-main", "p", 0)
    with pytest.raises(ValueError):
        ClosedBudget(max_retries=1)


def test_grant_consumption_is_atomic_and_one_shot_under_race():
    store = LinearizableAuthority()
    value = request(store)
    store.issue_for_offline_test("grant-1", value)
    barrier = threading.Barrier(3)
    outcomes: list[str] = []

    def contender(name: str) -> None:
        barrier.wait()
        try:
            store.reserve("grant-1", value, name, now=0)
            outcomes.append("accepted")
        except SafetyViolation:
            outcomes.append("denied")

    threads = [threading.Thread(target=contender, args=(f"reservation-{i}",)) for i in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["accepted", "denied"]


def test_same_request_digest_cannot_receive_two_sequential_grants():
    store = LinearizableAuthority()
    value = request(store)
    store.issue_for_offline_test("grant-1", value)

    with pytest.raises(SafetyViolation, match="Grant duplicado"):
        store.issue_for_offline_test("grant-2", value)
    with pytest.raises(SafetyViolation, match="Grant desconocido"):
        store.grant_snapshot("grant-2")


def test_authority_rejects_duck_typed_requests():
    class FakeRequest:
        epoch = 0
        digest = "forged"
        budget = ClosedBudget()

    store = LinearizableAuthority()
    with pytest.raises(SafetyViolation, match="tipado"):
        store.issue_for_offline_test("grant-fake", FakeRequest())  # type: ignore[arg-type]

    valid = request(store)
    store.issue_for_offline_test("grant-1", valid)
    with pytest.raises(SafetyViolation, match="tipado"):
        store.reserve(
            "grant-1", FakeRequest(), "reservation-fake", now=0  # type: ignore[arg-type]
        )


def test_same_request_digest_issue_race_creates_exactly_one_grant():
    store = LinearizableAuthority()
    value = request(store)
    barrier = threading.Barrier(3)
    outcomes: dict[str, str] = {}

    def issue(grant_id: str) -> None:
        barrier.wait()
        try:
            store.issue_for_offline_test(grant_id, value)
            outcomes[grant_id] = "issued"
        except SafetyViolation:
            outcomes[grant_id] = "denied"

    threads = [threading.Thread(target=issue, args=(f"grant-{index}",)) for index in (1, 2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert sorted(outcomes.values()) == ["denied", "issued"]
    loser = next(grant_id for grant_id, outcome in outcomes.items() if outcome == "denied")
    with pytest.raises(SafetyViolation, match="Grant desconocido"):
        store.grant_snapshot(loser)


def test_closed_request_digest_remains_burned_for_current_epoch():
    store, value, observer = reserved()
    complete_exchange(store, observer)

    with pytest.raises(SafetyViolation, match="Grant duplicado"):
        store.issue_for_offline_test("grant-2", value)


@pytest.mark.parametrize(
    "field_name",
    [
        "activation_deadline_seconds",
        "connect_deadline_seconds",
        "tls_deadline_seconds",
        "first_byte_deadline_seconds",
        "response_deadline_seconds",
        "hard_reservation_deadline_seconds",
    ],
)
def test_integer_and_float_deadline_forms_have_one_canonical_digest(field_name):
    store = LinearizableAuthority()
    integer_budget = replace(ClosedBudget(), **{field_name: 5})
    float_budget = replace(ClosedBudget(), **{field_name: 5.0})
    first = replace(request(store), budget=integer_budget)
    second = replace(request(store), budget=float_budget)
    assert first.digest == second.digest
    store.issue_for_offline_test("grant-1", first)
    with pytest.raises(SafetyViolation, match="Grant duplicado"):
        store.issue_for_offline_test("grant-2", second)


def test_global_exclusion_allows_only_one_of_two_distinct_grants():
    store = LinearizableAuthority()
    first = request(store)
    second = ImmutableLiveRequest("offline-test-002", SHA, "one_exchange", store.epoch)
    store.issue_for_offline_test("grant-1", first)
    store.issue_for_offline_test("grant-2", second)
    store.reserve("grant-1", first, "reservation-1", now=0)
    with pytest.raises(SafetyViolation):
        store.reserve("grant-2", second, "reservation-2", now=0)


def test_valid_request_without_grant_is_never_authorized():
    store = LinearizableAuthority()
    with pytest.raises(SafetyViolation):
        store.reserve("missing-grant", request(store), "reservation-1", now=0)


def test_revocation_and_reservation_race_is_linearizable_and_fail_closed():
    store = LinearizableAuthority()
    value = request(store)
    store.issue_for_offline_test("grant-1", value)
    barrier = threading.Barrier(3)
    outcomes: list[str] = []

    def do_reserve() -> None:
        barrier.wait()
        try:
            store.reserve("grant-1", value, "reservation-1", now=0)
            outcomes.append("reserved")
        except SafetyViolation:
            outcomes.append("reserve_denied")

    def do_revoke() -> None:
        barrier.wait()
        store.revoke()
        outcomes.append("revoked")

    threads = [threading.Thread(target=do_reserve), threading.Thread(target=do_revoke)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()
    assert "revoked" in outcomes
    assert set(outcomes) in ({"revoked", "reserve_denied"}, {"revoked", "reserved"})
    if "reserved" in outcomes:
        assert store.snapshot("reservation-1")["state"] == ReservationState.UNCERTAIN.value


def test_revocation_epoch_denies_stale_grant_and_active_reservation_activation():
    store, value, _ = reserved()
    assert store.revoke() == 1
    assert store.snapshot("reservation-1")["state"] == ReservationState.UNCERTAIN.value
    with pytest.raises(SafetyViolation):
        store.activate("reservation-1", now=10, evidence=None)
    with pytest.raises(SafetyViolation):
        store.reserve("grant-1", value, "reservation-2", now=10)


def test_invalid_monitor_clock_cannot_block_emergency_revocation():
    clock = ManualClock(0)
    store = LinearizableAuthority(clock=clock)
    first = request(store)
    second = ImmutableLiveRequest("offline-test-002", SHA, "one_exchange", store.epoch)
    store.issue_for_offline_test("grant-1", first)
    store.issue_for_offline_test("grant-2", second)
    store.reserve("grant-1", first, "reservation-1", now=0)

    clock.now = float("nan")
    assert store.revoke() == 1
    clock.now = 0

    assert store.grant_snapshot("grant-2").state.value == "revoked"
    assert store.snapshot("reservation-1")["state"] == ReservationState.UNCERTAIN.value
    with pytest.raises(SafetyViolation):
        store.reserve("grant-2", second, "reservation-2", now=0)


def test_throwing_monitor_clock_cannot_block_emergency_revocation():
    clock = ManualClock(0)
    store = LinearizableAuthority(clock=clock)
    first = request(store)
    second = ImmutableLiveRequest("offline-test-002", SHA, "one_exchange", store.epoch)
    store.issue_for_offline_test("grant-1", first)
    store.issue_for_offline_test("grant-2", second)
    store.reserve("grant-1", first, "reservation-1", now=0)

    def broken_clock():
        raise RuntimeError("clock backend failed")

    store._clock = broken_clock
    assert store.revoke() == 1
    store._clock = clock

    assert store.grant_snapshot("grant-2").state.value == "revoked"
    assert store.snapshot("reservation-1")["state"] == ReservationState.UNCERTAIN.value


def test_single_connection_single_request_single_final_response_and_release():
    store, _, observer = reserved()
    store.activate(
        "reservation-1", now=10,
        evidence=start_evidence(store, observer, observed_at=10),
    )
    store.open_connection("reservation-1", now=11)
    store.complete_tls("reservation-1", now=12)
    with pytest.raises(SafetyViolation):
        store.open_connection("reservation-1", now=11)
    store.send_http_request("reservation-1", now=13)
    with pytest.raises(SafetyViolation):
        store.send_http_request("reservation-1", now=13)
    store.receive_first_byte("reservation-1", now=14)
    store.receive_final_response("reservation-1", now=15)
    with pytest.raises(SafetyViolation):
        store.receive_final_response("reservation-1", now=15)
    store.begin_closing("reservation-1", now=16)
    evidence = closure_evidence(store, observer, observed_at=17, phase="normal")
    store.confirm_physical_closed("reservation-1", evidence=evidence)
    snapshot = store.snapshot("reservation-1")
    assert snapshot["physical_in_flight"] == 0
    assert snapshot["release_kind"] == "normal"
    with pytest.raises(SafetyViolation):
        store.confirm_physical_closed("reservation-1", evidence=evidence)


def test_redirect_upgrade_and_non_get_are_denied_before_exchange():
    for kwargs in ({"redirect": True}, {"upgrade": True}, {"method": "POST"}):
        store, _, observer = reserved()
        store.activate(
            "reservation-1", now=10,
            evidence=start_evidence(store, observer, observed_at=10),
        )
        store.open_connection("reservation-1", now=11)
        store.complete_tls("reservation-1", now=12)
        with pytest.raises(SafetyViolation):
            store.send_http_request("reservation-1", now=13, **kwargs)


def test_timeout_never_releases_and_requires_authoritative_fencing():
    store, _, observer = reserved()
    store.activate(
        "reservation-1", now=10,
        evidence=start_evidence(store, observer, observed_at=10),
    )
    store.deadline_expired("reservation-1", now=56)
    assert store.snapshot("reservation-1")["physical_in_flight"] == 1
    store.require_fencing("reservation-1")
    with pytest.raises(SafetyViolation):
        foreign = closure_evidence(
            store,
            IndependentFencingObserver(),
            observed_at=57,
            phase="fencing",
        )
        store.confirm_fenced_closed("reservation-1", evidence=foreign)
    evidence = closure_evidence(store, observer, observed_at=57, phase="fencing")
    store.confirm_fenced_closed("reservation-1", evidence=evidence)
    assert store.snapshot("reservation-1")["release_kind"] == "fencing"


def test_activation_timeout_can_be_fenced_with_proof_of_no_physical_attempt():
    store, _, observer = reserved()
    store.deadline_expired("reservation-1", now=15)
    assert store.snapshot("reservation-1")["physical_in_flight"] == 0
    store.require_fencing("reservation-1")
    store.confirm_fenced_closed(
        "reservation-1",
        evidence=closure_evidence(store, observer, observed_at=20, phase="fencing"),
    )
    assert store.snapshot("reservation-1")["state"] == ReservationState.FENCED.value


def test_lost_start_observation_fences_and_installs_conservative_pacing():
    store, _, observer = reserved()
    store.activate("reservation-1", now=10, evidence=None)
    assert store.snapshot("reservation-1")["physical_in_flight"] == 1
    store.require_fencing("reservation-1")
    store.confirm_fenced_closed(
        "reservation-1",
        evidence=closure_evidence(store, observer, observed_at=20, phase="fencing"),
    )
    value = request(store, request_id="offline-test-002")
    store.issue_for_offline_test("grant-2", value)
    store.reserve("grant-2", value, "reservation-2", now=21)
    with pytest.raises(SafetyViolation):
        store.activate("reservation-2", now=21.49, evidence=None)
    store.activate(
        "reservation-2", now=21.5,
        evidence=start_evidence(
            store, observer, observed_at=21.5, reservation_id="reservation-2"
        ),
    )


def test_physical_pacing_uses_start_to_start_not_reservation_time():
    store, _, observer = reserved()
    complete_exchange(store, observer, now=10, step=0)
    value = request(store, request_id="offline-test-002")
    store.issue_for_offline_test("grant-2", value)
    store.reserve("grant-2", value, "reservation-2", now=11)
    with pytest.raises(SafetyViolation):
        store.activate("reservation-2", now=11.49, evidence=None)
    store.activate(
        "reservation-2", now=11.5,
        evidence=start_evidence(
            store, observer, observed_at=11.5, reservation_id="reservation-2"
        ),
    )


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf"), -1.0])
def test_reservation_time_must_be_finite_nonnegative_and_monotonic(invalid: float):
    store = LinearizableAuthority()
    value = request(store)
    store.issue_for_offline_test("grant-1", value)
    with pytest.raises(SafetyViolation, match="Tiempo de reserva inválido"):
        store.reserve("grant-1", value, "reservation-1", now=invalid)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf"), 8.9])
def test_phase_time_cannot_be_nonfinite_or_roll_back(invalid: float):
    store, _, _ = reserved()
    with pytest.raises(SafetyViolation, match="no finito o no monotónico"):
        store.activate("reservation-1", now=invalid, evidence=None)


def test_later_phases_cannot_roll_back_clock_to_evade_deadlines():
    observer = IndependentFencingObserver()
    store = LinearizableAuthority(observer)
    value = request(store)
    store.issue_for_offline_test("grant-1", value)
    store.reserve("grant-1", value, "reservation-1", now=0)
    store.activate(
        "reservation-1", now=4.9,
        evidence=start_evidence(store, observer, observed_at=4.9),
    )
    with pytest.raises(SafetyViolation, match="no finito o no monotónico"):
        store.open_connection("reservation-1", now=-100)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf"), -1.0])
def test_fencing_observer_rejects_nonfinite_or_negative_time(invalid: float):
    observer = IndependentFencingObserver()
    with pytest.raises(ValueError, match="Evidencia de fencing inválida"):
        observer.observe_closed_for_offline_test(
            "reservation-1",
            epoch=0,
            request_digest="a" * 64,
            phase="fencing",
            observed_at=invalid,
            source="offline-monitor",
        )


def test_fencing_evidence_cannot_predate_reservation_or_last_transition():
    store, _, observer = reserved()
    store.activate("reservation-1", now=10, evidence=None)
    store.require_fencing("reservation-1")
    evidence = closure_evidence(store, observer, observed_at=9.5, phase="fencing")
    with pytest.raises(SafetyViolation, match="simulador inyectado"):
        store.confirm_fenced_closed("reservation-1", evidence=evidence)


@pytest.mark.parametrize(
    ("phase", "late_time"),
    [
        ("activation", 6),
        ("connect", 12),
        ("tls", 13),
        ("first_byte", 25),
        ("response", 36),
        ("hard", 46),
    ],
)
def test_each_physical_deadline_transitions_to_uncertain(phase: str, late_time: float):
    observer = IndependentFencingObserver()
    store = LinearizableAuthority(observer)
    value = request(store)
    store.issue_for_offline_test("grant-1", value)
    store.reserve("grant-1", value, "reservation-1", now=0)

    with pytest.raises(SafetyViolation, match="fencing requerido"):
        if phase == "activation":
            store.activate("reservation-1", now=late_time, evidence=None)
            return
        store.activate(
            "reservation-1", now=1,
            evidence=start_evidence(store, observer, observed_at=1),
        )
        if phase == "connect":
            store.open_connection("reservation-1", now=late_time)
            return
        store.open_connection("reservation-1", now=2)
        if phase == "tls":
            store.complete_tls("reservation-1", now=late_time)
            return
        store.complete_tls("reservation-1", now=3)
        store.send_http_request("reservation-1", now=4)
        if phase == "first_byte":
            store.receive_first_byte("reservation-1", now=late_time)
            return
        store.receive_first_byte("reservation-1", now=5)
        if phase == "response":
            store.receive_final_response("reservation-1", now=late_time)
            return
        store.receive_final_response("reservation-1", now=6)
        store.begin_closing("reservation-1", now=late_time)

    assert store.snapshot("reservation-1")["state"] == ReservationState.UNCERTAIN.value


def test_deadline_monitor_expires_hung_phase_without_another_transition():
    store, _, observer = reserved()
    store.activate(
        "reservation-1", now=10,
        evidence=start_evidence(store, observer, observed_at=10),
    )
    assert store.expire("reservation-1", now=20) is False
    assert store.expire("reservation-1", now=21) is True
    assert store.snapshot("reservation-1")["state"] == ReservationState.UNCERTAIN.value
    assert store.snapshot("reservation-1")["physical_in_flight"] == 1


def test_injected_clock_guards_every_later_operation_without_explicit_tick():
    clock = ManualClock(0)
    observer = IndependentFencingObserver()
    store = LinearizableAuthority(observer, clock=clock)
    value = request(store)
    store.issue_for_offline_test("grant-1", value)
    store.reserve("grant-1", value, "reservation-1", now=0)
    clock.now = 6
    with pytest.raises(SafetyViolation, match="Transición de reserva inválida"):
        store.activate("reservation-1", now=6, evidence=None)
    assert store.snapshot("reservation-1")["state"] == ReservationState.UNCERTAIN.value


def test_read_api_also_runs_injected_deadline_monitor():
    clock = ManualClock(0)
    store = LinearizableAuthority(clock=clock)
    value = request(store)
    store.issue_for_offline_test("grant-1", value)
    store.reserve("grant-1", value, "reservation-1", now=0)
    clock.now = 6
    assert store.grant_snapshot("grant-1").state.value == "consumed"
    assert store.snapshot("reservation-1")["state"] == ReservationState.UNCERTAIN.value


def test_default_monitor_clock_advances_without_explicit_transition(monkeypatch):
    clock = ManualClock(100)
    monkeypatch.setattr(live_safety.time, "monotonic", clock)
    store = LinearizableAuthority()
    value = request(store)
    store.issue_for_offline_test("grant-1", value)
    store.reserve("grant-1", value, "reservation-1", now=0)
    clock.now = 106
    assert store.snapshot("reservation-1")["state"] == ReservationState.UNCERTAIN.value


def test_explicit_timestamps_cannot_rebase_default_monitor_backwards(monkeypatch):
    clock = ManualClock(100)
    monkeypatch.setattr(live_safety.time, "monotonic", clock)
    observer = IndependentFencingObserver()
    store = LinearizableAuthority(observer)
    value = request(store)
    store.issue_for_offline_test("grant-1", value)
    store.reserve("grant-1", value, "reservation-1", now=0)

    clock.now = 104
    store.activate(
        "reservation-1",
        now=0,
        evidence=start_evidence(store, observer, observed_at=0),
    )
    clock.now = 113
    with pytest.raises(SafetyViolation, match="Transición de reserva inválida"):
        store.open_connection("reservation-1", now=0)

    snapshot = store.snapshot("reservation-1")
    assert snapshot["state"] == ReservationState.UNCERTAIN.value
    assert snapshot["physical_in_flight"] == 1


def test_monitor_backend_exception_marks_active_reservation_uncertain():
    class ThrowingClock:
        value = 0.0
        fail = False

        def __call__(self):
            if self.fail:
                raise RuntimeError("clock backend unavailable")
            return self.value

    clock = ThrowingClock()
    store = LinearizableAuthority(clock=clock)
    value = request(store)
    store.issue_for_offline_test("grant-1", value)
    store.reserve("grant-1", value, "reservation-1", now=0)
    clock.fail = True
    with pytest.raises(SafetyViolation, match="Clock del monitor no disponible"):
        store.snapshot("reservation-1")
    clock.fail = False
    assert store.snapshot("reservation-1")["state"] == ReservationState.UNCERTAIN.value
    with pytest.raises(SafetyViolation, match="Transición de reserva inválida"):
        store.activate("reservation-1", now=0, evidence=None)


@pytest.mark.parametrize(
    ("changes", "activate_at"),
    [
        ({"physical_started_at": 11}, 11),
        ({"_seal": None}, 10),
    ],
)
def test_rewritten_start_evidence_is_rejected_even_with_original_issuer(
    changes, activate_at
):
    store, _, observer = reserved()
    original = start_evidence(store, observer, observed_at=10)
    tampered = replace(original, **changes)
    with pytest.raises(SafetyViolation, match="evidencia del simulador"):
        store.activate("reservation-1", now=activate_at, evidence=tampered)
    assert store.snapshot("reservation-1")["state"] == ReservationState.UNCERTAIN.value


@pytest.mark.parametrize(
    "changes",
    [
        {"physical_closed_at": 18},
        {"source": "forged-source"},
        {"phase": "fencing"},
        {"_seal": None},
        {"source": {"not": "text"}},
    ],
)
def test_rewritten_closure_evidence_is_rejected(changes):
    store, _, observer = reserved()
    store.activate(
        "reservation-1", now=10,
        evidence=start_evidence(store, observer, observed_at=10),
    )
    store.open_connection("reservation-1", now=11)
    store.complete_tls("reservation-1", now=12)
    store.send_http_request("reservation-1", now=13)
    store.receive_first_byte("reservation-1", now=14)
    store.receive_final_response("reservation-1", now=15)
    store.begin_closing("reservation-1", now=16)
    original = closure_evidence(store, observer, observed_at=17, phase="normal")
    with pytest.raises(SafetyViolation, match="evidencia del simulador"):
        store.confirm_physical_closed(
            "reservation-1", evidence=replace(original, **changes)
        )
    assert store.snapshot("reservation-1")["state"] == ReservationState.CLOSING.value


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf"), True])
def test_injected_monitor_clock_fails_closed_on_invalid_values(invalid: object):
    clock = ManualClock(0)
    store = LinearizableAuthority(clock=clock)
    value = request(store)
    store.issue_for_offline_test("grant-1", value)
    store.reserve("grant-1", value, "reservation-1", now=0)
    clock.now = invalid  # type: ignore[assignment]
    with pytest.raises(SafetyViolation, match="Clock del monitor"):
        _ = store.epoch
    assert store.snapshot("reservation-1")["state"] == ReservationState.UNCERTAIN.value


def test_deadline_expired_rejects_early_caller_assertion():
    store, _, _ = reserved()
    with pytest.raises(SafetyViolation, match="todavía vigente"):
        store.deadline_expired("reservation-1", now=13)
    assert store.snapshot("reservation-1")["state"] == ReservationState.RESERVED.value


@pytest.mark.parametrize("invalid", [True, False, "0", None])
def test_reservation_and_transition_times_reject_non_numeric_types(invalid):
    store = LinearizableAuthority()
    value = request(store)
    store.issue_for_offline_test("grant-1", value)
    with pytest.raises(SafetyViolation, match="Tiempo de reserva inválido"):
        store.reserve("grant-1", value, "reservation-1", now=invalid)


def test_physical_start_requires_bound_simulator_evidence():
    store, _, observer = reserved()
    foreign = IndependentFencingObserver()
    with pytest.raises(SafetyViolation, match="Inicio exige evidencia"):
        store.activate(
            "reservation-1",
            now=10,
            evidence=start_evidence(store, foreign, observed_at=10),
        )
    assert store.snapshot("reservation-1")["state"] == ReservationState.UNCERTAIN.value


def test_authority_returns_only_immutable_snapshots_and_replay_stays_denied():
    store = LinearizableAuthority()
    value = request(store)
    grant = store.issue_for_offline_test("grant-1", value)
    reservation = store.reserve("grant-1", value, "reservation-1", now=0)
    with pytest.raises(FrozenInstanceError):
        grant.state = grant.state.ACTIVE  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        reservation.state = ReservationState.CLOSED  # type: ignore[misc]
    with pytest.raises(AttributeError):
        store.epoch = 0  # type: ignore[misc]
    with pytest.raises(AttributeError):
        store.active_reservation_id = None  # type: ignore[misc]
    with pytest.raises(TypeError):
        store.snapshot("reservation-1")["state"] = "closed"  # type: ignore[index]
    assert store.grant_snapshot("grant-1").state.value == "consumed"
    with pytest.raises(SafetyViolation):
        store.reserve("grant-1", value, "reservation-2", now=0)


def test_normal_closure_rejects_foreign_or_fabricated_evidence():
    store, _, observer = reserved()
    store.activate(
        "reservation-1", now=10,
        evidence=start_evidence(store, observer, observed_at=10),
    )
    store.open_connection("reservation-1", now=11)
    store.complete_tls("reservation-1", now=12)
    store.send_http_request("reservation-1", now=13)
    store.receive_first_byte("reservation-1", now=14)
    store.receive_final_response("reservation-1", now=15)
    store.begin_closing("reservation-1", now=16)
    foreign = closure_evidence(
        store, IndependentFencingObserver(), observed_at=17, phase="normal"
    )
    with pytest.raises(SafetyViolation, match="simulador inyectado"):
        store.confirm_physical_closed("reservation-1", evidence=foreign)
    valid = closure_evidence(store, observer, observed_at=17, phase="normal")
    with pytest.raises(SafetyViolation, match="simulador inyectado"):
        store.confirm_physical_closed(
            "reservation-1", evidence=replace(valid, request_digest="b" * 64)
        )
    with pytest.raises(SafetyViolation, match="simulador inyectado"):
        store.confirm_physical_closed(
            "reservation-1", evidence=replace(valid, phase="fencing")
        )
    store.confirm_physical_closed("reservation-1", evidence=valid)


def test_normal_close_is_linearizable_under_double_confirmation_race():
    store, _, observer = reserved()
    store.activate(
        "reservation-1", now=10,
        evidence=start_evidence(store, observer, observed_at=10),
    )
    store.open_connection("reservation-1", now=11)
    store.complete_tls("reservation-1", now=12)
    store.send_http_request("reservation-1", now=13)
    store.receive_first_byte("reservation-1", now=14)
    store.receive_final_response("reservation-1", now=15)
    store.begin_closing("reservation-1", now=16)
    evidence = closure_evidence(store, observer, observed_at=17, phase="normal")
    barrier = threading.Barrier(3)
    outcomes: list[str] = []

    def close_once() -> None:
        barrier.wait()
        try:
            store.confirm_physical_closed("reservation-1", evidence=evidence)
            outcomes.append("closed")
        except SafetyViolation:
            outcomes.append("denied")

    threads = [threading.Thread(target=close_once) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["closed", "denied"]


def test_fenced_close_is_linearizable_under_double_confirmation_race():
    store, _, observer = reserved()
    store.activate(
        "reservation-1", now=10,
        evidence=start_evidence(store, observer, observed_at=10),
    )
    store.deadline_expired("reservation-1", now=56)
    store.require_fencing("reservation-1")
    evidence = closure_evidence(store, observer, observed_at=57, phase="fencing")
    barrier = threading.Barrier(3)
    outcomes: list[str] = []

    def fence_once() -> None:
        barrier.wait()
        try:
            store.confirm_fenced_closed("reservation-1", evidence=evidence)
            outcomes.append("fenced")
        except SafetyViolation:
            outcomes.append("denied")

    threads = [threading.Thread(target=fence_once) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["denied", "fenced"]


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf"), True])
def test_all_closed_budget_deadlines_reject_nonfinite_or_boolean(invalid: object):
    for field_name in (
        "activation_deadline_seconds",
        "connect_deadline_seconds",
        "tls_deadline_seconds",
        "first_byte_deadline_seconds",
        "response_deadline_seconds",
        "hard_reservation_deadline_seconds",
    ):
        with pytest.raises(ValueError):
            ClosedBudget(**{field_name: invalid})


@pytest.mark.parametrize("field_name", ["max_connections", "max_http_requests", "max_final_responses", "max_retries"])
def test_closed_budget_rejects_boolean_integer_controls(field_name: str):
    with pytest.raises(ValueError):
        ClosedBudget(**{field_name: field_name != "max_retries"})


def test_offline_harnesses_and_clients_are_not_authority_by_reassignment():
    client = SafeHttpClient(
        allowed_hosts={"www.lacolonia.com"},
        forbidden_path_prefixes=(),
        user_agent="offline-test",
    )
    with pytest.raises(AttributeError):
        client.transport = lambda *_: None

    adapter = LaColoniaFacetDiscoveryAdapter()
    with pytest.raises(AttributeError):
        adapter._opener = lambda *_: None

    with pytest.raises(ValueError, match="módulos test"):
        OfflineTestTransport(len)  # type: ignore[arg-type]


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf"), True])
def test_safe_http_client_rejects_nonfinite_or_boolean_timeouts(invalid: object):
    with pytest.raises(ValueError):
        SafeHttpClient(
            allowed_hosts={"www.lacolonia.com"},
            forbidden_path_prefixes=(),
            user_agent="offline-test",
            timeout_seconds=invalid,  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError):
        SafeHttpClient(
            allowed_hosts={"www.lacolonia.com"},
            forbidden_path_prefixes=(),
            user_agent="offline-test",
            retry_delay_seconds=invalid,  # type: ignore[arg-type]
        )


def test_safe_http_allowed_hosts_collection_cannot_be_mutated():
    client = SafeHttpClient(
        allowed_hosts={"www.lacolonia.com"},
        forbidden_path_prefixes=(),
        user_agent="offline-test",
    )
    with pytest.raises(AttributeError):
        client.allowed_hosts.add("evil.invalid")  # type: ignore[attr-defined]


def test_stop_policy_is_fail_closed_for_all_canonical_signals():
    assert classify_stop(status=403) is StopReason.HTTP_403
    assert classify_stop(status=429) is StopReason.HTTP_429
    assert classify_stop(status=200, body="CAPTCHA") is StopReason.CAPTCHA
    assert classify_stop(status=200, body="Dirección requerida") is StopReason.AUTH_OR_LOCATION
    assert classify_stop(status=302) is StopReason.REDIRECT
    assert classify_stop(status=200, load_excessive=True) is StopReason.EXCESSIVE_LOAD
    assert classify_stop(status=599) is StopReason.UNKNOWN


def test_exact_dns_tls_policy_requires_attested_enforcement_capabilities():
    policy = exact_network_policy()
    enforcer = OfflineNetworkEnforcer()
    capabilities = enforcer.attest(policy)
    enforcer.validate(policy, capabilities)

    policy["selected_peer"] = "127.0.0.1"
    with pytest.raises(SafetyViolation):
        enforcer.attest(policy)

    policy["resolved_addresses"] = ("127.0.0.1",)
    with pytest.raises(SafetyViolation, match="prohibida"):
        enforcer.attest(policy)


def test_network_policy_closed_schema_rejects_every_missing_field_and_unknown_field():
    enforcer = OfflineNetworkEnforcer()
    policy = exact_network_policy()
    for field_name in tuple(policy):
        incomplete = dict(policy)
        incomplete.pop(field_name)
        with pytest.raises(SafetyViolation, match="incompleta|Versión"):
            enforcer.attest(incomplete)
    with_unknown = dict(policy, unsafe_extension=True)
    with pytest.raises(SafetyViolation, match="campos desconocidos"):
        enforcer.attest(with_unknown)


@pytest.mark.parametrize(
    "field_name",
    [
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
    ],
)
def test_network_policy_rejects_each_disabled_enforcement_control(field_name: str):
    policy = exact_network_policy()
    policy[field_name] = False
    with pytest.raises(SafetyViolation, match="Capability requerido"):
        OfflineNetworkEnforcer().attest(policy)


@pytest.mark.parametrize("field_name", ["connect_retries", "tls_retries", "http_retries"])
@pytest.mark.parametrize("invalid", [1, True, "0"])
def test_network_policy_rejects_hidden_or_malformed_retries(field_name: str, invalid: object):
    policy = exact_network_policy()
    policy[field_name] = invalid
    with pytest.raises(SafetyViolation, match="Retry oculto"):
        OfflineNetworkEnforcer().attest(policy)


def test_network_capability_attestation_is_exact_bound_and_issuer_scoped():
    policy = exact_network_policy()
    enforcer = OfflineNetworkEnforcer()
    attestation = enforcer.attest(policy)

    missing = replace(
        attestation,
        capabilities=attestation.capabilities - {"online_aia_disabled"},
    )
    with pytest.raises(SafetyViolation, match="capacidades"):
        enforcer.validate(policy, missing)

    foreign = OfflineNetworkEnforcer("foreign").attest(policy)
    with pytest.raises(SafetyViolation, match="capacidades"):
        enforcer.validate(policy, foreign)

    changed = exact_network_policy()
    changed["resolved_addresses"] = ("8.8.8.8",)
    changed["selected_peer"] = "8.8.8.8"
    with pytest.raises(SafetyViolation, match="capacidades"):
        enforcer.validate(changed, attestation)

    malformed_producer = replace(attestation, producer=123)  # type: ignore[arg-type]
    with pytest.raises(SafetyViolation, match="capacidades"):
        enforcer.validate(policy, malformed_producer)

    mutable_capabilities = set(attestation.capabilities)
    mutable = replace(attestation, capabilities=mutable_capabilities)  # type: ignore[arg-type]
    with pytest.raises(SafetyViolation, match="capacidades"):
        enforcer.validate(policy, mutable)
    mutable_capabilities.clear()
    assert not hasattr(live_safety, "validate_exact_network_policy")


@pytest.mark.parametrize("address", ["224.0.0.1", "239.1.1.1", "ff02::1", "ff0e::1"])
def test_network_policy_rejects_multicast_peers(address: str):
    policy = exact_network_policy()
    policy["resolved_addresses"] = (address,)
    policy["selected_peer"] = address
    with pytest.raises(SafetyViolation, match="dirección prohibida"):
        OfflineNetworkEnforcer().attest(policy)


def test_local_browser_guard_requires_and_blocks_websocket_channel():
    calls: dict[str, object] = {}

    class Context:
        def route(self, pattern, handler):
            calls["route"] = (pattern, handler)

        def route_web_socket(self, pattern, handler):
            calls["websocket"] = (pattern, handler)

    class WebSocket:
        url = "wss://www.lacolonia.com/forbidden"

        def close(self):
            calls["closed"] = True

    events: list[dict] = []
    sps_diag.install_local_network_guard(Context(), events=events)
    pattern, handler = calls["websocket"]
    assert pattern == "**/*"
    handler(WebSocket())
    assert calls["closed"] is True
    assert events == [{
        "url": "wss://www.lacolonia.com/forbidden",
        "action": "websocket_blocked",
    }]


@pytest.mark.parametrize(
    ("field_name", "invalid"),
    [
        ("schema_version", "2"),
        ("port", True),
        ("port", 444),
        ("max_dns_resolutions", True),
        ("max_dns_resolutions", 2),
        ("host", "lacolonia.com"),
        ("sni", "lacolonia.com"),
        ("host_header", "lacolonia.com"),
        ("resolved_addresses", ["1.1.1.1"]),
        ("resolved_addresses", ("1.1.1.1", "1.1.1.1")),
        ("selected_peer", 1),
    ],
)
def test_network_policy_rejects_malformed_exact_fields(field_name: str, invalid: object):
    policy = exact_network_policy()
    policy[field_name] = invalid
    with pytest.raises(SafetyViolation):
        OfflineNetworkEnforcer().attest(policy)


def test_real_transports_are_denied_but_injected_fakes_remain_offline_testable():
    client = SafeHttpClient(
        allowed_hosts={"www.lacolonia.com"},
        forbidden_path_prefixes=FORBIDDEN_PATH_PREFIXES,
        user_agent=USER_AGENT,
    )
    with pytest.raises(ExternalNetworkDeniedError):
        client.get("https://www.lacolonia.com/_v/segment/graphql/v1")
    with pytest.raises(FacetDiscoveryTransportError, match="GLOBAL LIVE BLOCKED"):
        LaColoniaFacetDiscoveryAdapter()(CATALOG_CATEGORIES_V1.requests[0])


def test_safe_http_client_rejects_redirect_response_from_fake():
    client = SafeHttpClient(
        allowed_hosts={"www.lacolonia.com"},
        forbidden_path_prefixes=(),
        user_agent="offline-test",
        transport=OfflineTestTransport(
            lambda url, headers, timeout: HttpResponse(302, url, {"Location": url}, b"")
        ),
    )
    with pytest.raises(HttpStatusError) as raised:
        client.get("https://www.lacolonia.com/test")
    assert raised.value.status_code == 302


def test_sps_context_is_separate_and_fail_closed():
    assert SpsTechnicalContextStatus.UNCONFIRMED.value == "unconfirmed"
    assert set(SpsTechnicalContextStatus) == {
        SpsTechnicalContextStatus.CONFIRMED,
        SpsTechnicalContextStatus.UNCONFIRMED,
        SpsTechnicalContextStatus.UNAVAILABLE,
    }


def test_local_only_replay_rejects_external_template_before_context_request():
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/la_colonia_sps_context_diagnostic.json").read_text()
    )

    class NeverRequest:
        @property
        def request(self):
            raise AssertionError("No debe alcanzar el cliente Playwright")

    external_event = dict(fixture["network"][0])
    external_event["url"] = external_event["url"].replace(
        "synthetic.invalid", "www.lacolonia.com"
    )
    with pytest.raises(sps_diag.DiagnosticSafetyError, match="antes de red"):
        sps_diag._execute_replay(
            NeverRequest(),
            external_event,
            sps_diag.LogicalRequestCounter(sps_diag.DiagnosticBudget()),
            "blocked_external",
            network_policy="local_only",
        )


@pytest.mark.parametrize("changed", [False, True])
def test_local_only_diagnostic_never_labels_synthetic_evidence_live_confirmed(
    changed: bool,
):
    mode, location = sps_diag._diagnostic_labels(
        "local_only", changed_context_observed=changed
    )
    assert mode == "synthetic_local"
    assert location == "offline_fixture"


def test_persisted_local_only_failure_artifact_is_explicitly_synthetic(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    original_import = builtins.__import__

    def blocked_playwright_import(name, *args, **kwargs):
        if name == "playwright.sync_api":
            raise ImportError("offline test")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_playwright_import)
    output = tmp_path / "synthetic-report.json"
    report = sps_diag.run_live(
        authorization_id="synthetic-test-id",
        active_ids={"synthetic-test-id"},
        output_path=output,
        _network_policy="local_only",
    )
    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert report.mode == persisted["mode"] == "synthetic_local"
    assert report.location_status == persisted["location_status"] == "offline_fixture"


