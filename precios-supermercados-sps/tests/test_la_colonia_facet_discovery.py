from __future__ import annotations

import inspect
import json
import socket
from datetime import UTC, datetime, timezone

import pytest

from precios_supermercados.scrapers.la_colonia_catalog_coverage import (
    PartitionSpec,
    evaluate_catalog_coverage,
    evaluate_partition_coverage,
    observe_coverage_page,
)
from precios_supermercados.scrapers.la_colonia_catalog_partitions import (
    estimate_partition_request_plan,
)
from precios_supermercados.scrapers.la_colonia_facet_discovery import (
    FACET_DISCOVERY_REQUEST_ID,
    FacetPartitionLimitError,
    IncompleteFacetTreeError,
    InvalidFacetQuantitiesError,
    analyze_category_facets,
    estimate_facet_discovery_budget,
    get_facet_discovery_plan,
    parse_facet_discovery_command,
    serialize_sanitized_facet_summary,
)
from precios_supermercados.scrapers.la_colonia_facet_discovery_runtime import (
    OUTCOME_INCOMPLETE_TREE,
    OUTCOME_INCONCLUSIVE,
    OUTCOME_INVALID_QUANTITIES,
    OUTCOME_NO_POSITIVE,
    OUTCOME_OVER_BUDGET,
    OUTCOME_SAMPLING,
    OUTCOME_WITHIN_BUDGET,
    FacetDiscoveryRuntime,
    render_facet_discovery_markdown,
    serialize_facet_discovery_summary,
)
from precios_supermercados.scrapers.la_colonia_runner import LaColoniaCatalogRunner

FIXED_TIME = datetime(2026, 8, 5, 19, 30, tzinfo=UTC)


def command(**updates):
    value = {
        "request_id": FACET_DISCOVERY_REQUEST_ID,
        "supermarket": "la_colonia",
        "mode": "facet_discovery",
        "discovery_plan": "catalog_categories_v1",
        "delay_seconds": 1.5,
        "allow_full": False,
    }
    value.update(updates)
    return value


def node(level, value, quantity, children=...):
    result = {
        "key": f"category-{level}",
        "value": value,
        "quantity": quantity,
    }
    if children is not ...:
        result["children"] = children
    return result


def complete_values(*, extra_level=False, zero_leaf=False, shared=False):
    first_quantity = 60 if shared else 55
    second_quantity = 60 if shared else 45
    first_leaf = node(4 if extra_level else 3, "SECRETO-A", first_quantity, [])
    if extra_level:
        first_leaf = node(3, "SECRETO-A3", first_quantity, [first_leaf])
    values = [
        node(
            1,
            "SECRETO-ROOT",
            100,
            [
                node(2, "SECRETO-PARENT-A", first_quantity, [first_leaf]),
                node(
                    2,
                    "SECRETO-PARENT-B",
                    second_quantity,
                    [node(3, "SECRETO-B", second_quantity, [])],
                ),
            ],
        )
    ]
    if zero_leaf:
        values[0]["children"].append(
            node(2, "SECRETO-ZERO-PARENT", 0, [node(3, "SECRETO-ZERO", 0, [])])
        )
    return values


def facets_payload(total=100, *, sampling=False, values=None):
    return {
        "recordsFiltered": total,
        "sampling": sampling,
        "facets": [
            {
                "type": "CATEGORYTREE",
                "values": complete_values() if values is None else values,
            }
        ],
    }


class FakeTransport:
    def __init__(self, root_total=100, facet_response=None):
        self.root_total = root_total
        self.facet_response = facet_response or facets_payload(root_total)
        self.calls = []

    def __call__(self, request):
        self.calls.append(request)
        if request.name == "root_total":
            return {"recordsFiltered": self.root_total}
        if request.name == "category_tree":
            return self.facet_response
        raise AssertionError("Solicitud no permitida")


def run_runtime(transport, **runtime_kwargs):
    sleeps = []
    runtime = FacetDiscoveryRuntime(
        transport,
        sleeper=sleeps.append,
        clock=lambda: FIXED_TIME,
        **runtime_kwargs,
    )
    return runtime.run(command()), sleeps, runtime


def test_valid_closed_contract():
    parsed = parse_facet_discovery_command(command())
    assert parsed.request_id == FACET_DISCOVERY_REQUEST_ID
    assert parsed.allow_full is False


