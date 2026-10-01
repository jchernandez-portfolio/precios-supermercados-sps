from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from precios_supermercados.automation.la_colonia_file_dispatcher import (
    DIAGNOSTIC_WORKFLOW,
    FACET_DISCOVERY_WORKFLOW,
    LIVE_WORKFLOW,
    evaluate_file_request,
    request_marker,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
COMMAND_WORKFLOW = (
    REPO_ROOT
    / ".github/workflows/precios-supermercados-sps-la-colonia-command.yml"
)
CONTROLLER = (
    REPO_ROOT
    / "precios-supermercados-sps/scripts/controlar_solicitud_archivo_la_colonia.js"
)
RECOVERY_WORKFLOW = (
    REPO_ROOT
    / ".github/workflows/precios-supermercados-sps-la-colonia-dispatch-recovery.yml"
)
OBSERVER = (
    REPO_ROOT
    / "precios-supermercados-sps/scripts/validar_resultado_controlador_la_colonia.js"
)
OPERATIONAL_PATH = (
    "precios-supermercados-sps/.automation/la-colonia-live-command.json"
)
HEAD_SHA = "1a515913a514d3b246c3445eddfff8fcb0d951b4"
FORBIDDEN_NETWORK_IMPORTS = frozenset(
    {"requests", "urllib", "httpx", "aiohttp", "socket"}
)


def authorized_context(**overrides):
    value = {
        "repository_owner": "jchernandez-portfolio",
        "repository_full_name": "jchernandez-portfolio/precios-supermercados-sps",
        "pr_number": 7,
        "state": "open",
        "base_repo_full_name": "jchernandez-portfolio/precios-supermercados-sps",
        "head_repo_full_name": "jchernandez-portfolio/precios-supermercados-sps",
        "head_repo_fork": False,
        "head_ref": "feature/la-colonia-full-crawl-validation",
        "head_sha": HEAD_SHA,
        "command_file_changed": True,
        "command_file_status": "ok",
    }
    value.update(overrides)
    return value


@pytest.fixture
def smoke_command():
    return {
        "request_id": "la-colonia-smoke-synthetic-001",
        "supermarket": "la_colonia",
        "mode": "smoke",
        "page_size": 10,
        "max_pages": 2,
        "max_products": 0,
        "delay_seconds": 1.5,
        "profile": "baseline",
        "thresholds": None,
        "allow_full": False,
    }


@pytest.fixture
def staged_command():
    return {
        "request_id": "la-colonia-staged-synthetic-001",
        "supermarket": "la_colonia",
        "mode": "staged",
        "page_size": 20,
        "max_pages": 0,
        "max_products": 100,
        "delay_seconds": 1.5,
        "profile": "baseline",
        "thresholds": None,
        "allow_full": False,
    }


@pytest.fixture
def diagnostic_command():
    return {
        "request_id": "la-colonia-window-diagnostic-380-399-001",
        "supermarket": "la_colonia",
        "mode": "diagnostic_overlap",
        "diagnostic_plan": "frontier_380_399_v1",
        "delay_seconds": 1.5,
        "allow_full": False,
    }


@pytest.fixture
def facet_command():
    return {
        "request_id": "la-colonia-facet-discovery-001",
        "supermarket": "la_colonia",
        "mode": "facet_discovery",
        "discovery_plan": "catalog_categories_v1",
        "delay_seconds": 1.5,
        "allow_full": False,
    }


def evaluate(command, *, context=None, comments=()):
    return evaluate_file_request(
        authorized_context() if context is None else context,
        json.dumps(command),
        comments,
    )


def _network_imports(source: str) -> set[str]:
    """Devuelve módulos raíz de red importados realmente por código Python."""

    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0])
    return imported & FORBIDDEN_NETWORK_IMPORTS


