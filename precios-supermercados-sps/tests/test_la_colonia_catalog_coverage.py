from __future__ import annotations

import json
from datetime import UTC, datetime, timezone
from typing import Any

import pytest

from precios_supermercados.scrapers.base import HttpResponse, OfflineTestTransport, SafeHttpClient
from precios_supermercados.scrapers.la_colonia import (
    FORBIDDEN_PATH_PREFIXES,
    USER_AGENT,
    LaColoniaExtractor,
)
from precios_supermercados.scrapers.la_colonia_catalog_coverage import (
    CatalogCoverageReport,
    PartitionSpec,
    evaluate_catalog_coverage,
    evaluate_partition_coverage,
    observe_coverage_page,
    validate_sanitized_coverage_summary,
)
from precios_supermercados.scrapers.la_colonia_runner import (
    CrawlConfig,
    LaColoniaCatalogRunner,
)

NAME_ASC = "OrderByNameASC"
RELEASE_DESC = "OrderByReleaseDateDESC"
FIXED_TIME = datetime(2026, 8, 5, 18, 0, tzinfo=UTC)


def keys(start: int, count: int, *, prefix: str = "P") -> list[str]:
    return [f"{prefix}{index:04d}" for index in range(start, start + count)]


def page(
    partition: str,
    start: int,
    end: int,
    total: int,
    values: list[str] | None = None,
    *,
    order_by: str = NAME_ASC,
):
    return observe_coverage_page(
        partition=partition,
        order_by=order_by,
        from_index=start,
        to_index=end,
        records_filtered=total,
        product_keys=values if values is not None else keys(start, end - start + 1),
    )


def partition_result(
    name: str,
    total: int,
    observations,
    *,
    primary_order_by: str = NAME_ASC,
):
    return evaluate_partition_coverage(
        PartitionSpec(
            name=name,
            facet_key="category-3",
            facet_value=name,
            expected_products=total,
        ),
        observations,
        primary_order_by=primary_order_by,
    )


def test_01_stable_sequential_traversal_demonstrates_coverage():
    result = partition_result(
        "stable",
        20,
        [page("stable", 0, 9, 20), page("stable", 10, 19, 20)],
    )

    assert result.coverage_demonstrated is True
    assert result.coverage_reason == "coverage_demonstrated"
    assert result.products_unique == 20
    assert result.duplicate_occurrences == 0
    assert result.repeated_page_sets == 0
    assert result.unexpected_overlaps == 0


def test_02_partial_page_rejects_coverage():
    result = partition_result(
        "partial",
        20,
        [page("partial", 0, 9, 20), page("partial", 10, 19, 20, keys(10, 9))],
    )

    assert result.coverage_demonstrated is False
    assert "partial_page" in result.coverage_reason
    assert result.pages_completed == 1


def test_03_block_b_equals_c_is_rejected():
    repeated = keys(0, 10)
    result = partition_result(
        "bc",
        20,
        [page("bc", 0, 9, 20, repeated), page("bc", 10, 19, 20, repeated)],
    )

    assert result.coverage_demonstrated is False
    assert result.repeated_page_sets == 1
    assert result.unexpected_overlaps == 1
    assert result.products_unique == 10
    assert "repeated_page_set" in result.coverage_reason


def test_04_block_d_equals_e_is_rejected():
    repeated = keys(20, 10)
    result = partition_result(
        "de",
        40,
        [
            page("de", 0, 9, 40),
            page("de", 10, 19, 40),
            page("de", 20, 29, 40, repeated),
            page("de", 30, 39, 40, repeated),
        ],
    )

    assert result.coverage_demonstrated is False
    assert result.repeated_page_sets == 1
    assert result.unexpected_overlaps >= 1
    assert result.products_unique == 30


def test_05_different_ranges_with_same_signature_are_detected():
    first = page("signature", 0, 9, 20, keys(0, 10))
    second = page("signature", 10, 19, 20, keys(0, 10))
    result = partition_result("signature", 20, [first, second])

    assert first.sequence_signature == second.sequence_signature
    assert first.set_signature == second.set_signature
    assert result.repeated_page_sets == 1
    assert result.coverage_demonstrated is False


