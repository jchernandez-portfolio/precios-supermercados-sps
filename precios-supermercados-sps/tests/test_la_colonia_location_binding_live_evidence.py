from __future__ import annotations

import json
from pathlib import Path

from precios_supermercados.location_binding_transition import (
    LocationBindingTransitionStatus,
    evaluate_location_binding_artifact,
)
from precios_supermercados.locations import LA_COLONIA_SPS, LocationGranularity

REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = (
    REPO_ROOT
    / "precios-supermercados-sps/reports/discovery/la-colonia-location-binding-2026-08-24.json"
)
EXPECTED_ARTIFACT_SHA256 = "80f2e4d333043a38954603c9c72086d241ac9b5a1cc1f10b71a9fde772588d95"
EXPECTED_SOURCE_KEY = (
    "request:regionid:sha256:"
    "d7732eccc99c8530a6d29cce4244920e65e85c1d5492facb05469dc3589cb8b7"
)


def test_preserved_live_artifact_closes_city_binding_without_granting_authority() -> None:
    payload = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    transition = evaluate_location_binding_artifact(payload)

    assert transition.status is LocationBindingTransitionStatus.CITY_BINDING_READY
    assert transition.granularity_candidate is LocationGranularity.CITY
    assert transition.confidence == "strong"
    assert transition.technical_binding_confirmed_for_location is True
    assert transition.source_location_key_candidate == EXPECTED_SOURCE_KEY
    assert transition.artifact_sha256 == EXPECTED_ARTIFACT_SHA256
    assert transition.evidence_ref == f"location_binding_radiography:sha256:{EXPECTED_ARTIFACT_SHA256}"
    assert transition.extraction_enabled is False
    assert transition.production_authority is False
    assert transition.catalog_accepted is False


def test_canonical_sps_location_matches_preserved_live_evidence() -> None:
    payload = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    transition = evaluate_location_binding_artifact(payload)

    assert LA_COLONIA_SPS.granularity is LocationGranularity.CITY
    assert LA_COLONIA_SPS.technical_binding_confirmed is True
    assert LA_COLONIA_SPS.source_location_key == transition.source_location_key_candidate
    assert LA_COLONIA_SPS.evidence == transition.evidence_ref
    assert LA_COLONIA_SPS.extraction_enabled is False
