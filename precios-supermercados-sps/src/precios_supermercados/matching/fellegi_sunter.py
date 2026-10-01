"""Modelo probabilístico Fellegi–Sunter (1969), en la variante de Splink.

Para cada campo ``f`` y nivel ``l``:

- ``m[f][l] = P(nivel l | match)``;
- ``u[f][l] = P(nivel l | no match)``.

El peso de match de una pareja es ``log2(λ/(1-λ)) + Σ_f log2(m/u)`` y la
probabilidad ``2^w / (1 + 2^w)``. ``u`` se estima con parejas aleatorias entre
cadenas distintas (casi todas no-match). ``m`` se estima con parejas
confirmadas por GTIN (etiquetas *silver*) y/o con EM sobre las parejas
candidatas manteniendo ``u`` fijo. El término de cada campo se guarda como
"waterfall" para que la revisión humana vea por qué una pareja puntúa.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from .comparison import FIELDS, FIELD_NAMES, ComparisonVector

MODEL_SCHEMA = "precios-sps-fellegi-sunter-model/v1"


class FellegiSunterError(ValueError):
    """El modelo no puede estimarse o aplicarse."""


def _normalize(counts: Sequence[float], laplace: float) -> list[float]:
    smoothed = [value + laplace for value in counts]
    total = sum(smoothed)
    if total <= 0:
        raise FellegiSunterError("model_counts_empty")
    return [value / total for value in smoothed]


def _count_levels(
    level_rows: Iterable[tuple[int, ...]],
    weights: Sequence[float] | None = None,
) -> tuple[list[list[float]], int]:
    counts = [[0.0] * len(levels) for _, levels in FIELDS]
    total = 0
    for position, row in enumerate(level_rows):
        weight = 1.0 if weights is None else float(weights[position])
        total += 1
        for index, level in enumerate(row):
            counts[index][level] += weight
    return counts, total


@dataclass
class FellegiSunterModel:
    m: dict[str, list[float]] = field(default_factory=dict)
    u: dict[str, list[float]] = field(default_factory=dict)
    prior: float = 1e-4
    weight_caps_bits: dict[str, float] = field(default_factory=dict)
    laplace: float = 1.0
    training: dict[str, object] = field(default_factory=dict)

    # -- estimación -------------------------------------------------------
    def fit_u(self, level_rows: Iterable[tuple[int, ...]], weights: Sequence[float] | None = None) -> int:
        counts, total = _count_levels(level_rows, weights)
        if total == 0:
            raise FellegiSunterError("u_sample_empty")
        self.u = {name: _normalize(counts[index], self.laplace) for index, name in enumerate(FIELD_NAMES)}
        self.training["u_pairs"] = total
        return total

    def fit_m(self, level_rows: Iterable[tuple[int, ...]], weights: Sequence[float] | None = None) -> int:
        counts, total = _count_levels(level_rows, weights)
        if total == 0:
            raise FellegiSunterError("m_sample_empty")
        self.m = {name: _normalize(counts[index], self.laplace) for index, name in enumerate(FIELD_NAMES)}
        self.training["m_pairs"] = total
        self.training["m_source"] = "silver_gtin_pairs"
        return total

    def fit_prior_em(self, level_rows: Sequence[tuple[int, ...]], *, iterations: int = 40) -> float:
        """Estima sólo λ (m y u fijos) sobre las parejas candidatas."""

        patterns = Counter(level_rows)
        if not patterns:
            raise FellegiSunterError("prior_sample_empty")
        prior = min(max(self.prior, 1e-6), 0.5)
        total = sum(patterns.values())
        for _ in range(iterations):
            expected = 0.0
            for row, count in patterns.items():
                expected += count * self._posterior(row, prior)
            new_prior = min(max(expected / total, 1e-6), 1 - 1e-6)
            if abs(new_prior - prior) < 1e-9:
                prior = new_prior
                break
            prior = new_prior
        self.prior = prior
        self.training["prior_source"] = "em_on_candidates_m_u_fixed"
        self.training["prior_pairs"] = total
        return prior

    def fit_em(self, level_rows: Sequence[tuple[int, ...]], *, iterations: int = 40, update_u: bool = False) -> int:
        """EM clásico (Winkler/Splink) sobre parejas candidatas.

        ``u`` se mantiene fijo por defecto (estimado por muestreo aleatorio),
        como recomienda Splink, para evitar que EM confunda clases.
        """

        patterns = Counter(level_rows)
        if not patterns:
            raise FellegiSunterError("em_sample_empty")
        if not self.m or not self.u:
            raise FellegiSunterError("em_requires_initial_m_u")
        prior = min(max(self.prior, 1e-6), 0.5)
        used = 0
        for used in range(1, iterations + 1):
            m_counts = [[0.0] * len(levels) for _, levels in FIELDS]
            u_counts = [[0.0] * len(levels) for _, levels in FIELDS]
            expected_matches = 0.0
            total = 0.0
            for row, count in patterns.items():
                posterior = self._posterior(row, prior)
                expected_matches += count * posterior
                total += count
                for index, level in enumerate(row):
                    m_counts[index][level] += count * posterior
                    u_counts[index][level] += count * (1 - posterior)
            new_m = {name: _normalize(m_counts[index], self.laplace * 0.01) for index, name in enumerate(FIELD_NAMES)}
            delta = max(
                abs(a - b) for name in FIELD_NAMES for a, b in zip(new_m[name], self.m[name])
            )
            self.m = new_m
            if update_u:
                self.u = {name: _normalize(u_counts[index], self.laplace * 0.01) for index, name in enumerate(FIELD_NAMES)}
            prior = min(max(expected_matches / total, 1e-6), 1 - 1e-6)
            if delta < 1e-6:
                break
        self.prior = prior
        self.training["m_source"] = "em_on_candidates"
        self.training["em_iterations"] = used
        return used

    # -- aplicación --------------------------------------------------------
    def partial_weight(self, field_name: str, level: int) -> float:
        m = self.m[field_name][level]
        u = self.u[field_name][level]
        weight = math.log2(m / u)
        cap = self.weight_caps_bits.get(field_name)
        if cap is not None:
            weight = max(-cap, min(cap, weight))
        return weight

    def prior_weight(self, prior: float | None = None) -> float:
        value = self.prior if prior is None else prior
        return math.log2(value / (1 - value))

    def _posterior(self, row: tuple[int, ...], prior: float) -> float:
        weight = self.prior_weight(prior) + sum(
            self.partial_weight(name, level) for name, level in zip(FIELD_NAMES, row)
        )
        return probability_from_weight(weight)

    def match_weight(self, row: tuple[int, ...]) -> float:
        return self.prior_weight() + sum(self.partial_weight(name, level) for name, level in zip(FIELD_NAMES, row))

    def score(self, vector: ComparisonVector) -> tuple[float, float]:
        weight = self.match_weight(vector.levels)
        return weight, probability_from_weight(weight)

    def waterfall(self, vector: ComparisonVector) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = [
            {
                "field": "prior",
                "level": "lambda",
                "m": None,
                "u": None,
                "bayes_factor": round(self.prior / (1 - self.prior), 8),
                "log2_weight": round(self.prior_weight(), 4),
            }
        ]
        for (name, levels), level in zip(FIELDS, vector.levels):
            m = self.m[name][level]
            u = self.u[name][level]
            rows.append(
                {
                    "field": name,
                    "level": levels[level],
                    "m": round(m, 6),
                    "u": round(u, 6),
                    "bayes_factor": round(m / u, 6),
                    "log2_weight": round(self.partial_weight(name, level), 4),
                }
            )
        return rows

    # -- serialización -----------------------------------------------------
    def parameters_table(self) -> list[dict[str, object]]:
        table = []
        for name, levels in FIELDS:
            for index, level in enumerate(levels):
                table.append(
                    {
                        "field": name,
                        "level": level,
                        "m": round(self.m[name][index], 6),
                        "u": round(self.u[name][index], 6),
                        "log2_bayes_factor": round(self.partial_weight(name, index), 4),
                    }
                )
        return table

    def to_json(self) -> dict[str, object]:
        return {
            "schema": MODEL_SCHEMA,
            "fields": {name: list(levels) for name, levels in FIELDS},
            "m": self.m,
            "u": self.u,
            "prior": self.prior,
            "weight_caps_bits": self.weight_caps_bits,
            "laplace": self.laplace,
            "training": self.training,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, object]) -> "FellegiSunterModel":
        if payload.get("schema") != MODEL_SCHEMA:
            raise FellegiSunterError("model_schema_invalid")
        fields = payload.get("fields")
        if fields != {name: list(levels) for name, levels in FIELDS}:
            raise FellegiSunterError("model_fields_mismatch")
        model = cls(
            m={key: [float(item) for item in value] for key, value in dict(payload["m"]).items()},  # type: ignore[arg-type]
            u={key: [float(item) for item in value] for key, value in dict(payload["u"]).items()},  # type: ignore[arg-type]
            prior=float(payload["prior"]),  # type: ignore[arg-type]
            weight_caps_bits={key: float(value) for key, value in dict(payload.get("weight_caps_bits") or {}).items()},  # type: ignore[arg-type]
            laplace=float(payload.get("laplace", 1.0)),  # type: ignore[arg-type]
            training=dict(payload.get("training") or {}),  # type: ignore[arg-type]
        )
        return model


def probability_from_weight(weight: float) -> float:
    if weight >= 60:
        return 1.0
    if weight <= -60:
        return 0.0
    odds = 2.0 ** weight
    return odds / (1.0 + odds)
