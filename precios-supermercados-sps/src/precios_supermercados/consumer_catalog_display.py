"""Normalización de presentación y marca para superficies públicas B2C.

Esta capa es sólo de presentación/consumo. No cambia identidad canónica, matching,
precios ni el histórico persistido. Las inferencias de marca se limitan a aliases
explícitos presentes en el nombre del producto.
"""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

from .product_identity_v2 import display_quantity, normalize_thousands_separators


_GENERIC_BRAND_KEYS = frozenset(
    {
        "rms",
        "marca comandes",
        "sin marca",
        "sin marca definida",
        "generico",
        "generica",
        "generic",
        "no aplica",
        "n a",
        "na",
        "none",
    }
)

# Alias explícitos: además de fijar capitalización, permiten recuperar la marca
# desde el nombre cuando la fuente sólo expone placeholders conocidos o cuando
# una etiqueta fuente contradice una única marca reconocible escrita en el nombre.
_BRAND_CANONICAL = {
    "bonovo": "Bonovo",
    "don cristobal": "Don Cristobal",
    "el ranchero": "El Ranchero",
    "gallina feliz": "Gallina Feliz",
    "great value": "Great Value",
    "hellmann s": "Hellmann's",
    "hellmanns": "Hellmann's",
    "kraft": "Kraft",
    "marca marketside": "Marketside",
    "marketside": "Marketside",
    "mccormick": "McCormick",
    "member s selection": "Member's Selection",
    "members selection": "Member's Selection",
    "mister huevo": "Mister Huevo",
    "norteno": "Norteño",
    "nutri yema": "Nutri Yema",
    "nutriyema": "Nutri Yema",
    "regia": "Regia",
    "rica yema": "Rica Yema",
    "suli": "Suli",
}

_COUNT_UNIT_PATTERN = r"(?:u|uni|un|und|unds|unid|unids|ud|uds|unidad|unidades)"
_EGG_COUNT_UNIT_PATTERN = rf"(?:{_COUNT_UNIT_PATTERN}|pack|packs|paquete|paquetes)"
_COUNT_RE = re.compile(
    rf"(?<!\w)(?P<amount>\d+(?:[.,]\d+)?)\s*(?P<unit>{_COUNT_UNIT_PATTERN})(?!\w)",
    re.IGNORECASE,
)
_MASS_RE = re.compile(
    r"(?<!\w)(?P<amount>\d+(?:[.,]\d+)?)\s*(?P<unit>kg|kilogramos?|g|gr|gramos?)(?!\w)",
    re.IGNORECASE,
)
_VOLUME_RE = re.compile(
    r"(?<!\w)(?P<amount>\d+(?:[.,]\d+)?)\s*(?P<unit>ml|l|lt|litros?)(?!\w)",
    re.IGNORECASE,
)
_OUNCE_RE = re.compile(
    r"(?<!\w)(?P<amount>\d+(?:[.,]\d+)?)\s*(?P<unit>oz|onza|onzas)(?!\w)",
    re.IGNORECASE,
)
_GALLON_RE = re.compile(
    r"(?<!\w)(?P<amount>\d+(?:[.,]\d+)?)\s*(?P<unit>gal|gl|galon|galones)(?!\w)",
    re.IGNORECASE,
)
_EGG_COUNT_RE = re.compile(
    rf"(?<!\w)(?P<count>\d{{1,3}})\s*(?:p|m|g|l|xl|jumbo)?\s*{_EGG_COUNT_UNIT_PATTERN}(?!\w)",
    re.IGNORECASE,
)

_VALID_PROFILE_PRESENTATION = frozenset(
    {"confirmed", "name_only", "source_only", "name_preferred_source_conflict"}
)
_EGG_FALSE_POSITIVES = frozenset(
    {"tallarin", "fideo", "mayonesa", "claras", "clara", "kinder", "toro"}
)
_GENERIC_PRESENTATIONS = frozenset({"un", "unidad", "unidades"})


