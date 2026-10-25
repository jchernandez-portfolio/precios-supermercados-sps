import importlib.util
import json
import socket
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "pricesmart" / "2026-08-31-probe"


def _module():
    spec = importlib.util.spec_from_file_location("pricesmart_probe_verify", REPORT / "verify.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reproduce_is_offline_and_exact(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network access forbidden")

    monkeypatch.setattr(socket, "socket", forbidden)
    observed = _module().reproduce()
    assert observed == json.loads((REPORT / "evidence.json").read_text())
    assert observed["capture"]["get_attempts"] == 8
    assert observed["capture"]["http_200"] == 7


def test_get_searches_are_not_accepted_as_bound_results():
    evidence = _module().reproduce()
    assertions = evidence["get_surface"]["mismatch_assertions"]
    assert assertions == {
        "bags_response_contains_huevos": True,
        "bags_response_contains_bolsas": False,
        "vegetables_response_contains_jabon_dove": True,
        "vegetables_response_contains_vegetables": False,
    }
    assert evidence["get_surface"]["requested_searches_bound_to_response"] is False


def test_graphql_contract_and_clubs_are_observed_but_not_prices():
    evidence = _module().reproduce()
    assert evidence["structured_source"]["base_url"] == "https://graphql-commerce.bloomreach.io"
    assert evidence["structured_source"]["operations_observed"] == [
        "channels",
        "productProjectionsSearch",
        "inventoryEntries",
        "products",
    ]
    assert [row["source_club_key"] for row in evidence["clubs"]["rows"]] == ["6602", "6603", "6604"]
    assert evidence["clubs"]["price_comparable_skus"] == 0
    assert evidence["clubs"]["price_differences"] is None


def test_capture_is_redacted_and_stopped_before_post_or_persistence():
    evidence = _module().reproduce()
    assert evidence["decision"]["probe_status"] == "PAUSED_PENDING_EXPLICIT_POST_AND_DOMAIN_AUTHORIZATION"
    assert evidence["decision"]["persistence"] is False
    assert evidence["decision"]["turso_access"] is False
    with tarfile.open(REPORT / "raw-capture.tar.gz", "r:gz") as archive:
        combined = b"".join(
            archive.extractfile(member).read()
            for member in archive.getmembers()
            if member.isfile()
        )
    assert b"[REDACTED:brDiscoveryAuthKey]" in combined
    assert b"AIzaSyCwr2B5ifWlhHf_eylH5jHalasRyMUIT8w" not in combined
