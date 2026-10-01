"""Blocking y generación de candidatos.

Combina dos familias de técnicas usadas en resolución de entidades a escala:

1. **Llaves de bloqueo deterministas** (unión de reglas, como los
   ``blocking_rules`` de Splink): marca + bucket de tamaño, tipo + bucket,
   marca + primer token, dos primeros tokens + dimensión. Ninguna regla aislada
   es obligatoria, así que un dato faltante no impide recuperar la pareja.
2. **Recuperación aproximada MinHash-LSH** sobre shingles de caracteres del
   nombre núcleo (Broder 1997; Indyk–Motwani), equivalente ligero a la
   recuperación por embeddings que usan los motores de product matching de
   retail: encuentra vecinos aunque no compartan una llave exacta.

Los vecinos de cada registro se re-rankean con la similitud de Jaccard
estimada por MinHash (más bonos por marca/tamaño) y se conservan los
``top_k`` por cada cadena contraria; el coseno TF-IDF completo se calcula
después, sólo para las parejas retenidas. Sólo se generan parejas
entre supermercados distintos.
"""
from __future__ import annotations

import random
import zlib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from .comparison import pair_key
from .standardize import StandardizedRecord, size_bucket

_MERSENNE = (1 << 61) - 1


@dataclass(frozen=True, slots=True)
class BlockingSettings:
    rules: tuple[str, ...] = ("brand_size", "type_size", "brand_token", "lead_tokens")
    max_block_size: int = 250
    size_bucket_log_step: float = 0.05
    num_perm: int = 48
    bands: int = 16
    shingle_size: int = 3
    max_bucket_size: int = 300
    seed: int = 20260930
    top_k_per_retailer: int = 6
    min_similarity: float = 0.15

    @classmethod
    def from_config(cls, section: Mapping[str, object]) -> "BlockingSettings":
        minhash = section.get("minhash") or {}
        assert isinstance(minhash, Mapping)
        return cls(
            rules=tuple(str(rule) for rule in section.get("rules", cls.rules)),  # type: ignore[union-attr]
            max_block_size=int(section.get("max_block_size", cls.max_block_size)),  # type: ignore[arg-type]
            size_bucket_log_step=float(section.get("size_bucket_log_step", cls.size_bucket_log_step)),  # type: ignore[arg-type]
            num_perm=int(minhash.get("num_perm", cls.num_perm)),
            bands=int(minhash.get("bands", cls.bands)),
            shingle_size=int(minhash.get("shingle_size", cls.shingle_size)),
            max_bucket_size=int(minhash.get("max_bucket_size", cls.max_bucket_size)),
            seed=int(minhash.get("seed", cls.seed)),
            top_k_per_retailer=int(section.get("top_k_per_retailer", cls.top_k_per_retailer)),  # type: ignore[arg-type]
            min_similarity=float(section.get("min_similarity", cls.min_similarity)),  # type: ignore[arg-type]
        )


@dataclass
class CandidateSet:
    pairs: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    diagnostics: dict[str, object] = field(default_factory=dict)

    def add(self, left_id: str, right_id: str, source: str) -> None:
        self.pairs.setdefault(pair_key(left_id, right_id), set()).add(source)

    def __contains__(self, key: tuple[str, str]) -> bool:
        return pair_key(*key) in self.pairs

    def __len__(self) -> int:
        return len(self.pairs)


def shingles(text: str, size: int) -> set[int]:
    compact = f" {text} "
    if len(compact) <= size:
        return {zlib.crc32(compact.encode("utf-8"))}
    return {
        zlib.crc32(compact[index : index + size].encode("utf-8"))
        for index in range(len(compact) - size + 1)
    }


class MinHasher:
    def __init__(self, num_perm: int, seed: int) -> None:
        generator = random.Random(seed)
        self.parameters = [
            (generator.randrange(1, _MERSENNE), generator.randrange(0, _MERSENNE))
            for _ in range(num_perm)
        ]

    def signature(self, values: Iterable[int]) -> tuple[int, ...]:
        items = list(values)
        if not items:
            return tuple([_MERSENNE] * len(self.parameters))
        return tuple(min((a * value + b) % _MERSENNE for value in items) for a, b in self.parameters)


def blocking_keys(record: StandardizedRecord, settings: BlockingSettings) -> list[tuple[object, ...]]:
    """Llaves de índice de un registro (un bucket de tamaño)."""

    keys: list[tuple[object, ...]] = []
    bucket = size_bucket(record.size, settings.size_bucket_log_step)
    first = record.core_tokens[0] if record.core_tokens else None
    if "brand_size" in settings.rules and record.brand_squashed and bucket is not None:
        keys.append(("brand_size", record.brand_squashed, bucket[0], bucket[1]))
    if "type_size" in settings.rules and record.product_type and bucket is not None:
        keys.append(("type_size", record.product_type, bucket[0], bucket[1]))
    if "brand_token" in settings.rules and record.brand_squashed and first:
        keys.append(("brand_token", record.brand_squashed, first))
    if "lead_tokens" in settings.rules and len(record.core_tokens) >= 2:
        lead = tuple(sorted(record.core_tokens[:2]))
        keys.append(("lead_tokens", lead, bucket[0] if bucket else "nodim"))
    return keys


def probe_keys(record: StandardizedRecord, settings: BlockingSettings) -> list[tuple[object, ...]]:
    """Llaves a consultar: igual que ``blocking_keys`` con buckets vecinos ±1."""

    probes: list[tuple[object, ...]] = []
    for key in blocking_keys(record, settings):
        if key[0] in {"brand_size", "type_size"}:
            for delta in (-1, 0, 1):
                probes.append((key[0], key[1], key[2], int(key[3]) + delta))  # type: ignore[call-overload]
        else:
            probes.append(key)
    return probes


