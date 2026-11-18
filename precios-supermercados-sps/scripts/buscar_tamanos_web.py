#!/usr/bin/env python3
"""Búsqueda web por GTIN: resuelve la cola de tamaños sin leer fotos; a mano sólo lo imposible.

Para cada código de ``cola.json`` (nunca el catálogo completo), en el orden de la cola,
pregunta por el peso oficial con marca, nombre y GTIN:

    Busca el peso oficial del producto [Marca] [Nombre] con GTIN [GTIN]. Revisa
    fabricantes, GS1, Open Food Facts y supermercados de Centroamérica. Ignora los
    supermercados de Honduras (Walmart, La Colonia, etc.) para evitar datos circulares.

Proveedor: **Gemini** con Grounding with Google Search (por defecto, ``GEMINI_API_KEY``)
o **Claude** con la herramienta ``web_search`` del SDK ``anthropic`` (``--provider claude``).

**Auto-aprobación** (``engine:<proveedor>_auto``, nunca sobre una decisión humana) sólo si:

1. encontrado con confianza ``alta`` o ``media``;
2. la fuente salió de la búsqueda y NO es un súper de Honduras (dato circular);
3. un pasaje citado de una fuente independiente trae el número;
4. ese tamaño coincide con el que publica al menos un súper (misma comparación que el motor).

Lo demás queda en la cola con «🤖 No se pudo verificar el dato en la web. Requiere revisión
humana». **Re-auditoría** (``--audit-max``): los códigos que la IA ya resolvió con fotos
también se buscan; si una fuente verificada dice otro tamaño, vuelven a la cola manual como
``correccion_ia`` (la decisión de la IA sigue vigente hasta que una persona decida).

Caché ``web-tamanos.json`` por GTIN (lo no resuelto se reintenta a los 30 días). Una consulta a
la vez, con tope de consultas y de minutos; ante cuota agotada o llave rechazada se detiene y
guarda lo avanzado. La llave sólo viaja en un encabezado, nunca en URLs ni logs.

    python scripts/buscar_tamanos_web.py --queue /tmp/cola/cola.json \
        --queue-with-auto /tmp/cola/cola-con-auto.json --auto-out /tmp/cola/auto-decisiones-web.json \
        --cache-in cola/web-tamanos.json --cache-out /tmp/cola/web-tamanos.json --max 40 --audit-max 20
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from precios_supermercados.size_decisions import PAYLOAD_SCHEMA  # noqa: E402

UNITS = ("g", "ml", "oz", "unit")
from sugerir_tamanos_ia import (  # noqa: E402
    API_BASE,
    STORE_NAMES,
    Gemini,
    ModelUnavailable,
    QuotaExceeded,
    SuggestionError,
    TemporarilyUnavailable,
    _json_from_text,
    _signature,
    offer_signature,
    parse_reading,
    same_size,
)

WEB_SCHEMA = "rpi-size-web/v2"
WEB_FILE = "web-tamanos.json"
PROMPT_VERSION = 3
ENGINE_BY_PROVIDER = {"off": "off_auto", "gemini": "gemini_auto", "claude": "claude_auto"}
PROVIDER_LABEL = {"off": "Open Food Facts", "gemini": "Gemini", "claude": "Claude"}
OFF_API = "https://world.openfoodfacts.org/api/v2/product/"
OFF_PAGE = "https://world.openfoodfacts.org/product/"
OFF_FIELDS = "code,product_name,brands,quantity,product_quantity,product_quantity_unit"
OFF_UNITS = {"g": "g", "kg": "kg", "mg": None, "ml": "ml", "cl": None, "l": "l"}
OFF_USER_AGENT = "precios-sps-cola-tamanos/1.0 (+https://github.com/jchernandez-portfolio/precios-supermercados-sps)"
DEFAULT_MODEL = "claude-sonnet-5-5"
GEMINI_MAX_MODELS = 3
DEFAULT_AUDIT_MAX = 20
# Súper de Honduras: su página es justo el dato que se revisa, no cuenta como fuente.
HN_DOMAINS = ("lacolonia.com", "supercolonial.com", "walmart.com.hn", "paiz.com.hn",
              "comisariatolosandes.com", "pricesmart.com")
WEB_SEARCH_TOOL = "web_search_20250305"
MAX_SEARCHES = 3
MAX_TOKENS = 1500
DEFAULT_MAX = 40
HARD_MAX = 300
DEFAULT_MINUTES = 60
DEFAULT_DELAY = 2.0
RETRY_UNRESOLVED_DAYS = 30
MAX_CONSECUTIVE_FAILURES = 3
MAX_SOURCES = 5
MAX_URL = 500
MAX_TEXT = 160
MANUAL_NOTE = "🤖 No se pudo verificar el dato en la web. Requiere revisión humana"
CORRECTION_NOTE = "Corrección de IA"
_GTIN = re.compile(r"\d{8,14}")
_MODEL = re.compile(r"claude-[a-z0-9-]{1,60}")
PROMPT = """Busca el peso oficial del producto {brand} {name} con GTIN {gtin}. Revisa fabricantes, GS1, \
Open Food Facts y supermercados de Centroamérica. Ignora los supermercados de Honduras (Walmart, La Colonia, \
Colonial, Paiz, PriceSmart, Los Andes) para evitar datos circulares.

