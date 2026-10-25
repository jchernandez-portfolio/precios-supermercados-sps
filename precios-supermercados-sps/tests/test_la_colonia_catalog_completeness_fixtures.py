from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from precios_supermercados.scrapers.la_colonia_catalog_coverage import (
    CanonicalCatalogEvidence,
    PartitionSpec,
    build_traversal_evidence,
    evaluate_canonical_catalog_coverage,
    evaluate_catalog_coverage,
    evaluate_overlap_recovery,
    evaluate_partition_coverage,
    observe_coverage_page,
    raw_page_evidence_from_response,
)
from precios_supermercados.scrapers.la_colonia_catalog_partitions import (
    build_structural_discovery_report,
    discover_leaf_category_partitions,
)

FIXTURE = Path(__file__).parent / "fixtures/la_colonia/catalog_completeness_scenarios.json"
ORDER = "OrderByNameASC"
RUN_ID = "offline-canonical-fixture"


def scenarios() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def structural_report(total: int, *, duplicate: bool = False):
    facet = {
        "type": "CATEGORYTREE",
        "values": [{
            "key": "category-1",
            "value": "leaf",
            "quantity": total,
            "children": [],
        }],
    }
    return build_structural_discovery_report(
        [facet, facet] if duplicate else [facet],
        run_id=RUN_ID,
        root_total=total,
    )


def raw_products(keys: list[str], *, leaf: str = "leaf") -> list[dict]:
    return [
        {
            "productId": key,
            "productReference": f"REF-{key}",
            "linkText": f"link-{key}",
            "categoryTree": [{"id": leaf}],
            "items": [{"itemId": f"SKU-{key}"}],
        }
        for key in keys
    ]


def raw_page(
    traversal_id: str,
    partition: str,
    value: list,
    *,
    order_by: str,
    purpose: str = "PRIMARY",
    leaf: str = "leaf",
):
    start, end, total, keys = value
    return raw_page_evidence_from_response(
        run_id=RUN_ID,
        traversal_id=traversal_id,
        partition=partition,
        order_by=order_by,
        from_index=start,
        to_index=end,
        response={"data": {"productSearch": {
            "recordsFiltered": total,
            "products": raw_products(keys, leaf=leaf),
        }}},
        purpose=purpose,
    )


def canonical_scenario(name: str):
    scenario = scenarios()[name]
    structure = structural_report(scenario["total"])
    partition = structure.valid_leaves[0].name
    primary_pages = [
        raw_page("traversal-a", partition, value, order_by=ORDER)
        for value in scenario["primary"]
    ] + [
        raw_page(
            "traversal-a",
            partition,
            value,
            order_by=ORDER,
            purpose="RECOVERY",
        )
        for value in scenario["recovery"]
    ]
    primary = build_traversal_evidence(
        run_id=RUN_ID,
        traversal_id="traversal-a",
        tree_digest=structure.tree_digest,
        order_by=ORDER,
        pages=primary_pages,
    )
    reconciliation = None
    if scenario["reconcile"] is not None:
        total = scenario["total"]
        reconciliation_pages = (
                raw_page_evidence_from_response(
                    run_id=RUN_ID,
                    traversal_id="traversal-b",
                    partition=partition,
                    order_by="OrderByReleaseDateDESC",
                    from_index=0,
                    to_index=max(total - 1, 0),
                    response={
                        "data": {"productSearch": {
                            "recordsFiltered": total,
                            "products": raw_products(scenario["reconcile"]),
                        }},
                        "extensions": {"offline_attempt": "reconciliation"},
                    },
                ),
        )
        reconciliation = build_traversal_evidence(
            run_id=RUN_ID,
            traversal_id="traversal-b",
            tree_digest=structure.tree_digest,
            order_by="OrderByReleaseDateDESC",
            pages=reconciliation_pages,
        )
    root_response = {"recordsFiltered": scenario["total"]}
    facets_response = {
        "recordsFiltered": scenario["total"],
        "sampling": False,
        "facets": [{
            "type": "CATEGORYTREE",
            "values": [{
                "key": "category-1",
                "value": "leaf",
                "quantity": scenario["total"],
                "children": [],
            }],
        }],
    }
    return evaluate_canonical_catalog_coverage(
        CanonicalCatalogEvidence(
            run_id=RUN_ID,
            root_response=root_response,
            facets_response=facets_response,
            primary=primary,
            reconciliation=reconciliation,
        )
    )


