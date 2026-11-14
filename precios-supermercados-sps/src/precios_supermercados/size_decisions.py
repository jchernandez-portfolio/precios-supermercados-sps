"""Decisiones humanas de tamaño por GTIN (panel de revisión de Súper Compras).

Cuando varias cadenas publican tamaños distintos para el mismo código de barras
(p. ej. Cholula: 150 g, 150 ml y 5 oz), una persona revisa el producto en la
página del súper y fija el tamaño oficial. La decisión vive en la tabla
``size_decisions`` (no en los perfiles, que se recalculan cada día) y el motor la
aplica en cada refresco: las ofertas que coinciden con el tamaño oficial forman
el grupo comparable y las demás quedan aisladas. ``different_products`` marca un
código reutilizado: ese GTIN no se agrupa.

Motor v2.10 ("corrige con evidencia"): cada decisión puede traer la lectura de la
foto de cada oferta (``evidence``, tabla derivada ``size_offer_evidence``) y un
alcance (``apply``: ``evidence`` por omisión o ``all``). El motor la usa para
corregir el tamaño de las ofertas cuyo título dice otra cosa (ver
``product_identity_v2.size_evidence_plan``).

Este módulo es puro (sin red): valida el JSON del panel, normaliza unidades y
decide si una presentación coincide con el tamaño oficial.
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
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
# Decisiones automáticas: sólo los motores de esta lista (nunca llegan por el panel).
ENGINE_ACTORS = frozenset({"gemini_auto", "claude_auto", "claude_sesion"})
# Claude en sesión: lo propone la IA pero sólo se guarda con la aprobación de una persona.
APPROVED_ENGINES = frozenset({"claude_sesion"})
# Evidencia por oferta (v2.10): lo leído en la foto de cada súper.
EVIDENCE_TABLE = "size_offer_evidence"
EVIDENCE_STORES = ("la_colonia", "colonial", "walmart", "pricesmart", "comisariato_los_andes", "paiz")
EVIDENCE_SOURCES = ("human", "claude_sesion", "claude_auto", "gemini_auto")
APPLY_SCOPES = ("evidence", "all")
MAX_EVIDENCE_TEXT = 60
# Fila de alcance: ``apply: "all"`` se guarda como una fila del GTIN con este súper y sin valor.
APPLY_ALL_STORE = "*"
APPLY_ALL_TEXT = "apply:all"
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
_STORE_LIST = ",".join(f"'{store}'" for store in (APPLY_ALL_STORE, *EVIDENCE_STORES))
_SOURCE_LIST = ",".join(f"'{source}'" for source in EVIDENCE_SOURCES)
EVIDENCE_TABLE_SQL = f"""CREATE TABLE IF NOT EXISTS {EVIDENCE_TABLE} (
    canonical_gtin TEXT NOT NULL CHECK (length(canonical_gtin) = 14),
    supermarket_id TEXT NOT NULL CHECK (supermarket_id IN ({_STORE_LIST})),
    value TEXT,
    unit TEXT CHECK (unit IS NULL OR unit IN ('g','ml','oz','unit')),
    source TEXT NOT NULL CHECK (source IN ({_SOURCE_LIST})),
    text TEXT CHECK (text IS NULL OR length(text) <= {MAX_EVIDENCE_TEXT}),
    decided_at_utc TEXT NOT NULL,
    PRIMARY KEY (canonical_gtin, supermarket_id),
    CHECK ((supermarket_id = '{APPLY_ALL_STORE}') = (value IS NULL AND unit IS NULL))
) STRICT"""
EVIDENCE_SELECT_SQL = (
    f"SELECT canonical_gtin,supermarket_id,value,unit,source,text,decided_at_utc FROM {EVIDENCE_TABLE} "
    "ORDER BY canonical_gtin,supermarket_id"
)
SELECT_SQL = (
    f"SELECT canonical_gtin,decision,net_content_value,net_content_unit,declared_value,declared_unit,"
    f"note,decided_by,decided_at_utc FROM {TABLE_NAME} ORDER BY canonical_gtin"
)


class SizeDecisionError(ValueError):
    """El JSON del panel o una fila guardada no cumple el contrato."""


@dataclass(frozen=True, slots=True)
class OfferEvidence:
    """Contenido neto leído en la foto de la oferta de un súper (unidad canónica)."""

    supermarket_id: str
    value: Decimal
    unit: str
    source: str
    text: str | None = None
    decided_at_utc: str = ""

    def __post_init__(self) -> None:
        if self.supermarket_id not in EVIDENCE_STORES:
            raise SizeDecisionError("size_evidence_store_invalid")
        if self.unit not in CANONICAL_UNITS or not self.value.is_finite() or self.value <= 0:
            raise SizeDecisionError("size_evidence_value_invalid")
        if self.source not in EVIDENCE_SOURCES:
            raise SizeDecisionError("size_evidence_source_invalid")
        if self.text is not None and len(self.text) > MAX_EVIDENCE_TEXT:
            raise SizeDecisionError("size_evidence_text_too_long")

    @property
    def signature(self) -> PresentationSignature:
        return PresentationSignature(
            dimension=_DIMENSION[self.unit], total_base=self.value, pack_count=1, unit_amount_base=self.value,
        )


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
    # v2.10: lectura de la foto por súper y alcance (``evidence`` | ``all``).
    evidence: tuple[OfferEvidence, ...] = ()
    apply: str = "evidence"
    # ``True`` si el envío trajo ``evidence`` o ``apply``: al guardarlo reemplaza la evidencia del GTIN.
    evidence_given: bool = False

    def __post_init__(self) -> None:
        if self.apply not in APPLY_SCOPES:
            raise SizeDecisionError("size_decision_apply_invalid")
        if self.decision != "size" and (self.evidence or self.apply != "evidence"):
            raise SizeDecisionError("size_decision_fields_invalid")
        if len({item.supermarket_id for item in self.evidence}) != len(self.evidence):
            raise SizeDecisionError("size_evidence_store_duplicate")
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


def parse_payload(text: str, *, decided_by: str, decided_at_utc: str, engine: bool = False) -> list[SizeDecision]:
    """JSON del panel (o de un motor) → decisiones válidas. Falla cerrado ante cualquier dato raro.

    Con ``engine=True`` el autor es un motor de ``ENGINE_ACTORS`` y queda como
    ``engine:<motor>``; si no, una cuenta de GitHub, ``human:<cuenta>``.
    """
    if not isinstance(text, str) or not text.strip():
        raise SizeDecisionError("size_decisions_empty")
    if len(text) > MAX_PAYLOAD_CHARS:
        raise SizeDecisionError("size_decisions_too_large")
    if engine and decided_by not in ENGINE_ACTORS:
        raise SizeDecisionError("size_decisions_actor_invalid")
    if not engine and not _ACTOR.fullmatch(decided_by or ""):
        raise SizeDecisionError("size_decisions_actor_invalid")
    author = f"engine:{decided_by}" if engine else f"human:{decided_by}"
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
        if not isinstance(item, Mapping) or set(item) - {"gtin", "decision", "value", "unit", "note", "evidence",
                                                         "apply"}:
            raise SizeDecisionError("size_decision_fields_invalid")
        gtin = canonicalize_gtin(str(item.get("gtin") or "").strip() or None)
        if gtin is None:
            raise SizeDecisionError("size_decision_gtin_invalid")
        if gtin in seen:
            raise SizeDecisionError("size_decision_gtin_duplicate")
        seen.add(gtin)
        kind = item.get("decision")
        note = _clean_text(item.get("note"))
        common = {"canonical_gtin": gtin, "note": note, "decided_by": author,
                  "decided_at_utc": decided_at_utc}
        if kind == "different_products":
            if any(key in item for key in ("evidence", "apply")) or item.get("value") is not None \
                    or item.get("unit") is not None:
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
        apply = item.get("apply", "evidence")
        if apply not in APPLY_SCOPES:
            raise SizeDecisionError("size_decision_apply_invalid")
        source = decided_by if engine else "human"
        evidence = _parse_evidence(item.get("evidence", []), source=source, decided_at_utc=decided_at_utc)
        result.append(SizeDecision(
            decision=kind, net_content_value=total.normalize(), net_content_unit=canonical_unit,
            declared_value=value.normalize(), declared_unit=unit, evidence=evidence, apply=apply,
            evidence_given="evidence" in item or "apply" in item, **common,
        ))
    return result


def _evidence_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SizeDecisionError("size_evidence_text_invalid")
    text = " ".join(unicodedata.normalize("NFC", value).split())
    if any(unicodedata.category(char).startswith("C") for char in text):
        raise SizeDecisionError("size_evidence_text_invalid")
    if len(text) > MAX_EVIDENCE_TEXT:
        raise SizeDecisionError("size_evidence_text_too_long")
    return text or None


def _parse_evidence(items: object, *, source: str, decided_at_utc: str) -> tuple[OfferEvidence, ...]:
    """``evidence`` del envío → lecturas por súper (lista cerrada, un súper una vez, mismos límites)."""
    if not isinstance(items, list) or len(items) > len(EVIDENCE_STORES):
        raise SizeDecisionError("size_evidence_invalid")
    result: list[OfferEvidence] = []
    for entry in items:
        if not isinstance(entry, Mapping) or set(entry) - {"store", "value", "unit", "text"} \
                or not {"store", "value", "unit"} <= set(entry):
            raise SizeDecisionError("size_evidence_invalid")
        store, unit = entry.get("store"), entry.get("unit")
        if store not in EVIDENCE_STORES:
            raise SizeDecisionError("size_evidence_store_invalid")
        if unit not in DECLARED_UNITS:
            raise SizeDecisionError("size_evidence_unit_invalid")
        value = _decimal(entry.get("value"))
        if value > MAX_VALUE[unit] or (unit == "unit" and value != value.to_integral_value()):
            raise SizeDecisionError("size_evidence_value_out_of_range")
        total, canonical_unit = canonical_size(value, unit)
        result.append(OfferEvidence(
            supermarket_id=str(store), value=total.normalize(), unit=canonical_unit, source=source,
            text=_evidence_text(entry.get("text")), decided_at_utc=decided_at_utc,
        ))
    if len({item.supermarket_id for item in result}) != len(result):
        raise SizeDecisionError("size_evidence_store_duplicate")
    return tuple(result)


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


def attach_evidence(
    decisions: Mapping[str, SizeDecision], rows: Iterable[Sequence[object]]
) -> dict[str, SizeDecision]:
    """Decisiones + filas de ``size_offer_evidence`` (sólo a las decisiones de tamaño)."""
    readings: dict[str, list[OfferEvidence]] = {}
    apply_all: set[str] = set()
    for gtin, store, value, unit, source, text, decided_at in rows:
        gtin = str(gtin)
        if store == APPLY_ALL_STORE:
            apply_all.add(gtin)
            continue
        readings.setdefault(gtin, []).append(OfferEvidence(
            supermarket_id=str(store), value=Decimal(str(value)), unit=str(unit), source=str(source),
            text=None if text is None else str(text), decided_at_utc=str(decided_at),
        ))
    result = dict(decisions)
    for gtin, decision in decisions.items():
        if decision.decision != "size" or (gtin not in readings and gtin not in apply_all):
            continue
        result[gtin] = replace(
            decision,
            evidence=tuple(sorted(readings.get(gtin, ()), key=lambda item: item.supermarket_id)),
            apply="all" if gtin in apply_all else "evidence",
        )
    return result


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


def _mass_volume_close(left: PresentationSignature, right: PresentationSignature, tolerance: Decimal) -> bool:
    """g contra ml (motor v2.9, lácteos/jugos/salsas): mismo número de unidades ± ``tolerance``."""
    if {left.dimension, right.dimension} != {"mass_g", "volume_ml"}:
        return False
    larger = max(left.total_base, right.total_base)
    return larger > 0 and abs(left.total_base - right.total_base) / larger <= tolerance


def match_official(
    official: PresentationSignature,
    presentation: PresentationSignature | None,
    *,
    mass_volume_tolerance: Decimal | None = None,
) -> tuple[bool, str | None]:
    """¿La presentación de una oferta coincide con el tamaño oficial? Y, si era onza, en qué lectura.

    Compara el contenido total (un paquete de 3 × 100 g coincide con 300 g) con la
    tolerancia imperial (2 % o 1.5 unidades). Una oferta sin presentación no
    contradice al oficial: se queda en el grupo. Con ``mass_volume_tolerance``
    (el motor la pasa sólo en grupos de lácteos, jugos y salsas: regla g ≈ ml de
    v2.9, 5 %) un oficial en g coincide con una oferta en ml y viceversa; las
    onzas no entran en esa equivalencia.
    """
    if presentation is None:
        return True, None
    for mine, reading in _readings(presentation):
        for theirs, _ in _readings(official):
            if mine.dimension == theirs.dimension and mine.dimension != "count" and _close(mine.total_base, theirs.total_base):
                return True, reading
            if mine.dimension == theirs.dimension == "count" and mine.total_base == theirs.total_base:
                return True, None
    if mass_volume_tolerance is not None and _mass_volume_close(presentation, official, mass_volume_tolerance):
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


# ------------------------------------------------- lectura de texto de una foto

_FLUID_OUNCE_ML = Decimal("29.5735295625")
_UNIT_WORDS = (
    (r"fl\.?\s*oz", "floz"), (r"kg|kilos?|kilogramos?", "kg"), (r"mg", "mg"),
    (r"g|gr|grs|gramos?", "g"), (r"ml|mililitros?", "ml"), (r"l|lt|lts|litros?", "l"),
    (r"oz|onzas?", "oz"), (r"lb|lbs|libras?", "lb"),
)
_UNIT_RE = "|".join(f"(?:{pattern})" for pattern, _ in _UNIT_WORDS)
_MEASURE = re.compile(rf"(?<![\w.,])(\d+(?:[.,]\d+)?)\s*({_UNIT_RE})(?![a-z])")
# "5 sobres x 10 g", "6 pack x 200 ml", "2 x 115 g": N envases de X cada uno.
_PACK = re.compile(rf"(?<![\w.,])(\d{{1,3}})\s*(?:[a-z]+\s*)?x\s*(\d+(?:[.,]\d+)?)\s*({_UNIT_RE})(?![a-z])")
# Lecturas que no dicen el contenido neto de lo que se vende: vista parcial, banner,
# "c/u", una sola unidad del paquete u otro diseño del empaque.
_NOT_NET = re.compile(r"parcial|banner|c/u|cada\s+un|\(1\s+(?:sobre|unidad|bolsita|lata|botella)\)|otro\s+dise")
# El peso escurrido no es el contenido neto: se descarta antes de leer.
_DRAINED = re.compile(r"(?:esc\.?|escurrido|drained)\s*\d+(?:[.,]\d+)?\s*[a-z]*")
_METRIC = frozenset({"g", "kg", "mg", "ml", "l"})


def _unit_name(word: str) -> str:
    return next(name for pattern, name in _UNIT_WORDS if re.fullmatch(pattern, word))


def _to_base(word: str, number: str) -> tuple[str, Decimal, bool]:
    """(unidad canónica, valor, métrica) de una medida del texto."""
    unit, value = _unit_name(word), Decimal(number.replace(",", "."))
    factor = {"kg": ("g", Decimal(1000)), "mg": ("g", Decimal("0.001")), "l": ("ml", Decimal(1000)),
              "floz": ("ml", _FLUID_OUNCE_ML), "lb": ("g", _POUND_TO_GRAMS)}.get(unit)
    canonical, value = (factor[0], value * factor[1]) if factor else (unit, value)
    return canonical, value, unit in _METRIC


def evidence_from_text(text: object) -> tuple[Decimal, str] | None:
    """Texto leído en una foto ("8.5 oz (240 g)", "5 sobres × 10 g") → (valor, unidad canónica).

    Sólo cuando es inequívoco; si no, ``None`` (esa oferta queda sin evidencia).
    Prefiere las medidas métricas (las imperiales sólo si no hay ninguna), un
    paquete "N × X" cuenta su total, descarta el peso escurrido y rechaza vistas
    parciales, banners, "c/u" y lecturas de una sola unidad del paquete.
    """
    if not isinstance(text, str):
        return None
    folded = unicodedata.normalize("NFKD", text.casefold())
    folded = "".join(char for char in folded if not unicodedata.combining(char)).replace("×", "x")
    if _NOT_NET.search(folded):
        return None
    folded = _DRAINED.sub(" ", folded)
    candidates: list[tuple[str, Decimal, bool]] = []
    counts: list[int] = []
    covered: list[tuple[int, int]] = []
    for match in _PACK.finditer(folded):
        count = int(match.group(1))
        if count < 2:
            continue
        unit, value, metric = _to_base(match.group(3), match.group(2))
        candidates.append((unit, value * count, metric))
        counts.append(count)
        covered.append(match.span())
    for match in _MEASURE.finditer(folded):
        if any(start <= match.start() < end for start, end in covered):
            continue
        candidates.append(_to_base(match.group(2), match.group(1)))
    if not candidates:
        return None
    chosen = [item for item in candidates if item[2]] or candidates
    if len({unit for unit, _, _ in chosen}) != 1:
        return None
    unit = chosen[0][0]
    values = sorted(value for _, value, _ in chosen)
    total, smallest = values[-1], values[0]
    if not all(_close(value, total) for value in values):
        # "10 × 6 fl oz (177 ml) · 1.77 L": la medida chica es la de cada envase.
        if any(not (_close(value, total) or _close(value, smallest)) for value in values):
            return None
        if not any(_close(smallest * count, total) for count in counts):
            return None
    total = total.quantize(Decimal("0.01")).normalize()
    if total == total.to_integral_value():
        total = total.quantize(Decimal(1))
    if total <= 0 or total > MAX_VALUE[unit] or (unit == "unit" and total != total.to_integral_value()):
        return None
    return total, unit
