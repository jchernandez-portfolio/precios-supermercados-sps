from __future__ import annotations

import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest

from precios_supermercados.google_sheets_adapter import GoogleSheetsWorkbookAdapter
from precios_supermercados.google_sheets_bootstrap import (
    MODE_APPLY_CONFIG,
    MODE_CHECK,
    GoogleSheetsBootstrapError,
    build_configuration_batch,
    run_google_sheets_bootstrap,
)
from precios_supermercados.storage_contract import LEGACY_SHEETS_MANAGED_TABLE_SPECS

SPREADSHEET_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz_0123456789"


class FakeTransport:
    def __init__(self, *, metadata=None, values_payload=None):
        self._spreadsheet_id = SPREADSHEET_ID
        self.metadata = deepcopy(metadata or {"sheets": []})
        self.values_payload = deepcopy(
            values_payload
            or {"spreadsheetId": SPREADSHEET_ID, "valueRanges": []}
        )
        self.metadata_calls = 0
        self.batch_get_calls = []
        self.batch_update_calls = []

    @property
    def spreadsheet_id(self):
        return self._spreadsheet_id

    def get_spreadsheet_metadata(self):
        self.metadata_calls += 1
        return deepcopy(self.metadata)

    def batch_get_values(self, ranges):
        self.batch_get_calls.append(tuple(ranges))
        return deepcopy(self.values_payload)

    def batch_update(self, payload):
        self.batch_update_calls.append(deepcopy(payload))
        return {"spreadsheetId": SPREADSHEET_ID, "replies": []}


