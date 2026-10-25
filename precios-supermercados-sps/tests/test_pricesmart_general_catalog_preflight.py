import importlib.util
import json
import socket
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/pricesmart/2026-09-02-general-catalog-preflight"


def _module():
    spec = importlib.util.spec_from_file_location("pricesmart_general_preflight", REPORT / "verify.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_general_catalog_preflight_is_offline_and_fail_closed(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network access forbidden")

    monkeypatch.setattr(socket, "socket", forbidden)
    _module().main()

    evidence = json.loads((REPORT / "evidence.json").read_text())
    assert evidence["live_pricesmart_requests"] == 0
    assert evidence["turso_operations"] == 0
    assert evidence["discovery_observations"]["query_count"] == 194
    assert evidence["discovery_observations"]["q_values"] == ["G10D03"]
    assert evidence["g10d03_taxonomy"]["node_count"] == 117
    assert evidence["decision"] == {
        "existing_alimentos_reusable_without_recrawl": True,
        "full_request_budget_calculable": False,
        "g10d03_complete": True,
        "general_catalog_complete": False,
        "minimal_partitions_determined": False,
        "next_gate": "small live taxonomy/count probe authorization",
        "root_taxonomy_demonstrated": False,
    }


def test_root_taxonomy_route_remains_a_derived_unprobed_contract():
    evidence = json.loads((REPORT / "evidence.json").read_text())
    contract = evidence["offline_application_contract"]
    assert contract["inferred_public_route_from_client_proxy"] == "/api/ct/getFacetCategories"
    assert contract["route_status"] == "derived_from_captured_client_code_not_live_probed"
    assert contract["root_filter_expression"] == "parent is not defined"
    assert contract["configured_category_limit"] == 200
