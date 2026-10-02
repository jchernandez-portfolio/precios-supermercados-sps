"""PriceSmart HN: especificaciones públicas de la página de detalle de producto.

PriceSmart no publica GTIN en su API de búsqueda ni en la ficha visible. El
"Número de ítem" (p. ej. ``415586``) es el número interno del club y nunca se
interpreta como GTIN. Esta capa extrae atributos descriptivos (peso/volumen neto,
peso unitario, conteo, marca, origen, alérgenos...) para que los productos sin
código de barras puedan emparejarse por atributos en la homologación.

Estrategia del parser (fail-closed por página):

1. JSON embebido: JSON-LD ``Product``/``BreadcrumbList`` y ``__NEXT_DATA__`` u
   otros ``<script type="application/json">``. En el JSON genérico sólo se
   aceptan pares etiqueta/valor dentro del subárbol que identifica al mismo
   ``pid`` de la página y cuya etiqueta es una especificación conocida.
2. DOM: se localiza el encabezado "Especificaciones" y se leen pares
   etiqueta/valor estructurados (``dl/dt/dd``, filas de tabla, filas de dos
   hijos) y, si no hay estructura reconocible, la secuencia de textos hoja.
3. Fusión por atributo: si JSON y DOM traen el mismo atributo con valores
   normalizados distintos, el atributo queda ``None`` con un conflicto
   registrado; nunca se elige un valor por preferencia.

Una página sin identidad verificable o sin bloque de especificaciones produce
``specs=None``; jamás se inventan valores. Los valores crudos se conservan junto
con la normalización (gramos y mililitros canónicos).

El parser se escribió contra un fixture sintético que reproduce la estructura
visible observada el 2026-10-01; la primera corrida real guarda HTML crudo de una
muestra para endurecerlo (ver ``docs/supermercados/pricesmart-especificaciones.md``).
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any, Iterator

from ..identifiers import canonicalize_gtin

PARSER_VERSION = "pricesmart-specs-parser/v1"
PRODUCT_BASE_URL = "https://www.pricesmart.com/es-hn/producto/"
PRODUCT_HOST = "www.pricesmart.com"
_SLUG_RE = re.compile(r"[A-Za-z0-9-]{1,200}")
_PID_RE = re.compile(r"[1-9][0-9]{0,11}")
TEXT_FIELD_LIMIT = 2000

STATUS_PARSED = "parsed"
STATUS_NO_SPECIFICATIONS = "no_specifications"
STATUS_IDENTITY_UNVERIFIED = "identity_unverified"
STATUS_IDENTITY_MISMATCH = "identity_mismatch"
STATUS_PARSE_FAILED = "parse_failed"
PARSE_STATUSES = frozenset({
    STATUS_PARSED,
    STATUS_NO_SPECIFICATIONS,
    STATUS_IDENTITY_UNVERIFIED,
    STATUS_IDENTITY_MISMATCH,
    STATUS_PARSE_FAILED,
})


class PriceSmartSpecsError(ValueError):
    pass


def build_product_url(slug: str, product_id: str) -> str:
    """URL pública de detalle tal como la publica el sitio: ``/producto/<slug>/<pid>``."""

    if not isinstance(slug, str) or not _SLUG_RE.fullmatch(slug):
        raise PriceSmartSpecsError("slug_invalid")
    if not isinstance(product_id, str) or not _PID_RE.fullmatch(product_id):
        raise PriceSmartSpecsError("product_id_invalid")
    return f"{PRODUCT_BASE_URL}{slug}/{product_id}"


# ---------------------------------------------------------------------------
# Texto y etiquetas
# ---------------------------------------------------------------------------

_WS = re.compile(r"\s+")


def clean_text(value: str | None) -> str:
    return _WS.sub(" ", value or "").strip()


def fold(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", clean_text(value)).encode("ascii", "ignore").decode()
    return clean_text(text.casefold()).rstrip(":").strip()


# Etiqueta plegada → atributo canónico. Las etiquetas desconocidas se conservan
# sólo como evidencia cruda (``raw_specifications``).
SPEC_LABELS: dict[str, str] = {
    "almacenamiento": "storage",
    "peso de la unidad (cada uno)": "unit_weight",
    "peso de la unidad": "unit_weight",
    "peso por unidad": "unit_weight",
    "peso unitario": "unit_weight",
    "peso neto": "net_weight",
    "peso neto aproximado": "net_weight",
    "contenido neto": "net_content",
    "contenido": "net_content",
    "volumen": "net_volume",
    "volumen neto": "net_volume",
    "capacidad": "net_volume",
    "libre de grasas trans": "trans_fat_free",
    "cantidad de paquetes (recuentos)": "pack_count",
    "cantidad de paquetes": "pack_count",
    "cantidad de unidades": "pack_count",
    "unidades por paquete": "pack_count",
    "conteo": "pack_count",
    "importado o nacional": "imported_or_national",
    "alergenos": "allergens",
    "marca": "brand",
    "pais de origen": "origin_country",
    "origen": "origin_country",
}
LIST_ATTRIBUTES = frozenset({"allergens"})
SECTION_LABELS = frozenset({
    "especificaciones",
    "descripcion",
    "ingredientes",
    "informacion del producto",
    "detalles de producto y especificaciones",
    "advertencias",
    "instrucciones de uso",
    "modo de empleo",
})
_SPEC_HEADING = "especificaciones"
_LABEL_PREFIXES = tuple(sorted(SPEC_LABELS, key=len, reverse=True))


def spec_key(label: str) -> str | None:
    return SPEC_LABELS.get(fold(label))


# ---------------------------------------------------------------------------
# Números y unidades
# ---------------------------------------------------------------------------

_MASS_FACTORS = {
    "kg": Decimal("1000"),
    "g": Decimal("1"),
    "mg": Decimal("0.001"),
    "lb": Decimal("453.59237"),
    "oz": Decimal("28.349523125"),
}
_VOLUME_FACTORS = {
    "l": Decimal("1000"),
    "ml": Decimal("1"),
    "cl": Decimal("10"),
    "floz": Decimal("29.5735295625"),
    "gal": Decimal("3785.411784"),
}
_UNIT_ALIASES = {
    "kg": "kg", "kgs": "kg", "kilo": "kg", "kilos": "kg", "kilogramo": "kg", "kilogramos": "kg",
    "g": "g", "gr": "g", "grs": "g", "gramo": "g", "gramos": "g",
    "mg": "mg",
    "lb": "lb", "lbs": "lb", "libra": "lb", "libras": "lb",
    "oz": "oz", "onza": "oz", "onzas": "oz",
    "ml": "ml", "mililitro": "ml", "mililitros": "ml",
    "cl": "cl",
    "l": "l", "lt": "l", "lts": "l", "litro": "l", "litros": "l",
    "fl oz": "floz", "fl. oz": "floz", "floz": "floz", "oz fl": "floz",
    "gal": "gal", "galon": "gal", "galones": "gal",
}
_UNIT_PATTERN = "|".join(
    re.escape(alias) for alias in sorted(_UNIT_ALIASES, key=len, reverse=True)
)
_NUMBER = r"\d+(?:[.,]\d+)*"
_QUANTITY_RE = re.compile(
    rf"(?<![\w.,])(?P<amount>{_NUMBER})\s*(?P<unit>{_UNIT_PATTERN})(?![\w])",
    re.IGNORECASE,
)
_IMPERIAL_UNITS = frozenset({"lb", "oz", "floz", "gal"})


def parse_number(text: str) -> Decimal | None:
    """Decimal fail-closed: ``"1.4500"`` → 1.4500, ``"1,5"`` → 1.5, ``"1,450"`` → None.

    Con ambos separadores el último es el decimal. Una coma seguida de grupos de
    exactamente tres dígitos es ambigua (miles o decimal) y no se adivina.
    """

    raw = clean_text(text)
    if not re.fullmatch(_NUMBER, raw):
        return None
    if "," in raw and "." in raw:
        decimal_sep = "," if raw.rfind(",") > raw.rfind(".") else "."
        thousands = "." if decimal_sep == "," else ","
        raw = raw.replace(thousands, "")
        if raw.count(decimal_sep) != 1:
            return None
        raw = raw.replace(",", ".")
    elif "," in raw:
        if re.fullmatch(r"\d{1,3}(?:,\d{3})+", raw) or raw.count(",") != 1:
            return None
        raw = raw.replace(",", ".")
    elif raw.count(".") > 1:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    return value if value.is_finite() and value > 0 else None


def decimal_text(value: Decimal) -> str:
    quantized = value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP).normalize()
    rendered = format(quantized, "f")
    return "0" if rendered in {"", "-0"} else rendered


@dataclass(frozen=True, slots=True)
class Quantity:
    amount: Decimal
    unit: str  # unidad canónica: kg, g, mg, lb, oz, l, ml, cl, floz, gal
    raw: str

    @property
    def dimension(self) -> str:
        return "mass" if self.unit in _MASS_FACTORS else "volume"

    @property
    def base(self) -> Decimal:
        factors = _MASS_FACTORS if self.dimension == "mass" else _VOLUME_FACTORS
        return self.amount * factors[self.unit]

    @property
    def imperial(self) -> bool:
        return self.unit in _IMPERIAL_UNITS


def _quantity_from_match(match: re.Match[str], *, volume_context: bool) -> Quantity | None:
    amount = parse_number(match.group("amount"))
    alias = clean_text(match.group("unit")).casefold()
    unit = _UNIT_ALIASES.get(alias)
    if amount is None or unit is None:
        return None
    if unit == "oz" and volume_context:
        unit = "floz"
    return Quantity(amount=amount, unit=unit, raw=clean_text(match.group(0)))


def parse_quantities(text: str | None, *, volume_context: bool = False) -> list[Quantity]:
    result: list[Quantity] = []
    for match in _QUANTITY_RE.finditer(text or ""):
        quantity = _quantity_from_match(match, volume_context=volume_context)
        if quantity is not None:
            result.append(quantity)
    return result


def prefer_metric(quantities: list[Quantity]) -> Quantity | None:
    if not quantities:
        return None
    for quantity in quantities:
        if not quantity.imperial:
            return quantity
    return quantities[0]


def _quantities_consistent(left: Decimal, right: Decimal, tolerance: Decimal = Decimal("0.02")) -> bool:
    larger = max(left, right)
    return abs(left - right) <= Decimal("1.5") or abs(left - right) / larger <= tolerance


# ---------------------------------------------------------------------------
# DOM mínimo sobre html.parser (sin dependencias nuevas)
# ---------------------------------------------------------------------------

_VOID = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr",
})
_RAW_TEXT_SKIP = frozenset({"script", "style", "noscript", "template", "svg"})
_IMPLICIT_CLOSE = {
    "dt": {"dt", "dd"},
    "dd": {"dt", "dd"},
    "li": {"li"},
    "tr": {"tr", "td", "th"},
    "td": {"td", "th"},
    "th": {"td", "th"},
    "p": {"p"},
    "option": {"option"},
}
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})


@dataclass(eq=False)
class Node:
    tag: str
    attrs: dict[str, str]
    parent: "Node | None" = None
    children: list["Node | str"] = field(default_factory=list)

    def elements(self) -> list["Node"]:
        return [child for child in self.children if isinstance(child, Node)]

    def iter(self) -> Iterator["Node"]:
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.iter()

    def leaves(self) -> list[tuple["Node", str]]:
        """Textos hoja visibles en orden de documento (sin scripts/estilos)."""

        result: list[tuple[Node, str]] = []

        def walk(node: Node) -> None:
            if node.tag in _RAW_TEXT_SKIP:
                return
            for child in node.children:
                if isinstance(child, Node):
                    walk(child)
                else:
                    text = clean_text(child)
                    if text:
                        result.append((node, text))

        walk(self)
        return result

    def text(self) -> str:
        return clean_text(" ".join(text for _, text in self.leaves()))

    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#document", {})
        self.stack: list[Node] = [self.root]
        self.scripts: list[tuple[dict[str, str], str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        closes = _IMPLICIT_CLOSE.get(tag)
        if closes:
            while len(self.stack) > 1 and self.stack[-1].tag in closes:
                self.stack.pop()
        node = Node(tag, {key.casefold(): (value or "") for key, value in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        node = Node(tag, {key.casefold(): (value or "") for key, value in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                node = self.stack[index]
                if tag == "script":
                    self.scripts.append((node.attrs, "".join(
                        child for child in node.children if isinstance(child, str)
                    )))
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def parse_dom(html: str) -> tuple[Node, list[tuple[dict[str, str], str]]]:
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root, builder.scripts


# ---------------------------------------------------------------------------
# JSON embebido
# ---------------------------------------------------------------------------

_GTIN_KEYS = frozenset({
    "gtin", "gtin8", "gtin12", "gtin13", "gtin14", "ean", "ean13", "upc", "barcode",
})
_PRODUCT_ID_KEYS = frozenset({
    "pid", "productid", "itemnumber", "item_number", "mastersku", "master_sku",
    "sku", "skuid",
})
_PAIR_LABEL_KEYS = ("name", "label", "attributename", "displayname", "title", "key")
_PAIR_VALUE_KEYS = ("value", "displayvalue", "values", "attributevalue", "text")


def _load_json(raw: str) -> Any:
    text = raw.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def _walk_json(value: Any) -> Iterator[Any]:
    stack = [value]
    while stack:
        current = stack.pop()
        yield current
        if isinstance(current, dict):
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)


def _json_ld_objects(scripts: list[tuple[dict[str, str], str]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for attrs, raw in scripts:
        if (attrs.get("type") or "").casefold() != "application/ld+json":
            continue
        for item in _walk_json(_load_json(raw)):
            if isinstance(item, dict) and "@type" in item:
                result.append(item)
    return result


def _ld_types(item: dict[str, Any]) -> set[str]:
    value = item.get("@type")
    values = value if isinstance(value, list) else [value]
    return {str(entry).casefold() for entry in values if isinstance(entry, str)}


def _scalar_text(value: Any) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, str)):
        text = clean_text(str(value))
        return text or None
    if isinstance(value, dict):
        return _scalar_text(value.get("name") or value.get("value"))
    return None


def _value_strings(value: Any) -> list[str]:
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_value_strings(item))
        return result
    text = _scalar_text(value)
    return [text] if text else []


def _identifies(item: dict[str, Any], product_id: str) -> bool | None:
    """True: mismo producto; False: otro producto; None: sin identificador de producto."""

    verdict: bool | None = None
    for key, value in item.items():
        if key.casefold() not in _PRODUCT_ID_KEYS:
            continue
        text = _scalar_text(value) if not isinstance(value, (dict, list)) else None
        if text is None or not text.isdigit():
            continue
        if text == product_id:
            return True
        verdict = False
    return verdict


def _product_subtree_items(document: Any, product_id: str) -> list[Any]:
    """Nodos JSON dentro de subárboles del mismo pid, sin entrar a otros productos."""

    roots: list[dict[str, Any]] = [
        item for item in _walk_json(document)
        if isinstance(item, dict) and _identifies(item, product_id) is True
    ]
    seen: set[int] = set()
    result: list[Any] = []
    for root in roots:
        stack: list[Any] = [root]
        while stack:
            current = stack.pop()
            if id(current) in seen:
                continue
            seen.add(id(current))
            if current is not root and isinstance(current, dict) and _identifies(current, product_id) is False:
                continue
            result.append(current)
            if isinstance(current, dict):
                stack.extend(current.values())
            elif isinstance(current, list):
                stack.extend(current)
    return result


@dataclass
class _Pairs:
    """Pares etiqueta/valor de una fuente, con conflictos internos detectados."""

    source: str
    values: dict[str, list[str]] = field(default_factory=dict)
    raw: list[dict[str, Any]] = field(default_factory=list)
    conflicts: set[str] = field(default_factory=set)

    def add(self, label: str, values: list[str]) -> None:
        values = [clean_text(value) for value in values if clean_text(value)]
        if not clean_text(label) or not values:
            return
        entry = {"label": clean_text(label), "value": values if len(values) > 1 else values[0]}
        if entry not in self.raw:
            self.raw.append(entry)
        key = spec_key(label)
        if key is None:
            return
        previous = self.values.get(key)
        if previous is None:
            self.values[key] = values
        elif [fold(v) for v in previous] != [fold(v) for v in values]:
            self.conflicts.add(key)

    @property
    def recognized(self) -> int:
        return len(self.values)


def _json_pairs(scripts: list[tuple[dict[str, str], str]], product_id: str) -> tuple[_Pairs, list[dict[str, str]], bool]:
    pairs = _Pairs("embedded_json")
    gtins: list[dict[str, str]] = []
    product_found = False
    for attrs, raw in scripts:
        script_type = (attrs.get("type") or "").casefold()
        if script_type == "application/ld+json":
            continue
        if attrs.get("id") != "__NEXT_DATA__" and script_type != "application/json":
            continue
        document = _load_json(raw)
        if document is None:
            continue
        items = _product_subtree_items(document, product_id)
        product_found = product_found or bool(items)
        source = "next_data" if attrs.get("id") == "__NEXT_DATA__" else "application_json"
        for item in items:
            if not isinstance(item, dict):
                continue
            lowered = {key.casefold(): value for key, value in item.items()}
            for key, value in lowered.items():
                if key in _GTIN_KEYS:
                    for text in _value_strings(value):
                        gtins.append({"source": f"{source}:{key}", "raw": text})
            label = next(
                (lowered[key] for key in _PAIR_LABEL_KEYS if isinstance(lowered.get(key), str)),
                None,
            )
            if label is None or spec_key(label) is None:
                continue
            value_key = next((key for key in _PAIR_VALUE_KEYS if key in lowered), None)
            if value_key is None:
                continue
            pairs.add(label, _value_strings(lowered[value_key]))
    return pairs, gtins, product_found


# ---------------------------------------------------------------------------
# DOM: bloque de especificaciones
# ---------------------------------------------------------------------------


def _split_inline_label(text: str) -> tuple[str, str] | None:
    """``"Peso neto: 1.45 kg"`` o ``"Marca Breadco"`` → (etiqueta, valor)."""

    folded = fold(text)
    for label in _LABEL_PREFIXES:
        if folded.startswith(label) and len(folded) > len(label):
            rest_folded = folded[len(label):]
            if not rest_folded[:1] in {":", " "}:
                continue
            # Recortar sobre el texto original conservando acentos.
            words = len(label.split())
            original_words = clean_text(text).split(" ")
            value = " ".join(original_words[words:]).lstrip(":").strip()
            label_text = " ".join(original_words[:words]).rstrip(":")
            if value:
                return label_text, value
    return None


def _structured_pairs(container: Node) -> _Pairs:
    pairs = _Pairs("dom_structured")
    for node in container.iter():
        if node.tag == "dl":
            label: str | None = None
            collected: list[str] = []
            for child in node.elements():
                if child.tag == "dt":
                    if label is not None:
                        pairs.add(label, collected)
                    label, collected = child.text(), []
                elif child.tag == "dd" and label is not None:
                    collected.extend(text for _, text in child.leaves())
                elif child.tag == "div":  # dl > div > dt + dd (HTML válido)
                    dts = [item for item in child.elements() if item.tag == "dt"]
                    dds = [item for item in child.elements() if item.tag == "dd"]
                    if len(dts) == 1 and dds:
                        pairs.add(dts[0].text(), [text for dd in dds for _, text in dd.leaves()])
            if label is not None:
                pairs.add(label, collected)
        elif node.tag == "tr":
            cells = [child for child in node.elements() if child.tag in {"td", "th"}]
            if len(cells) == 2:
                pairs.add(cells[0].text(), [text for _, text in cells[1].leaves()])
        else:
            elements = node.elements()
            if len(elements) == 2 and not any(
                isinstance(child, str) and clean_text(child) for child in node.children
            ):
                label_text = elements[0].text()
                if spec_key(label_text) is not None and elements[1].text():
                    pairs.add(label_text, [text for _, text in elements[1].leaves()])
    return pairs


def _text_sequence_pairs(leaves: list[str]) -> _Pairs:
    pairs = _Pairs("dom_text")
    index = 0
    while index < len(leaves):
        text = leaves[index]
        key = spec_key(text)
        if key is None:
            inline = _split_inline_label(text)
            if inline is not None:
                pairs.add(inline[0], [inline[1]])
            index += 1
            continue
        values: list[str] = []
        cursor = index + 1
        limit = 15 if key in LIST_ATTRIBUTES else 1
        while cursor < len(leaves) and len(values) < limit:
            candidate = leaves[cursor]
            if spec_key(candidate) is not None or fold(candidate) in SECTION_LABELS or _split_inline_label(candidate):
                break
            values.append(candidate)
            cursor += 1
        pairs.add(text, values)
        index = cursor
    return pairs


def _heading_candidates(root: Node) -> list[Node]:
    """Elementos más internos cuyo texto hoja es exactamente "Especificaciones" (O(n))."""

    result: list[Node] = []
    for owner, text in root.leaves():
        if fold(text) == _SPEC_HEADING and owner.tag != "#document" and owner not in result:
            result.append(owner)
    return result


def _recognized_labels(leaves: list[str]) -> int:
    return sum(spec_key(text) is not None or _split_inline_label(text) is not None for text in leaves)


def _spec_container(root: Node) -> tuple[Node, Node] | None:
    best: tuple[int, Node, Node] | None = None
    for heading in _heading_candidates(root):
        node: Node | None = heading.parent
        depth = 0
        while node is not None and node.tag != "#document" and depth < 8:
            leaves = [text for _, text in node.leaves()]
            if _recognized_labels(leaves) >= 1:
                size = len(leaves)
                if best is None or size < best[0]:
                    best = (size, heading, node)
                break
            node = node.parent
            depth += 1
    return None if best is None else (best[1], best[2])


def _leaves_after(container: Node, heading: Node) -> list[str]:
    heading_nodes = set(id(node) for node in heading.iter())
    result: list[str] = []
    started = False
    for owner, text in container.leaves():
        if id(owner) in heading_nodes:
            started = True
            continue
        if started:
            result.append(text)
    return result


def _dom_spec_pairs(root: Node) -> _Pairs | None:
    located = _spec_container(root)
    if located is None:
        return None
    heading, container = located
    structured = _structured_pairs(container)
    if structured.recognized:
        return structured
    sequence = _text_sequence_pairs(_leaves_after(container, heading))
    return sequence if sequence.recognized else None


def _section_text(leaves: list[str], label: str, *, max_leaves: int = 12) -> str | None:
    for index, text in enumerate(leaves):
        folded = fold(text)
        if folded == label:
            parts: list[str] = []
            for candidate in leaves[index + 1 : index + 1 + max_leaves]:
                if fold(candidate) in SECTION_LABELS:
                    break
                parts.append(candidate)
            value = clean_text(" ".join(parts))
            return value[:TEXT_FIELD_LIMIT] or None
        if folded.startswith(label + ":"):
            value = clean_text(text.split(":", 1)[1])
            return value[:TEXT_FIELD_LIMIT] or None
    return None


def _breadcrumb_dom(root: Node, title: str | None) -> list[str]:
    for node in root.iter():
        label = fold(node.attrs.get("aria-label"))
        classes = " ".join(node.classes()).casefold()
        if "breadcrumb" in label or "ruta de navegacion" in label or "breadcrumb" in classes:
            items = [
                text for _, text in node.leaves()
                if text not in {"›", ">", "/", "|", "»", "·"} and fold(text) not in {"inicio", "home"}
            ]
            if title and items and fold(items[-1]) == fold(title):
                items = items[:-1]
            if items:
                return items
    return []


# ---------------------------------------------------------------------------
# Normalización de atributos
# ---------------------------------------------------------------------------


@dataclass
class _Merged:
    values: dict[str, list[str]]
    sources: dict[str, str]
    conflicts: list[str]


def _normalized_signature(key: str, values: list[str]) -> str:
    if key in {"net_weight", "unit_weight", "net_volume", "net_content"}:
        quantity = prefer_metric(parse_quantities(" ".join(values), volume_context=key == "net_volume"))
        return "" if quantity is None else f"{quantity.dimension}:{decimal_text(quantity.base)}"
    return "|".join(fold(value) for value in values)


def _merge(json_pairs: _Pairs, dom_pairs: _Pairs | None) -> _Merged:
    values: dict[str, list[str]] = {}
    sources: dict[str, str] = {}
    conflicts: list[str] = []
    for pairs in (json_pairs, dom_pairs):
        if pairs is None:
            continue
        for key in sorted(pairs.conflicts):
            conflicts.append(f"{key}:ambiguous_within_{pairs.source}")
    keys = set(json_pairs.values) | set(dom_pairs.values if dom_pairs else {})
    for key in sorted(keys):
        if key in json_pairs.conflicts or (dom_pairs is not None and key in dom_pairs.conflicts):
            continue
        json_value = json_pairs.values.get(key)
        dom_value = dom_pairs.values.get(key) if dom_pairs else None
        if json_value is not None and dom_value is not None:
            if _normalized_signature(key, json_value) != _normalized_signature(key, dom_value):
                conflicts.append(f"{key}:embedded_json_vs_dom")
                continue
            values[key] = dom_value
            sources[key] = "embedded_json+dom"
        elif json_value is not None:
            values[key] = json_value
            sources[key] = json_pairs.source
        elif dom_value is not None:
            values[key] = dom_value
            sources[key] = dom_pairs.source if dom_pairs else "dom"
    return _Merged(values, sources, conflicts)


def _measure(values: list[str] | None, *, dimension: str, source: str) -> dict[str, Any] | None:
    if not values:
        return None
    raw = clean_text(" ".join(values))
    quantity = prefer_metric([
        item for item in parse_quantities(raw, volume_context=dimension == "volume")
        if item.dimension == dimension
    ])
    if quantity is None:
        return None
    key = "value_g" if dimension == "mass" else "value_ml"
    return {
        key: decimal_text(quantity.base),
        "raw": raw,
        "unit": quantity.unit,
        "imperial_derived": quantity.imperial,
        "source": source,
    }


def _joined(values: list[str] | None) -> str:
    return clean_text(" ".join(values or []))


def _pack_count(values: list[str] | None) -> int | None:
    text = _joined(values)
    if not text:
        return None
    number = parse_number(re.sub(r"(?i)\s*(unidades?|uds?|piezas?|paquetes?)\s*$", "", text))
    if number is None or number != number.to_integral_value() or number > 10000:
        return None
    return int(number)


def _yes_no(values: list[str] | None) -> bool | None:
    folded = fold(_joined(values))
    if folded in {"si", "yes", "verdadero"}:
        return True
    if folded in {"no", "false", "falso"}:
        return False
    return None


def _imported(values: list[str] | None) -> str | None:
    folded = fold(_joined(values))
    if folded in {"nacional", "importado"}:
        return folded
    return None


def _allergens(values: list[str] | None) -> list[str]:
    if not values:
        return []
    items: list[str] = []
    for value in values:
        for part in re.split(r"\s*[,;/\n]\s*|\s+y\s+", value):
            part = clean_text(part).strip(".")
            if part and part not in items:
                items.append(part)
    return items


_ORIGIN_RE = re.compile(
    r"hecho en\s+(?P<country>[A-Za-zÁÉÍÓÚÜÑáéíóúüñ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ .'-]{1,40}?)\s*(?:[.|,;]|$)",
    re.IGNORECASE,
)
_INFO_NET_RE = re.compile(
    rf"peso\s+neto\s*:?\s*(?P<first>{_NUMBER}\s*(?:{_UNIT_PATTERN}))(?:\s*/\s*(?P<second>{_NUMBER}\s*(?:{_UNIT_PATTERN})))?",
    re.IGNORECASE,
)


def _info_net_weight(text: str | None) -> dict[str, Any] | None:
    match = _INFO_NET_RE.search(text or "")
    if match is None:
        return None
    quantities = parse_quantities(" ".join(filter(None, (match.group("first"), match.group("second")))))
    quantity = prefer_metric([item for item in quantities if item.dimension == "mass"])
    if quantity is None:
        return None
    return {
        "value_g": decimal_text(quantity.base),
        "raw": clean_text(match.group(0)),
        "unit": quantity.unit,
        "imperial_derived": quantity.imperial,
        "source": "product_information",
    }


def _origin(text: str | None) -> str | None:
    match = _ORIGIN_RE.search(text or "")
    return clean_text(match.group("country")) if match else None


def presentation_hint(specs: dict[str, Any]) -> str | None:
    """Texto de presentación compatible con el parser de homologación.

    Sólo usa atributos sin conflicto: ``"16 x 90.63 g"`` si conteo × peso
    unitario cuadra con el neto, si no ``"1450 g"``/``"946 ml"`` y, como último
    recurso, ``"16 unidades"``.
    """

    conflicts = {item.split(":", 1)[0] for item in specs.get("conflicts", [])}
    net = specs.get("net_weight") if "net_weight" not in conflicts else None
    volume = specs.get("net_volume") if "net_volume" not in conflicts else None
    unit = specs.get("unit_weight") if "unit_weight" not in conflicts else None
    pack = specs.get("pack_count") if "pack_count" not in conflicts else None
    consistency = specs.get("consistency", {}).get("pack_x_unit_vs_net")
    if net and pack and pack > 1 and unit and consistency == "consistent":
        return f"{pack} x {unit['value_g']} g"
    if net:
        return f"{net['value_g']} g"
    if volume:
        return f"{volume['value_ml']} ml"
    if pack and pack > 1 and unit:
        return f"{pack} x {unit['value_g']} g"
    if pack and pack > 1:
        return f"{pack} unidades"
    return None


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpecParseResult:
    status: str
    specs: dict[str, Any] | None
    extraction_method: str | None
    error: str | None = None

    def __post_init__(self) -> None:
        if self.status not in PARSE_STATUSES:
            raise PriceSmartSpecsError("parse_status_invalid")
        if (self.status == STATUS_PARSED) != (self.specs is not None):
            raise PriceSmartSpecsError("parse_status_specs_mismatch")


_ITEM_NUMBER_RE = re.compile(r"n[uú]mero\s+de\s+[ií]tem\s*:?\s*#?\s*(\d{1,12})(?!\d)", re.IGNORECASE)


def _link_identity(root: Node, product_id: str) -> bool:
    for node in root.iter():
        if node.tag == "link" and "canonical" in (node.attrs.get("rel") or "").casefold().split():
            href = node.attrs.get("href") or ""
        elif node.tag == "meta" and (node.attrs.get("property") or "").casefold() == "og:url":
            href = node.attrs.get("content") or ""
        else:
            continue
        if href.split("?", 1)[0].rstrip("/").endswith(f"/{product_id}"):
            return True
    return False


def _gtin_candidates(raw: list[dict[str, str]], product_id: str) -> tuple[list[dict[str, Any]], str | None]:
    candidates: list[dict[str, Any]] = []
    for item in raw:
        text = re.sub(r"\D", "", item["raw"]) if re.fullmatch(r"[\d\s-]+", item["raw"]) else item["raw"]
        canonical = canonicalize_gtin(text) if text != product_id else None
        entry = {"source": item["source"], "raw": item["raw"], "valid_gs1": canonical is not None, "canonical": canonical}
        if entry not in candidates:
            candidates.append(entry)
    valid = {entry["canonical"] for entry in candidates if entry["canonical"]}
    return candidates, (next(iter(valid)) if len(valid) == 1 else None)


def parse_product_page(html: str, product_id: str) -> SpecParseResult:
    """Parsea una página de detalle. Nunca lanza: cualquier error es ``parse_failed``."""

    if not isinstance(product_id, str) or not _PID_RE.fullmatch(product_id):
        raise PriceSmartSpecsError("product_id_invalid")
    try:
        return _parse(html, product_id)
    except Exception as exc:  # noqa: BLE001 - fail-closed por página
        return SpecParseResult(STATUS_PARSE_FAILED, None, None, f"{type(exc).__name__}:{str(exc)[:200]}")


def _parse(html: str, product_id: str) -> SpecParseResult:
    if not isinstance(html, str) or not html.strip():
        return SpecParseResult(STATUS_PARSE_FAILED, None, None, "empty_html")
    root, scripts = parse_dom(html)
    leaves = [text for _, text in root.leaves()]
    page_text = " ".join(leaves)

    ld_objects = _json_ld_objects(scripts)
    ld_products = [item for item in ld_objects if "product" in _ld_types(item)]
    ld_breadcrumbs = [item for item in ld_objects if "breadcrumblist" in _ld_types(item)]
    json_pairs, gtin_raw, json_product_found = _json_pairs(scripts, product_id)

    # Identidad: el número de ítem visible debe ser exactamente el pid pedido.
    item_numbers = set(_ITEM_NUMBER_RE.findall(page_text))
    if item_numbers and product_id not in item_numbers:
        return SpecParseResult(
            STATUS_IDENTITY_MISMATCH, None, None,
            f"item_number_mismatch:{','.join(sorted(item_numbers))}",
        )
    ld_skus = {
        text for item in ld_products for text in _value_strings(item.get("sku")) if text.isdigit()
    }
    if ld_skus and product_id not in ld_skus:
        return SpecParseResult(STATUS_IDENTITY_MISMATCH, None, None, "json_ld_sku_mismatch")
    identity: list[str] = []
    if item_numbers:
        identity.append("visible_item_number")
    if ld_skus:
        identity.append("json_ld_sku")
    if json_product_found:
        identity.append("embedded_json_pid")
    if _link_identity(root, product_id):
        identity.append("canonical_url")
    if not identity:
        return SpecParseResult(STATUS_IDENTITY_UNVERIFIED, None, None, "no_identity_evidence")

    for item in ld_products:
        for key, value in item.items():
            if key.casefold() in _GTIN_KEYS:
                for text in _value_strings(value):
                    gtin_raw.append({"source": f"json_ld:{key}", "raw": text})

    dom_pairs = _dom_spec_pairs(root)
    if not json_pairs.recognized and dom_pairs is None:
        return SpecParseResult(STATUS_NO_SPECIFICATIONS, None, None, "specifications_block_not_found")

    merged = _merge(json_pairs, dom_pairs)
    method = "+".join(sorted({
        part for source in merged.sources.values() for part in source.split("+")
    })) or (dom_pairs.source if dom_pairs else json_pairs.source)

    title_nodes = [node for node in root.iter() if node.tag == "h1"]
    title = title_nodes[0].text() if title_nodes else None
    if not title:
        title = next((_scalar_text(item.get("name")) for item in ld_products if item.get("name")), None)

    breadcrumb: list[str] = []
    for item in ld_breadcrumbs:
        elements = item.get("itemListElement")
        if isinstance(elements, list):
            ordered = sorted(
                (entry for entry in elements if isinstance(entry, dict)),
                key=lambda entry: entry.get("position") if isinstance(entry.get("position"), int) else 0,
            )
            names = [
                _scalar_text(entry.get("name")) or _scalar_text(entry.get("item")) for entry in ordered
            ]
            breadcrumb = [name for name in names if name and fold(name) not in {"inicio", "home"}]
            if title and breadcrumb and fold(breadcrumb[-1]) == fold(title):
                breadcrumb = breadcrumb[:-1]
            if breadcrumb:
                break
    if not breadcrumb:
        breadcrumb = _breadcrumb_dom(root, title)

    info_text = _section_text(leaves, "informacion del producto")
    description = _section_text(leaves, "descripcion")
    ingredients = _section_text(leaves, "ingredientes")

    values = merged.values
    sources = merged.sources
    conflicts = list(merged.conflicts)

    def source_of(key: str) -> str:
        return sources.get(key, "")

    net_weight = _measure(values.get("net_weight"), dimension="mass", source=source_of("net_weight"))
    net_volume = _measure(values.get("net_volume"), dimension="volume", source=source_of("net_volume"))
    content = values.get("net_content")
    if content:
        content_mass = _measure(content, dimension="mass", source=source_of("net_content"))
        content_volume = _measure(content, dimension="volume", source=source_of("net_content"))
        if content_mass and not content_volume:
            if net_weight is None:
                net_weight = content_mass
            elif net_weight["value_g"] != content_mass["value_g"] and not _quantities_consistent(
                Decimal(net_weight["value_g"]), Decimal(content_mass["value_g"])
            ):
                conflicts.append("net_weight:peso_neto_vs_contenido_neto")
        elif content_volume and not content_mass:
            if net_volume is None:
                net_volume = content_volume
            elif not _quantities_consistent(
                Decimal(net_volume["value_ml"]), Decimal(content_volume["value_ml"])
            ):
                conflicts.append("net_volume:volumen_vs_contenido_neto")

    info_net = _info_net_weight(info_text)
    info_vs_spec = "not_applicable"
    if info_net is not None:
        if net_weight is None:
            net_weight = info_net
            info_vs_spec = "info_only"
        elif _quantities_consistent(Decimal(net_weight["value_g"]), Decimal(info_net["value_g"])):
            info_vs_spec = "consistent"
        else:
            info_vs_spec = "mismatch"
            conflicts.append("net_weight:specifications_vs_product_information")

    unit_weight = _measure(values.get("unit_weight"), dimension="mass", source=source_of("unit_weight"))
    pack_count = _pack_count(values.get("pack_count"))
    pack_consistency = "not_applicable"
    if net_weight and unit_weight and pack_count:
        total = Decimal(unit_weight["value_g"]) * pack_count
        pack_consistency = (
            "consistent" if _quantities_consistent(total, Decimal(net_weight["value_g"])) else "mismatch"
        )
        if pack_consistency == "mismatch":
            conflicts.append("pack_count:pack_x_unit_vs_net_weight")

    spec_origin = clean_text(" ".join(values.get("origin_country", []))) or None
    info_origin = _origin(info_text)
    origin = spec_origin or info_origin
    if spec_origin and info_origin and fold(spec_origin) != fold(info_origin):
        conflicts.append("origin_country:specifications_vs_product_information")
        origin = None

    brand_spec = clean_text(" ".join(values.get("brand", []))) or None
    brand_ld = next(
        (_scalar_text(item.get("brand")) for item in ld_products if item.get("brand")), None
    )
    brand = brand_spec or brand_ld
    if brand_spec and brand_ld and fold(brand_spec) != fold(brand_ld):
        conflicts.append("brand:specifications_vs_json_ld")
        brand = None

    gtin_candidates, gtin = _gtin_candidates(gtin_raw, product_id)
    specs: dict[str, Any] = {
        "parser_version": PARSER_VERSION,
        "product_id": product_id,
        "item_number": product_id if item_numbers else None,
        "identity_evidence": identity,
        "title": title,
        "brand": brand,
        "category_path": breadcrumb,
        "net_weight": net_weight,
        "net_volume": net_volume,
        "unit_weight": unit_weight,
        "pack_count": pack_count,
        "imported_or_national": _imported(values.get("imported_or_national")),
        "origin_country": origin,
        "storage": clean_text(" ".join(values.get("storage", []))) or None,
        "allergens": _allergens(values.get("allergens")),
        "trans_fat_free": _yes_no(values.get("trans_fat_free")),
        "description": description,
        "ingredients": ingredients,
        "product_information": info_text,
        "gtin": gtin,
        "gtin_candidates": gtin_candidates,
        "raw_specifications": (json_pairs.raw + (dom_pairs.raw if dom_pairs else []))[:80],
        "attribute_sources": dict(sorted(sources.items())),
        "consistency": {
            "pack_x_unit_vs_net": pack_consistency,
            "product_information_vs_specifications_net_weight": info_vs_spec,
        },
        "conflicts": sorted(set(conflicts)),
    }
    specs["presentation_hint"] = presentation_hint(specs)
    return SpecParseResult(STATUS_PARSED, specs, method)


def spec_fingerprint(specs: dict[str, Any]) -> str:
    serialized = json.dumps(specs, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