def _clean(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


def _fold(value: object) -> str | None:
    cleaned = _clean(value)
    if cleaned is None:
        return None
    decomposed = unicodedata.normalize("NFKD", cleaned)
    without_marks = "".join(
        char for char in decomposed if not unicodedata.combining(char)
    )
    normalized = re.sub(r"[^0-9a-zA-Z]+", " ", without_marks).casefold()
    return " ".join(normalized.split()) or None


def _decimal(value: object) -> Decimal | None:
    try:
        parsed = Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() and parsed > 0 else None


def _format_decimal(value: Decimal) -> str:
    # Redondeo sólo de visualización: "907.18474 g" (2 lb) → "907.18 g".
    return display_quantity(value)


def _smart_brand_case(value: str) -> str:
    """Arregla fuentes ALL CAPS/all lowercase sin reescribir marcas ya cuidadas."""
    if value.isupper() or value.islower():
        value = value.title()
        value = re.sub(r"'S\b", "'s", value)
    return value


def _brand_from_name(product_name: object) -> str | None:
    """Acepta sólo una marca conocida no ambigua escrita en el nombre."""
    name = _fold(product_name) or ""
    matches = {
        display
        for alias, display in _BRAND_CANONICAL.items()
        if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", name)
    }
    return next(iter(matches)) if len(matches) == 1 else None


def canonical_brand(raw_brand: object, product_name: object) -> str | None:
    """Devuelve marca pública estable sin publicar placeholders o conflictos obvios."""
    raw = _clean(raw_brand)
    key = _fold(raw)
    name_brand = _brand_from_name(product_name)
    if key is not None and key not in _GENERIC_BRAND_KEYS:
        raw_brand_display = _BRAND_CANONICAL.get(key, _smart_brand_case(raw))
        if name_brand is not None and name_brand != raw_brand_display:
            return name_brand
        return raw_brand_display
    return name_brand


def _is_shell_egg(product_name: object, product_type: object) -> bool:
    name = _fold(product_name) or ""
    tokens = name.split()
    if _EGG_FALSE_POSITIVES.intersection(tokens):
        return False
    type_is_egg = _fold(product_type) == "huevo"
    limit = 6 if type_is_egg else 4
    return any(token in {"huevo", "huevos"} for token in tokens[:limit])


def canonical_egg_size(product_name: object, product_type: object) -> str | None:
    """Normaliza tamaño de huevo sin confundir la G de grado con gramos."""
    if not _is_shell_egg(product_name, product_type):
        return None
    name = _fold(product_name) or ""
    if re.search(r"\b(?:extra grande|extra grandes|xl)\b", name) or re.search(
        rf"\d+\s*xl\s*{_COUNT_UNIT_PATTERN}\b", name
    ):
        return "Extra grande"
    if re.search(r"\bjumbo\b", name):
        return "Jumbo"
    if re.search(r"\b(?:pequeno|pequenos|pequena|pequenas)\b", name) or re.search(
        r"\bcarton\s+p\b", name
    ):
        return "Pequeño"
    if re.search(r"\b(?:mediano|medianos|mediana|medianas)\b", name) or re.search(
        r"\bcarton\s+m\b", name
    ) or re.search(r"\bhuevo(?:s)?\s+(?:liso\s+)?m\b", name):
        return "Mediano"
    if re.search(r"\b(?:grande|grandes)\b", name) or re.search(
        r"\bcarton\s+g\b", name
    ) or re.search(r"\bhuevo(?:s)?\s+g\b", name) or re.search(
        rf"\d+\s*g\s*{_COUNT_UNIT_PATTERN}\b", name
    ):
        return "Grande"
    return None


def _egg_count(product_name: object, source_presentation: object, product_type: object) -> int | None:
    if not _is_shell_egg(product_name, product_type):
        return None
    combined = " ".join(
        value for value in (_clean(product_name), _clean(source_presentation)) if value
    )
    matches = list(_EGG_COUNT_RE.finditer(combined))
    if not matches:
        return None
    value = int(matches[-1].group("count"))
    return value if value > 0 else None


def _from_dimension(dimension: object, total_base: object) -> str | None:
    total = _decimal(total_base)
    if total is None:
        return None
    if dimension == "count":
        if total != total.to_integral_value():
            return None
        return f"{int(total)} unidades"
    if dimension == "mass_g":
        if total >= 1000 and total % 1000 == 0:
            return f"{_format_decimal(total / 1000)} kg"
        return f"{_format_decimal(total)} g"
    if dimension == "volume_ml":
        if total >= 1000 and total % 1000 == 0:
            return f"{_format_decimal(total / 1000)} L"
        return f"{_format_decimal(total)} ml"
    if dimension == "ounce":
        return f"{_format_decimal(total)} oz"
    return None


def _from_text(value: object) -> str | None:
    text = _clean(value)
    if text is None:
        return None
    text = normalize_thousands_separators(text)
    matches: list[tuple[int, str, re.Match[str]]] = []
    for regex, kind in (
        (_COUNT_RE, "count"),
        (_MASS_RE, "mass"),
        (_VOLUME_RE, "volume"),
        (_OUNCE_RE, "ounce"),
        (_GALLON_RE, "gallon"),
    ):
        matches.extend((match.start(), kind, match) for match in regex.finditer(text))
    if not matches:
        return None
    _, kind, match = max(matches, key=lambda item: item[0])
    amount = _decimal(match.group("amount"))
    if amount is None:
        return None
    unit = match.group("unit").casefold()
    if kind == "count":
        return _from_dimension("count", amount)
    if kind == "mass":
        if unit.startswith("kg") or unit.startswith("kilo"):
            amount *= 1000
        return _from_dimension("mass_g", amount)
    if kind == "volume":
        if unit in {"l", "lt", "litro", "litros"}:
            amount *= 1000
        return _from_dimension("volume_ml", amount)
    if kind == "gallon":
        return f"{_format_decimal(amount)} gal"
    return _from_dimension("ounce", amount)


def canonical_presentation(
    *,
    source_presentation: object,
    product_name: object,
    product_type: object,
    presentation_dimension: object,
    presentation_total_base: object,
    presentation_status: object,
) -> str | None:
    """Presentación pública estable a partir de evidencia ya aceptada.

    Para huevos con cáscara, el conteo explícito del nombre tiene precedencia para
    evitar interpretar ``30G UND`` como 30 gramos: G es el tamaño/grado y UND el
    conteo. Para el resto se prioriza la firma normalizada persistida.
    """
    egg_count = _egg_count(product_name, source_presentation, product_type)
    if egg_count is not None:
        return f"{egg_count} unidades"

    if presentation_status in _VALID_PROFILE_PRESENTATION:
        normalized = _from_dimension(presentation_dimension, presentation_total_base)
        if normalized is not None:
            return normalized

    normalized = _from_text(source_presentation) or _from_text(product_name)
    if normalized is not None:
        return normalized

    # No destruimos una presentación fuente todavía no modelada (p. ej. una
    # unidad comercial rara). Los placeholders genéricos sí se ocultan.
    source = _clean(source_presentation)
    if source is not None and (_fold(source) or "") not in _GENERIC_PRESENTATIONS:
        return source
    return None
