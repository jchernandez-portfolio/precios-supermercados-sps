"""PriceSmart HN: parse and reconcile a complete club-bound Bloomreach capture."""
from __future__ import annotations
import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
ENDPOINT = "https://www.pricesmart.com/api/br_discovery/getProductsByKeyword"
CATEGORY_URL = "https://www.pricesmart.com/es-hn/categoria/Alimentos-G10D03/G10D03"
CATEGORY_ID = "G10D03"
CATEGORY_NAME = "Alimentos"
ROWS = 12
CATALOG_PRODUCTS = 1124
CLUBS = {
    "6603": {
        "location_id": "pricesmart_sps",
        "city": "San Pedro Sula",
        "name": "San Pedro Sula",
        "channel_id": "83a01076-4a4e-4163-9786-c59ef7c7c1a6",
    },
    "6602": {
        "location_id": "pricesmart_tgu",
        "city": "Tegucigalpa",
        "name": "Florencia",
        "channel_id": "93a6de43-d3c7-4887-a824-44c565dc3101",
    },
}
SCOPE = "public_ecommerce_club_bound_G10D03"
COMPLETE_CATALOG_SCOPE = "public_ecommerce_club_bound_all_departments"
class PriceSmartError(ValueError):
    pass
def require(condition: object, reason: str) -> None:
    if not condition:
        raise PriceSmartError(reason)
def _decimal(value: object, reason: str) -> Decimal:
    require(type(value) in {int, float, str}, reason)
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise PriceSmartError(reason) from exc
    require(result.is_finite(), reason)
    return result
def _minor_price(value: object) -> tuple[int, str]:
    require(type(value) is int and value > 0, "current_price_invalid")
    return value, format(Decimal(value) / 100, ".2f")
def _regular_price(value: object) -> tuple[Decimal, str]:
    amount = _decimal(value, "regular_price_invalid")
    require(amount > 0 and amount * 100 == (amount * 100).to_integral_value(), "regular_price_invalid")
    return amount, format(amount, ".2f")
def _variant_value(variant: dict, key: str) -> object | None:
    if key not in variant:
        return None
    value = variant[key]
    require(isinstance(value, list) and len(value) == 1, "variant_field_shape_invalid")
    return value[0]
def _commercial_value(doc: dict, variant: dict, key: str) -> object | None:
    variant_value = _variant_value(variant, key)
    document_value = doc.get(key)
    if variant_value is not None and document_value is not None:
        require(variant_value == document_value, "variant_document_value_mismatch")
    return variant_value if variant_value is not None else document_value
