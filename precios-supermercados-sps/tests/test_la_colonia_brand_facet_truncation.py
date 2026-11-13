"""Regresión run 43415891750: VTEX recorta la lista global de marcas de La Colonia.

La lista global de facets ``brand`` se cortó en 1,498 valores y las últimas marcas
alfabéticas (9–10 productos) no cayeron en ningún bucket. Las marcas faltantes se
recuperan pidiendo los facets por ``category-2``; la cobertura exacta sigue siendo
obligatoria después.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import obtener_catalogo_sps_la_colonia_operativo as operational  # noqa: E402


def _brand(value: str, quantity: int) -> dict:
    return {"key": "brand", "value": value, "quantity": quantity}


def _category(value: str, quantity: int) -> dict:
    return {"key": "category-2", "value": value, "quantity": quantity}


def _tree(*values: dict, total: int = 1, sampling: bool = False) -> dict:
    return {
        "productSearch": {"recordsFiltered": total},
        "facets": {"sampling": sampling, "facets": [{"type": "TEXT", "values": list(values)}]},
    }


class _Request:
    def __init__(self, by_category: dict[str, dict]):
        self.by_category = by_category
        self.urls: list[str] = []

    def get(self, url, **_kwargs):
        self.urls.append(url)
        variables = json.loads(parse_qs(urlparse(url).query)["variables"][0])
        selected = {item["key"]: item["value"] for item in variables["selectedFacets"]}
        return {"data": self.by_category[selected["category-2"]]}


class _Context:
    def __init__(self, by_category):
        self.request = _Request(by_category)


@pytest.fixture(autouse=True)
def _raw_json(monkeypatch):
    monkeypatch.setattr(
        operational.core, "_read_json_response", lambda response, *_a, **_k: response
    )


def _normalized(*values: dict) -> dict:
    return operational.core.LaColoniaFacetDiscoveryAdapter._normalize_tree(
        _tree(*values, total=100)
    )


def test_no_extra_requests_when_global_brands_cover_root():
    context = _Context({})
    diagnostic = {"product_requests_completed": 1}
    brands = [_brand("alpha", 60), _brand("beta", 40)]
    result = operational._complete_brand_values(
        context=context,
        normalized=_normalized(*brands, _category("abarrotes", 100)),
        brand_values=brands,
        root_total=100,
        diagnostic=diagnostic,
    )
    assert result == brands
    assert context.request.urls == []


def test_truncated_global_brand_list_recovers_missing_brands_by_category():
    brands = [_brand("alpha", 60), _brand("beta", 35)]
    context = _Context(
        {
            "abarrotes": _tree(_brand("alpha", 60), _brand("zambrano", 1), total=61),
            "hogar": _tree(_brand("beta", 35), _brand("zar", 3), _brand("zepol", 1), total=39),
        }
    )
    diagnostic = {"product_requests_completed": 1}
    result = operational._complete_brand_values(
        context=context,
        normalized=_normalized(*brands, _category("abarrotes", 61), _category("hogar", 39)),
        brand_values=brands,
        root_total=100,
        diagnostic=diagnostic,
    )
    assert {item["value"]: item["quantity"] for item in result} == {
        "alpha": 60,
        "beta": 35,
        "zambrano": 1,
        "zar": 3,
        "zepol": 1,
    }
    assert sum(item["quantity"] for item in result) == 100
    assert len(context.request.urls) == 2
    assert diagnostic["brand_values_recovered_from_categories"] == 3
    assert diagnostic["product_requests_completed"] == 3
    partitions = operational._build_brand_buckets(result, page_size=50)
    assert sum(partition.quantity for partition in partitions) == 100


def test_brand_split_across_categories_sums_quantities():
    brands = [_brand("alpha", 90)]
    context = _Context(
        {
            "a": _tree(_brand("alpha", 45), _brand("zest", 2), total=47),
            "b": _tree(_brand("alpha", 45), _brand("zest", 3), total=48),
        }
    )
    result = operational._complete_brand_values(
        context=context,
        normalized=_normalized(*brands, _category("a", 47), _category("b", 48)),
        brand_values=brands,
        root_total=95,
        diagnostic={"product_requests_completed": 0},
    )
    assert {item["value"]: item["quantity"] for item in result}["zest"] == 5


def test_category_sampling_stays_fail_closed():
    brands = [_brand("alpha", 90)]
    context = _Context({"a": _tree(_brand("zest", 2), total=2, sampling=True)})
    with pytest.raises(operational.full.FullCatalogError) as exc:
        operational._complete_brand_values(
            context=context,
            normalized=_normalized(*brands, _category("a", 92)),
            brand_values=brands,
            root_total=92,
            diagnostic={"product_requests_completed": 0},
        )
    assert exc.value.reason == "brand_facets_sampling_detected"


def test_category_discovery_respects_request_budget():
    brands = [_brand("alpha", 90)]
    context = _Context({"a": _tree(_brand("zest", 2), total=2)})
    with pytest.raises(operational.full.FullCatalogError) as exc:
        operational._complete_brand_values(
            context=context,
            normalized=_normalized(*brands, _category("a", 92)),
            brand_values=brands,
            root_total=92,
            diagnostic={"product_requests_completed": operational.core.MAX_PLANNED_PRODUCT_REQUESTS},
        )
    assert exc.value.reason == "partition_request_budget_exceeded"
    assert context.request.urls == []
