from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

import precios_supermercados.cloudflare_controlled_probe_trace_query as trace_query
from precios_supermercados.cloudflare_controlled_probe_observability import (
    CONTROLLED_PROBE_SERVICE,
    CONTROLLED_PROBE_SPAN_NAME,
    ControlledProbeObservabilityError,
)

TRACE_ID = "trace-controlled-001"
START = datetime(2026, 8, 22, 4, 30, 39, tzinfo=UTC)


def _summary_response(*, trace_id: str = TRACE_ID):
    return {
        "success": True,
        "errors": [],
        "result": {
            "traces": [
                {
                    "rootSpanName": "rpc",
                    "rootTransactionName": "ProbeLedger.execute",
                    "service": [CONTROLLED_PROBE_SERVICE],
                    "spans": 4,
                    "traceDurationMs": 800,
                    "traceStartMs": int(START.timestamp() * 1000),
                    "traceEndMs": int((START + timedelta(seconds=1)).timestamp() * 1000),
                    "traceId": trace_id,
                }
            ]
        },
    }


def _event(span_name: str, *, span_id: str, parent_span_id: str | None = None):
    metadata = {
        "id": f"event-{span_id}",
        "service": CONTROLLED_PROBE_SERVICE,
        "traceId": TRACE_ID,
        "spanId": span_id,
        "spanName": span_name,
        "startTime": int(START.timestamp() * 1000),
        "endTime": int((START + timedelta(milliseconds=500)).timestamp() * 1000),
    }
    if parent_span_id is not None:
        metadata["parentSpanId"] = parent_span_id
    return {
        "$metadata": metadata,
        "dataset": "cloudflare-workers",
        "source": {"service.name": CONTROLLED_PROBE_SERVICE},
        "$workers": {"scriptName": CONTROLLED_PROBE_SERVICE, "truncated": False},
    }


def test_discovery_uses_traces_view_and_only_indexed_service_filter():
    query = trace_query.build_trace_summary_query(
        from_utc=START - timedelta(seconds=30),
        to_utc=START + timedelta(seconds=30),
    )
    assert query["view"] == "traces"
    assert query["parameters"]["datasets"] == ["cloudflare-workers"]
    assert query["parameters"]["filters"] == [
        {
            "kind": "filter",
            "key": "$metadata.service",
            "operation": "eq",
            "type": "string",
            "value": CONTROLLED_PROBE_SERVICE,
        }
    ]


def test_detail_uses_invocations_view_and_trace_id_filter():
    query = trace_query.build_trace_invocations_query(
        from_utc=START - timedelta(seconds=30),
        to_utc=START + timedelta(seconds=30),
        trace_id=TRACE_ID,
    )
    assert query["view"] == "invocations"
    filters = query["parameters"]["filters"]
    assert {(item["key"], item["value"]) for item in filters} == {
        ("$metadata.service", CONTROLLED_PROBE_SERVICE),
        ("$metadata.traceId", TRACE_ID),
    }


def test_trace_summary_parser_is_discovery_only_and_requires_unique_trace_ids():
    assert trace_query.parse_trace_summary_response(_summary_response()) == (TRACE_ID,)

    duplicate = _summary_response()
    duplicate["result"]["traces"].append(dict(duplicate["result"]["traces"][0]))
    with pytest.raises(ControlledProbeObservabilityError) as exc:
        trace_query.parse_trace_summary_response(duplicate)
    assert exc.value.code == "probe_trace_summary_duplicate_id"


def test_trace_summary_parser_does_not_trust_optional_service_shape():
    for observed_service in (
        CONTROLLED_PROBE_SERVICE,
        [CONTROLLED_PROBE_SERVICE],
        ["other-worker"],
        {"name": CONTROLLED_PROBE_SERVICE},
        None,
    ):
        payload = _summary_response()
        payload["result"]["traces"][0]["service"] = observed_service
        assert trace_query.parse_trace_summary_response(payload) == (TRACE_ID,)

    payload = _summary_response()
    del payload["result"]["traces"][0]["service"]
    assert trace_query.parse_trace_summary_response(payload) == (TRACE_ID,)


