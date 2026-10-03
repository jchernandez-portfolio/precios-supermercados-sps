"""Primera corrida real 2026-10-03: el HTML del servidor no trae especificaciones.

La corrida 37132418500 descargó 900 fichas por HTTP y obtuvo 889
``no_specifications``: la página carga los valores después en el navegador. Estas
pruebas usan el DOM renderizado REAL (reducido) de la ficha 415586 capturado en
navegador ese día y cubren el transporte con Chromium y el corte temprano.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import obtener_especificaciones_pricesmart as capture  # noqa: E402
from precios_supermercados.scrapers.pricesmart_specs import (  # noqa: E402
    STATUS_NO_SPECIFICATIONS,
    STATUS_PARSED,
    parse_product_page,
)
from test_pricesmart_specs_capture import Clock, FakeTransport, catalog, run  # noqa: E402,F401

RENDERED = (ROOT / "tests" / "fixtures" / "pricesmart_specs" / "RENDERED-breadco-415586.html").read_text(encoding="utf-8")
URL = "https://www.pricesmart.com/es-hn/producto/breadco-pastelito-de-pina-16-unidades-415586/415586"


def _server_html() -> str:
    """Lo que entrega el servidor: mismo armazón, bloque de especificaciones vacío."""
    start = RENDERED.index('<div class="product-description__container">')
    end = RENDERED.index("</table>") + len("</table></li></ul></div></div></div>")
    return RENDERED[:start] + RENDERED[end:]


def test_rendered_real_dom_is_parsed_from_the_specifications_table():
    result = parse_product_page(RENDERED, "415586")
    assert result.status == STATUS_PARSED
    assert result.extraction_method == "dom_structured"
    specs = result.specs
    assert specs["brand"] == "Breadco"
    assert specs["category_path"] == ["Alimentos", "Panadería y repostería"]
    assert specs["net_weight"]["value_g"] == "1450"
    assert specs["unit_weight"]["value_g"] == "90.63"
    assert specs["pack_count"] == 16
    assert specs["imported_or_national"] == "nacional"
    assert specs["allergens"] == ["Leche", "Huevos", "Trigo"]
    assert specs["presentation_hint"] == "16 x 90.63 g"
    assert specs["consistency"]["pack_x_unit_vs_net"] == "consistent"
    assert "visible_item_number" in specs["identity_evidence"]
    assert specs["gtin"] is None  # el número de ítem nunca es GTIN


def test_server_html_without_values_is_no_specifications():
    assert parse_product_page(_server_html(), "415586").status == STATUS_NO_SPECIFICATIONS


@pytest.mark.parametrize(("failures", "evaluable", "expected"), [
    (30, 30, "early_failure_rate:30/30"),
    (25, 30, "early_failure_rate:25/30"),
    (24, 30, None),
    (29, 29, None),
])
def test_early_abort_only_at_the_checkpoint(failures, evaluable, expected):
    results = [{"status": "no_specifications"}] * failures + [{"status": STATUS_PARSED}] * (evaluable - failures)
    results += [{"status": "not_found"}] * 5  # no cuentan
    assert capture.early_abort_reason(results) == expected


def test_broken_format_stops_after_30_pages_instead_of_the_whole_budget(catalog, tmp_path: Path):
    items, evidence = catalog
    clock = Clock()
    server = _server_html()
    transport = FakeTransport(clock, default=lambda pid, url: capture.Response(200, url, server.replace("415586", pid).encode()))
    artifact, transport, _ = run(items[:200], evidence, tmp_path=tmp_path, transport=transport, clock=clock, max_items=200)
    assert artifact["result"] == "failed"
    assert artifact["run"]["aborted_reason"] == "early_failure_rate:30/30"
    assert len(transport.calls) == 30
    assert artifact["coverage"]["not_attempted"] == 170


def test_route_allows_only_pricesmart_documents_and_scripts():
    class Request:
        def __init__(self, url: str, kind: str):
            self.url, self.resource_type = url, kind

    class Route:
        def __init__(self, url: str, kind: str):
            self.request, self.result = Request(url, kind), None

        def abort(self):
            self.result = "abort"

        def continue_(self):
            self.result = "continue"

    cases = {
        (URL, "document"): "continue",
        ("https://www.pricesmart.com/api/ct/getProduct", "fetch"): "continue",
        ("https://www.pricesmart.com/_nuxt/app.js", "script"): "continue",
        ("https://www.pricesmart.com/img/p.jpg", "image"): "abort",
        ("https://www.pricesmart.com/f.woff2", "font"): "abort",
        ("https://static.cloudflareinsights.com/beacon.min.js", "script"): "abort",
        ("https://go.botmaker.com/rest/webchat/init.js", "script"): "abort",
    }
    for (url, kind), expected in cases.items():
        route = Route(url, kind)
        capture.BrowserTransport._route(route)
        assert route.result == expected, url


def _browser_or_skip() -> "capture.BrowserTransport":
    pytest.importorskip("playwright.sync_api")
    try:
        return capture.BrowserTransport()
    except Exception as exc:  # noqa: BLE001 - navegador no instalado en este entorno
        pytest.skip(f"chromium_not_available:{type(exc).__name__}")


def test_browser_transport_returns_rendered_dom_offline():
    transport = _browser_or_skip()
    try:
        transport._page.route(  # noqa: SLF001 - servimos la ficha sin red
            "https://www.pricesmart.com/**",
            lambda route: route.fulfill(status=200, body=RENDERED, content_type="text/html; charset=utf-8"),
        )
        response = transport(URL)
    finally:
        transport.close()
    assert (response.status, response.error) == (200, None)
    assert response.final_url == URL
    result = parse_product_page(response.body.decode("utf-8"), "415586")
    assert result.status == STATUS_PARSED and result.specs["brand"] == "Breadco"


def test_browser_transport_reports_http_errors_without_body():
    transport = _browser_or_skip()
    try:
        transport._page.route(  # noqa: SLF001
            "https://www.pricesmart.com/**",
            lambda route: route.fulfill(status=404, body="no", content_type="text/html"),
        )
        response = transport(URL)
    finally:
        transport.close()
    assert (response.status, response.body, response.error) == (404, b"", "http_404")
