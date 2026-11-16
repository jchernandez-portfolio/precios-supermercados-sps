"""Súper Compras: contraste AA de los tokens «Lima» (sólo tema claro).

Lee los tokens de ``:root`` en ``super-compras/styles.css`` y verifica WCAG 2.x.
El modo oscuro automático se quitó el 16-nov a pedido del dueño: la página se ve
siempre con la paleta clara aunque el sistema esté en modo oscuro.

* texto normal ≥ 4.5:1;
* elementos gráficos y anillos de foco ≥ 3:1.

También se puede correr como script para ver la tabla completa::

    python tests/test_super_compras_contraste.py
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "super-compras"
STYLES = APP_DIR / "styles.css"

TEXT_AA = 4.5
GRAPHIC_AA = 3.0

# (primer plano, fondo, mínimo, dónde se usa)
PAIRS: list[tuple[str, str, float, str]] = [
    ("ink", "bg", TEXT_AA, "texto sobre la página"),
    ("ink", "surface", TEXT_AA, "texto en tarjetas"),
    ("muted", "bg", TEXT_AA, "texto secundario sobre la página"),
    ("muted", "surface", TEXT_AA, "texto secundario en tarjetas"),
    ("good", "surface", TEXT_AA, "ahorro / más barato en tarjetas"),
    ("good", "bg", TEXT_AA, "«Precios de hoy» sobre la página"),
    ("good", "good-soft", TEXT_AA, "insignia «Más barato», súper más barato"),
    ("ink", "good-soft", TEXT_AA, "opción elegida en el menú"),
    ("on-lime", "lime", TEXT_AA, "etiqueta de precio y % de descuento"),
    ("on-charcoal", "charcoal", TEXT_AA, "barra superior, destacada, aviso"),
    ("lime", "charcoal", TEXT_AA, "botón principal, precio destacado"),
    ("lime", "charcoal-2", TEXT_AA, "botón principal al pasar el mouse"),
    ("on-charcoal", "charcoal-2", TEXT_AA, "riel lateral al pasar el mouse"),
    ("warn-ink", "warn-soft", TEXT_AA, "aviso: producto que ya no está"),
    ("warn-ink", "surface", TEXT_AA, "precio de otro día"),
    ("ink", "warn-soft", TEXT_AA, "texto del aviso"),
    ("danger", "surface", TEXT_AA, "errores en tarjetas"),
    ("danger", "bg", TEXT_AA, "errores sobre la página"),
    ("on-good", "good", TEXT_AA, "filtro activo del panel interno"),
    ("on-photo", "photo-bg", TEXT_AA, "inicial dentro del círculo con foto"),
    ("ink", "danger-soft", TEXT_AA, "panel interno: «es otro producto»"),
    ("alert-ink", "alert-soft", TEXT_AA, "panel interno: lectura en conflicto"),
    ("ink", "alert-soft-2", TEXT_AA, "panel interno: sugerencia en conflicto"),
    ("ai-ink", "bg", TEXT_AA, "panel interno: texto de la IA"),
    ("ai-ink", "surface", TEXT_AA, "panel interno: texto de la IA en tarjeta"),
    ("ink", "ai-soft", TEXT_AA, "panel interno: botón de la IA"),
    ("ink", "ai-soft-2", TEXT_AA, "panel interno: botón de la IA elegido"),
    ("focus", "bg", GRAPHIC_AA, "anillo de foco sobre la página"),
    ("focus", "surface", GRAPHIC_AA, "anillo de foco sobre tarjetas"),
    ("lime", "charcoal", GRAPHIC_AA, "anillo de foco en barras carbón"),
    ("edge", "bg", GRAPHIC_AA, "borde fuerte (filtro activo, secundario)"),
    ("edge", "surface", GRAPHIC_AA, "borde fuerte en tarjetas / menú"),
    ("good", "bg", GRAPHIC_AA, "barra del precio más barato"),
    ("ink", "surface", GRAPHIC_AA, "marcador del rango de precios"),
    ("alert-edge", "bg", GRAPHIC_AA, "panel interno: marca de alerta"),
    ("ai-edge", "surface", GRAPHIC_AA, "panel interno: borde de la IA"),
]


def _declarations(block: str) -> dict[str, str]:
    return {name: value.strip() for name, value in re.findall(r"--([\w-]+)\s*:\s*([^;]+);", block)}


def _block_after(css: str, start: int) -> str:
    """Devuelve el contenido entre la llave que abre en ``start`` y su cierre."""
    open_at = css.index("{", start)
    depth = 0
    for index in range(open_at, len(css)):
        if css[index] == "{":
            depth += 1
        elif css[index] == "}":
            depth -= 1
            if depth == 0:
                return css[open_at + 1:index]
    raise ValueError("llave sin cerrar")


def load_themes(css: str | None = None) -> dict[str, dict[str, str]]:
    css = STYLES.read_text(encoding="utf-8") if css is None else css
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    light_root = re.search(r"(?m)^:root\s*\{", css)
    assert light_root, ":root del tema claro"
    return {"claro": _declarations(_block_after(css, light_root.start()))}


def resolve(tokens: dict[str, str], name: str, seen: frozenset[str] = frozenset()) -> str:
    if name in seen:
        raise ValueError(f"referencia circular en --{name}")
    value = tokens[name]
    ref = re.fullmatch(r"var\(--([\w-]+)\)", value)
    if ref:
        return resolve(tokens, ref.group(1), seen | {name})
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}|#[0-9A-Fa-f]{3}", value):
        raise ValueError(f"--{name} no es un color hexadecimal: {value}")
    if len(value) == 4:
        value = "#" + "".join(ch * 2 for ch in value[1:])
    return value.upper()


def _luminance(hex_color: str) -> float:
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(foreground: str, background: str) -> float:
    high, low = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def report() -> list[tuple[str, str, str, str, str, float, float]]:
    rows = []
    for theme, tokens in load_themes().items():
        for fg, bg, minimum, _use in PAIRS:
            fg_hex, bg_hex = resolve(tokens, fg), resolve(tokens, bg)
            rows.append((theme, fg, bg, fg_hex, bg_hex, contrast(fg_hex, bg_hex), minimum))
    return rows


def test_contrast_formula_matches_wcag_reference_values() -> None:
    assert round(contrast("#000000", "#FFFFFF"), 2) == 21.0
    assert round(contrast("#777777", "#FFFFFF"), 2) == 4.48


@pytest.mark.parametrize("theme", ["claro"])
@pytest.mark.parametrize(("fg", "bg", "minimum", "use"), PAIRS, ids=[f"{fg}/{bg}-{'texto' if m == TEXT_AA else 'grafico'}" for fg, bg, m, _ in PAIRS])
def test_token_pair_meets_aa(theme: str, fg: str, bg: str, minimum: float, use: str) -> None:
    tokens = load_themes()[theme]
    ratio = contrast(resolve(tokens, fg), resolve(tokens, bg))
    assert ratio >= minimum, f"{theme}: --{fg} sobre --{bg} ({use}) = {ratio:.2f}:1 < {minimum}:1"


def test_only_the_light_lima_palette_ships() -> None:
    light = load_themes()["claro"]
    assert light["lime"].upper() == "#C6F432" and light["bg"].upper() == "#F4F5F0"
    assert light["photo-bg"].upper() == "#FFFFFF"
    css = STYLES.read_text(encoding="utf-8")
    assert "prefers-color-scheme" not in css and "color-scheme: dark" not in css
    assert "color-scheme: light;" in css
    assert '"Space Grotesk"' in light["font-display"] and "Inter" in light["font-body"]
    # Se conservan los esqueletos y el movimiento del pulido (#117).
    assert "@keyframes skel" in css and "@keyframes card-in" in css and "prefers-reduced-motion" in css


def test_theme_color_meta_is_light_only() -> None:
    html = (APP_DIR / "index.html").read_text(encoding="utf-8")
    assert '<meta name="color-scheme" content="light">' in html
    metas = re.findall(r'<meta name="theme-color" content="(#[0-9A-Fa-f]{6})"( media="[^"]*")?>', html)
    assert metas == [(resolve(load_themes()["claro"], "charcoal"), "")]


def test_no_hard_coded_colors_that_ignore_the_theme() -> None:
    """Fuera de los tokens sólo quedan colores de superficies siempre oscuras (intro, escáner)."""
    css = re.sub(r"/\*.*?\*/", "", STYLES.read_text(encoding="utf-8"), flags=re.S)
    allowed_rules = (".intro", "dialog.scanner", ".scanner-", ".scan-hit")
    offenders = []
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        selector = selector.strip()
        if selector.startswith(":root") or selector.startswith(allowed_rules):
            continue
        for decl in body.split(";"):
            if decl.strip().startswith("--"):
                continue
            if re.search(r"#[0-9A-Fa-f]{3,8}\b", decl):
                offenders.append(f"{selector} {{ {decl.strip()} }}")
    assert offenders == []


if __name__ == "__main__":
    print(f"{'tema':7} {'par':28} {'colores':17} {'ratio':>6}  AA")
    for theme, fg, bg, fg_hex, bg_hex, ratio, minimum in report():
        verdict = "sí" if ratio >= minimum else "NO"
        kind = "texto" if minimum == TEXT_AA else "gráfico"
        print(f"{theme:7} {fg + '/' + bg:28} {fg_hex}/{bg_hex} {ratio:6.2f}  {verdict} ({kind} ≥ {minimum})")
