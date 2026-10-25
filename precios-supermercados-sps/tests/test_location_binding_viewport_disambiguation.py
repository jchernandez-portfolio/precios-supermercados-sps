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
  <button class="btn-modal-selector" onclick="openLocation()">San pedro sula</button>

  <div id="responsive-copy" style="position:fixed;left:-2500px;top:20px;width:420px;height:260px">
    <div>¿Desde qué ciudad nos visita?</div>
    <label><input type="radio" name="city-offscreen" aria-label="San Pedro Sula">SAN PEDRO SULA</label>
    <label><input type="radio" name="city-offscreen" aria-label="Tegucigalpa">TEGUCIGALPA</label>
  </div>

  <div id="panel" hidden style="position:fixed;left:120px;top:80px;width:420px;height:260px;background:white">
    <div>¿Desde qué ciudad nos visita?</div>
    <label>
      <input id="sps" type="radio" name="city" aria-label="San Pedro Sula"
             onclick="localStorage.setItem('regionId','opaque-region-sps')">
      SAN PEDRO SULA
    </label>
    <label><input type="radio" name="city" aria-label="Tegucigalpa">TEGUCIGALPA</label>
  </div>

  <script>
    function openLocation() {
      document.getElementById('panel').hidden = false;
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
def test_capture_ignores_offviewport_responsive_modal_duplicate(local_page_url) -> None:
    result = capture.run_capture(
        authorization_id=AUTH,
        active_ids={AUTH},
        network_policy="local_only",
        target_url=local_page_url,
    )

    assert result.stop_reason is None
    assert result.visible_location == "San pedro sula"
    assert result.available_cities == ["San Pedro Sula", "Tegucigalpa"]
    assert result.logical_actions == 3
    assert result.binding_report is not None
    assert result.binding_report["granularity_candidate"] == "city"
    assert result.binding_report["confidence"] == "strong"
    assert result.binding_report["technical_binding_observed"] is True
    assert result.production_authority is False
    assert result.catalog_accepted is False
    assert result.extraction_enabled is False
