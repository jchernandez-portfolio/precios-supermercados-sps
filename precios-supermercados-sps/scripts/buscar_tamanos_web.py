#!/usr/bin/env python3
"""Claude con búsqueda web resuelve la cola de tamaños; a mano sólo queda lo imposible.

Para cada código de ``cola.json`` (nunca el catálogo completo), en el orden de la cola,
pregunta a Claude (SDK oficial ``anthropic``, herramienta de servidor ``web_search``)
por el peso oficial del fabricante, dándole marca, nombre, GTIN y los tamaños que
publica cada súper. Claude responde con sus fuentes citadas y un JSON final.

**Auto-aprobación** (``engine:claude_auto``, igual que la IA de fotos: nunca sobre una
decisión humana) sólo si se cumplen TODAS:

1. Claude dice ``encontrado`` con confianza ``alta``;
2. la URL de la fuente salió de la búsqueda (no inventada);
3. alguna cita textual de la búsqueda (``cited_text``) contiene el número encontrado;
4. ese tamaño coincide con el que publica al menos un súper (misma comparación que el
   motor: contenido total, 2 % o 1.5 unidades, onza líquida o de peso).

Se guarda el tamaño publicado que coincide (el de más súper si hay varios) y el código
sale de la cola. Todo lo demás se queda con la nota «🤖 Claude no pudo verificar el
dato en la web. Requiere revisión humana» (si encontró algo, el panel lo muestra como
sugerencia con su fuente; un clic sólo llena el formulario).

Lo consultado se guarda en ``web-tamanos.json`` por GTIN; sólo se pregunta lo nuevo (lo
no resuelto se reintenta a los 30 días). Una consulta a la vez, con tope de consultas y
de minutos; si la API responde 429 o rechaza la llave, se detiene y guarda lo avanzado.
La llave sólo viaja en un encabezado (la pone el SDK), nunca en URLs ni logs.

    python scripts/buscar_tamanos_web.py --queue /tmp/cola/cola.json \
        --queue-with-auto /tmp/cola/cola-con-auto.json --auto-out /tmp/cola/auto-decisiones-web.json \
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

from precios_supermercados.size_decisions import PAYLOAD_SCHEMA  # noqa: E402
from sugerir_tamanos_ia import (  # noqa: E402
    STORE_NAMES,
    _json_from_text,
    _signature,
    offer_signature,
    parse_reading,
    same_size,
)

WEB_SCHEMA = "rpi-size-web/v2"
WEB_FILE = "web-tamanos.json"
PROMPT_VERSION = 2
ENGINE = "claude_auto"
DEFAULT_MODEL = "claude-sonnet-5-5"
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
MANUAL_NOTE = "🤖 Claude no pudo verificar el dato en la web. Requiere revisión humana"
_GTIN = re.compile(r"\d{8,14}")
_MODEL = re.compile(r"claude-[a-z0-9-]{1,60}")
PROMPT = """Eres un verificador de datos de productos de supermercado en Honduras.

Busca en internet el peso o contenido neto OFICIAL del fabricante para este producto:
- Marca: {brand}
- Nombre: {name}
- GTIN (código de barras): {gtin}
- Tamaños que publican los supermercados (pueden estar mal): {sizes}

Reglas:
- Usa la búsqueda web. Prefiere la página del fabricante o una tienda que muestre este mismo GTIN/UPC.
- Es el contenido neto del empaque (Peso neto / Net Wt / Contenido neto), no el peso de envío.
- Si el empaque trae varias unidades, da el contenido TOTAL.
- No elijas un tamaño sólo porque lo publica un supermercado: reporta lo que dice la fuente y cítala.
- Si no hallas este producto exacto (mismo GTIN, o misma marca y nombre), o las fuentes se contradicen,
  responde encontrado=false o confianza "baja".

Explica en 1 o 2 frases con la cita de la fuente y termina con UNA línea con este JSON:
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


