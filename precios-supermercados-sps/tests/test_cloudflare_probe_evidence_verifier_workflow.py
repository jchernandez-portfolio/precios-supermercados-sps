"""Auditoría de seguridad del workflow que re-verifica evidencia física existente."""

from __future__ import annotations

import re
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "cloudflare-controlled-probe-evidence-verify.yml"
MARKER = ".github/workflows/requests/cloudflare-evidence-verify-request.json"

PINNED_ACTIONS = {
    "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
    "actions/setup-python": "5fda3b95a4ea91299a34e894583c3862153e4b97",
    "actions/download-artifact": "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
    "actions/github-script": "3a2844b7e9c422d3c10d287c895573f7108da1b3",
}


def _load() -> tuple[str, dict[str, object]]:
    raw = WORKFLOW.read_text(encoding="utf-8")
    value = yaml.load(raw, Loader=yaml.BaseLoader)
    assert isinstance(value, dict)
    return raw, value


def test_existing_probe_verifier_has_controlled_main_trigger_and_least_privilege():
    raw, workflow = _load()
    assert workflow["permissions"] == {"contents": "read", "actions": "read"}
    triggers = workflow["on"]
    assert isinstance(triggers, dict) and set(triggers) == {"workflow_dispatch", "push"}

    dispatch = triggers["workflow_dispatch"]
    assert isinstance(dispatch, dict) and set(dispatch) == {"inputs"}
    inputs = dispatch["inputs"]
    assert isinstance(inputs, dict)
    assert set(inputs) == {"source_run_id", "source_run_attempt", "source_commit_sha"}
    assert inputs["source_run_id"]["required"] == "true"
    assert inputs["source_run_attempt"]["default"] == "1"
    assert inputs["source_commit_sha"]["required"] == "true"
    assert triggers["push"] == {"branches": ["main"], "paths": [MARKER]}

    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    assert set(jobs) == {"preflight", "verify-existing-evidence", "publish-status"}
    preflight = jobs["preflight"]
    verifier = jobs["verify-existing-evidence"]
    publisher = jobs["publish-status"]

    assert preflight["if"] == (
        "${{ github.repository == 'jchernandez-portfolio/precios-supermercados-sps' && "
        "github.ref == 'refs/heads/main' }}"
    )
    assert "environment" not in preflight
    assert "permissions" not in preflight
    assert verifier["needs"] == "preflight"
    assert verifier["if"] == "${{ needs.preflight.outputs.allowed == 'true' }}"
    assert verifier["environment"] == "cloudflare-probe"
    assert "permissions" not in verifier
    assert publisher["needs"] == ["preflight", "verify-existing-evidence"]
    assert publisher["permissions"] == {"statuses": "write"}
    assert "environment" not in publisher

    assert "id-token" not in raw
    assert "ACTIONS_ID_TOKEN_REQUEST_TOKEN" not in raw
    assert "ACTIONS_ID_TOKEN_REQUEST_URL" not in raw


def test_push_trigger_is_marker_only_and_fixed_to_existing_evidence():
    raw, workflow = _load()
    preflight = workflow["jobs"]["preflight"]
    preflight_raw = "\n".join(str(step) for step in preflight["steps"])

    assert MARKER in preflight_raw
    assert "github.rest.repos.getCommit" in preflight_raw
    assert "files.length !== 1" in preflight_raw
    assert "probe_verify_trigger_not_marker_only" in preflight_raw
    assert "precios-sps-cloudflare-evidence-verify-request/v1" in preflight_raw
    assert "probe_verify_request_authority_must_be_false" in preflight_raw
    assert 'expected_run_id = "32551882793"' in preflight_raw
    assert 'expected_attempt = "1"' in preflight_raw
    assert 'expected_commit = "cc15edef22709911beb1d1b027ae4c9992da1944"' in preflight_raw
    assert "probe_verify_request_source_not_allowlisted" in preflight_raw
    assert "requestSequence" in preflight_raw
    assert "allowed=true" in preflight_raw
    assert 'payload.get("authority") is not False' in preflight_raw

    assert "CLOUDFLARE_PROBE_OBSERVABILITY_TOKEN" not in preflight_raw
    assert "CLOUDFLARE_ACCOUNT_ID" not in preflight_raw
    assert "CLOUDFLARE_PROBE_PUBLIC_KEY_SPKI_B64URL" not in preflight_raw
    assert "production_authority" in raw
    assert "catalog_accepted" in raw


