"""Candidatos producto fuente → producto maestro (shadow, sólo cola de revisión).

Compara cada producto **sin vínculo activo** contra los registros *golden* de
los maestros que tienen al menos un miembro en la misma ciudad. Reutiliza la
estandarización y los niveles de comparación del motor (``standardize.py``,
``comparison.py``): marca, tamaño (±2 % = ``close``), pack, variante, tipo y
nombre. El nombre se compara contra todos los miembros del maestro en la
ciudad (el mejor), el resto contra los atributos golden.

Reglas (política ``product_master`` v1):

- nunca produce vínculos: todo candidato va a revisión (``engine_auto``
  deshabilitado);
- un conflicto duro (marca, tamaño, pack, variante, códigos, tipo) descarta el
  candidato;
- un producto con **otro GTIN válido** distinto del GTIN del maestro sólo
  puede ir a revisión, aunque todos los atributos coincidan exactamente
  (``different_valid_gtin``);
- una decisión humana "Distinto" (rechazo) nunca se vuelve a proponer;
- si el maestro ya tiene un vínculo activo del mismo supermercado, el
  candidato no es vinculable (≤ 1 vínculo activo por cadena y maestro).
"""
from __future__ import annotations

import copy
import hashlib
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from ..product_master import GoldenRecord
from .comparison import ComparisonSettings, compare, name_similarity
from .standardize import SizeInfo, StandardizedRecord, decimal_text

MASTER_CANDIDATE_SCHEMA = "precios-sps-master-review-queue/v1"
_UNIT_DIMENSION = {"g": "mass_g", "ml": "volume_ml", "unit": "count", "oz": "ounce"}

FIELD_SCORES: dict[str, dict[str, float]] = {
    "brand": {
        "exact": 1.0,
        "fuzzy": 0.9,
        "cross_name": 0.7,
        "in_other_name": 0.6,
        "one_missing": 0.3,
        "both_missing": 0.3,
        "conflict": 0.0,
    },
    "size": {"exact": 1.0, "close": 0.9, "near": 0.4, "ounce_bridge": 0.6, "missing": 0.3, "conflict": 0.0},
    "pack": {"same_multi": 1.0, "same_single": 1.0, "missing": 0.6, "conflict": 0.0},
    "variant": {"agree": 1.0, "none": 0.9, "one_sided": 0.3, "partial": 0.4, "conflict": 0.0},
    "type": {
        "same_type": 1.0,
        "type_partial": 0.7,
        "same_department": 0.5,
        "unknown": 0.4,
        "department_conflict": 0.1,
        "type_conflict": 0.0,
    },
}
DEFAULT_WEIGHTS = {"brand": 0.25, "name": 0.30, "size": 0.20, "pack": 0.05, "variant": 0.10, "type": 0.10}
REVIEWER_COLUMNS = ("decision", "reviewer", "note")


@dataclass(frozen=True)
class MasterCandidateSettings:
    top_k: int = 2
    min_score: float = 0.55
    # Piso de nombre (nivel "low" del motor) y nombre mínimo cuando la marca no
    # coincide: marca + tipo + tamaño sin nombre parecido no es candidato
    # (política: one_brand_plus_type_plus_size está prohibido como identidad).
    min_name_score: float = 0.45
    min_name_score_without_brand: float = 0.60
    high: float = 0.85
    medium: float = 0.70
    max_block: int = 400
    max_candidates_per_record: int = 60
    size_bucket_step: float = 0.05
    weights: Mapping[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))

    def band(self, score: float) -> str | None:
        if score >= self.high:
            return "high"
        if score >= self.medium:
            return "medium"
        if score >= self.min_score:
            return "low"
        return None


def review_id(product_key: str, master_product_id: str) -> str:
    return "mr_" + hashlib.sha256(f"{product_key}|{master_product_id}".encode("utf-8")).hexdigest()[:16]


def _size_from_golden(record: GoldenRecord) -> SizeInfo | None:
    if record.net_content_value is None or record.net_content_unit is None:
        return None
    total = float(record.net_content_value)
    if total <= 0:
        return None
    pack = record.pack_count or 1
    return SizeInfo(
        dimension=_UNIT_DIMENSION[record.net_content_unit],
        total=total,
        pack_count=pack,
        unit_amount=total / pack if pack > 1 else None,
    )


