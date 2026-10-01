from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PORTFOLIO = ROOT / "portfolio"


def test_base_portfolio_contains_no_unsafe_cross_source_fallback_rows() -> None:
    base = (PORTFOLIO / "precios-portfolio.js").read_text(encoding="utf-8")

    assert "BASIC_COMPARISON" not in base
    assert "comparisonRows" not in base
    assert "Passion Jaguar" not in base
    assert "Passion Especial" not in base
    assert "misma marca y la misma presentación" not in base
    assert "same brand and presentation" not in base


def test_base_portfolio_comparison_table_starts_fail_closed() -> None:
    base = (PORTFOLIO / "precios-portfolio.js").read_text(encoding="utf-8")

    assert 'class="price-table-wrap"' in base
    assert 'hidden aria-hidden="true"' in base
    assert '<tbody></tbody>' in base
    assert 'data-comparison-safety="fail-closed"' in base
    assert "identidad fuerte" in base
    assert "strong identity" in base


def test_verified_adapter_is_the_only_path_that_reopens_public_sample() -> None:
    adapter = (PORTFOLIO / "precios-portfolio-current-state.js").read_text(encoding="utf-8")

    assert "precios-sps-safe-portfolio-sample/v1" in adapter
    assert "fail_closed_strong_identity_and_commercial_consistency" in adapter
    assert (
        "https://raw.githubusercontent.com/jchernandez-portfolio/precios-supermercados-sps/portfolio-data/"
        "precios-supermercados-sps/portfolio/sample-data.json"
    ) in adapter
    assert "validatePublishedSample" in adapter
    assert "if (!verifiedSample) return;" in adapter
    assert "tableWrap.hidden = false;" in adapter
    assert "verified-strong-identity" in adapter


def test_base_portfolio_fallback_uses_current_verified_scale() -> None:
    base = (PORTFOLIO / "precios-portfolio.js").read_text(encoding="utf-8")

    for value in ("<strong>6</strong>", "<strong>11</strong>", "<strong>58K+</strong>", "<strong>127K+</strong>"):
        assert value in base
    assert "5 fuentes web integradas" not in base
    assert "5 integrated web sources" not in base
