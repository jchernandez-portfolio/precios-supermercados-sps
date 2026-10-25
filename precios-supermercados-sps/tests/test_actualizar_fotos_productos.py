from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from test_product_images import png, product  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "actualizar_fotos_productos", Path(__file__).resolve().parents[1] / "scripts" / "actualizar_fotos_productos.py"
)
fotos = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = fotos
SPEC.loader.exec_module(fotos)

WM = "https://walmarthn.vteximg.com.br/arquivos/ids/{}/a.jpg"
CO = "https://cdn.shopify.com/s/{}.jpg"


class Backend:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def query(self, sql, args=()):
        self.calls.append((sql, tuple(args)))
        return [row for row in self.rows if row[0] in args]


class Downloader:
    def __init__(self, files, max_requests=1000):
        self.files, self.requests, self.max_requests, self.urls = files, 0, max_requests, []

    def download(self, url):
        self.requests += 1
        self.urls.append(url)
        if url not in self.files:
            raise fotos.sources.ImageSourceError("http_404")
        return self.files[url]


def catalog_document():
    return {"rows": [
        {"row_id": "canonical-aa11", "offers": [{"source_product_id": "walmart:1"}, {"source_product_id": "colonial:2"}]},
        {"row_id": "source-bb22", "offers": [{"source_product_id": "colonial:3"}]},
        {"row_id": "source-cc33", "offers": [{"source_product_id": "bad"}]},
    ]}


def test_catalog_rows_and_source_refs() -> None:
    rows = fotos.parse_catalog_rows(catalog_document())
    assert [(row.key, row.offers) for row in rows] == [
        ("aa11", (("walmart", 1), ("colonial", 2))), ("bb22", (("colonial", 3),))]
    backend = Backend([(1, "walmart", "501", "501", "77", "7401"), (2, "colonial", "9001", "9001", None, "7401"),
                       (3, "colonial", "9002", None, None, None)])
    refs = fotos.fetch_source_refs(backend, [3, 1, 2, 1])
    assert refs[1].keys == ("501", "77") and refs[3].ean is None
    assert fotos.needed_vtex_ids(rows, refs, "walmart") == ["77", "501"]


def test_load_catalog_reads_only_catalog_partitions() -> None:
    seen = []

    def fetch(url):
        seen.append(url)
        if url.endswith("manifest.json"):
            return {"files": [{"path": "search.json"}, {"path": "catalog/p0.json"}]}
        return catalog_document()

    assert len(fotos.load_catalog(fetch, "https://x.test/")) == 2
    assert seen == ["https://x.test/manifest.json", "https://x.test/catalog/p0.json"]


def test_load_catalogs_merges_every_published_city() -> None:
    def fetch(url):
        if url.endswith("cities.json"):
            return {"cities": [{"manifest": "manifest.json"}, {"manifest": "cities/tegucigalpa/manifest.json"},
                               {"manifest": "../evil/manifest.json"}]}
        if url.endswith("manifest.json"):
            return {"files": [{"path": "catalog/p0.json"}]}
        if "tegucigalpa" in url:
            return {"rows": [{"row_id": "canonical-aa11", "offers": [{"source_product_id": "paiz:9"}]},
                             {"row_id": "source-dd44", "offers": [{"source_product_id": "paiz:10"}]}]}
        return catalog_document()

    rows = {row.key: row.offers for row in fotos.load_catalogs(fetch, "https://x.test/")}
    assert rows["aa11"] == (("walmart", 1), ("colonial", 2), ("paiz", 9))
    assert set(rows) == {"aa11", "bb22", "dd44"}


def setup():
    rows = fotos.parse_catalog_rows(catalog_document())
    refs = {1: fotos.SourceRef("walmart", ("501",), "7401"), 2: fotos.SourceRef("colonial", ("9001",), "7401"),
            3: fotos.SourceRef("colonial", ("9002",), None)}
    maps = {"walmart": {"501": [WM.format(1), WM.format(2)]}, "colonial": {"9001": [CO.format("a")], "9002": [CO.format("b")]}}
    candidates = {row.key: fotos.row_candidates(row, refs, maps) for row in rows}
    return rows, candidates


def test_candidates_signature_and_plan() -> None:
    rows, candidates = setup()
    assert [(c.supermarket_id, c.position, c.gtin_match) for c in candidates["aa11"]] == [
        ("walmart", 0, True), ("walmart", 1, True), ("colonial", 0, True)]
    assert candidates["bb22"][0].gtin_match is False
    assert fotos.plan(rows, candidates, {}, 10) == ["aa11", "bb22"]
    index = {"aa11": {"sig": fotos.signature(candidates["aa11"])}}
    assert fotos.plan(rows, candidates, index, 10) == ["bb22"]
    assert fotos.plan(rows, candidates, {}, 1) == ["aa11"]
    assert fotos.plan(rows, candidates, {}, 10, unavailable={"walmart"}) == ["bb22"]


