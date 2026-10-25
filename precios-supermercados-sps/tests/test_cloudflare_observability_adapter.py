from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone

import pytest

from precios_supermercados.cloudflare_observability_adapter import (
    CloudflareObservabilityAdapterError,
    build_cloudflare_trace_detail_query,
    build_cloudflare_trace_discovery_query,
    parse_cloudflare_trace_detail_response,
    parse_cloudflare_trace_discovery_response,
)
from precios_supermercados.cloudflare_trace_evidence import ORIGIN_EXECUTION_SPAN_NAME

SERVICE = "precios-sps-provenance"
TRACE_ID = "trace-observability-001"
CUSTOM_SPAN_ID = "span-custom-001"
FETCH_SPAN_ID = "span-fetch-001"
INVOCATION_ID = "invocation-001"
VERSION_ID = "version-001"
AUTHORIZATION_ID = "authorization-001"
RUN_ID = "32516558546:1"
COMMIT_SHA = "a" * 40
REQUEST_DIGEST = "b" * 64
SOURCE_URL = "https://www.lacolonia.com/_v/segment/graphql/v1?workspace=master&maxAge=short"
START_MS = 1_787_340_500_000


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
        "eventType": "rpc",
        "requestId": "worker-request-001",
        "scriptName": SERVICE,
        "scriptVersion": {"id": VERSION_ID, "tag": "v1"},
        "truncated": False,
    }


def _custom_event(*, trace_id: str = TRACE_ID, span_id: str = CUSTOM_SPAN_ID) -> dict[str, object]:
    source = _standard_source()
    source.update(
        {
            "precios.trace_contract_version": "1",
            "precios.collector_provider": "cloudflare_workers",
            "precios.authorization_id": AUTHORIZATION_ID,
            "precios.run_id": RUN_ID,
            "precios.approved_commit_sha": COMMIT_SHA,
            "precios.reservation_id": "reservation-001",
            "precios.request_id": "request-001",
            "precios.request_digest": REQUEST_DIGEST,
            "precios.traversal_role": "primary",
            "precios.traversal_id": "traversal-primary-001",
            "precios.partition_id": "root",
        }
    )
    return {
        "$metadata": {
            "id": "event-custom-001",
            "service": SERVICE,
            "traceId": trace_id,
            "spanId": span_id,
            "spanName": ORIGIN_EXECUTION_SPAN_NAME,
            "startTime": START_MS,
            "endTime": START_MS + 800,
        },
        "dataset": "cloudflare-workers",
        "source": source,
        "timestamp": START_MS,
        "$workers": _workers(),
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
            "url.scheme": "https",
            "url.path": "/_v/segment/graphql/v1",
            "http.request.method": "GET",
            "http.response.status_code": 200,
            "http.response.body.size": 1234,
        }
    )
    return {
        "$metadata": {
            "id": "event-fetch-001",
            "service": SERVICE,
            "traceId": trace_id,
            "spanId": span_id,
            "parentSpanId": parent_span_id,
            "spanName": "fetch",
            "origin": "fetch",
            "startTime": START_MS + 100,
            "endTime": START_MS + 700,
        },
        "dataset": "cloudflare-workers",
        "source": source,
        "timestamp": START_MS + 100,
        "$workers": _workers(),
    }


def _unrelated_log() -> dict[str, object]:
    return {
        "$metadata": {
            "id": "event-log-001",
            "service": SERVICE,
            "traceId": TRACE_ID,
            "spanId": "span-log-001",
            "parentSpanId": CUSTOM_SPAN_ID,
            "spanName": "log",
            "startTime": START_MS + 200,
            "endTime": START_MS + 200,
        },
        "dataset": "cloudflare-workers",
        "source": "log-line",
        "timestamp": START_MS + 200,
        "$workers": _workers(),
    }


def _response(events: list[dict[str, object]], *, count: int | None = None) -> dict[str, object]:
    container: dict[str, object] = {"events": events}
    if count is not None:
        container["count"] = count
    return {
        "success": True,
        "errors": [],
        "messages": [{"message": "Successful request"}],
        "result": {"events": container},
    }


def _window() -> tuple[datetime, datetime]:
    start = datetime(2026, 8, 21, 19, 0, tzinfo=UTC)
    return start, start + timedelta(minutes=5)


def _filters(query: dict[str, object]) -> list[dict[str, object]]:
    parameters = query["parameters"]
    assert isinstance(parameters, dict)
    filters = parameters["filters"]
    assert isinstance(filters, list)
    return filters  # type: ignore[return-value]


