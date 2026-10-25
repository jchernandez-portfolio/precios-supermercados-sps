from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from precios_supermercados.la_colonia_edge_request import (
    EXPECTED_GRAPHQL_QUERY_SHA256,
    validate_la_colonia_edge_request,
)
from precios_supermercados.scrapers.la_colonia_graphql import build_product_search_url

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLI = PROJECT_ROOT / "edge" / "cloudflare" / "test" / "fixture-cli.mjs"


def _node() -> str:
    executable = shutil.which("node")
    assert executable is not None, "Node.js es obligatorio para el contrato cross-runtime"
    return executable


@pytest.mark.parametrize(
    "url",
    [
        build_product_search_url(page=1, page_size=1, order_by="OrderByReleaseDateDESC"),
        build_product_search_url(page=3, page_size=50, order_by="OrderByPriceDESC"),
        build_product_search_url(page=4, page_size=25, query="bebidas", category_map="category-2", order_by="OrderByNameASC"),
        build_product_search_url(page=2, page_size=10, full_text="café molido", order_by="OrderByPriceASC"),
    ],
)
def test_python_y_javascript_calculan_el_mismo_request_canonico(url: str) -> None:
    python_result = validate_la_colonia_edge_request(url)
    completed = subprocess.run(
        [_node(), str(CLI)],
        cwd=PROJECT_ROOT,
        input=json.dumps(
            {
                "url": url,
                "expectedGraphqlQuerySha256": EXPECTED_GRAPHQL_QUERY_SHA256,
            }
        ),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    javascript_result = json.loads(completed.stdout)
    assert javascript_result["ok"] is True
    assert javascript_result["from"] == python_result.from_index
    assert javascript_result["to"] == python_result.to_index
    assert javascript_result["orderBy"] == python_result.order_by
    assert javascript_result["canonicalRequestSha256"] == python_result.canonical_request_sha256
