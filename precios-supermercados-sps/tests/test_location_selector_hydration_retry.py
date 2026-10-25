from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from precios_supermercados.diagnostics.la_colonia_sps_context_diagnostic import (
    launch_compatible_chromium,
)
from precios_supermercados.diagnostics.location_binding_dom_controls import (
    CITY_STATE_UNSELECTED,
    open_location_selector,
    resolve_exact_city_control,
)

HTML = b'''<!doctype html>
<html><body>
  <button class="btn-modal-selector">Selecciona tu tienda</button>
  <div id="panel" hidden>
    <p>\xc2\xbfDesde qu\xc3\xa9 ciudad nos visita?</p>
    <div class="cont-btn-ciudad">
      <button class="btn-ciudad-selected">Tegucigalpa</button>
      <button class="btn-ciudad-noselected">San pedro sula</button>
    </div>
  </div>
  <script>
    window.hydratedClicks = 0;
    setTimeout(() => {
      const opener = document.querySelector('.btn-modal-selector');
      opener.addEventListener('click', () => {
        window.hydratedClicks += 1;
        document.getElementById('panel').hidden = false;
      });
    }, 1800);
  </script>
</body></html>'''


@pytest.fixture
def delayed_hydration_url():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(HTML)))
            self.end_headers()
            self.wfile.write(HTML)

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
def test_location_selector_retries_until_delayed_handler_is_hydrated(delayed_hydration_url) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser, _ = launch_compatible_chromium(playwright)
        context = browser.new_context(service_workers="block")
        page = context.new_page()
        try:
            page.goto(delayed_hydration_url, wait_until="domcontentloaded", timeout=10_000)

            resolved = open_location_selector(page)
            control = resolve_exact_city_control(page, "San Pedro Sula")

            assert resolved.source == "btn-modal-selector"
            assert page.locator("#panel").is_visible() is True
            assert page.evaluate("() => window.hydratedClicks") == 1
            assert control.state == CITY_STATE_UNSELECTED
            assert control.available_cities == ("San pedro sula", "Tegucigalpa")
        finally:
            context.close()
            browser.close()
