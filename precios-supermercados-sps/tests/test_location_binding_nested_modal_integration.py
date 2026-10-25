from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import precios_supermercados.diagnostics.la_colonia_location_binding_capture as capture

AUTH = "LC-location-binding-777"


def _html() -> str:
    return '''<!doctype html>
<html>
<body>
  <button class="btn-modal-selector" onclick="document.getElementById('modal').hidden=false">
    Selecciona tu tienda
  </button>
  <section id="modal" hidden>
    <div class="prompt-shell">
      <div class="prompt-inner">
        <h2>¿Desde qué ciudad nos visita?</h2>
      </div>
    </div>
    <div class="cities">
      <div role="radio" aria-label="Tegucigalpa">
        <input type="radio" aria-label="Tegucigalpa">
        <span>TEGUCIGALPA</span>
      </div>
      <div role="radio" aria-label="San Pedro Sula"
           onclick="localStorage.setItem('regionId','opaque-region-sps')">
        <input type="radio" aria-label="San Pedro Sula">
        <span>SAN PEDRO SULA</span>
      </div>
    </div>
  </section>
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


@pytest.mark.skipif(os.environ.get("CI") != "true", reason="browser loopback se valida en GitHub runner")
def test_capture_selects_sps_when_prompt_and_radio_have_nested_accessible_matches(local_page_url) -> None:
    result = capture.run_capture(
        authorization_id=AUTH,
        active_ids={AUTH},
        network_policy="local_only",
        target_url=local_page_url,
    )

    assert result.stop_reason is None
    assert result.available_cities == ["San Pedro Sula", "Tegucigalpa"]
    assert result.logical_actions == 3
    assert result.binding_report is not None
    assert result.binding_report["granularity_candidate"] == "city"
    assert result.binding_report["technical_binding_observed"] is True
    assert result.city_control_diagnostic is None
    assert result.production_authority is False
    assert result.catalog_accepted is False
    assert result.extraction_enabled is False