def test_06_duplicate_inside_page_is_rejected():
    values = keys(0, 9) + ["P0008"]
    result = partition_result("inside", 10, [page("inside", 0, 9, 10, values)])

    assert result.coverage_demonstrated is False
    assert "duplicate_within_page" in result.coverage_reason
    assert result.duplicate_occurrences == 1


def test_07_duplicate_between_pages_is_rejected():
    second = ["P0009"] + keys(11, 9)
    result = partition_result(
        "between",
        20,
        [page("between", 0, 9, 20), page("between", 10, 19, 20, second)],
    )

    assert result.coverage_demonstrated is False
    assert result.products_unique == 19
    assert result.duplicate_occurrences == 1
    assert result.unexpected_overlaps == 1


def test_08_omitted_product_is_detected_even_when_each_page_is_full():
    second = ["P0009"] + keys(11, 9)
    result = partition_result(
        "omitted",
        20,
        [page("omitted", 0, 9, 20), page("omitted", 10, 19, 20, second)],
    )

    assert all(item.products_received == 10 for item in [
        page("omitted", 0, 9, 20),
        page("omitted", 10, 19, 20, second),
    ])
    assert result.products_unique == 19
    assert "unique_products_below_partition_total" in result.coverage_reason


def test_09_stable_records_filtered_does_not_prove_coverage():
    observations = [
        page("false-stable", 0, 9, 20),
        page("false-stable", 10, 19, 20),
        page("false-stable", 5, 14, 20, keys(0, 10)),
    ]
    result = partition_result("false-stable", 20, observations)

    assert {item.records_filtered for item in observations} == {20}
    assert result.products_unique == 20
    assert result.coverage_demonstrated is False
    assert result.repeated_page_sets >= 1
    assert result.unexpected_overlaps >= 1


def test_10_changing_records_filtered_rejects_partition():
    result = partition_result(
        "changing",
        20,
        [page("changing", 0, 9, 20), page("changing", 10, 19, 21)],
    )

    assert result.coverage_demonstrated is False
    assert result.total_changes == 1
    assert "partition_total_changed" in result.coverage_reason


def test_11_complete_leaf_category_is_accepted():
    result = partition_result("leaf", 3, [page("leaf", 0, 2, 3)])

    assert result.coverage_demonstrated is True
    assert result.products_reported == 3
    assert result.products_unique == 3


def test_12_product_in_two_categories_is_deduplicated_globally():
    first = partition_result(
        "cat-a",
        2,
        [page("cat-a", 0, 1, 2, ["P0", "P1"])],
    )
    second = partition_result(
        "cat-b",
        2,
        [page("cat-b", 0, 1, 2, ["P1", "P2"])],
    )
    report = evaluate_catalog_coverage(
        [first, second],
        partitions_discovered=2,
        products_reported=3,
    )

    assert report.accepted is False
    assert "legacy_evidence_non_authoritative" in report.coverage_reason
    assert report.products_unique == 3
    assert report.duplicate_occurrences == 1


def test_13_uncategorized_product_prevents_global_acceptance():
    complete = partition_result("cat", 2, [page("cat", 0, 1, 2)])
    report = evaluate_catalog_coverage(
        [complete],
        partitions_discovered=1,
        products_reported=3,
        uncategorized_products=1,
    )

    assert report.accepted is False
    assert "uncategorized_products_present" in report.coverage_reason


def test_14_partial_partition_is_rejected_globally():
    partial = partition_result(
        "partial-partition",
        4,
        [page("partial-partition", 0, 3, 4, keys(0, 3))],
    )
    report = evaluate_catalog_coverage(
        [partial],
        partitions_discovered=1,
        products_reported=4,
    )

    assert report.accepted is False
    assert report.partitions_completed == 0
    assert "partitions_incomplete" in report.coverage_reason


