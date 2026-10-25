"""Fotos de producto para Súper Compras: medir, puntuar, elegir y normalizar.

Cada producto publicado puede tener fotos en varios supermercados. Este módulo
no descarga nada: recibe los bytes de cada foto candidata, mide su calidad,
elige una sola foto por producto y la deja en dos tamaños con el mismo formato
(cuadro blanco de 800 y 320 px, producto completo y centrado).

Versión 2 (2026-10-25), tras revisar fotos publicadas:

* **Producto completo.** Antes, una botella transparente se partía en bloques
  (tapa, etiqueta, base) porque el plástico claro parece fondo; el bloque de la
  tapa se tomaba por una franja y se recortaba, y los casi blancos se pintaban de
  blanco, así que sólo quedaba la etiqueta. Ahora sólo se recortan franjas
  angostas pegadas al borde de la foto (las de texto de Walmart) y sólo se
  vuelve blanco el fondo conectado al borde; el interior del producto no se toca.
* **Nitidez real.** Algunas fotos de 3000 px son fotos chicas agrandadas. Se mide
  la resolución efectiva (cuánto detalle se pierde al reducir y volver a
  agrandar) y se puntúa el tamaño real del producto en píxeles útiles.
* **Dos tamaños.** 800 px para el detalle (pantallas retina) y 320 px para las
  tarjetas, ambos WebP.

Se conservan los criterios validados en la versión 1: fondo blanco, foto
principal antes que la secundaria (suele ser el reverso), coincidencia por código
de barras, fotos repetidas entre productos fuera, coherencia entre súper y, en
empates, siempre la misma fuente para que la cuadrícula se vea pareja.
"""
from __future__ import annotations

import io
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageFilter, ImageOps

RETAILER_PRIORITY = ("colonial", "la_colonia", "walmart", "paiz", "comisariato_los_andes", "pricesmart")
MIN_SCORE = 55
NEAR_TIE_POINTS = 6
OUTPUT_SIZES = (800, 320)
OUTPUT_SIZE = OUTPUT_SIZES[0]
OUTPUT_FILL = 0.86
WEBP_QUALITY = {800: 82, 320: 80}
MAX_SOURCE_PIXELS = 25_000_000
MIN_SHORT_SIDE = 300
ANALYSIS_SIZE = 256
DETAIL_SIZE = 1000
THUMB_SIZE = 12
# Producto con al menos estos píxeles útiles en su lado largo: alcanza para 800 px.
HD_PRODUCT_PIXELS = 560
SD_PRODUCT_PIXELS = 260
EFFECTIVE_FACTORS = (0.75, 0.6, 0.5, 0.4, 0.3, 0.25, 0.2)
DETAIL_LOSS_LIMIT = 0.03

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
    effective: float = 1.0
    product_pixels: int = 0

    @property
    def quality(self) -> str:
        """Nivel de detalle real del producto: hd (800 px nítido), sd o low."""
        if self.product_pixels >= HD_PRODUCT_PIXELS:
            return "hd"
        return "sd" if self.product_pixels >= SD_PRODUCT_PIXELS else "low"


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


def _near_white(array: np.ndarray) -> np.ndarray:
    low = array.min(axis=2)
    spread = array.max(axis=2).astype(np.int16) - low
    return (low >= 236) & (spread < 16)


def _flood_from_border(candidate: np.ndarray) -> np.ndarray:
    """Casi blancos conectados al borde: el fondo, no el interior del producto."""
    mask = np.zeros_like(candidate)
    mask[0], mask[-1] = candidate[0], candidate[-1]
    mask[:, 0] |= candidate[:, 0]
    mask[:, -1] |= candidate[:, -1]
    while True:
        previous = mask.sum()
        for _ in range(8):
            grown = mask.copy()
            grown[1:] |= mask[:-1]
            grown[:-1] |= mask[1:]
            grown[:, 1:] |= mask[:, :-1]
            grown[:, :-1] |= mask[:, 1:]
            mask = grown & candidate
        if mask.sum() == previous:
            return mask


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


def _edge_strip(run: tuple[int, int, float], length: int) -> bool:
    """Franja angosta (menos del 30 %) que llega hasta el 5 % del borde."""
    start, end, _ = run
    margin = max(2, round(length * 0.05))
    return (end - start + 1) < length * 0.30 and (start <= margin or end >= length - 1 - margin)