def master_view(record: GoldenRecord, representative: StandardizedRecord) -> StandardizedRecord:
    """Registro estandarizado del maestro: tokens del miembro preferido + atributos golden."""

    view = copy.copy(representative)
    view.brand = record.brand
    view.brand_squashed = None if record.brand is None else record.brand.replace(" ", "")
    view.size = _size_from_golden(record)
    view.product_type = record.product_type
    view.identity_gtin = record.primary_gtin
    view.gtin = None
    return view


def _bucket(size: SizeInfo | None, step: float) -> tuple[str, int] | None:
    if size is None or size.total <= 0:
        return None
    return size.dimension, int(math.floor(math.log(size.total) / step))


@dataclass
class MasterIndex:
    views: dict[str, StandardizedRecord]
    members: dict[str, tuple[StandardizedRecord, ...]]
    by_brand: dict[str, set[str]]
    by_type_size: dict[tuple[str, str, int], set[str]]
    by_token: dict[str, set[str]]


def build_master_index(
    masters: Mapping[str, GoldenRecord],
    members_by_master: Mapping[str, Sequence[StandardizedRecord]],
    settings: MasterCandidateSettings,
) -> MasterIndex:
    views: dict[str, StandardizedRecord] = {}
    members: dict[str, tuple[StandardizedRecord, ...]] = {}
    by_brand: dict[str, set[str]] = defaultdict(set)
    by_type_size: dict[tuple[str, str, int], set[str]] = defaultdict(set)
    by_token: dict[str, set[str]] = defaultdict(set)
    for master_id in sorted(members_by_master):
        group = tuple(members_by_master[master_id])
        record = masters.get(master_id)
        if record is None or not group or record.status != "active":
            continue
        representative = sorted(group, key=lambda item: (-len(item.core_tokens), item.source_record_id))[0]
        view = master_view(record, representative)
        views[master_id] = view
        members[master_id] = group
        if view.brand_squashed:
            by_brand[view.brand_squashed].add(master_id)
        bucket = _bucket(view.size, settings.size_bucket_step)
        if view.product_type and bucket is not None:
            by_type_size[(view.product_type, bucket[0], bucket[1])].add(master_id)
        for member in group:
            for token in set(member.core_tokens):
                by_token[token].add(master_id)
    return MasterIndex(views, members, dict(by_brand), dict(by_type_size), dict(by_token))


def _candidate_masters(record: StandardizedRecord, index: MasterIndex, settings: MasterCandidateSettings) -> list[str]:
    votes: Counter[str] = Counter()
    bucket = _bucket(record.size, settings.size_bucket_step)
    if record.brand_squashed and record.brand_squashed in index.by_brand:
        block = index.by_brand[record.brand_squashed]
        if len(block) <= settings.max_block * 4:
            for master_id in block:
                votes[master_id] += 3
    if record.product_type and bucket is not None:
        for offset in (-1, 0, 1):
            block = index.by_type_size.get((record.product_type, bucket[0], bucket[1] + offset), set())
            if len(block) <= settings.max_block:
                for master_id in block:
                    votes[master_id] += 2
    for token in set(record.core_tokens):
        block = index.by_token.get(token, set())
        if 0 < len(block) <= settings.max_block:
            for master_id in block:
                votes[master_id] += 1
    ranked = sorted(votes, key=lambda master_id: (-votes[master_id], master_id))
    return ranked[: settings.max_candidates_per_record]


