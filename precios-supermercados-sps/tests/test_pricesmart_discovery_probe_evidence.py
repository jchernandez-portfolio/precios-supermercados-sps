import importlib.util
import json
import socket
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/pricesmart/2026-09-02-discovery-probe"


def _module():
    spec = importlib.util.spec_from_file_location("pricesmart_discovery_probe", REPORT / "verify.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_discovery_probe_reproduces_offline_and_fail_closed(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network access forbidden")

    monkeypatch.setattr(socket, "socket", forbidden)
    assert _module().reproduce() == json.loads((REPORT / "evidence.json").read_text())


def test_discovery_probe_totals_taxonomy_and_page_size():
    evidence = json.loads((REPORT / "evidence.json").read_text())
    assert evidence["run"]["post_attempts"] == evidence["run"]["http_200"] == 26
    assert evidence["run"]["retries"] == 0
    assert evidence["run"]["documents_returned"] == 455
    assert evidence["pagination"]["useful_page_size_demonstrated"] == 200
    assert evidence["catalog"]["remaining_num_found_sum"] == 1653
    assert evidence["catalog"]["gross_product_observations_per_club"] == 2777
    assert evidence["taxonomy"]["combined_unique_nodes"] == 546
    assert evidence["taxonomy"]["root_nodes"] == 26
    assert evidence["taxonomy"]["orphan_nodes"] == 0
    assert evidence["taxonomy"]["structure_collisions"] == 0


def test_discovery_probe_overlap_is_not_overclaimed():
    overlap = json.loads((REPORT / "evidence.json").read_text())["overlap"]
    assert overlap["sampled_product_memberships"] == 443
    assert overlap["sampled_unique_products"] == 440
    assert overlap["identity_proven_cross_root_products"] == 3
    assert overlap["foreign_root_facet_signal_count"] == 11
    assert overlap["sampled_product_overlap_with_alimentos"] == 0
    assert overlap["exact_full_overlap_demonstrated"] is False


def test_discovery_probe_full_budget_reuses_authorized_raw():
    evidence = json.loads((REPORT / "evidence.json").read_text())
    decision = evidence["partition_decision"]
    assert len(decision["remaining_direct_root_partitions"]) == 23
    assert decision["child_partitions_required"] == []
    assert decision["empty_roots"] == ["U11D13", "J10D44"]
    budget = evidence["full_budget"]
    assert budget["new_sps_base_requests_reusing_probe"] == 21
    assert budget["new_florencia_base_requests"] == 25
    assert budget["base_post_requests"] == 46
    assert budget["retry_reserve"] == 5
    assert budget["maximum_post_attempts"] == 51
    assert budget["alimentos_recrawl_requests"] == 0
    assert budget["turso_operations"] == 0
