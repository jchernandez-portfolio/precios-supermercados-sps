"""Taxonomía derivada de la ruta de categoría de cada supermercado.

El 69 % de las filas publicadas no tiene ``product_type`` porque la taxonomía
v2 sólo mira el nombre. Las rutas de categoría fuente (Walmart/Paiz traen tres
niveles; Colonial y PriceSmart un departamento) permiten:

1. asignar un departamento grueso (bloqueo y evidencia débil);
2. asignar tipo de producto por reglas de palabras clave sobre la ruta;
3. propagar el tipo dominante de una hoja a los productos sin tipo de esa hoja.

Nada de esto confirma identidad: sólo mejora el blocking y el vector de
comparación.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

import yaml

from ..product_homologation import fold_text

TAXONOMY_SCHEMA = "precios-sps-source-category-taxonomy/v1"

# Departamentos con fronteras difusas entre supermercados (p. ej. café está en
# Abarrotes en Walmart y en Bebidas en la taxonomía por nombre).
_DEPARTMENT_GROUPS = (
    frozenset({"Alimentos", "Bebidas"}),
    frozenset({"Limpieza", "Hogar"}),
    frozenset({"Cuidado personal", "Salud", "Bebés"}),
    frozenset({"Mascotas"}),
    frozenset({"General"}),
)


class SourceTaxonomyError(ValueError):
    """El mapa de taxonomía fuente no es válido."""


def department_group(department: str | None) -> int | None:
    if department is None:
        return None
    for index, group in enumerate(_DEPARTMENT_GROUPS):
        if department in group:
            return index
    return None


def departments_compatible(left: str | None, right: str | None) -> bool | None:
    """``None`` si falta alguno; si no, si pertenecen al mismo grupo."""

    left_group, right_group = department_group(left), department_group(right)
    if left_group is None or right_group is None:
        return None
    return left_group == right_group


def category_segments(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    parts = re.split(r"[/>|]+", value)
    return tuple(segment for part in parts if (segment := fold_text(part)))


def category_key(value: str | None) -> str | None:
    segments = category_segments(value)
    return "/".join(segments) if segments else None


@dataclass(frozen=True)
class LeafTypeMapping:
    category_key: str
    product_type: str
    support: int
    agreement: float


@dataclass(frozen=True)
class SourceTaxonomy:
    departments: Mapping[str, str]
    keyword_rules: tuple[tuple[str, str, str], ...]
    leaf_min_support: int = 5
    leaf_min_agreement: float = 0.8

    def department_for(self, category: str | None) -> str | None:
        segments = category_segments(category)
        for segment in segments:
            department = self.departments.get(segment)
            if department is not None:
                return department
        return None

    def type_for(self, category: str | None) -> str | None:
        segments = category_segments(category)
        if not segments:
            return None
        department = self.department_for(category)
        for segment in reversed(segments):
            for keyword, product_type, rule_department in self.keyword_rules:
                if re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", segment) is None:
                    continue
                if department is not None and departments_compatible(department, rule_department) is False:
                    continue
                return product_type
        return None

    def learn_leaf_types(
        self,
        observations: Iterable[tuple[str | None, str | None]],
    ) -> dict[str, LeafTypeMapping]:
        """Aprende hoja → tipo dominante a partir de tipos derivados del nombre."""

        counts: dict[str, Counter[str]] = defaultdict(Counter)
        for category, product_type in observations:
            key = category_key(category)
            if key is None or product_type is None:
                continue
            # Sólo rutas con al menos dos niveles: un departamento solo es
            # demasiado amplio para propagar un tipo.
            if key.count("/") < 1:
                continue
            counts[key][product_type] += 1
        result: dict[str, LeafTypeMapping] = {}
        for key, counter in sorted(counts.items()):
            total = sum(counter.values())
            product_type, top = counter.most_common(1)[0]
            agreement = top / total
            if total >= self.leaf_min_support and agreement >= self.leaf_min_agreement:
                result[key] = LeafTypeMapping(key, product_type, total, round(agreement, 4))
        return result


def load_source_taxonomy(path: Path) -> SourceTaxonomy:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SourceTaxonomyError("taxonomy_unreadable") from exc
    if not isinstance(raw, dict) or raw.get("schema") != TAXONOMY_SCHEMA:
        raise SourceTaxonomyError("taxonomy_schema_invalid")
    departments_raw = raw.get("departments")
    rules_raw = raw.get("keyword_rules")
    if not isinstance(departments_raw, dict) or not isinstance(rules_raw, list):
        raise SourceTaxonomyError("taxonomy_shape_invalid")
    departments: dict[str, str] = {}
    for key, value in departments_raw.items():
        folded = fold_text(str(key))
        if folded is None or department_group(str(value)) is None:
            raise SourceTaxonomyError(f"taxonomy_department_invalid:{key}")
        departments[folded] = str(value)
    rules: list[tuple[str, str, str]] = []
    for rule in rules_raw:
        if not isinstance(rule, list) or len(rule) != 3:
            raise SourceTaxonomyError("taxonomy_rule_shape_invalid")
        keyword = fold_text(str(rule[0]))
        if keyword is None or department_group(str(rule[2])) is None:
            raise SourceTaxonomyError(f"taxonomy_rule_invalid:{rule}")
        rules.append((keyword, str(rule[1]), str(rule[2])))
    propagation = raw.get("leaf_propagation") or {}
    return SourceTaxonomy(
        departments=departments,
        keyword_rules=tuple(rules),
        leaf_min_support=int(propagation.get("min_support", 5)),
        leaf_min_agreement=float(propagation.get("min_agreement", 0.8)),
    )