def score_against_master(
    record: StandardizedRecord,
    master_id: str,
    index: MasterIndex,
    comparison: ComparisonSettings,
    settings: MasterCandidateSettings,
) -> dict[str, object] | None:
    view = index.views[master_id]
    vector = compare(record, view, comparison)
    labels = vector.as_labels()
    hard = [item for item in vector.hard_conflicts if item not in {"gtin_different", "same_retailer"}]
    if hard:
        return None
    name_score = max(name_similarity(record, member) for member in index.members[master_id])
    if name_score < settings.min_name_score:
        return None
    brand_agrees = labels["brand"] in {"exact", "fuzzy", "cross_name"}
    if not brand_agrees and name_score < settings.min_name_score_without_brand:
        return None
    field_scores = {name: FIELD_SCORES[name][labels[name]] for name in FIELD_SCORES}
    field_scores["name"] = round(name_score, 4)
    weights = settings.weights
    score = sum(weights[name] * field_scores[name] for name in weights) / sum(weights.values())
    flags: list[str] = []
    if record.identity_gtin and view.identity_gtin:
        flags.append("same_gtin_excluded_member" if record.identity_gtin == view.identity_gtin else "different_valid_gtin")
    if labels["size"] in {"near", "ounce_bridge", "missing"}:
        flags.append(f"size_{labels['size']}")
    if labels["brand"] in {"one_missing", "both_missing"}:
        flags.append("brand_missing")
    if labels["variant"] in {"one_sided", "partial"}:
        flags.append(f"variant_{labels['variant']}")
    exact = (
        labels["brand"] == "exact"
        and labels["size"] == "exact"
        and labels["pack"] in {"same_single", "same_multi"}
        and labels["variant"] in {"agree", "none"}
        and labels["type"] == "same_type"
        and labels["codes"] in {"agree", "none"}
        and name_score >= comparison.name_levels[0]
    )
    band = settings.band(score)
    if band is None:
        return None
    if band == "high" and (
        any(flag.startswith("size_") or flag.startswith("variant_") for flag in flags)
        or ("different_valid_gtin" in flags and not exact)
        or labels["brand"] not in {"exact", "fuzzy"}
        or name_score < comparison.name_levels[2]
    ):
        band = "medium"
    return {
        "score": round(score, 4),
        "band": band,
        "name_score": round(name_score, 4),
        "field_levels": {name: labels[name] for name in ("brand", "name", "size", "pack", "variant", "codes", "type", "price")},
        "field_scores": field_scores,
        "flags": flags,
        "exact_attribute_match": exact,
        # Política v1: el motor nunca vincula; otro GTIN válido siempre es revisión.
        "decision_policy": "review_only",
    }


def _size_text(record: GoldenRecord) -> str | None:
    if record.net_content_value is None:
        return None
    unit = record.net_content_unit
    if record.pack_count and record.pack_count > 1 and unit != "unit":
        return f"{record.pack_count} x ({record.net_content_value} {unit} total)"
    return f"{record.net_content_value} {unit}"


