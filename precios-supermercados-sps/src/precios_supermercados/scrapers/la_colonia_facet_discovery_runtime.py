"""Runtime aislado y offline-auditable para ``facet_discovery``.

El runtime exige un transporte inyectado, ejecuta solo las dos solicitudes
lógicas del plan cerrado y nunca construye URLs ni consulta internet por sí
mismo.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .la_colonia_facet_discovery import (
    FACET_DISCOVERY_CONCURRENCY,
    FACET_DISCOVERY_DELAY_SECONDS,
    FACET_DISCOVERY_MAX_ARTIFACT_BYTES,
    FACET_DISCOVERY_MAX_REQUESTS,
    FACET_DISCOVERY_SCHEMA_VERSION,
    FacetBudget,
    FacetDiscoveryCommand,
    FacetDiscoveryPlan,
    FacetDiscoveryRequest,
    FacetPartitionLimitError,
    IncompleteFacetTreeError,
    InvalidFacetQuantitiesError,
    InvalidFacetStructureError,
    analyze_category_facets,
    estimate_facet_discovery_budget,
    get_facet_discovery_plan,
    parse_facet_discovery_command,
    read_root_total,
    serialize_sanitized_facet_summary,
    validate_sanitized_facet_summary,
)

OUTCOME_WITHIN_BUDGET = "within_budget"
OUTCOME_OVER_BUDGET = "over_budget"
OUTCOME_SAMPLING = "sampling_detected"
OUTCOME_INCOMPLETE_TREE = "incomplete_facet_tree"
OUTCOME_INVALID_QUANTITIES = "invalid_quantities"
OUTCOME_NO_POSITIVE = "no_positive_partitions"
OUTCOME_INCONCLUSIVE = "inconclusive"

_ALLOWED_OUTCOMES = frozenset(
    {
        OUTCOME_WITHIN_BUDGET,
        OUTCOME_OVER_BUDGET,
        OUTCOME_SAMPLING,
        OUTCOME_INCOMPLETE_TREE,
        OUTCOME_INVALID_QUANTITIES,
        OUTCOME_NO_POSITIVE,
        OUTCOME_INCONCLUSIVE,
    }
)

_ALLOWED_SUMMARY_KEYS = frozenset(
    {
        "schema_version",
        "request_id",
        "discovery_plan",
        "started_at",
        "finished_at",
        "requests_planned",
        "requests_attempted",
        "requests_completed",
        "delay_seconds_applied",
        "root_total",
        "sampling_detected",
        "facet_levels_detected",
        "facet_values_count",
        "leaf_partitions_count",
        "positive_leaf_partitions",
        "zero_quantity_partitions",
        "estimated_primary_requests",
        "estimated_probe_requests",
        "estimated_recovery_reserve",
        "estimated_reconciliation_requests",
        "estimated_total_requests",
        "request_limit",
        "within_request_limit",
        "discovery_completed",
        "discovery_outcome",
        "stop_reason",
        "quality_events",
    }
)


@dataclass(frozen=True, slots=True)
class FacetDiscoveryExecutionResult:
    summary: Mapping[str, Any]

    @property
    def accepted(self) -> bool:
        return self.summary.get("discovery_outcome") == OUTCOME_WITHIN_BUDGET


@dataclass(slots=True)
class _ExecutionState:
    command: FacetDiscoveryCommand
    plan: FacetDiscoveryPlan
    started_at: datetime
    requests_attempted: int = 0
    requests_completed: int = 0
    delay_seconds_applied: float = 0.0
    root_total: int = 0
    sampling_detected: bool = False
    facet_levels_detected: list[str] = field(default_factory=list)
    facet_values_count: dict[str, int] = field(default_factory=dict)
    leaf_partitions_count: int = 0
    positive_leaf_partitions: int = 0
    zero_quantity_partitions: int = 0
    budget: FacetBudget | None = None
    discovery_completed: bool = False
    outcome: str = OUTCOME_INCONCLUSIVE
    stop_reason: str = ""
    quality_events: list[str] = field(default_factory=list)


class FacetDiscoveryRuntime:
    """Ejecuta un plan cerrado con concurrencia uno y cero reintentos."""

    concurrency = FACET_DISCOVERY_CONCURRENCY

    def __init__(
        self,
        transport: Callable[[FacetDiscoveryRequest], Mapping[str, Any]],
        *,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] | None = None,
        max_retries: int = 0,
        max_requests: int = FACET_DISCOVERY_MAX_REQUESTS,
    ) -> None:
        if not callable(transport):
            raise ValueError("facet_discovery exige un transporte inyectado")
        if max_retries != 0:
            raise ValueError("facet_discovery exige max_retries=0")
        if not 1 <= max_requests <= FACET_DISCOVERY_MAX_REQUESTS:
            raise ValueError("max_requests debe estar entre 1 y 2")
        self.transport = transport
        self.sleeper = sleeper
        self.clock = clock or (lambda: datetime.now(UTC))
        self.max_retries = max_retries
        self.max_requests = max_requests

    def run(self, command_value: Mapping[str, Any]) -> FacetDiscoveryExecutionResult:
        command = parse_facet_discovery_command(command_value)
        plan = get_facet_discovery_plan(command.discovery_plan)
        state = _ExecutionState(
            command=command,
            plan=plan,
            started_at=_utc(self.clock()),
        )

        root_payload = self._request(state, plan.requests[0])
        if root_payload is None:
            return self._finish(state)
        try:
            state.root_total = read_root_total(root_payload)
        except InvalidFacetQuantitiesError:
            self._stop(state, OUTCOME_INVALID_QUANTITIES, "invalid_root_total")
            return self._finish(state)
        except InvalidFacetStructureError:
            self._stop(state, OUTCOME_INCONCLUSIVE, "invalid_root_structure")
            return self._finish(state)

        self.sleeper(FACET_DISCOVERY_DELAY_SECONDS)
        state.delay_seconds_applied += FACET_DISCOVERY_DELAY_SECONDS

        facet_payload = self._request(state, plan.requests[1])
        if facet_payload is None:
            return self._finish(state)
        try:
            analysis = analyze_category_facets(
                facet_payload,
                root_total=state.root_total,
                max_category_level=plan.max_category_level,
                max_partitions=plan.max_partitions,
            )
        except InvalidFacetQuantitiesError:
            self._stop(state, OUTCOME_INVALID_QUANTITIES, "invalid_quantities")
            return self._finish(state)
        except FacetPartitionLimitError:
            self._stop(state, OUTCOME_INCOMPLETE_TREE, "partition_limit_exceeded")
            return self._finish(state)
        except IncompleteFacetTreeError as exc:
            reason = str(exc)
            if "No existen particiones hoja positivas" in reason:
                self._stop(state, OUTCOME_NO_POSITIVE, "no_positive_partitions")
            else:
                self._stop(state, OUTCOME_INCOMPLETE_TREE, "incomplete_facet_tree")
            return self._finish(state)
        except InvalidFacetStructureError as exc:
            reason = str(exc)
            if "total cambió" in reason:
                self._stop(state, OUTCOME_INCONCLUSIVE, "catalog_total_changed")
            else:
                self._stop(state, OUTCOME_INCONCLUSIVE, "invalid_facet_structure")
            return self._finish(state)

        state.sampling_detected = analysis.sampling
        state.facet_levels_detected = list(analysis.facet_levels_detected)
        state.facet_values_count = dict(analysis.facet_values_count)
        state.leaf_partitions_count = analysis.leaf_partitions_count
        state.positive_leaf_partitions = analysis.positive_leaf_partitions
        state.zero_quantity_partitions = analysis.zero_quantity_partitions
        _extend_unique(state.quality_events, analysis.quality_events)

        if analysis.sampling:
            self._stop(state, OUTCOME_SAMPLING, "sampling_detected")
            return self._finish(state)

        state.budget = estimate_facet_discovery_budget(analysis, plan=plan)
        state.discovery_completed = True
        if state.budget.within_request_limit:
            state.outcome = OUTCOME_WITHIN_BUDGET
            state.stop_reason = ""
        else:
            state.outcome = OUTCOME_OVER_BUDGET
            state.stop_reason = "request_budget_exceeded"
            _append_unique(state.quality_events, "request_budget_exceeded")
        return self._finish(state)

    def _request(
        self,
        state: _ExecutionState,
        request: FacetDiscoveryRequest,
    ) -> Mapping[str, Any] | None:
        if state.requests_attempted >= self.max_requests:
            self._stop(
                state,
                OUTCOME_INCONCLUSIVE,
                "maximum_request_count_exceeded",
            )
            return None
        state.requests_attempted += 1
        try:
            payload = self.transport(request)
        except Exception:
            self._stop(state, OUTCOME_INCONCLUSIVE, "transport_failure")
            return None
        if not isinstance(payload, Mapping):
            self._stop(state, OUTCOME_INCONCLUSIVE, "invalid_transport_payload")
            return None
        state.requests_completed += 1
        return payload

    @staticmethod
    def _stop(state: _ExecutionState, outcome: str, reason: str) -> None:
        if outcome not in _ALLOWED_OUTCOMES:
            raise ValueError("Resultado facet_discovery desconocido")
        state.outcome = outcome
        state.stop_reason = reason
        state.discovery_completed = False
        _append_unique(state.quality_events, reason)

    def _finish(self, state: _ExecutionState) -> FacetDiscoveryExecutionResult:
        finished_at = _utc(self.clock())
        budget = state.budget
        summary: dict[str, Any] = {
            "schema_version": FACET_DISCOVERY_SCHEMA_VERSION,
            "request_id": state.command.request_id,
            "discovery_plan": state.plan.name,
            "started_at": state.started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "requests_planned": len(state.plan.requests),
            "requests_attempted": state.requests_attempted,
            "requests_completed": state.requests_completed,
            "delay_seconds_applied": state.delay_seconds_applied,
            "root_total": state.root_total,
            "sampling_detected": state.sampling_detected,
            "facet_levels_detected": state.facet_levels_detected,
            "facet_values_count": state.facet_values_count,
            "leaf_partitions_count": state.leaf_partitions_count,
            "positive_leaf_partitions": state.positive_leaf_partitions,
            "zero_quantity_partitions": state.zero_quantity_partitions,
            "estimated_primary_requests": budget.primary_requests if budget else 0,
            "estimated_probe_requests": budget.probe_requests if budget else 0,
            "estimated_recovery_reserve": budget.recovery_reserve if budget else 0,
            "estimated_reconciliation_requests": (
                budget.reconciliation_requests if budget else 0
            ),
            "estimated_total_requests": (
                budget.total_estimated_requests if budget else 0
            ),
            "request_limit": state.plan.request_limit,
            "within_request_limit": (
                budget.within_request_limit if budget else False
            ),
            "discovery_completed": state.discovery_completed,
            "discovery_outcome": state.outcome,
            "stop_reason": state.stop_reason,
            "quality_events": list(dict.fromkeys(state.quality_events)),
        }
        if set(summary) != _ALLOWED_SUMMARY_KEYS:
            raise ValueError("El resumen facet_discovery cambió su contrato")
        validate_sanitized_facet_summary(summary)
        return FacetDiscoveryExecutionResult(summary=summary)


def serialize_facet_discovery_summary(
    summary: Mapping[str, Any],
    *,
    max_bytes: int = FACET_DISCOVERY_MAX_ARTIFACT_BYTES,
) -> bytes:
    return serialize_sanitized_facet_summary(summary, max_bytes=max_bytes)


def render_facet_discovery_markdown(summary: Mapping[str, Any]) -> str:
    if set(summary) != _ALLOWED_SUMMARY_KEYS:
        raise ValueError("Resumen facet_discovery inválido")
    validate_sanitized_facet_summary(summary)
    levels = ", ".join(summary["facet_levels_detected"]) or "ninguno"
    counts = ", ".join(
        f"{key}={value}"
        for key, value in sorted(summary["facet_values_count"].items())
    ) or "ninguno"
    lines = [
        "# La Colonia — facet discovery sanitizado",
        "",
        f"- request_id: `{summary['request_id']}`",
        f"- plan: `{summary['discovery_plan']}`",
        f"- completed: `{summary['discovery_completed']}`",
        f"- outcome: `{summary['discovery_outcome']}`",
        f"- stop_reason: `{summary['stop_reason']}`",
        f"- requests: `{summary['requests_completed']}/{summary['requests_planned']}`",
        f"- root_total: `{summary['root_total']}`",
        f"- sampling_detected: `{summary['sampling_detected']}`",
        f"- facet_levels_detected: `{levels}`",
        f"- facet_values_count: `{counts}`",
        f"- leaf_partitions_count: `{summary['leaf_partitions_count']}`",
        f"- positive_leaf_partitions: `{summary['positive_leaf_partitions']}`",
        f"- zero_quantity_partitions: `{summary['zero_quantity_partitions']}`",
        "",
        "## Presupuesto",
        "",
        f"- primary: `{summary['estimated_primary_requests']}`",
        f"- probes: `{summary['estimated_probe_requests']}`",
        f"- recovery_reserve: `{summary['estimated_recovery_reserve']}`",
        f"- reconciliation: `{summary['estimated_reconciliation_requests']}`",
        f"- total: `{summary['estimated_total_requests']}`",
        f"- request_limit: `{summary['request_limit']}`",
        f"- within_request_limit: `{summary['within_request_limit']}`",
        "",
    ]
    rendered = "\n".join(lines)
    if len(rendered.encode("utf-8")) > FACET_DISCOVERY_MAX_ARTIFACT_BYTES:
        raise ValueError("El Markdown facet_discovery supera 64 KiB")
    return rendered


def _append_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


def _extend_unique(values: list[str], additions: Any) -> None:
    for value in additions:
        _append_unique(values, str(value))


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock debe devolver una fecha con zona horaria")
    return value.astimezone(UTC)
