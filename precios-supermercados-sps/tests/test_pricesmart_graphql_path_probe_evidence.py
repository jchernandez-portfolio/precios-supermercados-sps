import importlib.util
import json
import socket
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "pricesmart" / "2026-08-31-graphql-path-probe"


def module():
    spec = importlib.util.spec_from_file_location("pricesmart_graphql_path_verify", REPORT / "verify.py")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_reproduction_is_offline_and_exact(monkeypatch):
    monkeypatch.setattr(socket, "socket", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    assert module().reproduce() == json.loads((REPORT / "evidence.json").read_text())


def test_probe_stops_on_schema_mismatch_without_retry_or_mutation():
    evidence = module().reproduce()
    assert evidence["capture"]["post_attempts"] == 1
    assert evidence["capture"]["post_unused"] == 6
    assert evidence["capture"]["retries"] == 0
    assert evidence["capture"]["http_status"] == 400
    assert evidence["capture"]["graphql_error_codes"] == ["GRAPHQL_VALIDATION_FAILED"]
    assert evidence["request"]["mutation"] is False
    assert evidence["decision"]["status"] == "STOPPED_UNAUTHORIZED_SCHEMA_OPERATION_REQUIRED"


def test_find_channels_is_observed_but_not_claimed_as_authorized():
    evidence = module().reproduce()
    assert evidence["result"]["graphql_reached"] is True
    assert evidence["result"]["graphql_data"] is False
    assert evidence["decision"]["next_operation_observed"] == "findChannels"
    assert evidence["decision"]["next_operation_was_authorized"] is False


def test_no_commercial_result_or_turso_claimed():
    evidence = module().reproduce()
    assert evidence["result"]["price_comparable_skus"] == 0
    assert evidence["result"]["current_price_differences"] is None
    assert evidence["result"]["reported_regular_price_differences"] is None
    assert evidence["result"]["promotion_differences"] is None
    assert evidence["result"]["availability_only_differences"] is None
    assert evidence["decision"]["production_scraper"] is False
    assert evidence["decision"]["full_crawl"] is False
    assert evidence["decision"]["persistence"] is False
    assert evidence["decision"]["turso_access"] is False
