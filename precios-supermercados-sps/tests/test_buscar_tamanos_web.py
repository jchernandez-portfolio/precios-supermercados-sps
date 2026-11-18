"""Búsqueda web de tamaños (Gemini con Google Search): sólo la cola, nunca se guarda sola."""
from __future__ import annotations

import io
import json
import sys
import urllib.error
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import buscar_tamanos_web as web  # noqa: E402

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
HEINZ = "00013000002189"


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def heinz() -> dict:
    return {"gtin": HEINZ, "kind": "atipico", "name": "Mostaza Heinz Amarilla Natural Squeeze - 566 g",
            "offers": [{"store": "walmart", "value": "566", "unit": "g"},
                       {"store": "colonial", "value": "566", "unit": "g"},
                       {"store": "comisariato_los_andes", "value": "20", "unit": "oz"}],
            "ia": {"status": "sugerencia", "value": "566", "unit": "g"}}


def answer(payload: object, chunks: list | None = None) -> dict:
    text = payload if isinstance(payload, str) else "```json\n" + json.dumps(payload) + "\n```"
    candidate = {"content": {"parts": [{"text": text}]}}
    if chunks is not None:
        candidate["groundingMetadata"] = {"groundingChunks": chunks}
    return {"candidates": [candidate], "usageMetadata": {"promptTokenCount": 30, "candidatesTokenCount": 9}}


FOUND = {"encontrado": True, "valor": 20, "unidad": "oz", "texto": "NET WT 20 OZ (1 LB 4 OZ) 566g",
         "url": "https://www.heinz.com/product/00013000002189"}
CHUNKS = [{"web": {"uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc", "title": "heinz.com"}},
          {"web": {"uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc", "title": "heinz.com"}},
          {"web": {"uri": "javascript:alert(1)", "title": "x"}}]


class FakeSearch:
    model = "gemini-flash-latest"
    retries = 0
    last_error = None

    def __init__(self, answers: list) -> None:
        self.answers = answers
        self.asked: list[tuple[str, str]] = []

    def search(self, gtin: str, name: str) -> object:
        self.asked.append((gtin, name))
        result = self.answers.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def test_answer_with_size_and_sources_becomes_a_web_suggestion() -> None:
    result = web.parse_answer(answer(FOUND, CHUNKS))
    assert result["status"] == "encontrado"
    assert (result["value"], result["unit"]) == ("20", "oz")
    assert result["url"] == FOUND["url"] and result["verificada"] is True
    assert result["fuentes"] == [{"title": "heinz.com", "url": CHUNKS[0]["web"]["uri"]}]  # sin repetidos ni javascript:
    assert result["text"] == FOUND["texto"]


def test_unsafe_or_empty_answers_never_become_a_suggestion() -> None:
    assert web.parse_answer(answer({"encontrado": False}, CHUNKS))["reason"] == "no_encontrado"
    assert web.parse_answer(answer("no sé"))["reason"] == "respuesta_invalida"
    assert web.parse_answer(answer({**FOUND, "valor": -3}, CHUNKS))["reason"] == "tamano_invalido"
    assert web.parse_answer(answer({**FOUND, "unidad": "cajas"}, CHUNKS))["reason"] == "tamano_invalido"
    # Sin páginas de Google ni URL válida no hay de dónde verificar: no se sugiere.
    assert web.parse_answer(answer({**FOUND, "url": "ftp://x"}, []))["reason"] == "sin_fuente"
    keyed = web.parse_answer(answer({**FOUND, "url": "https://user:pw@heinz.com/x"}, CHUNKS))
    assert keyed["url"] == CHUNKS[0]["web"]["uri"] and keyed["verificada"] is False
    assert web.clean_url("https://a.com/" + "x" * 600) is None and web.clean_url("https://a b.com") is None


def test_only_queue_items_are_searched_first_then_ties_then_unresolved() -> None:
    items = [
        {"gtin": "1" * 14, "kind": "atipico", "ia": {"status": "auto"}},
        {"gtin": "2" * 14, "kind": "atipico", "ia": {"status": "no_legible"}},
        {"gtin": "3" * 14, "kind": "empate", "ia": {"status": "sugerencia"}},
        {"gtin": "4" * 14, "kind": "atipico", "ia": {"status": "sugerencia"}},
        {"gtin": "5" * 14, "kind": "atipico", "ia": {"status": "conflicto"}},
    ]
    order = [item["gtin"] for item in web.priority(items, ["4" * 14])]
    assert order == ["4" * 14, "3" * 14, "2" * 14, "5" * 14]  # el resuelto y la sugerencia clara no se buscan


