#!/usr/bin/env python3
"""Estado incremental y upsert barato de especificaciones PriceSmart en Turso.

Subcomandos:

- ``state``: lee sólo las llaves verificadas dentro de la ventana de frescura
  (índice cubriente) y escribe el JSON que consume la captura semanal;
- ``apply``: valida un artifact ``precios-sps-pricesmart-specs/v1`` exitoso y
  escribe sólo filas nuevas/cambiadas (las re-verificadas sólo tocan
  ``verified_at_utc``), verificando después únicamente las filas afectadas.

No consulta PriceSmart. No toca ``products``, ``price_history`` ni ``scrape_runs``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "scripts", ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from actualizar_mvp_sqlite_la_colonia import SnapshotError  # noqa: E402
from actualizar_mvp_turso_la_colonia import (  # noqa: E402
    _execute_rows,
    _pipeline,
    _run_batch,
    _stmt,
)
from precios_supermercados.pricesmart_specs_persistence import (  # noqa: E402
    DEFAULT_STALE_DAYS,
    PriceSmartSpecsPersistenceError,
    apply_rows,
    read_state,
    rows_from_artifact,
)


class TursoExecutor:
    def __init__(self, url: str, token: str) -> None:
        if not url.strip() or not token.strip():
            raise PriceSmartSpecsPersistenceError("turso_credentials_missing")
        self.url = url
        self.token = token

    def query(self, sql: str, args: tuple[object, ...] = ()) -> list[list[object]]:
        data = _pipeline(
            self.url,
            self.token,
            [{"type": "execute", "stmt": _stmt(sql, args)}, {"type": "close"}],
        )
        results = data.get("results")
        if not isinstance(results, list) or len(results) != 2:
            raise SnapshotError("pricesmart_specs_turso_query_response_invalid")
        return _execute_rows(results[0])

    def batch(self, steps: list[tuple[str, str, tuple[object, ...]]]) -> list[int | None]:
        results = _run_batch(self.url, self.token, steps)
        return [
            item.get("affected_row_count") if isinstance(item, dict) else None
            for item in results
        ]


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def command_state(executor: Any, output: Path, *, stale_days: int, now: datetime | None = None) -> dict[str, Any]:
    state = read_state(executor, now=now or _now(), stale_days=stale_days)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {key: value for key, value in state.items() if key != "fresh"} | {"fresh_keys": len(state["fresh"])}


def command_apply(executor: Any, artifact_path: Path) -> dict[str, Any]:
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    rows = rows_from_artifact(artifact)
    return apply_rows(executor, rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    state = sub.add_parser("state")
    state.add_argument("--output", type=Path, required=True)
    state.add_argument("--stale-days", type=int, default=DEFAULT_STALE_DAYS)
    apply = sub.add_parser("apply")
    apply.add_argument("--artifact", type=Path, required=True)
    args = parser.parse_args()
    executor = TursoExecutor(
        os.environ.get("TURSO_DATABASE_URL", ""),
        os.environ.get("TURSO_AUTH_TOKEN", ""),
    )
    if args.command == "state":
        result = command_state(executor, args.output, stale_days=args.stale_days)
    else:
        result = command_apply(executor, args.artifact)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (PriceSmartSpecsPersistenceError, SnapshotError, OSError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc
