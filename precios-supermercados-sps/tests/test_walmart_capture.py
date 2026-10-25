"""Reproduce the accepted capture offline and reject actual acquisition defects."""
import copy
import gzip
import hashlib
import json
import socket
import sys
import tarfile
from pathlib import Path

import pytest
import requests

from precios_supermercados.scrapers.walmart import WalmartError, parse_products, reconcile_capture

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/walmart/2026-08-31-full"
sys.path.insert(0, str(ROOT / "scripts"))
import actualizar_mvp_sqlite_la_colonia as local
from _evidencia import asegurar  # noqa: E402

asegurar("reports/walmart/2026-08-31-full/raw-capture.tar.gz")

@pytest.fixture(scope="module")
def capture(tmp_path_factory):
    evidence = json.loads((REPORT / "evidence.json").read_text())
    for name, expected in evidence["artifacts"].items():
        raw = (REPORT/name).read_bytes()
        assert len(raw) == expected["bytes"]
        assert hashlib.sha256(raw).hexdigest() == expected["sha256"]
    directory = tmp_path_factory.mktemp("walmart-capture")
    with tarfile.open(REPORT / "raw-capture.tar.gz") as tar:
        tar.extractall(directory, filter="data")
    ledger = json.loads((directory/"requests.json").read_text())
    assert ledger["closed"] is True
    assert len(ledger["records"]) == 514 <= ledger["max_requests"]
    assert ledger["retries"] == 0 <= ledger["max_retries"]
    assert ledger["elapsed_seconds"] < ledger["max_seconds"] and ledger["concurrency"] == 1
    return directory

def test_complete_raw_reproduces_all_three_snapshots_without_http(capture, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("offline acceptance attempted network")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(requests.Session, "request", forbidden)
    expected = {s["location_id"]: s for s in json.loads((REPORT/"evidence.json").read_text())["snapshots"]}
    snapshots = reconcile_capture(capture)
    assert len(snapshots) == 3
    assert sum(len(s["page_evidence"]) for s in snapshots) == 478
    for snap in snapshots:
        raw = json.dumps(snap, ensure_ascii=False, separators=(",", ":")).encode()
        entry = expected[snap["location_id"]]
        assert hashlib.sha256(raw).hexdigest() == entry["json_sha256"]
        assert raw == gzip.decompress((REPORT/entry["file"]).read_bytes())
        local.validate_snapshot_bytes(raw, supermarket_id="walmart")
    by_location = {s["location_id"]: {r["source_key"]: r for r in s["products"]} for s in snapshots}
    assert by_location["walmart_sps"]["37305"]["current_price"] == "9.50"
    a, b = (by_location[k]["68100"] for k in ["walmart_tgu_ffaa", "walmart_tgu_el_sauce"])
    assert (a["current_price"], a["reported_regular_price"], a["is_promotion"]) == ("1895.00", "2195.00", True)
    assert (b["current_price"], b["reported_regular_price"], b["is_promotion"]) == ("1895.00", "1895.00", False)
    assert sum(r["current_price"] is None for s in snapshots for r in s["products"]) == 1597

@pytest.mark.parametrize("file,reason", [
    ("recovery.json", "page_count_or_duplicate"),
    ("partition-recovery.json", "product_membership_overlap"),
])
def test_primary_capture_is_rejected_without_required_membership_repair(capture, monkeypatch, file, reason):
    original = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda p, *a, **kw: "[]" if p == capture/file else original(p, *a, **kw))
    with pytest.raises(WalmartError, match=reason):
        reconcile_capture(capture)

def test_changed_raw_is_not_accepted(capture, monkeypatch):
    original = Path.read_bytes
    target = capture/json.loads((capture/"requests.json").read_text())["records"][0]["file"]
    monkeypatch.setattr(Path, "read_bytes", lambda p: b"altered" if p == target else original(p))
    with pytest.raises(WalmartError, match="raw_hash_mismatch"):
        reconcile_capture(capture)

def source_product():
    return {"productId": "10", "productName": "Producto", "brand": "Marca",
        "categories": ["/Frescos/"], "items": [{"itemId": "100", "name": "Variante",
        "ean": "1234567890123", "referenceId": [{"Key": "RefId"}],
        "measurementUnit": "kg", "unitMultiplier": 0.25,
        "sellers": [{"sellerId": "1", "commertialOffer": {
            "Price": 9.5, "ListPrice": 12, "AvailableQuantity": 10000}}]}]}

def test_variants_missing_reference_and_weighted_price_preserved():
    product = source_product()
    other = copy.deepcopy(product["items"][0]); other["itemId"] = "101"
    other["sellers"][0]["commertialOffer"]["AvailableQuantity"] = None
    product["items"].append(other)
    rows, details = parse_products([product])
    assert len(rows) == 2 and all(r["current_price"] == "9.50" for r in rows)
    assert rows[0]["reference"] is None and rows[1]["availability"] == "unknown"
    assert details["100"]["unit_multiplier"] == 0.25

@pytest.mark.parametrize("mutation", ["zero_available", "negative", "fraction", "nan", "infinite_quantity", "boolean_quantity", "regular_below", "two_sellers", "duplicate", "missing_items"])
def test_ambiguous_or_invalid_offers_rejected(mutation):
    product = source_product(); item = product["items"][0]; offer = item["sellers"][0]["commertialOffer"]
    if mutation == "zero_available": offer.update(Price=0, ListPrice=0)
    elif mutation == "negative": offer["Price"] = -1
    elif mutation == "fraction": offer["Price"] = 1.001
    elif mutation == "nan": offer["Price"] = "NaN"
    elif mutation == "infinite_quantity": offer["AvailableQuantity"] = float("inf")
    elif mutation == "boolean_quantity": offer["AvailableQuantity"] = True
    elif mutation == "regular_below": offer["ListPrice"] = 1
    elif mutation == "two_sellers": item["sellers"].append(copy.deepcopy(item["sellers"][0]))
    elif mutation == "duplicate": product["items"].append(copy.deepcopy(item))
    else: product["items"] = []
    with pytest.raises(WalmartError): parse_products([product])

def test_unavailable_zero_offer_retains_identity_but_never_free_price():
    product = source_product()
    product["items"][0]["sellers"][0]["commertialOffer"].update(Price=0, ListPrice=0, AvailableQuantity=0)
    rows, details = parse_products([product])
    assert (rows[0]["current_price"], rows[0]["reported_regular_price"], rows[0]["is_promotion"]) == (None,None,None)
    assert rows[0]["availability"] == "out_of_stock" and rows[0]["item_id"] == "100"
    assert details["100"]["source_price"] == "0.00"