def test_annotate_caps_queries_reuses_cache_and_marks_matching_stores() -> None:
    other = {"gtin": "07501017003075", "kind": "empate", "name": "Otro", "offers": [], "ia": {"status": "no_legible"}}
    old = (NOW - timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cache = {"07501017003075": {"status": "no_encontrado", "prompt": web.PROMPT_VERSION, "utc": old}}
    queue = {"items": [heinz(), other, {"gtin": "x", "kind": "empate"}]}
    client = FakeSearch([answer(FOUND, CHUNKS), answer({"encontrado": False}, [])])
    sleeps: list[float] = []
    summary = web.annotate(queue, cache, client=client, limit=5, first=[HEINZ], sleep=sleeps.append, now=NOW)
    assert [gtin for gtin, _ in client.asked] == [HEINZ, "07501017003075"]  # lo viejo «no encontrado» se reintenta
    assert sleeps == [web.DEFAULT_DELAY]
    found = queue["items"][0]["web"]
    assert found["status"] == "encontrado" and found["coincide_con"] == ["Walmart", "Colonial", "Los Andes"]
    assert queue["items"][1]["web"]["status"] == "no_encontrado" and "web" not in queue["items"][2]
    assert summary["consultas"] == 2 and summary["encontrados"] == 1 and summary["no_encontrados"] == 1
    # Segunda corrida: todo está en caché y vigente, no se pregunta nada más.
    again = FakeSearch([])
    web.annotate(queue, cache, client=again, limit=5, now=NOW)
    assert again.asked == [] and queue["items"][0]["web"]["value"] == "20"


def test_quota_or_limit_stops_and_keeps_what_was_found() -> None:
    items = [{"gtin": f"{n:014d}", "kind": "empate", "name": str(n), "offers": []} for n in range(1, 4)]
    cache: dict = {}
    summary = web.annotate({"items": items}, cache, client=FakeSearch([answer(FOUND, CHUNKS)]), limit=1, now=NOW,
                           sleep=lambda _: None)
    assert summary["detenido_por"] == "tope_de_consultas" and list(cache) == ["00000000000001"]
    summary = web.annotate({"items": items}, cache, client=FakeSearch([web.QuotaExceeded("cuota_agotada_429")]),
                           limit=5, now=NOW, sleep=lambda _: None)
    assert summary["detenido_por"] == "cuota_agotada" and list(cache) == ["00000000000001"]
    assert web.annotate({"items": items}, cache, client=None, limit=5, now=NOW)["detenido_por"] == "sin_llave"


def test_search_request_uses_google_search_tool_and_key_only_in_header() -> None:
    sent: list = []

    def opener(request, timeout):
        sent.append(request)
        if request.full_url.endswith("models?pageSize=1000"):
            models = [{"name": "models/gemini-flash-latest", "supportedGenerationMethods": ["generateContent"]}]
            return Response(json.dumps({"models": models}).encode())
        return Response(json.dumps(answer(FOUND, CHUNKS)).encode())

    client = web.WebSearch("SECRET-KEY", opener=opener, sleep=lambda _: None)
    result = web.parse_answer(client.search(HEINZ, 'Mostaza "Heinz"'))
    assert result["status"] == "encontrado"
    body = json.loads(sent[1].data)
    assert body["tools"] == [{"google_search": {}}] and "responseSchema" not in body["generationConfig"]
    prompt = body["contents"][0]["parts"][0]["text"]
    assert prompt.startswith(f"Busca en internet el peso oficial del producto con GTIN {HEINZ} y nombre \"Mostaza 'Heinz'\"")
    assert "Devuelve el peso y la URL de la fuente" in prompt
    assert all("SECRET-KEY" not in request.full_url for request in sent)
    assert sent[1].get_header("X-goog-api-key") == "SECRET-KEY"


def test_quota_error_falls_back_to_the_next_model_then_stops() -> None:
    def opener(request, timeout):
        if request.full_url.endswith("models?pageSize=1000"):
            models = [{"name": f"models/{name}", "supportedGenerationMethods": ["generateContent"]}
                      for name in ("gemini-flash-latest", "gemini-flash-lite-latest")]
            return Response(json.dumps({"models": models}).encode())
        raise urllib.error.HTTPError(request.full_url, 429, "quota", {}, io.BytesIO(b"{}"))

    client = web.WebSearch("k", opener=opener, sleep=lambda _: None)
    summary = web.annotate({"items": [heinz()]}, {}, client=client, limit=3, first=[HEINZ], now=NOW)
    assert summary["detenido_por"] == "cuota_agotada" and client.model == "gemini-flash-lite-latest"


def test_main_writes_cache_and_annotated_queue_without_a_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    stamp = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = {**web.parse_answer(answer(FOUND, CHUNKS)), "prompt": web.PROMPT_VERSION, "utc": stamp}
    (tmp_path / "cache.json").write_text(json.dumps({"schema": web.WEB_SCHEMA, "entries": {HEINZ: entry, "../x": {}}}))
    (tmp_path / "cola.json").write_text(json.dumps({"schema": "rpi-size-queue/v1", "items": [heinz()]}))
    assert web.main(["--queue", str(tmp_path / "cola.json"), "--cache-in", str(tmp_path / "cache.json"),
                     "--cache-out", str(tmp_path / "out.json"), "--max", "0"]) == 0
    queue = json.loads((tmp_path / "cola.json").read_text())
    assert queue["items"][0]["web"]["url"] == FOUND["url"] and queue["web"]["detenido_por"] == "sin_llave"
    assert list(json.loads((tmp_path / "out.json").read_text())["entries"]) == [HEINZ]


def test_main_rejects_bad_parameters(tmp_path: Path) -> None:
    for extra in (["--max", "500"], ["--first", "abc"], ["--delay", "0"]):
        try:
            web.main(["--queue", str(tmp_path / "c.json"), "--cache-out", str(tmp_path / "o.json"), *extra])
        except SystemExit as error:
            assert str(error) == "parametros_invalidos"
        else:
            raise AssertionError(extra)
