from datetime import UTC, datetime, timezone
from decimal import Decimal

import pytest

from precios_supermercados.enums import (
    AvailabilityStatus,
    LocationStatus,
    ReviewStatus,
    SourceKeyType,
)
from precios_supermercados.identifiers import (
    generate_offer_id,
    generate_source_product_id,
    generate_state_hash,
)
from precios_supermercados.models import NormalizedOffer, RawProduct, ValidatedOffer


def build_offer(**overrides):
    source_product_id = generate_source_product_id("super-demo", SourceKeyType.SKU, "SKU-001")
    offer_id = generate_offer_id("super-demo", "sps-principal", source_product_id)
    data = {
        "supermarket_id": "super-demo",
        "location_id": "sps-principal",
        "source_product_id": source_product_id,
        "source_key_type": SourceKeyType.SKU,
        "source_key": "SKU-001",
        "product_id": "prod_arroz_blanco_1000_g",
        "offer_id": offer_id,
        "source_name": "Arroz Blanco Marca Demo 1 kg",
        "product_url": "https://example.com/productos/arroz?utm_source=test",
        "normalized_name": "Arroz blanco",
        "normalized_brand": "Marca Demo",
        "category": "Abarrotes",
        "subcategory": "Arroz",
        "unit_count": 1,
        "content_per_unit": Decimal("1000"),
        "measurement_unit": "g",
        "total_content": Decimal("1000"),
        "currency": "HNL",
        "current_price": Decimal("32.50"),
        "is_promotion": False,
        "availability": AvailabilityStatus.IN_STOCK,
        "location_status": LocationStatus.CONFIRMED,
        "observed_at_utc": datetime(2026, 8, 5, 0, 0, tzinfo=UTC),
        "scrape_run_id": "run_20260805_000000",
        "extractor_version": "0.1.0",
        "schema_version": "1.0.0",
        "source_url": "https://example.com/api/products/SKU-001?utm_campaign=test",
        "source_sku": "SKU-001",
        "source_brand": "MARCA DEMO",
        "source_presentation": "1 kg",
        "source_category": "Granos básicos",
        "image_url": "https://example.com/images/arroz.jpg",
        "reported_regular_price": Decimal("35.00"),
        "unit_price": Decimal("0.0325"),
        "unit_price_basis": "1 g",
        "location_evidence": "Sucursal seleccionada en la interfaz",
        "location_confidence": Decimal("1"),
        "raw_values": {
            "current_price": "L 32.50",
            "reported_regular_price": "L 35.00",
        },
    }
    data.update(overrides)
    return NormalizedOffer(**data)


def test_creates_valid_models():
    raw = RawProduct(
        supermarket_id="super-demo",
        location_id="sps-principal",
        source_key_type="sku",
        source_key="SKU-001",
        source_name="Arroz Blanco Marca Demo 1 kg",
        product_url="https://example.com/productos/arroz",
        observed_at_utc=datetime(2026, 8, 5, 0, 0, tzinfo=UTC),
        scrape_run_id="run_20260805_000000",
        extractor_version="0.1.0",
        schema_version="1.0.0",
        source_url="https://example.com/api/products/SKU-001",
        location_status="confirmed",
        location_evidence="Sucursal seleccionada en la interfaz",
        location_confidence="1",
        raw_values={"name": "Arroz Blanco Marca Demo 1 kg"},
    )
    offer = build_offer()
    validated = ValidatedOffer(
        offer=offer,
        state_hash=generate_state_hash(offer),
        validated_at_utc=datetime(2026, 8, 5, 0, 1, tzinfo=UTC),
    )

    assert raw.source_key_type is SourceKeyType.SKU
    assert offer.current_price == Decimal("32.50")
    assert offer.review_status is ReviewStatus.READY
    assert offer.pending_fields == ()
    assert validated.offer.offer_id == offer.offer_id
    assert validated.quality_events == ()


def test_missing_product_url_is_preserved_as_unknown_instead_of_invented() -> None:
    raw = RawProduct(
        supermarket_id="super-demo",
        location_id="sps-principal",
        source_key_type="sku",
        source_key="SKU-001",
        source_name="Arroz Blanco Marca Demo 1 kg",
        product_url=None,
        observed_at_utc=datetime(2026, 8, 5, 0, 0, tzinfo=UTC),
        scrape_run_id="run_20260805_000000",
        extractor_version="0.1.0",
        schema_version="1.0.0",
        source_url="https://example.com/catalog",
    )
    offer = build_offer(product_url=None)

    assert raw.product_url is None
    assert offer.product_url is None


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("availability", "sold_out"),
        ("location_status", "verified"),
        ("source_key_type", "price"),
    ],
)
def test_rejects_invalid_enums(field_name, invalid_value):
    with pytest.raises(ValueError):
        build_offer(**{field_name: invalid_value})


def test_requires_utc_datetime():
    with pytest.raises(ValueError, match="UTC"):
        build_offer(observed_at_utc=datetime(2026, 8, 4, 18, 0))


def test_in_stock_without_current_price_is_rejected():
    with pytest.raises(ValueError, match="current_price"):
        build_offer(current_price=None, availability=AvailabilityStatus.IN_STOCK)


def test_out_of_stock_without_current_price_is_accepted():
    offer = build_offer(
        current_price=None,
        reported_regular_price=None,
        availability=AvailabilityStatus.OUT_OF_STOCK,
    )
    assert offer.current_price is None
    assert offer.availability is AvailabilityStatus.OUT_OF_STOCK


def test_unknown_without_current_price_is_accepted():
    offer = build_offer(
        current_price=None,
        reported_regular_price=None,
        availability=AvailabilityStatus.UNKNOWN,
    )
    assert offer.current_price is None
    assert offer.availability is AvailabilityStatus.UNKNOWN


def test_not_listed_without_current_price_is_accepted():
    offer = build_offer(
        current_price=None,
        reported_regular_price=None,
        availability=AvailabilityStatus.NOT_LISTED,
    )
    assert offer.current_price is None


def test_product_without_interpretable_presentation_is_preserved_for_review():
    offer = build_offer(
        source_presentation="Presentación especial",
        unit_count=None,
        content_per_unit=None,
        measurement_unit=None,
        total_content=None,
    )
    validated = ValidatedOffer(
        offer=offer,
        state_hash=generate_state_hash(offer),
        validated_at_utc=datetime(2026, 8, 5, 0, 1, tzinfo=UTC),
    )

    assert offer.review_status is ReviewStatus.NEEDS_REVIEW
    assert set(offer.pending_fields) >= {
        "unit_count",
        "content_per_unit",
        "measurement_unit",
        "total_content",
    }
    assert "pending_normalization:unit_count" in validated.quality_events
    assert "pending_normalization:total_content" in validated.quality_events


def test_product_without_brand_is_preserved_without_inventing_one():
    offer = build_offer(normalized_brand=None, source_brand=None)
    validated = ValidatedOffer(
        offer=offer,
        state_hash=generate_state_hash(offer),
        validated_at_utc=datetime(2026, 8, 5, 0, 1, tzinfo=UTC),
    )

    assert offer.normalized_brand is None
    assert offer.review_status is ReviewStatus.NEEDS_REVIEW
    assert "normalized_brand" in offer.pending_fields
    assert "pending_normalization:normalized_brand" in validated.quality_events


def test_confirmed_location_requires_evidence_and_confidence():
    with pytest.raises(ValueError, match="evidencia"):
        build_offer(location_status=LocationStatus.CONFIRMED, location_evidence=None)
