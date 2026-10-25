import importlib.util
import json
import socket
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "pricesmart" / "2026-09-01-browser-request-probe"


def module():
    spec = importlib.util.spec_from_file_location("pricesmart_browser_request_verify", REPORT / "verify.py")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_reproduction_is_offline_and_exact(monkeypatch):
    monkeypatch.setattr(socket, "socket", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    assert module().reproduce() == json.loads((REPORT / "evidence.json").read_text())


def test_browser_request_and_replay_are_public_and_identical():
    evidence = module().reproduce()
    assert evidence["request"]["endpoint"].endswith("/api/br_discovery/getProductsByKeyword")
    assert evidence["request"]["method"] == "POST"
    assert evidence["request"]["cookie_required"] is False
    assert set(evidence["request"]["headers"]) == {"Accept", "Content-Type", "Referer"}
    assert evidence["capture"]["response_exact_match"] is True
    assert evidence["capture"]["direct_replays"] == 1


def test_country_price_identity_and_availability_are_demonstrated():
    evidence = module().reproduce()
    product = evidence["control_product"]
    assert product["sku"] == product["pid"] == product["master_sku"] == "479223"
    assert product["currency"] == "HNL"
    assert product["current_price_minor"] == 35995
    assert product["current_price"] == 359.95
    assert product["visible_browser_price"] == "L 359.95"
    assert product["availability"] == "in_stock"


def test_regular_price_and_price_promotion_are_not_inferred():
    product = module().reproduce()["control_product"]
    assert product["reported_regular_price"] is None
    assert product["reported_regular_price_declared"] is False
    assert product["is_promotion"] is None
    assert product["price_promotion_declared"] is False
    assert product["campaign_ids"]
    assert product["campaign_ids_are_not_price_promotion_proof"] is True


def test_sps_binding_and_tgu_granularity_are_not_claimed():
    evidence = module().reproduce()
    clubs = evidence["club_assessment"]
    assert evidence["request"]["country_binding"] == "HN"
    assert evidence["request"]["club_binding"] is None
    assert clubs["sps_request_binding_demonstrated"] is False
    assert clubs["club_specific_price_facet_names_observed"] is True
    assert clubs["club_specific_price_values_returned_for_products"] is False
    assert clubs["comparable_skus_between_clubs"] == 0
    assert clubs["tgu_granularity"] is None


def test_protocol_overrun_blocks_production_and_full():
    evidence = module().reproduce()
    assert evidence["capture"]["duration_cap_compliant"] is False
    assert evidence["capture"]["duration_overrun_seconds"] == 27.919
    assert evidence["decision"]["outcome"] == "B"
    assert evidence["decision"]["public_hn_catalog_price_source_demonstrated"] is True
    assert evidence["decision"]["sps_club_price_source_demonstrated"] is False
    assert evidence["decision"]["production_scraper"] is False
    assert evidence["decision"]["full_crawl"] is False
    assert evidence["decision"]["persistence"] is False
    assert evidence["decision"]["turso_access"] is False