def parse_documents(documents: list[dict], club: str) -> tuple[list[dict], dict[str, dict]]:
    require(club in CLUBS, "club_invalid")
    require(isinstance(documents, list) and documents, "documents_invalid")
    rows: list[dict] = []
    details: dict[str, dict] = {}
    product_ids: set[str] = set()
    for doc in documents:
        require(isinstance(doc, dict), "document_invalid")
        pid, title = doc.get("pid"), doc.get("title")
        require(isinstance(pid, str) and pid.isdigit() and int(pid) > 0, "product_id_invalid")
        require(pid not in product_ids, "product_id_duplicate")
        require(isinstance(title, str) and title.strip(), "title_invalid")
        require(doc.get("currency") == "HNL" and doc.get("fractionDigits") == 2, "currency_invalid")
        slug = doc.get("slug")
        require(isinstance(slug, str) and re.fullmatch(r"[A-Za-z0-9-]+", slug), "slug_invalid")
        brand = doc.get("brand")
        require(brand is None or isinstance(brand, str), "brand_invalid")
        variants = doc.get("variants")
        require(isinstance(variants, list) and variants, "variants_missing")
        product_ids.add(pid)
        for variant in variants:
            require(isinstance(variant, dict), "variant_invalid")
            sku = variant.get("skuid")
            require(isinstance(sku, str) and sku and sku not in details, "sku_invalid_or_duplicate")
            price_key = f"price_HN_{club}"
            regular_key = f"original_price_without_saving_HN_{club}"
            saving_key = f"saving_amount_HN_{club}"
            available_key = f"availability_HN_{club}"
            inventory_key = f"inventory_HN_{club}"
            price_value = _commercial_value(doc, variant, price_key)
            regular_value = _commercial_value(doc, variant, regular_key)
            saving_value = _commercial_value(doc, variant, saving_key)
            available_value = _commercial_value(doc, variant, available_key)
            inventory_value = _commercial_value(doc, variant, inventory_key)
            require(available_value in {None, "true", "false"}, "availability_signal_invalid")
            require(inventory_value in {None, "in stock", "out of stock"}, "inventory_signal_invalid")
            availability = "in_stock" if available_value == "true" and inventory_value == "in stock" else "out_of_stock"
            current_price: str | None = None
            reported_regular: str | None = None
            is_promotion: bool | None = None
            promotion_evidence = "unpriced"
            if price_value is not None:
                current_minor, current_price = _minor_price(price_value)
                if regular_value is None and saving_value is None:
                    is_promotion = False
                    promotion_evidence = "requested_fields_absent"
                else:
                    require(regular_value is not None and saving_value is not None, "promotion_fields_partial")
                    regular_amount, reported_regular = _regular_price(regular_value)
                    saving_amount = _decimal(saving_value, "saving_amount_invalid")
                    current_amount = Decimal(current_minor) / 100
                    require(regular_amount > current_amount and saving_amount < 0, "promotion_relationship_invalid")
                    require(abs((current_amount - regular_amount) - saving_amount) <= Decimal("0.01"), "saving_amount_mismatch")
                    is_promotion = True
                    promotion_evidence = "regular_and_negative_saving_declared"
            else:
                require(regular_value is None and saving_value is None, "unpriced_promotion_invalid")
                require(availability == "out_of_stock", "unpriced_available_invalid")
            row = {
                "product_id": pid,
                "item_id": sku,
                "source_key_type": "item_id",
                "source_key": sku,
                "source_name": title.strip(),
                "brand": brand or None,
                "reference": sku,
                # Bloomreach no expone barcode/GTIN/UPC en los campos `fl` usados ni en
                # las fixtures. Las variantes `<pid>-<13 dígitos>` superan el check digit
                # GS1, pero son SKU fuente y en la captura completa 2026-09-02 no
                # coinciden con ningún GTIN de Walmart/Paiz/Colonial: no se reinterpretan.
                "ean": None,
                "category": CATEGORY_NAME,
                "presentation": None,
                "current_price": current_price,
                "reported_regular_price": reported_regular,
                "is_promotion": is_promotion,
                "availability": availability,
            }
            rows.append(row)
            details[sku] = {
                "product_id": pid,
                "master_sku": doc.get("master_sku"),
                "source_price_minor": price_value,
                "source_regular_price": regular_value,
                "source_saving_amount": saving_value,
                "source_availability": available_value,
                "source_inventory": inventory_value,
                "promotion_evidence": promotion_evidence,
                "slug": slug,
                "image_url": doc.get("thumb_image"),
                "campaign_ids": doc.get("promoid_HN", []),
            }
    return rows, details


def parse_catalog_memberships(
    category_documents: list[dict], club: str
) -> tuple[list[dict], dict[str, dict], dict]:
    """Deduplicate root memberships and parse one complete club catalog.

    The public taxonomy is not a partition: a product can belong to more than one
    root. Duplicate memberships must carry the exact same source document. The
    returned rows retain every root name in a deterministic, human-readable
    category string and every root id/name in source_details.
    """
    require(club in CLUBS, "club_invalid")
    require(isinstance(category_documents, list) and category_documents, "category_documents_invalid")
    documents_by_pid: dict[str, dict] = {}
    memberships: dict[str, list[tuple[str, str]]] = {}
    raw_memberships = 0
    for group in category_documents:
        require(isinstance(group, dict), "category_group_invalid")
        category_id = group.get("category_id")
        category_name = group.get("category_name")
        documents = group.get("documents")
        require(isinstance(category_id, str) and category_id, "category_id_invalid")
        require(isinstance(category_name, str) and category_name, "category_name_invalid")
        require(isinstance(documents, list) and documents, "category_documents_invalid")
        for document in documents:
            require(isinstance(document, dict), "document_invalid")
            pid = document.get("pid")
            require(isinstance(pid, str) and pid, "product_id_invalid")
            raw_memberships += 1
            if pid in documents_by_pid:
                require(documents_by_pid[pid] == document, "cross_category_document_mismatch")
            else:
                documents_by_pid[pid] = document
            pair = (category_id, category_name)
            memberships.setdefault(pid, [])
            if pair not in memberships[pid]:
                memberships[pid].append(pair)

    rows, details = parse_documents(list(documents_by_pid.values()), club)
    for row in rows:
        pairs = memberships[row["product_id"]]
        row["category"] = " | ".join(name for _, name in pairs)
        detail = details[row["source_key"]]
        detail["category_ids"] = [category_id for category_id, _ in pairs]
        detail["category_names"] = [name for _, name in pairs]
    return rows, details, {
        "root_memberships": raw_memberships,
        "unique_products": len(documents_by_pid),
        "cross_root_products": sum(len(pairs) > 1 for pairs in memberships.values()),
        "duplicate_product_memberships": raw_memberships - len(documents_by_pid),
        "sku_memberships": sum(len(document.get("variants") or []) for group in category_documents for document in group["documents"]),
        "unique_skus": len(rows),
        "duplicate_sku_memberships": (
            sum(len(document.get("variants") or []) for group in category_documents for document in group["documents"])
            - len(rows)
        ),
    }
