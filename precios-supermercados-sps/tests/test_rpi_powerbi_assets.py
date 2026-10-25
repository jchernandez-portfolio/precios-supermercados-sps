from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "powerbi" / "rpi"


def test_rpi_powerbi_queries_use_one_local_business_mart_source() -> None:
    queries = {path.name: path.read_text(encoding="utf-8") for path in (ROOT / "queries").glob("*.pq")}
    assert set(queries) == {
        "BusinessMart.pq", "BusinessMetadata.pq", "DimProduct.pq", "DimRetailer.pq", "DimLocation.pq",
        "DimCategory.pq", "DimBrand.pq", "FactCurrentComparison.pq", "FactPriceHistory.pq",
        "FactPromotionAnalysis.pq", "FactBasketCost.pq", "FactMetricCoverage.pq", "SourceFreshness.pq",
    }
    assert 'File.Contents(BusinessMartPath)' in queries["BusinessMart.pq"]
    assert 'rpi-business-mart/v1' in queries["BusinessMart.pq"]
    assert 'fail_closed_strong_identity_and_commercial_consistency' in queries["BusinessMart.pq"]
    assert "fact_price_history" in queries["FactPriceHistory.pq"]
    assert "fact_promotion_analysis" in queries["FactPromotionAnalysis.pq"]
    for name, raw in queries.items():
        if name != "BusinessMart.pq":
            assert "Source = BusinessMart" in raw
            assert "File.Contents" not in raw
            assert "Web.Contents" not in raw
        lowered = raw.casefold()
        assert "libsql://" not in lowered
        assert "turso_auth_token" not in lowered


def test_rpi_powerbi_measures_fail_closed_on_freshness_and_keep_history_as_aggregation_only() -> None:
    dax = (ROOT / "measures.dax").read_text(encoding="utf-8")
    assert 'Estado comparación' in dax
    assert '= "COMPARABLE"' in dax
    assert "PCI promedio" in dax
    assert "Cobertura %" in dax
    assert "ProductCount > 0" in dax
    assert "BLANK()" in dax
    assert "Promociones declaradas activas" in dax
    assert "Fuentes stale" in dax
    assert "Movimientos de precio observados" in dax
    assert "Promociones con reducción histórica" in dax
    assert "FactPriceHistory[change_pct]" in dax
    assert "FactPromotionAnalysis[historical_price_reduction]" in dax


def test_all_nine_b2b_pages_and_current_limitations_are_documented() -> None:
    pages = (ROOT / "page-spec.md").read_text(encoding="utf-8")
    for title in (
        "Executive Market Overview", "Competitive Pricing", "Category Intelligence",
        "Price Movements", "Promotion Intelligence", "Brand Intelligence",
        "Geographic Intelligence", "Assortment / Coverage", "Opportunities & Alerts",
    ):
        assert title in pages
    assert "ABSENT" in pages
    assert "no implica causalidad" in pages
    assert "estado vacío" in pages
    assert "FactPriceHistory" in pages
    assert "FactPromotionAnalysis" in pages
    assert "No interpola" in pages
