from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "generar_muestra_portafolio_segura.py"
SPEC = importlib.util.spec_from_file_location("generar_muestra_portafolio_segura", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

POLICY = "fail_closed_strong_identity_and_commercial_consistency"


def publication():
    return {
        "schema": "precios-sps-publication/v1",
        "comparison_policy": POLICY,
        "currency": "HNL",
        "scope": [
            {"supermarket_id": "colonial", "location_id": "colonial_sps"},
            {"supermarket_id": "walmart", "location_id": "walmart_sps"},
        ],
        "offers": [
            {"canonical_product_id": "prod_a", "canonical_gtin": "7590002040003", "supermarket_id": "colonial", "location_id": "colonial_sps", "source_record_id": "colonial:1", "current_price": "100.00", "is_best_price": True},
            {"canonical_product_id": "prod_a", "canonical_gtin": "7590002040003", "supermarket_id": "walmart", "location_id": "walmart_sps", "source_record_id": "walmart:2", "current_price": "120.00", "is_best_price": False},
            {"canonical_product_id": "prod_b", "canonical_gtin": "4006381333931", "supermarket_id": "colonial", "location_id": "colonial_sps", "source_record_id": "colonial:3", "current_price": "50.00", "is_best_price": True},
            {"canonical_product_id": "prod_b", "canonical_gtin": "4006381333931", "supermarket_id": "walmart", "location_id": "walmart_sps", "source_record_id": "walmart:4", "current_price": "55.00", "is_best_price": False},
        ],
        "products": [
            {"canonical_product_id": "prod_a", "canonical_gtin": "7590002040003", "best_supermarket_id": "colonial", "best_location_id": "colonial_sps", "best_price": "100.00", "highest_price": "120.00", "savings_vs_highest": "20.00", "savings_vs_highest_pct": "16.67", "supermarket_count": 2},
            {"canonical_product_id": "prod_b", "canonical_gtin": "4006381333931", "best_supermarket_id": "colonial", "best_location_id": "colonial_sps", "best_price": "50.00", "highest_price": "55.00", "savings_vs_highest": "5.00", "savings_vs_highest_pct": "9.09", "supermarket_count": 2},
        ],
    }


def descriptors():
    return {
        "schema": "precios-sps-safe-source-descriptors/v1",
        "comparison_policy": POLICY,
        "source_backend": "turso",
        "row_count": 4,
        "canonical_product_count": 2,
        "rows": [
            {"canonical_product_id": "prod_a", "canonical_gtin": "7590002040003", "source_record_id": "colonial:1", "supermarket_id": "colonial", "source_name": "Suavizante Downy Pureza 800 ml", "source_brand": "Downy", "source_presentation": "800 ml", "source_category": "Limpieza"},
            {"canonical_product_id": "prod_a", "canonical_gtin": "7590002040003", "source_record_id": "walmart:2", "supermarket_id": "walmart", "source_name": "Downy Suavizante Pureza 800 ML", "source_brand": "Downy", "source_presentation": "800 ml", "source_category": "Limpieza"},
            {"canonical_product_id": "prod_b", "canonical_gtin": "4006381333931", "source_record_id": "colonial:3", "supermarket_id": "colonial", "source_name": "Producto B", "source_brand": "Marca B", "source_presentation": "1 und", "source_category": "Otro"},
            {"canonical_product_id": "prod_b", "canonical_gtin": "4006381333931", "source_record_id": "walmart:4", "supermarket_id": "walmart", "source_name": "Producto B Walmart", "source_brand": "Marca B", "source_presentation": "1 und", "source_category": "Otro"},
        ],
    }


def consumer_mart():
    return {
        "schema": "rpi-consumer-mart/v2",
        "comparison_policy": POLICY,
        "comparison_status": "COMPARABLE",
        "currency": "HNL",
        "as_of": "2026-09-08T12:00:00Z",
        "scope": [
            {"supermarket_id": "la_colonia", "location_id": "la_colonia_sps"},
            {"supermarket_id": "walmart", "location_id": "walmart_sps"},
        ],
        "product_count": 2,
        "products": [
            {
                "canonical_product_id": "prod_a",
                "canonical_gtin": "7590002040003",
                "recommended_source_product_ids": ["la_colonia:1"],
                "offers": [
                    {
                        "canonical_product_id": "prod_a", "canonical_gtin": "7590002040003",
                        "source_product_id": "la_colonia:1", "supermarket_id": "la_colonia",
                        "location_id": "la_colonia_sps", "product_name": "Suavizante Downy Pureza 800 ml",
                        "brand": "Downy", "presentation": "800 ml", "category": "Limpieza",
                        "current_price": "100.00", "is_best_price": True,
                        "difference_vs_best_abs": "0.00", "difference_vs_best_pct": "0.00",
                    },
                    {
                        "canonical_product_id": "prod_a", "canonical_gtin": "7590002040003",
                        "source_product_id": "walmart:2", "supermarket_id": "walmart",
                        "location_id": "walmart_sps", "product_name": "Downy Suavizante Pureza 800 ML",
                        "brand": "Downy", "presentation": "800 ml", "category": "Limpieza",
                        "current_price": "120.00", "is_best_price": False,
                        "difference_vs_best_abs": "20.00", "difference_vs_best_pct": "20.00",
                    },
                ],
            },
            {
                "canonical_product_id": "prod_b",
                "canonical_gtin": "4006381333931",
                "recommended_source_product_ids": ["la_colonia:3", "walmart:4"],
                "offers": [
                    {
                        "canonical_product_id": "prod_b", "canonical_gtin": "4006381333931",
                        "source_product_id": "la_colonia:3", "supermarket_id": "la_colonia",
                        "location_id": "la_colonia_sps", "product_name": "Producto B",
                        "brand": "Marca B", "presentation": "1 und", "category": "Otro",
                        "current_price": "50.00", "is_best_price": True,
                        "difference_vs_best_abs": "0.00", "difference_vs_best_pct": "0.00",
                    },
                    {
                        "canonical_product_id": "prod_b", "canonical_gtin": "4006381333931",
                        "source_product_id": "walmart:4", "supermarket_id": "walmart",
                        "location_id": "walmart_sps", "product_name": "Producto B Walmart",
                        "brand": "Marca B", "presentation": "1 und", "category": "Otro",
                        "current_price": "50.00", "is_best_price": True,
                        "difference_vs_best_abs": "0.00", "difference_vs_best_pct": "0.00",
                    },
                ],
            },
        ],
    }


def test_sample_keeps_source_names_and_ranks_only_safe_products() -> None:
    result = module.build_sample(publication(), descriptors(), limit=1)
    assert result["schema"] == "precios-sps-safe-portfolio-sample/v1"
    assert result["comparison_policy"] == POLICY
    assert result["row_count"] == 1
    row = result["rows"][0]
    assert row["canonical_product_id"] == "prod_a"
    assert row["canonical_gtin"] == "7590002040003"
    assert [offer["source_name"] for offer in row["offers"]] == [
        "Suavizante Downy Pureza 800 ml",
        "Downy Suavizante Pureza 800 ML",
    ]
    assert [offer["source_category"] for offer in row["offers"]] == ["Limpieza", "Limpieza"]
    assert row["savings_vs_highest"] == "20.00"


def test_sample_from_consumer_reuses_safe_descriptors_and_python_best_set() -> None:
    result = module.build_sample_from_consumer(consumer_mart(), limit=10)
    assert result["schema"] == "precios-sps-safe-portfolio-sample/v1"
    assert result["source_schema"] == "rpi-consumer-mart/v2"
    assert result["source_as_of"] == "2026-09-08T12:00:00Z"
    assert result["row_count"] == 2
    assert result["rows"][0]["canonical_product_id"] == "prod_a"
    assert result["rows"][0]["best_price"] == "100.00"
    assert result["rows"][0]["highest_price"] == "120.00"
    assert result["rows"][0]["savings_vs_highest"] == "20.00"
    assert result["rows"][0]["savings_vs_highest_pct"] == "16.67"
    assert [offer["source_name"] for offer in result["rows"][0]["offers"]] == [
        "Suavizante Downy Pureza 800 ml",
        "Downy Suavizante Pureza 800 ML",
    ]
    tied = next(row for row in result["rows"] if row["canonical_product_id"] == "prod_b")
    assert tied["savings_vs_highest"] == "0.00"
    assert all(offer["is_best_price"] for offer in tied["offers"])


def test_sample_from_consumer_fails_closed_when_comparison_is_blocked() -> None:
    broken = consumer_mart()
    broken["comparison_status"] = "INSUFFICIENT_FRESH_COMPARISON"
    broken["products"][0]["recommended_source_product_ids"] = []
    with pytest.raises(module.SampleError, match="consumer_comparison_not_available"):
        module.build_sample_from_consumer(broken, limit=10)


def test_sample_from_consumer_rejects_recommendation_drift() -> None:
    broken = consumer_mart()
    broken["products"][0]["recommended_source_product_ids"] = ["walmart:2"]
    with pytest.raises(module.SampleError, match="consumer_recommendation_best_set_mismatch"):
        module.build_sample_from_consumer(broken, limit=10)


def test_sample_from_consumer_rejects_best_delta_drift() -> None:
    broken = consumer_mart()
    broken["products"][0]["offers"][0]["difference_vs_best_abs"] = "1.00"
    with pytest.raises(module.SampleError, match="consumer_best_delta_invalid"):
        module.build_sample_from_consumer(broken, limit=10)


def test_sample_fails_if_any_safe_offer_lacks_matching_descriptor() -> None:
    broken = descriptors()
    broken["rows"] = broken["rows"][:-1]
    with pytest.raises(module.SampleError, match="descriptor_missing_for_safe_offer"):
        module.build_sample(publication(), broken, limit=10)


def test_sample_fails_if_descriptor_identity_disagrees() -> None:
    broken = descriptors()
    broken["rows"][0]["canonical_gtin"] = "4006381333931"
    with pytest.raises(module.SampleError, match="descriptor_publication_identity_mismatch"):
        module.build_sample(publication(), broken, limit=10)


def test_sample_rejects_descriptors_not_belonging_to_safe_offers() -> None:
    broken = descriptors()
    broken["rows"].append(
        {"canonical_product_id": "prod_x", "canonical_gtin": "7501031311309", "source_record_id": "colonial:99", "supermarket_id": "colonial", "source_name": "Café Passion Especial 1 lb", "source_brand": "Passion", "source_presentation": "1 lb", "source_category": "Café"}
    )
    with pytest.raises(module.SampleError, match="descriptor_set_not_exactly_safe_offers"):
        module.build_sample(publication(), broken, limit=10)


def test_sample_rejects_product_gtin_that_disagrees_with_its_safe_offers() -> None:
    broken = publication()
    broken["products"][0]["canonical_gtin"] = "4006381333931"
    with pytest.raises(module.SampleError, match="publication_product_gtin_conflict"):
        module.build_sample(broken, descriptors(), limit=10)


def test_sample_rejects_nonpositive_current_price() -> None:
    broken = publication()
    broken["offers"][0]["current_price"] = "0.00"
    with pytest.raises(module.SampleError, match="publication_current_price_invalid"):
        module.build_sample(broken, descriptors(), limit=10)


def test_sample_rejects_unbounded_limit() -> None:
    with pytest.raises(module.SampleError, match="sample_limit_invalid"):
        module.build_sample(publication(), descriptors(), limit=51)
