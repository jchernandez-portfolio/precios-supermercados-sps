"""Identidad de producto v2: normalización semántica y candidatos conservadores.

Esta capa separa cuatro pasos que no deben confundirse:

1. normalización de texto/unidades;
2. extracción de atributos semánticos;
3. generación de candidatos;
4. decisión de identidad.

Los candidatos sin identificador global siguen siendo ``review_required``. El
ranking numérico sólo ordena la cola privada; nunca confirma identidad ni puede
compensar un conflicto material. Este módulo es inicialmente audit-only: no
reemplaza el motor persistido hasta que una muestra real demuestre su precisión.
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_HALF_UP
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Iterable

from .gtin_policy import (
    SKU_DERIVED_GTIN_SUPERMARKETS,
    restricted_circulation_reason,
    restricted_gtin_master_partition,
)
from .identifiers import generate_gtin_product_id
from .product_homologation import (
    ExactGtinGroup,
    HomologationResult,
    MatchCandidate,
    PresentationSignature,
    ProductHomologationError,
    ProductProfile,
    SourceProductRecord,
    TaxonomyAssignment,
    assign_taxonomy,
    fold_text,
    normalize_brand,
    presentations_compatible,
    profile_product,
    resolve_presentation,
)

IDENTITY_NORMALIZATION_VERSION = "product-homologation-v2.4"

_GENERIC_BRANDS = frozenset(
    {
        "rms",
        "comandes",
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

# Alias demostrados en los catálogos actuales. Sólo se usan cuando la marca
# fuente es genérica/ausente o para detectar una contradicción explícita.
_BRAND_ALIASES = {
    "bonovo": "bonovo",
    "don cristobal": "don cristobal",
    "el ranchero": "el ranchero",
    "gallina feliz": "gallina feliz",
    "great value": "great value",
    "marca marketside": "marketside",
    "marketside": "marketside",
    "mister huevo": "mister huevo",
    "norteno": "norteno",
    "nutri yema": "nutri yema",
    "nutriyema": "nutri yema",
    "rica yema": "rica yema",
    "suli": "suli",
}

# Variantes ortográficas demostradas por grupos con el mismo GTIN. No se usa
# distancia difusa para marcas: cada alias debe incorporarse con evidencia.
_BRAND_CANONICAL_ALIASES = {
    "buchanan s": "buchanan",
    "elmigo": "el migo",
    "mott s": "mott",
    "wrigleys": "wrigley",
}

_COUNT_ALIASES = r"u|uni|un|und|unds|unid|unids|ud|uds|unidad|unidades"
_COUNT_RE = re.compile(
    rf"(?<!\w)(?P<count>\d{{1,4}})\s*(?P<unit>{_COUNT_ALIASES})(?!\w)",
    re.IGNORECASE,
)
_EGG_COUNT_RE = re.compile(
    rf"(?<!\w)(?P<count>\d{{1,3}})\s*(?P<size>xl|g|m|p)?\s*(?P<unit>{_COUNT_ALIASES})(?!\w)",
    re.IGNORECASE,
)
_MG_RE = re.compile(r"(?<!\w)(?P<amount>\d+(?:[.,]\d+)?)\s*mg(?!\w)", re.IGNORECASE)
_LIBRA_RE = re.compile(r"(?<!\w)(?P<amount>\d+(?:[.,]\d+)?)\s*libras?(?!\w)", re.IGNORECASE)
_GRAMOS_RE = re.compile(r"(?<!\w)(?P<amount>\d+(?:[.,]\d+)?)\s*grs?(?:\.)?(?!\w)", re.IGNORECASE)
_LITROS_RE = re.compile(r"(?<!\w)(?P<amount>\d+(?:[.,]\d+)?)\s*lts?(?:\.)?(?!\w)", re.IGNORECASE)
_FRACTION_WITH_UNIT_RE = re.compile(
    r"(?<![\d/])(?P<fraction>1\s*/\s*2|1\s*/\s*4|3\s*/\s*4)"
    r"(?=\s*(?:mg|kg|grs?|gramos?|g|lbs?|libras?|oz|onzas?|ml|lts?|litros?|l)(?!\w))",
    re.IGNORECASE,
)
# Envases contables que, seguidos de "de/x <cantidad>", declaran la cantidad por
# envase: "12 latas de 355 ml", "6 botellas x 600 ml", "10 sobres de 25 g".
_CONTAINER_ALIASES = (
    r"latas?|botellas?|botellitas?|bolsitas?|sobres?|sachets?|cajitas?|vasitos?|"
    r"tarritos?|frascos?|barras?"
)
_NATURAL_MULTIPACK_RE = re.compile(
    rf"(?<!\w)(?P<count>\d{{1,3}})\s*(?:{_COUNT_ALIASES}|{_CONTAINER_ALIASES})\s*"
    r"(?:de|x)\s*(?P<amount>\d+(?:[.,]\d+)?)\s*"
    r"(?P<unit>mg|kg|grs?|gramos?|g|lbs?|libras?|oz|onzas?|ml|lts?|litros?|l)(?!\w)",
    re.IGNORECASE,
)
_COMPACT_SLASH_MULTIPACK_RE = re.compile(
    r"(?<![\d/])(?P<count>\d{1,3})\s*/\s*(?P<amount>\d+(?:[.,]\d+)?)\s*"
    r"(?P<unit>mg|kg|grs?|gramos?|g|lbs?|libras?|oz|onzas?|ml|lts?|litros?|l)(?!\w)",
    re.IGNORECASE,
)

# Combos, kits y promociones multi-producto no son comparables con el producto
# individual. Se detectan sobre el nombre fuente sin plegar porque "+" importa.
_BUNDLE_TERMS_RE = re.compile(
    r"(?<!\w)(?:combo|combos|kit|gratis|obsequio|incluye|incluyen)(?!\w)",
    re.IGNORECASE,
)
_PROMO_MULTIBUY_RE = re.compile(r"(?<![\w/.,])(?P<take>[2-4])\s*[x×]\s*(?P<pay>[1-3])(?![\w/.,])", re.IGNORECASE)
_QUANTITY_PLUS_RE = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:mg|kg|grs?|gramos?|g|lbs?|libras?|oz|onzas?|ml|lts?|litros?|l|%)\s*\+"
    r"|(?<=\s)\+\s*\d+(?:[.,]\d+)?\s*(?:mg|kg|grs?|gramos?|g|lbs?|libras?|oz|onzas?|ml|lts?|litros?|l|%)(?!\w)",
    re.IGNORECASE,
)


def is_bundle_name(name: str | None) -> bool:
    """Combo/kit/promoción multi-producto declarado en el nombre fuente."""

    if not name:
        return False
    text = unicodedata.normalize("NFKC", name)
    if _BUNDLE_TERMS_RE.search(text) or _QUANTITY_PLUS_RE.search(text):
        return True
    return any(
        int(match.group("take")) > int(match.group("pay"))
        for match in _PROMO_MULTIBUY_RE.finditer(text)
    )


_EGG_FALSE_CONTEXT = frozenset(
    {"tallarin", "tallarines", "fideo", "fideos", "mayonesa", "kinder", "toro"}
)

_VARIANT_GROUPS = (
    frozenset({"clasico", "orig", "original", "normal", "regular"}),
    frozenset({"zero", "sin azucar"}),
    frozenset({"light", "diet", "bajo en azucar"}),
    frozenset({"entera", "entero"}),
    frozenset({"descremada", "descremado"}),
    frozenset({"semidescremada", "semidescremado"}),
)
# Índices de _VARIANT_GROUPS que son una formulación distinta de la estándar.
_NON_DEFAULT_FORMULATION_GROUPS = frozenset({1, 2})
_FLAVOR_ALIASES = {
    "apple": "manzana",
    "arandano": "arándano",
    "arandanos": "arándano",
    "banana": "banano",
    "banano": "banano",
    "blackberry": "mora",
    "blueberry": "arándano",
    "cacao": "chocolate",
    "cajeta": "cajeta",
    "camaron": "camarón",
    "caramel": "caramelo",
    "caramelo": "caramelo",
    "carrot": "zanahoria",
    "cereza": "cereza",
    "cherry": "cereza",
    "chocolate": "chocolate",
    "chicken": "pollo",
    "coco": "coco",
    "coconut": "coco",
    "durazno": "melocotón",
    "fresa": "fresa",
    "fresas": "fresa",
    "frambuesa": "frambuesa",
    "fig": "higo",
    "grape": "uva",
    "guava": "guayaba",
    "guayaba": "guayaba",
    "higo": "higo",
    "higos": "higo",
    "lemon": "limón",
    "lima": "lima",
    "lime": "lima",
    "limon": "limón",
    "limon rosa": "limón_rosa",
    "lavanda": "lavanda",
    "lavender": "lavanda",
    "mandarina": "mandarina",
    "mango": "mango",
    "manzana": "manzana",
    "maracuya": "maracuyá",
    "melocoton": "melocotón",
    "mora": "mora",
    "moras": "mora",
    "naranja": "naranja",
    "orange": "naranja",
    "papaya": "papaya",
    "passionfruit": "maracuyá",
    "peach": "melocotón",
    "pear": "pera",
    "pera": "pera",
    "pina": "piña",
    "pineapple": "piña",
    "raspberry": "frambuesa",
    "shrimp": "camarón",
    "sandia": "sandía",
    "strawberry": "fresa",
    "sweet potato": "camote",
    "tangerine": "mandarina",
    "uva": "uva",
    "vainilla": "vainilla",
    "vanilla": "vainilla",
    "vegetable": "vegetales",
    "vegetales": "vegetales",
    "watermelon": "sandía",
    "zanahoria": "zanahoria",
    "anis": "anís",
    "anise": "anís",
    "camote": "camote",
    "pollo": "pollo",
    "tutti frutti": "tutti_frutti",
}

_PACKAGING_ALIASES = {
    "bolsa": "bag",
    "botella": "bottle",
    "caja": "box",
    "carton": "box",
    "doy pack": "doypack",
    "doypack": "doypack",
    "lata": "can",
}

_PRODUCT_ATTRIBUTE_ALIASES = {
    "Aceite comestible": {
        "oil_blend": {
            "blend": "blend",
            "mezcla": "blend",
        },
        "oil_base": {
            "aceite vegetal": "vegetal",
            "aguacate": "aguacate",
            "canola": "canola",
            "coco": "coco",
            "girasol": "girasol",
            "maiz": "maíz",
            "oliva": "oliva",
            "soya": "soya",
        },
    },
    "Cerveza": {
        "beer_color": {
            "clara": "light",
            "dunkel": "dark",
            "oscura": "dark",
        },
    },
    "Frijol": {
        "bean_kind": {
            "blanco": "blanco",
            "blancos": "blanco",
            "carita": "carita",
            "negro": "negro",
            "negros": "negro",
            "pinto": "pinto",
            "pintos": "pinto",
            "rojo": "rojo",
            "rojos": "rojo",
        },
        "bean_style": {
            "enteros": "entero",
            "molidos": "refrito",
            "refritos": "refrito",
            "volteados": "refrito",
        },
    },
    "Pasta": {
        "pasta_shape": {
            "codito": "codito",
            "coditos": "codito",
            "espagueti": "spaghetti",
            "fusilli": "tornillo",
            "lasagna": "lasagna",
            "lasana": "lasagna",
            "macaroni": "macarrón",
            "macarron": "macarrón",
            "penne": "penne",
            "pluma": "penne",
            "spaghetti": "spaghetti",
            "tagliatelle": "tagliatelle",
            "tornillo": "tornillo",
        },
    },
    "Pañal": {
        "diaper_line": {
            "classic": "classic",
            "prot": "protect",
            "protect": "protect",
        },
    },
    "Queso": {
        "cheese_kind": {
            "americano": "americano",
            "cheddar": "cheddar",
            "mozzarella": "mozzarella",
            "parmesano": "parmesano",
            "semi seco": "semiseco",
            "suizo": "suizo",
            "seco": "seco",
        },
    },
    "Jugo": {
        "pulp_status": {
            "c pulpa": "with_pulp",
            "con pulpa": "with_pulp",
            "s pulpa": "without_pulp",
            "sin pulpa": "without_pulp",
        },
    },
    "Salsa": {
        "heat_level": {
            "hot": "hot",
            "medium": "medium",
            "mild": "mild",
        },
        "pepper_kind": {
            "chile cabro": "cabro",
            "habanero": "habanero",
            "jalapeno": "jalapeño",
        },
    },
    "Sardina": {
        "spice_status": {
            "picante": "spicy",
        },
    },
    "Sopa": {
        "spice_status": {
            "con chile": "spicy",
            "picante": "spicy",
        },
    },
    "Atún": {
        "smoke_status": {
            "ahumado": "smoked",
        },
    },
    "Vino": {
        "wine_color": {
            "blanco": "white",
            "red": "red",
            "rosado": "rose",
            "rose": "rose",
            "tinto": "red",
            "white": "white",
        },
    },
}

_STRONG_ATTRIBUTE_PREFIXES = (
    "bean_kind:",
    "bean_style:",
    "beer_color:",
    "cheese_kind:",
    "diaper_size:",
    "diaper_stage:",
    "diaper_line:",
    "egg_size:",
    "flavor:",
    "hair_shade:",
    "heat_level:",
    "oil_base:",
    "oil_blend:",
    "pasta_shape:",
    "pepper_kind:",
    "pulp_status:",
    "smoke_status:",
    "spice_status:",
    "wine_color:",
)

_MATCH_STOPWORDS = frozenset(
    {
        "de",
        "del",
        "la",
        "el",
        "los",
        "las",
        "y",
        "con",
        "para",
        "en",
        "por",
        "doy",
        "pack",
        "packs",
        "paquete",
        "paquetes",
        "caja",
        "carton",
        "botella",
        "bolsa",
        "u",
        "uni",
        "un",
        "und",
        "unds",
        "unid",
        "unids",
        "ud",
        "uds",
        "unidad",
        "unidades",
        "mg",
        "g",
        "gr",
        "grs",
        "gramo",
        "gramos",
        "kg",
        "lb",
        "lbs",
        "libra",
        "libras",
        "oz",
        "ml",
        "l",
        "lt",
        "litro",
        "litros",
        "x",
    }
)


@dataclass(frozen=True, slots=True)
class BrandResolution:
    canonical_brand: str | None
    source: str
    source_brand: str | None
    name_brand: str | None
    conflict: bool = False


@dataclass(frozen=True, slots=True)
class CandidateEvidence:
    left_source_record_id: str
    right_source_record_id: str
    decision_state: str
    confidence_level: str
    ranking_score: Decimal
    matching_signals: tuple[str, ...]
    conflict_signals: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CanonicalPresentation:
    """Campos derivados de presentación sin sustituir evidencia fuente."""

    raw_presentation: str | None
    normalized_quantity: Decimal | None
    normalized_unit: str | None
    normalized_pack_count: int | None
    canonical_total: Decimal | None
    display_presentation: str | None
    status: str


def _phrase_present(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def build_brand_lexicon(records: Iterable[SourceProductRecord]) -> frozenset[str]:
    """Crea un vocabulario de marcas ya observadas como marcas fuente reales."""
    values = {
        brand
        for record in records
        if (brand := canonicalize_brand_key(record.source_brand)) is not None
    }
    values.update(_BRAND_ALIASES.values())
    return frozenset(values)


def canonicalize_brand_key(value: str | None) -> str | None:
    """Normaliza sólo alias de marca respaldados por evidencia del catálogo."""

    folded = fold_text(value)
    if folded is None or folded in _GENERIC_BRANDS:
        return None
    brand = normalize_brand(value)
    if brand is None:
        return None
    return _BRAND_CANONICAL_ALIASES.get(brand, brand)


def source_brand_role(value: str | None) -> str:
    """Clasifica el campo fuente sin atribuir fabricante ni propietario."""

    folded = fold_text(value)
    if folded is None:
        return "unknown"
    if folded in _GENERIC_BRANDS:
        return "retailer_placeholder"
    return "retailer_reported_brand"


def _brands_in_name(name: str, brand_lexicon: frozenset[str]) -> set[str]:
    """Busca frases de marca en tiempo acotado por longitud del nombre.

    Recorrer todo el vocabulario para cada producto era cuadrático en catálogos
    reales. Los n-gramas conservan la misma evidencia léxica y escalan linealmente.
    """

    tokens = name.split()
    maximum_words = max((len(value.split()) for value in brand_lexicon), default=1)
    matches: set[str] = set()
    for width in range(1, min(maximum_words, len(tokens)) + 1):
        for start in range(0, len(tokens) - width + 1):
            phrase = " ".join(tokens[start : start + width])
            if phrase in brand_lexicon:
                matches.add(phrase)
    return matches


def resolve_brand(
    record: SourceProductRecord,
    *,
    brand_lexicon: frozenset[str],
) -> BrandResolution:
    source_brand = canonicalize_brand_key(record.source_brand)
    name = fold_text(record.source_name) or ""
    matches = _brands_in_name(name, brand_lexicon)

    for alias, canonical in _BRAND_ALIASES.items():
        if _phrase_present(name, alias):
            matches.add(canonical)
    for alias, canonical in _BRAND_CANONICAL_ALIASES.items():
        if _phrase_present(name, alias):
            matches.add(canonical)

    # Preferimos el match de frase más larga cuando todos los matches son
    # anidados (p. ej. una marca de dos palabras que contiene otra corta).
    if len(matches) > 1:
        longest = max(matches, key=len)
        if all(candidate == longest or _phrase_present(longest, candidate) for candidate in matches):
            matches = {longest}

    name_brand = next(iter(matches)) if len(matches) == 1 else None
    if source_brand is not None:
        if name_brand is not None and name_brand != source_brand:
            return BrandResolution(
                canonical_brand=None,
                source="source_conflict",
                source_brand=source_brand,
                name_brand=name_brand,
                conflict=True,
            )
        return BrandResolution(source_brand, "source", source_brand, name_brand)
    if name_brand is not None:
        return BrandResolution(name_brand, "name_known_brand", None, name_brand)
    return BrandResolution(None, "missing", None, None)


def _egg_false_context(text: str) -> bool:
    tokens = set(text.split())
    return bool(tokens & _EGG_FALSE_CONTEXT)


def is_shell_egg(record: SourceProductRecord, taxonomy: TaxonomyAssignment | None = None) -> bool:
    text = fold_text(record.source_name) or ""
    taxonomy = taxonomy or assign_taxonomy(record)
    if taxonomy.product_type != "Huevo" or _egg_false_context(text):
        return False
    if "claras" in text.split() or "clara" in text.split():
        return False
    return bool(re.search(r"\bhuevos?\b", text))


def canonical_egg_size(record: SourceProductRecord, taxonomy: TaxonomyAssignment | None = None) -> str | None:
    if not is_shell_egg(record, taxonomy):
        return None
    text = fold_text(record.source_name) or ""
    if re.search(r"\b(?:extra grande|extra grandes|xl)\b", text) or re.search(
        rf"\d+\s*xl\s*(?:{_COUNT_ALIASES})\b", text
    ):
        return "Extra grande"
    if re.search(r"\bjumbo\b", text):
        return "Jumbo"
    if re.search(r"\b(?:pequeno|pequenos|pequena|pequenas)\b", text) or re.search(r"\bcarton\s+p\b", text):
        return "Pequeño"
    if re.search(r"\b(?:mediano|medianos|mediana|medianas)\b", text) or re.search(r"\bcarton\s+m\b", text) or re.search(r"\bhuevos?\s+(?:liso\s+)?m\b", text):
        return "Mediano"
    if re.search(r"\b(?:grande|grandes)\b", text) or re.search(r"\bcarton\s+g\b", text) or re.search(r"\bhuevos?\s+g\b", text) or re.search(
        rf"\d+\s*g\s*(?:{_COUNT_ALIASES})\b", text
    ):
        return "Grande"
    return None


def assign_taxonomy_v2(record: SourceProductRecord) -> TaxonomyAssignment:
    """Corrige falsos positivos demostrados antes de aplicar la taxonomía v1."""
    text = fold_text(record.source_name) or ""
    tokens = set(text.split())
    if "abrillantador" in tokens and "calzado" in tokens:
        return TaxonomyAssignment(
            "Hogar",
            "Cuidado del calzado",
            "Abrillantador de calzado",
            "v2_shoe_polish_before_cafe_color",
        )
    if (
        "miel de abeja" in text
        and not (
            tokens
            & {"cereal", "galleta", "galletas", "jabon", "shampoo", "te", "yogurt"}
        )
    ):
        return TaxonomyAssignment("Alimentos", "Miel", "Miel", "v2_honey_before_panal_brand")
    if "yogur" in tokens:
        return TaxonomyAssignment("Alimentos", "Lácteos", "Yogurt", "v2_yogur_alias")
    if {"tallarin", "tallarines", "fideo", "fideos"} & tokens:
        rule_id = "v2_egg_noodle" if "huevo" in tokens else "v2_noodle_alias"
        return TaxonomyAssignment("Alimentos", "Pastas", "Pasta", rule_id)
    if "mayonesa" in tokens:
        return TaxonomyAssignment("Alimentos", "Salsas y aderezos", "Mayonesa", "v2_mayonesa")
    if "kinder" in tokens and "huevo" in tokens:
        return TaxonomyAssignment("Alimentos", "Dulces y chocolates", "Chocolate", "v2_kinder_huevo")
    if "toro" in tokens and ("huevo" in tokens or "huevos" in tokens):
        return TaxonomyAssignment(None, None, None, "v2_huevos_toro_unresolved")
    return assign_taxonomy(record)


def _replace_mg(match: re.Match[str]) -> str:
    amount = Decimal(match.group("amount").replace(",", ".")) / Decimal("1000")
    return f"{format(amount.normalize(), 'f')} g"


def _replace_fraction_with_unit(match: re.Match[str]) -> str:
    fraction = match.group("fraction").replace(" ", "")
    values = {
        "1/2": Decimal("0.5"),
        "1/4": Decimal("0.25"),
        "3/4": Decimal("0.75"),
    }
    return format(values[fraction].normalize(), "f")


def _replace_natural_multipack(match: re.Match[str]) -> str:
    return f"{match.group('count')} x {match.group('amount')} {match.group('unit')}"


def _normalize_parser_text(
    value: str | None,
    *,
    shell_egg: bool,
) -> str | None:
    if value is None:
        return None
    # "ShampAguac&Sab550ml" → "ShampAguac&Sab 550ml": sin esto la cantidad pegada
    # a una palabra (frecuente en Colonial) no se reconoce como presentación.
    text = " ".join(_GLUED_LETTERS_DIGIT_RE.sub(" ", value).split())
    text = _FRACTION_WITH_UNIT_RE.sub(_replace_fraction_with_unit, text)
    text = _NATURAL_MULTIPACK_RE.sub(_replace_natural_multipack, text)
    text = _COMPACT_SLASH_MULTIPACK_RE.sub(_replace_natural_multipack, text)
    if shell_egg:
        text = _EGG_COUNT_RE.sub(lambda match: f"{match.group('count')} unidades", text)
    else:
        text = _COUNT_RE.sub(lambda match: f"{match.group('count')} unidades", text)
    text = _MG_RE.sub(_replace_mg, text)
    text = _LIBRA_RE.sub(lambda match: f"{match.group('amount')} lb", text)
    text = _GRAMOS_RE.sub(lambda match: f"{match.group('amount')} g", text)
    text = _LITROS_RE.sub(lambda match: f"{match.group('amount')} l", text)
    return text


def resolve_presentation_v2(
    record: SourceProductRecord,
    taxonomy: TaxonomyAssignment | None = None,
) -> tuple[PresentationSignature | None, str]:
    taxonomy = taxonomy or assign_taxonomy_v2(record)
    shell_egg = is_shell_egg(record, taxonomy)
    normalized = SourceProductRecord(
        source_record_id=record.source_record_id,
        supermarket_id=record.supermarket_id,
        source_name=_normalize_parser_text(record.source_name, shell_egg=shell_egg) or record.source_name,
        source_brand=record.source_brand,
        source_presentation=_normalize_parser_text(record.source_presentation, shell_egg=shell_egg),
        source_category=record.source_category,
        barcode=record.barcode,
    )
    return resolve_presentation(normalized)


def canonical_presentation_fields(
    record: SourceProductRecord,
    taxonomy: TaxonomyAssignment | None = None,
) -> CanonicalPresentation:
    """Expone cantidad/unidad/pack canónicos junto al valor raw conservado."""

    signature, status = resolve_presentation_v2(record, taxonomy)
    raw = record.source_presentation
    if signature is None:
        return CanonicalPresentation(raw, None, None, None, None, None, status)
    unit_by_dimension = {
        "mass_g": "g",
        "volume_ml": "ml",
        "count": "unit",
        "ounce": "oz",
    }
    unit = unit_by_dimension[signature.dimension]
    quantity = signature.unit_amount_base or signature.total_base
    if signature.dimension == "count":
        quantity = signature.total_base
        normalized_pack_count = 1
        display = f"{int(signature.total_base)} unidades"
    elif signature.pack_count > 1 and signature.unit_amount_base is not None:
        normalized_pack_count = signature.pack_count
        display = (
            f"{signature.pack_count} × {format(signature.unit_amount_base.normalize(), 'f')} {unit}"
        )
    else:
        normalized_pack_count = signature.pack_count
        display = f"{format(signature.total_base.normalize(), 'f')} {unit}"
    return CanonicalPresentation(
        raw_presentation=raw,
        normalized_quantity=quantity,
        normalized_unit=unit,
        normalized_pack_count=normalized_pack_count,
        canonical_total=signature.total_base,
        display_presentation=display,
        status=status,
    )


def candidate_presentations_compatible(left: PresentationSignature, right: PresentationSignature) -> bool:
    """Compatibilidad para GENERAR candidatos; nunca confirma identidad."""
    if presentations_compatible(left, right):
        return True
    if left.dimension != right.dimension or left.pack_count != right.pack_count:
        return False
    if left.dimension in {"count", "ounce"}:
        return False
    larger = max(left.total_base, right.total_base)
    difference = abs(left.total_base - right.total_base)
    relative = difference / larger
    if left.dimension == "mass_g":
        absolute_limit = Decimal("15") if larger < Decimal("1000") else Decimal("30")
        relative_limit = Decimal("0.02") if larger < Decimal("1000") else Decimal("0.015")
        return difference <= absolute_limit and relative <= relative_limit
    if left.dimension == "volume_ml":
        absolute_limit = Decimal("20") if larger < Decimal("1000") else Decimal("30")
        relative_limit = Decimal("0.02") if larger < Decimal("1000") else Decimal("0.015")
        return difference <= absolute_limit and relative <= relative_limit
    return False


def _matching_tokens(profile: ProductProfile) -> tuple[str, ...]:
    tokens = (fold_text(profile.record.source_name) or "").split()
    brand_tokens = set((profile.normalized_brand or "").split())
    type_tokens = set((fold_text(profile.taxonomy.product_type) or "").split())
    result: list[str] = []
    for token in tokens:
        if (
            token in _MATCH_STOPWORDS
            or token in brand_tokens
            or token in type_tokens
            or any(character.isdigit() for character in token)
        ):
            continue
        if re.fullmatch(r"\d+(?:[.,]\d+)?", token):
            continue
        result.append(token)
    return tuple(result)


def _name_similarity(left: ProductProfile, right: ProductProfile) -> Decimal:
    left_tokens = set(left.matching_tokens)
    right_tokens = set(right.matching_tokens)
    if not left_tokens or not right_tokens:
        return Decimal("0")
    jaccard = Decimal(len(left_tokens & right_tokens)) / Decimal(len(left_tokens | right_tokens))
    sequence = Decimal(
        str(SequenceMatcher(None, " ".join(left.matching_tokens), " ".join(right.matching_tokens)).ratio())
    )
    return (jaccard * Decimal("0.6") + sequence * Decimal("0.4")).quantize(Decimal("0.0001"))


# Palabras pegadas de catálogos abreviados (Colonial): "AlmendVainiSinAzucar946ml"
# → "Almend Vaini Sin Azucar 946 ml". Idea portada de la estandarización del
# motor probabilístico (rama rpi/homolog-engine), sin compartir módulo.
_GLUED_CAMEL_RE = re.compile(r"(?<=[a-záéíóúñü])(?=[A-ZÁÉÍÓÚÑÜ])")
_GLUED_LETTERS_DIGIT_RE = re.compile(r"(?<=[A-Za-záéíóúñÁÉÍÓÚÑ]{3})(?=\d)")
_GLUED_DIGIT_LETTERS_RE = re.compile(r"(?<=\d)(?=[A-Za-záéíóúñÁÉÍÓÚÑ]{2})")

# Abreviaturas observadas en nombres Colonial cuyo GTIN coincide con Walmart/Paiz
# (captura 2026-08-30). Sólo se usan para detectar variantes declaradas; cada
# entrada expande un token completo, nunca un prefijo libre.
NAME_ABBREVIATIONS: dict[str, str] = {
    "arand": "arandano",
    "arandan": "arandano",
    "bana": "banano",
    "banan": "banano",
    "blueb": "blueberry",
    "bluebery": "blueberry",
    "camar": "camaron",
    "choc": "chocolate",
    "choco": "chocolate",
    "chocol": "chocolate",
    "fres": "fresa",
    "lavan": "lavanda",
    "ligth": "light",
    "limo": "limon",
    "manz": "manzana",
    "mazana": "manzana",
    "meloc": "melocoton",
    "meloct": "melocoton",
    "meloctn": "melocoton",
    "naran": "naranja",
    "naranj": "naranja",
    "rasberry": "raspberry",
    "stranwberry": "strawberry",
    "strawb": "strawberry",
    "vain": "vainilla",
    "vaini": "vainilla",
    "vainil": "vainilla",
    "vainila": "vainilla",
    "vanila": "vainilla",
    "watermelo": "watermelon",
    "zanah": "zanahoria",
    "zanaho": "zanahoria",
}
# Frases (ya plegadas) equivalentes a "sin azucar": "S/Azu", "Sugar Free", "Unsw".
NAME_PHRASE_ABBREVIATIONS: dict[str, str] = {
    "s azu": "sin azucar",
    "s azuc": "sin azucar",
    "s azucar": "sin azucar",
    "sugar free": "sin azucar",
    "no sugar": "sin azucar",
    "unsw": "sin azucar",
    "unsweetened": "sin azucar",
}


def split_glued_words(name: str) -> str:
    text = _GLUED_CAMEL_RE.sub(" ", name)
    text = _GLUED_LETTERS_DIGIT_RE.sub(" ", text)
    return _GLUED_DIGIT_LETTERS_RE.sub(" ", text)


def normalize_variant_text(name: str | None) -> str:
    """Texto plegado para detectar variantes, con palabras pegadas y abreviaturas."""

    text = fold_text(split_glued_words(name or "")) or ""
    for phrase, expansion in NAME_PHRASE_ABBREVIATIONS.items():
        text = re.sub(rf"(?<!\w){re.escape(phrase)}(?!\w)", expansion, text)
    return " ".join(NAME_ABBREVIATIONS.get(token, token) for token in text.split())


@lru_cache(maxsize=131_072)
def _variant_labels(profile: ProductProfile) -> frozenset[str]:
    text = normalize_variant_text(profile.record.source_name)
    # "Zero Alcohol" (enjuague bucal) no es la formulación "zero" sin azúcar.
    text = re.sub(r"(?<!\w)zero\s+alcohol(?!\w)", "sin_alcohol", text)
    labels: set[str] = set()
    for group in _VARIANT_GROUPS:
        for label in group:
            if _phrase_present(text, label):
                labels.add(label)
    for alias, canonical in _FLAVOR_ALIASES.items():
        if _phrase_present(text, alias):
            labels.add(f"flavor:{canonical}")
    for alias, canonical in _PACKAGING_ALIASES.items():
        if _phrase_present(text, alias):
            labels.add(f"packaging:{canonical}")
    for attribute, aliases in _PRODUCT_ATTRIBUTE_ALIASES.get(
        profile.taxonomy.product_type or "",
        {},
    ).items():
        for alias, canonical in aliases.items():
            if _phrase_present(text, alias):
                labels.add(f"{attribute}:{canonical}")
    if is_bundle_name(profile.record.source_name):
        labels.add("bundle:declared")
    egg_size = canonical_egg_size(profile.record, profile.taxonomy)
    if egg_size is not None:
        labels.add(f"egg_size:{fold_text(egg_size)}")
    if profile.taxonomy.product_type == "Pañal":
        size = re.search(r"\btalla\s+(xxg|xxl|xg|xl|g|l|m|s|p)(?!\w)", text)
        stage = re.search(r"\b(?:etapa|stage)\s*([1-7])(?!\d)", text)
        if size is not None:
            labels.add(f"diaper_size:{size.group(1)}")
        for standalone_size in re.findall(
            r"(?<!\w)(xxg|xxl|xg|xl|g|l|m|s)(?!\w)",
            text,
        ):
            labels.add(f"diaper_size:{standalone_size}")
        if stage is not None:
            labels.add(f"diaper_stage:{stage.group(1)}")
    if profile.taxonomy.product_type == "Tinte para cabello":
        for shade in re.findall(
            r"(?<!\w)(?:tono\s*)?(\d{1,2}(?:[.,]\d{1,2})?)(?![\d.,])(?!\s*(?:g|gr|ml|oz)\b)",
            text,
        ):
            labels.add(f"hair_shade:{shade.replace(',', '.')}")
    return frozenset(labels)


def _hard_conflicts(left: ProductProfile, right: ProductProfile) -> tuple[str, ...]:
    conflicts: set[str] = set()
    if left.canonical_gtin is not None and right.canonical_gtin is not None and left.canonical_gtin != right.canonical_gtin:
        conflicts.add("different_valid_gtin")
    if (
        left.taxonomy.product_type is not None
        and right.taxonomy.product_type is not None
        and left.taxonomy.product_type != right.taxonomy.product_type
    ):
        conflicts.add("product_type_conflict")
    if left.normalized_brand and right.normalized_brand and left.normalized_brand != right.normalized_brand:
        conflicts.add("brand_conflict")
    if (
        left.presentation is not None
        and right.presentation is not None
        and not candidate_presentations_compatible(left.presentation, right.presentation)
    ):
        conflicts.add("presentation_conflict")

    left_labels = _variant_labels(left)
    right_labels = _variant_labels(right)
    if ("bundle:declared" in left_labels) != ("bundle:declared" in right_labels):
        conflicts.add("bundle_vs_single_conflict")
    left_sizes = {item for item in left_labels if item.startswith("egg_size:")}
    right_sizes = {item for item in right_labels if item.startswith("egg_size:")}
    if left_sizes and right_sizes and left_sizes != right_sizes:
        conflicts.add("egg_size_conflict")
    left_flavors = {item for item in left_labels if item.startswith("flavor:")}
    right_flavors = {item for item in right_labels if item.startswith("flavor:")}
    if left_flavors and right_flavors and left_flavors != right_flavors:
        conflicts.add("flavor_conflict")
    for prefix, reason in (
        ("bean_kind:", "bean_kind_conflict"),
        ("bean_style:", "bean_style_conflict"),
        ("beer_color:", "beer_color_conflict"),
        ("cheese_kind:", "cheese_kind_conflict"),
        ("diaper_size:", "diaper_size_conflict"),
        ("diaper_stage:", "diaper_stage_conflict"),
        ("diaper_line:", "diaper_line_conflict"),
        ("hair_shade:", "hair_shade_conflict"),
        ("heat_level:", "heat_level_conflict"),
        ("oil_base:", "oil_base_conflict"),
        ("oil_blend:", "oil_blend_conflict"),
        ("packaging:", "packaging_conflict"),
        ("pasta_shape:", "pasta_shape_conflict"),
        ("pepper_kind:", "pepper_kind_conflict"),
        ("pulp_status:", "pulp_status_conflict"),
        ("smoke_status:", "smoke_status_conflict"),
        ("spice_status:", "spice_status_conflict"),
        ("wine_color:", "wine_color_conflict"),
    ):
        left_values = {item for item in left_labels if item.startswith(prefix)}
        right_values = {item for item in right_labels if item.startswith(prefix)}
        if left_values and right_values and left_values != right_values:
            conflicts.add(reason)

    # Grupos de formulación: sólo contradicen si ambos productos declaran de
    # forma explícita grupos diferentes.
    def formulation_group(labels: frozenset[str]) -> int | None:
        for index, group in enumerate(_VARIANT_GROUPS):
            if any(label in labels for label in group):
                return index
        return None

    left_group = formulation_group(left_labels)
    right_group = formulation_group(right_labels)
    if left_group is not None and right_group is not None and left_group != right_group:
        conflicts.add("variant_conflict")
    return tuple(sorted(conflicts))


def _formulation_group(labels: frozenset[str]) -> int | None:
    for index, group in enumerate(_VARIANT_GROUPS):
        if any(label in labels for label in group):
            return index
    return None


def one_sided_variant_conflicts(left: ProductProfile, right: ProductProfile) -> tuple[str, ...]:
    """Variante declarada sólo por un lado; bloquea identidad automática.

    Un sabor/aroma ("fresa", "lavanda", "pollo") o una formulación no estándar
    ("zero", "sin azúcar", "light", "diet") presente en un nombre y ausente en el
    otro no se presume equivalente aunque compartan GTIN. Para candidatos sin GTIN
    no es un conflicto duro: siguen como revisión y nunca reciben STRONG.
    """

    left_labels = _variant_labels(left)
    right_labels = _variant_labels(right)
    conflicts: set[str] = set()
    # El sustantivo del tipo no es una variante: "Chocolate Ferrero Rocher" no
    # declara sabor chocolate frente a "FERRERO ROCHER Chocolates".
    type_nouns = {
        f"flavor:{fold_text(profile.taxonomy.product_type)}"
        for profile in (left, right)
        if profile.taxonomy.product_type is not None
    }
    left_flavors = any(item.startswith("flavor:") and item not in type_nouns for item in left_labels)
    right_flavors = any(item.startswith("flavor:") and item not in type_nouns for item in right_labels)
    if left_flavors != right_flavors:
        conflicts.add("one_sided_flavor_declared")
    left_group = _formulation_group(left_labels)
    right_group = _formulation_group(right_labels)
    if (left_group in _NON_DEFAULT_FORMULATION_GROUPS and right_group is None) or (
        right_group in _NON_DEFAULT_FORMULATION_GROUPS and left_group is None
    ):
        conflicts.add("one_sided_variant_declared")
    return tuple(sorted(conflicts))


def _has_asymmetric_strong_attribute(
    left: ProductProfile,
    right: ProductProfile,
) -> bool:
    """Evita llamar STRONG a un candidato con un atributo material omitido."""

    left_labels = _variant_labels(left)
    right_labels = _variant_labels(right)
    for prefix in _STRONG_ATTRIBUTE_PREFIXES:
        left_values = {item for item in left_labels if item.startswith(prefix)}
        right_values = {item for item in right_labels if item.startswith(prefix)}
        if bool(left_values) != bool(right_values):
            return True

    def formulation_declared(labels: frozenset[str]) -> bool:
        return any(any(label in labels for label in group) for group in _VARIANT_GROUPS)

    return formulation_declared(left_labels) != formulation_declared(right_labels)


def _candidate_score(left: ProductProfile, right: ProductProfile) -> tuple[Decimal, tuple[str, ...]]:
    """Score sólo para ordenar review candidates; no representa probabilidad."""
    signals: list[str] = []
    score = Decimal("0")
    if left.taxonomy.product_type == right.taxonomy.product_type and left.taxonomy.product_type:
        signals.append("same_product_type")
        score += Decimal("0.20")
    elif left.normalized_name == right.normalized_name:
        signals.append("same_folded_name_unclassified")
        score += Decimal("0.20")
    if (
        left.presentation is not None
        and right.presentation is not None
        and candidate_presentations_compatible(left.presentation, right.presentation)
    ):
        signals.append("presentation_candidate_compatible")
        score += Decimal("0.25")
    elif left.presentation is None or right.presentation is None:
        signals.append("presentation_missing")
    if left.normalized_brand and right.normalized_brand and left.normalized_brand == right.normalized_brand:
        signals.append("same_canonical_brand")
        score += Decimal("0.20")
    elif bool(left.normalized_brand) != bool(right.normalized_brand):
        signals.append("one_brand_missing")
        score += Decimal("0.05")
    similarity = _name_similarity(left, right)
    if similarity > 0:
        signals.append(f"name_similarity:{format(similarity, 'f')}")
    score += similarity * Decimal("0.45")
    return min(score, Decimal("1")).quantize(Decimal("0.0001")), tuple(signals)


def _presentation_bucket(value: PresentationSignature) -> tuple[str, int, int]:
    if value.dimension in {"count", "ounce"}:
        bucket = int(value.total_base.to_integral_value(rounding=ROUND_HALF_UP))
    else:
        bucket = int((value.total_base / Decimal("25")).to_integral_value(rounding=ROUND_HALF_UP))
    return value.dimension, value.pack_count, bucket


def _neighbor_buckets(value: PresentationSignature) -> range:
    _, _, bucket = _presentation_bucket(value)
    span = 0 if value.dimension in {"count", "ounce"} else 2
    return range(bucket - span, bucket + span + 1)


def profile_product_v2(
    record: SourceProductRecord,
    *,
    brand_lexicon: frozenset[str],
) -> ProductProfile:
    base = profile_product(record)
    taxonomy = assign_taxonomy_v2(record)
    brand = resolve_brand(record, brand_lexicon=brand_lexicon)
    presentation, presentation_status = resolve_presentation_v2(record, taxonomy)
    provisional = replace(
        base,
        normalized_brand=brand.canonical_brand,
        taxonomy=taxonomy,
        presentation=presentation,
        presentation_status=presentation_status,
    )
    return replace(provisional, matching_tokens=_matching_tokens(provisional))


_GTIN_PAIR_IGNORED_REASONS = frozenset({"brand_conflict", "different_valid_gtin", "presentation_missing"})


def _gtin_pair_conflicts(left: ProductProfile, right: ProductProfile) -> set[str]:
    """Conflictos materiales entre dos miembros de cadenas distintas con igual GTIN."""

    reasons: set[str] = set(one_sided_variant_conflicts(left, right))
    sku_derived = (
        left.record.supermarket_id in SKU_DERIVED_GTIN_SUPERMARKETS
        or right.record.supermarket_id in SKU_DERIVED_GTIN_SUPERMARKETS
    )
    for reason in _hard_conflicts(left, right):
        if reason == "presentation_conflict":
            reasons.add("cross_source_presentation_conflict")
        elif reason == "brand_conflict" and sku_derived and _sku_gtin_brand_contradiction(left, right):
            # Un GTIN derivado de SKU no basta frente a una marca contradictoria.
            reasons.add("sku_gtin_brand_conflict")
        elif reason not in _GTIN_PAIR_IGNORED_REASONS:
            reasons.add(reason)
    if sku_derived:
        if not sku_gtin_name_agreement(left, right):
            reasons.add("sku_gtin_name_disagreement")
        if _model_number_conflict(left, right):
            reasons.add("model_number_conflict")
    return reasons


# --- Acuerdo mínimo de nombre para GTIN derivados de SKU (Colonial) -----------
# Un SKU GS1 válido puede ser el código equivocado. Calibración 2026-10-01 sobre
# los 2,359 grupos Colonial: exigir al menos un token significativo común y que
# coincida al menos 1/3 de los tokens del lado más corto excluye "LOREAL Vol
# Blackest Black" vs "Lash Paradise", "DIANA Favori Mix Criollo" vs "Chicharrón
# con yuca", "D OLANCHO Chile Añejo" vs "Salsa Riberenas" y "DEL RANCHO
# Chicharron Picosit" vs "Boquita Chicharrón Picante" (234 grupos, ~10%).
SKU_NAME_MIN_OVERLAP = Decimal("0.34")
_NAME_AGREEMENT_STOPWORDS = frozenset(
    {
        "a", "al", "and", "bandeja", "bolsa", "bote", "botella", "c", "caja", "cja", "con",
        "cong", "ct", "de", "del", "doypack", "e", "ea", "el", "en", "frasco", "g", "gr",
        "gramos", "grms", "grs", "indicado", "kg", "l", "la", "las", "lata", "lb", "lbs",
        "los", "lt", "lts", "ltrs", "marca", "mas", "mg", "ml", "new", "nuevo", "o", "of",
        "onz", "oz", "p", "pack", "paquete", "para", "pet", "piezas", "pk", "plus", "por",
        "precio", "presentacion", "pz", "pzas", "pzs", "s", "sabor", "sin", "sobre", "the",
        "tipo", "u", "ud", "uds", "un", "und", "unid", "unidad", "unidades", "vidrio",
        "with", "x", "y",
    }
)
# Traducciones inglés→español observadas en nombres Colonial frente a Walmart/Paiz.
_NAME_AGREEMENT_TRANSLATIONS = {
    "almond": "almendra", "almonds": "almendra", "beef": "carne", "black": "negro",
    "bread": "pan", "cheese": "queso", "chicken": "pollo", "coffee": "cafe",
    "cookie": "galleta", "cookies": "galleta", "corn": "maiz", "cream": "crema",
    "dientes": "dental", "grape": "uva", "green": "verde", "honey": "miel",
    "juice": "jugo", "milk": "leche", "oil": "aceite", "olive": "oliva",
    "onion": "cebolla", "red": "rojo", "rice": "arroz", "sauce": "salsa",
    "soap": "jabon", "sugar": "azucar", "tea": "te", "water": "agua", "white": "blanco",
}


def _agreement_tokens(name: str, brand_tokens: frozenset[str]) -> list[str]:
    tokens: list[str] = []
    for token in normalize_variant_text(name).split():
        if (
            len(token) < 3
            or token in _NAME_AGREEMENT_STOPWORDS
            or token in brand_tokens
            or any(character.isdigit() for character in token)
        ):
            continue
        token = _NAME_AGREEMENT_TRANSLATIONS.get(token, token)
        token = fold_text(_FLAVOR_ALIASES.get(token, token)) or token
        if token not in tokens:
            tokens.append(token)
    return tokens


def _agreement_token_match(left: str, right: str) -> bool:
    """Igualdad tolerante a abreviaturas: prefijo común o alta similitud."""

    if left == right:
        return True
    common = 0
    for a, b in zip(left, right):
        if a != b:
            break
        common += 1
    shorter = min(len(left), len(right))
    if common >= 3 and (common == shorter or common >= 4):
        return True
    return shorter >= 5 and SequenceMatcher(None, left, right).ratio() >= 0.8


def sku_gtin_name_agreement(left: ProductProfile, right: ProductProfile) -> bool:
    """Acuerdo mínimo de nombre sin marca ni tamaño (sólo GTIN derivado de SKU)."""

    brand_tokens: set[str] = set()
    for value in (
        left.normalized_brand,
        right.normalized_brand,
        left.record.source_brand,
        right.record.source_brand,
    ):
        folded = fold_text(value)
        if folded:
            brand_tokens.update(folded.split())
            brand_tokens.add(folded.replace(" ", ""))
    frozen = frozenset(brand_tokens)
    left_tokens = _agreement_tokens(left.record.source_name, frozen)
    right_tokens = _agreement_tokens(right.record.source_name, frozen)
    if not left_tokens or not right_tokens:
        return False
    used: set[int] = set()
    matched = 0
    for token in left_tokens:
        for index, other in enumerate(right_tokens):
            if index not in used and _agreement_token_match(token, other):
                used.add(index)
                matched += 1
                break
    if matched == 0:
        return False
    return Decimal(matched) / Decimal(min(len(left_tokens), len(right_tokens))) >= SKU_NAME_MIN_OVERLAP


# Cosméticos: un número de tono/modelo distinto ("Light 20" vs "Light Honey 120")
# es otro artículo. Sólo se aplica cuando el nombre es de maquillaje/color.
_COSMETIC_TERMS = frozenset(
    {
        "base", "corrector", "delineador", "esmalte", "labial", "lipstick", "maquillaje",
        "mascara", "polvo", "rimel", "rubor", "sombra", "tono",
    }
)
_MODEL_NUMBER_RE = re.compile(
    r"(?<![\w.,/])#?\s*(?P<number>\d{1,4})(?![\d.,/])"
    r"(?!\s*(?:%|x|mg|kg|grs?|g|ml|lts?|l|oz|onz|lbs?|un|und|unds|uds|ud|u|unid|unidades|pz|pzs|piezas|ct|s|spf|fps)(?![a-z]))",
    re.IGNORECASE,
)


def _model_numbers(profile: ProductProfile) -> frozenset[str]:
    return frozenset(
        match.group("number").lstrip("0") or "0"
        for match in _MODEL_NUMBER_RE.finditer(split_glued_words(profile.record.source_name))
    )


def _model_number_conflict(left: ProductProfile, right: ProductProfile) -> bool:
    words = set(normalize_variant_text(left.record.source_name).split()) | set(
        normalize_variant_text(right.record.source_name).split()
    )
    if not words & _COSMETIC_TERMS and "Tinte para cabello" not in {
        left.taxonomy.product_type,
        right.taxonomy.product_type,
    }:
        return False
    left_numbers = _model_numbers(left)
    right_numbers = _model_numbers(right)
    return bool(left_numbers and right_numbers and not left_numbers & right_numbers)


def _squashed(value: str | None) -> str:
    return (fold_text(value) or "").replace(" ", "")


def _sku_gtin_brand_contradiction(left: ProductProfile, right: ProductProfile) -> bool:
    """Contradicción de marca suficiente para invalidar un GTIN derivado de SKU.

    La marca resuelta mezcla fabricante y línea ("Frito Lay" vs "Cheetos",
    "Hormel" vs "Spam") y errores tipográficos ("Marisela" vs "Marinela"). Sólo
    hay contradicción cuando ninguna marca aparece en el nombre del otro lado, no
    comparten las primeras cuatro letras y su similitud es baja.
    """

    left_brand = _squashed(left.normalized_brand)
    right_brand = _squashed(right.normalized_brand)
    if not left_brand or not right_brand or left_brand == right_brand:
        return False
    if left_brand in _squashed(right.record.source_name) or right_brand in _squashed(left.record.source_name):
        return False
    if left_brand[:4] == right_brand[:4]:
        return False
    return SequenceMatcher(None, left_brand, right_brand).ratio() < 0.75


def _member_intrinsic_conflicts(member: ProductProfile) -> set[str]:
    reasons: set[str] = set()
    if member.presentation_status == "conflict":
        reasons.add("source_presentation_conflict")
    if member.presentation_status == "ambiguous_multipack":
        reasons.add("ambiguous_multipack_presentation")
    return reasons


def _resolve_gtin_members(
    gtin: str,
    members: list[ProductProfile],
) -> tuple[list[ProductProfile], dict[str, set[str]], set[str]]:
    """Separa miembros comparables de los que tienen un conflicto atribuible.

    Orden fail-closed: (1) GTIN restringido fuera del maestro compartido, (2)
    colisión por cadena (se excluyen todos los registros de esa cadena: no se
    elige uno), (3) conflicto intrínseco del miembro, (4) conflictos por pareja.
    En (4) se retiran, en rondas, todos los miembros con el mayor número de
    conflictos; un empate retira a todos los empatados, porque la evidencia no
    permite atribuir el error a uno solo. Devuelve (restantes, excluidos, motivos
    de grupo cuando no es posible conservar un núcleo).
    """

    excluded: dict[str, set[str]] = {}
    group_reasons: set[str] = set()

    def exclude(member: ProductProfile, reasons: Iterable[str]) -> None:
        excluded.setdefault(member.record.source_record_id, set()).update(reasons)

    remaining = list(members)
    if restricted_circulation_reason(gtin) is not None:
        allowed, _ = restricted_gtin_master_partition(
            member.record.supermarket_id for member in remaining
        )
        for member in remaining:
            if allowed is None or member.record.supermarket_id not in allowed:
                exclude(member, ("restricted_gtin_outside_shared_master",))
        remaining = [member for member in remaining if member.record.source_record_id not in excluded]

    per_retailer: dict[str, int] = defaultdict(int)
    for member in remaining:
        per_retailer[member.record.supermarket_id] += 1
    for member in remaining:
        if per_retailer[member.record.supermarket_id] > 1:
            exclude(member, ("retailer_collision",))
    remaining = [member for member in remaining if member.record.source_record_id not in excluded]

    for member in remaining:
        intrinsic = _member_intrinsic_conflicts(member)
        if intrinsic:
            exclude(member, intrinsic)
    remaining = [member for member in remaining if member.record.source_record_id not in excluded]

    while True:
        degree: dict[str, int] = defaultdict(int)
        pair_reasons: dict[str, set[str]] = defaultdict(set)
        for index, left in enumerate(remaining):
            for right in remaining[index + 1 :]:
                reasons = _gtin_pair_conflicts(left, right)
                if not reasons:
                    continue
                for member in (left, right):
                    degree[member.record.source_record_id] += 1
                    pair_reasons[member.record.source_record_id].update(reasons)
        if not degree:
            break
        highest = max(degree.values())
        for member in remaining:
            source_id = member.record.source_record_id
            if degree.get(source_id) == highest:
                exclude(member, pair_reasons[source_id])
        remaining = [member for member in remaining if member.record.source_record_id not in excluded]

    if len({member.record.supermarket_id for member in remaining}) < 2:
        for reasons in excluded.values():
            group_reasons.update(reasons)
    return remaining, excluded, group_reasons


def _exact_groups(profiles: tuple[ProductProfile, ...]) -> tuple[ExactGtinGroup, ...]:
    index: dict[str, list[ProductProfile]] = defaultdict(list)
    for profile in profiles:
        if profile.canonical_gtin is not None:
            index[profile.canonical_gtin].append(profile)
    groups: list[ExactGtinGroup] = []
    for gtin, members in sorted(index.items()):
        supermarkets = sorted({member.record.supermarket_id for member in members})
        if len(supermarkets) < 2:
            continue
        remaining, excluded, group_reasons = _resolve_gtin_members(gtin, members)
        remaining_supermarkets = sorted({member.record.supermarket_id for member in remaining})
        if len(remaining_supermarkets) >= 2:
            groups.append(
                ExactGtinGroup(
                    canonical_gtin=gtin,
                    canonical_product_id=generate_gtin_product_id(gtin),
                    source_record_ids=tuple(member.record.source_record_id for member in remaining),
                    supermarket_ids=tuple(remaining_supermarkets),
                    comparison_status="ready",
                    conflict_reasons=(),
                    excluded_members=tuple(
                        (source_id, tuple(sorted(reasons)))
                        for source_id, reasons in sorted(excluded.items())
                    ),
                )
            )
            continue
        groups.append(
            ExactGtinGroup(
                canonical_gtin=gtin,
                canonical_product_id=generate_gtin_product_id(gtin),
                source_record_ids=tuple(member.record.source_record_id for member in members),
                supermarket_ids=tuple(supermarkets),
                comparison_status="review_required",
                conflict_reasons=tuple(sorted(group_reasons)),
            )
        )
    return tuple(groups)


def homologate_products_v2(
    records: Iterable[SourceProductRecord],
    *,
    candidate_threshold: Decimal = Decimal("0.72"),
) -> HomologationResult:
    if candidate_threshold < 0 or candidate_threshold > 1:
        raise ProductHomologationError("candidate_threshold_invalid")
    records = tuple(records)
    if len({record.source_record_id for record in records}) != len(records):
        raise ProductHomologationError("source_record_id_duplicate")
    brand_lexicon = build_brand_lexicon(records)
    profiles = tuple(
        sorted(
            (profile_product_v2(record, brand_lexicon=brand_lexicon) for record in records),
            key=lambda profile: profile.record.source_record_id,
        )
    )
    exact_groups = _exact_groups(profiles)

    brand_blocks: dict[tuple[str, str, int, int, str], list[ProductProfile]] = defaultdict(list)
    token_blocks: dict[tuple[str, str, int, int, str], list[ProductProfile]] = defaultdict(list)
    for profile in profiles:
        if (
            profile.taxonomy.product_type is None
            or profile.presentation is None
            or profile.presentation_status in {"conflict", "ambiguous_multipack"}
        ):
            continue
        dimension, pack_count, bucket = _presentation_bucket(profile.presentation)
        if profile.normalized_brand is not None:
            brand_blocks[(profile.taxonomy.product_type, dimension, pack_count, bucket, profile.normalized_brand)].append(profile)
        for token in set(profile.matching_tokens):
            token_blocks[(profile.taxonomy.product_type, dimension, pack_count, bucket, token)].append(profile)

    candidates: list[MatchCandidate] = []
    seen: set[tuple[str, str]] = set()

    def consider(left: ProductProfile, right: ProductProfile) -> None:
        if left.record.supermarket_id == right.record.supermarket_id:
            return
        pair = tuple(sorted((left.record.source_record_id, right.record.source_record_id)))
        if pair in seen:
            return
        seen.add(pair)
        conflicts = _hard_conflicts(left, right)
        if conflicts:
            return
        if left.canonical_gtin is not None and right.canonical_gtin is not None:
            return
        score, signals = _candidate_score(left, right)
        if score < candidate_threshold:
            return
        candidate_brand = left.normalized_brand or right.normalized_brand or "unknown"
        candidates.append(
            MatchCandidate(
                left_source_record_id=left.record.source_record_id,
                right_source_record_id=right.record.source_record_id,
                left_supermarket_id=left.record.supermarket_id,
                right_supermarket_id=right.record.supermarket_id,
                product_type=left.taxonomy.product_type or right.taxonomy.product_type or "unclassified",
                normalized_brand=candidate_brand,
                score=score,
                reason="candidate_only:" + "+".join(signals),
                status="review_required",
            )
        )

    # Captura falsos negativos obvios incluso cuando la taxonomía aún es un gap.
    exact_names: dict[str, list[ProductProfile]] = defaultdict(list)
    for profile in profiles:
        exact_names[profile.normalized_name].append(profile)
    for members in exact_names.values():
        if len({member.record.supermarket_id for member in members}) < 2:
            continue
        for index, left in enumerate(members):
            for right in members[index + 1 :]:
                consider(left, right)

    # Los índices secundarios evitan comparar cada producto contra un bloque
    # completo de marca ausente. Se comparan marcas iguales o nombres que
    # comparten al menos un token semántico, incluyendo buckets vecinos.
    for left in profiles:
        if (
            left.taxonomy.product_type is None
            or left.presentation is None
            or left.presentation_status in {"conflict", "ambiguous_multipack"}
        ):
            continue
        dimension, pack_count, _ = _presentation_bucket(left.presentation)
        pool: dict[str, ProductProfile] = {}
        for bucket in _neighbor_buckets(left.presentation):
            if left.normalized_brand is not None:
                for right in brand_blocks.get(
                    (left.taxonomy.product_type, dimension, pack_count, bucket, left.normalized_brand),
                    (),
                ):
                    pool[right.record.source_record_id] = right
            for token in set(left.matching_tokens):
                for right in token_blocks.get(
                    (left.taxonomy.product_type, dimension, pack_count, bucket, token),
                    (),
                ):
                    pool[right.record.source_record_id] = right
        for right in pool.values():
            if right.record.source_record_id != left.record.source_record_id:
                consider(left, right)
    candidates.sort(key=lambda candidate: (-candidate.score, candidate.left_source_record_id, candidate.right_source_record_id))
    return HomologationResult(profiles=profiles, exact_gtin_groups=exact_groups, candidates=tuple(candidates))


def explain_candidate(
    left: ProductProfile,
    right: ProductProfile,
) -> CandidateEvidence:
    conflicts = _hard_conflicts(left, right)
    if conflicts:
        return CandidateEvidence(
            left.record.source_record_id,
            right.record.source_record_id,
            "CONFLICT",
            "CONFLICT",
            Decimal("0"),
            (),
            conflicts,
        )
    score, signals = _candidate_score(left, right)
    if (
        left.canonical_gtin is not None
        and left.canonical_gtin == right.canonical_gtin
    ):
        level = "EXACT"
    elif (
        score >= Decimal("0.8875")
        and "presentation_candidate_compatible" in signals
        and not _has_asymmetric_strong_attribute(left, right)
        and (
            "same_canonical_brand" in signals
            or left.normalized_name == right.normalized_name
        )
    ):
        level = "STRONG"
    elif score >= Decimal("0.72"):
        level = "REVIEW"
    else:
        level = "INSUFFICIENT"
    return CandidateEvidence(
        left.record.source_record_id,
        right.record.source_record_id,
        "review_required" if level in {"STRONG", "REVIEW"} else level,
        level,
        score,
        signals,
        (),
    )


def audit_identity_quality(result: HomologationResult) -> dict[str, object]:
    """Métricas derivadas sin mutar catálogo ni histórico comercial."""
    profiles = result.profiles
    exact_multi = [group for group in result.exact_gtin_groups if len(group.supermarket_ids) >= 2]
    cluster_sizes: dict[str, int] = defaultdict(int)
    for group in exact_multi:
        cluster_sizes[str(len(group.source_record_ids))] += 1
    retailer_collisions = 0
    exact_brand_label_disagreements = 0
    exact_taxonomy_disagreements = 0
    profiles_by_id = {
        profile.record.source_record_id: profile
        for profile in profiles
    }
    for group in exact_multi:
        members = [profiles_by_id[source_id] for source_id in group.source_record_ids]
        supermarkets = [profile.record.supermarket_id for profile in members]
        if len(supermarkets) != len(set(supermarkets)):
            retailer_collisions += 1
        brands = {
            brand
            for profile in members
            if (brand := canonicalize_brand_key(profile.record.source_brand)) is not None
        }
        if len(brands) > 1:
            exact_brand_label_disagreements += 1
        product_types = {
            profile.taxonomy.product_type
            for profile in members
            if profile.taxonomy.product_type is not None
        }
        if len(product_types) > 1 or (
            product_types
            and any(profile.taxonomy.product_type is None for profile in members)
        ):
            exact_taxonomy_disagreements += 1
    profile_blockers: defaultdict[str, int] = defaultdict(int)
    retailer_distribution: defaultdict[str, int] = defaultdict(int)
    category_distribution: defaultdict[str, int] = defaultdict(int)
    presentation_status_distribution: defaultdict[str, int] = defaultdict(int)
    for profile in profiles:
        retailer_distribution[profile.record.supermarket_id] += 1
        category_distribution[profile.taxonomy.category or "__unclassified__"] += 1
        presentation_status_distribution[profile.presentation_status] += 1
        if profile.taxonomy.product_type is None:
            profile_blockers["taxonomy_missing"] += 1
        if profile.normalized_brand is None:
            profile_blockers["brand_missing_or_conflicting"] += 1
        if profile.presentation is None:
            profile_blockers[f"presentation_{profile.presentation_status}"] += 1
        if profile.canonical_gtin is None:
            profile_blockers["global_identifier_missing"] += 1
    confidence_distribution: defaultdict[str, int] = defaultdict(int)
    candidate_type_distribution: defaultdict[str, int] = defaultdict(int)
    retailer_pair_distribution: defaultdict[str, int] = defaultdict(int)
    for candidate in result.candidates:
        evidence = explain_candidate(
            profiles_by_id[candidate.left_source_record_id],
            profiles_by_id[candidate.right_source_record_id],
        )
        confidence_distribution[evidence.confidence_level] += 1
        candidate_type_distribution[candidate.product_type] += 1
        retailer_pair_distribution["|".join(sorted((candidate.left_supermarket_id, candidate.right_supermarket_id)))] += 1
    return {
        "source_products": len(profiles),
        "normalized_brand": sum(profile.normalized_brand is not None for profile in profiles),
        "missing_brand": sum(profile.normalized_brand is None for profile in profiles),
        "normalized_presentation": sum(profile.presentation is not None for profile in profiles),
        "missing_or_conflicting_presentation": sum(
            profile.presentation is None for profile in profiles
        ),
        "exact_identity_groups": len(result.exact_gtin_groups),
        "exact_comparable_groups": sum(group.comparison_status == "ready" for group in result.exact_gtin_groups),
        "exact_review_groups": sum(group.comparison_status == "review_required" for group in result.exact_gtin_groups),
        "review_candidates": len(result.candidates),
        "multi_retailer_clusters": len(exact_multi),
        "cluster_size_distribution": dict(sorted(cluster_sizes.items(), key=lambda item: int(item[0]))),
        "retailer_collision_clusters": retailer_collisions,
        "exact_gtin_brand_label_disagreement_groups": exact_brand_label_disagreements,
        "exact_gtin_taxonomy_disagreement_groups": exact_taxonomy_disagreements,
        "individual_without_global_identity": sum(profile.canonical_gtin is None for profile in profiles),
        "profile_blockers": dict(sorted(profile_blockers.items())),
        "retailer_distribution": dict(sorted(retailer_distribution.items())),
        "category_distribution": dict(sorted(category_distribution.items())),
        "presentation_status_distribution": dict(sorted(presentation_status_distribution.items())),
        "candidate_confidence_distribution": dict(sorted(confidence_distribution.items())),
        "candidate_type_distribution": dict(sorted(candidate_type_distribution.items())),
        "candidate_retailer_pair_distribution": dict(sorted(retailer_pair_distribution.items())),
    }