@pytest.mark.parametrize("name", ["A_normal_full_page", "B_intermediate_20_returns_19", "C_duplicate_between_windows", "E_recovered_by_overlap"])
def test_a_l_consistent_cases_remain_blocked_without_trusted_collector(name: str):
    report = canonical_scenario(name)
    assert report.accepted is False
    assert "trusted_collector_provenance_unavailable" in report.coverage_reason


@pytest.mark.parametrize("name", ["F_missing_not_recoverable", "G_total_changes", "J_truncated_response", "K_reordered_results", "L_unknown_residual"])
def test_a_l_incomplete_cases_use_the_canonical_pipeline(name: str):
    report = canonical_scenario(name)
    assert report.accepted is False
    assert report.coverage_reason != "coverage_demonstrated"


def _two_traversal_report(primary_products, reconciliation_products, *, same_response=False):
    structure = structural_report(2)
    partition = structure.valid_leaves[0].name

    def traversal(traversal_id, order_by, products, marker):
        response = {"data": {"productSearch": {
            "recordsFiltered": 2,
            "products": products,
        }}}
        response["extensions"] = {
            "caller_marker" if same_response else "offline_attempt": marker
        }
        page = raw_page_evidence_from_response(
            run_id=RUN_ID,
            traversal_id=traversal_id,
            partition=partition,
            order_by=order_by,
            from_index=0,
            to_index=1,
            response=response,
        )
        return build_traversal_evidence(
            run_id=RUN_ID,
            traversal_id=traversal_id,
            tree_digest=structure.tree_digest,
            order_by=order_by,
            pages=(page,),
        )

    return evaluate_canonical_catalog_coverage(CanonicalCatalogEvidence(
        run_id=RUN_ID,
        root_response={"recordsFiltered": 2},
        facets_response={
            "recordsFiltered": 2,
            "sampling": False,
            "facets": [{"type": "CATEGORYTREE", "values": [{
                "key": "category-1", "value": "leaf", "quantity": 2,
                "children": [],
            }]}],
        },
        primary=traversal("a", ORDER, primary_products, "a"),
        reconciliation=traversal(
            "b", "OrderByReleaseDateDESC", reconciliation_products, "b"
        ),
    ))


def test_identical_response_relabelled_as_reconciliation_is_rejected():
    products = raw_products(["A", "B"])
    report = _two_traversal_report(products, products, same_response=True)
    assert report.accepted is False
    assert "reconciliation_response_reused" in report.coverage_reason


def test_product_sku_ownership_swap_is_rejected():
    first = raw_products(["A", "B"])
    swapped = raw_products(["A", "B"])
    swapped[0]["items"], swapped[1]["items"] = swapped[1]["items"], swapped[0]["items"]
    report = _two_traversal_report(first, swapped)
    assert report.accepted is False
    assert "product_sku_mapping_mismatch" in report.coverage_reason


@pytest.mark.parametrize("from_index,to_index", [(False, 1), (0, True), (0.0, 1)])
def test_raw_page_ranges_reject_bool_and_non_integer(from_index, to_index):
    with pytest.raises(ValueError, match="Rango de página inválido"):
        raw_page_evidence_from_response(
            run_id=RUN_ID,
            traversal_id="a",
            partition="partition-0001",
            order_by=ORDER,
            from_index=from_index,
            to_index=to_index,
            response={"data": {"productSearch": {
                "recordsFiltered": 2,
                "products": raw_products(["A", "B"]),
            }}},
        )


@pytest.mark.parametrize("field,value", [("productId", {"bad": 1}), ("itemId", False)])
def test_raw_identity_fields_reject_non_string_values(field, value):
    products = raw_products(["A", "B"])
    if field == "itemId":
        products[0]["items"][0][field] = value
    else:
        products[0][field] = value
        products[0]["productReference"] = None
        products[0]["linkText"] = None
    if field == "itemId":
        with pytest.raises(ValueError, match="itemId"):
            _two_traversal_report(products, raw_products(["A", "B"]))
    else:
        with pytest.raises(ValueError, match="productId"):
            _two_traversal_report(products, raw_products(["A", "B"]))


def observation(partition: str, value: list):
    start, end, total, keys = value
    return observe_coverage_page(
        partition=partition,
        order_by=ORDER,
        from_index=start,
        to_index=end,
        records_filtered=total,
        product_keys=keys,
    )


