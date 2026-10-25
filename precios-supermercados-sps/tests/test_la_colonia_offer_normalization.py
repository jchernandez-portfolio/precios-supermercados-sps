from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from precios_supermercados.enums import AvailabilityStatus, LocationStatus, SourceKeyType
from precios_supermercados.identifiers import generate_state_hash
from precios_supermercados.la_colonia_offer_normalization import (
    LaColoniaOfferNormalizationError,
    normalize_and_validate_la_colonia_raw_product,
    normalize_la_colonia_raw_product,
)
from precios_supermercados.models import RawProduct
from precios_supermercados.offer_normalization import normalize_raw_product

BASE_TIME = datetime(2026, 8, 26, 2, 40, 57, tzinfo=UTC)


def _raw(
    *,
    source_key: str = "fixture-1",
    source_name: str = "Producto Fixture 473 Ml",
    brand: str = "Marca Fixture",
    presentation: str | None = "473 Ml",
    category: str = "Supermercado > Categoría > Subcategoría",
    current_price: str = "99.90",
    source_sku: str | None = "SKU-FIXTURE",
    ean: str | None = None,
) -> RawProduct:
    return RawProduct(
        supermarket_id="la_colonia",
        location_id="la_colonia_sps",
        source_key_type=SourceKeyType.INTERNAL_ID,
        source_key=source_key,
        source_name=source_name,
        product_url=f"https://www.lacolonia.com/producto-{source_key}",
        observed_at_utc=BASE_TIME,
        scrape_run_id="run-fixture",
        extractor_version="fixture-extractor",
        schema_version="fixture-schema",
        source_url="https://www.lacolonia.com/_v/segment/graphql/v1?fixture=1",
        source_sku=source_sku,
        source_brand=brand,
        source_presentation=presentation,
        source_category=category,
        image_url="https://www.lacolonia.com/arquivos/fixture.jpg",
        location_status=LocationStatus.CONFIRMED,
        location_evidence="fixture:sps_binding",
        location_confidence=Decimal("1"),
        raw_values={
            "product_id": source_key,
            "item_id": source_key,
            "reference": source_sku,
            "ean": ean,
            "subcategory": category.split(">")[-1].strip(),
            "current_price": current_price,
            "reported_regular_price": None,
            "is_promotion": False,
            "availability": AvailabilityStatus.IN_STOCK.value,
            "measurement_unit": "un",
            "unit_multiplier": "1",
            "weighted_product": False,
        },
    )


def test_reviewed_registry_multipack_overrides_weaker_source_presentation_and_preserves_provenance() -> None:
    raw = _raw(
        source_key="16289",
        source_name="2Pk Crema Dental Colgate Lumimous White 2X100 Ml",
        brand="Colgate",
        presentation="100 Ml",
        category="Supermercado > Belleza y Cuidado Personal > Cuidado Oral",
        current_price="199.9",
        source_sku="7509546065878",
        ean="7509546065878",
    )

    result = normalize_la_colonia_raw_product(raw, currency="HNL")
    offer = result.offer

    assert result.product_normalization.presentation_normalized == "2 x 100 ml"
    assert result.product_normalization.presentation_kind == "physical"
    assert result.product_normalization.content_scope == "per_unit"
    assert offer.unit_count == 2
    assert offer.content_per_unit == Decimal("100")
    assert offer.measurement_unit == "ml"
    assert offer.total_content == Decimal("200")
    assert offer.source_presentation == "100 Ml"
    assert offer.current_price == Decimal("199.9")
    assert offer.availability is AvailabilityStatus.IN_STOCK
    assert offer.product_url == raw.product_url
    assert offer.source_url == raw.source_url
    assert offer.scrape_run_id == raw.scrape_run_id
    assert offer.extractor_version == raw.extractor_version
    assert offer.schema_version == raw.schema_version
    assert offer.location_id == raw.location_id
    assert offer.location_status is LocationStatus.CONFIRMED
    assert offer.location_evidence == raw.location_evidence
    assert offer.location_confidence == Decimal("1")


