"""Contratos públicos SPS y reconciliación offline de Comisariato Los Andes.

Este módulo no abre sockets. La adquisición live vive en un workflow temporal y
la normalización/reconciliación se reproduce únicamente desde evidencia RAW.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

SUPERMARKET_ID = "comisariato_los_andes"
LOCATION_ID = "comisariato_los_andes_sps"
CITY = "San Pedro Sula"
CURRENCY = "HNL"
SCOPE = "public_ecommerce_sps_store_1_pd"
API_BASE = "https://andes.aveapplications.com"
CATALOG_ENDPOINT = f"{API_BASE}/api/em/material/paginate"
STORE_ENDPOINT = f"{API_BASE}/api/em/store/get_filter"
SOURCE_URL = "https://comisariatolosandes.com/todos"
IMAGE_BASE = (
    "https://comisariatolosandesfiles.s3.us-east-2.amazonaws.com/"
    "folder/products/500X500/"
)
BUSINESS_PARTNER = 1
STORE_ID = 1
OFFICE_CODE = "00"
LOCATION_ONE_CODE = "COR"
LOCATION_TWO_CODE = "501"
DELIVERY_TYPE = "PD"
PAGE_SIZE = 100
EXTRACTOR_VERSION = "comisariato_los_andes_v1"
SCHEMA_VERSION = "1"


class LosAndesError(ValueError):
    """La evidencia no satisface el contrato comercial demostrado."""


def require(value: object, reason: str) -> None:
    if not value:
        raise LosAndesError(reason)


def _number(value: object, reason: str, *, allow_none: bool = False) -> Decimal | None:
    if value is None and allow_none:
        return None
    require(type(value) in {int, float, str}, reason)
    if isinstance(value, float):
        require(math.isfinite(value), reason)
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise LosAndesError(reason) from exc
    require(amount.is_finite(), reason)
    return amount


def _money(value: object, reason: str, *, allow_none: bool = False) -> str | None:
    amount = _number(value, reason, allow_none=allow_none)
    if amount is None:
        return None
    require(amount >= 0, reason)
    require(amount * 100 == (amount * 100).to_integral_value(), reason)
    return format(amount, ".2f")


def _positive_int(value: object, reason: str, *, allow_zero: bool = False) -> int:
    require(type(value) is int, reason)
    require(value >= 0 if allow_zero else value > 0, reason)
    return value


def build_store_request() -> dict[str, object]:
    return {
        "business_partner": BUSINESS_PARTNER,
        "cityCode": None,
        "pickPosition": DELIVERY_TYPE,
    }


def build_catalog_request(skip: int, take: int = PAGE_SIZE) -> tuple[str, dict[str, object]]:
    _positive_int(skip, "skip_invalid", allow_zero=True)
    _positive_int(take, "take_invalid")
    require(take <= PAGE_SIZE, "take_above_verified_page_size")
    return f"{CATALOG_ENDPOINT}?skip={skip}&take={take}", {
        "businessPartner": BUSINESS_PARTNER,
        "storeId": STORE_ID,
        "groupCode": "0",
        "officeCode": OFFICE_CODE,
        "type": DELIVERY_TYPE,
        "sortBy": "category",
        "sortOption": "ASC",
        "search": "",
        "filter": {
            "priceMin": None,
            "priceMax": None,
            "brand": None,
            "supplier": None,
            "bulletPoint": None,
            "agency": None,
        },
        "source": "WEB",
        "hidden": "0",
    }


def validate_store_payload(payload: object) -> dict[str, Any]:
    """Exige la única tienda SPS observada por el selector público."""
    require(isinstance(payload, list) and len(payload) == 1, "store_payload_invalid")
    store = payload[0]
    require(isinstance(store, dict), "store_payload_invalid")
    require(store.get("id") == STORE_ID, "store_id_changed")
    require(store.get("office_code") == OFFICE_CODE, "store_office_changed")
    require(store.get("location_one_code") == LOCATION_ONE_CODE, "store_department_changed")
    require(store.get("location_one_name") == "CORTES", "store_department_name_changed")
    require(store.get("location_two_code") == LOCATION_TWO_CODE, "store_city_changed")
    require(store.get("location_two_name") == "SAN PEDRO SULA", "store_city_name_changed")
    require(store.get("name") == "COMISARIATO LOS ANDES", "store_name_changed")
    return store


def _image_url(product: dict[str, Any]) -> str | None:
    images = product.get("images")
    if not images:
        return None
    require(isinstance(images, list), "images_invalid")
    active = [item for item in images if isinstance(item, dict) and item.get("active") == "1"]
    candidates = active or [item for item in images if isinstance(item, dict)]
    if not candidates:
        return None
    image = sorted(candidates, key=lambda item: item.get("position", 0))[0]
    filename = image.get("fileName")
    require(isinstance(filename, str) and filename.strip(), "image_filename_invalid")
    return IMAGE_BASE + filename.strip().lstrip("/")


def _availability(product: dict[str, Any]) -> tuple[str, Decimal | None]:
    """Conserva la señal fuente sin inventarle semántica de disponibilidad."""
    signal = _number(
        product.get("availibilityCount"),
        "availability_signal_invalid",
        allow_none=True,
    )
    if signal is not None:
        require(signal >= 0, "availability_signal_invalid")
    return "unknown", signal


def _pricing(
    product: dict[str, Any],
) -> tuple[str | None, str | None, bool | None, dict[str, Any]]:
    current = _money(product.get("newPrice"), "new_price_invalid", allow_none=True)
    mirrored = _money(product.get("price"), "price_invalid", allow_none=True)
    old = _money(product.get("oldPrice"), "old_price_invalid", allow_none=True)
    discount = _number(product.get("discount"), "discount_invalid", allow_none=True)
    discount_two = _number(
        product.get("discountTwo"), "discount_two_invalid", allow_none=True
    )
    raw_list_price = product.get("listPrice")  # literal "PD" en el crawl completo

    if current is None or Decimal(current) == 0:
        require(mirrored is None or Decimal(mirrored) == 0, "unpriced_price_mismatch")
        require(old is None or Decimal(old) == 0, "unpriced_regular_price_invalid")
        require(discount is None or discount == 0, "unpriced_discount_invalid")
        require(discount_two is None or discount_two == 0, "unpriced_discount_two_invalid")
        return None, None, None, {
            "price_status": "unpriced",
            "source_new_price": product.get("newPrice"),
            "source_price": product.get("price"),
            "source_old_price": product.get("oldPrice"),
            "source_list_price": raw_list_price,
            "source_discount": product.get("discount"),
            "source_discount_two": product.get("discountTwo"),
            "regular_price_evidence": [],
        }

    require(Decimal(current) > 0, "new_price_invalid")
    if mirrored is not None:
        require(mirrored == current, "new_price_price_mismatch")

    regular: str | None = None
    if old is not None and Decimal(old) > 0:
        require(Decimal(old) >= Decimal(current), "oldPrice_below_current")
        if Decimal(old) > Decimal(current):
            regular = old

    nonzero_discounts = [
        value for value in (discount, discount_two) if value is not None and value != 0
    ]
    if nonzero_discounts:
        require(regular is not None, "discount_without_regular_price_evidence")
        expected = Decimal(regular) - Decimal(current)
        for value in nonzero_discounts:
            require(value == expected, "discount_amount_mismatch")

    promotion = regular is not None
    return current, regular, promotion, {
        "price_status": "observed",
        "source_new_price": product.get("newPrice"),
        "source_price": product.get("price"),
        "source_old_price": product.get("oldPrice"),
        "source_list_price": raw_list_price,
        "source_discount": product.get("discount"),
        "source_discount_two": product.get("discountTwo"),
        "regular_price_evidence": ["oldPrice"] if regular is not None else [],
    }


def parse_products(
    products: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    require(isinstance(products, list), "products_invalid")
    rows: list[dict[str, Any]] = []
    details: dict[str, dict[str, Any]] = {}
    for product in products:
        require(isinstance(product, dict), "product_invalid")
        code = product.get("code")
        name = product.get("name")
        require(isinstance(code, str) and code.strip(), "source_code_invalid")
        code = code.strip()
        require(code not in details, "source_code_duplicate")
        require(isinstance(name, str) and name.strip(), "source_name_invalid")
        name = name.strip()

        availability, availability_signal = _availability(product)
        current, regular, promotion, pricing = _pricing(product)
        brand = product.get("brandName")
        category = product.get("materialGroupName")
        unit = product.get("unitMeasureCode")
        require(brand is None or isinstance(brand, str), "brand_invalid")
        require(category is None or isinstance(category, str), "category_invalid")
        require(unit is None or isinstance(unit, str), "unit_measure_invalid")

        rows.append(
            {
                "product_id": code,
                "item_id": code,
                "source_key_type": "sku",
                "source_key": code,
                "source_name": name,
                "brand": brand.strip() if isinstance(brand, str) and brand.strip() else None,
                "reference": code,
                # El catálogo público no expone barcode/GTIN. `code` es un código de
                # material interno (`0001-` + 15 dígitos, p. ej. 7400051 o 99001005224):
                # nunca se reinterpreta como GTIN aunque quite ceros a la izquierda.
                "ean": None,
                "category": (
                    category.strip()
                    if isinstance(category, str) and category.strip()
                    else None
                ),
                "presentation": unit.strip() if isinstance(unit, str) and unit.strip() else None,
                "current_price": current,
                "reported_regular_price": regular,
                "is_promotion": promotion,
                "availability": availability,
            }
        )
        details[code] = {
            **pricing,
            "source_availibility_count": product.get("availibilityCount"),
            "availability_signal": (
                None if availability_signal is None else str(availability_signal)
            ),
            "availability_interpretation": "not_proven",
            "source_stock": product.get("stock"),
            "source_brand_id": product.get("brandId"),
            "source_material_group_code": product.get("materialGroupCode"),
            "source_material_group_name": product.get("materialGroupName"),
            "source_unit_measure_code": product.get("unitMeasureCode"),
            "source_unit_measure_name": product.get("unitMeasureName"),
            "source_is_adult": product.get("is_adult"),
            "image_url": _image_url(product),
        }
    return rows, details


def parse_catalog_page(
    payload: dict[str, Any], *, expected_skip: int, take: int = PAGE_SIZE
) -> dict[str, Any]:
    require(isinstance(payload, dict), "catalog_payload_invalid")
    expected_skip = _positive_int(
        expected_skip, "expected_skip_invalid", allow_zero=True
    )
    take = _positive_int(take, "take_invalid")
    total = _positive_int(payload.get("totalItems"), "total_items_invalid")
    pages = _positive_int(payload.get("totalPages"), "total_pages_invalid")
    item_per_page = _positive_int(payload.get("itemPerPage"), "item_per_page_invalid")
    current_page = _positive_int(payload.get("currentPage"), "current_page_invalid")
    data = payload.get("data")
    require(isinstance(data, list), "catalog_data_invalid")
    expected_count = min(take, total - expected_skip)
    require(expected_count >= 0, "page_offset_out_of_range")
    require(len(data) == expected_count, "page_item_count_mismatch")
    require(item_per_page == len(data), "item_per_page_mismatch")
    require(current_page == expected_skip // take + 1, "current_page_mismatch")
    require(pages == math.ceil(total / take), "total_pages_mismatch")
    rows, details = parse_products(data)
    return {
        "total_items": total,
        "total_pages": pages,
        "item_per_page": item_per_page,
        "current_page": current_page,
        "rows": rows,
        "source_details": details,
    }


def _read_hashed(path: Path, expected_sha256: str) -> bytes:
    require(path.is_file(), "raw_file_missing")
    raw = path.read_bytes()
    require(
        hashlib.sha256(raw).hexdigest() == expected_sha256,
        "raw_file_hash_mismatch",
    )
    return raw


def _read_json_record(directory: Path, record: dict[str, Any]) -> object:
    filename = record.get("response_file")
    digest = record.get("response_sha256")
    require(
        isinstance(filename, str) and Path(filename).name == filename,
        "unsafe_raw_path",
    )
    require(isinstance(digest, str) and len(digest) == 64, "response_hash_invalid")
    raw = _read_hashed(directory / filename, digest)
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LosAndesError("raw_json_invalid") from exc


def reconcile_capture(directory: str | Path) -> dict[str, Any]:
    """Reproduce un crawl SPS completo exclusivamente desde RAW + ledger."""
    directory = Path(directory)
    ledger_path = directory / "ledger.json"
    require(ledger_path.is_file(), "ledger_missing")
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LosAndesError("ledger_invalid") from exc

    require(ledger.get("closed") is True, "capture_not_closed")
    require(ledger.get("supermarket_id") == SUPERMARKET_ID, "capture_supermarket_invalid")
    require(ledger.get("location_id") == LOCATION_ID, "capture_location_invalid")
    require(ledger.get("concurrency") == 1, "capture_concurrency_invalid")
    request_count = _positive_int(ledger.get("request_count"), "request_count_invalid")
    retry_count = _positive_int(
        ledger.get("retry_count"), "retry_count_invalid", allow_zero=True
    )
    require(request_count <= 400 and retry_count <= 10, "capture_budget_exceeded")

    store_record = ledger.get("store_evidence")
    require(isinstance(store_record, dict), "store_evidence_missing")
    require(store_record.get("status") in {200, 201}, "store_request_failed")
    require(store_record.get("retries") == 0, "store_request_failed")
    require(store_record.get("url") == STORE_ENDPOINT, "store_url_mismatch")
    require(store_record.get("request_body") == build_store_request(), "store_body_mismatch")
    store = validate_store_payload(_read_json_record(directory, store_record))

    records = ledger.get("pages")
    require(isinstance(records, list) and records, "page_ledger_invalid")
    final_record = ledger.get("final_recheck")
    require(isinstance(final_record, dict), "final_recheck_missing")
    require(request_count == len(records) + 2, "request_count_mismatch")

    all_rows: list[dict[str, Any]] = []
    all_details: dict[str, dict[str, Any]] = {}
    page_evidence: list[dict[str, Any]] = []
    totals: set[int] = set()
    page_counts: set[int] = set()
    offsets: set[int] = set()
    observed: list[datetime] = []

    for record in sorted(records, key=lambda item: item.get("skip", -1)):
        require(isinstance(record, dict), "page_ledger_invalid")
        skip = _positive_int(record.get("skip"), "page_skip_invalid", allow_zero=True)
        take = _positive_int(record.get("take"), "page_take_invalid")
        require(take == PAGE_SIZE, "page_take_unverified")
        require(skip not in offsets, "page_skip_duplicate")
        offsets.add(skip)
        require(
            record.get("status") in {200, 201} and record.get("retries") == 0,
            "page_request_failed",
        )
        request_url, request_body = build_catalog_request(skip, take)
        require(record.get("url") == request_url, "page_url_mismatch")
        require(record.get("request_body") == request_body, "page_body_mismatch")
        payload = _read_json_record(directory, record)
        parsed = parse_catalog_page(payload, expected_skip=skip, take=take)
        totals.add(parsed["total_items"])
        page_counts.add(parsed["total_pages"])
        for row in parsed["rows"]:
            source_key = row["source_key"]
            require(source_key not in all_details, "cross_page_source_code_duplicate")
            all_rows.append(row)
            all_details[source_key] = parsed["source_details"][source_key]

        stamp = record.get("observed_at_utc")
        require(isinstance(stamp, str) and stamp.endswith("Z"), "observed_at_invalid")
        try:
            observed.append(datetime.fromisoformat(stamp[:-1] + "+00:00"))
        except ValueError as exc:
            raise LosAndesError("observed_at_invalid") from exc
        page_evidence.append(
            {
                "skip": skip,
                "take": take,
                "http_status": record.get("status"),
                "response_sha256": record["response_sha256"],
                "response_bytes": record.get("response_bytes"),
            }
        )

    require(
        len(totals) == 1 and len(page_counts) == 1,
        "catalog_total_changed_during_capture",
    )
    total = next(iter(totals))
    total_pages = next(iter(page_counts))
    require(len(records) == total_pages, "catalog_page_count_incomplete")
    require(offsets == set(range(0, total, PAGE_SIZE)), "catalog_offsets_incomplete")
    require(
        len(all_rows) == total and len(all_details) == total,
        "catalog_membership_incomplete",
    )

    require(final_record.get("status") in {200, 201}, "final_recheck_failed")
    require(final_record.get("retries") == 0, "final_recheck_failed")
    final_url, final_body = build_catalog_request(0, PAGE_SIZE)
    require(final_record.get("url") == final_url, "final_recheck_url_mismatch")
    require(final_record.get("request_body") == final_body, "final_recheck_body_mismatch")
    final_payload = _read_json_record(directory, final_record)
    final_parsed = parse_catalog_page(final_payload, expected_skip=0, take=PAGE_SIZE)
    require(final_parsed["total_items"] == total, "final_total_recheck_mismatch")
    require(final_parsed["total_pages"] == total_pages, "final_pages_recheck_mismatch")

    all_rows.sort(key=lambda row: row["source_key"])
    observed_at = (
        max(observed).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    availability_counts = dict(
        sorted(Counter(row["availability"] for row in all_rows).items())
    )
    promotion_counts = {
        "promotion": sum(row["is_promotion"] is True for row in all_rows),
        "not_promotion": sum(row["is_promotion"] is False for row in all_rows),
        "unknown": sum(row["is_promotion"] is None for row in all_rows),
    }
    membership_sha256 = hashlib.sha256(
        "\n".join(row["source_key"] for row in all_rows).encode()
    ).hexdigest()
    return {
        "result": "success",
        "supermarket_id": SUPERMARKET_ID,
        "location_id": LOCATION_ID,
        "city": CITY,
        "currency": CURRENCY,
        "scope": SCOPE,
        "store_id": STORE_ID,
        "store_name": store["name"],
        "office_code": OFFICE_CODE,
        "location_one_code": LOCATION_ONE_CODE,
        "location_two_code": LOCATION_TWO_CODE,
        "location_verified_same_run": True,
        "catalog_complete": True,
        "validation_passed": True,
        "observed_at_utc": observed_at,
        "catalog_products_reported": total,
        "unique_products_extracted": total,
        "membership_count": total,
        "membership_sha256": membership_sha256,
        "skus_extracted": len(all_rows),
        "skus_with_price": sum(row["current_price"] is not None for row in all_rows),
        "availability_counts": availability_counts,
        "promotion_counts": promotion_counts,
        "products": all_rows,
        "source_details": all_details,
        "page_evidence": page_evidence,
        "request_count": request_count,
        "retry_count": retry_count,
    }
