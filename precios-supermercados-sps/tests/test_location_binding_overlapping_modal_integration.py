from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import precios_supermercados.diagnostics.la_colonia_location_binding_capture as capture
from precios_supermercados.diagnostics.location_binding_dom_controls import (
    CITY_CONTROL_READY_TIMEOUT_MS,
)

AUTH = "LC-location-binding-777"


def _html() -> str:
    return '''<!doctype html>
<html>
<head>
  <style>
    .modal-copy { position: fixed; left: 495px; top: 373px; width: 450px; height: 254px; background: white; }
    .cont-btn-ciudad { position: absolute; left: 50px; top: 95px; width: 354px; height: 70px; }
    .cont-btn-ciudad button { position: absolute; top: 15px; width: 160px; height: 40px; }
    .cont-btn-ciudad button:first-child { left: 0; }
    .cont-btn-ciudad button:last-child { left: 190px; }
  </style>
</head>
<body>
  <button class="btn-modal-selector" onclick="openLocation()">Selecciona tu tienda</button>
  <div id="mount"></div>
  <script>
    const copy = (id) => `
      <div class="modal-copy" id="${id}">
        <h3>¿Desde qué ciudad nos visita?</h3>
        <div class="cont-btn-ciudad">
          <button class="btn-ciudad-noselected">Tegucigalpa</button>
          <button class="btn-ciudad-noselected" onclick="selectSps()"><span class="radio"></span>San pedro sula</button>
        </div>
      </div>`;
    function openLocation() {
      setTimeout(() => {
        document.getElementById('mount').innerHTML = copy('first') + copy('second');
      }, 3300);
    }
    function selectSps() {
      localStorage.setItem('regionId', 'opaque-region-sps');
      document.querySelector('.btn-modal-selector').textContent = 'San pedro sula';
      document.getElementById('mount').innerHTML = '';
    }
  </script>
</body>
</html>'''


@pytest.fixture
def local_page_url():
    body = _html().encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_readiness_window_covers_observed_delayed_modal_hydration() -> None:
    assert CITY_CONTROL_READY_TIMEOUT_MS == 5_000


@pytest.mark.skipif(os.environ.get("CI") != "true", reason="browser loopback se valida en GitHub runner")
def test_capture_collapses_overlapping_duplicate_modal_and_selects_sps(local_page_url) -> None:
    result = capture.run_capture(
        authorization_id=AUTH,
        active_ids={AUTH},
        network_policy="local_only",
        target_url=local_page_url,
    )

    assert result.stop_reason is None
    assert result.visible_location == "San pedro sula"
    assert result.available_cities == ["San pedro sula", "Tegucigalpa"]
    assert result.logical_actions == 3
    assert result.binding_report is not None
    assert result.binding_report["granularity_candidate"] == "city"
    assert result.binding_report["technical_binding_observed"] is True
    assert result.binding_report["confidence"] == "strong"
    assert result.production_authority is False
    assert result.catalog_accepted is False
    assert result.extraction_enabled is False