def _content_span(runs: list[tuple[int, int, float]], length: int,
                  strips: bool = True) -> tuple[tuple[int, int] | None, float]:
    """Tramo del producto: todo lo que no sea una franja angosta pegada a un lado.

    Walmart pega franjas verticales de texto a los lados de la foto; ésas se quitan
    (sólo en columnas). Los bloques separados por un hueco (la tapa sobre una
    botella transparente, dos piezas de un paquete) son parte del producto.
    """
    if not runs:
        return None, 0.0
    if not strips or len(runs) == 1:
        return (min(run[0] for run in runs), max(run[1] for run in runs)), 0.0
    inner = [run for run in runs if not _edge_strip(run, length)]
    if not inner:
        main = max(runs, key=lambda run: run[2])
        return (main[0], main[1]), 0.0
    strips = [run for run in runs if run not in inner]
    weight = sum(run[2] for run in inner)
    extra = sum(run[2] for run in strips) / weight if weight else 0.0
    return (min(run[0] for run in inner), max(run[1] for run in inner)), extra


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


def effective_resolution(image: Image.Image, box: tuple[float, float, float, float] | None = None) -> tuple[float, int]:
    """Fracción de la resolución que tiene detalle real y lado largo útil del producto.

    Reduce y vuelve a agrandar el producto: si casi no se pierde detalle, la foto
    ya estaba agrandada. Devuelve (factor, píxeles útiles del lado largo).
    """
    width, height = image.size
    x0, y0, x1, y1 = box or (0.0, 0.0, 1.0, 1.0)
    crop = image.crop((int(x0 * width), int(y0 * height), max(int(x0 * width) + 1, int(round(x1 * width))),
                       max(int(y0 * height) + 1, int(round(y1 * height)))))
    long_side = max(crop.size)
    scale = min(1.0, DETAIL_SIZE / long_side) if long_side else 1.0
    gray = crop.convert("L")
    if scale < 1.0:
        gray = gray.resize((max(8, round(gray.width * scale)), max(8, round(gray.height * scale))),
                           Image.Resampling.LANCZOS)
    if min(gray.size) < 16:
        return 1.0, int(long_side)
    base = np.asarray(gray, dtype=np.float32)

    def loss(factor: float) -> float:
        small = gray.resize((max(2, round(gray.width * factor)), max(2, round(gray.height * factor))),
                            Image.Resampling.LANCZOS)
        back = np.asarray(small.resize(gray.size, Image.Resampling.LANCZOS), dtype=np.float32)
        return float(np.square(base - back).mean())

    reference = loss(0.125)
    if reference < 1.0:  # producto liso: no hay detalle que medir
        return 1.0, int(long_side * scale) if scale < 1.0 else int(long_side)
    effective = 1.0
    for factor in EFFECTIVE_FACTORS:
        if loss(factor) / reference > DETAIL_LOSS_LIMIT:
            break
        effective = factor
    useful = long_side * scale * effective
    return effective, int(round(useful))


