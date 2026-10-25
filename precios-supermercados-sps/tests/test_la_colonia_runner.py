from __future__ import annotations

import importlib.util
import json
from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any

import pytest

import precios_supermercados.scrapers.la_colonia_runner as runner_module
from precios_supermercados.scrapers.base import HttpResponse, OfflineTestTransport, SafeHttpClient
from precios_supermercados.scrapers.la_colonia import (
    FORBIDDEN_PATH_PREFIXES,
    USER_AGENT,
    LaColoniaExtractor,
    decode_search_variables,
)
from precios_supermercados.scrapers.la_colonia_catalog_coverage import (
    CanonicalCatalogEvidence,
    build_traversal_evidence,
    raw_page_evidence_from_response,
)
from precios_supermercados.scrapers.la_colonia_catalog_partitions import (
    build_structural_discovery_report,
)
from precios_supermercados.scrapers.la_colonia_graphql import (
    MAX_CATALOG_PAGE_SIZE,
    build_product_search_url,
)
from precios_supermercados.scrapers.la_colonia_runner import (
    AcceptanceProfile,
    AcceptanceThresholds,
    CrawlConfig,
    LaColoniaCatalogRunner,
    _continuity_error,
)

FIXED_TIME = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)


class StepClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        self.value += 0.01
        return self.value


class PlannedTransport:
    def __init__(self, plans: dict[int, Any]) -> None:
        self.plans = plans
        self.calls: list[int] = []
        self.call_counts: defaultdict[int, int] = defaultdict(int)

    def __call__(self, url: str, headers, timeout: float) -> HttpResponse:
        variables = decode_search_variables(url)
        from_index = int(variables["from"])
        self.calls.append(from_index)
        call_index = self.call_counts[from_index]
        self.call_counts[from_index] += 1
        plan = self.plans[from_index]
        if isinstance(plan, list):
            selected = plan[min(call_index, len(plan) - 1)]
        else:
            selected = plan
        if isinstance(selected, tuple):
            status, payload = selected
        else:
            status, payload = 200, selected
        if isinstance(payload, bytes):
            body = payload
        else:
            body = json.dumps(payload).encode("utf-8")
        return HttpResponse(status, url, {}, body)


def make_product(
    index: int,
    *,
    product_id: str | None = None,
    item_id: str | None = None,
    price: float | int = 10,
) -> dict[str, Any]:
    product_id = product_id or f"P{index:05d}"
    item_id = item_id or f"S{index:05d}"
    return {
        "productId": product_id,
        "productName": f"Producto controlado {index:05d} 500 G",
        "productReference": f"REF-{product_id}",
        "linkText": f"producto-controlado-{index:05d}-500-g",
        "brand": "Marca controlada",
        "categories": ["/Supermercado/Pruebas/"],
        "categoryTree": [
            {"id": "1", "name": "Supermercado"},
            {"id": "2", "name": "Pruebas"},
        ],
        "items": [
            {
                "itemId": item_id,
                "name": "500 G",
                "nameComplete": f"Producto controlado {index:05d} 500 G",
                "ean": f"7421{index:09d}",
                "referenceId": [{"Key": "RefId", "Value": f"SKU-{item_id}"}],
                "measurementUnit": "un",
                "unitMultiplier": 1,
                "images": [{"imageUrl": "https://lacolonia.vtexassets.com/test.jpg"}],
                "sellers": [
                    {
                        "sellerId": "1",
                        "sellerDefault": True,
                        "commercialOffer": {
                            "Price": price,
                            "ListPrice": price,
                            "AvailableQuantity": 20 if price else 0,
                            "discountHighlights": [],
                            "teasers": [],
                        },
                    }
                ],
            }
        ],
    }


