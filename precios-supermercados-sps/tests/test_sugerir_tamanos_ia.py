"""Sugerencias de tamaño con IA de visión: fotos, validación, caché, cuota y llave."""
from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import sugerir_tamanos_ia as ia  # noqa: E402
from auditar_identidad_productos import CatalogRow  # noqa: E402

EL_MIGO = {
    "gtin": "00012656001065", "kind": "atipico", "name": "Maíz Dulce En Granos El Migo - 148 g",
    "offers": [
        {"store": "colonial", "ref": "colonial:1", "value": "8.5", "unit": "oz"},
        {"store": "la_colonia", "ref": "la_colonia:2", "value": "240", "unit": "g"},
        {"store": "walmart", "ref": "walmart:3", "value": "148", "unit": "g"},
    ],
}
KEY_A, KEY_B = "a" * 24, "b" * 24
PHOTOS = {"colonial:1": f"{ia.PHOTO_BASE}800/aa/{KEY_A}-1111111111.webp",
          "walmart:3": f"{ia.PHOTO_BASE}800/bb/{KEY_B}-2222222222.webp"}


def test_photo_urls_follow_catalog_cards_and_skip_cards_without_photo() -> None:
    cities = {"sps": [CatalogRow("sps", KEY_A, "x", [("colonial", 1)]), CatalogRow("sps", KEY_B, "y", [("walmart", 3)]),
                      CatalogRow("sps", "c" * 24, "z", [("la_colonia", 2)])]}
    index = {KEY_A: {"h": "1111111111"}, KEY_B: {"h": "2222222222", "de": KEY_A}, "c" * 24: {"none": 1}}
    assert ia.photo_urls(cities, index) == PHOTOS
    assert ia.item_photos(EL_MIGO, PHOTOS) == [PHOTOS["colonial:1"], PHOTOS["walmart:3"]]


def test_model_prefers_the_flash_alias_then_the_newest_numbered_flash() -> None:
    assert ia.resolve_model(["models/gemini-2.5-flash", "models/gemini-flash-latest"]) == "gemini-flash-latest"
    assert ia.resolve_model(["models/gemini-2.5-flash", "models/gemini-3.5-flash", "models/gemini-3.5-flash-lite",
                             "models/gemini-3.5-pro"]) == "gemini-3.5-flash"
    with pytest.raises(ia.SuggestionError):
        ia.resolve_model(["models/gemini-3.5-pro"])


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"legible": True, "same_product": True, "value": 240, "unit": "g", "label_text": "PESO NETO 240 g"},
         {"status": "sugerencia", "value": "240", "unit": "g", "text": "PESO NETO 240 g"}),
        # 8.5 oz se acepta como onza: coincide con la oferta de Colonial (y con 241 g en peso).
        ({"legible": True, "same_product": True, "value": 8.5, "unit": "oz"},
         {"status": "sugerencia", "value": "8.5", "unit": "oz", "text": None}),
        ({"legible": True, "same_product": True, "value": 0.24, "unit": "kg"},
         {"status": "sugerencia", "value": "240", "unit": "g", "text": None}),
        ({"legible": False, "same_product": True, "value": None, "unit": None},
         {"status": "no_legible", "reason": "ilegible"}),
        ({"legible": True, "same_product": False, "value": 240, "unit": "g", "label_text": "Elote 400 g"},
         {"status": "no_legible", "reason": "otra_presentacion", "text": "Elote 400 g"}),
        # Legible pero no coincide con ningún tamaño publicado: foto de otra presentación.
        ({"legible": True, "same_product": True, "value": 425, "unit": "g"},
         {"status": "no_legible", "reason": "otra_presentacion", "text": None}),
        ({"legible": True, "same_product": True, "value": -3, "unit": "g"},
         {"status": "no_legible", "reason": "ilegible", "text": None}),
        ({"legible": True, "same_product": True, "value": 240, "unit": "toneladas"},
         {"status": "no_legible", "reason": "ilegible", "text": None}),
        ("<script>", {"status": "no_legible", "reason": "respuesta_invalida"}),
        (None, {"status": "no_legible", "reason": "respuesta_invalida"}),
    ],
)
def test_model_answers_are_validated_before_becoming_a_suggestion(raw: object, expected: dict) -> None:
    assert ia.interpret(raw, EL_MIGO["offers"]) == expected


class FakeClient:
    def __init__(self, answers: list[object]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, int]] = []
        self.model = "gemini-flash-latest"

    def read_label(self, name: str, images: list[bytes]) -> object:
        self.calls.append((name, len(images)))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def queue(*items: dict) -> dict:
    return {"schema": "rpi-size-queue/v1", "items": [json.loads(json.dumps(item)) for item in items]}


