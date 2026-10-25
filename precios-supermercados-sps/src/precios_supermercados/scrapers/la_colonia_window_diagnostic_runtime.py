"""Runtime offline-auditable para el diagnóstico de ventanas de La Colonia.

Este módulo selecciona planes cerrados y ejecuta consultas secuenciales mediante
``SafeHttpClient``. No conoce GitHub Actions, no persiste payloads y no modifica
el runner normal del catálogo.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .base import (
    BlockedResponseError,
    HttpStatusError,
    RateLimitedError,
    SafeHttpClient,
    ScraperError,
    StructureChangedError,
)
from .la_colonia import FORBIDDEN_PATH_PREFIXES, USER_AGENT
from .la_colonia_window_diagnostic import (
    DEFAULT_MAX_ARTIFACT_BYTES,
    WindowDiagnosticReport,
    WindowObservation,
    WindowSpec,
    build_window_url,
    diagnose_windows,
    observe_window_payload,
    validate_sanitized_summary,
)

DIAGNOSTIC_PLAN_NAME = "frontier_380_399_v1"
DIAGNOSTIC_SCHEMA_VERSION = "1.0.0"
DIAGNOSTIC_DELAY_SECONDS = 1.5
DIAGNOSTIC_MAX_REQUESTS = 12
DIAGNOSTIC_MAX_DURATION_SECONDS = 300.0
DIAGNOSTIC_CONCURRENCY = 1
REQUEST_ID_RE = re.compile(r"[a-z0-9](?:[a-z0-9._-]{0,78}[a-z0-9])?\Z")

EXIT_COMPLETED = 0
EXIT_ANOMALY = 2
EXIT_CATALOG_CHANGED = 3
EXIT_TECHNICAL_STOP = 4
EXIT_SECURITY_FAILURE = 5


@dataclass(frozen=True, slots=True)
class DiagnosticPlan:
    name: str
    phase_one: tuple[WindowSpec, ...]
    phase_two: tuple[WindowSpec, ...]
    max_requests: int = DIAGNOSTIC_MAX_REQUESTS

    def __post_init__(self) -> None:
        if self.name != DIAGNOSTIC_PLAN_NAME:
            raise ValueError("Plan diagnóstico no autorizado")
        if len(self.phase_one) != 8:
            raise ValueError("La fase uno debe contener exactamente 8 ventanas")
        if len(self.phase_two) != 4:
            raise ValueError("La fase dos debe contener exactamente 4 ventanas")
        if len(self.phase_one) + len(self.phase_two) > self.max_requests:
            raise ValueError("El plan supera el máximo de solicitudes")
        if self.max_requests != DIAGNOSTIC_MAX_REQUESTS:
            raise ValueError("El máximo de solicitudes debe ser 12")


FRONTIER_380_399_V1 = DiagnosticPlan(
    name=DIAGNOSTIC_PLAN_NAME,
    phase_one=(
        WindowSpec("A", 360, 379, "OrderByNameASC"),
        WindowSpec("B", 370, 389, "OrderByNameASC"),
        WindowSpec("C", 380, 399, "OrderByNameASC"),
        WindowSpec("D", 390, 409, "OrderByNameASC"),
        WindowSpec("E", 400, 419, "OrderByNameASC"),
        WindowSpec("F", 380, 389, "OrderByNameASC"),
        WindowSpec("G", 390, 399, "OrderByNameASC"),
        WindowSpec("H", 350, 399, "OrderByNameASC"),
    ),
    phase_two=(
        WindowSpec("C", 380, 399, "OrderByReleaseDateDESC"),
        WindowSpec("F", 380, 389, "OrderByReleaseDateDESC"),
        WindowSpec("G", 390, 399, "OrderByReleaseDateDESC"),
        WindowSpec("H", 350, 399, "OrderByReleaseDateDESC"),
    ),
)


@dataclass(frozen=True, slots=True)
class DiagnosticExecutionResult:
    summary: Mapping[str, Any]
    exit_code: int

    @property
    def completed(self) -> bool:
        return bool(self.summary.get("completed"))


@dataclass(slots=True)
class _ExecutionState:
    request_id: str
    plan: DiagnosticPlan
    started_at: datetime
    requests_planned: int = 8
    requests_attempted: int = 0
    requests_completed: int = 0
    delay_seconds_applied: float = 0.0
    observations: list[WindowObservation] = field(default_factory=list)
    quality_events: list[str] = field(default_factory=list)
    stop_reason: str = ""
    exit_code: int = EXIT_COMPLETED
    phase_two_started: bool = False
    last_elapsed: float = 0.0


def get_diagnostic_plan(name: str) -> DiagnosticPlan:
    if name != DIAGNOSTIC_PLAN_NAME:
        raise ValueError("Plan diagnóstico desconocido")
    return FRONTIER_380_399_V1


class LaColoniaWindowDiagnosticRuntime:
    """Ejecuta un plan cerrado con una sola solicitud lógica a la vez."""

    concurrency = DIAGNOSTIC_CONCURRENCY

    def __init__(
        self,
        client: SafeHttpClient | None = None,
        *,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        clock: Callable[[], datetime] | None = None,
        max_duration_seconds: float = DIAGNOSTIC_MAX_DURATION_SECONDS,
    ) -> None:
        if (
            isinstance(max_duration_seconds, bool)
            or not isinstance(max_duration_seconds, (int, float))
            or not math.isfinite(max_duration_seconds)
            or max_duration_seconds <= 0
        ):
            raise ValueError("max_duration_seconds debe ser mayor que cero")
        self.client = client or SafeHttpClient(
            allowed_hosts={"www.lacolonia.com"},
            forbidden_path_prefixes=FORBIDDEN_PATH_PREFIXES,
            user_agent=USER_AGENT,
            max_retries=0,
            retry_delay_seconds=0,
        )
        if self.client.max_retries != 0:
            raise ValueError("El diagnóstico exige max_retries=0 para respetar 12 solicitudes")
        self.sleeper = sleeper
        self.monotonic = monotonic
        self.clock = clock or (lambda: datetime.now(UTC))
        self.max_duration_seconds = float(max_duration_seconds)
        self._run_lock = threading.Lock()

    def run(
        self,
        *,
        request_id: str,
        plan_name: str = DIAGNOSTIC_PLAN_NAME,
        delay_seconds: float = DIAGNOSTIC_DELAY_SECONDS,
    ) -> DiagnosticExecutionResult:
        _validate_request_id(request_id)
        if (
            isinstance(delay_seconds, bool)
            or not isinstance(delay_seconds, (int, float))
            or not math.isfinite(delay_seconds)
            or delay_seconds != DIAGNOSTIC_DELAY_SECONDS
        ):
            raise ValueError("delay_seconds debe ser exactamente 1.5")
        if not self._run_lock.acquire(blocking=False):
            raise ValueError("diagnostic_runtime_already_running")
        try:
            return self._run_locked(
                request_id=request_id,
                plan_name=plan_name,
                delay_seconds=float(delay_seconds),
            )
        finally:
            self._run_lock.release()

    def _run_locked(
        self,
        *,
        request_id: str,
        plan_name: str,
        delay_seconds: float,
    ) -> DiagnosticExecutionResult:
        plan = get_diagnostic_plan(plan_name)
        started_at = _utc(self.clock())
        state = _ExecutionState(request_id=request_id, plan=plan, started_at=started_at)
        try:
            started_monotonic = self.monotonic()
        except Exception:
            started_monotonic = None
        if not _finite_number(started_monotonic):
            self._technical_stop(state, "invalid_monotonic_clock")
            return self._finish(state, None, None, None)
        started_monotonic = float(started_monotonic)

        phase_one = self._execute_phase(
            state,
            plan.phase_one,
            delay_seconds=delay_seconds,
            started_monotonic=started_monotonic,
        )
        if state.exit_code not in {EXIT_COMPLETED, EXIT_ANOMALY}:
            return self._finish(state, phase_one, None, started_monotonic)

        phase_one_findings = _derive_phase_one_findings(phase_one)
        _extend_unique(state.quality_events, phase_one_findings)

        if _phase_two_required(phase_one, phase_one_findings):
            state.phase_two_started = True
            state.requests_planned = DIAGNOSTIC_MAX_REQUESTS
            self.sleeper(delay_seconds)
            state.delay_seconds_applied += delay_seconds
            if not self._duration_within_budget(state, started_monotonic):
                return self._finish(state, phase_one, None, started_monotonic)
            phase_two = self._execute_phase(
                state,
                plan.phase_two,
                delay_seconds=delay_seconds,
                started_monotonic=started_monotonic,
            )
        else:
            phase_two = None

        if state.exit_code in {EXIT_COMPLETED, EXIT_ANOMALY}:
            outcome = _derive_outcome(phase_one, phase_two, phase_one_findings)
            if outcome == "catalog_changed":
                state.exit_code = EXIT_CATALOG_CHANGED
                state.stop_reason = "catalog_total_changed"
            elif outcome == "no_anomaly_observed":
                state.exit_code = EXIT_COMPLETED
            else:
                state.exit_code = EXIT_ANOMALY
            _append_unique(state.quality_events, f"diagnostic:{outcome}")

        return self._finish(state, phase_one, phase_two, started_monotonic)

    def _execute_phase(
        self,
        state: _ExecutionState,
        windows: Sequence[WindowSpec],
        *,
        delay_seconds: float,
        started_monotonic: float,
    ) -> WindowDiagnosticReport | None:
        phase_observations: list[WindowObservation] = []
        for index, window in enumerate(windows):
            if state.requests_attempted >= state.plan.max_requests:
                self._technical_stop(state, "maximum_request_count_exceeded")
                break
            if not self._duration_within_budget(state, started_monotonic):
                break

            state.requests_attempted += 1
            try:
                response = self.client.get(build_window_url(window))
                if not self._duration_within_budget(state, started_monotonic):
                    break
                observation = observe_window_payload(
                    window,
                    response.json(),
                    response_bytes=len(response.body),
                )
                if not self._duration_within_budget(state, started_monotonic):
                    break
            except BlockedResponseError:
                self._technical_stop(state, "http_403_or_captcha")
                break
            except RateLimitedError:
                self._technical_stop(state, "persistent_http_429")
                break
            except HttpStatusError as exc:
                self._technical_stop(state, f"http_status_{exc.status_code}")
                break
            except (StructureChangedError, ValueError, TypeError, KeyError):
                self._technical_stop(state, "invalid_graphql_structure")
                break
            except ScraperError:
                self._technical_stop(state, "scraper_error")
                break

            phase_observations.append(observation)
            state.observations.append(observation)
            state.requests_completed += 1
            _extend_unique(state.quality_events, observation.quality_events)

            if state.observations[0].records_filtered != observation.records_filtered:
                state.exit_code = EXIT_CATALOG_CHANGED
                state.stop_reason = "catalog_total_changed"
                _append_unique(state.quality_events, "quality:catalog_total_changed")
                break

            if index < len(windows) - 1:
                self.sleeper(delay_seconds)
                state.delay_seconds_applied += delay_seconds
                if not self._duration_within_budget(state, started_monotonic):
                    break

        if not phase_observations:
            return None
        try:
            return diagnose_windows(phase_observations)
        except ValueError:
            self._technical_stop(state, "diagnostic_comparison_failed")
            return None

    @staticmethod
    def _technical_stop(state: _ExecutionState, reason: str) -> None:
        state.exit_code = EXIT_TECHNICAL_STOP
        state.stop_reason = reason
        _append_unique(state.quality_events, f"stop:{reason}")

    def _duration_within_budget(
        self,
        state: _ExecutionState,
        started_monotonic: float,
    ) -> bool:
        try:
            now = self.monotonic()
        except Exception:
            self._technical_stop(state, "invalid_monotonic_clock")
            return False
        if not _finite_number(now):
            self._technical_stop(state, "invalid_monotonic_clock")
            return False
        elapsed = float(now) - started_monotonic
        if elapsed < state.last_elapsed:
            self._technical_stop(state, "non_monotonic_clock")
            return False
        state.last_elapsed = elapsed
        if elapsed > self.max_duration_seconds:
            self._technical_stop(state, "maximum_duration_exceeded")
            return False
        return True

    def _finish(
        self,
        state: _ExecutionState,
        phase_one: WindowDiagnosticReport | None,
        phase_two: WindowDiagnosticReport | None,
        started_monotonic: float | None,
    ) -> DiagnosticExecutionResult:
        if started_monotonic is not None:
            self._duration_within_budget(state, started_monotonic)
        finished_at = _utc(self.clock())
        duration = state.last_elapsed
        totals = [item.records_filtered for item in state.observations]
        total_initial = totals[0] if totals else 0
        total_final = totals[-1] if totals else 0
        total_change = total_final - total_initial
        total_change_ratio = abs(total_change) / total_initial if total_initial else 0.0

        phase_one_summary = phase_one.sanitized_summary() if phase_one else None
        windows = [item.sanitized_dict() for item in state.observations]
        overlaps = _sanitized_overlaps(phase_one, phase_two)

        diagnostic_outcome = _outcome_from_events(state.quality_events, state.exit_code)
        completed = state.exit_code in {EXIT_COMPLETED, EXIT_ANOMALY}
        anomalies_detected = state.exit_code == EXIT_ANOMALY
        summary: dict[str, Any] = {
            "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
            "request_id": state.request_id,
            "diagnostic_plan": state.plan.name,
            "started_at": state.started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_seconds": round(duration, 6),
            "requests_planned": state.requests_planned,
            "requests_attempted": state.requests_attempted,
            "requests_completed": state.requests_completed,
            "delay_seconds_applied": state.delay_seconds_applied,
            "completed": completed,
            "anomalies_detected": anomalies_detected,
            "phase_two_started": state.phase_two_started,
            "windows": windows,
            "overlaps": overlaps,
            "expected_unique_positions": (
                int(phase_one_summary["expected_unique_positions"])
                if phase_one_summary
                else 0
            ),
            "products_unique_in_union": (
                int(phase_one_summary["products_unique_in_union"])
                if phase_one_summary
                else 0
            ),
            "union_delta": int(phase_one_summary["union_delta"]) if phase_one_summary else 0,
            "repeated_occurrences": (
                int(phase_one_summary["repeated_occurrences"])
                if phase_one_summary
                else 0
            ),
            "duplicates_within_windows": (
                int(phase_one_summary["duplicates_within_windows"])
                if phase_one_summary
                else 0
            ),
            "total_initial": total_initial,
            "total_final": total_final,
            "total_change_absolute": total_change,
            "total_change_ratio": total_change_ratio,
            "quality_events": list(dict.fromkeys(state.quality_events)),
            "diagnostic_outcome": diagnostic_outcome,
            "stop_reason": state.stop_reason,
        }
        validate_sanitized_summary(summary)
        return DiagnosticExecutionResult(summary=summary, exit_code=state.exit_code)


def serialize_diagnostic_summary(
    summary: Mapping[str, Any],
    *,
    max_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
) -> bytes:
    validate_sanitized_summary(summary)
    encoded = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True).encode(
        "utf-8"
    )
    if len(encoded) > max_bytes:
        raise ValueError("El artefacto diagnóstico supera el límite de 64 KiB")
    return encoded


def _finite_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def render_diagnostic_markdown(summary: Mapping[str, Any]) -> str:
    validate_sanitized_summary(summary)
    lines = [
        "# La Colonia — diagnóstico de ventanas",
        "",
        f"- request_id: `{summary['request_id']}`",
        f"- plan: `{summary['diagnostic_plan']}`",
        f"- completed: `{summary['completed']}`",
        f"- diagnostic_outcome: `{summary['diagnostic_outcome']}`",
        f"- stop_reason: `{summary['stop_reason']}`",
        f"- requests: `{summary['requests_completed']}/{summary['requests_planned']}`",
        "",
        "## Ventanas sanitizadas",
        "",
        "| Ventana | from | to | order_by | esperados | devueltos | SKU | total | bytes | firma | eventos |",
        "|---|---:|---:|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for window in summary.get("windows", []):
        events = ", ".join(window.get("quality_events", [])) or "ninguno"
        lines.append(
            "| {window} | {from_} | {to} | `{order}` | {expected} | {returned} | "
            "{skus} | {total} | {bytes_} | `{signature}` | {events} |".format(
                window=window["window"],
                from_=window["from"],
                to=window["to"],
                order=window["order_by"],
                expected=window["products_expected"],
                returned=window["products_returned"],
                skus=window["skus_returned"],
                total=window["records_filtered"],
                bytes_=window["response_bytes"],
                signature=window["signature"],
                events=events,
            )
        )
    lines.extend(
        [
            "",
            "## Agregados",
            "",
            f"- expected_unique_positions: `{summary['expected_unique_positions']}`",
            f"- products_unique_in_union: `{summary['products_unique_in_union']}`",
            f"- union_delta: `{summary['union_delta']}`",
            f"- repeated_occurrences: `{summary['repeated_occurrences']}`",
            f"- duplicates_within_windows: `{summary['duplicates_within_windows']}`",
            f"- total_initial: `{summary['total_initial']}`",
            f"- total_final: `{summary['total_final']}`",
            "",
            "El informe contiene únicamente métricas agregadas y firmas de ventanas completas.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_diagnostic_artifacts(
    result: DiagnosticExecutionResult,
    output_dir: Path,
    *,
    max_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
) -> None:
    json_bytes = serialize_diagnostic_summary(result.summary, max_bytes=max_bytes)
    markdown = render_diagnostic_markdown(result.summary).encode("utf-8")
    if len(markdown) > max_bytes:
        raise ValueError("El informe Markdown supera el límite de 64 KiB")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "diagnostic-summary.json").write_bytes(json_bytes)
    (output_dir / "diagnostic-summary.md").write_bytes(markdown)


def _derive_phase_one_findings(report: WindowDiagnosticReport | None) -> list[str]:
    if report is None:
        return []
    by_name = {item.window.name: item for item in report.observations}
    findings: list[str] = []
    c = by_name.get("C")
    f = by_name.get("F")
    g = by_name.get("G")
    h = by_name.get("H")

    if c and f and g and h:
        fg_unique = len(set(f._product_keys).union(g._product_keys))
        if (
            c.products_returned < c.window.width
            and f.products_returned == f.window.width
            and g.products_returned == g.window.width
            and fg_unique == 20
            and h.products_returned == h.window.width
        ):
            findings.append("window_size_dependent")
        partial_f = f.products_returned < f.window.width
        partial_g = g.products_returned < g.window.width
        if partial_f ^ partial_g:
            findings.append("localized_missing_position")

    if any(item.delta != 0 for item in report.overlaps):
        findings.append("unexpected_overlap")
    if report.union_delta < 0:
        findings.append("union_below_expected")
    if any("quality:partial_window" in item.quality_events for item in report.observations):
        findings.append("partial_window_observed")
    return list(dict.fromkeys(findings))


def _phase_two_required(
    report: WindowDiagnosticReport | None,
    findings: Sequence[str],
) -> bool:
    if report is None:
        return False
    if "quality:catalog_total_changed" in report.quality_events:
        return False
    has_anomaly = bool(findings)
    decisive = {
        "window_size_dependent",
        "localized_missing_position",
        "unexpected_overlap",
        "union_below_expected",
    }
    return has_anomaly and not any(item in decisive for item in findings)


def _derive_outcome(
    phase_one: WindowDiagnosticReport | None,
    phase_two: WindowDiagnosticReport | None,
    findings: Sequence[str],
) -> str:
    if phase_one is None:
        return "inconclusive"
    if "quality:catalog_total_changed" in phase_one.quality_events:
        return "catalog_changed"
    if phase_two is not None and _order_pattern_changed(phase_one, phase_two):
        return "order_dependent"
    for outcome in (
        "localized_missing_position",
        "window_size_dependent",
        "unexpected_overlap",
        "union_below_expected",
    ):
        if outcome in findings:
            return outcome
    if findings:
        return "inconclusive"
    return "no_anomaly_observed"


def _order_pattern_changed(
    phase_one: WindowDiagnosticReport,
    phase_two: WindowDiagnosticReport,
) -> bool:
    first = {item.window.name: item for item in phase_one.observations}
    second = {item.window.name: item for item in phase_two.observations}
    for name in ("C", "F", "G", "H"):
        left = first.get(name)
        right = second.get(name)
        if left is None or right is None:
            continue
        left_partial = left.products_returned < left.window.width
        right_partial = right.products_returned < right.window.width
        if left_partial != right_partial:
            return True
        if left.products_returned != right.products_returned:
            return True
    return False


def _sanitized_overlaps(
    phase_one: WindowDiagnosticReport | None,
    phase_two: WindowDiagnosticReport | None,
) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for label, report in (
        ("OrderByNameASC", phase_one),
        ("OrderByReleaseDateDESC", phase_two),
    ):
        if report is None:
            continue
        for item in report.overlaps:
            value = item.as_dict()
            value["left"] = f"{label}:{value['left']}"
            value["right"] = f"{label}:{value['right']}"
            values.append(value)
    return values


def _outcome_from_events(events: Sequence[str], exit_code: int) -> str:
    for prefix in reversed(events):
        if prefix.startswith("diagnostic:"):
            return prefix.split(":", 1)[1]
    if exit_code == EXIT_CATALOG_CHANGED:
        return "catalog_changed"
    if exit_code == EXIT_TECHNICAL_STOP:
        return "inconclusive"
    if exit_code == EXIT_SECURITY_FAILURE:
        return "security_failure"
    return "inconclusive"


def _validate_request_id(request_id: str) -> None:
    if not isinstance(request_id, str) or not REQUEST_ID_RE.fullmatch(request_id):
        raise ValueError("request_id no cumple el formato permitido")


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _extend_unique(values: list[str], new_values: Sequence[str]) -> None:
    for value in new_values:
        _append_unique(values, value)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock debe devolver una fecha con zona horaria")
    return value.astimezone(UTC)
