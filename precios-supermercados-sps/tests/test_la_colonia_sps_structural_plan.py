from __future__ import annotations

import json
from urllib.parse import urlencode

import pytest

from precios_supermercados.locations import LocationConfig, LocationGranularity
from precios_supermercados.scrapers.la_colonia_sps_facet_context import (
    EphemeralSpsRequestContextCollector,
    confirmed_sps_facet_binding,
    fingerprint_context_value,
)
from precios_supermercados.scrapers.la_colonia_sps_structural_plan import (
    SpsStructuralFacetPlan,
    SpsStructuralPlanError,
    build_sps_structural_facet_plan,
)

GRAPHQL_URL = "https://www.lacolonia.com/_v/segment/graphql/v1"
RAW_REGION = "opaque-region-for-structural-plan-test"


class FakeRequest:
    def __init__(self, *, url=GRAPHQL_URL, headers=None, body=None):
        self.url = url
        self.headers = headers or {}
        self._body = body

    @property
    def post_data_json(self):
        return self._body


def binding_for(raw=RAW_REGION):
    return confirmed_sps_facet_binding(
        LocationConfig(
            location_id="la_colonia_sps",
            supermarket_id="la_colonia",
            city_id="sps",
            city_name="San Pedro Sula",
            granularity=LocationGranularity.CITY,
            is_available=True,
            in_scope=True,
            extraction_enabled=False,
            technical_binding_confirmed=True,
            source_location_key=(
                "request:regionid:sha256:" + fingerprint_context_value(raw)
            ),
            evidence="location_binding_radiography:sha256:" + "c" * 64,
        )
    )


def context_for(request: FakeRequest, raw=RAW_REGION):
    binding = binding_for(raw)
    collector = EphemeralSpsRequestContextCollector()
    collector.observe_request(request)
    return binding, collector.resolve(binding)


def test_builds_exact_root_tree_pair_with_one_sps_context() -> None:
    binding, context = context_for(
        FakeRequest(headers={"X-VTEX-Region": RAW_REGION})
    )

    plan = build_sps_structural_facet_plan(context, binding=binding)

    assert isinstance(plan, SpsStructuralFacetPlan)
    assert plan.location_id == "la_colonia_sps"
    assert plan.city_name == "San Pedro Sula"
    assert plan.binding_source_key == binding.source_key
    assert plan.binding_evidence == binding.evidence
    assert plan.context_fingerprint == binding.expected_fingerprint
    assert plan.placement.value == "header"
    assert plan.wire_key == "X-VTEX-Region"
    assert plan.value_path == ()
    assert tuple(item.request_kind for item in plan.requests) == (
        "root_total",
        "category_tree",
    )
    assert tuple(item.sequence for item in plan.requests) == (1, 2)
    assert plan.requests[0].canonical_request_digest != plan.requests[1].canonical_request_digest
    assert plan.requires_same_browser_context is True
    assert plan.network_executed is False
    assert plan.production_authority is False
    assert plan.catalog_accepted is False
    assert plan.extraction_enabled is False


def test_public_plan_is_stable_sanitized_and_contains_no_raw_region() -> None:
    binding, context = context_for(
        FakeRequest(headers={"X-VTEX-Region": RAW_REGION})
    )
    first = build_sps_structural_facet_plan(context, binding=binding)
    second = build_sps_structural_facet_plan(context, binding=binding)

    assert first.digest == second.digest
    assert len(first.digest) == 64
    public = first.public_dict()
    rendered = json.dumps(public, ensure_ascii=False, sort_keys=True)
    assert public["plan_digest"] == first.digest
    assert public["network_executed"] is False
    assert public["production_authority"] is False
    assert public["catalog_accepted"] is False
    assert public["extraction_enabled"] is False
    assert public["raw_values_exposed"] is False
    assert RAW_REGION not in rendered
    assert RAW_REGION not in repr(first)
    assert RAW_REGION not in repr(first.requests[0])


