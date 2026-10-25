from __future__ import annotations

from copy import deepcopy

import pytest

from precios_supermercados.google_sheets_adapter import (
    GoogleSheetsAdapterError,
    GoogleSheetsWorkbookAdapter,
    hydrate_store_from_managed_rows,
    managed_existing_ranges,
    parse_managed_values_payload,
    snapshot_row_counts,
)
from precios_supermercados.google_sheets_plan import parse_spreadsheet_metadata
from precios_supermercados.storage_contract import LEGACY_SHEETS_MANAGED_TABLE_SPECS
from precios_supermercados.tabular_persistence import (
    CFG_SUPERMARKETS,
    FACT_SCRAPE_RUNS,
    TABLE_SPECS,
)
from precios_supermercados.tabular_store import TabularBatch

SPREADSHEET_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz_0123456789"


def metadata_payload(*titles: str) -> dict:
    sheets = [
        {
            "properties": {
                "sheetId": index + 10,
                "title": title,
                "gridProperties": {
                    "rowCount": 100,
                    "columnCount": 50,
                    "frozenRowCount": 1,
                },
            }
        }
        for index, title in enumerate(titles)
    ]
    return {"sheets": sheets}


def row_for(table_name: str, **values) -> dict:
    spec = TABLE_SPECS[table_name]
    row = {column: None for column in spec.columns}
    row.update(values)
    return row


def sheet_values(table_name: str, *rows: dict) -> list[list]:
    spec = TABLE_SPECS[table_name]
    return [
        list(spec.columns),
        *[[row[column] for column in spec.columns] for row in rows],
    ]


def range_payload(table_name: str, values: list[list]) -> dict:
    return {
        "range": f"{table_name}!A1:ZZ100",
        "majorDimension": "ROWS",
        "values": values,
    }


