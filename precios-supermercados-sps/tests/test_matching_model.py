"""Fellegi–Sunter, blocking, clustering con restricciones y umbrales."""
from __future__ import annotations

import math
import random

import pytest

from precios_supermercados.matching.blocking import (
    BlockingSettings,
    MinHasher,
    generate_candidates,
    shingles,
)
from precios_supermercados.matching.clustering import Edge, cluster_id, constrained_clustering
from precios_supermercados.matching.comparison import FIELDS, FIELD_NAMES, ComparisonVector
from precios_supermercados.matching.config import DEFAULT_TAXONOMY_PATH, load_engine_config
from precios_supermercados.matching.fellegi_sunter import (
    FellegiSunterError,
    FellegiSunterModel,
    probability_from_weight,
)
from precios_supermercados.matching.standardize import Standardizer
from precios_supermercados.matching.taxonomy import load_source_taxonomy
from precios_supermercados.matching.text import TfidfModel
from precios_supermercados.matching.thresholds import (
    LabelledScore,
    band_metrics,
    choose_thresholds,
    wilson_lower,
)

from _matching_fixtures import build_records

LEVEL_COUNT = [len(levels) for _, levels in FIELDS]


def _row(**labels: str) -> tuple[int, ...]:
    return tuple(
        dict(FIELDS)[name].index(labels.get(name, dict(FIELDS)[name][-1]))
        for name in FIELD_NAMES
    )


def _vector(row: tuple[int, ...]) -> ComparisonVector:
    return ComparisonVector("a", "b", row, "missing", 0.5, (), ())


# -- Fellegi–Sunter --------------------------------------------------------------
def test_fellegi_sunter_weights_waterfall_and_roundtrip() -> None:
    good = _row(brand="exact", name="very_high", size="exact", variant="agree", price="ratio_le_1_3")
    bad = _row(brand="conflict", name="very_low", size="conflict", variant="conflict", price="ratio_gt_3")
    model = FellegiSunterModel(weight_caps_bits={"price": 1.0})
    model.fit_m([good] * 90 + [bad] * 10)
    model.fit_u([bad] * 90 + [good] * 10)
    model.prior = 0.1
    weight_good, probability_good = model.score(_vector(good))
    weight_bad, probability_bad = model.score(_vector(bad))
    assert probability_good > 0.99 > 0.01 > probability_bad
    waterfall = model.waterfall(_vector(good))
    assert waterfall[0]["field"] == "prior"
    assert sum(float(row["log2_weight"]) for row in waterfall) == pytest.approx(weight_good, abs=1e-3)
    price_row = next(row for row in waterfall if row["field"] == "price")
    assert abs(float(price_row["log2_weight"])) <= 1.0
    restored = FellegiSunterModel.from_json(model.to_json())
    assert restored.score(_vector(good)) == pytest.approx((weight_good, probability_good))
    assert len(model.parameters_table()) == sum(LEVEL_COUNT)


def test_fellegi_sunter_em_recovers_prior() -> None:
    rng = random.Random(3)
    good = _row(brand="exact", name="very_high", size="exact")
    bad = _row(brand="conflict", name="very_low", size="conflict")
    model = FellegiSunterModel()
    model.fit_m([good] * 95 + [bad] * 5)
    model.fit_u([bad] * 95 + [good] * 5)
    sample = [good if rng.random() < 0.3 else bad for _ in range(2000)]
    model.prior = 0.01
    prior = model.fit_prior_em(sample)
    assert prior == pytest.approx(0.3, abs=0.05)
    model.fit_em(sample, iterations=20)
    assert model.training["m_source"] == "em_on_candidates"
    with pytest.raises(FellegiSunterError):
        FellegiSunterModel().fit_u([])
    with pytest.raises(FellegiSunterError):
        FellegiSunterModel().fit_em([good])
    assert probability_from_weight(100) == 1.0 and probability_from_weight(-100) == 0.0
    assert probability_from_weight(0) == 0.5


# -- blocking ----------------------------------------------------------------------
def _standardized():  # type: ignore[no-untyped-def]
    config = load_engine_config()
    items = Standardizer(
        build_records(),
        vocabulary=config.variant_vocabulary,
        taxonomy=load_source_taxonomy(DEFAULT_TAXONOMY_PATH),
        synonyms=config.token_synonyms,
    ).run()
    tfidf = TfidfModel(item.core_text for item in items)
    for item in items:
        item.tfidf = tfidf.vector(item.core_text)
    return items


