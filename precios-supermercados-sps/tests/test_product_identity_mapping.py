from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timezone
from decimal import Decimal

import pytest

from precios_supermercados.enums import AvailabilityStatus, LocationStatus, SourceKeyType
from precios_supermercados.identifiers import (
    canonicalize_gtin,
    generate_gtin_product_id,
    generate_pending_product_id,
)
from precios_supermercados.locations import LA_COLONIA_ONLINE_SOURCE_CONTEXT
from precios_supermercados.models import RawProduct
from precios_supermercados.offer_normalization import (
    OfferNormalizationError,
    normalize_raw_product,
    parse_presentation,
    validate_normalized_offer,
)
from precios_supermercados.tabular_persistence import (
    DIM_PRODUCTS,
    MAP_SOURCE_PRODUCTS,
    TabularPersistenceError,
    product_dimension_row,
    source_product_mapping_row,
)

OBSERVED = datetime(2026, 8, 22, 20, 0, tzinfo=UTC)


def raw_product(
    *,
    barcode: str | None = None,
    presentation: str = "800 Ml",
    location_status: LocationStatus = LocationStatus.UNKNOWN,
    location_confidence=None,
) -> RawProduct:
    evidence = LA_COLONIA_ONLINE_SOURCE_CONTEXT.evidence
    return RawProduct(
        supermarket_id="la_colonia",
        location_id=LA_COLONIA_ONLINE_SOURCE_CONTEXT.location_id,
        source_key_type=SourceKeyType.INTERNAL_ID,
        source_key="2003",
        source_name="Suavizante Downy Pureza 800 Ml",
        product_url="https://www.lacolonia.com/suavizante-downy-pureza-800-ml/p",
        observed_at_utc=OBSERVED,
        scrape_run_id="run-product-identity",
        extractor_version="test",
        schema_version="1",
        source_url="https://www.lacolonia.com/_v/segment/graphql/v1?x=1",
        source_sku="7590002040003",
        source_brand="Downy",
        source_presentation=presentation,
        source_category="Cuidado del Hogar > Lavandería y Calzado",
        location_status=location_status,
        location_evidence=evidence,
        location_confidence=location_confidence,
        raw_values={
            "ean": barcode,
            "current_price": "59.9",
            "reported_regular_price": "99.9",
            "is_promotion": True,
            "availability": AvailabilityStatus.IN_STOCK.value,
            "subcategory": "Lavandería y Calzado",
            "measurement_unit": "un",
            "unit_multiplier": "1",
            "weighted_product": False,
        },
    )


def test_gtin_validation_normalizes_equivalent_lengths_to_gtin14() -> None:
    assert canonicalize_gtin("7590002040003") == "07590002040003"
    assert canonicalize_gtin("07590002040003") == "07590002040003"
    assert generate_gtin_product_id("7590002040003") == (
        "prod_gtin_07590002040003"
    )
    assert canonicalize_gtin("742100000001") is None
    assert canonicalize_gtin("not-a-barcode") is None


def test_valid_gtin_becomes_cross_supermarket_product_identity_by_default() -> None:
    offer = normalize_raw_product(
        raw_product(barcode="7590002040003"),
        currency="HNL",
    )

    assert offer.barcode == "7590002040003"
    assert offer.product_id == "prod_gtin_07590002040003"
    validated = validate_normalized_offer(offer, validated_at_utc=OBSERVED)
    assert "pending_product_mapping" not in validated.quality_events


def test_invalid_or_missing_gtin_stays_in_pending_mapping_queue() -> None:
    offer = normalize_raw_product(
        raw_product(barcode="742100000001"),
        currency="HNL",
    )
    validated = validate_normalized_offer(offer, validated_at_utc=OBSERVED)
    mapping = source_product_mapping_row(validated)

    assert offer.product_id == generate_pending_product_id(offer.source_product_id)
    assert "pending_product_mapping" in validated.quality_events
    assert mapping["mapping_status"] == "pending"
    assert mapping["mapping_method"] == "pending"
    assert mapping["review_reason"] == "pending_product_mapping"
    assert product_dimension_row(validated) is None


def test_forged_pending_product_id_is_rejected_before_persistence() -> None:
    offer = normalize_raw_product(
        raw_product(barcode="742100000001"),
        currency="HNL",
    )
    forged = replace(offer, product_id="prod_pending_" + "0" * 32)
    validated = validate_normalized_offer(forged, validated_at_utc=OBSERVED)

    with pytest.raises(
        TabularPersistenceError,
        match="product_id_pending_no_reconcilia_con_source_product_id",
    ):
        source_product_mapping_row(validated)

    with pytest.raises(
        TabularPersistenceError,
        match="product_id_pending_no_reconcilia_con_source_product_id",
    ):
        product_dimension_row(validated)


def test_explicit_reviewed_mapping_overrides_default_gtin_identity() -> None:
    offer = normalize_raw_product(
        raw_product(barcode="7590002040003"),
        currency="HNL",
        product_id_resolver=lambda _raw, _source_product_id: "prod_reviewed_downy_800ml",
    )
    validated = validate_normalized_offer(offer, validated_at_utc=OBSERVED)
    mapping = source_product_mapping_row(validated)
    dimension = product_dimension_row(validated)

    assert mapping["mapping_status"] == "mapped"
    assert mapping["mapping_method"] == "explicit"
    assert mapping["review_reason"] is None
    assert dimension is not None
    assert dimension["product_id"] == "prod_reviewed_downy_800ml"
    assert dimension["canonical_gtin"] == "07590002040003"


def test_dim_products_has_no_source_or_commercial_offer_noise() -> None:
    forbidden = {
        "supermarket_id",
        "source_product_id",
        "source_key",
        "source_sku",
        "source_name",
        "source_brand",
        "source_presentation",
        "source_url",
        "product_url",
        "current_price",
        "reported_regular_price",
        "is_promotion",
        "availability",
        "scrape_run_id",
        "location_id",
    }
    assert forbidden.isdisjoint(DIM_PRODUCTS.columns)
    assert MAP_SOURCE_PRODUCTS.primary_key == ("source_product_id",)
    assert "review_reason" in MAP_SOURCE_PRODUCTS.columns


def test_multipack_remains_two_units_instead_of_collapsing_to_single_pack() -> None:
    presentation = parse_presentation(
        raw_product(barcode=None, presentation="2 x 500 ml")
    )
    assert presentation.unit_count == 2
    assert presentation.content_per_unit == Decimal("500")
    assert presentation.measurement_unit == "ml"
    assert presentation.total_content == Decimal("1000")


def test_source_only_location_cannot_claim_confirmed_sps_binding() -> None:
    raw = raw_product(
        barcode=None,
        location_status=LocationStatus.CONFIRMED,
        location_confidence=Decimal("1"),
    )
    with pytest.raises(
        OfferNormalizationError,
        match="source_location_cannot_claim_commercial_binding",
    ):
        normalize_raw_product(raw, currency="HNL")


def test_source_only_location_preserves_unknown_context() -> None:
    offer = normalize_raw_product(raw_product(barcode=None), currency="HNL")
    assert offer.location_id == "la_colonia_online"
    assert offer.location_status is LocationStatus.UNKNOWN
    assert offer.location_evidence == LA_COLONIA_ONLINE_SOURCE_CONTEXT.evidence
    assert offer.location_confidence is None
