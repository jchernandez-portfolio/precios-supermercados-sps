from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "obtener_catalogo_sps_la_colonia_operativo_v2.py"
SPEC = importlib.util.spec_from_file_location("obtener_catalogo_sps_la_colonia_operativo_v2", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_transient_graphql_failure_retries_once_from_zero(monkeypatch, tmp_path: Path) -> None:
    output = tmp_path / "full-catalog.json"
    calls: list[list[str] | None] = []
    sleeps: list[float] = []

    def fake_main(argv):
        calls.append(argv)
        if len(calls) == 1:
            output.write_text(
                json.dumps({"result": "stopped", "reason": "product_search_graphql_errors"}),
                encoding="utf-8",
            )
            return 3
        output.write_text(json.dumps({"result": "success"}), encoding="utf-8")
        return 0

    monkeypatch.setattr(module.operational, "main", fake_main)
    monkeypatch.setattr(module.time, "sleep", lambda seconds: sleeps.append(seconds))

    argv = ["--live-read-only", "--allow-full-catalog", "--output", str(output)]
    assert module.main(argv) == 0
    assert calls == [argv, argv]
    assert sleeps == [module.RUN_RETRY_DELAY_SECONDS]


def test_structural_failure_is_not_retried(monkeypatch, tmp_path: Path) -> None:
    output = tmp_path / "full-catalog.json"
    calls = 0

    def fake_main(argv):
        nonlocal calls
        calls += 1
        output.write_text(
            json.dumps({"result": "stopped", "reason": "product_payload_not_parseable"}),
            encoding="utf-8",
        )
        return 3

    monkeypatch.setattr(module.operational, "main", fake_main)
    monkeypatch.setattr(module.time, "sleep", lambda seconds: (_ for _ in ()).throw(AssertionError("unexpected sleep")))

    argv = ["--live-read-only", "--allow-full-catalog", "--output", str(output)]
    assert module.main(argv) == 3
    assert calls == 1


def test_missing_failure_artifact_is_not_retried(monkeypatch, tmp_path: Path) -> None:
    output = tmp_path / "missing.json"
    calls = 0

    def fake_main(argv):
        nonlocal calls
        calls += 1
        return 3

    monkeypatch.setattr(module.operational, "main", fake_main)
    monkeypatch.setattr(module.time, "sleep", lambda seconds: (_ for _ in ()).throw(AssertionError("unexpected sleep")))

    argv = ["--live-read-only", "--allow-full-catalog", "--output", str(output)]
    assert module.main(argv) == 3
    assert calls == 1