def make_payload(
    start: int,
    count: int,
    total: int,
    *,
    price: float | int = 10,
    products: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    values = products or [
        make_product(index, price=price) for index in range(start, start + count)
    ]
    return {
        "data": {
            "productSearch": {
                "recordsFiltered": total,
                "products": values,
            }
        }
    }


def build_runner(plans: dict[int, Any], *, client_retries: int = 0):
    transport = PlannedTransport(plans)
    client = SafeHttpClient(
        allowed_hosts={"www.lacolonia.com"},
        forbidden_path_prefixes=FORBIDDEN_PATH_PREFIXES,
        user_agent=USER_AGENT,
        max_retries=client_retries,
        retry_delay_seconds=0,
        transport=OfflineTestTransport(transport),
        sleeper=lambda _: None,
    )
    extractor = LaColoniaExtractor(client=client, clock=lambda: FIXED_TIME)
    runner = LaColoniaCatalogRunner(
        extractor,
        sleeper=lambda _: None,
        monotonic=StepClock(),
        clock=lambda: FIXED_TIME,
    )
    return runner, transport


def run_catalog(
    plans: dict[int, Any],
    *,
    page_size: int = 10,
    max_pages: int | None = None,
    max_products: int | None = None,
    profile: AcceptanceProfile = AcceptanceProfile.BASELINE,
    thresholds: AcceptanceThresholds | None = None,
    max_retries: int = 0,
    canonical_evidence: CanonicalCatalogEvidence | None = None,
):
    runner, transport = build_runner(plans, client_retries=max_retries)
    result = runner.run(
        CrawlConfig(
            page_size=page_size,
            max_pages=max_pages,
            max_products=max_products,
            delay_seconds=0,
            max_retries=max_retries,
            order_by="OrderByNameASC",
        ),
        run_id="offline_runner_test",
        profile=profile,
        thresholds=thresholds,
        canonical_evidence=canonical_evidence,
    )
    return result, transport


def complete_plans(*, pages: int, page_size: int = 10, total: int | None = None):
    total = total or pages * page_size
    return {
        page * page_size: make_payload(page * page_size, page_size, total)
        for page in range(pages)
    }


def permissive_thresholds(**overrides) -> AcceptanceThresholds:
    values = {
        "max_missing_price_ratio": 1.0,
        "max_duplicate_sku_ratio": 1.0,
        "max_duplicate_product_ratio": 1.0,
        "max_total_change_ratio": 1.0,
    }
    values.update(overrides)
    return AcceptanceThresholds(**values)


def canonical_evidence_for_plans(
    plans: dict[int, Mapping[str, Any]], *, total: int
) -> CanonicalCatalogEvidence:
    facets = [{
        "type": "CATEGORYTREE",
        "values": [{
            "key": "category-1", "value": "1", "quantity": total,
            "children": [{
                "key": "category-2", "value": "2", "quantity": total,
                "children": [],
            }],
        }],
    }]
    structure = build_structural_discovery_report(
        facets, run_id="offline_runner_test", root_total=total
    )
    partition = structure.valid_leaves[0].name

    def traversal(traversal_id: str, order_by: str):
        traversal_plans = plans
        if traversal_id == "reconciliation":
            traversal_plans = {
                start: {
                    "data": {
                        "productSearch": {
                            **payload["data"]["productSearch"],
                            "products": list(
                                reversed(payload["data"]["productSearch"]["products"])
                            ),
                        }
                    }
                }
                for start, payload in plans.items()
            }
        pages = tuple(
            raw_page_evidence_from_response(
                run_id="offline_runner_test",
                traversal_id=traversal_id,
                partition=partition,
                order_by=order_by,
                from_index=start,
                to_index=start + len(payload["data"]["productSearch"]["products"]) - 1,
                response=payload,
            )
            for start, payload in sorted(traversal_plans.items())
        )
        return build_traversal_evidence(
            run_id="offline_runner_test",
            traversal_id=traversal_id,
            tree_digest=structure.tree_digest,
            order_by=order_by,
            pages=pages,
        )

    return CanonicalCatalogEvidence(
        run_id="offline_runner_test",
        root_response={"recordsFiltered": total},
        facets_response={
            "recordsFiltered": total,
            "sampling": False,
            "facets": facets,
        },
        primary=traversal("primary", "OrderByNameASC"),
        reconciliation=traversal("reconciliation", "OrderByReleaseDateDESC"),
    )


def test_two_complete_pages_are_collected_but_not_catalog_accepted_without_coverage():
    result, transport = run_catalog(complete_plans(pages=2), max_pages=2)

    assert result.metrics.collection_succeeded is True
    assert result.metrics.accepted is False
    assert "canonical_coverage_missing" in result.metrics.rejection_reasons
    assert result.metrics.pages_expected == 2
    assert result.metrics.pages_completed == 2
    assert result.metrics.products_returned == 20
    assert result.metrics.skus_extracted == 20
    assert transport.calls == [0, 10]


def test_runner_rejects_external_coverage_without_trusted_collector_provenance():
    plans = complete_plans(pages=2)
    evidence = canonical_evidence_for_plans(plans, total=20)
    result, transport = run_catalog(
        plans,
        max_pages=2,
        canonical_evidence=evidence,
    )

    assert result.metrics.collection_succeeded is True
    assert result.metrics.catalog_complete is False
    assert result.metrics.accepted is False
    assert result.coverage is not None and result.coverage.accepted is False
    assert "trusted_collector_provenance_unavailable" in result.coverage.coverage_reason
    assert transport.calls == [0, 10]


def test_real_cli_returns_success_only_with_canonical_coverage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    plans = {0: make_payload(0, 2, 2)}
    facets = [{
        "type": "CATEGORYTREE",
        "values": [{
            "key": "category-1", "value": "1", "quantity": 2,
            "children": [{
                "key": "category-2", "value": "2", "quantity": 2,
                "children": [],
            }],
        }],
    }]
    evidence = {
        "schema_version": "1",
        "run_id": "offline_cli_test",
        "root_response": {"recordsFiltered": 2},
        "facets_response": {
            "recordsFiltered": 2, "sampling": False, "facets": facets,
        },
        "primary": {
            "traversal_id": "a", "order_by": "OrderByNameASC",
            "pages": [{
                "partition": "partition-0001", "from_index": 0, "to_index": 1,
                    "response": {
                        "data": {
                            "productSearch": {
                                **plans[0]["data"]["productSearch"],
                                "products": list(reversed(
                                    plans[0]["data"]["productSearch"]["products"]
                                )),
                            }
                        }
                    }, "purpose": "PRIMARY",
            }],
        },
        "reconciliation": {
            "traversal_id": "b", "order_by": "OrderByReleaseDateDESC",
            "pages": [{
                "partition": "partition-0001", "from_index": 0, "to_index": 1,
                "response": plans[0], "purpose": "PRIMARY",
            }],
        },
    }
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    script_path = Path(__file__).parents[1] / "scripts/probar_la_colonia.py"
    spec = importlib.util.spec_from_file_location("probar_la_colonia_test", script_path)
    assert spec is not None and spec.loader is not None
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, "LaColoniaCatalogRunner", lambda: build_runner(plans)[0])

    exit_code = cli.main([
        "--mode", "smoke", "--page-size", "10", "--max-pages", "1",
        "--delay-seconds", "0", "--canonical-evidence", str(evidence_path),
        "--output-dir", str(tmp_path / "out"),
    ])

    assert exit_code == 2
    summary = json.loads(capsys.readouterr().out)
    assert summary["metrics"]["accepted"] is False
    assert summary["coverage"]["accepted"] is False
    assert "trusted_collector_provenance_unavailable" in summary["coverage"]["coverage_reason"]