@pytest.mark.parametrize(
    ("name", "presentation", "expected_display", "expected_unit"),
    [
        ("Aceite de Maíz para Cocinar 1 Gl", "1 Gl", "1 galón", "gal"),
        ("Cinta Tape transparente 1 Ct", None, "1 unidad", "unit"),
        ("Apio Local Mazo", None, "1 mazo", "bunch"),
        ("Arándanos La Carreta Bandeja", None, "1 bandeja", "tray"),
    ],
)
def test_new_unregistered_product_uses_only_safe_general_rules(
    name: str,
    presentation: str | None,
    expected_display: str,
    expected_unit: str,
) -> None:
    raw = _raw(source_name=name, presentation=presentation)

    result = normalize_la_colonia_raw_product(raw, currency="HNL", registry={})

    assert result.product_normalization.presentation_normalized == expected_display
    assert result.offer.measurement_unit == expected_unit
    assert result.offer.unit_count == 1
    assert result.offer.content_per_unit == Decimal("1")
    assert result.offer.total_content == Decimal("1")


def test_reviewed_mixed_pack_preserves_declared_measure_without_inventing_scope() -> None:
    raw = _raw(
        source_key="17488",
        source_name="2Pk Desodorante Gel Gillette Anti Hydra Aloe 82Gr",
        brand="Gillette",
        presentation="82Gr",
        category="Supermercado > Belleza y Cuidado Personal > Desodorantes Hombres",
    )

    result = normalize_la_colonia_raw_product(raw, currency="HNL")

    assert result.product_normalization.presentation_normalized == "2 unidades · 82 g"
    assert result.product_normalization.presentation_kind == "mixed"
    assert result.product_normalization.declared_content == Decimal("82")
    assert result.product_normalization.content_scope == "unspecified"
    assert result.offer.unit_count == 2
    assert result.offer.measurement_unit == "g"
    assert result.offer.content_per_unit is None
    assert result.offer.total_content is None


def test_stale_known_override_fails_closed_even_when_name_would_be_parseable() -> None:
    raw = _raw(
        source_key="known",
        source_name="Aguacate Liso Un",
        presentation=None,
    )
    stale_registry = {"known": {"source_signature": "0" * 64}}

    with pytest.raises(
        LaColoniaOfferNormalizationError,
        match="product_normalization_review_required",
    ):
        normalize_la_colonia_raw_product(
            raw,
            currency="HNL",
            registry=stale_registry,
        )


def test_ambiguous_tint_code_is_not_promoted_to_count_for_new_product() -> None:
    raw = _raw(
        source_name="Tinte Ejemplo Rubio 5U",
        presentation=None,
    )

    with pytest.raises(
        LaColoniaOfferNormalizationError,
        match="product_normalization_pending",
    ):
        normalize_la_colonia_raw_product(raw, currency="HNL", registry={})


def test_validated_hash_is_sealed_after_source_specific_overlay() -> None:
    raw = _raw(
        source_key="16289",
        source_name="2Pk Crema Dental Colgate Lumimous White 2X100 Ml",
        brand="Colgate",
        presentation="100 Ml",
        category="Supermercado > Belleza y Cuidado Personal > Cuidado Oral",
        source_sku="7509546065878",
        ean="7509546065878",
    )
    generic = normalize_raw_product(raw, currency="HNL")

    result = normalize_and_validate_la_colonia_raw_product(
        raw,
        currency="HNL",
        validated_at_utc=BASE_TIME + timedelta(minutes=1),
    )
    validated = result.validated_offer

    assert generic.unit_count == 1
    assert generic.total_content == Decimal("100")
    assert validated.offer.unit_count == 2
    assert validated.offer.total_content == Decimal("200")
    assert validated.state_hash == generate_state_hash(validated.offer)
    assert validated.state_hash != generate_state_hash(generic)


def test_wrong_supermarket_is_rejected_before_generic_normalization() -> None:
    raw = _raw()
    raw = RawProduct(
        supermarket_id="otro_super",
        location_id=raw.location_id,
        source_key_type=raw.source_key_type,
        source_key=raw.source_key,
        source_name=raw.source_name,
        product_url=raw.product_url,
        observed_at_utc=raw.observed_at_utc,
        scrape_run_id=raw.scrape_run_id,
        extractor_version=raw.extractor_version,
        schema_version=raw.schema_version,
        source_url=raw.source_url,
        source_sku=raw.source_sku,
        source_brand=raw.source_brand,
        source_presentation=raw.source_presentation,
        source_category=raw.source_category,
        image_url=raw.image_url,
        location_status=raw.location_status,
        location_evidence=raw.location_evidence,
        location_confidence=raw.location_confidence,
        raw_values=raw.raw_values,
    )

    with pytest.raises(LaColoniaOfferNormalizationError, match="supermarket_mismatch"):
        normalize_la_colonia_raw_product(raw, currency="HNL", registry={})
