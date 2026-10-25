from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from urllib.parse import parse_qsl, urlsplit

import pytest

from precios_supermercados.catalog_location_context import (
    CatalogEdgeLocationContext,
    CatalogLocationContextError,
    catalog_edge_location_context_for_request,
    prepare_sps_catalog_wire_request,
)
from precios_supermercados.la_colonia_edge_request import validate_la_colonia_edge_request
from precios_supermercados.scrapers.la_colonia_graphql import build_product_search_url
from precios_supermercados.scrapers.la_colonia_sps_structural_plan import (
    build_sps_structural_facet_plan,
)
from precios_supermercados.sps_context_bound_discovery import (
    bind_verified_structural_discovery_to_sps,
)
from precios_supermercados.structural_discovery_manifest import (
    build_verified_structural_discovery,
)

TESTS = Path(__file__).parent


def _helper(filename: str, module_name: str) -> ModuleType:
    path = TESTS / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


CONTEXT_HELPER = _helper(
    "test_sps_context_bound_discovery.py",
    "precios_sps_context_bound_discovery_helper_for_catalog",
)
PLAN_HELPER = CONTEXT_HELPER.PLAN_HELPER


def _catalog_request(page: int = 1):
    return validate_la_colonia_edge_request(
        build_product_search_url(
            page=page,
            page_size=5,
            query="supermercado",
            category_map="category-1",
            order_by="OrderByNameASC",
        )
    )


def _proof_for(fake_request):
    binding, context = PLAN_HELPER.context_for(fake_request)
    plan = build_sps_structural_facet_plan(context, binding=binding)
    root = CONTEXT_HELPER._contextual_platform("root_total", plan)
    tree = CONTEXT_HELPER._contextual_platform("category_tree", plan)
    discovery = build_verified_structural_discovery(
        root_total=root,
        category_tree=tree,
    )
    proof = bind_verified_structural_discovery_to_sps(
        discovery,
        {
            "root_total": root.observation,
            "category_tree": tree.observation,
        },
        plan,
        binding=binding,
    )
    return proof


def test_header_context_se_aplica_a_pagina_sin_exponer_region_raw() -> None:
    proof = _proof_for(
        PLAN_HELPER.FakeRequest(headers={"X-VTEX-Region": PLAN_HELPER.RAW_REGION})
    )
    request = _catalog_request()

    prepared = prepare_sps_catalog_wire_request(proof, request)
    context = catalog_edge_location_context_for_request(proof, request)

    assert prepared.location_id == "la_colonia_sps"
    assert prepared.base_request_digest == request.canonical_request_sha256
    assert prepared.placement.value == "header"
    assert prepared.wire_key == "X-VTEX-Region"
    assert prepared.value_path == ()
    assert prepared.wire_request_fingerprint == context.wire_request_fingerprint
    assert context.location_id == "la_colonia_sps"
    assert context.binding_source_key == proof.binding_source_key
    assert context.binding_evidence == proof.binding_evidence
    assert context.context_fingerprint == proof.context_fingerprint

    public = json.dumps(prepared.public_dict(), ensure_ascii=False, sort_keys=True)
    envelope_public = json.dumps(context.public_dict(), ensure_ascii=False, sort_keys=True)
    assert PLAN_HELPER.RAW_REGION not in public
    assert PLAN_HELPER.RAW_REGION not in envelope_public
    assert PLAN_HELPER.RAW_REGION not in repr(prepared)
    assert PLAN_HELPER.RAW_REGION not in repr(context)
    assert prepared.public_dict()["raw_values_exposed"] is False
    assert context.public_dict()["raw_values_exposed"] is False

    wire_url, wire_headers = prepared._reveal_for_gateway()
    assert wire_url == request.source_url
    assert wire_headers == {"X-VTEX-Region": PLAN_HELPER.RAW_REGION}
    assert context.wire_dict()["rawValue"] == PLAN_HELPER.RAW_REGION


def test_query_context_se_aplica_a_pagina_y_preserva_request_base() -> None:
    proof = _proof_for(
        PLAN_HELPER.FakeRequest(
            url=PLAN_HELPER.GRAPHQL_URL + "?regionId=" + PLAN_HELPER.RAW_REGION
        )
    )
    request = _catalog_request(page=2)

    prepared = prepare_sps_catalog_wire_request(proof, request)
    wire_url, wire_headers = prepared._reveal_for_gateway()

    assert prepared.base_request_digest == request.canonical_request_sha256
    assert prepared.placement.value == "query"
    assert prepared.wire_key == "regionId"
    assert wire_headers == {}
    pairs = parse_qsl(urlsplit(wire_url).query, keep_blank_values=True)
    region_values = [value for key, value in pairs if key == "regionId"]
    assert region_values == [PLAN_HELPER.RAW_REGION]
    assert "regionId" not in dict(parse_qsl(urlsplit(request.source_url).query))


def test_wire_fingerprint_es_distinto_por_pagina_aunque_binding_sea_el_mismo() -> None:
    proof = _proof_for(
        PLAN_HELPER.FakeRequest(headers={"X-VTEX-Region": PLAN_HELPER.RAW_REGION})
    )

    first = prepare_sps_catalog_wire_request(proof, _catalog_request(page=1))
    second = prepare_sps_catalog_wire_request(proof, _catalog_request(page=2))

    assert first.context_fingerprint == second.context_fingerprint
    assert first.binding_source_key == second.binding_source_key
    assert first.wire_request_fingerprint != second.wire_request_fingerprint


def test_no_acepta_request_no_validado_ni_proof_caller_controlled() -> None:
    proof = _proof_for(
        PLAN_HELPER.FakeRequest(headers={"X-VTEX-Region": PLAN_HELPER.RAW_REGION})
    )

    with pytest.raises(CatalogLocationContextError) as invalid_request:
        prepare_sps_catalog_wire_request(proof, object())  # type: ignore[arg-type]
    assert invalid_request.value.code == "validated_catalog_request_required"

    with pytest.raises(CatalogLocationContextError) as invalid_proof:
        prepare_sps_catalog_wire_request(object(), _catalog_request())  # type: ignore[arg-type]
    assert invalid_proof.value.code == "verified_sps_structural_context_required"


def test_envelope_no_admite_fingerprint_que_no_corresponde_al_raw() -> None:
    proof = _proof_for(
        PLAN_HELPER.FakeRequest(headers={"X-VTEX-Region": PLAN_HELPER.RAW_REGION})
    )
    valid = catalog_edge_location_context_for_request(proof, _catalog_request())

    with pytest.raises(CatalogLocationContextError) as captured:
        CatalogEdgeLocationContext(
            location_id=valid.location_id,
            binding_source_key=valid.binding_source_key,
            binding_evidence=valid.binding_evidence,
            context_fingerprint=valid.context_fingerprint,
            placement=valid.placement,
            wire_key=valid.wire_key,
            value_path=valid.value_path,
            wire_request_fingerprint=valid.wire_request_fingerprint,
            _raw_value="other-region",
        )
    assert captured.value.code == "catalog_context_raw_fingerprint_mismatch"
