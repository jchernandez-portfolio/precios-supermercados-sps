"""Medición del "PR B" (sólo lectura; NO cambia el motor): g ≈ ml y peso sobre conteo.

Igual que la medición de la regla de onzas, corre la resolución REAL de los
grupos GTIN (``resolve_gtin_group`` del motor v2.8, con las decisiones de
``size_decisions``) y compara grupo por grupo la resolución vigente contra la
de cada variante candidata. Las variantes se simulan sobre los datos sin tocar
el motor: mientras se resuelve un grupo, el conflicto de tamaño entre dos
ofertas (``cross_source_presentation_conflict``) se descarta si la regla
candidata lo explica; todo lo demás (tipo, sabor, colisiones, multipack
ambiguo, decisiones humanas...) sigue igual.

Reglas candidatas:

- **A · g ≈ ml**: en lácteos, jugos y salsas (categoría del súper o del árbol
  que contenga ``yogurt``, ``leche``, ``queso``, ``crema``, ``jugo``,
  ``nectar``, ``salsa``, ``aderezo``, ``mayonesa`` o ``ketchup``) g y ml con el
  mismo número de unidades valen lo mismo si difieren ≤ 5 % (también ≤ 2 % y
  "todas las categorías"). Estándar: gramos.
- **B · peso sobre conteo**: una oferta que sólo publica un conteo ("6
  unidades") no contradice a otra con peso/volumen; el peso es el tamaño y el
  conteo va a ``pack_count``. ``amplia``: cualquier conteo. ``media``: conteo 1,
  mismo pack, o el título de la oferta con peso menciona ese conteo ("50
  bolsas"). ``segura``: sólo conteo 1 o mismo pack.

"Nunca empeorar": si con la variante un grupo comparable queda con menos
cadenas, se bloquea o pierde alguna oferta, se conserva la resolución vigente
(se cuenta aparte). Después se verifica que ningún grupo empeora.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal

from precios_supermercados import product_identity_v2 as identity
from precios_supermercados.product_homologation import ProductProfile, fold_text

SCHEMA = "rpi-medicion-pr-b/v1"
SIZE_CONFLICT = "cross_source_presentation_conflict"
GML_KEYWORDS = ("yogurt", "leche", "queso", "crema", "jugo", "nectar", "salsa", "aderezo", "mayonesa", "ketchup")
WEIGHT_DIMENSIONS = frozenset({"mass_g", "volume_ml", "ounce"})
# Qué tipos de conteo acepta cada variante de B. ``conteo_sin_respaldo`` con más de
# una unidad es el caso riesgoso: paquete contra unidad suelta (Caprisun 10 vs 177 ml).
COUNT_KINDS = {
    "segura": frozenset({"conteo_1", "mismo_pack"}),
    "media": frozenset({"conteo_1", "mismo_pack", "titulo_menciona_conteo"}),
    "amplia": frozenset({"conteo_1", "mismo_pack", "titulo_menciona_conteo", "conteo_sin_respaldo"}),
}
RISKY_KIND = "conteo_sin_respaldo"
MAX_EXAMPLES = 10
MAX_RISKY_EXAMPLES = 5
OWNER_REFERENCE = {  # aproximada, del dueño: grupos que mejoran
    "A_g_ml_5pct_categorias": 30,
    "B_conteo_media": 70,
    "B_conteo_amplia": 174,
    "A5_mas_B_media": 100,
}
CSV_COLUMNS = (
    "variante", "gtin", "cambio", "reglas", "riesgo", "supermercado", "product_id", "nombre", "categoria",
    "presentacion", "presentacion_estandar", "rol_antes", "rol_con_variante",
)
_COUNT_WORDS = (
    "unidades", "unidad", "unids", "unid", "und", "uds", "ud", "un", "u", "bolsas", "bolsitas", "bolsa", "sobres",
    "sobre", "saquitos", "piezas", "pieza", "pzas", "pza", "pzs", "pz", "rollos", "tabletas", "capsulas", "ct",
    "count", "pack", "pk", "paquetes", "latas", "botellas", "barras", "cajitas", "cajas", "toallitas", "toallas",
    "panales", "porciones", "rebanadas", "tortillas", "filtros",
)
_COUNT_WORDS_RE = "|".join(_COUNT_WORDS)


@dataclass(frozen=True, slots=True)
class Variant:
    name: str
    description: str
    gml_tolerance: Decimal | None = None
    gml_all_categories: bool = False
    count_mode: str | None = None


VARIANTS = (
    Variant("A_g_ml_5pct_categorias", "g ≈ ml ≤ 5 % en lácteos, jugos y salsas", Decimal("0.05")),
    Variant("A_g_ml_2pct_categorias", "g ≈ ml ≤ 2 % en lácteos, jugos y salsas", Decimal("0.02")),
    Variant("A_g_ml_5pct_todas", "g ≈ ml ≤ 5 % en todas las categorías", Decimal("0.05"), True),
    Variant("B_conteo_amplia", "peso sobre conteo: cualquier conteo", count_mode="amplia"),
    Variant("B_conteo_media", "peso sobre conteo: conteo 1, mismo pack o el título menciona el conteo",
            count_mode="media"),
    Variant("B_conteo_segura", "peso sobre conteo: sólo conteo 1 o mismo pack", count_mode="segura"),
    Variant("A5_mas_B_segura", "g ≈ ml ≤ 5 % (categorías) + conteo seguro", Decimal("0.05"), count_mode="segura"),
    Variant("A5_mas_B_media", "g ≈ ml ≤ 5 % (categorías) + conteo medio", Decimal("0.05"), count_mode="media"),
    Variant("A5_mas_B_amplia", "g ≈ ml ≤ 5 % (categorías) + conteo amplio", Decimal("0.05"), count_mode="amplia"),
)


# ---------------------------------------------------------------- reglas

def gml_keyword(profile: ProductProfile) -> str | None:
    """Primera palabra clave de lácteos/jugos/salsas en la categoría del súper o del árbol."""
    texts = (profile.record.source_category, profile.taxonomy.category, profile.taxonomy.subcategory,
             profile.taxonomy.product_type)
    folded = " ".join(fold_text(text) or "" for text in texts)
    return next((keyword for keyword in GML_KEYWORDS if keyword in folded), None)


def gml_kind(left: ProductProfile, right: ProductProfile, tolerance: Decimal, all_categories: bool) -> str | None:
    """``g_ml`` si una oferta en g y otra en ml (mismo pack) difieren ≤ ``tolerance``."""
    p, q = left.presentation, right.presentation
    if p is None or q is None or {p.dimension, q.dimension} != {"mass_g", "volume_ml"}:
        return None
    if p.pack_count != q.pack_count:
        return None
    larger = max(p.total_base, q.total_base)
    if larger <= 0 or abs(p.total_base - q.total_base) / larger > tolerance:
        return None
    if all_categories or gml_keyword(left) or gml_keyword(right):
        return "g_ml"
    return None


def _plain(text: str | None) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(char for char in decomposed if not unicodedata.combining(char)).lower()


def mentions_count(title: str | None, count: int) -> bool:
    """¿El título dice ese conteo? "Té 50 bolsas 100 g", "Leche 200 ml x 6", "Pack de 6"."""
    text = _plain(title)
    number = re.escape(str(count))
    after = rf"(?<![\d.,]){number}\s*-?\s*(?:{_COUNT_WORDS_RE})(?![a-z])"
    before = rf"(?<![a-z])(?:pack|paquete|caja|x)\s*(?:de\s*)?{number}(?![\d.,])(?!\s*(?:g|gr|grs|ml|kg|l|lt|oz)\b)"
    return bool(re.search(after, text) or re.search(before, text))


def count_kind(left: ProductProfile, right: ProductProfile) -> str | None:
    """Tipo de par conteo contra peso/volumen (``None`` si no es ese par)."""
    for counted, weighed in ((left, right), (right, left)):
        c, w = counted.presentation, weighed.presentation
        if c is None or w is None or c.dimension != "count" or w.dimension not in WEIGHT_DIMENSIONS:
            continue
        if c.total_base != c.total_base.to_integral_value():
            return None
        units = int(c.total_base)
        if units == 1:
            return "conteo_1"
        if w.pack_count > 1 and units == w.pack_count:
            return "mismo_pack"
        if mentions_count(weighed.record.source_name, units):
            return "titulo_menciona_conteo"
        return RISKY_KIND
    return None


def explain(variant: Variant, left: ProductProfile, right: ProductProfile) -> str | None:
    """Regla de la variante que explica el desacuerdo de tamaño entre dos ofertas."""
    if variant.gml_tolerance is not None:
        kind = gml_kind(left, right, variant.gml_tolerance, variant.gml_all_categories)
        if kind:
            return kind
    if variant.count_mode is not None:
        kind = count_kind(left, right)
        if kind in COUNT_KINDS[variant.count_mode]:
            return kind
    return None


@contextmanager
def engine_variant(variant: Variant) -> Iterator[None]:
    """El motor real, con el conflicto de tamaño que la variante explica descartado."""
    original = identity._gtin_pair_conflicts

    def pair(left: ProductProfile, right: ProductProfile, *, ounce_rule: bool = False) -> set[str]:
        reasons = original(left, right, ounce_rule=ounce_rule)
        if SIZE_CONFLICT in reasons and explain(variant, left, right):
            reasons.discard(SIZE_CONFLICT)
        return reasons

    identity._gtin_pair_conflicts = pair
    try:
        yield
    finally:
        identity._gtin_pair_conflicts = original


# ------------------------------------------------------------- medición

def _ids(members: Iterable[ProductProfile]) -> set[str]:
    return {member.record.source_record_id for member in members}


def _chains(members: Iterable[ProductProfile]) -> int:
    return len({member.record.supermarket_id for member in members})


def _size_out(excluded: Mapping[str, Iterable[str]]) -> bool:
    return any(SIZE_CONFLICT in reasons for reasons in excluded.values())


def worsening(baseline: Mapping[str, object], remaining: Sequence[ProductProfile]) -> str | None:
    """"Nunca empeorar": ``menos_cadenas`` (incluye bloquearse) o ``pierde_ofertas``; si no, ``None``."""
    if baseline["status"] != "ready":
        return None  # un grupo bloqueado no puede empeorar
    if _chains(remaining) < max(2, _chains(baseline["remaining"])):
        return "menos_cadenas"
    if not _ids(baseline["remaining"]) <= _ids(remaining):
        return "pierde_ofertas"
    return None


def _applied(variant: Variant, members: Sequence[ProductProfile]) -> dict[str, set[str]]:
    """Regla aplicada → ofertas que tocó, en el grupo comparable final."""
    applied: dict[str, set[str]] = {}
    for index, left in enumerate(members):
        for right in members[index + 1:]:
            if left.record.supermarket_id == right.record.supermarket_id:
                continue
            if SIZE_CONFLICT not in identity._gtin_pair_conflicts(left, right):
                continue
            kind = explain(variant, left, right)
            if kind:
                applied.setdefault(kind, set()).update({left.record.source_record_id, right.record.source_record_id})
    return applied


def _presentation_text(profile: ProductProfile) -> str | None:
    value = profile.presentation
    if value is None:
        return None
    total = format(value.total_base.normalize(), "f")
    return f"{value.pack_count}×{total} {value.dimension}" if value.pack_count > 1 else f"{total} {value.dimension}"


def _standard_text(profile: ProductProfile, applied: Mapping[str, set[str]]) -> str | None:
    source_id = profile.record.source_record_id
    value = profile.presentation
    if value is None:
        return None
    if value.dimension == "volume_ml" and source_id in applied.get("g_ml", ()):
        return f"{format(value.total_base.normalize(), 'f')} g (de ml)"
    if value.dimension == "count" and any(source_id in ids for kind, ids in applied.items() if kind != "g_ml"):
        return f"pack_count={int(value.total_base)} (tamaño = peso del grupo)"
    return None


def classify(baseline: Mapping[str, object], remaining: Sequence[ProductProfile],
             excluded: Mapping[str, Iterable[str]]) -> str | None:
    """Cambio de un grupo que mejora (``None`` si queda igual)."""
    after_in = _ids(remaining)
    if baseline["status"] != "ready":
        if _chains(remaining) < 2:
            return None
        return "empate_a_mayoria" if _size_out(excluded) else "empate_a_listo"
    if after_in == _ids(baseline["remaining"]):
        return None
    if _size_out(baseline["excluded"]):
        return "atipico_recupera_parcial" if _size_out(excluded) else "atipico_a_listo"
    return "listo_recupera_otras"


def measure_variant(variant: Variant, groups: Sequence[Mapping[str, object]],
                    product_of: Mapping[str, int]) -> tuple[dict[str, object], list[dict[str, object]]]:
    changes: Counter = Counter()
    rules: Counter = Counter()
    keywords: Counter = Counter()
    guard: Counter = Counter()
    entering = touched = affected = risky_groups = decided = worse_after_guard = 0
    examples: list[dict[str, object]] = []
    lines: list[dict[str, object]] = []
    for group in groups:
        if group.get("decision") is not None:
            decided += 1  # la decisión humana/IA manda: la variante no aplica
            continue
        gtin = str(group["gtin"])
        with engine_variant(variant):
            (remaining, excluded, _), _ = identity.resolve_gtin_group(gtin, list(group["members"]))
        reason = worsening(group, remaining)
        if reason:
            guard[reason] += 1
            remaining, excluded = list(group["remaining"]), group["excluded"]
        if worsening(group, remaining):
            worse_after_guard += 1
        change = classify(group, remaining, excluded)
        if change is None:
            continue
        changes[change] += 1
        before_in, after_in = _ids(group["remaining"]), _ids(remaining)
        entering += len(after_in - before_in)
        applied = _applied(variant, list(remaining))
        touched_ids = set().union(*applied.values()) if applied else set()
        touched += len(touched_ids)
        affected += len((after_in - before_in) | touched_ids)
        rules.update(applied.keys())
        risky = RISKY_KIND in applied and any(
            m.presentation is not None and m.presentation.dimension == "count" and m.presentation.total_base > 1
            and m.record.source_record_id in applied[RISKY_KIND] for m in remaining)
        risky_groups += risky
        if "g_ml" in applied:
            found = {gml_keyword(m) for m in remaining if m.record.source_record_id in applied["g_ml"]} - {None}
            keywords[min(found) if found else "otra_categoria"] += 1
        offers = []
        for member in sorted(group["members"], key=lambda m: m.record.source_record_id):
            source_id = member.record.source_record_id
            offers.append({
                "supermercado": member.record.supermarket_id, "product_id": product_of.get(source_id),
                "nombre": member.record.source_name, "categoria": member.record.source_category,
                "presentacion": _presentation_text(member), "presentacion_estandar": _standard_text(member, applied),
                "rol_antes": "comparable" if source_id in before_in else "fuera",
                "rol_con_variante": "comparable" if source_id in after_in else "fuera",
            })
        example = {"gtin": gtin, "cambio": change, "reglas": sorted(applied), "riesgo": bool(risky), "ofertas": offers}
        examples.append(example)
        for offer in offers:
            lines.append({"variante": variant.name, "gtin": gtin, "cambio": change, "reglas": " + ".join(sorted(applied)),
                          "riesgo": "paquete_vs_unidad" if risky else "", **offer})
    unblocked = changes["empate_a_listo"] + changes["empate_a_mayoria"]
    summary = {
        "descripcion": variant.description,
        "grupos_que_mejoran": sum(changes.values()),
        "grupos_desbloqueados": unblocked,
        "empate_a_listo": changes["empate_a_listo"],
        "empate_a_mayoria": changes["empate_a_mayoria"],
        "atipico_a_listo": changes["atipico_a_listo"],
        "atipico_recupera_parcial": changes["atipico_recupera_parcial"],
        "listo_recupera_otras": changes["listo_recupera_otras"],
        "ofertas_que_entran_a_comparar": entering,
        "ofertas_con_regla_aplicada": touched,
        "ofertas_afectadas": affected,
        "reglas_aplicadas_por_grupo": dict(sorted(rules.items())),
        "palabras_clave_g_ml": dict(sorted(keywords.items())),
        "grupos_riesgosos_paquete_vs_unidad": risky_groups,
        "nunca_empeorar": {
            "grupos_que_empeorarian_sin_guarda": sum(guard.values()),
            "menos_cadenas_o_bloqueo": guard["menos_cadenas"],
            "pierden_ofertas": guard["pierde_ofertas"],
            "grupos_que_empeoran_con_guarda": worse_after_guard,
            "verificado_ninguno_empeora": worse_after_guard == 0,
        },
        "grupos_con_decision_omitidos": decided,
        "ejemplos": pick_examples(examples),
    }
    return summary, lines


def pick_examples(examples: Sequence[Mapping[str, object]]) -> list[Mapping[str, object]]:
    """Hasta 10: primero los riesgosos (máx. 5), luego el resto, en orden de GTIN."""
    risky = [example for example in examples if example["riesgo"]]
    safe = [example for example in examples if not example["riesgo"]]
    chosen = risky[:MAX_RISKY_EXAMPLES] + safe[:MAX_EXAMPLES - min(len(risky), MAX_RISKY_EXAMPLES)]
    chosen += risky[MAX_RISKY_EXAMPLES:MAX_RISKY_EXAMPLES + MAX_EXAMPLES - len(chosen)]
    return chosen[:MAX_EXAMPLES]


def measure_pr_b(groups: Sequence[Mapping[str, object]], product_of: Mapping[str, int],
                 variants: Sequence[Variant] = VARIANTS) -> dict[str, object]:
    """Todas las variantes sobre los grupos GTIN vigentes (≥ 2 cadenas, con decisiones)."""
    results: dict[str, object] = {}
    lines: list[dict[str, object]] = []
    for variant in variants:
        summary, variant_lines = measure_variant(variant, groups, product_of)
        results[variant.name] = summary
        lines.extend(variant_lines)
    return {
        "schema": SCHEMA,
        "motor": "product_identity_v2 vigente, sin cambios (variantes simuladas al resolver cada grupo)",
        "base": "resolución vigente con regla de onzas y decisiones de size_decisions",
        "grupos_evaluados": len(groups),
        "resumen": {name: {"grupos_que_mejoran": result["grupos_que_mejoran"],
                           "grupos_desbloqueados": result["grupos_desbloqueados"],
                           "riesgosos": result["grupos_riesgosos_paquete_vs_unidad"],
                           "empeoran": result["nunca_empeorar"]["grupos_que_empeoran_con_guarda"]}
                    for name, result in results.items()},
        "referencia_aproximada_del_dueno": OWNER_REFERENCE,
        "variantes": results,
        "_lineas": lines,
    }
