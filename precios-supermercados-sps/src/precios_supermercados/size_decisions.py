"""Decisiones humanas de tamaño por GTIN (panel de revisión de Súper Compras).

Cuando varias cadenas publican tamaños distintos para el mismo código de barras
(p. ej. Cholula: 150 g, 150 ml y 5 oz), una persona revisa el producto en la
página del súper y fija el tamaño oficial. La decisión vive en la tabla
``size_decisions`` (no en los perfiles, que se recalculan cada día) y el motor la
aplica en cada refresco: las ofertas que coinciden con el tamaño oficial forman
el grupo comparable y las demás quedan aisladas. ``different_products`` marca un
código reutilizado: ese GTIN no se agrupa.

Este módulo es puro (sin red): valida el JSON del panel, normaliza unidades y
decide si una presentación coincide con el tamaño oficial.
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .identifiers import canonicalize_gtin
from .product_homologation import PresentationSignature, ounces_as_mass, ounces_as_volume

TABLE_NAME = "size_decisions"
PAYLOAD_SCHEMA = "rpi-size-decisions/v1"
DECISIONS = ("size", "different_products")
DECLARED_UNITS = ("g", "ml", "oz", "lb", "unit")
CANONICAL_UNITS = ("g", "ml", "oz", "unit")
MAX_DECISIONS = 300
MAX_PAYLOAD_CHARS = 60_000  # workflow_dispatch acepta 65,535 caracteres por llamada
MAX_NOTE_CHARS = 200
MAX_VALUE = {"g": Decimal("100000"), "ml": Decimal("100000"), "oz": Decimal("3500"), "lb": Decimal("220"),
             "unit": Decimal("1000")}
_POUND_TO_GRAMS = Decimal("453.59237")
_DIMENSION = {"g": "mass_g", "ml": "volume_ml", "oz": "ounce", "unit": "count"}
_ACTOR = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
_RELATIVE_TOLERANCE = Decimal("0.02")
_ABSOLUTE_TOLERANCE = Decimal("1.5")

TABLE_SQL = f"""CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    canonical_gtin TEXT PRIMARY KEY CHECK (length(canonical_gtin) = 14),
    decision TEXT NOT NULL CHECK (decision IN ('size','different_products')),
    net_content_value TEXT,
    net_content_unit TEXT CHECK (net_content_unit IS NULL OR net_content_unit IN ('g','ml','oz','unit')),
    declared_value TEXT,
    declared_unit TEXT CHECK (declared_unit IS NULL OR declared_unit IN ('g','ml','oz','lb','unit')),
    note TEXT,
    decided_by TEXT NOT NULL,
    decided_at_utc TEXT NOT NULL,
    CHECK ((decision = 'size') = (net_content_value IS NOT NULL AND net_content_unit IS NOT NULL))
) STRICT"""
SELECT_SQL = (
    f"SELECT canonical_gtin,decision,net_content_value,net_content_unit,declared_value,declared_unit,"
    f"note,decided_by,decided_at_utc FROM {TABLE_NAME} ORDER BY canonical_gtin"
)


class SizeDecisionError(ValueError):
    """El JSON del panel o una fila guardada no cumple el contrato."""


@dataclass(frozen=True, slots=True)
class SizeDecision:
    canonical_gtin: str
    decision: str
    net_content_value: Decimal | None = None
    net_content_unit: str | None = None
    declared_value: Decimal | None = None
    declared_unit: str | None = None
    note: str | None = None
    decided_by: str = "human"
    decided_at_utc: str = ""

    def __post_init__(self) -> None:
        if canonicalize_gtin(self.canonical_gtin) != self.canonical_gtin:
            raise SizeDecisionError("size_decision_gtin_invalid")
        if self.decision not in DECISIONS:
            raise SizeDecisionError("size_decision_kind_invalid")
        sized = self.net_content_value is not None and self.net_content_unit is not None
        if (self.decision == "size") != sized:
            raise SizeDecisionError("size_decision_value_required")
        if sized and (self.net_content_unit not in CANONICAL_UNITS or self.net_content_value <= 0):
            raise SizeDecisionError("size_decision_value_invalid")

    @property
    def official(self) -> PresentationSignature | None:
        """Tamaño oficial como firma (g, ml, onza sin convertir o unidades)."""
        if self.decision != "size":
            return None
        total = self.net_content_value
        return PresentationSignature(
            dimension=_DIMENSION[str(self.net_content_unit)], total_base=total, pack_count=1, unit_amount_base=total,
        )


# ------------------------------------------------------------------ validación

def _clean_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SizeDecisionError("size_decision_note_invalid")
    text = " ".join(unicodedata.normalize("NFC", value).split())
    if any(unicodedata.category(char).startswith("C") for char in text):
        raise SizeDecisionError("size_decision_note_invalid")
    if len(text) > MAX_NOTE_CHARS:
        raise SizeDecisionError("size_decision_note_too_long")
    return text or None


def _decimal(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise SizeDecisionError("size_decision_value_invalid")
    try:
        number = Decimal(str(value).strip().replace(",", "."))
    except InvalidOperation as error:
        raise SizeDecisionError("size_decision_value_invalid") from error
    if not number.is_finite() or number <= 0:
        raise SizeDecisionError("size_decision_value_invalid")
    return number


def canonical_size(value: Decimal, unit: str) -> tuple[Decimal, str]:
    """Libras a gramos; el resto queda en su unidad (la onza no se adivina)."""
    if unit == "lb":
        return value * _POUND_TO_GRAMS, "g"
    return value, unit


def parse_payload(text: str, *, decided_by: str, decided_at_utc: str) -> list[SizeDecision]:
    """JSON del panel → decisiones válidas. Falla cerrado ante cualquier dato raro."""
    if not isinstance(text, str) or not text.strip():
        raise SizeDecisionError("size_decisions_empty")
    if len(text) > MAX_PAYLOAD_CHARS:
        raise SizeDecisionError("size_decisions_too_large")
    if not _ACTOR.fullmatch(decided_by or ""):
        raise SizeDecisionError("size_decisions_actor_invalid")
    try:
        document = json.loads(text)
    except ValueError as error:
        raise SizeDecisionError("size_decisions_json_invalid") from error
    if not isinstance(document, Mapping) or document.get("schema") != PAYLOAD_SCHEMA:
        raise SizeDecisionError("size_decisions_schema_invalid")
    items = document.get("decisions")
    if not isinstance(items, list) or not items:
        raise SizeDecisionError("size_decisions_empty")
    if len(items) > MAX_DECISIONS:
        raise SizeDecisionError("size_decisions_too_many")
    result: list[SizeDecision] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, Mapping) or set(item) - {"gtin", "decision", "value", "unit", "note"}:
            raise SizeDecisionError("size_decision_fields_invalid")
        gtin = canonicalize_gtin(str(item.get("gtin") or "").strip() or None)
        if gtin is None:
            raise SizeDecisionError("size_decision_gtin_invalid")
        if gtin in seen:
            raise SizeDecisionError("size_decision_gtin_duplicate")
        seen.add(gtin)
        kind = item.get("decision")
        note = _clean_text(item.get("note"))
        common = {"canonical_gtin": gtin, "note": note, "decided_by": f"human:{decided_by}",
                  "decided_at_utc": decided_at_utc}
        if kind == "different_products":
            if item.get("value") is not None or item.get("unit") is not None:
                raise SizeDecisionError("size_decision_fields_invalid")
            result.append(SizeDecision(decision=kind, **common))
            continue
        if kind != "size":
            raise SizeDecisionError("size_decision_kind_invalid")
        unit = item.get("unit")
        if unit not in DECLARED_UNITS:
            raise SizeDecisionError("size_decision_unit_invalid")
        value = _decimal(item.get("value"))
        if value > MAX_VALUE[unit] or (unit == "unit" and value != value.to_integral_value()):
            raise SizeDecisionError("size_decision_value_out_of_range")
        total, canonical_unit = canonical_size(value, unit)
        result.append(SizeDecision(
            decision=kind, net_content_value=total.normalize(), net_content_unit=canonical_unit,
            declared_value=value.normalize(), declared_unit=unit, **common,
        ))
    return result


def decision_from_row(row: Sequence[object]) -> SizeDecision:
    gtin, kind, value, unit, declared_value, declared_unit, note, decided_by, decided_at = row
    return SizeDecision(
        canonical_gtin=str(gtin), decision=str(kind),
        net_content_value=None if value is None else Decimal(str(value)),
        net_content_unit=None if unit is None else str(unit),
        declared_value=None if declared_value is None else Decimal(str(declared_value)),
        declared_unit=None if declared_unit is None else str(declared_unit),
        note=None if note is None else str(note), decided_by=str(decided_by), decided_at_utc=str(decided_at),
    )


def index_decisions(decisions: Iterable[SizeDecision]) -> dict[str, SizeDecision]:
    result: dict[str, SizeDecision] = {}
    for decision in decisions:
        if decision.canonical_gtin in result:
            raise SizeDecisionError("size_decision_gtin_duplicate")
        result[decision.canonical_gtin] = decision
    return result


# -------------------------------------------------------------------- coincidir

def _close(left: Decimal, right: Decimal) -> bool:
    larger = max(left, right)
    return abs(left - right) <= _ABSOLUTE_TOLERANCE or abs(left - right) / larger <= _RELATIVE_TOLERANCE


def _readings(signature: PresentationSignature) -> list[tuple[PresentationSignature, str | None]]:
    """La firma tal cual y, si es onza sin convertir, sus dos lecturas (líquido y peso)."""
    if signature.dimension != "ounce":
        return [(signature, None)]
    return [(signature, None), (ounces_as_volume(signature), "fluid"), (ounces_as_mass(signature), "mass")]


def match_official(official: PresentationSignature, presentation: PresentationSignature | None) -> tuple[bool, str | None]:
    """¿La presentación de una oferta coincide con el tamaño oficial? Y, si era onza, en qué lectura.

    Compara el contenido total (un paquete de 3 × 100 g coincide con 300 g) con la
    tolerancia imperial (2 % o 1.5 unidades). Una oferta sin presentación no
    contradice al oficial: se queda en el grupo.
    """
    if presentation is None:
        return True, None
    for mine, reading in _readings(presentation):
        for theirs, _ in _readings(official):
            if mine.dimension == theirs.dimension and mine.dimension != "count" and _close(mine.total_base, theirs.total_base):
                return True, reading
            if mine.dimension == theirs.dimension == "count" and mine.total_base == theirs.total_base:
                return True, None
    return False, None


def human_attributes(decision: SizeDecision) -> dict[str, dict[str, object]]:
    """Atributos humanos del producto maestro (``human_attributes_json``) para un tamaño."""
    if decision.decision != "size":
        return {}
    meta = {"decided_by": decision.decided_by, "decided_at_utc": decision.decided_at_utc, "source": TABLE_NAME}
    return {
        "net_content_value": {"value": format(decision.net_content_value, "f"), **meta},
        "net_content_unit": {"value": decision.net_content_unit, **meta},
    }
