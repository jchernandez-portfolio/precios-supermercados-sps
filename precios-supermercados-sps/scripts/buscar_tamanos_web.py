#!/usr/bin/env python3
"""Búsqueda web del tamaño oficial (Gemini con Google Search) SÓLO para la cola de revisión.

Para cada código que sigue en ``cola.json`` (nunca el catálogo completo) pregunta a
Gemini, con la herramienta ``google_search`` (Grounding with Google Search):

    Busca en internet el peso oficial del producto con GTIN [X] y nombre [Y].
    Devuelve el peso y la URL de la fuente.

La respuesta se valida igual que una lectura de foto (valor y unidad en rango) y se
anota en el código como ``item["web"]`` con la URL de la fuente y las páginas que
Google usó (``groundingMetadata``). El panel la muestra como «🌐 Sugerencia Web: 566 g»:
un clic sólo llena el formulario; **nunca se guarda sola** (no hay decisión automática
desde la web).

Prioridad: los GTIN de ``--first``, luego los empates y luego los que la IA de fotos no
pudo resolver (no legible, sin foto, conflicto, discrepancia). Lo consultado se guarda
en ``web-tamanos.json`` por GTIN; sólo se pregunta lo nuevo (lo «no encontrado» se
reintenta a los 30 días). Una consulta a la vez con pausa; si se acaba la cuota de
búsqueda se detiene y guarda lo avanzado. La llave sólo viaja en el encabezado
``x-goog-api-key``, nunca en URLs ni en logs.

    python scripts/buscar_tamanos_web.py --queue /tmp/cola/cola.json \
        --cache-in cola/web-tamanos.json --cache-out /tmp/cola/web-tamanos.json --max 40
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from sugerir_tamanos_ia import (  # noqa: E402
    API_BASE,
    STORE_NAMES,
    Gemini,
    ModelUnavailable,
    QuotaExceeded,
    SuggestionError,
    TemporarilyUnavailable,
    _int,
    _json_from_text,
    _signature,
    offer_signature,
    parse_reading,
    same_size,
)

WEB_SCHEMA = "rpi-size-web/v1"
WEB_FILE = "web-tamanos.json"
PROMPT_VERSION = 1
DEFAULT_MAX = 40
HARD_MAX = 200
DEFAULT_DELAY = 6.5
RETRY_NOT_FOUND_DAYS = 30
MAX_SOURCES = 3
MAX_URL = 500
MAX_TEXT = 120
PRIORITY_IA = ("no_legible", "sin_foto", "conflicto", "discrepancia")
_GTIN = re.compile(r"\d{8,14}")
PROMPT = """Busca en internet el peso oficial del producto con GTIN {gtin} y nombre "{name}". \
Devuelve el peso y la URL de la fuente.

Reglas:
- Usa la búsqueda de Google. Prefiere la página del fabricante o una tienda que muestre el GTIN/UPC.
- El peso es el contenido neto del empaque (Peso neto / Net Wt / Contenido neto), no el peso de envío.
- Si el empaque trae varias unidades, da el contenido total y dilo en "texto".
- Si no encuentras este producto exacto (mismo GTIN o mismo nombre y marca), responde encontrado=false.

