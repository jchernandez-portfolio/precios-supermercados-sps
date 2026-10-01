from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
POWERBI_ROOT = PROJECT_ROOT / "powerbi"
QUERY_ROOT = POWERBI_ROOT / "queries"
STATIC_URL = (
    "https://raw.githubusercontent.com/jchernandez-portfolio/precios-supermercados-sps/portfolio-data/"
    "precios-supermercados-sps/published/bi/la-colonia-walmart-sps/dataset.json"
)


def test_powerbi_static_dataset_is_the_only_web_query() -> None:
    expected = {
        "StaticDataset.pq",
        "Offers.pq",
        "Products.pq",
        "CommonBasket.pq",
        "Scope.pq",
        "SourceDescriptors.pq",
        "RefreshMetadata.pq",
    }
    actual = {path.name for path in QUERY_ROOT.glob("*.pq")}
    assert actual == expected

    static = (QUERY_ROOT / "StaticDataset.pq").read_text(encoding="utf-8")
    assert STATIC_URL in static
    assert "Json.Document(Web.Contents(SourceUrl" in static
    assert "precios-sps-static-bi-dataset/v1" in static
    assert "fail_closed_strong_identity_and_commercial_consistency" in static
    assert "schema_invalid" in static
    assert "policy_invalid" in static

    for path in sorted(QUERY_ROOT.glob("*.pq")):
        raw = path.read_text(encoding="utf-8")
        lowered = raw.casefold()
        assert "libsql://" not in lowered
        assert "turso_auth_token" not in lowered
        assert "turso_database_url" not in lowered
        if path.name != "StaticDataset.pq":
            assert "Web.Contents(" not in raw
            assert "Source = StaticDataset" in raw


def test_powerbi_row_queries_keep_schema_when_safe_universe_is_empty() -> None:
    for filename in (
        "Offers.pq",
        "Products.pq",
        "CommonBasket.pq",
        "Scope.pq",
        "SourceDescriptors.pq",
    ):
        raw = (QUERY_ROOT / filename).read_text(encoding="utf-8")
        assert "Table.FromRecords(" in raw
        assert "MissingField.UseNull" in raw


def test_powerbi_model_uses_stable_ids_and_explicit_scope_keys() -> None:
    offers = (QUERY_ROOT / "Offers.pq").read_text(encoding="utf-8")
    products = (QUERY_ROOT / "Products.pq").read_text(encoding="utf-8")
    basket = (QUERY_ROOT / "CommonBasket.pq").read_text(encoding="utf-8")
    scope = (QUERY_ROOT / "Scope.pq").read_text(encoding="utf-8")
    descriptors = (QUERY_ROOT / "SourceDescriptors.pq").read_text(encoding="utf-8")

    assert "canonical_product_id" in offers
    assert "source_record_id" in offers
    assert "scope_key" in offers
    assert "canonical_product_id" in products
    assert "canonical_gtin" in products
    assert "scope_key" in basket
    assert "scope_key" in scope
    assert "source_record_id" in descriptors
    assert "source_name" in descriptors
    assert "source_presentation" in descriptors


def test_powerbi_measures_fail_closed_for_empty_common_basket() -> None:
    dax = (POWERBI_ROOT / "measures.dax").read_text(encoding="utf-8")

    assert "Productos comparables" in dax
    assert "Productos canasta común" in dax
    assert "ProductCount = 0" in dax
    assert "BLANK()" in dax
    assert "Canasta más barata" in dax
    assert "Diferencia entre canastas" in dax
    assert "Sin productos comparables para este alcance" in dax


def test_powerbi_readme_documents_single_static_refresh_source() -> None:
    readme = (POWERBI_ROOT / "README.md").read_text(encoding="utf-8")

    assert STATIC_URL in readme
    assert "Sólo `StaticDataset` debe acceder a Web" in readme
    assert "no ejecuta una consulta adicional a Turso" in readme
    assert "Products[canonical_product_id]" in readme
    assert "SourceDescriptors[source_record_id]" in readme
    assert "Scope[scope_key]" in readme
