"""Fotos de producto para Súper Compras: medir, puntuar, elegir y normalizar.

Cada producto publicado puede tener fotos en varios supermercados. Este módulo
no descarga nada: recibe los bytes de cada foto candidata, mide su calidad,
elige una sola foto por producto y la deja con el mismo formato que las demás
(cuadro blanco de 400 px, producto centrado al 80 %, blanco igualado).

Criterio validado con una muestra de 100 productos (2026-10-15):

* puntaje: fondo blanco 35, resolución 20, nitidez 15, encuadre 15, formato
  cuadrado 5, coincidencia por código de barras 5, foto principal 5;
* la foto secundaria de un súper (suele ser la parte trasera) pierde 20;
* las franjas o sellos pegados al lado del producto se recortan cuando están
  separados del producto y, si no se pueden separar, la foto pierde 25;
* entre fotos con puntaje parecido (menos de 10 puntos) gana siempre la misma
  fuente para que la cuadrícula se vea pareja;
* una foto que no se parece a las de los otros súper se descarta (otro
  producto con el mismo código de barras);
* el fondo no se vuelve transparente: los empaques blancos perdían partes.
  Se igualan el punto blanco y los casi blancos; la app funde el blanco con
  el gris de la tarjeta.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np
from PIL import Image, ImageOps

RETAILER_PRIORITY = ("colonial", "la_colonia", "walmart", "comisariato_los_andes", "pricesmart")
MIN_SCORE = 60
NEAR_TIE_POINTS = 10
OUTPUT_SIZE = 400
OUTPUT_FILL = 0.80
WEBP_QUALITY = 80
MAX_SOURCE_PIXELS = 25_000_000
MIN_SHORT_SIDE = 300
ANALYSIS_SIZE = 256
THUMB_SIZE = 12

Image.MAX_IMAGE_PIXELS = MAX_SOURCE_PIXELS


class ProductImageError(ValueError):
    """La foto no se puede leer o no cumple el contrato mínimo."""


@dataclass(frozen=True)
class Candidate:
    supermarket_id: str
    position: int
    url: str
    gtin_match: bool = False


@dataclass(frozen=True)
class Metrics:
    width: int
    height: int
    bytes: int
    border_white: float
    fill: float
    foreground: float
    touch: int
    sharpness: float
    strip_extra: float
    bbox: tuple[float, float, float, float] | None
    main_bbox: tuple[float, float, float, float] | None
    thumb: np.ndarray = field(repr=False, compare=False)


@dataclass(frozen=True)
class Scored:
    candidate: Candidate
    metrics: Metrics | None
    score: int
    flags: tuple[str, ...]


# --------------------------------------------------------------------- lectura

def load_image(data: bytes) -> Image.Image:
    """Abre la foto como RGB sobre blanco; rechaza tamaños absurdos."""
    if not data:
        raise ProductImageError("image_empty")
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (OSError, Image.DecompressionBombError, ValueError) as error:
        raise ProductImageError("image_unreadable") from error
    if image.width * image.height > MAX_SOURCE_PIXELS:
        raise ProductImageError("image_too_large")
    image = ImageOps.exif_transpose(image)
    if image.mode in ("RGBA", "LA", "P"):
        rgba = image.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, (255, 255, 255))
        canvas.paste(rgba, mask=rgba.split()[3])
        return canvas
    return image.convert("RGB")


def _white_balance(array: np.ndarray) -> np.ndarray:
    """Lleva el color del borde (fondo) a blanco puro si el fondo es claro."""
    border = np.concatenate([array[0], array[-1], array[:, 0], array[:, -1]]).astype(np.float32)
    reference = np.median(border, axis=0)
    if reference.min() <= 195 or reference.min() >= 254:
        return array
    scaled = array.astype(np.float32) * (255.0 / np.maximum(reference, 1.0))
    return np.clip(scaled, 0, 255).astype(np.uint8)


def _background_mask(array: np.ndarray) -> np.ndarray:
    low = array.min(axis=2)
    spread = array.max(axis=2).astype(np.int16) - low
    return (low >= 236) & (spread < 16)


def _runs(profile: np.ndarray, other: int) -> list[tuple[int, int, float]]:
    threshold = max(1.0, other * 0.01)
    result: list[tuple[int, int, float]] = []
    start = -1
    gap = 0
    for index, value in enumerate(profile):
        if value > threshold:
            if start < 0:
                start = index
            gap = 0
        elif start >= 0:
            gap += 1
            if gap >= 3:
                end = index - gap
                result.append((start, end, float(profile[start:end + 1].sum())))
                start, gap = -1, 0
    if start >= 0:
        result.append((start, len(profile) - 1, float(profile[start:].sum())))
    return result


def _main_run(runs: list[tuple[int, int, float]], length: int) -> tuple[tuple[int, int, float] | None, float]:
    """Elige el producto entre bloques separados; las franjas al borde no cuentan.

    Walmart pega franjas angostas de texto al borde de la foto. Si el bloque
    más pesado es una de esas franjas (botellas transparentes pesan poco), se
    prefiere el bloque que no toca el borde.
    """
    if not runs:
        return None, 0.0

    def edge_strip(run: tuple[int, int, float]) -> bool:
        start, end, _ = run
        narrow = (end - start + 1) < length * 0.30
        return narrow and (start <= 1 or end >= length - 2)

    inner = [run for run in runs if not edge_strip(run)]
    pool = inner or runs
    main = max(pool, key=lambda run: run[2])
    others = [run for run in runs if run is not main and run[2] > main[2] * 0.04]
    extra = sum(run[2] for run in others) / main[2] if main[2] else 0.0
    return main, extra


def _thumb(array: np.ndarray, box: tuple[float, float, float, float] | None) -> np.ndarray:
    """Huella de 12×12 en color del producto dentro de un cuadro (conserva forma y color)."""
    height, width = array.shape[:2]
    x0, y0, x1, y1 = box or (0.0, 0.0, 1.0, 1.0)
    left, top = int(x0 * width), int(y0 * height)
    right, bottom = max(left + 1, int(round(x1 * width))), max(top + 1, int(round(y1 * height)))
    crop = Image.fromarray(array[top:bottom, left:right])
    side = max(crop.width, crop.height)
    square = Image.new("RGB", (side, side), (255, 255, 255))
    square.paste(crop, ((side - crop.width) // 2, (side - crop.height) // 2))
    small = square.resize((THUMB_SIZE, THUMB_SIZE), Image.Resampling.BILINEAR)
    values = np.asarray(small, dtype=np.float32).ravel()
    values -= values.mean()
    norm = float(np.linalg.norm(values))
    return values / norm if norm else values


def measure(image: Image.Image, size_bytes: int) -> Metrics:
    width, height = image.size
    small = image.copy()
    small.thumbnail((ANALYSIS_SIZE, ANALYSIS_SIZE), Image.Resampling.BILINEAR)
    array = _white_balance(np.asarray(small))
    h, w = array.shape[:2]
    background = _background_mask(array)
    band = max(1, round(min(w, h) * 0.03))
    border = np.zeros_like(background)
    border[:band, :] = border[-band:, :] = True
    border[:, :band] = border[:, -band:] = True
    border_white = float(background[border].mean()) if border.any() else 0.0
    foreground = ~background
    fg_share = float(foreground.mean())
    gray = array.astype(np.float32) @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    lap = (4 * gray[1:-1, 1:-1] - gray[:-2, 1:-1] - gray[2:, 1:-1] - gray[1:-1, :-2] - gray[1:-1, 2:])
    sharpness = float(lap.var()) if lap.size else 0.0
    if not foreground.any():
        return Metrics(width, height, size_bytes, border_white, 0.0, 0.0, 0, sharpness, 0.0, None, None,
                       np.zeros(THUMB_SIZE * THUMB_SIZE * 3, dtype=np.float32))
    rows = np.flatnonzero(foreground.any(axis=1))
    cols = np.flatnonzero(foreground.any(axis=0))
    y0, y1, x0, x1 = int(rows[0]), int(rows[-1]), int(cols[0]), int(cols[-1])
    bbox = (x0 / w, y0 / h, (x1 + 1) / w, (y1 + 1) / h)
    fill = ((x1 - x0 + 1) * (y1 - y0 + 1)) / (w * h)
    touch = sum((x0 <= 0, y0 <= 0, x1 >= w - 1, y1 >= h - 1))
    col_runs = _runs(foreground.sum(axis=0), h)
    row_runs = _runs(foreground.sum(axis=1), w)
    main_col, extra_col = _main_run(col_runs, w)
    main_row, extra_row = _main_run(row_runs, h)
    extra = max(extra_col, extra_row)
    main_bbox = None
    if extra > 0.03 and main_col and main_row:
        main_bbox = (main_col[0] / w, main_row[0] / h, (main_col[1] + 1) / w, (main_row[1] + 1) / h)
    return Metrics(
        width=width, height=height, bytes=size_bytes, border_white=round(border_white, 4),
        fill=round(float(fill), 4), foreground=round(fg_share, 4), touch=int(touch),
        sharpness=round(sharpness, 1), strip_extra=round(float(extra), 4),
        bbox=tuple(round(v, 4) for v in bbox), main_bbox=tuple(round(v, 4) for v in main_bbox) if main_bbox else None,
        thumb=_thumb(array, main_bbox or bbox),
    )


# ---------------------------------------------------------------------- puntaje

def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def score(candidate: Candidate, metrics: Metrics | None, *, duplicate: bool = False) -> tuple[int, tuple[str, ...]]:
    if metrics is None:
        return 0, ("error",)
    if metrics.bbox is None or metrics.foreground < 0.02:
        return 0, ("vacía",)
    if duplicate:
        return 0, ("genérica",)
    flags: list[str] = []
    short = min(metrics.width, metrics.height)
    if short < MIN_SHORT_SIDE:
        flags.append("pequeña")
    if metrics.border_white < 0.85:
        flags.append("fondo no blanco")
    if metrics.touch >= 2:
        flags.append("recortada")
    if metrics.sharpness < 300:
        flags.append("borrosa")
    fill = metrics.fill
    framing = 1.0 if 0.3 <= fill <= 0.9 else (_clamp(fill / 0.3) if fill < 0.3 else _clamp((1 - fill) / 0.1))
    value = (
        35 * _clamp((metrics.border_white - 0.6) / 0.38)
        + 20 * _clamp((short - 300) / 700)
        + 15 * _clamp(metrics.sharpness / 1500)
        + 15 * framing
        + 5 * (short / max(metrics.width, metrics.height))
        + (5 if candidate.gtin_match else 0)
        + 5
        - (15 if metrics.touch >= 2 else 0)
        - (20 if short < MIN_SHORT_SIDE else 0)
    )
    if candidate.position > 0:
        value -= 20
        flags.append("foto secundaria")
    if metrics.strip_extra > 0.03:
        if metrics.main_bbox:
            value -= 5
            flags.append("franja recortada")
        else:
            value -= 25
            flags.append("franja")
    return int(round(_clamp(value, 0, 100))), tuple(flags)


def duplicate_keys(items: Iterable[tuple[str, Metrics]]) -> set[str]:
    """Fotos idénticas en dos o más productos (logos o "sin imagen")."""
    seen: dict[tuple[int, int, int], set[str]] = {}
    for product_key, metrics in items:
        seen.setdefault((metrics.width, metrics.height, metrics.bytes), set()).add(product_key)
    return {f"{w}x{h}:{b}" for (w, h, b), owners in seen.items() if len(owners) >= 2}


def metrics_key(metrics: Metrics) -> str:
    return f"{metrics.width}x{metrics.height}:{metrics.bytes}"


def _similarity(left: Metrics, right: Metrics) -> float:
    return float(np.dot(left.thumb, right.thumb))


def choose(scored: Sequence[Scored]) -> Scored | None:
    """Una foto por producto: calidad, coherencia entre súper y fuente estable."""
    usable = [item for item in scored if item.metrics is not None and item.score >= MIN_SCORE]
    if not usable:
        return None
    mains = {}
    for item in scored:
        if item.metrics is not None and item.metrics.bbox is not None and item.candidate.position == 0:
            mains.setdefault(item.candidate.supermarket_id, item)
    consistent = []
    for item in usable:
        others = [other for store, other in mains.items() if store != item.candidate.supermarket_id]
        if len(others) >= 2:
            agree = np.median([_similarity(a.metrics, b.metrics) for i, a in enumerate(others) for b in others[i + 1:]])
            mine = np.median([_similarity(item.metrics, other.metrics) for other in others])
            if agree > 0.6 and mine < 0.35:
                continue
        consistent.append(item)
    if not consistent:
        return None
    top = max(item.score for item in consistent)
    near = [item for item in consistent if item.score >= top - NEAR_TIE_POINTS and item.candidate.position == 0]
    pool = near or [item for item in consistent if item.score == top]

    def rank(item: Scored) -> tuple[int, int]:
        store = item.candidate.supermarket_id
        order = RETAILER_PRIORITY.index(store) if store in RETAILER_PRIORITY else len(RETAILER_PRIORITY)
        return order, -item.score

    return sorted(pool, key=rank)[0]


# ------------------------------------------------------------------ normalizado

def normalize(image: Image.Image, crop: tuple[float, float, float, float] | None = None) -> bytes:
    """Cuadro blanco de 400 px con el producto centrado al 80 % (WebP)."""
    width, height = image.size
    if crop:
        x0, y0, x1, y1 = crop
        box = (int(x0 * width), int(y0 * height), max(int(x0 * width) + 1, int(round(x1 * width))),
               max(int(y0 * height) + 1, int(round(y1 * height))))
        image = image.crop(box)
    working = image.copy()
    working.thumbnail((900, 900), Image.Resampling.LANCZOS)
    array = _white_balance(np.asarray(working))
    low = array.min(axis=2)
    spread = array.max(axis=2).astype(np.int16) - low
    near_white = (low >= 240) & (spread < 14)
    array = array.copy()
    array[near_white] = 255
    content = ~near_white
    if content.any():
        rows = np.flatnonzero(content.any(axis=1))
        cols = np.flatnonzero(content.any(axis=0))
        y0, y1, x0, x1 = int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1
    else:
        y0, y1, x0, x1 = 0, array.shape[0], 0, array.shape[1]
    product = Image.fromarray(array[y0:y1, x0:x1])
    target = int(OUTPUT_SIZE * OUTPUT_FILL)
    scale = min(target / product.width, target / product.height)
    size = (max(1, round(product.width * scale)), max(1, round(product.height * scale)))
    product = product.resize(size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (OUTPUT_SIZE, OUTPUT_SIZE), (255, 255, 255))
    canvas.paste(product, ((OUTPUT_SIZE - size[0]) // 2, (OUTPUT_SIZE - size[1]) // 2))
    buffer = io.BytesIO()
    canvas.save(buffer, format="WEBP", quality=WEBP_QUALITY, method=6)
    return buffer.getvalue()
