"""IA de visión: lectura por foto, auto-resolución, caché, cuota, modelos y llave."""
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

A, B, C = ("a" * 24) + "-1111111111", ("b" * 24) + "-2222222222", ("c" * 24) + "-3333333333"


def offer(store: str, value: str | None, unit: str | None, foto: str | None) -> dict:
    return {"store": store, "name": f"{store} producto", "ref": f"{store}:1", "value": value, "unit": unit,
            "foto": foto}


def mazola() -> dict:
    # Walmart/Paiz publican 1 kg; Colonial/La Colonia 908 g. Cada súper con su foto.
    return {"gtin": "00750894620685", "kind": "empate", "name": "Mazola manteca 1 kilo", "offers": [
        offer("colonial", "908", "g", A), offer("la_colonia", "908", "g", A),
        offer("paiz", "1000", "g", B), offer("walmart", "1000", "g", B)]}


def goya() -> dict:
    # 3 súper publican 116 g y 2 publican 64 g: la mayoría es 116 g.
    return {"gtin": "00041331013734", "kind": "empate", "name": "GOYA Alcaparras 116g", "offers": [
        offer("colonial", "116", "g", A), offer("comisariato_los_andes", "116", "g", A),
        offer("la_colonia", "116", "g", A), offer("paiz", "64", "g", B), offer("walmart", "64", "g", B)]}


def reading(photo: int, value: float | None, unit: str | None = "g", legible: bool = True, same: bool = True,
            text: str | None = None) -> dict:
    return {"photo": photo, "legible": legible, "same_product": same, "value": value, "unit": unit, "label_text": text}


# ------------------------------------------------------------------ decisión


def test_photo_reading_matching_the_majority_is_resolved_automatically() -> None:
    item = goya()
    readings = ia.readings_by_photo({"readings": [reading(1, 116, text="Peso Neto: 116 g"), reading(2, 116)]},
                                    ia.item_photos(item))
    result = ia.resolve_item(item, readings, auto_allowed=True)
    assert result == {"status": "auto", "value": "116", "unit": "g", "text": "Peso Neto: 116 g"}
    by_store = {o["store"]: o["ia"] for o in item["offers"]}
    assert by_store["colonial"]["coincide"] is True and by_store["walmart"]["coincide"] is False  # 64 g ≠ foto
    assert ia.resolve_item(goya(), readings, auto_allowed=False)["status"] == "sugerencia"  # Flash-Lite: sólo sugiere


def test_mazola_tie_and_photos_that_disagree_go_to_manual_review() -> None:
    # Las dos fotos dicen 1 kg, pero 1 kg es 2 de 4: no hay mayoría → conflicto.
    item = mazola()
    both_kilo = ia.readings_by_photo({"readings": [reading(1, 1, "kg", text="CONT. NETO 1 KILO"), reading(2, 1000)]},
                                     ia.item_photos(item))
    result = ia.resolve_item(item, both_kilo, auto_allowed=True)
    assert result["status"] == "conflicto" and result["reason"] == "minoria" and result["value"] == "1000"
    colonial = next(o for o in item["offers"] if o["store"] == "colonial")
    assert colonial["ia"]["value"] == "1000" and colonial["ia"]["coincide"] is False  # foto 1 kg, título 908 g ⚠
    # Cada foto dice lo que dice su título → las fotos no coinciden entre sí → conflicto.
    split = ia.readings_by_photo({"readings": [reading(1, 908), reading(2, 1000)]}, ia.item_photos(mazola()))
    assert ia.resolve_item(mazola(), split, auto_allowed=True)["reason"] == "fotos_distintas"


