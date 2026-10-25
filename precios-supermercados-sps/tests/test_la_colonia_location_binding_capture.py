from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import precios_supermercados.diagnostics.la_colonia_location_binding_capture as capture

SYNTHETIC_AUTH = "LC-location-binding-777"
CONSUMED_AUTHS = frozenset(
    {
        "LC-location-binding-336",
        "LC-location-binding-331",
        "LC-location-binding-332",
        "LC-location-binding-333",
        "LC-location-binding-334",
        "LC-location-binding-335",
        "LC-location-binding-337",
    }
)


def _html(*, store_mode: str = "strong") -> str:
    if store_mode == "none":
        stores = ""
    else:
        if store_mode == "strong":
            plaza_effect = "localStorage.setItem('storeId','opaque-store-pedregal')"
            mega_effect = "localStorage.setItem('storeId','opaque-store-megamall')"
        else:
            plaza_effect = "document.cookie='vtex_session=opaque-session-store-pedregal; path=/'"
            mega_effect = "document.cookie='vtex_session=opaque-session-store-megamall; path=/'"
        stores = f'''
        <div id="stores" role="listbox" hidden>
          <button role="option" aria-label="Plaza Pedregal" onclick="{plaza_effect}">Plaza Pedregal</button>
          <button role="option" aria-label="Mega Mall" onclick="{mega_effect}">Mega Mall</button>
        </div>
        '''
    show_stores = (
        "document.getElementById('stores').hidden=false;"
        if store_mode != "none"
        else ""
    )
    return f'''<!doctype html>
    <html><body>
      <button role="button" aria-label="Selecciona tu tienda"
              onclick="document.getElementById('panel').hidden=false">
        Selecciona tu tienda
      </button>
      <div id="panel" hidden>
        <select id="city" aria-label="Ciudad"
          onchange="if(this.value==='San Pedro Sula'){{localStorage.setItem('regionId','opaque-region-sps');document.cookie='vtex_session=opaque-session-city; path=/';{show_stores}}}">
          <option>Selecciona tu ciudad</option>
          <option role="option" aria-label="San Pedro Sula">San Pedro Sula</option>
          <option role="option" aria-label="Tegucigalpa">Tegucigalpa</option>
        </select>
        {stores}
      </div>
    </body></html>'''


@pytest.fixture
def local_site():
    state = {"html": _html()}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            raw = state["html"].encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_live_is_closed_and_all_location_binding_authorizations_are_consumed() -> None:
    assert capture.LIVE_EXECUTION_ENABLED is False
    assert capture.ACTIVE_AUTHORIZATION_IDS == frozenset()
    assert capture.CONSUMED_AUTHORIZATION_IDS == CONSUMED_AUTHS

    for authorization_id in CONSUMED_AUTHS:
        consumed = capture.run_capture(authorization_id=authorization_id)
        assert consumed.browser_started is False
        assert consumed.target_navigation_started is False
        assert consumed.stop_reason == "authorization_id_consumed"

    unrelated = capture.run_capture(authorization_id=SYNTHETIC_AUTH)
    assert unrelated.browser_started is False
    assert unrelated.target_navigation_started is False
    assert unrelated.stop_reason == "authorization_id_not_active"
    assert unrelated.production_authority is False
    assert unrelated.catalog_accepted is False
    assert unrelated.extraction_enabled is False


def test_live_runtime_cannot_override_allow_list_or_fuse() -> None:
    result = capture.run_capture(
        authorization_id=SYNTHETIC_AUTH,
        active_ids={SYNTHETIC_AUTH},
        live_execution_enabled=True,
    )
    assert result.stop_reason == "live_runtime_overrides_forbidden"
    assert result.browser_started is False
    assert result.target_navigation_started is False


def test_authorization_format_and_consumption_are_fail_closed() -> None:
    with pytest.raises(capture.LocationBindingCaptureError, match="authorization_id_invalid_format"):
        capture.validate_capture_authorization(
            authorization_id="bad",
            network_policy="local_only",
            active_ids={"bad"},
        )
    with pytest.raises(capture.LocationBindingCaptureError, match="authorization_id_consumed"):
        capture.validate_capture_authorization(
            authorization_id=SYNTHETIC_AUTH,
            network_policy="local_only",
            active_ids={SYNTHETIC_AUTH},
            consumed_ids={SYNTHETIC_AUTH},
        )


class FakeRequest:
    headers = {"x-vtex-segment": "opaque-segment", "authorization": "do-not-copy"}
    url = "https://synthetic.invalid/graphql?regionId=opaque-region&productId=do-not-copy"
    post_data_json = {
        "variables": {
            "storeId": "opaque-store",
            "productId": "do-not-copy",
            "nested": {"salesChannel": "2"},
        }
    }


def test_request_collector_keeps_only_context_fields_not_full_request() -> None:
    collector = capture.RequestContextCollector()
    collector.observe_request(FakeRequest())
    snapshot = collector.snapshot()

    assert set(snapshot) == {"regionId", "salesChannel", "storeId", "vtex_segment"}
    serialized = json.dumps(snapshot, sort_keys=True)
    assert "authorization" not in serialized
    assert "productId" not in serialized
    assert "do-not-copy" not in serialized
    assert "synthetic.invalid" not in serialized


