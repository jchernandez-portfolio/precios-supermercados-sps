#!/usr/bin/env python3
"""Prepara una tanda de la cola de tamaños para que Claude la revise en una sesión (sólo lectura).

Baja ``cola.json`` (pública, rama ``revision-tamanos``), toma los siguientes N códigos
pendientes (empates primero; sin decisión ni auto-resolución; salta los ya vistos en
``revision-claude/vistos.json``) y arma UNA imagen JPG por código: la foto 800 px de
cada oferta en cuadrícula, rotulada con súper, título y tamaño publicado ("foto
compartida" si la foto vino de otro súper). Escribe las imágenes y ``lote.json`` en
``revision-claude/`` (ignorada por git). No escribe en Turso ni lanza workflows.

    python scripts/preparar_revision_claude.py --n 25 --primero 00012656001065
"""
from __future__ import annotations

import argparse
import io
import json
import re
import textwrap
import time
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUEUE_URL = "https://raw.githubusercontent.com/jchernandez-portfolio/precios-supermercados-sps/revision-tamanos/cola.json"
PHOTO_BASE = "https://pub-0dcb2204775c484dbe6681e659eee236.r2.dev/fotos/v2/"
_PHOTO_NAME = re.compile(r"[0-9a-f]{24}-[0-9a-f]{6,64}")
_GTIN = re.compile(r"\d{14}")
OUT_DIR = ROOT / "revision-claude"
SEEN_FILE = "vistos.json"
BATCH_FILE = "lote.json"
DEFAULT_N = 25
TILE = 560
CAPTION = 130
HEADER = 70
MAX_COLUMNS = 3
PHOTO_DELAY = 0.3
USER_AGENT = "precios-sps-revision-claude/1.0 (+https://github.com/jchernandez-portfolio/precios-supermercados-sps)"
STORE_NAMES = {"la_colonia": "La Colonia", "colonial": "Colonial", "walmart": "Walmart", "pricesmart": "PriceSmart",
               "comisariato_los_andes": "Los Andes", "paiz": "Paiz"}
FONTS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf",
         "C:/Windows/Fonts/arial.ttf")


# -------------------------------------------------------------------- selección


def load_seen(path: Path) -> dict[str, dict[str, object]]:
    if not path.is_file() or path.is_symlink():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    seen = document.get("vistos") if isinstance(document, Mapping) else None
    return {str(key): dict(value) for key, value in seen.items() if isinstance(value, Mapping)} \
        if isinstance(seen, Mapping) else {}


def pending(queue: Mapping[str, object], seen: Mapping[str, object], n: int, first: Sequence[str] = ()) -> list[dict]:
    """Siguientes N códigos: los pedidos primero, luego empates y atípicos; sin auto ni ya vistos."""
    items = [item for item in queue.get("items") or () if isinstance(item, Mapping)
             and _GTIN.fullmatch(str(item.get("gtin") or ""))
             and (item.get("ia") or {}).get("status") != "auto" and str(item["gtin"]) not in seen]
    wanted = {gtin: index for index, gtin in enumerate(first)}
    items.sort(key=lambda item: (wanted.get(str(item["gtin"]), len(wanted)), item.get("kind") != "empate"))
    return [dict(item) for item in items[:n]]


def store_name(store: object) -> str:
    return STORE_NAMES.get(str(store), str(store))


def offer_label(offer: Mapping[str, object]) -> list[str]:
    store = store_name(offer.get("store"))
    size = offer.get("size") or "sin tamaño"
    lines = [f"{store} · publicado: {size}"]
    source = offer.get("foto_de")
    if offer.get("foto") and source and source != offer.get("store"):
        lines[0] += f" · foto compartida (de {store_name(source)})"
    if not offer.get("foto"):
        lines[0] += " · sin foto"
    lines += textwrap.wrap(str(offer.get("name") or ""), 58)[:2]
    if offer.get("code"):
        lines.append(f"código {offer['code']}")
    return lines


# ----------------------------------------------------------------------- imagen