@pytest.mark.parametrize(
    ("fixture_name", "mode", "workflow"),
    [
        ("smoke_command", "smoke", LIVE_WORKFLOW),
        ("staged_command", "staged", LIVE_WORKFLOW),
        ("diagnostic_command", "diagnostic_overlap", DIAGNOSTIC_WORKFLOW),
        ("facet_command", "facet_discovery", FACET_DISCOVERY_WORKFLOW),
    ],
)
def test_each_closed_contract_is_independently_accepted(
    request, fixture_name, mode, workflow
):
    command = request.getfixturevalue(fixture_name)
    decision = evaluate(command)
    assert decision.accepted is True
    assert decision.mode == mode
    assert decision.workflow == workflow
    assert decision.inputs is not None
    assert "workflow" not in command
    assert "workflow" not in decision.inputs


def test_transition_from_diagnostic_to_facet_preserves_closed_validation(
    diagnostic_command, facet_command
):
    previous = evaluate(diagnostic_command)
    current = evaluate(facet_command)
    assert previous.accepted is True
    assert previous.workflow == DIAGNOSTIC_WORKFLOW
    assert current.accepted is True
    assert current.workflow == FACET_DISCOVERY_WORKFLOW
    assert current.inputs == {
        "request_id": "la-colonia-facet-discovery-001",
        "discovery_plan": "catalog_categories_v1",
        "delay_seconds": "1.5",
    }


def test_command_file_changed_false_rejects_without_dispatch_or_comment(facet_command):
    decision = evaluate(
        facet_command,
        context=authorized_context(
            command_file_changed=False,
            command_file_status="not_modified",
        ),
    )
    assert decision.accepted is False
    assert decision.should_comment is False
    assert decision.workflow is None
    assert decision.inputs is None


def test_command_file_changed_true_allows_evaluation(facet_command):
    decision = evaluate(facet_command)
    assert decision.accepted is True
    assert decision.head_sha == HEAD_SHA


def test_superseded_commit_rejects_silently(facet_command):
    decision = evaluate(
        facet_command,
        context=authorized_context(command_file_status="superseded"),
    )
    assert decision.accepted is False
    assert decision.should_comment is False
    assert "reemplazado" in decision.reason


def test_missing_command_file_rejects_with_reason():
    decision = evaluate_file_request(
        authorized_context(command_file_status="missing"),
        None,
    )
    assert decision.accepted is False
    assert decision.should_comment is True
    assert "no existe" in decision.reason


def test_facet_request_id_is_normalized_from_trusted_code(facet_command):
    decision = evaluate(facet_command)
    assert decision.request_id == "la-colonia-facet-discovery-001"
    assert decision.inputs["request_id"] == "la-colonia-facet-discovery-001"
    assert decision.inputs["delay_seconds"] == "1.5"


def test_existing_comment_marker_does_not_control_replay(facet_command):
    marker = request_marker("la-colonia-facet-discovery-001")
    decision = evaluate(facet_command, comments=[marker])
    assert decision.accepted is True


def test_absence_of_comment_only_means_request_is_eligible_not_that_a_run_exists(
    facet_command,
):
    decision = evaluate(facet_command, comments=[])
    assert decision.accepted is True
    assert decision.workflow == FACET_DISCOVERY_WORKFLOW
    assert not hasattr(decision, "controller_run_id")
    assert not hasattr(decision, "dispatch_sent")


@pytest.mark.parametrize(
    "field",
    ["url", "URL", "query", "selectedFacets", "workflow", "headers"],
)
def test_arbitrary_control_fields_are_rejected(facet_command, field):
    facet_command[field] = "forbidden"
    decision = evaluate(facet_command)
    assert decision.accepted is False
    assert decision.inputs is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"mode": "unknown"},
        {"allow_full": True},
        {"delay_seconds": 1.0},
        {"request_id": "la-colonia-facet-discovery-002"},
    ],
)
def test_invalid_facet_contracts_are_rejected(facet_command, overrides):
    facet_command.update(overrides)
    decision = evaluate(facet_command)
    assert decision.accepted is False
    assert decision.inputs is None


