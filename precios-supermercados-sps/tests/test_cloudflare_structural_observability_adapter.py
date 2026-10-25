from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone

import pytest

from precios_supermercados.cloudflare_structural_observability_adapter import (
    CloudflareStructuralObservabilityAdapterError,
    build_cloudflare_structural_trace_detail_query,
    build_cloudflare_structural_trace_discovery_query,
    parse_cloudflare_structural_trace_detail_response,
    parse_cloudflare_structural_trace_discovery_response,
)
from precios_supermercados.cloudflare_structural_trace_evidence import (
    STRUCTURAL_EXECUTION_SPAN_NAME,
)

SERVICE = "precios-sps-provenance"
TRACE_ID = "trace-structural-observability-001"
CUSTOM_SPAN_ID = "span-structural-custom-001"
FETCH_SPAN_ID = "span-structural-fetch-001"
INVOCATION_ID = "invocation-structural-001"
VERSION_ID = "version-structural-001"
AUTHORIZATION_ID = "authorization-structural-001"
RUN_ID = "32524000000:1"
COMMIT_SHA = "a" * 40
REQUEST_DIGEST = "b" * 64
REQUEST_KIND = "root_total"
SOURCE_URL = "https://www.lacolonia.com/_v/segment/graphql/v1?workspace=master&maxAge=short"
START_MS = 1_787_342_000_000


def _standard_source() -> dict[str, object]:
    return {
        "cloud.provider": "cloudflare",
        "cloud.platform": "cloudflare.workers",
        "faas.invocation_id": INVOCATION_ID,
        "service.name": SERVICE,
        "cloudflare.script_version.id": VERSION_ID,
    }


def _workers() -> dict[str, object]:
    return {
        "scriptName": SERVICE,
        "scriptVersion": {"id": VERSION_ID},
        "truncated": False,
    }


def _custom_event(
    *,
    trace_id: str = TRACE_ID,
    span_id: str = CUSTOM_SPAN_ID,
    request_kind: str = REQUEST_KIND,
) -> dict[str, object]:
    source = _standard_source()
    source.update(
        {
            "precios.trace_contract_version": "1",
            "precios.collector_provider": "cloudflare_workers",
            "precios.authorization_id": AUTHORIZATION_ID,
            "precios.run_id": RUN_ID,
            "precios.approved_commit_sha": COMMIT_SHA,
            "precios.reservation_id": f"reservation-{request_kind}",
            "precios.request_id": f"request-{request_kind}",
            "precios.request_digest": REQUEST_DIGEST,
            "precios.request_kind": request_kind,
        }
    )
    return {
        "$metadata": {
            "service": SERVICE,
            "traceId": trace_id,
            "spanId": span_id,
            "spanName": STRUCTURAL_EXECUTION_SPAN_NAME,
            "startTime": START_MS,
            "endTime": START_MS + 800,
        },
        "$workers": _workers(),
        "dataset": "cloudflare-workers",
        "source": source,
        "timestamp": START_MS,
    }


def _fetch_event(
    *,
    trace_id: str = TRACE_ID,
    parent_span_id: str = CUSTOM_SPAN_ID,
    span_id: str = FETCH_SPAN_ID,
) -> dict[str, object]:
    source = _standard_source()
    source.update(
        {
            "url.full": SOURCE_URL,
            "http.request.method": "GET",
            "http.response.status_code": 200,
            "http.response.body.size": 321,
        }
    )
    return {
        "$metadata": {
            "service": SERVICE,
            "traceId": trace_id,
            "spanId": span_id,
            "parentSpanId": parent_span_id,
            "spanName": "fetch",
            "startTime": START_MS + 100,
            "endTime": START_MS + 700,
        },
        "$workers": _workers(),
        "dataset": "cloudflare-workers",
        "source": source,
        "timestamp": START_MS + 100,
    }


def _log_event() -> dict[str, object]:
    return {
        "$metadata": {
            "service": SERVICE,
            "traceId": TRACE_ID,
            "spanId": "span-log-001",
            "parentSpanId": CUSTOM_SPAN_ID,
            "spanName": "log",
            "startTime": START_MS + 200,
            "endTime": START_MS + 200,
        },
        "$workers": _workers(),
        "dataset": "cloudflare-workers",
        "source": "log line",
        "timestamp": START_MS + 200,
    }


def _response(events: list[dict[str, object]], *, count: int | None = None) -> dict[str, object]:
    container: dict[str, object] = {"events": events}
    if count is not None:
        container["count"] = count
    return {
        "success": True,
        "errors": [],
        "result": {"events": container},
    }


