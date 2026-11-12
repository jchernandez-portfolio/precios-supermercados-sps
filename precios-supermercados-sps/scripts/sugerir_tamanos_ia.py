#!/usr/bin/env python3
"""Sugerencias de tamaño con IA de visión para la cola de revisión (``cola.json``).

Para cada código en conflicto toma las fotos públicas de sus ofertas (bucket R2 de
Súper Compras), le pide a Gemini (Flash vigente) que lea el "Peso neto" /
"Contenido neto" impreso en el empaque y anota el resultado en el item:

- ``{"status": "sugerencia", "value": "240", "unit": "g", "text": "PESO NETO 240 g"}``
  sólo si la etiqueta es legible, la foto es del mismo producto y el tamaño leído
  coincide con alguno de los tamaños publicados (si no coincide con ninguno, lo
  más probable es que la foto sea de otra presentación);
- ``{"status": "no_legible", "reason": "ilegible" | "otra_presentacion"}`` en otro caso;
- ``{"status": "sin_foto"}`` si ninguna oferta tiene foto.

Es sólo una sugerencia: el panel la muestra como botón y una persona decide.
Las respuestas se guardan en ``ia-tamanos.json`` (rama ``revision-tamanos``) por
código + fotos; sólo se consulta lo nuevo. Cortesía con la API: una consulta a la
vez, pausa entre consultas, y ante un límite de cuota (429) se detiene y guarda:
la siguiente corrida sigue donde quedó. La llave llega por ``GEMINI_API_KEY`` y
sólo viaja en el encabezado ``x-goog-api-key``. Sin llave, sólo aplica lo guardado.
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

from auditar_identidad_productos import CATALOG_BASE, load_cities  # noqa: E402
from precios_supermercados.product_homologation import PresentationSignature  # noqa: E402
from precios_supermercados.size_decisions import canonical_size, match_official  # noqa: E402

CACHE_SCHEMA = "rpi-size-ai/v1"
PROMPT_VERSION = 1
CACHE_FILE = "ia-tamanos.json"
PHOTO_BASE = "https://pub-0dcb2204775c484dbe6681e659eee236.r2.dev/fotos/v2/"
PHOTO_INDEX_SCHEMA = "super-compras-product-images/v2"
API_BASE = "https://generativelanguage.googleapis.com/v1beta/"
PREFERRED_MODEL = "gemini-flash-latest"
MAX_PHOTOS = 3
DEFAULT_DELAY = 6.5  # ~9 consultas por minuto: dentro de la capa gratuita
DEFAULT_MINUTES = 25
UNITS_FROM_MODEL = {"g": ("g", 1), "kg": ("g", 1000), "ml": ("ml", 1), "l": ("ml", 1000), "oz": ("oz", 1),
                    "fl_oz": ("oz", 1), "lb": ("lb", 1), "unit": ("unit", 1)}
DIMENSION = {"g": "mass_g", "ml": "volume_ml", "oz": "ounce", "unit": "count"}
USER_AGENT = "precios-sps-sugerencias-ia/1.0 (+https://github.com/jchernandez-portfolio/precios-supermercados-sps)"
PROMPT = """Eres un asistente que lee etiquetas de productos de supermercado.
Producto esperado: "{name}".
Mira las fotos. Busca el contenido neto impreso en el empaque ("Peso neto", "Contenido neto",
"Net wt", "Net contents", "Cont. neto"). Responde sólo con lo que se lee en la imagen; no adivines
por el nombre del producto.
- legible: true sólo si el contenido neto se lee con claridad en alguna foto.
- same_product: false si la foto muestra otra marca, otro sabor/variante u otra presentación.
- value y unit: el número y la unidad tal como están impresos (unit en g, kg, ml, l, oz, fl_oz, lb o unit).
  Si hay paquete de varias unidades, el contenido total del paquete.
