#!/usr/bin/env python3
"""Paso 0: medición de reglas de identidad (sólo lectura; no cambia ninguna regla).

Recalcula en memoria, con el mismo código que usa producción
(``profile_product_v2`` + ``resolve_gtin_group``, igual que
``build_homologation_rows``), los grupos GTIN exactos y responde:

1. cuántos grupos exactos (GTIN en ≥ 2 cadenas) quedan bloqueados
   (``review_required``) o con miembros excluidos, por cada motivo;
2. simulación del paso 1: cuántas tarjetas del catálogo público se unen si las
   sucursales del mismo producto (``súper:product_id``) comparten tarjeta;
3. simulación del paso 3: cuántos grupos/maestros pasan a agruparse si una
   oferta con conflicto SÓLO de tamaño se queda en la tarjeta (aislada del
   precio por unidad) en vez de salir de ella. Separa los casos con mayoría de
   tamaño de los empates (p. ej. 1 contra 1), que necesitan una decisión.

Aplica las decisiones de tamaño (tabla ``size_decisions``: humanas y de IA)
igual que el refresco de homologación, si la tabla existe; sin ellas los GTIN
decididos darían diferencias falsas contra producción. Antes de medir verifica
que el estado recalculado coincide con el guardado en
``product_homologation_profiles``. Sólo ejecuta SELECT (envoltorio ``ReadOnly``
de la auditoría de identidad); deja ``medicion-reglas.json`` y
``grupos-reglas.csv`` en ``--output-dir``. Con ``--pr-b`` mide además, sobre los
mismos grupos, las reglas candidatas del PR B (``medir_pr_b.py``: g ≈ ml y peso
sobre conteo) y deja ``medicion-pr-b.json`` y ``pr-b-grupos.csv``. Desde el motor
v2.9 la variante elegida ya está aplicada: el PR B y la regla de onzas se miden
contra la base sin las reglas v2.9 (``size_rules=False``) y el PR B verifica que
el motor coincide con su simulación (``motor_vigente.ya_aplicada``).

Motor v2.10 ("corrige con evidencia"): lee también ``size_offer_evidence`` (si
existe) y lista en ``correccion_con_evidencia`` (``medicion-reglas.json``) y en
``evidencia-grupos.csv`` las fichas decididas que cambian por la evidencia de foto:
por GTIN, el modo (mayoría, propia, empate), las ofertas corregidas (con lo que
publica el súper), las contradichas y las que siguen aisladas sin evidencia (y si
``apply: "all"`` las corregiría).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from auditar_identidad_productos import (  # noqa: E402
    CATALOG_BASE,
    AuditError,
    CatalogRow,
    ReadOnly,
    _http_json,
    load_cities,
)
from medir_pr_b import CSV_COLUMNS as PR_B_CSV_COLUMNS  # noqa: E402
from medir_pr_b import measure_pr_b  # noqa: E402
from precios_supermercados.pricesmart_specs_persistence import PROFILE_ATTRIBUTES_SQL  # noqa: E402
from precios_supermercados.pricesmart_specs_persistence import TABLE_NAME as SPECS_TABLE  # noqa: E402
from precios_supermercados.product_homologation import (  # noqa: E402
    PresentationSignature,
    ProductProfile,
    ounces_as_mass,
    ounces_as_volume,
)
from precios_supermercados.product_homologation_persistence import (  # noqa: E402
    apply_source_spec_attributes,
    records_from_product_rows,
)
from precios_supermercados.product_identity_v2 import (  # noqa: E402
    SIZE_EVIDENCE_CONTRADICTED,
    _exact_groups,
    _resolve_gtin_members,
    build_brand_lexicon,
    candidate_presentations_compatible,
    canonical_presentation_from_signature,
    ounce_reading,
    profile_product_v2,
    resolve_gtin_group,
)
from precios_supermercados.size_decisions import (  # noqa: E402
    EVIDENCE_SELECT_SQL,
    EVIDENCE_TABLE,
    SizeDecision,
    attach_evidence,
    decision_from_row,
    index_decisions,
)
from precios_supermercados.size_decisions import SELECT_SQL as DECISIONS_SELECT_SQL  # noqa: E402
from precios_supermercados.size_decisions import TABLE_NAME as DECISIONS_TABLE  # noqa: E402

PAGE = 2000
# Motivos que el paso 3 trataría como "tamaño en conflicto": la oferta sigue en
# la tarjeta pero sin precio por unidad. Escenario B agrega multipack ambiguo
# (no recomendado: se mide sólo para dimensionar).
SIZE_REASONS = frozenset({"cross_source_presentation_conflict", "source_presentation_conflict"})
SIZE_REASONS_B = SIZE_REASONS | {"ambiguous_multipack_presentation"}
ASKED_REASONS = (
    "cross_source_presentation_conflict",
    "source_presentation_conflict",
    "product_type_conflict",
    "restricted_gtin_outside_shared_master",
    "ambiguous_multipack_presentation",
)
OUNCE_CSV_COLUMNS = (
    "gtin", "cambio", "base_onza", "supermercado", "product_id", "nombre", "presentacion",
    "presentacion_convertida", "rol_antes", "rol_con_regla",
)
EVIDENCE_CSV_COLUMNS = (
    "gtin", "decision", "modo", "supermercado", "product_id", "nombre", "publicado", "rol_sin_evidencia",
    "rol_con_evidencia", "rol_con_apply_all", "foto",
)
CSV_COLUMNS = (
    "gtin", "estado_actual", "estado_simulado", "tamano", "rol", "supermercado", "product_id",
    "nombre", "presentacion", "estado_presentacion", "motivos_miembro",
)


# ------------------------------------------------------------------ lectura

def fetch_all_products(db: ReadOnly) -> tuple[tuple[int, object], ...]:
    """Todos los productos, igual que el refresco de homologación (keyset por PK)."""
    expected = db.query("SELECT COUNT(*) FROM products")[0][0]
    records: list[tuple[int, object]] = []
    cursor = 0
    while True:
        rows = db.query(
            "SELECT product_id,supermarket_id,name,brand,presentation,category,ean "
            "FROM products WHERE product_id>? ORDER BY product_id LIMIT ?", (cursor, PAGE),
        )
        if not rows:
            break
        adapted = records_from_product_rows(tuple(tuple(row) for row in rows))
        records.extend(adapted)
        cursor = adapted[-1][0]
        if len(rows) < PAGE:
            break
    if len(records) != expected:
        raise AuditError(f"productos_incompletos:{expected}:{len(records)}")
    return tuple(records)


def fetch_spec_attributes(db: ReadOnly) -> dict[int, tuple[str | None, str | None]] | None:
    present = db.query("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (SPECS_TABLE,))
    if not present:
        return None
    return {int(pid): (brand, hint) for pid, brand, hint in db.query(PROFILE_ATTRIBUTES_SQL)}


def fetch_size_decisions(db: ReadOnly) -> dict[str, SizeDecision] | None:
    """Decisiones de tamaño (humanas y de IA) con su evidencia, como las lee el refresco. Sin tabla: ``None``."""
    present = {str(row[0]) for row in db.query(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?,?)", (DECISIONS_TABLE, EVIDENCE_TABLE))}
    if DECISIONS_TABLE not in present:
        return None
    decisions = index_decisions(decision_from_row(row) for row in db.query(DECISIONS_SELECT_SQL))
    if EVIDENCE_TABLE not in present:
        return decisions
    return attach_evidence(decisions, db.query(EVIDENCE_SELECT_SQL))


def decision_author(decision: SizeDecision) -> str:
    """``human`` (sin la cuenta) o ``engine:<motor>``."""
    return decision.decided_by if decision.decided_by.startswith("engine:") else "human"


def fetch_stored_status(db: ReadOnly) -> dict[int, str]:
    stored: dict[int, str] = {}
    cursor = 0
    while True:
        rows = db.query(
            "SELECT product_id, comparison_status FROM product_homologation_profiles "
            "WHERE product_id>? ORDER BY product_id LIMIT ?", (cursor, PAGE),
        )
        if not rows:
            break
        for product_id, status in rows:
            stored[int(product_id)] = str(status)
        cursor = int(rows[-1][0])
        if len(rows) < PAGE:
            break
    return stored


# ------------------------------------------------------------- recálculo

def build_profiles(records: Sequence[object]) -> tuple[ProductProfile, ...]:
    lexicon = build_brand_lexicon(records)
    return tuple(sorted((profile_product_v2(record, brand_lexicon=lexicon) for record in records),
                        key=lambda profile: profile.record.source_record_id))


def resolve_groups(profiles: Sequence[ProductProfile], *, ounce_rule: bool = True,
                   size_decisions: Mapping[str, SizeDecision] | None = None,
                   size_rules: bool = True) -> list[dict[str, object]]:
    """Grupos GTIN en ≥ 2 cadenas con la resolución de producción, miembro a miembro.

    Una decisión de ``size_decisions`` manda sobre la resolución (con o sin la
    regla de onzas), igual que en ``_exact_groups``. ``size_rules=False``: sin las
    reglas de tamaño v2.9 (base v2.8, para medir).
    """
    by_gtin: dict[str, list[ProductProfile]] = defaultdict(list)
    for profile in profiles:
        if profile.canonical_gtin is not None:
            by_gtin[profile.canonical_gtin].append(profile)
    groups = []
    for gtin, members in sorted(by_gtin.items()):
        if len({member.record.supermarket_id for member in members}) < 2:
            continue
        decision = (size_decisions or {}).get(gtin)
        if ounce_rule or decision is not None:  # vigente: regla de onzas con "nunca empeorar" o decisión
            (remaining, excluded, group_reasons), _ = resolve_gtin_group(
                gtin, list(members), decision, size_rules=size_rules)
        else:  # como era antes de la regla de onzas
            remaining, excluded, group_reasons = _resolve_gtin_members(gtin, list(members))
        ready = len({member.record.supermarket_id for member in remaining}) >= 2
        groups.append({
            "gtin": gtin, "members": members, "remaining": remaining,
            "excluded": {key: frozenset(value) for key, value in excluded.items()},
            "status": "ready" if ready else "review_required",
            "group_reasons": frozenset(group_reasons),
            "decision": decision,
        })
    return groups


def current_status(profiles: Sequence[ProductProfile], groups: Sequence[Mapping[str, object]]) -> dict[str, str]:
    """Estado por producto, con la misma asignación que ``build_homologation_rows``."""
    by_source: dict[str, str] = {}
    for group in groups:
        for member in group["members"]:
            by_source[member.record.source_record_id] = str(group["status"])
        if group["status"] == "ready":
            for source_id in group["excluded"]:
                by_source[source_id] = "review_required"
    result = {}
    for profile in profiles:
        source_id = profile.record.source_record_id
        if profile.canonical_gtin is None:
            result[source_id] = "unmapped"
        else:
            result[source_id] = by_source.get(source_id, "single_source")
    return result


def size_clusters(members: Iterable[ProductProfile]) -> list[int]:
    """Tamaños de los grupos de presentaciones compatibles (mayor primero)."""
    clusters: list[list[ProductProfile]] = []
    for member in members:
        if member.presentation is None:
            continue
        for cluster in clusters:
            if candidate_presentations_compatible(cluster[0].presentation, member.presentation):
                cluster.append(member)
                break
        else:
            clusters.append([member])
    return sorted((len(cluster) for cluster in clusters), reverse=True)


def simulate(group: Mapping[str, object], readmit_reasons: frozenset[str]) -> dict[str, object]:
    """Paso 3: la oferta excluida sólo por tamaño vuelve a la tarjeta (sin precio por unidad)."""
    excluded: Mapping[str, frozenset[str]] = group["excluded"]
    readmitted = {source_id for source_id, reasons in excluded.items() if reasons and reasons <= readmit_reasons}
    kept = {member.record.source_record_id for member in group["remaining"]} | readmitted
    members = [member for member in group["members"] if member.record.source_record_id in kept]
    chains = {member.record.supermarket_id for member in members}
    clusters = size_clusters(members)
    majority = bool(clusters) and clusters[0] >= 2 and (len(clusters) == 1 or clusters[0] > clusters[1])
    return {
        "readmitted": readmitted, "kept": kept, "grouped": len(chains) >= 2,
        "size": "mayoria" if majority else "empate_o_sin_tamano",
    }


# ------------------------------------------------- regla de onzas (simulada)

def ounce_basis(left: PresentationSignature | None, right: PresentationSignature | None) -> str | None:
    """Lectura de la onza (``fluid``/``mass``): la misma función de producción."""
    return ounce_reading(left, right)


def _converted_text(profile: ProductProfile, basis: str | None) -> str | None:
    if profile.presentation is None or profile.presentation.dimension != "ounce" or basis is None:
        return None
    converted = (ounces_as_volume if basis == "fluid" else ounces_as_mass)(profile.presentation)
    total = converted.total_base.quantize(Decimal("0.1"))
    return f"{total} {'ml' if basis == 'fluid' else 'g'}"


def simulate_ounce_rule(groups: Sequence[Mapping[str, object]], ounce_groups: Sequence[Mapping[str, object]],
                        product_of: Mapping[str, int]) -> tuple[dict[str, object], list[dict[str, object]], list[frozenset[str]]]:
    """Compara grupo por grupo la resolución vigente con la de la regla de onzas."""
    after = {str(group["gtin"]): group for group in ounce_groups}
    stats: Counter = Counter()
    lines: list[dict[str, object]] = []
    merge_sets: list[frozenset[str]] = []
    unit_price_offers = 0
    for group in groups:
        gtin = str(group["gtin"])
        new = after[gtin]
        members = group["members"]
        has_ounce = any(m.presentation is not None and m.presentation.dimension == "ounce" for m in members)
        size_conflict = "cross_source_presentation_conflict" in group["group_reasons"] or any(
            "cross_source_presentation_conflict" in reasons for reasons in group["excluded"].values())
        if has_ounce and size_conflict:
            stats["grupos_con_onza_y_conflicto_de_tamano"] += 1
            stats[f"de_esos_{group['status']}"] += 1
        before_in = {m.record.source_record_id for m in group["remaining"]}
        after_in = {m.record.source_record_id for m in new["remaining"]}
        unblocked = group["status"] == "review_required" and new["status"] == "ready"
        rejoined = group["status"] == "ready" and new["status"] == "ready" and after_in > before_in
        if new["status"] == "review_required" and group["status"] == "ready":
            stats["grupos_que_empeoran"] += 1  # no debería pasar: la regla sólo quita conflictos
        if not (unblocked or rejoined):
            if has_ounce and size_conflict:
                stats["siguen_igual"] += 1
            continue
        stats["bloqueados_a_listos" if unblocked else "listos_que_recuperan_ofertas"] += 1
        stats["ofertas_que_entran_a_comparar"] += len(after_in - before_in)
        merge_sets.append(frozenset(after_in))
        bases = {ounce_basis(a.presentation, b.presentation) for a in new["remaining"] for b in new["remaining"]} - {None}
        basis = next(iter(bases)) if len(bases) == 1 else None
        stats[f"base_{basis or 'mixta'}"] += 1
        gained = [m for m in new["remaining"] if m.presentation is not None and m.presentation.dimension == "ounce"]
        if basis:
            unit_price_offers += len(gained)
            stats["grupos_con_precio_unitario_nuevo"] += 1 if gained else 0
        for member in members:
            source_id = member.record.source_record_id
            lines.append({
                "gtin": gtin, "cambio": "bloqueado_a_listo" if unblocked else "recupera_ofertas",
                "base_onza": basis or "mixta", "supermercado": member.record.supermarket_id,
                "product_id": product_of[source_id], "nombre": member.record.source_name,
                "presentacion": _presentation_text(member), "presentacion_convertida": _converted_text(member, basis),
                "rol_antes": "comparable" if source_id in before_in else "fuera",
                "rol_con_regla": "comparable" if source_id in after_in else "fuera",
            })
    summary = dict(sorted(stats.items())) | {"productos_en_onzas_que_ganan_precio_unitario": unit_price_offers}
    return summary, lines, merge_sets


# ------------------------------------------------------------ tarjetas

class Components:
    def __init__(self, keys: Iterable[str]) -> None:
        self.parent = {key: key for key in keys}

    def find(self, key: str) -> str:
        while self.parent[key] != key:
            self.parent[key] = self.parent[self.parent[key]]
            key = self.parent[key]
        return key

    def union(self, keys: Sequence[str]) -> None:
        for other in keys[1:]:
            left, right = self.find(keys[0]), self.find(other)
            if left != right:
                self.parent[right] = left


def card_stats(rows: Sequence[CatalogRow], components: Components) -> dict[str, int]:
    chains: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        chains[components.find(row.key)].update(store for store, _ in row.offers)
    return {"tarjetas": len(chains), "tarjetas_con_2_o_mas_supermercados": sum(len(v) >= 2 for v in chains.values())}


def simulate_cards(rows: Sequence[CatalogRow], merge_sets: Mapping[str, Sequence[frozenset[str]]]) -> dict[str, object]:
    cards_of: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        for store, pid in row.offers:
            cards_of[f"{store}:{pid}"].append(row.key)
    components = Components(row.key for row in rows)
    result: dict[str, object] = {"actual": card_stats(rows, components)}
    split = {source: keys for source, keys in cards_of.items() if len(set(keys)) > 1}
    for keys in split.values():
        components.union(sorted(set(keys)))
    step1 = card_stats(rows, components)
    result["paso_1_sucursales_unidas"] = step1 | {
        "productos_partidos_por_sucursal": len(split),
        "tarjetas_que_desaparecen": result["actual"]["tarjetas"] - step1["tarjetas"],
        "por_supermercado": dict(sorted(Counter(source.split(":", 1)[0] for source in split).items())),
    }
    for label, sets in merge_sets.items():
        scenario = Components(row.key for row in rows)
        scenario.parent = dict(components.parent)
        for members in sets:
            keys = sorted({key for source in members for key in cards_of.get(source, ())})
            if len(keys) > 1:
                scenario.union(keys)
        stats = card_stats(rows, scenario)
        result[label] = stats | {"tarjetas_que_desaparecen_vs_paso_1": step1["tarjetas"] - stats["tarjetas"]}
    return result


# --------------------------------------------------------------- medición

def _presentation_text(profile: ProductProfile) -> str | None:
    value = profile.presentation
    if value is None:
        return None
    total = format(value.total_base.normalize(), "f")
    return f"{value.pack_count}×{total} {value.dimension}" if value.pack_count > 1 else f"{total} {value.dimension}"


def _published(profile: ProductProfile) -> str:
    if profile.presentation is None:
        return "sin tamaño"
    shown = canonical_presentation_from_signature(None, profile.presentation, profile.presentation_status)
    return str(shown.display_presentation)


def _role(source_id: str, kept: set[str], excluded: Mapping[str, set[str]], corrected: Mapping[str, object]) -> str:
    if source_id in corrected:
        return "corregida"
    if source_id in kept:
        return "en_grupo"
    reasons = excluded.get(source_id, set())
    if SIZE_EVIDENCE_CONTRADICTED in reasons:
        return "contradicha"
    if "human_size_decision_mismatch" in reasons:
        return "aislada_por_tamano"
    return "fuera_por_otro_motivo"


def evidence_report(profiles: Sequence[ProductProfile], decisions: Mapping[str, SizeDecision],
                    product_of: Mapping[str, int]) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Fichas decididas que cambian con la evidencia de foto (v2.10) frente a sólo el título (v2.9)."""
    by_gtin: dict[str, list[ProductProfile]] = defaultdict(list)
    for profile in profiles:
        if profile.canonical_gtin in decisions:
            by_gtin[profile.canonical_gtin].append(profile)
    stats: Counter = Counter()
    cards: list[dict[str, object]] = []
    lines: list[dict[str, object]] = []
    for gtin, decision in sorted(decisions.items()):
        members = by_gtin.get(gtin, [])
        if decision.decision != "size" or len({m.record.supermarket_id for m in members}) < 2:
            continue
        runs = {}
        for label, variant in (("sin_evidencia", replace(decision, evidence=(), apply="evidence")),
                               ("con_evidencia", decision), ("con_apply_all", replace(decision, apply="all"))):
            details: dict[str, object] = {}
            (kept, excluded, _), _ = resolve_gtin_group(gtin, list(members), variant, details=details)
            runs[label] = ({m.record.source_record_id for m in kept}, excluded, details)
        now = runs["con_evidencia"]
        roles = {label: {m.record.source_record_id: _role(m.record.source_record_id, run[0], run[1],
                                                           run[2].get("corrections") or {})
                         for m in members} for label, run in runs.items()}
        changed = roles["sin_evidencia"] != roles["con_evidencia"]
        mode = str(now[2].get("mode"))
        stats["fichas_decididas_en_2_o_mas_cadenas"] += 1
        stats[f"modo_{mode}"] += 1
        stats["con_evidencia_guardada"] += bool(decision.evidence)
        stats["fichas_que_cambian"] += changed
        stats["fichas_que_cambiarian_con_apply_all"] += roles["sin_evidencia"] != roles["con_apply_all"]
        stats["ofertas_corregidas"] += sum(role == "corregida" for role in roles["con_evidencia"].values())
        stats["ofertas_contradichas"] += sum(role == "contradicha" for role in roles["con_evidencia"].values())
        stats["ofertas_aisladas_sin_evidencia"] += sum(
            role == "aislada_por_tamano" for role in roles["con_evidencia"].values())
        photo = {item.supermarket_id: item for item in decision.evidence}
        official = f"{format(decision.net_content_value, 'f')} {decision.net_content_unit}"
        offers = []
        for member in sorted(members, key=lambda m: m.record.source_record_id):
            source_id, store = member.record.source_record_id, member.record.supermarket_id
            reading = photo.get(store)
            row = {
                "gtin": gtin, "decision": official, "modo": mode, "supermercado": store,
                "product_id": product_of.get(source_id), "nombre": member.record.source_name,
                "publicado": _published(member), "rol_sin_evidencia": roles["sin_evidencia"][source_id],
                "rol_con_evidencia": roles["con_evidencia"][source_id],
                "rol_con_apply_all": roles["con_apply_all"][source_id],
                "foto": "" if reading is None else f"{format(reading.value, 'f')} {reading.unit}",
            }
            lines.append(row)
            offers.append({key: row[key] for key in ("supermercado", "publicado", "foto", "rol_sin_evidencia",
                                                      "rol_con_evidencia", "rol_con_apply_all")})
        cards.append({
            "gtin": gtin, "decision": official, "autor": decision_author(decision), "apply": decision.apply,
            "modo": mode, "cambia": changed, "nunca_empeorar_aplicado": bool(now[2].get("fallback_v2_9")),
            "fotos_confirman": sorted(now[2].get("confirm") or ()),
            "fotos_contradicen": sorted(now[2].get("contra") or ()),
            "ofertas": offers,
        })
    summary = dict(sorted(stats.items())) | {
        "fichas": [card for card in cards if card["cambia"] or card["modo"] == "empate"
                   or any(o["rol_con_evidencia"] == "aislada_por_tamano" for o in card["ofertas"])],
    }
    return summary, lines