@pytest.mark.parametrize(
    "name",
    [name for name in scenarios() if name[0] in "ABCEFGJKL"],
)
def test_recovery_scenarios_are_fail_closed(name: str):
    scenario = scenarios()[name]
    partition = PartitionSpec("leaf", "category-3", "fixture", scenario["total"])
    result = evaluate_overlap_recovery(
        partition,
        [observation("leaf", item) for item in scenario["primary"]],
        [observation("leaf", item) for item in scenario["recovery"]],
        reconciliation_pages=(
            None
            if scenario["reconcile"] is None
            else [observe_coverage_page(
                partition="leaf",
                order_by="OrderByReleaseDateDESC",
                from_index=0,
                to_index=scenario["total"] - 1,
                records_filtered=scenario["total"],
                product_keys=scenario["reconcile"],
            )]
        ),
    )
    expected = (
        "LEGACY_EVIDENCE_CONSISTENT"
        if scenario["expected"] == "COMPLETE"
        else scenario["expected"]
    )
    assert result.status == expected
    assert result.deterministic_union == tuple(sorted(result.deterministic_union))
    if result.status == "INCOMPLETE":
        assert result.reasons


def test_duplicate_between_leaves_does_not_hide_global_omission():
    scenario = scenarios()["D_duplicate_between_leaves"]
    results = []
    for index, keys in enumerate(scenario["leaves"]):
        spec = PartitionSpec(f"leaf-{index}", "category-3", f"v-{index}", len(keys))
        results.append(
            evaluate_partition_coverage(
                spec,
                [observe_coverage_page(
                    partition=spec.name,
                    order_by=ORDER,
                    from_index=0,
                    to_index=len(keys) - 1,
                    records_filtered=len(keys),
                    product_keys=keys,
                )],
            )
        )
    report = evaluate_catalog_coverage(
        results,
        partitions_discovered=2,
        products_reported=scenario["global_total"],
    )
    assert report.accepted is False
    assert "legacy_evidence_non_authoritative" in report.coverage_reason
    assert report.products_unique == 3
    assert report.duplicate_occurrences == 1


def test_empty_leaf_is_explicitly_complete_without_inventing_products():
    scenario = scenarios()["H_empty_leaf"]
    spec = PartitionSpec("empty", "category-3", "empty", scenario["total"])
    page = observe_coverage_page(
        partition="empty", order_by=ORDER, from_index=0, to_index=0,
        records_filtered=0, product_keys=[],
    )
    result = evaluate_partition_coverage(spec, [page])
    assert result.coverage_demonstrated is True
    assert result.products_unique == 0


def test_duplicate_category_facet_is_deduplicated_deterministically():
    scenario = scenarios()["I_duplicate_facet"]
    facet = {"type": "CATEGORYTREE", "values": [{
        "key": "category-1", "value": "agua", "quantity": scenario["quantity"], "children": []
    }]}
    report = build_structural_discovery_report(
        [facet, facet], run_id=RUN_ID, root_total=scenario["quantity"]
    )
    assert report.valid is True
    assert len(report.valid_leaves) == 1
    assert report.duplicate_structural_nodes == 1


def test_h_empty_leaf_runs_through_full_canonical_pipeline():
    total = scenarios()["H_empty_leaf"]["total"]
    structure = structural_report(total)
    primary = build_traversal_evidence(
        run_id=RUN_ID,
        traversal_id="empty-a",
        tree_digest=structure.tree_digest,
        order_by=ORDER,
        pages=(),
    )
    reconciliation = build_traversal_evidence(
        run_id=RUN_ID,
        traversal_id="empty-b",
        tree_digest=structure.tree_digest,
        order_by="OrderByReleaseDateDESC",
        pages=(),
    )
    facet = {
        "type": "CATEGORYTREE",
        "values": [{
            "key": "category-1", "value": "leaf", "quantity": 0, "children": []
        }],
    }
    report = evaluate_canonical_catalog_coverage(
        CanonicalCatalogEvidence(
            RUN_ID,
            {"recordsFiltered": 0},
            {"recordsFiltered": 0, "sampling": False, "facets": [facet]},
            primary,
            reconciliation,
        )
    )
    assert report.accepted is False
    assert "collector_observation_missing" in report.coverage_reason
    assert report.products_unique == 0


