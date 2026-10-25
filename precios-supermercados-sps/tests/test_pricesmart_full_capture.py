import copy
import gzip
import hashlib
import importlib.util
import json
import tarfile
from pathlib import Path

import pytest

from _evidencia import asegurar  # noqa: E402
from precios_supermercados.scrapers.pricesmart import (
    CLUBS,
    PriceSmartError,
    parse_documents,
    reconcile_capture,
)

asegurar("reports/pricesmart/2026-09-01-full/raw-capture.tar.gz")

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/pricesmart/2026-09-01-full"
FIXTURES = Path(__file__).parent / "fixtures/pricesmart"

def fixture(club="6603"):
    return json.loads((FIXTURES / f"documents_{club}.json").read_text())

def extract_capture(tmp_path):
    with tarfile.open(REPORT / "raw-capture.tar.gz", "r:gz") as archive:
        members = archive.getmembers()
        assert all(member.isfile() and not Path(member.name).is_absolute() and ".." not in Path(member.name).parts for member in members)
        archive.extractall(tmp_path, filter="data")
    return tmp_path / "live"

def test_fixtures_are_source_exact_and_cover_commercial_shapes(tmp_path):
    live = extract_capture(tmp_path)
    ledger = json.loads((live / "ledger.json").read_text())
    for club in CLUBS:
        value = fixture(club)
        source = {}
        for record in ledger["attempts"]:
            if record["club"] != club:
                continue
            wrapper = json.loads((live / record["response_file"]).read_text())
            for doc in json.loads(wrapper["body_raw"])["response"]["docs"]:
                source[doc["pid"]] = doc
        assert all(doc == source[doc["pid"]] for doc in value["documents"])
        rows, details = parse_documents(value["documents"], club)
        assert len(rows) == len(details) == 7
        by_sku = {row["source_key"]: row for row in rows}
        assert by_sku["479223"]["current_price"] == "359.95"
        assert by_sku["479223"]["is_promotion"] is False
        assert by_sku["479223"]["availability"] == "in_stock"
        assert by_sku["464663"]["current_price"] is None
        assert by_sku["464663"]["is_promotion"] is None
        assert by_sku["464663"]["availability"] == "out_of_stock"
        assert by_sku["99246"]["is_promotion"] is True
        assert by_sku["99246"]["reported_regular_price"] is not None
        assert {key for key in by_sku if key.startswith("317825-")} == {
            "317825-8000500142943", "317825-8000500142967", "317825-8000500142981"
        }
        assert by_sku["317825"]["current_price"] is None

@pytest.mark.parametrize("kind", ["partial_promo", "saving", "available_unpriced", "mismatch", "club"])
def test_parser_fails_closed_on_semantic_corruption(kind):
    documents = copy.deepcopy(fixture()["documents"])
    promo = next(doc for doc in documents if doc["pid"] == "99246")
    unpriced = next(doc for doc in documents if doc["pid"] == "464663")
    if kind == "partial_promo":
        del promo["saving_amount_HN_6603"]
        del promo["variants"][0]["saving_amount_HN_6603"]
    elif kind == "saving":
        promo["saving_amount_HN_6603"] = "-1.0"
        promo["variants"][0]["saving_amount_HN_6603"] = ["-1.0"]
    elif kind == "available_unpriced":
        unpriced["availability_HN_6603"] = "true"
        unpriced["inventory_HN_6603"] = "in stock"
        unpriced["variants"][0].update(availability_HN_6603=["true"], inventory_HN_6603=["in stock"])
    elif kind == "mismatch":
        promo["variants"][0]["price_HN_6603"] = [1]
    with pytest.raises(PriceSmartError):
        parse_documents(documents, "6604" if kind == "club" else "6603")

def test_full_raw_hashes_pagination_snapshots_and_budget(tmp_path):
    live = extract_capture(tmp_path)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    for item in manifest["files"]:
        raw = (tmp_path / item["path"]).read_bytes()
        assert len(raw) == item["bytes"]
        assert hashlib.sha256(raw).hexdigest() == item["sha256"]
    snapshots = reconcile_capture(live)
    assert [snap["club_id"] for snap in snapshots] == ["6603", "6602"]
    assert all(snap["catalog_products_reported"] == 1124 for snap in snapshots)
    assert all(snap["unique_products_extracted"] == 1124 for snap in snapshots)
    assert all(snap["skus_extracted"] == 1127 for snap in snapshots)
    assert [snap["skus_with_price"] for snap in snapshots] == [1080, 1074]
    assert [snap["promotion_counts"]["true"] for snap in snapshots] == [18, 17]
    assert snapshots[0]["membership_sha256"] == snapshots[1]["membership_sha256"]
    assert snapshots[0]["sku_membership_sha256"] == snapshots[1]["sku_membership_sha256"]
    for snapshot in snapshots:
        raw = gzip.decompress((REPORT / f"{snapshot['location_id']}.json.gz").read_bytes())
        assert json.loads(raw) == snapshot
    result = json.loads((live / "result.json").read_text())
    assert result["post_attempts"] == 188
    assert result["retries"] == 0
    assert result["remaining_budget"] == 20
    assert result["elapsed_seconds"] < 1800 and result["excluded_club"] == "6604"
    assert manifest["tls_preflight"]["http_posts_observed"] == 0

def test_published_evidence_is_exactly_reproducible():
    spec = importlib.util.spec_from_file_location("pricesmart_report_verify", REPORT / "verify.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.reproduce() == json.loads((REPORT / "evidence.json").read_text())

def test_full_reconciliation_rejects_one_changed_raw_byte(tmp_path):
    live = extract_capture(tmp_path)
    ledger = json.loads((live / "ledger.json").read_text())
    path = live / ledger["attempts"][40]["response_file"]
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(PriceSmartError, match="raw_file_hash_mismatch"):
        reconcile_capture(live)
