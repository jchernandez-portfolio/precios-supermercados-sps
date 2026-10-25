"""Fail closed on unpriced discovery evidence; never contact a live source."""
import importlib.util
import io
import json
import socket
import tarfile
from pathlib import Path

import pytest

REPORT = Path(__file__).resolve().parents[1] / "reports/maxi-df/2026-08-31-probe"
spec = importlib.util.spec_from_file_location("maxi_df_probe", REPORT / "verify.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def raw(name):
    with tarfile.open(REPORT / "raw-capture.tar.gz") as archive:
        return archive.extractfile(name).read().decode()


def test_reproduce_with_network_forbidden(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline evidence must not open a socket")
    monkeypatch.setattr(socket, "socket", forbidden)
    evidence = probe.reproduce(REPORT / "raw-capture.tar.gz")
    assert evidence == json.loads((REPORT / "evidence.json").read_text())
    assert evidence["metrics"]["unique_code_candidates"] == 96
    assert evidence["codes_shared_between_listing_templates"] == ["70751700208"]
    assert evidence["technical_verdict"] == "NO-GO TEMPORAL PARA PRICE TRACKING WEB"


def test_unpriced_campaign_does_not_become_promotion_or_stock():
    rows, comments = probe.inspect_products(raw("06.raw"), 6)
    assert len(rows) == comments == 90
    assert all(r["current_price"] is None and r["reported_regular_price"] is None
               and r["is_promotion"] is None and r["availability"] == "unknown"
               and r["supermarket_id"] is None and r["location_id"] is None
               and not r["accepted_for_persistence"] for r in rows)


def test_unknowns_do_not_count_as_commercial_equivalence():
    comparison = probe.reproduce(REPORT / "raw-capture.tar.gz")["commercial_comparison"]
    assert comparison["comparable_skus"] == 0
    assert all(comparison[k] is None for k in ("current_price_differences",
               "reported_regular_price_differences", "promotion_differences",
               "availability_only_differences"))
    assert comparison["decision"] == "unresolved_no_comparable_commercial_contexts"


@pytest.mark.parametrize("index", [6, 19])
def test_uncommented_price_requires_reassessment(index):
    body = raw(f"{index:02}.raw")
    changed = body.replace('<!--<div class="precio_producto">Q20.00 c/u</div>',
                           '<div class="precio_producto">Q20.00 c/u</div><!--', 1)
    assert changed != body
    with pytest.raises(ValueError, match="Observed price markup"):
        probe.inspect_products(changed, index)


def test_visible_code_cannot_be_replaced_by_different_url_identity():
    body = raw("06.raw").replace("Código: 85041800708", "Código: 00000000000", 1)
    with pytest.raises(ValueError, match="Visible code differs"):
        probe.inspect_products(body, 6)


def test_locator_format_filter_is_not_commercial_store_identity():
    stores, formats = probe.inspect_locator(raw("03.raw"))
    assert formats == {"4": "Despensa", "6": "Maxi Despensa"}
    assert len(stores) == 99
    assert all(set(s) == {"title", "formato", "ubicacion", "horario", "geometry"} for s in stores)


def test_corrupted_raw_fails_before_evidence_acceptance(tmp_path):
    changed = tmp_path / "changed.tar.gz"
    with tarfile.open(REPORT / "raw-capture.tar.gz") as source, tarfile.open(changed, "w:gz") as target:
        for member in source:
            body = source.extractfile(member).read()
            if member.name == "06.raw":
                body = body.replace(b"85041800708", b"85041800709", 1)
            target.addfile(member, io.BytesIO(body))
    with pytest.raises(ValueError, match="Published RAW hash mismatch"):
        probe.reproduce(changed)
