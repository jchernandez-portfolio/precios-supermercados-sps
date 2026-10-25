"""Transporte autenticado y fail-closed para Google Sheets.

La URL de API, el OAuth scope y el token endpoint están fijados en código. El
caller sólo aporta el ``spreadsheet_id`` y el JSON de una service account. Las
credenciales se transforman inmediatamente en una sesión autorizada y no se
conserva el JSON secreto en la instancia.

No hay retries de aplicación ni redirects. El transporte no conoce fuentes
comerciales ni sus endpoints.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

import requests
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

SHEETS_API_ORIGIN = "https://sheets.googleapis.com"
SHEETS_API_VERSION = "v4"
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
GOOGLE_OAUTH_TOKEN_URI = "https://oauth2.googleapis.com/token"

_SPREADSHEET_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,256}$")
_SERVICE_ACCOUNT_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.iam\.gserviceaccount\.com$"
)
_MAX_SERVICE_ACCOUNT_JSON_BYTES = 64 * 1024
_MAX_RANGES = 64
_MAX_RANGE_LENGTH = 256


class GoogleSheetsTransportError(RuntimeError):
    """Error sanitizado del transporte; nunca incluye credenciales ni body remoto."""

    def __init__(self, code: str, *, status_code: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class JsonResponse(Protocol):
    status_code: int
    headers: Mapping[str, str]

    def json(self) -> Any: ...


class SessionLike(Protocol):
    def request(self, method: str, url: str, **kwargs: Any) -> JsonResponse: ...


def _required_text(value: str, field_name: str, *, max_length: int) -> str:
    if not isinstance(value, str):
        raise GoogleSheetsTransportError(f"{field_name}_invalid")
    cleaned = value.strip()
    if not cleaned or cleaned != value or len(cleaned) > max_length:
        raise GoogleSheetsTransportError(f"{field_name}_invalid")
    if any(ord(character) < 32 for character in cleaned):
        raise GoogleSheetsTransportError(f"{field_name}_invalid")
    return cleaned


def validate_spreadsheet_id(value: str) -> str:
    """Valida el identificador opaco; nunca acepta una URL como sustituto."""

    spreadsheet_id = _required_text(value, "spreadsheet_id", max_length=256)
    if not _SPREADSHEET_ID_RE.fullmatch(spreadsheet_id):
        raise GoogleSheetsTransportError("spreadsheet_id_invalid")
    return spreadsheet_id


def parse_service_account_info(raw_json: str) -> dict[str, Any]:
    """Valida la forma mínima de una service account sin exponer sus secretos."""

    if not isinstance(raw_json, str) or not raw_json.strip():
        raise GoogleSheetsTransportError("service_account_json_invalid")
    if len(raw_json.encode("utf-8")) > _MAX_SERVICE_ACCOUNT_JSON_BYTES:
        raise GoogleSheetsTransportError("service_account_json_too_large")
    try:
        value = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise GoogleSheetsTransportError("service_account_json_invalid") from exc
    if not isinstance(value, dict):
        raise GoogleSheetsTransportError("service_account_json_invalid")

    if value.get("type") != "service_account":
        raise GoogleSheetsTransportError("service_account_type_invalid")
    if value.get("token_uri") != GOOGLE_OAUTH_TOKEN_URI:
        raise GoogleSheetsTransportError("service_account_token_uri_invalid")

    email = value.get("client_email")
    if not isinstance(email, str) or not _SERVICE_ACCOUNT_EMAIL_RE.fullmatch(email):
        raise GoogleSheetsTransportError("service_account_email_invalid")

    private_key = value.get("private_key")
    if (
        not isinstance(private_key, str)
        or len(private_key) > 16_384
        or not private_key.startswith("-----BEGIN PRIVATE KEY-----\n")
        or not private_key.rstrip().endswith("-----END PRIVATE KEY-----")
    ):
        raise GoogleSheetsTransportError("service_account_private_key_invalid")

    private_key_id = value.get("private_key_id")
    project_id = value.get("project_id")
    if not isinstance(private_key_id, str) or not private_key_id.strip():
        raise GoogleSheetsTransportError("service_account_private_key_id_invalid")
    if not isinstance(project_id, str) or not project_id.strip():
        raise GoogleSheetsTransportError("service_account_project_id_invalid")

    # Se devuelve una copia superficial únicamente para construir Credentials.
    return dict(value)


def build_authorized_session(
    raw_service_account_json: str,
    *,
    session_factory: Callable[[Any], SessionLike] = AuthorizedSession,
) -> SessionLike:
    """Crea una sesión limitada al único scope de Google Sheets."""

    info = parse_service_account_info(raw_service_account_json)
    try:
        credentials = service_account.Credentials.from_service_account_info(
            info,
            scopes=(SHEETS_SCOPE,),
        )
        session = session_factory(credentials)
    except Exception as exc:  # La librería puede variar el tipo concreto.
        raise GoogleSheetsTransportError("service_account_credentials_invalid") from exc
    return session


def _validate_ranges(ranges: Sequence[str]) -> tuple[str, ...]:
    if isinstance(ranges, (str, bytes)) or not isinstance(ranges, Sequence):
        raise GoogleSheetsTransportError("ranges_invalid")
    if len(ranges) > _MAX_RANGES:
        raise GoogleSheetsTransportError("ranges_too_many")
    result: list[str] = []
    for value in ranges:
        result.append(_required_text(value, "range", max_length=_MAX_RANGE_LENGTH))
    return tuple(result)


def _json_mapping(response: JsonResponse) -> dict[str, Any]:
    if not isinstance(response.status_code, int):
        raise GoogleSheetsTransportError("response_status_invalid")
    if response.status_code < 200 or response.status_code >= 300:
        raise GoogleSheetsTransportError(
            "google_sheets_http_error",
            status_code=response.status_code,
        )
    try:
        payload = response.json()
    except Exception as exc:
        raise GoogleSheetsTransportError("google_sheets_json_invalid") from exc
    if not isinstance(payload, dict):
        raise GoogleSheetsTransportError("google_sheets_json_invalid")
    return payload


class GoogleSheetsHttpTransport:
    """Cliente mínimo para metadata, batchGet de valores y batchUpdate."""

    def __init__(
        self,
        spreadsheet_id: str,
        session: SessionLike,
        *,
        connect_timeout_seconds: float = 5.0,
        read_timeout_seconds: float = 30.0,
    ) -> None:
        self._spreadsheet_id = validate_spreadsheet_id(spreadsheet_id)
        if not hasattr(session, "request") or not callable(session.request):
            raise GoogleSheetsTransportError("authorized_session_invalid")
        if connect_timeout_seconds <= 0 or connect_timeout_seconds > 30:
            raise GoogleSheetsTransportError("connect_timeout_invalid")
        if read_timeout_seconds <= 0 or read_timeout_seconds > 120:
            raise GoogleSheetsTransportError("read_timeout_invalid")
        self._session = session
        self._timeout = (float(connect_timeout_seconds), float(read_timeout_seconds))

    @classmethod
    def from_service_account_json(
        cls,
        spreadsheet_id: str,
        raw_service_account_json: str,
        *,
        session_factory: Callable[[Any], SessionLike] = AuthorizedSession,
        connect_timeout_seconds: float = 5.0,
        read_timeout_seconds: float = 30.0,
    ) -> GoogleSheetsHttpTransport:
        session = build_authorized_session(
            raw_service_account_json,
            session_factory=session_factory,
        )
        return cls(
            spreadsheet_id,
            session,
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
        )

    @property
    def spreadsheet_id(self) -> str:
        return self._spreadsheet_id

    @property
    def spreadsheet_url(self) -> str:
        return (
            f"{SHEETS_API_ORIGIN}/{SHEETS_API_VERSION}/spreadsheets/"
            f"{self._spreadsheet_id}"
        )

    def _request_json(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        if not url.startswith(f"{SHEETS_API_ORIGIN}/{SHEETS_API_VERSION}/"):
            raise GoogleSheetsTransportError("google_sheets_url_forbidden")
        headers = dict(kwargs.pop("headers", {}))
        headers["Accept"] = "application/json"
        try:
            response = self._session.request(
                method,
                url,
                headers=headers,
                timeout=self._timeout,
                allow_redirects=False,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise GoogleSheetsTransportError("google_sheets_transport_error") from exc
        except Exception as exc:
            raise GoogleSheetsTransportError("google_sheets_transport_error") from exc
        status = getattr(response, "status_code", None)
        if isinstance(status, int) and 300 <= status < 400:
            raise GoogleSheetsTransportError(
                "google_sheets_redirect_forbidden",
                status_code=status,
            )
        return _json_mapping(response)

    def get_spreadsheet_metadata(self) -> dict[str, Any]:
        """Lee sólo propiedades necesarias para planificar tabs gestionados."""

        return self._request_json(
            "GET",
            self.spreadsheet_url,
            params={
                "includeGridData": "false",
                "fields": (
                    "sheets.properties(sheetId,title,gridProperties"
                    "(rowCount,columnCount,frozenRowCount))"
                ),
            },
        )

    def batch_get_values(self, ranges: Sequence[str]) -> dict[str, Any]:
        """Lee ranges A1 ya construidos por la capa de adapter."""

        normalized_ranges = _validate_ranges(ranges)
        if not normalized_ranges:
            return {"spreadsheetId": self._spreadsheet_id, "valueRanges": []}
        return self._request_json(
            "GET",
            f"{self.spreadsheet_url}/values:batchGet",
            params={
                "ranges": normalized_ranges,
                "majorDimension": "ROWS",
                "valueRenderOption": "UNFORMATTED_VALUE",
                "dateTimeRenderOption": "FORMATTED_STRING",
            },
        )

    def batch_update(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Aplica el plan atómico construido por ``google_sheets_plan``."""

        if not isinstance(payload, Mapping):
            raise GoogleSheetsTransportError("batch_update_payload_invalid")
        requests_value = payload.get("requests")
        if not isinstance(requests_value, list) or not requests_value:
            raise GoogleSheetsTransportError("batch_update_requests_invalid")
        if payload.get("includeSpreadsheetInResponse") is not False:
            raise GoogleSheetsTransportError("batch_update_response_mode_invalid")
        if set(payload) != {"requests", "includeSpreadsheetInResponse"}:
            raise GoogleSheetsTransportError("batch_update_payload_shape_invalid")
        return self._request_json(
            "POST",
            f"{self.spreadsheet_url}:batchUpdate",
            headers={"Content-Type": "application/json; charset=utf-8"},
            json=dict(payload),
        )