def test_trace_summary_parser_still_enforces_structural_limits_and_time_order():
    invalid_spans = _summary_response()
    invalid_spans["result"]["traces"][0]["spans"] = 0
    with pytest.raises(ControlledProbeObservabilityError) as exc:
        trace_query.parse_trace_summary_response(invalid_spans)
    assert exc.value.code == "probe_trace_summary_0_spans_invalid"

    invalid_time = _summary_response()
    trace = invalid_time["result"]["traces"][0]
    trace["traceEndMs"] = trace["traceStartMs"] - 1
    with pytest.raises(ControlledProbeObservabilityError) as exc:
        trace_query.parse_trace_summary_response(invalid_time)
    assert exc.value.code == "probe_trace_summary_time_order_invalid"


def test_invocations_are_flattened_without_rewriting_span_events():
    custom = _event(CONTROLLED_PROBE_SPAN_NAME, span_id="custom")
    fetch = _event("fetch", span_id="fetch", parent_span_id="custom")
    payload = {
        "success": True,
        "errors": [],
        "result": {"invocations": {"request-1": [custom, fetch]}},
    }
    assert trace_query.normalize_invocations_response(payload) == (custom, fetch)


def test_client_uses_trace_then_invocations_and_skips_unrelated_trace(monkeypatch):
    verified = object()
    reconciled = object()
    candidate = object()
    artifact = {
        "receiptPayload": {
            "physical_started_at_utc": "2026-08-22T04:30:39.000Z",
            "response_completed_at_utc": "2026-08-22T04:30:40.000Z",
        }
    }

    monkeypatch.setattr(trace_query, "verify_controlled_probe_artifact", lambda *a, **k: verified)
    monkeypatch.setattr(
        trace_query,
        "parse_controlled_probe_trace_detail_response",
        lambda payload, *, expected_trace_id: (candidate,),
    )

    def reconcile(observed_verified, observed_artifact, candidates):
        assert observed_verified is verified
        assert observed_artifact is artifact
        assert candidates == [candidate]
        return reconciled

    monkeypatch.setattr(trace_query, "reconcile_controlled_probe_trace", reconcile)

    custom = _event(CONTROLLED_PROBE_SPAN_NAME, span_id="custom")
    unrelated = _event("fetch", span_id="other")
    calls = []

    class Transport:
        def post_json(self, path, *, bearer_token, payload):
            calls.append(payload)
            if payload["view"] == "traces":
                response = _summary_response(trace_id="trace-unrelated")
                response["result"]["traces"].append(
                    {
                        **response["result"]["traces"][0],
                        "traceId": TRACE_ID,
                    }
                )
                return response
            trace_filter = next(
                item for item in payload["parameters"]["filters"] if item["key"] == "$metadata.traceId"
            )
            if trace_filter["value"] == "trace-unrelated":
                event = dict(unrelated)
                event["$metadata"] = dict(unrelated["$metadata"], traceId="trace-unrelated")
                return {
                    "success": True,
                    "errors": [],
                    "result": {"invocations": {"request-unrelated": [event]}},
                }
            return {
                "success": True,
                "errors": [],
                "result": {"invocations": {"request-controlled": [custom]}},
            }

    client = trace_query.ControlledProbeTraceQueryVerifierClient("a" * 32, Transport())
    result = client.reconcile_artifact(
        artifact,
        public_key_spki_b64url="public-key",
        expected_commit_sha="b" * 40,
        expected_run_id="32551882793",
        expected_run_attempt=1,
        bearer_token="token",
    )
    assert result is reconciled
    assert [call["view"] for call in calls] == ["traces", "invocations", "invocations"]