@pytest.mark.skipif(os.environ.get("CI") != "true", reason="browser loopback se valida en GitHub runner")
def test_local_capture_discovers_all_cities_and_store_granularity(local_site, tmp_path) -> None:
    state, base_url = local_site
    state["html"] = _html(store_mode="strong")
    output = tmp_path / "store.json"

    result = capture.run_capture(
        authorization_id=SYNTHETIC_AUTH,
        active_ids={SYNTHETIC_AUTH},
        network_policy="local_only",
        target_url=base_url,
        output_path=output,
        budget=capture.DiagnosticBudget(
            max_logical_requests=4,
            minimum_delay_seconds=1.5,
        ),
    )

    assert result.stop_reason is None
    assert result.available_cities == ["San Pedro Sula", "Tegucigalpa"]
    assert result.store_selection_observed is True
    assert result.available_stores == ["Mega Mall", "Plaza Pedregal"]
    assert result.selected_store == "Mega Mall"
    assert result.binding_report is not None
    assert result.binding_report["granularity_candidate"] == "store"
    assert result.binding_report["confidence"] == "strong"
    assert result.binding_report["raw_values_exposed"] is False
    assert result.logical_actions == 4

    rendered = output.read_text(encoding="utf-8")
    for raw in (
        "opaque-region-sps",
        "opaque-store-pedregal",
        "opaque-store-megamall",
        "opaque-session-city",
    ):
        assert raw not in rendered
    persisted = json.loads(rendered)
    assert persisted["production_authority"] is False
    assert persisted["catalog_accepted"] is False
    assert persisted["extraction_enabled"] is False


@pytest.mark.skipif(os.environ.get("CI") != "true", reason="browser loopback se valida en GitHub runner")
def test_local_capture_can_conclude_city_when_no_store_selection_exists(local_site) -> None:
    state, base_url = local_site
    state["html"] = _html(store_mode="none")

    result = capture.run_capture(
        authorization_id=SYNTHETIC_AUTH,
        active_ids={SYNTHETIC_AUTH},
        network_policy="local_only",
        target_url=base_url,
    )

    assert result.stop_reason is None
    assert result.available_cities == ["San Pedro Sula", "Tegucigalpa"]
    assert result.store_selection_observed is False
    assert result.selected_store is None
    assert result.binding_report["granularity_candidate"] == "city"
    assert result.binding_report["confidence"] == "strong"
    assert result.logical_actions == 3


@pytest.mark.skipif(os.environ.get("CI") != "true", reason="browser loopback se valida en GitHub runner")
def test_weak_store_session_change_does_not_falsely_confirm_city(local_site) -> None:
    state, base_url = local_site
    state["html"] = _html(store_mode="weak")

    result = capture.run_capture(
        authorization_id=SYNTHETIC_AUTH,
        active_ids={SYNTHETIC_AUTH},
        network_policy="local_only",
        target_url=base_url,
    )

    assert result.stop_reason is None
    assert result.store_selection_observed is True
    assert result.selected_store == "Mega Mall"
    assert result.binding_report["granularity_candidate"] == "unknown"
    assert result.binding_report["confidence"] == "weak"
    assert result.binding_report["technical_binding_observed"] is False


def test_live_target_must_be_exact_home_before_any_browser_start() -> None:
    result = capture.run_capture(
        authorization_id=SYNTHETIC_AUTH,
        network_policy="live",
        target_url="https://example.com/",
    )
    assert result.browser_started is False
    assert result.target_navigation_started is False
    assert result.stop_reason == "live_target_not_exact_la_colonia_home"


@pytest.mark.parametrize(
    "url",
    [
        "http://www.lacolonia.com/",
        "https://www.lacolonia.com/producto",
        "https://www.lacolonia.com/?x=1",
        "https://www.lacolonia.com/#fragment",
        "https://www.lacolonia.com:444/",
        "https://user@www.lacolonia.com/",
    ],
)
def test_live_target_rejects_home_lookalikes(url: str) -> None:
    result = capture.run_capture(
        authorization_id=SYNTHETIC_AUTH,
        network_policy="live",
        target_url=url,
    )
    assert result.stop_reason == "live_target_not_exact_la_colonia_home"
    assert result.browser_started is False


def test_public_result_rejects_any_attempt_to_grant_authority() -> None:
    result = capture.LocationBindingCaptureResult(
        mode="synthetic_local",
        started_at="fixture",
        production_authority=True,
    )
    with pytest.raises(
        capture.LocationBindingCaptureError,
        match="capture_cannot_grant_commercial_authority",
    ):
        result.public_dict()


def test_public_result_accepts_only_bounded_city_control_diagnostic() -> None:
    result = capture.LocationBindingCaptureResult(
        mode="synthetic_local",
        started_at="fixture",
        city_control_diagnostic={
            "stage": "role",
            "role": "radio",
            "candidate_count": 2,
            "effective_count": 2,
        },
    )
    rendered = result.public_dict()
    assert rendered["city_control_diagnostic"] == {
        "stage": "role",
        "role": "radio",
        "candidate_count": 2,
        "effective_count": 2,
    }


def test_public_result_rejects_raw_dom_detail_in_city_control_diagnostic() -> None:
    result = capture.LocationBindingCaptureResult(
        mode="synthetic_local",
        started_at="fixture",
        city_control_diagnostic={
            "stage": "role",
            "role": "radio",
            "candidate_count": 2,
            "selector": "div.secret-layout > input",
        },
    )
    with pytest.raises(
        capture.LocationBindingCaptureError,
        match="city_control_diagnostic_invalid",
    ):
        result.public_dict()