def test_reading_that_matches_no_title_is_a_discrepancy_and_unreadable_photos_stay_manual() -> None:
    names = ia.item_photos(goya())
    odd = ia.readings_by_photo({"readings": [reading(1, 380, text="NET WT 12 OZ (380 g)")]}, names)
    assert ia.resolve_item(goya(), odd, auto_allowed=True)["status"] == "discrepancia"
    blank = ia.readings_by_photo({"readings": [reading(1, None, None, legible=False)]}, names)
    assert ia.resolve_item(goya(), blank, auto_allowed=True) == {"status": "no_legible", "reason": "ilegible"}
    other = ia.readings_by_photo({"readings": [reading(1, 116, same=False), reading(2, 64, same=False)]}, names)
    assert ia.resolve_item(goya(), other, auto_allowed=True)["reason"] == "otro_producto"
    assert ia.resolve_item({**goya(), "offers": [offer("paiz", "64", "g", None)]}, {}, auto_allowed=True) == {
        "status": "sin_foto"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (reading(1, 0.24, "kg"), {"status": "leido", "value": "240", "unit": "g", "text": None}),
        (reading(1, 8.5, "fl_oz"), {"status": "leido", "value": "8.5", "unit": "oz", "text": None}),
        (reading(1, 2, "lb"), {"status": "leido", "value": "907.18", "unit": "g", "text": None}),
        (reading(1, -3), {"status": "no_legible", "reason": "ilegible", "text": None}),
        (reading(1, 240, "toneladas"), {"status": "no_legible", "reason": "ilegible", "text": None}),
        ("<script>", {"status": "no_legible", "reason": "respuesta_invalida"}),
    ],
)
def test_each_photo_reading_is_validated(raw: object, expected: dict) -> None:
    assert ia.parse_reading(raw) == expected


def test_readings_ignore_unknown_photo_numbers() -> None:
    names = [A, B]
    result = ia.readings_by_photo({"readings": [reading(7, 100), reading(True, 100), reading(2, 100)]}, names)
    assert result[A] == {"status": "no_legible", "reason": "sin_respuesta"} and result[B]["status"] == "leido"
    assert ia.readings_by_photo(None, names)[B]["reason"] == "sin_respuesta"


def test_prompt_labels_photos_by_store_without_titles_or_sizes() -> None:
    lines = ia.photo_lines(mazola(), ia.item_photos(mazola()))
    assert lines == "Foto 1 = Colonial, La Colonia\nFoto 2 = Paiz, Walmart"
    assert "908" not in lines and "producto" not in lines


# ------------------------------------------------------------------ corrida


class FakeClient:
    def __init__(self, answers: list[object], model: str = "gemini-3.5-flash") -> None:
        self.answers = answers
        self.calls: list[tuple[str, str, int]] = []
        self.model = model
        self.retries = 0
        self.last_error = None

    def read_labels(self, name: str, photos: str, images: list[bytes]) -> object:
        self.calls.append((name, photos, len(images)))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def queue(*items: dict) -> dict:
    return {"schema": "rpi-size-queue/v1", "items": [json.loads(json.dumps(item)) for item in items]}


def test_annotate_moves_auto_items_out_of_the_queue_and_reuses_the_cache() -> None:
    cache: dict = {}
    client = FakeClient([{"readings": [reading(1, 116), reading(2, 116)]},
                         {"readings": [reading(1, 1000), reading(2, 1000)]}])
    document = queue(goya(), mazola())
    stats, resolved = ia.annotate(document, cache, client=client, fetch_image=lambda url: b"img", minutes=5,
                                  delay=0, sleep=lambda _: None, now="T")
    assert [item["gtin"] for item in resolved] == ["00041331013734"]
    assert [item["gtin"] for item in document["items"]] == ["00750894620685"]
    assert stats["auto"] == 1 and stats["conflicto"] == 1 and stats["consultas"] == 2
    assert client.calls[0][2] == 2 and cache["00041331013734"]["model"] == "gemini-3.5-flash"
    decision = ia.auto_decision(resolved[0])
    assert decision["decision"] == "size" and (decision["value"], decision["unit"]) == ("116", "g")

    # Sin llave: no consulta y reaplica lo guardado.
    again = queue(goya())
    stats, resolved = ia.annotate(again, cache, client=None, fetch_image=lambda url: b"", minutes=5, delay=0)
    assert [item["gtin"] for item in resolved] == ["00041331013734"] and stats["detenido_por"] == "sin_llave"


