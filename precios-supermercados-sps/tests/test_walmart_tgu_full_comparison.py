"""Granularity evidence from existing full snapshots, with no live traffic."""
import csv
import gzip
import importlib.util
import io
import json
import socket
from pathlib import Path

import pytest
import requests

from _evidencia import asegurar

REPORT = Path(__file__).resolve().parents[1]/"reports/walmart/2026-08-31-full"
# compare_tgu.py relee la captura completa del 2026-08-31 (archivada en un release).
asegurar("reports/walmart/2026-08-31-full/raw-capture.tar.gz")
spec = importlib.util.spec_from_file_location("walmart_tgu_comparison", REPORT/"compare_tgu.py")
comparison = importlib.util.module_from_spec(spec)
spec.loader.exec_module(comparison)


def test_all_shared_skus_and_raw_reproduce_full_comparison_without_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("offline comparison attempted network")
    monkeypatch.setattr(requests.Session, "request", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    result, raw_csv = comparison.reproduce()
    assert result == json.loads((REPORT/"tgu-comparison.json").read_text())
    assert raw_csv == gzip.decompress((REPORT/"tgu-comparison.csv.gz").read_bytes())
    counts = result["counts"]
    assert counts["shared_skus"] == 12867
    assert counts["commercially_comparable"] == 12042
    assert counts["commercially_equal"] == 11787
    assert counts["commercially_different"] == counts["commercial_differences_in_stock_both"] == 255
    assert [counts[f+"_differences"] for f in comparison.COMMERCIAL] == [218,197,57]
    assert counts["availability_differences_all_shared"] == 933
    assert counts["availability_only_with_commercial_equality"] == 331
    assert (counts["one_price_missing"], counts["both_prices_missing"]) == (602,223)
    assert counts["raw_pages_verified"] == 319 and counts["raw_skus_verified"] == 28088
    assert result["decision"] == "keep_two_commercial_contexts_for_production_persistence"
    assert result["availability_used_to_justify_separation"] is False
    assert result["causal_anchor"]["reproduced_in_full"] is True
    rows = list(csv.DictReader(io.StringIO(raw_csv.decode())))
    assert len(rows) == len({r["sku"] for r in rows}) == 12867
    missing = [r for r in rows if r["commercial_comparable"] == "False"]
    assert len(missing) == 825
    assert all(r["current_price_differs"] == r["reported_regular_price_differs"] == r["is_promotion_differs"] == "" for r in missing)


@pytest.mark.parametrize("change,category,fields", [
    ({"current_price": "100.0"}, "equal_observed_state", []),
    ({"current_price": "90.00"}, "commercial_difference", ["current_price"]),
    ({"reported_regular_price": "150.00"}, "commercial_difference", ["reported_regular_price"]),
    ({"reported_regular_price": "100.00", "is_promotion": False}, "commercial_difference", ["reported_regular_price", "is_promotion"]),
    ({"availability": "out_of_stock"}, "availability_only", []),
    ({"current_price": None, "reported_regular_price": None, "is_promotion": None, "availability": "out_of_stock"}, "one_price_missing", []),
])
def test_field_comparison_does_not_conflate_stock_or_unknown_prices(change, category, fields):
    a = {"current_price": "100.00", "reported_regular_price": "120.00", "is_promotion": True, "availability": "in_stock"}
    result = comparison.classify(a, a | change)
    assert result["classification"] == category
    assert result["commercial_differences"] == fields
    assert result["commercial_comparable"] is (category != "one_price_missing")


def test_two_missing_prices_are_not_evidence_of_commercial_equivalence():
    row = {"current_price": None, "reported_regular_price": None, "is_promotion": None, "availability": "out_of_stock"}
    result = comparison.classify(row, row.copy())
    assert result["classification"] == "both_prices_missing"
    assert result["commercial_comparable"] is False
    assert result["commercial_differences"] == []


def test_changed_snapshot_bytes_cannot_reproduce_comparison(monkeypatch):
    original = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda p: b"altered" if p == REPORT/"walmart_tgu_ffaa.json.gz" else original(p))
    with pytest.raises(ValueError, match="snapshot_archive_hash_mismatch"):
        comparison.reproduce()