- label_text: el texto exacto leído (máximo 60 caracteres)."""
RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "legible": {"type": "BOOLEAN"},
        "same_product": {"type": "BOOLEAN"},
        "value": {"type": "NUMBER", "nullable": True},
        "unit": {"type": "STRING", "nullable": True, "enum": list(UNITS_FROM_MODEL)},
        "label_text": {"type": "STRING", "nullable": True},
    },
    "required": ["legible", "same_product"],
}


class SuggestionError(RuntimeError):
    """Error de la API que obliga a detener la corrida (se guarda lo avanzado)."""


class QuotaExceeded(SuggestionError):
    """La API respondió 429: se acabó la cuota por ahora."""


# ------------------------------------------------------------------ fotos


def photo_urls(cities: Mapping[str, Sequence[object]], index: Mapping[str, Mapping[str, object]]) -> dict[str, str]:
    """``"walmart:51"`` → URL de la foto de 800 px de su tarjeta (si tiene)."""
    result: dict[str, str] = {}
    for rows in cities.values():
        for row in rows:
            entry = index.get(row.key)
            digest = entry.get("h") if isinstance(entry, Mapping) else None
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{6,64}", digest) or entry.get("none"):
                continue
            url = f"{PHOTO_BASE}800/{row.key[:2]}/{row.key}-{digest}.webp"
            for store, product_id in row.offers:
                result.setdefault(f"{store}:{product_id}", url)
    return result


def item_photos(item: Mapping[str, object], photos: Mapping[str, str]) -> list[str]:
    urls: list[str] = []
    for offer in item.get("offers") or ():
        url = photos.get(str(offer.get("ref") or ""))
        if url and url not in urls:
            urls.append(url)
    return urls[:MAX_PHOTOS]


def cache_key(urls: Sequence[str]) -> str:
    return f"p{PROMPT_VERSION}:" + "|".join(sorted(url.rsplit("/", 1)[-1] for url in urls))


# ------------------------------------------------------------------- modelo


def resolve_model(models: Sequence[str]) -> str:
    """El alias ``gemini-flash-latest`` si existe; si no, el Flash numerado más nuevo (no Lite)."""
    names = {name.removeprefix("models/") for name in models}
    if PREFERRED_MODEL in names:
        return PREFERRED_MODEL
    numbered = []
    for name in names:
        match = re.fullmatch(r"gemini-(\d+(?:\.\d+)?)-flash", name)
        if match:
            numbered.append((Decimal(match.group(1)), name))
    if not numbered:
        raise SuggestionError("modelo_flash_no_disponible")
    return max(numbered)[1]


def interpret(raw: object, offers: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Respuesta del modelo (no confiable) → sugerencia validada o "no legible"."""
    if not isinstance(raw, Mapping) or not isinstance(raw.get("legible"), bool):
        return {"status": "no_legible", "reason": "respuesta_invalida"}
    text = raw.get("label_text")
    text = " ".join(str(text).split())[:60] if isinstance(text, str) else None
    if not raw.get("legible"):
        return {"status": "no_legible", "reason": "ilegible"}
    if raw.get("same_product") is not True:
        return {"status": "no_legible", "reason": "otra_presentacion", "text": text}
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
    official = PresentationSignature(DIMENSION[canonical], total, 1, total)
    published = []
    for offer in offers:
        if offer.get("value") and offer.get("unit") in DIMENSION:
            amount = Decimal(str(offer["value"]))
            published.append(PresentationSignature(DIMENSION[str(offer["unit"])], amount, 1, amount))
    if not any(match_official(official, signature)[0] for signature in published):
        return {"status": "no_legible", "reason": "otra_presentacion", "text": text}
    return {"status": "sugerencia", "value": format(total, "f"), "unit": canonical, "text": text}


class Gemini:
    def __init__(self, key: str, *, opener: Callable = urllib.request.urlopen) -> None:
        self.key = key
        self.opener = opener
        self.model: str | None = None

    def _request(self, url: str, body: Mapping[str, object] | None = None) -> object:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, data=data, method="GET" if body is None else "POST", headers={
            "x-goog-api-key": self.key, "Content-Type": "application/json", "User-Agent": USER_AGENT,
        })
        try:
            with self.opener(request, timeout=90) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code == 429:
                raise QuotaExceeded("cuota_agotada_429") from error
            raise SuggestionError(f"gemini_http_{error.code}") from error

    def pick_model(self) -> str:
        if self.model is None:
            listing = self._request(API_BASE + "models?pageSize=1000")
            models = [str(item.get("name", "")) for item in (listing.get("models") or ())
                      if isinstance(item, Mapping) and "generateContent" in (item.get("supportedGenerationMethods") or ())]
            self.model = resolve_model(models)
        return self.model

    def read_label(self, name: str, images: Sequence[bytes]) -> object:
        parts: list[dict[str, object]] = [{"text": PROMPT.format(name=name.replace('"', "'")[:120])}]
        parts += [{"inline_data": {"mime_type": "image/webp", "data": base64.b64encode(image).decode("ascii")}}
                  for image in images]
        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json",
                                 "responseSchema": RESPONSE_SCHEMA},
        }
        answer = self._request(f"{API_BASE}models/{self.pick_model()}:generateContent", body)
        try:
            return json.loads(answer["candidates"][0]["content"]["parts"][0]["text"])
        except (KeyError, IndexError, TypeError, ValueError):
            return None


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


