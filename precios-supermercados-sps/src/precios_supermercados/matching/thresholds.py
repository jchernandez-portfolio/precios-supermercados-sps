"""Umbrales precision-first y métricas de evaluación.

El umbral de auto-match es el menor umbral de probabilidad cuya precisión
acumulada (parejas etiquetadas con probabilidad ≥ t, elegibles para auto) es
≥ ``target_precision`` (0.98 por defecto) con soporte mínimo. La banda media
[t_review, t_auto) va a revisión humana; el resto es no-match.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Iterable, Mapping, Sequence

BANDS = ("auto_match", "review", "non_match")
AUTO_DISABLED = 1.01  # probabilidad inalcanzable: el segmento no tiene auto-match


@dataclass(frozen=True, slots=True)
class LabelledScore:
    probability: float
    is_match: bool
    auto_eligible: bool
    group: str = "all"


@dataclass(frozen=True, slots=True)
class Thresholds:
    auto: float
    review: float
    auto_precision: float | None
    auto_precision_wilson_lower: float | None
    auto_support: int
    review_recall: float | None
    source: str
    review_band_precision: float | None = None

    def band(self, probability: float, *, auto_eligible: bool, hard_conflict: bool) -> str:
        if hard_conflict:
            return "non_match"
        if probability >= self.auto and auto_eligible:
            return "auto_match"
        if probability >= self.review:
            return "review"
        return "non_match"

    def to_json(self) -> dict[str, object]:
        return {
            "auto_match_min_probability": self.auto,
            "review_min_probability": self.review,
            "auto_precision_at_threshold": self.auto_precision,
            "auto_precision_wilson_lower_95": self.auto_precision_wilson_lower,
            "auto_support": self.auto_support,
            "review_recall_at_threshold": self.review_recall,
            "review_band_precision_calibration": self.review_band_precision,
            "source": self.source,
        }


def wilson_lower(successes: int, total: int, z: float = 1.959964) -> float | None:
    if total == 0:
        return None
    phat = successes / total
    denominator = 1 + z * z / total
    centre = phat + z * z / (2 * total)
    margin = z * math.sqrt(phat * (1 - phat) / total + z * z / (4 * total * total))
    return (centre - margin) / denominator


def choose_thresholds(
    scores: Sequence[LabelledScore],
    *,
    target_precision: float = 0.98,
    min_support: int = 50,
    review_min_precision: float = 0.5,
    review_floor: float = 0.05,
    fallback_auto: float = 0.995,
    fallback_review: float = 0.5,
    confidence_z: float = 0.0,
) -> Thresholds:
    """Elige t_auto (precisión ≥ objetivo) y t_review (precisión acumulada ≥ piso).

    Con ``confidence_z > 0`` el criterio es el límite inferior de Wilson (no la
    precisión puntual): elegir "el menor umbral que llega a 0.98" sobre muchos
    umbrales candidatos sobreestima la precisión fuera de muestra.

    Las parejas con conflicto duro o no recuperadas llegan con probabilidad 0 y
    no cuentan para la banda de revisión.
    """

    eligible = sorted((item for item in scores if item.auto_eligible), key=lambda item: -item.probability)
    auto = None
    precision_at = None
    support_at = 0
    tp = fp = 0
    index = 0
    while index < len(eligible):
        probability = eligible[index].probability
        while index < len(eligible) and eligible[index].probability == probability:
            if eligible[index].is_match:
                tp += 1
            else:
                fp += 1
            index += 1
        support = tp + fp
        precision = tp / support
        bound = precision if confidence_z <= 0 else (wilson_lower(tp, support, confidence_z) or 0.0)
        if support >= min_support and bound >= target_precision:
            auto, precision_at, support_at = probability, precision, support
    source = "silver_labels"
    if auto is None:
        auto, source = fallback_auto, "fallback_insufficient_labels"
        tp_fb = sum(1 for item in eligible if item.probability >= auto and item.is_match)
        support_at = sum(1 for item in eligible if item.probability >= auto)
        precision_at = tp_fb / support_at if support_at else None
    auto_tp = round(precision_at * support_at) if precision_at is not None else 0

    reviewable = sorted((item for item in scores if item.probability > 0), key=lambda item: -item.probability)
    positives_total = sum(1 for item in reviewable if item.is_match)
    review = fallback_review
    review_recall = None
    if reviewable and positives_total:
        tp = fp = 0
        candidate = None
        index = 0
        while index < len(reviewable):
            probability = reviewable[index].probability
            while index < len(reviewable) and reviewable[index].probability == probability:
                tp += int(reviewable[index].is_match)
                fp += int(not reviewable[index].is_match)
                index += 1
            if tp / (tp + fp) >= review_min_precision:
                candidate = probability
        review = candidate if candidate is not None else fallback_review
        review = max(review_floor, min(review, auto))
        review_recall = sum(1 for item in reviewable if item.is_match and item.probability >= review) / positives_total
    band = [
        item
        for item in reviewable
        if review <= item.probability and not (item.probability >= auto and item.auto_eligible)
    ]
    review_band_precision = round(sum(1 for item in band if item.is_match) / len(band), 6) if band else None
    return Thresholds(
        auto=auto,
        review=review,
        auto_precision=None if precision_at is None else round(precision_at, 6),
        auto_precision_wilson_lower=None if precision_at is None else round(wilson_lower(auto_tp, support_at) or 0.0, 6),
        auto_support=support_at,
        review_recall=None if review_recall is None else round(review_recall, 6),
        source=source,
        review_band_precision=review_band_precision,
    )


def prf(tp: int, fp: int, fn: int) -> dict[str, float | int | None]:
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall > 0
        else None
    )
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": None if precision is None else round(precision, 4),
        "precision_wilson_lower_95": None if tp + fp == 0 else round(wilson_lower(tp, tp + fp) or 0.0, 4),
        "recall": None if recall is None else round(recall, 4),
        "f1": None if f1 is None else round(f1, 4),
    }


def band_metrics(
    rows: Iterable[tuple[str, bool, str]],
    *,
    total_positives_by_group: Mapping[str, int] | None = None,
) -> dict[str, object]:
    """``rows`` = (banda, es_match, grupo). Métricas por banda y por grupo.

    ``auto_match`` se evalúa como clasificador (match ↔ banda auto);
    ``auto_or_review`` mide la cobertura de la revisión humana.
    """

    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for band, is_match, group in rows:
        for key in ("all", group):
            counts[key][f"{band}:{'pos' if is_match else 'neg'}"] += 1
    result: dict[str, object] = {}
    for group, values in sorted(counts.items()):
        positives = sum(values[f"{band}:pos"] for band in BANDS)
        if total_positives_by_group is not None:
            positives = max(positives, total_positives_by_group.get(group, positives))
        auto_tp, auto_fp = values["auto_match:pos"], values["auto_match:neg"]
        review_tp, review_fp = values["review:pos"], values["review:neg"]
        result[group] = {
            "labelled_pairs": sum(values.values()),
            "positives": positives,
            "band_counts": {band: {"pos": values[f"{band}:pos"], "neg": values[f"{band}:neg"]} for band in BANDS},
            "auto_match": prf(auto_tp, auto_fp, positives - auto_tp),
            "review": prf(review_tp, review_fp, positives - review_tp),
            "auto_or_review": prf(auto_tp + review_tp, auto_fp + review_fp, positives - auto_tp - review_tp),
        }
    return result


@dataclass(frozen=True)
class SegmentedThresholds:
    """Umbrales por par de supermercados (segmento), precision-first.

    Un umbral global quedaría dominado por los pares fáciles (p. ej. Walmart y
    Paiz comparten plataforma y nombres). Cada par con soporte suficiente en
    calibración recibe su propio umbral; los pares sin etiquetas suficientes
    (PriceSmart sin GTIN; Comisariato hasta acumular etiquetas con su GTIN
    reconstruido) no tienen auto-match: su precisión no
    está medida. Sus parejas sobre el umbral más estricto observado
    (``unmeasured_auto``) van a revisión con prioridad hasta etiquetar el golden.
    """

    pooled: Thresholds
    by_group: Mapping[str, Thresholds]
    unlabelled: Thresholds
    unmeasured_auto: float = AUTO_DISABLED

    @property
    def auto(self) -> float:
        return self.unlabelled.auto

    @property
    def review(self) -> float:
        return self.pooled.review

    def for_group(self, group: str | None) -> Thresholds:
        if group is not None and group in self.by_group:
            return self.by_group[group]
        return self.unlabelled

    def band(self, probability: float, *, auto_eligible: bool, hard_conflict: bool, group: str | None = None) -> str:
        return self.for_group(group).band(probability, auto_eligible=auto_eligible, hard_conflict=hard_conflict)

    def to_json(self) -> dict[str, object]:
        return {
            "pooled": self.pooled.to_json(),
            "unlabelled_retailer_pairs": self.unlabelled.to_json(),
            "unmeasured_high_confidence_min_probability": self.unmeasured_auto,
            "by_retailer_pair": {group: value.to_json() for group, value in sorted(self.by_group.items())},
        }


def choose_segmented_thresholds(scores: Sequence[LabelledScore], **settings: float) -> SegmentedThresholds:
    pooled = choose_thresholds(scores, **settings)  # type: ignore[arg-type]
    target = float(settings.get("target_precision", 0.98))
    groups: dict[str, list[LabelledScore]] = defaultdict(list)
    for item in scores:
        groups[item.group].append(item)
    by_group: dict[str, Thresholds] = {}
    unresolved: dict[str, list[LabelledScore]] = {}
    for group, items in sorted(groups.items()):
        chosen = choose_thresholds(items, **settings)  # type: ignore[arg-type]
        if chosen.source != "silver_labels":
            unresolved[group] = items
            continue
        # La banda de revisión es común; sólo el auto-match se segmenta.
        by_group[group] = replace(chosen, review=min(pooled.review, chosen.auto), source="silver_labels_segment")
    strictest = max([pooled.auto, *(value.auto for value in by_group.values())])
    # Pares con etiquetas pero sin soporte suficiente: se prueba el umbral más
    # estricto en sus propias etiquetas; si no llega al objetivo, sin auto-match.
    for group, items in unresolved.items():
        above = [item for item in items if item.auto_eligible and item.probability >= strictest]
        tp = sum(1 for item in above if item.is_match)
        if above and tp / len(above) < target:
            by_group[group] = Thresholds(
                auto=AUTO_DISABLED,
                review=pooled.review,
                auto_precision=round(tp / len(above), 6),
                auto_precision_wilson_lower=round(wilson_lower(tp, len(above)) or 0.0, 6),
                auto_support=len(above),
                review_recall=None,
                source="auto_disabled_precision_below_target",
            )
        elif above:
            by_group[group] = Thresholds(
                auto=strictest,
                review=min(pooled.review, strictest),
                auto_precision=round(tp / len(above), 6),
                auto_precision_wilson_lower=round(wilson_lower(tp, len(above)) or 0.0, 6),
                auto_support=len(above),
                review_recall=None,
                source="strictest_threshold_checked_low_support",
            )
    unlabelled = Thresholds(
        auto=AUTO_DISABLED,
        review=pooled.review,
        auto_precision=None,
        auto_precision_wilson_lower=None,
        auto_support=0,
        review_recall=None,
        source="unmeasured_segment_review_only",
    )
    return SegmentedThresholds(pooled=pooled, by_group=by_group, unlabelled=unlabelled, unmeasured_auto=strictest)