def _font(size: int):
    from PIL import ImageFont  # noqa: PLC0415

    for path in FONTS:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def compose(item: Mapping[str, object], photos: Mapping[str, bytes]) -> bytes:
    """Cuadrícula con una casilla por oferta (foto + rótulo) y el código arriba."""
    from PIL import Image, ImageDraw  # noqa: PLC0415

    offers = list(item.get("offers") or ())
    columns = max(1, min(MAX_COLUMNS, len(offers)))
    rows = max(1, -(-len(offers) // columns))
    canvas = Image.new("RGB", (columns * TILE, HEADER + rows * (TILE + CAPTION)), "white")
    draw = ImageDraw.Draw(canvas)
    title, small = _font(26), _font(19)
    header = f"{item.get('gtin')} · {item.get('kind')} · {str(item.get('name') or '')[:70]}"
    draw.text((12, 18), header, fill="black", font=title)
    for index, offer in enumerate(offers):
        x = (index % columns) * TILE
        y = HEADER + (index // columns) * (TILE + CAPTION)
        data = photos.get(str(offer.get("foto")))
        if data:
            try:
                picture = Image.open(io.BytesIO(data)).convert("RGB")
                picture.thumbnail((TILE - 16, TILE - 16))
                canvas.paste(picture, (x + (TILE - picture.width) // 2, y + (TILE - picture.height) // 2))
            except OSError:
                draw.text((x + 20, y + TILE // 2), "foto no legible", fill="gray", font=small)
        else:
            draw.text((x + 20, y + TILE // 2), "sin foto", fill="gray", font=small)
        draw.rectangle((x + 2, y + 2, x + TILE - 3, y + TILE + CAPTION - 3), outline="#999999", width=2)
        for line_number, line in enumerate(offer_label(offer)):
            draw.text((x + 10, y + TILE + 6 + line_number * 24), line, fill="black", font=small)
    output = io.BytesIO()
    canvas.save(output, "JPEG", quality=85)
    return output.getvalue()


# ----------------------------------------------------------------------- red


def fetch_bytes(url: str, limit: int = 20_000_000) -> bytes:
    if not (url.startswith(PHOTO_BASE) or url == QUEUE_URL):
        raise ValueError("url_no_permitida")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - URLs fijas https
        return response.read(limit)


def photo_url(name: str, size: int = 800) -> str:
    return f"{PHOTO_BASE}{size}/{name[:2]}/{name}.webp"


def prepare(queue: Mapping[str, object], out_dir: Path, *, n: int, first: Sequence[str] = (),
            fetch: Callable[[str], bytes] = fetch_bytes, sleep: Callable[[float], None] = time.sleep) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    items = pending(queue, load_seen(out_dir / SEEN_FILE), n, first)
    batch: list[dict[str, object]] = []
    for item in items:
        photos: dict[str, bytes] = {}
        for offer in item.get("offers") or ():
            name = offer.get("foto")
            if not isinstance(name, str) or not _PHOTO_NAME.fullmatch(name) or name in photos:
                continue
            try:
                photos[name] = fetch(photo_url(name))
            except (OSError, ValueError):
                photos[name] = b""
            sleep(PHOTO_DELAY)
        image = out_dir / f"{item['gtin']}.jpg"
        image.write_bytes(compose(item, photos))
        batch.append({
            "gtin": item["gtin"], "kind": item.get("kind"), "name": item.get("name"), "image": image.name,
            "ia_previa": item.get("ia"),
            "offers": [{key: offer.get(key) for key in ("store", "name", "code", "size", "value", "unit", "foto",
                                                         "foto_de")} for offer in item.get("offers") or ()],
        })
    document = {"generated_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "cola_generada_utc": queue.get("generated_utc"), "items": batch}
    (out_dir / BATCH_FILE).write_text(json.dumps(document, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return document


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n", type=int, default=DEFAULT_N)
    parser.add_argument("--primero", action="append", default=[], help="GTIN que va primero (repetible)")
    parser.add_argument("--cola", type=Path, help="cola.json local (si no, la pública)")
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)
    if not 1 <= args.n <= 100 or any(not _GTIN.fullmatch(gtin) for gtin in args.primero):
        raise SystemExit("parametros_invalidos")
    raw = args.cola.read_bytes() if args.cola else fetch_bytes(QUEUE_URL)
    queue = json.loads(raw.decode("utf-8"))
    document = prepare(queue, args.out, n=args.n, first=args.primero)
    print(json.dumps({"preparados": len(document["items"]), "carpeta": str(args.out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
