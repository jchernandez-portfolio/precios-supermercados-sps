#!/usr/bin/env python3
"""Captura read-only completa del catálogo público SPS de Colonial."""
from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from obtener_catalogo_colonial import Download, collect

MAX_REQUESTS_HARD = 450
MAX_DEADLINE_SECONDS = 1200


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-read-only", action="store_true")
    parser.add_argument("--allow-full-catalog", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-requests", type=int, default=MAX_REQUESTS_HARD)
    parser.add_argument("--deadline-seconds", type=int, default=MAX_DEADLINE_SECONDS)
    args = parser.parse_args()
    if not args.live_read_only or not args.allow_full_catalog:
        raise SystemExit("explicit_live_full_catalog_authorization_required")
    if not 1 <= args.max_requests <= MAX_REQUESTS_HARD:
        raise SystemExit("request_budget_invalid")
    if not 1 <= args.deadline_seconds <= MAX_DEADLINE_SECONDS:
        raise SystemExit("deadline_invalid")

    deadline_utc = datetime.now(UTC) + timedelta(seconds=args.deadline_seconds)
    downloader = Download(args.output, deadline_utc, [], args.max_requests, args.deadline_seconds)
    try:
        snapshot = collect(downloader.get)
        times = [
            datetime.fromisoformat(row["observed_at"])
            for row in downloader.records
            if row.get("status") == 200
        ]
        if not times:
            raise SystemExit("no_successful_source_observations")
        snapshot["observation_started_at_utc"] = min(times).strftime("%Y-%m-%dT%H:%M:%SZ")
        snapshot["observed_at_utc"] = max(times).strftime("%Y-%m-%dT%H:%M:%SZ")
        snapshot["metrics"] = dict(
            downloader.metrics,
            elapsed_seconds=round(time.monotonic() - downloader.started, 3),
        )
        target = args.output / "full-catalog.json"
        target.write_text(
            json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        print(json.dumps({key: value for key, value in snapshot.items() if key != "products"}, ensure_ascii=False, sort_keys=True))
    finally:
        downloader.save()
        downloader.session.close()


if __name__ == "__main__":
    main()
