"""Vectores de comparación por pareja (niveles discretos estilo Splink).

Cada campo produce un nivel discreto; el modelo Fellegi–Sunter aprende m/u por
nivel. El GTIN se compara aparte como regla determinista (llave primaria GS1)
y nunca entra al modelo, de modo que la evaluación con etiquetas GTIN es ciega
al GTIN.

Además de los niveles, la comparación produce:

- ``hard_conflicts``: contradicciones que bloquean cualquier unión (política
  v1: marca, tipo, presentación, variante, GTIN distinto);
- ``auto_caps``: razones por las que la pareja, aunque puntúe alto, sólo puede
  ir a revisión (onza sin ``fl oz``, tamaño ausente, variante declarada de un
  solo lado, etc.).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from .standardize import StandardizedRecord, stem
from .taxonomy import departments_compatible
from .text import cosine, jaro_winkler, soft_token_overlap, tokens_match

OUNCE_TO_G = 28.349523125
FL_OUNCE_TO_ML = 29.5735295625

FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("brand", ("exact", "fuzzy", "cross_name", "in_other_name", "one_missing", "both_missing", "conflict")),
    ("name", ("very_high", "high", "medium", "low", "very_low")),
    ("name_diff", ("none", "one_side_minor", "one_side_major", "both_minor", "both_major")),
    ("size", ("exact", "close", "near", "ounce_bridge", "missing", "conflict")),
    ("pack", ("same_multi", "same_single", "missing", "conflict")),
    ("variant", ("agree", "none", "one_sided", "partial", "conflict")),
    ("codes", ("agree", "none", "one_sided", "partial", "conflict")),
    ("type", ("same_type", "type_partial", "same_department", "unknown", "department_conflict", "type_conflict")),
    ("price", ("ratio_le_1_3", "ratio_le_2", "ratio_le_3", "ratio_gt_3", "missing")),
)
FIELD_NAMES = tuple(name for name, _ in FIELDS)
FIELD_LEVELS = dict(FIELDS)
GTIN_LEVELS = ("same", "different", "restricted", "missing")

HARD_CONFLICT_LEVELS = {
    "brand": {"conflict"},
    "size": {"conflict"},
    "pack": {"conflict"},
    "variant": {"conflict"},
    "codes": {"conflict"},
    "type": {"type_conflict"},
}
AUTO_CAP_LEVELS = {
    "size": {"near", "ounce_bridge", "missing"},
    "brand": {"one_missing", "both_missing"},
    "variant": {"one_sided", "partial"},
    "codes": {"one_sided", "partial"},
}


@dataclass(frozen=True, slots=True)
class ComparisonSettings:
    name_levels: tuple[float, ...] = (0.88, 0.74, 0.60, 0.45)
    exact_relative: float = 0.005
    exact_absolute: float = 1.5
    close_relative: float = 0.02
    near_relative: float = 0.06
    ounce_bridge_relative: float = 0.03
    price_ratio_levels: tuple[float, ...] = (1.3, 2.0, 3.0)
    implicit_defaults: Mapping[str, frozenset[str]] = field(default_factory=dict)

    @classmethod
    def from_config(
        cls,
        section: Mapping[str, object],
        implicit_defaults: Mapping[str, frozenset[str]] | None = None,
    ) -> "ComparisonSettings":
        size = section.get("size") or {}
        assert isinstance(size, Mapping)
        return cls(
            name_levels=tuple(float(value) for value in section.get("name_levels", cls.name_levels)),  # type: ignore[union-attr]
            exact_relative=float(size.get("exact_relative", cls.exact_relative)),
            exact_absolute=float(size.get("exact_absolute", cls.exact_absolute)),
            close_relative=float(size.get("close_relative", cls.close_relative)),
            near_relative=float(size.get("near_relative", cls.near_relative)),
            ounce_bridge_relative=float(size.get("ounce_bridge_relative", cls.ounce_bridge_relative)),
            price_ratio_levels=tuple(float(value) for value in section.get("price_ratio_levels", cls.price_ratio_levels)),  # type: ignore[union-attr]
            implicit_defaults=dict(implicit_defaults or {}),
        )


@dataclass(frozen=True, slots=True)
class ComparisonVector:
    left_id: str
    right_id: str
    levels: tuple[int, ...]
    gtin_level: str
    name_score: float
    hard_conflicts: tuple[str, ...]
    auto_caps: tuple[str, ...]
    same_retailer: bool = False
    exact_name: bool = False

    def level_name(self, field: str) -> str:
        index = FIELD_NAMES.index(field)
        return FIELD_LEVELS[field][self.levels[index]]

    def as_labels(self) -> dict[str, str]:
        return {field: FIELD_LEVELS[field][level] for field, level in zip(FIELD_NAMES, self.levels)}


def _level(field: str, label: str) -> int:
    return FIELD_LEVELS[field].index(label)


_BRAND_STOPWORDS = frozenset({"el", "la", "los", "las", "de", "del", "y", "marca", "mr", "the"})


def brand_in_name(brand: StandardizedRecord, other: StandardizedRecord) -> bool:
    """La marca de ``brand`` aparece en el nombre de ``other`` (tokens o pegada)."""

    if not brand.brand or not brand.brand_squashed:
        return False
    if len(brand.brand_squashed) >= 4 and brand.brand_squashed in "".join(other.name_tokens):
        return True
    tokens = [token for token in brand.brand.split() if token not in _BRAND_STOPWORDS]
    stems = {stem(token) for token in other.name_tokens}
    return bool(tokens) and all(token in other.name_tokens or stem(token) in stems for token in tokens)


def compare_brand(left: StandardizedRecord, right: StandardizedRecord) -> str:
    if left.brand and right.brand:
        if left.brand == right.brand or left.brand_squashed == right.brand_squashed:
            return "exact"
        a, b = left.brand_squashed or "", right.brand_squashed or ""
        if (len(a) >= 4 and len(b) >= 4 and (a.startswith(b) or b.startswith(a))) or jaro_winkler(a, b) >= 0.92:
            return "fuzzy"
        # Submarca/fabricante: "Purina" vs "Dog Chow" cuando el nombre del otro
        # lado menciona la marca ("Comida para perro Purina Dog Chow").
        if brand_in_name(left, right) or brand_in_name(right, left):
            return "cross_name"
        return "conflict"
    if left.brand or right.brand:
        known, other = (left, right) if left.brand else (right, left)
        if brand_in_name(known, other):
            return "in_other_name"
        return "one_missing"
    return "both_missing"


def _relative(a: float, b: float) -> float:
    return abs(a - b) / max(a, b)


def compare_size(left: StandardizedRecord, right: StandardizedRecord, settings: ComparisonSettings) -> str:
    a, b = left.size, right.size
    if a is None or b is None:
        return "missing"
    if a.dimension == b.dimension:
        difference = abs(a.total - b.total)
        relative = _relative(a.total, b.total)
        if a.dimension in {"count", "ounce"}:
            if difference < 1e-9:
                return "exact"
            if a.dimension == "ounce" and relative <= settings.close_relative:
                return "close"
            return "conflict"
        if difference <= settings.exact_absolute or relative <= settings.exact_relative:
            return "exact"
        if relative <= settings.close_relative:
            return "close"
        if relative <= settings.near_relative:
            return "near"
        return "conflict"
    dimensions = {a.dimension, b.dimension}
    if "ounce" in dimensions and dimensions & {"mass_g", "volume_ml"}:
        ounce, metric = (a, b) if a.dimension == "ounce" else (b, a)
        factor = OUNCE_TO_G if metric.dimension == "mass_g" else FL_OUNCE_TO_ML
        if _relative(ounce.total * factor, metric.total) <= settings.ounce_bridge_relative:
            return "ounce_bridge"
        return "conflict"
    if "count" in dimensions:
        # "12 unidades" frente a "500 g" no es contradictorio por sí mismo.
        return "missing"
    return "conflict"


def compare_pack(left: StandardizedRecord, right: StandardizedRecord) -> str:
    a, b = left.size, right.size
    if a is None or b is None or a.dimension == "count" or b.dimension == "count":
        return "missing"
    if a.pack_count == b.pack_count:
        return "same_multi" if a.pack_count > 1 else "same_single"
    return "conflict"


def _set_level(
    left: Mapping[str, frozenset[str]],
    right: Mapping[str, frozenset[str]],
    implicit_defaults: Mapping[str, frozenset[str]] | None = None,
) -> str:
    if not left and not right:
        return "none"
    implicit_defaults = implicit_defaults or {}
    families = set(left) | set(right)
    one_sided = partial = agree = False
    for family in families:
        a, b = left.get(family), right.get(family)
        if not a or not b:
            declared = a or b or frozenset()
            # "Original"/"entera" omitido de un lado es el valor por defecto.
            if not declared <= implicit_defaults.get(family, frozenset()):
                one_sided = True
            continue
        if a == b:
            agree = True
        elif a & b:
            partial = True
        else:
            return "conflict"
    if partial:
        return "partial"
    if one_sided:
        return "one_sided"
    return "agree" if agree else "none"


def compare_codes(left: StandardizedRecord, right: StandardizedRecord) -> str:
    a, b = left.codes, right.codes
    if not a and not b:
        return "none"
    if not a or not b:
        return "one_sided"
    if a == b:
        return "agree"
    if a & b:
        return "partial"
    return "conflict"


def name_similarity(left: StandardizedRecord, right: StandardizedRecord) -> float:
    if not left.core_tokens or not right.core_tokens:
        return 0.0
    return 0.5 * cosine(left.tfidf, right.tfidf) + 0.5 * soft_token_overlap(left.core_tokens, right.core_tokens)


def _unmatched(tokens: Sequence[str], other_tokens: Sequence[str]) -> list[str]:
    result = []
    for token in tokens:
        if any(tokens_match(token, other) for other in other_tokens):
            continue
        result.append(token)
    return result


def compare_name_diff(left: StandardizedRecord, right: StandardizedRecord) -> str:
    """Tokens distintivos que sólo aparecen de un lado.

    Que *ambos* lados tengan tokens raros sin pareja ("Total Anti Sarro" frente
    a "Total Whitening") es la señal típica de variantes distintas; tokens
    extra comunes de un solo lado suelen ser descriptores ("Pasta Dental").
    """

    left_other = tuple(right.core_tokens) + tuple(stem(token) for token in right.name_tokens)
    right_other = tuple(left.core_tokens) + tuple(stem(token) for token in left.name_tokens)
    left_extra = _unmatched(left.core_tokens, left_other)
    right_extra = _unmatched(right.core_tokens, right_other)
    left_major = any(token in left.rare_tokens for token in left_extra)
    right_major = any(token in right.rare_tokens for token in right_extra)
    if not left_extra and not right_extra:
        return "none"
    if not left_extra or not right_extra:
        return "one_side_major" if (left_major or right_major) else "one_side_minor"
    return "both_major" if (left_major and right_major) else "both_minor"


def compare_name(score: float, settings: ComparisonSettings) -> str:
    labels = FIELD_LEVELS["name"]
    for label, cutoff in zip(labels, settings.name_levels):
        if score >= cutoff:
            return label
    return labels[-1]


def _types_related(a: str, b: str) -> bool:
    fa, fb = a.casefold(), b.casefold()
    return fa.startswith(fb) or fb.startswith(fa)


def compare_type(left: StandardizedRecord, right: StandardizedRecord) -> str:
    if left.product_type and right.product_type:
        if left.product_type == right.product_type:
            return "same_type"
        if _types_related(left.product_type, right.product_type):
            return "type_partial"
        return "type_conflict"
    compatible = departments_compatible(left.department, right.department)
    if compatible is None:
        return "unknown"
    return "same_department" if compatible else "department_conflict"


def compare_price(left: StandardizedRecord, right: StandardizedRecord, size_level: str, settings: ComparisonSettings) -> str:
    ratio: float | None = None
    if (
        left.unit_price is not None
        and right.unit_price is not None
        and left.unit_price_basis == right.unit_price_basis
        and left.unit_price > 0
        and right.unit_price > 0
    ):
        ratio = max(left.unit_price, right.unit_price) / min(left.unit_price, right.unit_price)
    elif size_level in {"exact", "close"} or (left.size is None and right.size is None):
        a, b = left.record.price, right.record.price
        if a is not None and b is not None:
            ratio = float(max(a, b) / min(a, b))
    if ratio is None:
        return "missing"
    labels = FIELD_LEVELS["price"]
    for label, cutoff in zip(labels, settings.price_ratio_levels):
        if ratio <= cutoff:
            return label
    return "ratio_gt_3"


def compare_gtin(left: StandardizedRecord, right: StandardizedRecord) -> str:
    if left.gtin is None or right.gtin is None:
        return "missing"
    if left.gtin.restricted or right.gtin.restricted:
        return "restricted"
    return "same" if left.gtin.gtin14 == right.gtin.gtin14 else "different"


def compare(
    left: StandardizedRecord,
    right: StandardizedRecord,
    settings: ComparisonSettings,
) -> ComparisonVector:
    score = name_similarity(left, right)
    size_level = compare_size(left, right, settings)
    labels = {
        "brand": compare_brand(left, right),
        "name": compare_name(score, settings),
        "name_diff": compare_name_diff(left, right),
        "size": size_level,
        "pack": compare_pack(left, right),
        "variant": _set_level(left.variants, right.variants, settings.implicit_defaults),
        "codes": compare_codes(left, right),
        "type": compare_type(left, right),
        "price": compare_price(left, right, size_level, settings),
    }
    gtin_level = compare_gtin(left, right)
    hard = sorted(
        f"{field}_{labels[field]}" for field, bad in HARD_CONFLICT_LEVELS.items() if labels[field] in bad
    )
    if gtin_level == "different":
        hard.append("gtin_different")
    if left.supermarket_id == right.supermarket_id:
        hard.append("same_retailer")
    caps = sorted(
        f"{field}_{labels[field]}" for field, capped in AUTO_CAP_LEVELS.items() if labels[field] in capped
    )
    if left.record.name_reconstructed or right.record.name_reconstructed:
        caps.append("name_reconstructed")
    left_id, right_id = pair_key(left.source_record_id, right.source_record_id)
    return ComparisonVector(
        left_id=left_id,
        right_id=right_id,
        levels=tuple(_level(field, labels[field]) for field in FIELD_NAMES),
        gtin_level=gtin_level,
        name_score=round(score, 4),
        hard_conflicts=tuple(sorted(hard)),
        auto_caps=tuple(caps),
        same_retailer=left.supermarket_id == right.supermarket_id,
        exact_name=left.profile.normalized_name == right.profile.normalized_name,
    )


def pair_key(left_id: str, right_id: str) -> tuple[str, str]:
    return (left_id, right_id) if left_id <= right_id else (right_id, left_id)


def level_matrix(vectors: Sequence[ComparisonVector]) -> list[tuple[int, ...]]:
    return [vector.levels for vector in vectors]
