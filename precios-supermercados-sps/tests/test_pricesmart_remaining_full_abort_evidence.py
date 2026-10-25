import importlib.util
import json
import socket
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/pricesmart/2026-09-02-remaining-full-abort"


def _module():
    spec = importlib.util.spec_from_file_location("pricesmart_full_abort", REPORT / "verify.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_remaining_full_abort_reproduces_offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network")

    monkeypatch.setattr(socket, "socket", forbidden)
    assert _module().reproduce() == json.loads((REPORT / "evidence.json").read_text())


def test_remaining_full_stopped_before_accepting_incomplete_window():
    evidence = json.loads((REPORT / "evidence.json").read_text())
    assert evidence["attempt"]["post_attempts"] == 1
    assert evidence["attempt"]["accepted_pages"] == 0
    assert evidence["attempt"]["turso_operations"] == 0
    assert evidence["failure_evidence"]["repeated_product_ids"] == ["507265"]
    assert evidence["failure_evidence"]["combined_unique_products"] == 57
    assert evidence["failure_evidence"]["expected_products"] == 58
    assert evidence["failure_evidence"]["partial_probe_windows_reusable_for_completeness"] is False


def test_revised_budget_starts_each_root_at_zero():
    plan = json.loads((REPORT / "evidence.json").read_text())["revised_plan"]
    assert plan["sps_base_requests"] == plan["florencia_base_requests"] == 25
    assert plan["new_base_requests"] == 50
    assert plan["prior_consumed_attempts"] == 1
    assert plan["retry_reserve"] == 5
    assert plan["global_post_attempts_max"] == 56
    assert plan["global_documents_returned_max"] == 3352
    assert all(row["offsets"][0] == 0 for row in plan["root_windows"])
    assert plan["alimentos_recrawl_requests"] == 0
    assert plan["requires_authorization_extension"] is True