def test_generic_urls_need_three_products() -> None:
    c = fotos.images.Candidate("walmart", 0, "u")
    assert fotos.generic_urls({"a": [c], "b": [c]}) == set()
    assert fotos.generic_urls({"a": [c], "b": [c], "d": [c]}) == {"u"}


def test_process_writes_normalized_photo_and_index(tmp_path: Path) -> None:
    rows, candidates = setup()
    good = png(product())
    files = {
        fotos.sources.download_url("walmart", WM.format(1)): png(product(size=250, box=(80, 30, 170, 220))),
        fotos.sources.download_url("walmart", WM.format(2)): good,
        fotos.sources.download_url("colonial", CO.format("a")): png(product(size=900)),
    }
    downloader = Downloader(files)
    index = {"aa11": {"sig": "old", "v": 3}}
    review: list = []
    stats = fotos.process(["aa11", "bb22"], candidates, downloader, tmp_path, index, log=lambda _: None,
                          review=review)
    assert stats["con_foto"] == 1 and stats["sin_foto"] == 1 and stats["error_descarga"] == 1
    entry = index["aa11"]
    assert entry["sm"] == "colonial" and len(entry["h"]) == 10 and entry["q"] in {"hd", "sd", "low"}
    assert "v" not in entry and entry["px"] > 0
    assert index["bb22"] == {"sig": fotos.signature(candidates["bb22"]), "none": 1}
    for size in (800, 320):
        output = Image.open(tmp_path / str(size) / "aa" / f"aa11-{entry['h']}.webp")
        assert output.format == "WEBP" and output.size == (size, size)
    assert [item["key"] for item in review] == ["aa11", "bb22"]
    assert review[0]["file"] == f"320/aa/aa11-{entry['h']}.webp" and review[1]["chosen"] is None
    page = fotos.write_review(tmp_path, review, fotos.PUBLIC_BASE, stats).read_text(encoding="utf-8")
    assert f"{fotos.PUBLIC_BASE}/fotos/v2/320/aa/aa11-{entry['h']}.webp" in page and "sin foto" in page
    assert "<script" not in page
    # La segunda foto de Walmart sólo se pidió porque la principal era pequeña.
    assert len([url for url in downloader.urls if "walmarthn" in url]) == 2
    path = fotos.write_index(tmp_path, index, stats)
    document = json.loads(path.read_text())
    assert document["schema"] == fotos.INDEX_SCHEMA and document["with_photo"] == 1
    assert fotos.parse_index(document) == index


def test_process_stops_cleanly_when_download_budget_runs_out(tmp_path: Path) -> None:
    _, candidates = setup()
    downloader = Downloader({}, max_requests=4)
    index: dict = {}
    stats = fotos.process(["aa11", "bb22"], candidates, downloader, tmp_path, index, log=lambda _: None)
    assert stats["filas_pospuestas"] == 2 and index == {}


def test_secondary_photo_is_skipped_when_main_is_good(tmp_path: Path) -> None:
    _, candidates = setup()
    files = {fotos.sources.download_url("walmart", WM.format(1)): png(product()),
             fotos.sources.download_url("colonial", CO.format("a")): png(product())}
    downloader = Downloader(files)
    fotos.process(["aa11"], candidates, downloader, tmp_path, {}, log=lambda _: None)
    assert fotos.sources.download_url("walmart", WM.format(2)) not in downloader.urls


def test_parse_index_ignores_other_schemas() -> None:
    assert fotos.parse_index(None) == {} and fotos.parse_index({"schema": "x", "items": {"a": {}}}) == {}


AUTHORIZATION = Path(__file__).resolve().parents[1] / ".automation" / "product-images-capture-authorization.json"


def test_registered_authorization_covers_default_run() -> None:
    document = fotos.load_authorization(AUTHORIZATION, api_delay=1.0, image_delay=0.15,
                                        max_products=fotos.DEFAULT_MAX_PRODUCTS, max_image_requests=20000)
    assert document["approved_by"] == "project_owner"


@pytest.mark.parametrize("change, code", [
    ({"live_read_only_authorized": False}, "revoked"),
    ({"schema": "x"}, "schema"),
    ({"max_products_per_run": 10}, "max_products"),
    ({"min_api_delay_seconds": 5}, "api_delay"),
    ({"allowed_api_origins": []}, "origins"),
])
def test_authorization_is_fail_closed(tmp_path: Path, change, code) -> None:
    document = json.loads(AUTHORIZATION.read_text(encoding="utf-8"))
    document.update(change)
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(fotos.PhotoUpdateError, match=code):
        fotos.load_authorization(path, api_delay=1.0, image_delay=0.15, max_products=4000, max_image_requests=12000)