def test_lite_model_readings_never_resolve_alone() -> None:
    client = FakeClient([{"readings": [reading(1, 116), reading(2, 116)]}], model="gemini-flash-lite-latest")
    document = queue(goya())
    stats, resolved = ia.annotate(document, {}, client=client, fetch_image=lambda url: b"", minutes=5, delay=0)
    assert resolved == [] and document["items"][0]["ia"]["status"] == "sugerencia"


def test_annotate_stops_on_quota_and_on_a_dead_api() -> None:
    items = [{**goya(), "gtin": f"0004133101{n:04d}"} for n in range(8)]
    client = FakeClient([{"readings": [reading(1, None, None, legible=False)]}, ia.QuotaExceeded("429")])
    stats, _ = ia.annotate(queue(*items[:3]), {}, client=client, fetch_image=lambda url: b"", minutes=5, delay=0,
                           sleep=lambda _: None)
    assert stats["detenido_por"] == "cuota_agotada" and stats["pendiente"] == 2 and len(client.calls) == 2
    dead = FakeClient([ia.TemporarilyUnavailable("gemini_http_503")] * 8)
    stats, _ = ia.annotate(queue(*items), {}, client=dead, fetch_image=lambda url: b"", minutes=5, delay=0,
                           sleep=lambda _: None)
    assert stats["no_disponible"] == 5 and stats["detenido_por"] == "no_disponible:gemini_http_503"


def test_history_keeps_only_what_is_still_decided_by_the_engine() -> None:
    cache = {"1": {"auto_item": {"gtin": "1", "resuelto_utc": "2026-11-12"}},
             "2": {"auto_item": {"gtin": "2", "resuelto_utc": "2026-11-11"}}}
    history = ia.merge_history([{"gtin": "3"}], cache, engine_decided=["2", "3"], now="2026-11-13")
    assert [item["gtin"] for item in history] == ["3", "2"]  # "1" ya lo cambió una persona


# -------------------------------------------------------------------- modelo


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def models_response(*names: str) -> Response:
    return Response(json.dumps({"models": [
        {"name": f"models/{name}", "supportedGenerationMethods": ["generateContent"]} for name in names]}).encode())


def http_error(request, code: int, message: str = "x"):
    body = json.dumps({"error": {"code": code, "message": message}}).encode()
    return urllib.error.HTTPError(request.full_url, code, "x", {}, io.BytesIO(body))


def test_candidates_put_the_alias_first_then_numbered_flash_then_lite() -> None:
    models = ["models/gemini-3.5-flash-lite", "models/gemini-flash-latest", "models/gemini-2.5-flash",
              "models/gemini-3.5-flash", "models/gemini-flash-lite-latest", "models/gemini-3.5-pro"]
    assert ia.model_candidates(models) == ["gemini-flash-latest", "gemini-3.5-flash", "gemini-2.5-flash",
                                           "gemini-flash-lite-latest", "gemini-3.5-flash-lite"]
    assert ia.is_lite("gemini-flash-lite-latest") and not ia.is_lite("gemini-3.5-flash")


def test_key_only_in_header_photos_inline_and_json_requested() -> None:
    requests: list[object] = []

    def opener(request, timeout):
        requests.append(request)
        if request.full_url.endswith("models?pageSize=1000"):
            return models_response("gemini-flash-latest")
        answer = {"readings": [reading(1, 240)]}
        return Response(json.dumps({"candidates": [{"content": {"parts": [{"text": json.dumps(answer)}]}}]}).encode())

    client = ia.Gemini("clave-de-prueba", opener=opener)
    assert client.read_labels('Maíz "El Migo"', "Foto 1 = Paiz", [b"RIFF0000WEBP"])["readings"][0]["value"] == 240
    for request in requests:
        assert "clave-de-prueba" not in request.full_url
        assert request.get_header("X-goog-api-key") == "clave-de-prueba"
    body = json.loads(requests[1].data)
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    parts = body["contents"][0]["parts"]
    assert "Maíz 'El Migo'" in parts[0]["text"] and "Foto 1 = Paiz" in parts[0]["text"]
    assert parts[1] == {"text": "Foto 1:"} and parts[2]["inline_data"]["mime_type"] == "image/webp"