def annotate(queue: dict[str, object], photos: Mapping[str, str], cache: dict[str, dict[str, object]], *,
             client: Gemini | None, fetch_image: Callable[[str], bytes], minutes: float, delay: float,
             sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
             now: str = "") -> dict[str, object]:
    """Anota ``item["ia"]`` con lo guardado y consulta lo nuevo mientras quede tiempo y cuota."""
    stats = {"sugerencia": 0, "no_legible": 0, "sin_foto": 0, "pendiente": 0, "consultas": 0, "errores": 0}
    stop: str | None = None if client else "sin_llave"
    deadline = clock() + minutes * 60
    for item in queue.get("items") or ():
        gtin = str(item.get("gtin"))
        urls = item_photos(item, photos)
        if not urls:
            item["ia"] = {"status": "sin_foto"}
            stats["sin_foto"] += 1
            continue
        key = cache_key(urls)
        entry = cache.get(gtin)
        if (entry is None or entry.get("key") != key) and stop is None and clock() < deadline:
            try:
                if stats["consultas"]:
                    sleep(delay)
                images = [fetch_image(url) for url in urls]
                raw = client.read_label(str(item.get("name") or ""), images)
                stats["consultas"] += 1
                entry = {"key": key, "raw": raw if isinstance(raw, Mapping) else None, "at": now}
                cache[gtin] = entry
            except QuotaExceeded:
                stop = "cuota_agotada"
            except SuggestionError as error:
                stop = str(error)
            except (OSError, ValueError):
                stats["errores"] += 1  # una foto que no bajó no detiene la corrida
        if entry is None or entry.get("key") != key:
            stats["pendiente"] += 1
            continue
        result = interpret(entry.get("raw"), item.get("offers") or ())
        item["ia"] = result
        stats[str(result["status"])] += 1
    if stop is None and clock() >= deadline:
        stop = "tiempo_agotado"
    return {**stats, "detenido_por": stop, "modelo": client.model if client else None}


def _fetch_json(url: str) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - URLs fijas https
        return json.loads(response.read().decode("utf-8"))


def _fetch_image(url: str) -> bytes:
    if not url.startswith(PHOTO_BASE):
        raise ValueError("foto_fuera_del_bucket")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - bucket público fijo
        data = response.read(5_000_000)
    if not data.startswith(b"RIFF") or data[8:12] != b"WEBP":
        raise ValueError("foto_no_webp")
    return data


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--queue", type=Path, required=True, help="cola.json a anotar (se reescribe)")
    parser.add_argument("--cache-in", type=Path, help="ia-tamanos.json de la corrida anterior")
    parser.add_argument("--cache-out", type=Path, required=True)
    parser.add_argument("--minutes", type=float, default=DEFAULT_MINUTES)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    args = parser.parse_args(argv)
    if args.delay < 1 or not 0 <= args.minutes <= 150:
        raise SystemExit("parametros_invalidos")
    queue = json.loads(args.queue.read_text(encoding="utf-8"))
    cache = load_cache(args.cache_in) if args.cache_in else {}
    cities = load_cities(_fetch_json, CATALOG_BASE)
    index_document = _fetch_json(PHOTO_BASE + "index.json")
    index = index_document.get("items") if isinstance(index_document, Mapping) and \
        index_document.get("schema") == PHOTO_INDEX_SCHEMA else None
    photos = photo_urls(cities, index if isinstance(index, Mapping) else {})
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    client = Gemini(key) if key else None
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    summary = annotate(queue, photos, cache, client=client, fetch_image=_fetch_image,
                       minutes=args.minutes, delay=args.delay, now=now)
    queue["ia"] = {**summary, "actualizado_utc": now}
    args.queue.write_text(json.dumps(queue, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    args.cache_out.write_text(json.dumps({"schema": CACHE_SCHEMA, "entries": dict(sorted(cache.items()))},
                                         ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
