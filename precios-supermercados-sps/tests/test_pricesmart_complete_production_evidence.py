import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/turso-production/2026-09-02-pricesmart-complete"


def test_pricesmart_complete_production_evidence_is_reproducible():
    spec = importlib.util.spec_from_file_location("pricesmart_complete_production", REPORT / "verify.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    evidence = module.reproduce()
    assert evidence == json.loads((REPORT / "evidence.json").read_text())
    assert evidence["decision"]["status"] == "PRICESMART_COMPLETE_IN_PRODUCTION"
    assert sum(row["history_opened"] for row in evidence["loads"]) == 9902
    assert all(row["history_closed"] == 0 for row in evidence["loads"])
    assert evidence["final_state"]["pricesmart"]["current_periods"] == 12156
    assert evidence["usage"]["after_florencia_and_final_verification"]["remaining_rows_written"] == 9368649
