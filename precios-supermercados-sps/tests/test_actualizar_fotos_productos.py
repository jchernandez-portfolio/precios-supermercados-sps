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
            return {"rows": [{"row_id": "canonical-aa11", "offers": [{"source_product_id": "paiz:9@paiz_tgu_proceres"},
                                                                      {"source_product_id": "paiz:9@paiz_tgu_multiplaza"}]},
                             {"row_id": "source-dd44", "offers": [{"source_product_id": "paiz:10@paiz_tgu_proceres"}]}]}
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
    # Criterios nuevos: sólo se reevalúan las fotos elegidas con puntaje bajo bajo criterios viejos.
    sig = fotos.signature(candidates["aa11"])
    assert fotos.plan(rows, candidates, {"aa11": {"sig": sig, "h": "0123456789", "s": 58}}, 10) == ["aa11", "bb22"]
    assert fotos.plan(rows, candidates, {"aa11": {"sig": sig, "h": "0123456789", "s": 92}}, 10) == ["bb22"]
    assert fotos.plan(rows, candidates, {"aa11": {"sig": sig, "h": "0123456789", "s": 58, "c": fotos.CRITERIA_VERSION}}, 10) == ["bb22"]
    # Sin foto con criterios anteriores también se reevalúa una vez (la "genérica" de antes).
    assert fotos.plan(rows, candidates, {"aa11": {"sig": sig, "none": 1}}, 10) == ["aa11", "bb22"]
    assert fotos.plan(rows, candidates, {"aa11": {"sig": sig, "none": 1, "c": 3}}, 10) == ["aa11", "bb22"]
    assert fotos.plan(rows, candidates, {"aa11": {"sig": sig, "none": 1, "c": fotos.CRITERIA_VERSION}}, 10) == ["bb22"]


def test_generic_urls_need_four_products() -> None:
    c = fotos.images.Candidate("walmart", 0, "u")
    assert fotos.generic_urls({"a": [c], "b": [c], "d": [c]}) == set()
    assert fotos.generic_urls({"a": [c], "b": [c], "d": [c], "e": [c]}) == {"u"}


def test_prune_generic_uses_the_whole_index(tmp_path: Path) -> None:
    """Un "sin imagen" que llega de a pocos por corrida se detecta con el índice completo."""
    index = {key: {"sig": "s" + key[1:], "h": "aaaaaaaaaa", "sm": "walmart", "s": 90, "q": "hd", "c": 3} for key in ("k1", "k2", "k3")}
    index["k4"] = {"sig": "s4", "h": "bbbbbbbbbb", "sm": "walmart", "s": 90, "q": "hd", "c": 3}
    index["k5"] = {"sig": "s5", "none": 1, "c": 3}
    assert fotos.prune_generic(index, tmp_path) == 0  # el mismo artículo en 3 filas sigue con foto
    index["k6"] = {"sig": "s6", "h": "aaaaaaaaaa", "sm": "paiz", "s": 80, "q": "hd", "c": 4}
    files = [tmp_path / relative for relative in fotos.image_paths("k6", "aaaaaaaaaa").values()]
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    assert fotos.prune_generic(index, tmp_path) == 4
    assert all(index[key] == {"sig": "s" + key[1:] if key != "k6" else "s6", "none": 1, "c": fotos.CRITERIA_VERSION, "gen": 1}
               for key in ("k1", "k2", "k3", "k6"))
    assert index["k4"]["h"] == "bbbbbbbbbb" and not any(path.exists() for path in files)


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
    assert index["bb22"] == {"sig": fotos.signature(candidates["bb22"]), "none": 1, "c": fotos.CRITERIA_VERSION}
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


def test_process_stops_cleanly_when_time_runs_out(tmp_path: Path) -> None:
    """Pasado el plazo no se empiezan filas nuevas; lo hecho queda en el índice y el resto se pospone."""
    _, candidates = setup()
    downloader = Downloader({})
    index: dict = {}
    ticks = iter([0.0, 999.0])
    stats = fotos.process(["aa11", "bb22"], candidates, downloader, tmp_path, index, log=lambda _: None,
                          deadline=100.0, clock=lambda: next(ticks))
    assert stats["filas_pospuestas"] == 1 and stats["tiempo_agotado"] == 1
    assert set(index) == {"aa11"}


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
                                        max_products=fotos.DEFAULT_MAX_PRODUCTS, max_image_requests=30000)
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


# ------------------------------------------- Tegucigalpa: una tarjeta por producto

def _key(text: str) -> str:
    return fotos.card_key(text)


def _webp(tag: bytes) -> bytes:
    return b"RIFF\x00\x00\x00\x00WEBP" + tag