Reglas:
- Es el contenido neto del empaque (Peso neto / Net Wt / Contenido neto), no el peso de envío.
- Si el empaque trae varias unidades, da el contenido TOTAL.
- Si no hallas este producto exacto (mismo GTIN, o misma marca y nombre), o las fuentes se contradicen,
  responde encontrado=false o confianza "baja".

Explica en 1 o 2 frases citando la fuente (con el número tal como lo escribe) y termina con UNA línea con este JSON:
{{"encontrado": true, "valor": 566, "unidad": "g", "texto": "NET WT 20 OZ (566 g)", "url": "https://...", \
"confianza": "alta", "gtin_verificado": true}}
unidad es una de: g, kg, ml, l, oz, fl_oz, lb, unit. confianza es alta, media o baja."""


class StopRun(RuntimeError):
    """La API no deja seguir en esta corrida (cuota, llave, permiso o modelo): se guarda lo avanzado."""


# ------------------------------------------------------------------ respuesta


def _as_dict(value: object) -> object:
    """Respuesta del SDK (objetos pydantic) o de una prueba (dict) → dicts y listas simples."""
    dump = getattr(value, "model_dump", None)
    return dump() if callable(dump) else value


def clean_url(value: object) -> str | None:
    """URL pública http(s) sin credenciales ni espacios (lo demás se descarta)."""
    if not isinstance(value, str):
        return None
    url = value.strip()
    if not url or len(url) > MAX_URL or any(char.isspace() for char in url):
        return None
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        return None
    return url


def _host(url: str | None) -> str:
    host = urllib.parse.urlsplit(url).hostname if url else None
    return (host or "").lower().removeprefix("www.")


def source_host(source: Mapping[str, str]) -> str:
    """Dominio de una fuente. Gemini devuelve URLs de redirección de Google: su título es el dominio."""
    host = _host(source.get("url"))
    if host.endswith("vertexaisearch.cloud.google.com"):
        host = " ".join(str(source.get("title") or "").split()).lower().removeprefix("www.")
    return host


def is_honduran_store(host: str) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in HN_DOMAINS)


def read_response(response: object) -> dict[str, object]:
    """Claude: texto final, páginas que devolvió la búsqueda, citas y errores de la herramienta."""
    document = _as_dict(response)
    blocks = document.get("content") if isinstance(document, Mapping) else None
    texts: list[str] = []
    results: list[dict[str, str]] = []
    citations: list[dict[str, str]] = []
    errors: list[str] = []
    searches = 0
    for block in blocks if isinstance(blocks, list) else ():
        block = _as_dict(block)
        if not isinstance(block, Mapping):
            continue
        kind = block.get("type")
        if kind == "text":
            texts.append(str(block.get("text") or ""))
            for citation in block.get("citations") or ():
                citation = _as_dict(citation)
                url = clean_url(citation.get("url")) if isinstance(citation, Mapping) else None
                if url:
                    citations.append({"url": url, "title": " ".join(str(citation.get("title") or "").split())[:80],
                                      "cited_text": " ".join(str(citation.get("cited_text") or "").split())[:150]})
        elif kind == "server_tool_use":
            searches += 1
        elif kind == "web_search_tool_result":
            content = _as_dict(block.get("content"))
            if isinstance(content, Mapping):
                errors.append(str(content.get("error_code") or "error"))
                continue
            for result in content if isinstance(content, list) else ():
                result = _as_dict(result)
                url = clean_url(result.get("url")) if isinstance(result, Mapping) else None
                if url and all(seen["url"] != url for seen in results):
                    results.append({"url": url, "title": " ".join(str(result.get("title") or "").split())[:80]})
    usage = _as_dict(document.get("usage")) if isinstance(document, Mapping) else None
    return {"text": "".join(texts), "results": results, "citations": citations, "errors": errors,
            "searches": searches, "usage": usage if isinstance(usage, Mapping) else {}}


def read_gemini(answer: object) -> dict[str, object]:
    """Gemini con Google Search: texto, páginas (``groundingChunks``) y pasajes apoyados en cada
    página (``groundingSupports``), con la misma forma que :func:`read_response`."""
    try:
        candidate = answer["candidates"][0]
    except (KeyError, IndexError, TypeError):
        candidate = {}
    parts = candidate.get("content", {}).get("parts") if isinstance(candidate.get("content"), Mapping) else None
    text = "".join(str(part.get("text") or "") for part in parts or () if isinstance(part, Mapping))
    metadata = candidate.get("groundingMetadata") if isinstance(candidate.get("groundingMetadata"), Mapping) else {}
    pages: list[dict[str, str] | None] = []
    for chunk in metadata.get("groundingChunks") or ():
        web = chunk.get("web") if isinstance(chunk, Mapping) else None
        url = clean_url(web.get("uri")) if isinstance(web, Mapping) else None
        title = " ".join(str(web.get("title") or "").split())[:80] if isinstance(web, Mapping) else ""
        pages.append({"url": url, "title": title or _host(url)} if url else None)
    results: list[dict[str, str]] = []
    for page in pages:
        if page and all(seen["url"] != page["url"] for seen in results):
            results.append(page)
    citations: list[dict[str, str]] = []
    for support in metadata.get("groundingSupports") or ():
        if not isinstance(support, Mapping):
            continue
        segment = support.get("segment") if isinstance(support.get("segment"), Mapping) else {}
        cited = " ".join(str(segment.get("text") or "").split())[:150]
        for index in support.get("groundingChunkIndices") or ():
            if isinstance(index, int) and 0 <= index < len(pages) and pages[index]:
                citations.append({**pages[index], "cited_text": cited})
    queries = metadata.get("webSearchQueries")
    usage = answer.get("usageMetadata") if isinstance(answer, Mapping) else None
    usage = usage if isinstance(usage, Mapping) else {}
    return {"text": text, "results": results, "citations": citations, "errors": [],
            "searches": len(queries) if isinstance(queries, list) else 0,
            "usage": {"input_tokens": usage.get("promptTokenCount") or 0,
                      "output_tokens": usage.get("candidatesTokenCount") or 0}}


def final_json(text: str) -> object:
    """El JSON de la última línea (la prosa con las citas va antes)."""
    start = text.rfind('{"encontrado"')
    if start >= 0:
        end = text.find("}", start)
        try:
            return json.loads(text[start:end + 1])
        except ValueError:
            pass
    return _json_from_text(text)


def _number_cited(raw_value: object, citations: Sequence[Mapping[str, str]]) -> bool:
    """¿Alguna cita textual de la búsqueda trae el número que Claude reporta?"""
    try:
        number = Decimal(str(raw_value))
    except ArithmeticError:
        return False
    if not number.is_finite() or number <= 0:
        return False
    text = format(number.normalize(), "f")
    integer, _, decimals = text.partition(".")
    pattern = re.escape(integer) + (r"[.,]" + re.escape(decimals) if decimals else r"(?:[.,]0+)?")
    regex = re.compile(r"(?<![\d.,])" + pattern + r"(?![\d])")
    return any(regex.search(citation.get("cited_text") or "") for citation in citations)


def parse_answer(read: Mapping[str, object]) -> dict[str, object]:
    """Respuesta del modelo (no confiable) → ``encontrado`` (validado) o ``no_encontrado``."""
    results = list(read.get("results") or [])
    citations = list(read.get("citations") or [])
    unique: list[dict[str, str]] = []
    for source in citations + results:
        if all(seen["url"] != source["url"] for seen in unique):
            unique.append({"title": source.get("title") or _host(source["url"]), "url": source["url"]})
    base = {"fuentes": unique[:MAX_SOURCES], "busquedas": int(read.get("searches") or 0)}
    raw = final_json(str(read.get("text") or ""))
    if not isinstance(raw, Mapping) or not isinstance(raw.get("encontrado"), bool):
        return {"status": "no_encontrado", "reason": "respuesta_invalida", **base}
    if not raw["encontrado"]:
        return {"status": "no_encontrado", "reason": "no_encontrado", **base}
    reading = parse_reading({"legible": True, "same_product": True, "value": raw.get("valor"),
                             "unit": raw.get("unidad"), "label_text": raw.get("texto")})
    if reading["status"] != "leido":
        return {"status": "no_encontrado", "reason": "tamano_invalido", **base}
    if not unique:
        return {"status": "no_encontrado", "reason": "sin_fuente", **base}
    url = clean_url(raw.get("url"))
    known_urls = {source["url"] for source in unique}
    known_hosts = {source_host(source) for source in citations + results}
    from_search = url is not None and (url in known_urls or _host(url) in known_hosts)
    independent = [citation for citation in citations if not is_honduran_store(source_host(citation))]
    text = raw.get("texto")
    confidence = raw.get("confianza") if raw.get("confianza") in ("alta", "media", "baja") else "baja"
    return {"status": "encontrado", "value": reading["value"], "unit": reading["unit"],
            "text": " ".join(str(text).split())[:MAX_TEXT] if isinstance(text, str) and text.strip() else None,
            "url": url if from_search else unique[0]["url"], "url_de_busqueda": from_search,
            "fuente_hondurena": is_honduran_store(_host(url) if from_search else source_host(unique[0])),
            "cita_con_numero": _number_cited(raw.get("valor"), independent), "confianza": confidence,
            "gtin_verificado": raw.get("gtin_verificado") is True, **base}


# ------------------------------------------------------------------ consulta


def brand_of(item: Mapping[str, object]) -> str:
    brand = item.get("brand")
    return " ".join(str(brand).split())[:60] if isinstance(brand, str) and brand.strip() else "(sin marca publicada)"


def build_prompt(item: Mapping[str, object]) -> str:
    """Sólo marca, nombre y GTIN: sin los tamaños de los súper, para no sesgar la búsqueda."""
    name = " ".join(str(item.get("name") or "").replace('"', "'").split())[:140]
    brand = brand_of(item)
    return PROMPT.format(brand="" if brand.startswith("(") else brand, name=name, gtin=item.get("gtin")).replace(
        "producto  ", "producto ")


class ClaudeWebSearch:
    """Claude con la herramienta ``web_search`` por el SDK oficial (reintentos del SDK)."""

    name = "claude"

    def __init__(self, key: str = "", *, model: str = DEFAULT_MODEL, client: object | None = None) -> None:
        if client is None:
            import anthropic  # sólo en el workflow; las pruebas inyectan un cliente falso

            client = anthropic.Anthropic(api_key=key, max_retries=4, timeout=180)
        self.client = client
        self.model = model
        self.usage = {"entrada": 0, "salida": 0, "busquedas": 0}
        self.last_error: str | None = None

    def search(self, item: Mapping[str, object]) -> dict[str, object]:
        try:
            response = self.client.messages.create(
                model=self.model, max_tokens=MAX_TOKENS,
                tools=[{"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": MAX_SEARCHES}],
                messages=[{"role": "user", "content": build_prompt(item)}],
            )
        except Exception as error:  # noqa: BLE001 - se clasifica por código HTTP
            status = getattr(error, "status_code", None)
            self.last_error = f"{status or type(error).__name__}: {' '.join(str(error).split())[:300]}"
            if status in (400, 401, 403, 404, 429):
                raise StopRun(f"claude_http_{status}") from error
            raise
        read = read_response(response)
        usage = read["usage"]
        self.usage["entrada"] += int(usage.get("input_tokens") or 0)
        self.usage["salida"] += int(usage.get("output_tokens") or 0)
        self.usage["busquedas"] += int(read["searches"])
        return read


class GeminiWebSearch(Gemini):
    """Gemini con Grounding with Google Search (mismo manejo de cuota y reintentos que la IA de fotos).

    Prueba como mucho ``GEMINI_MAX_MODELS`` modelos: si el primero no tiene cuota, recorrer todos
    sólo gasta minutos esperando 429."""

    name = "gemini"

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.usage = {"entrada": 0, "salida": 0, "busquedas": 0}
        self.errors_by_model: dict[str, str] = {}

    def pick_model(self) -> str:
        model = super().pick_model()
        self.candidates = self.candidates[:GEMINI_MAX_MODELS]
        return model

    def _next_model(self) -> bool:
        if self.model and self.last_error:
            self.errors_by_model[self.model] = self.last_error
        return super()._next_model()

    def search(self, item: Mapping[str, object]) -> dict[str, object]:
        body = {"contents": [{"role": "user", "parts": [{"text": build_prompt(item)}]}],
                "tools": [{"google_search": {}}], "generationConfig": {"temperature": 0}}
        while True:
            try:
                answer = self._request(f"{API_BASE}models/{self.pick_model()}:generateContent", body)
                break
            except (TemporarilyUnavailable, QuotaExceeded, ModelUnavailable) as error:
                if not self._next_model():
                    raise StopRun("cuota_agotada" if isinstance(error, QuotaExceeded) else str(error)) from error
            except SuggestionError as error:  # llave inválida, permiso, petición rechazada
                raise StopRun(str(error)) from error
        read = read_gemini(answer)
        self.usage["entrada"] += int(read["usage"].get("input_tokens") or 0)
        self.usage["salida"] += int(read["usage"].get("output_tokens") or 0)
        self.usage["busquedas"] += int(read["searches"])
        return read


def off_code(gtin: str) -> str:
    """GTIN-14 de la cola → código que usa Open Food Facts (EAN-13 si el primer dígito es 0)."""
    return gtin[1:] if len(gtin) == 14 and gtin.startswith("0") else gtin


class OpenFoodFactsLookup:
    """Open Food Facts por GTIN (gratis, sin llave, datos abiertos). Una consulta exacta por código:
    no hay búsqueda ni modelo, así que no inventa; sólo usa ``product_quantity`` con unidad de masa
    o volumen. Respeta el límite público (≤ 100 lecturas por minuto) con la pausa de la corrida."""

    name = "off"
    model = "openfoodfacts-api-v2"

    def __init__(self, *, opener: Callable = urllib.request.urlopen) -> None:
        self.opener = opener
        self.usage = {"entrada": 0, "salida": 0, "busquedas": 0}
        self.last_error: str | None = None

    def search(self, item: Mapping[str, object]) -> dict[str, object]:
        code = off_code(str(item.get("gtin")))
        request = urllib.request.Request(f"{OFF_API}{code}.json?fields={OFF_FIELDS}",
                                         headers={"User-Agent": OFF_USER_AGENT})
        try:
            with self.opener(request, timeout=30) as response:
                document = json.loads(response.read(2_000_000).decode("utf-8"))
        except urllib.error.HTTPError as error:
            self.last_error = f"{error.code}: off"
            if error.code == 404:
                document = {"status": 0}
            elif error.code == 429:
                raise StopRun("off_http_429") from error
            else:
                raise
        self.usage["busquedas"] += 1
        return read_off(document, code)


def read_off(document: object, code: str) -> dict[str, object]:
    """Respuesta de Open Food Facts → misma forma que una búsqueda citada (fuente = la ficha del producto)."""
    product = document.get("product") if isinstance(document, Mapping) and document.get("status") == 1 else None
    page = {"url": f"{OFF_PAGE}{code}", "title": "world.openfoodfacts.org"}
    if not isinstance(product, Mapping):
        return {"text": json.dumps({"encontrado": False}), "results": [], "citations": [], "errors": [],
                "searches": 1, "usage": {}}
    unit = OFF_UNITS.get(str(product.get("product_quantity_unit") or "").strip().lower())
    try:
        value = Decimal(str(product.get("product_quantity")))
    except ArithmeticError:
        value = Decimal(0)
    quantity = " ".join(str(product.get("quantity") or "").split())[:80]
    if unit is None or not value.is_finite() or value <= 0:
        answer = {"encontrado": False}
        return {"text": json.dumps(answer), "results": [page], "citations": [], "errors": [], "searches": 1,
                "usage": {}}
    number = format(value.normalize(), "f")
    cited = f"{quantity} · product_quantity {number} {unit}".strip(" ·")
    answer = {"encontrado": True, "valor": number, "unidad": unit, "texto": quantity or f"{number} {unit}",
              "url": page["url"], "confianza": "alta", "gtin_verificado": True}
    return {"text": json.dumps(answer), "results": [page], "citations": [{**page, "cited_text": cited[:150]}],
            "errors": [], "searches": 1, "usage": {}}


# ------------------------------------------------------------------ decisión


def matching_offers(item: Mapping[str, object], result: Mapping[str, object]) -> list[Mapping[str, object]]:
    if result.get("status") != "encontrado":
        return []
    found = _signature(Decimal(str(result["value"])), str(result["unit"]))
    return [offer for offer in item.get("offers") or ()
            if (own := offer_signature(offer)) is not None and same_size(own, found)]


def store_labels(offers: Sequence[Mapping[str, object]]) -> list[str]:
    labels: list[str] = []
    for offer in offers:
        label = STORE_NAMES.get(str(offer.get("store")), str(offer.get("store")))
        if label not in labels:
            labels.append(label)
    return labels


def verification_reasons(result: Mapping[str, object]) -> list[str]:
    """Por qué el dato web NO está verificado (vacío = verificado, sin mirar los súper)."""
    if result.get("status") != "encontrado":
        return [str(result.get("reason") or "no_encontrado")]
    reasons = []
    if result.get("confianza") not in ("alta", "media"):
        reasons.append(f"confianza_{result.get('confianza')}")
    if not result.get("url_de_busqueda"):
        reasons.append("url_no_vino_de_la_busqueda")
    if result.get("fuente_hondurena"):
        reasons.append("fuente_es_un_super_de_honduras")
    if not result.get("cita_con_numero"):
        reasons.append("ninguna_cita_independiente_trae_el_numero")
    return reasons


def auto_reasons(item: Mapping[str, object], result: Mapping[str, object]) -> list[str]:
    """Por qué NO se aprueba sola (vacío = se aprueba)."""
    reasons = verification_reasons(result)
    if result.get("status") == "encontrado" and not matching_offers(item, result):
        reasons.append("no_coincide_con_ningun_super")
    return reasons


def official_size(item: Mapping[str, object], result: Mapping[str, object]) -> tuple[str, str]:
    """Tamaño publicado que coincide con la web; si hay varias formas (566 g y 20 oz), la de más súper."""
    counts: dict[tuple[str, str], int] = {}
    for offer in matching_offers(item, result):
        key = (str(offer["value"]), str(offer["unit"]))
        counts[key] = counts.get(key, 0) + 1
    return max(counts, key=lambda key: (counts[key], key))


def provider_of(entry: Mapping[str, object]) -> str:
    provider = entry.get("proveedor")
    return provider if provider in ENGINE_BY_PROVIDER else "claude"


def decision_for(item: Mapping[str, object], result: Mapping[str, object]) -> dict[str, object]:
    value, unit = official_size(item, result)
    host = _host(str(result.get("url")))
    who = PROVIDER_LABEL[provider_of(result)]
    note = f"{who} (web, {host}): {result.get('text') or f'{value} {unit}'} — {result.get('url')}"
    return {"gtin": item["gtin"], "decision": "size", "value": value, "unit": unit, "note": note[:200]}


def web_annotation(item: Mapping[str, object], entry: Mapping[str, object]) -> dict[str, object]:
    web = {key: entry[key] for key in ("status", "value", "unit", "text", "url", "confianza", "fuentes", "reason",
                                       "modelo", "proveedor", "utc") if key in entry}
    web["coincide_con"] = store_labels(matching_offers(item, entry))
    reasons = auto_reasons(item, entry)
    web["auto"] = not reasons
    if reasons:
        web["motivos"] = reasons
        web["nota"] = MANUAL_NOTE
    return web


# ------------------------------------------------------------------ cola


def priority(items: Sequence[Mapping[str, object]], first: Sequence[str]) -> list[Mapping[str, object]]:
    """Primero los GTIN pedidos a mano y luego el orden de la cola (empates primero)."""
    wanted = {gtin: position for position, gtin in enumerate(first)}
    return sorted(items, key=lambda item: (0, wanted[item["gtin"]]) if item.get("gtin") in wanted else (1, 0))


def is_fresh(entry: Mapping[str, object] | None, now: datetime) -> bool:
    if not isinstance(entry, Mapping) or entry.get("prompt") != PROMPT_VERSION:
        return False
    if entry.get("status") == "encontrado":
        return True
    try:
        when = datetime.strptime(str(entry.get("utc")), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return False
    return now - when < timedelta(days=RETRY_UNRESOLVED_DAYS)


def search_new(items: Sequence[dict[str, object]], cache: dict[str, dict[str, object]], *,
               client: ClaudeWebSearch | None, limit: int, minutes: float, first: Sequence[str] = (),
               delay: float = DEFAULT_DELAY, sleep: Callable[[float], None] = time.sleep,
               clock: Callable[[], float] = time.monotonic, now: datetime,
               deadline: float | None = None) -> dict[str, object]:
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    stats = {"consultas": 0, "errores": 0}
    stop: str | None = None if client else "sin_llave"
    if stop is None and limit <= 0:
        stop = "sin_consultas"
    deadline = deadline if deadline is not None else clock() + minutes * 60
    failures = 0
    for item in priority(items, first):
        if stop is not None:
            break
        gtin = str(item.get("gtin"))
        if not _GTIN.fullmatch(gtin) or (is_fresh(cache.get(gtin), now) and gtin not in first):
            continue
        if stats["consultas"] >= limit:
            stop = "tope_de_consultas"
            break
        if clock() >= deadline:
            stop = "tiempo_agotado"
            break
        try:
            if stats["consultas"] or stats["errores"]:
                sleep(delay)
            read = client.search(item)
        except StopRun as error:
            stop = str(error)
            break
        except Exception:  # noqa: BLE001 - 5xx/529/red tras los reintentos del SDK: sigue con el siguiente
            stats["errores"] += 1
            failures += 1
            if failures >= MAX_CONSECUTIVE_FAILURES:
                stop = "no_disponible"
            continue
        failures = 0
        stats["consultas"] += 1
        cache[gtin] = {**parse_answer(read), "prompt": PROMPT_VERSION, "modelo": client.model,
                       "proveedor": client.name, "utc": stamp}
    return {**stats, "detenido_por": stop}


def annotate(queue: dict[str, object], cache: Mapping[str, Mapping[str, object]]
             ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Anota cada código con lo guardado y separa lo que se aprueba solo (sale de la cola)."""
    remaining: list[dict[str, object]] = []
    resolved: list[dict[str, object]] = []
    for item in queue.get("items") or ():
        if not isinstance(item, dict):
            continue
        entry = cache.get(str(item.get("gtin")))
        if not isinstance(entry, Mapping) or entry.get("prompt") != PROMPT_VERSION:
            item.pop("web", None)
            remaining.append(item)
            continue
        item["web"] = web_annotation(item, entry)
        (resolved if item["web"]["auto"] else remaining).append(item)
    return remaining, resolved


