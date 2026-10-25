from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

import precios_supermercados.scrapers.la_colonia_window_diagnostic_runtime as runtime_module
from precios_supermercados.scrapers.base import HttpResponse, OfflineTestTransport, SafeHttpClient
from precios_supermercados.scrapers.la_colonia import FORBIDDEN_PATH_PREFIXES, USER_AGENT
from precios_supermercados.scrapers.la_colonia_window_diagnostic import WindowDiagnosticReport
from precios_supermercados.scrapers.la_colonia_window_diagnostic_runtime import (
    DIAGNOSTIC_MAX_REQUESTS,
    DIAGNOSTIC_PLAN_NAME,
    EXIT_ANOMALY,
    EXIT_CATALOG_CHANGED,
    EXIT_COMPLETED,
    EXIT_TECHNICAL_STOP,
    FRONTIER_380_399_V1,
    LaColoniaWindowDiagnosticRuntime,
    get_diagnostic_plan,
    render_diagnostic_markdown,
    serialize_diagnostic_summary,
    write_diagnostic_artifacts,
)

FIXED_TIME = datetime(2026, 8, 5, 20, 0, tzinfo=UTC)


class StepClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        self.value += 0.01
        return self.value


def product(key: str, *, items: int = 1):
    return {
        "productId": key,
        "productName": f"Synthetic {key}",
        "items": [{"itemId": f"S-{key}-{index}"} for index in range(items)],
    }


def payload(keys, *, total: int = 9291):
    return {
        "data": {
            "productSearch": {
                "recordsFiltered": total,
                "products": [product(key) for key in keys],
            }
        }
    }


def variables(url: str):
    return json.loads(parse_qs(urlsplit(url).query)["variables"][0])


class PlannedTransport:
    def __init__(self, planner):
        self.planner = planner
        self.calls = []

    def __call__(self, url, headers, timeout):
        values = variables(url)
        self.calls.append(values)
        status, body = self.planner(values, len(self.calls))
        if not isinstance(body, bytes):
            body = json.dumps(body).encode("utf-8")
        return HttpResponse(status, url, {}, body)


def complete_planner(values, call_number):
    start = int(values["from"])
    end = int(values["to"])
    order = values["orderBy"]
    prefix = "N" if order == "OrderByNameASC" else "R"
    return 200, payload([f"{prefix}{index}" for index in range(start, end + 1)])


def build_runtime(
    planner=complete_planner,
    *,
    sleeper=None,
    max_duration=300.0,
    monotonic=None,
):
    transport = PlannedTransport(planner)
    client = SafeHttpClient(
        allowed_hosts={"www.lacolonia.com"},
        forbidden_path_prefixes=FORBIDDEN_PATH_PREFIXES,
        user_agent=USER_AGENT,
        max_retries=0,
        retry_delay_seconds=0,
        transport=OfflineTestTransport(transport),
        sleeper=lambda _: None,
    )
    sleeps = [] if sleeper is None else sleeper
    runtime = LaColoniaWindowDiagnosticRuntime(
        client,
        sleeper=sleeps.append,
        monotonic=monotonic or StepClock(),
        clock=lambda: FIXED_TIME,
        max_duration_seconds=max_duration,
    )
    return runtime, transport, sleeps


def test_only_closed_plan_is_allowed():
    assert get_diagnostic_plan(DIAGNOSTIC_PLAN_NAME) is FRONTIER_380_399_V1
    with pytest.raises(ValueError, match="desconocido"):
        get_diagnostic_plan("arbitrary")


def test_plan_has_exact_order_and_limits():
    assert [item.name for item in FRONTIER_380_399_V1.phase_one] == list("ABCDEFGH")
    assert [(item.from_index, item.to_index) for item in FRONTIER_380_399_V1.phase_one] == [
        (360, 379),
        (370, 389),
        (380, 399),
        (390, 409),
        (400, 419),
        (380, 389),
        (390, 399),
        (350, 399),
    ]
    assert len(FRONTIER_380_399_V1.phase_one) == 8
    assert len(FRONTIER_380_399_V1.phase_two) == 4
    assert FRONTIER_380_399_V1.max_requests == DIAGNOSTIC_MAX_REQUESTS == 12


def test_complete_phase_one_uses_eight_requests_and_exit_zero():
    runtime, transport, sleeps = build_runtime()
    result = runtime.run(request_id="diagnostic-test-001")
    assert result.exit_code == EXIT_COMPLETED
    assert result.summary["requests_attempted"] == 8
    assert result.summary["requests_completed"] == 8
    assert result.summary["phase_two_started"] is False
    assert len(transport.calls) == 8
    assert sleeps == [1.5] * 7
    assert runtime.concurrency == 1


