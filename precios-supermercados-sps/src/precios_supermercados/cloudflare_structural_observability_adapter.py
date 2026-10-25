"""Adapter offline de Workers Observability para facet discovery estructural.

No realiza HTTP ni contiene credenciales. Convierte únicamente evidencia de
telemetría ya obtenida en ``CloudflareStructuralTraceEvidence`` y revalida cada
atributo relevante; los filtros de la API nunca se consideran autoridad.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import NoReturn

from precios_supermercados.cloudflare_structural_trace_evidence import (
    CLOUD_PLATFORM,
    CLOUD_PROVIDER,
    STRUCTURAL_EXECUTION_SPAN_NAME,
    STRUCTURAL_TRACE_CONTRACT_VERSION,
    CloudflareStructuralTraceEvidence,
)

OBSERVABILITY_DATASET = "cloudflare-workers"
STRUCTURAL_DISCOVERY_QUERY_ID = "precios-sps-structural-trace-discovery-v1"
STRUCTURAL_DETAIL_QUERY_ID = "precios-sps-structural-trace-detail-v1"
DEFAULT_WORKER_SERVICE = "precios-sps-provenance"
MAX_QUERY_EVENTS = 100
MAX_QUERY_WINDOW = timedelta(minutes=15)
_SAFE_INTEGER_MAX = 2**53 - 1
_MISSING = object()
_ALLOWED_KINDS = {"root_total", "category_tree"}

_CUSTOM_ATTRIBUTES = (
    "precios.trace_contract_version",
    "precios.collector_provider",
    "precios.authorization_id",
    "precios.run_id",
    "precios.approved_commit_sha",
    "precios.reservation_id",
    "precios.request_id",
    "precios.request_digest",
    "precios.request_kind",
)


class CloudflareStructuralObservabilityAdapterError(ValueError):
    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


def _fail(code: str, message: str | None = None) -> NoReturn:
    raise CloudflareStructuralObservabilityAdapterError(code, message)


def _mapping(value: object, code: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        _fail(code)
    return value


def _sequence(value: object, code: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        _fail(code)
    return value


def _text(value: object, code: str, *, maximum: int = 20_000) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or len(value) > maximum
        or any(character.isspace() for character in value)
    ):
        _fail(code)
    return value


def _kind(value: object, code: str = "request_kind_invalid") -> str:
    result = _text(value, code, maximum=64)
    if result not in _ALLOWED_KINDS:
        _fail(code)
    return result


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
    else:
        _fail(code)
    if not minimum <= parsed <= maximum:
        _fail(code)
    return parsed


def _utc(value: object, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(UTC)


def _epoch_ms(value: object, code: str) -> datetime:
    milliseconds = _safe_integer(value, code, minimum=1)
    try:
        return datetime.fromtimestamp(milliseconds / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise CloudflareStructuralObservabilityAdapterError(code) from exc


def _filter(key: str, value: str) -> dict[str, object]:
    return {
        "kind": "filter",
        "key": key,
        "operation": "eq",
        "type": "string",
        "value": value,
    }


def _query_window(from_utc: datetime, to_utc: datetime) -> tuple[int, int]:
    start = _utc(from_utc, "query_from_invalid")
    end = _utc(to_utc, "query_to_invalid")
    if end <= start:
        _fail("query_window_invalid")
    if end - start > MAX_QUERY_WINDOW:
        _fail("query_window_too_wide")
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


def build_cloudflare_structural_trace_discovery_query(
    *,
    from_utc: datetime,
    to_utc: datetime,
    authorization_id: str,
    run_id: str,
    request_kind: str,
    service_name: str = DEFAULT_WORKER_SERVICE,
) -> dict[str, object]:
    """Localiza sólo el custom span del request estructural esperado."""

    from_ms, to_ms = _query_window(from_utc, to_utc)
    authorization = _text(authorization_id, "authorization_id_invalid", maximum=128)
    run = _text(run_id, "run_id_invalid", maximum=256)
    kind = _kind(request_kind)
    service = _text(service_name, "service_name_invalid", maximum=256)
    return _query_payload(
        query_id=STRUCTURAL_DISCOVERY_QUERY_ID,
        from_ms=from_ms,
        to_ms=to_ms,
        filters=[
            _filter("$metadata.service", service),
            _filter("$metadata.spanName", STRUCTURAL_EXECUTION_SPAN_NAME),
            _filter("precios.authorization_id", authorization),
            _filter("precios.run_id", run),
            _filter("precios.request_kind", kind),
            _filter("precios.trace_contract_version", STRUCTURAL_TRACE_CONTRACT_VERSION),
        ],
    )


def build_cloudflare_structural_trace_detail_query(
    *,
    from_utc: datetime,
    to_utc: datetime,
    trace_id: str,
    service_name: str = DEFAULT_WORKER_SERVICE,
) -> dict[str, object]:
    from_ms, to_ms = _query_window(from_utc, to_utc)
    trace = _text(trace_id, "trace_id_invalid", maximum=512)
    service = _text(service_name, "service_name_invalid", maximum=256)
    return _query_payload(
        query_id=STRUCTURAL_DETAIL_QUERY_ID,
        from_ms=from_ms,
        to_ms=to_ms,
        filters=[
            _filter("$metadata.service", service),
            _filter("$metadata.traceId", trace),
        ],
    )


def _raw_events(payload: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    envelope = _mapping(payload, "observability_response_invalid")
    if envelope.get("success") is not True:
        _fail("observability_response_unsuccessful")
    errors = envelope.get("errors")
    if errors not in (None, []):
        _fail("observability_response_errors")

    result = _mapping(envelope.get("result"), "observability_result_invalid")
    events_container = _mapping(result.get("events"), "observability_events_container_invalid")
    events = _sequence(events_container.get("events"), "observability_events_invalid")
    if len(events) > MAX_QUERY_EVENTS:
        _fail("observability_events_above_limit")
    count = events_container.get("count")
    if count is not None and _safe_integer(count, "observability_events_count_invalid") > len(events):
        _fail("observability_events_truncated")

    normalized: list[Mapping[str, object]] = []
    for index, event in enumerate(events):
        item = _mapping(event, f"event_{index}_invalid")
        if item.get("dataset") != OBSERVABILITY_DATASET:
            _fail(f"event_{index}_dataset_invalid")
        metadata = _mapping(item.get("$metadata"), f"event_{index}_metadata_invalid")
        workers = item.get("$workers")
        if workers is not None:
            worker_map = _mapping(workers, f"event_{index}_workers_invalid")
            if worker_map.get("truncated") is True:
                _fail(f"event_{index}_truncated")
        if metadata.get("truncated") is True:
            _fail(f"event_{index}_metadata_truncated")
        normalized.append(item)
    return tuple(normalized)


def _source_mapping(event: Mapping[str, object]) -> Mapping[str, object] | None:
    source = event.get("source")
    if source is None or isinstance(source, str):
        return None
    return _mapping(source, "event_source_invalid")


def _attribute_optional(event: Mapping[str, object], key: str) -> object:
    source = _source_mapping(event)
    source_value = source.get(key, _MISSING) if source is not None else _MISSING
    top_value = event.get(key, _MISSING)
    if source_value is not _MISSING and top_value is not _MISSING and source_value != top_value:
        _fail(f"event_attribute_conflict:{key}")
    if source_value is not _MISSING:
        return source_value
    if top_value is not _MISSING:
        return top_value
    return _MISSING


def _attribute(event: Mapping[str, object], key: str, code: str) -> object:
    value = _attribute_optional(event, key)
    if value is _MISSING:
        _fail(code)
    return value


def _metadata(event: Mapping[str, object], *, prefix: str = "event") -> Mapping[str, object]:
    return _mapping(event.get("$metadata"), f"{prefix}_metadata_invalid")


def _workers(event: Mapping[str, object], *, prefix: str) -> Mapping[str, object]:
    workers = _mapping(event.get("$workers"), f"{prefix}_workers_invalid")
    if workers.get("truncated") is True:
        _fail(f"{prefix}_truncated")
    return workers


def _custom_span_context(event: Mapping[str, object]) -> dict[str, str]:
    values: dict[str, str] = {}
    for key in _CUSTOM_ATTRIBUTES:
        values[key] = _text(
            _attribute(event, key, f"custom_attribute_{key}_invalid"),
            f"custom_attribute_{key}_invalid",
            maximum=512,
        )
    if values["precios.trace_contract_version"] != STRUCTURAL_TRACE_CONTRACT_VERSION:
        _fail("custom_trace_contract_version_invalid")
    if values["precios.collector_provider"] != "cloudflare_workers":
        _fail("custom_collector_provider_invalid")
    _kind(values["precios.request_kind"], "custom_request_kind_invalid")
    return values


def _standard_span_identity(
    event: Mapping[str, object],
    *,
    prefix: str,
) -> tuple[str, str, str, str, str]:
    metadata = _metadata(event, prefix=prefix)
    workers = _workers(event, prefix=prefix)
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
        _attribute(event, "cloudflare.script_version.id", f"{prefix}_script_version_invalid"),
        f"{prefix}_script_version_invalid",
        maximum=512,
    )
    if _attribute(event, "cloud.provider", f"{prefix}_cloud_provider_invalid") != CLOUD_PROVIDER:
        _fail(f"{prefix}_cloud_provider_invalid")
    if _attribute(event, "cloud.platform", f"{prefix}_cloud_platform_invalid") != CLOUD_PLATFORM:
        _fail(f"{prefix}_cloud_platform_invalid")
    if metadata.get("service") != service:
        _fail(f"{prefix}_service_metadata_mismatch")
    worker_script = workers.get("scriptName")
    if worker_script is not None and worker_script != service:
        _fail(f"{prefix}_worker_script_mismatch")
    script_version = workers.get("scriptVersion")
    if script_version is not None:
        script_version_map = _mapping(script_version, f"{prefix}_worker_script_version_invalid")
        if script_version_map.get("id") != version:
            _fail(f"{prefix}_worker_script_version_mismatch")
    return trace_id, span_id, invocation_id, service, version


def _span_times(event: Mapping[str, object], *, prefix: str) -> tuple[datetime, datetime]:
    metadata = _metadata(event, prefix=prefix)
    start = _epoch_ms(metadata.get("startTime"), f"{prefix}_start_time_invalid")
    end = _epoch_ms(metadata.get("endTime"), f"{prefix}_end_time_invalid")
    if end < start:
        _fail(f"{prefix}_time_order_invalid")
    return start, end


def parse_cloudflare_structural_trace_discovery_response(
    payload: Mapping[str, object],
    *,
    authorization_id: str,
    run_id: str,
    request_kind: str,
    service_name: str = DEFAULT_WORKER_SERVICE,
) -> tuple[str, ...]:
    authorization = _text(authorization_id, "authorization_id_invalid", maximum=128)
    run = _text(run_id, "run_id_invalid", maximum=256)
    kind = _kind(request_kind)
    service = _text(service_name, "service_name_invalid", maximum=256)
    trace_ids: list[str] = []
    for index, event in enumerate(_raw_events(payload)):
        metadata = _metadata(event, prefix=f"discovery_{index}")
        if metadata.get("spanName") != STRUCTURAL_EXECUTION_SPAN_NAME:
            _fail("discovery_span_name_invalid")
        context = _custom_span_context(event)
        if context["precios.authorization_id"] != authorization:
            _fail("discovery_authorization_mismatch")
        if context["precios.run_id"] != run:
            _fail("discovery_run_mismatch")
        if context["precios.request_kind"] != kind:
            _fail("discovery_request_kind_mismatch")
        trace_id, _span_id, _invocation, observed_service, _version = _standard_span_identity(
            event,
            prefix=f"discovery_{index}",
        )
        if observed_service != service:
            _fail("discovery_service_mismatch")
        trace_ids.append(trace_id)
    if len(set(trace_ids)) != len(trace_ids):
        _fail("discovery_trace_id_duplicate")
    return tuple(trace_ids)


def _is_fetch_child(event: Mapping[str, object], custom_span_id: str, trace_id: str) -> bool:
    metadata = _metadata(event)
    if metadata.get("parentSpanId") != custom_span_id or metadata.get("traceId") != trace_id:
        return False
    required = (
        "url.full",
        "http.request.method",
        "http.response.status_code",
        "http.response.body.size",
    )
    return all(_attribute_optional(event, key) is not _MISSING for key in required)


def _build_trace_evidence(
    *,
    custom_event: Mapping[str, object],
    fetch_event: Mapping[str, object],
) -> CloudflareStructuralTraceEvidence:
    fetch_metadata = _metadata(fetch_event, prefix="fetch")
    custom_trace_id, custom_span_id, custom_invocation, service, version = _standard_span_identity(
        custom_event,
        prefix="custom",
    )
    fetch_trace_id, fetch_span_id, fetch_invocation, fetch_service, fetch_version = _standard_span_identity(
        fetch_event,
        prefix="fetch",
    )
    if fetch_trace_id != custom_trace_id:
        _fail("fetch_trace_id_mismatch")
    if fetch_metadata.get("parentSpanId") != custom_span_id:
        _fail("fetch_parent_span_mismatch")
    if fetch_invocation != custom_invocation:
        _fail("fetch_invocation_mismatch")
    if fetch_service != service:
        _fail("fetch_service_mismatch")
    if fetch_version != version:
        _fail("fetch_script_version_mismatch")

    context = _custom_span_context(custom_event)
    custom_start, custom_end = _span_times(custom_event, prefix="custom")
    fetch_start, fetch_end = _span_times(fetch_event, prefix="fetch")
    fetch_url = _text(_attribute(fetch_event, "url.full", "fetch_url_invalid"), "fetch_url_invalid")
    fetch_method = _text(
        _attribute(fetch_event, "http.request.method", "fetch_method_invalid"),
        "fetch_method_invalid",
        maximum=16,
    )
    fetch_status = _safe_integer(
        _attribute(fetch_event, "http.response.status_code", "fetch_status_invalid"),
        "fetch_status_invalid",
        minimum=100,
        maximum=599,
    )
    fetch_body_size = _safe_integer(
        _attribute(fetch_event, "http.response.body.size", "fetch_body_size_invalid"),
        "fetch_body_size_invalid",
    )

    return CloudflareStructuralTraceEvidence(
        trace_id=custom_trace_id,
        custom_span_id=custom_span_id,
        fetch_span_id=fetch_span_id,
        fetch_parent_span_id=_text(
            fetch_metadata.get("parentSpanId"),
            "fetch_parent_span_id_invalid",
            maximum=512,
        ),
        faas_invocation_id=custom_invocation,
        service_name=service,
        script_version_id=version,
        custom_span_name=STRUCTURAL_EXECUTION_SPAN_NAME,
        trace_contract_version=context["precios.trace_contract_version"],
        cloud_provider=CLOUD_PROVIDER,
        cloud_platform=CLOUD_PLATFORM,
        collector_provider=context["precios.collector_provider"],
        authorization_id=context["precios.authorization_id"],
        run_id=context["precios.run_id"],
        approved_commit_sha=context["precios.approved_commit_sha"],
        reservation_id=context["precios.reservation_id"],
        request_id=context["precios.request_id"],
        request_digest=context["precios.request_digest"],
        request_kind=context["precios.request_kind"],
        fetch_url=fetch_url,
        fetch_method=fetch_method,
        fetch_status=fetch_status,
        fetch_response_body_size=fetch_body_size,
        custom_started_at_utc=custom_start,
        custom_completed_at_utc=custom_end,
        fetch_started_at_utc=fetch_start,
        fetch_completed_at_utc=fetch_end,
    )


def parse_cloudflare_structural_trace_detail_response(
    payload: Mapping[str, object],
    *,
    expected_trace_id: str,
    service_name: str = DEFAULT_WORKER_SERVICE,
) -> tuple[CloudflareStructuralTraceEvidence, ...]:
    trace_id = _text(expected_trace_id, "expected_trace_id_invalid", maximum=512)
    service = _text(service_name, "service_name_invalid", maximum=256)
    events = _raw_events(payload)
    for event in events:
        metadata = _metadata(event)
        observed_trace = metadata.get("traceId")
        if observed_trace is not None and observed_trace != trace_id:
            _fail("detail_trace_id_mismatch")
        observed_service = metadata.get("service")
        if observed_service is not None and observed_service != service:
            _fail("detail_service_mismatch")

    custom_events = [
        event
        for event in events
        if _metadata(event).get("spanName") == STRUCTURAL_EXECUTION_SPAN_NAME
    ]
    evidences: list[CloudflareStructuralTraceEvidence] = []
    for custom_event in custom_events:
        custom_metadata = _metadata(custom_event, prefix="custom")
        custom_trace_id = _text(
            custom_metadata.get("traceId"),
            "custom_trace_id_invalid",
            maximum=512,
        )
        if custom_trace_id != trace_id:
            _fail("custom_trace_id_mismatch")
        custom_span_id = _text(
            custom_metadata.get("spanId"),
            "custom_span_id_invalid",
            maximum=512,
        )
        fetch_candidates = [
            event for event in events if _is_fetch_child(event, custom_span_id, custom_trace_id)
        ]
        if not fetch_candidates:
            _fail("origin_fetch_span_missing")
        if len(fetch_candidates) != 1:
            _fail("origin_fetch_span_not_unique")
        evidences.append(
            _build_trace_evidence(
                custom_event=custom_event,
                fetch_event=fetch_candidates[0],
            )
        )

    physical_ids = [evidence.physical_evidence_id for evidence in evidences]
    if len(set(physical_ids)) != len(physical_ids):
        _fail("physical_evidence_duplicate")
    return tuple(evidences)
