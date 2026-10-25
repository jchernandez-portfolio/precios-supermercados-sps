"""Reconciliación externa de la sonda controlada con Workers Observability.

La evidencia criptográfica del Worker es necesaria pero no suficiente. Esta capa
consulta telemetría de plataforma mediante un transporte inyectado, exige un
custom span único y un único child ``fetch`` hacia el origen controlado y liga
ese span al recibo Ed25519 ya verificado.

Nada en este módulo concede autoridad de catálogo ni permite apuntar a La
Colonia. El resultado sólo demuestra propiedades de infraestructura de la sonda.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import NoReturn, Protocol

from precios_supermercados.cloudflare_controlled_probe_verifier import (
    CONTROLLED_PROBE_ORIGIN_PATH,
    CONTROLLED_PROBE_PURPOSE,
    VerifiedControlledProbeEvidence,
    verify_controlled_probe_artifact,
)
from precios_supermercados.edge_provenance import canonical_json_bytes

OBSERVABILITY_DATASET = "cloudflare-workers"
CONTROLLED_PROBE_SERVICE = "precios-sps-controlled-probe"
CONTROLLED_PROBE_SPAN_NAME = "precios_sps.cloudflare.controlled_origin_probe"
CONTROLLED_PROBE_TRACE_CONTRACT_VERSION = "1"
CONTROLLED_PROBE_TRACE_DOMAIN = b"precios-sps/cloudflare-controlled-probe-trace/v1\0"
DISCOVERY_QUERY_ID = "precios-sps-controlled-probe-trace-discovery-v1"
DETAIL_QUERY_ID = "precios-sps-controlled-probe-trace-detail-v1"
OBSERVABILITY_QUERY_PATH_TEMPLATE = "/accounts/{account_id}/workers/observability/telemetry/query"
QUERY_WINDOW_MARGIN = timedelta(seconds=30)
MAX_QUERY_WINDOW = timedelta(minutes=15)
MAX_QUERY_EVENTS = 100
MAX_DETAIL_QUERIES = 100
MAX_CLOCK_SKEW = timedelta(seconds=10)

_ACCOUNT_ID = re.compile(r"[0-9a-f]{32}\Z")
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_INTEGER_MAX = 2**53 - 1
_MISSING = object()

_PROBE_ATTRIBUTES = (
    "precios.probe_contract_version",
    "precios.probe_purpose",
    "precios.probe_id",
    "precios.approved_commit_sha",
    "precios.github_run_id",
    "precios.github_run_attempt",
    "precios.target_kind",
)


class ControlledProbeObservabilityError(ValueError):
    """La telemetría de sonda es inválida, ambigua o no reconcilia."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


def _fail(code: str, message: str | None = None) -> NoReturn:
    raise ControlledProbeObservabilityError(code, message)