Responde SOLO con un objeto JSON, sin texto adicional:
{{"encontrado": true, "valor": 566, "unidad": "g", "texto": "NET WT 20 OZ (566 g)", "url": "https://..."}}
unidad es una de: g, kg, ml, l, oz, fl_oz, lb, unit. Usa null en valor/unidad/url si no aplica."""


# ------------------------------------------------------------------ respuesta


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


def grounding_sources(answer: object) -> list[dict[str, str]]:
    """Páginas que Google usó para responder (``groundingChunks[].web``), sin repetir."""
    try:
        chunks = answer["candidates"][0]["groundingMetadata"]["groundingChunks"]
    except (KeyError, IndexError, TypeError):
        return []
    sources: list[dict[str, str]] = []
    for chunk in chunks if isinstance(chunks, list) else ():
        web = chunk.get("web") if isinstance(chunk, Mapping) else None
        url = clean_url(web.get("uri")) if isinstance(web, Mapping) else None
        if url is None or any(source["url"] == url for source in sources):
            continue
        title = web.get("title")
        title = " ".join(str(title).split())[:80] if isinstance(title, str) and title.strip() else _host(url)
        sources.append({"title": title, "url": url})
    return sources[:MAX_SOURCES]


def answer_text(answer: object) -> str:
    try:
        parts = answer["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError):
        return ""
    return "".join(str(part.get("text") or "") for part in parts if isinstance(part, Mapping))


def parse_answer(answer: object) -> dict[str, object]:
    """Respuesta de Gemini (no confiable) → ``encontrado`` con tamaño válido o ``no_encontrado``."""
    raw = _json_from_text(answer_text(answer))
    sources = grounding_sources(answer)
    if not isinstance(raw, Mapping) or not isinstance(raw.get("encontrado"), bool):
        return {"status": "no_encontrado", "reason": "respuesta_invalida", "fuentes": sources}
    if not raw["encontrado"]:
        return {"status": "no_encontrado", "reason": "no_encontrado", "fuentes": sources}
    reading = parse_reading({"legible": True, "same_product": True, "value": raw.get("valor"),
                             "unit": raw.get("unidad"), "label_text": raw.get("texto")})
    if reading["status"] != "leido":
        return {"status": "no_encontrado", "reason": "tamano_invalido", "fuentes": sources}
    url = clean_url(raw.get("url"))
    if not sources and url is None:
        return {"status": "no_encontrado", "reason": "sin_fuente", "fuentes": []}
    text = raw.get("texto")
    text = " ".join(str(text).split())[:MAX_TEXT] if isinstance(text, str) and text.strip() else None
    hosts = {_host(source["url"]) for source in sources} | {source["title"].lower().removeprefix("www.")
                                                            for source in sources}
    return {"status": "encontrado", "value": reading["value"], "unit": reading["unit"], "text": text,
            "url": url or sources[0]["url"], "verificada": bool(url) and _host(url) in hosts,
            "fuentes": sources}


# ------------------------------------------------------------------ consulta


class WebSearch(Gemini):
    """Gemini con la herramienta ``google_search`` (mismo manejo de cuota, reintentos y modelos)."""

    name = "gemini_web"

    def search(self, gtin: str, name: str) -> object:
        prompt = PROMPT.format(gtin=gtin, name=" ".join(name.replace('"', "'").split())[:120])
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "tools": [{"google_search": {}}],
            "generationConfig": {"temperature": 0},
        }
        while True:
            try:
                answer = self._request(f"{API_BASE}models/{self.pick_model()}:generateContent", body)
                break
            except (TemporarilyUnavailable, QuotaExceeded, ModelUnavailable):
                if not self._next_model():
                    raise
        usage = answer.get("usageMetadata") if isinstance(answer, Mapping) else None
        if isinstance(usage, Mapping):
            self.usage["entrada"] += _int(usage.get("promptTokenCount"))
            self.usage["salida"] += _int(usage.get("candidatesTokenCount"))
        return answer


# ------------------------------------------------------------------ cola


def matching_stores(item: Mapping[str, object], result: Mapping[str, object]) -> list[str]:
    """Súper cuyo tamaño publicado coincide con lo encontrado en la web."""
    if result.get("status") != "encontrado":
        return []
    found = _signature(Decimal(str(result["value"])), str(result["unit"]))
    stores: list[str] = []
    for offer in item.get("offers") or ():
        signature = offer_signature(offer)
        label = STORE_NAMES.get(str(offer.get("store")), str(offer.get("store")))
        if signature is not None and same_size(signature, found) and label not in stores:
            stores.append(label)
    return stores


def priority(items: Sequence[Mapping[str, object]], first: Sequence[str]) -> list[Mapping[str, object]]:
    """Orden de consulta: ``--first``, empates, lo que la IA de fotos no resolvió; el resto no se busca."""
    wanted = {gtin: position for position, gtin in enumerate(first)}

    def rank(item: Mapping[str, object]) -> tuple[int, int] | None:
        gtin = str(item.get("gtin"))
        if gtin in wanted:
            return (0, wanted[gtin])
        if item.get("kind") == "empate":
            return (1, 0)
        status = (item.get("ia") or {}).get("status") if isinstance(item.get("ia"), Mapping) else None
        if status in PRIORITY_IA:
            return (2, PRIORITY_IA.index(status))
        return None

    ranked = [(order, index, item) for index, item in enumerate(items) if (order := rank(item)) is not None]
    return [item for _, _, item in sorted(ranked, key=lambda entry: (entry[0], entry[1]))]


def is_fresh(entry: Mapping[str, object] | None, now: datetime) -> bool:
    if not isinstance(entry, Mapping) or entry.get("prompt") != PROMPT_VERSION:
        return False
    if entry.get("status") == "encontrado":
        return True
    try:
        when = datetime.strptime(str(entry.get("utc")), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return False
    return now - when < timedelta(days=RETRY_NOT_FOUND_DAYS)


def annotate(queue: dict[str, object], cache: dict[str, dict[str, object]], *, client: WebSearch | None,
             limit: int, first: Sequence[str] = (), delay: float = DEFAULT_DELAY,
             sleep: Callable[[float], None] = time.sleep, now: datetime | None = None) -> dict[str, object]:
    """Busca lo nuevo (hasta ``limit``) y anota ``item["web"]`` con lo guardado; devuelve el resumen."""
    now = now or datetime.now(UTC)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    items = [item for item in queue.get("items") or () if isinstance(item, dict)]
    stats = {"consultas": 0, "encontrados": 0, "no_encontrados": 0, "errores": 0}
    stop: str | None = None if client else "sin_llave"
    if limit <= 0 and stop is None:
        stop = "sin_consultas"
    for item in priority(items, first):
        if stop is not None:
            break
        gtin = str(item.get("gtin"))
        if not _GTIN.fullmatch(gtin) or (is_fresh(cache.get(gtin), now) and gtin not in first):
            continue
        if stats["consultas"] >= limit:
            stop = "tope_de_consultas"
            break
        try:
            if stats["consultas"]:
                sleep(delay)
            answer = client.search(gtin, str(item.get("name") or ""))
        except QuotaExceeded:
            stop = "cuota_agotada"
            break
        except SuggestionError as error:
            stop = str(error)
            break
        except (OSError, ValueError):
            stats["errores"] += 1
            continue
        stats["consultas"] += 1
        result = parse_answer(answer)
        cache[gtin] = {**result, "prompt": PROMPT_VERSION, "modelo": client.model, "utc": stamp}
    shown = 0
    for item in items:
        entry = cache.get(str(item.get("gtin")))
        if not isinstance(entry, Mapping) or entry.get("prompt") != PROMPT_VERSION:
            item.pop("web", None)
            continue
        web = {key: entry[key] for key in ("status", "value", "unit", "text", "url", "verificada", "fuentes",
                                           "reason", "modelo", "utc") if key in entry}
        web["coincide_con"] = matching_stores(item, entry)
        item["web"] = web
        shown += 1
        stats["encontrados" if entry.get("status") == "encontrado" else "no_encontrados"] += 1
    return {**stats, "en_cola": shown, "detenido_por": stop, "modelo": client.model if client else None,
            "reintentos": client.retries if client else 0,
            "ultimo_error_api": client.last_error if client else None}


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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--queue", type=Path, required=True, help="cola.json a anotar (se reescribe)")
    parser.add_argument("--cache-in", type=Path, help="web-tamanos.json de la corrida anterior")
    parser.add_argument("--cache-out", type=Path, required=True)
    parser.add_argument("--max", type=int, default=DEFAULT_MAX, help="consultas máximas en esta corrida")
    parser.add_argument("--first", default="", help="GTIN a buscar primero, separados por coma")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    args = parser.parse_args(argv)
    first = [code.strip() for code in args.first.split(",") if code.strip()]
    if not 0 <= args.max <= HARD_MAX or args.delay < 1 or len(first) > 50 \
            or not all(_GTIN.fullmatch(code) for code in first):
        raise SystemExit("parametros_invalidos")
    queue = json.loads(args.queue.read_text(encoding="utf-8"))
    cache = load_cache(args.cache_in)
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    client = WebSearch(key) if key else None
    now = datetime.now(UTC)
    summary = annotate(queue, cache, client=client, limit=args.max, first=first, delay=args.delay, now=now)
    queue["web"] = {**summary, "actualizado_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
    _write(args.cache_out, {"schema": WEB_SCHEMA, "entries": dict(sorted(cache.items()))})
    _write(args.queue, queue)
    print(json.dumps(summary, ensure_ascii=False))
    for gtin in first:
        entry = cache.get(gtin)
        if entry:
            print("web_" + gtin + "=" + json.dumps({key: entry.get(key) for key in ("status", "value", "unit",
                                                                                     "text", "url", "fuentes")},
                                                   ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