def load_cli_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "inicializar_google_sheets.py"
    )
    spec = importlib.util.spec_from_file_location("inicializar_google_sheets_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_configuration_batch_contains_only_configuration_tables():
    batch = build_configuration_batch()

    assert set(batch.rows) == {"cfg_supermarkets", "cfg_locations"}
    assert len(batch.rows["cfg_supermarkets"]) == 1
    assert len(batch.rows["cfg_locations"]) == 2
    assert not any(name.startswith("fact_") for name in batch.rows)


def test_default_configuration_records_confirmed_sps_binding_without_enabling_extraction():
    batch = build_configuration_batch()
    locations = {
        row["location_id"]: row for row in batch.rows["cfg_locations"]
    }

    assert set(locations) == {"la_colonia_sps", "la_colonia_tgu"}
    assert locations["la_colonia_sps"]["city_name"] == "San Pedro Sula"
    assert locations["la_colonia_sps"]["granularity"] == "city"
    assert locations["la_colonia_sps"]["in_scope"] is True
    assert locations["la_colonia_sps"]["extraction_enabled"] is False
    assert locations["la_colonia_sps"]["technical_binding_confirmed"] is True
    assert locations["la_colonia_sps"]["source_location_key"] == (
        "request:regionid:sha256:"
        "d7732eccc99c8530a6d29cce4244920e65e85c1d5492facb05469dc3589cb8b7"
    )

    assert locations["la_colonia_tgu"]["city_name"] == "Tegucigalpa"
    assert locations["la_colonia_tgu"]["in_scope"] is False
    assert locations["la_colonia_tgu"]["extraction_enabled"] is False
    assert locations["la_colonia_tgu"]["technical_binding_confirmed"] is False


def test_check_mode_reads_only_legacy_sheets_contract():
    transport = FakeTransport(metadata={"sheets": []})
    adapter = GoogleSheetsWorkbookAdapter(transport)

    result = run_google_sheets_bootstrap(adapter, mode=MODE_CHECK)

    assert result.mode == MODE_CHECK
    assert result.wrote is False
    assert result.created == 0
    assert result.updated == 0
    assert result.replayed == 0
    assert result.nonempty_managed_tables == 0
    assert result.payload_bytes is None
    assert set(result.row_counts) == set(LEGACY_SHEETS_MANAGED_TABLE_SPECS)
    assert all(count == 0 for count in result.row_counts.values())
    assert transport.metadata_calls == 1
    assert transport.batch_get_calls == []
    assert transport.batch_update_calls == []


def test_apply_config_initializes_only_legacy_tabs_but_leaves_fact_tables_empty():
    transport = FakeTransport(metadata={"sheets": []})
    adapter = GoogleSheetsWorkbookAdapter(transport)

    result = run_google_sheets_bootstrap(adapter, mode=MODE_APPLY_CONFIG)

    assert result.wrote is True
    assert result.created == 3
    assert result.updated == 0
    assert result.replayed == 0
    assert result.nonempty_managed_tables == 0
    assert result.row_counts["cfg_supermarkets"] == 1
    assert result.row_counts["cfg_locations"] == 2
    for table_name in LEGACY_SHEETS_MANAGED_TABLE_SPECS:
        if table_name.startswith("fact_"):
            assert result.row_counts[table_name] == 0
    assert result.payload_bytes is not None and result.payload_bytes > 0
    assert len(transport.batch_update_calls) == 1

    requests = transport.batch_update_calls[0]["requests"]
    added_titles = {
        request["addSheet"]["properties"]["title"]
        for request in requests
        if "addSheet" in request
    }
    assert added_titles == set(LEGACY_SHEETS_MANAGED_TABLE_SPECS)
    assert "dim_products" not in added_titles
    assert "map_source_products" not in added_titles

    fact_sheet_ids = {
        request["addSheet"]["properties"]["sheetId"]
        for request in requests
        if "addSheet" in request
        and request["addSheet"]["properties"]["title"].startswith("fact_")
    }
    fact_writes = [
        request["updateCells"]
        for request in requests
        if "updateCells" in request
        and request["updateCells"]["range"]["sheetId"] in fact_sheet_ids
    ]
    assert fact_writes
    assert all(len(write["rows"]) == 1 for write in fact_writes)


def test_invalid_bootstrap_mode_fails_before_transport_use():
    transport = FakeTransport(metadata={"sheets": []})
    adapter = GoogleSheetsWorkbookAdapter(transport)

    with pytest.raises(GoogleSheetsBootstrapError, match="bootstrap_mode_invalid"):
        run_google_sheets_bootstrap(adapter, mode="full-crawl")

    assert transport.metadata_calls == 0
    assert transport.batch_update_calls == []


def test_cli_defaults_to_read_only_check():
    module = load_cli_module()
    args = module.build_parser().parse_args([])
    assert args.mode == MODE_CHECK


def test_retired_cli_fails_closed_before_credentials(monkeypatch, capsys):
    module = load_cli_module()
    monkeypatch.delenv(module.SPREADSHEET_ID_ENV, raising=False)
    monkeypatch.delenv(module.SERVICE_ACCOUNT_JSON_ENV, raising=False)

    exit_code = module.main(["--mode", MODE_CHECK])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.out == ""
    assert '"status": "error"' in captured.err
    assert "google_sheets_storage_retired" in captured.err
    assert "missing_precios_sps_google" not in captured.err
    assert "Traceback" not in captured.err


def test_cli_unexpected_exception_never_prints_exception_or_secret(monkeypatch, capsys):
    module = load_cli_module()
    secret = "DO-NOT-LEAK-PRIVATE-KEY-MATERIAL"

    def explode(_mode):
        raise RuntimeError(secret)

    monkeypatch.setattr(module, "run", explode)

    exit_code = module.main(["--mode", MODE_CHECK])
    captured = capsys.readouterr()

    assert exit_code == 3
    assert captured.out == ""
    assert "storage_bootstrap_unexpected_error" in captured.err
    assert secret not in captured.err
    assert "RuntimeError" not in captured.err
    assert "Traceback" not in captured.err


def test_cli_source_contains_no_scraper_entrypoint_or_cloudflare_gateway():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "inicializar_google_sheets.py"
    )
    source = path.read_text(encoding="utf-8")

    assert "probar_la_colonia.py" not in source
    assert "diagnosticar_ventanas_la_colonia.py" not in source
    assert "descubrir_facets_la_colonia.py" not in source
    assert "CLOUDFLARE_PROBE_GATEWAY_URL" not in source
    assert ".workers.dev" not in source
