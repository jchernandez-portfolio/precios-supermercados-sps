from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

import precios_supermercados.cloudflare_controlled_probe_events_query as events_query
from precios_supermercados.cloudflare_controlled_probe_observability import (
    CONTROLLED_PROBE_SERVICE,
    CONTROLLED_PROBE_SPAN_NAME,
    ControlledProbeObservabilityError,
)

TRACE_ID = "trace-controlled-001"
START = datetime(2026, 8, 22, 4, 30, 39, tzinfo=UTC)


def _summary_response():
    return {
        "success": True,
        "errors": [],
        "result": {
            "traces": [
                {
                    "rootSpanName": "rpc",
                    "rootTransactionName": "ProbeLedger.execute",
                    "service": None,
                    "spans": 4,
                    "traceDurationMs": 800,
                    "traceStartMs": int(START.timestamp() * 1000),
                    "traceEndMs": int((START + timedelta(seconds=1)).timestamp() * 1000),
                    "traceId": TRACE_ID,
                }
            ]
        },
    }


def _event(span_name: str):
    return {
        "$metadata": {
            "id": f"event-{span_name}",
            "service": CONTROLLED_PROBE_SERVICE,
            "traceId": TRACE_ID,
            "spanId": f"span-{span_name}",
            "spanName": span_name,
        },
        "dataset": "cloudflare-workers",
        "source": {},
        "$workers": {"scriptName": CONTROLLED_PROBE_SERVICE, "truncated": False},
    }


def _events_response(*events):
    return {
        "success": True,
        "errors": [],
        "result": {"events": {"events": list(events), "count": len(events)}},
    }


def test_trace_events_query_uses_only_service_and_trace_id_filters():
    query = events_query.build_trace_events_query(
        from_utc=START - timedelta(seconds=30),
        to_utc=START + timedelta(seconds=30),
        trace_id=TRACE_ID,
    )
    assert query["view"] == "events"
    assert query["parameters"]["datasets"] == ["cloudflare-workers"]
    assert {(item["key"], item["value"]) for item in query["parameters"]["filters"]} == {
        ("$metadata.service", CONTROLLED_PROBE_SERVICE),
        ("$metadata.traceId", TRACE_ID),
    }
    assert all(not item["key"].startswith("precios.") for item in query["parameters"]["filters"])


def test_events_response_rejects_truncation_and_wrong_dataset():
    truncated = _events_response(_event(CONTROLLED_PROBE_SPAN_NAME))
    truncated["result"]["events"]["count"] = 2
    with pytest.raises(ControlledProbeObservabilityError) as exc:
        events_query.normalize_events_response(truncated)
    assert exc.value.code == "probe_observability_events_truncated"

    wrong = _events_response(_event(CONTROLLED_PROBE_SPAN_NAME))
    wrong["result"]["events"]["events"][0]["dataset"] = "other"
    with pytest.raises(ControlledProbeObservabilityError) as exc:
        events_query.normalize_events_response(wrong)
    assert exc.value.code == "probe_event_0_dataset_invalid"


def test_client_uses_traces_then_events_and_local_reconciliation(monkeypatch):
    verified = object()
    candidate = object()
    reconciled = object()
    artifact = {
        "receiptPayload": {
            "physical_started_at_utc": "2026-08-22T04:30:39.000Z",
            "response_completed_at_utc": "2026-08-22T04:30:40.000Z",
        }
    }

    monkeypatch.setattr(events_query, "verify_controlled_probe_artifact", lambda *a, **k: verified)
    monkeypatch.setattr(
        events_query,
        "parse_controlled_probe_trace_detail_response",
        lambda payload, *, expected_trace_id: (candidate,),
    )

    def reconcile(observed_verified, observed_artifact, candidates):
        assert observed_verified is verified
        assert observed_artifact is artifact
        assert candidates == [candidate]
        return reconciled

    monkeypatch.setattr(events_query, "reconcile_controlled_probe_trace", reconcile)

    calls = []

    class Transport:
        def post_json(self, path, *, bearer_token, payload):
            calls.append(payload)
            if payload["view"] == "traces":
                return _summary_response()
            return _events_response(
                _event(CONTROLLED_PROBE_SPAN_NAME),
                _event("fetch"),
            )

    client = events_query.ControlledProbeEventsVerifierClient("a" * 32, Transport())
    result = client.reconcile_artifact(
        artifact,
        public_key_spki_b64url="public-key",
        expected_commit_sha="b" * 40,
        expected_run_id="32551882793",
        expected_run_attempt=1,
        bearer_token="token",
    )
    assert result is reconciled
    assert [call["view"] for call in calls] == ["traces", "events"]


def test_client_does_not_accept_trace_without_controlled_span(monkeypatch):
    artifact = {
        "receiptPayload": {
            "physical_started_at_utc": "2026-08-22T04:30:39.000Z",
            "response_completed_at_utc": "2026-08-22T04:30:40.000Z",
        }
    }
    monkeypatch.setattr(events_query, "verify_controlled_probe_artifact", lambda *a, **k: object())

    class Transport:
        def post_json(self, path, *, bearer_token, payload):
            if payload["view"] == "traces":
                return _summary_response()
            return _events_response(_event("fetch"))

    client = events_query.ControlledProbeEventsVerifierClient("a" * 32, Transport())
    with pytest.raises(ControlledProbeObservabilityError) as exc:
        client.reconcile_artifact(
            artifact,
            public_key_spki_b64url="public-key",
            expected_commit_sha="b" * 40,
            expected_run_id="32551882793",
            expected_run_attempt=1,
            bearer_token="token",
        )
    assert exc.value.code == "probe_discovery_trace_missing"