def measure(image: Image.Image, size_bytes: int) -> Metrics:
    width, height = image.size
    small = image.copy()
    small.thumbnail((ANALYSIS_SIZE, ANALYSIS_SIZE), Image.Resampling.BILINEAR)
    array = _white_balance(np.asarray(small))
    h, w = array.shape[:2]
    near = _near_white(array)
    background = _flood_from_border(near)
    band = max(1, round(min(w, h) * 0.03))
    border = np.zeros_like(background)
    border[:band, :] = border[-band:, :] = True
    border[:, :band] = border[:, -band:] = True
    border_white = float(near[border].mean()) if border.any() else 0.0
    foreground = ~background
    fg_share = float(foreground.mean())
    gray = array.astype(np.float32) @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    lap = (4 * gray[1:-1, 1:-1] - gray[:-2, 1:-1] - gray[2:, 1:-1] - gray[1:-1, :-2] - gray[1:-1, 2:])
    sharpness = float(lap.var()) if lap.size else 0.0
    if not foreground.any() or fg_share < 0.002:
        return Metrics(width, height, size_bytes, border_white, 0.0, fg_share, 0, sharpness, 0.0, None, None,
                       np.zeros(THUMB_SIZE * THUMB_SIZE * 3, dtype=np.float32))
    col_span, extra_col = _content_span(_runs(foreground.sum(axis=0), h), w)
    row_span, extra_row = _content_span(_runs(foreground.sum(axis=1), w), h, strips=False)
    if col_span is None or row_span is None:
        rows = np.flatnonzero(foreground.any(axis=1))
        cols = np.flatnonzero(foreground.any(axis=0))
        row_span, col_span = (int(rows[0]), int(rows[-1])), (int(cols[0]), int(cols[-1]))
    x0, x1 = col_span
    y0, y1 = row_span
    bbox = (x0 / w, y0 / h, (x1 + 1) / w, (y1 + 1) / h)
    fill = ((x1 - x0 + 1) * (y1 - y0 + 1)) / (w * h)
    touch = sum((x0 <= 0, y0 <= 0, x1 >= w - 1, y1 >= h - 1))
    extra = max(extra_col, extra_row)
    main_bbox = bbox if extra > 0.0 else None
    rounded = tuple(round(v, 4) for v in bbox)
    effective, useful = effective_resolution(image, rounded)
    return Metrics(
        width=width, height=height, bytes=size_bytes, border_white=round(border_white, 4),
        fill=round(float(fill), 4), foreground=round(fg_share, 4), touch=int(touch),
        sharpness=round(sharpness, 1), strip_extra=round(float(extra), 4),
        bbox=rounded, main_bbox=rounded if main_bbox else None,
        thumb=_thumb(array, bbox), effective=round(effective, 2), product_pixels=useful,
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
    if metrics.effective < 0.6:
        flags.append("agrandada")
    if metrics.product_pixels < SD_PRODUCT_PIXELS:
        flags.append("poco detalle")
    framing = 1.0 if metrics.touch == 0 else (0.4 if metrics.touch == 1 else 0.0)
    value = (
        25 * _clamp((metrics.border_white - 0.6) / 0.38)
        + 35 * _clamp((metrics.product_pixels - 200) / (600 - 200))
        + 10 * _clamp(metrics.sharpness / 1500)
        + 15 * framing
        + (5 if candidate.gtin_match else 0)
        + 10
        - (20 if short < MIN_SHORT_SIDE else 0)
    )
    if candidate.position > 0:
        value -= 20
        flags.append("foto secundaria")
    if metrics.strip_extra > 0.0:
        value -= 3
        flags.append("franja recortada")
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

    def rank(item: Scored) -> tuple[int, int, int]:
        store = item.candidate.supermarket_id
        order = RETAILER_PRIORITY.index(store) if store in RETAILER_PRIORITY else len(RETAILER_PRIORITY)
        # Primero el nivel de detalle (hd > sd > low); en el mismo nivel, la fuente estable.
        level = {"hd": 0, "sd": 1, "low": 2}[item.metrics.quality]
        return level, order, -item.score

    return sorted(pool, key=rank)[0]


# ------------------------------------------------------------------ normalizado

def _encode(canvas: Image.Image, size: int) -> bytes:
    buffer = io.BytesIO()
    canvas.save(buffer, format="WEBP", quality=WEBP_QUALITY.get(size, 80), method=6)
    return buffer.getvalue()


def render(image: Image.Image, crop: tuple[float, float, float, float] | None = None) -> dict[int, bytes]:
    """WebP de 800 y 320 px: cuadro blanco con el producto completo centrado al 86 %."""
    width, height = image.size
    if crop:
        x0, y0, x1, y1 = crop
        margin_x, margin_y = 0.01 * width, 0.01 * height
        box = (max(0, int(x0 * width - margin_x)), max(0, int(y0 * height - margin_y)),
               min(width, max(int(x0 * width) + 1, int(round(x1 * width + margin_x)))),
               min(height, max(int(y0 * height) + 1, int(round(y1 * height + margin_y)))))
        image = image.crop(box)
    working = image.copy()
    working.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
    array = _white_balance(np.asarray(working)).copy()
    near = _near_white(array)
    # El fondo se busca en una versión chica (rápido) y se aplica a los casi blancos.
    mask_small = Image.fromarray(near.astype(np.uint8) * 255)
    mask_small.thumbnail((ANALYSIS_SIZE * 2, ANALYSIS_SIZE * 2), Image.Resampling.NEAREST)
    flooded = _flood_from_border(np.asarray(mask_small) > 127)
    background = np.asarray(Image.fromarray(flooded.astype(np.uint8) * 255).resize(
        (array.shape[1], array.shape[0]), Image.Resampling.NEAREST)) > 127
    background &= near
    array[background] = 255
    content = ~background
    rows = np.flatnonzero(content.sum(axis=1) > max(1, content.shape[1] * 0.002))
    cols = np.flatnonzero(content.sum(axis=0) > max(1, content.shape[0] * 0.002))
    if rows.size and cols.size:
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
    result = {OUTPUT_SIZE: _encode(canvas, OUTPUT_SIZE)}
    for side in OUTPUT_SIZES[1:]:
        small = canvas.resize((side, side), Image.Resampling.LANCZOS)
        small = small.filter(ImageFilter.UnsharpMask(radius=0.8, percent=45, threshold=2))
        result[side] = _encode(small, side)
    return result


def normalize(image: Image.Image, crop: tuple[float, float, float, float] | None = None) -> bytes:
    """Compatibilidad: sólo la versión de 800 px."""
    return render(image, crop)[OUTPUT_SIZE]
