from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import precios_supermercados.diagnostics.la_colonia_location_binding_capture as capture

AUTH = "LC-location-binding-777"


def _html(*, sps_selected: bool) -> str:
    if sps_selected:
        initial_header = "San pedro sula"
        tgu_class = "btn-ciudad-noselected"
        sps_class = "btn-ciudad-selected"
        bootstrap = "localStorage.setItem('regionId','opaque-region-sps');"
        sps_effect = "localStorage.setItem('regionId','unexpected-click');"
    else:
        initial_header = "Tegucigalpa"
        tgu_class = "btn-ciudad-selected"
        sps_class = "btn-ciudad-noselected"
        bootstrap = ""
        sps_effect = "localStorage.setItem('regionId','opaque-region-sps');"

    return f'''<!doctype html>
<html><body>
  <div class="cont-btn-selector">
    <button id="header-city" class="btn-modal-selector"
            onclick="document.getElementById('city-panel').hidden=false">
      {initial_header}
    </button>
  </div>
  <div id="city-panel" hidden>
    <div class="cont-btn-ciudad">
      <button id="tgu" class="{tgu_class}">
        <span class="radio"></span>Tegucigalpa
      </button>
      <button id="sps" class="{sps_class}" onclick="selectSps()">
        <span class="radio"></span>San pedro sula
      </button>
    </div>
  </div>
  <script>
    {bootstrap}
    function selectSps() {{
      {sps_effect}
      document.getElementById('tgu').className = 'btn-ciudad-noselected';
      document.getElementById('sps').className = 'btn-ciudad-selected';
      document.getElementById('header-city').textContent = 'San pedro sula';
    }}
  </script>
</body></html>'''


@pytest.fixture
def local_site():
    state = {"html": _html(sps_selected=False)}

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


@pytest.mark.skipif(os.environ.get("CI") != "true", reason="browser loopback se valida en GitHub runner")
def test_real_city_button_shape_transitions_to_sps_and_observes_context(local_site) -> None:
    state, base_url = local_site
    state["html"] = _html(sps_selected=False)

    result = capture.run_capture(
        authorization_id=AUTH,
        active_ids={AUTH},
        network_policy="local_only",
        target_url=base_url,
    )

    assert result.stop_reason is None
    assert result.visible_location == "San pedro sula"
    assert result.available_cities == ["San pedro sula", "Tegucigalpa"]
    assert result.logical_actions == 3
    assert result.binding_report is not None
    assert result.binding_report["granularity_candidate"] == "city"
    assert result.binding_report["confidence"] == "strong"
    assert result.binding_report["technical_binding_observed"] is True
    assert result.production_authority is False
    assert result.catalog_accepted is False
    assert result.extraction_enabled is False


@pytest.mark.skipif(os.environ.get("CI") != "true", reason="browser loopback se valida en GitHub runner")
def test_already_selected_sps_is_verified_without_clicking_city(local_site) -> None:
    state, base_url = local_site
    state["html"] = _html(sps_selected=True)

    result = capture.run_capture(
        authorization_id=AUTH,
        active_ids={AUTH},
        network_policy="local_only",
        target_url=base_url,
    )

    assert result.stop_reason is None
    assert result.visible_location == "San pedro sula"
    assert result.available_cities == ["San pedro sula", "Tegucigalpa"]
    assert result.logical_actions == 2
    assert result.binding_report is not None
    assert result.binding_report["granularity_candidate"] == "unknown"
    assert result.binding_report["technical_binding_observed"] is False
    assert result.production_authority is False
    assert result.catalog_accepted is False
    assert result.extraction_enabled is False