def _window() -> tuple[datetime, datetime]:
    start = datetime(2026, 8, 21, 20, 0, tzinfo=UTC)
    return start, start + timedelta(minutes=5)


def _filters(query: dict[str, object]) -> list[dict[str, object]]:
    parameters = query["parameters"]
    assert isinstance(parameters, dict)
    filters = parameters["filters"]
    assert isinstance(filters, list)
    return filters  # type: ignore[return-value]


def test_discovery_query_fija_span_contexto_kind_y_version() -> None:
    start, end = _window()
    query = build_cloudflare_structural_trace_discovery_query(
        from_utc=start,
        to_utc=end,
        authorization_id=AUTHORIZATION_ID,
        run_id=RUN_ID,
        request_kind="root_total",
    )
    by_key = {item["key"]: item["value"] for item in _filters(query)}
    assert by_key == {
        "$metadata.service": SERVICE,
        "$metadata.spanName": STRUCTURAL_EXECUTION_SPAN_NAME,
        "precios.authorization_id": AUTHORIZATION_ID,
        "precios.run_id": RUN_ID,
        "precios.request_kind": "root_total",
        "precios.trace_contract_version": "1",
    }


def test_discovery_query_rechaza_kind_ajeno_y_ventana_excesiva() -> None:
    start, _ = _window()
    with pytest.raises(CloudflareStructuralObservabilityAdapterError) as kind_error:
        build_cloudflare_structural_trace_discovery_query(
            from_utc=start,
            to_utc=start + timedelta(minutes=1),
            authorization_id=AUTHORIZATION_ID,
            run_id=RUN_ID,
            request_kind="primary",
        )
    assert kind_error.value.code == "request_kind_invalid"

    with pytest.raises(CloudflareStructuralObservabilityAdapterError) as window_error:
        build_cloudflare_structural_trace_discovery_query(
            from_utc=start,
            to_utc=start + timedelta(minutes=16),
            authorization_id=AUTHORIZATION_ID,
            run_id=RUN_ID,
            request_kind="root_total",
        )
    assert window_error.value.code == "query_window_too_wide"


def test_detail_query_solo_filtra_servicio_y_trace() -> None:
    start, end = _window()
    query = build_cloudflare_structural_trace_detail_query(
        from_utc=start,
        to_utc=end,
        trace_id=TRACE_ID,
    )
    assert {item["key"] for item in _filters(query)} == {
        "$metadata.service",
        "$metadata.traceId",
    }


def test_discovery_revalida_kind_y_devuelve_trace_unico() -> None:
    result = parse_cloudflare_structural_trace_discovery_response(
        _response([_custom_event()], count=1),
        authorization_id=AUTHORIZATION_ID,
        run_id=RUN_ID,
        request_kind="root_total",
    )
    assert result == (TRACE_ID,)


def test_discovery_no_confia_en_kind_que_declaro_el_filtro_api() -> None:
    event = _custom_event(request_kind="category_tree")
    with pytest.raises(CloudflareStructuralObservabilityAdapterError) as captured:
        parse_cloudflare_structural_trace_discovery_response(
            _response([event]),
            authorization_id=AUTHORIZATION_ID,
            run_id=RUN_ID,
            request_kind="root_total",
        )
    assert captured.value.code == "discovery_request_kind_mismatch"


def test_discovery_rechaza_trace_duplicado() -> None:
    with pytest.raises(CloudflareStructuralObservabilityAdapterError) as captured:
        parse_cloudflare_structural_trace_discovery_response(
            _response([_custom_event(), _custom_event(span_id="span-other")]),
            authorization_id=AUTHORIZATION_ID,
            run_id=RUN_ID,
            request_kind="root_total",
        )
    assert captured.value.code == "discovery_trace_id_duplicate"


def test_detail_convierte_custom_y_fetch_en_evidencia_estructural() -> None:
    result = parse_cloudflare_structural_trace_detail_response(
        _response([_custom_event(), _fetch_event(), _log_event()], count=3),
        expected_trace_id=TRACE_ID,
    )
    assert len(result) == 1
    evidence = result[0]
    assert evidence.request_kind == "root_total"
    assert evidence.authorization_id == AUTHORIZATION_ID
    assert evidence.run_id == RUN_ID
    assert evidence.request_digest == REQUEST_DIGEST
    assert evidence.fetch_url == SOURCE_URL
    assert evidence.fetch_status == 200
    assert evidence.fetch_response_body_size == 321
    assert evidence.production_authority is False
    assert len(evidence.physical_evidence_id) == 64