def test_minhash_is_deterministic() -> None:
    hasher = MinHasher(16, seed=7)
    assert hasher.signature(shingles("jugo naranja", 3)) == MinHasher(16, seed=7).signature(shingles("jugo naranja", 3))
    assert hasher.signature([]) == tuple([(1 << 61) - 1] * 16)


def test_candidates_recover_cross_retailer_matches_without_gtin() -> None:
    items = _standardized()
    candidates = generate_candidates(items, BlockingSettings(), include_gtin_rule=False)
    by_id = {item.source_record_id: item for item in items}
    for left, right in candidates.pairs:
        assert by_id[left].supermarket_id != by_id[right].supermarket_id
    # Cada producto Walmart recupera su par de Colonial (nombre en mayúsculas y pegado).
    recovered = sum(
        1 for index in range(30) if ("colonial:" + str(300 + index), "walmart:" + str(100 + index)) in candidates.pairs
    )
    assert recovered >= 27
    assert candidates.diagnostics["candidate_pairs"] == len(candidates)
    again = generate_candidates(items, BlockingSettings(), include_gtin_rule=False)
    assert again.pairs == candidates.pairs


def test_oversized_blocks_are_skipped() -> None:
    items = _standardized()
    settings = BlockingSettings(max_block_size=1, max_bucket_size=1, top_k_per_retailer=1)
    candidates = generate_candidates(items, settings, include_gtin_rule=False)
    assert candidates.diagnostics["oversized_blocks_skipped"] > 0
    assert all(sources <= {"exact_name", "retailer_twin"} for sources in candidates.pairs.values()) or len(candidates) < 200


# -- clustering -----------------------------------------------------------------------
def test_constrained_clustering_respects_one_offer_per_context() -> None:
    slot = {"a1": "walmart_sps", "a2": "walmart_sps", "b": "colonial_sps", "c": "la_colonia_sps"}
    edges = [Edge("a1", "b", 9.0, False), Edge("a2", "b", 8.0, False), Edge("b", "c", 7.0, False), Edge("a1", "c", 6.0, False)]
    result = constrained_clustering(edges, slot, lambda a, b: "auto", linkage="complete")
    assert [cluster.members for cluster in result.clusters] == [("a1", "b", "c")]
    assert result.rejected["retailer_collision"] == 1
    assert result.clusters[0].cluster_id == cluster_id(["c", "b", "a1"])


def test_complete_linkage_blocks_unsupported_transitivity() -> None:
    slot = {"a": "s1", "b": "s2", "c": "s3"}
    edges = [Edge("a", "b", 9.0, False), Edge("b", "c", 8.0, False)]
    status = {("a", "c"): "review", ("c", "a"): "review"}
    complete = constrained_clustering(edges, slot, lambda x, y: status.get((x, y), "auto"), linkage="complete")
    assert [cluster.members for cluster in complete.clusters] == [("a", "b")]
    assert complete.rejected["complete_linkage"] == 1
    single = constrained_clustering(edges, slot, lambda x, y: "review", linkage="single")
    assert [cluster.members for cluster in single.clusters] == [("a", "b", "c")]
    gtin_first = constrained_clustering(
        [Edge("a", "b", 1.0, True), Edge("a", "c", 50.0, False)],
        {"a": "s1", "b": "s2", "c": "s2"},
        lambda x, y: "auto",
    )
    assert gtin_first.clusters[0].members == ("a", "b") and gtin_first.clusters[0].gtin_only
    with pytest.raises(ValueError):
        constrained_clustering([], {}, lambda x, y: "auto", linkage="average")


