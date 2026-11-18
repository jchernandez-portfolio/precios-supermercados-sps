"""Claude con búsqueda web resuelve la cola de tamaños; sólo lo verificado se aprueba solo."""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import buscar_tamanos_web as web  # noqa: E402

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
HEINZ = "00013000002189"
URL = "https://www.heinz.com/product/00013000002189"


def heinz() -> dict:
    return {"gtin": HEINZ, "kind": "atipico", "brand": "Heinz", "name": "Mostaza Heinz Amarilla Natural Squeeze - 566 g",
            "offers": [{"store": "walmart", "size": "566 g", "value": "566", "unit": "g"},
                       {"store": "colonial", "size": "566 g", "value": "566", "unit": "g"},
                       {"store": "comisariato_los_andes", "size": "20 oz", "value": "20", "unit": "oz"}]}


def response(answer: dict | str, *, cited: str = "NET WT 20 OZ (1 LB 4 OZ) 566g", url: str = URL,
             error: str | None = None) -> dict:
    final = answer if isinstance(answer, str) else json.dumps(answer)
    result = {"type": "web_search_tool_result_error", "error_code": error} if error else [
        {"type": "web_search_result", "url": url, "title": "Heinz", "encrypted_content": "x", "page_age": None},
        {"type": "web_search_result", "url": "javascript:alert(1)", "title": "x", "encrypted_content": "x"},
    ]
    return {"content": [
        {"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {"query": "heinz 00013000002189"}},
        {"type": "web_search_tool_result", "tool_use_id": "s1", "content": result},
        {"type": "text", "text": "La etiqueta dice 20 oz.", "citations": [] if error else [
            {"type": "web_search_result_location", "url": url, "title": "Heinz", "cited_text": cited}]},
        {"type": "text", "text": "\n" + final},
    ], "usage": {"input_tokens": 1200, "output_tokens": 80}}


FOUND = {"encontrado": True, "valor": 20, "unidad": "oz", "texto": "NET WT 20 OZ (1 LB 4 OZ) 566g", "url": URL,
         "confianza": "alta", "gtin_verificado": True}


class FakeMessages:
    def __init__(self, answers: list) -> None:
        self.answers = answers
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class FakeClient:
    def __init__(self, answers: list) -> None:
        self.messages = FakeMessages(answers)


class ApiError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"Error code: {status}")
        self.status_code = status


def claude(answers: list) -> web.ClaudeWebSearch:
    return web.ClaudeWebSearch(client=FakeClient(answers))


def parsed(answer: dict | str, **kwargs) -> dict:
    return web.parse_answer(web.read_response(response(answer, **kwargs)))


def test_request_sends_brand_name_gtin_and_published_sizes_with_the_web_search_tool() -> None:
    client = claude([response(FOUND)])
    client.search(heinz())
    call = client.client.messages.calls[0]
    assert call["model"] == "claude-sonnet-5-5" and call["max_tokens"] == web.MAX_TOKENS
    assert call["tools"] == [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}]
    prompt = call["messages"][0]["content"]
    assert "Marca: Heinz" in prompt and f"GTIN (código de barras): {HEINZ}" in prompt
    assert "Walmart: 566 g; Colonial: 566 g; Los Andes: 20 oz" in prompt
    assert "No elijas un tamaño sólo porque lo publica un supermercado" in prompt
    assert client.usage == {"entrada": 1200, "salida": 80, "busquedas": 1}


def test_verified_answer_is_approved_with_the_published_size_that_matches() -> None:
    item = heinz()
    result = parsed(FOUND)
    assert (result["value"], result["unit"], result["confianza"]) == ("20", "oz", "alta")
    assert result["url_de_busqueda"] is True and result["cita_con_numero"] is True
    assert result["fuentes"] == [{"title": "Heinz", "url": URL}]  # sin repetidos ni javascript:
    assert web.auto_reasons(item, result) == []
    decision = web.decision_for(item, result)
    # 20 oz = 566 g: se guarda la forma que publican más súper.
    assert (decision["gtin"], decision["decision"], decision["value"], decision["unit"]) == (HEINZ, "size", "566", "g")
    assert decision["note"].startswith("Claude (web, heinz.com): NET WT 20 OZ") and URL in decision["note"]


@pytest.mark.parametrize(("answer", "kwargs", "reason"), [
    ({**FOUND, "confianza": "media"}, {}, "confianza_media"),
    ({**FOUND, "url": "https://inventada.example/heinz"}, {}, "url_no_vino_de_la_busqueda"),
    (FOUND, {"cited": "Heinz Yellow Mustard squeeze bottle"}, "ninguna_cita_trae_el_numero"),
    ({**FOUND, "valor": 14, "texto": "14 oz"}, {"cited": "NET WT 14 OZ"}, "no_coincide_con_ningun_super"),
    ({"encontrado": False}, {}, "no_encontrado"),
    ("no sé", {}, "respuesta_invalida"),
    ({**FOUND, "unidad": "cajas"}, {}, "tamano_invalido"),
    (FOUND, {"error": "max_uses_exceeded"}, "sin_fuente"),
])
def test_anything_not_fully_verified_stays_for_a_person(answer, kwargs, reason) -> None:
    item = heinz()
    entry = {**parsed(answer, **kwargs), "prompt": web.PROMPT_VERSION}
    assert reason in web.auto_reasons(item, entry)
    note = web.web_annotation(item, entry)
    assert note["auto"] is False and note["nota"] == web.MANUAL_NOTE


def test_cited_number_must_be_the_same_number() -> None:
    cite = [{"cited_text": "Peso neto 1.5 kg (1500 g)"}]
    assert web._number_cited("1.5", cite) and web._number_cited(1500, cite)
    assert not web._number_cited(15, cite) and not web._number_cited(150, cite) and not web._number_cited(None, cite)