def read_response(response: object) -> dict[str, object]:
    """Texto final, páginas que devolvió la búsqueda, citas y errores de la herramienta."""
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
    """Respuesta de Claude (no confiable) → ``encontrado`` (validado) o ``no_encontrado``."""
    results = list(read.get("results") or [])
    citations = list(read.get("citations") or [])
    sources = [{"title": item["title"] or _host(item["url"]), "url": item["url"]} for item in citations + results]
    unique: list[dict[str, str]] = []
    for source in sources:
        if all(seen["url"] != source["url"] for seen in unique):
            unique.append(source)
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
    known = {item["url"] for item in results} | {item["url"] for item in citations}
    url = clean_url(raw.get("url"))
    verified_url = url if url in known else None
    if not known:
        return {"status": "no_encontrado", "reason": "sin_fuente", **base}
    text = raw.get("texto")
    confidence = raw.get("confianza") if raw.get("confianza") in ("alta", "media", "baja") else "baja"
    return {"status": "encontrado", "value": reading["value"], "unit": reading["unit"],
            "text": " ".join(str(text).split())[:MAX_TEXT] if isinstance(text, str) and text.strip() else None,
            "url": verified_url or unique[0]["url"], "url_de_busqueda": verified_url is not None,
            "cita_con_numero": _number_cited(raw.get("valor"), citations), "confianza": confidence,
            "gtin_verificado": raw.get("gtin_verificado") is True, **base}


# ------------------------------------------------------------------ consulta


def brand_of(item: Mapping[str, object]) -> str:
    brand = item.get("brand")
    return " ".join(str(brand).split())[:60] if isinstance(brand, str) and brand.strip() else "(sin marca publicada)"


def published_sizes(item: Mapping[str, object]) -> str:
    sizes = []
    for offer in item.get("offers") or ():
        if offer.get("size"):
            store = STORE_NAMES.get(str(offer.get("store")), str(offer.get("store")))
            sizes.append(f"{store}: {offer['size']}")
    return "; ".join(sizes) or "ninguno"


def build_prompt(item: Mapping[str, object]) -> str:
    name = " ".join(str(item.get("name") or "").replace('"', "'").split())[:140]
    return PROMPT.format(brand=brand_of(item), name=name, gtin=item.get("gtin"), sizes=published_sizes(item))


class ClaudeWebSearch:
    """Claude con la herramienta ``web_search`` por el SDK oficial (reintentos del SDK)."""

    name = "claude_web"

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


def auto_reasons(item: Mapping[str, object], result: Mapping[str, object]) -> list[str]:
    """Por qué NO se aprueba sola (vacío = se aprueba)."""
    if result.get("status") != "encontrado":
        return [str(result.get("reason") or "no_encontrado")]
    reasons = []
    if result.get("confianza") != "alta":
        reasons.append(f"confianza_{result.get('confianza')}")
    if not result.get("url_de_busqueda"):
        reasons.append("url_no_vino_de_la_busqueda")
    if not result.get("cita_con_numero"):
        reasons.append("ninguna_cita_trae_el_numero")
    if not matching_offers(item, result):
        reasons.append("no_coincide_con_ningun_super")
    return reasons


def official_size(item: Mapping[str, object], result: Mapping[str, object]) -> tuple[str, str]:
    """Tamaño publicado que coincide con la web; si hay varias formas (566 g y 20 oz), la de más súper."""
    counts: dict[tuple[str, str], int] = {}
    for offer in matching_offers(item, result):
        key = (str(offer["value"]), str(offer["unit"]))
        counts[key] = counts.get(key, 0) + 1
    return max(counts, key=lambda key: (counts[key], key))


def decision_for(item: Mapping[str, object], result: Mapping[str, object]) -> dict[str, object]:
    value, unit = official_size(item, result)
    host = _host(str(result.get("url")))
    note = f"Claude (web, {host}): {result.get('text') or f'{value} {unit}'} — {result.get('url')}"
    return {"gtin": item["gtin"], "decision": "size", "value": value, "unit": unit, "note": note[:200]}