def test_quota_waits_a_minute_once_then_falls_back_to_the_next_model() -> None:
    used: list[str] = []
    waits: list[float] = []

    def opener(request, timeout):
        if request.full_url.endswith("models?pageSize=1000"):
            return models_response("gemini-3.5-flash", "gemini-flash-lite-latest")
        model = request.full_url.rsplit("/", 1)[-1].split(":")[0]
        used.append(model)
        if model == "gemini-3.5-flash":
            raise http_error(request, 429, "You exceeded your current quota")
        return Response(json.dumps({"candidates": [{"content": {"parts": [{"text": "{\"readings\": []}"}]}}]}).encode())

    client = ia.Gemini("k", opener=opener, sleep=waits.append)
    assert client.read_labels("x", "Foto 1 = Paiz", [b"img"]) == {"readings": []}
    assert used == ["gemini-3.5-flash", "gemini-3.5-flash", "gemini-flash-lite-latest"] and waits == [60]
    assert client.model == "gemini-flash-lite-latest" and client.last_error.startswith("429")


def test_overloaded_model_is_retried_then_replaced() -> None:
    used: list[str] = []
    waits: list[float] = []

    def opener(request, timeout):
        if request.full_url.endswith("models?pageSize=1000"):
            return models_response("gemini-flash-latest", "gemini-3.5-flash")
        model = request.full_url.rsplit("/", 1)[-1].split(":")[0]
        used.append(model)
        if model == "gemini-flash-latest":
            raise http_error(request, 503, "The model is overloaded.")
        return Response(json.dumps({"candidates": [{"content": {"parts": [{"text": "{\"readings\": []}"}]}}]}).encode())

    client = ia.Gemini("k", opener=opener, sleep=waits.append)
    client.read_labels("x", "Foto 1 = Paiz", [b"img"])
    assert used == ["gemini-flash-latest"] * 3 + ["gemini-3.5-flash"] and waits == [15, 45]
    assert client.last_error == "503: The model is overloaded."


def test_bad_key_stops_without_retries() -> None:
    client = ia.Gemini("k", opener=lambda request, timeout: (_ for _ in ()).throw(http_error(request, 403)),
                       sleep=lambda _: pytest.fail("no se reintenta un 403"))
    with pytest.raises(ia.SuggestionError, match="gemini_http_403"):
        client.pick_model()


def test_cache_file_is_read_defensively(tmp_path: Path) -> None:
    path = tmp_path / ia.CACHE_FILE
    assert ia.load_cache(path) == {}
    path.write_text("no es json")
    assert ia.load_cache(path) == {}
    path.write_text(json.dumps({"schema": "rpi-size-ai/v1", "entries": {"0001": {"key": "k"}}}))
    assert ia.load_cache(path) == {}  # versión anterior del prompt: se vuelve a leer
    path.write_text(json.dumps({"schema": ia.CACHE_SCHEMA, "entries": {"0001": {"key": "k"}, "x": 3}}))
    assert ia.load_cache(path) == {"0001": {"key": "k"}}


# ----------------------------------------------------- Gemini: modelo retirado


def test_gemini_model_no_longer_available_moves_to_the_next_candidate() -> None:
    used: list[str] = []

    def opener(request, timeout):
        if request.full_url.endswith("models?pageSize=1000"):
            return models_response("gemini-2.5-flash", "gemini-2.0-flash")
        model = request.full_url.rsplit("/", 1)[-1].split(":")[0]
        used.append(model)
        if model == "gemini-2.5-flash":
            raise http_error(request, 404, "models/gemini-2.5-flash is no longer available to new users")
        return Response(json.dumps({"candidates": [{"content": {"parts": [{"text": "{\"readings\": []}"}]}}],
                                    "usageMetadata": {"promptTokenCount": 900, "candidatesTokenCount": 40}}).encode())

    client = ia.Gemini("k", opener=opener, sleep=lambda _: pytest.fail("un 404 no se reintenta"))
    assert client.read_labels("x", "Foto 1 = Paiz", [b"img"]) == {"readings": []}
    assert used == ["gemini-2.5-flash", "gemini-2.0-flash"] and client.model == "gemini-2.0-flash"
    assert client.usage == {"entrada": 900, "salida": 40}


