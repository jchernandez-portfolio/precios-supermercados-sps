"""Orquestación del motor shadow por ciudad.

``MatchingEngine`` encadena estandarización → blocking → comparación →
Fellegi–Sunter → umbrales → clustering → salidas. Las etiquetas *silver* son
parejas entre supermercados que comparten un GTIN válido y no restringido
(``barcode`` o ``silver_gtin``). Se dividen por hash del GTIN en
entrenamiento (60 %), calibración de umbrales (20 %) y prueba (20 %), de modo
que la precisión reportada no se mide sobre los datos con los que se eligió el
umbral. El modelo nunca ve el GTIN: la evaluación es ciega al GTIN.
"""
from __future__ import annotations

import copy
import hashlib
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from ..product_identity_decisions import candidate_id
from .blocking import BlockingSettings, CandidateSet, generate_candidates
from .clustering import Cluster, Edge, constrained_clustering
from .comparison import ComparisonSettings, ComparisonVector, compare, pair_key
from .config import EngineConfig
from .fellegi_sunter import FellegiSunterModel
from .records import MatchRecord
from .standardize import StandardizedRecord, Standardizer
from .taxonomy import SourceTaxonomy
from .text import TfidfModel
from .thresholds import LabelledScore, SegmentedThresholds, band_metrics, choose_segmented_thresholds, prf
from .unit_price import comparable_alternatives

SPLITS = ("train", "calibration", "test")


def split_for_key(value: str) -> str:
    bucket = int(hashlib.sha256(value.encode("utf-8")).hexdigest()[:8], 16) % 10
    if bucket < 6:
        return "train"
    if bucket < 8:
        return "calibration"
    return "test"


def retailer_pair(left: str, right: str) -> str:
    return "|".join(sorted((left, right)))


@dataclass
class ScoredPair:
    vector: ComparisonVector
    weight: float
    probability: float
    band: str
    decision_source: str
    sources: tuple[str, ...] = ()

    @property
    def key(self) -> tuple[str, str]:
        return (self.vector.left_id, self.vector.right_id)


@dataclass
class SilverLabel:
    is_match: bool
    split: str
    gtins: tuple[str, ...]


@dataclass
class CityRun:
    city: str
    records: list[StandardizedRecord]
    by_id: dict[str, StandardizedRecord]
    candidates: CandidateSet
    vectors: dict[tuple[str, str], ComparisonVector]
    silver_positive_vectors: dict[tuple[str, str], ComparisonVector]
    labels: dict[tuple[str, str], SilverLabel]
    leaf_types: dict[str, object]
    scored: dict[tuple[str, str], ScoredPair] = field(default_factory=dict)
    clusters: list[Cluster] = field(default_factory=list)
    clustering_rejected: dict[str, int] = field(default_factory=dict)
    alternatives: list[dict[str, object]] = field(default_factory=list)
    thresholds: SegmentedThresholds | None = None


