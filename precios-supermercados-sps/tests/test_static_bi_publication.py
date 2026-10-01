from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "precios-supermercados-sps-portfolio-data-sync.yml"
BI_URL = (
    "https://raw.githubusercontent.com/jchernandez-portfolio/precios-supermercados-sps/portfolio-data/"
    "precios-supermercados-sps/published/bi/la-colonia-walmart-sps/dataset.json"
)


def test_rpi_consumer_publication_reuses_safe_artifact_without_turso_reads_and_is_atomic() -> None:
    raw = WORKFLOW.read_text(encoding="utf-8")

    assert "safe-analytics-la-colonia-walmart-sps-${{ github.event.workflow_run.id }}" in raw
    assert "rpi-consumer-mart/v2" in raw
    assert "rpi-business-mart/v1" in raw
    assert "rpi-marts-manifest/v1" in raw
    assert "precios-sps-safe-portfolio-sample/v1" in raw
    assert "fail_closed_strong_identity_and_commercial_consistency" in raw
    assert "portfolio_sync_offer_count_mismatch" in raw
    assert "portfolio_sync_product_count_mismatch" in raw
    assert "portfolio_sync_hash_mismatch" in raw
    assert "portfolio_sync_secret_material_detected" in raw
    assert "portfolio_sync_consumer_analysis_invalid" in raw
    assert "rpi-consumer-analysis/v1" in raw
    assert "source_workflow_run_id" in raw
    assert "source_head_sha" in raw
    assert 'published="$repository/precios-supermercados-sps/published/rpi"' in raw
    assert '"$published/consumer-mart.json"' in raw
    assert '"$published/manifest.json"' in raw
    assert "precios-supermercados-sps/portfolio/sample-data.json" in raw
    assert "precios-supermercados-sps/published/rpi/business-mart.json" not in raw
    assert "ref: portfolio-data" in raw
    assert 'git -C "$repository" push origin HEAD:portfolio-data' in raw
    assert "createBlob" not in raw
    assert "createOrUpdateFileContents" not in raw

    assert "TURSO_DATABASE_URL: ${{ secrets." not in raw
    assert "TURSO_AUTH_TOKEN: ${{ secrets." not in raw
    assert "scripts/exportar_modelo_analitico.py" not in raw
    assert "scripts/generar_descriptores_publicacion_segura.py" not in raw
    assert "scripts/exportar_rpi_marts.py" not in raw
    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1" in raw


def test_power_bi_legacy_refresh_path_remains_available_while_rpi_business_mart_uses_artifacts() -> None:
    readme = (PROJECT_ROOT / "powerbi" / "README.md").read_text(encoding="utf-8")
    guide = (PROJECT_ROOT / "docs" / "BI-IMPLEMENTATION-GUIDE.md").read_text(encoding="utf-8")
    rpi_readme = (PROJECT_ROOT / "powerbi" / "rpi" / "README.md").read_text(encoding="utf-8")

    for document in (readme, guide):
        assert BI_URL in document
        assert "precios-sps-static-bi-dataset/v1" in document
        assert "no" in document.casefold() and "turso" in document.casefold()

    assert "publication.offers" in guide
    assert "publication.products" in guide
    assert "publication.common_basket" in guide
    assert "source_descriptors" in guide
    assert "rpi-business-mart/v1" in rpi_readme
    assert "artifact RPI" in rpi_readme
