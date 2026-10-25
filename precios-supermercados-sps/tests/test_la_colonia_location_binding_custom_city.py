from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import precios_supermercados.diagnostics.la_colonia_location_binding_capture as capture

SYNTHETIC_AUTH = "LC-location-binding-777"


def _html(*, duplicate_target: bool = False) -> str:
    duplicate = (
        '<button role="button" aria-label="San Pedro Sula">San Pedro Sula</button>'
        if duplicate_target
        else ""
    )
    return f'''<!doctype html>
    <html><body>
      <button role="button" aria-label="Selecciona tu tienda"
              onclick="document.getElementById('panel').hidden=false">
        Selecciona tu tienda
      </button>
      <div id="panel" hidden>
        <div role="group" aria-label="Ciudad">
          <button role="button" aria-label="San Pedro Sula"
                  onclick="localStorage.setItem('regionId','opaque-region-sps')">
            San Pedro Sula
          </button>
          {duplicate}
          <button role="button" aria-label="Tegucigalpa">Tegucigalpa</button>
        </div>
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


@pytest.mark.skipif(os.environ.get("CI") != "true", reason="browser loopback se valida en GitHub runner")
def test_capture_accepts_exact_unique_custom_city_button(local_site) -> None:
    _state, base_url = local_site

    result = capture.run_capture(
        authorization_id=SYNTHETIC_AUTH,
        active_ids={SYNTHETIC_AUTH},
        network_policy="local_only",
        target_url=base_url,
    )

    assert result.stop_reason is None
    assert result.available_cities == ["San Pedro Sula"]
    assert result.logical_actions == 3
    assert result.store_selection_observed is False
    assert result.binding_report is not None
    assert result.binding_report["granularity_candidate"] == "city"
    assert result.binding_report["confidence"] == "strong"
    assert result.binding_report["technical_binding_observed"] is True
    assert result.production_authority is False
    assert result.catalog_accepted is False
    assert result.extraction_enabled is False


@pytest.mark.skipif(os.environ.get("CI") != "true", reason="browser loopback se valida en GitHub runner")
def test_capture_rejects_duplicate_custom_city_buttons_before_selection(local_site) -> None:
    state, base_url = local_site
    state["html"] = _html(duplicate_target=True)

    result = capture.run_capture(
        authorization_id=SYNTHETIC_AUTH,
        active_ids={SYNTHETIC_AUTH},
        network_policy="local_only",
        target_url=base_url,
    )

    assert result.stop_reason == "target_city_not_unique"
    assert result.logical_actions == 2
    assert result.target_navigation_completed is True
    assert result.binding_report is None
    assert result.production_authority is False
    assert result.catalog_accepted is False
    assert result.extraction_enabled is False