def test_decisive_partial_window_is_anomaly_without_phase_two():
    def planner(values, call_number):
        start, end = int(values["from"]), int(values["to"])
        keys = [f"N{index}" for index in range(start, end + 1)]
        if values["orderBy"] == "OrderByNameASC" and start == 380 and end == 399:
            keys = keys[:-1]
        return 200, payload(keys)

    runtime, transport, _ = build_runtime(planner)
    result = runtime.run(request_id="diagnostic-test-002")
    assert result.exit_code == EXIT_ANOMALY
    assert result.summary["anomalies_detected"] is True
    assert result.summary["phase_two_started"] is False
    assert result.summary["diagnostic_outcome"] in {
        "unexpected_overlap",
        "union_below_expected",
        "window_size_dependent",
        "localized_missing_position",
    }
    assert len(transport.calls) == 8


def test_ambiguous_result_activates_phase_two(monkeypatch):
    monkeypatch.setattr(runtime_module, "_phase_two_required", lambda report, findings: True)
    runtime, transport, sleeps = build_runtime()
    result = runtime.run(request_id="diagnostic-test-003")
    assert result.summary["phase_two_started"] is True
    assert result.summary["requests_attempted"] == 12
    assert len(transport.calls) == 12
    assert sleeps == [1.5] * 11


def test_phase_two_never_exceeds_twelve_requests(monkeypatch):
    monkeypatch.setattr(runtime_module, "_phase_two_required", lambda report, findings: True)
    runtime, transport, _ = build_runtime()
    result = runtime.run(request_id="diagnostic-test-004")
    assert result.summary["requests_planned"] == 12
    assert result.summary["requests_completed"] <= 12
    assert len(transport.calls) <= 12


def test_http_403_stops_with_exit_four():
    runtime, transport, _ = build_runtime(lambda values, n: (403, b"blocked"))
    result = runtime.run(request_id="diagnostic-test-005")
    assert result.exit_code == EXIT_TECHNICAL_STOP
    assert result.summary["stop_reason"] == "http_403_or_captcha"
    assert len(transport.calls) == 1


def test_http_429_is_persistent_without_retries():
    runtime, transport, _ = build_runtime(lambda values, n: (429, b"limited"))
    result = runtime.run(request_id="diagnostic-test-006")
    assert result.exit_code == EXIT_TECHNICAL_STOP
    assert result.summary["stop_reason"] == "persistent_http_429"
    assert len(transport.calls) == 1


def test_http_5xx_stops_without_retry():
    runtime, transport, _ = build_runtime(lambda values, n: (503, b"error"))
    result = runtime.run(request_id="diagnostic-test-007")
    assert result.exit_code == EXIT_TECHNICAL_STOP
    assert result.summary["stop_reason"] == "http_status_503"
    assert len(transport.calls) == 1


def test_catalog_change_stops_with_exit_three():
    def planner(values, call_number):
        start, end = int(values["from"]), int(values["to"])
        total = 9291 if call_number == 1 else 9292
        return 200, payload([f"N{index}" for index in range(start, end + 1)], total=total)

    runtime, transport, _ = build_runtime(planner)
    result = runtime.run(request_id="diagnostic-test-008")
    assert result.exit_code == EXIT_CATALOG_CHANGED
    assert result.summary["diagnostic_outcome"] == "catalog_changed"
    assert result.summary["requests_attempted"] == 2
    assert len(transport.calls) == 2


def test_invalid_graphql_structure_stops():
    runtime, _, _ = build_runtime(lambda values, n: (200, {"data": {"wrong": {}}}))
    result = runtime.run(request_id="diagnostic-test-009")
    assert result.exit_code == EXIT_TECHNICAL_STOP
    assert result.summary["stop_reason"] == "invalid_graphql_structure"


def test_runtime_rejects_retries_and_non_fixed_delay():
    transport = PlannedTransport(complete_planner)
    with pytest.raises(ValueError, match="max_retries debe ser 0"):
        SafeHttpClient(
            allowed_hosts={"www.lacolonia.com"},
            forbidden_path_prefixes=FORBIDDEN_PATH_PREFIXES,
            user_agent=USER_AGENT,
            max_retries=1,
            transport=OfflineTestTransport(transport),
        )

    runtime, _, _ = build_runtime()
    with pytest.raises(ValueError, match="1.5"):
        runtime.run(request_id="diagnostic-test-010", delay_seconds=1.0)


@pytest.mark.parametrize("invalid", [True, float("nan"), float("inf"), float("-inf"), 0])
def test_runtime_rejects_invalid_max_duration(invalid):
    with pytest.raises(ValueError, match="max_duration_seconds"):
        LaColoniaWindowDiagnosticRuntime(max_duration_seconds=invalid)


def test_response_crossing_duration_budget_is_discarded_before_completion():
    class ManualClock:
        value = 0.0

        def __call__(self):
            return self.value

    clock = ManualClock()

    def planner(values, call_number):
        result = complete_planner(values, call_number)
        if call_number == 8:
            clock.value = 301.0
        return result

    runtime, transport, _ = build_runtime(planner, monotonic=clock)
    result = runtime.run(request_id="diagnostic-duration-response")

    assert len(transport.calls) == 8
    assert result.exit_code == EXIT_TECHNICAL_STOP
    assert result.summary["stop_reason"] == "maximum_duration_exceeded"
    assert result.summary["requests_completed"] == 7
    assert result.summary["completed"] is False


