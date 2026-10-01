from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from precios_supermercados.product_homologation import SourceProductRecord, homologate_products
from precios_supermercados.product_identity_v2 import homologate_products_v2
from scripts.exportar_revision_homologacion import SCHEMA, build_review_queue


def product(record_id: str, supermarket: str, name: str, **kwargs: object) -> SourceProductRecord:
    return SourceProductRecord(
        source_record_id=record_id,
        supermarket_id=supermarket,
        source_name=name,
        source_brand=kwargs.get("brand"),
        source_presentation=kwargs.get("presentation"),
        barcode=kwargs.get("barcode"),
    )


def test_review_queue_exposes_fuzzy_candidates_and_taxonomy_gaps_without_approving_them() -> None:
    result = homologate_products(
        (
            product("la_colonia:1", "la_colonia", "Harina de Maíz Maseca Original 4.5 lb", brand="Maseca", presentation="4.5 lb"),
            product("pricesmart:2", "pricesmart", "Maseca Harina Maiz Original 2.04 kg", brand="MASECA", presentation="2.04 kg"),
            product("colonial:3", "colonial", "Producto Especial XYZ", brand="Marca X"),
        ),
        candidate_threshold=Decimal("0.65"),
    )
    queue = build_review_queue(result, generated_at_utc="2026-09-10T21:00:00Z")
    assert queue["schema"] == SCHEMA
    assert queue["private_review_artifact"] is True
    assert queue["public_serving_allowed"] is False
    assert queue["summary"]["fuzzy_review_candidates_total"] == 1
    assert queue["summary"]["taxonomy_gaps_total"] == 1
    candidate = queue["fuzzy_candidates"]["rows"][0]
    assert candidate["left"]["source_record_id"] == "la_colonia:1"
    assert candidate["right"]["source_record_id"] == "pricesmart:2"
    assert candidate["recommended_action"] == "human_review"
    assert candidate["allowed_decisions"] == ["same_product", "different_products", "pending"]
    assert candidate["left"]["canonical_gtin"] is None
    assert queue["taxonomy_gaps"]["rows"][0]["source_record_id"] == "colonial:3"


def test_review_queue_includes_same_gtin_conflicts_with_source_evidence() -> None:
    result = homologate_products(
        (
            product("a", "la_colonia", "Maiz El Migo Dulce En Grano 240 g", brand="El Migo", presentation="240 g", barcode="012656001065"),
            product("b", "walmart", "Maíz Dulce En Granos El Migo - 148 g", brand="El Migo", presentation="148 g", barcode="012656001065"),
        )
    )
    queue = build_review_queue(result, generated_at_utc="2026-09-10T21:00:00Z")
    assert queue["exact_gtin_conflicts"]["total"] == 1
    conflict = queue["exact_gtin_conflicts"]["rows"][0]
    assert conflict["canonical_gtin"] == "00012656001065"
    assert "cross_source_presentation_conflict" in conflict["conflict_reasons"]
    assert len(conflict["products"]) == 2
    assert conflict["recommended_action"] == "verify_same_gtin_commercial_consistency"


def test_review_queue_limits_large_human_review_sections_deterministically() -> None:
    records = tuple(
        product(f"s:{index}", "colonial", f"Objeto raro {index}", brand="Marca X")
        for index in range(5)
    )
    result = homologate_products(records)
    queue = build_review_queue(
        result,
        generated_at_utc="2026-09-10T21:00:00Z",
        candidate_limit=0,
        taxonomy_gap_limit=2,
    )
    assert queue["fuzzy_candidates"]["included"] == 0
    assert queue["taxonomy_gaps"]["total"] == 5
    assert queue["taxonomy_gaps"]["included"] == 2
    assert queue["taxonomy_gaps"]["truncated"] is True


def test_review_v2_exposes_raw_canonical_fields_and_before_after_metrics() -> None:
    records = (
        product(
            "colonial:1",
            "colonial",
            "Nutri Yema Claras de Huevo 554gr",
            brand="Nutri Yema",
        ),
        product(
            "andes:2",
            "comisariato_los_andes",
            "Nutri Yema Claras de Huevo Líquidas 1.2 lb",
            brand="Nutri Yema",
            presentation="UN",
        ),
    )
    baseline = homologate_products(records)
    result = homologate_products_v2(records)
    queue = build_review_queue(
        result,
        baseline_result=baseline,
        generated_at_utc="2026-09-14T21:00:00Z",
    )

    assert queue["before_after"]["before_engine"] == "product-homologation-v1"
    assert queue["before_after"]["after_engine"] == "product-homologation-v2.4"
    assert "normalized_brand" in queue["before_after"]["before"]
    assert "normalized_brand" in queue["before_after"]["after"]
    candidate = queue["review_candidates"]["rows"][0]
    assert candidate["candidate_id"].startswith("candidate_")
    assert candidate["confidence_level"] == "STRONG"
    assert candidate["decision_state"] == "review_required"
    products = {
        item["source_record_id"]: item
        for item in (candidate["left"], candidate["right"])
    }
    assert products["colonial:1"]["raw_presentation"] is None
    assert products["colonial:1"]["normalized_quantity"] == "554"
    assert products["colonial:1"]["normalized_unit"] == "g"
    assert products["andes:2"]["source_presentation"] == "UN"
    assert products["andes:2"]["canonical_total"] == "544.310844"
    assert products["colonial:1"]["source_brand_role"] == "retailer_reported_brand"
    assert products["colonial:1"]["normalized_brand_role"] == "commercial_brand_candidate"
    assert products["colonial:1"]["manufacturer_brand"] is None
    assert products["colonial:1"]["owner_brand"] is None
    assert products["colonial:1"]["manufacturer_owner_status"] == "not_exposed_by_source"
    assert len(products["colonial:1"]["evidence_fingerprint"]) == 64
    assert candidate["decision_contract"]["policy_version"] == "precios-sps-product-identity-policy/v1"
    assert candidate["decision_contract"]["public_serving_allowed"] is False
    assert "VERIFIED_EQUIVALENT" in candidate["allowed_relations"]
    assert queue["summary"]["image_signal_status"] == "not_persisted_in_products_table"