def test_i_duplicate_facet_runs_through_full_canonical_pipeline():
    total = scenarios()["I_duplicate_facet"]["quantity"]
    structure = structural_report(total, duplicate=True)
    partition = structure.valid_leaves[0].name
    first_page = raw_page(
        "duplicate-a", partition, [0, 1, total, ["A", "B"]], order_by=ORDER
    )
    second_page = raw_page(
        "duplicate-b",
        partition,
        [0, 1, total, ["B", "A"]],
        order_by="OrderByReleaseDateDESC",
    )
    primary = build_traversal_evidence(
        run_id=RUN_ID, traversal_id="duplicate-a", tree_digest=structure.tree_digest,
        order_by=ORDER, pages=(first_page,),
    )
    reconciliation = build_traversal_evidence(
        run_id=RUN_ID, traversal_id="duplicate-b", tree_digest=structure.tree_digest,
        order_by="OrderByReleaseDateDESC", pages=(second_page,),
    )
    facet = {
        "type": "CATEGORYTREE",
        "values": [{
            "key": "category-1", "value": "leaf", "quantity": total, "children": []
        }],
    }
    report = evaluate_canonical_catalog_coverage(
        CanonicalCatalogEvidence(
            RUN_ID,
            {"recordsFiltered": total},
            {"recordsFiltered": total, "sampling": False, "facets": [facet, facet]},
            primary,
            reconciliation,
        )
    )
    assert report.accepted is False
    assert "trusted_collector_provenance_unavailable" in report.coverage_reason


def test_d_cross_leaf_duplicate_does_not_replace_global_union_evidence():
    scenario = scenarios()["D_duplicate_between_leaves"]
    facets = [{
        "type": "CATEGORYTREE",
        "values": [
            {"key": "category-1", "value": "left", "quantity": 2, "children": []},
            {"key": "category-1", "value": "right", "quantity": 2, "children": []},
        ],
    }]
    structure = build_structural_discovery_report(
        facets, run_id=RUN_ID, root_total=scenario["global_total"]
    )
    pages_a = []
    pages_b = []
    for leaf, keys in zip(structure.valid_leaves, scenario["leaves"]):
        leaf_value = leaf.facet_value
        pages_a.append(raw_page_evidence_from_response(
            run_id=RUN_ID, traversal_id="union-a", partition=leaf.name,
            order_by=ORDER, from_index=0, to_index=1,
            response={"data": {"productSearch": {
                "recordsFiltered": 2,
                "products": raw_products(keys, leaf=leaf_value),
            }}},
        ))
        pages_b.append(raw_page_evidence_from_response(
            run_id=RUN_ID, traversal_id="union-b", partition=leaf.name,
            order_by="OrderByReleaseDateDESC", from_index=0, to_index=1,
            response={"data": {"productSearch": {
                "recordsFiltered": 2,
                "products": raw_products(list(reversed(keys)), leaf=leaf_value),
            }}},
        ))
    primary = build_traversal_evidence(
        run_id=RUN_ID, traversal_id="union-a", tree_digest=structure.tree_digest,
        order_by=ORDER, pages=pages_a,
    )
    reconciliation = build_traversal_evidence(
        run_id=RUN_ID, traversal_id="union-b", tree_digest=structure.tree_digest,
        order_by="OrderByReleaseDateDESC", pages=pages_b,
    )
    report = evaluate_canonical_catalog_coverage(CanonicalCatalogEvidence(
        RUN_ID,
        {"recordsFiltered": scenario["global_total"]},
        {"recordsFiltered": scenario["global_total"], "sampling": False, "facets": facets},
        primary,
        reconciliation,
    ))
    assert report.accepted is False
    assert "trusted_collector_provenance_unavailable" in report.coverage_reason
    assert report.products_unique == 3
    assert report.duplicate_occurrences == 1


def test_duplicate_compensating_missing_is_canonically_incomplete():
    scenario = {
        "total": 4,
        "primary": [[0, 3, 4, ["A", "B", "C", "A"]]],
        "recovery": [],
        "reconcile": ["A", "B", "C", "D"],
    }
    # Reuse the exact canonical construction without accepting fixture counters.
    structure = structural_report(4)
    partition = structure.valid_leaves[0].name
    primary_page = raw_page("dup-a", partition, scenario["primary"][0], order_by=ORDER)
    second_page = raw_page(
        "dup-b", partition, [0, 3, 4, scenario["reconcile"]],
        order_by="OrderByReleaseDateDESC",
    )
    primary = build_traversal_evidence(
        run_id=RUN_ID, traversal_id="dup-a", tree_digest=structure.tree_digest,
        order_by=ORDER, pages=(primary_page,),
    )
    reconciliation = build_traversal_evidence(
        run_id=RUN_ID, traversal_id="dup-b", tree_digest=structure.tree_digest,
        order_by="OrderByReleaseDateDESC", pages=(second_page,),
    )
    facets = [{"type": "CATEGORYTREE", "values": [{
        "key": "category-1", "value": "leaf", "quantity": 4, "children": []
    }]}]
    report = evaluate_canonical_catalog_coverage(CanonicalCatalogEvidence(
        RUN_ID, {"recordsFiltered": 4},
        {"recordsFiltered": 4, "sampling": False, "facets": facets},
        primary, reconciliation,
    ))
    assert report.accepted is False
    assert "duplicate_compensates_omission" in report.coverage_reason


