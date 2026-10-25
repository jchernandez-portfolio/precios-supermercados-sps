#!/usr/bin/env python3
"""Ejecuta particiones por marca con una sola re-resolución DOM de SPS."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import obtener_catalogo_sps_la_colonia_particionado_v3 as brand  # noqa: E402


def _single_city_reresolution_wrapper(original: Callable[..., Any]) -> Callable[..., Any]:
    """Reintenta una sola vez cuando el selector SPS aún no apareció en el DOM."""

    def verify(page: Any, context: Any, collector: Any) -> Any:
        try:
            return original(page, context, collector)
        except Exception as exc:
            if "target_city_not_found" not in str(exc).casefold():
                raise
        try:
            page.reload(wait_until="domcontentloaded", timeout=20_000)
        except Exception:
            page.goto(
                brand.frontier.base.full.radiography.TARGET_URL,
                wait_until="domcontentloaded",
                timeout=20_000,
            )
        page.wait_for_timeout(350)
        try:
            return original(page, context, collector)
        except Exception as exc:
            if "target_city_not_found" in str(exc).casefold():
                raise brand.frontier.base.full.FullCatalogError(
                    "target_city_not_found_after_single_reresolution"
                ) from exc
            raise

    return verify


def main(argv: list[str] | None = None) -> int:
    target = brand.frontier.base.full
    original = target._verify_sps_binding
    target._verify_sps_binding = _single_city_reresolution_wrapper(original)
    try:
        return brand.main(argv)
    finally:
        target._verify_sps_binding = original


if __name__ == "__main__":
    raise SystemExit(main())
