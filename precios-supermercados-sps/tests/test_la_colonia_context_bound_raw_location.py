from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest

import precios_supermercados.scrapers.la_colonia_context_bound_raw_location as raw_location_module
from precios_supermercados.enums import LocationStatus
from precios_supermercados.locations import LA_COLONIA_SPS as CANONICAL_LA_COLONIA_SPS
from precios_supermercados.offer_normalization import (
    normalize_raw_product,
    validate_normalized_offer,
)
from precios_supermercados.scrapers.la_colonia import LaColoniaExtractor
from precios_supermercados.scrapers.la_colonia_context_bound_raw_location import (
    ContextBoundRawLocationError,
    materialize_context_bound_sps_primary_catalog,
    materialize_context_bound_sps_raw_page,
)

TESTS = Path(__file__).parent


def _helper(filename: str, module_name: str) -> ModuleType:
    path = TESTS / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


CATALOG_HELPER = _helper(
    "test_la_colonia_context_bound_catalog_transport.py",
    "precios_sps_context_bound_catalog_helper_for_raw_location",
)


@pytest.fixture(autouse=True)
def _restore_canonical_location_after_test():
    yield
    raw_location_module.LA_COLONIA_SPS = CANONICAL_LA_COLONIA_SPS


def _full_products(order_by: str, start: int, end: int, total: int) -> list[dict[str, object]]:
    identities = list(range(1, total + 1))
    if order_by == "OrderByNameDESC":
        identities.reverse()
    selected = identities[start : min(end + 1, total)]
    return [
        {
            "productId": f"product-{value}",
            "productReference": f"reference-{value}",
            "productName": f"Producto {value} 1 L",
            "linkText": f"product-{value}",
            "categories": ["/supermercado/"],
            "categoryTree": [{"name": "Supermercado"}],
            "brand": "Marca",
            "items": [
                {
                    "itemId": f"sku-{value}",
                    "name": "1 L",
                    "nameComplete": f"Producto {value} 1 L",
                    "referenceId": [{"Key": "RefId", "Value": f"sku-ref-{value}"}],
                    "ean": "",
                    "measurementUnit": "un",
                    "unitMultiplier": 1,
                    "images": [
                        {"imageUrl": f"https://www.lacolonia.com/image-{value}.jpg"}
                    ],
                    "sellers": [
                        {
                            "sellerId": "1",
                            "sellerDefault": True,
                            "commercialOffer": {
                                "Price": 20 + value,
                                "ListPrice": 25 + value,
                                "AvailableQuantity": 10,
                            },
                        }
                    ],
                }
            ],
        }
        for value in selected
    ]


def _collection():
    original = CATALOG_HELPER._products
    CATALOG_HELPER._products = _full_products
    try:
        collector, _transport, proof = CATALOG_HELPER._collector()
        collection = collector.collect_all()
    finally:
        CATALOG_HELPER._products = original

    observation = _primary_observation(collection)
    payload = observation.page.verified_receipt.receipt.payload
    # El helper existente usa un regionId sintético para no conservar el valor real.
    # Alineamos sólo la constante local de este módulo de prueba con ese binding
    # firmado. Producción continúa usando LA_COLONIA_SPS canónico.
    raw_location_module.LA_COLONIA_SPS = replace(
        CANONICAL_LA_COLONIA_SPS,
        source_location_key=payload.binding_source_key,
        evidence=payload.binding_evidence,
    )
    return collection, proof


def _primary_observation(collection):
    return next(
        item
        for item in collection.observations
        if item.expected.traversal_role == "primary"
    )


def test_historico_permanece_online_y_solo_la_frontera_promueve_a_sps() -> None:
    collection, proof = _collection()
    observation = _primary_observation(collection)
    payload = observation.page.verified_receipt.receipt.payload

    historical = LaColoniaExtractor(
        clock=lambda: payload.response_completed_at_utc,
    ).parse_payload(
        observation.page.payload,
        scrape_run_id=payload.run_id,
        source_url=observation.page.source_url,
        page_size=observation.page.page_size,
    )
    assert historical.accepted is True
    assert historical.products
    assert {item.location_id for item in historical.products} == {"la_colonia_online"}
    assert {item.location_status for item in historical.products} == {LocationStatus.UNKNOWN}

    result = materialize_context_bound_sps_raw_page(observation)

    assert result.location_id == "la_colonia_sps"
    assert result.production_authority is False
    assert result.catalog_accepted is False
    assert result.extraction_enabled is False
    assert len(result.products) == len(historical.products)
    assert {item.location_id for item in result.products} == {"la_colonia_sps"}
    assert {item.location_status for item in result.products} == {LocationStatus.CONFIRMED}
    assert {item.location_confidence for item in result.products} == {Decimal("1")}
    assert all(
        item.location_evidence is not None
        and item.location_evidence.startswith("catalog_context_v3:receipt_sha256=")
        for item in result.products
    )

    raw_region = CATALOG_HELPER.SPS_HELPER.PLAN_HELPER.RAW_REGION
    assert raw_region not in repr(result)
    assert raw_region not in json.dumps(
        [item.location_evidence for item in result.products],
        sort_keys=True,
    )
    assert result.context_fingerprint == proof.context_fingerprint


def test_producto_promovido_no_emite_pending_location_binding() -> None:
    collection, _proof = _collection()
    result = materialize_context_bound_sps_raw_page(_primary_observation(collection))

    normalized = normalize_raw_product(result.products[0], currency="HNL")
    validated = validate_normalized_offer(
        normalized,
        validated_at_utc=datetime(2026, 8, 24, 18, 0, tzinfo=UTC),
    )

    assert normalized.location_id == "la_colonia_sps"
    assert normalized.location_status is LocationStatus.CONFIRMED
    assert "pending_location_binding" not in validated.quality_events


def test_catalogo_materializa_solo_primary_y_mantiene_autoridad_cerrada() -> None:
    collection, _proof = _collection()

    result = materialize_context_bound_sps_primary_catalog(collection)

    assert result.pages
    assert all(page.traversal_role == "primary" for page in result.pages)
    assert result.product_count == sum(len(page.products) for page in result.pages)
    assert result.run_id == collection.primary.run_id
    assert result.plan_digest == collection.plan_digest
    assert result.discovery_digest == collection.discovery_digest
    assert result.structural_context_plan_digest == collection.structural_context_plan_digest
    assert result.production_authority is False
    assert result.catalog_accepted is False
    assert result.extraction_enabled is False

    source_identities = [
        (item.source_key_type.value, item.source_key)
        for page in result.pages
        for item in page.products
    ]
    assert len(source_identities) == len(set(source_identities))


def test_rechaza_binding_source_distinto_del_canonico() -> None:
    collection, _proof = _collection()
    observation = _primary_observation(collection)
    payload = observation.page.verified_receipt.receipt.payload
    payload.binding_source_key = "request:regionid:sha256:" + "0" * 64

    with pytest.raises(ContextBoundRawLocationError) as captured:
        materialize_context_bound_sps_raw_page(observation)

    assert captured.value.code == "raw_location_binding_source_mismatch"


def test_rechaza_evidencia_de_item_que_no_corresponde_al_payload() -> None:
    collection, _proof = _collection()
    observation = _primary_observation(collection)
    first_product = observation.raw_evidence.products[0]
    object.__setattr__(first_product, "item_ids", ("sku-forged",))

    with pytest.raises(ContextBoundRawLocationError) as captured:
        materialize_context_bound_sps_raw_page(observation)

    assert captured.value.code in {
        "raw_location_sku_returned_count_mismatch",
        "raw_location_item_identity_mismatch",
    }
