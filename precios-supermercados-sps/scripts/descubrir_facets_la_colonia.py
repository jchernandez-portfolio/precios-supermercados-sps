#!/usr/bin/env python3
"""Ejecuta el plan cerrado y confiable de facet discovery de La Colonia."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from precios_supermercados.scrapers.la_colonia_facet_discovery import (  # noqa: E402
    FACET_DISCOVERY_MAX_ARTIFACT_BYTES,
)
from precios_supermercados.scrapers.la_colonia_facet_discovery_adapter import (  # noqa: E402
    LaColoniaFacetDiscoveryAdapter,
)
from precios_supermercados.scrapers.la_colonia_facet_discovery_runtime import (  # noqa: E402
    OUTCOME_INCOMPLETE_TREE,
    OUTCOME_INVALID_QUANTITIES,
    OUTCOME_NO_POSITIVE,
    OUTCOME_OVER_BUDGET,
    OUTCOME_SAMPLING,
    OUTCOME_WITHIN_BUDGET,
    FacetDiscoveryRuntime,
    render_facet_discovery_markdown,
    serialize_facet_discovery_summary,
)

JSON_ARTIFACT = Path("facet-discovery-summary.json")
MARKDOWN_ARTIFACT = Path("facet-discovery-summary.md")

EXIT_WITHIN_BUDGET = 0
EXIT_OVER_BUDGET = 2
EXIT_SAMPLING = 3
EXIT_INCOMPLETE = 4
EXIT_INVALID = 5
EXIT_SECURITY = 6


def _exit_code(summary: dict) -> int:
    outcome = summary.get("discovery_outcome")
    if outcome == OUTCOME_WITHIN_BUDGET:
        return EXIT_WITHIN_BUDGET
    if outcome == OUTCOME_OVER_BUDGET:
        return EXIT_OVER_BUDGET
    if outcome == OUTCOME_SAMPLING:
        return EXIT_SAMPLING
    if outcome in {OUTCOME_INCOMPLETE_TREE, OUTCOME_NO_POSITIVE}:
        return EXIT_INCOMPLETE
    if outcome == OUTCOME_INVALID_QUANTITIES:
        return EXIT_INVALID
    if summary.get("stop_reason") == "catalog_total_changed":
        return EXIT_INCOMPLETE
    return EXIT_INVALID


def _write_artifacts(summary: dict) -> None:
    json_bytes = serialize_facet_discovery_summary(summary)
    markdown_bytes = render_facet_discovery_markdown(summary).encode("utf-8")
    if len(json_bytes) > FACET_DISCOVERY_MAX_ARTIFACT_BYTES:
        raise ValueError("JSON facet discovery supera 64 KiB")
    if len(markdown_bytes) > FACET_DISCOVERY_MAX_ARTIFACT_BYTES:
        raise ValueError("Markdown facet discovery supera 64 KiB")
    if len(json_bytes) + len(markdown_bytes) > FACET_DISCOVERY_MAX_ARTIFACT_BYTES:
        raise ValueError("El conjunto de artefactos facet discovery supera 64 KiB")
    JSON_ARTIFACT.write_bytes(json_bytes)
    MARKDOWN_ARTIFACT.write_bytes(markdown_bytes)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--discovery-plan", required=True)
    parser.add_argument("--delay-seconds", required=True)
    args = parser.parse_args(argv)

    try:
        delay = float(args.delay_seconds)
        command = {
            "request_id": args.request_id,
            "supermarket": "la_colonia",
            "mode": "facet_discovery",
            "discovery_plan": args.discovery_plan,
            "delay_seconds": delay,
            "allow_full": False,
        }
        adapter = LaColoniaFacetDiscoveryAdapter()
        runtime = FacetDiscoveryRuntime(adapter, max_retries=0, max_requests=2)
        result = runtime.run(command)
        summary = dict(result.summary)
        _write_artifacts(summary)
        print(json.dumps({
            "discovery_completed": summary["discovery_completed"],
            "discovery_outcome": summary["discovery_outcome"],
            "within_request_limit": summary["within_request_limit"],
            "stop_reason": summary["stop_reason"],
        }, sort_keys=True))
        return _exit_code(summary)
    except (OSError, TypeError, ValueError):
        return EXIT_SECURITY


if __name__ == "__main__":
    raise SystemExit(main())
