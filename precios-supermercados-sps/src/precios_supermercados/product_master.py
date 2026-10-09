"""Producto maestro (MDM / golden record) para la homologación entre cadenas.

Separa tres cosas que antes vivían en una sola columna
(``product_homologation_profiles.canonical_product_id = prod_gtin_<14>``):

- ``master_products``: identidad estable (``mp_…``) con atributos *golden*
  elegidos por reglas de supervivencia y su procedencia;
- ``master_product_links``: qué producto fuente pertenece a qué maestro, con
  qué método, evidencia, autor y estado (historial: nunca se borra);
- ``master_link_rejections``: decisiones humanas "Distinto" que nunca se
  vuelven a proponer.

Este módulo es Python puro y determinista: construye el estado deseado desde
los perfiles derivados ya calculados en memoria, lo compara con el estado
persistido leído de forma acotada y produce un plan mínimo de escrituras. Los
mismos pasos SQL sirven para SQLite (pruebas/herramientas) y Turso (lotes
Hrana). Ver ``docs/homologation/product-master-v1.md``.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Iterable, Mapping, Protocol, Sequence

import yaml

from .gtin_policy import SKU_DERIVED_GTIN_SUPERMARKETS
from .identifiers import canonicalize_gtin, generate_gtin_product_id
from .product_homologation import fold_text
from .product_identity_v2 import (
    _FLAVOR_ALIASES,
    _VARIANT_GROUPS,
    _phrase_present,
    normalize_variant_text,
)

MASTER_BUILDER_VERSION = "product-master-v1"
MASTER_POLICY_SCHEMA = "precios-sps-product-master-policy/v1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY_PATH = PROJECT_ROOT / "config" / "homologation" / "identity-policy-v1.yaml"

MASTER_TABLE = "master_products"
LINK_TABLE = "master_product_links"
REJECTION_TABLE = "master_link_rejections"
STATE_TABLE = "master_sync_state"
DIRTY_TABLE = "master_sync_dirty"
MASTER_TABLES = (MASTER_TABLE, LINK_TABLE, REJECTION_TABLE, STATE_TABLE, DIRTY_TABLE)

LINK_METHODS = ("gtin_exact", "gtin_sku_derived", "engine_auto", "manual_review", "reviewed_decision")
GTIN_LINK_METHODS = frozenset({"gtin_exact", "gtin_sku_derived"})
# El refresco diario administra estos métodos (los recalcula y los reemplaza).
# Métodos que el refresco recalcula y escribe en cada corrida (GTIN, registro
# revisado y la regla del motor por atributos); ``manual_review`` sólo lo cambia
# la herramienta de revisión.
SYNC_MANAGED_METHODS = frozenset({"gtin_exact", "gtin_sku_derived", "reviewed_decision", "engine_auto"})
# Vínculos curados: no se derivan del GTIN; los exportadores los superponen.
CURATED_LINK_METHODS = frozenset({"manual_review", "reviewed_decision", "engine_auto"})
LINK_STATUSES = ("active", "rejected", "superseded")
MASTER_STATUSES = ("active", "merged", "retired")
ORIGIN_METHODS = ("gtin", "manual_review", "reviewed_decision", "engine")
NET_CONTENT_UNITS = ("g", "ml", "unit", "oz")
HUMAN_ATTRIBUTES = (
    "brand",
    "manufacturer",
    "display_name",
    "product_type",
    "category",
    "net_content_value",
    "net_content_unit",
    "pack_count",
    "variant",
    "origin",
)
GOLDEN_ATTRIBUTES = HUMAN_ATTRIBUTES
_ID_RE = re.compile(r"^mp_[0-9a-f]{20}$")
_DIMENSION_UNIT = {"mass_g": "g", "volume_ml": "ml", "count": "unit", "ounce": "oz"}
_PRESENTATION_EXCLUDED = frozenset({"conflict", "ambiguous_multipack", "missing"})
_PRESENTATION_STATUS_RANK = {
    "confirmed": 0,
    "source_only": 1,
    "name_only": 1,
    "name_preferred_source_conflict": 2,
}


class ProductMasterError(ValueError):
    """El estado maestro no cumple el contrato."""


# --------------------------------------------------------------------------
# Política
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MasterPolicy:
    enabled: bool = True
    active_methods: frozenset[str] = frozenset(
        {"gtin_exact", "gtin_sku_derived", "manual_review", "reviewed_decision"}
    )
    serving_methods: frozenset[str] = frozenset({"manual_review", "reviewed_decision"})
    sku_derived_supermarkets: frozenset[str] = SKU_DERIVED_GTIN_SUPERMARKETS
    different_valid_gtin: str = "review_only"

    def __post_init__(self) -> None:
        unknown = (self.active_methods | self.serving_methods) - set(LINK_METHODS)
        if unknown:
            raise ProductMasterError("master_policy_link_method_unknown:" + ",".join(sorted(unknown)))
        if not self.serving_methods <= CURATED_LINK_METHODS:
            raise ProductMasterError("master_policy_serving_method_not_curated")
        if not self.serving_methods <= self.active_methods:
            raise ProductMasterError("master_policy_serving_method_inactive")
        if self.different_valid_gtin != "review_only":
            raise ProductMasterError("master_policy_different_gtin_must_be_review_only")

    @property
    def gtin_links_enabled(self) -> bool:
        return self.enabled and bool(self.active_methods & GTIN_LINK_METHODS)

    def method_active(self, method: str) -> bool:
        return self.enabled and method in self.active_methods

    def digest(self) -> str:
        return _sha256(
            {
                "enabled": self.enabled,
                "active_methods": sorted(self.active_methods),
                "serving_methods": sorted(self.serving_methods),
                "sku_derived_supermarkets": sorted(self.sku_derived_supermarkets),
                "different_valid_gtin": self.different_valid_gtin,
            }
        )

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> "MasterPolicy":
        section = raw.get("product_master")
        if section is None:
            # Sin sección explícita: sólo vínculos GTIN (reproduce la identidad vigente).
            return cls(serving_methods=frozenset(), active_methods=frozenset(GTIN_LINK_METHODS))
        if not isinstance(section, Mapping):
            raise ProductMasterError("master_policy_shape_invalid")
        if section.get("schema") != MASTER_POLICY_SCHEMA:
            raise ProductMasterError("master_policy_schema_invalid")
        methods = section.get("link_methods")
        if not isinstance(methods, Mapping) or set(methods) != set(LINK_METHODS):
            raise ProductMasterError("master_policy_link_methods_invalid")
        if any(value not in {"active", "disabled"} for value in methods.values()):
            raise ProductMasterError("master_policy_link_method_state_invalid")
        serving = section.get("serving_link_methods")
        if not isinstance(serving, list) or any(not isinstance(item, str) for item in serving):
            raise ProductMasterError("master_policy_serving_invalid")
        restricted = raw.get("restricted_gtin")
        sku_derived = SKU_DERIVED_GTIN_SUPERMARKETS
        if isinstance(restricted, Mapping) and isinstance(restricted.get("sku_derived_gtin_supermarkets"), list):
            sku_derived = frozenset(str(item) for item in restricted["sku_derived_gtin_supermarkets"])
        enabled = section.get("enabled")
        if not isinstance(enabled, bool):
            raise ProductMasterError("master_policy_enabled_invalid")
        return cls(
            enabled=enabled,
            active_methods=frozenset(name for name, state in methods.items() if state == "active"),
            serving_methods=frozenset(serving),
            sku_derived_supermarkets=sku_derived,
            different_valid_gtin=str(section.get("different_valid_gtin", "review_only")),
        )


def load_master_policy(path: Path = DEFAULT_POLICY_PATH) -> MasterPolicy:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ProductMasterError("master_policy_unreadable") from exc
    if not isinstance(raw, Mapping):
        raise ProductMasterError("master_policy_shape_invalid")
    return MasterPolicy.from_mapping(raw)


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(payload: object) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def gtin_master_id(gtin: str) -> str:
    """Maestro nacido de un GTIN: id determinista (reruns idempotentes)."""

    canonical = canonicalize_gtin(gtin)
    if canonical is None:
        raise ProductMasterError("master_gtin_invalid")
    return "mp_" + hashlib.sha256(f"gtin:{canonical}".encode("utf-8")).hexdigest()[:20]


def seeded_master_id(kind: str, seed: str) -> str:
    """Maestro sin GTIN: id opaco estable a partir de la decisión que lo crea."""

    if kind not in {"verified", "manual", "engine"} or not seed.strip():
        raise ProductMasterError("master_seed_invalid")
    return "mp_" + hashlib.sha256(f"{kind}:{seed}".encode("utf-8")).hexdigest()[:20]


def is_master_id(value: object) -> bool:
    return isinstance(value, str) and _ID_RE.fullmatch(value) is not None


def public_canonical_id(master_product_id: str, primary_gtin: str | None) -> str:
    """Id público de catálogo: ``prod_gtin_*`` para maestros GTIN (paridad), ``mp_*`` si no."""

    if primary_gtin:
        return generate_gtin_product_id(primary_gtin)
    return master_product_id


def source_product_key(supermarket_id: str, product_id: int) -> str:
    return f"{supermarket_id}:{product_id}"


def _decimal_text(value: Decimal) -> str:
    rendered = format(value.normalize(), "f")
    return "0" if rendered in {"", "-0"} else rendered


def _clean_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


# --------------------------------------------------------------------------
# Entrada del builder
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MemberProfile:
    """Perfil derivado + nombre fuente de un producto, lo mínimo para el maestro."""

    product_id: int
    supermarket_id: str
    source_name: str
    canonical_gtin: str | None
    comparison_status: str
    normalized_brand: str | None = None
    brand_resolution_source: str = "missing"
    product_type: str | None = None
    taxonomy_rule_id: str | None = None
    category: str | None = None
    presentation_dimension: str | None = None
    presentation_total_base: str | None = None
    presentation_pack_count: int | None = None
    presentation_status: str = "missing"

    @property
    def key(self) -> str:
        return source_product_key(self.supermarket_id, self.product_id)

    @classmethod
    def from_profile_row(cls, row: object, source_name: str) -> "MemberProfile":
        """Adapta ``ProductHomologationRow`` (o un objeto con los mismos campos)."""

        return cls(
            product_id=int(getattr(row, "product_id")),
            supermarket_id=str(getattr(row, "supermarket_id")),
            source_name=source_name,
            canonical_gtin=getattr(row, "canonical_gtin"),
            comparison_status=str(getattr(row, "comparison_status")),
            normalized_brand=getattr(row, "normalized_brand"),
            brand_resolution_source=str(getattr(row, "brand_resolution_source", "missing")),
            product_type=getattr(row, "product_type"),
            taxonomy_rule_id=getattr(row, "taxonomy_rule_id"),
            category=getattr(row, "category"),
            presentation_dimension=getattr(row, "presentation_dimension"),
            presentation_total_base=getattr(row, "presentation_total_base"),
            presentation_pack_count=getattr(row, "presentation_pack_count"),
            presentation_status=str(getattr(row, "presentation_status")),
        )


def member_profiles(
    rows: Iterable[object],
    names_by_product_id: Mapping[int, str],
) -> tuple[MemberProfile, ...]:
    result = []
    for row in rows:
        product_id = int(getattr(row, "product_id"))
        name = names_by_product_id.get(product_id)
        if name is None:
            raise ProductMasterError("master_member_source_name_missing")
        result.append(MemberProfile.from_profile_row(row, name))
    return tuple(sorted(result, key=lambda item: item.product_id))


# --------------------------------------------------------------------------
# Golden record
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GoldenRecord:
    master_product_id: str
    primary_gtin: str | None
    origin_method: str
    display_name: str
    brand: str | None = None
    manufacturer: str | None = None
    product_type: str | None = None
    category: str | None = None
    net_content_value: str | None = None
    net_content_unit: str | None = None
    pack_count: int | None = None
    variant: str | None = None
    origin: str | None = None
    attribute_provenance: Mapping[str, object] = field(default_factory=dict)
    human_attributes: Mapping[str, object] = field(default_factory=dict)
    status: str = "active"
    merged_into: str | None = None
    builder_version: str = MASTER_BUILDER_VERSION

    def __post_init__(self) -> None:
        if not is_master_id(self.master_product_id):
            raise ProductMasterError("master_product_id_invalid")
        if self.primary_gtin is not None and (
            len(self.primary_gtin) != 14 or not self.primary_gtin.isdigit()
        ):
            raise ProductMasterError("master_primary_gtin_invalid")
        if self.origin_method not in ORIGIN_METHODS:
            raise ProductMasterError("master_origin_method_invalid")
        if not self.display_name.strip():
            raise ProductMasterError("master_display_name_missing")
        if self.net_content_unit is not None and self.net_content_unit not in NET_CONTENT_UNITS:
            raise ProductMasterError("master_net_content_unit_invalid")
        if (self.net_content_value is None) != (self.net_content_unit is None):
            raise ProductMasterError("master_net_content_partial")
        if self.pack_count is not None and (type(self.pack_count) is not int or self.pack_count <= 0):
            raise ProductMasterError("master_pack_count_invalid")
        if self.status not in MASTER_STATUSES:
            raise ProductMasterError("master_status_invalid")
        if (self.status == "merged") != (self.merged_into is not None):
            raise ProductMasterError("master_merged_into_mismatch")
        if set(self.human_attributes) - set(HUMAN_ATTRIBUTES):
            raise ProductMasterError("master_human_attribute_unknown")

    def golden_payload(self) -> dict[str, object]:
        return {
            "master_product_id": self.master_product_id,
            "primary_gtin": self.primary_gtin,
            "origin_method": self.origin_method,
            "brand": self.brand,
            "manufacturer": self.manufacturer,
            "display_name": self.display_name,
            "product_type": self.product_type,
            "category": self.category,
            "net_content_value": self.net_content_value,
            "net_content_unit": self.net_content_unit,
            "pack_count": self.pack_count,
            "variant": self.variant,
            "origin": self.origin,
            "attribute_provenance": self.attribute_provenance,
            "human_attributes": self.human_attributes,
            "builder_version": self.builder_version,
        }

    @property
    def record_hash(self) -> str:
        """Huella de atributos golden + procedencia (sin estado ni timestamps)."""

        return _sha256(self.golden_payload())

    @property
    def public_id(self) -> str:
        return public_canonical_id(self.master_product_id, self.primary_gtin)


def golden_variant_labels(source_name: str) -> frozenset[str]:
    """Variante declarada en el nombre: sabor/aroma y formulación no estándar.

    Reutiliza el vocabulario del motor v2 (sabores con alias, ``zero``/``sin
    azúcar``/``light``, entera/descremada) sin construir un perfil completo.
    """

    text = normalize_variant_text(source_name)
    text = re.sub(r"(?<!\w)zero\s+alcohol(?!\w)", "sin_alcohol", text)
    labels: set[str] = set()
    for index, group in enumerate(_VARIANT_GROUPS):
        if index == 0:
            continue  # "Original/Clásico" es el valor por defecto, no una variante.
        for label in group:
            if _phrase_present(text, label):
                labels.add(label)
    for alias, canonical in _FLAVOR_ALIASES.items():
        if _phrase_present(text, alias):
            labels.add(f"flavor:{canonical}")
    return frozenset(labels)


def _render_variant(labels: frozenset[str]) -> str | None:
    if not labels:
        return None
    rendered = sorted(label.removeprefix("flavor:") for label in labels)
    return ", ".join(rendered)


def _imperial_derived(total: Decimal) -> bool:
    """Cantidad convertida desde lb/oz (más de 2 decimales: 453.59237, 28.3495…)."""

    exponent = total.normalize().as_tuple().exponent
    return isinstance(exponent, int) and exponent < -2


def _provenance(rule: str, supporters: Sequence[MemberProfile], total: int) -> dict[str, object]:
    ids = sorted({member.product_id for member in supporters})
    return {
        "rule": rule,
        "support": len(supporters),
        "members": total,
        "supermarkets": sorted({member.supermarket_id for member in supporters}),
        "product_ids": ids[:20],
    }


def _display_order(members: Sequence[MemberProfile], sku_derived: frozenset[str]) -> list[MemberProfile]:
    """Orden de preferencia para nombre/desempates.

    Nombre compartido por más cadenas (Walmart/Paiz publican el mismo) > barcode
    explícito (Colonial/Comisariato abrevian) > nombre más completo.
    """

    def name_key(member: MemberProfile) -> str:
        folded = fold_text(member.source_name) or ""
        return " ".join(token for token in folded.split() if not any(char.isdigit() for char in token))

    def tokens(member: MemberProfile) -> int:
        return len(name_key(member).split())

    shared = Counter(name_key(member) for member in members)
    return sorted(
        members,
        key=lambda member: (
            -shared[name_key(member)],
            member.supermarket_id in sku_derived,
            -tokens(member),
            -len(member.source_name),
            member.supermarket_id,
            member.product_id,
        ),
    )


def _vote(
    members: Sequence[MemberProfile],
    value_of: Callable[[MemberProfile], object | None],
    tier_of: Callable[[MemberProfile], int],
    order: Sequence[MemberProfile],
) -> tuple[object | None, list[MemberProfile], int | None]:
    """Mejor nivel de evidencia → más frecuente → primer miembro en ``order``."""

    voters = [(member, value_of(member)) for member in members]
    voters = [(member, value) for member, value in voters if value is not None]
    if not voters:
        return None, [], None
    best_tier = min(tier_of(member) for member, _ in voters)
    tiered = [(member, value) for member, value in voters if tier_of(member) == best_tier]
    counts = Counter(value for _, value in tiered)
    rank = {member.product_id: index for index, member in enumerate(order)}

    def first_rank(value: object) -> int:
        return min(rank.get(member.product_id, 1 << 30) for member, candidate in tiered if candidate == value)

    winner = sorted(counts, key=lambda value: (-counts[value], first_rank(value), str(value)))[0]
    supporters = [member for member, value in tiered if value == winner]
    return winner, supporters, best_tier


def build_golden_record(
    master_product_id: str,
    *,
    primary_gtin: str | None,
    origin_method: str,
    members: Sequence[MemberProfile],
    human_attributes: Mapping[str, Mapping[str, object]] | None = None,
    sku_derived_supermarkets: frozenset[str] = SKU_DERIVED_GTIN_SUPERMARKETS,
) -> GoldenRecord:
    """Supervivencia determinista de atributos con procedencia.

    - marca: reportada por la fuente > inferida del nombre; luego la más
      frecuente; empate por el miembro preferido;
    - nombre: barcode explícito antes que GTIN derivado de SKU (nombres
      abreviados), luego el más completo;
    - tipo/categoría: regla por nombre > palabra clave de la ruta fuente, luego
      el más frecuente;
    - contenido neto: métrico > onza; valor no convertido desde lb/oz > valor
      convertido; más frecuente; presentación confirmada > inferida;
    - variante: conjunto declarado más frecuente (vacío = sin variante);
    - un atributo fijado por un humano nunca se sobrescribe.
    """

    if not members:
        raise ProductMasterError("master_members_missing")
    ordered_members = sorted(members, key=lambda member: member.product_id)
    if len({member.product_id for member in ordered_members}) != len(ordered_members):
        raise ProductMasterError("master_member_duplicate")
    order = _display_order(ordered_members, sku_derived_supermarkets)
    total = len(ordered_members)
    provenance: dict[str, object] = {}
    values: dict[str, object | None] = {}

    display = order[0]
    values["display_name"] = _clean_text(display.source_name) or display.source_name
    provenance["display_name"] = _provenance(
        "shared_name_then_explicit_barcode_then_most_complete", [display], total
    )

    brand_tier = {"source": 0, "name_known_brand": 1}
    brand, supporters, tier = _vote(
        ordered_members,
        lambda member: _clean_text(member.normalized_brand),
        lambda member: brand_tier.get(member.brand_resolution_source, 2),
        order,
    )
    values["brand"] = brand
    provenance["brand"] = (
        _provenance(
            "source_reported_brand_majority" if tier == 0 else "name_derived_brand_majority",
            supporters,
            total,
        )
        if brand is not None
        else {"rule": "no_member_value", "members": total}
    )

    def type_tier(member: MemberProfile) -> int:
        return 1 if (member.taxonomy_rule_id or "").startswith("source_category") else 0

    product_type, supporters, tier = _vote(
        ordered_members, lambda member: _clean_text(member.product_type), type_tier, order
    )
    values["product_type"] = product_type
    provenance["product_type"] = (
        _provenance("name_rule_majority" if tier == 0 else "source_category_keyword_majority", supporters, total)
        if product_type is not None
        else {"rule": "no_member_value", "members": total}
    )
    category_pool = [member for member in ordered_members if product_type is None or member.product_type == product_type]
    category, supporters, _ = _vote(
        category_pool or ordered_members, lambda member: _clean_text(member.category), lambda _: 0, order
    )
    values["category"] = category
    provenance["category"] = (
        _provenance("category_of_winning_type_majority", supporters, total)
        if category is not None
        else {"rule": "no_member_value", "members": total}
    )

    candidates: dict[tuple[str, str, int], list[MemberProfile]] = defaultdict(list)
    best_status: dict[tuple[str, str, int], int] = {}
    for member in ordered_members:
        if (
            member.presentation_dimension not in _DIMENSION_UNIT
            or member.presentation_total_base is None
            or member.presentation_status in _PRESENTATION_EXCLUDED
        ):
            continue
        try:
            total_base = Decimal(member.presentation_total_base)
        except InvalidOperation:
            continue
        if not total_base.is_finite() or total_base <= 0:
            continue
        key = (member.presentation_dimension, _decimal_text(total_base), int(member.presentation_pack_count or 1))
        candidates[key].append(member)
        rank = _PRESENTATION_STATUS_RANK.get(member.presentation_status, 3)
        best_status[key] = min(best_status.get(key, 9), rank)
    if candidates:
        def presentation_rank(key: tuple[str, str, int]) -> tuple[object, ...]:
            dimension, total_text, _ = key
            return (
                1 if dimension == "ounce" else 0,
                1 if _imperial_derived(Decimal(total_text)) else 0,
                -len(candidates[key]),
                best_status[key],
                Decimal(total_text),
                key,
            )

        chosen = sorted(candidates, key=presentation_rank)[0]
        dimension, total_text, pack = chosen
        values["net_content_value"] = total_text
        values["net_content_unit"] = _DIMENSION_UNIT[dimension]
        values["pack_count"] = pack
        rule = (
            "metric_over_imperial_then_majority"
            if dimension != "ounce"
            else "ounce_only_available"
        )
        provenance["net_content"] = _provenance(rule, candidates[chosen], total)
        provenance["net_content"]["alternatives"] = len(candidates) - 1  # type: ignore[index]
    else:
        values["net_content_value"] = None
        values["net_content_unit"] = None
        values["pack_count"] = None
        provenance["net_content"] = {"rule": "no_usable_presentation", "members": total}

    label_sets = {member.product_id: golden_variant_labels(member.source_name) for member in ordered_members}
    variant_counts = Counter(labels for labels in label_sets.values() if labels)
    if variant_counts:
        rank = {member.product_id: index for index, member in enumerate(order)}
        winner = sorted(
            variant_counts,
            key=lambda labels: (
                -variant_counts[labels],
                -len(labels),
                min(rank[pid] for pid, value in label_sets.items() if value == labels),
            ),
        )[0]
        values["variant"] = _render_variant(winner)
        provenance["variant"] = _provenance(
            "declared_variant_majority",
            [member for member in ordered_members if label_sets[member.product_id] == winner],
            total,
        )
    else:
        values["variant"] = None
        provenance["variant"] = {"rule": "no_declared_variant", "members": total}

    values["manufacturer"] = None
    values["origin"] = None
    provenance["manufacturer"] = {"rule": "no_source_data"}
    provenance["origin"] = {"rule": "no_source_data"}

    humans = {
        str(name): dict(entry)
        for name, entry in sorted((human_attributes or {}).items())
    }
    for name, entry in humans.items():
        if name not in HUMAN_ATTRIBUTES:
            raise ProductMasterError("master_human_attribute_unknown")
        if "value" not in entry:
            raise ProductMasterError("master_human_attribute_value_missing")
        values[name] = entry["value"]
        provenance[name] = {
            "rule": "human_set",
            "decided_by": entry.get("decided_by"),
            "decided_at_utc": entry.get("decided_at_utc"),
        }
    pack_count = values.get("pack_count")
    return GoldenRecord(
        master_product_id=master_product_id,
        primary_gtin=primary_gtin,
        origin_method=origin_method,
        display_name=str(values["display_name"]),
        brand=values.get("brand"),  # type: ignore[arg-type]
        manufacturer=values.get("manufacturer"),  # type: ignore[arg-type]
        product_type=values.get("product_type"),  # type: ignore[arg-type]
        category=values.get("category"),  # type: ignore[arg-type]
        net_content_value=None if values.get("net_content_value") is None else str(values["net_content_value"]),
        net_content_unit=values.get("net_content_unit"),  # type: ignore[arg-type]
        pack_count=None if pack_count is None else int(pack_count),  # type: ignore[arg-type]
        variant=values.get("variant"),  # type: ignore[arg-type]
        origin=values.get("origin"),  # type: ignore[arg-type]
        attribute_provenance=dict(sorted(provenance.items())),
        human_attributes=humans,
    )


# --------------------------------------------------------------------------
# Vínculos y estado deseado
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MasterLink:
    product_id: int
    supermarket_id: str
    master_product_id: str
    link_method: str
    decided_by: str
    evidence: Mapping[str, object] = field(default_factory=dict)
    confidence: float | None = None
    decided_at_utc: str | None = None
    link_id: int | None = None
    status: str = "active"

    def __post_init__(self) -> None:
        if type(self.product_id) is not int or self.product_id <= 0:
            raise ProductMasterError("master_link_product_id_invalid")
        if not self.supermarket_id.strip():
            raise ProductMasterError("master_link_supermarket_missing")
        if not is_master_id(self.master_product_id):
            raise ProductMasterError("master_link_master_id_invalid")
        if self.link_method not in LINK_METHODS:
            raise ProductMasterError("master_link_method_invalid")
        if self.status not in LINK_STATUSES:
            raise ProductMasterError("master_link_status_invalid")
        if not (
            self.decided_by == "system"
            or (self.decided_by.startswith("engine:") and len(self.decided_by) > 7)
            or (self.decided_by.startswith("human:") and len(self.decided_by) > 6)
        ):
            raise ProductMasterError("master_link_decided_by_invalid")
        if self.link_method in GTIN_LINK_METHODS and self.decided_by != "system":
            raise ProductMasterError("master_link_gtin_must_be_system")
        if self.link_method in {"manual_review", "reviewed_decision"} and not self.decided_by.startswith("human:"):
            raise ProductMasterError("master_link_manual_must_be_human")
        if self.link_method == "engine_auto" and not self.decided_by.startswith("engine:"):
            raise ProductMasterError("master_link_engine_must_be_engine")
        if self.confidence is not None and not 0.0 <= float(self.confidence) <= 1.0:
            raise ProductMasterError("master_link_confidence_invalid")

    @property
    def key(self) -> str:
        return source_product_key(self.supermarket_id, self.product_id)

    @property
    def link_hash(self) -> str:
        return _sha256(
            {
                "product_id": self.product_id,
                "supermarket_id": self.supermarket_id,
                "master_product_id": self.master_product_id,
                "link_method": self.link_method,
                "decided_by": self.decided_by,
                "evidence": self.evidence,
                "confidence": None if self.confidence is None else round(float(self.confidence), 6),
            }
        )


@dataclass
class DesiredState:
    masters: dict[str, GoldenRecord]
    links: dict[int, MasterLink]
    gtin_members: dict[str, tuple[int, ...]]
    diagnostics: dict[str, int]

    def digest(self) -> str:
        return _sha256(
            {
                "masters": sorted((master_id, record.record_hash, record.status) for master_id, record in self.masters.items()),
                "links": sorted((product_id, link.link_hash) for product_id, link in self.links.items()),
            }
        )

    def master_of_product(self, product_id: int) -> str | None:
        link = self.links.get(product_id)
        return None if link is None else link.master_product_id


def gtin_link_method(supermarket_id: str, policy: MasterPolicy) -> str:
    return "gtin_sku_derived" if supermarket_id in policy.sku_derived_supermarkets else "gtin_exact"


def build_desired_state(
    members: Sequence[MemberProfile],
    *,
    policy: MasterPolicy,
    curated_links: Sequence[MasterLink] = (),
    human_attributes: Mapping[str, Mapping[str, Mapping[str, object]]] | None = None,
    existing_master_meta: Mapping[str, tuple[str, str | None]] | None = None,
) -> DesiredState:
    """Estado maestro deseado, completo y determinista, desde perfiles en memoria.

    - Un maestro por GTIN canónico con al menos un perfil ``ready`` o
      ``single_source`` (mismos miembros que la identidad vigente).
    - Vínculo GTIN activo sólo para perfiles ``ready``/``single_source``; los
      ``review_required`` (miembros excluidos, colisiones) quedan sin vínculo.
    - Dos ``single_source`` del mismo supermercado con el mismo GTIN no se
      vinculan (colisión: ≤ 1 vínculo activo por cadena y maestro).
    - Los vínculos curados (manual/registro/motor) tienen prioridad sobre el
      GTIN del propio producto y se pasan tal cual.
    """

    human_attributes = human_attributes or {}
    existing_master_meta = existing_master_meta or {}
    by_product = {member.product_id: member for member in members}
    if len(by_product) != len(members):
        raise ProductMasterError("master_member_duplicate")
    diagnostics: Counter[str] = Counter()

    curated_by_product: dict[int, MasterLink] = {}
    for link in curated_links:
        if link.status != "active" or link.link_method not in CURATED_LINK_METHODS:
            raise ProductMasterError("master_curated_link_invalid")
        if link.product_id in curated_by_product:
            raise ProductMasterError("master_curated_link_duplicate_product")
        if link.product_id not in by_product:
            diagnostics["curated_link_product_missing"] += 1
            continue
        if not policy.method_active(link.link_method):
            diagnostics[f"curated_link_inactive_{link.link_method}"] += 1
            continue
        curated_by_product[link.product_id] = link

    gtin_groups: dict[str, list[MemberProfile]] = defaultdict(list)
    for member in members:
        if member.canonical_gtin is not None:
            gtin_groups[member.canonical_gtin].append(member)

    links: dict[int, MasterLink] = {}
    master_members: dict[str, list[MemberProfile]] = defaultdict(list)
    master_meta: dict[str, tuple[str, str | None]] = {}
    gtin_members: dict[str, tuple[int, ...]] = {}
    if policy.gtin_links_enabled:
        for gtin, group in sorted(gtin_groups.items()):
            eligible = [member for member in group if member.comparison_status in {"ready", "single_source"}]
            if not eligible:
                diagnostics["gtin_groups_without_eligible_member"] += 1
                continue
            master_id = gtin_master_id(gtin)
            gtin_members[master_id] = tuple(sorted(member.product_id for member in group))
            per_supermarket = Counter(member.supermarket_id for member in eligible)
            for member in eligible:
                if member.product_id in curated_by_product:
                    diagnostics["gtin_link_overridden_by_curated"] += 1
                    continue
                if per_supermarket[member.supermarket_id] > 1:
                    # ready ya excluye colisiones; sólo single_source puede llegar aquí.
                    diagnostics["single_source_same_retailer_collision"] += 1
                    continue
                method = gtin_link_method(member.supermarket_id, policy)
                if not policy.method_active(method):
                    diagnostics[f"gtin_link_inactive_{method}"] += 1
                    continue
                links[member.product_id] = MasterLink(
                    product_id=member.product_id,
                    supermarket_id=member.supermarket_id,
                    master_product_id=master_id,
                    link_method=method,
                    decided_by="system",
                    evidence={"canonical_gtin": gtin},
                )
                master_members[master_id].append(member)
                master_meta[master_id] = ("gtin", gtin)

    for product_id, link in sorted(curated_by_product.items()):
        member = by_product[product_id]
        if link.supermarket_id != member.supermarket_id:
            raise ProductMasterError("master_curated_link_supermarket_mismatch")
        links[product_id] = link
        master_members[link.master_product_id].append(member)
        if link.master_product_id not in master_meta:
            meta = existing_master_meta.get(link.master_product_id)
            if meta is None:
                raise ProductMasterError("master_curated_link_target_unknown")
            master_meta[link.master_product_id] = meta

    masters: dict[str, GoldenRecord] = {}
    for master_id, group in sorted(master_members.items()):
        supermarkets = Counter(member.supermarket_id for member in group)
        if any(count > 1 for count in supermarkets.values()):
            # Un curado que choca con un GTIN del mismo supermercado: gana el curado.
            curated_ids = {pid for pid in curated_by_product if curated_by_product[pid].master_product_id == master_id}
            kept: list[MemberProfile] = []
            seen: set[str] = set()
            for member in sorted(group, key=lambda item: (item.product_id not in curated_ids, item.product_id)):
                if member.supermarket_id in seen:
                    links.pop(member.product_id, None)
                    diagnostics["retailer_slot_conflict_dropped"] += 1
                    continue
                seen.add(member.supermarket_id)
                kept.append(member)
            group = kept
        origin, primary_gtin = master_meta[master_id]
        masters[master_id] = build_golden_record(
            master_id,
            primary_gtin=primary_gtin,
            origin_method=origin,
            members=group,
            human_attributes=human_attributes.get(master_id),
            sku_derived_supermarkets=policy.sku_derived_supermarkets,
        )
    diagnostics["masters"] = len(masters)
    diagnostics["active_links"] = len(links)
    diagnostics["multi_retailer_masters"] = _multi_retailer_count(links)
    return DesiredState(masters=masters, links=links, gtin_members=gtin_members, diagnostics=dict(sorted(diagnostics.items())))


def _multi_retailer_count(links: Mapping[int, MasterLink]) -> int:
    retailers: dict[str, set[str]] = defaultdict(set)
    for link in links.values():
        retailers[link.master_product_id].add(link.supermarket_id)
    return sum(1 for value in retailers.values() if len(value) >= 2)


def profile_state_digest(state: Mapping[int, tuple[str, str]]) -> str:
    """Huella del estado de perfiles (product_id → hash, versión)."""

    return _sha256(sorted((int(key), value[0], value[1]) for key, value in state.items()))


# --------------------------------------------------------------------------
# Esquema
# --------------------------------------------------------------------------

MASTER_COLUMNS = (
    "master_product_id",
    "primary_gtin",
    "origin_method",
    "brand",
    "manufacturer",
    "display_name",
    "product_type",
    "category",
    "net_content_value",
    "net_content_unit",
    "pack_count",
    "variant",
    "origin",
    "attribute_provenance_json",
    "human_attributes_json",
    "status",
    "merged_into",
    "record_hash",
    "builder_version",
    "created_at_utc",
    "updated_at_utc",
    "version",
)
LINK_COLUMNS = (
    "link_id",
    "product_id",
    "supermarket_id",
    "master_product_id",
    "link_method",
    "confidence",
    "evidence_json",
    "decided_by",
    "decided_at_utc",
    "status",
    "status_changed_at_utc",
    "link_hash",
)
REJECTION_COLUMNS = (
    "product_id",
    "supermarket_id",
    "master_product_id",
    "decided_by",
    "decided_at_utc",
    "note",
    "evidence_json",
)
STATE_COLUMNS = (
    "state_id",
    "builder_version",
    "normalization_version",
    "policy_digest",
    "profile_digest",
    "state_digest",
    "masters",
    "active_links",
    "synced_at_utc",
)
DIRTY_COLUMNS = ("master_product_id", "reason", "marked_at_utc")
EXPECTED_COLUMNS = {
    MASTER_TABLE: MASTER_COLUMNS,
    LINK_TABLE: LINK_COLUMNS,
    REJECTION_TABLE: REJECTION_COLUMNS,
    STATE_TABLE: STATE_COLUMNS,
    DIRTY_TABLE: DIRTY_COLUMNS,
}

_METHOD_LIST = ",".join(f"'{method}'" for method in LINK_METHODS)
_STATUS_LIST = ",".join(f"'{status}'" for status in LINK_STATUSES)

TABLE_SQL = {
    MASTER_TABLE: f"""CREATE TABLE IF NOT EXISTS {MASTER_TABLE} (
    master_product_id TEXT PRIMARY KEY CHECK (
        length(master_product_id) = 23 AND substr(master_product_id,1,3) = 'mp_'
    ),
    primary_gtin TEXT CHECK (
        primary_gtin IS NULL OR (length(primary_gtin) = 14 AND primary_gtin NOT GLOB '*[^0-9]*')
    ),
    origin_method TEXT NOT NULL CHECK (origin_method IN ('gtin','manual_review','reviewed_decision','engine')),
    brand TEXT,
    manufacturer TEXT,
    display_name TEXT NOT NULL CHECK (length(trim(display_name)) > 0),
    product_type TEXT,
    category TEXT,
    net_content_value TEXT,
    net_content_unit TEXT CHECK (net_content_unit IS NULL OR net_content_unit IN ('g','ml','unit','oz')),
    pack_count INTEGER CHECK (pack_count IS NULL OR pack_count > 0),
    variant TEXT,
    origin TEXT,
    attribute_provenance_json TEXT NOT NULL CHECK (json_valid(attribute_provenance_json)),
    human_attributes_json TEXT NOT NULL DEFAULT '{{}}' CHECK (json_valid(human_attributes_json)),
    status TEXT NOT NULL CHECK (status IN ('active','merged','retired')),
    merged_into TEXT REFERENCES {MASTER_TABLE}(master_product_id),
    record_hash TEXT NOT NULL CHECK (length(record_hash) = 64),
    builder_version TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    CHECK ((net_content_value IS NULL) = (net_content_unit IS NULL)),
    CHECK ((status = 'merged') = (merged_into IS NOT NULL)),
    CHECK (merged_into IS NULL OR merged_into <> master_product_id)
) STRICT""",
    LINK_TABLE: f"""CREATE TABLE IF NOT EXISTS {LINK_TABLE} (
    link_id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL,
    supermarket_id TEXT NOT NULL,
    master_product_id TEXT NOT NULL REFERENCES {MASTER_TABLE}(master_product_id),
    link_method TEXT NOT NULL CHECK (link_method IN ({_METHOD_LIST})),
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    evidence_json TEXT NOT NULL CHECK (json_valid(evidence_json)),
    decided_by TEXT NOT NULL CHECK (
        decided_by = 'system' OR decided_by GLOB 'engine:?*' OR decided_by GLOB 'human:?*'
    ),
    decided_at_utc TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ({_STATUS_LIST})),
    status_changed_at_utc TEXT,
    link_hash TEXT NOT NULL CHECK (length(link_hash) = 64),
    FOREIGN KEY (product_id, supermarket_id) REFERENCES products(product_id, supermarket_id),
    CHECK (link_method NOT IN ('gtin_exact','gtin_sku_derived') OR decided_by = 'system'),
    CHECK (link_method NOT IN ('manual_review','reviewed_decision') OR decided_by GLOB 'human:?*'),
    CHECK (link_method <> 'engine_auto' OR decided_by GLOB 'engine:?*')
) STRICT""",
    REJECTION_TABLE: f"""CREATE TABLE IF NOT EXISTS {REJECTION_TABLE} (
    product_id INTEGER NOT NULL,
    supermarket_id TEXT NOT NULL,
    master_product_id TEXT NOT NULL REFERENCES {MASTER_TABLE}(master_product_id),
    decided_by TEXT NOT NULL CHECK (decided_by GLOB 'human:?*'),
    decided_at_utc TEXT NOT NULL,
    note TEXT,
    evidence_json TEXT NOT NULL CHECK (json_valid(evidence_json)),
    PRIMARY KEY (product_id, master_product_id),
    FOREIGN KEY (product_id, supermarket_id) REFERENCES products(product_id, supermarket_id)
) STRICT""",
    STATE_TABLE: f"""CREATE TABLE IF NOT EXISTS {STATE_TABLE} (
    state_id INTEGER PRIMARY KEY CHECK (state_id = 1),
    builder_version TEXT NOT NULL,
    normalization_version TEXT NOT NULL,
    policy_digest TEXT NOT NULL CHECK (length(policy_digest) = 64),
    profile_digest TEXT NOT NULL CHECK (length(profile_digest) = 64),
    state_digest TEXT NOT NULL CHECK (length(state_digest) = 64),
    masters INTEGER NOT NULL CHECK (masters >= 0),
    active_links INTEGER NOT NULL CHECK (active_links >= 0),
    synced_at_utc TEXT NOT NULL
) STRICT""",
    DIRTY_TABLE: f"""CREATE TABLE IF NOT EXISTS {DIRTY_TABLE} (
    master_product_id TEXT PRIMARY KEY REFERENCES {MASTER_TABLE}(master_product_id),
    reason TEXT NOT NULL,
    marked_at_utc TEXT NOT NULL
) STRICT""",
}

INDEX_SQL = (
    (
        "idx_master_products_primary_gtin",
        f"CREATE UNIQUE INDEX IF NOT EXISTS idx_master_products_primary_gtin ON {MASTER_TABLE}(primary_gtin) "
        "WHERE primary_gtin IS NOT NULL",
    ),
    (
        "idx_master_products_human",
        f"CREATE INDEX IF NOT EXISTS idx_master_products_human ON {MASTER_TABLE}(master_product_id) "
        "WHERE human_attributes_json <> '{}'",
    ),
    (
        "idx_master_links_one_active_per_product",
        f"CREATE UNIQUE INDEX IF NOT EXISTS idx_master_links_one_active_per_product ON {LINK_TABLE}(product_id) "
        "WHERE status = 'active'",
    ),
    (
        "idx_master_links_one_active_per_retailer",
        f"CREATE UNIQUE INDEX IF NOT EXISTS idx_master_links_one_active_per_retailer "
        f"ON {LINK_TABLE}(master_product_id, supermarket_id) WHERE status = 'active'",
    ),
    (
        "idx_master_links_master",
        f"CREATE INDEX IF NOT EXISTS idx_master_links_master ON {LINK_TABLE}(master_product_id, status)",
    ),
    (
        "idx_master_links_method",
        f"CREATE INDEX IF NOT EXISTS idx_master_links_method ON {LINK_TABLE}(link_method, status)",
    ),
    (
        "idx_master_rejections_master",
        f"CREATE INDEX IF NOT EXISTS idx_master_rejections_master ON {REJECTION_TABLE}(master_product_id)",
    ),
)
SCHEMA_OBJECTS = tuple(MASTER_TABLES) + tuple(name for name, _ in INDEX_SQL)


class MasterStore(Protocol):
    """Lecturas acotadas y escrituras en lotes atómicos (SQLite o Turso)."""

    def query(self, sql: str, args: tuple[object, ...] = ()) -> list[tuple[object, ...]]: ...

    def write(self, steps: list[tuple[str, str, tuple[object, ...]]]) -> None: ...


class SQLiteMasterStore:
    """Adaptador SQLite (pruebas, herramientas locales) con conteo de filas leídas."""

    def __init__(self, connection) -> None:  # type: ignore[no-untyped-def]
        self.connection = connection
        self.rows_read = 0
        self.statements: list[str] = []

    def query(self, sql: str, args: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
        self.statements.append(" ".join(sql.split()))
        rows = [tuple(row) for row in self.connection.execute(sql, args).fetchall()]
        self.rows_read += len(rows)
        return rows

    def write(self, steps: list[tuple[str, str, tuple[object, ...]]]) -> None:
        con = self.connection
        in_tx = False
        try:
            for name, sql, args in steps:
                self.statements.append(" ".join(sql.split()))
                if name == "begin":
                    con.execute("BEGIN IMMEDIATE")
                    in_tx = True
                    continue
                if name == "commit":
                    con.execute("COMMIT")
                    in_tx = False
                    continue
                con.execute(sql, args)
        except Exception:
            if in_tx:
                con.execute("ROLLBACK")
            raise


def existing_schema_objects(store: MasterStore) -> set[str]:
    placeholders = ",".join("?" for _ in SCHEMA_OBJECTS)
    rows = store.query(
        f"SELECT name FROM sqlite_master WHERE type IN ('table','index') AND name IN ({placeholders})",
        tuple(SCHEMA_OBJECTS),
    )
    return {str(row[0]) for row in rows}


def ensure_master_schema(store: MasterStore) -> dict[str, object]:
    """Migración idempotente (CREATE IF NOT EXISTS) + verificación de columnas."""

    present = existing_schema_objects(store)
    created: list[str] = []
    if not set(SCHEMA_OBJECTS) <= present:
        steps: list[tuple[str, str, tuple[object, ...]]] = [("begin", "BEGIN IMMEDIATE", ())]
        for table in MASTER_TABLES:
            if table not in present:
                steps.append((f"create_{table}", TABLE_SQL[table], ()))
                created.append(table)
        for name, sql in INDEX_SQL:
            if name not in present:
                steps.append((f"create_{name}", sql, ()))
                created.append(name)
        steps.append(("commit", "COMMIT", ()))
        store.write(steps)
    verify_master_schema(store)
    return {"created": created, "already_applied": not created}


def verify_master_schema(store: MasterStore) -> None:
    present = existing_schema_objects(store)
    missing = sorted(set(SCHEMA_OBJECTS) - present)
    if missing:
        raise ProductMasterError("master_schema_missing:" + ",".join(missing))
    for table, expected in EXPECTED_COLUMNS.items():
        columns = tuple(str(row[0]) for row in store.query("SELECT name FROM pragma_table_info(?) ORDER BY cid", (table,)))
        if columns != expected:
            raise ProductMasterError(f"master_schema_mismatch:{table}")


# --------------------------------------------------------------------------
# Lecturas acotadas del estado persistido
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExistingMaster:
    master_product_id: str
    record_hash: str
    version: int
    created_at_utc: str
    status: str
    origin_method: str
    primary_gtin: str | None


@dataclass(frozen=True, slots=True)
class SyncState:
    builder_version: str
    normalization_version: str
    policy_digest: str
    profile_digest: str
    state_digest: str
    masters: int
    active_links: int
    synced_at_utc: str


_LINK_SELECT = (
    f"SELECT link_id,product_id,supermarket_id,master_product_id,link_method,confidence,"
    f"evidence_json,decided_by,decided_at_utc,status FROM {LINK_TABLE}"
)
_MASTER_SELECT = (
    f"SELECT master_product_id,record_hash,version,created_at_utc,status,origin_method,primary_gtin FROM {MASTER_TABLE}"
)
PAGE_SIZE = 2000
IN_CHUNK = 400


def _link_from_row(row: Sequence[object]) -> MasterLink:
    link_id, product_id, supermarket_id, master_id, method, confidence, evidence, decided_by, decided_at, status = row
    try:
        evidence_value = json.loads(str(evidence))
    except json.JSONDecodeError as exc:
        raise ProductMasterError("master_link_evidence_invalid") from exc
    if type(product_id) is not int or type(link_id) is not int:
        raise ProductMasterError("master_link_row_invalid")
    return MasterLink(
        product_id=product_id,
        supermarket_id=str(supermarket_id),
        master_product_id=str(master_id),
        link_method=str(method),
        decided_by=str(decided_by),
        evidence=evidence_value if isinstance(evidence_value, dict) else {},
        confidence=None if confidence is None else float(confidence),  # type: ignore[arg-type]
        decided_at_utc=str(decided_at),
        link_id=link_id,
        status=str(status),
    )


def _master_from_row(row: Sequence[object]) -> ExistingMaster:
    master_id, record_hash, version, created_at, status, origin, gtin = row
    if type(version) is not int:
        raise ProductMasterError("master_row_invalid")
    return ExistingMaster(str(master_id), str(record_hash), version, str(created_at), str(status), str(origin), None if gtin is None else str(gtin))


def read_sync_state(store: MasterStore) -> SyncState | None:
    rows = store.query(
        f"SELECT builder_version,normalization_version,policy_digest,profile_digest,state_digest,masters,"
        f"active_links,synced_at_utc FROM {STATE_TABLE} WHERE state_id=1"
    )
    if not rows:
        return None
    row = rows[0]
    return SyncState(*(str(value) if index not in {5, 6} else int(value) for index, value in enumerate(row)))  # type: ignore[arg-type]


def read_curated_links(store: MasterStore, methods: Iterable[str] = CURATED_LINK_METHODS) -> tuple[MasterLink, ...]:
    """Vínculos curados activos por índice ``(link_method, status)``: sin recorrer GTIN."""

    methods = tuple(sorted(set(methods)))
    if not methods:
        return ()
    placeholders = ",".join("?" for _ in methods)
    rows = store.query(
        f"{_LINK_SELECT} WHERE link_method IN ({placeholders}) AND status='active' ORDER BY link_id",
        methods,
    )
    return tuple(_link_from_row(row) for row in rows)


def read_human_attributes(store: MasterStore) -> dict[str, dict[str, dict[str, object]]]:
    rows = store.query(
        f"SELECT master_product_id,human_attributes_json FROM {MASTER_TABLE} "
        "WHERE human_attributes_json <> '{}' ORDER BY master_product_id"
    )
    result: dict[str, dict[str, dict[str, object]]] = {}
    for master_id, payload in rows:
        value = json.loads(str(payload))
        if not isinstance(value, dict):
            raise ProductMasterError("master_human_attributes_invalid")
        result[str(master_id)] = value
    return result


def read_dirty_masters(store: MasterStore) -> set[str]:
    return {str(row[0]) for row in store.query(f"SELECT master_product_id FROM {DIRTY_TABLE} ORDER BY master_product_id")}


def _chunked(values: Sequence[object], size: int = IN_CHUNK) -> Iterable[Sequence[object]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def read_masters_by_id(store: MasterStore, master_ids: Iterable[str]) -> dict[str, ExistingMaster]:
    ids = sorted(set(master_ids))
    result: dict[str, ExistingMaster] = {}
    for chunk in _chunked(ids):
        placeholders = ",".join("?" for _ in chunk)
        for row in store.query(f"{_MASTER_SELECT} WHERE master_product_id IN ({placeholders})", tuple(chunk)):
            master = _master_from_row(row)
            result[master.master_product_id] = master
    return result


def read_active_links_by_product(store: MasterStore, product_ids: Iterable[int]) -> dict[int, MasterLink]:
    ids = sorted(set(int(value) for value in product_ids))
    result: dict[int, MasterLink] = {}
    for chunk in _chunked(ids):
        placeholders = ",".join("?" for _ in chunk)
        for row in store.query(
            f"{_LINK_SELECT} WHERE product_id IN ({placeholders}) AND status='active'", tuple(chunk)
        ):
            link = _link_from_row(row)
            result[link.product_id] = link
    return result


def read_active_links_by_master(store: MasterStore, master_ids: Iterable[str]) -> dict[int, MasterLink]:
    ids = sorted(set(master_ids))
    result: dict[int, MasterLink] = {}
    for chunk in _chunked(ids):
        placeholders = ",".join("?" for _ in chunk)
        for row in store.query(
            f"{_LINK_SELECT} WHERE master_product_id IN ({placeholders}) AND status='active'", tuple(chunk)
        ):
            link = _link_from_row(row)
            result[link.product_id] = link
    return result


def read_all_masters(store: MasterStore) -> dict[str, ExistingMaster]:
    result: dict[str, ExistingMaster] = {}
    cursor = ""
    while True:
        rows = store.query(
            f"{_MASTER_SELECT} WHERE master_product_id>? ORDER BY master_product_id LIMIT {PAGE_SIZE}", (cursor,)
        )
        for row in rows:
            master = _master_from_row(row)
            result[master.master_product_id] = master
        if len(rows) < PAGE_SIZE:
            break
        cursor = str(rows[-1][0])
    return result


def read_all_active_links(store: MasterStore) -> dict[int, MasterLink]:
    result: dict[int, MasterLink] = {}
    cursor = -1
    while True:
        rows = store.query(
            f"{_LINK_SELECT} WHERE product_id>? AND status='active' ORDER BY product_id LIMIT {PAGE_SIZE}", (cursor,)
        )
        for row in rows:
            link = _link_from_row(row)
            if link.product_id in result:
                raise ProductMasterError("master_link_multiple_active")
            result[link.product_id] = link
        if len(rows) < PAGE_SIZE:
            break
        cursor = int(rows[-1][1])  # type: ignore[arg-type]
    return result


# --------------------------------------------------------------------------
# Diff y plan de escrituras
# --------------------------------------------------------------------------


@dataclass
class MasterPlan:
    master_upserts: list[tuple[GoldenRecord, int, str]] = field(default_factory=list)  # (record, version, created_at)
    master_retirements: list[tuple[str, int]] = field(default_factory=list)  # (id, new version)
    link_supersedes: list[int] = field(default_factory=list)
    link_inserts: list[MasterLink] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not (self.master_upserts or self.master_retirements or self.link_supersedes or self.link_inserts)


def diff_master_state(
    desired: DesiredState,
    existing_masters: Mapping[str, ExistingMaster],
    existing_links: Mapping[int, MasterLink],
    *,
    now: str,
    scope_masters: set[str] | None = None,
    scope_products: set[int] | None = None,
) -> MasterPlan:
    """Plan mínimo: sólo inserta/actualiza lo que cambió. ``scope`` = modo incremental."""

    plan = MasterPlan()
    counts: Counter[str] = Counter()
    master_ids = set(desired.masters) | {
        master_id for master_id, master in existing_masters.items() if master.status == "active"
    }
    if scope_masters is not None:
        master_ids &= scope_masters
    for master_id in sorted(master_ids):
        record = desired.masters.get(master_id)
        current = existing_masters.get(master_id)
        if record is None:
            if current is not None and current.status == "active":
                plan.master_retirements.append((master_id, current.version + 1))
                counts["masters_retired"] += 1
            continue
        if current is None:
            plan.master_upserts.append((record, 1, now))
            counts["masters_inserted"] += 1
        elif current.record_hash != record.record_hash or current.status != record.status:
            plan.master_upserts.append((record, current.version + 1, current.created_at_utc))
            counts["masters_updated"] += 1
        else:
            counts["masters_unchanged"] += 1

    product_ids = set(desired.links) | set(existing_links)
    if scope_products is not None:
        product_ids &= scope_products
    for product_id in sorted(product_ids):
        wanted = desired.links.get(product_id)
        current = existing_links.get(product_id)
        if current is not None and current.link_method not in SYNC_MANAGED_METHODS:
            # manual_review: sólo lo cambia la herramienta de revisión.
            if wanted is None or wanted.link_hash != current.link_hash:
                counts["curated_links_left_untouched"] += 1
            else:
                counts["links_unchanged"] += 1
            continue
        if wanted is not None and wanted.link_method not in SYNC_MANAGED_METHODS:
            counts["curated_links_left_untouched"] += 1
            continue
        if wanted is None:
            if current is not None:
                plan.link_supersedes.append(int(current.link_id))  # type: ignore[arg-type]
                counts["links_superseded"] += 1
            continue
        if current is None:
            plan.link_inserts.append(replace(wanted, decided_at_utc=now))
            counts["links_inserted"] += 1
        elif current.link_hash != wanted.link_hash:
            plan.link_supersedes.append(int(current.link_id))  # type: ignore[arg-type]
            plan.link_inserts.append(replace(wanted, decided_at_utc=now))
            counts["links_replaced"] += 1
        else:
            counts["links_unchanged"] += 1
    plan.counts = dict(sorted(counts.items()))
    return plan


def _master_payload(record: GoldenRecord, version: int, created_at: str, now: str) -> dict[str, object]:
    return {
        "master_product_id": record.master_product_id,
        "primary_gtin": record.primary_gtin,
        "origin_method": record.origin_method,
        "brand": record.brand,
        "manufacturer": record.manufacturer,
        "display_name": record.display_name,
        "product_type": record.product_type,
        "category": record.category,
        "net_content_value": record.net_content_value,
        "net_content_unit": record.net_content_unit,
        "pack_count": record.pack_count,
        "variant": record.variant,
        "origin": record.origin,
        "attribute_provenance_json": _canonical_json(record.attribute_provenance),
        "human_attributes_json": _canonical_json(record.human_attributes),
        "status": record.status,
        "merged_into": record.merged_into,
        "record_hash": record.record_hash,
        "builder_version": record.builder_version,
        "created_at_utc": created_at,
        "updated_at_utc": now,
        "version": version,
    }


def _link_payload(link: MasterLink, now: str) -> dict[str, object]:
    return {
        "product_id": link.product_id,
        "supermarket_id": link.supermarket_id,
        "master_product_id": link.master_product_id,
        "link_method": link.link_method,
        "confidence": link.confidence,
        "evidence_json": _canonical_json(link.evidence),
        "decided_by": link.decided_by,
        "decided_at_utc": link.decided_at_utc or now,
        "status": "active",
        "status_changed_at_utc": None,
        "link_hash": link.link_hash,
    }


_INTEGER_MASTER_COLUMNS = {"pack_count", "version"}
MASTER_UPSERT_SQL = (
    f"INSERT INTO {MASTER_TABLE} ({','.join(MASTER_COLUMNS)}) SELECT "
    + ",".join(
        f"CAST(json_extract(value,'$.{column}') AS INTEGER)" if column in _INTEGER_MASTER_COLUMNS else f"json_extract(value,'$.{column}')"
        for column in MASTER_COLUMNS
    )
    + " FROM json_each(?) WHERE 1 ON CONFLICT(master_product_id) DO UPDATE SET "
    + ",".join(f"{column}=excluded.{column}" for column in MASTER_COLUMNS if column not in {"master_product_id", "created_at_utc"})
)
LINK_INSERT_COLUMNS = LINK_COLUMNS[1:]
LINK_INSERT_SQL = (
    f"INSERT INTO {LINK_TABLE} ({','.join(LINK_INSERT_COLUMNS)}) SELECT "
    + ",".join(
        "CAST(json_extract(value,'$.product_id') AS INTEGER)"
        if column == "product_id"
        else ("CAST(json_extract(value,'$.confidence') AS REAL)" if column == "confidence" else f"json_extract(value,'$.{column}')")
        for column in LINK_INSERT_COLUMNS
    )
    + " FROM json_each(?)"
)
LINK_SUPERSEDE_SQL = (
    f"UPDATE {LINK_TABLE} SET status='superseded', status_changed_at_utc=? "
    "WHERE status='active' AND link_id IN (SELECT CAST(value AS INTEGER) FROM json_each(?))"
)
MASTER_RETIRE_SQL = (
    f"UPDATE {MASTER_TABLE} SET status='retired', updated_at_utc=?, version=version+1 "
    "WHERE status='active' AND master_product_id IN (SELECT value FROM json_each(?))"
)
STATE_UPSERT_SQL = (
    f"INSERT INTO {STATE_TABLE} ({','.join(STATE_COLUMNS)}) VALUES (1,?,?,?,?,?,?,?,?) "
    "ON CONFLICT(state_id) DO UPDATE SET builder_version=excluded.builder_version,"
    "normalization_version=excluded.normalization_version,policy_digest=excluded.policy_digest,"
    "profile_digest=excluded.profile_digest,state_digest=excluded.state_digest,masters=excluded.masters,"
    "active_links=excluded.active_links,synced_at_utc=excluded.synced_at_utc"
)
WRITE_CHUNK = 1000


def plan_write_batches(plan: MasterPlan, *, now: str) -> list[list[tuple[str, str, tuple[object, ...]]]]:
    """Lotes atómicos en orden seguro: maestros → reemplazos → altas → retiros."""

    batches: list[list[tuple[str, str, tuple[object, ...]]]] = []

    def batch(name: str, sql: str, args: tuple[object, ...]) -> None:
        batches.append([("begin", "BEGIN IMMEDIATE", ()), (name, sql, args), ("commit", "COMMIT", ())])

    for index, chunk in enumerate(_chunked(plan.master_upserts, WRITE_CHUNK)):
        payload = [_master_payload(record, version, created, now) for record, version, created in chunk]  # type: ignore[misc]
        batch(f"master_upsert_{index}", MASTER_UPSERT_SQL, (_canonical_json(payload),))
    for index, chunk in enumerate(_chunked(sorted(plan.link_supersedes), WRITE_CHUNK)):
        batch(f"link_supersede_{index}", LINK_SUPERSEDE_SQL, (now, _canonical_json(list(chunk))))
    for index, chunk in enumerate(_chunked(plan.link_inserts, WRITE_CHUNK)):
        payload = [_link_payload(link, now) for link in chunk]  # type: ignore[arg-type]
        batch(f"link_insert_{index}", LINK_INSERT_SQL, (_canonical_json(payload),))
    retire_ids = sorted(master_id for master_id, _ in plan.master_retirements)
    for index, chunk in enumerate(_chunked(retire_ids, WRITE_CHUNK)):
        batch(f"master_retire_{index}", MASTER_RETIRE_SQL, (now, _canonical_json(list(chunk))))
    return batches


# --------------------------------------------------------------------------
# Sincronización (refresco diario)
# --------------------------------------------------------------------------


def sync_product_master(
    store: MasterStore,
    members: Sequence[MemberProfile],
    *,
    policy: MasterPolicy,
    normalization_version: str,
    now: str,
    prior_profile_digest: str,
    new_profile_digest: str,
    changed_product_ids: Iterable[int] | None,
    reviewed_links: Sequence[MasterLink] = (),
    engine_links: Sequence[MasterLink] = (),
    apply: bool = True,
    full_threshold: float = 0.25,
) -> dict[str, object]:
    """Escribe maestros + vínculos de forma incremental y con lecturas acotadas.

    Lecturas por corrida sin cambios: ``sqlite_master`` + 1 fila de estado +
    vínculos curados + maestros con atributos humanos + marcas *dirty*. Con
    cambios: además, los vínculos/maestros de los productos cambiados (por
    índice). Sólo la primera corrida o un cambio de versión/política leen el
    estado maestro completo.
    """

    if not policy.enabled:
        return {"enabled": False, "mode": "disabled"}
    reads_before = getattr(store, "rows_read", None)
    present = existing_schema_objects(store)
    schema_ready = set(SCHEMA_OBJECTS) <= present
    schema: dict[str, object] = {"created": [], "already_applied": schema_ready}
    if apply and not schema_ready:
        schema = ensure_master_schema(store)
        schema_ready = True
    tables_exist = schema_ready or set(MASTER_TABLES) <= present

    state = read_sync_state(store) if tables_exist else None
    # ``engine_auto`` no se lee del store: se recalcula en cada corrida desde los
    # perfiles (``engine_links``) y el diff borra los que dejaron de cumplir.
    curated_existing = read_curated_links(store, {"manual_review"}) if tables_exist else ()
    engine_existing = read_curated_links(store, {"engine_auto"}) if tables_exist else ()
    human = read_human_attributes(store) if tables_exist else {}
    dirty = read_dirty_masters(store) if tables_exist else set()
    curated_targets = {link.master_product_id for link in (*curated_existing, *reviewed_links, *engine_links)}
    target_meta: dict[str, tuple[str, str | None]] = {}
    if curated_targets and tables_exist:
        for master_id, master in read_masters_by_id(store, curated_targets).items():
            target_meta[master_id] = (master.origin_method, master.primary_gtin)
    for link in (*reviewed_links, *engine_links):
        if link.master_product_id not in target_meta:
            gtin = link.evidence.get("primary_gtin") if isinstance(link.evidence, Mapping) else None
            target_meta[link.master_product_id] = ("gtin", str(gtin)) if gtin else ("reviewed_decision", None)

    # Un vínculo del registro no reemplaza uno manual del mismo producto.
    manual_products = {link.product_id for link in curated_existing}
    curated = list(curated_existing) + [link for link in reviewed_links if link.product_id not in manual_products]
    # Un vínculo del motor nunca reemplaza uno manual ni del registro revisado.
    decided = {link.product_id for link in curated}
    curated += [link for link in engine_links if link.product_id not in decided]
    desired = build_desired_state(
        members,
        policy=policy,
        curated_links=curated,
        human_attributes=human,
        existing_master_meta=target_meta,
    )
    desired_digest = desired.digest()
    changed = None if changed_product_ids is None else set(int(value) for value in changed_product_ids)

    if state is None:
        mode, reason = "full", "no_sync_state"
    elif state.builder_version != MASTER_BUILDER_VERSION:
        mode, reason = "full", "builder_version_changed"
    elif state.normalization_version != normalization_version:
        mode, reason = "full", "normalization_version_changed"
    elif state.policy_digest != policy.digest():
        mode, reason = "full", "policy_changed"
    elif state.state_digest == desired_digest and not dirty:
        mode, reason = "noop", "state_digest_unchanged"
    elif state.profile_digest != prior_profile_digest or changed is None:
        mode, reason = "full", "profiles_out_of_sync_with_master"
    elif len(changed) > full_threshold * max(1, len(members)):
        mode, reason = "full", "large_change_set"
    else:
        mode, reason = "incremental", "profile_changes"

    plan = MasterPlan()
    scope_counts: dict[str, int] = {}
    if mode == "full":
        existing_masters = read_all_masters(store) if tables_exist else {}
        existing_links = read_all_active_links(store) if tables_exist else {}
        plan = diff_master_state(desired, existing_masters, existing_links, now=now)
    elif mode == "incremental":
        assert changed is not None
        scope_products = set(changed) | {link.product_id for link in curated} | {
            link.product_id for link in (*curated_existing, *engine_existing)
        }
        current_links = read_active_links_by_product(store, scope_products)
        scope_masters = set(dirty) | set(human)
        for product_id in scope_products:
            wanted = desired.master_of_product(product_id)
            if wanted is not None:
                scope_masters.add(wanted)
            if product_id in current_links:
                scope_masters.add(current_links[product_id].master_product_id)
        member_by_id = {member.product_id: member for member in members}
        for product_id in changed:
            member = member_by_id.get(product_id)
            if member is not None and member.canonical_gtin is not None:
                scope_masters.add(gtin_master_id(member.canonical_gtin))
        # Miembros de los maestros afectados: sus vínculos pueden cambiar por
        # colisiones aunque su propio perfil no cambie.
        master_links = read_active_links_by_master(store, scope_masters)
        current_links.update(master_links)
        scope_products |= set(master_links)
        for master_id in scope_masters:
            scope_products |= set(desired.gtin_members.get(master_id, ()))
        scope_products |= {pid for pid, link in desired.links.items() if link.master_product_id in scope_masters}
        missing = scope_products - set(current_links)
        if missing:
            current_links.update(read_active_links_by_product(store, missing))
        existing_masters = read_masters_by_id(store, scope_masters)
        plan = diff_master_state(
            desired,
            existing_masters,
            current_links,
            now=now,
            scope_masters=scope_masters,
            scope_products=scope_products,
        )
        scope_counts = {"scope_masters": len(scope_masters), "scope_products": len(scope_products)}

    written = False
    if apply and mode != "noop":
        for steps in plan_write_batches(plan, now=now):
            store.write(steps)
        final_steps: list[tuple[str, str, tuple[object, ...]]] = [("begin", "BEGIN IMMEDIATE", ())]
        final_steps.append(
            (
                "state_upsert",
                STATE_UPSERT_SQL,
                (
                    MASTER_BUILDER_VERSION,
                    normalization_version,
                    policy.digest(),
                    new_profile_digest,
                    desired_digest,
                    len(desired.masters),
                    len(desired.links),
                    now,
                ),
            )
        )
        if dirty:
            final_steps.append(
                (
                    "clear_dirty",
                    f"DELETE FROM {DIRTY_TABLE} WHERE master_product_id IN (SELECT value FROM json_each(?))",
                    (_canonical_json(sorted(dirty)),),
                )
            )
        final_steps.append(("commit", "COMMIT", ()))
        store.write(final_steps)
        written = True
    elif apply and mode == "noop" and state is not None and state.profile_digest != new_profile_digest:
        # Perfiles cambiaron sin efecto en el maestro: avanzar la huella de perfiles.
        store.write(
            [
                ("begin", "BEGIN IMMEDIATE", ()),
                (
                    "state_profile_digest",
                    f"UPDATE {STATE_TABLE} SET profile_digest=?, synced_at_utc=? WHERE state_id=1",
                    (new_profile_digest, now),
                ),
                ("commit", "COMMIT", ()),
            ]
        )
        written = True
    result: dict[str, object] = {
        "enabled": True,
        "builder_version": MASTER_BUILDER_VERSION,
        "mode": mode,
        "mode_reason": reason,
        "dry_run": not apply,
        "schema": schema,
        "state_written": written,
        "masters": len(desired.masters),
        "active_links": len(desired.links),
        "curated_links": len(curated),
        "diagnostics": desired.diagnostics,
        "plan": plan.counts,
        **scope_counts,
    }
    if reads_before is not None:
        result["rows_read"] = int(getattr(store, "rows_read")) - int(reads_before)
    return result


# --------------------------------------------------------------------------
# Superposición para exportadores
# --------------------------------------------------------------------------


def curated_link_overrides(store: MasterStore, policy: MasterPolicy) -> dict[str, str]:
    """``supermarket:product_id`` → id público del maestro para vínculos curados servibles.

    Lee sólo ``sqlite_master`` y los vínculos curados activos por índice; con
    sólo vínculos GTIN (o sin tablas) devuelve ``{}`` y los exportadores
    producen exactamente la salida vigente.
    """

    methods = tuple(sorted(policy.serving_methods)) if policy.enabled else ()
    if not methods:
        return {}
    present = {
        str(row[0])
        for row in store.query(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?,?)", (MASTER_TABLE, LINK_TABLE)
        )
    }
    if present != {MASTER_TABLE, LINK_TABLE}:
        return {}
    placeholders = ",".join("?" for _ in methods)
    rows = store.query(
        f"SELECT l.product_id,l.supermarket_id,l.master_product_id,m.primary_gtin FROM {LINK_TABLE} AS l "
        f"JOIN {MASTER_TABLE} AS m ON m.master_product_id=l.master_product_id "
        f"WHERE l.link_method IN ({placeholders}) AND l.status='active' AND m.status='active' "
        "ORDER BY l.product_id",
        methods,
    )
    result: dict[str, str] = {}
    for product_id, supermarket_id, master_id, primary_gtin in rows:
        if type(product_id) is not int or not isinstance(supermarket_id, str) or not is_master_id(master_id):
            raise ProductMasterError("master_curated_override_row_invalid")
        result[source_product_key(supermarket_id, product_id)] = public_canonical_id(
            str(master_id), None if primary_gtin is None else str(primary_gtin)
        )
    return result


__all__ = [
    "CURATED_LINK_METHODS",
    "DesiredState",
    "GoldenRecord",
    "LINK_METHODS",
    "MASTER_BUILDER_VERSION",
    "MasterLink",
    "MasterPlan",
    "MasterPolicy",
    "MemberProfile",
    "ProductMasterError",
    "SQLiteMasterStore",
    "build_desired_state",
    "build_golden_record",
    "curated_link_overrides",
    "diff_master_state",
    "ensure_master_schema",
    "gtin_master_id",
    "load_master_policy",
    "public_canonical_id",
    "seeded_master_id",
    "sync_product_master",
]


# --------------------------------------------------------------------------
# Registro de decisiones revisadas → vínculos ``reviewed_decision``
# --------------------------------------------------------------------------


def master_for_decision(decision_master_product_id: str) -> tuple[str, str | None]:
    """``prod_gtin_<14>`` → maestro GTIN; ``prod_verified_*`` → maestro sembrado."""

    if decision_master_product_id.startswith("prod_gtin_"):
        gtin = canonicalize_gtin(decision_master_product_id.removeprefix("prod_gtin_"))
        if gtin is None:
            raise ProductMasterError("reviewed_decision_master_gtin_invalid")
        return gtin_master_id(gtin), gtin
    return seeded_master_id("verified", decision_master_product_id), None


def reviewed_decision_links(
    decisions: Sequence[object],
    records: Sequence[tuple[int, object]],
    *,
    policy: MasterPolicy,
) -> tuple[tuple[MasterLink, ...], dict[str, int]]:
    """Convierte decisiones aprobadas y vigentes del registro en vínculos.

    Sólo aplica decisiones cuyas huellas siguen coincidiendo con la evidencia
    fuente actual (``assess_product_relation``); las obsoletas, pendientes o
    retiradas se reportan y no crean vínculo. Con el registro vacío no hace
    trabajo (ni construye el léxico de marcas).
    """

    from .product_identity_decisions import IDENTITY_RELATIONS, assess_product_relation
    from .product_identity_v2 import build_brand_lexicon, profile_product_v2

    diagnostics: Counter[str] = Counter()
    approved = [
        decision
        for decision in decisions
        if getattr(decision, "status") == "approved" and getattr(decision, "relation") in IDENTITY_RELATIONS
    ]
    if not approved or not policy.method_active("reviewed_decision"):
        return (), {}
    lexicon = build_brand_lexicon(record for _, record in records)  # type: ignore[misc]
    by_source = {getattr(record, "source_record_id"): (product_id, record) for product_id, record in records}
    links: dict[int, MasterLink] = {}
    conflicted: set[int] = set()
    for decision in sorted(approved, key=lambda item: getattr(item, "candidate_id")):
        left = by_source.get(getattr(decision, "left_source_record_id"))
        right = by_source.get(getattr(decision, "right_source_record_id"))
        if left is None or right is None:
            diagnostics["reviewed_decision_source_missing"] += 1
            continue
        left_profile = profile_product_v2(left[1], brand_lexicon=lexicon)  # type: ignore[arg-type]
        right_profile = profile_product_v2(right[1], brand_lexicon=lexicon)  # type: ignore[arg-type]
        try:
            assessment = assess_product_relation(left_profile, right_profile, decision)  # type: ignore[arg-type]
        except ValueError:
            diagnostics["reviewed_decision_invalid"] += 1
            continue
        if assessment.decision_state != "approved" or assessment.master_product_id is None:
            diagnostics[f"reviewed_decision_{assessment.decision_state}"] += 1
            continue
        master_id, primary_gtin = master_for_decision(assessment.master_product_id)
        for product_id, record in (left, right):
            link = MasterLink(
                product_id=int(product_id),
                supermarket_id=str(getattr(record, "supermarket_id")),
                master_product_id=master_id,
                link_method="reviewed_decision",
                decided_by="human:" + str(getattr(decision, "reviewed_by")).strip(),
                evidence={
                    "candidate_id": getattr(decision, "candidate_id"),
                    "relation": assessment.relation,
                    "evidence_codes": sorted(assessment.evidence_codes),
                    "decision_master_product_id": assessment.master_product_id,
                    "primary_gtin": primary_gtin,
                    "reviewed_at_utc": getattr(decision, "reviewed_at_utc"),
                },
            )
            previous = links.get(link.product_id)
            if previous is not None and previous.master_product_id != master_id:
                conflicted.add(link.product_id)
                continue
            links.setdefault(link.product_id, link)
        diagnostics["reviewed_decision_applied"] += 1
    for product_id in conflicted:
        links.pop(product_id, None)
        diagnostics["reviewed_decision_membership_conflict"] += 1
    return tuple(link for _, link in sorted(links.items())), dict(sorted(diagnostics.items()))