def test_discovery_query_filtra_custom_span_y_contexto_sin_pretender_incluir_child_fetch() -> None:
    start, end = _window()
    query = build_cloudflare_trace_discovery_query(
        from_utc=start,
        to_utc=end,
        authorization_id=AUTHORIZATION_ID,
        run_id=RUN_ID,
    )

    assert query["view"] == "events"
    assert query["limit"] == 100
    assert query["timeframe"] == {
        "from": int(start.timestamp() * 1000),
        "to": int(end.timestamp() * 1000),
    }
    assert {item["key"] for item in _filters(query)} == {
        "$metadata.service",
        "$metadata.spanName",
        "precios.authorization_id",
        "precios.run_id",
        "precios.trace_contract_version",
    }


def test_detail_query_usa_trace_id_y_no_atributos_custom_que_no_se_propagan_al_fetch() -> None:
    start, end = _window()
    query = build_cloudflare_trace_detail_query(
        from_utc=start,
        to_utc=end,
        trace_id=TRACE_ID,
    )

    keys = {item["key"] for item in _filters(query)}
    assert keys == {"$metadata.service", "$metadata.traceId"}
    assert "precios.authorization_id" not in keys


def test_query_rechaza_ventana_mayor_a_quince_minutos() -> None:
    start, _ = _window()
    with pytest.raises(CloudflareObservabilityAdapterError) as captured:
        build_cloudflare_trace_discovery_query(
            from_utc=start,
            to_utc=start + timedelta(minutes=16),
            authorization_id=AUTHORIZATION_ID,
            run_id=RUN_ID,
        )
    assert captured.value.code == "query_window_too_wide"


def test_discovery_revalida_filtros_y_devuelve_trace_id_unico() -> None:
    result = parse_cloudflare_trace_discovery_response(
        _response([_custom_event()], count=1),
        authorization_id=AUTHORIZATION_ID,
        run_id=RUN_ID,
    )
    assert result == (TRACE_ID,)


def test_discovery_no_confia_en_filtro_api_si_authorization_no_coincide() -> None:
    event = _custom_event()
    source = event["source"]
    assert isinstance(source, dict)
    source["precios.authorization_id"] = "authorization-other"

    with pytest.raises(CloudflareObservabilityAdapterError) as captured:
        parse_cloudflare_trace_discovery_response(
            _response([event]),
            authorization_id=AUTHORIZATION_ID,
            run_id=RUN_ID,
        )
    assert captured.value.code == "discovery_authorization_mismatch"


def test_discovery_rechaza_trace_id_duplicado() -> None:
    first = _custom_event()
    second = _custom_event(span_id="span-custom-002")
    second["$metadata"]["id"] = "event-custom-002"  # type: ignore[index]

    with pytest.raises(CloudflareObservabilityAdapterError) as captured:
        parse_cloudflare_trace_discovery_response(
            _response([first, second]),
            authorization_id=AUTHORIZATION_ID,
            run_id=RUN_ID,
        )
    assert captured.value.code == "discovery_trace_id_duplicate"


def test_detail_convierte_custom_span_y_fetch_en_evidencia_no_productiva() -> None:
    evidence = parse_cloudflare_trace_detail_response(
        _response([_custom_event(), _fetch_event(), _unrelated_log()], count=3),
        expected_trace_id=TRACE_ID,
    )

    assert len(evidence) == 1
    item = evidence[0]
    assert item.trace_id == TRACE_ID
    assert item.custom_span_id == CUSTOM_SPAN_ID
    assert item.fetch_span_id == FETCH_SPAN_ID
    assert item.fetch_parent_span_id == CUSTOM_SPAN_ID
    assert item.faas_invocation_id == INVOCATION_ID
    assert item.script_version_id == VERSION_ID
    assert item.authorization_id == AUTHORIZATION_ID
    assert item.run_id == RUN_ID
    assert item.request_digest == REQUEST_DIGEST
    assert item.fetch_url == SOURCE_URL
    assert item.fetch_status == 200
    assert item.fetch_response_body_size == 1234
    assert item.production_authority is False
    assert len(item.physical_evidence_id) == 64


def test_log_con_source_string_no_rompe_detalle_si_no_es_span_relevante() -> None:
    result = parse_cloudflare_trace_detail_response(
        _response([_unrelated_log(), _custom_event(), _fetch_event()]),
        expected_trace_id=TRACE_ID,
    )
    assert len(result) == 1


def test_resultado_truncado_falla_antes_de_reconciliar() -> None:
    with pytest.raises(CloudflareObservabilityAdapterError) as captured:
        parse_cloudflare_trace_detail_response(
            _response([_custom_event(), _fetch_event()], count=3),
            expected_trace_id=TRACE_ID,
        )
    assert captured.value.code == "observability_events_truncated"


