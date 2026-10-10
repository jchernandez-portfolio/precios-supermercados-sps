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
