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
    cropped = scored("walmart", product(box=(-50, -50, 1050, 1050)))
    assert cropped.score == 0 and cropped.flags == ("fondo no blanco",)
    one_side = scored("walmart", product(box=(-50, 150, 670, 850)))
    assert one_side.score > 0 and {"fondo no blanco"} <= set(one_side.flags) and one_side.metrics.touch == 1


def test_non_white_backgrounds_are_rejected_not_just_penalized() -> None:
    """Producto sobre una mesa, fondo oscuro o ilustración con regla → 0 puntos.

    En la primera corrida pasaban con 55 puntos justos: el "producto" medido era el
    cuadro entero. Un fondo casi blanco (balance de blancos) sigue siendo válido.
    """
    table = Image.new("RGB", (1000, 1000), (120, 80, 50))
    draw = ImageDraw.Draw(table)
    draw.rectangle((0, 600, 1000, 1000), fill=(90, 60, 30))
    draw.rounded_rectangle((380, 250, 620, 700), radius=30, fill=(200, 30, 40))
    ruler = product()  # ilustración: camiseta amarilla hasta dos bordes con el producto encima
    ImageDraw.Draw(ruler).rectangle((0, 0, 1000, 700), fill=(250, 230, 90))
    for image in (table, product(background=(40, 40, 40)), ruler):
        scene = scored("walmart", image)
        assert scene.score == 0 and scene.flags == ("fondo no blanco",)
        assert pi.choose([scene]) is None
    assert scored("walmart", product(background=(244, 241, 234))).score >= 85


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
    half = pi.OUTPUT_SIZE / 2
    assert abs((x0 + x1) / 2 - half) < 12 and abs((y0 + y1) / 2 - half) < 12
    assert max(x1 - x0, y1 - y0) == pytest.approx(pi.OUTPUT_SIZE * pi.OUTPUT_FILL, abs=12)
    assert len(output) < 120_000


def test_normalize_with_crop_removes_edge_strip() -> None:
    image = product(box=(250, 150, 590, 850), strip=(880, 250, 970, 760))
    item = scored("walmart", image)
    result = np.asarray(Image.open(io.BytesIO(pi.normalize(image, item.metrics.bbox))).convert("RGB")).astype(int)
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


def clear_bottle(size: int = 1000, top: int = 120) -> Image.Image:
    """Botella transparente: tapa azul, plástico casi blanco y etiqueta con puntos (caso Agua Zen)."""
    image = Image.new("RGB", (size, size), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    s = size / 1000
    shift = top - 120
    draw.rounded_rectangle((470 * s, (120 + shift) * s, 530 * s, (190 + shift) * s), radius=8 * s, fill=(30, 110, 210))
    draw.rectangle((455 * s, (190 + shift) * s, 545 * s, (230 + shift) * s), outline=(225, 228, 232),
                   width=max(1, int(3 * s)), fill=(250, 251, 252))
    draw.rounded_rectangle((400 * s, (230 + shift) * s, 600 * s, (880 + shift) * s), radius=40 * s,
                           outline=(222, 226, 230), width=max(1, int(4 * s)), fill=(249, 250, 251))
    rng = np.random.default_rng(1)
    for _ in range(500):
        x, y, r = rng.uniform(410, 590) * s, (rng.uniform(440, 690) + shift) * s, rng.uniform(2, 6) * s
        color = tuple(int(v) for v in rng.choice([[40, 160, 200], [120, 60, 160], [20, 40, 150]]))
        draw.ellipse((x - r, y - r, x + r, y + r), fill=color)
    return image


def jpeg(image: Image.Image, quality: int = 88) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


def measured(image: Image.Image) -> pi.Metrics:
    data = jpeg(image)
    return pi.measure(pi.load_image(data), len(data))


def blue_pixels(webp: bytes) -> int:
    array = np.asarray(Image.open(io.BytesIO(webp)).convert("RGB")).astype(int)
    return int(((array[:, :, 2] > 170) & (array[:, :, 0] < 90) & (array[:, :, 1] > 70) & (array[:, :, 1] < 150)).sum())


def test_clear_bottle_keeps_cap_and_whole_body() -> None:
    """Antes la tapa se tomaba por franja y se recortaba: sólo quedaba la etiqueta."""
    image = clear_bottle()
    metrics = measured(image)
    x0, y0, x1, y1 = metrics.bbox
    assert y0 < 0.13 and y1 > 0.86, metrics.bbox
    assert metrics.strip_extra == 0.0 and metrics.main_bbox is None
    rendered = pi.render(image, metrics.bbox)
    assert blue_pixels(rendered[800]) > 1500
    # El plástico casi blanco del interior se conserva (no se pinta de blanco).
    array = np.asarray(Image.open(io.BytesIO(rendered[800])).convert("RGB")).astype(int)
    outline = (array.min(axis=2) < 236) & (array.min(axis=2) > 180) & (array.max(axis=2) - array.min(axis=2) < 20)
    assert outline.sum() > 800


def test_cap_touching_the_top_is_never_cropped() -> None:
    metrics = measured(clear_bottle(top=4))
    assert metrics.bbox[1] < 0.02
    assert blue_pixels(pi.render(clear_bottle(top=4), metrics.bbox)[800]) > 1500


def test_upscaled_photo_is_detected_and_loses_to_a_sharp_one() -> None:
    sharp = clear_bottle()
    upscaled = sharp.resize((330, 330), Image.Resampling.LANCZOS).resize((1000, 1000), Image.Resampling.BICUBIC)
    sharp_metrics, upscaled_metrics = measured(sharp), measured(upscaled)
    assert sharp_metrics.effective == 1.0 and sharp_metrics.quality == "hd"
    assert upscaled_metrics.effective <= 0.5 and upscaled_metrics.quality != "hd"
    candidates = [
        pi.Candidate("colonial", 0, "https://cdn.shopify.com/a.jpg", True),
        pi.Candidate("la_colonia", 0, "https://lacolonia.vteximg.com.br/a.jpg", True),
    ]
    scored_items = []
    for candidate, metrics in zip(candidates, (upscaled_metrics, sharp_metrics)):
        value, flags = pi.score(candidate, metrics)
        scored_items.append(pi.Scored(candidate, metrics, value, flags))
    assert "agrandada" in scored_items[0].flags
    # Colonial gana los empates, pero no con una foto agrandada contra una nítida.
    assert pi.choose(scored_items).candidate.supermarket_id == "la_colonia"


def test_render_outputs_both_sizes_as_small_webp() -> None:
    rendered = pi.render(product())
    assert set(rendered) == {800, 320}
    for size, data in rendered.items():
        image = Image.open(io.BytesIO(data))
        assert image.format == "WEBP" and image.size == (size, size)
    assert len(rendered[800]) < 120_000 and len(rendered[320]) < 40_000
