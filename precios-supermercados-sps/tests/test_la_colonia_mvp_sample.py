from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from precios_supermercados.scrapers.la_colonia import LaColoniaExtractor

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "la_colonia"
SCRIPT = PROJECT_ROOT / "scripts" / "probar_muestra_sps_la_colonia.py"
SPEC = importlib.util.spec_from_file_location("probar_muestra_sps_la_colonia", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

FIXED_TIME = datetime(2026, 8, 24, 23, 0, tzinfo=UTC)


def _payload(*, total: int = 9291):
    value = json.loads((FIXTURES / "product_search_page.json").read_text(encoding="utf-8"))
    value["data"]["productSearch"]["recordsFiltered"] = total
    return value


def _result():
    payload = _payload()
    extractor = LaColoniaExtractor(clock=lambda: FIXED_TIME)
    return extractor.parse_payload(
        payload,
        scrape_run_id="mvp_sample_test",
        source_url=extractor.build_page_url(page_size=5),
        page_size=5,
    )


def _walk_keys(value):
    if isinstance(value, dict):
        for key, nested in value.items():
            yield str(key).casefold()
            yield from _walk_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_keys(nested)


def _fake_response(variables: dict[str, object]):
    url = (
        "https://www.lacolonia.com/_v/segment/graphql/v1?"
        + urlencode(
            {
                "operationName": "productSearchV3",
                "variables": json.dumps(variables, separators=(",", ":")),
            }
        )
    )
    request = SimpleNamespace(url=url, post_data_json=None)
    return SimpleNamespace(url=url, request=request)


def test_builds_small_sps_sample_from_existing_extractor_contract() -> None:
    artifact = module.build_sample_artifact(
        extraction_result=_result(),
        sample_size=5,
        observed_at_utc=FIXED_TIME,
        location_verified_same_run=True,
    )

    assert artifact["sample_type"] == "la_colonia_sps_mvp_read_only"
    assert artifact["sample_only"] is True
    assert artifact["location_id"] == "la_colonia_sps"
    assert artifact["city"] == "San Pedro Sula"
    assert artifact["location_verified_same_run"] is True
    assert artifact["sample_skus_returned"] == 5
    assert artifact["sample_skus_with_price"] == 3
    assert artifact["validation_passed"] is True
    assert artifact["production_authority"] is False
    assert artifact["catalog_accepted"] is False
    assert artifact["commercial_persistence"] is False
    assert artifact["extraction_enabled"] is False
    assert artifact["raw_context_persisted"] is False

    first = artifact["products"][0]
    assert first["source_name"] == "Malteada Sula Chocosula 473 Ml"
    assert first["brand"] == "Sula"
    assert first["presentation"] == "473 Ml"
    assert first["current_price"] == "19.9"
    assert first["product_id"] == "1001"
    assert first["item_id"] == "2001"


def test_sample_never_relabels_unverified_context_as_sps() -> None:
    with pytest.raises(module.MvpSampleError, match="sps_location_not_verified_same_run"):
        module.build_sample_artifact(
            extraction_result=_result(),
            sample_size=5,
            observed_at_utc=FIXED_TIME,
            location_verified_same_run=False,
        )


def test_sample_artifact_does_not_persist_request_or_session_context() -> None:
    artifact = module.build_sample_artifact(
        extraction_result=_result(),
        sample_size=5,
        observed_at_utc=FIXED_TIME,
        location_verified_same_run=True,
    )
    keys = set(_walk_keys(artifact))

    assert "source_url" not in keys
    assert "request_url" not in keys
    assert "regionid" not in keys
    assert "headers" not in keys
    assert "cookies" not in keys
    assert "token" not in keys
    assert "vtex_session" not in keys
    assert "raw_values" not in keys


def test_historical_catalog_signature_remains_preferred_but_not_required() -> None:
    historical = _fake_response(
        {
            "query": "supermercado",
            "selectedFacets": [{"key": "category-1", "value": "supermercado"}],
            "from": 0,
            "to": 9,
        }
    )
    changed_routing = _fake_response(
        {
            "query": "",
            "selectedFacets": [{"key": "department", "value": "mercado"}],
            "from": 0,
            "to": 9,
        }
    )

    assert module._historical_catalog_signature(historical) is True
    assert module._historical_catalog_signature(changed_routing) is False
    assert module._catalog_candidate_rank(historical, _payload())[0] == 1
    assert module._catalog_candidate_rank(changed_routing, _payload())[0] == 0


def test_fallback_rank_prefers_larger_product_search_after_catalog_navigation() -> None:
    current_shape = _fake_response(
        {
            "query": "",
            "selectedFacets": [{"key": "department", "value": "mercado"}],
            "from": 0,
            "to": 9,
        }
    )
    recommendation = _fake_response(
        {
            "query": "",
            "selectedFacets": [],
            "from": 0,
            "to": 4,
        }
    )

    catalog_rank = module._catalog_candidate_rank(current_shape, _payload(total=9291))
    recommendation_rank = module._catalog_candidate_rank(
        recommendation, _payload(total=25)
    )

    assert catalog_rank[0] == 0
    assert catalog_rank > recommendation_rank
    assert catalog_rank[1] == 9291


def test_failure_artifact_is_sanitized_and_keeps_only_operational_counts() -> None:
    artifact = module.build_failure_artifact(
        reason="catalog_product_search_response_not_observed",
        diagnostic={
            "location_verified_same_run": True,
            "graphql_responses_seen": 4,
            "product_search_payloads_seen": 2,
            "catalog_candidates_seen": 2,
            "blocked_http_status_observed": None,
            "request_url": "must-not-leak",
            "regionid": "must-not-leak",
        },
        observed_at_utc=FIXED_TIME,
    )

    assert artifact["result"] == "stopped"
    assert artifact["reason"] == "catalog_product_search_response_not_observed"
    assert artifact["location_verified_same_run"] is True
    assert artifact["graphql_responses_seen"] == 4
    assert artifact["product_search_payloads_seen"] == 2
    assert artifact["catalog_candidates_seen"] == 2
    assert artifact["blocked_http_status_observed"] is None
    assert artifact["production_authority"] is False
    assert artifact["catalog_accepted"] is False
    assert artifact["raw_context_persisted"] is False
    keys = set(_walk_keys(artifact))
    assert "request_url" not in keys
    assert "regionid" not in keys


def test_live_failure_writes_sanitized_artifact_for_next_offline_diagnosis(
    monkeypatch, tmp_path: Path
) -> None:
    def fail(**_):
        raise module.MvpSampleError(
            "catalog_product_search_response_not_observed",
            diagnostic={
                "location_verified_same_run": True,
                "graphql_responses_seen": 3,
                "product_search_payloads_seen": 0,
                "catalog_candidates_seen": 0,
            },
        )

    monkeypatch.setattr(module, "_run_live_sample", fail)
    output = tmp_path / "sample.json"

    exit_code = module.main(
        ["--live-read-only", "--sample-size", "5", "--output", str(output)]
    )

    assert exit_code == 3
    artifact = json.loads(output.read_text(encoding="utf-8"))
    assert artifact["result"] == "stopped"
    assert artifact["graphql_responses_seen"] == 3
    assert artifact["location_verified_same_run"] is True
    assert artifact["production_authority"] is False


def test_live_fuse_is_required_before_browser_path(monkeypatch) -> None:
    monkeypatch.setattr(
        module,
        "_run_live_sample",
        lambda **_: (_ for _ in ()).throw(AssertionError("browser path must not run")),
    )

    with pytest.raises(SystemExit) as captured:
        module.main(["--sample-size", "5"])
    assert captured.value.code == 2
