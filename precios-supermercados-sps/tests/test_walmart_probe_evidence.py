"""Offline regression of captured Walmart evidence; no live integration."""
import importlib.util
import io
import json
import tarfile
from pathlib import Path
import pytest

from _evidencia import asegurar  # noqa: E402

asegurar("reports/walmart/2026-08-31-probe/raw-capture.tar.gz")

REPORT = Path(__file__).resolve().parents[1] / "reports/walmart/2026-08-31-probe"
spec = importlib.util.spec_from_file_location("walmart_probe_evidence", REPORT / "verify.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

def test_captured_probe_reproduces_regional_price_decision():
    evidence = probe.reproduce(REPORT / "raw-capture.tar.gz")
    assert evidence == json.loads((REPORT / "evidence.json").read_text())
    assert evidence["tgu_comparison"]["decision"] == "separate_commercial_contexts"
    assert len(evidence["unpriced_partition_rows"]) == 4
    assert all(row["current_price"] is None for row in evidence["unpriced_partition_rows"])
    assert evidence["first_product"]["current_price"] == "9.5"
    assert not evidence["remote_persistence"]

def test_modified_response_cannot_reproduce_trusted_evidence(tmp_path):
    changed = tmp_path / "changed.tar.gz"
    with tarfile.open(REPORT / "raw-capture.tar.gz", "r:gz") as source, tarfile.open(changed, "w:gz") as target:
        for member in source:
            body = source.extractfile(member).read()
            if member.name == "07.raw":
                body = body.replace(b'2195.0', b'1895.0', 1)
                assert body != source.extractfile(member).read()
            target.addfile(member, io.BytesIO(body))
    with pytest.raises(ValueError, match="RAW hash mismatch"):
        probe.reproduce(changed)

def test_zero_price_with_available_offer_is_not_silently_classified():
    with tarfile.open(REPORT / "raw-capture.tar.gz", "r:gz") as source:
        product = json.load(source.extractfile("02.raw"))[0]
    offer = product["items"][0]["sellers"][0]["commertialOffer"]
    offer.update(Price=0, ListPrice=0, AvailableQuantity=10000)
    with pytest.raises(ValueError, match="Ambiguous zero-price offer"):
        probe.rows([product])