def test_unknown_plan_is_rejected():
    with pytest.raises(ValueError, match="desconocido"):
        get_facet_discovery_plan("otra-cosa")
    with pytest.raises(ValueError, match="discovery_plan"):
        parse_facet_discovery_command(command(discovery_plan="otra-cosa"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("url", "https://example.invalid"),
        ("query", "arbitraria"),
        ("selectedFacets", []),
        ("from", 0),
        ("to", 49),
        ("orderBy", "OrderByNameASC"),
        ("page_size", 50),
        ("max_pages", 1),
        ("max_products", 50),
        ("max_requests", 2),
        ("profile", "baseline"),
        ("thresholds", {}),
        ("full", True),
        ("workflow", "anything.yml"),
    ],
)
def test_arbitrary_command_fields_are_rejected(field, value):
    with pytest.raises(ValueError, match="no permitidos"):
        parse_facet_discovery_command(command(**{field: value}))


def test_valid_root_total_is_recorded():
    result, _, _ = run_runtime(FakeTransport(root_total=100))
    assert result.summary["root_total"] == 100


def test_sampling_false_allows_analysis():
    result, _, _ = run_runtime(FakeTransport())
    assert result.summary["sampling_detected"] is False
    assert result.summary["discovery_outcome"] == OUTCOME_WITHIN_BUDGET


def test_sampling_true_stops_without_budget():
    transport = FakeTransport(facet_response=facets_payload(sampling=True))
    result, _, _ = run_runtime(transport)
    assert result.accepted is False
    assert result.summary["discovery_outcome"] == OUTCOME_SAMPLING
    assert result.summary["estimated_total_requests"] == 0
    assert result.summary["discovery_completed"] is False


def test_category_one_two_and_three_are_detected():
    analysis = analyze_category_facets(facets_payload(), root_total=100)
    assert analysis.facet_levels_detected == (
        "category-1",
        "category-2",
        "category-3",
    )
    assert analysis.facet_values_count == {
        "category-1": 1,
        "category-2": 2,
        "category-3": 2,
    }


def test_additional_allowed_level_is_detected_as_leaf():
    payload = facets_payload(values=complete_values(extra_level=True))
    analysis = analyze_category_facets(payload, root_total=100)
    assert analysis.facet_levels_detected[-1] == "category-4"
    assert sorted(item.level for item in analysis.leaf_partitions) == [3, 4]


def test_complete_leaf_tree_uses_most_specific_nodes():
    analysis = analyze_category_facets(facets_payload(), root_total=100)
    assert analysis.leaf_partitions_count == 2
    assert analysis.positive_leaf_partitions == 2
    assert all(item.level == 3 for item in analysis.leaf_partitions)


def test_missing_children_marks_tree_incomplete():
    values = [node(1, "ROOT", 100, [node(2, "CUT", 100)])]
    with pytest.raises(IncompleteFacetTreeError, match="children"):
        analyze_category_facets(facets_payload(values=values), root_total=100)


def test_zero_quantity_leaf_is_counted_but_not_partitioned():
    analysis = analyze_category_facets(
        facets_payload(values=complete_values(zero_leaf=True)),
        root_total=100,
    )
    assert analysis.zero_quantity_partitions == 1
    assert analysis.positive_leaf_partitions == 2


def test_negative_quantity_is_rejected():
    values = [node(1, "ROOT", 100, [node(2, "BAD", -1, [])])]
    with pytest.raises(InvalidFacetQuantitiesError, match="negativo"):
        analyze_category_facets(facets_payload(values=values), root_total=100)


def test_non_numeric_quantity_is_rejected():
    values = [node(1, "ROOT", 100, [node(2, "BAD", "x", [])])]
    with pytest.raises(InvalidFacetQuantitiesError, match="entero"):
        analyze_category_facets(facets_payload(values=values), root_total=100)


def test_conflicting_duplicate_path_is_rejected():
    values = [
        node(
            1,
            "ROOT",
            100,
            [node(2, "DUP", 50, []), node(2, "DUP", 40, [])],
        )
    ]
    with pytest.raises(InvalidFacetQuantitiesError, match="incompatibles"):
        analyze_category_facets(facets_payload(values=values), root_total=100)


