from __future__ import annotations

import json
from urllib.parse import urlencode

import pytest

from precios_supermercados.location_binding_radiography import (
    ContextStage,
    analyze_location_binding,
)
from precios_supermercados.locations import (
    LA_COLONIA_SPS,
    LocationConfig,
    LocationGranularity,
)
from precios_supermercados.scrapers.la_colonia_facet_discovery import (
    CATALOG_CATEGORIES_V1,
)
from precios_supermercados.scrapers.la_colonia_sps_facet_context import (
    FACET_CONTEXT_HOST,
    FACET_CONTEXT_PATH,
    EphemeralSpsRequestContextCollector,
    RequestContextPlacement,
    SpsFacetContextError,
    confirmed_sps_facet_binding,
    fingerprint_context_value,
    prepare_sps_facet_execution,
)

GRAPHQL_URL = f"https://{FACET_CONTEXT_HOST}{FACET_CONTEXT_PATH}"


class FakeRequest:
    def __init__(self, *, url=GRAPHQL_URL, headers=None, body=None):
        self.url = url
        self.headers = headers or {}
        self._body = body

    @property
    def post_data_json(self):
        return self._body


RAW_REGION = "opaque-region-context-for-offline-test"
RAW_OTHER = "different-region-context"


def synthetic_location(raw_value=RAW_REGION) -> LocationConfig:
    digest = fingerprint_context_value(raw_value)
    return LocationConfig(
        location_id="la_colonia_sps",
        supermarket_id="la_colonia",
        city_id="sps",
        city_name="San Pedro Sula",
        granularity=LocationGranularity.CITY,
        is_available=True,
        in_scope=True,
        extraction_enabled=False,
        technical_binding_confirmed=True,
        source_location_key=f"request:regionid:sha256:{digest}",
        evidence="location_binding_radiography:sha256:" + "a" * 64,
    )


def synthetic_binding(raw_value=RAW_REGION):
    return confirmed_sps_facet_binding(synthetic_location(raw_value))


def test_canonical_sps_binding_is_confirmed_city_but_does_not_enable_extraction():
    binding = confirmed_sps_facet_binding()

    assert binding.location_id == "la_colonia_sps"
    assert binding.city_name == "San Pedro Sula"
    assert binding.context_key == "regionid"
    assert binding.expected_fingerprint == (
        "d7732eccc99c8530a6d29cce4244920e65e85c1d5492facb05469dc3589cb8b7"
    )
    assert binding.source_key == LA_COLONIA_SPS.source_location_key
    assert binding.evidence == LA_COLONIA_SPS.evidence
    assert LA_COLONIA_SPS.extraction_enabled is False
    assert binding.public_dict()["raw_values_exposed"] is False


def test_unconfirmed_or_non_city_location_cannot_prepare_sps_binding():
    unconfirmed = LocationConfig(
        location_id="la_colonia_sps",
        supermarket_id="la_colonia",
        city_id="sps",
        city_name="San Pedro Sula",
        granularity=LocationGranularity.UNKNOWN,
        is_available=True,
        in_scope=True,
        extraction_enabled=False,
        technical_binding_confirmed=False,
        source_location_key=None,
        evidence="website_city_selector",
    )
    with pytest.raises(SpsFacetContextError, match="sps_city_granularity_required"):
        confirmed_sps_facet_binding(unconfirmed)


def test_context_fingerprint_matches_location_binding_radiography_algorithm():
    before = ContextStage(name="before", channels={"request": {"regionId": "old"}})
    after = ContextStage(name="after_city", channels={"request": {"regionId": RAW_REGION}})

    report = analyze_location_binding(
        city_name="San Pedro Sula",
        before=before,
        after_city=after,
        store_selection_observed=False,
    )
    signal = next(item for item in report.signals if item.key.casefold() == "regionid")

    assert signal.city_fingerprint == fingerprint_context_value(RAW_REGION)


def test_query_region_context_is_resolved_only_when_fingerprint_matches():
    binding = synthetic_binding()
    collector = EphemeralSpsRequestContextCollector()
    collector.observe_request(
        FakeRequest(
            url=(
                GRAPHQL_URL
                + "?"
                + urlencode({"regionId": RAW_REGION, "operationName": "x"})
            )
        )
    )

    context = collector.resolve(binding)

    assert context.placement is RequestContextPlacement.QUERY
    assert context.context_key == "regionid"
    assert context.wire_key == "regionId"
    assert context.value_path == ()
    assert context.fingerprint == binding.expected_fingerprint
    assert context.reveal_for_transport(binding) == RAW_REGION


def test_nested_query_json_preserves_container_and_value_path():
    binding = synthetic_binding()
    variables = json.dumps(
        {"delivery": {"context": {"regionId": RAW_REGION}}},
        separators=(",", ":"),
    )
    collector = EphemeralSpsRequestContextCollector()
    collector.observe_request(
        FakeRequest(url=GRAPHQL_URL + "?" + urlencode({"variables": variables}))
    )

    context = collector.resolve(binding)

    assert context.placement is RequestContextPlacement.QUERY
    assert context.wire_key == "variables"
    assert context.value_path == ("delivery", "context", "regionId")
    assert context.reveal_for_transport(binding) == RAW_REGION


def test_header_alias_can_resolve_same_confirmed_region_context():
    binding = synthetic_binding()
    collector = EphemeralSpsRequestContextCollector()
    collector.observe_request(FakeRequest(headers={"X-VTEX-Region": RAW_REGION}))

    context = collector.resolve(binding)

    assert context.placement is RequestContextPlacement.HEADER
    assert context.wire_key == "X-VTEX-Region"
    assert context.value_path == ()
    assert context.reveal_for_transport(binding) == RAW_REGION


