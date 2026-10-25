#!/usr/bin/env python3
"""Evalúa un artifact full-catalog de La Colonia SPS sin hacer red ni persistir."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from precios_supermercados.scrapers.la_colonia_operational_artifact import (  # noqa: E402
    OperationalCatalogArtifactError,
    assess_operational_catalog_artifact,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evalúa offline un full-catalog sanitizado de La Colonia SPS."
    )
    parser.add_argument("artifact", type=Path, help="Ruta al full-catalog.json")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Ruta opcional para escribir sólo el assessment sanitizado.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        artifact = json.loads(args.artifact.read_text(encoding="utf-8"))
        assessment = assess_operational_catalog_artifact(artifact)
    except (OSError, json.JSONDecodeError, OperationalCatalogArtifactError) as exc:
        print(json.dumps({"result": "invalid", "reason": str(exc)}, ensure_ascii=False))
        return 2

    payload = asdict(assessment)
    payload["run_status"] = assessment.run_status.value
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if assessment.technical_catalog_complete else 3


if __name__ == "__main__":
    raise SystemExit(main())
