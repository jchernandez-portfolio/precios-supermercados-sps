from __future__ import annotations

import json

import pytest
import requests

from precios_supermercados.google_sheets_transport import (
    GOOGLE_OAUTH_TOKEN_URI,
    SHEETS_API_ORIGIN,
    SHEETS_SCOPE,
    GoogleSheetsHttpTransport,
    GoogleSheetsTransportError,
    build_authorized_session,
    parse_service_account_info,
    validate_spreadsheet_id,
)

SPREADSHEET_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz_0123456789"


def service_account_payload(**overrides) -> dict:
    value = {
        "type": "service_account",
        "project_id": "precios-sps-test",
        "private_key_id": "key-id-001",
        "private_key": (
            "-----BEGIN PRIVATE KEY-----\n"
            "TEST-ONLY-NOT-A-REAL-KEY\n"
            "-----END PRIVATE KEY-----\n"
        ),
        "client_email": "precios-test@precios-sps-test.iam.gserviceaccount.com",
        "client_id": "1234567890",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": GOOGLE_OAUTH_TOKEN_URI,
    }
    value.update(overrides)
    return value


def service_account_json(**overrides) -> str:
    return json.dumps(service_account_payload(**overrides), indent=2)


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.headers = {"content-type": "application/json"}
        self._payload = {} if payload is None else payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [FakeResponse()])

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError("unexpected request")
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def test_spreadsheet_id_accepts_only_opaque_identifier_not_url() -> None:
    assert validate_spreadsheet_id(SPREADSHEET_ID) == SPREADSHEET_ID
    for invalid in (
        "https://docs.google.com/spreadsheets/d/" + SPREADSHEET_ID,
        "short",
        " contains-space-abcdefghijkl",
        "abc/defghijklmnopqrstuvwxyz12345",
    ):
        with pytest.raises(GoogleSheetsTransportError, match="spreadsheet_id_invalid"):
            validate_spreadsheet_id(invalid)


def test_service_account_parser_accepts_pretty_json_and_returns_copy() -> None:
    raw = service_account_json()
    parsed = parse_service_account_info(raw)
    assert parsed["type"] == "service_account"
    assert parsed["token_uri"] == GOOGLE_OAUTH_TOKEN_URI
    assert parsed["client_email"].endswith(".iam.gserviceaccount.com")
    parsed["project_id"] = "mutated"
    assert json.loads(raw)["project_id"] == "precios-sps-test"


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"type": "authorized_user"}, "service_account_type_invalid"),
        ({"token_uri": "https://evil.invalid/token"}, "service_account_token_uri_invalid"),
        ({"client_email": "person@example.com"}, "service_account_email_invalid"),
        ({"private_key": "not-a-key"}, "service_account_private_key_invalid"),
        ({"private_key_id": ""}, "service_account_private_key_id_invalid"),
        ({"project_id": ""}, "service_account_project_id_invalid"),
    ],
)
def test_service_account_parser_is_fail_closed(overrides, code) -> None:
    with pytest.raises(GoogleSheetsTransportError, match=code):
        parse_service_account_info(service_account_json(**overrides))


def test_service_account_parser_never_includes_secret_in_error() -> None:
    secret = "SUPER-SECRET-PRIVATE-MATERIAL"
    payload = service_account_payload(private_key=secret)
    with pytest.raises(GoogleSheetsTransportError) as exc:
        parse_service_account_info(json.dumps(payload))
    assert secret not in str(exc.value)


def test_build_authorized_session_uses_only_sheets_scope(monkeypatch) -> None:
    observed = {}
    fake_credentials = object()
    fake_session = FakeSession()

    def fake_credentials_factory(info, *, scopes):
        observed["info_type"] = info["type"]
        observed["scopes"] = scopes
        return fake_credentials

    monkeypatch.setattr(
        "precios_supermercados.google_sheets_transport.service_account.Credentials.from_service_account_info",
        fake_credentials_factory,
    )

    def session_factory(credentials):
        assert credentials is fake_credentials
        return fake_session

    session = build_authorized_session(
        service_account_json(),
        session_factory=session_factory,
    )
    assert session is fake_session
    assert observed == {"info_type": "service_account", "scopes": (SHEETS_SCOPE,)}


def test_metadata_request_uses_fixed_host_fields_timeouts_and_no_redirects() -> None:
    session = FakeSession(
        [FakeResponse(payload={"sheets": [{"properties": {"sheetId": 1}}]})]
    )
    transport = GoogleSheetsHttpTransport(SPREADSHEET_ID, session)
    result = transport.get_spreadsheet_metadata()
    assert result["sheets"][0]["properties"]["sheetId"] == 1

    assert len(session.calls) == 1
    method, url, kwargs = session.calls[0]
    assert method == "GET"
    assert url == f"{SHEETS_API_ORIGIN}/v4/spreadsheets/{SPREADSHEET_ID}"
    assert kwargs["allow_redirects"] is False
    assert kwargs["timeout"] == (5.0, 30.0)
    assert kwargs["headers"] == {"Accept": "application/json"}
    assert kwargs["params"]["includeGridData"] == "false"
    assert kwargs["params"]["fields"].startswith("sheets.properties(")


