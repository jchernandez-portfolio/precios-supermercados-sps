#!/usr/bin/env python3
"""IA de visión para la cola de revisión de tamaños: lee cada foto y resuelve sola lo claro.

Para cada código en conflicto manda a Gemini (Flash vigente) las fotos que ya
tenemos en el bucket R2 (``offer["foto"]``, las mismas que ve el panel), cada una
rotulada con su súper, y pide leer el "Peso neto" / "Contenido neto" de cada foto.
Al modelo no se le dan los títulos ni los tamaños publicados (para que no los
repita): la comparación foto contra título la hace este script. Con esas lecturas:

- **auto**: todas las lecturas legibles dicen lo mismo y ese tamaño es el que
  publica la mayoría (más de la mitad) de las ofertas → decisión automática
  (``engine:gemini_auto``), el código sale de la cola. Sólo con un modelo Flash
  completo (no Lite) y nunca sobre una decisión humana.
- **conflicto**: las fotos dicen tamaños distintos, o lo leído coincide sólo con
  una minoría (p. ej. Mazola 1 kg contra 908 g) → revisión manual.
- **discrepancia**: lo leído no coincide con ningún título → revisión manual.
- **no_legible** / **sin_foto** → revisión manual.

Cada oferta lleva su propia lectura (``offer["ia"]``) para que el panel muestre
"🤖 IA leyó: 1 kg" al lado de la foto, en verde si coincide con su título y en
alerta si no. Las respuestas se guardan en ``ia-tamanos.json`` por código + fotos;
sólo se consulta lo nuevo. Una consulta a la vez con pausa; ante 5xx reintenta y
cambia de modelo; ante 429 espera un minuto, reintenta y, si sigue, cambia a
Flash-Lite (sólo sugiere, no resuelve solo) o se detiene y guarda lo avanzado.
La llave llega por ``GEMINI_API_KEY`` y sólo viaja en el encabezado ``x-goog-api-key``.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from precios_supermercados.product_homologation import PresentationSignature  # noqa: E402
from precios_supermercados.size_decisions import PAYLOAD_SCHEMA, canonical_size, match_official  # noqa: E402

CACHE_SCHEMA = "rpi-size-ai/v2"
PROMPT_VERSION = 2
CACHE_FILE = "ia-tamanos.json"
AUTO_FILE = "auto-decisiones.json"
ENGINE_ACTOR = "gemini_auto"
PHOTO_BASE = "https://pub-0dcb2204775c484dbe6681e659eee236.r2.dev/fotos/v2/"
_PHOTO_NAME = re.compile(r"[0-9a-f]{24}-[0-9a-f]{6,64}")
API_BASE = "https://generativelanguage.googleapis.com/v1beta/"
PREFERRED_MODEL = "gemini-flash-latest"
MAX_PHOTOS = 6
MAX_AUTO_HISTORY = 1500
DEFAULT_DELAY = 6.5
DEFAULT_MINUTES = 25
TRANSIENT_CODES = frozenset({500, 502, 503, 504})
RETRY_WAITS = (15, 45)
QUOTA_WAIT = 60
MAX_CONSECUTIVE_UNAVAILABLE = 5
UNITS_FROM_MODEL = {"g": ("g", 1), "kg": ("g", 1000), "ml": ("ml", 1), "l": ("ml", 1000), "oz": ("oz", 1),
                    "fl_oz": ("oz", 1), "lb": ("lb", 1), "unit": ("unit", 1)}
DIMENSION = {"g": "mass_g", "ml": "volume_ml", "oz": "ounce", "unit": "count"}
USER_AGENT = "precios-sps-sugerencias-ia/2.0 (+https://github.com/jchernandez-portfolio/precios-supermercados-sps)"
PROMPT = """Eres un asistente que lee etiquetas de productos de supermercado.
Todas las fotos deberían ser del mismo producto: "{name}".
Cada foto viene de un supermercado:
{photos}
En CADA foto busca el contenido neto impreso en el empaque ("Peso neto", "Contenido neto",
"Net wt", "Net contents", "Cont. neto"). Responde sólo con lo que se lee en la imagen; no
adivines por el nombre del producto.
Para cada foto devuelve:
- photo: el número de la foto.
- legible: true sólo si el contenido neto se lee con claridad.
- same_product: false si la foto muestra otra marca u otro sabor/variante.
- value y unit: número y unidad tal como están impresos (unit en g, kg, ml, l, oz, fl_oz, lb o unit).
  Si es un paquete de varias unidades, el contenido total del paquete.