def test_detail_acepta_atributos_top_level_si_source_no_los_contiene() -> None:
    custom = _custom_event()
    source = custom["source"]
    assert isinstance(source, dict)
    moved = {
        key: source.pop(key)
        for key in list(source)
        if key.startswith("precios.")
    }
    custom.update(moved)
    result = parse_cloudflare_structural_trace_detail_response(
        _response([custom, _fetch_event()]),
        expected_trace_id=TRACE_ID,
    )
    assert result[0].request_kind == "root_total"


def test_atributo_conflictivo_entre_source_y_top_level_falla() -> None:
    custom = _custom_event()
    custom["precios.request_kind"] = "category_tree"
    with pytest.raises(CloudflareStructuralObservabilityAdapterError) as captured:
        parse_cloudflare_structural_trace_detail_response(
            _response([custom, _fetch_event()]),
            expected_trace_id=TRACE_ID,
        )
    assert captured.value.code == "event_attribute_conflict:precios.request_kind"


def test_resultado_truncado_falla_cerrado() -> None:
    with pytest.raises(CloudflareStructuralObservabilityAdapterError) as captured:
        parse_cloudflare_structural_trace_detail_response(
            _response([_custom_event(), _fetch_event()], count=3),
            expected_trace_id=TRACE_ID,
        )
    assert captured.value.code == "observability_events_truncated"


def test_custom_sin_fetch_y_doble_fetch_fallan_cerrado() -> None:
    with pytest.raises(CloudflareStructuralObservabilityAdapterError) as missing:
        parse_cloudflare_structural_trace_detail_response(
            _response([_custom_event()]),
            expected_trace_id=TRACE_ID,
        )
    assert missing.value.code == "origin_fetch_span_missing"

    second = _fetch_event(span_id="fetch-other")
    with pytest.raises(CloudflareStructuralObservabilityAdapterError) as duplicate:
        parse_cloudflare_structural_trace_detail_response(
            _response([_custom_event(), _fetch_event(), second]),
            expected_trace_id=TRACE_ID,
        )
    assert duplicate.value.code == "origin_fetch_span_not_unique"


def test_fetch_de_otro_invocation_o_version_falla() -> None:
    fetch = _fetch_event()
    source = fetch["source"]
    assert isinstance(source, dict)
    source["faas.invocation_id"] = "invocation-other"
    with pytest.raises(CloudflareStructuralObservabilityAdapterError) as invocation:
        parse_cloudflare_structural_trace_detail_response(
            _response([_custom_event(), fetch]),
            expected_trace_id=TRACE_ID,
        )
    assert invocation.value.code == "fetch_invocation_mismatch"

    fetch = _fetch_event()
    source = fetch["source"]
    workers = fetch["$workers"]
    assert isinstance(source, dict)
    assert isinstance(workers, dict)
    source["cloudflare.script_version.id"] = "version-other"
    workers["scriptVersion"] = {"id": "version-other"}
    with pytest.raises(CloudflareStructuralObservabilityAdapterError) as version:
        parse_cloudflare_structural_trace_detail_response(
            _response([_custom_event(), fetch]),
            expected_trace_id=TRACE_ID,
        )
    assert version.value.code == "fetch_script_version_mismatch"


def test_detail_rechaza_evento_de_otro_trace_y_body_size_fraccional() -> None:
    log = _log_event()
    metadata = log["$metadata"]
    assert isinstance(metadata, dict)
    metadata["traceId"] = "trace-attacker"
    with pytest.raises(CloudflareStructuralObservabilityAdapterError) as trace:
        parse_cloudflare_structural_trace_detail_response(
            _response([_custom_event(), _fetch_event(), log]),
            expected_trace_id=TRACE_ID,
        )
    assert trace.value.code == "detail_trace_id_mismatch"

    fetch = _fetch_event()
    source = fetch["source"]
    assert isinstance(source, dict)
    source["http.response.body.size"] = 321.5
    with pytest.raises(CloudflareStructuralObservabilityAdapterError) as size:
        parse_cloudflare_structural_trace_detail_response(
            _response([_custom_event(), fetch]),
            expected_trace_id=TRACE_ID,
        )
    assert size.value.code == "fetch_body_size_invalid"


def test_parse_no_muta_payload() -> None:
    payload = _response([_custom_event(), _fetch_event(), _log_event()])
    before = deepcopy(payload)
    parse_cloudflare_structural_trace_detail_response(
        payload,
        expected_trace_id=TRACE_ID,
    )
    assert payload == before
