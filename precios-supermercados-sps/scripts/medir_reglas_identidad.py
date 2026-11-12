#!/usr/bin/env python3
"""Paso 0: medición de reglas de identidad (sólo lectura; no cambia ninguna regla).

Recalcula en memoria, con el mismo código que usa producción
(``profile_product_v2`` + ``_resolve_gtin_members``, igual que
``build_homologation_rows``), los grupos GTIN exactos y responde:

1. cuántos grupos exactos (GTIN en ≥ 2 cadenas) quedan bloqueados
   (``review_required``) o con miembros excluidos, por cada motivo;
2. simulación del paso 1: cuántas tarjetas del catálogo público se unen si las
   sucursales del mismo producto (``súper:product_id``) comparten tarjeta;
3. simulación del paso 3: cuántos grupos/maestros pasan a agruparse si una
   oferta con conflicto SÓLO de tamaño se queda en la tarjeta (aislada del
   precio por unidad) en vez de salir de ella. Separa los casos con mayoría de
   tamaño de los empates (p. ej. 1 contra 1), que necesitan una decisión.

Antes de medir verifica que el estado recalculado coincide con el guardado en
``product_homologation_profiles``. Sólo ejecuta SELECT (envoltorio ``ReadOnly``
de la auditoría de identidad); deja ``medicion-reglas.json`` y
``grupos-reglas.csv`` en ``--output-dir``.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
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
from precios_supermercados.pricesmart_specs_persistence import PROFILE_ATTRIBUTES_SQL  # noqa: E402
from precios_supermercados.pricesmart_specs_persistence import TABLE_NAME as SPECS_TABLE  # noqa: E402
from precios_supermercados.product_homologation import ProductProfile  # noqa: E402
from precios_supermercados.product_homologation_persistence import (  # noqa: E402
    apply_source_spec_attributes,
    records_from_product_rows,
)
from precios_supermercados.product_identity_v2 import (  # noqa: E402
    _exact_groups,
    _resolve_gtin_members,
    build_brand_lexicon,
    candidate_presentations_compatible,
    profile_product_v2,
)

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


def resolve_groups(profiles: Sequence[ProductProfile]) -> list[dict[str, object]]:
    """Grupos GTIN en ≥ 2 cadenas con la resolución de producción, miembro a miembro."""
    by_gtin: dict[str, list[ProductProfile]] = defaultdict(list)
    for profile in profiles:
        if profile.canonical_gtin is not None:
            by_gtin[profile.canonical_gtin].append(profile)
    groups = []
    for gtin, members in sorted(by_gtin.items()):
        if len({member.record.supermarket_id for member in members}) < 2:
            continue
        remaining, excluded, group_reasons = _resolve_gtin_members(gtin, list(members))
        ready = len({member.record.supermarket_id for member in remaining}) >= 2
        groups.append({
            "gtin": gtin, "members": members, "remaining": remaining,
            "excluded": {key: frozenset(value) for key, value in excluded.items()},
            "status": "ready" if ready else "review_required",
            "group_reasons": frozenset(group_reasons),
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


def measure(db, fetch: Callable[[str], object], base: str = CATALOG_BASE) -> tuple[dict[str, object], list[dict[str, object]]]:
    reader = ReadOnly(db)
    products = fetch_all_products(reader)
    specs = fetch_spec_attributes(reader)
    if specs is not None:
        products, _ = apply_source_spec_attributes(products, specs)
    product_of = {record.source_record_id: product_id for product_id, record in products}
    profiles = build_profiles([record for _, record in products])
    groups = resolve_groups(profiles)

    # Guarda: la resolución recalculada debe ser la de producción.
    production = {group.canonical_gtin: group.comparison_status for group in _exact_groups(profiles)}
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

    rows_by_city = load_cities(fetch, base)
    cards = {city: simulate_cards(rows, merge_sets) for city, rows in rows_by_city.items()}

    summary = {
        "generado_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "alcance": "toda la base (productos) para grupos GTIN; catálogo público vigente para tarjetas",
        "verificacion_contra_perfiles_guardados": dict(sorted(agreement.items())),
        "grupos_gtin_en_2_o_mas_cadenas": {
            "total": len(groups),
            "listos_sin_exclusiones": sum(1 for g in groups if g["status"] == "ready" and not g["excluded"]),
            "listos_con_ofertas_excluidas": len(with_exclusions),
            "bloqueados_review_required": len(blocked),
        },
        "motivos": reasons_table,
        "combinaciones_de_motivos_en_bloqueados": dict(combos.most_common(15)),
        "simulacion_paso_3": simulation,
        "tarjetas_por_ciudad": cards,
        "sentencias_ejecutadas": reader.statements,
    }
    return summary, lines


def write_outputs(output: Path, summary: Mapping[str, object], lines: Sequence[Mapping[str, object]]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "medicion-reglas.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with (output / "grupos-reglas.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--catalog-base", default=CATALOG_BASE)
    args = parser.parse_args(argv)
    from exportar_modelo_analitico import TursoBackend

    backend = TursoBackend(os.environ.get("TURSO_DATABASE_URL", ""), os.environ.get("TURSO_AUTH_TOKEN", ""))
    summary, lines = measure(backend, _http_json, args.catalog_base)
    write_outputs(args.output_dir, summary, lines)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