def measure(db, fetch: Callable[[str], object], base: str = CATALOG_BASE, *,
            pr_b: bool = False) -> tuple[dict[str, object], list[dict[str, object]]]:
    reader = ReadOnly(db)
    products = fetch_all_products(reader)
    specs = fetch_spec_attributes(reader)
    if specs is not None:
        products, _ = apply_source_spec_attributes(products, specs)
    product_of = {record.source_record_id: product_id for product_id, record in products}
    profiles = build_profiles([record for _, record in products])
    stored_decisions = fetch_size_decisions(reader)
    decisions = stored_decisions or {}
    groups = resolve_groups(profiles, size_decisions=decisions)

    # Guarda: la resolución recalculada debe ser la de producción (con sus decisiones).
    production = {group.canonical_gtin: group.comparison_status for group in _exact_groups(profiles, decisions)}
    mine = {str(group["gtin"]): str(group["status"]) for group in groups}
    if production != mine:
        raise AuditError("resolucion_distinta_de_produccion")
    recalculated = current_status(profiles, groups)
    stored = fetch_stored_status(reader)
    agreement = Counter(
        "coincide" if stored.get(product_of[source]) == status else f"{stored.get(product_of[source])}->{status}"
        for source, status in recalculated.items()
    )

    # 1. Motivos.
    blocked = [group for group in groups if group["status"] == "review_required"]
    with_exclusions = [group for group in groups if group["status"] == "ready" and group["excluded"]]
    blocked_by_reason = Counter(reason for group in blocked for reason in group["group_reasons"])
    blocked_only = Counter(next(iter(group["group_reasons"])) for group in blocked if len(group["group_reasons"]) == 1)
    excluded_groups_by_reason = Counter(
        reason for group in with_exclusions for reason in set().union(*group["excluded"].values()))
    excluded_members_by_reason = Counter(
        reason for group in groups for reasons in group["excluded"].values() for reason in reasons)
    combos = Counter(" + ".join(sorted(group["group_reasons"])) for group in blocked)
    reasons_table = {
        reason: {
            "grupos_bloqueados_con_este_motivo": blocked_by_reason.get(reason, 0),
            "grupos_bloqueados_solo_por_este_motivo": blocked_only.get(reason, 0),
            "grupos_listos_con_ofertas_excluidas_por_este_motivo": excluded_groups_by_reason.get(reason, 0),
            "ofertas_excluidas_por_este_motivo": excluded_members_by_reason.get(reason, 0),
        }
        for reason in (*ASKED_REASONS, *sorted((set(blocked_by_reason) | set(excluded_members_by_reason))
                                               - set(ASKED_REASONS)))
    }

    # 3. Simulación por grupo (escenarios A y B).
    simulation: dict[str, dict[str, object]] = {}
    lines: list[dict[str, object]] = []
    merge_sets: dict[str, list[frozenset[str]]] = {
        "paso_1_y_3_solo_mayoria": [], "paso_1_y_3_con_empates": []}
    for label, reasons in (("A_solo_tamano", SIZE_REASONS), ("B_tamano_y_multipack", SIZE_REASONS_B)):
        unblocked = Counter()
        rejoined_groups = rejoined_offers = 0
        for group in groups:
            result = simulate(group, reasons)
            if group["status"] == "review_required" and result["grouped"]:
                unblocked[result["size"]] += 1
            elif group["status"] == "ready" and result["readmitted"]:
                rejoined_groups += 1
                rejoined_offers += len(result["readmitted"])
            if label != "A_solo_tamano":
                continue
            changed = (group["status"] == "review_required" and result["grouped"]) or bool(result["readmitted"])
            if changed:
                kept = frozenset(result["kept"])
                merge_sets["paso_1_y_3_con_empates"].append(kept)
                if group["status"] == "ready" or result["size"] == "mayoria":
                    merge_sets["paso_1_y_3_solo_mayoria"].append(kept)
            if group["status"] == "review_required" or group["excluded"]:
                simulated = "agrupado" if result["grouped"] else "bloqueado"
                for member in group["members"]:
                    source_id = member.record.source_record_id
                    role = ("nucleo" if member in group["remaining"] else
                            "vuelve_a_la_tarjeta" if source_id in result["readmitted"] else "sigue_fuera")
                    lines.append({
                        "gtin": group["gtin"], "estado_actual": group["status"], "estado_simulado": simulated,
                        "tamano": result["size"], "rol": role, "supermercado": member.record.supermarket_id,
                        "product_id": product_of[source_id], "nombre": member.record.source_name,
                        "presentacion": _presentation_text(member), "estado_presentacion": member.presentation_status,
                        "motivos_miembro": " + ".join(sorted(group["excluded"].get(source_id, ()))),
                    })
        simulation[label] = {
            "maestros_desbloqueados_total": sum(unblocked.values()),
            "maestros_desbloqueados_con_mayoria_de_tamano": unblocked.get("mayoria", 0),
            "maestros_desbloqueados_en_empate": unblocked.get("empate_o_sin_tamano", 0),
            "grupos_listos_que_recuperan_ofertas": rejoined_groups,
            "ofertas_que_vuelven_a_su_tarjeta": rejoined_offers,
        }

    # Efecto de la regla de onzas: resolución sin la regla contra la v2.8 (sin las
    # reglas de tamaño v2.9, que mide el PR B), para no mezclar efectos.
    base_groups = resolve_groups(profiles, size_decisions=decisions, size_rules=False)
    ounce_summary, ounce_lines, ounce_sets = simulate_ounce_rule(
        resolve_groups(profiles, ounce_rule=False, size_decisions=decisions, size_rules=False),
        base_groups, product_of)
    merge_sets["paso_1_y_regla_de_onzas"] = ounce_sets

    evidence_summary, evidence_lines = evidence_report(profiles, decisions, product_of)

    rows_by_city = load_cities(fetch, base)
    cards = {city: simulate_cards(rows, merge_sets) for city, rows in rows_by_city.items()}

    summary = {
        "generado_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "alcance": "toda la base (productos) para grupos GTIN; catálogo público vigente para tarjetas",
        "verificacion_contra_perfiles_guardados": dict(sorted(agreement.items())),
        "decisiones_de_tamano": decision_stats(decisions, groups, table_present=stored_decisions is not None),
        "grupos_gtin_en_2_o_mas_cadenas": {
            "total": len(groups),
            "listos_sin_exclusiones": sum(1 for g in groups if g["status"] == "ready" and not g["excluded"]),
            "listos_con_ofertas_excluidas": len(with_exclusions),
            "bloqueados_review_required": len(blocked),
        },
        "motivos": reasons_table,
        "combinaciones_de_motivos_en_bloqueados": dict(combos.most_common(15)),
        "simulacion_paso_3": simulation,
        "simulacion_regla_de_onzas": ounce_summary,
        "correccion_con_evidencia": evidence_summary,
        "tarjetas_por_ciudad": cards,
        "sentencias_ejecutadas": reader.statements,
        "_lineas_onzas": ounce_lines,
        "_lineas_evidencia": evidence_lines,
    }
    if pr_b:  # medición del PR B sobre los mismos grupos (sin otra lectura de Turso)
        result = measure_pr_b(base_groups, product_of, current=groups)
        summary["medicion_pr_b_resumen"] = result["resumen"]
        summary["medicion_pr_b_motor_vigente"] = result["motor_vigente"]
        summary["_pr_b"] = result
    return summary, lines


