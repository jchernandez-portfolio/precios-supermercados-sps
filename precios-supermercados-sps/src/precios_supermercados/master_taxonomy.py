"""Árbol maestro de categorías v1 y tabla de equivalencias por supermercado.

Una sola clasificación pública para todos los supermercados:
Departamento > Categoría > Subcategoría > Tipo de producto, con la estructura de
GS1 GPC (Segmento > Familia > Clase > Bloque). Ver
``docs/homologation/master-category-tree-v1.md``.

Orden de asignación (de mayor a menor autoridad):

1. Decisión manual (reservado; hoy no hay decisiones registradas).
2. Mismo producto en otro supermercado (grupo comparable por código): el grupo
   toma el nodo más específico y, en empate, el más votado.
3. Tabla de equivalencias de la categoría que publica el supermercado.
4. Tipo de producto por nombre (motor de homologación), siempre dentro de un
   departamento compatible con la equivalencia.
4b. Subcategoría por palabras clave del nombre (``name-subcategory-rules-v1.csv``)
   cuando lo anterior no llega a subcategoría; sólo dentro de la rama que ya
   dieron la equivalencia o el tipo, y nunca reemplaza un nodo más específico.
5. Sin evidencia: queda sin categoría (lista de revisión). Nunca se inventa.

Este módulo sólo decide la taxonomía **pública**; no toca identidad ni
comparabilidad (el motor de homologación conserva su taxonomía interna).
"""
from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TREE_PATH = PROJECT_ROOT / "config" / "homologation" / "master-category-tree-v1.json"
CROSSWALK_PATH = PROJECT_ROOT / "config" / "homologation" / "source-category-crosswalk-v1.csv"
NAME_RULES_PATH = PROJECT_ROOT / "config" / "homologation" / "name-subcategory-rules-v1.csv"
TREE_SCHEMA = "precios-sps-master-category-tree/v1"
LEVELS = ("department", "category", "subcategory")
CROSSWALK_LEVELS = frozenset({"subcategory", "category", "department", "by_name", "excluded"})
OUNCE_BASES = frozenset({"mass", "fluid"})
UNIT_REFERENCES = frozenset({"per_100"})

# Departamentos de la taxonomía interna previa (v2.x) -> departamento del árbol.
# Sólo se usa como último recurso cuando no hay equivalencia ni tipo.
LEGACY_DEPARTMENTS = {
    "Alimentos": "Alimentos",
    "Bebidas": "Bebidas y tabaco",
    "Cuidado personal": "Cuidado personal y belleza",
    "Limpieza": "Limpieza",
    "Bebés": "Bebé",
    "Mascotas": "Mascotas",
    "Salud": "Salud y farmacia",
}


class MasterTaxonomyError(ValueError):
    pass