def web_annotation(item: Mapping[str, object], entry: Mapping[str, object]) -> dict[str, object]:
    web = {key: entry[key] for key in ("status", "value", "unit", "text", "url", "confianza", "fuentes", "reason",
                                       "modelo", "utc") if key in entry}
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
               clock: Callable[[], float] = time.monotonic, now: datetime) -> dict[str, object]:
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    stats = {"consultas": 0, "errores": 0}
    stop: str | None = None if client else "sin_llave"
    if stop is None and limit <= 0:
        stop = "sin_consultas"
    deadline = clock() + minutes * 60
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
        cache[gtin] = {**parse_answer(read), "prompt": PROMPT_VERSION, "modelo": client.model, "utc": stamp}
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--queue", type=Path, required=True, help="cola.json a anotar (se reescribe)")
    parser.add_argument("--queue-with-auto", type=Path, required=True,
                        help="cola de respaldo con lo automático todavía adentro (se crea o se anota)")
    parser.add_argument("--auto-out", type=Path, required=True, help="decisiones automáticas (engine:claude_auto)")
    parser.add_argument("--cache-in", type=Path, help="web-tamanos.json de la corrida anterior")
    parser.add_argument("--cache-out", type=Path, required=True)
    parser.add_argument("--max", type=int, default=DEFAULT_MAX, help="consultas máximas en esta corrida")
    parser.add_argument("--minutes", type=float, default=DEFAULT_MINUTES)
    parser.add_argument("--first", default="", help="GTIN a buscar primero, separados por coma")
    parser.add_argument("--model", default=os.environ.get("CLAUDE_MODEL", "").strip() or DEFAULT_MODEL)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    args = parser.parse_args(argv)
    first = [code.strip() for code in args.first.split(",") if code.strip()]
    if not 0 <= args.max <= HARD_MAX or not 0 <= args.minutes <= 120 or args.delay < 0.5 or len(first) > 50 \
            or not all(_GTIN.fullmatch(code) for code in first) or not _MODEL.fullmatch(args.model):
        raise SystemExit("parametros_invalidos")
    queue = json.loads(args.queue.read_text(encoding="utf-8"))
    with_auto = json.loads(args.queue_with_auto.read_text(encoding="utf-8")) \
        if args.queue_with_auto.is_file() and not args.queue_with_auto.is_symlink() else json.loads(json.dumps(queue))
    cache = load_cache(args.cache_in)
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    client = ClaudeWebSearch(key, model=args.model) if key and args.max > 0 else None
    now = datetime.now(UTC)
    items = [item for item in queue.get("items") or () if isinstance(item, dict)]
    summary = search_new(items, cache, client=client, limit=args.max, minutes=args.minutes, first=first,
                         delay=args.delay, now=now)
    if client is None and summary["detenido_por"] == "sin_llave" and args.max <= 0:
        summary["detenido_por"] = "sin_consultas"
    remaining, resolved = annotate(queue, cache)
    mark_with_auto(with_auto, cache)
    queue["items"] = remaining
    queue["counts"] = {kind: sum(item.get("kind") == kind for item in remaining) for kind in ("empate", "atipico")}
    web_items = [item["web"] for item in [*remaining, *resolved] if "web" in item]
    queue["web"] = {**summary, "aprobados_solos": len(resolved),
                    "en_revision": sum(not web["auto"] for web in web_items),
                    "con_sugerencia": sum(web["status"] == "encontrado" and not web["auto"] for web in web_items),
                    "modelo": client.model if client else None,
                    "uso": dict(client.usage) if client else None,
                    "ultimo_error_api": client.last_error if client else None,
                    "actualizado_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
    with_auto["web"] = queue["web"]
    decisions = [decision_for(item, cache[str(item["gtin"])]) for item in resolved]
    _write(args.auto_out, {"schema": PAYLOAD_SCHEMA, "decisions": decisions})
    _write(args.cache_out, {"schema": WEB_SCHEMA, "entries": dict(sorted(cache.items()))})
    _write(args.queue_with_auto, with_auto)
    _write(args.queue, queue)
    print(json.dumps({key: value for key, value in queue["web"].items() if key != "actualizado_utc"},
                     ensure_ascii=False))
    for item in resolved:
        print(f"auto {item['gtin']}: {item['web'].get('value')} {item['web'].get('unit')} — {item['web'].get('url')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
