import importlib.util
import json
import socket
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "pricesmart" / "2026-08-31-graphql-probe"


def module():
    spec = importlib.util.spec_from_file_location("pricesmart_graphql_verify", REPORT / "verify.py")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_reproduction_is_offline_and_exact(monkeypatch):
    monkeypatch.setattr(socket, "socket", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    assert module().reproduce() == json.loads((REPORT / "evidence.json").read_text())


def test_probe_stops_on_root_404_without_mutation_or_retry():
    evidence = module().reproduce()
    assert evidence["capture"]["post_attempts"] == 1
    assert evidence["capture"]["retries"] == 0
    assert evidence["capture"]["http_status"] == 404
    assert evidence["capture"]["response_body"] == "Cannot POST /"
    assert evidence["request"]["mutation"] is False
    assert evidence["decision"]["status"] == "STOPPED_OTHER_ENDPOINT_PATH_REQUIRED"


def test_no_commercial_result_or_turso_claimed():
    evidence = module().reproduce()
    assert evidence["result"]["graphql_reached"] is False
    assert evidence["result"]["price_comparable_skus"] == 0
    assert evidence["result"]["current_price_differences"] is None
    assert evidence["decision"]["persistence"] is False
    assert evidence["decision"]["turso_access"] is False