# ------------------------------------------------------------------- Claude


def claude_models(*ids: str, vision: bool = True) -> Response:
    return Response(json.dumps({"data": [
        {"id": model, "capabilities": {"image_input": {"supported": vision}}} for model in ids]}).encode())


def claude_answer(readings: list[dict], stop: str = "end_turn") -> Response:
    return Response(json.dumps({"stop_reason": stop, "usage": {"input_tokens": 1500, "output_tokens": 60},
                                "content": [{"type": "text", "text": json.dumps({"readings": readings})}]}).encode())


def claude_opener(fail: dict[int, object] | None = None, models=("claude-sonnet-5-5", "claude-haiku-5-5")):
    """Responde el listado y, en cada mensaje, el error de ``fail[n]`` o una lectura de 240 g."""
    calls: list[object] = []

    def opener(request, timeout):
        calls.append(request)
        if request.full_url.endswith("models?limit=1000"):
            return claude_models(*models)
        number = sum(1 for call in calls if call.full_url.endswith("/messages"))
        error = (fail or {}).get(number)
        if error is not None:
            raise http_error(request, *error) if isinstance(error, tuple) else error
        return claude_answer([reading(1, 240, text="PESO NETO 240 g")])

    return opener, calls


def test_claude_sends_the_key_only_in_headers_images_as_webp_and_a_strict_schema() -> None:
    opener, calls = claude_opener()
    client = ia.Claude("sk-ant-prueba", opener=opener)
    answer = client.read_labels('Maíz "El Migo"', "Foto 1 = Paiz", [b"RIFF0000WEBP"])
    assert answer["readings"][0]["value"] == 240
    for request in calls:
        assert "sk-ant-prueba" not in request.full_url
        assert request.get_header("X-api-key") == "sk-ant-prueba"
        assert request.get_header("Anthropic-version") == ia.CLAUDE_VERSION
    body = json.loads(calls[-1].data)
    assert "sk-ant-prueba" not in calls[-1].data.decode()
    assert body["model"] == "claude-sonnet-5-5"  # por defecto: mejor lectura de etiquetas
    assert body["output_config"]["format"] == {"type": "json_schema", "schema": ia.CLAUDE_SCHEMA}
    content = body["messages"][0]["content"]
    assert "Maíz 'El Migo'" in content[0]["text"] and content[1] == {"type": "text", "text": "Foto 1:"}
    assert content[2]["source"]["media_type"] == "image/webp" and content[2]["source"]["type"] == "base64"
    assert client.usage == {"entrada": 1500, "salida": 60} and client.queries == 1


def test_claude_falls_back_to_the_newest_sonnet_then_haiku_when_the_model_is_missing() -> None:
    opener, _ = claude_opener(models=("claude-opus-5-5", "claude-haiku-6", "claude-haiku-5-5"))
    assert ia.Claude("k", model="claude-sonnet-9", opener=opener).pick_model() == "claude-haiku-6"
    opener, _ = claude_opener(models=("claude-haiku-6", "claude-sonnet-6", "claude-sonnet-5-5"))
    assert ia.Claude("k", model="claude-sonnet-9", opener=opener).pick_model() == "claude-sonnet-6"
    opener, _ = claude_opener(models=("claude-sonnet-5-5", "claude-haiku-5-5"))
    assert ia.Claude("k", model="claude-haiku-5-5", opener=opener).pick_model() == "claude-haiku-5-5"
    opener, _ = claude_opener(models=("claude-haiku-5-5",))
    with pytest.raises(ia.ProviderFailed, match="claude_modelo_no_disponible"):
        ia.Claude("k", opener=lambda r, timeout: claude_models("claude-haiku-5-5", vision=False)).pick_model()