def test_changed_total_stops_runtime():
    transport = FakeTransport(
        root_total=100,
        facet_response=facets_payload(total=101),
    )
    result, _, _ = run_runtime(transport)
    assert result.summary["discovery_outcome"] == OUTCOME_INCONCLUSIVE
    assert result.summary["stop_reason"] == "catalog_total_changed"


def analysis_for_page_counts(page_counts):
    quantities = [pages * 50 for pages in page_counts]
    total = sum(quantities)
    children = [
        node(2, f"PARENT-{index}", quantity, [node(3, f"LEAF-{index}", quantity, [])])
        for index, quantity in enumerate(quantities)
    ]
    payload = facets_payload(
        total=total,
        values=[node(1, "ROOT", total, children)],
    )
    return analyze_category_facets(payload, root_total=total)


def test_budget_below_500():
    budget = estimate_facet_discovery_budget(analysis_for_page_counts([10, 8, 1]))
    assert budget.total_estimated_requests < 500
    assert budget.within_request_limit is True


def test_budget_exactly_500():
    budget = estimate_facet_discovery_budget(analysis_for_page_counts([82, 81, 1]))
    assert budget.total_estimated_requests == 500
    assert budget.within_request_limit is True


def test_budget_above_500():
    budget = estimate_facet_discovery_budget(analysis_for_page_counts([82, 81, 2]))
    assert budget.total_estimated_requests > 500
    assert budget.within_request_limit is False


def test_runtime_classifies_over_budget():
    analysis = analysis_for_page_counts([82, 81, 2])
    root_total = analysis.root_total
    values = []
    for index, item in enumerate(analysis.leaf_partitions):
        values.append(
            node(
                2,
                f"PARENT-{index}",
                item.quantity,
                [node(3, f"LEAF-{index}", item.quantity, [])],
            )
        )
    transport = FakeTransport(
        root_total=root_total,
        facet_response=facets_payload(
            total=root_total,
            values=[node(1, "ROOT", root_total, values)],
        ),
    )
    result, _, _ = run_runtime(transport)
    assert result.summary["discovery_outcome"] == OUTCOME_OVER_BUDGET
    assert result.summary["within_request_limit"] is False


def test_maximum_request_count_stops_before_second_request():
    result, sleeps, _ = run_runtime(FakeTransport(), max_requests=1)
    assert result.summary["requests_attempted"] == 1
    assert result.summary["requests_completed"] == 1
    assert result.summary["stop_reason"] == "maximum_request_count_exceeded"
    assert sleeps == [1.5]


def test_concurrency_is_one():
    _, _, runtime = run_runtime(FakeTransport())
    assert runtime.concurrency == 1


def test_max_retries_must_be_zero():
    with pytest.raises(ValueError, match="max_retries=0"):
        FacetDiscoveryRuntime(FakeTransport(), max_retries=1)


def test_artifact_is_sanitized_and_has_only_closed_fields():
    result, _, _ = run_runtime(FakeTransport())
    encoded = serialize_facet_discovery_summary(result.summary)
    value = json.loads(encoded)
    assert set(value) == set(result.summary)
    assert value["discovery_outcome"] == OUTCOME_WITHIN_BUDGET


def test_category_names_and_values_are_absent_from_json_and_markdown():
    result, _, _ = run_runtime(FakeTransport())
    json_text = serialize_facet_discovery_summary(result.summary).decode()
    markdown = render_facet_discovery_markdown(result.summary)
    for secret in (
        "SECRETO-ROOT",
        "SECRETO-PARENT-A",
        "SECRETO-A",
        "SECRETO-B",
    ):
        assert secret not in json_text
        assert secret not in markdown


def test_products_and_skus_are_absent_from_artifact():
    result, _, _ = run_runtime(FakeTransport())
    text = serialize_facet_discovery_summary(result.summary).decode()
    for forbidden in ("productId", "itemId", "sku", "ean", "brand", "price"):
        assert forbidden not in text


def test_artifact_limit_is_enforced():
    result, _, _ = run_runtime(FakeTransport())
    with pytest.raises(ValueError, match="64 KiB"):
        serialize_sanitized_facet_summary(result.summary, max_bytes=10)


def test_runner_normal_is_not_integrated_with_facet_discovery():
    source = inspect.getsource(LaColoniaCatalogRunner)
    assert "facet_discovery" not in source
    assert "FacetDiscovery" not in source