def test_15_repeated_partition_page_is_rejected_globally():
    repeated = partition_result(
        "repeated-partition",
        4,
        [
            page("repeated-partition", 0, 1, 4, ["P0", "P1"]),
            page("repeated-partition", 2, 3, 4, ["P0", "P1"]),
        ],
    )
    report = evaluate_catalog_coverage(
        [repeated],
        partitions_discovered=1,
        products_reported=4,
    )

    assert report.accepted is False
    assert report.repeated_page_sets == 1


def test_16_complete_union_between_categories_is_accepted():
    first = partition_result(
        "union-a",
        2,
        [page("union-a", 0, 1, 2, ["P0", "P1"])],
    )
    second = partition_result(
        "union-b",
        2,
        [page("union-b", 0, 1, 2, ["P2", "P3"])],
    )
    report = evaluate_catalog_coverage(
        [first, second],
        partitions_discovered=2,
        products_reported=4,
    )

    assert report.accepted is False
    assert report.coverage_demonstrated is False
    assert "legacy_evidence_non_authoritative" in report.coverage_reason
    assert report.products_unique == report.products_reported == 4


def test_17_incomplete_union_is_rejected():
    first = partition_result(
        "incomplete-a",
        2,
        [page("incomplete-a", 0, 1, 2, ["P0", "P1"])],
    )
    second = partition_result(
        "incomplete-b",
        2,
        [page("incomplete-b", 0, 1, 2, ["P2", "P3"])],
    )
    report = evaluate_catalog_coverage(
        [first, second],
        partitions_discovered=2,
        products_reported=5,
    )

    assert report.accepted is False
    assert report.products_unique == 4
    assert "global_union_below_reported_total" in report.coverage_reason


def test_18_two_orderings_reconcile_when_complete_sets_match():
    observations = [
        page("orders", 0, 9, 20, keys(0, 10), order_by=NAME_ASC),
        page("orders", 10, 19, 20, keys(10, 10), order_by=NAME_ASC),
        page("orders", 0, 9, 20, keys(10, 10), order_by=RELEASE_DESC),
        page("orders", 10, 19, 20, keys(0, 10), order_by=RELEASE_DESC),
    ]
    result = partition_result("orders", 20, observations)

    assert result.coverage_demonstrated is True
    assert result.orderings_attempted == 2
    assert result.orderings_reconciled is True


def test_19_two_orderings_reconciliation_failure_is_rejected():
    observations = [
        page("orders-fail", 0, 9, 20, keys(0, 10), order_by=NAME_ASC),
        page("orders-fail", 10, 19, 20, keys(10, 10), order_by=NAME_ASC),
        page("orders-fail", 0, 9, 20, keys(10, 10), order_by=RELEASE_DESC),
        page(
            "orders-fail",
            10,
            19,
            20,
            keys(0, 9) + ["OTHER"],
            order_by=RELEASE_DESC,
        ),
    ]
    result = partition_result("orders-fail", 20, observations)

    assert result.coverage_demonstrated is False
    assert result.orderings_reconciled is False
    assert "order_reconciliation_failed" in result.coverage_reason


def test_20_maximum_request_limit_rejects_catalog():
    complete = partition_result(
        "limit",
        20,
        [page("limit", 0, 9, 20), page("limit", 10, 19, 20)],
    )
    report = evaluate_catalog_coverage(
        [complete],
        partitions_discovered=1,
        products_reported=20,
        request_limit=1,
    )

    assert report.accepted is False
    assert "request_limit_exceeded" in report.coverage_reason


