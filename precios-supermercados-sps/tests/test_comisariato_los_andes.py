"""Pruebas offline del contrato demostrado de Comisariato Los Andes SPS."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from precios_supermercados.scrapers.comisariato_los_andes import (
    CATALOG_ENDPOINT,
    IMAGE_BASE,
    LOCATION_ID,
    PAGE_SIZE,
    STORE_ENDPOINT,
    STORE_ID,
    LosAndesError,
    build_catalog_request,
    build_store_request,
    parse_catalog_page,
    parse_products,
    reconcile_capture,
    validate_store_payload,
)

FIXTURE = Path(__file__).parent / "fixtures/comisariato_los_andes/catalog_page_sample.json"


def source_product(**updates):
    product = {
        "availibilityCount": 8,
        "brandId": 9,
        "brandName": "Marca",
        "code": "0001-000000000000123",
        "discount": None,
        "discountTwo": None,
        "images": [{"active": "1", "fileName": "p.webp", "position": 0}],
        "is_adult": "0",
        "listPrice": "PD",
        "materialGroupCode": "100",
        "materialGroupName": "ABARROTES",
        "name": "Producto de prueba",
        "newPrice": 10,
        "oldPrice": None,
        "price": 10,
        "stock": 100,
        "unitMeasureCode": "UN",
        "unitMeasureName": "Unidad",
    }
    product.update(updates)
    return product


def store_payload():
    return [
        {
            "id": 1,
            "name": "COMISARIATO LOS ANDES",
            "address": "SAN PEDRO SULA, CORTES, BARRIO LOS ANDES 6 CALLE 15 Y 15 AVENIDA",
            "location_one_code": "COR",
            "location_one_name": "CORTES",
            "location_two_code": "501",
            "location_two_name": "SAN PEDRO SULA",
            "office_code": "00",
            "latitude": "15.51108636534507",
            "longitude": "-88.03563552495497",
        }
    ]


def test_request_contract_is_sps_store_bound_and_bounded():
    assert build_store_request() == {
        "business_partner": 1,
        "cityCode": None,
        "pickPosition": "PD",
    }
    url, body = build_catalog_request(200)
    assert url == f"{CATALOG_ENDPOINT}?skip=200&take={PAGE_SIZE}"
    assert body["businessPartner"] == 1
    assert body["storeId"] == STORE_ID == 1
    assert body["officeCode"] == "00"
    assert body["type"] == "PD"
    assert body["source"] == "WEB"
    assert body["hidden"] == "0"
    with pytest.raises(LosAndesError, match="take_above_verified_page_size"):
        build_catalog_request(0, PAGE_SIZE + 1)


def test_store_payload_proves_exact_sps_binding():
    store = validate_store_payload(store_payload())
    assert store["id"] == 1
    assert store["office_code"] == "00"
    assert store["location_one_code"] == "COR"
    assert store["location_two_code"] == "501"
    assert store["location_two_name"] == "SAN PEDRO SULA"

    changed = store_payload()
    changed[0]["location_two_code"] = "999"
    with pytest.raises(LosAndesError, match="store_city_changed"):
        validate_store_payload(changed)


def test_proven_sample_normalizes_identity_price_and_image_without_inventing_stock():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    parsed = parse_catalog_page(payload, expected_skip=0, take=6)
    assert parsed["total_items"] == 6646
    assert parsed["total_pages"] == 1108
    assert len(parsed["rows"]) == 6
    by_code = {row["source_key"]: row for row in parsed["rows"]}
    first = by_code["0001-000099001005224"]
    assert first["source_key_type"] == "sku"
    assert first["product_id"] == first["item_id"] == first["source_key"]
    assert first["current_price"] == "149.95"
    assert first["reported_regular_price"] is None
    assert first["is_promotion"] is False
    assert first["availability"] == "unknown"
    assert first["brand"] == "Marca COMANDES"
    assert first["category"] == "READY TO GO"
    detail = parsed["source_details"][first["source_key"]]
    assert detail["source_availibility_count"] == 1000
    assert detail["availability_interpretation"] == "not_proven"
    # La muestra temprana devolvió 0; el crawl completo posterior devolvió "PD".
    # Ambos se preservan literalmente y nunca se usan como precio monetario.
    assert detail["source_list_price"] == 0
    assert detail["image_url"] == (
        IMAGE_BASE
        + "_0001_Ready_to_go_aderezo_para_ensaladas_-_99001005224_74ae411_2b2f61b.webp"
    )
    zero_signal = by_code["0001-000000007400051"]
    assert zero_signal["availability"] == "unknown"
    assert zero_signal["current_price"] == "18.90"
    assert parsed["source_details"][zero_signal["source_key"]]["source_availibility_count"] == 0


def test_explicit_old_price_and_matching_discount_prove_promotion():
    product = source_product(oldPrice=12, newPrice=9.5, price=9.5, discount=2.5)
    rows, details = parse_products([product])
    row = rows[0]
    assert (row["current_price"], row["reported_regular_price"], row["is_promotion"]) == (
        "9.50",
        "12.00",
        True,
    )
    assert details[row["source_key"]]["regular_price_evidence"] == ["oldPrice"]
    assert details[row["source_key"]]["source_list_price"] == "PD"


def test_list_price_is_delivery_mode_not_a_monetary_signal():
    rows, details = parse_products([source_product(listPrice="PD")])
    row = rows[0]
    assert row["current_price"] == "10.00"
    assert row["reported_regular_price"] is None
    assert row["is_promotion"] is False
    assert details[row["source_key"]]["source_list_price"] == "PD"


@pytest.mark.parametrize(
    "updates,reason",
    [
        ({"code": ""}, "source_code_invalid"),
        ({"name": ""}, "source_name_invalid"),
        ({"newPrice": -1, "price": -1}, "new_price_invalid"),
        ({"availibilityCount": -1}, "availability_signal_invalid"),
    ],
)
def test_ambiguous_or_invalid_source_signals_fail_closed(updates, reason):
    with pytest.raises(LosAndesError, match=reason):
        parse_products([source_product(**updates)])


@pytest.mark.parametrize(
    "updates,reason",
    [
        ({"newPrice": 10, "price": 9}, "new_price_price_mismatch"),
        ({"newPrice": 10, "price": 10, "oldPrice": 9}, "oldPrice_below_current"),
        ({"discount": 5}, "discount_without_regular_price_evidence"),
        ({"newPrice": 10, "price": 10, "oldPrice": 12, "discount": 1}, "discount_amount_mismatch"),
    ],
)
def test_one_product_price_conflict_is_quarantined_without_a_price(updates, reason):
    """Un producto con precios contradictorios ya no tumba la cadena (2026-10-22 a 24)."""
    rows, details = parse_products([source_product(**updates)])
    row = rows[0]
    assert row["current_price"] is None and row["reported_regular_price"] is None and row["is_promotion"] is None
    detail = details[row["source_key"]]
    assert detail["price_status"] == "quarantined_price_conflict" and detail["quarantine_reason"] == reason


def test_price_quarantine_has_a_per_run_limit():
    from precios_supermercados.scrapers.comisariato_los_andes import price_quarantine_limit
    assert price_quarantine_limit(6277) == 31 and price_quarantine_limit(100) == 10


def test_unpriced_product_does_not_invent_promotion_or_availability():
    rows, details = parse_products(
        [source_product(availibilityCount=0, newPrice=0, price=0, oldPrice=None)]
    )
    row = rows[0]
    assert row["availability"] == "unknown"
    assert row["current_price"] is None
    assert row["reported_regular_price"] is None
    assert row["is_promotion"] is None
    assert details[row["source_key"]]["price_status"] == "unpriced"


def test_missing_availability_is_also_preserved_as_unknown():
    rows, details = parse_products([source_product(availibilityCount=None)])
    assert rows[0]["availability"] == "unknown"
    assert details[rows[0]["source_key"]]["source_availibility_count"] is None


def test_duplicate_source_code_is_rejected():
    product = source_product()
    with pytest.raises(LosAndesError, match="source_code_duplicate"):
        parse_products([product, copy.deepcopy(product)])


def test_page_metadata_must_match_offset_and_page_size():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    broken = copy.deepcopy(payload)
    broken["currentPage"] = 2
    with pytest.raises(LosAndesError, match="current_page_mismatch"):
        parse_catalog_page(broken, expected_skip=0, take=6)


def test_complete_capture_reconciles_only_from_hashed_raw(tmp_path):
    products = [
        source_product(code="0001-000000000000001", availibilityCount=3),
        source_product(
            code="0001-000000000000002",
            availibilityCount=0,
            name="Otro producto",
        ),
    ]
    payload = {
        "currentPage": 1,
        "data": products,
        "itemPerPage": 2,
        "totalItems": 2,
        "totalPages": 1,
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    response_name = "page-00000.json"
    final_name = "final-recheck.json"
    store_name = "store-evidence.json"
    (tmp_path / response_name).write_bytes(raw)
    (tmp_path / final_name).write_bytes(raw)
    store_raw = json.dumps(store_payload(), ensure_ascii=False, separators=(",", ":")).encode()
    (tmp_path / store_name).write_bytes(store_raw)

    url, body = build_catalog_request(0, 100)
    page_record = {
        "skip": 0,
        "take": 100,
        "status": 201,
        "retries": 0,
        "url": url,
        "request_body": body,
        "response_file": response_name,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "response_bytes": len(raw),
        "observed_at_utc": "2026-09-04T01:30:00Z",
    }
    final_record = {
        **page_record,
        "response_file": final_name,
        "observed_at_utc": "2026-09-04T01:30:02Z",
    }
    store_record = {
        "status": 200,
        "retries": 0,
        "url": STORE_ENDPOINT,
        "request_body": build_store_request(),
        "response_file": store_name,
        "response_sha256": hashlib.sha256(store_raw).hexdigest(),
        "response_bytes": len(store_raw),
        "observed_at_utc": "2026-09-04T01:29:59Z",
    }
    ledger = {
        "closed": True,
        "supermarket_id": "comisariato_los_andes",
        "location_id": LOCATION_ID,
        "store_id": 1,
        "office_code": "00",
        "location_two_code": "501",
        "concurrency": 1,
        "request_count": 3,
        "retry_count": 0,
        "store_evidence": store_record,
        "final_total_items": 2,
        "final_recheck": final_record,
        "pages": [page_record],
    }
    (tmp_path / "ledger.json").write_text(json.dumps(ledger), encoding="utf-8")
    snapshot = reconcile_capture(tmp_path)
    assert snapshot["catalog_complete"] is True
    assert snapshot["location_verified_same_run"] is True
    assert snapshot["store_name"] == "COMISARIATO LOS ANDES"
    assert snapshot["catalog_products_reported"] == 2
    assert snapshot["unique_products_extracted"] == 2
    assert snapshot["availability_counts"] == {"unknown": 2}
    assert snapshot["promotion_counts"] == {
        "promotion": 0,
        "not_promotion": 2,
        "unknown": 0,
    }

    (tmp_path / response_name).write_bytes(raw + b" ")
    with pytest.raises(LosAndesError, match="raw_file_hash_mismatch"):
        reconcile_capture(tmp_path)


def test_capture_rejects_missing_same_run_store_evidence(tmp_path):
    (tmp_path / "ledger.json").write_text(
        json.dumps(
            {
                "closed": True,
                "supermarket_id": "comisariato_los_andes",
                "location_id": LOCATION_ID,
                "concurrency": 1,
                "request_count": 1,
                "retry_count": 0,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(LosAndesError, match="store_evidence_missing"):
        reconcile_capture(tmp_path)


def test_capture_quarantines_one_price_conflict_and_persists_the_rest(tmp_path):
    """2026-10-22: un producto con newPrice ≠ price dejaba a Los Andes STALE tres días."""
    products = [
        source_product(code="0001-000000000000001", availibilityCount=3),
        source_product(
            code="0001-000000000000002",
            availibilityCount=0,
            name="Otro producto",
            newPrice=12,
            price=11,
        ),
    ]
    payload = {
        "currentPage": 1,
        "data": products,
        "itemPerPage": 2,
        "totalItems": 2,
        "totalPages": 1,
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    response_name = "page-00000.json"
    final_name = "final-recheck.json"
    store_name = "store-evidence.json"
    (tmp_path / response_name).write_bytes(raw)
    (tmp_path / final_name).write_bytes(raw)
    store_raw = json.dumps(store_payload(), ensure_ascii=False, separators=(",", ":")).encode()
    (tmp_path / store_name).write_bytes(store_raw)

    url, body = build_catalog_request(0, 100)
    page_record = {
        "skip": 0,
        "take": 100,
        "status": 201,
        "retries": 0,
        "url": url,
        "request_body": body,
        "response_file": response_name,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "response_bytes": len(raw),
        "observed_at_utc": "2026-09-04T01:30:00Z",
    }
    final_record = {
        **page_record,
        "response_file": final_name,
        "observed_at_utc": "2026-09-04T01:30:02Z",
    }
    store_record = {
        "status": 200,
        "retries": 0,
        "url": STORE_ENDPOINT,
        "request_body": build_store_request(),
        "response_file": store_name,
        "response_sha256": hashlib.sha256(store_raw).hexdigest(),
        "response_bytes": len(store_raw),
        "observed_at_utc": "2026-09-04T01:29:59Z",
    }
    ledger = {
        "closed": True,
        "supermarket_id": "comisariato_los_andes",
        "location_id": LOCATION_ID,
        "store_id": 1,
        "office_code": "00",
        "location_two_code": "501",
        "concurrency": 1,
        "request_count": 3,
        "retry_count": 0,
        "store_evidence": store_record,
        "final_total_items": 2,
        "final_recheck": final_record,
        "pages": [page_record],
    }
    (tmp_path / "ledger.json").write_text(json.dumps(ledger), encoding="utf-8")
    snapshot = reconcile_capture(tmp_path)
    assert snapshot["catalog_products_reported"] == 2 and snapshot["unique_products_extracted"] == 2
    assert [row["source_key"] for row in snapshot["products"]] == ["0001-000000000000001"]
    assert snapshot["skus_extracted"] == 1 and snapshot["skus_with_price"] == 1
    assert snapshot["price_quarantine"]["count"] == 1
    assert snapshot["price_quarantine"]["products"][0]["reason"] == "new_price_price_mismatch"
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import actualizar_mvp_turso_comisariato_los_andes as persistence
    persistence.validate_snapshot_bytes(json.dumps(snapshot, ensure_ascii=False).encode())
