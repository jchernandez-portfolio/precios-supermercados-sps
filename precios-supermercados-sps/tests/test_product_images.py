from __future__ import annotations

import io

import numpy as np
import pytest

pytest.importorskip("PIL")
from PIL import Image, ImageDraw  # noqa: E402

from precios_supermercados import product_images as pi  # noqa: E402


def png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def product(background=(255, 255, 255), size=1000, box=(330, 150, 670, 850), color=(200, 30, 40), strip=None,
            texture=True) -> Image.Image:
    image = Image.new("RGB", (size, size), background)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(box, radius=30, fill=color)
    if texture:
        for y in range(box[1] + 40, box[3] - 40, 60):
            draw.text((box[0] + 30, y), "MARCA 500 ml", fill=(255, 255, 255))
    if strip:
        draw.rectangle(strip, fill=(255, 170, 0))
    if texture:
        # Detalle fino como el de un empaque real (las fotos planas salen "borrosas").
        array = np.asarray(image).astype(np.int16)
        x0, y0, x1, y1 = (max(0, v) for v in box)
        rng = np.random.default_rng(7)
        patch = array[y0:y1, x0:x1]
        noise = rng.integers(-60, 61, size=(patch.shape[0] // 6 + 1, patch.shape[1] // 6 + 1, 1), dtype=np.int16)
        patch += np.repeat(np.repeat(noise, 6, axis=0), 6, axis=1)[:patch.shape[0], :patch.shape[1]]
        image = Image.fromarray(np.clip(array, 0, 255).astype(np.uint8))
    return image


def scored(store: str, image: Image.Image, position: int = 0, gtin: bool = True) -> pi.Scored:
    data = png(image)
    metrics = pi.measure(pi.load_image(data), len(data))
    candidate = pi.Candidate(store, position, f"https://example.test/{store}/{position}.png", gtin)
    value, flags = pi.score(candidate, metrics)
    return pi.Scored(candidate, metrics, value, flags)


def test_clean_white_background_photo_scores_high_and_flags_nothing() -> None:
    item = scored("colonial", product())
    assert item.score >= 85
    assert item.flags == ()
    assert item.metrics.border_white > 0.95


def test_off_white_background_is_recognized_after_white_balance() -> None:
    item = scored("la_colonia", product(background=(244, 241, 234)))
    assert item.metrics.border_white > 0.9
    assert "fondo no blanco" not in item.flags


def test_small_blurry_cropped_and_dark_background_photos_lose_points() -> None:
    small = scored("walmart", product(size=250, box=(80, 30, 170, 220)))
    assert "pequeña" in small.flags and small.score < 60
    dark = scored("walmart", product(background=(40, 40, 40)))
    assert "fondo no blanco" in dark.flags and dark.score < 60
    cropped = scored("walmart", product(box=(-50, -50, 1050, 1050)))
    assert "recortada" in cropped.flags


def test_secondary_photo_is_penalized() -> None:
    main = scored("walmart", product(), position=0)
    back = scored("walmart", product(), position=1)
    assert main.score - back.score == 20
    assert "foto secundaria" in back.flags


def test_edge_strip_is_detected_and_cropped_away() -> None:
    item = scored("walmart", product(box=(250, 150, 590, 850), strip=(880, 250, 970, 760)))
    assert "franja recortada" in item.flags
    x0, _, x1, _ = item.metrics.main_bbox
    assert x1 < 0.7 and x0 > 0.2


def test_light_bottle_wins_over_heavier_edge_banner() -> None:
    """Caso Ron Plata: botella clara y franja de color pesada pegada al borde."""
    image = product(box=(300, 120, 520, 900), color=(225, 225, 215), texture=False, strip=(860, 100, 999, 900))
    item = scored("walmart", image)
    assert item.metrics.main_bbox is not None
    x0, _, x1, _ = item.metrics.main_bbox
    assert x0 < 0.35 and x1 < 0.6


def test_choose_prefers_stable_source_on_near_ties_and_quality_otherwise() -> None:
    walmart = scored("walmart", product())
    colonial = scored("colonial", product(size=900))
    assert abs(walmart.score - colonial.score) <= pi.NEAR_TIE_POINTS
    assert pi.choose([walmart, colonial]).candidate.supermarket_id == "colonial"
    poor = scored("colonial", product(size=250, box=(80, 30, 170, 220)))
    assert pi.choose([walmart, poor]).candidate.supermarket_id == "walmart"


def test_choose_rejects_photo_that_disagrees_with_other_stores() -> None:
    same = dict(box=(330, 150, 670, 850), color=(200, 30, 40))
    a = scored("walmart", product(**same))
    b = scored("la_colonia", product(**same))
    odd = scored("colonial", product(box=(100, 400, 900, 600), color=(20, 120, 220)))
    chosen = pi.choose([a, b, odd])
    assert chosen.candidate.supermarket_id in {"walmart", "la_colonia"}


def test_choose_returns_none_when_nothing_reaches_minimum() -> None:
    small = scored("walmart", product(size=250, box=(80, 30, 170, 220)))
    assert pi.choose([small]) is None
    assert pi.choose([]) is None


def test_duplicate_images_across_products_are_generic() -> None:
    data = png(product())
    metrics = pi.measure(pi.load_image(data), len(data))
    keys = pi.duplicate_keys([("a", metrics), ("b", metrics)])
    assert pi.metrics_key(metrics) in keys
    value, flags = pi.score(pi.Candidate("walmart", 0, "u"), metrics, duplicate=True)
    assert value == 0 and flags == ("genérica",)


def test_normalize_outputs_square_white_webp_with_product_centered() -> None:
    image = product(background=(244, 241, 234), box=(100, 300, 500, 700))
    output = pi.normalize(image)
    result = Image.open(io.BytesIO(output))
    assert result.format == "WEBP" and result.size == (pi.OUTPUT_SIZE, pi.OUTPUT_SIZE)
    array = np.asarray(result.convert("RGB")).astype(int)
    assert array[:10].min() >= 245 and array[:, :10].min() >= 245
    content = np.argwhere(array.min(axis=2) < 200)
    (y0, x0), (y1, x1) = content.min(axis=0), content.max(axis=0)
    assert abs((x0 + x1) / 2 - 200) < 8 and abs((y0 + y1) / 2 - 200) < 8
    assert max(x1 - x0, y1 - y0) == pytest.approx(pi.OUTPUT_SIZE * pi.OUTPUT_FILL, abs=6)
    assert len(output) < 40_000


def test_normalize_with_crop_removes_edge_strip() -> None:
    image = product(box=(250, 150, 590, 850), strip=(880, 250, 970, 760))
    item = scored("walmart", image)
    result = np.asarray(Image.open(io.BytesIO(pi.normalize(image, item.metrics.main_bbox))).convert("RGB")).astype(int)
    orange = (result[:, :, 0] > 230) & (result[:, :, 1] > 140) & (result[:, :, 1] < 200) & (result[:, :, 2] < 60)
    assert orange.sum() < 20


def test_normalize_keeps_white_parts_of_white_products() -> None:
    image = Image.new("RGB", (800, 800), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((250, 100, 550, 700), outline=(150, 150, 150), width=6, fill=(250, 250, 250))
    draw.rectangle((300, 300, 500, 400), fill=(30, 90, 200))
    result = np.asarray(Image.open(io.BytesIO(pi.normalize(image))).convert("RGB")).astype(int)
    outline = (result.min(axis=2) < 200) & (result.max(axis=2) - result.min(axis=2) < 20)
    assert outline.sum() > 500


def test_load_image_rejects_garbage_and_flattens_transparency() -> None:
    with pytest.raises(pi.ProductImageError):
        pi.load_image(b"not an image")
    with pytest.raises(pi.ProductImageError):
        pi.load_image(b"")
    rgba = Image.new("RGBA", (50, 50), (0, 0, 0, 0))
    loaded = pi.load_image(png(rgba))
    assert loaded.mode == "RGB" and loaded.getpixel((0, 0)) == (255, 255, 255)
