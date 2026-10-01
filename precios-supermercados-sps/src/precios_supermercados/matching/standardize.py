"""Estandarización: registro fuente → representación comparable.

Reutiliza la normalización v2.x vigente (marca, presentación, taxonomía por
nombre) y agrega lo que el motor probabilístico necesita: nombre núcleo,
códigos, atributos de variante, tamaño canónico, departamento/tipo desde la
ruta de categoría, GTIN de identidad y precio unitario.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Iterable, Mapping, Sequence

from ..product_homologation import ProductProfile, fold_text
from ..product_identity_v2 import build_brand_lexicon, canonicalize_brand_key, profile_product_v2
from .gtin import GtinInfo, classify_gtin, identity_gtin
from .records import MatchRecord
from .taxonomy import LeafTypeMapping, SourceTaxonomy, category_key

_UNIT_TOKENS = frozenset(
    {
        "g", "gr", "grs", "gramo", "gramos", "kg", "kgs", "mg", "lb", "lbs", "libra", "libras",
        "oz", "onza", "onzas", "fl", "ml", "cc", "l", "lt", "lts", "litro", "litros", "gal",
        "galon", "galones", "gl", "u", "un", "und", "unds", "uds", "ud", "unid", "unids",
        "unidad", "unidades", "pack", "packs", "pk", "x", "pza", "pzas", "pieza", "piezas",
        "ft", "pies", "m", "mts", "cm", "mm",
    }
)
_STOPWORDS = frozenset(
    {
        "de", "del", "la", "el", "los", "las", "y", "con", "para", "en", "por", "a", "al",
        "sin", "o", "e", "the", "of", "and", "marca",
    }
)
_PACKAGING_TOKENS = frozenset(
    {
        "bolsa", "lata", "botella", "caja", "carton", "frasco", "sobre", "paquete", "doypack",
        "doy", "tetra", "pet", "vidrio", "envase", "tarro", "tubo", "bote", "bandeja",
        "presentacion", "contenido", "neto", "peso", "aprox", "aproximadamente",
    }
)
_NUMBER_RE = re.compile(r"^\d+(?:[.,]\d+)?$")
_ALNUM_CODE_RE = re.compile(r"^(?=.*\d)(?=.*[a-z])[a-z0-9]{2,}$")
_COUNT_HINTS = frozenset({"x", "unidades", "unidad", "und", "uds", "un", "u", "pack", "pk", "rollos", "rollo", "piezas", "tabletas", "capsulas"})


_CAMEL_RE = re.compile(r"(?<=[a-záéíóúñü])(?=[A-ZÁÉÍÓÚÑÜ])")
_LETTERS_DIGIT_RE = re.compile(r"(?<=[A-Za-záéíóúñÁÉÍÓÚÑ]{3})(?=\d)")
_DIGIT_LETTERS_RE = re.compile(r"(?<=\d)(?=[A-Za-záéíóúñÁÉÍÓÚÑ]{2})")


def split_glued_words(name: str) -> str:
    """Separa palabras pegadas típicas de algunos catálogos.

    ``AbrazosVainilla`` → ``Abrazos Vainilla``; ``Botell330ml`` →
    ``Botell 330 ml``. Códigos cortos como ``V8`` o ``B12`` se conservan.
    """

    text = _CAMEL_RE.sub(" ", name)
    text = _LETTERS_DIGIT_RE.sub(" ", text)
    text = _DIGIT_LETTERS_RE.sub(" ", text)
    return text


def stem(token: str) -> str:
    if len(token) > 5 and token.endswith("es"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


@dataclass(frozen=True, slots=True)
class SizeInfo:
    dimension: str
    total: float
    pack_count: int
    unit_amount: float | None

    @property
    def canonical_unit(self) -> str:
        return {"mass_g": "g", "volume_ml": "ml", "count": "unit", "ounce": "oz"}[self.dimension]


@dataclass(slots=True)
class StandardizedRecord:
    record: MatchRecord
    profile: ProductProfile
    brand: str | None
    brand_squashed: str | None
    brand_source: str
    name_tokens: tuple[str, ...]
    core_tokens: tuple[str, ...]
    codes: frozenset[str]
    variants: Mapping[str, frozenset[str]]
    size: SizeInfo | None
    presentation_status: str
    product_type: str | None
    product_type_source: str | None
    department: str | None
    category_key: str | None
    gtin: GtinInfo | None
    identity_gtin: str | None
    silver_gtin: str | None
    unit_price: float | None
    unit_price_basis: str | None
    tfidf: dict[str, float] = field(default_factory=dict)
    rare_tokens: frozenset[str] = frozenset()

    @property
    def source_record_id(self) -> str:
        return self.record.source_record_id

    @property
    def supermarket_id(self) -> str:
        return self.record.supermarket_id

    @property
    def context_id(self) -> str:
        """Contexto de precio (ubicación). En TGU cada tienda tiene su producto fuente."""

        locations = self.record.location_ids
        return locations[0] if len(locations) == 1 else self.record.supermarket_id

    @property
    def core_text(self) -> str:
        return " ".join(self.core_tokens)


def _size_info(profile: ProductProfile) -> SizeInfo | None:
    presentation = profile.presentation
    if presentation is None:
        return None
    return SizeInfo(
        dimension=presentation.dimension,
        total=float(presentation.total_base),
        pack_count=presentation.pack_count,
        unit_amount=None if presentation.unit_amount_base is None else float(presentation.unit_amount_base),
    )


class VariantLexicon:
    """Vocabulario de variantes indexado por frase de tokens (búsqueda lineal)."""

    def __init__(self, vocabulary: Mapping[str, Mapping[str, str]]) -> None:
        self.phrases: dict[tuple[str, ...], tuple[str, str]] = {}
        for family, entries in vocabulary.items():
            for term, value in entries.items():
                folded = fold_text(term)
                if folded:
                    self.phrases.setdefault(tuple(folded.split()), (family, value))
        self.max_words = max((len(phrase) for phrase in self.phrases), default=1)

    def extract(self, tokens: Sequence[str], *, excluded: Iterable[tuple[str, ...]] = ()) -> dict[str, frozenset[str]]:
        """Atributos declarados; frases largas primero y sin solaparse.

        ``excluded`` son frases (p. ej. la marca) cuyos tokens no cuentan como
        variante: "La Fresa" como marca no declara sabor fresa.
        """

        blocked = [False] * len(tokens)
        for phrase in excluded:
            width = len(phrase)
            if not width:
                continue
            for start in range(len(tokens) - width + 1):
                if tuple(tokens[start : start + width]) == phrase:
                    for index in range(start, start + width):
                        blocked[index] = True
        result: dict[str, set[str]] = {}
        for width in range(min(self.max_words, len(tokens)), 0, -1):
            for start in range(len(tokens) - width + 1):
                if any(blocked[start : start + width]):
                    continue
                hit = self.phrases.get(tuple(tokens[start : start + width]))
                if hit is None:
                    continue
                family, value = hit
                result.setdefault(family, set()).add(value)
                for index in range(start, start + width):
                    blocked[index] = True
        return {family: frozenset(values) for family, values in sorted(result.items())}


def extract_variants(
    text: str,
    vocabulary: Mapping[str, Mapping[str, str]],
    *,
    excluded_phrases: Iterable[str] = (),
) -> dict[str, frozenset[str]]:
    lexicon = VariantLexicon(vocabulary)
    excluded = [tuple((fold_text(item) or "").split()) for item in excluded_phrases]
    return lexicon.extract(text.split(), excluded=excluded)


def _codes(tokens: list[str]) -> frozenset[str]:
    """Números/códigos que no forman parte de la presentación (``No.55``, ``V8``)."""

    codes: set[str] = set()
    for index, token in enumerate(tokens):
        following = tokens[index + 1] if index + 1 < len(tokens) else ""
        previous = tokens[index - 1] if index > 0 else ""
        if _NUMBER_RE.match(token):
            if following in _UNIT_TOKENS or following in _COUNT_HINTS or previous in {"x", "pack", "pk"}:
                continue
            if following == "en" and index + 2 < len(tokens) and _NUMBER_RE.match(tokens[index + 2]):
                codes.add(f"{token}en{tokens[index + 2]}")
                continue
            if previous == "en" and index >= 2 and _NUMBER_RE.match(tokens[index - 2]):
                continue
            codes.add(token.replace(",", "."))
        elif _ALNUM_CODE_RE.match(token):
            # "500g", "12oz", "6x355ml": tamaños pegados no son códigos.
            if re.match(r"^\d+(?:[.,]\d+)?(?:" + "|".join(sorted(_UNIT_TOKENS, key=len, reverse=True)) + r")$", token):
                continue
            if re.match(r"^\d+x\d+", token):
                continue
            codes.add(token)
    return frozenset(codes)


def _core_tokens(name_tokens: list[str], brand_tokens: set[str]) -> tuple[str, ...]:
    result: list[str] = []
    for token in name_tokens:
        if token in brand_tokens or token in _STOPWORDS or token in _UNIT_TOKENS or token in _PACKAGING_TOKENS:
            continue
        if any(character.isdigit() for character in token):
            continue
        if len(token) == 1:
            continue
        result.append(stem(token))
    return tuple(result)


def _unit_price(record: MatchRecord, size: SizeInfo | None) -> tuple[float | None, str | None]:
    price = record.price
    if price is None or size is None or size.total <= 0:
        return None, None
    value = float(price)
    if size.dimension == "mass_g":
        return value / size.total * 1000.0, "HNL/kg"
    if size.dimension == "volume_ml":
        return value / size.total * 1000.0, "HNL/L"
    if size.dimension == "count":
        return value / size.total, "HNL/unit"
    if size.dimension == "ounce":
        return value / size.total, "HNL/oz"
    return None, None


class Standardizer:
    """Estandariza un universo de registros con un léxico de marcas común."""

    def __init__(
        self,
        records: Iterable[MatchRecord],
        *,
        vocabulary: Mapping[str, Mapping[str, str]],
        taxonomy: SourceTaxonomy | None = None,
        synonyms: Mapping[str, str] | None = None,
    ) -> None:
        self.records = tuple(records)
        self.synonyms = dict(synonyms or {})
        self.vocabulary = vocabulary
        self.taxonomy = taxonomy
        self.brand_lexicon = build_brand_lexicon(record.to_source_record() for record in self.records)
        self.variant_lexicon = VariantLexicon(vocabulary)
        self.leaf_types: dict[str, LeafTypeMapping] = {}

    def run(self) -> list[StandardizedRecord]:
        standardized = [self.standardize(record) for record in self.records]
        if self.taxonomy is not None:
            self.leaf_types = self.taxonomy.learn_leaf_types(
                (item.record.source_category, item.product_type)
                for item in standardized
                if item.product_type_source == "name"
            )
            for item in standardized:
                if item.product_type is None and item.category_key in self.leaf_types:
                    item.product_type = self.leaf_types[item.category_key].product_type
                    item.product_type_source = "category_leaf"
        return standardized

    def standardize(self, record: MatchRecord) -> StandardizedRecord:
        source = record.to_source_record()
        profile = profile_product_v2(source, brand_lexicon=self.brand_lexicon)
        split_name = split_glued_words(record.source_name)
        size_profile = profile
        if profile.presentation is None and split_name != record.source_name:
            size_profile = profile_product_v2(replace(source, source_name=split_name), brand_lexicon=self.brand_lexicon)
        name = fold_text(split_name) or ""
        name_tokens = [self.synonyms.get(token, token) for token in name.split()]
        brand = profile.normalized_brand
        source_brand = canonicalize_brand_key(record.source_brand)
        if brand is None:
            brand_source = "source_conflict" if source_brand is not None else "missing"
        elif source_brand == brand:
            brand_source = "source"
        else:
            brand_source = "name_known_brand"
        brand_tokens: set[str] = set()
        excluded: list[tuple[str, ...]] = []
        for candidate in {brand, fold_text(record.source_brand)}:
            if candidate:
                brand_tokens.update(candidate.split())
                excluded.append(tuple(candidate.split()))
        variants = self.variant_lexicon.extract(name_tokens, excluded=excluded)
        size = _size_info(size_profile)
        product_type = profile.taxonomy.product_type or size_profile.taxonomy.product_type
        product_type_source = "name" if product_type is not None else None
        department = None
        if self.taxonomy is not None:
            department = self.taxonomy.department_for(record.source_category)
            if product_type is None:
                product_type = self.taxonomy.type_for(record.source_category)
                product_type_source = "category_rule" if product_type is not None else None
        name_category = profile.taxonomy.category or size_profile.taxonomy.category
        if department is None and name_category is not None:
            department = name_category
        gtin = classify_gtin(record.barcode)
        silver = identity_gtin(record.silver_gtin) or identity_gtin(record.barcode)
        unit_price, basis = _unit_price(record, size)
        return StandardizedRecord(
            record=record,
            profile=profile,
            brand=brand,
            brand_squashed=None if brand is None else brand.replace(" ", ""),
            brand_source=brand_source,
            name_tokens=tuple(name_tokens),
            core_tokens=_core_tokens(name_tokens, brand_tokens),
            codes=_codes(name_tokens),
            variants=variants,
            size=size,
            presentation_status=size_profile.presentation_status,
            product_type=product_type,
            product_type_source=product_type_source,
            department=department,
            category_key=category_key(record.source_category),
            gtin=gtin,
            identity_gtin=None if gtin is None or gtin.restricted else gtin.gtin14,
            silver_gtin=silver,
            unit_price=unit_price,
            unit_price_basis=basis,
        )


def size_bucket(size: SizeInfo | None, step: float) -> tuple[str, int] | None:
    if size is None or size.total <= 0:
        return None
    return size.dimension, int(math.floor(math.log(size.total) / step))


def decimal_text(value: Decimal | float | None) -> str | None:
    if value is None:
        return None
    return format(Decimal(str(value)).normalize(), "f")
