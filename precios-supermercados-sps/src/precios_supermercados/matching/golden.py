"""Conjunto golden: muestreo estratificado, formato de etiquetado y evaluación.

El muestreo es estratificado por banda (auto / revisión / no-match cercano) y
por par de supermercados, con semilla fija. Cada fila lleva el tamaño de su
estrato para estimar precisión ponderada a la población (estimador de
Horvitz–Thompson por estrato). Las etiquetas silver (GTIN) no van en el archivo
de etiquetado para no sesgar al etiquetador; se guardan en un archivo aparte.
"""
from __future__ import annotations

import csv
import io
import random
from collections import Counter, defaultdict
from typing import Iterable, Mapping, Sequence

from .thresholds import prf, wilson_lower

GOLDEN_SCHEMA = "precios-sps-matching-golden-set/v1"
GOLDEN_LABELS = ("match", "no_match", "variant", "alternative", "unsure")
POSITIVE_LABELS = frozenset({"match"})
NEGATIVE_LABELS = frozenset({"no_match", "variant", "alternative"})
DEFAULT_BAND_SHARES = {"auto_match": 0.35, "review": 0.40, "non_match": 0.25}
GOLDEN_FIELDS = (
    "golden_id",
    "city",
    "stratum_band",
    "stratum_retailer_pair",
    "stratum_population",
    "stratum_sample",
    "candidate_id",
    "match_probability",
    "band",
    "left_source_record_id",
    "left_supermarket_id",
    "left_source_name",
    "left_source_brand",
    "left_size",
    "left_current_price",
    "right_source_record_id",
    "right_supermarket_id",
    "right_source_name",
    "right_source_brand",
    "right_size",
    "right_current_price",
    "waterfall",
    "label",
    "labeler",
    "labeled_at_utc",
    "notes",
)


class GoldenSetError(ValueError):
    """El archivo golden no es válido."""


