from __future__ import annotations

import copy

import pytest

from precios_supermercados.location_binding_transition import (
    LocationBindingTransitionError,
    LocationBindingTransitionStatus,
    evaluate_location_binding_artifact,
    propose_city_location_binding,
)
from precios_supermercados.locations import LocationConfig, LocationGranularity

CITY_KEY = "localStorage:regionid:sha256:" + "a" * 64
STORE_KEY = "request_variable:storeid:sha256:" + "b" * 64


def unconfirmed_sps() -> LocationConfig:
    return LocationConfig(
        location_id="la_colonia_sps",
        supermarket_id="la_colonia",
        city_id="sps",
        city_name="San Pedro Sula",
        granularity=LocationGranularity.UNKNOWN,
        is_available=True,
        in_scope=True,
        extraction_enabled=False,
        technical_binding_confirmed=False,
        source_location_key=None,
        evidence="website_city_selector",
    )


def artifact(
    *,
    granularity: str = "city",
    confidence: str = "strong",
    technical: bool = True,
    source_key: str | None = CITY_KEY,
    store_selection_observed: bool = False,
    selected_store: str | None = None,
) -> dict:
    return {
        "mode": "live",
        "started_at": "2026-08-22T15:00:00-0600",
        "completed_at": "2026-08-22T15:00:05-0600",
        "target_host": "www.lacolonia.com",
        "target_city": "San Pedro Sula",
        "available_cities": ["San Pedro Sula", "Tegucigalpa"],
        "available_stores": ["Mega Mall", "Plaza Pedregal"] if store_selection_observed else [],
        "selected_store": selected_store,
        "store_selection_observed": store_selection_observed,
        "logical_actions": 4 if store_selection_observed else 3,
        "stop_reason": None,
        "errors": [],
        "binding_report": {
            "city_name": "San Pedro Sula",
            "store_selection_observed": store_selection_observed,
            "store_name": selected_store,
            "granularity_candidate": granularity,
            "confidence": confidence,
            "technical_binding_observed": technical,
            "source_location_key_candidate": source_key,
            "decisive_stage": "after_store" if granularity == "store" else "after_city",
            "signals": [],
            "raw_values_exposed": False,
        },
        "production_authority": False,
        "catalog_accepted": False,
        "extraction_enabled": False,
    }


def test_strong_city_evidence_is_ready_but_does_not_enable_extraction() -> None:
    transition = evaluate_location_binding_artifact(artifact())

    assert transition.status is LocationBindingTransitionStatus.CITY_BINDING_READY
    assert transition.granularity_candidate is LocationGranularity.CITY
    assert transition.technical_binding_confirmed_for_location is True
    assert transition.requires_store_binding_discovery is False
    assert transition.source_location_key_candidate == CITY_KEY
    assert transition.extraction_enabled is False
    assert transition.production_authority is False
    assert transition.catalog_accepted is False

    current = unconfirmed_sps()
    proposed = propose_city_location_binding(current, transition)
    assert proposed.granularity is LocationGranularity.CITY
    assert proposed.technical_binding_confirmed is True
    assert proposed.source_location_key == CITY_KEY
    assert proposed.evidence == transition.evidence_ref
    assert proposed.extraction_enabled is False
    assert current.granularity is LocationGranularity.UNKNOWN
    assert current.technical_binding_confirmed is False


def test_city_can_be_ready_even_when_store_ui_exists_but_context_stays_city_level() -> None:
    payload = artifact(
        store_selection_observed=True,
        selected_store="Mega Mall",
    )
    transition = evaluate_location_binding_artifact(payload)
    assert transition.status is LocationBindingTransitionStatus.CITY_BINDING_READY
    assert transition.selected_store == "Mega Mall"
    assert transition.requires_store_binding_discovery is False


