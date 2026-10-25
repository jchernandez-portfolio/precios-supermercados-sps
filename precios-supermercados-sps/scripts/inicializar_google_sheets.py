#!/usr/bin/env python3
"""Entry point histórico de Google Sheets, retirado fail-closed.

BigQuery es el único backend físico activo. Se conservan helpers de validación y
salida para poder auditar la implementación histórica, pero ninguna invocación de
este CLI puede construir transporte, usar credenciales ni escribir en Sheets.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from precios_supermercados.google_sheets_adapter import (
    GoogleSheetsAdapterError,
    GoogleSheetsWorkbookAdapter,  # noqa: F401 - la prueba del CLI retirado lo parchea
)
from precios_supermercados.google_sheets_bootstrap import (
    ALLOWED_BOOTSTRAP_MODES,
    MODE_CHECK,
    GoogleSheetsBootstrapError,
    run_google_sheets_bootstrap,  # noqa: F401 - la prueba del CLI retirado lo parchea
)
from precios_supermercados.google_sheets_transport import (
    GoogleSheetsHttpTransport,  # noqa: F401 - la prueba del CLI retirado lo parchea
    GoogleSheetsTransportError,
)

SPREADSHEET_ID_ENV = "PRECIOS_SPS_GOOGLE_SPREADSHEET_ID"
SERVICE_ACCOUNT_JSON_ENV = "PRECIOS_SPS_GOOGLE_SERVICE_ACCOUNT_JSON"
RETIRED_ERROR_CODE = "google_sheets_storage_retired"
_SAFE_OUTPUT = re.compile(r"^[a-z0-9_.-]{1,128}$")


class StorageCliError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not isinstance(value, str) or not value.strip():
        raise StorageCliError(f"missing_{name.casefold()}")
    return value


def _required_storage_envs() -> tuple[str, str]:
    """Valida credenciales históricas sin utilizarlas en el entrypoint retirado."""

    values = {
        SPREADSHEET_ID_ENV: os.environ.get(SPREADSHEET_ID_ENV),
        SERVICE_ACCOUNT_JSON_ENV: os.environ.get(SERVICE_ACCOUNT_JSON_ENV),
    }
    missing = tuple(
        name
        for name, value in values.items()
        if not isinstance(value, str) or not value.strip()
    )
    if missing == (SPREADSHEET_ID_ENV, SERVICE_ACCOUNT_JSON_ENV):
        raise StorageCliError(
            "missing_precios_sps_google_spreadsheet_id_and_service_account_json"
        )
    if missing:
        raise StorageCliError(f"missing_{missing[0].casefold()}")

    spreadsheet_id = values[SPREADSHEET_ID_ENV]
    service_account_json = values[SERVICE_ACCOUNT_JSON_ENV]
    assert isinstance(spreadsheet_id, str)
    assert isinstance(service_account_json, str)
    return spreadsheet_id, service_account_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Entry point histórico de Google Sheets retirado; "
            "BigQuery es el backend activo."
        )
    )
    parser.add_argument(
        "--mode",
        choices=sorted(ALLOWED_BOOTSTRAP_MODES),
        default=MODE_CHECK,
    )
    return parser


def run(mode: str) -> dict[str, object]:
    """Falla antes de leer credenciales o construir cualquier transporte de red."""

    del mode
    raise StorageCliError(RETIRED_ERROR_CODE)


def _safe_error_code(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and _SAFE_OUTPUT.fullmatch(code):
        return code
    return "storage_bootstrap_unexpected_error"


def _write_github_outputs(
    *,
    result: str,
    error_code: str,
    wrote: bool | None,
) -> bool:
    """Publica sólo señales allowlisted; un fallo del canal no oculta el resultado CLI."""

    if result not in {"ok", "error"}:
        return False
    if not _SAFE_OUTPUT.fullmatch(error_code):
        return False
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return False

    wrote_text = "unknown" if wrote is None else str(wrote).casefold()
    try:
        with Path(output_path).open("a", encoding="utf-8") as handle:
            handle.write(f"result={result}\n")
            handle.write(f"error_code={error_code}\n")
            handle.write(f"wrote={wrote_text}\n")
    except OSError:
        return False
    return True


def _report_error(code: str, *, exit_code: int) -> int:
    _write_github_outputs(result="error", error_code=code, wrote=None)
    print(
        json.dumps({"status": "error", "error_code": code}, sort_keys=True),
        file=sys.stderr,
    )
    return exit_code


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run(args.mode)
    except (
        StorageCliError,
        GoogleSheetsTransportError,
        GoogleSheetsAdapterError,
        GoogleSheetsBootstrapError,
    ) as exc:
        return _report_error(_safe_error_code(exc), exit_code=2)
    except Exception:
        # Evita que una excepción de librería imprima request/body/credenciales.
        return _report_error("storage_bootstrap_unexpected_error", exit_code=3)

    wrote = result.get("wrote")
    wrote_value = wrote if isinstance(wrote, bool) else None
    _write_github_outputs(result="ok", error_code="none", wrote=wrote_value)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