def _load_hashed(path: Path, expected: str) -> dict:
    require(path.is_file(), "raw_file_missing")
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == expected, "raw_file_hash_mismatch")
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PriceSmartError("raw_json_invalid") from exc
def reconcile_capture(directory: str | Path) -> list[dict]:
    directory = Path(directory)
    ledger = json.loads((directory / "ledger.json").read_text())
    result = json.loads((directory / "result.json").read_text())
    require(
        result.get("complete") is True
        and result.get("clubs") == ["6603", "6602"]
        and result.get("excluded_club") == "6604"
        and result.get("post_attempts") == 188
        and result.get("retries") == 0
        and result.get("successful_pages") == 188
        and result.get("remaining_budget") == 20
        and result.get("expected_total_by_club") == {"6603": CATALOG_PRODUCTS, "6602": CATALOG_PRODUCTS}
        and result.get("elapsed_seconds", 1801) <= 1800,
        "capture_result_invalid",
    )
    attempts = ledger.get("attempts")
    require(isinstance(attempts, list) and len(attempts) == 188, "capture_ledger_invalid")
    require(ledger.get("aborted_reason") is None, "capture_aborted")
    snapshots = []
    for club, config in CLUBS.items():
        records = [record for record in attempts if record.get("club") == club]
        require(len(records) == 94, "club_page_count_invalid")
        require({record.get("start") for record in records} == set(range(0, 1128, 12)), "club_offsets_invalid")
        documents: list[dict] = []
        page_evidence = []
        observed = []
        for record in sorted(records, key=lambda value: value["start"]):
            require(
                record.get("phase") == "base"
                and record.get("http_status") == 200
                and record.get("valid") is True
                and record.get("rows") == ROWS,
                "page_ledger_invalid",
            )
            request_path = directory / record["request_file"]
            response_path = directory / record["response_file"]
            request = _load_hashed(request_path, record["request_sha256"])
            response_wrapper = _load_hashed(response_path, record["response_sha256"])
            require(
                request.get("method") == "POST"
                and request.get("url") == ENDPOINT
                and request.get("cookie_header_present") is False
                and request.get("headers", {}).get("Referer") == CATEGORY_URL,
                "request_scope_invalid",
            )
            body_raw = request.get("body_raw")
            require(isinstance(body_raw, str) and hashlib.sha256(body_raw.encode()).hexdigest() == request.get("body_sha256"), "request_body_hash_invalid")
            query_batch = json.loads(body_raw)
            require(isinstance(query_batch, list) and len(query_batch) == 1, "request_batch_invalid")
            query = query_batch[0]
            fields = set(query.get("fl", "").split(","))
            required_fields = {
                f"price_HN_{club}", f"availability_HN_{club}", f"inventory_HN_{club}",
                f"saving_amount_HN_{club}", f"original_price_without_saving_HN_{club}",
            }
            require(
                query.get("url") == CATEGORY_URL
                and query.get("q") == CATEGORY_ID
                and query.get("view_id") == "HN"
                and query.get("start") == record["start"]
                and query.get("rows") == ROWS
                and required_fields.issubset(fields)
                and not any("_HN_6604" in field for field in fields),
                "request_binding_invalid",
            )
            response_raw = response_wrapper.get("body_raw")
            require(
                response_wrapper.get("status") == 200
                and isinstance(response_raw, str)
                and hashlib.sha256(response_raw.encode()).hexdigest() == response_wrapper.get("body_sha256")
                and response_wrapper.get("body_sha256") == record.get("response_body_sha256"),
                "response_hash_invalid",
            )
            payload = json.loads(response_raw)
            response = payload["response"]
            expected_count = 8 if record["start"] == 1116 else ROWS
            require(
                response.get("numFound") == CATALOG_PRODUCTS
                and response.get("start") == record["start"]
                and len(response.get("docs", [])) == expected_count,
                "response_page_invalid",
            )
            if record["start"] == 0:
                root_facets = payload.get("facet_counts", {}).get("facet_fields", {}).get("category", [])
                root = [item for item in root_facets if item.get("cat_id") == CATEGORY_ID]
                require(len(root) == 1 and root[0].get("cat_name") == CATEGORY_NAME and root[0].get("count") == CATALOG_PRODUCTS, "category_facet_invalid")
            documents.extend(response["docs"])
            observed.extend((request["observed_at_utc"], response_wrapper["finished_at_utc"]))
            page_evidence.append({
                "start": record["start"],
                "request_sha256": request["body_sha256"],
                "response_sha256": response_wrapper["body_sha256"],
                "response_bytes": response_wrapper["body_bytes"],
            })
        product_ids = [document["pid"] for document in documents]
        require(len(product_ids) == CATALOG_PRODUCTS and len(set(product_ids)) == CATALOG_PRODUCTS, "catalog_membership_incomplete")
        rows, source_details = parse_documents(documents, club)
        require(len(rows) == 1127 and len(source_details) == 1127, "sku_membership_incomplete")
        rows.sort(key=lambda row: row["source_key"])
        product_membership = hashlib.sha256("\n".join(sorted(product_ids)).encode()).hexdigest()
        sku_membership = hashlib.sha256("\n".join(row["source_key"] for row in rows).encode()).hexdigest()
        availability_counts = dict(Counter(row["availability"] for row in rows))
        promotion_counts = {
            "true": sum(row["is_promotion"] is True for row in rows),
            "false": sum(row["is_promotion"] is False for row in rows),
            "unknown_unpriced": sum(row["is_promotion"] is None for row in rows),
        }
        timestamps = sorted(datetime.fromisoformat(value.replace("Z", "+00:00")) for value in observed)
        snapshots.append({
            "result": "success",
            "supermarket_id": "pricesmart",
            "location_id": config["location_id"],
            "city": config["city"],
            "club_id": club,
            "club_name": config["name"],
            "channel_id": config["channel_id"],
            "currency": "HNL",
            "scope": SCOPE,
            "category_id": CATEGORY_ID,
            "category_name": CATEGORY_NAME,
            "catalog_complete": True,
            "validation_passed": True,
            "location_verified_same_run": True,
            "observation_started_at_utc": timestamps[0].isoformat().replace("+00:00", "Z"),
            "observed_at_utc": timestamps[-1].isoformat().replace("+00:00", "Z"),
            "catalog_products_reported": CATALOG_PRODUCTS,
            "unique_products_extracted": CATALOG_PRODUCTS,
            "skus_extracted": len(rows),
            "skus_with_price": sum(row["current_price"] is not None for row in rows),
            "membership_count": CATALOG_PRODUCTS,
            "membership_sha256": product_membership,
            "sku_membership_sha256": sku_membership,
            "availability_counts": availability_counts,
            "promotion_counts": promotion_counts,
            "products": rows,
            "source_details": source_details,
            "page_evidence": page_evidence,
        })
    require(snapshots[0]["membership_sha256"] == snapshots[1]["membership_sha256"], "club_product_membership_mismatch")
    require(snapshots[0]["sku_membership_sha256"] == snapshots[1]["sku_membership_sha256"], "club_sku_membership_mismatch")
    return snapshots