def test_branch_photo_moves_before_and_after_tegucigalpa_merges_branches() -> None:
    ffaa, sauce, merged = _key("walmart:51@walmart_tgu_ffaa"), _key("walmart:51@walmart_tgu_el_sauce"), _key("walmart:51")
    index = {ffaa: {"h": "a" * 10, "s": 80, "sig": "x"}, sauce: {"h": "b" * 10, "s": 95, "sig": "x"}}
    before = fotos.parse_catalog_rows({"rows": [
        {"row_id": f"source-{ffaa}", "offers": [{"source_product_id": "walmart:51@walmart_tgu_ffaa"}]},
        {"row_id": f"source-{sauce}", "offers": [{"source_product_id": "walmart:51@walmart_tgu_el_sauce"}]},
        {"row_id": "product-cc", "offers": [{"source_product_id": "paiz:7@paiz_tgu_proceres"}]},
    ]})
    after = fotos.parse_catalog_rows({"rows": [
        {"row_id": f"source-{merged}", "offers": [{"source_product_id": "walmart:51@walmart_tgu_ffaa"},
                                                  {"source_product_id": "walmart:51@walmart_tgu_el_sauce"}]},
    ]})
    # Antes y después del cambio se hereda la misma foto: la de mejor puntaje.
    assert fotos.branch_photo_moves(before, index) == [(merged, sauce)]
    assert fotos.branch_photo_moves(after, index) == [(merged, sauce)]
    # Criterios más nuevos antes que puntaje: una foto de criterios v3 con más puntaje no gana.
    newer = {**index, ffaa: {**index[ffaa], "c": 4}, sauce: {**index[sauce], "c": 3}}
    assert fotos.branch_photo_moves(after, newer) == [(merged, ffaa)]
    # Con foto propia (p. ej. la misma llave en SPS) no se toca; las filas comparables tampoco.
    assert fotos.branch_photo_moves(after, {**index, merged: {"h": "c" * 10}}) == []
    assert after[0].branch_ids and after[0].source_row and not before[2].source_row


def test_adopt_branch_photos_copies_published_files_and_checks_the_hash(tmp_path: Path) -> None:
    import hashlib

    donor, target, bad_donor, bad_target = "ab" + "1" * 22, "cd" + "2" * 22, "ef" + "3" * 22, "gh" + "4" * 22
    big = _webp(b"800")
    digest = hashlib.sha256(big).hexdigest()[:10]
    index = {donor: {"h": digest, "s": 90, "sig": "x", "c": 4}, bad_donor: {"h": "f" * 10, "s": 90}}
    served = {f"https://r2.test/fotos/v2/800/ab/{donor}-{digest}.webp": big,
              f"https://r2.test/fotos/v2/320/ab/{donor}-{digest}.webp": _webp(b"320"),
              f"https://r2.test/fotos/v2/800/ef/{bad_donor}-{'f' * 10}.webp": _webp(b"otra"),
              f"https://r2.test/fotos/v2/320/ef/{bad_donor}-{'f' * 10}.webp": _webp(b"otra")}
    missing_target, missing_donor = "ij" + "5" * 22, "kl" + "6" * 22
    index[missing_donor] = {"h": "e" * 10, "s": 50}
    stats = fotos.adopt_branch_photos([(target, donor), (bad_target, bad_donor), (missing_target, missing_donor)],
                                      index, tmp_path, served.__getitem__, "https://r2.test", log=lambda _: None,
                                      workers=3)
    assert stats == {"fotos_heredadas_de_sucursal": 1, "herencia_huella_distinta": 1, "herencia_error_descarga": 1}
    assert index[target] == {"h": digest, "s": 90, "sig": "x", "c": 4, "de": donor}
    assert bad_target not in index
    assert (tmp_path / f"800/cd/{target}-{digest}.webp").read_bytes() == big
    assert (tmp_path / f"320/cd/{target}-{digest}.webp").read_bytes() == _webp(b"320")


def test_inherited_photos_do_not_count_as_generic_owners(tmp_path: Path) -> None:
    index = {"k1": {"h": "a" * 10}, "k2": {"h": "a" * 10}, "k3": {"h": "a" * 10},
             "n1": {"h": "a" * 10, "de": "k1"}, "n2": {"h": "a" * 10, "de": "k2"}}
    assert fotos.prune_generic(index, tmp_path) == 0  # 3 dueños reales (< 4)
    index["k4"] = {"h": "a" * 10}
    assert fotos.prune_generic(index, tmp_path) == 6  # genérica: caen también las heredadas
    assert all(item.get("gen") for item in index.values())