def test_malformed_positive_leaf_is_invalid_structural_evidence():
    report = build_structural_discovery_report(
        [{"type": "CATEGORYTREE", "values": [{
            "key": "", "value": "leaf", "quantity": 1, "children": []
        }]}],
        run_id=RUN_ID,
        root_total=1,
    )
    assert report.valid is False
    assert report.invalid_positive_leaves == 1
    assert "invalid_positive_leaf" in report.errors


def test_one_traversal_cannot_reconcile_with_itself():
    structure = structural_report(1)
    partition = structure.valid_leaves[0].name
    page = raw_page("same", partition, [0, 0, 1, ["A"]], order_by=ORDER)
    traversal = build_traversal_evidence(
        run_id=RUN_ID, traversal_id="same", tree_digest=structure.tree_digest,
        order_by=ORDER, pages=(page,),
    )
    facets = [{"type": "CATEGORYTREE", "values": [{
        "key": "category-1", "value": "leaf", "quantity": 1, "children": []
    }]}]
    report = evaluate_canonical_catalog_coverage(CanonicalCatalogEvidence(
        RUN_ID, {"recordsFiltered": 1},
        {"recordsFiltered": 1, "sampling": False, "facets": facets},
        traversal, traversal,
    ))
    assert report.accepted is False
    assert "self_reconciliation_forbidden" in report.coverage_reason


def test_membership_is_derived_from_raw_category_evidence():
    structure = structural_report(1)
    partition = structure.valid_leaves[0].name
    wrong = raw_page(
        "membership-a", partition, [0, 0, 1, ["A"]],
        order_by=ORDER, leaf="different-leaf",
    )
    right = raw_page(
        "membership-b", partition, [0, 0, 1, ["A"]],
        order_by="OrderByReleaseDateDESC",
    )
    primary = build_traversal_evidence(
        run_id=RUN_ID, traversal_id="membership-a", tree_digest=structure.tree_digest,
        order_by=ORDER, pages=(wrong,),
    )
    reconciliation = build_traversal_evidence(
        run_id=RUN_ID, traversal_id="membership-b", tree_digest=structure.tree_digest,
        order_by="OrderByReleaseDateDESC", pages=(right,),
    )
    facets = [{"type": "CATEGORYTREE", "values": [{
        "key": "category-1", "value": "leaf", "quantity": 1, "children": []
    }]}]
    report = evaluate_canonical_catalog_coverage(CanonicalCatalogEvidence(
        RUN_ID, {"recordsFiltered": 1},
        {"recordsFiltered": 1, "sampling": False, "facets": facets},
        primary, reconciliation,
    ))
    assert report.accepted is False
    assert "partition_membership_invalid" in report.coverage_reason


def test_stale_tree_or_plan_digest_is_fail_closed():
    structure = structural_report(1)
    partition = structure.valid_leaves[0].name
    page = raw_page("stale-a", partition, [0, 0, 1, ["A"]], order_by=ORDER)
    stale = build_traversal_evidence(
        run_id=RUN_ID, traversal_id="stale-a", tree_digest="0" * 64,
        order_by=ORDER, pages=(page,),
    )
    second_page = raw_page(
        "stale-b", partition, [0, 0, 1, ["A"]],
        order_by="OrderByReleaseDateDESC",
    )
    second = build_traversal_evidence(
        run_id=RUN_ID, traversal_id="stale-b", tree_digest=structure.tree_digest,
        order_by="OrderByReleaseDateDESC", pages=(second_page,),
    )
    facets = [{"type": "CATEGORYTREE", "values": [{
        "key": "category-1", "value": "leaf", "quantity": 1, "children": []
    }]}]
    report = evaluate_canonical_catalog_coverage(CanonicalCatalogEvidence(
        RUN_ID, {"recordsFiltered": 1},
        {"recordsFiltered": 1, "sampling": False, "facets": facets},
        stale, second,
    ))
    assert report.accepted is False
    assert "traversal_tree_digest_mismatch" in report.coverage_reason

    valid_primary = build_traversal_evidence(
        run_id=RUN_ID, traversal_id="stale-a", tree_digest=structure.tree_digest,
        order_by=ORDER, pages=(page,),
    )
    tampered = replace(second, plan_digest="0" * 64)
    report = evaluate_canonical_catalog_coverage(CanonicalCatalogEvidence(
        RUN_ID, {"recordsFiltered": 1},
        {"recordsFiltered": 1, "sampling": False, "facets": facets},
        valid_primary, tampered,
    ))
    assert report.accepted is False
    assert "traversal_plan_digest_invalid" in report.coverage_reason