def test_batch_get_values_uses_only_fixed_endpoint_and_render_modes() -> None:
    session = FakeSession(
        [FakeResponse(payload={"spreadsheetId": SPREADSHEET_ID, "valueRanges": []})]
    )
    transport = GoogleSheetsHttpTransport(SPREADSHEET_ID, session)
    result = transport.batch_get_values(("'cfg_supermarkets'!A:E", "'cfg_locations'!A:K"))
    assert result["valueRanges"] == []

    method, url, kwargs = session.calls[0]
    assert method == "GET"
    assert url == (
        f"{SHEETS_API_ORIGIN}/v4/spreadsheets/{SPREADSHEET_ID}/values:batchGet"
    )
    assert kwargs["params"]["ranges"] == (
        "'cfg_supermarkets'!A:E",
        "'cfg_locations'!A:K",
    )
    assert kwargs["params"]["majorDimension"] == "ROWS"
    assert kwargs["params"]["valueRenderOption"] == "UNFORMATTED_VALUE"
    assert kwargs["params"]["dateTimeRenderOption"] == "FORMATTED_STRING"
    assert kwargs["allow_redirects"] is False


def test_empty_batch_get_is_local_and_does_not_hit_network() -> None:
    session = FakeSession([])
    transport = GoogleSheetsHttpTransport(SPREADSHEET_ID, session)
    result = transport.batch_get_values(())
    assert result == {"spreadsheetId": SPREADSHEET_ID, "valueRanges": []}
    assert session.calls == []


def test_batch_update_requires_exact_planner_shape_and_posts_json() -> None:
    session = FakeSession([FakeResponse(payload={"spreadsheetId": SPREADSHEET_ID})])
    transport = GoogleSheetsHttpTransport(SPREADSHEET_ID, session)
    payload = {
        "requests": [{"addSheet": {"properties": {"title": "cfg_supermarkets"}}}],
        "includeSpreadsheetInResponse": False,
    }
    assert transport.batch_update(payload)["spreadsheetId"] == SPREADSHEET_ID

    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert url == f"{SHEETS_API_ORIGIN}/v4/spreadsheets/{SPREADSHEET_ID}:batchUpdate"
    assert kwargs["json"] == payload
    assert kwargs["headers"]["Content-Type"] == "application/json; charset=utf-8"
    assert kwargs["headers"]["Accept"] == "application/json"
    assert kwargs["allow_redirects"] is False

    invalid_payloads = (
        {},
        {"requests": [], "includeSpreadsheetInResponse": False},
        {"requests": [{}], "includeSpreadsheetInResponse": True},
        {
            "requests": [{}],
            "includeSpreadsheetInResponse": False,
            "extra": True,
        },
    )
    for invalid in invalid_payloads:
        with pytest.raises(GoogleSheetsTransportError):
            transport.batch_update(invalid)
    assert len(session.calls) == 1


def test_redirect_http_error_invalid_json_and_transport_error_are_sanitized() -> None:
    cases = (
        (FakeResponse(302, {}), "google_sheets_redirect_forbidden", 302),
        (FakeResponse(403, {"error": {"message": "secret remote detail"}}), "google_sheets_http_error", 403),
        (FakeResponse(200, ValueError("raw body secret")), "google_sheets_json_invalid", None),
        (requests.ConnectionError("credential-like secret"), "google_sheets_transport_error", None),
    )
    for response, code, status in cases:
        session = FakeSession([response])
        transport = GoogleSheetsHttpTransport(SPREADSHEET_ID, session)
        with pytest.raises(GoogleSheetsTransportError) as exc:
            transport.get_spreadsheet_metadata()
        assert exc.value.code == code
        assert exc.value.status_code == status
        assert "secret" not in str(exc.value)
        assert len(session.calls) == 1


def test_transport_has_no_retry_and_timeout_is_bounded() -> None:
    for connect_timeout, read_timeout in ((0, 30), (31, 30), (5, 0), (5, 121)):
        with pytest.raises(GoogleSheetsTransportError):
            GoogleSheetsHttpTransport(
                SPREADSHEET_ID,
                FakeSession(),
                connect_timeout_seconds=connect_timeout,
                read_timeout_seconds=read_timeout,
            )

    session = FakeSession([requests.Timeout("once")])
    transport = GoogleSheetsHttpTransport(SPREADSHEET_ID, session)
    with pytest.raises(GoogleSheetsTransportError, match="google_sheets_transport_error"):
        transport.get_spreadsheet_metadata()
    assert len(session.calls) == 1


def test_ranges_are_bounded_and_reject_control_characters() -> None:
    transport = GoogleSheetsHttpTransport(SPREADSHEET_ID, FakeSession())
    with pytest.raises(GoogleSheetsTransportError, match="ranges_invalid"):
        transport.batch_get_values("A1")
    with pytest.raises(GoogleSheetsTransportError, match="ranges_too_many"):
        transport.batch_get_values(tuple("A1" for _ in range(65)))
    with pytest.raises(GoogleSheetsTransportError, match="range_invalid"):
        transport.batch_get_values(("A1\nAuthorization: secret",))


def test_transport_source_contains_no_drive_or_lacolonia_endpoint() -> None:
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "precios_supermercados"
        / "google_sheets_transport.py"
    ).read_text(encoding="utf-8").lower()
    assert "drive.googleapis.com" not in source
    assert "docs.google.com" not in source
    assert "lacolonia" not in source
    assert "la_colonia" not in source
    assert "authorization" not in source
