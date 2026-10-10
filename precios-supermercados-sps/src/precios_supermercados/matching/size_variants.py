"""Relación "otra presentación" (regla V v1): mismo producto, otro tamaño.

Une dos productos públicos cuando son la misma familia (marca, nombre, sabor y
variante) en distinto tamaño o paquete, para mostrar "Otras presentaciones" con
su precio por kg/L/unidad. No es identidad: nunca fusiona ofertas ni entra a
comparaciones de precio, ranking, PCI o canastas.

Precisión medida y aprobada por el responsable (2026-10-09): muestra de 240
pares etiquetados para diseñar las guardas, holdout ciego de 94 pares con
98.5 % de aciertos ponderado por estrato y revisión del responsable de 50 pares
al azar sin objeciones.

Regla V (nivel "otra presentación"):

- misma marca (forma compacta idéntica) y puntaje de nombre ≥ 0.74;
- variante sin conflicto (``agree``/``none``) y a lo sumo una palabra menor
  distinta (``name_diff`` ``none``/``one_side_minor``);
- sin conflicto de tipo, departamento ni códigos; nunca el mismo GTIN;
- misma dimensión de tamaño (masa, volumen, conteo u onzas) y distinto tamaño o
  paquete (si tamaño y paquete coinciden es el mismo producto, no otra
  presentación).

Guardas (errores observados en la muestra):

- variante declarada en un solo lado: líquido, light, premium, gel, barra,
  almendras, dulce, mix, zero, repuesto, rellena;
- talla o etapa distinta (pañales) y tamaño de huevo distinto (G/M/P);
- objetos cuyo "tamaño" es capacidad o peso del objeto (hieleras, mancuernas,
  tazas, cajas organizadoras, platos, vasos, tintes, ambientadores);
- tamaño fuera de rango (> 60 kg o > 60 L: error de lectura);
- precio por unidad incoherente: el tamaño grande no puede costar por unidad
  más del doble ni menos de la sexta parte que el chico.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Callable, Iterable, Mapping, Sequence

from .comparison import ComparisonSettings, compare
from .config import DEFAULT_CONFIG_PATH, DEFAULT_TAXONOMY_PATH, load_engine_config
from .records import MatchRecord
from .taxonomy import load_source_taxonomy

RULE_ID = "size-variant-rule-v"
RULE_VERSION = "1"
MIN_NAME_SCORE = 0.74
LARGE_SIZE_RATIO = 5
MAX_TOTAL = {"mass_g": 60000.0, "volume_ml": 60000.0}
MAX_SMALL_OVER_LARGE_UNIT_PRICE = 6.0
MAX_LARGE_OVER_SMALL_UNIT_PRICE = 2.0

_VARIANT_OK = {"agree", "none"}
_NAME_DIFF_OK = {"none", "one_side_minor"}
_TYPE_BLOCKING = {"type_conflict", "department_conflict"}
_SAME_SIZE = {"exact", "close"}
_SAME_PACK = {"same_single", "same_multi", "missing"}

_TOKEN_ALIASES = {"liq": "liquido"}
ONE_SIDED_VARIANT_TOKENS = frozenset(
    {"liquido", "gel", "light", "premium", "dulce", "mix", "almendras", "barra", "zero", "repuesto", "rellena"}
)
# Colonial pega palabras ("JugoNaranjaPremium"): estas se buscan como subcadena.
_GLUED_VARIANT_TOKENS = frozenset({"premium", "light"})
OBJECT_TOKENS = frozenset(
    {
        "hielera", "mancuerna", "mancuernas", "taza", "tazas", "tazon", "tazones", "organizadora",
        "plato", "platos", "vaso", "vasos", "tinte", "ambientador",
    }
)
_TALLA_RE = re.compile(r"(?:etapa|talla)\s*#?\s*(\d{1,2}(?:\s*-\s*\d{1,2}\b)?|x*[smlgp]\b)")
_EGG_SIZES = {
    "g": "grande", "grande": "grande", "grandes": "grande",
    "m": "mediano", "mediano": "mediano", "medianos": "mediano",
    "p": "pequeno", "pequeno": "pequeno", "pequenos": "pequeno",
    "extra": "extra", "xl": "extra", "jumbo": "jumbo",
}


@dataclass(frozen=True, slots=True)
class SizeVariantItem:
    """Producto público candidato (una fila del catálogo)."""

    key: str
    supermarket_id: str
    name: str
    brand: str | None
    presentation: str | None
    category: str | None = None
    price: Decimal | None = None


@dataclass(frozen=True, slots=True)
class SizeVariantLink:
    key_a: str
    key_b: str
    dimension: str
    total_a: float
    total_b: float
    name_score: float

    @property
    def ratio(self) -> float:
        small, large = sorted((self.total_a, self.total_b))
        return large / small if small > 0 else float("inf")


def _ascii(value: str | None) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    return "".join(char for char in decomposed if not unicodedata.combining(char)).casefold()


def _tokens(name: str) -> set[str]:
    return {_TOKEN_ALIASES.get(token, token) for token in re.findall(r"[a-z]+", _ascii(name))}


def _variant_flags(name: str) -> set[str]:
    flags = _tokens(name) & ONE_SIDED_VARIANT_TOKENS
    flat = _ascii(name)
    flags.update(token for token in _GLUED_VARIANT_TOKENS if token in flat)
    return flags


def _tallas(name: str) -> set[str]:
    return {re.sub(r"\s+", "", match) for match in _TALLA_RE.findall(_ascii(name))}


def _egg_sizes(name: str) -> set[str]:
    return {_EGG_SIZES[token] for token in re.findall(r"[a-z]+", _ascii(name)) if token in _EGG_SIZES}


def rule_v(labels: Mapping[str, str], name_score: float, gtin_level: str) -> str:
    """Motivo de rechazo de la regla V, o ``"ok"`` si es otra presentación."""

    if gtin_level in {"same", "exact"}:
        return "same_gtin"
    if name_score < MIN_NAME_SCORE:
        return "name_low"
    if labels["brand"] != "exact":
        return "brand"
    if labels["variant"] not in _VARIANT_OK:
        return "variant"
    if labels["name_diff"] not in _NAME_DIFF_OK:
        return "name_diff"
    if labels["type"] in _TYPE_BLOCKING:
        return "type"
    if labels["codes"] == "conflict":
        return "codes"
    if labels["size"] in _SAME_SIZE and labels["pack"] in _SAME_PACK:
        return "same_size"
    return "ok"


def _unit_price(price: Decimal | None, total: float) -> Decimal | None:
    if price is None or price <= 0 or total <= 0:
        return None
    return price / Decimal(str(total))


def guard(
    name_a: str,
    name_b: str,
    *,
    dimension: str,
    total_a: float,
    total_b: float,
    price_a: Decimal | None = None,
    price_b: Decimal | None = None,
) -> str | None:
    """Motivo de exclusión por las guardas, o ``None`` si el par es publicable."""

    tokens_a, tokens_b = _tokens(name_a), _tokens(name_b)
    if (tokens_a | tokens_b) & OBJECT_TOKENS:
        return "object"
    tallas_a, tallas_b = _tallas(name_a), _tallas(name_b)
    if tallas_a and tallas_b and tallas_a != tallas_b:
        return "talla"
    if {"huevo", "huevos"} & (tokens_a | tokens_b) and _egg_sizes(name_a) != _egg_sizes(name_b):
        return "talla"
    if _variant_flags(name_a) ^ _variant_flags(name_b):
        return "variant_word"
    limit = MAX_TOTAL.get(dimension)
    if limit is not None and max(total_a, total_b) > limit:
        return "size_out_of_range"
    unit_a, unit_b = _unit_price(price_a, total_a), _unit_price(price_b, total_b)
    if unit_a is not None and unit_b is not None:
        large, small = (unit_a, unit_b) if total_a > total_b else (unit_b, unit_a)
        if small / large > Decimal(str(MAX_SMALL_OVER_LARGE_UNIT_PRICE)) or large / small > Decimal(
            str(MAX_LARGE_OVER_SMALL_UNIT_PRICE)
        ):
            return "unit_price_incoherent"
    return None


def _default_standardize() -> tuple[Callable[[Sequence[MatchRecord]], list], ComparisonSettings]:
    from .master_candidates import standardize_records

    config = load_engine_config(DEFAULT_CONFIG_PATH)
    settings = ComparisonSettings.from_config(config.section("comparison"), config.implicit_defaults)
    taxonomy = load_source_taxonomy(DEFAULT_TAXONOMY_PATH)
    rare = float(config.section("comparison").get("rare_token_share", 0.005))

    def standardize(records: Sequence[MatchRecord]) -> list:
        return standardize_records(records, config=config, taxonomy=taxonomy, rare_token_share=rare)

    return standardize, settings


def size_variant_links(
    items: Iterable[SizeVariantItem],
    *,
    standardize: Callable[[Sequence[MatchRecord]], list] | None = None,
    settings: ComparisonSettings | None = None,
) -> tuple[list[SizeVariantLink], dict[str, int]]:
    """Pares "otra presentación" entre productos públicos (regla V v1 + guardas).

    Bloquea por marca compacta y luego por (dimensión, departamento); compara
    cada par del bloque con el motor de homologación. Devuelve los vínculos
    directos (sin cierre transitivo) y contadores de diagnóstico.
    """

    values = list(items)
    diagnostics: Counter[str] = Counter(items=len(values))
    if len(values) < 2:
        return [], dict(diagnostics)
    if standardize is None or settings is None:
        default_standardize, default_settings = _default_standardize()
        standardize = standardize or default_standardize
        settings = settings or default_settings
    by_key = {item.key: item for item in values}
    if len(by_key) != len(values):
        raise ValueError("size_variant_duplicate_key")
    records = [
        MatchRecord(
            source_record_id=item.key,
            supermarket_id=item.supermarket_id,
            city="ALL",
            source_name=item.name,
            source_brand=item.brand,
            source_presentation=item.presentation,
            source_category=item.category,
        )
        for item in values
    ]
    blocks: dict[tuple[str, str, str], list] = defaultdict(list)
    for standardized in standardize(records):
        if standardized.brand_squashed and standardized.size is not None and standardized.size.total > 0:
            blocks[(standardized.brand_squashed, standardized.size.dimension, standardized.department or "")].append(
                standardized
            )
    links: list[SizeVariantLink] = []
    for _, block in sorted(blocks.items()):
        block.sort(key=lambda entry: entry.source_record_id)
        for index, left in enumerate(block):
            for right in block[index + 1 :]:
                diagnostics["pairs"] += 1
                if left.identity_gtin and left.identity_gtin == right.identity_gtin:
                    diagnostics["rejected_same_gtin"] += 1
                    continue
                vector = compare(left, right, settings)
                reason = rule_v(vector.as_labels(), vector.name_score, vector.gtin_level)
                if reason != "ok":
                    diagnostics[f"rejected_{reason}"] += 1
                    continue
                item_a, item_b = by_key[left.source_record_id], by_key[right.source_record_id]
                excluded = guard(
                    item_a.name,
                    item_b.name,
                    dimension=left.size.dimension,
                    total_a=float(left.size.total),
                    total_b=float(right.size.total),
                    price_a=item_a.price,
                    price_b=item_b.price,
                )
                if excluded:
                    diagnostics[f"guard_{excluded}"] += 1
                    continue
                links.append(
                    SizeVariantLink(
                        key_a=left.source_record_id,
                        key_b=right.source_record_id,
                        dimension=left.size.dimension,
                        total_a=float(left.size.total),
                        total_b=float(right.size.total),
                        name_score=round(float(vector.name_score), 4),
                    )
                )
    diagnostics["links"] = len(links)
    return links, dict(sorted(diagnostics.items()))


def parse_price(value: object) -> Decimal | None:
    """Precio público ``"123.45"`` → ``Decimal``; ``None`` si no es válido."""

    try:
        price = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return price if price.is_finite() and price > 0 else None


__all__ = [
    "LARGE_SIZE_RATIO",
    "MIN_NAME_SCORE",
    "RULE_ID",
    "RULE_VERSION",
    "SizeVariantItem",
    "SizeVariantLink",
    "guard",
    "parse_price",
    "rule_v",
    "size_variant_links",
]