def fold_key(value: object) -> str:
    text = unicodedata.normalize("NFKD", value if isinstance(value, str) else "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


@dataclass(frozen=True, slots=True)
class Node:
    department: str | None
    category: str | None = None
    subcategory: str | None = None
    product_type: str | None = None

    @property
    def depth(self) -> int:
        return sum(value is not None for value in (self.department, self.category, self.subcategory, self.product_type))

    def contains(self, other: "Node") -> bool:
        """``other`` está dentro de este nodo (mismo camino hasta su profundidad)."""
        for mine, theirs in ((self.department, other.department), (self.category, other.category),
                             (self.subcategory, other.subcategory)):
            if mine is None:
                return True
            if mine != theirs:
                return False
        return True


@dataclass(frozen=True, slots=True)
class CrosswalkEntry:
    level: str
    node: Node | None  # None para by_name / excluded


@dataclass(frozen=True, slots=True)
class Taxonomy:
    tree: dict
    paths: frozenset[tuple[str, str, str]]
    categories: frozenset[tuple[str, str]]
    departments: frozenset[str]
    type_nodes: dict[str, Node]
    crosswalk: dict[tuple[str, str], CrosswalkEntry]
    excluded_label: str
    # Atributos por subcategoría: {"ounce": "mass"|"fluid", "unit_reference": "per_100"}.
    attributes: dict[tuple[str, str, str], dict[str, str]] = field(default_factory=dict)


def _load_tree(path: Path) -> dict:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema") != TREE_SCHEMA:
        raise MasterTaxonomyError("master_tree_schema_invalid")
    return document


@lru_cache(maxsize=1)
def load(tree_path: Path = TREE_PATH, crosswalk_path: Path = CROSSWALK_PATH) -> Taxonomy:
    tree = _load_tree(tree_path)
    paths: set[tuple[str, str, str]] = set()
    type_nodes: dict[str, Node] = {}
    attributes: dict[tuple[str, str, str], dict[str, str]] = {}
    for department in tree["departments"]:
        for category in department["categories"]:
            for subcategory in category["subcategories"]:
                path = (department["name"], category["name"], subcategory["name"])
                if path in paths:
                    raise MasterTaxonomyError("master_tree_duplicate_node")
                paths.add(path)
                attrs = {key: subcategory[key] for key in ("ounce", "unit_reference") if key in subcategory}
                if attrs.get("ounce", "mass") not in OUNCE_BASES or attrs.get("unit_reference", "per_100") not in UNIT_REFERENCES:
                    raise MasterTaxonomyError(f"master_tree_attribute_invalid:{path}")
                attributes[path] = attrs
                for product_type in subcategory.get("product_types", []):
                    if product_type in type_nodes:
                        raise MasterTaxonomyError(f"master_tree_duplicate_product_type:{product_type}")
                    type_nodes[product_type] = Node(*path, product_type)
    categories = frozenset((d, c) for d, c, _ in paths)
    departments = frozenset(d for d, _, _ in paths)
    crosswalk: dict[tuple[str, str], CrosswalkEntry] = {}
    with crosswalk_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            level = row["level"]
            if level not in CROSSWALK_LEVELS:
                raise MasterTaxonomyError("crosswalk_level_invalid")
            node = None
            if level in ("subcategory", "category", "department"):
                node = Node(row["department"] or None, row["category"] or None, row["subcategory"] or None)
                if (level == "subcategory" and (node.department, node.category, node.subcategory) not in paths) or \
                        (level == "category" and ((node.department, node.category) not in categories or node.subcategory)) or \
                        (level == "department" and (node.department not in departments or node.category or node.subcategory)):
                    raise MasterTaxonomyError(f"crosswalk_node_invalid:{row['supermarket_id']}:{row['source_category']}")
            key = (row["supermarket_id"], fold_key(row["source_category"]))
            if key in crosswalk:
                raise MasterTaxonomyError(f"crosswalk_duplicate:{key}")
            crosswalk[key] = CrosswalkEntry(level, node)
    return Taxonomy(tree, frozenset(paths), categories, departments, type_nodes, crosswalk,
                    str(tree.get("excluded_label") or "Fuera del catálogo"), attributes)


@dataclass(frozen=True, slots=True)
class NameRule:
    node: Node
    keywords: tuple[str, ...]
    excludes: tuple[str, ...]


def _phrase(value: str) -> str:
    return f" {fold_key(value)} "


def name_text(product_name: object) -> str:
    """Nombre normalizado para reglas: separa palabras pegadas ("JugoNaranja")."""
    raw = product_name if isinstance(product_name, str) else ""
    raw = re.sub(r"(?<=[a-záéíóúñ])(?=[A-ZÁÉÍÓÚÑ])", " ", raw.replace("&", " & "))
    return f" {fold_key(raw)} "


@lru_cache(maxsize=1)
def load_name_rules(path: Path = NAME_RULES_PATH, tree_path: Path = TREE_PATH) -> tuple[NameRule, ...]:
    """Reglas de subcategoría por nombre, en orden (la primera compatible gana)."""
    paths = load(tree_path).paths
    rules: list[NameRule] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            node = Node(row["department"], row["category"], row["subcategory"])
            if (node.department, node.category, node.subcategory) not in paths:
                raise MasterTaxonomyError(f"name_rule_node_invalid:{row['subcategory']}")
            keywords = tuple(_phrase(item) for item in row["keywords"].split("|") if item.strip())
            excludes = tuple(_phrase(item) for item in (row.get("excludes") or "").split("|") if item.strip())
            if not keywords:
                raise MasterTaxonomyError(f"name_rule_without_keywords:{row['subcategory']}")
            rules.append(NameRule(node, keywords, excludes))
    return tuple(rules)


def name_subcategory(product_name: object, within: Node | None, rules: Iterable[NameRule] | None = None) -> Node | None:
    """Subcategoría por palabras clave del nombre dentro de ``within`` (o sin rama)."""
    text = name_text(product_name)
    if not text.strip():
        return None
    for rule in load_name_rules() if rules is None else rules:
        if within is not None and not within.contains(rule.node):
            continue
        if any(keyword in text for keyword in rule.keywords) and not any(item in text for item in rule.excludes):
            return rule.node
    return None


@dataclass(frozen=True, slots=True)
class Assignment:
    node: Node | None
    source: str  # crosswalk+type | crosswalk | type | legacy | excluded | unassigned (+name / name)
    crosswalk_level: str | None  # None = categoría del súper sin equivalencia


def assign_offer(
    supermarket_id: str,
    source_category: str | None,
    product_type: str | None,
    identity_category: str | None = None,
    taxonomy: Taxonomy | None = None,
    product_name: str | None = None,
) -> Assignment:
    """Nodo público de una oferta individual (pasos 3 a 5).

    Con ``product_name`` aplica además el paso 4b (subcategoría por nombre) a
    las asignaciones que no llegaron a subcategoría.
    """
    assignment = _assign_offer(supermarket_id, source_category, product_type, identity_category, taxonomy)
    if product_name is None or assignment.source == "excluded":
        return assignment
    if assignment.node is not None and assignment.node.subcategory is not None:
        return assignment
    refined = name_subcategory(product_name, assignment.node)
    if refined is None:
        return assignment
    source = "name" if assignment.node is None else f"{assignment.source}+name"
    return Assignment(refined, source, assignment.crosswalk_level)


def _assign_offer(
    supermarket_id: str,
    source_category: str | None,
    product_type: str | None,
    identity_category: str | None = None,
    taxonomy: Taxonomy | None = None,
) -> Assignment:
    tax = taxonomy or load()
    entry = tax.crosswalk.get((supermarket_id, fold_key(source_category))) if source_category else None
    level = entry.level if entry else None
    if entry and entry.level == "excluded":
        return Assignment(None, "excluded", level)
    type_node = tax.type_nodes.get(product_type) if product_type else None
    cross = entry.node if entry else None
    if cross is not None:
        if type_node is not None and cross.contains(type_node):
            return Assignment(type_node, "crosswalk+type", level)
        return Assignment(cross, "crosswalk", level)
    if type_node is not None:
        return Assignment(type_node, "type", level)
    legacy = LEGACY_DEPARTMENTS.get(identity_category or "")
    if legacy is not None:
        return Assignment(Node(legacy), "legacy", level)
    return Assignment(None, "unassigned", level)


def assign_group(assignments: Iterable[Assignment]) -> Assignment:
    """Paso 2: un grupo comparable comparte el nodo más específico y más votado.

    Gana el nodo más profundo; en empate, el que tiene más votos compatibles
    (ofertas en su mismo camino), luego más votos exactos y, al final, orden
    alfabético del camino para que el resultado sea determinístico.
    """
    items = list(assignments)
    candidates = [a for a in items if a.node is not None]
    if not candidates:
        if items and all(a.source == "excluded" for a in items):
            return items[0]
        return next((a for a in items if a.source != "excluded"), items[0]) if items else Assignment(None, "unassigned", None)
    votes = Counter(a.node for a in candidates)

    def rank(node: Node) -> tuple:
        compatible = sum(count for other, count in votes.items() if node.contains(other) or other.contains(node))
        path = "|".join(value or "" for value in (node.department, node.category, node.subcategory, node.product_type))
        return (-node.depth, -compatible, -votes[node], path)

    best = min(votes, key=rank)
    return next(a for a in candidates if a.node == best)


def public_fields(node: Node | None) -> tuple[str | None, str | None]:
    """(category, product_type) públicos: departamento y segundo nivel navegable.

    El segundo nivel es el tipo de producto cuando existe y, si no, la
    subcategoría o la categoría del árbol; así toda fila con departamento tiene
    un filtro navegable sin inventar tipos.
    """
    if node is None or node.department is None:
        return None, None
    return node.department, node.product_type or node.subcategory or node.category


def unmapped_source_categories(pairs: Iterable[tuple[str, str | None]], taxonomy: Taxonomy | None = None) -> list[tuple[str, str]]:
    """Categorías del súper sin fila en la tabla de equivalencias (aviso de gobierno)."""
    tax = taxonomy or load()
    missing = {(sm, cat) for sm, cat in pairs if cat and (sm, fold_key(cat)) not in tax.crosswalk}
    return sorted(missing)


def node_attribute(node: Node | None, key: str, taxonomy: Taxonomy | None = None) -> str | None:
    """Atributo de subcategoría (``ounce``/``unit_reference``) de un nodo.

    Un nodo a nivel categoría o departamento sólo hereda el atributo si TODAS
    sus subcategorías lo comparten (si no, es ambiguo y se devuelve ``None``).
    """
    if node is None or node.department is None:
        return None
    tax = taxonomy or load()
    if node.subcategory is not None:
        return tax.attributes.get((node.department, node.category, node.subcategory), {}).get(key)
    values = {
        attrs.get(key)
        for path, attrs in tax.attributes.items()
        if path[0] == node.department and (node.category is None or path[1] == node.category)
    }
    return values.pop() if len(values) == 1 else None


def ounce_basis(
    supermarket_id: str,
    source_category: str | None,
    product_type: str | None,
    taxonomy: Taxonomy | None = None,
) -> str | None:
    """"mass" (oz de peso), "fluid" (oz líquida) o ``None`` (no convertir)."""
    assignment = assign_offer(supermarket_id, source_category, product_type, taxonomy=taxonomy)
    return node_attribute(assignment.node, "ounce", taxonomy)


_UNIT_REFERENCE = {
    ("mass_g", None): (1000, "kg"),
    ("mass_g", "per_100"): (100, "100 g"),
    ("volume_ml", None): (1000, "L"),
    ("volume_ml", "per_100"): (100, "100 ml"),
    ("count", None): (1, "unidad"),
    ("count", "per_100"): (1, "unidad"),
}


def unit_reference(node: Node | None, dimension: str | None, taxonomy: Taxonomy | None = None) -> tuple[int, str] | None:
    """(cantidad base, etiqueta) del precio unitario público: fija por subcategoría."""
    if dimension is None:
        return None
    return _UNIT_REFERENCE.get((dimension, node_attribute(node, "unit_reference", taxonomy)))
