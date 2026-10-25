import importlib.util
import json
import socket
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/pricesmart/2026-09-02-taxonomy-probe"


def _module():
    spec = importlib.util.spec_from_file_location("pricesmart_taxonomy_probe", REPORT / "verify.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_taxonomy_probe_reproduces_offline_and_fail_closed(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network access forbidden")

    monkeypatch.setattr(socket, "socket", forbidden)
    observed = _module().reproduce()
    expected = json.loads((REPORT / "evidence.json").read_text())
    assert observed == expected


def test_taxonomy_probe_is_complete_for_the_root_page():
    evidence = json.loads((REPORT / "evidence.json").read_text())
    taxonomy = evidence["taxonomy"]
    assert taxonomy["root_categories_returned"] == 26
    assert taxonomy["requested_limit"] == 200
    assert taxonomy["short_page_terminal"] is True
    assert taxonomy["offset_200_required"] is False
    assert taxonomy["unique_ids"] == taxonomy["unique_keys"] == taxonomy["unique_slugs"] == 26
    assert taxonomy["all_parent_null"] is True
    assert taxonomy["all_ancestors_empty"] is True
    assert [row["category_key"] for row in taxonomy["categories"]] == [
        "S10D45", "G10D03", "H30D22", "H20D09", "G10D08014", "P10D51",
        "B10D27", "H10D21", "S30D26", "O20D30", "E10D24", "S20D23",
        "C10D29", "M10D43", "F10D40", "F20D27", "O10D25", "R10D22",
        "A10D20", "T10D46", "L10D22", "U10D72", "U11D13", "T20D42",
        "V10D79", "J10D44",
    ]


def test_taxonomy_probe_preserves_scope_budget_and_recovery_provenance():
    evidence = json.loads((REPORT / "evidence.json").read_text())
    assert evidence["run"]["post_attempts"] == 2
    assert evidence["run"]["retries"] == 1
    assert evidence["run"]["remaining_post_budget"] == 2
    assert evidence["run"]["byte_identical_recovery"] is True
    assert evidence["scope_boundary"] == {
        "assets_requested": 0,
        "browser_requests": 0,
        "child_taxonomy_observed": False,
        "discovery_requests": 0,
        "overlap_observed": False,
        "product_counts_observed": False,
        "products_requested": 0,
        "turso_operations": 0,
    }
    preflight = evidence["next_probe_preflight"]
    assert preflight["probe_club"] == "6603"
    assert preflight["excluded_clubs"] == ["6602", "6604"]
    assert preflight["remaining_root_count"] == 25
    assert preflight["maximum_post_attempts"] == 31
    assert preflight["maximum_returned_product_documents"] == 500
    assert preflight["g10d03_page_size_fallback_allowed"] is False
