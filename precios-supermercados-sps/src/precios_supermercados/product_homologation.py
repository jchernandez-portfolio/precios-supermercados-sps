"""Homologación conservadora de productos entre supermercados.

La clasificación descriptiva vive separada del estado comercial: cambiar una
categoría, un tipo de producto o un candidato de matching nunca modifica por sí
solo un periodo de precio.

Confianza:
- GTIN válido idéntico = identidad fuerte, pero una contradicción descriptiva
  puede bloquear su uso automático en comparaciones de precio;
- sin GTIN, el motor sólo genera candidatos ``review_required``;
- dos GTIN válidos distintos nunca se proponen como el mismo producto;
- marcas placeholder no participan en matching;
- multipacks ambiguos no se reducen a una unidad;
- la presentación fuente de Paiz es evidencia auxiliar porque la auditoría real
  mostró contradicciones sistemáticas con el nombre comercial anunciado.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from difflib import SequenceMatcher
from typing import Iterable

from .gtin_policy import restricted_gtin_shared_master_ok
from .identifiers import canonicalize_gtin, generate_gtin_product_id


class ProductHomologationError(ValueError):
    """Un registro no cumple el contrato mínimo de homologación."""


def _required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProductHomologationError(f"{field_name}_missing")
    return value.strip()


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def fold_text(value: str | None) -> str | None:
    """Normaliza texto para comparación sin destruir el texto fuente."""

    value = _optional_text(value)
    if value is None:
        return None
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(
        char for char in decomposed if not unicodedata.combining(char)
    )
    normalized = re.sub(r"[^0-9a-zA-Z]+", " ", without_marks).casefold()
    collapsed = " ".join(normalized.split())
    return collapsed or None


_GENERIC_BRANDS = frozenset(
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


def normalize_brand(value: str | None) -> str | None:
    """Devuelve una marca comparable o ``None`` para placeholders de fuente."""

    normalized = fold_text(value)
    if normalized is None or normalized in _GENERIC_BRANDS:
        return None
    return normalized


@dataclass(frozen=True, slots=True)
class SourceProductRecord:
    source_record_id: str
    supermarket_id: str
    source_name: str
    source_brand: str | None = None
    source_presentation: str | None = None
    source_category: str | None = None
    barcode: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_record_id",
            _required_text(self.source_record_id, "source_record_id"),
        )
        object.__setattr__(
            self,
            "supermarket_id",
            _required_text(self.supermarket_id, "supermarket_id"),
        )
        object.__setattr__(
            self,
            "source_name",
            _required_text(self.source_name, "source_name"),
        )
        for field_name in (
            "source_brand",
            "source_presentation",
            "source_category",
            "barcode",
        ):
            object.__setattr__(
                self, field_name, _optional_text(getattr(self, field_name))
            )


@dataclass(frozen=True, slots=True)
class TaxonomyAssignment:
    category: str | None
    subcategory: str | None
    product_type: str | None
    rule_id: str | None


@dataclass(frozen=True, slots=True)
class PresentationSignature:
    """Presentación comparable; ``total_base`` representa el paquete completo."""

    dimension: str
    total_base: Decimal
    pack_count: int
    unit_amount_base: Decimal | None = None
    # Etiqueta dual ("16 oz (454 g)"): la presentación canónica es la métrica y
    # las onzas declaradas por unidad se conservan sólo como evidencia auxiliar
    # para seguir siendo comparable con fuentes que sólo publican onzas.
    declared_ounces: Decimal | None = None

    def __post_init__(self) -> None:
        if self.dimension not in {"mass_g", "volume_ml", "count", "ounce"}:
            raise ProductHomologationError("presentation_dimension_invalid")
        if self.total_base <= 0 or self.pack_count <= 0:
            raise ProductHomologationError("presentation_value_invalid")
        if self.unit_amount_base is not None and self.unit_amount_base <= 0:
            raise ProductHomologationError("presentation_unit_value_invalid")
        if self.declared_ounces is not None and (
            self.declared_ounces <= 0 or self.dimension not in {"mass_g", "volume_ml"}
        ):
            raise ProductHomologationError("presentation_declared_ounces_invalid")


@dataclass(frozen=True, slots=True)
class ProductProfile:
    record: SourceProductRecord
    normalized_name: str
    normalized_brand: str | None
    canonical_gtin: str | None
    canonical_product_id: str | None
    taxonomy: TaxonomyAssignment
    presentation: PresentationSignature | None
    presentation_status: str
    matching_tokens: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExactGtinGroup:
    canonical_gtin: str
    canonical_product_id: str
    source_record_ids: tuple[str, ...]
    supermarket_ids: tuple[str, ...]
    comparison_status: str = "ready"
    conflict_reasons: tuple[str, ...] = ()
    # Miembros retirados de un grupo ``ready`` por un conflicto atribuible sólo a
    # ellos: ``(source_record_id, motivos)``. Nunca participan en la comparación.
    excluded_members: tuple[tuple[str, tuple[str, ...]], ...] = ()


@dataclass(frozen=True, slots=True)
class MatchCandidate:
    left_source_record_id: str
    right_source_record_id: str
    left_supermarket_id: str
    right_supermarket_id: str
    product_type: str
    normalized_brand: str
    score: Decimal
    reason: str = "brand+product_type+presentation+name_similarity"
    status: str = "review_required"


@dataclass(frozen=True, slots=True)
class HomologationResult:
    profiles: tuple[ProductProfile, ...]
    exact_gtin_groups: tuple[ExactGtinGroup, ...]
    candidates: tuple[MatchCandidate, ...]

    @property
    def summary(self) -> dict[str, int]:
        exact_members = {
            source_id
            for group in self.exact_gtin_groups
            for source_id in group.source_record_ids
        }
        return {
            "source_products": len(self.profiles),
            "classified_product_type": sum(
                profile.taxonomy.product_type is not None for profile in self.profiles
            ),
            "valid_gtin": sum(
                profile.canonical_gtin is not None for profile in self.profiles
            ),
            "exact_gtin_groups_cross_supermarket": len(self.exact_gtin_groups),
            "exact_gtin_groups_comparison_ready": sum(
                group.comparison_status == "ready"
                for group in self.exact_gtin_groups
            ),
            "exact_gtin_groups_review_required": sum(
                group.comparison_status == "review_required"
                for group in self.exact_gtin_groups
            ),
            "source_products_in_exact_gtin_groups": len(exact_members),
            "review_candidates": len(self.candidates),
            "presentation_conflicts": sum(
                profile.presentation_status == "conflict"
                for profile in self.profiles
            ),
            "presentation_ambiguous_multipacks": sum(
                profile.presentation_status == "ambiguous_multipack"
                for profile in self.profiles
            ),
            "paiz_source_presentation_overrides": sum(
                profile.presentation_status == "name_preferred_source_conflict"
                for profile in self.profiles
            ),
            "generic_brand_placeholders": sum(
                profile.record.source_brand is not None
                and profile.normalized_brand is None
                for profile in self.profiles
            ),
        }


@dataclass(frozen=True, slots=True)
class _TaxonomyRule:
    rule_id: str
    category: str
    subcategory: str
    product_type: str
    all_terms: tuple[str, ...] = ()
    any_terms: tuple[str, ...] = ()
    forbidden_terms: tuple[str, ...] = ()

    def matches(self, text: str) -> bool:
        padded = f" {text} "

        def present(term: str) -> bool:
            return f" {term} " in padded if " " not in term else term in text

        return (
            all(present(term) for term in self.all_terms)
            and (not self.any_terms or any(present(term) for term in self.any_terms))
            and not any(present(term) for term in self.forbidden_terms)
        )


# Reglas de sustantivo/tipo primero; ingredientes y descriptores después.
# Sólo se usa el nombre anunciado para product_type. source_category se conserva
# como evidencia fuente, no como verdad canónica.
_TAXONOMY_RULES = (
    _TaxonomyRule("desodorante", "Cuidado personal", "Higiene", "Desodorante", any_terms=("desodorante",)),
    _TaxonomyRule("shampoo", "Cuidado personal", "Cabello", "Shampoo", any_terms=("shampoo", "champu")),
    _TaxonomyRule("acondicionador", "Cuidado personal", "Cabello", "Acondicionador", any_terms=("acondicionador",)),
    _TaxonomyRule("pasta_dental", "Cuidado personal", "Higiene oral", "Pasta dental", any_terms=("pasta dental", "crema dental")),
    _TaxonomyRule("cepillo_dental", "Cuidado personal", "Higiene oral", "Cepillo dental", ("cepillo",), ("dental", "dientes")),
    _TaxonomyRule("enjuague_bucal", "Cuidado personal", "Higiene oral", "Enjuague bucal", any_terms=("enjuague bucal", "enjuague oral")),
    _TaxonomyRule("agua_micelar", "Cuidado personal", "Cuidado facial", "Agua micelar", ("agua", "micelar")),
    _TaxonomyRule("tinte_cabello", "Cuidado personal", "Cabello", "Tinte para cabello", any_terms=("tinte",), forbidden_terms=("pintura",)),
    _TaxonomyRule("protector_diario", "Cuidado personal", "Higiene femenina", "Protector diario", ("protector",), ("diario", "diarios")),
    _TaxonomyRule("toalla_femenina", "Cuidado personal", "Higiene femenina", "Toalla femenina", ("toalla",), ("femenina", "femeninas", "sanitaria", "sanitarias")),
    _TaxonomyRule("panal", "Bebés", "Pañales", "Pañal", any_terms=("panal", "panales")),
    _TaxonomyRule("toallita_humeda", "Bebés", "Higiene", "Toallita húmeda", any_terms=("toallitas humedas", "toallas humedas")),
    _TaxonomyRule("jabon", "Cuidado personal", "Higiene", "Jabón", any_terms=("jabon",)),
    _TaxonomyRule("detergente", "Limpieza", "Lavandería", "Detergente", any_terms=("detergente",)),
    _TaxonomyRule("suavizante", "Limpieza", "Lavandería", "Suavizante", any_terms=("suavizante",)),
    _TaxonomyRule("lavaplatos", "Limpieza", "Cocina", "Lavaplatos", any_terms=("lavaplatos", "lavatrastes")),
    _TaxonomyRule("desinfectante", "Limpieza", "Superficies", "Desinfectante", any_terms=("desinfectante",)),
    _TaxonomyRule("cloro", "Limpieza", "Superficies", "Cloro", any_terms=("cloro", "lejia")),
    _TaxonomyRule("limpiador", "Limpieza", "Superficies", "Limpiador", any_terms=("limpiador",)),
    _TaxonomyRule("aromatizante", "Hogar", "Ambientadores", "Aromatizante", any_terms=("aromatizante", "ambientador")),
    _TaxonomyRule("papel_higienico", "Hogar", "Papel", "Papel higiénico", ("papel", "higienico")),
    _TaxonomyRule("papel_toalla", "Hogar", "Papel", "Papel toalla", any_terms=("papel toalla", "toalla de papel", "toallas de papel")),
    _TaxonomyRule("servilleta", "Hogar", "Papel", "Servilleta", any_terms=("servilleta", "servilletas")),
    _TaxonomyRule("alimento_perro", "Mascotas", "Alimento para mascotas", "Alimento para perro", any_terms=("alimento para perro", "comida para perro", "alimento canino")),
    _TaxonomyRule("alimento_gato", "Mascotas", "Alimento para mascotas", "Alimento para gato", any_terms=("alimento para gato", "comida para gato", "alimento felino")),
    _TaxonomyRule("atun", "Alimentos", "Conservas", "Atún", any_terms=("atun",)),
    _TaxonomyRule("sardina", "Alimentos", "Conservas", "Sardina", any_terms=("sardina", "sardinas")),
    _TaxonomyRule("yogurt", "Alimentos", "Lácteos", "Yogurt", any_terms=("yogurt", "yoghurt")),
    _TaxonomyRule("avena", "Alimentos", "Cereales", "Avena", any_terms=("avena", "mosh")),
    _TaxonomyRule("cereal", "Alimentos", "Cereales", "Cereal", any_terms=("cereal", "corn flakes", "hojuelas")),
    _TaxonomyRule("pasta", "Alimentos", "Pastas", "Pasta", any_terms=("pasta", "espagueti", "spaghetti", "macaroni", "macarron", "macarrones")),
    _TaxonomyRule("arroz", "Alimentos", "Arroz y granos", "Arroz", any_terms=("arroz",)),
    _TaxonomyRule("frijol", "Alimentos", "Frijoles y legumbres", "Frijol", any_terms=("frijol", "frijoles")),
    _TaxonomyRule("harina_maiz", "Alimentos", "Harinas", "Harina de maíz", ("harina",), ("maiz",)),
    _TaxonomyRule("harina_trigo", "Alimentos", "Harinas", "Harina de trigo", ("harina",), ("trigo",)),
    _TaxonomyRule("leche_polvo", "Alimentos", "Lácteos", "Leche en polvo", ("leche", "polvo")),
    _TaxonomyRule("leche_condensada", "Alimentos", "Lácteos", "Leche condensada", ("leche", "condensada")),
    _TaxonomyRule("leche_evaporada", "Alimentos", "Lácteos", "Leche evaporada", ("leche", "evaporada")),
    _TaxonomyRule("leche", "Alimentos", "Lácteos", "Leche", ("leche",), forbidden_terms=("condensada", "evaporada", "polvo")),
    _TaxonomyRule("queso", "Alimentos", "Lácteos", "Queso", any_terms=("queso",)),
    _TaxonomyRule("margarina", "Alimentos", "Aceites y grasas", "Margarina", any_terms=("margarina",)),
    _TaxonomyRule("mantequilla_mani", "Alimentos", "Untables", "Mantequilla de maní", ("mantequilla",), ("mani",)),
    _TaxonomyRule("mantequilla", "Alimentos", "Lácteos", "Mantequilla", ("mantequilla",), forbidden_terms=("mani",)),
    _TaxonomyRule("huevo", "Alimentos", "Huevos", "Huevo", any_terms=("huevo", "huevos")),
    _TaxonomyRule("mayonesa", "Alimentos", "Salsas y aderezos", "Mayonesa", any_terms=("mayonesa",)),
    _TaxonomyRule("ketchup", "Alimentos", "Salsas y aderezos", "Ketchup", any_terms=("ketchup", "catsup")),
    _TaxonomyRule("mostaza", "Alimentos", "Salsas y aderezos", "Mostaza", any_terms=("mostaza",)),
    _TaxonomyRule("aderezo", "Alimentos", "Salsas y aderezos", "Aderezo", any_terms=("aderezo",)),
    _TaxonomyRule("salsa", "Alimentos", "Salsas y aderezos", "Salsa", any_terms=("salsa",)),
    _TaxonomyRule("galleta", "Alimentos", "Galletas", "Galleta", any_terms=("galleta", "galletas")),
    _TaxonomyRule("gelatina", "Alimentos", "Postres", "Gelatina", any_terms=("gelatina",)),
    _TaxonomyRule("helado", "Alimentos", "Congelados", "Helado", any_terms=("helado", "paleta")),
    _TaxonomyRule("sopa", "Alimentos", "Sopas", "Sopa", any_terms=("sopa",)),
    _TaxonomyRule("pan_molde", "Alimentos", "Panadería", "Pan de molde", ("pan",), ("molde", "sandwich")),
    _TaxonomyRule("pan", "Alimentos", "Panadería", "Pan", any_terms=("pan",), forbidden_terms=("panal", "pantalon")),
    _TaxonomyRule("azucar", "Alimentos", "Azúcar y endulzantes", "Azúcar", any_terms=("azucar",), forbidden_terms=("sin azucar", "bajo en azucar", "cero azucar", "menos azucar")),
    _TaxonomyRule("sal", "Alimentos", "Condimentos", "Sal", any_terms=("sal",), forbidden_terms=("salsa", "salmon")),
    _TaxonomyRule("chocolate", "Alimentos", "Dulces y chocolates", "Chocolate", any_terms=("chocolate",)),
    _TaxonomyRule("aceite", "Alimentos", "Aceites y grasas", "Aceite comestible", any_terms=("aceite",), forbidden_terms=("motor", "cabello", "corporal", "esencial", "desodorante", "shampoo", "micelar", "crema")),
    _TaxonomyRule("cafe", "Bebidas", "Café", "Café", any_terms=("cafe",)),
    _TaxonomyRule("te", "Bebidas", "Té", "Té", any_terms=("te",), forbidden_terms=("set", "tinte")),
    _TaxonomyRule("jugo", "Bebidas", "Jugos", "Jugo", any_terms=("jugo", "nectar"), forbidden_terms=("shampoo", "crema")),
    _TaxonomyRule("refresco", "Bebidas", "Refrescos", "Refresco", any_terms=("refresco", "gaseosa", "soda")),
    _TaxonomyRule("agua", "Bebidas", "Agua", "Agua", any_terms=("agua",), forbidden_terms=("oxigenada", "micelar", "colonia", "atun", "sardina", "jabon", "gato", "perro")),
    _TaxonomyRule("cerveza", "Bebidas", "Alcohólicas", "Cerveza", any_terms=("cerveza",)),
    _TaxonomyRule("vino", "Bebidas", "Alcohólicas", "Vino", any_terms=("vino",)),
    _TaxonomyRule("ron", "Bebidas", "Alcohólicas", "Ron", any_terms=("ron",), forbidden_terms=("ronroneo",)),
)


_HEAD_TAXONOMY: tuple[tuple[str, str, str, str, str], ...] = (
    ("desodorante ambiental", "Hogar", "Ambientadores", "Aromatizante", "head_desodorante_ambiental"),
    ("limpiador desinfectante", "Limpieza", "Superficies", "Limpiador desinfectante", "head_limpiador_desinfectante"),
    ("desinfectante limpiador", "Limpieza", "Superficies", "Limpiador desinfectante", "head_desinfectante_limpiador"),
    ("bebida de almendra", "Bebidas", "Bebidas vegetales", "Bebida vegetal", "head_bebida_almendra"),
    ("leche de almendra", "Bebidas", "Bebidas vegetales", "Bebida vegetal", "head_leche_almendra"),
    ("leche en polvo", "Alimentos", "Lácteos", "Leche en polvo", "head_leche_polvo"),
    ("leche condensada", "Alimentos", "Lácteos", "Leche condensada", "head_leche_condensada"),
    ("leche evaporada", "Alimentos", "Lácteos", "Leche evaporada", "head_leche_evaporada"),
    ("pasta de tomate", "Alimentos", "Salsas y aderezos", "Pasta de tomate", "head_pasta_tomate"),
    ("pasta dental", "Cuidado personal", "Higiene oral", "Pasta dental", "head_pasta_dental"),
    ("crema dental", "Cuidado personal", "Higiene oral", "Pasta dental", "head_crema_dental"),
    ("jabon lavaplatos", "Limpieza", "Cocina", "Lavaplatos", "head_jabon_lavaplatos"),
    ("jabon lavatrasto", "Limpieza", "Cocina", "Lavaplatos", "head_jabon_lavatrasto"),
    ("jabon lavatrastes", "Limpieza", "Cocina", "Lavaplatos", "head_jabon_lavatrastes"),
    ("papel higienico", "Hogar", "Papel", "Papel higiénico", "head_papel_higienico"),
    ("papel toalla", "Hogar", "Papel", "Papel toalla", "head_papel_toalla"),
    ("toalla de papel", "Hogar", "Papel", "Papel toalla", "head_toalla_papel"),
    ("toallas de papel", "Hogar", "Papel", "Papel toalla", "head_toallas_papel"),
    ("atun", "Alimentos", "Conservas", "Atún", "head_atun"),
    ("sardina", "Alimentos", "Conservas", "Sardina", "head_sardina"),
    ("sardinas", "Alimentos", "Conservas", "Sardina", "head_sardinas"),
    ("yogurt", "Alimentos", "Lácteos", "Yogurt", "head_yogurt"),
    ("yoghurt", "Alimentos", "Lácteos", "Yogurt", "head_yoghurt"),
    ("helado", "Alimentos", "Congelados", "Helado", "head_helado"),
    ("helados", "Alimentos", "Congelados", "Helado", "head_helados"),
    ("paleta", "Alimentos", "Congelados", "Helado", "head_paleta"),
    ("galleta", "Alimentos", "Galletas", "Galleta", "head_galleta"),
    ("galletas", "Alimentos", "Galletas", "Galleta", "head_galletas"),
    ("cereal", "Alimentos", "Cereales", "Cereal", "head_cereal"),
    ("avena", "Alimentos", "Cereales", "Avena", "head_avena"),
    ("sopa", "Alimentos", "Sopas", "Sopa", "head_sopa"),
    ("salsa", "Alimentos", "Salsas y aderezos", "Salsa", "head_salsa"),
    ("mayonesa", "Alimentos", "Salsas y aderezos", "Mayonesa", "head_mayonesa"),
    ("ketchup", "Alimentos", "Salsas y aderezos", "Ketchup", "head_ketchup"),
    ("mostaza", "Alimentos", "Salsas y aderezos", "Mostaza", "head_mostaza"),
    ("aderezo", "Alimentos", "Salsas y aderezos", "Aderezo", "head_aderezo"),
    ("pan", "Alimentos", "Panadería", "Pan", "head_pan"),
    ("chocolate", "Alimentos", "Dulces y chocolates", "Chocolate", "head_chocolate"),
    ("arroz", "Alimentos", "Arroz y granos", "Arroz", "head_arroz"),
    ("frijol", "Alimentos", "Frijoles y legumbres", "Frijol", "head_frijol"),
    ("frijoles", "Alimentos", "Frijoles y legumbres", "Frijol", "head_frijoles"),
    ("leche", "Alimentos", "Lácteos", "Leche", "head_leche"),
    ("queso", "Alimentos", "Lácteos", "Queso", "head_queso"),
    ("mantequilla", "Alimentos", "Lácteos", "Mantequilla", "head_mantequilla"),
    ("margarina", "Alimentos", "Aceites y grasas", "Margarina", "head_margarina"),
    ("huevo", "Alimentos", "Huevos", "Huevo", "head_huevo"),
    ("huevos", "Alimentos", "Huevos", "Huevo", "head_huevos"),
    ("refresco", "Bebidas", "Refrescos", "Refresco", "head_refresco"),
    ("gaseosa", "Bebidas", "Refrescos", "Refresco", "head_gaseosa"),
    ("soda", "Bebidas", "Refrescos", "Refresco", "head_soda"),
    ("jugo", "Bebidas", "Jugos", "Jugo", "head_jugo"),
    ("agua", "Bebidas", "Agua", "Agua", "head_agua"),
    ("cafe", "Bebidas", "Café", "Café", "head_cafe"),
    ("te", "Bebidas", "Té", "Té", "head_te"),
    ("cerveza", "Bebidas", "Alcohólicas", "Cerveza", "head_cerveza"),
    ("vino", "Bebidas", "Alcohólicas", "Vino", "head_vino"),
    ("ron", "Bebidas", "Alcohólicas", "Ron", "head_ron"),
    ("cloro", "Limpieza", "Superficies", "Cloro", "head_cloro"),
    ("lavaplatos", "Limpieza", "Cocina", "Lavaplatos", "head_lavaplatos"),
    ("lavatrasto", "Limpieza", "Cocina", "Lavaplatos", "head_lavatrasto"),
    ("lavatrastes", "Limpieza", "Cocina", "Lavaplatos", "head_lavatrastes"),
    ("detergente", "Limpieza", "Lavandería", "Detergente", "head_detergente"),
    ("suavizante", "Limpieza", "Lavandería", "Suavizante", "head_suavizante"),
    ("desinfectante", "Limpieza", "Superficies", "Desinfectante", "head_desinfectante"),
    ("limpiador", "Limpieza", "Superficies", "Limpiador", "head_limpiador"),
    ("aromatizante", "Hogar", "Ambientadores", "Aromatizante", "head_aromatizante"),
    ("ambientador", "Hogar", "Ambientadores", "Aromatizante", "head_ambientador"),
    ("shampoo", "Cuidado personal", "Cabello", "Shampoo", "head_shampoo"),
    ("champu", "Cuidado personal", "Cabello", "Shampoo", "head_champu"),
    ("acondicionador", "Cuidado personal", "Cabello", "Acondicionador", "head_acondicionador"),
    ("desodorante", "Cuidado personal", "Higiene", "Desodorante", "head_desodorante"),
    ("jabon", "Cuidado personal", "Higiene", "Jabón", "head_jabon"),
)


_UNIT_FACTORS: dict[str, tuple[str, Decimal]] = {
    "g": ("mass_g", Decimal("1")),
    "gr": ("mass_g", Decimal("1")),
    "gramo": ("mass_g", Decimal("1")),
    "gramos": ("mass_g", Decimal("1")),
    "kg": ("mass_g", Decimal("1000")),
    "lb": ("mass_g", Decimal("453.59237")),
    "lbs": ("mass_g", Decimal("453.59237")),
    "ml": ("volume_ml", Decimal("1")),
    "l": ("volume_ml", Decimal("1000")),
    "lt": ("volume_ml", Decimal("1000")),
    "litro": ("volume_ml", Decimal("1000")),
    "litros": ("volume_ml", Decimal("1000")),
    "fl oz": ("volume_ml", Decimal("29.5735295625")),
    "oz fl": ("volume_ml", Decimal("29.5735295625")),
    "oz": ("ounce", Decimal("1")),
    "onza": ("ounce", Decimal("1")),
    "onzas": ("ounce", Decimal("1")),
    "un": ("count", Decimal("1")),
    "und": ("count", Decimal("1")),
    "uds": ("count", Decimal("1")),
    "ud": ("count", Decimal("1")),
    "unidad": ("count", Decimal("1")),
    "unidades": ("count", Decimal("1")),
}
_UNIT_PATTERN = "|".join(
    re.escape(unit) for unit in sorted(_UNIT_FACTORS, key=len, reverse=True)
)
_EXPLICIT_MULTIPACK_RE = re.compile(
    rf"(?<!\w)(?P<count>\d+)\s*[x×]\s*(?P<amount>\d+(?:[.,]\d+)?)\s*(?P<unit>{_UNIT_PATTERN})(?!\w)",
    re.IGNORECASE,
)
_MEASURE_UNIT_PATTERN = "|".join(
    re.escape(unit)
    for unit in sorted(_UNIT_FACTORS, key=len, reverse=True)
    if _UNIT_FACTORS[unit][0] != "count"
)
# "355 ml x 6", "50 g X 3 Unidades": cantidad primero y conteo después. El
# conteo no puede ir seguido de otra unidad de medida ("10 cm x 20 cm").
_REVERSE_MULTIPACK_RE = re.compile(
    rf"(?<!\w)(?P<amount>\d+(?:[.,]\d+)?)\s*(?P<unit>{_UNIT_PATTERN})\s*[x×]\s*(?P<count>\d{{1,3}})"
    rf"(?![\d.,])(?!\s*(?:{_MEASURE_UNIT_PATTERN}|cm|mm|m|plg|pulg)(?!\w))",
    re.IGNORECASE,
)
_SLASH_MULTIPACK_RE = re.compile(
    rf"(?<!\w)(?P<count>\d+)\s*(?:unidades?|uds?|und|ud)\s*/\s*(?P<amount>\d+(?:[.,]\d+)?)\s*(?P<unit>{_UNIT_PATTERN})(?!\w)",
    re.IGNORECASE,
)
_SINGLE_RE = re.compile(
    rf"(?<!\w)(?P<amount>\d+(?:[.,]\d+)?)\s*(?P<unit>{_UNIT_PATTERN})(?!\w)",
    re.IGNORECASE,
)
_AMBIGUOUS_PACK_RE = re.compile(
    r"(?<!\w)(?:(?P<before>\d+)\s*(?:pack|pk)|(?:pack|pk)\s*(?P<after>\d+))(?!\w)",
    re.IGNORECASE,
)
_OUNCE_TO_GRAMS = Decimal("28.349523125")
_FLUID_OUNCE_TO_ML = Decimal("29.5735295625")
# Tolerancia entre etiquetas duales del mismo envase (redondeo de fabricante:
# "240 g o 8 oz" difiere 5.8%). Por encima, las dos etiquetas no describen la
# misma cantidad y la presentación queda sin resolver.
_DUAL_LABEL_TOLERANCE = Decimal("0.08")
_STOPWORDS = frozenset(
    {
        "de", "del", "la", "el", "los", "las", "y", "con", "para", "en", "por",
        "un", "und", "uds", "ud", "unidad", "unidades", "g", "gr", "kg", "lb",
        "lbs", "oz", "fl", "ml", "l", "lt", "x", "pack", "pk",
    }
)


def assign_taxonomy(record: SourceProductRecord) -> TaxonomyAssignment:
    text = fold_text(record.source_name) or ""
    for term, category, subcategory, product_type, rule_id in _HEAD_TAXONOMY:
        if text == term or text.startswith(term + " "):
            return TaxonomyAssignment(category, subcategory, product_type, rule_id)
    for rule in _TAXONOMY_RULES:
        if rule.matches(text):
            return TaxonomyAssignment(
                category=rule.category,
                subcategory=rule.subcategory,
                product_type=rule.product_type,
                rule_id=rule.rule_id,
            )
    return TaxonomyAssignment(None, None, None, None)


def _decimal(value: str) -> Decimal | None:
    try:
        parsed = Decimal(value.replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed > 0 else None


def _signature(
    amount_text: str,
    unit_text: str,
    *,
    pack_count: int = 1,
) -> PresentationSignature | None:
    amount = _decimal(amount_text)
    unit_info = _UNIT_FACTORS.get(" ".join(unit_text.casefold().split()))
    if amount is None or unit_info is None or pack_count <= 0:
        return None
    dimension, factor = unit_info
    unit_amount = amount * factor
    if dimension == "count":
        if pack_count != 1 or amount != amount.to_integral_value():
            return None
        count = int(amount)
        return PresentationSignature(
            dimension="count",
            total_base=Decimal(count),
            pack_count=count,
            unit_amount_base=Decimal("1"),
        )
    return PresentationSignature(
        dimension=dimension,
        total_base=unit_amount * pack_count,
        pack_count=pack_count,
        unit_amount_base=unit_amount,
    )


def _parse_presentation_text(
    value: str | None,
) -> tuple[PresentationSignature | None, bool]:
    value = _optional_text(value)
    if value is None:
        return None, False

    explicit = list(_EXPLICIT_MULTIPACK_RE.finditer(value))
    if explicit:
        match = explicit[-1]
        return (
            _signature(
                match.group("amount"),
                match.group("unit"),
                pack_count=int(match.group("count")),
            ),
            False,
        )

    reverse = list(_REVERSE_MULTIPACK_RE.finditer(value))
    if reverse:
        match = reverse[-1]
        return (
            _signature(
                match.group("amount"),
                match.group("unit"),
                pack_count=int(match.group("count")),
            ),
            False,
        )

    slash = list(_SLASH_MULTIPACK_RE.finditer(value))
    if slash:
        match = slash[-1]
        return (
            _signature(
                match.group("amount"),
                match.group("unit"),
                pack_count=int(match.group("count")),
            ),
            False,
        )

    # "3 Pack - 142 g" o "620 g Pack 2" no dice inequívocamente si el peso
    # anunciado corresponde a cada unidad o al paquete completo.
    pack_scan_value = re.sub(
        r"(?i)\b(?:doy|tetra)\s+pack\b",
        "",
        value,
    )
    # "1 Pack" es una sola unidad y no introduce ambigüedad.
    if any(
        int(match.group("before") or match.group("after")) != 1
        for match in _AMBIGUOUS_PACK_RE.finditer(pack_scan_value)
    ):
        return None, True

    singles = list(_SINGLE_RE.finditer(value))
    if not singles:
        return None, False
    return _prefer_metric_single(singles), False


def _unit_dimension(match: re.Match[str]) -> str | None:
    info = _UNIT_FACTORS.get(" ".join(match.group("unit").casefold().split()))
    return None if info is None else info[0]


def _prefer_metric_single(singles: list[re.Match[str]]) -> PresentationSignature | None:
    """Con etiqueta dual onzas + métrica, la métrica es canónica.

    "16 oz (454 g)", "240 g o 8 oz" y "420 g / 14 oz" producen la misma firma
    métrica sin importar el orden. Las onzas se conservan como evidencia auxiliar
    cuando ambas etiquetas son coherentes (como peso o como volumen); si no lo
    son, la presentación no se resuelve (fail-closed).
    """

    ounces = [match for match in singles if _unit_dimension(match) == "ounce"]
    metric = [match for match in singles if _unit_dimension(match) in {"mass_g", "volume_ml"}]
    if not ounces or not metric:
        match = singles[-1]
        return _signature(match.group("amount"), match.group("unit"))
    signature = _signature(metric[-1].group("amount"), metric[-1].group("unit"))
    ounce_value = _decimal(ounces[-1].group("amount"))
    if signature is None or ounce_value is None:
        return None
    factor = _OUNCE_TO_GRAMS if signature.dimension == "mass_g" else _FLUID_OUNCE_TO_ML
    converted = ounce_value * factor
    if abs(converted - signature.total_base) / signature.total_base > _DUAL_LABEL_TOLERANCE:
        return None
    return PresentationSignature(
        dimension=signature.dimension,
        total_base=signature.total_base,
        pack_count=signature.pack_count,
        unit_amount_base=signature.unit_amount_base,
        declared_ounces=ounce_value,
    )


def _ounce_label_compatible(
    left: PresentationSignature,
    right: PresentationSignature,
) -> bool:
    """Onzas-sólo vs etiqueta dual: compara contra las onzas declaradas."""

    ounce, metric = (left, right) if left.dimension == "ounce" else (right, left)
    if ounce.dimension != "ounce" or metric.declared_ounces is None:
        return False
    if ounce.pack_count != metric.pack_count:
        return False
    return ounce.total_base == metric.declared_ounces * metric.pack_count


def presentations_compatible(
    left: PresentationSignature,
    right: PresentationSignature,
) -> bool:
    if _ounce_label_compatible(left, right):
        return True
    if left.dimension != right.dimension or left.pack_count != right.pack_count:
        return False
    if left.dimension in {"count", "ounce"}:
        return left.total_base == right.total_base
    larger = max(left.total_base, right.total_base)
    difference = abs(left.total_base - right.total_base)
    return difference <= Decimal("1.5") or difference / larger <= Decimal("0.005")


def resolve_presentation(
    record: SourceProductRecord,
) -> tuple[PresentationSignature | None, str]:
    source, source_ambiguous = _parse_presentation_text(record.source_presentation)
    name, name_ambiguous = _parse_presentation_text(record.source_name)

    if name_ambiguous:
        return None, "ambiguous_multipack"

    if source is not None and name is not None:
        if presentations_compatible(source, name):
            return source, "confirmed"
        if record.supermarket_id == "paiz":
            return name, "name_preferred_source_conflict"
        return None, "conflict"

    if name is not None:
        return name, "name_only"

    if source_ambiguous:
        return None, "ambiguous_multipack"

    if source is not None:
        return source, "source_only"

    return None, "missing"


def _matching_tokens(
    name: str,
    brand: str | None,
    taxonomy: TaxonomyAssignment,
) -> tuple[str, ...]:
    tokens = (fold_text(name) or "").split()
    brand_tokens = set((normalize_brand(brand) or "").split())
    type_tokens = set((fold_text(taxonomy.product_type) or "").split())
    return tuple(
        token
        for token in tokens
        if token not in _STOPWORDS
        and token not in brand_tokens
        and token not in type_tokens
        and not token.isdigit()
    )


def profile_product(record: SourceProductRecord) -> ProductProfile:
    normalized_name = fold_text(record.source_name)
    assert normalized_name is not None
    taxonomy = assign_taxonomy(record)
    normalized_brand = normalize_brand(record.source_brand)
    canonical_gtin = canonicalize_gtin(record.barcode)
    presentation, presentation_status = resolve_presentation(record)
    return ProductProfile(
        record=record,
        normalized_name=normalized_name,
        normalized_brand=normalized_brand,
        canonical_gtin=canonical_gtin,
        canonical_product_id=(
            generate_gtin_product_id(canonical_gtin)
            if canonical_gtin is not None
            else None
        ),
        taxonomy=taxonomy,
        presentation=presentation,
        presentation_status=presentation_status,
        matching_tokens=_matching_tokens(
            record.source_name,
            record.source_brand,
            taxonomy,
        ),
    )


def _name_similarity(left: ProductProfile, right: ProductProfile) -> Decimal:
    left_tokens = set(left.matching_tokens)
    right_tokens = set(right.matching_tokens)
    if not left_tokens or not right_tokens:
        return Decimal("0")
    jaccard = Decimal(len(left_tokens & right_tokens)) / Decimal(
        len(left_tokens | right_tokens)
    )
    sequence = Decimal(
        str(
            SequenceMatcher(
                None,
                " ".join(left.matching_tokens),
                " ".join(right.matching_tokens),
            ).ratio()
        )
    )
    return (
        jaccard * Decimal("0.6") + sequence * Decimal("0.4")
    ).quantize(Decimal("0.0001"))


def _presentation_bucket(value: PresentationSignature) -> tuple[str, int, int]:
    if value.dimension in {"count", "ounce"}:
        bucket = int(
            (value.total_base * Decimal("10")).to_integral_value(
                rounding=ROUND_HALF_UP
            )
        )
    else:
        bucket = int(
            (value.total_base / Decimal("5")).to_integral_value(
                rounding=ROUND_HALF_UP
            )
        )
    return value.dimension, value.pack_count, bucket


def _gtin_group_conflicts(members: list[ProductProfile]) -> tuple[str, ...]:
    reasons: set[str] = set()
    if any(member.presentation_status == "conflict" for member in members):
        reasons.add("source_presentation_conflict")
    if any(
        member.presentation_status == "ambiguous_multipack"
        for member in members
    ):
        reasons.add("ambiguous_multipack_presentation")

    comparable = [
        member
        for member in members
        if member.presentation is not None
        and member.presentation_status not in {"conflict", "ambiguous_multipack"}
    ]
    for index, left in enumerate(comparable):
        for right in comparable[index + 1 :]:
            if left.record.supermarket_id == right.record.supermarket_id:
                continue
            assert left.presentation is not None and right.presentation is not None
            if not presentations_compatible(left.presentation, right.presentation):
                reasons.add("cross_source_presentation_conflict")

    types = {
        member.taxonomy.product_type
        for member in members
        if member.taxonomy.product_type is not None
    }
    if len(types) > 1:
        reasons.add("product_type_conflict")
    gtins = {member.canonical_gtin for member in members}
    if len(gtins) == 1 and not restricted_gtin_shared_master_ok(
        next(iter(gtins)),
        (member.record.supermarket_id for member in members),
    ):
        reasons.add("restricted_gtin_outside_shared_master")
    return tuple(sorted(reasons))


def homologate_products(
    records: Iterable[SourceProductRecord],
    *,
    candidate_threshold: Decimal = Decimal("0.72"),
) -> HomologationResult:
    """Clasifica, agrupa GTIN exactos y genera candidatos revisables."""

    if candidate_threshold < 0 or candidate_threshold > 1:
        raise ProductHomologationError("candidate_threshold_invalid")

    profiles = tuple(
        sorted(
            (profile_product(record) for record in records),
            key=lambda profile: profile.record.source_record_id,
        )
    )
    if len({profile.record.source_record_id for profile in profiles}) != len(profiles):
        raise ProductHomologationError("source_record_id_duplicate")

    gtin_index: dict[str, list[ProductProfile]] = defaultdict(list)
    for profile in profiles:
        if profile.canonical_gtin is not None:
            gtin_index[profile.canonical_gtin].append(profile)

    exact_groups: list[ExactGtinGroup] = []
    for gtin, members in sorted(gtin_index.items()):
        supermarkets = sorted({member.record.supermarket_id for member in members})
        if len(supermarkets) < 2:
            continue
        conflicts = _gtin_group_conflicts(members)
        exact_groups.append(
            ExactGtinGroup(
                canonical_gtin=gtin,
                canonical_product_id=generate_gtin_product_id(gtin),
                source_record_ids=tuple(
                    member.record.source_record_id for member in members
                ),
                supermarket_ids=tuple(supermarkets),
                comparison_status=("review_required" if conflicts else "ready"),
                conflict_reasons=conflicts,
            )
        )

    blocks: dict[
        tuple[str, str, str, int, int], list[ProductProfile]
    ] = defaultdict(list)
    for profile in profiles:
        if (
            profile.taxonomy.product_type is None
            or profile.normalized_brand is None
            or profile.presentation is None
            or profile.presentation_status in {"conflict", "ambiguous_multipack"}
        ):
            continue
        dimension, pack_count, bucket = _presentation_bucket(profile.presentation)
        blocks[
            (
                profile.taxonomy.product_type,
                profile.normalized_brand,
                dimension,
                pack_count,
                bucket,
            )
        ].append(profile)

    candidates: list[MatchCandidate] = []
    seen_pairs: set[tuple[str, str]] = set()
    for members in blocks.values():
        for index, left in enumerate(members):
            for right in members[index + 1 :]:
                if left.record.supermarket_id == right.record.supermarket_id:
                    continue
                pair = tuple(
                    sorted(
                        (
                            left.record.source_record_id,
                            right.record.source_record_id,
                        )
                    )
                )
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)

                if left.canonical_gtin is not None and right.canonical_gtin is not None:
                    # Igual GTIN ya está en exact_groups; GTIN distinto contradice match.
                    continue

                assert left.presentation is not None and right.presentation is not None
                if not presentations_compatible(left.presentation, right.presentation):
                    continue

                score = _name_similarity(left, right)
                if score < candidate_threshold:
                    continue

                candidates.append(
                    MatchCandidate(
                        left_source_record_id=left.record.source_record_id,
                        right_source_record_id=right.record.source_record_id,
                        left_supermarket_id=left.record.supermarket_id,
                        right_supermarket_id=right.record.supermarket_id,
                        product_type=left.taxonomy.product_type,
                        normalized_brand=left.normalized_brand,
                        score=score,
                    )
                )

    candidates.sort(
        key=lambda candidate: (
            -candidate.score,
            candidate.left_source_record_id,
            candidate.right_source_record_id,
        )
    )
    return HomologationResult(
        profiles=profiles,
        exact_gtin_groups=tuple(exact_groups),
        candidates=tuple(candidates),
    )