def test_custom_span_sin_child_fetch_falla_cerrado() -> None:
    with pytest.raises(CloudflareObservabilityAdapterError) as captured:
        parse_cloudflare_trace_detail_response(
            _response([_custom_event()]),
            expected_trace_id=TRACE_ID,
        )
    assert captured.value.code == "origin_fetch_span_missing"


def test_dos_fetch_children_fallan_por_ambiguedad() -> None:
    duplicate = _fetch_event(span_id="span-fetch-002")
    duplicate["$metadata"]["id"] = "event-fetch-002"  # type: ignore[index]
    with pytest.raises(CloudflareObservabilityAdapterError) as captured:
        parse_cloudflare_trace_detail_response(
            _response([_custom_event(), _fetch_event(), duplicate]),
            expected_trace_id=TRACE_ID,
        )
    assert captured.value.code == "origin_fetch_span_not_unique"


def test_invocation_distinta_entre_custom_y_fetch_falla() -> None:
    fetch = _fetch_event()
    source = fetch["source"]
    assert isinstance(source, dict)
    source["faas.invocation_id"] = "invocation-other"

    with pytest.raises(CloudflareObservabilityAdapterError) as captured:
        parse_cloudflare_trace_detail_response(
            _response([_custom_event(), fetch]),
            expected_trace_id=TRACE_ID,
        )
    assert captured.value.code == "fetch_invocation_mismatch"


def test_version_de_plataforma_distinta_entre_spans_falla() -> None:
    fetch = _fetch_event()
    source = fetch["source"]
    workers = fetch["$workers"]
    assert isinstance(source, dict)
    assert isinstance(workers, dict)
    source["cloudflare.script_version.id"] = "version-other"
    workers["scriptVersion"] = {"id": "version-other"}

    with pytest.raises(CloudflareObservabilityAdapterError) as captured:
        parse_cloudflare_trace_detail_response(
            _response([_custom_event(), fetch]),
            expected_trace_id=TRACE_ID,
        )
    assert captured.value.code == "fetch_script_version_mismatch"


def test_evento_de_otro_trace_en_respuesta_detail_falla() -> None:
    unrelated = _unrelated_log()
    unrelated["$metadata"]["traceId"] = "trace-other"  # type: ignore[index]

    with pytest.raises(CloudflareObservabilityAdapterError) as captured:
        parse_cloudflare_trace_detail_response(
            _response([_custom_event(), _fetch_event(), unrelated]),
            expected_trace_id=TRACE_ID,
        )
    assert captured.value.code == "detail_trace_id_mismatch"


def test_evento_marcado_truncated_falla_cerrado() -> None:
    fetch = _fetch_event()
    workers = fetch["$workers"]
    assert isinstance(workers, dict)
    workers["truncated"] = True

    with pytest.raises(CloudflareObservabilityAdapterError) as captured:
        parse_cloudflare_trace_detail_response(
            _response([_custom_event(), fetch]),
            expected_trace_id=TRACE_ID,
        )
    assert captured.value.code == "event_1_truncated"


def test_body_size_no_entero_falla() -> None:
    fetch = _fetch_event()
    source = fetch["source"]
    assert isinstance(source, dict)
    source["http.response.body.size"] = 1234.5

    with pytest.raises(CloudflareObservabilityAdapterError) as captured:
        parse_cloudflare_trace_detail_response(
            _response([_custom_event(), fetch]),
            expected_trace_id=TRACE_ID,
        )
    assert captured.value.code == "fetch_body_size_invalid"


def test_source_string_sin_atributos_top_level_falla_por_contexto_ausente() -> None:
    custom = _custom_event()
    custom["source"] = "not-structured"

    with pytest.raises(CloudflareObservabilityAdapterError) as captured:
        parse_cloudflare_trace_detail_response(
            _response([custom, _fetch_event()]),
            expected_trace_id=TRACE_ID,
        )
    assert captured.value.code == "custom_invocation_id_invalid"


def test_response_unsuccessful_falla() -> None:
    response = _response([])
    response["success"] = False
    with pytest.raises(CloudflareObservabilityAdapterError) as captured:
        parse_cloudflare_trace_detail_response(response, expected_trace_id=TRACE_ID)
    assert captured.value.code == "observability_response_unsuccessful"


def test_parse_no_muta_payload_de_entrada() -> None:
    payload = _response([_custom_event(), _fetch_event(), _unrelated_log()])
    before = deepcopy(payload)
    parse_cloudflare_trace_detail_response(payload, expected_trace_id=TRACE_ID)
    assert payload == before