def generate_master_candidates(
    records: Sequence[StandardizedRecord],
    *,
    city: str,
    masters: Mapping[str, GoldenRecord],
    product_key_of: Mapping[str, str],
    master_of_product: Mapping[str, str],
    rejections: Iterable[tuple[str, str]] = (),
    comparison: ComparisonSettings | None = None,
    settings: MasterCandidateSettings | None = None,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Cola de revisión top-k por producto no homologado, con desglose del score.

    "No homologado" = sin vínculo activo, o vinculado sólo a su propio maestro
    de un único miembro (``single_source``: GTIN que ninguna otra cadena
    comparte). Para estos últimos la pareja con otro maestro de un único
    miembro aparece una sola vez (la dirección con mayor score).
    ``master_of_product`` es global (todas las ciudades): ``product_key`` →
    maestro activo.
    """

    comparison = comparison or ComparisonSettings()
    settings = settings or MasterCandidateSettings()
    master_keys: dict[str, set[str]] = defaultdict(set)
    for key, master_id in master_of_product.items():
        master_keys[master_id].add(key)
    master_supermarkets = {master_id: {key.split(":", 1)[0] for key in keys} for master_id, keys in master_keys.items()}
    members_by_master: dict[str, list[StandardizedRecord]] = defaultdict(list)
    for record in records:
        key = product_key_of.get(record.source_record_id)
        if key is not None and key in master_of_product:
            members_by_master[master_of_product[key]].append(record)
    index = build_master_index(masters, members_by_master, settings)
    by_record = {record.source_record_id: record for record in records}
    rejected = {(str(product), str(master)) for product, master in rejections}
    diagnostics: Counter[str] = Counter()
    scored_by_record: dict[str, list[tuple[str, dict[str, object]]]] = {}
    state_of: dict[str, tuple[str, str | None]] = {}
    for record in sorted(records, key=lambda item: item.source_record_id):
        product_key = product_key_of.get(record.source_record_id)
        if product_key is None:
            diagnostics["record_without_product_key"] += 1
            continue
        own = master_of_product.get(product_key)
        if own is not None and len(master_keys[own]) > 1:
            continue
        state = "unlinked" if own is None else "single_member_master"
        diagnostics[f"candidates_from_{state}"] += 1
        state_of[record.source_record_id] = (state, own)
        scored: list[tuple[str, dict[str, object]]] = []
        for master_id in _candidate_masters(record, index, settings):
            if master_id == own:
                continue
            if (product_key, master_id) in rejected:
                diagnostics["rejected_pairs_skipped"] += 1
                continue
            if record.supermarket_id in master_supermarkets.get(master_id, set()):
                diagnostics["retailer_slot_taken_skipped"] += 1
                continue
            result = score_against_master(record, master_id, index, comparison, settings)
            if result is None:
                continue
            scored.append((master_id, result))
        scored.sort(key=lambda item: (-float(item[1]["score"]), item[0]))  # type: ignore[arg-type]
        scored_by_record[record.source_record_id] = scored[: max(settings.top_k, 5)]

    # Dos maestros de un miembro: la pareja se propone una sola vez.
    best_pair: dict[tuple[str, str], tuple[float, str]] = {}
    for record_id, scored in scored_by_record.items():
        _, own = state_of[record_id]
        if own is None:
            continue
        for master_id, result in scored:
            if len(master_keys.get(master_id, ())) != 1:
                continue
            pair = tuple(sorted((own, master_id)))
            candidate = (float(result["score"]), record_id)  # type: ignore[arg-type]
            if pair not in best_pair or candidate > best_pair[pair]:
                best_pair[pair] = candidate  # type: ignore[index]

    rows: list[dict[str, object]] = []
    for record_id in sorted(scored_by_record):
        state, own = state_of[record_id]
        kept = []
        for master_id, result in scored_by_record[record_id]:
            if own is not None and len(master_keys.get(master_id, ())) == 1:
                pair = tuple(sorted((own, master_id)))
                if best_pair.get(pair, (0.0, ""))[1] != record_id:  # type: ignore[index]
                    diagnostics["symmetric_single_member_pair_deduplicated"] += 1
                    continue
            kept.append((master_id, result))
        if kept:
            diagnostics[f"with_candidate_from_{state}"] += 1
        for rank, (master_id, result) in enumerate(kept[: settings.top_k], start=1):
            rows.append(
                _queue_row(
                    by_record[record_id],
                    rank=rank,
                    city=city,
                    product_key=product_key_of[record_id],
                    link_state=state,
                    own_master=own,
                    master_id=master_id,
                    golden=masters[master_id],
                    master_supermarkets=master_supermarkets,
                    result=result,
                )
            )
    band_counts = Counter(str(row["band"]) for row in rows if row["rank"] == 1)
    retailer_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        if row["rank"] == 1:
            retailer_counts[str(row["supermarket_id"])][str(row["band"])] += 1
    summary: dict[str, object] = {
        "city": city,
        "masters_indexed": len(index.views),
        "queue_rows": len(rows),
        "products_with_candidate_by_band": dict(sorted(band_counts.items())),
        "products_with_candidate_by_retailer": {
            key: dict(sorted(value.items())) for key, value in sorted(retailer_counts.items())
        },
        "different_valid_gtin_candidates": sum(
            1 for row in rows if row["rank"] == 1 and "different_valid_gtin" in row["flags"]  # type: ignore[operator]
        ),
        "exact_attribute_match_candidates": sum(1 for row in rows if row["rank"] == 1 and row["exact_attribute_match"]),
        **dict(sorted(diagnostics.items())),
    }
    return rows, summary


def _queue_row(
    record: StandardizedRecord,
    *,
    rank: int,
    city: str,
    product_key: str,
    link_state: str,
    own_master: str | None,
    master_id: str,
    golden: GoldenRecord,
    master_supermarkets: Mapping[str, set[str]],
    result: Mapping[str, object],
) -> dict[str, object]:
    source = record.record
    row: dict[str, object] = {
        "review_id": review_id(product_key, master_id),
        "city": city,
        "rank": rank,
        "master_product_id": master_id,
        "master_public_id": golden.public_id,
        "master_primary_gtin": golden.primary_gtin,
        "master_display_name": golden.display_name,
        "master_brand": golden.brand,
        "master_size": _size_text(golden),
        "master_product_type": golden.product_type,
        "master_variant": golden.variant,
        "master_supermarkets": sorted(master_supermarkets.get(master_id, set())),
        "product_key": product_key,
        "product_link_state": link_state,
        "product_master_product_id": own_master,
        "source_record_id": source.source_record_id,
        "supermarket_id": source.supermarket_id,
        "product_name": source.source_name,
        "product_brand": record.brand,
        "product_presentation": source.source_presentation,
        "product_size": None
        if record.size is None
        else f"{decimal_text(round(record.size.total, 4))} {record.size.canonical_unit}",
        "product_type": record.product_type,
        "product_gtin": record.identity_gtin,
        "product_price": source.current_price,
        **result,
    }
    for column in REVIEWER_COLUMNS:
        row[column] = ""
    return row


CSV_COLUMNS = (
    "review_id",
    "city",
    "rank",
    "band",
    "score",
    "master_product_id",
    "master_public_id",
    "master_primary_gtin",
    "master_display_name",
    "master_brand",
    "master_size",
    "master_product_type",
    "master_variant",
    "master_supermarkets",
    "product_key",
    "product_link_state",
    "product_master_product_id",
    "supermarket_id",
    "product_name",
    "product_brand",
    "product_presentation",
    "product_size",
    "product_type",
    "product_gtin",
    "product_price",
    "name_score",
    "field_levels",
    "flags",
    "exact_attribute_match",
    "decision",
    "reviewer",
    "note",
)


def csv_row(row: Mapping[str, object]) -> dict[str, str]:
    result: dict[str, str] = {}
    for column in CSV_COLUMNS:
        value = row.get(column)
        if isinstance(value, dict):
            value = " ".join(f"{key}={item}" for key, item in value.items())
        elif isinstance(value, list):
            value = ";".join(str(item) for item in value)
        elif isinstance(value, bool):
            value = "true" if value else "false"
        result[column] = "" if value is None else str(value)
    return result


__all__ = [
    "CSV_COLUMNS",
    "MASTER_CANDIDATE_SCHEMA",
    "MasterCandidateSettings",
    "csv_row",
    "generate_master_candidates",
    "master_view",
    "review_id",
    "score_against_master",
]


def standardize_records(
    records: Sequence[object],
    *,
    config: object,
    taxonomy: object | None,
    rare_token_share: float = 0.005,
) -> list[StandardizedRecord]:
    """Estandarización + TF-IDF + tokens raros, igual que ``MatchingEngine.prepare_city``
    pero sin generar todas las parejas (sólo se compara contra maestros)."""

    from .standardize import Standardizer
    from .text import TfidfModel

    standardizer = Standardizer(
        records,  # type: ignore[arg-type]
        vocabulary=getattr(config, "variant_vocabulary"),
        taxonomy=taxonomy,  # type: ignore[arg-type]
        synonyms=getattr(config, "token_synonyms"),
    )
    standardized = standardizer.run()
    tfidf = TfidfModel(item.core_text for item in standardized)
    token_df = Counter(token for item in standardized for token in set(item.core_tokens))
    rare_limit = max(3, int(len(standardized) * rare_token_share))
    for item in standardized:
        item.tfidf = tfidf.vector(item.core_text)
        item.rare_tokens = frozenset(token for token in item.core_tokens if token_df[token] <= rare_limit)
    return standardized
