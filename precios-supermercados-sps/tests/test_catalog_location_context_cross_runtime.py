from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote, urlsplit

import pytest

from precios_supermercados.catalog_location_context import CatalogEdgeLocationContext
from precios_supermercados.scrapers.la_colonia_graphql import build_product_search_url
from precios_supermercados.scrapers.la_colonia_sps_facet_context import (
    RequestContextPlacement,
    fingerprint_context_value,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (
    PROJECT_ROOT
    / "edge"
    / "cloudflare"
    / "test"
    / "catalog-location-context-fixture-cli.mjs"
)


def _node() -> str:
    executable = shutil.which("node")
    assert executable is not None, "Node.js es obligatorio para validar el wire cross-runtime"
    return executable


def _canonical_wire_fingerprint(url: str, headers: dict[str, str]) -> str:
    payload = json.dumps(
        {
            "headers": dict(sorted(headers.items(), key=lambda item: item[0].casefold())),
            "method": "GET",
            "url": url,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _query_wire(base_url: str, wire_key: str, raw: str) -> str:
    parsed = urlsplit(base_url)
    assert parsed.fragment == ""
    separator = "&" if parsed.query else "?"
    return (
        f"{base_url}{separator}"
        f"{quote(wire_key, safe='-._~')}={quote(raw, safe='-._~')}"
    )


@pytest.mark.parametrize(
    "raw",
    (
        "synthetic-region-alpha",
        "synthetic region~with*reserved+slash/value?x=y",
        "área-sps-ñ-01",
    ),
)
def test_query_wire_is_identical_between_python_and_worker(raw: str) -> None:
    base_url = build_product_search_url(
        page=1,
        page_size=10,
        order_by="OrderByNameASC",
    )
    wire_key = "regionId"
    fetch_url = _query_wire(base_url, wire_key, raw)
    fingerprint = fingerprint_context_value(raw)
    context = CatalogEdgeLocationContext(
        location_id="la_colonia_sps",
        binding_source_key=f"request:regionid:sha256:{fingerprint}",
        binding_evidence="location_binding_radiography:sha256:" + "c" * 64,
        context_fingerprint=fingerprint,
        placement=RequestContextPlacement.QUERY,
        wire_key=wire_key,
        value_path=(),
        wire_request_fingerprint=_canonical_wire_fingerprint(fetch_url, {}),
        _raw_value=raw,
    )

    result = subprocess.run(
        [_node(), str(FIXTURE)],
        cwd=PROJECT_ROOT,
        input=json.dumps(
            {"originUrl": base_url, "locationContext": context.wire_dict()},
            ensure_ascii=False,
        ),
        text=True,
        capture_output=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    parsed = json.loads(result.stdout)
    assert parsed["ok"] is True
    assert parsed["fetchUrl"] == fetch_url
    assert parsed["fetchHeaders"] == {}
    assert parsed["receiptContext"]["wireRequestFingerprint"] == context.wire_request_fingerprint


def test_header_wire_is_identical_between_python_and_worker() -> None:
    raw = "synthetic-header-region"
    base_url = build_product_search_url(
        page=2,
        page_size=10,
        order_by="OrderByNameDESC",
    )
    wire_key = "X-VTEX-Region"
    fingerprint = fingerprint_context_value(raw)
    headers = {wire_key: raw}
    context = CatalogEdgeLocationContext(
        location_id="la_colonia_sps",
        binding_source_key=f"request:regionid:sha256:{fingerprint}",
        binding_evidence="location_binding_radiography:sha256:" + "d" * 64,
        context_fingerprint=fingerprint,
        placement=RequestContextPlacement.HEADER,
        wire_key=wire_key,
        value_path=(),
        wire_request_fingerprint=_canonical_wire_fingerprint(base_url, headers),
        _raw_value=raw,
    )

    result = subprocess.run(
        [_node(), str(FIXTURE)],
        cwd=PROJECT_ROOT,
        input=json.dumps(
            {"originUrl": base_url, "locationContext": context.wire_dict()},
            ensure_ascii=False,
        ),
        text=True,
        capture_output=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    parsed = json.loads(result.stdout)
    assert parsed["fetchUrl"] == base_url
    assert parsed["fetchHeaders"] == headers
