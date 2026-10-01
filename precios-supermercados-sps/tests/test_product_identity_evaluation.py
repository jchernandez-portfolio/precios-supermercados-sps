from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.evaluar_identidad_productos import SCHEMA, evaluate_golden_pairs


def test_golden_identity_evaluation_has_no_false_positive() -> None:
    report = evaluate_golden_pairs(
        ROOT / "tests" / "fixtures" / "homologation" / "golden-pairs-v1.jsonl"
    )
    assert report["schema"] == SCHEMA
    assert report["summary"] == {
        "cases": 8,
        "passed": 8,
        "failed": 0,
        "identity_true_positive": 2,
        "identity_false_positive": 0,
        "identity_false_negative": 0,
        "identity_precision": 1.0,
        "identity_recall": 1.0,
    }