def test_legacy_coverage_metrics_cannot_accept_a_catalog():
    partition = PartitionSpec("p", "category-3", "private", 2)
    observations = [
        observe_coverage_page(
            partition="p",
            order_by="OrderByNameASC",
            from_index=0,
            to_index=1,
            records_filtered=2,
            product_keys=["A", "B"],
        )
    ]
    covered = evaluate_partition_coverage(partition, observations)
    report = evaluate_catalog_coverage(
        [covered], partitions_discovered=1, products_reported=2
    )
    assert covered.coverage_demonstrated is True
    assert report.accepted is False
    assert "legacy_evidence_non_authoritative" in report.coverage_reason


def test_runtime_does_not_need_real_internet(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("No debe abrir sockets")

    monkeypatch.setattr(socket, "create_connection", fail)
    result, _, _ = run_runtime(FakeTransport())
    assert result.accepted is True


def test_shared_leaf_quantities_are_allowed_but_reported():
    analysis = analyze_category_facets(
        facets_payload(values=complete_values(shared=True)),
        root_total=100,
    )
    assert analysis.leaf_quantity_sum == 120
    assert "leaf_quantities_exceed_root_total" in analysis.quality_events


def test_global_residual_marks_tree_incomplete():
    values = [
        node(
            1,
            "ROOT",
            100,
            [node(2, "PARENT", 90, [node(3, "LEAF", 90, [])])],
        )
    ]
    with pytest.raises(IncompleteFacetTreeError, match="no cubre"):
        analyze_category_facets(facets_payload(values=values), root_total=100)


def test_partition_limit_is_enforced():
    values = [
        node(
            1,
            "ROOT",
            3,
            [
                node(2, "A", 1, []),
                node(2, "B", 1, []),
                node(2, "C", 1, []),
            ],
        )
    ]
    with pytest.raises(FacetPartitionLimitError):
        analyze_category_facets(
            facets_payload(total=3, values=values),
            root_total=3,
            max_partitions=2,
        )


def test_empty_catalog_with_valid_zero_leaf_is_within_budget():
    values = [node(1, "ROOT", 0, [node(2, "ZERO", 0, [])])]
    transport = FakeTransport(
        root_total=0,
        facet_response=facets_payload(total=0, values=values),
    )
    result, _, _ = run_runtime(transport)
    assert result.summary["discovery_outcome"] == OUTCOME_WITHIN_BUDGET
    assert result.summary["positive_leaf_partitions"] == 0
    assert result.summary["zero_quantity_partitions"] == 1


def test_runtime_classifies_invalid_quantities():
    values = [node(1, "ROOT", 100, [node(2, "BAD", -1, [])])]
    transport = FakeTransport(
        facet_response=facets_payload(values=values),
    )
    result, _, _ = run_runtime(transport)
    assert result.summary["discovery_outcome"] == OUTCOME_INVALID_QUANTITIES


def test_runtime_classifies_incomplete_tree():
    values = [node(1, "ROOT", 100, [node(2, "CUT", 100)])]
    transport = FakeTransport(facet_response=facets_payload(values=values))
    result, _, _ = run_runtime(transport)
    assert result.summary["discovery_outcome"] == OUTCOME_INCOMPLETE_TREE


def test_partition_budget_keeps_historical_defaults_and_supports_probes():
    partitions = (
        PartitionSpec("a", "category-3", "a", 100),
        PartitionSpec("b", "category-3", "b", 50),
    )
    old = estimate_partition_request_plan(
        partitions,
        page_size=50,
        recovery_windows_per_partition=2,
        reconcile_with_second_order=True,
    )
    assert old.primary_requests == 3
    assert old.probe_requests == 0
    assert old.recovery_reserve == 4
    assert old.reconciliation_requests == 3
    assert old.requests_planned == 10

    closed = estimate_partition_request_plan(
        partitions,
        page_size=50,
        include_boundary_probes=True,
        recovery_windows_per_partition=4,
        max_recovery_partitions=1,
        reconcile_with_second_order=True,
        max_reconciliation_partitions=1,
    )
    assert closed.primary_requests == 3
    assert closed.probe_requests == 1
    assert closed.recovery_reserve == 4
    assert closed.reconciliation_requests == 2
    assert closed.requests_planned == 10