def audit_candidates(queue: Mapping[str, object]) -> list[dict[str, object]]:
    """Códigos que la IA ya resolvió con fotos (``auto_resueltas``) con su tamaño guardado."""
    found = []
    for item in queue.get("auto_resueltas") or ():
        ia = item.get("ia") if isinstance(item, dict) else None
        if isinstance(ia, Mapping) and ia.get("status") == "auto" and ia.get("value") and ia.get("unit") in UNITS:
            found.append(item)
    return found


def audit(candidates: Sequence[dict[str, object]], cache: Mapping[str, Mapping[str, object]]
          ) -> tuple[list[dict[str, object]], int]:
    """Lo resuelto por fotos contra la web: si una fuente verificada dice otro tamaño, vuelve a la cola
    manual como ``correccion_ia`` (la decisión de la IA sigue hasta que una persona decida)."""
    corrections: list[dict[str, object]] = []
    confirmed = 0
    for item in candidates:
        entry = cache.get(str(item.get("gtin")))
        if not isinstance(entry, Mapping) or entry.get("prompt") != PROMPT_VERSION or verification_reasons(entry):
            continue
        ia = item["ia"]
        decided = _signature(Decimal(str(ia["value"])), str(ia["unit"]))
        found = _signature(Decimal(str(entry["value"])), str(entry["unit"]))
        web = {**web_annotation(item, entry), "auto": False}
        if same_size(decided, found):
            item["web"] = {**web, "verifica": True}
            item["web"].pop("nota", None)
            item["web"].pop("motivos", None)
            confirmed += 1
            continue
        web["nota"] = (f"{CORRECTION_NOTE}: la IA guardó {ia['value']} {ia['unit']} por las fotos; "
                       f"la web dice {entry['value']} {entry['unit']}. Requiere revisión humana")
        corrections.append({"gtin": item["gtin"], "kind": "correccion_ia", "name": item.get("name"),
                            "brand": item.get("brand"), "offers": item.get("offers") or [], "ia": ia, "web": web})
    return corrections, confirmed


