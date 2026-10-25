from __future__ import annotations

import json
from datetime import UTC, datetime, timezone
from decimal import Decimal
from pathlib import Path

from precios_supermercados.enums import (
    AvailabilityStatus,
    LocationStatus,
    ReviewStatus,
    SourceKeyType,
)
from precios_supermercados.identifiers import (
    generate_offer_id,
    generate_pending_product_id,
    generate_source_product_id,
    generate_state_hash,
)
from precios_supermercados.offer_normalization import (
    normalize_and_validate_raw_products,
    normalize_raw_product,
    validate_normalized_offer,
)
from precios_supermercados.scrapers.la_colonia import LaColoniaExtractor

FIXTURES = Path(__file__).parent / "fixtures" / "la_colonia"
OBSERVED_AT = datetime(2026, 8, 22, 20, 0, tzinfo=UTC)
VALIDATED_AT = datetime(2026, 8, 22, 20, 1, tzinfo=UTC)


def _raw_products():
    payload = json.loads((FIXTURES / "product_search_page.json").read_text(encoding="utf-8"))
    extractor = LaColoniaExtractor(clock=lambda: OBSERVED_AT)
    result = extractor.parse_payload(
        payload,
        scrape_run_id="run_raw_normalized_validated",
        source_url=extractor.build_page_url(page_size=5),
        page_size=5,
    )
    assert result.accepted is True
    return result.products


def _validated():
    return normalize_and_validate_raw_products(
        _raw_products(),
        currency="HNL",
        validated_at_utc=VALIDATED_AT,
    )


def test_la_colonia_raw_reaches_normalized_and_validated_contracts() -> None:
    raw_products = _raw_products()
    validated = normalize_and_validate_raw_products(
        raw_products,
        currency="HNL",
        validated_at_utc=VALIDATED_AT,
    )

    assert len(validated) == len(raw_products) == 5
    first_raw = raw_products[0]
    first = validated[0]
    assert first_raw.source_key_type is SourceKeyType.INTERNAL_ID
    assert first_raw.source_key == "2001"
    assert first.offer.source_product_id == generate_source_product_id(
        first_raw.supermarket_id,
        first_raw.source_key_type,
        first_raw.source_key,
    )
    assert first.offer.offer_id == generate_offer_id(
        first_raw.supermarket_id,
        first_raw.location_id,
        first.offer.source_product_id,
    )
    assert first.state_hash == generate_state_hash(first.offer)
    assert first.offer.product_id == generate_pending_product_id(first.offer.source_product_id)
    assert first.offer.product_id.startswith("prod_pending_")
    assert "pending_product_mapping" in first.quality_events


def test_identity_and_state_hash_are_deterministic_for_same_raw_observation() -> None:
    raw = _raw_products()[0]
    first = normalize_raw_product(raw, currency="HNL")
    second = normalize_raw_product(raw, currency="HNL")

    assert first.source_product_id == second.source_product_id
    assert first.product_id == second.product_id
    assert first.offer_id == second.offer_id
    assert generate_state_hash(first) == generate_state_hash(second)


def test_packaged_presentation_is_normalized_without_unit_conversion() -> None:
    first = _validated()[0].offer

    assert first.source_presentation == "473 Ml"
    assert first.unit_count == 1
    assert first.content_per_unit == Decimal("473")
    assert first.measurement_unit == "ml"
    assert first.total_content == Decimal("473")
    assert first.review_status is ReviewStatus.READY
    assert first.pending_fields == ()


def test_weighted_product_preserves_source_weight_basis() -> None:
    weighted = _validated()[1].offer

    assert weighted.source_name == "Fresa X Lb"
    assert weighted.unit_count == 1
    assert weighted.content_per_unit == Decimal("1")
    assert weighted.measurement_unit == "lb"
    assert weighted.total_content == Decimal("1")
    assert weighted.current_price == Decimal("94.9")


def test_price_and_promotion_keep_source_semantics() -> None:
    promotion = _validated()[2].offer

    assert promotion.current_price == Decimal("59.9")
    assert promotion.reported_regular_price == Decimal("99.9")
    assert promotion.is_promotion is True
    assert promotion.currency == "HNL"


def test_out_of_stock_and_unknown_can_remain_without_price() -> None:
    validated = _validated()
    exhausted = validated[3].offer
    unknown = validated[4].offer

    assert exhausted.availability is AvailabilityStatus.OUT_OF_STOCK
    assert exhausted.current_price is None
    assert unknown.availability is AvailabilityStatus.UNKNOWN
    assert unknown.current_price is None


def test_missing_source_attributes_are_not_invented_and_emit_review_events() -> None:
    missing = _validated()[4]

    assert missing.offer.normalized_brand is None
    assert missing.offer.category is None
    assert missing.offer.subcategory is None
    assert missing.offer.unit_count is None
    assert missing.offer.content_per_unit is None
    assert missing.offer.measurement_unit is None
    assert missing.offer.total_content is None
    assert missing.offer.review_status is ReviewStatus.NEEDS_REVIEW
    assert missing.review_status is ReviewStatus.NEEDS_REVIEW
    assert "pending_normalization:normalized_brand" in missing.quality_events
    assert "pending_normalization:category" in missing.quality_events
    assert "pending_normalization:total_content" in missing.quality_events


def test_raw_source_location_is_preserved_and_not_relabelled_as_sps() -> None:
    first = _validated()[0]

    assert first.offer.location_id == "la_colonia_online"
    assert first.offer.location_status is LocationStatus.UNKNOWN
    assert first.offer.location_evidence == (
        "Catálogo público en línea sin selección obligatoria de ciudad o sucursal."
    )
    assert first.offer.location_confidence is None
    assert "pending_location_binding" in first.quality_events
    assert "sps" not in first.offer.location_id.casefold()


def test_explicit_product_mapping_can_replace_pending_singleton_without_changing_source_identity() -> None:
    raw = _raw_products()[0]
    pending = normalize_raw_product(raw, currency="HNL")
    mapped = normalize_raw_product(
        raw,
        currency="HNL",
        product_id_resolver=lambda _raw, _source_product_id: "prod_reviewed_malteada_473_ml",
    )
    validated = validate_normalized_offer(
        mapped,
        validated_at_utc=VALIDATED_AT,
    )

    assert mapped.source_product_id == pending.source_product_id
    assert mapped.offer_id == pending.offer_id
    assert mapped.product_id == "prod_reviewed_malteada_473_ml"
    assert "pending_product_mapping" not in validated.quality_events