def test_ten_complete_pages_still_require_canonical_coverage():
    result, _ = run_catalog(complete_plans(pages=10), max_pages=10)

    assert result.metrics.collection_succeeded is True
    assert result.metrics.accepted is False
    assert result.metrics.pages_completed == 10
    assert result.metrics.page_coverage == pytest.approx(1.0)
    assert result.metrics.products_returned == 100
    assert result.metrics.proposed_thresholds


def test_last_page_with_fewer_products_is_legitimate():
    plans = {
        0: make_payload(0, 10, 14),
        10: make_payload(10, 4, 14),
    }
    result, _ = run_catalog(plans)

    assert result.metrics.collection_succeeded is True
    assert result.metrics.accepted is False
    assert result.pages[-1].products_expected == 4
    assert result.pages[-1].products_returned == 4


def test_incomplete_intermediate_page_stops_run():
    plans = {
        0: make_payload(0, 4, 25),
        10: make_payload(10, 10, 25),
    }
    result, transport = run_catalog(plans, max_pages=2)

    assert result.metrics.accepted is False
    assert "partial_product_page" in result.metrics.rejection_reasons
    assert result.metrics.pages_completed == 0
    assert transport.calls == [0]


def test_unexpected_empty_page_stops_run():
    plans = {
        0: make_payload(0, 10, 20),
        10: make_payload(10, 0, 20, products=[]),
    }
    result, transport = run_catalog(plans, max_pages=2)

    assert result.metrics.accepted is False
    assert "unexpected_empty_page" in result.metrics.rejection_reasons
    assert transport.calls == [0, 10]


