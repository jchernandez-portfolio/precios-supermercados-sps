"""Consulta actual de Workers Observability para la sonda controlada.

Cloudflare separa la telemetría por vistas: ``traces`` devuelve resúmenes de
trazas e ``invocations`` devuelve los eventos/spans agrupados por invocación.
Esta capa usa esas vistas únicamente para localizar candidatos. La autoridad
sigue estando en el recibo Ed25519 y en la reconciliación estricta existente.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol

from precios_supermercados.cloudflare_controlled_probe_observability import (
    CONTROLLED_PROBE_SERVICE,
    CONTROLLED_PROBE_SPAN_NAME,
    OBSERVABILITY_DATASET,
    ControlledProbeObservabilityError,
    PlatformReconciledControlledProbe,
    parse_controlled_probe_trace_detail_response,
    reconcile_controlled_probe_trace,
)
from precios_supermercados.cloudflare_controlled_probe_verifier import (
    verify_controlled_probe_artifact,
)

TRACE_DISCOVERY_QUERY_ID = "precios-sps-controlled-probe-traces-v2"
TRACE_DETAIL_QUERY_ID = "precios-sps-controlled-probe-invocations-v2"
OBSERVABILITY_QUERY_PATH_TEMPLATE = (
    "/accounts/{account_id}/workers/observability/telemetry/query"
)
QUERY_WINDOW_MARGIN = timedelta(seconds=30)
MAX_QUERY_WINDOW = timedelta(minutes=15)
MAX_TRACE_SUMMARIES = 100
MAX_INVOCATION_GROUPS = 200
MAX_INVOCATION_EVENTS = 1000


def _fail(code: str) -> None:
    raise ControlledProbeObservabilityError(code)


def _mapping(value: object, code: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        _fail(code)
    return value


def _sequence(value: object, code: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        _fail(code)
    return value


def _text(value: object, code: str, *, maximum: int = 1024) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or len(value) > maximum
    ):
        _fail(code)
    return value


def _integer(
    value: object,
    code: str,
    *,
    minimum: int = 0,
    maximum: int = 2**53 - 1,
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


def _timestamp(value: object, code: str) -> datetime:
    text = _text(value, code, maximum=64)
    if not text.endswith("Z"):
        _fail(code)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise ControlledProbeObservabilityError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(code)
    return parsed.astimezone(UTC)


def _account_id(value: object) -> str:
    text = _text(value, "probe_observability_account_id_invalid", maximum=32)
    if len(text) != 32 or any(character not in "0123456789abcdef" for character in text):
        _fail("probe_observability_account_id_invalid")
    return text


def _filter(key: str, value: str) -> dict[str, object]:
    return {
        "kind": "filter",
        "key": key,
        "operation": "eq",
        "type": "string",
        "value": value,
    }


def _window(from_utc: datetime, to_utc: datetime) -> tuple[int, int]:
    if (
        not isinstance(from_utc, datetime)
        or not isinstance(to_utc, datetime)
        or from_utc.tzinfo is None
        or to_utc.tzinfo is None
    ):
        _fail("probe_query_window_invalid")
    start = from_utc.astimezone(UTC)
    end = to_utc.astimezone(UTC)
    if end <= start or end - start > MAX_QUERY_WINDOW:
        _fail("probe_query_window_invalid")
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _payload(
    *,
    query_id: str,
    view: str,
    from_utc: datetime,
    to_utc: datetime,
    filters: list[dict[str, object]],
) -> dict[str, object]:
    from_ms, to_ms = _window(from_utc, to_utc)
    return {
        "queryId": query_id,
        "timeframe": {"from": from_ms, "to": to_ms},
        "view": view,
        "limit": MAX_TRACE_SUMMARIES,
        "parameters": {
            "datasets": [OBSERVABILITY_DATASET],
            "filterCombination": "and",
            "filters": filters,
            "limit": MAX_TRACE_SUMMARIES,
        },
    }


def build_trace_summary_query(
    *,
    from_utc: datetime,
    to_utc: datetime,
    service_name: str = CONTROLLED_PROBE_SERVICE,
) -> dict[str, object]:
    """Descubre trazas del Worker en la ventana firmada; no decide autoridad."""

    service = _text(service_name, "probe_service_name_invalid", maximum=256)
    return _payload(
        query_id=TRACE_DISCOVERY_QUERY_ID,
        view="traces",
        from_utc=from_utc,
        to_utc=to_utc,
        filters=[_filter("$metadata.service", service)],
    )


def build_trace_invocations_query(
    *,
    from_utc: datetime,
    to_utc: datetime,
    trace_id: str,
    service_name: str = CONTROLLED_PROBE_SERVICE,
) -> dict[str, object]:
    trace = _text(trace_id, "probe_trace_id_invalid", maximum=512)
    service = _text(service_name, "probe_service_name_invalid", maximum=256)
    return _payload(
        query_id=TRACE_DETAIL_QUERY_ID,
        view="invocations",
        from_utc=from_utc,
        to_utc=to_utc,
        filters=[
            _filter("$metadata.service", service),
            _filter("$metadata.traceId", trace),
        ],
    )


def _successful_result(payload: Mapping[str, object]) -> Mapping[str, object]:
    envelope = _mapping(payload, "probe_observability_response_invalid")
    if envelope.get("success") is not True:
        _fail("probe_observability_response_unsuccessful")
    if envelope.get("errors") not in (None, []):
        _fail("probe_observability_response_errors")
    return _mapping(envelope.get("result"), "probe_observability_result_invalid")


def parse_trace_summary_response(
    payload: Mapping[str, object],
    *,
    service_name: str = CONTROLLED_PROBE_SERVICE,
) -> tuple[str, ...]:
    """Extrae IDs candidatos de ``result.traces`` sin usar el resumen como autoridad.

    ``trace.service`` es un campo de resumen de plataforma y se ha observado con
    formas inconsistentes. La consulta ya se limita por ``$metadata.service``,
    pero la identidad del servicio se vuelve a exigir de forma estricta en los
    eventos de ``invocations`` mediante ``service.name``, ``$metadata.service``
    y ``$workers.scriptName`` antes de aceptar cualquier evidencia.
    """

    _text(service_name, "probe_service_name_invalid", maximum=256)
    result = _successful_result(payload)
    traces = _sequence(result.get("traces"), "probe_observability_traces_invalid")
    if len(traces) > MAX_TRACE_SUMMARIES:
        _fail("probe_trace_count_above_limit")

    trace_ids: list[str] = []
    for index, raw in enumerate(traces):
        trace = _mapping(raw, f"probe_trace_summary_{index}_invalid")
        trace_id = _text(
            trace.get("traceId"),
            f"probe_trace_summary_{index}_id_invalid",
            maximum=512,
        )
        _integer(trace.get("spans"), f"probe_trace_summary_{index}_spans_invalid", minimum=1)
        start_ms = _integer(
            trace.get("traceStartMs"),
            f"probe_trace_summary_{index}_start_invalid",
            minimum=1,
        )
        end_ms = _integer(
            trace.get("traceEndMs"),
            f"probe_trace_summary_{index}_end_invalid",
            minimum=1,
        )
        if end_ms < start_ms:
            _fail("probe_trace_summary_time_order_invalid")
        trace_ids.append(trace_id)

    if len(trace_ids) != len(set(trace_ids)):
        _fail("probe_trace_summary_duplicate_id")
    if not trace_ids:
        _fail("probe_discovery_trace_missing")
    return tuple(trace_ids)


def normalize_invocations_response(payload: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    """Aplana ``result.invocations`` conservando cada evento original."""

    result = _successful_result(payload)
    invocations = _mapping(
        result.get("invocations"),
        "probe_observability_invocations_invalid",
    )
    if len(invocations) > MAX_INVOCATION_GROUPS:
        _fail("probe_invocation_group_count_above_limit")

    events: list[Mapping[str, object]] = []
    for request_id, raw_group in invocations.items():
        _text(request_id, "probe_invocation_request_id_invalid", maximum=512)
        group = _sequence(raw_group, "probe_invocation_group_invalid")
        for raw_event in group:
            event = _mapping(raw_event, "probe_invocation_event_invalid")
            events.append(event)
            if len(events) > MAX_INVOCATION_EVENTS:
                _fail("probe_invocation_event_count_above_limit")
    return tuple(events)


def _legacy_events_envelope(events: Sequence[Mapping[str, object]]) -> dict[str, object]:
    copied = [dict(event) for event in events]
    return {
        "success": True,
        "errors": [],
        "messages": [],
        "result": {"events": {"events": copied, "count": len(copied)}},
    }


def _contains_controlled_span(events: Sequence[Mapping[str, object]]) -> bool:
    for event in events:
        metadata = event.get("$metadata")
        if isinstance(metadata, Mapping) and metadata.get("spanName") == CONTROLLED_PROBE_SPAN_NAME:
            return True
    return False


class ControlledProbeTraceQueryTransport(Protocol):
    def post_json(
        self,
        path: str,
        *,
        bearer_token: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]: ...


class ControlledProbeTraceQueryVerifierClient:
    """Re-verifica evidencia firmada usando las vistas actuales de trazas."""

    def __init__(self, account_id: str, transport: ControlledProbeTraceQueryTransport) -> None:
        account = _account_id(account_id)
        if transport is None or not callable(getattr(transport, "post_json", None)):
            _fail("probe_observability_transport_invalid")
        self._transport = transport
        self._path = OBSERVABILITY_QUERY_PATH_TEMPLATE.format(account_id=account)

    @property
    def query_path(self) -> str:
        return self._path

    def _post(self, payload: Mapping[str, object], *, bearer_token: str) -> Mapping[str, object]:
        token = _text(bearer_token, "probe_observability_bearer_invalid", maximum=20_000)
        try:
            response = self._transport.post_json(
                self._path,
                bearer_token=token,
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
        top = _mapping(artifact, "probe_artifact_invalid")
        receipt = _mapping(top.get("receiptPayload"), "probe_receipt_invalid")
        start = _timestamp(
            receipt.get("physical_started_at_utc"),
            "probe_receipt_start_invalid",
        )
        end = _timestamp(
            receipt.get("response_completed_at_utc"),
            "probe_receipt_end_invalid",
        )
        if end < start:
            _fail("probe_receipt_time_order_invalid")
        from_utc = start - QUERY_WINDOW_MARGIN
        to_utc = end + QUERY_WINDOW_MARGIN
        if to_utc - from_utc > MAX_QUERY_WINDOW:
            _fail("probe_receipt_observability_window_too_wide")

        discovery = self._post(
            build_trace_summary_query(from_utc=from_utc, to_utc=to_utc),
            bearer_token=bearer_token,
        )
        trace_ids = parse_trace_summary_response(discovery)

        candidates = []
        for trace_id in trace_ids:
            detail = self._post(
                build_trace_invocations_query(
                    from_utc=from_utc,
                    to_utc=to_utc,
                    trace_id=trace_id,
                ),
                bearer_token=bearer_token,
            )
            events = normalize_invocations_response(detail)
            if not _contains_controlled_span(events):
                continue
            candidates.extend(
                parse_controlled_probe_trace_detail_response(
                    _legacy_events_envelope(events),
                    expected_trace_id=trace_id,
                )
            )

        if not candidates:
            _fail("probe_discovery_trace_missing")
        return reconcile_controlled_probe_trace(verified, artifact, candidates)