def test_21_sanitized_summary_contains_only_aggregate_metrics():
    complete = partition_result(
        "sanitize",
        2,
        [page("sanitize", 0, 1, 2, ["PRIVATE-A", "PRIVATE-B"])],
    )
    report = evaluate_catalog_coverage(
        [complete],
        partitions_discovered=1,
        products_reported=2,
    )
    summary = report.sanitized_summary()
    rendered = json.dumps(summary)

    expected_fields = {
        "schema_version",
        "partitions_discovered",
        "partitions_attempted",
        "partitions_completed",
        "pages_expected",
        "pages_attempted",
        "pages_completed",
        "products_reported",
        "products_received",
        "products_unique",
        "duplicate_occurrences",
        "repeated_page_sets",
        "unexpected_overlaps",
        "missing_coverage_events",
        "total_changes",
        "uncategorized_products",
        "request_limit",
        "coverage_demonstrated",
        "coverage_reason",
        "accepted",
        "run_id",
        "tree_digest",
        "primary_plan_digest",
        "reconciliation_plan_digest",
    }
    assert set(summary) == expected_fields
    assert "PRIVATE-A" not in rendered
    assert "PRIVATE-B" not in rendered
    for forbidden in ("productId", "itemId", "productName", "price", "url"):
        assert forbidden not in rendered

    with pytest.raises(ValueError, match="no permitido"):
        validate_sanitized_coverage_summary({"productId": "PRIVATE"})


def _raw_product(index: int) -> dict[str, Any]:
    return {
        "productId": f"P{index}",
        "productName": f"Producto {index} 500 G",
        "productReference": f"REF-{index}",
        "linkText": f"producto-{index}",
        "brand": "Marca",
        "categories": ["/Supermercado/Pruebas/"],
        "items": [
            {
                "itemId": f"S{index}",
                "name": "500 G",
                "nameComplete": f"Producto {index} 500 G",
                "ean": f"742100{index:06d}",
                "referenceId": [{"Key": "RefId", "Value": f"SKU-{index}"}],
                "measurementUnit": "un",
                "unitMultiplier": 1,
                "images": [],
                "sellers": [
                    {
                        "sellerId": "1",
                        "sellerDefault": True,
                        "commercialOffer": {
                            "Price": 10,
                            "ListPrice": 10,
                            "AvailableQuantity": 10,
                            "discountHighlights": [],
                            "teasers": [],
                        },
                    }
                ],
            }
        ],
    }


def test_22_normal_runner_still_rejects_partial_pages():
    payload = {
        "data": {
            "productSearch": {
                "recordsFiltered": 20,
                "products": [_raw_product(index) for index in range(9)],
            }
        }
    }

    def transport(url, headers, timeout):
        return HttpResponse(200, url, {}, json.dumps(payload).encode("utf-8"))

    client = SafeHttpClient(
        allowed_hosts={"www.lacolonia.com"},
        forbidden_path_prefixes=FORBIDDEN_PATH_PREFIXES,
        user_agent=USER_AGENT,
        max_retries=0,
        transport=OfflineTestTransport(transport),
        sleeper=lambda _: None,
    )
    extractor = LaColoniaExtractor(client=client, clock=lambda: FIXED_TIME)
    runner = LaColoniaCatalogRunner(
        extractor,
        sleeper=lambda _: None,
        clock=lambda: FIXED_TIME,
    )
    result = runner.run(
        CrawlConfig(
            page_size=10,
            max_pages=2,
            delay_seconds=0,
            max_retries=0,
            order_by=NAME_ASC,
        ),
        run_id="coverage-offline-partial",
    )

    assert result.metrics.accepted is False
    assert "partial_product_page" in result.metrics.rejection_reasons
    assert result.metrics.pages_completed == 0


def test_23_caller_cannot_supply_membership_boolean():
    with pytest.raises(TypeError, match="membership_valid"):
        observe_coverage_page(
            partition="membership",
            order_by=NAME_ASC,
            from_index=0,
            to_index=1,
            records_filtered=2,
            product_keys=["A", "B"],
            membership_valid=True,
        )


def test_24_missing_discovered_partition_is_rejected():
    complete = partition_result("one", 1, [page("one", 0, 0, 1)])
    report = evaluate_catalog_coverage(
        [complete],
        partitions_discovered=2,
        products_reported=1,
    )

    assert report.accepted is False
    assert "partition_count_mismatch" in report.coverage_reason


def test_25_catalog_report_type_is_explicit():
    complete = partition_result("type", 1, [page("type", 0, 0, 1)])
    report = evaluate_catalog_coverage(
        [complete],
        partitions_discovered=1,
        products_reported=1,
    )

    assert isinstance(report, CatalogCoverageReport)
    assert report.accepted is False
