"""Precio unitario normalizado (HNL/kg, HNL/L, HNL/unidad).

Sólo alimenta la relación ``COMPARABLE_ALTERNATIVE`` (mismo producto en otra
presentación o sustituto cercano). Nunca se usa como identidad ni para el
ahorro "mismo producto" del comparador.
"""
from __future__ import annotations

from typing import Iterable, Mapping

from .comparison import ComparisonVector
from .standardize import StandardizedRecord

ALTERNATIVE_RELATION = "COMPARABLE_ALTERNATIVE"


def is_comparable_alternative(vector: ComparisonVector) -> bool:
    labels = vector.as_labels()
    return (
        labels["size"] == "conflict"
        and labels["pack"] in {"same_single", "same_multi", "conflict", "missing"}
        and labels["brand"] in {"exact", "fuzzy"}
        and labels["variant"] in {"agree", "none"}
        and labels["codes"] in {"agree", "none", "one_sided"}
        and labels["type"] in {"same_type", "type_partial", "same_department", "unknown"}
        and labels["name"] in {"very_high", "high"}
        and vector.gtin_level != "same"
    )


def comparable_alternatives(
    vectors: Iterable[ComparisonVector],
    records: Mapping[str, StandardizedRecord],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for vector in vectors:
        if not is_comparable_alternative(vector):
            continue
        left, right = records[vector.left_id], records[vector.right_id]
        if (
            left.unit_price is None
            or right.unit_price is None
            or left.unit_price_basis != right.unit_price_basis
            or left.unit_price_basis == "HNL/oz"
        ):
            continue
        cheaper = left if left.unit_price <= right.unit_price else right
        rows.append(
            {
                "relation": ALTERNATIVE_RELATION,
                "identity": False,
                "left_source_record_id": left.source_record_id,
                "right_source_record_id": right.source_record_id,
                "left_supermarket_id": left.supermarket_id,
                "right_supermarket_id": right.supermarket_id,
                "left_name": left.record.source_name,
                "right_name": right.record.source_name,
                "unit_price_basis": left.unit_price_basis,
                "left_unit_price": round(left.unit_price, 4),
                "right_unit_price": round(right.unit_price, 4),
                "unit_price_ratio": round(max(left.unit_price, right.unit_price) / min(left.unit_price, right.unit_price), 4),
                "cheaper_per_unit_source_record_id": cheaper.source_record_id,
                "name_score": vector.name_score,
            }
        )
    rows.sort(key=lambda row: (str(row["left_source_record_id"]), str(row["right_source_record_id"])))
    return rows