def test_repeated_page_is_detected():
    first = make_payload(0, 10, 20)
    result, _ = run_catalog({0: first, 10: first}, max_pages=2)

    assert result.metrics.accepted is False
    assert "repeated_page" in result.metrics.rejection_reasons
    assert result.metrics.pages_completed == 1


def test_duplicate_sku_between_pages_is_rejected_before_dedup_can_hide_it():
    page_one = make_payload(0, 10, 20)
    page_two_products = [make_product(index) for index in range(10, 20)]
    page_two_products[0]["items"][0]["itemId"] = "S00000"
    page_two_products[0]["items"][0]["referenceId"] = [
        {"Key": "RefId", "Value": "SKU-S00000"}
    ]
    page_two = make_payload(10, 10, 20, products=page_two_products)

    result, _ = run_catalog({0: page_one, 10: page_two}, max_pages=2)

    assert result.metrics.collection_succeeded is False
    assert result.metrics.accepted is False
    assert "duplicate_sku_identity" in result.metrics.rejection_reasons
    assert result.metrics.duplicate_skus == 1
    assert result.metrics.skus_returned == 20
    assert result.metrics.skus_extracted == 19


def test_canonical_coverage_and_runner_reject_duplicate_sku_across_products():
    page_one = make_payload(0, 10, 20)
    page_two_products = [make_product(index) for index in range(10, 20)]
    page_two_products[0]["items"][0]["itemId"] = "S00000"
    page_two = make_payload(10, 10, 20, products=page_two_products)
    plans = {0: page_one, 10: page_two}
    evidence = canonical_evidence_for_plans(plans, total=20)

    result, _ = run_catalog(
        plans,
        max_pages=2,
        canonical_evidence=evidence,
    )

    assert result.coverage is not None and result.coverage.accepted is False
    assert "duplicate_sku_identity" in result.coverage.coverage_reason
    assert result.metrics.catalog_complete is False
    assert result.metrics.accepted is False


def test_duplicate_product_between_pages_rejects_collection():
    page_one = make_payload(0, 10, 20)
    page_two_products = [make_product(index) for index in range(10, 20)]
    page_two_products[0]["productId"] = "P00000"
    page_two = make_payload(10, 10, 20, products=page_two_products)

    result, _ = run_catalog({0: page_one, 10: page_two}, max_pages=2)

    assert result.metrics.accepted is False
    assert result.metrics.collection_succeeded is False
    assert result.metrics.duplicate_products == 1
    assert "duplicate_product_identity" in result.metrics.rejection_reasons


def test_catalog_total_without_changes_is_recorded():
    result, _ = run_catalog(complete_plans(pages=2), max_pages=2)

    assert result.metrics.products_reported_initial == 20
    assert result.metrics.products_reported_final == 20
    assert result.metrics.total_change_absolute == 0
    assert "catalog_total_changed_during_run" not in result.metrics.warnings


def test_catalog_total_increase_is_fail_closed_in_baseline():
    plans = {
        0: make_payload(0, 10, 20),
        10: make_payload(10, 10, 21),
    }
    result, _ = run_catalog(plans, max_pages=2)

    assert result.metrics.accepted is False
    assert result.metrics.collection_succeeded is False
    assert result.metrics.products_reported_final == 21
    assert result.metrics.total_change_absolute == 1
    assert "catalog_total_changed_during_run" in result.metrics.warnings


def test_catalog_total_decrease_rejects_incompatible_last_page():
    plans = {
        0: make_payload(0, 10, 20),
        10: make_payload(10, 9, 19),
    }
    result, _ = run_catalog(plans, max_pages=2)

    assert result.metrics.accepted is False
    assert result.metrics.products_reported_final == 19
    assert "partial_product_page" in result.metrics.rejection_reasons


def test_http_403_stops_immediately():
    result, transport = run_catalog({0: (403, b"blocked")}, max_pages=2)

    assert result.metrics.accepted is False
    assert result.metrics.http_403 == 1
    assert "http_403_or_captcha" in result.metrics.rejection_reasons
    assert transport.calls == [0]