class MatchingEngine:
    def __init__(self, config: EngineConfig, taxonomy: SourceTaxonomy | None = None) -> None:
        self.config = config
        self.taxonomy = taxonomy
        self.blocking = BlockingSettings.from_config(config.section("blocking"))
        self.comparison = ComparisonSettings.from_config(config.section("comparison"), config.implicit_defaults)
        model_section = config.section("model")
        self.seed = int(model_section.get("seed", 20260930))
        self.u_pairs = int(model_section.get("u_random_pairs", 120000))
        self.laplace = float(model_section.get("laplace", 1.0))
        self.em_iterations = int(model_section.get("em_iterations", 40))
        self.weight_caps = {str(k): float(v) for k, v in dict(model_section.get("weight_caps_bits") or {}).items()}
        self.balance_retailer_pairs = bool(model_section.get("balance_retailer_pairs", False))
        clustering = config.section("clustering")
        self.linkage = str(clustering.get("linkage", "complete"))
        self.max_cluster_size = int(clustering.get("max_cluster_size", 8))
        self.threshold_settings = config.section("thresholds")
        self.rare_token_share = float(config.section("comparison").get("rare_token_share", 0.005))

    # -- 1. preparación ----------------------------------------------------
    def prepare_city(self, city: str, records: Sequence[MatchRecord]) -> CityRun:
        standardizer = Standardizer(
            records,
            vocabulary=self.config.variant_vocabulary,
            taxonomy=self.taxonomy,
            synonyms=self.config.token_synonyms,
        )
        standardized = standardizer.run()
        tfidf = TfidfModel(item.core_text for item in standardized)
        token_df = Counter(token for item in standardized for token in set(item.core_tokens))
        rare_limit = max(3, int(len(standardized) * self.rare_token_share))
        for item in standardized:
            item.tfidf = tfidf.vector(item.core_text)
            item.rare_tokens = frozenset(token for token in item.core_tokens if token_df[token] <= rare_limit)
        by_id = {item.source_record_id: item for item in standardized}
        candidates = generate_candidates(standardized, self.blocking)
        vectors = {
            key: compare(by_id[key[0]], by_id[key[1]], self.comparison)
            for key in candidates.pairs
        }
        positives, labels = self._silver_labels(standardized, by_id, candidates)
        positive_vectors = {}
        for key in positives:
            vector = vectors.get(key)
            if vector is None:
                vector = compare(by_id[key[0]], by_id[key[1]], self.comparison)
            positive_vectors[key] = vector
        return CityRun(
            city=city,
            records=standardized,
            by_id=by_id,
            candidates=candidates,
            vectors=vectors,
            silver_positive_vectors=positive_vectors,
            labels=labels,
            leaf_types={key: value.__dict__ for key, value in standardizer.leaf_types.items()},
        )

    def _silver_labels(
        self,
        records: Sequence[StandardizedRecord],
        by_id: Mapping[str, StandardizedRecord],
        candidates: CandidateSet,
    ) -> tuple[set[tuple[str, str]], dict[tuple[str, str], SilverLabel]]:
        groups: dict[str, list[StandardizedRecord]] = defaultdict(list)
        for record in records:
            if record.silver_gtin and not record.record.name_reconstructed:
                groups[record.silver_gtin].append(record)
        positives: set[tuple[str, str]] = set()
        labels: dict[tuple[str, str], SilverLabel] = {}
        for gtin, members in groups.items():
            if len(members) > 10 or len({member.supermarket_id for member in members}) < 2:
                continue
            split = split_for_key(gtin)
            for index, left in enumerate(members):
                for right in members[index + 1 :]:
                    if left.supermarket_id == right.supermarket_id:
                        continue
                    key = pair_key(left.source_record_id, right.source_record_id)
                    positives.add(key)
                    labels[key] = SilverLabel(True, split, (gtin,))
        # Negativos silver "duros" (exclusión 1:1): A tiene pareja GTIN B en la
        # cadena de C, C ≠ B y C tiene otro GTIN válido. "GTIN distinto" a
        # secas no basta: el mismo producto circula con GTIN de origen/empaque
        # distinto entre cadenas, lo que contaminaría las etiquetas negativas.
        partners: dict[tuple[str, str], set[str]] = defaultdict(set)
        for left_id, right_id in positives:
            partners[(left_id, by_id[right_id].supermarket_id)].add(right_id)
            partners[(right_id, by_id[left_id].supermarket_id)].add(left_id)
        for key in candidates.pairs:
            if key in labels:
                continue
            left, right = by_id[key[0]], by_id[key[1]]
            if left.supermarket_id == right.supermarket_id:
                continue
            if left.record.name_reconstructed or right.record.name_reconstructed:
                continue
            if not left.silver_gtin or not right.silver_gtin or left.silver_gtin == right.silver_gtin:
                continue
            left_partners = partners.get((left.source_record_id, right.supermarket_id), set())
            right_partners = partners.get((right.source_record_id, left.supermarket_id), set())
            if not left_partners and not right_partners:
                continue
            # Etiqueta ambigua: C es un listado casi idéntico de la pareja B en
            # la misma cadena (otro GTIN para el mismo artículo). No se usa.
            if any(
                self._near_duplicate(by_id[partner], right) for partner in left_partners
            ) or any(self._near_duplicate(by_id[partner], left) for partner in right_partners):
                continue
            gtins = tuple(sorted((left.silver_gtin, right.silver_gtin)))
            labels[key] = SilverLabel(False, split_for_key(gtins[0]), gtins)
        return positives, labels

    @staticmethod
    def _group(run: CityRun, key: tuple[str, str]) -> str:
        return retailer_pair(run.by_id[key[0]].supermarket_id, run.by_id[key[1]].supermarket_id)

    def _near_duplicate(self, a: StandardizedRecord, b: StandardizedRecord) -> bool:
        labels = compare(a, b, self.comparison).as_labels()
        return (
            labels["name"] == "very_high"
            and labels["size"] in {"exact", "missing"}
            and labels["brand"] in {"exact", "fuzzy", "both_missing"}
            and labels["variant"] in {"agree", "none"}
            and labels["codes"] in {"agree", "none"}
        )

    # -- 2. entrenamiento --------------------------------------------------
    def random_pair_vectors(self, run: CityRun, count: int, rng: random.Random) -> list[tuple[int, ...]]:
        records = run.records
        rows: list[tuple[int, ...]] = []
        if len(records) < 2 or len({item.supermarket_id for item in records}) < 2:
            return rows
        attempts = 0
        while len(rows) < count and attempts < count * 20:
            attempts += 1
            left = records[rng.randrange(len(records))]
            right = records[rng.randrange(len(records))]
            if left.supermarket_id == right.supermarket_id:
                continue
            if left.silver_gtin and left.silver_gtin == right.silver_gtin:
                continue
            rows.append(compare(left, right, self.comparison).levels)
        return rows

    def train(self, runs: Sequence[CityRun]) -> FellegiSunterModel:
        """Entrena m/u/λ.

        - ``m``: parejas silver positivas (GTIN compartido) del split train;
        - ``u``: parejas *candidatas* etiquetadas como no-match (GTIN distinto)
          del split train. Estimar ``u`` dentro de la población bloqueada
          corrige la correlación marca–nombre que existe entre no-matches de un
          mismo bloque; ``u`` por parejas aleatorias (Splink clásico) se guarda
          como diagnóstico y se usa sólo si faltan negativos;
        - ``λ``: EM sobre todas las parejas candidatas con m/u fijos.
        """

        rng = random.Random(self.seed)
        total_records = sum(len(run.records) for run in runs) or 1
        random_rows: list[tuple[int, ...]] = []
        for run in runs:
            share = max(1000, round(self.u_pairs * len(run.records) / total_records))
            random_rows.extend(self.random_pair_vectors(run, share, rng))
        negatives = [
            (run.vectors[key].levels, self._group(run, key))
            for run in runs
            for key, label in run.labels.items()
            if not label.is_match and label.split == "train" and key in run.vectors
        ]
        positives = [
            (vector.levels, self._group(run, key))
            for run in runs
            for key, vector in run.silver_positive_vectors.items()
            if run.labels[key].split == "train"
        ]
        if self.balance_retailer_pairs:
            negative_rows, negative_weights = _balanced(negatives)
            m_rows, m_weights = _balanced(positives)
        else:
            negative_rows, negative_weights = [levels for levels, _ in negatives], None
            m_rows, m_weights = [levels for levels, _ in positives], None
        model = FellegiSunterModel(laplace=self.laplace, weight_caps_bits=dict(self.weight_caps))
        random_model = FellegiSunterModel(laplace=self.laplace)
        random_model.fit_u(random_rows)
        if len(negative_rows) >= 1000:
            model.fit_u(negative_rows, negative_weights)
            model.training["u_source"] = "silver_negative_candidate_pairs"
        else:
            model.fit_u(random_rows)
            model.training["u_source"] = "random_cross_retailer_pairs"
        model.training["u_random_pairs"] = len(random_rows)
        model.training["u_random"] = random_model.u
        model.fit_m(m_rows, m_weights)
        model.training["pair_weighting"] = "balanced_by_retailer_pair" if self.balance_retailer_pairs else "observed"
        model.training["train_positive_pairs_by_retailer_pair"] = dict(sorted(Counter(group for _, group in positives).items()))
        candidate_rows = [vector.levels for run in runs for vector in run.vectors.values() if not vector.same_retailer]
        model.prior = 1e-2
        model.fit_prior_em(candidate_rows, iterations=self.em_iterations)
        model.training["train_positive_pairs"] = len(m_rows)
        model.training["train_negative_pairs"] = len(negative_rows)
        model.training["candidate_pairs"] = len(candidate_rows)
        return model

    def train_em_variant(self, runs: Sequence[CityRun], supervised: FellegiSunterModel) -> FellegiSunterModel:
        model = copy.deepcopy(supervised)
        candidate_rows = [vector.levels for run in runs for vector in run.vectors.values() if not vector.same_retailer]
        model.fit_em(candidate_rows, iterations=self.em_iterations)
        return model

    # -- 3. scoring --------------------------------------------------------
    def score_vector(
        self,
        vector: ComparisonVector,
        model: FellegiSunterModel,
        thresholds: SegmentedThresholds,
        *,
        gtin_blind: bool,
        sources: tuple[str, ...] = (),
        group: str | None = None,
    ) -> ScoredPair:
        weight, probability = model.score(vector)
        if vector.same_retailer:
            # Mismo supermercado en otro contexto (tienda): sólo se une con GTIN o
            # nombre exacto y sin contradicciones; nunca por probabilidad.
            others = [item for item in vector.hard_conflicts if item != "same_retailer"]
            if not others and (vector.gtin_level == "same" or vector.exact_name):
                return ScoredPair(vector, weight, 1.0, "auto_match", "retailer_twin", sources)
            return ScoredPair(vector, weight, probability, "non_match", "same_retailer", sources)
        hard = [item for item in vector.hard_conflicts if not (gtin_blind and item == "gtin_different")]
        if not gtin_blind and vector.gtin_level == "same":
            if hard:
                return ScoredPair(vector, weight, probability, "review", "gtin_rule_with_conflict", sources)
            return ScoredPair(vector, weight, 1.0, "auto_match", "gtin_rule", sources)
        if hard:
            return ScoredPair(vector, weight, probability, "non_match", "hard_conflict", sources)
        band = thresholds.band(probability, auto_eligible=not vector.auto_caps, hard_conflict=False, group=group)
        return ScoredPair(vector, weight, probability, band, "fellegi_sunter", sources)

    def labelled_scores(
        self,
        runs: Sequence[CityRun],
        model: FellegiSunterModel,
        split: str,
    ) -> list[tuple[CityRun, tuple[str, str], LabelledScore, bool]]:
        """Parejas etiquetadas de un split, ciegas al GTIN.

        Devuelve también si la pareja fue recuperada por blocking sin GTIN.
        """

        rows = []
        for run in runs:
            for key, label in run.labels.items():
                if label.split != split:
                    continue
                sources = run.candidates.pairs.get(key, set())
                retrieved = bool(sources - {"gtin"})
                vector = run.vectors.get(key) or run.silver_positive_vectors.get(key)
                if vector is None:
                    continue
                _, probability = model.score(vector)
                hard = [item for item in vector.hard_conflicts if item != "gtin_different"]
                if hard or not retrieved:
                    probability_used = 0.0
                else:
                    probability_used = probability
                group = retailer_pair(run.by_id[key[0]].supermarket_id, run.by_id[key[1]].supermarket_id)
                rows.append(
                    (
                        run,
                        key,
                        LabelledScore(probability_used, label.is_match, not vector.auto_caps and not hard, group),
                        retrieved,
                    )
                )
        return rows

    def calibrate(self, runs: Sequence[CityRun], model: FellegiSunterModel) -> SegmentedThresholds:
        # Sólo parejas recuperadas por blocking ciego al GTIN: el umbral evalúa
        # el scoring; el recall de blocking se reporta aparte.
        scores = [item[2] for item in self.labelled_scores(runs, model, "calibration") if item[3]]
        settings = self.threshold_settings
        return choose_segmented_thresholds(
            scores,
            target_precision=float(settings.get("target_precision", 0.98)),
            min_support=int(settings.get("min_support", 50)),
            review_min_precision=float(settings.get("review_min_precision", 0.5)),
            confidence_z=float(settings.get("confidence_z", 0.0)),
            review_floor=float(settings.get("review_floor_probability", 0.05)),
            fallback_auto=float(settings.get("fallback_auto_probability", 0.995)),
            fallback_review=float(settings.get("fallback_review_probability", 0.5)),
        )

    def evaluate(
        self,
        runs: Sequence[CityRun],
        model: FellegiSunterModel,
        thresholds: SegmentedThresholds,
        split: str = "test",
    ) -> dict[str, object]:
        rows = self.labelled_scores(runs, model, split)
        errors: dict[str, list[dict[str, object]]] = {"false_positive": [], "false_negative": []}
        band_rows = []
        positives_by_group: Counter[str] = Counter()
        retrieved_positives = 0
        for run, key, score, retrieved in rows:
            band = thresholds.band(score.probability, auto_eligible=score.auto_eligible, hard_conflict=False, group=score.group)
            if score.probability == 0.0 and not retrieved:
                band = "non_match"
            band_rows.append((band, score.is_match, score.group))
            kind = None
            if band == "auto_match" and not score.is_match:
                kind = "false_positive"
            elif band == "non_match" and score.is_match and retrieved and score.probability > 0:
                kind = "false_negative"
            if kind is not None:
                left, right = run.by_id[key[0]], run.by_id[key[1]]
                errors[kind].append(
                    {
                        "probability": round(score.probability, 4),
                        "retailer_pair": score.group,
                        "left": f"{left.supermarket_id}: {left.record.source_name}",
                        "right": f"{right.supermarket_id}: {right.record.source_name}",
                        "gtins": list(run.labels[key].gtins),
                    }
                )
            if score.is_match:
                positives_by_group[score.group] += 1
                positives_by_group["all"] += 1
                retrieved_positives += int(retrieved)
        metrics = band_metrics(band_rows)
        total_positive = positives_by_group["all"]
        return {
            "split": split,
            "labelled_pairs": len(rows),
            "positive_pairs": total_positive,
            "negative_pairs": len(rows) - total_positive,
            "blocking_recall_gtin_blind": round(retrieved_positives / total_positive, 4) if total_positive else None,
            "by_retailer_pair": metrics,
            "error_examples": {
                kind: sorted(items, key=lambda item: (-float(item["probability"]), str(item["left"])))[:40]  # type: ignore[arg-type]
                for kind, items in errors.items()
            },
        }

    def score_city(self, run: CityRun, model: FellegiSunterModel, thresholds: SegmentedThresholds) -> None:
        run.thresholds = thresholds

        def group_of(key: tuple[str, str]) -> str:
            return retailer_pair(run.by_id[key[0]].supermarket_id, run.by_id[key[1]].supermarket_id)

        run.scored = {
            key: self.score_vector(
                vector,
                model,
                thresholds,
                gtin_blind=False,
                sources=tuple(sorted(run.candidates.pairs[key])),
                group=group_of(key),
            )
            for key, vector in run.vectors.items()
        }
        context_of = {record.source_record_id: record.context_id for record in run.records}
        cache: dict[tuple[str, str], str] = {}

        def pair_status(a: str, b: str) -> str:
            key = pair_key(a, b)
            if key in cache:
                return cache[key]
            scored = run.scored.get(key)
            if scored is None:
                vector = compare(run.by_id[key[0]], run.by_id[key[1]], self.comparison)
                scored = self.score_vector(vector, model, thresholds, gtin_blind=False, group=group_of(key))
            if scored.decision_source in {"gtin_rule", "retailer_twin"}:
                status = "gtin"
            else:
                status = "auto" if scored.band == "auto_match" else scored.band
            cache[key] = status
            return status

        deterministic = {"gtin_rule", "retailer_twin"}
        edges = [
            Edge(
                scored.vector.left_id,
                scored.vector.right_id,
                1e9 if scored.decision_source in deterministic else scored.weight,
                scored.decision_source in deterministic,
            )
            for scored in run.scored.values()
            if scored.band == "auto_match"
        ]
        clustering = constrained_clustering(
            edges,
            context_of,
            pair_status,
            linkage=self.linkage,
            max_cluster_size=self.max_cluster_size,
        )
        run.clusters = clustering.clusters
        run.clustering_rejected = clustering.rejected
        run.alternatives = comparable_alternatives(run.vectors.values(), run.by_id)

    def review_selection(self, run: CityRun, *, top_k: int | None = None) -> set[tuple[str, str]]:
        """Parejas auto/revisión (no GTIN) limitadas a top-k por registro y cadena."""

        if top_k is None:
            top_k = int(self.threshold_settings.get("review_top_k_per_record", 2))
        ranked = sorted(
            (
                scored
                for scored in run.scored.values()
                if scored.band in {"auto_match", "review"}
                and not scored.vector.same_retailer
                and scored.decision_source == "fellegi_sunter"
            ),
            key=lambda item: (-item.probability, item.key),
        )
        taken: Counter[tuple[str, str]] = Counter()
        selected: set[tuple[str, str]] = set()
        for scored in ranked:
            left, right = scored.key
            left_slot = (left, run.by_id[right].supermarket_id)
            right_slot = (right, run.by_id[left].supermarket_id)
            if taken[left_slot] >= top_k or taken[right_slot] >= top_k:
                continue
            taken[left_slot] += 1
            taken[right_slot] += 1
            selected.add(scored.key)
        return selected

    def one_to_one(self, run: CityRun, keys: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
        """Asignación voraz 1:1 por cadena (mejor probabilidad primero)."""

        ordered = sorted(keys, key=lambda key: (-run.scored[key].probability, key))
        used: set[tuple[str, str]] = set()
        result = []
        for left, right in ordered:
            left_slot = (left, run.by_id[right].supermarket_id)
            right_slot = (right, run.by_id[left].supermarket_id)
            if left_slot in used or right_slot in used:
                continue
            used.update((left_slot, right_slot))
            result.append((left, right))
        return result

    # -- 4. resumen de impacto ----------------------------------------------
    def city_summary(self, run: CityRun) -> dict[str, object]:
        published_group: dict[str, str] = {}
        for record in run.records:
            canonical = record.record.published_canonical_product_id
            if canonical and record.record.extra.get("published_comparability", "comparable") == "comparable":
                published_group[record.source_record_id] = canonical
        members_by_group: dict[str, set[str]] = defaultdict(set)
        for record_id, group in published_group.items():
            members_by_group[group].add(run.by_id[record_id].supermarket_id)
        current_comparable = {
            record_id
            for record_id, group in published_group.items()
            if len(members_by_group[group]) >= 2
        }

        def already_same(key: tuple[str, str]) -> bool:
            a, b = published_group.get(key[0]), published_group.get(key[1])
            return a is not None and a == b

        band_counts: Counter[str] = Counter()
        unmeasured_high: Counter[str] = Counter()
        new_counts: Counter[str] = Counter()
        new_by_pair: dict[str, Counter[str]] = defaultdict(Counter)
        for key, scored in run.scored.items():
            if scored.vector.same_retailer:
                band_counts["retailer_twin" if scored.decision_source == "retailer_twin" else "same_retailer_rejected"] += 1
                continue
            label = scored.band if scored.decision_source != "gtin_rule" else "auto_match_gtin_rule"
            band_counts[label] += 1
            group = retailer_pair(run.by_id[key[0]].supermarket_id, run.by_id[key[1]].supermarket_id)
            if (
                run.thresholds is not None
                and scored.band == "review"
                and scored.decision_source == "fellegi_sunter"
                and group not in run.thresholds.by_group
                and not scored.vector.auto_caps
                and scored.probability >= run.thresholds.unmeasured_auto
                and not already_same(key)
            ):
                unmeasured_high[group] += 1
            if scored.band in {"auto_match", "review"} and not already_same(key):
                tag = label if scored.decision_source != "gtin_rule" else "auto_match_gtin_rule"
                new_counts[tag] += 1
                new_by_pair[retailer_pair(run.by_id[key[0]].supermarket_id, run.by_id[key[1]].supermarket_id)][tag] += 1

        # Proyección: componentes = grupos publicados ∪ clusters del motor.
        parent: dict[str, str] = {record.source_record_id: record.source_record_id for record in run.records}

        def find(item: str) -> str:
            while parent[item] != item:
                parent[item] = parent[parent[item]]
                item = parent[item]
            return item

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        by_group: dict[str, list[str]] = defaultdict(list)
        for record_id in current_comparable:
            by_group[published_group[record_id]].append(record_id)
        for members in by_group.values():
            for other in members[1:]:
                union(members[0], other)
        for cluster in run.clusters:
            for other in cluster.members[1:]:
                union(cluster.members[0], other)
        components: dict[str, list[str]] = defaultdict(list)
        for record in run.records:
            components[find(record.source_record_id)].append(record.source_record_id)
        projected_comparable_records = 0
        projected_comparable_entities = 0
        collisions = 0
        for members in components.values():
            supermarkets = {run.by_id[item].supermarket_id for item in members}
            contexts = [run.by_id[item].context_id for item in members]
            if len(supermarkets) >= 2:
                projected_comparable_entities += 1
                projected_comparable_records += len(members)
            if len(set(contexts)) != len(contexts):
                collisions += 1
        current_entities = len(run.records) - len(current_comparable) + len(by_group)

        # Escenario "cola revisada": auto + revisión 1:1, si los revisores
        # confirmaran las parejas (cota superior; ver precisión de la banda).
        review_keys = self.one_to_one(run, self.review_selection(run))
        parent_review = dict(parent)

        def find_review(item: str) -> str:
            while parent_review[item] != item:
                parent_review[item] = parent_review[parent_review[item]]
                item = parent_review[item]
            return item

        for left_id, right_id in review_keys:
            ra, rb = find_review(left_id), find_review(right_id)
            if ra != rb:
                parent_review[rb] = ra
        review_components: dict[str, list[str]] = defaultdict(list)
        for record in run.records:
            review_components[find_review(record.source_record_id)].append(record.source_record_id)
        with_review_records = sum(
            len(members)
            for members in review_components.values()
            if len({run.by_id[item].supermarket_id for item in members}) >= 2
        )
        per_retailer: dict[str, dict[str, object]] = {}
        comparable_ids = {
            item
            for members in components.values()
            if len({run.by_id[x].supermarket_id for x in members}) >= 2
            for item in members
        }
        for supermarket, items in sorted(_group_by(run.records, lambda r: r.supermarket_id).items()):
            ids = [item.source_record_id for item in items]
            current = sum(1 for item in ids if item in current_comparable)
            projected = sum(1 for item in ids if item in comparable_ids)
            per_retailer[supermarket] = {
                "source_records": len(ids),
                "current_comparable_pct": round(100 * current / len(ids), 1),
                "projected_comparable_pct": round(100 * projected / len(ids), 1),
            }
        product_type_known = sum(1 for record in run.records if record.product_type)
        type_sources = Counter(record.product_type_source or "none" for record in run.records)
        return {
            "city": run.city,
            "source_records": len(run.records),
            "product_type_coverage_pct": round(100 * product_type_known / len(run.records), 1) if run.records else 0.0,
            "product_type_source": dict(sorted(type_sources.items())),
            "leaf_type_mappings": len(run.leaf_types),
            "blocking": run.candidates.diagnostics,
            "candidate_bands": dict(sorted(band_counts.items())),
            "new_vs_published": dict(sorted(new_counts.items())),
            "new_vs_published_by_retailer_pair": {key: dict(sorted(value.items())) for key, value in sorted(new_by_pair.items())},
            "review_high_confidence_unmeasured_by_retailer_pair": dict(sorted(unmeasured_high.items())),
            "clusters": {
                "multi_retailer_clusters": len(run.clusters),
                "gtin_only_clusters": sum(1 for cluster in run.clusters if cluster.gtin_only),
                "size_distribution": dict(sorted(Counter(len(cluster.members) for cluster in run.clusters).items())),
                "rejected_merges": run.clustering_rejected,
            },
            "coverage": {
                "current_comparable_records": len(current_comparable),
                "current_comparable_records_pct": round(100 * len(current_comparable) / len(run.records), 2) if run.records else 0.0,
                "current_entities": current_entities,
                "current_comparable_entities": len(by_group),
                "current_comparable_entities_pct": round(100 * len(by_group) / current_entities, 2) if current_entities else 0.0,
                "projected_comparable_records": projected_comparable_records,
                "projected_comparable_records_pct": round(100 * projected_comparable_records / len(run.records), 2) if run.records else 0.0,
                "projected_entities": len(components),
                "projected_comparable_entities": projected_comparable_entities,
                "projected_comparable_entities_pct": round(100 * projected_comparable_entities / len(components), 2) if components else 0.0,
                "projected_retailer_collision_components": collisions,
                "upper_bound_if_review_confirmed_pairs": len(review_keys),
                "upper_bound_if_review_confirmed_records_pct": round(100 * with_review_records / len(run.records), 2) if run.records else 0.0,
                "per_retailer": per_retailer,
            },
            "comparable_alternatives": len(run.alternatives),
            "silver_labels": {
                "positive_pairs": sum(1 for label in run.labels.values() if label.is_match),
                "negative_candidate_pairs": sum(1 for label in run.labels.values() if not label.is_match),
                "positive_pairs_by_retailer_pair": dict(
                    sorted(
                        Counter(
                            retailer_pair(run.by_id[key[0]].supermarket_id, run.by_id[key[1]].supermarket_id)
                            for key, label in run.labels.items()
                            if label.is_match
                        ).items()
                    )
                ),
            },
        }


def _balanced(rows: Sequence[tuple[tuple[int, ...], str]]) -> tuple[list[tuple[int, ...]], list[float]]:
    """Pondera para que cada par de cadenas aporte lo mismo a m/u.

    Sin esto, Paiz–Walmart (misma plataforma, nombres idénticos) domina las
    etiquetas y el modelo castiga similitudes de nombre "altas" que son típicas
    entre cadenas con estilos de nombre distintos.
    """

    counts = Counter(group for _, group in rows)
    if not counts:
        return [], []
    per_group = len(rows) / len(counts)
    return [levels for levels, _ in rows], [per_group / counts[group] for _, group in rows]


def _group_by(items: Iterable[StandardizedRecord], key) -> dict[str, list[StandardizedRecord]]:  # type: ignore[no-untyped-def]
    result: dict[str, list[StandardizedRecord]] = defaultdict(list)
    for item in items:
        result[key(item)].append(item)
    return result


def pair_candidate_id(key: tuple[str, str]) -> str:
    return candidate_id(key[0], key[1])


def model_report(model: FellegiSunterModel) -> dict[str, object]:
    return {
        "prior_lambda": round(model.prior, 8),
        "prior_log2": round(model.prior_weight(), 4),
        "training": model.training,
        "parameters": model.parameters_table(),
    }


def summarize_metrics(evaluation: Mapping[str, object]) -> dict[str, object]:
    groups = evaluation.get("by_retailer_pair") or {}
    overall = groups.get("all", {}) if isinstance(groups, Mapping) else {}
    return {
        "labelled_pairs": evaluation.get("labelled_pairs"),
        "positive_pairs": evaluation.get("positive_pairs"),
        "blocking_recall_gtin_blind": evaluation.get("blocking_recall_gtin_blind"),
        "auto_match": overall.get("auto_match") if isinstance(overall, Mapping) else None,
        "auto_or_review": overall.get("auto_or_review") if isinstance(overall, Mapping) else None,
    }


__all__ = [
    "CityRun",
    "MatchingEngine",
    "ScoredPair",
    "SPLITS",
    "model_report",
    "pair_candidate_id",
    "prf",
    "retailer_pair",
    "split_for_key",
    "summarize_metrics",
]