- label_text: el texto exacto leído (máximo 60 caracteres)."""
RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "readings": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "photo": {"type": "INTEGER"},
                    "legible": {"type": "BOOLEAN"},
                    "same_product": {"type": "BOOLEAN"},
                    "value": {"type": "NUMBER", "nullable": True},
                    "unit": {"type": "STRING", "nullable": True, "enum": list(UNITS_FROM_MODEL)},
                    "label_text": {"type": "STRING", "nullable": True},
                },
                "required": ["photo", "legible", "same_product"],
            },
        },
    },
    "required": ["readings"],
}
STORE_NAMES = {"la_colonia": "La Colonia", "colonial": "Colonial", "walmart": "Walmart", "pricesmart": "PriceSmart",
               "comisariato_los_andes": "Los Andes", "paiz": "Paiz"}


class SuggestionError(RuntimeError):
    """Error de la API que obliga a detener la corrida (se guarda lo avanzado)."""


class QuotaExceeded(SuggestionError):
    """La API respondió 429 dos veces: se acabó la cuota por ahora."""


class TemporarilyUnavailable(SuggestionError):
    """La API siguió sin responder (5xx) después de los reintentos."""


# ------------------------------------------------------------------ fotos


def photo_url(name: str, size: int = 800) -> str:
    return f"{PHOTO_BASE}{size}/{name[:2]}/{name}.webp"


def item_photos(item: Mapping[str, object]) -> list[str]:
    """Fotos distintas de las ofertas del código (nombres ``<tarjeta>-<huella>``), en orden."""
    names: list[str] = []
    for offer in item.get("offers") or ():
        name = offer.get("foto")
        if isinstance(name, str) and _PHOTO_NAME.fullmatch(name) and name not in names:
            names.append(name)
    return names[:MAX_PHOTOS]


def cache_key(names: Sequence[str]) -> str:
    return f"p{PROMPT_VERSION}:" + "|".join(sorted(names))


def photo_lines(item: Mapping[str, object], names: Sequence[str]) -> str:
    lines = []
    for number, name in enumerate(names, 1):
        stores = [STORE_NAMES.get(str(offer.get("store")), str(offer.get("store")))
                  for offer in item.get("offers") or () if offer.get("foto") == name]
        lines.append(f"Foto {number} = {', '.join(stores)}")
    return "\n".join(lines)


# ------------------------------------------------------------------- lectura


def _signature(value: Decimal, unit: str) -> PresentationSignature:
    return PresentationSignature(DIMENSION[unit], value, 1, value)


def offer_signature(offer: Mapping[str, object]) -> PresentationSignature | None:
    if not offer.get("value") or offer.get("unit") not in DIMENSION:
        return None
    try:
        return _signature(Decimal(str(offer["value"])), str(offer["unit"]))
    except InvalidOperation:
        return None


def same_size(left: PresentationSignature, right: PresentationSignature) -> bool:
    return match_official(left, right)[0] or match_official(right, left)[0]


def parse_reading(raw: object) -> dict[str, object]:
    """Lectura de una foto (no confiable) → ``leido`` con tamaño válido o ``no_legible``."""
    if not isinstance(raw, Mapping) or not isinstance(raw.get("legible"), bool):
        return {"status": "no_legible", "reason": "respuesta_invalida"}
    text = raw.get("label_text")
    text = " ".join(str(text).split())[:60] if isinstance(text, str) else None
    if not raw.get("legible"):
        return {"status": "no_legible", "reason": "ilegible"}
    if raw.get("same_product") is not True:
        return {"status": "no_legible", "reason": "otro_producto", "text": text}
    unit = UNITS_FROM_MODEL.get(str(raw.get("unit")))
    try:
        value = Decimal(str(raw.get("value")))
    except (InvalidOperation, ValueError):
        value = Decimal(0)
    if unit is None or not value.is_finite() or value <= 0 or value > 100000:
        return {"status": "no_legible", "reason": "ilegible", "text": text}
    total, canonical = canonical_size(value * unit[1], unit[0])
    total = total.quantize(Decimal("0.01")).normalize()
    if canonical == "unit" and total != total.to_integral_value():
        return {"status": "no_legible", "reason": "ilegible", "text": text}
    return {"status": "leido", "value": format(total, "f"), "unit": canonical, "text": text}


def readings_by_photo(raw: object, names: Sequence[str]) -> dict[str, dict[str, object]]:
    """Respuesta del modelo → lectura validada por foto (las fotos sin respuesta quedan ilegibles)."""
    items = raw.get("readings") if isinstance(raw, Mapping) else None
    result = {name: {"status": "no_legible", "reason": "sin_respuesta"} for name in names}
    for entry in items if isinstance(items, list) else ():
        number = entry.get("photo") if isinstance(entry, Mapping) else None
        if isinstance(number, int) and not isinstance(number, bool) and 1 <= number <= len(names):
            result[names[number - 1]] = parse_reading(entry)
    return result


def majority_offer(offers: Sequence[Mapping[str, object]]) -> Mapping[str, object] | None:
    """Oferta representativa del tamaño que publica MÁS de la mitad de las ofertas con tamaño."""
    sized = [(offer, signature) for offer in offers if (signature := offer_signature(offer)) is not None]
    for offer, signature in sized:
        agreeing = sum(1 for _, other in sized if same_size(signature, other))
        if agreeing * 2 > len(sized):
            return offer
    return None


def resolve_item(item: dict[str, object], readings: Mapping[str, Mapping[str, object]], *,
                 auto_allowed: bool) -> dict[str, object]:
    """Anota cada oferta con su lectura y decide: auto, sugerencia, conflicto, discrepancia o no legible."""
    offers = list(item.get("offers") or ())
    read: list[tuple[dict[str, object], PresentationSignature]] = []
    for offer in offers:
        reading = readings.get(str(offer.get("foto")))
        if reading is None:
            continue
        annotated = dict(reading)
        if reading["status"] == "leido":
            signature = _signature(Decimal(str(reading["value"])), str(reading["unit"]))
            own = offer_signature(offer)
            annotated["coincide"] = bool(own and same_size(signature, own))
            read.append((annotated, signature))
        offer["ia"] = annotated
    if not readings:
        return {"status": "sin_foto"}
    if not read:
        reasons = {str(r.get("reason")) for r in readings.values()}
        return {"status": "no_legible", "reason": "otro_producto" if reasons == {"otro_producto"} else "ilegible"}
    first, signature = read[0]
    if not all(same_size(signature, other) for _, other in read[1:]):
        return {"status": "conflicto", "reason": "fotos_distintas", "value": first["value"], "unit": first["unit"],
                "text": first.get("text")}
    published = [own for offer in offers if (own := offer_signature(offer)) is not None]
    matching = [offer for offer in offers if (own := offer_signature(offer)) is not None and same_size(signature, own)]
    summary = {"value": first["value"], "unit": first["unit"], "text": first.get("text")}
    if not matching:
        return {"status": "discrepancia", **summary}
    majority = majority_offer(offers)
    if majority is None or not same_size(signature, offer_signature(majority)) or len(matching) * 2 <= len(published):
        return {"status": "conflicto", "reason": "minoria", **summary}
    official = {"value": str(majority["value"]), "unit": str(majority["unit"]), "text": first.get("text")}
    return {"status": "auto" if auto_allowed else "sugerencia", **official}


# ------------------------------------------------------------------- modelo


def model_candidates(models: Sequence[str]) -> list[str]:
    """Flash en orden de preferencia: alias vigente, numerados (más nuevo primero) y, como
    respaldo, los Flash-Lite."""
    names = {name.removeprefix("models/") for name in models}
    ranked: list[str] = [PREFERRED_MODEL] if PREFERRED_MODEL in names else []
    for pattern in (r"gemini-(\d+(?:\.\d+)?)-flash", r"gemini-(\d+(?:\.\d+)?)-flash-lite"):
        numbered = sorted(((Decimal(match.group(1)), name) for name in names
                           if (match := re.fullmatch(pattern, name))), reverse=True)
        if pattern.endswith("lite") and "gemini-flash-lite-latest" in names:
            ranked.append("gemini-flash-lite-latest")
        ranked += [name for _, name in numbered]
    if not ranked:
        raise SuggestionError("modelo_flash_no_disponible")
    return ranked


def resolve_model(models: Sequence[str]) -> str:
    return model_candidates(models)[0]


def is_lite(model: str | None) -> bool:
    return bool(model) and "lite" in str(model)


class Gemini:
    def __init__(self, key: str, *, opener: Callable = urllib.request.urlopen,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.key = key
        self.opener = opener
        self.sleep = sleep
        self.model: str | None = None
        self.candidates: list[str] = []
        self.retries = 0
        self.last_error: str | None = None

    def _request(self, url: str, body: Mapping[str, object] | None = None) -> object:
        data = None if body is None else json.dumps(body).encode("utf-8")
        attempt = 0
        quota_waited = False
        while True:
            request = urllib.request.Request(url, data=data, method="GET" if body is None else "POST", headers={
                "x-goog-api-key": self.key, "Content-Type": "application/json", "User-Agent": USER_AGENT,
            })
            try:
                with self.opener(request, timeout=90) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                self.last_error = _error_message(error)
                if error.code == 429:
                    if quota_waited:  # límite por minuto ya descartado: es la cuota del día
                        raise QuotaExceeded("cuota_agotada_429") from error
                    quota_waited = True
                    self.retries += 1
                    self.sleep(QUOTA_WAIT)
                    continue
                if error.code not in TRANSIENT_CODES:
                    raise SuggestionError(f"gemini_http_{error.code}") from error
                if attempt == len(RETRY_WAITS):
                    raise TemporarilyUnavailable(f"gemini_http_{error.code}") from error
            except (urllib.error.URLError, TimeoutError) as error:
                if attempt == len(RETRY_WAITS):
                    raise TemporarilyUnavailable("gemini_sin_conexion") from error
            self.retries += 1
            self.sleep(RETRY_WAITS[attempt])
            attempt += 1

    def pick_model(self) -> str:
        if self.model is None:
            listing = self._request(API_BASE + "models?pageSize=1000")
            models = [str(item.get("name", "")) for item in (listing.get("models") or ())
                      if isinstance(item, Mapping) and "generateContent" in (item.get("supportedGenerationMethods") or ())]
            self.candidates = model_candidates(models)
            self.model = self.candidates[0]
        return self.model

    def _next_model(self) -> bool:
        position = self.candidates.index(self.model) if self.model in self.candidates else len(self.candidates)
        if position + 1 >= len(self.candidates):
            return False
        self.model = self.candidates[position + 1]
        return True

    def read_labels(self, name: str, photos: str, images: Sequence[bytes]) -> object:
        prompt = PROMPT.format(name=name.replace('"', "'")[:120], photos=photos)
        parts: list[dict[str, object]] = [{"text": prompt}]
        for number, image in enumerate(images, 1):
            parts.append({"text": f"Foto {number}:"})
            parts.append({"inline_data": {"mime_type": "image/webp", "data": base64.b64encode(image).decode("ascii")}})
        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json",
                                 "responseSchema": RESPONSE_SCHEMA},
        }
        while True:
            try:
                answer = self._request(f"{API_BASE}models/{self.pick_model()}:generateContent", body)
                break
            except (TemporarilyUnavailable, QuotaExceeded):
                if not self._next_model():
                    raise
        try:
            return json.loads(answer["candidates"][0]["content"]["parts"][0]["text"])
        except (KeyError, IndexError, TypeError, ValueError):
            return None


def _error_message(error: urllib.error.HTTPError) -> str:
    """Mensaje corto del error de la API (sin encabezados: la llave nunca aparece)."""
    try:
        document = json.loads(error.read(4000).decode("utf-8", "replace"))
        message = document.get("error", {}).get("message") if isinstance(document, Mapping) else None
    except (OSError, ValueError, AttributeError):
        message = None
    return f"{error.code}: {' '.join(str(message or '').split())[:160]}"


# ------------------------------------------------------------------- corrida


def load_cache(path: Path) -> dict[str, dict[str, object]]:
    if not path.is_file() or path.is_symlink():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    entries = document.get("entries") if isinstance(document, Mapping) and document.get("schema") == CACHE_SCHEMA else None
    return {str(key): dict(value) for key, value in entries.items() if isinstance(value, Mapping)} if isinstance(entries, Mapping) else {}


def auto_decision(item: Mapping[str, object]) -> dict[str, object]:
    ia = item["ia"]
    note = f"Gemini: {ia.get('text')}" if ia.get("text") else "Gemini: tamaño de la mayoría confirmado en la foto"
    return {"gtin": item["gtin"], "decision": "size", "value": ia["value"], "unit": ia["unit"], "note": note[:200]}


def annotate(queue: dict[str, object], cache: dict[str, dict[str, object]], *,
             client: Gemini | None, fetch_image: Callable[[str], bytes], minutes: float, delay: float,
             sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
             now: str = "") -> tuple[dict[str, object], list[dict[str, object]]]:
    """Lee lo nuevo mientras quede tiempo y cuota, anota la cola y separa lo que se resuelve solo."""
    stats = {"auto": 0, "sugerencia": 0, "conflicto": 0, "discrepancia": 0, "no_legible": 0, "sin_foto": 0,
             "pendiente": 0, "consultas": 0, "errores": 0, "no_disponible": 0}
    stop: str | None = None if client else "sin_llave"
    deadline = clock() + minutes * 60
    unavailable_in_a_row = 0
    remaining: list[dict[str, object]] = []
    resolved: list[dict[str, object]] = []
    for item in queue.get("items") or ():
        gtin = str(item.get("gtin"))
        names = item_photos(item)
        if not names:
            item["ia"] = {"status": "sin_foto"}
            stats["sin_foto"] += 1
            remaining.append(item)
            continue
        key = cache_key(names)
        entry = cache.get(gtin)
        if (entry is None or entry.get("key") != key) and stop is None and clock() < deadline:
            try:
                if stats["consultas"]:
                    sleep(delay)
                images = [fetch_image(photo_url(name)) for name in names]
                raw = client.read_labels(str(item.get("name") or ""), photo_lines(item, names), images)
                stats["consultas"] += 1
                unavailable_in_a_row = 0
                entry = {"key": key, "raw": raw if isinstance(raw, Mapping) else None, "model": client.model, "at": now}
                cache[gtin] = entry
            except QuotaExceeded:
                stop = "cuota_agotada"
            except TemporarilyUnavailable as error:
                stats["no_disponible"] += 1
                unavailable_in_a_row += 1
                if unavailable_in_a_row >= MAX_CONSECUTIVE_UNAVAILABLE:
                    stop = f"no_disponible:{error}"
            except SuggestionError as error:
                stop = str(error)
            except (OSError, ValueError):
                stats["errores"] += 1  # una foto que no bajó no detiene la corrida
        if entry is None or entry.get("key") != key:
            stats["pendiente"] += 1
            remaining.append(item)
            continue
        readings = readings_by_photo(entry.get("raw"), names)
        item["ia"] = resolve_item(item, readings, auto_allowed=not is_lite(entry.get("model")))
        stats[str(item["ia"]["status"])] += 1
        (resolved if item["ia"]["status"] == "auto" else remaining).append(item)
    if stop is None and clock() >= deadline:
        stop = "tiempo_agotado"
    queue["items"] = remaining
    summary = {**stats, "detenido_por": stop, "modelo": client.model if client else None,
               "reintentos": client.retries if client else 0,
               "ultimo_error_api": client.last_error if client else None}
    return summary, resolved


def merge_history(resolved: Sequence[Mapping[str, object]], cache: Mapping[str, Mapping[str, object]],
                  engine_decided: Sequence[str], now: str) -> list[dict[str, object]]:
    """Historial de lo que resolvió la IA (para auditar en el panel): lo nuevo y lo anterior
    que sigue decidido por la IA (si una persona lo cambió, ya no aparece)."""
    still = set(engine_decided)
    history = [{**item, "resuelto_utc": now} for item in resolved]
    seen = {item["gtin"] for item in history}
    previous = sorted(((gtin, entry) for gtin, entry in cache.items()
                       if isinstance(entry.get("auto_item"), Mapping) and gtin in still and gtin not in seen),
                      key=lambda pair: str(pair[1]["auto_item"].get("resuelto_utc", "")), reverse=True)
    history += [dict(entry["auto_item"]) for _, entry in previous]
    return history[:MAX_AUTO_HISTORY]


def _fetch_image(url: str) -> bytes:
    if not url.startswith(PHOTO_BASE):
        raise ValueError("foto_fuera_del_bucket")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - bucket público fijo
        data = response.read(5_000_000)
    if not data.startswith(b"RIFF") or data[8:12] != b"WEBP":
        raise ValueError("foto_no_webp")
    return data


def _write(path: Path, document: object) -> None:
    path.write_text(json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--queue", type=Path, required=True, help="cola.json a anotar (se reescribe)")
    parser.add_argument("--cache-in", type=Path, help="ia-tamanos.json de la corrida anterior")
    parser.add_argument("--cache-out", type=Path, required=True)
    parser.add_argument("--auto-out", type=Path, required=True, help="decisiones automáticas para guardar")
    parser.add_argument("--queue-with-auto", type=Path, required=True,
                        help="cola con lo automático todavía adentro (si no se pudo guardar)")
    parser.add_argument("--minutes", type=float, default=DEFAULT_MINUTES)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    args = parser.parse_args(argv)
    if args.delay < 1 or not 0 <= args.minutes <= 150:
        raise SystemExit("parametros_invalidos")
    queue = json.loads(args.queue.read_text(encoding="utf-8"))
    cache = load_cache(args.cache_in) if args.cache_in else {}
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    client = Gemini(key) if key else None
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    summary, resolved = annotate(queue, cache, client=client, fetch_image=_fetch_image,
                                 minutes=args.minutes, delay=args.delay, now=now)
    for item in resolved:
        cache[str(item["gtin"])]["auto_item"] = {**item, "resuelto_utc": now}
    history = merge_history(resolved, cache, queue.get("decididas_ia") or (), now)
    queue["ia"] = {**summary, "actualizado_utc": now}
    queue["counts"] = {kind: sum(item.get("kind") == kind for item in queue["items"]) for kind in ("empate", "atipico")}
    # Si guardar lo automático falla, se publica esta cola: lo "auto" vuelve como sugerencia.
    pending = [{**item, "ia": {**item["ia"], "status": "sugerencia"}} for item in resolved]
    new = {item["gtin"] for item in resolved}
    with_auto = {**queue, "items": [*pending, *queue["items"]],
                 "auto_resueltas": [item for item in history if item["gtin"] not in new]}
    queue["auto_resueltas"] = history
    _write(args.queue_with_auto, with_auto)
    _write(args.queue, queue)
    _write(args.auto_out, {"schema": PAYLOAD_SCHEMA, "decisions": [auto_decision(item) for item in resolved]})
    _write(args.cache_out, {"schema": CACHE_SCHEMA, "entries": dict(sorted(cache.items()))})
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