def test_store_granularity_never_promotes_city_location() -> None:
    payload = artifact(
        granularity="store",
        source_key=STORE_KEY,
        store_selection_observed=True,
        selected_store="Mega Mall",
    )
    transition = evaluate_location_binding_artifact(payload)

    assert transition.status is LocationBindingTransitionStatus.STORE_BINDING_DISCOVERY_REQUIRED
    assert transition.granularity_candidate is LocationGranularity.STORE
    assert transition.technical_binding_confirmed_for_location is False
    assert transition.requires_store_binding_discovery is True
    with pytest.raises(LocationBindingTransitionError, match="city_binding_transition_required"):
        propose_city_location_binding(unconfirmed_sps(), transition)


def test_unknown_weak_evidence_stays_inconclusive() -> None:
    payload = artifact(
        granularity="unknown",
        confidence="weak",
        technical=False,
        source_key=None,
        store_selection_observed=True,
        selected_store="Mega Mall",
    )
    transition = evaluate_location_binding_artifact(payload)
    assert transition.status is LocationBindingTransitionStatus.INCONCLUSIVE
    assert transition.technical_binding_confirmed_for_location is False
    assert transition.requires_store_binding_discovery is False


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("mode", "synthetic_local", "live_evidence_required"),
        ("stop_reason", "playwright_timeout", "successful_capture_required"),
        ("target_host", "example.com", "unexpected_target_host"),
        ("target_city", "Tegucigalpa", "unexpected_target_city"),
        ("production_authority", True, "production_authority_must_be_false"),
        ("catalog_accepted", True, "catalog_accepted_must_be_false"),
        ("extraction_enabled", True, "extraction_enabled_must_be_false"),
    ],
)
def test_non_authoritative_live_contract_is_required(field: str, value, message: str) -> None:
    payload = artifact()
    payload[field] = value
    with pytest.raises(LocationBindingTransitionError, match=message):
        evaluate_location_binding_artifact(payload)


def test_raw_values_exposed_flag_is_rejected() -> None:
    payload = artifact()
    payload["binding_report"]["raw_values_exposed"] = True
    with pytest.raises(LocationBindingTransitionError, match="sanitized_binding_report_required"):
        evaluate_location_binding_artifact(payload)


def test_malformed_or_cleartext_source_location_key_is_rejected() -> None:
    payload = artifact(source_key="localStorage:regionid:San Pedro Sula")
    with pytest.raises(LocationBindingTransitionError, match="source_location_key_candidate_invalid"):
        evaluate_location_binding_artifact(payload)


def test_store_candidate_requires_selected_store_evidence() -> None:
    payload = artifact(
        granularity="store",
        source_key=STORE_KEY,
        store_selection_observed=False,
        selected_store=None,
    )
    with pytest.raises(LocationBindingTransitionError, match="selected_store_evidence_required"):
        evaluate_location_binding_artifact(payload)


def test_unknown_candidate_cannot_smuggle_strong_binding() -> None:
    payload = artifact(
        granularity="unknown",
        confidence="strong",
        technical=True,
        source_key=CITY_KEY,
    )
    with pytest.raises(
        LocationBindingTransitionError,
        match="unknown_binding_must_remain_non_authoritative",
    ):
        evaluate_location_binding_artifact(payload)


def test_artifact_fingerprint_is_deterministic_and_changes_with_evidence() -> None:
    first_payload = artifact()
    second_payload = copy.deepcopy(first_payload)
    first = evaluate_location_binding_artifact(first_payload)
    second = evaluate_location_binding_artifact(second_payload)
    assert first.artifact_sha256 == second.artifact_sha256
    assert first.evidence_ref == second.evidence_ref

    second_payload["available_cities"].append("La Ceiba")
    changed = evaluate_location_binding_artifact(second_payload)
    assert changed.artifact_sha256 != first.artifact_sha256


def test_city_proposal_rejects_wrong_location_identity() -> None:
    transition = evaluate_location_binding_artifact(artifact())
    wrong = copy.copy(unconfirmed_sps())
    object.__setattr__(wrong, "location_id", "other_sps")
    with pytest.raises(LocationBindingTransitionError, match="location_id_mismatch"):
        propose_city_location_binding(wrong, transition)