def test_overlap_positions_must_contain_the_same_identities():
    structure = structural_report(4)
    partition = structure.valid_leaves[0].name
    primary_pages = (
        raw_page("overlap-a", partition, [0, 3, 4, ["A", "B", "C", "D"]], order_by=ORDER),
        raw_page(
            "overlap-a", partition, [2, 3, 4, ["A", "B"]],
            order_by=ORDER, purpose="RECOVERY",
        ),
    )
    second_page = raw_page(
        "overlap-b", partition, [0, 3, 4, ["D", "C", "B", "A"]],
        order_by="OrderByReleaseDateDESC",
    )
    primary = build_traversal_evidence(
        run_id=RUN_ID, traversal_id="overlap-a", tree_digest=structure.tree_digest,
        order_by=ORDER, pages=primary_pages,
    )
    reconciliation = build_traversal_evidence(
        run_id=RUN_ID, traversal_id="overlap-b", tree_digest=structure.tree_digest,
        order_by="OrderByReleaseDateDESC", pages=(second_page,),
    )
    facets = [{"type": "CATEGORYTREE", "values": [{
        "key": "category-1", "value": "leaf", "quantity": 4, "children": []
    }]}]
    report = evaluate_canonical_catalog_coverage(CanonicalCatalogEvidence(
        RUN_ID, {"recordsFiltered": 4},
        {"recordsFiltered": 4, "sampling": False, "facets": facets},
        primary, reconciliation,
    ))
    assert report.accepted is False
    assert "overlap_position_conflict" in report.coverage_reason


def test_product_identity_is_tagged_and_uses_only_closed_fallback_order():
    page = raw_page_evidence_from_response(
        run_id=RUN_ID,
        traversal_id="identity",
        partition="partition-0001",
        order_by=ORDER,
        from_index=0,
        to_index=2,
        response={"data": {"productSearch": {
            "recordsFiltered": 3,
            "products": [
                {"productId": "P", "productReference": "R", "linkText": "L", "categoryTree": [{"id": "leaf"}], "items": [{"itemId": "S1"}]},
                {"productReference": "R", "linkText": "L", "categoryTree": [{"id": "leaf"}], "items": [{"itemId": "S2"}]},
                {"linkText": "L", "productName": "NOT-IDENTITY", "categoryTree": [{"id": "leaf"}], "items": [{"itemId": "S3"}]},
            ],
        }}},
    )
    assert [product.identity for product in page.products] == [
        "productId:P",
        "productReference:R",
        "linkText:L",
    ]


def test_root_total_is_derived_from_raw_response_not_a_caller_count():
    structure = structural_report(1)
    partition = structure.valid_leaves[0].name
    first = raw_page("root-a", partition, [0, 0, 1, ["A"]], order_by=ORDER)
    second = raw_page(
        "root-b", partition, [0, 0, 1, ["A"]],
        order_by="OrderByReleaseDateDESC",
    )
    primary = build_traversal_evidence(
        run_id=RUN_ID, traversal_id="root-a", tree_digest=structure.tree_digest,
        order_by=ORDER, pages=(first,),
    )
    reconciliation = build_traversal_evidence(
        run_id=RUN_ID, traversal_id="root-b", tree_digest=structure.tree_digest,
        order_by="OrderByReleaseDateDESC", pages=(second,),
    )
    facets = [{"type": "CATEGORYTREE", "values": [{
        "key": "category-1", "value": "leaf", "quantity": 1, "children": []
    }]}]
    report = evaluate_canonical_catalog_coverage(CanonicalCatalogEvidence(
        RUN_ID,
        {"recordsFiltered": "1"},
        {"recordsFiltered": 1, "sampling": False, "facets": facets},
        primary,
        reconciliation,
    ))
    assert report.accepted is False
    assert "invalid_structural_evidence" in report.coverage_reason
