from __future__ import annotations

import json

import pytest

from precios_supermercados import product_image_sources as src

VTEX = "https://walmarthn.vteximg.com.br/arquivos/ids/412345/foto.jpg?v=1"


def test_vtex_resize_and_shopify_width() -> None:
    assert src.vtex_resized(VTEX) == "https://walmarthn.vteximg.com.br/arquivos/ids/412345-800-800/foto.jpg?v=1"
    assert src.vtex_resized(src.vtex_resized(VTEX, 300)).count("-800-800") == 1
    url = src.shopify_resized("https://cdn.shopify.com/s/files/1/a.jpg?v=9&width=100")
    assert url == "https://cdn.shopify.com/s/files/1/a.jpg?v=9&width=800"
    assert src.download_url("comisariato_los_andes", "x") == "x"


def test_parse_vtex_search_maps_items_and_drops_foreign_hosts() -> None:
    payload = [{"items": [
        {"itemId": "101", "images": [{"imageUrl": VTEX}, {"imageUrl": VTEX}, {"imageUrl": "https://evil.test/a.jpg"},
                                     {"imageUrl": VTEX.replace("412345", "412346")}, {"imageUrl": VTEX.replace("412345", "7")}]},
        {"itemId": "abc", "images": [{"imageUrl": VTEX}]},
    ]}]
    result = src.parse_vtex_search(payload)
    assert list(result) == ["101"]
    assert len(result["101"]) == src.MAX_IMAGES_PER_ITEM and result["101"][0] == VTEX
    with pytest.raises(src.ImageSourceError):
        src.parse_vtex_search({"error": 1})


def test_parse_shopify_prefers_variant_specific_image() -> None:
    payload = {"products": [{
        "images": [{"position": 1, "src": "https://cdn.shopify.com/a.jpg", "variant_ids": []},
                   {"position": 2, "src": "https://cdn.shopify.com/b.jpg", "variant_ids": [22]}],
        "variants": [{"id": 11}, {"id": 22}],
    }, {"images": [], "variants": [{"id": 33}]}]}
    result = src.parse_shopify_products(payload)
    assert result["11"] == ["https://cdn.shopify.com/a.jpg", "https://cdn.shopify.com/b.jpg"]
    assert result["22"][0] == "https://cdn.shopify.com/b.jpg"
    assert result["33"] == []


def test_parse_los_andes_orders_active_then_position() -> None:
    payload = {"totalItems": 250, "data": [{"code": "0001-000074123456789", "images": [
        {"fileName": "c.jpg", "active": "0", "position": 0},
        {"fileName": "/b.jpg", "active": "1", "position": 2},
        {"fileName": "a.jpg", "active": "1", "position": 1},
    ]}]}
    result, total = src.parse_los_andes_page(payload)
    assert total == 250
    assert [url.rsplit("/", 1)[1] for url in result["0001-000074123456789"]] == ["a.jpg", "b.jpg"]


def test_parse_pricesmart_maps_pid_and_variant_skus() -> None:
    thumb = "https://d31f1ehqijlcua.cloudfront.net/n/x.jpg"
    payload = {"response": {"numFound": 1, "docs": [
        {"pid": "123", "thumb_image": thumb, "variants": [{"skuid": "123-7401234567890"}]},
        {"pid": "999", "thumb_image": "http://insecure.test/x.jpg"},
    ]}}
    result, found = src.parse_pricesmart_docs(payload)
    assert found == 1 and result == {"123": [thumb], "123-7401234567890": [thumb]}


class FakeResponse:
    def __init__(self, status: int = 200, payload: object = None, body: bytes = b"") -> None:
        self.status_code, self.payload, self.body = status, payload, body

    def json(self) -> object:
        return self.payload

    def iter_content(self, size: int):
        for start in range(0, len(self.body), size):
            yield self.body[start:start + size]


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.headers: dict[str, str] = {}
        self.responses = responses
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method: str, url: str, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


def client(responses: list[FakeResponse], **kwargs) -> tuple[src.ImageSourceClient, FakeSession, list[float]]:
    session, sleeps = FakeSession(responses), []
    return src.ImageSourceClient(session, delay=1.0, sleep=sleeps.append, **kwargs), session, sleeps


def test_vtex_client_batches_sku_ids_and_waits_between_requests() -> None:
    ids = [str(n) for n in range(1, 52)]
    api, session, sleeps = client([FakeResponse(payload=[]), FakeResponse(payload=[{"items": [
        {"itemId": "51", "images": [{"imageUrl": VTEX}]}]}])])
    assert api.vtex("walmart", ids) == {"51": [VTEX]}
    assert len(session.calls) == 2 and session.calls[0][1].count("fq=skuId:") == 50
    assert session.calls[1][1].startswith("https://www.walmart.com.hn/api/catalog_system/pub/products/search?fq=skuId:51")
    assert sleeps == [1.0] and "PreciosSupermercadosSPS" in session.headers["User-Agent"]


def test_client_retries_transient_errors_and_records_vtex_failures() -> None:
    api, session, sleeps = client([FakeResponse(503), FakeResponse(payload=[])])
    assert api.vtex("la_colonia", ["5"]) == {}
    assert len(session.calls) == 2 and 10 in sleeps
    api, _, _ = client([FakeResponse(404)])
    assert api.vtex("la_colonia", ["5"]) == {} and api.errors == ["la_colonia:http_404"]


def test_los_andes_client_paginates_until_total() -> None:
    page = lambda code: {"totalItems": 150, "data": [{"code": code, "images": [{"fileName": "a.jpg", "active": "1"}]}]}
    api, session, _ = client([FakeResponse(payload=page("A")), FakeResponse(payload=page("B"))])
    assert set(api.los_andes()) == {"A", "B"}
    assert "skip=100" in session.calls[1][1] and json.loads(session.calls[0][2]["data"])["type"] == "PD"


def test_colonial_client_stops_on_empty_page() -> None:
    api, session, _ = client([FakeResponse(payload={"products": [{"images": [], "variants": [{"id": 1}]}]}),
                              FakeResponse(payload={"products": []})])
    assert api.colonial() == {"1": []}
    assert session.calls[1][1].endswith("page=2")


def test_download_enforces_host_and_size() -> None:
    api, _, _ = client([FakeResponse(body=b"x" * 10)])
    assert api.download(VTEX) == b"x" * 10
    with pytest.raises(src.ImageSourceError, match="host"):
        api.download("https://evil.test/a.jpg")
    api, _, _ = client([FakeResponse(body=b"x" * (src.MAX_IMAGE_BYTES + 1))])
    with pytest.raises(src.ImageSourceError, match="too_large"):
        api.download(VTEX)


def test_request_budget_is_enforced() -> None:
    api, _, _ = client([FakeResponse(body=b"1")], max_requests=1)
    api.download(VTEX)
    with pytest.raises(src.ImageSourceError, match="budget"):
        api.download(VTEX)
