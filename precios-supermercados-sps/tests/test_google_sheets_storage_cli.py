from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "inicializar_google_sheets.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("storage_cli_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_safe_error_code_rejects_values_that_could_leak_or_inject() -> None:
    module = load_module()

    class SafeError(Exception):
        code = "missing_precios_sps_google_service_account_json"

    class UnsafeError(Exception):
        code = "access denied: secret=abc\nother=value"

    assert module._safe_error_code(SafeError()) == (
        "missing_precios_sps_google_service_account_json"
    )
    assert module._safe_error_code(UnsafeError()) == (
        "storage_bootstrap_unexpected_error"
    )


def test_required_storage_envs_reports_all_missing_credentials(monkeypatch) -> None:
    module = load_module()
    monkeypatch.delenv(module.SPREADSHEET_ID_ENV, raising=False)
    monkeypatch.delenv(module.SERVICE_ACCOUNT_JSON_ENV, raising=False)

    with pytest.raises(module.StorageCliError) as exc_info:
        module._required_storage_envs()

    assert exc_info.value.code == (
        "missing_precios_sps_google_spreadsheet_id_and_service_account_json"
    )


def test_required_storage_envs_reports_each_missing_credential(monkeypatch) -> None:
    module = load_module()
    monkeypatch.setenv(module.SPREADSHEET_ID_ENV, "spreadsheet-id")
    monkeypatch.delenv(module.SERVICE_ACCOUNT_JSON_ENV, raising=False)
    with pytest.raises(module.StorageCliError) as exc_info:
        module._required_storage_envs()
    assert exc_info.value.code == "missing_precios_sps_google_service_account_json"

    monkeypatch.delenv(module.SPREADSHEET_ID_ENV, raising=False)
    monkeypatch.setenv(module.SERVICE_ACCOUNT_JSON_ENV, "{}")
    with pytest.raises(module.StorageCliError) as exc_info:
        module._required_storage_envs()
    assert exc_info.value.code == "missing_precios_sps_google_spreadsheet_id"


def test_required_storage_envs_returns_nonempty_values(monkeypatch) -> None:
    module = load_module()
    monkeypatch.setenv(module.SPREADSHEET_ID_ENV, "spreadsheet-id")
    monkeypatch.setenv(module.SERVICE_ACCOUNT_JSON_ENV, "{\"type\":\"service_account\"}")

    assert module._required_storage_envs() == (
        "spreadsheet-id",
        "{\"type\":\"service_account\"}",
    )


def test_retired_cli_fails_before_reading_credentials_or_building_transport(monkeypatch) -> None:
    module = load_module()

    def forbidden(*args, **kwargs):
        raise AssertionError("retired CLI must not inspect credentials or touch transport")

    monkeypatch.setattr(module, "_required_storage_envs", forbidden)
    monkeypatch.setattr(module.GoogleSheetsHttpTransport, "from_service_account_json", forbidden)

    with pytest.raises(module.StorageCliError) as exc_info:
        module.run(module.MODE_CHECK)

    assert exc_info.value.code == module.RETIRED_ERROR_CODE


def test_retired_cli_main_reports_closed_state_without_credentials(monkeypatch, capsys) -> None:
    module = load_module()
    monkeypatch.delenv(module.SPREADSHEET_ID_ENV, raising=False)
    monkeypatch.delenv(module.SERVICE_ACCOUNT_JSON_ENV, raising=False)

    assert module.main([]) == 2
    error = capsys.readouterr().err
    assert module.RETIRED_ERROR_CODE in error
    assert "service_account" not in error


def test_github_outputs_are_closed_sanitized_values(tmp_path, monkeypatch) -> None:
    module = load_module()
    output = tmp_path / "github-output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    assert module._write_github_outputs(
        result="error",
        error_code="managed_sheet_header_mismatch",
        wrote=None,
    ) is True

    assert output.read_text(encoding="utf-8") == (
        "result=error\n"
        "error_code=managed_sheet_header_mismatch\n"
        "wrote=unknown\n"
    )
    assert module._write_github_outputs(
        result="error",
        error_code="unsafe\nvalue",
        wrote=None,
    ) is False


def test_github_output_channel_failure_does_not_replace_cli_result(tmp_path, monkeypatch) -> None:
    module = load_module()
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "missing" / "output.txt"))

    assert module._write_github_outputs(
        result="error",
        error_code="storage_job_failed",
        wrote=None,
    ) is False
    assert module._report_error("storage_job_failed", exit_code=2) == 2