# -- umbrales -----------------------------------------------------------------------
def test_choose_thresholds_precision_first() -> None:
    scores = [LabelledScore(0.99, True, True)] * 98 + [LabelledScore(0.99, False, True)] * 1
    scores += [LabelledScore(0.9, True, True)] * 50 + [LabelledScore(0.9, False, True)] * 10
    scores += [LabelledScore(0.5, True, False)] * 20 + [LabelledScore(0.5, False, False)] * 10
    scores += [LabelledScore(0.2, True, True)] * 2 + [LabelledScore(0.2, False, True)] * 400
    thresholds = choose_thresholds(scores, target_precision=0.98, min_support=50)
    assert thresholds.auto == 0.99
    assert thresholds.auto_precision == pytest.approx(98 / 99, abs=1e-6)
    assert thresholds.review == 0.5
    assert thresholds.band(0.995, auto_eligible=True, hard_conflict=False) == "auto_match"
    assert thresholds.band(0.995, auto_eligible=False, hard_conflict=False) == "review"
    assert thresholds.band(0.995, auto_eligible=True, hard_conflict=True) == "non_match"
    assert thresholds.band(0.1, auto_eligible=True, hard_conflict=False) == "non_match"
    fallback = choose_thresholds([LabelledScore(0.9, False, True)] * 10, min_support=5)
    assert fallback.source == "fallback_insufficient_labels"


def test_wilson_and_band_metrics() -> None:
    assert wilson_lower(0, 0) is None
    assert wilson_lower(98, 100) == pytest.approx(0.9300, abs=1e-3)
    metrics = band_metrics(
        [("auto_match", True, "a|b"), ("auto_match", False, "a|b"), ("review", True, "a|c"), ("non_match", True, "a|c")]
    )
    assert metrics["all"]["auto_match"]["precision"] == 0.5
    assert metrics["all"]["auto_match"]["recall"] == pytest.approx(1 / 3, abs=1e-3)
    assert metrics["a|c"]["auto_or_review"]["recall"] == 0.5
    assert not math.isnan(metrics["all"]["auto_or_review"]["f1"])


def test_segmented_thresholds_are_stricter_for_hard_and_unlabelled_pairs() -> None:
    from precios_supermercados.matching.thresholds import choose_segmented_thresholds

    easy = [LabelledScore(0.7, True, True, "paiz|walmart")] * 200
    hard = [LabelledScore(0.7, True, True, "colonial|walmart")] * 60 + [LabelledScore(0.7, False, True, "colonial|walmart")] * 20
    hard += [LabelledScore(0.95, True, True, "colonial|walmart")] * 60
    segmented = choose_segmented_thresholds(easy + hard, target_precision=0.98, min_support=50)
    assert segmented.for_group("paiz|walmart").auto == 0.7
    assert segmented.for_group("colonial|walmart").auto == 0.95
    # Pares sin etiquetas (p. ej. Comisariato): sin auto-match, prioridad en revisión.
    assert segmented.for_group("comisariato_los_andes|walmart").source == "unmeasured_segment_review_only"
    assert segmented.band(0.99, auto_eligible=True, hard_conflict=False, group="comisariato_los_andes|walmart") == "review"
    assert segmented.unmeasured_auto == 0.95
    assert segmented.band(0.8, auto_eligible=True, hard_conflict=False, group="paiz|walmart") == "auto_match"
    assert segmented.band(0.8, auto_eligible=True, hard_conflict=False, group="colonial|walmart") != "auto_match"
    assert set(segmented.to_json()) == {
        "pooled",
        "unlabelled_retailer_pairs",
        "unmeasured_high_confidence_min_probability",
        "by_retailer_pair",
    }


def test_wilson_criterion_and_disabled_segments() -> None:
    from precios_supermercados.matching.thresholds import AUTO_DISABLED, choose_segmented_thresholds

    point = [LabelledScore(0.9, True, True)] * 99 + [LabelledScore(0.9, False, True)]
    assert choose_thresholds(point, min_support=50).auto == 0.9
    assert choose_thresholds(point, min_support=50, confidence_z=1.2816).source == "fallback_insufficient_labels"
    easy = [LabelledScore(0.7, True, True, "paiz|walmart")] * 300
    weak = [LabelledScore(0.8, True, True, "colonial|walmart")] * 5 + [LabelledScore(0.8, False, True, "colonial|walmart")] * 5
    segmented = choose_segmented_thresholds(easy + weak, min_support=50)
    disabled = segmented.for_group("colonial|walmart")
    assert disabled.auto == AUTO_DISABLED and disabled.source == "auto_disabled_precision_below_target"
    assert segmented.band(0.99, auto_eligible=True, hard_conflict=False, group="colonial|walmart") == "review"