class FakeTransport:
    def __init__(self, *, metadata: dict, values_payload: dict | None = None):
        self._spreadsheet_id = SPREADSHEET_ID
        self.metadata = deepcopy(metadata)
        self.values_payload = deepcopy(
            values_payload
            if values_payload is not None
            else {"spreadsheetId": SPREADSHEET_ID, "valueRanges": []}
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
        return {"spreadsheetId": self._spreadsheet_id, "replies": []}


def test_managed_ranges_only_include_existing_legacy_project_tabs():
    metadata = parse_spreadsheet_metadata(
        metadata_payload(
            "Sheet1",
            "cfg_supermarkets",
            "dim_products",
            "map_source_products",
            "fact_scrape_runs",
        )
    )

    result = managed_existing_ranges(metadata)

    assert result == (
        ("cfg_supermarkets", "'cfg_supermarkets'!A:E"),
        ("fact_scrape_runs", "'fact_scrape_runs'!A:O"),
    )


def test_parse_empty_managed_tab_is_valid_uninitialized_table():
    payload = {
        "spreadsheetId": SPREADSHEET_ID,
        "valueRanges": [
            {"range": "cfg_supermarkets!A1:E100"},
        ],
    }

    parsed = parse_managed_values_payload(
        ("cfg_supermarkets",),
        payload,
        expected_spreadsheet_id=SPREADSHEET_ID,
    )

    assert parsed["cfg_supermarkets"] == ()


def test_deferred_table_is_not_accepted_as_managed_physical_input():
    payload = {
        "spreadsheetId": SPREADSHEET_ID,
        "valueRanges": [
            {"range": "dim_products!A1:L100"},
        ],
    }

    with pytest.raises(GoogleSheetsAdapterError, match="managed_table_unknown"):
        parse_managed_values_payload(("dim_products",), payload)


def test_parse_exact_header_and_integral_google_number():
    run = row_for(
        "fact_scrape_runs",
        scrape_run_id="run-001",
        supermarket_id="demo",
        location_id="demo_sps",
        run_status="success",
        catalog_accepted=True,
        started_at_utc="2026-08-22T12:00:00.000Z",
        finished_at_utc="2026-08-22T12:01:00.000Z",
        products_observed=10.0,
        offers_observed=10.0,
        current_created=2.0,
        current_changed=0.0,
        current_confirmed=8.0,
        offers_ignored=0.0,
        quality_event_count=0.0,
    )
    payload = {
        "spreadsheetId": SPREADSHEET_ID,
        "valueRanges": [
            range_payload("fact_scrape_runs", sheet_values("fact_scrape_runs", run))
        ],
    }

    parsed = parse_managed_values_payload(
        ("fact_scrape_runs",),
        payload,
        expected_spreadsheet_id=SPREADSHEET_ID,
    )

    parsed_run = parsed["fact_scrape_runs"][0]
    assert parsed_run["products_observed"] == 10
    assert type(parsed_run["products_observed"]) is int
    assert parsed_run["catalog_accepted"] is True


def test_header_mismatch_fails_closed():
    values = [list(CFG_SUPERMARKETS.columns), ["demo", "Demo", "HN", "global", True]]
    values[0][1] = "name"
    payload = {
        "spreadsheetId": SPREADSHEET_ID,
        "valueRanges": [range_payload("cfg_supermarkets", values)],
    }

    with pytest.raises(GoogleSheetsAdapterError, match="managed_sheet_header_mismatch"):
        parse_managed_values_payload(("cfg_supermarkets",), payload)


def test_row_wider_than_contract_fails_closed():
    row = row_for(
        "cfg_supermarkets",
        supermarket_id="demo",
        supermarket_name="Demo",
        country_code="HN",
        location_selection_mode="global",
        is_active=True,
    )
    values = sheet_values("cfg_supermarkets", row)
    values[1].append("unexpected")
    payload = {
        "valueRanges": [range_payload("cfg_supermarkets", values)],
    }

    with pytest.raises(GoogleSheetsAdapterError, match="managed_sheet_row_too_wide"):
        parse_managed_values_payload(("cfg_supermarkets",), payload)


def test_wrong_boolean_type_fails_closed():
    row = row_for(
        "cfg_supermarkets",
        supermarket_id="demo",
        supermarket_name="Demo",
        country_code="HN",
        location_selection_mode="global",
        is_active="TRUE",
    )
    payload = {
        "valueRanges": [
            range_payload(
                "cfg_supermarkets",
                sheet_values("cfg_supermarkets", row),
            )
        ],
    }

    with pytest.raises(
        GoogleSheetsAdapterError,
        match="managed_sheet_boolean_type_invalid",
    ):
        parse_managed_values_payload(("cfg_supermarkets",), payload)


def test_duplicate_primary_key_is_rejected_even_when_rows_are_identical():
    row = row_for(
        "cfg_supermarkets",
        supermarket_id="demo",
        supermarket_name="Demo",
        country_code="HN",
        location_selection_mode="global",
        is_active=True,
    )
    payload = {
        "valueRanges": [
            range_payload(
                "cfg_supermarkets",
                sheet_values("cfg_supermarkets", row, row),
            )
        ],
    }

    with pytest.raises(
        GoogleSheetsAdapterError,
        match="managed_sheet_primary_key_duplicate",
    ):
        parse_managed_values_payload(("cfg_supermarkets",), payload)


def test_returned_value_range_must_match_requested_table():
    payload = {
        "valueRanges": [
            {
                "range": "fact_scrape_runs!A1:O2",
                "values": [],
            }
        ]
    }

    with pytest.raises(GoogleSheetsAdapterError, match="value_range_table_mismatch"):
        parse_managed_values_payload(("cfg_supermarkets",), payload)


def test_spreadsheet_id_mismatch_is_rejected():
    payload = {
        "spreadsheetId": "anotherSpreadsheetIdentifier_123456789",
        "valueRanges": [],
    }

    with pytest.raises(
        GoogleSheetsAdapterError,
        match="batch_get_spreadsheet_id_mismatch",
    ):
        parse_managed_values_payload(
            (),
            payload,
            expected_spreadsheet_id=SPREADSHEET_ID,
        )


def test_hydration_restores_legacy_rows_and_reports_only_managed_counts():
    row = row_for(
        "cfg_supermarkets",
        supermarket_id="demo",
        supermarket_name="Demo",
        country_code="HN",
        location_selection_mode="global",
        is_active=True,
    )

    store = hydrate_store_from_managed_rows({"cfg_supermarkets": (row,)})
    counts = snapshot_row_counts(store)

    assert store.row("cfg_supermarkets", "demo")["supermarket_name"] == "Demo"
    assert counts["cfg_supermarkets"] == 1
    assert counts["fact_offers_current"] == 0
    assert set(counts) == set(LEGACY_SHEETS_MANAGED_TABLE_SPECS)
    assert store.count("dim_products") == 0
    assert store.count("map_source_products") == 0


def test_adapter_initializes_new_workbook_using_only_legacy_tabs():
    transport = FakeTransport(metadata=metadata_payload("Sheet1"))
    adapter = GoogleSheetsWorkbookAdapter(transport)
    row = row_for(
        "cfg_supermarkets",
        supermarket_id="demo",
        supermarket_name="Demo",
        country_code="HN",
        location_selection_mode="global",
        is_active=True,
    )

    result = adapter.apply(TabularBatch(rows={"cfg_supermarkets": (row,)}))

    assert transport.metadata_calls == 1
    assert transport.batch_get_calls == []
    assert len(transport.batch_update_calls) == 1
    assert result.created == 1
    assert result.updated == 0
    assert result.initial_row_counts["cfg_supermarkets"] == 0
    assert result.final_row_counts["cfg_supermarkets"] == 1
    assert result.managed_sheet_count == len(LEGACY_SHEETS_MANAGED_TABLE_SPECS)

    requests = transport.batch_update_calls[0]["requests"]
    added_titles = {
        request["addSheet"]["properties"]["title"]
        for request in requests
        if "addSheet" in request
    }
    assert added_titles == set(LEGACY_SHEETS_MANAGED_TABLE_SPECS)


def test_adapter_rejects_batch_for_deferred_logical_table_before_remote_read():
    transport = FakeTransport(metadata=metadata_payload("Sheet1"))
    adapter = GoogleSheetsWorkbookAdapter(transport)

    with pytest.raises(
        GoogleSheetsAdapterError,
        match="tabular_batch_contains_deferred_table",
    ):
        adapter.apply(TabularBatch(rows={"dim_products": ()}))

    assert transport.metadata_calls == 0
    assert transport.batch_get_calls == []
    assert transport.batch_update_calls == []


def test_adapter_reads_existing_snapshot_then_upserts_without_losing_other_row():
    first = row_for(
        "cfg_supermarkets",
        supermarket_id="demo",
        supermarket_name="Nombre anterior",
        country_code="HN",
        location_selection_mode="global",
        is_active=True,
    )
    other = row_for(
        "cfg_supermarkets",
        supermarket_id="other",
        supermarket_name="Otro",
        country_code="HN",
        location_selection_mode="global",
        is_active=True,
    )
    values_payload = {
        "spreadsheetId": SPREADSHEET_ID,
        "valueRanges": [
            range_payload(
                "cfg_supermarkets",
                sheet_values("cfg_supermarkets", first, other),
            )
        ],
    }
    transport = FakeTransport(
        metadata=metadata_payload("Sheet1", "cfg_supermarkets"),
        values_payload=values_payload,
    )
    adapter = GoogleSheetsWorkbookAdapter(transport)
    replacement = dict(first)
    replacement["supermarket_name"] = "Nombre nuevo"

    result = adapter.apply(
        TabularBatch(rows={"cfg_supermarkets": (replacement,)})
    )

    assert result.created == 0
    assert result.updated == 1
    assert result.initial_row_counts["cfg_supermarkets"] == 2
    assert result.final_row_counts["cfg_supermarkets"] == 2
    assert transport.batch_get_calls == [("'cfg_supermarkets'!A:E",)]

    requests = transport.batch_update_calls[0]["requests"]
    writes = [
        request["updateCells"]
        for request in requests
        if "updateCells" in request
        and request["updateCells"]["range"]["sheetId"] == 11
    ]
    assert len(writes) == 1
    rendered_rows = writes[0]["rows"]
    rendered_names = [
        row["values"][1].get("userEnteredValue", {}).get("stringValue")
        for row in rendered_rows[1:]
    ]
    assert rendered_names == ["Nombre nuevo", "Otro"]


def test_immutable_run_conflict_aborts_before_remote_write():
    existing = row_for(
        "fact_scrape_runs",
        scrape_run_id="run-001",
        supermarket_id="demo",
        location_id="demo_sps",
        run_status="success",
        catalog_accepted=False,
        started_at_utc="2026-08-22T12:00:00.000Z",
        finished_at_utc="2026-08-22T12:01:00.000Z",
        products_observed=0,
        offers_observed=0,
        current_created=0,
        current_changed=0,
        current_confirmed=0,
        offers_ignored=0,
        quality_event_count=0,
    )
    values_payload = {
        "spreadsheetId": SPREADSHEET_ID,
        "valueRanges": [
            range_payload(
                "fact_scrape_runs",
                sheet_values("fact_scrape_runs", existing),
            )
        ],
    }
    transport = FakeTransport(
        metadata=metadata_payload("fact_scrape_runs"),
        values_payload=values_payload,
    )
    adapter = GoogleSheetsWorkbookAdapter(transport)
    divergent = dict(existing)
    divergent["run_status"] = "failed"

    with pytest.raises(GoogleSheetsAdapterError, match="tabular_batch_conflict"):
        adapter.apply(TabularBatch(rows={"fact_scrape_runs": (divergent,)}))

    assert transport.batch_update_calls == []


def test_existing_legacy_tab_with_bad_header_aborts_before_remote_write():
    bad_values = [list(CFG_SUPERMARKETS.columns)]
    bad_values[0][0] = "id"
    transport = FakeTransport(
        metadata=metadata_payload("cfg_supermarkets"),
        values_payload={
            "spreadsheetId": SPREADSHEET_ID,
            "valueRanges": [range_payload("cfg_supermarkets", bad_values)],
        },
    )
    adapter = GoogleSheetsWorkbookAdapter(transport)

    with pytest.raises(GoogleSheetsAdapterError, match="managed_sheet_header_mismatch"):
        adapter.apply(TabularBatch(rows={}))

    assert transport.batch_update_calls == []
