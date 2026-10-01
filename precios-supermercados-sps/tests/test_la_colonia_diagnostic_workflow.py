from __future__ import annotations

import json
from pathlib import Path

from precios_supermercados.automation.la_colonia_file_dispatcher import (
    DIAGNOSTIC_WORKFLOW,
    evaluate_file_request,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = (
    REPOSITORY_ROOT
    / ".github"
    / "workflows"
    / "precios-supermercados-sps-la-colonia-diagnostic.yml"
)


def valid_context():
    return {
        "repository_owner": "jchernandez-portfolio",
        "repository_full_name": "jchernandez-portfolio/precios-supermercados-sps",
        "pr_number": 7,
        "state": "open",
        "base_repo_full_name": "jchernandez-portfolio/precios-supermercados-sps",
        "head_repo_full_name": "jchernandez-portfolio/precios-supermercados-sps",
        "head_repo_fork": False,
        "head_ref": "feature/la-colonia-full-crawl-validation",
        "head_sha": "a" * 40,
        "command_file_changed": True,
        "command_file_status": "ok",
    }


def diagnostic_command():
    return {
        "request_id": "la-colonia-window-diagnostic-380-399-001",
        "supermarket": "la_colonia",
        "mode": "diagnostic_overlap",
        "diagnostic_plan": "frontier_380_399_v1",
        "delay_seconds": 1.5,
        "allow_full": False,
    }


def test_diagnostic_workflow_is_manual_only():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    for forbidden_trigger in (
        "schedule:",
        "push:",
        "pull_request:",
        "pull_request_target:",
        "issue_comment:",
    ):
        assert forbidden_trigger not in text


def test_workflow_has_only_closed_inputs():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "diagnostic_plan:" in text
    assert "frontier_380_399_v1" in text
    assert "request_id:" in text
    assert "delay_seconds:" in text
    for forbidden_input in (
        "from:",
        "to:",
        "windows:",
        "order_by:",
        "max_requests:",
        "url:",
        "query:",
        "selectedFacets:",
        "allow_full:",
        "max_pages:",
        "max_products:",
        "profile:",
        "thresholds:",
    ):
        assert forbidden_input not in text


def test_workflow_calls_dedicated_script_and_artifact_names():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "scripts/diagnosticar_ventanas_la_colonia.py" in text
    assert "diagnostic-summary.json" in text
    assert "diagnostic-summary.md" in text
    assert "la-colonia-window-diagnostic" in text


def test_exit_code_two_is_technical_success_but_three_to_five_fail():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert '"0"|"2"' in text
    assert '"3"|"4"|"5"' in text


def test_trusted_dispatcher_accepts_exact_diagnostic_contract():
    decision = evaluate_file_request(
        valid_context(),
        json.dumps(diagnostic_command()),
        existing_comment_markers=(),
    )
    assert decision.accepted is True
    assert decision.mode == "diagnostic_overlap"
    assert decision.workflow == DIAGNOSTIC_WORKFLOW
    assert decision.inputs == {
        "request_id": "la-colonia-window-diagnostic-380-399-001",
        "diagnostic_plan": "frontier_380_399_v1",
        "delay_seconds": "1.5",
    }