def test_existing_probe_verifier_has_no_gateway_or_live_fetch_capability():
    raw, _ = _load()
    assert "CLOUDFLARE_PROBE_GATEWAY_URL" not in raw
    assert "PROBE_GATEWAY_URL" not in raw
    assert ".workers.dev" not in raw
    assert "/v1/probe" not in raw
    assert "curl " not in raw
    assert "scripts/probar_la_colonia.py" not in raw
    assert "scripts/diagnosticar_ventanas_la_colonia.py" not in raw
    assert "scripts/descubrir_facets_la_colonia.py" not in raw
    assert "CLOUDFLARE_PROBE_OBSERVABILITY_TOKEN" in raw
    assert "CLOUDFLARE_PROBE_PUBLIC_KEY_SPKI_B64URL" in raw
    assert "CLOUDFLARE_ACCOUNT_ID" in raw


def test_existing_probe_verifier_actions_are_pinned_and_checkouts_are_immutable():
    _, workflow = _load()
    all_steps = []
    for job in workflow["jobs"].values():
        all_steps.extend(job.get("steps", []))
    uses_steps = [step for step in all_steps if "uses" in step]
    for step in uses_steps:
        action, separator, revision = step["uses"].partition("@")
        assert separator and re.fullmatch(r"[0-9a-f]{40}", revision)
        assert PINNED_ACTIONS[action] == revision

    checkouts = [
        step for step in uses_steps if step["uses"].startswith("actions/checkout@")
    ]
    assert len(checkouts) == 2
    assert all(
        step["with"] == {
            "ref": "${{ github.sha }}",
            "persist-credentials": "false",
        }
        for step in checkouts
    )

    verifier = workflow["jobs"]["verify-existing-evidence"]
    download = next(
        step
        for step in verifier["steps"]
        if str(step.get("uses", "")).startswith("actions/download-artifact@")
    )
    assert download["with"] == {
        "name": "precios-sps-cloudflare-controlled-probe",
        "path": ".probe-evidence",
        "github-token": "${{ github.token }}",
        "repository": "${{ github.repository }}",
        "run-id": "${{ needs.preflight.outputs.source_run_id }}",
    }


def test_existing_probe_verifier_binds_source_and_publishes_only_sanitized_result():
    raw, workflow = _load()
    verifier = workflow["jobs"]["verify-existing-evidence"]
    assert verifier["env"]["SOURCE_RUN_ID"] == "${{ needs.preflight.outputs.source_run_id }}"
    assert verifier["env"]["SOURCE_RUN_ATTEMPT"] == (
        "${{ needs.preflight.outputs.source_run_attempt }}"
    )
    assert verifier["env"]["SOURCE_COMMIT_SHA"] == (
        "${{ needs.preflight.outputs.source_commit_sha }}"
    )

    assert 'expected_commit_sha=os.environ["SOURCE_COMMIT_SHA"]' in raw
    assert 'expected_run_id=os.environ["SOURCE_RUN_ID"]' in raw
    assert 'expected_run_attempt=int(os.environ["SOURCE_RUN_ATTEMPT"])' in raw
    assert "verify_controlled_probe_artifact" in raw
    assert "ControlledProbeEventsVerifierClient" in raw
    assert "cloudflare_controlled_probe_events_query" in raw
    assert 'exc.code != "probe_discovery_trace_missing"' in raw
    assert "for query_attempt in range(1, 13):" in raw
    assert "time.sleep(15)" in raw

    publisher = workflow["jobs"]["publish-status"]
    publisher_raw = "\n".join(str(step) for step in publisher["steps"])
    assert "precios-sps/cloudflare-evidence-verifier" in publisher_raw
    assert "precios-sps/cloudflare-verifier-result/" in publisher_raw
    assert "CLOUDFLARE_PROBE_OBSERVABILITY_TOKEN" not in publisher_raw
    assert "CLOUDFLARE_ACCOUNT_ID" not in publisher_raw
    assert "PROBE_PUBLIC_KEY_SPKI_B64URL" not in publisher_raw