def test_query_context_produces_pair_with_same_direct_placement() -> None:
    binding, context = context_for(
        FakeRequest(url=GRAPHQL_URL + "?regionId=" + RAW_REGION)
    )

    plan = build_sps_structural_facet_plan(context, binding=binding)

    assert plan.placement.value == "query"
    assert plan.wire_key == "regionId"
    assert plan.value_path == ()
    assert all(item.wire.placement.value == "query" for item in plan.requests)
    assert all(item.wire.wire_key == "regionId" for item in plan.requests)


def test_nested_query_context_stays_fail_closed_until_transport_is_demonstrated() -> None:
    variables = json.dumps(
        {"delivery": {"regionId": RAW_REGION}},
        separators=(",", ":"),
    )
    binding, context = context_for(
        FakeRequest(url=GRAPHQL_URL + "?" + urlencode({"variables": variables}))
    )

    with pytest.raises(
        SpsStructuralPlanError,
        match="sps_structural_plan_wire_sps_region_nested_query_transport_not_supported",
    ):
        build_sps_structural_facet_plan(context, binding=binding)


def test_body_context_stays_fail_closed_for_fixed_get_plan() -> None:
    binding, context = context_for(
        FakeRequest(body={"variables": {"regionId": RAW_REGION}})
    )

    with pytest.raises(
        SpsStructuralPlanError,
        match="sps_structural_plan_wire_sps_region_body_transport_not_supported",
    ):
        build_sps_structural_facet_plan(context, binding=binding)


def test_context_from_different_binding_cannot_build_plan() -> None:
    first_binding, context = context_for(
        FakeRequest(headers={"X-VTEX-Region": RAW_REGION})
    )
    other_binding = binding_for("different-region")
    assert first_binding.source_key != other_binding.source_key

    with pytest.raises(
        SpsStructuralPlanError,
        match="sps_structural_plan_context_sps_binding_changed",
    ):
        build_sps_structural_facet_plan(context, binding=other_binding)


def test_constructor_rejects_binding_source_that_does_not_match_context() -> None:
    binding, context = context_for(
        FakeRequest(headers={"X-VTEX-Region": RAW_REGION})
    )
    valid = build_sps_structural_facet_plan(context, binding=binding)

    with pytest.raises(
        SpsStructuralPlanError,
        match="sps_structural_plan_binding_context_mismatch",
    ):
        SpsStructuralFacetPlan(
            location_id=valid.location_id,
            city_name=valid.city_name,
            binding_source_key=(
                "request:regionid:sha256:" + fingerprint_context_value("other")
            ),
            binding_evidence=valid.binding_evidence,
            context_fingerprint=valid.context_fingerprint,
            placement=valid.placement,
            wire_key=valid.wire_key,
            value_path=valid.value_path,
            requests=valid.requests,
        )


def test_constructor_rejects_invalid_request_member_with_controlled_error() -> None:
    binding, context = context_for(
        FakeRequest(headers={"X-VTEX-Region": RAW_REGION})
    )
    valid = build_sps_structural_facet_plan(context, binding=binding)

    with pytest.raises(
        SpsStructuralPlanError,
        match="sps_structural_plan_request_invalid",
    ):
        SpsStructuralFacetPlan(
            location_id=valid.location_id,
            city_name=valid.city_name,
            binding_source_key=valid.binding_source_key,
            binding_evidence=valid.binding_evidence,
            context_fingerprint=valid.context_fingerprint,
            placement=valid.placement,
            wire_key=valid.wire_key,
            value_path=valid.value_path,
            requests=(valid.requests[0], object()),  # type: ignore[arg-type]
        )


def test_plan_constructor_cannot_grant_commercial_flags() -> None:
    binding, context = context_for(
        FakeRequest(headers={"X-VTEX-Region": RAW_REGION})
    )
    valid = build_sps_structural_facet_plan(context, binding=binding)

    with pytest.raises(SpsStructuralPlanError, match="sps_structural_plan_catalog_acceptance_forbidden"):
        SpsStructuralFacetPlan(
            location_id=valid.location_id,
            city_name=valid.city_name,
            binding_source_key=valid.binding_source_key,
            binding_evidence=valid.binding_evidence,
            context_fingerprint=valid.context_fingerprint,
            placement=valid.placement,
            wire_key=valid.wire_key,
            value_path=valid.value_path,
            requests=valid.requests,
            catalog_accepted=True,
        )
