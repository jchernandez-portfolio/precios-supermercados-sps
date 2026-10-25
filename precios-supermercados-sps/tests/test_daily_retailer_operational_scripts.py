from __future__ import annotations

import base64
import importlib.util
import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _load(name: str):
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


colonial = _load("obtener_catalogo_colonial_operativo")
walmart = _load("obtener_catalogo_walmart_operativo")
pricesmart = _load("obtener_catalogo_pricesmart_operativo")


def test_walmart_operational_contract_reuses_proven_store_binding() -> None:
    seller = "walmarthnwm947"
    assert walmart.region_id(seller) == base64.b64encode(("SW#" + seller).encode()).decode()
    assert walmart.SALES_CHANNEL == "1"
    assert walmart.PAGE_SIZE == 100
    assert walmart.CATEGORY2_PARTITIONS == {"articulos-para-el-hogar", "ropa-y-zapateria"}
    assert walmart.MAX_REQUESTS_HARD == 700
    assert walmart.DEFAULT_MAX_RETRIES == 2
    assert walmart.MAX_RETRIES_HARD == 2
    assert walmart.RETRYABLE_HTTP_STATUSES == {429, 500, 502, 503, 504}


def test_walmart_retries_transient_http_failure_within_request_budget(monkeypatch, tmp_path: Path) -> None:
    seller = "walmarthnwm947"
    calls = 0

    class Response:
        status = 200

        def __init__(self, url: str) -> None:
            self.url = url

        def geturl(self) -> str:
            return self.url

        def read(self) -> bytes:
            return b'{"recordsFiltered":0,"products":[]}'

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> bool:
            return False

    def fake_urlopen(request, timeout: int):
        nonlocal calls
        assert timeout == 45
        calls += 1
        if calls == 1:
            raise walmart.urllib.error.HTTPError(
                request.full_url,
                500,
                "transient",
                {},
                io.BytesIO(b'{"error":"transient"}'),
            )
        return Response(request.full_url)

    monkeypatch.setattr(walmart.urllib.request, "urlopen", fake_urlopen)
    capture = walmart.Capture(tmp_path, delay=0, max_requests=3, max_retries=2)
    path = f"/api/io/_v/api/intelligent-search/product_search/accesscontrollist/{seller}"
    doc, record = capture.get(
        seller,
        "test/page-001",
        path,
        {**walmart.common_query(seller), "count": "1", "page": "1"},
    )

    assert doc == {"recordsFiltered": 0, "products": []}
    assert record["status"] == 200
    assert calls == 2
    assert capture.retry_count == 1
    assert [item["status"] for item in capture.records] == [500, 200]
    assert [item["tag"] for item in capture.records] == ["test/page-001", "test/page-001/retry-1"]
    assert len(list(tmp_path.glob("*.raw"))) == 2


def test_walmart_does_not_retry_non_transient_http_failure(monkeypatch, tmp_path: Path) -> None:
    seller = "walmarthnwm947"
    calls = 0

    def fake_urlopen(request, timeout: int):
        nonlocal calls
        assert timeout == 45
        calls += 1
        raise walmart.urllib.error.HTTPError(request.full_url, 400, "bad request", {}, io.BytesIO(b"{}"))

    monkeypatch.setattr(walmart.urllib.request, "urlopen", fake_urlopen)
    capture = walmart.Capture(tmp_path, delay=0, max_requests=3, max_retries=2)
    path = f"/api/io/_v/api/intelligent-search/product_search/accesscontrollist/{seller}"
    with pytest.raises(RuntimeError, match="request_failed:test/page-001:400:http_400"):
        capture.get(
            seller,
            "test/page-001",
            path,
            {**walmart.common_query(seller), "count": "1", "page": "1"},
        )

    assert calls == 1
    assert capture.retry_count == 0
    assert len(capture.records) == 1


def test_pricesmart_operational_contract_is_bounded_to_two_accepted_clubs() -> None:
    assert pricesmart.category_url("G10D03", "Alimentos") == (
        "https://www.pricesmart.com/es-hn/categoria/Alimentos-G10D03/G10D03"
    )
    assert len(pricesmart.ROOTS) == 26
    assert {key for key, _ in pricesmart.ROOTS} >= {"U11D13", "J10D44", "G10D03"}
    sps_fields = pricesmart.fields_for_club("6603")
    assert "price_HN_6603" in sps_fields
    assert "availability_HN_6603" in sps_fields
    assert "_HN_6604" not in sps_fields
    assert pricesmart.MAX_REQUESTS_HARD == 80
    assert pricesmart.DEFAULT_MAX_RETRIES == 2
    assert pricesmart.MAX_RETRIES_HARD == 2
    assert pricesmart.RETRYABLE_HTTP_STATUSES == {429, 500, 502, 503, 504}


def test_pricesmart_retries_transient_http_failure_within_request_budget(monkeypatch, tmp_path: Path) -> None:
    calls = 0

    class Response:
        status = 200

        def geturl(self) -> str:
            return pricesmart.ENDPOINT

        def read(self) -> bytes:
            return b'{"response":{"numFound":0,"start":0,"docs":[]}}'

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> bool:
            return False

    def fake_urlopen(request, timeout: int):
        nonlocal calls
        assert timeout == 45
        calls += 1
        if calls == 1:
            raise pricesmart.urllib.error.HTTPError(
                request.full_url,
                503,
                "transient",
                {},
                io.BytesIO(b'{"error":"transient"}'),
            )
        return Response()

    monkeypatch.setattr(pricesmart.urllib.request, "urlopen", fake_urlopen)
    capture = pricesmart.Capture(tmp_path, delay=0, max_requests=3, max_retries=2)
    payload, record = capture.post("6603", "G10D03", "Alimentos", 0)

    assert payload == {"response": {"numFound": 0, "start": 0, "docs": []}}
    assert record["http_status"] == 200
    assert calls == 2
    assert capture.retry_count == 1
    assert [item["http_status"] for item in capture.records] == [503, 200]
    assert [item["attempt"] for item in capture.records] == [1, 2]
    assert len(list((tmp_path / "6603").glob("*.json"))) == 2


@pytest.mark.parametrize(
    ("module", "argv"),
    [
        (colonial, ["prog", "--output", "out"]),
        (
            walmart,
            ["prog", "--output-directory", "out", "--raw-directory", "raw", "--evidence-output", "evidence.json"],
        ),
        (
            pricesmart,
            ["prog", "--output-directory", "out", "--raw-directory", "raw", "--evidence-output", "evidence.json"],
        ),
    ],
)
def test_operational_full_catalog_entrypoints_fail_closed_without_both_fuses(monkeypatch, module, argv) -> None:
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit, match="explicit_live_full_catalog_authorization_required"):
        module.main()