def test_invalid_head_sha_is_rejected(facet_command):
    decision = evaluate(
        facet_command,
        context=authorized_context(head_sha="not-a-sha"),
    )
    assert decision.accepted is False
    assert "SHA" in decision.reason


def test_fork_is_rejected(facet_command):
    decision = evaluate(
        facet_command,
        context=authorized_context(
            head_repo_full_name="other/Portafolio",
            head_repo_fork=True,
        ),
    )
    assert decision.accepted is False
    assert "forks" in decision.reason


def test_invalid_or_missing_pr_number_is_rejected(facet_command):
    decision = evaluate(
        facet_command,
        context=authorized_context(pr_number=0),
    )
    assert decision.accepted is False
    assert "número" in decision.reason


def test_expected_event_and_paths_filter_are_declared_in_main_workflow():
    text = COMMAND_WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request_target:" in text
    assert "types: [synchronize]" in text
    assert "paths:" in text
    assert text.count(OPERATIONAL_PATH) == 1
    assert "ref: ${{ github.workflow_sha }}" in text
    assert "persist-credentials: false" in text


def test_action_other_than_synchronize_is_not_in_the_trigger_allow_list():
    text = COMMAND_WORKFLOW.read_text(encoding="utf-8")
    trigger = text.split("pull_request_target:", 1)[1].split("permissions:", 1)[0]
    assert "types: [synchronize]" in trigger
    for action in ("opened", "reopened", "edited", "closed"):
        assert action not in trigger


def test_controller_recovers_commit_and_file_from_event_head_sha():
    text = CONTROLLER.read_text(encoding="utf-8")
    assert "context.payload.after || payloadPull?.head?.sha" in text
    assert "github.rest.repos.getCommit" in text
    assert "file.filename === COMMAND_PATH" in text
    assert "github.rest.repos.getContent" in text
    assert "ref: eventHeadSha" in text


def test_controller_is_globally_blocked_without_dispatch_endpoint():
    text = CONTROLLER.read_text(encoding="utf-8")
    assert "GLOBAL LIVE BLOCKED" in text
    assert "/actions/workflows/" not in text
    assert "github.request(" not in text


def test_comments_are_observability_only_and_controller_does_not_dispatch():
    text = CONTROLLER.read_text(encoding="utf-8")
    assert "/dispatches" not in text
    assert "github.graphql" not in text
    assert "createComment" not in text
    assert "listComments" not in text


def test_upload_artifact_runs_always_and_recovery_never_dispatches():
    workflow = COMMAND_WORKFLOW.read_text(encoding="utf-8")
    recovery = RECOVERY_WORKFLOW.read_text(encoding="utf-8")
    observer = OBSERVER.read_text(encoding="utf-8")
    assert "if: always()" in workflow
    assert "dispatcher-result.json" in workflow
    assert "workflow_run:" in recovery
    assert "ref: ${{ github.workflow_sha }}" in recovery
    assert "FACET_WORKFLOW" in observer
    assert "RECOVERY_REQUIRED" in observer
    assert "/dispatches" not in recovery
    assert "/dispatches" not in observer


def test_transition_audit_is_offline_only():
    source = Path(__file__).read_text(encoding="utf-8")
    assert _network_imports(source) == set()


def test_transition_audit_rejects_requests_import():
    assert _network_imports("import requests\n") == {"requests"}


def test_transition_audit_rejects_urllib_import_from():
    assert _network_imports("from urllib import request\n") == {"urllib"}


def test_transition_audit_accepts_source_without_network_imports():
    assert _network_imports("import json\nfrom pathlib import Path\n") == set()


def test_transition_audit_ignores_network_names_inside_strings():
    source = 'message = "requests. urllib httpx aiohttp socket."\n'
    assert _network_imports(source) == set()
