from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_public_cross_retailer_sample_stays_fail_closed_until_strong_identity_output_exists() -> None:
    sample = json.loads((PROJECT_ROOT / "portfolio" / "sample-data.json").read_text(encoding="utf-8"))

    assert sample["schema_version"] == 3
    assert sample["comparison_policy"] == "fail_closed_strong_identity_and_commercial_consistency"
    assert sample["publication_status"] == "withheld_until_strong_identity_verified"
    assert sample["rows"] == []
    assert sample["safety_regression"]["example"] == "Passion Jaguar vs Passion Especial"
    assert sample["safety_regression"]["automatic_best_price_allowed"] is False
    assert sample["replacement_contract"] == "precios-sps-publication/v1"
    assert "same_brand_and_same_presentation" not in json.dumps(sample, ensure_ascii=False)


def test_portfolio_overlay_hides_legacy_table_and_names_fail_closed_gate() -> None:
    source = (PROJECT_ROOT / "portfolio" / "precios-portfolio-current-state.js").read_text(encoding="utf-8")

    assert "tableWrap.hidden = true" in source
    assert "data-price-ranking-legend" in source
    assert "comparisonSafety = 'fail-closed'" in source
    assert "Passion Jaguar" in source
    assert "Passion Especial" in source
    assert "marca + presentación" in source
    assert "brand + presentation" in source


def test_portfolio_can_activate_only_the_verified_fail_closed_sample_contract() -> None:
    source = (PROJECT_ROOT / "portfolio" / "precios-portfolio-current-state.js").read_text(encoding="utf-8")

    assert "precios-sps-safe-portfolio-sample/v1" in source
    assert "fail_closed_strong_identity_and_commercial_consistency" in source
    assert "canonical_gtin" in source
    assert "^\\d{8,14}$" in source
    assert "la_colonia_sps" in source
    assert "walmart_sps" in source
    assert "verified-strong-identity" in source
    assert "fetch(new URL(SAFE_SAMPLE_URL, document.baseURI)" in source
    assert "tableWrap.hidden = false" in source
    assert "source_name" in source
    assert "source_presentation" in source
    lowered = source.lower()
    assert "function matchbyname" not in lowered
    assert "function match_by_name" not in lowered
    assert "matchingbyname(" not in lowered
    assert "match_by_name(" not in lowered