def decision_stats(decisions: Mapping[str, SizeDecision], groups: Sequence[Mapping[str, object]], *,
                   table_present: bool) -> dict[str, object]:
    """Cuántas decisiones se aplicaron, de quién y qué estado dejan (sin nombres de cuentas)."""
    decided = [group for group in groups if group["decision"] is not None]
    return {
        "tabla_size_decisions": "presente" if table_present else "ausente",
        "decisiones_leidas": len(decisions),
        "por_tipo": dict(sorted(Counter(decision.decision for decision in decisions.values()).items())),
        "por_autor": dict(sorted(Counter(decision_author(decision) for decision in decisions.values()).items())),
        "aplicadas_a_grupos_en_2_o_mas_cadenas": len(decided),
        "grupos_decididos_listos": sum(1 for group in decided if group["status"] == "ready"),
        "grupos_decididos_en_revision": sum(1 for group in decided if group["status"] == "review_required"),
        "decisiones_sin_grupo_vigente": len(decisions) - len(decided),
    }


def write_outputs(output: Path, summary: Mapping[str, object], lines: Sequence[Mapping[str, object]]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    public = {key: value for key, value in summary.items() if not key.startswith("_")}
    (output / "medicion-reglas.json").write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8")
    with (output / "onzas-grupos.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUNCE_CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(summary.get("_lineas_onzas") or ())
    with (output / "evidencia-grupos.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EVIDENCE_CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(summary.get("_lineas_evidencia") or ())
    with (output / "grupos-reglas.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(lines)
    pr_b = summary.get("_pr_b")
    if pr_b:
        public_pr_b = {key: value for key, value in pr_b.items() if not key.startswith("_")}
        (output / "medicion-pr-b.json").write_text(json.dumps(public_pr_b, ensure_ascii=False, indent=2),
                                                   encoding="utf-8")
        with (output / "pr-b-grupos.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=PR_B_CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(pr_b["_lineas"])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--catalog-base", default=CATALOG_BASE)
    parser.add_argument("--pr-b", action="store_true",
                        help="también mide el PR B (g ≈ ml y peso sobre conteo): medicion-pr-b.json y pr-b-grupos.csv")
    args = parser.parse_args(argv)
    from exportar_modelo_analitico import TursoBackend

    backend = TursoBackend(os.environ.get("TURSO_DATABASE_URL", ""), os.environ.get("TURSO_AUTH_TOKEN", ""))
    summary, lines = measure(backend, _http_json, args.catalog_base, pr_b=args.pr_b)
    write_outputs(args.output_dir, summary, lines)
    print(json.dumps({key: value for key, value in summary.items() if not key.startswith("_")},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
