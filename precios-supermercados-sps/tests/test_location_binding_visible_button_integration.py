from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import precios_supermercados.diagnostics.la_colonia_location_binding_capture as capture

AUTH = "LC-location-binding-777"


def _html() -> str:
    return '''<!doctype html>
<html><body>
  <div class="vtex-flex-layout-0-x-flexColChild vtex-flex-layout-0-x-flexColChild--notificationBarRight pb0" style="height: 100%;">
    <div class="cont-btn-selector">
      <button class="btn-modal-selector" onclick="openLocation()">San pedro sula</button>
    </div>
  </div>
  <div aria-hidden="true" style="display:none">
    <select aria-label="Ciudad">
      <option>Selecciona tu ciudad</option>
      <option aria-label="San Pedro Sula">San Pedro Sula</option>
      <option aria-label="Tegucigalpa">Tegucigalpa</option>
    </select>
  </div>
  <div id="panel" hidden>
    <div id="city-slot"></div>
  </div>
  <script>
    function openLocation() {
      document.getElementById('panel').hidden = false;
      setTimeout(() => {
        document.getElementById('city-slot').innerHTML = `
          <select aria-label="Ciudad" onchange="if(this.value==='San Pedro Sula'){localStorage.setItem('regionId','opaque-region-sps')}">
            <option>Selecciona tu ciudad</option>
            <option aria-label="San Pedro Sula">San Pedro Sula</option>
            <option aria-label="Tegucigalpa">Tegucigalpa</option>
          </select>`;
      }, 700);
    }
  </script>
</body></html>'''


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


@pytest.mark.skipif(os.environ.get("CI") != "true", reason="browser loopback se valida en GitHub runner")
def test_capture_waits_for_visible_city_control_and_ignores_hidden_duplicate(local_page_url) -> None:
    result = capture.run_capture(
        authorization_id=AUTH,
        active_ids={AUTH},
        network_policy="local_only",
        target_url=local_page_url,
    )

    assert result.stop_reason is None
    assert result.visible_location == "San pedro sula"
    assert result.available_cities == ["San Pedro Sula", "Tegucigalpa"]
    assert result.binding_report is not None
    assert result.binding_report["granularity_candidate"] == "city"
    assert result.binding_report["technical_binding_observed"] is True
    assert result.logical_actions == 3
    assert result.production_authority is False
    assert result.catalog_accepted is False
    assert result.extraction_enabled is False
