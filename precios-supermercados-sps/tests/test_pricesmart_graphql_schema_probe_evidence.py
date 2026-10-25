import importlib.util
import json
import socket
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "pricesmart" / "2026-08-31-graphql-schema-probe"


def module():
    spec = importlib.util.spec_from_file_location("pricesmart_graphql_schema_verify", REPORT / "verify.py")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_reproduction_is_offline_and_exact(monkeypatch):
    monkeypatch.setattr(socket, "socket", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    assert module().reproduce() == json.loads((REPORT / "evidence.json").read_text())


def test_introspection_is_blocked_and_find_channels_is_unbound():
    evidence = module().reproduce()
    assert evidence["capture"]["post_attempts"] == 2
    assert evidence["capture"]["post_unused"] == 1
    assert evidence["capture"]["retries"] == 0
    assert evidence["capture"]["introspection_graphql_code"] == "GRAPHQL_VALIDATION_FAILED"
    assert evidence["capture"]["find_channels_graphql_code"] == "BAD_USER_INPUT"
    assert evidence["capture"]["upstream_status"] == 404
    assert "/changeme/channels" in evidence["capture"]["upstream_url"]


def test_no_binding_commercial_result_or_tgu_decision_claimed():
    evidence = module().reproduce()
    assert evidence["result"]["club_binding_demonstrated"] is False
    assert evidence["result"]["find_channels_data"] is None
    assert evidence["result"]["price_comparable_skus"] == 0
    assert evidence["result"]["current_price_differences"] is None
    assert evidence["result"]["reported_regular_price_differences"] is None
    assert evidence["result"]["promotion_differences"] is None
    assert evidence["result"]["availability_only_differences"] is None
    assert evidence["result"]["tgu_granularity"] is None


def test_no_product_work_full_persistence_or_turso_claimed():
    evidence = module().reproduce()
    assert evidence["decision"]["status"] == "BLOCKED_PUBLIC_GRAPHQL_TENANT_BINDING"
    assert evidence["decision"]["public_price_tracking_viable"] is False
    assert evidence["decision"]["production_scraper"] is False
    assert evidence["decision"]["full_crawl"] is False
    assert evidence["decision"]["persistence"] is False
    assert evidence["decision"]["model_change"] is False
    assert evidence["decision"]["turso_access"] is False