def test_claude_refusal_or_bad_json_leaves_the_photo_unread() -> None:
    def opener(request, timeout):
        if request.full_url.endswith("models?limit=1000"):
            return claude_models("claude-sonnet-5-5")
        return claude_answer([], stop="refusal")

    assert ia.Claude("k", opener=opener).read_labels("x", "Foto 1 = Paiz", [b"img"]) is None


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        ((400, "Your credit balance is too low to access the Anthropic API."), "claude_sin_credito"),
        ((402, "billing"), "claude_sin_credito"),
        ((401, "invalid x-api-key"), "claude_llave_invalida_401"),
        ((404, "model: claude-haiku-5-5"), "claude_modelo_no_disponible"),
    ],
)
def test_claude_fatal_errors_fail_fast_without_retries(error: tuple, reason: str) -> None:
    opener, _ = claude_opener({1: error})
    client = ia.Claude("k", opener=opener, sleep=lambda _: pytest.fail("no se reintenta"))
    with pytest.raises(ia.ProviderFailed, match=reason):
        client.read_labels("x", "Foto 1 = Paiz", [b"img"])
    assert "k" not in (client.last_error or "").split()


def test_claude_overloaded_529_is_retried_with_waits_before_giving_up() -> None:
    waits: list[float] = []
    opener, calls = claude_opener({1: (529, "Overloaded"), 2: (529, "Overloaded")})
    client = ia.Claude("k", opener=opener, sleep=waits.append)
    assert client.read_labels("x", "Foto 1 = Paiz", [b"img"])["readings"][0]["value"] == 240
    assert waits == [15, 45] and client.retries == 2
    opener, _ = claude_opener({n: (529, "Overloaded") for n in range(1, 4)})
    with pytest.raises(ia.ProviderFailed, match="claude_saturado_529"):
        ia.Claude("k", opener=opener, sleep=lambda _: None).read_labels("x", "Foto 1 = Paiz", [b"img"])


def test_claude_429_waits_once_then_gives_up() -> None:
    waits: list[float] = []
    opener, _ = claude_opener({1: (429, "rate"), 2: (429, "rate")})
    with pytest.raises(ia.ProviderFailed, match="claude_limite_429"):
        ia.Claude("k", opener=opener, sleep=waits.append).read_labels("x", "Foto 1 = Paiz", [b"img"])
    assert waits == [60]


def test_claude_query_cap_stops_before_spending() -> None:
    opener, calls = claude_opener()
    client = ia.Claude("k", max_queries=2, opener=opener)
    client.read_labels("x", "Foto 1 = Paiz", [b"img"])
    client.read_labels("x", "Foto 1 = Paiz", [b"img"])
    with pytest.raises(ia.ProviderFailed, match="claude_tope_consultas"):
        client.read_labels("x", "Foto 1 = Paiz", [b"img"])
    assert sum(1 for call in calls if call.full_url.endswith("/messages")) == 2


# ------------------------------------------------------------------- cadena


class FakeProvider(FakeClient):
    def __init__(self, name: str, answers: list[object], model: str) -> None:
        super().__init__(answers, model=model)
        self.name = name
        self.usage = {"entrada": 0, "salida": 0}


