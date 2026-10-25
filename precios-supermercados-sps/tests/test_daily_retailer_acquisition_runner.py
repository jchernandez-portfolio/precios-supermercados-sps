from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "ejecutar_adquisicion_diaria.py"
SPEC = importlib.util.spec_from_file_location("ejecutar_adquisicion_diaria", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_commands_preserve_existing_production_limits_and_retries() -> None:
    los_andes = module.COMMANDS["los_andes"][0]
    walmart = module.COMMANDS["walmart"][0]
    pricesmart = module.COMMANDS["pricesmart"][0]

    assert "--max-retries" in los_andes
    assert los_andes[los_andes.index("--max-retries") + 1] == "1"
    assert "--max-requests" in walmart
    assert walmart[walmart.index("--max-requests") + 1] == "700"
    assert "--max-requests" in pricesmart
    assert pricesmart[pricesmart.index("--max-requests") + 1] == "80"
    assert all("--live-read-only" in command and "--allow-full-catalog" in command for commands in module.COMMANDS.values() for command in commands)


def test_successful_runner_writes_handoff_only_after_validation(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []
    events: list[str] = []

    def fake_run(command: tuple[str, ...], *, cwd: Path, check: bool) -> None:
        assert cwd == module.ROOT
        assert check is True
        calls.append(command)
        events.append("run")

    def fake_validate(retailer: str, artifact_root: Path) -> None:
        assert retailer == "los_andes"
        assert artifact_root == tmp_path / "run-artifacts"
        events.append("validate")

    original_write = module.write_handoff

    def tracked_write(*args, **kwargs):
        events.append("write")
        return original_write(*args, **kwargs)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(module, "validate_retailer", fake_validate)
    monkeypatch.setattr(module, "write_handoff", tracked_write)

    artifact_root = tmp_path / "run-artifacts"
    result = module.run_retailer(
        "los_andes",
        artifact_root=artifact_root,
        run_id="34500000000",
        run_attempt=2,
    )

    assert calls == [module.COMMANDS["los_andes"][0]]
    assert events == ["run", "validate", "write"]
    assert result["accepted"] is True
    marker = json.loads((artifact_root / module.HANDOFF_FILE).read_text(encoding="utf-8"))
    assert marker == result
    assert marker["run_attempt"] == 2


def test_failed_command_never_writes_accepted_handoff(monkeypatch, tmp_path: Path) -> None:
    def fake_run(command: tuple[str, ...], *, cwd: Path, check: bool) -> None:
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    artifact_root = tmp_path / "run-artifacts"

    with pytest.raises(subprocess.CalledProcessError):
        module.run_retailer(
            "colonial",
            artifact_root=artifact_root,
            run_id="34500000000",
            run_attempt=1,
        )

    assert not (artifact_root / module.HANDOFF_FILE).exists()


def test_validation_failure_never_writes_accepted_handoff(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: None)

    def reject(_retailer: str, _artifact_root: Path) -> None:
        raise module.AcquisitionError("catalog_not_complete")

    monkeypatch.setattr(module, "validate_retailer", reject)
    artifact_root = tmp_path / "run-artifacts"

    with pytest.raises(module.AcquisitionError, match="catalog_not_complete"):
        module.run_retailer(
            "walmart",
            artifact_root=artifact_root,
            run_id="34500000000",
            run_attempt=1,
        )

    assert not (artifact_root / module.HANDOFF_FILE).exists()


def test_handoff_rejects_untrusted_identity(tmp_path: Path) -> None:
    artifact_root = tmp_path / "run-artifacts"
    artifact_root.mkdir()

    with pytest.raises(module.AcquisitionError, match="retailer_not_allowed"):
        module.write_handoff("unknown", artifact_root, run_id="34500000000", run_attempt=1)
    with pytest.raises(module.AcquisitionError, match="github_run_id_invalid"):
        module.write_handoff("paiz", artifact_root, run_id="bad", run_attempt=1)
    with pytest.raises(module.AcquisitionError, match="github_run_attempt_invalid"):
        module.write_handoff("paiz", artifact_root, run_id="34500000000", run_attempt=0)