def generate_candidates(
    records: Sequence[StandardizedRecord],
    settings: BlockingSettings,
    *,
    include_gtin_rule: bool = True,
) -> CandidateSet:
    by_id = {record.source_record_id: record for record in records}
    index: dict[tuple[object, ...], list[str]] = defaultdict(list)
    for record in records:
        for key in blocking_keys(record, settings):
            index[key].append(record.source_record_id)
    oversized = {key for key, members in index.items() if len(members) > settings.max_block_size}

    hasher = MinHasher(settings.num_perm, settings.seed)
    rows_per_band = max(1, settings.num_perm // settings.bands)
    lsh: dict[tuple[int, tuple[int, ...]], list[str]] = defaultdict(list)
    signatures: dict[str, tuple[int, ...]] = {}
    for record in records:
        text = " ".join(filter(None, (record.brand_squashed, record.core_text)))
        if not text:
            continue
        signature = hasher.signature(shingles(text, settings.shingle_size))
        signatures[record.source_record_id] = signature
        for band in range(settings.bands):
            chunk = signature[band * rows_per_band : (band + 1) * rows_per_band]
            if chunk:
                lsh[(band, chunk)].append(record.source_record_id)
    oversized_buckets = sum(1 for members in lsh.values() if len(members) > settings.max_bucket_size)

    exact_names: dict[str, list[str]] = defaultdict(list)
    gtins: dict[str, list[str]] = defaultdict(list)
    for record in records:
        exact_names[record.profile.normalized_name].append(record.source_record_id)
        if record.identity_gtin:
            gtins[record.identity_gtin].append(record.source_record_id)

    result = CandidateSet()
    pool_sizes: list[int] = []
    for record in records:
        pool: dict[str, set[str]] = defaultdict(set)
        for key in probe_keys(record, settings):
            if key in oversized:
                continue
            for other in index.get(key, ()):
                pool[other].add(str(key[0]))
        signature = signatures.get(record.source_record_id)
        if signature is not None:
            for band in range(settings.bands):
                chunk = signature[band * rows_per_band : (band + 1) * rows_per_band]
                members = lsh.get((band, chunk), ())
                if len(members) > settings.max_bucket_size:
                    continue
                for other in members:
                    pool[other].add("minhash_lsh")
        pool.pop(record.source_record_id, None)
        pool_sizes.append(len(pool))
        by_retailer: dict[str, list[tuple[float, str, set[str]]]] = defaultdict(list)
        for other_id, sources in pool.items():
            other = by_id[other_id]
            if other.supermarket_id == record.supermarket_id:
                continue
            other_signature = signatures.get(other_id)
            if signature is None or other_signature is None:
                similarity = 0.0
            else:
                similarity = sum(1 for a, b in zip(signature, other_signature) if a == b) / len(signature)
            bonus = 0.0
            if record.brand_squashed and record.brand_squashed == other.brand_squashed:
                bonus += 0.15
            if "brand_size" in sources or "type_size" in sources:
                bonus += 0.1
            if similarity < settings.min_similarity and bonus < 0.2:
                continue
            by_retailer[other.supermarket_id].append((similarity + bonus, other_id, sources))
        for candidates in by_retailer.values():
            candidates.sort(key=lambda item: (-item[0], item[1]))
            for _, other_id, sources in candidates[: settings.top_k_per_retailer]:
                for source in sources:
                    result.add(record.source_record_id, other_id, source)
    for members in exact_names.values():
        _add_cross(result, members, by_id, "exact_name")
        _add_twins(result, members, by_id)
    for members in gtins.values():
        _add_twins(result, members, by_id)
        if include_gtin_rule:
            _add_cross(result, members, by_id, "gtin")
    pool_sizes.sort()
    result.diagnostics = {
        "records": len(records),
        "blocking_keys": len(index),
        "oversized_blocks_skipped": len(oversized),
        "lsh_buckets": len(lsh),
        "lsh_oversized_buckets_skipped": oversized_buckets,
        "pool_size_median": pool_sizes[len(pool_sizes) // 2] if pool_sizes else 0,
        "pool_size_p95": pool_sizes[int(len(pool_sizes) * 0.95)] if pool_sizes else 0,
        "candidate_pairs": len(result),
        "retailer_twin_pairs": sum(1 for sources in result.pairs.values() if sources == {"retailer_twin"}),
    }
    return result


def _add_cross(result: CandidateSet, members: Sequence[str], by_id: Mapping[str, StandardizedRecord], source: str) -> None:
    if len(members) < 2 or len(members) > 50:
        return
    for index, left in enumerate(members):
        for right in members[index + 1 :]:
            if by_id[left].supermarket_id != by_id[right].supermarket_id:
                result.add(left, right, source)


def _add_twins(result: CandidateSet, members: Sequence[str], by_id: Mapping[str, StandardizedRecord]) -> None:
    """Mismo supermercado, distinto contexto (tienda) y mismo GTIN/nombre exacto."""

    if len(members) < 2 or len(members) > 50:
        return
    for index, left in enumerate(members):
        for right in members[index + 1 :]:
            a, b = by_id[left], by_id[right]
            if a.supermarket_id == b.supermarket_id and a.context_id != b.context_id:
                result.add(left, right, "retailer_twin")
