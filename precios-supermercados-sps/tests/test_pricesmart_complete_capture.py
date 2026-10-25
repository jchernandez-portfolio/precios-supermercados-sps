import copy
import gzip
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/pricesmart/2026-09-02-complete"
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

from actualizar_mvp_sqlite_la_colonia import validate_snapshot_bytes
from precios_supermercados.scrapers.pricesmart import PriceSmartError, parse_catalog_memberships


def load_verifier():
    spec = importlib.util.spec_from_file_location("pricesmart_complete", REPORT / "verify.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_complete_report_is_reproducible_and_exact():
    evidence = load_verifier().reproduce()
    assert evidence == json.loads((REPORT / "evidence.json").read_text())
    assert {key: evidence["capture"][key] for key in (
        "complete", "post_attempts_this_run", "retries", "returned_documents"
    )} == {
        "complete": True,
        "post_attempts_this_run": 50,
        "retries": 0,
        "returned_documents": 3306,
    }
    assert evidence["completeness"] == {
        "taxonomy_roots": 26,
        "nonempty_roots": 24,
        "empty_roots": 2,
        "reused_alimentos_products_per_club": 1124,
        "remaining_root_memberships_per_club": 1653,
        "all_root_memberships_per_club": 2777,
        "unique_products_per_club": 2766,
        "all_root_sku_memberships_per_club": 6115,
        "unique_skus_per_club": 6078,
        "cross_root_products_per_club": 11,
        "duplicate_product_memberships_per_club": 11,
        "duplicate_sku_memberships_per_club": 37,
        "pagination_holes": 0,
        "unexpected_page_repeats": 0,
    }
    assert evidence["sps_vs_florencia"]["shared_skus"] == 6078
    assert evidence["sps_vs_florencia"]["both_priced_price_differences"] == 115
    assert evidence["offline_persistence"]["delta_per_location"]["new_sku_offers"] == 4951
    assert evidence["offline_persistence"]["turso_access"] is False


@pytest.mark.parametrize("location", ["pricesmart_sps", "pricesmart_tgu"])
def test_complete_snapshot_contract(location):
    raw = gzip.decompress((REPORT / f"{location}.json.gz").read_bytes())
    snapshot = validate_snapshot_bytes(raw, supermarket_id="pricesmart")
    assert snapshot["scope"] == "public_ecommerce_club_bound_all_departments"
    assert snapshot["unique_products_extracted"] == 2766
    assert snapshot["skus_extracted"] == 6078


def test_cross_root_memberships_deduplicate_only_identical_documents():
    document = {
        "pid": "1",
        "title": "Producto",
        "currency": "HNL",
        "fractionDigits": 2,
        "slug": "producto",
        "brand": None,
        "variants": [{"skuid": "sku-1"}],
        "price_HN_6603": 1234,
        "availability_HN_6603": "true",
        "inventory_HN_6603": "in stock",
    }
    groups = [
        {"category_id": "A", "category_name": "Uno", "documents": [document]},
        {"category_id": "B", "category_name": "Dos", "documents": [copy.deepcopy(document)]},
    ]
    rows, details, summary = parse_catalog_memberships(groups, "6603")
    assert len(rows) == 1 and rows[0]["category"] == "Uno | Dos"
    assert details["sku-1"]["category_ids"] == ["A", "B"]
    assert summary == {
        "root_memberships": 2,
        "unique_products": 1,
        "cross_root_products": 1,
        "duplicate_product_memberships": 1,
        "sku_memberships": 2,
        "unique_skus": 1,
        "duplicate_sku_memberships": 1,
    }
    broken = copy.deepcopy(groups)
    broken[1]["documents"][0]["title"] = "Distinto"
    with pytest.raises(PriceSmartError, match="cross_category_document_mismatch"):
        parse_catalog_memberships(broken, "6603")