def test_annotate_queries_new_items_reuses_the_cache_and_marks_items_without_photo() -> None:
    no_photo = {**EL_MIGO, "gtin": "07411000204139", "offers": [{"store": "paiz", "ref": "paiz:9", "value": "180", "unit": "g"}]}
    cache: dict = {}
    client = FakeClient([{"legible": True, "same_product": True, "value": 240, "unit": "g"}])
    document = queue(EL_MIGO, no_photo)
    stats = ia.annotate(document, PHOTOS, cache, client=client, fetch_image=lambda url: b"img", minutes=5, delay=0,
                        sleep=lambda _: None, now="T")
    assert client.calls == [("Maíz Dulce En Granos El Migo - 148 g", 2)]
    assert document["items"][0]["ia"]["status"] == "sugerencia" and document["items"][1]["ia"] == {"status": "sin_foto"}
    assert stats["consultas"] == 1 and stats["detenido_por"] is None
    assert cache[EL_MIGO["gtin"]]["key"] == ia.cache_key(list(PHOTOS.values()))

    # Segunda corrida sin llave: no consulta y aplica lo guardado.
    again = queue(EL_MIGO)
    stats = ia.annotate(again, PHOTOS, cache, client=None, fetch_image=lambda url: b"", minutes=5, delay=0)
    assert again["items"][0]["ia"]["value"] == "240" and stats["detenido_por"] == "sin_llave"

    # Si cambian las fotos, la respuesta guardada ya no vale: queda pendiente.
    changed = {**PHOTOS, "walmart:3": f"{ia.PHOTO_BASE}800/bb/{KEY_B}-3333333333.webp"}
    stale = queue(EL_MIGO)
    stats = ia.annotate(stale, changed, cache, client=None, fetch_image=lambda url: b"", minutes=5, delay=0)
    assert "ia" not in stale["items"][0] and stats["pendiente"] == 1


def test_annotate_stops_on_quota_and_keeps_what_it_already_read() -> None:
    second = {**EL_MIGO, "gtin": "00012656001066"}
    third = {**EL_MIGO, "gtin": "00012656001067"}
    client = FakeClient([{"legible": False, "same_product": True}, ia.QuotaExceeded("429")])
    cache: dict = {}
    document = queue(EL_MIGO, second, third)
    stats = ia.annotate(document, PHOTOS, cache, client=client, fetch_image=lambda url: b"", minutes=5, delay=0,
                        sleep=lambda _: None)
    assert stats["detenido_por"] == "cuota_agotada" and stats["consultas"] == 1 and stats["pendiente"] == 2
    assert len(client.calls) == 2  # la tercera ya no se intenta
    assert document["items"][0]["ia"] == {"status": "no_legible", "reason": "ilegible"}
    assert list(cache) == [EL_MIGO["gtin"]]


def test_annotate_respects_the_time_budget() -> None:
    ticks = iter([0, 0, 120, 120, 120])
    client = FakeClient([{"legible": False, "same_product": True}] * 3)
    document = queue(EL_MIGO, {**EL_MIGO, "gtin": "00012656001066"})
    stats = ia.annotate(document, PHOTOS, {}, client=client, fetch_image=lambda url: b"", minutes=1, delay=0,
                        sleep=lambda _: None, clock=lambda: next(ticks))
    assert len(client.calls) == 1 and stats["detenido_por"] == "tiempo_agotado"


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_gemini_sends_the_key_only_in_the_header_and_asks_for_json() -> None:
    requests: list[object] = []

    def opener(request, timeout):
        requests.append(request)
        if request.full_url.endswith("models?pageSize=1000"):
            return Response(json.dumps({"models": [
                {"name": "models/gemini-flash-latest", "supportedGenerationMethods": ["generateContent"]}]}).encode())
        answer = {"legible": True, "same_product": True, "value": 240, "unit": "g"}
        return Response(json.dumps({"candidates": [{"content": {"parts": [{"text": json.dumps(answer)}]}}]}).encode())

    client = ia.Gemini("clave-de-prueba", opener=opener)
    assert client.read_label('Maíz "El Migo"', [b"RIFF0000WEBP"])["value"] == 240
    listing, generate = requests
    assert generate.full_url == f"{ia.API_BASE}models/gemini-flash-latest:generateContent"
    for request in requests:
        assert "clave-de-prueba" not in request.full_url
        assert request.get_header("X-goog-api-key") == "clave-de-prueba"
    body = json.loads(generate.data)
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["generationConfig"]["temperature"] == 0
    parts = body["contents"][0]["parts"]
    assert parts[1]["inline_data"]["mime_type"] == "image/webp" and "Maíz 'El Migo'" in parts[0]["text"]


def test_gemini_quota_and_auth_errors_stop_the_run() -> None:
    def opener(code):
        def fail(request, timeout):
            raise urllib.error.HTTPError(request.full_url, code, "x", {}, io.BytesIO(b""))
        return fail
    with pytest.raises(ia.QuotaExceeded):
        ia.Gemini("k", opener=opener(429)).pick_model()
    with pytest.raises(ia.SuggestionError, match="gemini_http_403"):
        ia.Gemini("k", opener=opener(403)).pick_model()


def test_cache_file_is_read_defensively(tmp_path: Path) -> None:
    path = tmp_path / ia.CACHE_FILE
    assert ia.load_cache(path) == {}
    path.write_text("no es json")
    assert ia.load_cache(path) == {}
    path.write_text(json.dumps({"schema": ia.CACHE_SCHEMA, "entries": {"0001": {"key": "k"}, "x": 3}}))
    assert ia.load_cache(path) == {"0001": {"key": "k"}}
