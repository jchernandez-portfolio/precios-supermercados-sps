#!/usr/bin/env python3
"""Ejecuta el catálogo operativo existente de La Colonia para Tegucigalpa.

No duplica el scraper: adapta únicamente la ciudad/ubicación del runner SPS ya
probado. Reintenta de forma acotada sólo errores transitorios de páginas de
producto y conserva fail-closed para cualquier otro fallo.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import obtener_catalogo_sps_la_colonia_operativo as operational  # noqa: E402
import obtener_catalogo_sps_la_colonia_operativo_v2 as recovery  # noqa: E402

TARGET_CITY = "Tegucigalpa"
LOCATION_ID = "la_colonia_tgu"
CATALOG_TYPE = "la_colonia_tgu_full_read_only"
MAX_GRAPHQL_RETRIES = 2
GRAPHQL_RETRY_DELAY_SECONDS = 2.0


def _with_product_graphql_retry(
    fetch_page: Callable[..., int],
    **kwargs: Any,
) -> int:
    """Reintenta sólo fallos transitorios observados en productSearchV3 de TGU."""

    for attempt in range(MAX_GRAPHQL_RETRIES + 1):
        try:
            return fetch_page(**kwargs)
        except operational.full.FullCatalogError as exc:
            if (
                exc.reason != "product_search_graphql_errors"
                or attempt >= MAX_GRAPHQL_RETRIES
            ):
                raise
            time.sleep(GRAPHQL_RETRY_DELAY_SECONDS)
        except PlaywrightTimeoutError:
            if attempt >= MAX_GRAPHQL_RETRIES:
                raise
            time.sleep(GRAPHQL_RETRY_DELAY_SECONDS)
    raise AssertionError("unreachable")


def main(argv: list[str] | None = None) -> int:
    original_ensure_city = operational.ensure_operational_city
    original_run_catalog = operational._run_catalog
    original_fetch_page = operational._fetch_known_total_page
    original_location_id = operational.full.passive.MVP_LOCATION_ID
    original_target_city = operational.full.passive.TARGET_CITY

    def ensure_tgu(page: Any, *, max_dom_reresolutions: int = 1) -> str | None:
        return original_ensure_city(
            page,
            TARGET_CITY,
            max_dom_reresolutions=max_dom_reresolutions,
        )

    def fetch_tgu_page(**kwargs: Any) -> int:
        return _with_product_graphql_retry(original_fetch_page, **kwargs)

    def run_tgu_catalog(*, page_size: int, delay_seconds: float) -> dict[str, Any]:
        artifact = dict(
            original_run_catalog(
                page_size=page_size,
                delay_seconds=delay_seconds,
            )
        )
        artifact["catalog_type"] = CATALOG_TYPE
        artifact["location_id"] = LOCATION_ID
        artifact["city"] = TARGET_CITY
        return artifact

    operational.ensure_operational_city = ensure_tgu
    operational._fetch_known_total_page = fetch_tgu_page
    operational._run_catalog = run_tgu_catalog
    operational.full.passive.MVP_LOCATION_ID = LOCATION_ID
    operational.full.passive.TARGET_CITY = TARGET_CITY
    try:
        return recovery.main(argv)
    finally:
        operational.full.passive.TARGET_CITY = original_target_city
        operational.full.passive.MVP_LOCATION_ID = original_location_id
        operational._run_catalog = original_run_catalog
        operational._fetch_known_total_page = original_fetch_page
        operational.ensure_operational_city = original_ensure_city


if __name__ == "__main__":
    raise SystemExit(main())
