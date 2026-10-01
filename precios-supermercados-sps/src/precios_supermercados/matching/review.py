"""Cola de revisión humana (human-in-the-loop) e importador de decisiones.

La cola exporta ambas ofertas, el score, la explicación término a término
(waterfall Fellegi–Sunter) y una decisión sugerida. El importador convierte
filas revisadas al formato existente ``reviewed-decisions-v1.json``
(``ReviewedIdentityDecision``), validando evidencia y huellas. Sólo escribe el
registro privado; la publicación sigue gobernada por la política de identidad
(modo ``shadow``, ``public_serving_allowed: false``).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from ..product_identity_decisions import (
    DECISION_SCHEMA,
    IDENTITY_POLICY_VERSION,
    ProductIdentityDecisionError,
    ReviewedIdentityDecision,
    candidate_id,
    profile_evidence_fingerprint,
)
from ..product_identity_v2 import IDENTITY_NORMALIZATION_VERSION
from . import MATCHING_ENGINE_VERSION
from .comparison import ComparisonVector
from .fellegi_sunter import FellegiSunterModel
from .standardize import StandardizedRecord, decimal_text

REVIEW_SCHEMA = "precios-sps-matching-review-queue/v1"
REVIEW_DECISIONS = {
    "same_product": "VERIFIED_EQUIVALENT",
    "different_products": "CONFLICT",
    "variant": "PRODUCT_VARIANT",
    "alternative": "COMPARABLE_ALTERNATIVE",
    "pending": "UNRESOLVED",
}
REGISTRY_GRADE_AUTHORITY = "turso_products"

SIDE_FIELDS = (
    "source_record_id",
    "supermarket_id",
    "context_id",
    "source_name",
    "source_brand",
    "source_presentation",
    "source_category",
    "canonical_brand",
    "product_type",
    "size",
    "current_price",
    "unit_price",
    "unit_price_basis",
    "canonical_gtin",
    "published_canonical_product_id",
    "evidence_fingerprint",
    "fingerprint_authority",
)
REVIEWER_FIELDS = (
    "review_decision",
    "master_product_id",
    "evidence_codes",
    "evidence_references",
    "rationale",
    "reviewed_by",
    "reviewed_at_utc",
)
CSV_FIELDS = (
    "candidate_id",
    "city",
    "band",
    "decision_source",
    "match_probability",
    "match_weight",
    "suggested_decision",
    "suggested_relation",
    "suggested_master_product_id",
    "cluster_id",
    "hard_conflicts",
    "auto_caps",
    "comparison",
    "waterfall",
    *(f"left_{name}" for name in SIDE_FIELDS),
    *(f"right_{name}" for name in SIDE_FIELDS),
    *REVIEWER_FIELDS,
)


class ReviewImportError(ValueError):
    """Una fila revisada no puede convertirse en decisión auditada."""


def _size_text(record: StandardizedRecord) -> str | None:
    size = record.size
    if size is None:
        return None
    total = decimal_text(round(size.total, 4))
    if size.pack_count > 1 and size.unit_amount is not None and size.dimension != "count":
        return f"{size.pack_count} x {decimal_text(round(size.unit_amount, 4))} {size.canonical_unit}"
    return f"{total} {size.canonical_unit}"


def side_payload(record: StandardizedRecord) -> dict[str, object]:
    source = record.record
    return {
        "source_record_id": source.source_record_id,
        "supermarket_id": source.supermarket_id,
        "context_id": record.context_id,
        "source_name": source.source_name,
        "source_brand": source.source_brand,
        "source_presentation": source.source_presentation,
        "source_category": source.source_category,
        "canonical_brand": record.brand,
        "product_type": record.product_type,
        "size": _size_text(record),
        "current_price": source.current_price,
        "unit_price": None if record.unit_price is None else round(record.unit_price, 4),
        "unit_price_basis": record.unit_price_basis,
        "canonical_gtin": record.identity_gtin,
        "published_canonical_product_id": source.published_canonical_product_id,
        "evidence_fingerprint": profile_evidence_fingerprint(record.profile),
        "fingerprint_authority": source.fingerprint_authority,
    }


def suggested_master_product_id(left: StandardizedRecord, right: StandardizedRecord, cluster_id: str | None) -> str:
    if left.identity_gtin and left.identity_gtin == right.identity_gtin:
        return f"prod_gtin_{left.identity_gtin}"
    for record in (left, right):
        canonical = record.record.published_canonical_product_id
        if canonical and canonical.startswith("prod_gtin_"):
            return canonical
    seed = cluster_id or "|".join(sorted((left.source_record_id, right.source_record_id)))
    return "prod_verified_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def waterfall_text(waterfall: Sequence[Mapping[str, object]]) -> str:
    parts = []
    for row in waterfall:
        weight = float(row["log2_weight"])  # type: ignore[arg-type]
        parts.append(f"{row['field']}:{row['level']}({weight:+.2f})")
    return " ".join(parts)


def suggested_decision(band: str, vector: ComparisonVector) -> tuple[str, str]:
    if vector.hard_conflicts:
        return "different_products", "CONFLICT"
    if band == "auto_match":
        return "same_product", "VERIFIED_EQUIVALENT"
    if band == "review":
        return "needs_review", "UNRESOLVED"
    return "different_products", "UNRESOLVED"


def review_row(
    *,
    city: str,
    left: StandardizedRecord,
    right: StandardizedRecord,
    vector: ComparisonVector,
    model: FellegiSunterModel,
    band: str,
    decision_source: str,
    probability: float,
    weight: float,
    cluster_id: str | None = None,
) -> dict[str, object]:
    if left.source_record_id > right.source_record_id:
        left, right = right, left
    waterfall = model.waterfall(vector)
    decision, relation = suggested_decision(band, vector)
    row: dict[str, object] = {
        "candidate_id": candidate_id(left.source_record_id, right.source_record_id),
        "city": city,
        "band": band,
        "decision_source": decision_source,
        "match_probability": round(probability, 6),
        "match_weight": round(weight, 4),
        "suggested_decision": decision,
        "suggested_relation": relation,
        "suggested_master_product_id": suggested_master_product_id(left, right, cluster_id),
        "cluster_id": cluster_id,
        "hard_conflicts": list(vector.hard_conflicts),
        "auto_caps": list(vector.auto_caps),
        "comparison": vector.as_labels(),
        "waterfall": waterfall,
        "left": side_payload(left),
        "right": side_payload(right),
    }
    for name in REVIEWER_FIELDS:
        row[name] = None
    return row


def flatten_row(row: Mapping[str, object]) -> dict[str, str]:
    flat: dict[str, str] = {}
    for name in CSV_FIELDS:
        if name.startswith("left_") or name.startswith("right_"):
            side, field = name.split("_", 1)
            value = (row.get(side) or {}).get(field)  # type: ignore[union-attr]
        elif name == "waterfall":
            value = waterfall_text(row.get("waterfall") or [])  # type: ignore[arg-type]
        elif name in {"hard_conflicts", "auto_caps"}:
            value = ";".join(row.get(name) or [])  # type: ignore[arg-type]
        elif name == "comparison":
            value = json.dumps(row.get(name) or {}, ensure_ascii=False, sort_keys=True)
        else:
            value = row.get(name)
        flat[name] = "" if value is None else str(value)
    return flat


def write_review_queue(rows: Sequence[Mapping[str, object]], jsonl_path: Path, csv_path: Path, *, metadata: Mapping[str, object]) -> None:
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with jsonl_path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({"schema": REVIEW_SCHEMA, "metadata": dict(metadata)}, ensure_ascii=False, sort_keys=True) + "\n")
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(flatten_row(row))


# -- importación -------------------------------------------------------------
@dataclass(frozen=True)
class ImportResult:
    decisions: tuple[ReviewedIdentityDecision, ...]
    skipped: int
    errors: tuple[dict[str, str], ...]


def read_review_csv(text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text))
    missing = {"candidate_id", "left_source_record_id", "right_source_record_id", "review_decision"} - set(reader.fieldnames or ())
    if missing:
        raise ReviewImportError("review_csv_columns_missing:" + ",".join(sorted(missing)))
    return [dict(row) for row in reader]


def _split_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.replace(",", ";").split(";") if item.strip()]


def decision_from_review_row(
    row: Mapping[str, str],
    *,
    now_utc: str,
    allow_offline_fingerprints: bool = False,
) -> ReviewedIdentityDecision | None:
    decision = (row.get("review_decision") or "").strip()
    if not decision or decision == "skip":
        return None
    if decision not in REVIEW_DECISIONS:
        raise ReviewImportError("review_decision_invalid")
    authorities = {row.get("left_fingerprint_authority") or "", row.get("right_fingerprint_authority") or ""}
    if authorities != {REGISTRY_GRADE_AUTHORITY} and not allow_offline_fingerprints:
        raise ReviewImportError("fingerprint_authority_not_registry_grade")
    left_id = (row.get("left_source_record_id") or "").strip()
    right_id = (row.get("right_source_record_id") or "").strip()
    left_fp = (row.get("left_evidence_fingerprint") or "").strip()
    right_fp = (row.get("right_evidence_fingerprint") or "").strip()
    if left_id > right_id:
        left_id, right_id = right_id, left_id
        left_fp, right_fp = right_fp, left_fp
    pair = candidate_id(left_id, right_id)
    if row.get("candidate_id") and row["candidate_id"] != pair:
        raise ReviewImportError("review_candidate_id_mismatch")
    relation = REVIEW_DECISIONS[decision]
    left_gtin = (row.get("left_canonical_gtin") or "").strip()
    right_gtin = (row.get("right_canonical_gtin") or "").strip()
    evidence = _split_list(row.get("evidence_codes"))
    if relation == "VERIFIED_EQUIVALENT" and left_gtin and left_gtin == right_gtin and "same_valid_gtin" in evidence:
        relation = "EXACT_TRADE_ITEM"
    status = "pending" if relation == "UNRESOLVED" else "approved"
    master = None
    if relation in {"VERIFIED_EQUIVALENT", "EXACT_TRADE_ITEM"}:
        master = (row.get("master_product_id") or "").strip() or (row.get("suggested_master_product_id") or "").strip() or None
        if relation == "EXACT_TRADE_ITEM":
            master = f"prod_gtin_{left_gtin}"
    if not evidence and relation not in {"VERIFIED_EQUIVALENT", "EXACT_TRADE_ITEM"}:
        evidence = ["human_verified", "source_title"]
    reviewed_by = (row.get("reviewed_by") or "").strip()
    rationale = (row.get("rationale") or "").strip()
    reviewed_at = (row.get("reviewed_at_utc") or "").strip() or now_utc
    try:
        return ReviewedIdentityDecision(
            candidate_id=pair,
            left_source_record_id=left_id,
            right_source_record_id=right_id,
            left_evidence_fingerprint=left_fp,
            right_evidence_fingerprint=right_fp,
            relation=relation,
            status=status,
            master_product_id=master,
            evidence_codes=tuple(evidence),
            evidence_references=tuple(_split_list(row.get("evidence_references"))),
            rationale=rationale,
            reviewed_by=reviewed_by,
            reviewed_at_utc=reviewed_at,
            policy_version=IDENTITY_POLICY_VERSION,
            engine_version=IDENTITY_NORMALIZATION_VERSION,
        )
    except ProductIdentityDecisionError as exc:
        raise ReviewImportError(str(exc)) from exc


def import_review_rows(
    rows: Iterable[Mapping[str, str]],
    *,
    now_utc: str | None = None,
    allow_offline_fingerprints: bool = False,
) -> ImportResult:
    now = now_utc or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    decisions: list[ReviewedIdentityDecision] = []
    errors: list[dict[str, str]] = []
    skipped = 0
    seen: set[str] = set()
    for number, row in enumerate(rows, start=2):
        try:
            decision = decision_from_review_row(row, now_utc=now, allow_offline_fingerprints=allow_offline_fingerprints)
        except ReviewImportError as exc:
            errors.append({"line": str(number), "candidate_id": row.get("candidate_id") or "", "error": str(exc)})
            continue
        if decision is None:
            skipped += 1
            continue
        if decision.candidate_id in seen:
            errors.append({"line": str(number), "candidate_id": decision.candidate_id, "error": "review_duplicate_candidate"})
            continue
        seen.add(decision.candidate_id)
        decisions.append(decision)
    return ImportResult(tuple(decisions), skipped, tuple(errors))


def decision_to_mapping(decision: ReviewedIdentityDecision) -> dict[str, object]:
    return {
        "candidate_id": decision.candidate_id,
        "left_source_record_id": decision.left_source_record_id,
        "right_source_record_id": decision.right_source_record_id,
        "left_evidence_fingerprint": decision.left_evidence_fingerprint,
        "right_evidence_fingerprint": decision.right_evidence_fingerprint,
        "relation": decision.relation,
        "status": decision.status,
        "master_product_id": decision.master_product_id,
        "evidence_codes": list(decision.evidence_codes),
        "evidence_references": list(decision.evidence_references),
        "rationale": decision.rationale,
        "reviewed_by": decision.reviewed_by,
        "reviewed_at_utc": decision.reviewed_at_utc,
        "policy_version": decision.policy_version,
        "engine_version": decision.engine_version,
    }


def merge_into_registry(
    path: Path,
    decisions: Sequence[ReviewedIdentityDecision],
    *,
    replace_existing: bool = False,
) -> dict[str, int]:
    """Agrega decisiones al registro privado (escritura atómica y validada)."""

    from ..product_identity_decisions import load_reviewed_decisions

    existing = list(load_reviewed_decisions(path))
    by_id = {decision.candidate_id: decision for decision in existing}
    added = replaced = kept = 0
    for decision in decisions:
        if decision.candidate_id in by_id:
            if not replace_existing:
                kept += 1
                continue
            replaced += 1
        else:
            added += 1
        by_id[decision.candidate_id] = decision
    payload = {
        "schema": DECISION_SCHEMA,
        "policy_version": IDENTITY_POLICY_VERSION,
        "decisions": [decision_to_mapping(by_id[key]) for key in sorted(by_id)],
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # Re-valida el archivo completo antes de reemplazar el registro.
    load_reviewed_decisions(temporary)
    temporary.replace(path)
    return {"added": added, "replaced": replaced, "kept_existing": kept, "total": len(by_id)}


def review_metadata(*, generated_at_utc: str, thresholds: Mapping[str, object], policy_gate: Mapping[str, object]) -> dict[str, object]:
    return {
        "engine_version": MATCHING_ENGINE_VERSION,
        "normalization_version": IDENTITY_NORMALIZATION_VERSION,
        "policy_version": IDENTITY_POLICY_VERSION,
        "generated_at_utc": generated_at_utc,
        "thresholds": dict(thresholds),
        "policy_gate": dict(policy_gate),
        "public_serving_allowed": False,
        "review_decisions": sorted(REVIEW_DECISIONS),
        "identity_evidence_rule": "same_product requiere human_verified + (barcode_visible | manufacturer_catalog | same_valid_gtin | package_front+package_back)",
    }