def test_nested_body_region_context_is_observed_without_persisting_body():
    binding = synthetic_binding()
    collector = EphemeralSpsRequestContextCollector()
    collector.observe_request(
        FakeRequest(body={"variables": {"delivery": {"regionId": RAW_REGION}}})
    )

    context = collector.resolve(binding)

    assert context.placement is RequestContextPlacement.BODY
    assert context.wire_key == "$body"
    assert context.value_path == ("variables", "delivery", "regionId")
    public = context.public_dict()
    assert public["value_path"] == ["variables", "delivery", "regionId"]
    assert public["raw_values_exposed"] is False
    assert public["target_host"] == FACET_CONTEXT_HOST
    assert public["target_path"] == FACET_CONTEXT_PATH
    assert RAW_REGION not in json.dumps(public, sort_keys=True)
    assert RAW_REGION not in repr(context)


def test_region_context_on_unrelated_request_is_ignored():
    binding = synthetic_binding()
    collector = EphemeralSpsRequestContextCollector()
    collector.observe_request(
        FakeRequest(
            url="https://www.lacolonia.com/other?regionId=" + RAW_REGION,
            headers={"X-VTEX-Region": RAW_REGION},
        )
    )

    with pytest.raises(SpsFacetContextError, match="sps_region_context_not_observed"):
        collector.resolve(binding)


def test_missing_or_wrong_region_context_fails_closed():
    binding = synthetic_binding()
    missing = EphemeralSpsRequestContextCollector()
    with pytest.raises(SpsFacetContextError, match="sps_region_context_not_observed"):
        missing.resolve(binding)

    wrong = EphemeralSpsRequestContextCollector()
    wrong.observe_request(FakeRequest(headers={"X-VTEX-Region": RAW_OTHER}))
    with pytest.raises(SpsFacetContextError, match="sps_region_context_fingerprint_mismatch"):
        wrong.resolve(binding)


def test_matching_and_conflicting_region_values_fail_closed():
    binding = synthetic_binding()
    collector = EphemeralSpsRequestContextCollector()
    collector.observe_request(
        FakeRequest(
            url=GRAPHQL_URL + "?regionId=" + RAW_REGION,
            headers={"X-VTEX-Region": RAW_OTHER},
        )
    )

    with pytest.raises(SpsFacetContextError, match="sps_region_context_conflict"):
        collector.resolve(binding)


def test_same_region_in_two_request_placements_is_still_ambiguous():
    binding = synthetic_binding()
    collector = EphemeralSpsRequestContextCollector()
    collector.observe_request(
        FakeRequest(
            url=GRAPHQL_URL + "?regionId=" + RAW_REGION,
            headers={"X-VTEX-Region": RAW_REGION},
        )
    )

    with pytest.raises(
        SpsFacetContextError,
        match="sps_region_context_placement_ambiguous",
    ):
        collector.resolve(binding)


def test_same_region_with_two_structural_paths_is_ambiguous():
    binding = synthetic_binding()
    collector = EphemeralSpsRequestContextCollector()
    collector.observe_request(
        FakeRequest(
            body={
                "first": {"regionId": RAW_REGION},
                "second": {"regionId": RAW_REGION},
            }
        )
    )

    with pytest.raises(
        SpsFacetContextError,
        match="sps_region_context_value_path_ambiguous",
    ):
        collector.resolve(binding)


def test_prepared_facet_execution_is_fixed_sanitized_and_network_free():
    binding = synthetic_binding()
    collector = EphemeralSpsRequestContextCollector()
    collector.observe_request(FakeRequest(headers={"X-VTEX-Region": RAW_REGION}))
    context = collector.resolve(binding)

    prepared = prepare_sps_facet_execution(
        CATALOG_CATEGORIES_V1.requests[0],
        context,
        binding=binding,
    )
    public = prepared.public_dict()

    assert prepared.structural_request.request_kind == "root_total"
    assert public["logical_request"] == "root_total"
    assert public["network_executed"] is False
    assert public["production_authority"] is False
    assert public["catalog_accepted"] is False
    assert public["extraction_enabled"] is False
    assert public["raw_values_exposed"] is False
    rendered = json.dumps(public, sort_keys=True)
    assert RAW_REGION not in rendered
    assert RAW_REGION not in repr(prepared.context)


def test_context_cannot_be_revealed_against_a_different_binding():
    first_binding = synthetic_binding()
    second_binding = synthetic_binding(RAW_OTHER)
    collector = EphemeralSpsRequestContextCollector()
    collector.observe_request(FakeRequest(headers={"X-VTEX-Region": RAW_REGION}))
    context = collector.resolve(first_binding)

    with pytest.raises(SpsFacetContextError, match="sps_binding_changed"):
        context.reveal_for_transport(second_binding)


def test_only_two_closed_facet_operations_can_be_prepared():
    binding = synthetic_binding()
    collector = EphemeralSpsRequestContextCollector()
    collector.observe_request(FakeRequest(headers={"X-VTEX-Region": RAW_REGION}))
    context = collector.resolve(binding)

    for logical in CATALOG_CATEGORIES_V1.requests:
        prepared = prepare_sps_facet_execution(logical, context, binding=binding)
        assert prepared.structural_request.request_kind == logical.name

    unknown = type(CATALOG_CATEGORIES_V1.requests[0])("unknown", 3, "unknown")
    with pytest.raises(SpsFacetContextError, match="facet_request_kind_not_allowed"):
        prepare_sps_facet_execution(unknown, context, binding=binding)