def test_persistent_http_429_stops_after_bounded_retries():
    plan = [(429, b"rate limited")]
    result, transport = run_catalog({0: plan}, max_pages=2, max_retries=0)

    assert result.metrics.accepted is False
    assert result.metrics.http_429 == 1
    assert result.metrics.persistent_http_429 == 1
    assert "persistent_http_429" in result.metrics.rejection_reasons
    assert transport.calls == [0]


def test_http_500_stops_after_bounded_retries():
    plan = [(500, b"error")]
    result, transport = run_catalog({0: plan}, max_pages=2, max_retries=0)

    assert result.metrics.accepted is False
    assert result.metrics.http_5xx == 1
    assert "http_status_500" in result.metrics.rejection_reasons
    assert transport.calls == [0]


def test_structural_change_is_rejected():
    result, _ = run_catalog({0: {"data": {"unexpected": {}}}}, max_pages=2)

    assert result.metrics.accepted is False
    assert "structure_changed" in result.metrics.rejection_reasons
    assert result.metrics.structural_events >= 1


def test_first_critical_error_prevents_next_request():
    plans = {
        0: make_payload(0, 3, 20),
        10: make_payload(10, 10, 20),
    }
    result, transport = run_catalog(plans, max_pages=2)

    assert result.metrics.accepted is False
    assert transport.calls == [0]


def test_full_page_coverage_is_one():
    result, _ = run_catalog(complete_plans(pages=2), max_pages=2)

    assert result.metrics.pages_completed == result.metrics.pages_expected
    assert result.metrics.page_coverage == pytest.approx(1.0)


def test_incomplete_coverage_is_below_one():
    plans = {
        0: make_payload(0, 10, 20),
        10: make_payload(10, 5, 20),
    }
    result, _ = run_catalog(plans, max_pages=2)

    assert result.metrics.pages_completed == 1
    assert result.metrics.pages_expected == 2
    assert result.metrics.page_coverage == pytest.approx(0.5)
    assert result.metrics.accepted is False


def test_global_metrics_are_calculated_separately():
    first = make_payload(0, 10, 20)
    first["data"]["productSearch"]["products"][0]["items"][0]["sellers"][0][
        "commercialOffer"
    ]["Price"] = 0
    first["data"]["productSearch"]["products"][0]["items"][0]["sellers"][0][
        "commercialOffer"
    ]["ListPrice"] = 0
    second_products = [make_product(index) for index in range(10, 20)]
    second_products[0]["items"][0]["sellers"][0]["commercialOffer"][
        "discountHighlights"
    ] = [{"name": "Oferta"}]
    second_products[1]["items"][0]["measurementUnit"] = "kg"
    second = make_payload(10, 10, 20, products=second_products)

    result, _ = run_catalog({0: first, 10: second}, max_pages=2)

    metrics = result.metrics
    assert metrics.products_returned == 20
    assert metrics.skus_returned == 20
    assert metrics.skus_extracted == 20
    assert metrics.skus_with_price == 19
    assert metrics.skus_without_price == 1
    assert metrics.promotional_skus == 1
    assert metrics.weighted_skus == 1


def test_incorrect_ordering_between_pages_is_detected():
    assert (
        _continuity_error(
            page=2,
            from_index=10,
            to_index=19,
            page_size=10,
            previous_to=9,
            order_by="OrderByReleaseDateDESC",
            expected_order_by="OrderByNameASC",
        )
        == "ordering_changed_during_run"
    )


def test_overlapping_ranges_are_detected():
    assert (
        _continuity_error(
            page=2,
            from_index=9,
            to_index=18,
            page_size=10,
            previous_to=9,
            order_by="OrderByNameASC",
            expected_order_by="OrderByNameASC",
        )
        == "overlapping_page_ranges"
    )


def test_range_gap_is_detected():
    assert (
        _continuity_error(
            page=2,
            from_index=11,
            to_index=20,
            page_size=10,
            previous_to=9,
            order_by="OrderByNameASC",
            expected_order_by="OrderByNameASC",
        )
        == "page_range_gap"
    )


def test_page_with_no_sku_price_is_rejected():
    result, _ = run_catalog({0: make_payload(0, 10, 10, price=0)}, max_pages=1)

    assert result.metrics.accepted is False
    assert result.metrics.skus_with_price == 0
    assert "page_without_prices" in result.metrics.rejection_reasons


