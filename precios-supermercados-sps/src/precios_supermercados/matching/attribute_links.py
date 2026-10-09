"""Vínculos ``engine_auto`` por atributos para cadenas sin GTIN (regla A v1).

PriceSmart y Los Andes no publican un código de barras que coincida con otra
cadena. Esta regla los vincula al producto maestro (GTIN) de OTRA cadena cuando
son el mismo artículo según sus atributos, con la precisión medida y aprobada
por el responsable (2026-10-09: 161/163 = 98.8 % en 387 pares etiquetados,
IC95 95.6–99.7 %; revisión del responsable 50/50 de acuerdo).

Regla A (nivel "exacto"):

- misma marca (forma compacta idéntica; nunca marca propia "Member's Selection");
- puntaje de nombre ≥ 0.74 (banda alta);
- variante sin conflicto (``agree``/``none``) y a lo sumo una palabra menor
  distinta en el nombre (``name_diff`` ``none``/``one_side_minor``);
- mismo tamaño (``exact``/``close``) y mismo paquete;
- sin conflicto de tipo, departamento ni códigos, y nunca dos GTIN válidos
  distintos.

Guardas de unicidad: el maestro destino no puede tener ya un producto de la
misma cadena; si dos productos de una cadena apuntan al mismo maestro o un
producto tiene dos maestros empatados, no se vincula ninguno (ambiguo).

Los vínculos se recalculan en cada refresco desde los perfiles en memoria: si
un producto cambia y deja de cumplir la regla, su vínculo desaparece. Un
vínculo manual o del registro revisado siempre tiene prioridad.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Iterable, Mapping, Sequence

from ..product_master import MasterLink, MemberProfile, gtin_master_id
from .comparison import ComparisonSettings, compare
from .config import DEFAULT_CONFIG_PATH, DEFAULT_TAXONOMY_PATH, load_engine_config
from .records import MatchRecord
from .taxonomy import load_source_taxonomy

RULE_ID = "attribute-rule-a"
RULE_VERSION = "1"
DECIDED_BY = f"engine:{RULE_ID}@{RULE_VERSION}"
TARGET_SUPERMARKETS = frozenset({"pricesmart", "comisariato_los_andes"})
MIN_NAME_SCORE = 0.74
PRIVATE_LABEL_BRANDS = frozenset({"membersselection", "memberselection", "members"})
_ELIGIBLE_STATUSES = frozenset({"ready", "single_source"})
_BLOCKING = {
    "brand": {"conflict"},
    "variant": {"conflict", "one_sided", "partial"},
    "codes": {"conflict"},
    "type": {"type_conflict", "department_conflict"},
}
_SIZE_OK = {"exact", "close"}
_PACK_OK = {"same_single", "same_multi", "missing"}
_NAME_DIFF_OK = {"none", "one_side_minor"}
# "Barquillos 12 Unidades 60 g": paquete aunque la presentación diga 60 g.
_DECLARED_MULTIPACK_RE = re.compile(r"(?<![\w.,/])(?P<count>\d{1,3})\s*(?:unidades|unids?|unds?|uds?)(?!\w)", re.IGNORECASE)


def declares_multipack(name: str) -> bool:
    """El nombre declara N ≥ 2 unidades (para tamaños en g/ml exige paquete)."""

    return any(int(match.group("count")) >= 2 for match in _DECLARED_MULTIPACK_RE.finditer(name or ""))


def rule_a_exact(labels: Mapping[str, str], name_score: float, gtin_level: str) -> bool:
    """La pareja cumple la regla A de "mismo producto" (nivel exacto)."""

    if gtin_level == "different" or name_score < MIN_NAME_SCORE:
        return False
    if any(labels[field] in bad for field, bad in _BLOCKING.items()):
        return False
    return labels["size"] in _SIZE_OK and labels["pack"] in _PACK_OK and labels["name_diff"] in _NAME_DIFF_OK


def engine_attribute_links(
    products: Sequence[tuple[int, object]],
    members: Sequence[MemberProfile],
    *,
    standardize=None,
) -> tuple[list[MasterLink], dict[str, int]]:
    """Vínculos ``engine_auto`` (regla A) de PriceSmart/Los Andes a maestros GTIN.

    ``products`` son ``(product_id, SourceProductRecord)``; ``members`` los
    perfiles del mismo refresco. ``standardize`` permite inyectar la
    estandarización en pruebas.
    """

    from .master_candidates import standardize_records

    diagnostics: Counter[str] = Counter()
    by_id = {member.product_id: member for member in members}
    records: list[MatchRecord] = []
    for product_id, record in products:
        member = by_id.get(product_id)
        if member is None:
            continue
        records.append(
            MatchRecord(
                source_record_id=member.key,
                supermarket_id=member.supermarket_id,
                city="ALL",
                source_name=str(getattr(record, "source_name")),
                source_brand=getattr(record, "source_brand", None),
                source_presentation=getattr(record, "source_presentation", None),
                source_category=getattr(record, "source_category", None),
                barcode=getattr(record, "barcode", None),
            )
        )
    if not any(item.supermarket_id in TARGET_SUPERMARKETS for item in records):
        return [], {"targets": 0}

    config = load_engine_config(DEFAULT_CONFIG_PATH)
    settings = ComparisonSettings.from_config(config.section("comparison"), config.implicit_defaults)
    if standardize is None:
        taxonomy = load_source_taxonomy(DEFAULT_TAXONOMY_PATH)
        rare = float(config.section("comparison").get("rare_token_share", 0.005))

        def standardize(items):  # noqa: E306 - dependencia inyectable
            return standardize_records(items, config=config, taxonomy=taxonomy, rare_token_share=rare)

    standardized = standardize(records)
    member_of_key = {member.key: member for member in members}

    # Maestros GTIN y las cadenas que ya los ocupan.
    gtin_chains: dict[str, set[str]] = defaultdict(set)
    multi_chain_gtins: set[str] = set()
    for member in members:
        if member.canonical_gtin and member.comparison_status in _ELIGIBLE_STATUSES:
            gtin_chains[member.canonical_gtin].add(member.supermarket_id)
    for gtin, chains in gtin_chains.items():
        if len(chains) >= 2:
            multi_chain_gtins.add(gtin)

    partners_by_brand: dict[str, list] = defaultdict(list)
    targets = []
    for item in standardized:
        member = member_of_key[item.source_record_id]
        if item.supermarket_id in TARGET_SUPERMARKETS:
            targets.append((item, member))
        elif (
            item.brand_squashed
            and member.canonical_gtin
            and member.comparison_status in _ELIGIBLE_STATUSES
        ):
            partners_by_brand[item.brand_squashed].append((item, member))

    proposals: dict[int, tuple[float, str, MemberProfile, MemberProfile, dict[str, str]]] = {}
    for item, member in targets:
        diagnostics["targets"] += 1
        if member.canonical_gtin in multi_chain_gtins:
            diagnostics["target_already_multi_chain"] += 1
            continue
        if not item.brand_squashed or item.brand_squashed in PRIVATE_LABEL_BRANDS:
            diagnostics["target_without_comparable_brand"] += 1
            continue
        scored: list[tuple[float, str, MemberProfile, dict[str, str]]] = []
        for partner_item, partner in partners_by_brand.get(item.brand_squashed, ()):
            vector = compare(item, partner_item, settings)
            labels = vector.as_labels()
            if not rule_a_exact(labels, vector.name_score, vector.gtin_level):
                continue
            if (
                item.size is not None
                and item.size.dimension != "count"
                and labels["pack"] != "same_multi"
                and (declares_multipack(item.record.source_name) != declares_multipack(partner_item.record.source_name))
            ):
                diagnostics["declared_multipack_mismatch"] += 1
                continue
            master_id = gtin_master_id(partner.canonical_gtin)
            if member.supermarket_id in gtin_chains[partner.canonical_gtin]:
                diagnostics["master_has_same_chain"] += 1
                continue
            scored.append((vector.name_score, master_id, partner, labels))
        if not scored:
            diagnostics["no_rule_a_match"] += 1
            continue
        scored.sort(key=lambda entry: (-entry[0], entry[1]))
        best = scored[0]
        if any(other[1] != best[1] and other[0] == best[0] for other in scored[1:]):
            diagnostics["ambiguous_tie"] += 1
            continue
        proposals[member.product_id] = (best[0], best[1], member, best[2], best[3])

    # ≤ 1 producto por cadena y maestro: un empate o dos aspirantes = ninguno.
    claims: dict[tuple[str, str], list[int]] = defaultdict(list)
    for product_id, (_, master_id, member, _, _) in proposals.items():
        claims[(master_id, member.supermarket_id)].append(product_id)
    links: list[MasterLink] = []
    for (master_id, _), product_ids in sorted(claims.items()):
        if len(product_ids) > 1:
            diagnostics["same_chain_contention"] += len(product_ids)
            continue
        score, _, member, partner, labels = proposals[product_ids[0]]
        links.append(
            MasterLink(
                product_id=member.product_id,
                supermarket_id=member.supermarket_id,
                master_product_id=master_id,
                link_method="engine_auto",
                decided_by=DECIDED_BY,
                evidence={
                    "rule": RULE_ID,
                    "rule_version": RULE_VERSION,
                    "primary_gtin": partner.canonical_gtin,
                    "partner": partner.key,
                    "name_score": round(score, 4),
                    "labels": {key: labels[key] for key in ("brand", "name", "name_diff", "size", "pack", "variant", "type")},
                },
                confidence=round(score, 4),
            )
        )
        diagnostics[f"links_{member.supermarket_id}"] += 1
    links.sort(key=lambda link: link.product_id)
    diagnostics["links"] = len(links)
    return links, dict(sorted(diagnostics.items()))


__all__ = [
    "DECIDED_BY",
    "MIN_NAME_SCORE",
    "RULE_ID",
    "RULE_VERSION",
    "TARGET_SUPERMARKETS",
    "engine_attribute_links",
    "rule_a_exact",
]