def _allocate(total: int, available: Mapping[str, int]) -> dict[str, int]:
    """Reparto equitativo entre estratos con redistribución del sobrante."""

    allocation = {key: 0 for key in available}
    remaining = total
    open_keys = sorted(key for key, count in available.items() if count > 0)
    while remaining > 0 and open_keys:
        share = max(1, remaining // len(open_keys))
        next_open = []
        for key in open_keys:
            if remaining <= 0:
                break
            take = min(share, available[key] - allocation[key], remaining)
            allocation[key] += take
            remaining -= take
            if allocation[key] < available[key]:
                next_open.append(key)
        if next_open == open_keys and share == 0:
            break
        open_keys = next_open
    return allocation


def sample_golden_pairs(
    rows: Sequence[Mapping[str, object]],
    *,
    size: int = 600,
    seed: int = 20260930,
    band_shares: Mapping[str, float] = DEFAULT_BAND_SHARES,
    non_match_min_probability: float = 0.01,
) -> list[dict[str, object]]:
    """``rows`` son filas de cola de revisión (ver ``review.review_row``)."""

    eligible: dict[tuple[str, str], list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        band = str(row["band"])
        if band not in band_shares:
            continue
        if band == "non_match" and float(row["match_probability"]) < non_match_min_probability:  # type: ignore[arg-type]
            continue
        pair = "|".join(sorted((str(row["left"]["supermarket_id"]), str(row["right"]["supermarket_id"]))))  # type: ignore[index]
        eligible[(band, pair)].append(row)
    rng = random.Random(seed)
    sampled: list[dict[str, object]] = []
    band_available = Counter()
    for (band, _), items in eligible.items():
        band_available[band] += len(items)
    band_targets = _allocate_bands(size, band_shares, band_available)
    for band, target in sorted(band_targets.items()):
        strata = {pair: len(items) for (stratum_band, pair), items in eligible.items() if stratum_band == band}
        allocation = _allocate(target, strata)
        for pair, count in sorted(allocation.items()):
            population = sorted(eligible[(band, pair)], key=lambda item: str(item["candidate_id"]))
            chosen = rng.sample(population, count) if count < len(population) else population
            for item in sorted(chosen, key=lambda value: str(value["candidate_id"])):
                sampled.append(
                    {
                        "row": item,
                        "stratum_band": band,
                        "stratum_retailer_pair": pair,
                        "stratum_population": len(population),
                        "stratum_sample": count,
                    }
                )
    return sampled


def _allocate_bands(size: int, shares: Mapping[str, float], available: Mapping[str, int]) -> dict[str, int]:
    targets = {band: min(available.get(band, 0), int(round(size * share))) for band, share in shares.items()}
    shortfall = size - sum(targets.values())
    for band in sorted(shares, key=lambda key: -shares[key]):
        if shortfall <= 0:
            break
        extra = min(shortfall, available.get(band, 0) - targets[band])
        targets[band] += extra
        shortfall -= extra
    return targets


def golden_csv(sampled: Sequence[Mapping[str, object]]) -> str:
    from .review import waterfall_text

    handle = io.StringIO()
    writer = csv.DictWriter(handle, fieldnames=GOLDEN_FIELDS)
    writer.writeheader()
    for index, item in enumerate(sampled, start=1):
        row = item["row"]
        assert isinstance(row, Mapping)
        left, right = row["left"], row["right"]
        assert isinstance(left, Mapping) and isinstance(right, Mapping)
        writer.writerow(
            {
                "golden_id": f"g{index:04d}",
                "city": row["city"],
                "stratum_band": item["stratum_band"],
                "stratum_retailer_pair": item["stratum_retailer_pair"],
                "stratum_population": item["stratum_population"],
                "stratum_sample": item["stratum_sample"],
                "candidate_id": row["candidate_id"],
                "match_probability": row["match_probability"],
                "band": row["band"],
                "left_source_record_id": left["source_record_id"],
                "left_supermarket_id": left["supermarket_id"],
                "left_source_name": left["source_name"],
                "left_source_brand": left.get("source_brand") or "",
                "left_size": left.get("size") or "",
                "left_current_price": left.get("current_price") or "",
                "right_source_record_id": right["source_record_id"],
                "right_supermarket_id": right["supermarket_id"],
                "right_source_name": right["source_name"],
                "right_source_brand": right.get("source_brand") or "",
                "right_size": right.get("size") or "",
                "right_current_price": right.get("current_price") or "",
                "waterfall": waterfall_text(row.get("waterfall") or []),  # type: ignore[arg-type]
                "label": "",
                "labeler": "",
                "labeled_at_utc": "",
                "notes": "",
            }
        )
    return handle.getvalue()


def read_golden_csv(text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text))
    missing = {"golden_id", "stratum_band", "stratum_retailer_pair", "stratum_population", "stratum_sample", "band", "label"} - set(reader.fieldnames or ())
    if missing:
        raise GoldenSetError("golden_columns_missing:" + ",".join(sorted(missing)))
    rows = [dict(row) for row in reader]
    for row in rows:
        label = (row.get("label") or "").strip()
        if label and label not in GOLDEN_LABELS:
            raise GoldenSetError(f"golden_label_invalid:{row.get('golden_id')}:{label}")
    return rows


def evaluate_golden(rows: Iterable[Mapping[str, str]]) -> dict[str, object]:
    """Precisión/recall/F1 por banda y por par de supermercados.

    - precisión de una banda = matches / etiquetados en la banda;
    - recall del auto-match = matches en banda auto / matches etiquetados
      (ponderado por estrato para estimar la población);
    - ``unsure`` y filas sin etiqueta no cuentan.
    """

    labelled = []
    unlabelled = 0
    for row in rows:
        label = (row.get("label") or "").strip()
        if not label or label == "unsure":
            unlabelled += 1
            continue
        weight = int(row["stratum_population"]) / max(1, int(row["stratum_sample"]))
        labelled.append((row["band"], label in POSITIVE_LABELS, row["stratum_retailer_pair"], weight, label))

    def summarize(items: list[tuple[str, bool, str, float, str]]) -> dict[str, object]:
        by_band: dict[str, dict[str, object]] = {}
        for band in ("auto_match", "review", "non_match"):
            members = [item for item in items if item[0] == band]
            positives = sum(1 for item in members if item[1])
            weighted_total = sum(item[3] for item in members)
            weighted_positive = sum(item[3] for item in members if item[1])
            by_band[band] = {
                "labelled": len(members),
                "matches": positives,
                "precision": None if not members else round(positives / len(members), 4),
                "precision_wilson_lower_95": None if not members else round(wilson_lower(positives, len(members)) or 0.0, 4),
                "precision_weighted": None if not weighted_total else round(weighted_positive / weighted_total, 4),
                "label_distribution": dict(Counter(item[4] for item in members)),
            }
        tp = sum(1 for item in items if item[0] == "auto_match" and item[1])
        fp = sum(1 for item in items if item[0] == "auto_match" and not item[1])
        fn = sum(1 for item in items if item[0] != "auto_match" and item[1])
        weighted_tp = sum(item[3] for item in items if item[0] == "auto_match" and item[1])
        weighted_fn = sum(item[3] for item in items if item[0] != "auto_match" and item[1])
        weighted_fp = sum(item[3] for item in items if item[0] == "auto_match" and not item[1])
        return {
            "bands": by_band,
            "auto_match_classifier": prf(tp, fp, fn),
            "auto_match_weighted": {
                "precision": None if weighted_tp + weighted_fp == 0 else round(weighted_tp / (weighted_tp + weighted_fp), 4),
                "recall_within_candidates": None if weighted_tp + weighted_fn == 0 else round(weighted_tp / (weighted_tp + weighted_fn), 4),
            },
        }

    by_pair: dict[str, list[tuple[str, bool, str, float, str]]] = defaultdict(list)
    for item in labelled:
        by_pair[item[2]].append(item)
    return {
        "schema": GOLDEN_SCHEMA,
        "labelled_rows": len(labelled),
        "unlabelled_or_unsure_rows": unlabelled,
        "overall": summarize(labelled),
        "by_retailer_pair": {pair: summarize(items) for pair, items in sorted(by_pair.items())},
    }
