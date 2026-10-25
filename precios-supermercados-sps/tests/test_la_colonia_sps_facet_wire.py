from __future__ import annotations

import json
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from precios_supermercados.locations import LocationConfig, LocationGranularity
from precios_supermercados.scrapers.la_colonia_facet_discovery import CATALOG_CATEGORIES_V1
from precios_supermercados.scrapers.la_colonia_sps_facet_context import (
    EphemeralSpsRequestContextCollector,
    SpsFacetContextError,
    confirmed_sps_facet_binding,
    fingerprint_context_value,
    prepare_sps_facet_execution,
)
from precios_supermercados.scrapers.la_colonia_sps_facet_wire import (
    SpsFacetWireError,
    prepare_sps_facet_wire_request,
)

GRAPHQL_URL = "https://www.lacolonia.com/_v/segment/graphql/v1"
RAW_REGION = "opaque-region-for-wire-test"


class FakeRequest:
    def __init__(self, *, url=GRAPHQL_URL, headers=None, body=None):
        self.url = url
        self.headers = headers or {}
        self._body = body

    @property
    def post_data_json(self):
        return self._body


def binding_for(raw=RAW_REGION):
    location = LocationConfig(
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
        evidence="location_binding_radiography:sha256:" + "b" * 64,
    )
    return confirmed_sps_facet_binding(location)


def execution_from(request: FakeRequest):
    binding = binding_for()
    collector = EphemeralSpsRequestContextCollector()
    collector.observe_request(request)
    context = collector.resolve(binding)
    return prepare_sps_facet_execution(
        CATALOG_CATEGORIES_V1.requests[0],
        context,
        binding=binding,
    )


def test_query_placement_is_applied_only_after_verified_context():
    execution = execution_from(
        FakeRequest(url=GRAPHQL_URL + "?regionId=" + RAW_REGION)
    )

    wire = prepare_sps_facet_wire_request(execution)
    public = wire.public_dict()
    revealed_url, revealed_headers = wire.reveal_for_transport(execution.binding)

    params = parse_qs(urlsplit(revealed_url).query)
    assert params["regionId"] == [RAW_REGION]
    assert revealed_headers == {}
    assert public["placement"] == "query"
    assert public["value_path"] == []
    assert public["requires_same_browser_context"] is True
    assert public["network_executed"] is False
    assert public["production_authority"] is False
    assert public["catalog_accepted"] is False
    assert public["extraction_enabled"] is False
    assert public["raw_values_exposed"] is False
    assert RAW_REGION not in json.dumps(public, sort_keys=True)
    assert RAW_REGION not in repr(wire)


def test_header_placement_is_applied_to_exact_observed_wire_key():
    execution = execution_from(
        FakeRequest(headers={"X-VTEX-Region": RAW_REGION})
    )

    wire = prepare_sps_facet_wire_request(execution)
    revealed_url, revealed_headers = wire.reveal_for_transport(execution.binding)

    assert revealed_url == execution.structural_request.source_url
    assert dict(revealed_headers) == {"X-VTEX-Region": RAW_REGION}
    assert wire.public_dict()["placement"] == "header"
    assert wire.public_dict()["wire_key"] == "X-VTEX-Region"
    assert wire.public_dict()["value_path"] == []
    assert wire.public_dict()["requires_same_browser_context"] is True
    assert RAW_REGION not in repr(wire)


def test_nested_query_context_does_not_guess_how_to_mutate_graphql_variables():
    variables = json.dumps(
        {"context": {"regionId": RAW_REGION}},
        separators=(",", ":"),
    )
    execution = execution_from(
        FakeRequest(url=GRAPHQL_URL + "?" + urlencode({"variables": variables}))
    )

    with pytest.raises(
        SpsFacetWireError,
        match="sps_region_nested_query_transport_not_supported",
    ):
        prepare_sps_facet_wire_request(execution)


def test_body_placement_does_not_silently_change_the_get_contract():
    execution = execution_from(
        FakeRequest(body={"variables": {"regionId": RAW_REGION}})
    )

    with pytest.raises(
        SpsFacetWireError,
        match="sps_region_body_transport_not_supported",
    ):
        prepare_sps_facet_wire_request(execution)


def test_wire_request_fingerprint_is_stable_but_does_not_expose_region():
    first = prepare_sps_facet_wire_request(
        execution_from(FakeRequest(headers={"X-VTEX-Region": RAW_REGION}))
    )
    second = prepare_sps_facet_wire_request(
        execution_from(FakeRequest(headers={"X-VTEX-Region": RAW_REGION}))
    )

    assert first.wire_request_fingerprint == second.wire_request_fingerprint
    assert len(first.wire_request_fingerprint) == 64
    assert RAW_REGION not in first.wire_request_fingerprint


def test_wire_material_cannot_be_revealed_with_another_binding():
    execution = execution_from(FakeRequest(headers={"X-VTEX-Region": RAW_REGION}))
    wire = prepare_sps_facet_wire_request(execution)
    other = binding_for("different")

    with pytest.raises(SpsFacetWireError, match="sps_binding_changed"):
        wire.reveal_for_transport(other)


def test_unverified_context_never_reaches_wire_preparation():
    binding = binding_for()
    collector = EphemeralSpsRequestContextCollector()
    collector.observe_request(FakeRequest(headers={"X-VTEX-Region": "wrong"}))

    with pytest.raises(
        SpsFacetContextError,
        match="sps_region_context_fingerprint_mismatch",
    ):
        collector.resolve(binding)