def _mapping(value: object, code: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        _fail(code)
    return value


def _sequence(value: object, code: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        _fail(code)
    return value


def _text(value: object, code: str, *, maximum: int = 20_000, allow_spaces: bool = False) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or len(value) > maximum:
        _fail(code)
    if not allow_spaces and any(character.isspace() for character in value):
        _fail(code)
    return value


def _sha1(value: object, code: str) -> str:
    text = _text(value, code, maximum=40)
    if not _SHA1.fullmatch(text):
        _fail(code)
    return text


def _sha256(value: object, code: str) -> str:
    text = _text(value, code, maximum=64)
    if not _SHA256.fullmatch(text):
        _fail(code)
    return text


def _safe_integer(
    value: object,
    code: str,
    *,
    minimum: int = 0,
    maximum: int = _SAFE_INTEGER_MAX,
) -> int:
    if isinstance(value, bool):
        _fail(code)
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, float) and value.is_integer():
        parsed = int(value)
    elif isinstance(value, str) and value.isascii() and value.isdecimal():
        parsed = int(value)
    else:
        _fail(code)
    if not minimum <= parsed <= maximum:
        _fail(code)
    return parsed


def _utc(value: datetime, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(UTC)


def _iso_timestamp(value: object, code: str) -> datetime:
    text = _text(value, code, maximum=64)
    if not text.endswith("Z"):
        _fail(code)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise ControlledProbeObservabilityError(code) from exc
    return _utc(parsed, code)


def _epoch_ms(value: object, code: str) -> datetime:
    milliseconds = _safe_integer(value, code, minimum=1)
    try:
        return datetime.fromtimestamp(milliseconds / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise ControlledProbeObservabilityError(code) from exc


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _filter(key: str, value: str) -> dict[str, object]:
    return {"kind": "filter", "key": key, "operation": "eq", "type": "string", "value": value}


def _query_window(from_utc: datetime, to_utc: datetime) -> tuple[int, int]:
    start = _utc(from_utc, "probe_query_from_invalid")
    end = _utc(to_utc, "probe_query_to_invalid")
    if end <= start:
        _fail("probe_query_window_invalid")
    if end - start > MAX_QUERY_WINDOW:
        _fail("probe_query_window_too_wide")
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _query_payload(
    *,
    query_id: str,
    from_ms: int,
    to_ms: int,
    filters: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "queryId": query_id,
        "timeframe": {"from": from_ms, "to": to_ms},
        "view": "events",
        "limit": MAX_QUERY_EVENTS,
        "parameters": {
            "datasets": [OBSERVABILITY_DATASET],
            "filterCombination": "and",
            "filters": filters,
            "limit": MAX_QUERY_EVENTS,
        },
    }


def build_controlled_probe_trace_discovery_query(
    *,
    from_utc: datetime,
    to_utc: datetime,
    probe_id: str,
    approved_commit_sha: str,
    github_run_id: str,
    github_run_attempt: int,
    service_name: str = CONTROLLED_PROBE_SERVICE,
) -> dict[str, object]:
    """Localiza el custom span exacto de la sonda; el servidor no es autoridad."""

    from_ms, to_ms = _query_window(from_utc, to_utc)
    probe = _text(probe_id, "probe_id_invalid", maximum=128)
    commit = _sha1(approved_commit_sha, "probe_commit_sha_invalid")
    run_id = _text(github_run_id, "probe_run_id_invalid", maximum=64)
    run_attempt = _safe_integer(github_run_attempt, "probe_run_attempt_invalid", minimum=1, maximum=100)
    service = _text(service_name, "probe_service_name_invalid", maximum=256)
    return _query_payload(
        query_id=DISCOVERY_QUERY_ID,
        from_ms=from_ms,
        to_ms=to_ms,
        filters=[
            _filter("$metadata.service", service),
            _filter("$metadata.spanName", CONTROLLED_PROBE_SPAN_NAME),
            _filter("precios.probe_contract_version", CONTROLLED_PROBE_TRACE_CONTRACT_VERSION),
            _filter("precios.probe_id", probe),
            _filter("precios.approved_commit_sha", commit),
            _filter("precios.github_run_id", run_id),
            _filter("precios.github_run_attempt", str(run_attempt)),
        ],
    )


def build_controlled_probe_trace_detail_query(
    *,
    from_utc: datetime,
    to_utc: datetime,
    trace_id: str,
    service_name: str = CONTROLLED_PROBE_SERVICE,
) -> dict[str, object]:
    from_ms, to_ms = _query_window(from_utc, to_utc)
    trace = _text(trace_id, "probe_trace_id_invalid", maximum=512)
    service = _text(service_name, "probe_service_name_invalid", maximum=256)
    return _query_payload(
        query_id=DETAIL_QUERY_ID,
        from_ms=from_ms,
        to_ms=to_ms,
        filters=[
            _filter("$metadata.service", service),
            _filter("$metadata.traceId", trace),
        ],
    )


def _raw_events(payload: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    envelope = _mapping(payload, "probe_observability_response_invalid")
    if envelope.get("success") is not True:
        _fail("probe_observability_response_unsuccessful")
    if envelope.get("errors") not in (None, []):
        _fail("probe_observability_response_errors")
    result = _mapping(envelope.get("result"), "probe_observability_result_invalid")
    events_container = _mapping(result.get("events"), "probe_observability_events_container_invalid")
    events = _sequence(events_container.get("events"), "probe_observability_events_invalid")
    if len(events) > MAX_QUERY_EVENTS:
        _fail("probe_observability_events_above_limit")
    count = events_container.get("count")
    if count is not None and _safe_integer(count, "probe_observability_count_invalid") > len(events):
        _fail("probe_observability_events_truncated")

    normalized: list[Mapping[str, object]] = []
    for index, event in enumerate(events):
        item = _mapping(event, f"probe_event_{index}_invalid")
        if item.get("dataset") != OBSERVABILITY_DATASET:
            _fail(f"probe_event_{index}_dataset_invalid")
        metadata = _mapping(item.get("$metadata"), f"probe_event_{index}_metadata_invalid")
        if metadata.get("truncated") is True:
            _fail(f"probe_event_{index}_metadata_truncated")
        workers = item.get("$workers")
        if workers is not None:
            worker_map = _mapping(workers, f"probe_event_{index}_workers_invalid")
            if worker_map.get("truncated") is True:
                _fail(f"probe_event_{index}_truncated")
        normalized.append(item)
    return tuple(normalized)


def _source(event: Mapping[str, object]) -> Mapping[str, object] | None:
    value = event.get("source")
    if value is None or isinstance(value, str):
        return None
    return _mapping(value, "probe_event_source_invalid")


def _attribute_optional(event: Mapping[str, object], key: str) -> object:
    source = _source(event)
    source_value = source.get(key, _MISSING) if source is not None else _MISSING
    top_value = event.get(key, _MISSING)
    if source_value is not _MISSING and top_value is not _MISSING and source_value != top_value:
        _fail(f"probe_event_attribute_conflict:{key}")
    if source_value is not _MISSING:
        return source_value
    return top_value


def _attribute(event: Mapping[str, object], key: str, code: str) -> object:
    value = _attribute_optional(event, key)
    if value is _MISSING:
        _fail(code)
    return value


def _metadata(event: Mapping[str, object], prefix: str) -> Mapping[str, object]:
    return _mapping(event.get("$metadata"), f"{prefix}_metadata_invalid")


def _workers(event: Mapping[str, object], prefix: str) -> Mapping[str, object]:
    workers = _mapping(event.get("$workers"), f"{prefix}_workers_invalid")
    if workers.get("truncated") is True:
        _fail(f"{prefix}_truncated")
    return workers


def _standard_span_identity(
    event: Mapping[str, object],
    *,
    prefix: str,
) -> tuple[str, str, str, str, str]:
    metadata = _metadata(event, prefix)
    workers = _workers(event, prefix)
    trace_id = _text(metadata.get("traceId"), f"{prefix}_trace_id_invalid", maximum=512)
    span_id = _text(metadata.get("spanId"), f"{prefix}_span_id_invalid", maximum=512)
    invocation_id = _text(
        _attribute(event, "faas.invocation_id", f"{prefix}_invocation_id_invalid"),
        f"{prefix}_invocation_id_invalid",
        maximum=512,
    )
    service = _text(
        _attribute(event, "service.name", f"{prefix}_service_invalid"),
        f"{prefix}_service_invalid",
        maximum=256,
    )
    version = _text(
        _attribute(event, "cloudflare.script_version.id", f"{prefix}_version_invalid"),
        f"{prefix}_version_invalid",
        maximum=512,
    )
    if _attribute(event, "cloud.provider", f"{prefix}_cloud_provider_invalid") != "cloudflare":
        _fail(f"{prefix}_cloud_provider_invalid")
    if _attribute(event, "cloud.platform", f"{prefix}_cloud_platform_invalid") != "cloudflare.workers":
        _fail(f"{prefix}_cloud_platform_invalid")
    if metadata.get("service") != service:
        _fail(f"{prefix}_service_metadata_mismatch")
    if workers.get("scriptName") not in (None, service):
        _fail(f"{prefix}_worker_script_mismatch")
    script_version = workers.get("scriptVersion")
    if script_version is not None:
        script_map = _mapping(script_version, f"{prefix}_worker_version_invalid")
        if script_map.get("id") != version:
            _fail(f"{prefix}_worker_version_mismatch")
    return trace_id, span_id, invocation_id, service, version


def _span_times(event: Mapping[str, object], prefix: str) -> tuple[datetime, datetime]:
    metadata = _metadata(event, prefix)
    start = _epoch_ms(metadata.get("startTime"), f"{prefix}_start_time_invalid")
    end = _epoch_ms(metadata.get("endTime"), f"{prefix}_end_time_invalid")
    if end < start:
        _fail(f"{prefix}_time_order_invalid")
    return start, end


def _probe_context(event: Mapping[str, object]) -> dict[str, str]:
    values: dict[str, str] = {}
    for key in _PROBE_ATTRIBUTES:
        values[key] = _text(
            _attribute(event, key, f"probe_attribute_{key}_invalid"),
            f"probe_attribute_{key}_invalid",
            maximum=512,
        )
    if values["precios.probe_contract_version"] != CONTROLLED_PROBE_TRACE_CONTRACT_VERSION:
        _fail("probe_trace_contract_version_mismatch")
    if values["precios.probe_purpose"] != CONTROLLED_PROBE_PURPOSE:
        _fail("probe_trace_purpose_mismatch")
    if values["precios.target_kind"] != "controlled_workers_dev_origin":
        _fail("probe_trace_target_kind_mismatch")
    _sha1(values["precios.approved_commit_sha"], "probe_trace_commit_sha_invalid")
    _safe_integer(values["precios.github_run_attempt"], "probe_trace_run_attempt_invalid", minimum=1, maximum=100)
    return values


@dataclass(frozen=True, slots=True)
class ControlledProbeTraceEvidence:
    """Custom span de sonda y su único fetch físico observado por Cloudflare."""

    trace_id: str
    custom_span_id: str
    fetch_span_id: str
    fetch_parent_span_id: str
    faas_invocation_id: str
    service_name: str
    script_version_id: str
    probe_id: str
    approved_commit_sha: str
    github_run_id: str
    github_run_attempt: int
    fetch_url: str
    fetch_method: str
    fetch_status: int
    fetch_response_body_size: int
    custom_started_at_utc: datetime
    custom_completed_at_utc: datetime
    fetch_started_at_utc: datetime
    fetch_completed_at_utc: datetime
    trace_contract_version: str = CONTROLLED_PROBE_TRACE_CONTRACT_VERSION
    purpose: str = CONTROLLED_PROBE_PURPOSE
    production_authority: bool = False
    catalog_accepted: bool = False

    def __post_init__(self) -> None:
        for name in (
            "trace_id",
            "custom_span_id",
            "fetch_span_id",
            "fetch_parent_span_id",
            "faas_invocation_id",
            "service_name",
            "script_version_id",
            "probe_id",
            "github_run_id",
            "fetch_url",
        ):
            _text(getattr(self, name), f"probe_trace_{name}_invalid")
        _sha1(self.approved_commit_sha, "probe_trace_commit_sha_invalid")
        _safe_integer(self.github_run_attempt, "probe_trace_run_attempt_invalid", minimum=1, maximum=100)
        _safe_integer(self.fetch_status, "probe_trace_fetch_status_invalid", minimum=100, maximum=599)
        _safe_integer(self.fetch_response_body_size, "probe_trace_fetch_size_invalid")
        if self.trace_contract_version != CONTROLLED_PROBE_TRACE_CONTRACT_VERSION:
            _fail("probe_trace_contract_version_invalid")
        if self.purpose != CONTROLLED_PROBE_PURPOSE:
            _fail("probe_trace_purpose_invalid")
        if self.fetch_parent_span_id != self.custom_span_id or self.fetch_span_id == self.custom_span_id:
            _fail("probe_trace_parent_identity_invalid")
        if self.fetch_method != "GET":
            _fail("probe_trace_fetch_method_invalid")
        custom_start = _utc(self.custom_started_at_utc, "probe_trace_custom_start_invalid")
        custom_end = _utc(self.custom_completed_at_utc, "probe_trace_custom_end_invalid")
        fetch_start = _utc(self.fetch_started_at_utc, "probe_trace_fetch_start_invalid")
        fetch_end = _utc(self.fetch_completed_at_utc, "probe_trace_fetch_end_invalid")
        if custom_end < custom_start or fetch_end < fetch_start:
            _fail("probe_trace_time_order_invalid")
        if fetch_start < custom_start or fetch_end > custom_end:
            _fail("probe_trace_fetch_outside_custom_span")
        object.__setattr__(self, "custom_started_at_utc", custom_start)
        object.__setattr__(self, "custom_completed_at_utc", custom_end)
        object.__setattr__(self, "fetch_started_at_utc", fetch_start)
        object.__setattr__(self, "fetch_completed_at_utc", fetch_end)
        if self.production_authority is not False or self.catalog_accepted is not False:
            _fail("probe_trace_authority_forbidden")

    def canonical_dict(self) -> dict[str, object]:
        return {
            "approved_commit_sha": self.approved_commit_sha,
            "catalog_accepted": False,
            "custom_completed_at_utc": _timestamp(self.custom_completed_at_utc),
            "custom_span_id": self.custom_span_id,
            "custom_started_at_utc": _timestamp(self.custom_started_at_utc),
            "faas_invocation_id": self.faas_invocation_id,
            "fetch_completed_at_utc": _timestamp(self.fetch_completed_at_utc),
            "fetch_method": self.fetch_method,
            "fetch_parent_span_id": self.fetch_parent_span_id,
            "fetch_response_body_size": self.fetch_response_body_size,
            "fetch_span_id": self.fetch_span_id,
            "fetch_started_at_utc": _timestamp(self.fetch_started_at_utc),
            "fetch_status": self.fetch_status,
            "fetch_url": self.fetch_url,
            "github_run_attempt": self.github_run_attempt,
            "github_run_id": self.github_run_id,
            "probe_id": self.probe_id,
            "production_authority": False,
            "purpose": self.purpose,
            "script_version_id": self.script_version_id,
            "service_name": self.service_name,
            "trace_contract_version": self.trace_contract_version,
            "trace_id": self.trace_id,
        }

    @property
    def physical_evidence_id(self) -> str:
        return hashlib.sha256(
            CONTROLLED_PROBE_TRACE_DOMAIN + canonical_json_bytes(self.canonical_dict())
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class PlatformReconciledControlledProbe:
    verified_probe: VerifiedControlledProbeEvidence
    trace_evidence: ControlledProbeTraceEvidence
    platform_evidence_reconciled: bool = True
    production_authority: bool = False
    catalog_accepted: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.verified_probe, VerifiedControlledProbeEvidence):
            _fail("probe_platform_verified_evidence_invalid")
        if not isinstance(self.trace_evidence, ControlledProbeTraceEvidence):
            _fail("probe_platform_trace_evidence_invalid")
        if self.platform_evidence_reconciled is not True:
            _fail("probe_platform_reconciliation_state_invalid")
        if self.production_authority is not False or self.catalog_accepted is not False:
            _fail("probe_platform_authority_forbidden")

    @property
    def physical_evidence_id(self) -> str:
        return self.trace_evidence.physical_evidence_id


def parse_controlled_probe_trace_discovery_response(
    payload: Mapping[str, object],
    *,
    probe_id: str,
    approved_commit_sha: str,
    github_run_id: str,
    github_run_attempt: int,
    service_name: str = CONTROLLED_PROBE_SERVICE,
) -> tuple[str, ...]:
    probe = _text(probe_id, "probe_id_invalid", maximum=128)
    commit = _sha1(approved_commit_sha, "probe_commit_sha_invalid")
    run_id = _text(github_run_id, "probe_run_id_invalid", maximum=64)
    run_attempt = _safe_integer(github_run_attempt, "probe_run_attempt_invalid", minimum=1, maximum=100)
    service = _text(service_name, "probe_service_name_invalid", maximum=256)
    trace_ids: list[str] = []
    for index, event in enumerate(_raw_events(payload)):
        metadata = _metadata(event, f"probe_discovery_{index}")
        if metadata.get("spanName") != CONTROLLED_PROBE_SPAN_NAME:
            _fail("probe_discovery_span_name_mismatch")
        context = _probe_context(event)
        if context["precios.probe_id"] != probe:
            _fail("probe_discovery_probe_id_mismatch")
        if context["precios.approved_commit_sha"] != commit:
            _fail("probe_discovery_commit_mismatch")
        if context["precios.github_run_id"] != run_id:
            _fail("probe_discovery_run_id_mismatch")
        if int(context["precios.github_run_attempt"]) != run_attempt:
            _fail("probe_discovery_run_attempt_mismatch")
        trace_id, _span_id, _invocation, observed_service, _version = _standard_span_identity(
            event,
            prefix=f"probe_discovery_{index}",
        )
        if observed_service != service:
            _fail("probe_discovery_service_mismatch")
        trace_ids.append(trace_id)
    if not trace_ids:
        _fail("probe_discovery_trace_missing")
    if len(trace_ids) != 1:
        _fail("probe_discovery_trace_not_unique")
    return tuple(trace_ids)


def _is_fetch_child(event: Mapping[str, object], custom_span_id: str, trace_id: str) -> bool:
    metadata = _metadata(event, "probe_detail")
    if metadata.get("parentSpanId") != custom_span_id or metadata.get("traceId") != trace_id:
        return False
    required = (
        "url.full",
        "http.request.method",
        "http.response.status_code",
        "http.response.body.size",
    )
    return all(_attribute_optional(event, key) is not _MISSING for key in required)


def parse_controlled_probe_trace_detail_response(
    payload: Mapping[str, object],
    *,
    expected_trace_id: str,
    service_name: str = CONTROLLED_PROBE_SERVICE,
) -> tuple[ControlledProbeTraceEvidence, ...]:
    expected = _text(expected_trace_id, "probe_expected_trace_id_invalid", maximum=512)
    service = _text(service_name, "probe_service_name_invalid", maximum=256)
    events = _raw_events(payload)
    for event in events:
        metadata = _metadata(event, "probe_detail")
        if metadata.get("traceId") not in (None, expected):
            _fail("probe_detail_trace_id_mismatch")
        if metadata.get("service") not in (None, service):
            _fail("probe_detail_service_mismatch")

    custom_events = [
        event for event in events if _metadata(event, "probe_detail").get("spanName") == CONTROLLED_PROBE_SPAN_NAME
    ]
    if len(custom_events) != 1:
        _fail("probe_custom_span_not_unique")
    custom = custom_events[0]
    custom_metadata = _metadata(custom, "probe_custom")
    custom_trace_id = _text(custom_metadata.get("traceId"), "probe_custom_trace_id_invalid", maximum=512)
    if custom_trace_id != expected:
        _fail("probe_custom_trace_id_mismatch")
    custom_span_id = _text(custom_metadata.get("spanId"), "probe_custom_span_id_invalid", maximum=512)
    fetch_candidates = [event for event in events if _is_fetch_child(event, custom_span_id, expected)]
    if len(fetch_candidates) != 1:
        _fail("probe_origin_fetch_span_not_unique")
    fetch = fetch_candidates[0]

    custom_trace, custom_span, custom_invocation, custom_service, custom_version = _standard_span_identity(
        custom,
        prefix="probe_custom",
    )
    fetch_trace, fetch_span, fetch_invocation, fetch_service, fetch_version = _standard_span_identity(
        fetch,
        prefix="probe_fetch",
    )
    fetch_metadata = _metadata(fetch, "probe_fetch")
    if fetch_trace != custom_trace or fetch_metadata.get("parentSpanId") != custom_span:
        _fail("probe_fetch_parent_mismatch")
    if fetch_invocation != custom_invocation or fetch_service != custom_service or fetch_version != custom_version:
        _fail("probe_fetch_execution_identity_mismatch")
    if custom_service != service:
        _fail("probe_custom_service_mismatch")

    context = _probe_context(custom)
    custom_start, custom_end = _span_times(custom, "probe_custom")
    fetch_start, fetch_end = _span_times(fetch, "probe_fetch")
    evidence = ControlledProbeTraceEvidence(
        trace_id=custom_trace,
        custom_span_id=custom_span,
        fetch_span_id=fetch_span,
        fetch_parent_span_id=_text(fetch_metadata.get("parentSpanId"), "probe_fetch_parent_invalid", maximum=512),
        faas_invocation_id=custom_invocation,
        service_name=custom_service,
        script_version_id=custom_version,
        probe_id=context["precios.probe_id"],
        approved_commit_sha=context["precios.approved_commit_sha"],
        github_run_id=context["precios.github_run_id"],
        github_run_attempt=_safe_integer(
            context["precios.github_run_attempt"],
            "probe_trace_run_attempt_invalid",
            minimum=1,
            maximum=100,
        ),
        fetch_url=_text(_attribute(fetch, "url.full", "probe_fetch_url_invalid"), "probe_fetch_url_invalid"),
        fetch_method=_text(
            _attribute(fetch, "http.request.method", "probe_fetch_method_invalid"),
            "probe_fetch_method_invalid",
            maximum=16,
        ),
        fetch_status=_safe_integer(
            _attribute(fetch, "http.response.status_code", "probe_fetch_status_invalid"),
            "probe_fetch_status_invalid",
            minimum=100,
            maximum=599,
        ),
        fetch_response_body_size=_safe_integer(
            _attribute(fetch, "http.response.body.size", "probe_fetch_size_invalid"),
            "probe_fetch_size_invalid",
        ),
        custom_started_at_utc=custom_start,
        custom_completed_at_utc=custom_end,
        fetch_started_at_utc=fetch_start,
        fetch_completed_at_utc=fetch_end,
    )
    return (evidence,)


def _signed_receipt_context(artifact: Mapping[str, object]) -> Mapping[str, object]:
    top = _mapping(artifact, "probe_artifact_invalid")
    return _mapping(top.get("receiptPayload"), "probe_receipt_invalid")


def reconcile_controlled_probe_trace(
    verified_probe: VerifiedControlledProbeEvidence,
    artifact: Mapping[str, object],
    candidates: Sequence[ControlledProbeTraceEvidence],
    *,
    clock_skew: timedelta = MAX_CLOCK_SKEW,
) -> PlatformReconciledControlledProbe:
    if not isinstance(verified_probe, VerifiedControlledProbeEvidence):
        _fail("probe_verified_evidence_invalid")
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes, bytearray)):
        _fail("probe_trace_candidates_invalid")
    if not isinstance(clock_skew, timedelta) or clock_skew < timedelta(0) or clock_skew > timedelta(minutes=1):
        _fail("probe_clock_skew_invalid")

    receipt = _signed_receipt_context(artifact)
    if artifact.get("evidenceId") != verified_probe.evidence_id:
        _fail("probe_artifact_verified_identity_mismatch")
    expected_commit = _sha1(receipt.get("approved_commit_sha"), "probe_receipt_commit_invalid")
    expected_run_id = _text(receipt.get("github_run_id"), "probe_receipt_run_id_invalid", maximum=64)
    expected_attempt = _safe_integer(
        receipt.get("github_run_attempt"),
        "probe_receipt_run_attempt_invalid",
        minimum=1,
        maximum=100,
    )
    expected_probe_id = _text(receipt.get("probe_id"), "probe_receipt_probe_id_invalid", maximum=128)
    expected_host = _text(receipt.get("target_host"), "probe_receipt_target_host_invalid", maximum=512)
    expected_release = _text(receipt.get("collector_release_id"), "probe_receipt_release_invalid", maximum=512)
    expected_size = _safe_integer(receipt.get("response_body_bytes"), "probe_receipt_size_invalid", minimum=1)
    expected_start = _iso_timestamp(receipt.get("physical_started_at_utc"), "probe_receipt_start_invalid")
    expected_end = _iso_timestamp(receipt.get("response_completed_at_utc"), "probe_receipt_end_invalid")

    matching = [
        item
        for item in candidates
        if isinstance(item, ControlledProbeTraceEvidence)
        and item.probe_id == expected_probe_id
        and item.approved_commit_sha == expected_commit
        and item.github_run_id == expected_run_id
        and item.github_run_attempt == expected_attempt
    ]
    if len(matching) != 1:
        _fail("probe_matching_trace_not_unique")
    evidence = matching[0]
    if verified_probe.probe_id != expected_probe_id or verified_probe.github_run_id != expected_run_id:
        _fail("probe_verified_context_mismatch")
    if verified_probe.github_run_attempt != expected_attempt:
        _fail("probe_verified_attempt_mismatch")
    if verified_probe.target_host != expected_host or verified_probe.collector_release_id != expected_release:
        _fail("probe_verified_receipt_binding_mismatch")

    expected_url = f"https://{expected_host}{CONTROLLED_PROBE_ORIGIN_PATH}"
    if evidence.fetch_url != expected_url:
        _fail("probe_trace_fetch_url_mismatch")
    if evidence.fetch_method != "GET" or evidence.fetch_status != 200:
        _fail("probe_trace_fetch_http_mismatch")
    if evidence.fetch_response_body_size != expected_size:
        _fail("probe_trace_fetch_body_size_mismatch")
    if evidence.script_version_id != expected_release:
        _fail("probe_trace_script_version_mismatch")
    if evidence.service_name != CONTROLLED_PROBE_SERVICE:
        _fail("probe_trace_service_mismatch")
    if evidence.fetch_started_at_utc < expected_start - clock_skew:
        _fail("probe_trace_fetch_started_too_early")
    if evidence.fetch_started_at_utc > expected_end + clock_skew:
        _fail("probe_trace_fetch_started_too_late")
    if evidence.fetch_completed_at_utc < expected_start - clock_skew:
        _fail("probe_trace_fetch_completed_too_early")
    if evidence.fetch_completed_at_utc > expected_end + clock_skew:
        _fail("probe_trace_fetch_completed_too_late")
    return PlatformReconciledControlledProbe(
        verified_probe=verified_probe,
        trace_evidence=evidence,
    )


class ControlledProbeObservabilityTransport(Protocol):
    def post_json(
        self,
        path: str,
        *,
        bearer_token: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]: ...


def _bearer(value: object) -> str:
    return _text(value, "probe_observability_bearer_invalid", maximum=20_000)


class ControlledProbeObservabilityVerifierClient:
    """Verifica receipt + telemetría de plataforma sin conceder autoridad productiva."""

    def __init__(self, account_id: str, transport: ControlledProbeObservabilityTransport) -> None:
        if not isinstance(account_id, str) or not _ACCOUNT_ID.fullmatch(account_id):
            _fail("probe_observability_account_id_invalid")
        if transport is None or not callable(getattr(transport, "post_json", None)):
            _fail("probe_observability_transport_invalid")
        self._account_id = account_id
        self._transport = transport
        self._path = OBSERVABILITY_QUERY_PATH_TEMPLATE.format(account_id=account_id)

    @property
    def query_path(self) -> str:
        return self._path

    def _post(self, payload: Mapping[str, object], *, bearer_token: str) -> Mapping[str, object]:
        try:
            response = self._transport.post_json(
                self._path,
                bearer_token=_bearer(bearer_token),
                payload=payload,
            )
        except ControlledProbeObservabilityError:
            raise
        except Exception as exc:
            raise ControlledProbeObservabilityError("probe_observability_transport_error") from exc
        if not isinstance(response, Mapping):
            _fail("probe_observability_transport_response_invalid")
        return response

    def reconcile_artifact(
        self,
        artifact: Mapping[str, object],
        *,
        public_key_spki_b64url: str,
        expected_commit_sha: str,
        expected_run_id: str,
        expected_run_attempt: int,
        bearer_token: str,
    ) -> PlatformReconciledControlledProbe:
        verified = verify_controlled_probe_artifact(
            artifact,
            public_key_spki_b64url=public_key_spki_b64url,
            expected_commit_sha=expected_commit_sha,
            expected_run_id=expected_run_id,
            expected_run_attempt=expected_run_attempt,
        )
        receipt = _signed_receipt_context(artifact)
        start = _iso_timestamp(receipt.get("physical_started_at_utc"), "probe_receipt_start_invalid")
        end = _iso_timestamp(receipt.get("response_completed_at_utc"), "probe_receipt_end_invalid")
        from_utc = start - QUERY_WINDOW_MARGIN
        to_utc = end + QUERY_WINDOW_MARGIN
        if to_utc - from_utc > MAX_QUERY_WINDOW:
            _fail("probe_receipt_observability_window_too_wide")

        discovery = self._post(
            build_controlled_probe_trace_discovery_query(
                from_utc=from_utc,
                to_utc=to_utc,
                probe_id=verified.probe_id,
                approved_commit_sha=expected_commit_sha,
                github_run_id=expected_run_id,
                github_run_attempt=expected_run_attempt,
            ),
            bearer_token=bearer_token,
        )
        trace_ids = parse_controlled_probe_trace_discovery_response(
            discovery,
            probe_id=verified.probe_id,
            approved_commit_sha=expected_commit_sha,
            github_run_id=expected_run_id,
            github_run_attempt=expected_run_attempt,
        )
        if len(trace_ids) > MAX_DETAIL_QUERIES:
            _fail("probe_trace_count_above_limit")

        candidates: list[ControlledProbeTraceEvidence] = []
        for trace_id in trace_ids:
            detail = self._post(
                build_controlled_probe_trace_detail_query(
                    from_utc=from_utc,
                    to_utc=to_utc,
                    trace_id=trace_id,
                ),
                bearer_token=bearer_token,
            )
            candidates.extend(
                parse_controlled_probe_trace_detail_response(
                    detail,
                    expected_trace_id=trace_id,
                )
            )
        return reconcile_controlled_probe_trace(verified, artifact, candidates)
