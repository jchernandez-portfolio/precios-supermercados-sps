"""Ficha de producto por GTIN con Claude y búsqueda web (prueba, sin escribir decisiones)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import buscar_tamanos_web as web  # noqa: E402
import fichas_producto_web as fichas  # noqa: E402

URL = "https://www.superselectos.com/products?productId=14978"
SHEET = {"encontrado": True, "marca": "Diana", "fabricante": "Productos Alimenticios Diana",
         "nombre_estandar": "Diana Elotitos Limón 157 g", "variante": "Limón", "medida_valor": 157,
         "medida_unidad": "g", "unidades": 1, "contenido_por_unidad": None,
         "descripcion": "Boquitas de maíz sabor elote y limón en bolsa de 157 g.",
         "categoria": "Snacks > Boquitas de maíz", "url": URL, "texto_citado": "Elotito Con Limon Diana 157 g",
         "confianza": "alta"}


def response(sheet: dict, cited: str = "Elotito Con Limon Diana 157 g", url: str = URL) -> dict:
    return {"content": [
        {"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {"query": "748757001566"}},
        {"type": "web_search_tool_result", "tool_use_id": "s1", "content": [
            {"type": "web_search_result", "url": url, "title": "Super Selectos", "page_age": "3 days ago",
             "encrypted_content": "x"}]},
        {"type": "text", "text": "Super Selectos lo vende como 157 g.", "citations": [
            {"type": "web_search_result_location", "url": url, "title": "Super Selectos", "cited_text": cited}]},
        {"type": "text", "text": "\n" + json.dumps(sheet)},
    ], "usage": {"input_tokens": 900, "output_tokens": 120}}


class Messages:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class Client:
    def __init__(self, answers):
        self.messages = Messages(answers)


def test_sheet_keeps_fields_sources_page_age_and_checks_the_cited_number() -> None:
    client = web.ClaudeWebSearch(client=Client([response(SHEET)]))
    index = {"00748757001566": {"gtin": "00748757001566", "brand": "Diana", "name": "Diana elotitos con limon",
                                "offers": [{"size": "194 g"}, {"size": "157 g"}]}}
    sheets, stop = fichas.run(["00748757001566"], index, client)
    assert stop is None
    sheet = sheets[0]
    assert sheet["nombre_estandar"] == "Diana Elotitos Limón 157 g" and sheet["medida_valor"] == "157"
    assert sheet["url_de_busqueda"] and sheet["cita_con_numero"] and not sheet["fuente_hondurena"]
    assert sheet["paginas"][0]["page_age"] == "3 days ago" and sheet["tamanos_super"] == ["194 g", "157 g"]
    call = client.client.messages.calls[0]
    prompt = call["messages"][0]["content"]
    assert "GTIN 748757001566" in prompt and "Ignora los supermercados de Honduras" in prompt
    assert "194 g" not in prompt  # sin los tamaños de los súper
    assert call["tools"][0]["max_uses"] == fichas.MAX_USES


def test_honduran_store_citation_does_not_verify_the_number() -> None:
    url = "https://www.walmart.com.hn/elotitos"
    read = web.read_response(response({**SHEET, "url": url}, url=url))
    sheet = fichas.sheet_from(read)
    assert sheet["fuente_hondurena"] is True and sheet["cita_con_numero"] is False


def test_rate_limit_stops_and_keeps_previous_sheets(tmp_path: Path, monkeypatch) -> None:
    class ApiError(Exception):
        status_code = 429

    client = web.ClaudeWebSearch(client=Client([response(SHEET), ApiError("slow")]))
    sheets, stop = fichas.run(["00748757001566", "00748757006714"], {}, client, sleep=lambda _: None)
    assert [s["gtin"] for s in sheets] == ["00748757001566"] and stop == "claude_http_429"
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="sin_llave"):
        fichas.main(["--gtins", "748757001566", "--output", str(tmp_path / "o.json")])
    with pytest.raises(SystemExit, match="parametros_invalidos"):
        fichas.main(["--gtins", "abc", "--output", str(tmp_path / "o.json")])
