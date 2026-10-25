from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "probar_muestra_sps_la_colonia_bound.py"
SPEC = importlib.util.spec_from_file_location("probar_muestra_sps_la_colonia_bound", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def _request(*, headers=None, url="https://www.lacolonia.com/", payload=None):
    return SimpleNamespace(
        headers=headers or {},
        url=url,
        post_data_json=payload,
    )


def test_region_tracker_replays_only_exact_fingerprint_from_header() -> None:
    raw = "opaque-sps-region"
    tracker = module.RegionContextTracker(
        expected_fingerprint=module._stable_fingerprint(raw)
    )
    tracker.reset_and_enable()
    tracker.observe_request(
        _request(headers={"x-vtex-region": raw, "x-other": "ignored"})
    )

    headers, query = tracker.replay_context()

    assert tracker.fingerprint_verified is True
    assert tracker.replayable_count == 1
    assert headers == {"x-vtex-region": raw}
    assert query == ()


def test_region_tracker_replays_exact_query_placement_without_persisting_url() -> None:
    raw = "opaque-sps-region"
    tracker = module.RegionContextTracker(
        expected_fingerprint=module._stable_fingerprint(raw)
    )
    tracker.reset_and_enable()
    tracker.observe_request(
        _request(
            url=(
                "https://www.lacolonia.com/_v/segment/graphql/v1?"
                "operationName=storeQuery&regionId=opaque-sps-region"
            )
        )
    )

    headers, query = tracker.replay_context()

    assert headers == {}
    assert query == (("regionId", raw),)
    assert tracker.replayable_count == 1


def test_region_tracker_fails_closed_when_only_body_placement_matches() -> None:
    raw = "opaque-sps-region"
    tracker = module.RegionContextTracker(
        expected_fingerprint=module._stable_fingerprint(raw)
    )
    tracker.reset_and_enable()
    tracker.observe_request(
        _request(payload={"variables": {"regionId": raw}})
    )

    assert tracker.fingerprint_verified is True
    assert tracker.body_only_match_observed is True
    assert tracker.replayable_count == 0
    with pytest.raises(
        module.passive.MvpSampleError,
        match="sps_region_binding_observed_but_not_replayable",
    ):
        tracker.replay_context()


def test_region_tracker_rejects_wrong_region_value() -> None:
    tracker = module.RegionContextTracker(
        expected_fingerprint=module._stable_fingerprint("expected")
    )
    tracker.reset_and_enable()
    tracker.observe_request(_request(headers={"x-vtex-region": "other"}))

    assert tracker.fingerprint_verified is False
    assert tracker.replayable_count == 0
    with pytest.raises(
        module.passive.MvpSampleError,
        match="sps_region_binding_not_observed_same_run",
    ):
        tracker.replay_context()


def test_query_context_is_appended_without_replacing_catalog_contract() -> None:
    base = (
        "https://www.lacolonia.com/_v/segment/graphql/v1?"
        "operationName=productSearchV3&workspace=master"
    )
    result = module._append_query_context(base, (("regionId", "opaque"),))

    assert "operationName=productSearchV3" in result
    assert "workspace=master" in result
    assert "regionId=opaque" in result


def test_failure_artifact_exposes_only_bounded_diagnostics() -> None:
    artifact = module._failure_artifact(
        "sps_region_binding_not_replayable",
        {
            "location_verified_same_run": True,
            "graphql_responses_seen": 9,
            "product_search_payloads_seen": 0,
            "catalog_candidates_seen": 0,
            "blocked_http_status_observed": None,
            "region_binding_fingerprint_verified": True,
            "region_context_replayable_placements": 1,
            "region_context_body_only_observed": False,
            "explicit_product_search_requests": 0,
            "regionid": "must-not-leak",
            "request_url": "must-not-leak",
        },
    )

    assert artifact["capture_strategy"] == "passive_then_single_explicit_bound"
    assert artifact["region_binding_fingerprint_verified"] is True
    assert artifact["region_context_replayable_placements"] == 1
    assert artifact["explicit_product_search_requests"] == 0
    assert "regionid" not in artifact
    assert "request_url" not in artifact


def test_production_fingerprint_matches_canonical_project_state() -> None:
    state = (PROJECT_ROOT / "docs" / "PROJECT_STATE.md").read_text(encoding="utf-8")
    assert module.SPS_REGION_FINGERPRINT in state
    assert "ACTIVE_AUTHORIZATION_IDS = []" in state


def test_cli_requires_both_live_fuses_before_browser_path(monkeypatch) -> None:
    monkeypatch.setattr(
        module,
        "_run_live_bound_sample",
        lambda **_: (_ for _ in ()).throw(AssertionError("browser must not run")),
    )

    with pytest.raises(SystemExit) as missing_live:
        module.main(["--allow-single-explicit-product-search"])
    assert missing_live.value.code == 2

    with pytest.raises(SystemExit) as missing_explicit:
        module.main(["--live-read-only"])
    assert missing_explicit.value.code == 2