@pytest.mark.parametrize("reason", ["claude_sin_credito", "claude_llave_invalida_401", "claude_saturado_529"])
def test_chain_switches_to_gemini_for_the_rest_of_the_run(reason: str) -> None:
    good = {"readings": [reading(1, 116), reading(2, 116)]}
    claude = FakeProvider("claude", [ia.ProviderFailed(reason)], "claude-haiku-5-5")
    gemini = FakeProvider("gemini", [good, good], "gemini-3.5-flash")
    chain = ia.VisionChain([claude, gemini])
    cache: dict = {}
    items = [goya(), {**goya(), "gtin": "00041331019999"}]
    stats, resolved = ia.annotate(queue(*items), cache, client=chain, fetch_image=lambda url: b"", minutes=5,
                                  delay=0, sleep=lambda _: None, now="T")
    assert len(claude.calls) == 1 and len(gemini.calls) == 2  # Claude no se vuelve a intentar
    assert stats["cambio_de_proveedor"] == reason and stats["consultas_por_proveedor"] == {"claude": 0, "gemini": 2}
    assert {entry["provider"] for entry in cache.values()} == {"gemini"}
    assert [ia.auto_engine(item) for item in resolved] == ["gemini_auto", "gemini_auto"]


def test_claude_readings_resolve_as_claude_auto_and_are_labelled_in_the_queue() -> None:
    claude = FakeProvider("claude", [{"readings": [reading(1, 116, text="Peso neto 116 g"), reading(2, 116)]},
                                     {"readings": [reading(1, 1000), reading(2, 1000)]}], "claude-haiku-5-5")
    chain = ia.VisionChain([claude, FakeProvider("gemini", [], "gemini-3.5-flash")])
    cache: dict = {}
    document = queue(goya(), mazola())
    stats, resolved = ia.annotate(document, cache, client=chain, fetch_image=lambda url: b"", minutes=5, delay=0,
                                  sleep=lambda _: None, now="T")
    assert [ia.auto_engine(item) for item in resolved] == ["claude_auto"]
    assert ia.auto_decision(resolved[0])["note"] == "Claude: Peso neto 116 g"
    assert document["items"][0]["ia"]["fuente"] == "claude"
    assert all(offer["ia"]["fuente"] == "claude" for offer in document["items"][0]["offers"])
    assert cache["00041331013734"]["provider"] == "claude" and stats["proveedor"] == "claude"
    assert stats["tokens"]["claude"] == {"entrada": 0, "salida": 0}


def test_chain_with_only_claude_stops_the_run_when_claude_fails() -> None:
    chain = ia.VisionChain([FakeProvider("claude", [ia.ProviderFailed("claude_sin_credito")], "claude-haiku-5-5")])
    stats, _ = ia.annotate(queue(goya(), mazola()), {}, client=chain, fetch_image=lambda url: b"", minutes=5,
                           delay=0, sleep=lambda _: None)
    assert stats["detenido_por"] == "claude_sin_credito" and stats["pendiente"] == 2


def test_old_cache_entries_count_as_gemini_and_main_splits_files_by_engine(tmp_path: Path, monkeypatch) -> None:
    assert ia.entry_provider({"model": "gemini-3.5-flash"}) == "gemini"
    names = ia.item_photos(goya())
    cache = {"00041331013734": {"key": ia.cache_key(names), "provider": "claude", "model": "claude-haiku-5-5",
                                "raw": {"readings": [reading(1, 116), reading(2, 116)]}},
             "00750894620685": {"key": ia.cache_key(ia.item_photos(mazola())), "model": "gemini-3.5-flash",
                                "raw": {"readings": [reading(1, 1000), reading(2, 1000)]}}}
    (tmp_path / "cola.json").write_text(json.dumps(queue(goya(), {**mazola(), "offers": mazola()["offers"][2:]})))
    (tmp_path / "cache.json").write_text(json.dumps({"schema": ia.CACHE_SCHEMA, "entries": cache}))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    ia.main(["--queue", str(tmp_path / "cola.json"), "--cache-in", str(tmp_path / "cache.json"),
             "--cache-out", str(tmp_path / "out.json"), "--auto-out", str(tmp_path / "auto.json"),
             "--queue-with-auto", str(tmp_path / "con-auto.json"), "--minutes", "0"])
    claude = json.loads((tmp_path / "auto-claude.json").read_text())["decisions"]
    gemini = json.loads((tmp_path / "auto.json").read_text())["decisions"]
    assert [d["gtin"] for d in claude] == ["00041331013734"]
    assert all(d["gtin"] != "00041331013734" for d in gemini)