def test_sku_duplicates_do_not_grant_canonical_acceptance_inside_threshold():
    page_one = make_payload(0, 10, 20)
    page_two_products = [make_product(index) for index in range(10, 20)]
    page_two_products[0]["items"][0]["itemId"] = "S00000"
    page_two_products[0]["items"][0]["referenceId"] = [
        {"Key": "RefId", "Value": "SKU-S00000"}
    ]
    page_two = make_payload(10, 10, 20, products=page_two_products)

    result, _ = run_catalog(
        {0: page_one, 10: page_two},
        max_pages=2,
        profile=AcceptanceProfile.VALIDATION,
        thresholds=permissive_thresholds(max_duplicate_sku_ratio=0.1),
    )

    assert result.metrics.duplicate_skus == 1
    assert result.metrics.duplicate_sku_ratio == pytest.approx(0.05)
    assert result.metrics.collection_succeeded is False
    assert result.metrics.accepted is False
    assert "duplicate_sku_identity" in result.metrics.rejection_reasons


def test_validation_rejects_ratio_above_observed_threshold():
    plans = {
        0: make_payload(0, 10, 20),
        10: make_payload(10, 10, 21),
    }
    result, _ = run_catalog(
        plans,
        max_pages=2,
        profile=AcceptanceProfile.VALIDATION,
        thresholds=permissive_thresholds(max_total_change_ratio=0.01),
    )

    assert result.metrics.total_change_ratio == pytest.approx(0.05)
    assert result.metrics.accepted is False
    assert "catalog_total_change_above_threshold" in result.metrics.rejection_reasons


def test_validation_requires_all_thresholds():
    result, _ = run_catalog(
        complete_plans(pages=2),
        max_pages=2,
        profile=AcceptanceProfile.VALIDATION,
        thresholds=AcceptanceThresholds(max_missing_price_ratio=0.1),
    )

    assert result.metrics.accepted is False
    assert "validation_thresholds_missing" in result.metrics.rejection_reasons


def test_max_products_limits_scope_with_constant_page_size():
    plans = complete_plans(pages=5, page_size=20, total=200)
    result, transport = run_catalog(
        plans,
        page_size=20,
        max_products=100,
    )

    assert result.metrics.pages_expected == 10
    assert result.metrics.pages_planned == 5
    assert result.metrics.products_returned == 100
    assert result.metrics.collection_succeeded is False
    assert result.metrics.accepted is False
    assert transport.calls == [0, 20, 40, 60, 80]


def test_page_size_options_up_to_fifty_are_supported():
    for page_size in (10, 20, 30, 50):
        variables = decode_search_variables(
            build_product_search_url(
                page=2,
                page_size=page_size,
                order_by="OrderByNameASC",
            )
        )
        assert variables["from"] == page_size
        assert variables["to"] == page_size * 2 - 1
        assert variables["orderBy"] == "OrderByNameASC"
    assert MAX_CATALOG_PAGE_SIZE == 50


def test_more_than_fifty_products_per_page_is_rejected():
    with pytest.raises(ValueError, match="page_size"):
        CrawlConfig(page_size=51)


def test_non_multiple_max_products_is_rejected():
    with pytest.raises(ValueError, match="múltiplo"):
        CrawlConfig(page_size=30, max_products=100)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("page_size", True),
        ("max_pages", True),
        ("max_products", True),
        ("max_retries", False),
    ],
)
def test_crawl_config_rejects_boolean_numeric_values(field, value):
    with pytest.raises(ValueError):
        CrawlConfig(**{field: value})


@pytest.mark.parametrize("value", [True, False, float("nan"), float("inf")])
def test_acceptance_thresholds_reject_non_finite_or_boolean_values(value):
    with pytest.raises(ValueError):
        AcceptanceThresholds(max_missing_price_ratio=value)


def test_sanitized_summary_contains_hashes_not_catalog_rows():
    result, _ = run_catalog({0: make_payload(0, 10, 10)}, max_pages=1)
    summary = result.sanitized_summary()

    assert "products" not in summary
    assert len(summary["sample_source_key_hashes"]) == 5
    assert all(len(value) == 16 for value in summary["sample_source_key_hashes"])