def test_run_resolves_in_queue_order_caps_queries_and_reuses_cache() -> None:
    tie = {"gtin": "07501017003075", "kind": "empate", "brand": "Sedal", "name": "Shampoo",
           "offers": [{"store": "walmart", "size": "400 ml", "value": "400", "unit": "ml"}]}
    queue = {"items": [tie, heinz(), {"gtin": "x", "kind": "empate"}]}
    cache: dict = {}
    client = claude([response({"encontrado": False}), response(FOUND)])
    sleeps: list[float] = []
    stats = web.search_new(queue["items"], cache, client=client, limit=5, minutes=10, sleep=sleeps.append, now=NOW)
    assert stats == {"consultas": 2, "errores": 0, "detenido_por": None} and sleeps == [web.DEFAULT_DELAY]
    remaining, resolved = web.annotate(queue, cache)
    assert [item["gtin"] for item in resolved] == [HEINZ]
    assert [item["gtin"] for item in remaining] == ["07501017003075", "x"]
    assert remaining[0]["web"]["nota"] == web.MANUAL_NOTE and "web" not in remaining[1]
    assert resolved[0]["web"]["coincide_con"] == ["Walmart", "Colonial", "Los Andes"]
    # Segunda corrida: lo guardado está vigente, no se pregunta nada.
    again = claude([])
    assert web.search_new(queue["items"], cache, client=again, limit=5, minutes=10, now=NOW)["consultas"] == 0
    # Lo no resuelto se reintenta a los 30 días.
    later = NOW + timedelta(days=31)
    retry = claude([response({"encontrado": False})])
    web.search_new(queue["items"], cache, client=retry, limit=5, minutes=10, now=later, sleep=lambda _: None)
    assert len(retry.client.messages.calls) == 1


def test_rate_limit_or_bad_key_stops_the_run_and_server_errors_skip_the_item() -> None:
    items = [{"gtin": f"{n:014d}", "kind": "empate", "name": str(n), "offers": []} for n in range(1, 6)]
    cache: dict = {}
    client = claude([ApiError(529), response(FOUND), ApiError(429)])
    stats = web.search_new(items, cache, client=client, limit=10, minutes=10, now=NOW, sleep=lambda _: None)
    assert stats == {"consultas": 1, "errores": 1, "detenido_por": "claude_http_429"}
    assert list(cache) == ["00000000000002"] and "429" in client.last_error
    many = claude([ApiError(500), ApiError(500), ApiError(500)])
    assert web.search_new(items, {}, client=many, limit=10, minutes=10, now=NOW,
                          sleep=lambda _: None)["detenido_por"] == "no_disponible"
    assert web.search_new(items, {}, client=claude([]), limit=0, minutes=10, now=NOW)["detenido_por"] == "sin_consultas"
    assert web.search_new(items, {}, client=None, limit=5, minutes=10, now=NOW)["detenido_por"] == "sin_llave"
    capped = claude([response(FOUND)])
    assert web.search_new(items, {}, client=capped, limit=1, minutes=10, now=NOW)["detenido_por"] == "tope_de_consultas"


def test_main_writes_engine_decisions_and_a_fallback_queue(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    entry = {**parsed(FOUND), "prompt": web.PROMPT_VERSION, "modelo": "claude-sonnet-5-5",
             "utc": NOW.strftime("%Y-%m-%dT%H:%M:%SZ")}
    tie = {"gtin": "07501017003075", "kind": "empate", "name": "Shampoo", "offers": []}
    (tmp_path / "cache.json").write_text(json.dumps({"schema": web.WEB_SCHEMA, "entries": {HEINZ: entry, "../x": {}}}))
    (tmp_path / "cola.json").write_text(json.dumps({"schema": "rpi-size-queue/v1", "items": [tie, heinz()]}))
    assert web.main(["--queue", str(tmp_path / "cola.json"), "--queue-with-auto", str(tmp_path / "con-auto.json"),
                     "--auto-out", str(tmp_path / "auto.json"), "--cache-in", str(tmp_path / "cache.json"),
                     "--cache-out", str(tmp_path / "out.json"), "--max", "0"]) == 0
    queue = json.loads((tmp_path / "cola.json").read_text())
    assert [item["gtin"] for item in queue["items"]] == ["07501017003075"] and queue["counts"]["atipico"] == 0
    assert queue["web"]["aprobados_solos"] == 1 and queue["web"]["detenido_por"] == "sin_consultas"
    auto = json.loads((tmp_path / "auto.json").read_text())
    assert auto["schema"] == "rpi-size-decisions/v1"
    assert [(d["gtin"], d["value"], d["unit"]) for d in auto["decisions"]] == [(HEINZ, "566", "g")]
    # Si guardar falla, se publica la cola de respaldo: Heinz sigue adentro, como sugerencia.
    fallback = json.loads((tmp_path / "con-auto.json").read_text())
    heinz_item = next(item for item in fallback["items"] if item["gtin"] == HEINZ)
    assert heinz_item["web"]["auto"] is False and heinz_item["web"]["value"] == "20"
    assert list(json.loads((tmp_path / "out.json").read_text())["entries"]) == [HEINZ]


def test_main_rejects_bad_parameters(tmp_path: Path) -> None:
    base = ["--queue", str(tmp_path / "c.json"), "--queue-with-auto", str(tmp_path / "a.json"),
            "--auto-out", str(tmp_path / "d.json"), "--cache-out", str(tmp_path / "o.json")]
    for extra in (["--max", "500"], ["--first", "abc"], ["--model", "gpt-4"], ["--minutes", "500"]):
        with pytest.raises(SystemExit, match="parametros_invalidos"):
            web.main([*base, *extra])