def test_sleep_crossing_duration_budget_stops_before_next_request():
    class ManualClock:
        value = 0.0

        def __call__(self):
            return self.value

    clock = ManualClock()
    transport = PlannedTransport(complete_planner)
    client = SafeHttpClient(
        allowed_hosts={"www.lacolonia.com"},
        forbidden_path_prefixes=FORBIDDEN_PATH_PREFIXES,
        user_agent=USER_AGENT,
        max_retries=0,
        retry_delay_seconds=0,
        transport=OfflineTestTransport(transport),
        sleeper=lambda _: None,
    )

    def sleeper(_seconds):
        clock.value = 301.0

    runtime = LaColoniaWindowDiagnosticRuntime(
        client,
        sleeper=sleeper,
        monotonic=clock,
        clock=lambda: FIXED_TIME,
    )
    result = runtime.run(request_id="diagnostic-duration-sleep")

    assert len(transport.calls) == 1
    assert result.exit_code == EXIT_TECHNICAL_STOP
    assert result.summary["requests_completed"] == 1


def test_same_runtime_rejects_concurrent_run_before_second_request():
    entered = threading.Event()
    release = threading.Event()

    def planner(values, call_number):
        entered.set()
        assert release.wait(timeout=2)
        return complete_planner(values, call_number)

    runtime, transport, _ = build_runtime(planner)
    first_result = []
    thread = threading.Thread(
        target=lambda: first_result.append(runtime.run(request_id="diagnostic-concurrent-1"))
    )
    thread.start()
    assert entered.wait(timeout=2)
    with pytest.raises(ValueError, match="already_running"):
        runtime.run(request_id="diagnostic-concurrent-2")
    assert len(transport.calls) == 1
    release.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert len(first_result) == 1


def test_request_id_is_validated():
    runtime, _, _ = build_runtime()
    with pytest.raises(ValueError, match="request_id"):
        runtime.run(request_id="INVALID ID")


def test_artifacts_are_json_and_markdown_only(tmp_path):
    runtime, _, _ = build_runtime()
    result = runtime.run(request_id="diagnostic-test-011")
    write_diagnostic_artifacts(result, tmp_path)
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "diagnostic-summary.json",
        "diagnostic-summary.md",
    ]
    loaded = json.loads((tmp_path / "diagnostic-summary.json").read_text())
    rendered = json.dumps(loaded)
    assert loaded["diagnostic_plan"] == DIAGNOSTIC_PLAN_NAME
    assert "payload" not in rendered
    assert "productId" not in rendered
    assert "itemId" not in rendered
    assert "price" not in rendered.lower()


def test_markdown_is_derived_from_sanitized_summary():
    runtime, _, _ = build_runtime()
    result = runtime.run(request_id="diagnostic-test-012")
    markdown = render_diagnostic_markdown(result.summary)
    assert "diagnostic_outcome" in markdown
    assert "productId" not in markdown
    assert "payload" not in markdown


def test_artifact_limit_is_enforced():
    runtime, _, _ = build_runtime()
    result = runtime.run(request_id="diagnostic-test-013")
    with pytest.raises(ValueError, match="64 KiB"):
        serialize_diagnostic_summary(result.summary, max_bytes=10)


def test_summary_contains_only_allowed_window_fields():
    runtime, _, _ = build_runtime()
    summary = runtime.run(request_id="diagnostic-test-014").summary
    assert set(summary["windows"][0]) == {
        "window",
        "from",
        "to",
        "order_by",
        "products_expected",
        "products_returned",
        "skus_returned",
        "records_filtered",
        "response_bytes",
        "signature",
        "quality_events",
    }


def test_phase_two_rule_is_deterministic_for_ambiguous_report():
    report = WindowDiagnosticReport(
        observations=(),
        overlaps=(),
        expected_unique_positions=0,
        products_unique_in_union=0,
        repeated_occurrences=0,
        duplicates_within_windows=0,
        total_initial=9291,
        total_final=9291,
        quality_events=(),
    )
    assert runtime_module._phase_two_required(report, ["partial_window_observed"]) is True
    assert runtime_module._phase_two_required(report, ["union_below_expected"]) is False


def test_no_real_network_is_used_in_tests():
    runtime, transport, _ = build_runtime()
    runtime.run(request_id="diagnostic-test-015")
    assert transport.calls
    assert all(call["query"] == "supermercado" for call in transport.calls)


def test_maximum_duration_stops_before_request():
    runtime, transport, _ = build_runtime(max_duration=0.001)
    result = runtime.run(request_id="diagnostic-test-016")
    assert result.exit_code == EXIT_TECHNICAL_STOP
    assert result.summary["stop_reason"] == "maximum_duration_exceeded"
    assert transport.calls == []