def mark_with_auto(document: dict[str, object], cache: Mapping[str, Mapping[str, object]]) -> None:
    """Cola de respaldo (si no se pudo guardar): mismas notas, pero lo automático sigue adentro."""
    for item in document.get("items") or ():
        entry = cache.get(str(item.get("gtin"))) if isinstance(item, dict) else None
        if isinstance(entry, Mapping) and entry.get("prompt") == PROMPT_VERSION:
            item["web"] = {**web_annotation(item, entry), "auto": False}


def load_cache(path: Path | None) -> dict[str, dict[str, object]]:
    if path is None or not path.is_file() or path.is_symlink():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    entries = document.get("entries") if isinstance(document, Mapping) and document.get("schema") == WEB_SCHEMA \
        else None
    if not isinstance(entries, Mapping):
        return {}
    return {str(key): dict(value) for key, value in entries.items()
            if _GTIN.fullmatch(str(key)) and isinstance(value, Mapping)}


def _write(path: Path, document: object) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    temporary.replace(path)


def auto_out_path(base: Path, provider: str) -> Path:
    """``auto-decisiones-web.json`` → ``auto-decisiones-web-gemini.json`` (un archivo por motor)."""
    return base.with_name(f"{base.stem}-{provider}{base.suffix}")


def build_client(provider: str, model: str) -> ClaudeWebSearch | GeminiWebSearch | OpenFoodFactsLookup | None:
    if provider == "off":
        return OpenFoodFactsLookup()
    if provider == "gemini":
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        return GeminiWebSearch(key) if key else None
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    return ClaudeWebSearch(key, model=model) if key else None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--queue", type=Path, required=True, help="cola.json a anotar (se reescribe)")
    parser.add_argument("--queue-with-auto", type=Path, required=True,
                        help="cola de respaldo con lo automático todavía adentro (se crea o se anota)")
    parser.add_argument("--auto-out", type=Path, required=True,
                        help="base de las decisiones automáticas: se escribe <base>-gemini.json y <base>-claude.json")
    parser.add_argument("--cache-in", type=Path, help="web-tamanos.json de la corrida anterior")
    parser.add_argument("--cache-out", type=Path, required=True)
    parser.add_argument("--provider", choices=sorted(ENGINE_BY_PROVIDER), default="off")
    parser.add_argument("--max", type=int, default=DEFAULT_MAX, help="consultas máximas a la cola en esta corrida")
    parser.add_argument("--audit-max", type=int, default=0, help="consultas máximas para re-auditar lo resuelto")
    parser.add_argument("--minutes", type=float, default=DEFAULT_MINUTES)
    parser.add_argument("--first", default="", help="GTIN a buscar primero, separados por coma")
    parser.add_argument("--model", default=os.environ.get("CLAUDE_MODEL", "").strip() or DEFAULT_MODEL)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    args = parser.parse_args(argv)
    first = [code.strip() for code in args.first.split(",") if code.strip()]
    if not 0 <= args.max <= HARD_MAX or not 0 <= args.audit_max <= HARD_MAX or not 0 <= args.minutes <= 120 \
            or args.delay < 0.5 or len(first) > 50 or not all(_GTIN.fullmatch(code) for code in first) \
            or not _MODEL.fullmatch(args.model):
        raise SystemExit("parametros_invalidos")
    queue = json.loads(args.queue.read_text(encoding="utf-8"))
    with_auto = json.loads(args.queue_with_auto.read_text(encoding="utf-8")) \
        if args.queue_with_auto.is_file() and not args.queue_with_auto.is_symlink() else json.loads(json.dumps(queue))
    cache = load_cache(args.cache_in)
    client = build_client(args.provider, args.model) if args.max + args.audit_max > 0 else None
    now = datetime.now(UTC)
    deadline = time.monotonic() + args.minutes * 60
    items = [item for item in queue.get("items") or () if isinstance(item, dict)]
    summary = search_new(items, cache, client=client, limit=args.max, minutes=args.minutes, first=first,
                         delay=args.delay, now=now, deadline=deadline)
    candidates = audit_candidates(queue)
    audit_stop = summary["detenido_por"] if summary["detenido_por"] not in (None, "tope_de_consultas",
                                                                             "sin_consultas") else None
    audit_stats = search_new(candidates, cache, client=None if audit_stop else client, limit=args.audit_max,
                             minutes=args.minutes, delay=args.delay, now=now, deadline=deadline)
    if client is None and args.max + args.audit_max <= 0:
        summary["detenido_por"] = audit_stats["detenido_por"] = "sin_consultas"
    remaining, resolved = annotate(queue, cache)
    corrections, confirmed = audit(candidates, cache)
    mark_with_auto(with_auto, cache)
    queue["items"] = [*remaining, *corrections]
    with_auto["items"] = [*(with_auto.get("items") or []), *corrections]
    queue["counts"] = {kind: sum(item.get("kind") == kind for item in queue["items"])
                       for kind in ("empate", "atipico", "correccion_ia")}
    with_auto["counts"] = queue["counts"]
    web_items = [item["web"] for item in [*remaining, *resolved] if "web" in item]
    queue["web"] = {**summary, "proveedor": args.provider, "aprobados_solos": len(resolved),
                    "en_revision": sum(not web["auto"] for web in web_items),
                    "con_sugerencia": sum(web["status"] == "encontrado" and not web["auto"] for web in web_items),
                    "auditoria": {"consultas": audit_stats["consultas"], "errores": audit_stats["errores"],
                                  "detenido_por": audit_stop or audit_stats["detenido_por"],
                                  "confirmados": confirmed, "a_correccion": len(corrections),
                                  "pendientes": sum(1 for item in candidates if str(item["gtin"]) not in cache)},
                    "modelo": client.model if client else None,
                    "uso": dict(client.usage) if client else None,
                    "ultimo_error_api": client.last_error if client else None,
                    "errores_por_modelo": dict(getattr(client, "errors_by_model", {}) or {}),
                    "actualizado_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
    with_auto["web"] = queue["web"]
    for provider in ENGINE_BY_PROVIDER:
        decisions = [decision_for(item, cache[str(item["gtin"])]) for item in resolved
                     if provider_of(cache[str(item["gtin"])]) == provider]
        _write(auto_out_path(args.auto_out, provider), {"schema": PAYLOAD_SCHEMA, "decisions": decisions})
    _write(args.cache_out, {"schema": WEB_SCHEMA, "entries": dict(sorted(cache.items()))})
    _write(args.queue_with_auto, with_auto)
    _write(args.queue, queue)
    print(json.dumps({key: value for key, value in queue["web"].items() if key != "actualizado_utc"},
                     ensure_ascii=False))
    for item in resolved:
        print(f"auto {item['gtin']}: {item['web'].get('value')} {item['web'].get('unit')} — {item['web'].get('url')}")
    for item in corrections:
        print(f"correccion {item['gtin']}: {item['web'].get('nota')} — {item['web'].get('url')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
