from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import obtener_catalogo_walmart_operativo as operational  # noqa: E402


def product(product_id: str) -> dict:
    return {"productId": product_id, "items": [{"itemId": product_id}]}


def page(total: int, *product_ids: str) -> dict:
    return {
        "recordsFiltered": total,
        "products": [product(product_id) for product_id in product_ids],
    }


def facets(key: str, **values: int) -> dict:
    return {
        "facets": [
            {
                "key": key,
                "values": [
                    {"value": value, "quantity": quantity}
                    for value, quantity in values.items()
                ],
            }
        ]
    }


class FakeCapture:
    def __init__(self, responses: dict[str, dict]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get(self, _seller, tag, _path, _query):
        self.calls.append(tag)
        return self.responses[tag], {
            "tag": tag,
            "url": f"https://www.walmart.com.hn/{tag}",
            "sha256": tag.encode().hex().ljust(64, "0")[:64],
            "observed_at": "2026-09-07T20:51:41Z",
        }


def total_drift_responses() -> dict[str, dict]:
    prefix = "seller/category-1/electronica"
    recovery = f"{prefix}/recovery-total-drift"
    return {
        f"{prefix}/page-001": page(5, "1", "2"),
        f"{recovery}/page-001": page(5, "1", "2"),
        f"{recovery}/page-002": page(5, "3", "4"),
        f"{recovery}/page-003": page(5, "5"),
    }


def category2_drift_responses() -> dict[str, dict]:
    prefix = "seller/category-1/electronica/category-2"
    recovery = "/recovery-total-drift"
    return {
        "seller/electronica/category2-facets": facets("category-2", audio=4, video=2),
        f"{prefix}/audio/page-001": page(5, "1", "2"),
        "seller/electronica/category2-facets/recovery-total-drift": facets(
            "category-2", audio=5, video=2
        ),
        f"{prefix}/audio{recovery}/page-001": page(5, "1", "2"),
        f"{prefix}/audio{recovery}/page-002": page(5, "3", "4"),
        f"{prefix}/audio{recovery}/page-003": page(5, "5"),
        f"{prefix}/video{recovery}/page-001": page(2, "6", "7"),
        "seller/electronica/category2-facets/recovery-confirmation": facets(
            "category-2", audio=5, video=2
        ),
    }


def membership_shift_responses() -> dict[str, dict]:
    parent = "seller/category-1/electronica"
    recovery = "/recovery-membership-overlap"
    facet_prefix = "seller/electronica/category2-facets"
    return {
        f"{parent}/page-001": page(4, "1", "2"),
        f"{parent}/page-002": page(4, "2", "3"),
        f"{facet_prefix}{recovery}": facets("category-2", audio=2, video=2),
        f"{parent}/category-2/audio{recovery}/page-001": page(2, "1", "2"),
        f"{parent}/category-2/video{recovery}/page-001": page(2, "3", "4"),
        f"{facet_prefix}/recovery-membership-confirmation": facets(
            "category-2", audio=2, video=2
        ),
    }


def test_category_total_drift_restarts_category_and_requires_exact_membership(monkeypatch):
    monkeypatch.setattr(operational, "PAGE_SIZE", 2)
    capture = FakeCapture(total_drift_responses())

    products, evidence, final_total, recovery = operational._capture_category(
        capture,
        seller="seller",
        category="electronica",
        expected_total=4,
        common={},
        facets_root="/facets/seller",
    )

    assert set(products) == {"1", "2", "3", "4", "5"}
    assert final_total == 5
    assert len(evidence) == 3
    assert recovery == {
        "category": "electronica",
        "strategy": "parent_category_exact_restart",
        "trigger_tag": "seller/category-1/electronica/page-001",
        "trigger_sha256": "73656c6c65722f63617465676f72792d312f656c656374726f6e6963612f7061",
        "trigger_observed_at": "2026-09-07T20:51:41Z",
        "previous_total": 4,
        "recovered_total": 5,
    }


def test_category_total_drift_rejects_a_second_change(monkeypatch):
    monkeypatch.setattr(operational, "PAGE_SIZE", 2)
    responses = total_drift_responses()
    responses[
        "seller/category-1/electronica/recovery-total-drift/page-001"
    ] = page(6, "1", "2")

    with pytest.raises(RuntimeError, match="category_total_changed_again"):
        operational._capture_category(
            FakeCapture(responses),
            seller="seller",
            category="electronica",
            expected_total=4,
            common={},
            facets_root="/facets/seller",
        )


def test_category_total_recovery_rejects_repeated_membership(monkeypatch):
    monkeypatch.setattr(operational, "PAGE_SIZE", 2)
    responses = total_drift_responses()
    responses[
        "seller/category-1/electronica/recovery-total-drift/page-002"
    ] = page(5, "2", "3")

    with pytest.raises(RuntimeError, match="product_membership_overlap"):
        operational._capture_category(
            FakeCapture(responses),
            seller="seller",
            category="electronica",
            expected_total=4,
            common={},
            facets_root="/facets/seller",
        )


def test_category2_total_drift_rechecks_facets_and_restarts_all_child_partitions(monkeypatch):
    monkeypatch.setattr(operational, "PAGE_SIZE", 2)
    monkeypatch.setattr(operational, "CATEGORY2_PARTITIONS", {"electronica"})
    capture = FakeCapture(category2_drift_responses())

    products, evidence, final_total, recovery = operational._capture_category(
        capture,
        seller="seller",
        category="electronica",
        expected_total=6,
        common={},
        facets_root="/facets/seller",
    )

    assert set(products) == {"1", "2", "3", "4", "5", "6", "7"}
    assert final_total == 7
    assert len(evidence) == 4
    assert recovery is not None
    assert recovery["strategy"] == "category2_exact_restart"
    assert recovery["previous_total"] == 6
    assert recovery["recovered_total"] == 7
    assert recovery["previous_partition_total"] == 4
    assert recovery["recovered_partition_total"] == 5
    assert capture.calls[-1] == "seller/electronica/category2-facets/recovery-confirmation"


def _stale_child_facet_parent_responses() -> dict[str, dict]:
    responses = category2_drift_responses()
    responses["seller/electronica/category2-facets/recovery-total-drift"] = facets(
        "category-2", audio=4, video=2
    )
    parent = "seller/category-1/electronica"
    first = f"{parent}/recovery-parent-facet-disagreement"
    confirmation = f"{parent}/recovery-parent-facet-disagreement-confirmation"
    for prefix in (first, confirmation):
        responses[f"{prefix}/page-001"] = page(6, "1", "2")
        responses[f"{prefix}/page-002"] = page(6, "3", "4")
        responses[f"{prefix}/page-003"] = page(6, "5", "6")
    return responses


def test_category2_stale_facet_uses_two_exact_parent_membership_passes(monkeypatch):
    monkeypatch.setattr(operational, "PAGE_SIZE", 2)
    monkeypatch.setattr(operational, "CATEGORY2_PARTITIONS", {"electronica"})
    capture = FakeCapture(_stale_child_facet_parent_responses())

    products, evidence, final_total, recovery = operational._capture_category(
        capture,
        seller="seller",
        category="electronica",
        expected_total=6,
        common={},
        facets_root="/facets/seller",
    )

    assert set(products) == {"1", "2", "3", "4", "5", "6"}
    assert len(evidence) == 3
    assert final_total == 6
    assert recovery is not None
    assert recovery["strategy"] == "category2_stale_facet_parent_restart"
    assert recovery["previous_partition_total"] == 4
    assert recovery["observed_partition_total"] == 5
    assert recovery["confirmed_membership_sha256"]
    assert capture.calls[-1].endswith(
        "recovery-parent-facet-disagreement-confirmation/page-003"
    )


def test_category2_stale_facet_rejects_unconfirmed_parent_membership(monkeypatch):
    monkeypatch.setattr(operational, "PAGE_SIZE", 2)
    monkeypatch.setattr(operational, "CATEGORY2_PARTITIONS", {"electronica"})
    responses = _stale_child_facet_parent_responses()
    responses[
        "seller/category-1/electronica/"
        "recovery-parent-facet-disagreement-confirmation/page-003"
    ] = page(6, "5", "7")

    with pytest.raises(RuntimeError, match="category2_parent_membership_not_confirmed"):
        operational._capture_category(
            FakeCapture(responses),
            seller="seller",
            category="electronica",
            expected_total=6,
            common={},
            facets_root="/facets/seller",
        )


def test_category2_changed_facet_still_requires_observed_total_confirmation(monkeypatch):
    monkeypatch.setattr(operational, "PAGE_SIZE", 2)
    monkeypatch.setattr(operational, "CATEGORY2_PARTITIONS", {"electronica"})
    responses = category2_drift_responses()
    responses["seller/electronica/category2-facets/recovery-total-drift"] = facets(
        "category-2", audio=4, video=3
    )
    with pytest.raises(RuntimeError, match="category2_drift_not_confirmed"):
        operational._capture_category(
            FakeCapture(responses),
            seller="seller",
            category="electronica",
            expected_total=6,
            common={},
            facets_root="/facets/seller",
        )


def test_category2_total_recovery_fails_closed_if_facets_change_again(monkeypatch):
    monkeypatch.setattr(operational, "PAGE_SIZE", 2)
    monkeypatch.setattr(operational, "CATEGORY2_PARTITIONS", {"electronica"})
    responses = category2_drift_responses()
    responses["seller/electronica/category2-facets/recovery-confirmation"] = facets(
        "category-2", audio=5, video=3
    )

    with pytest.raises(RuntimeError, match="category2_changed_during_recovery"):
        operational._capture_category(
            FakeCapture(responses),
            seller="seller",
            category="electronica",
            expected_total=6,
            common={},
            facets_root="/facets/seller",
        )


def test_membership_shift_repartitions_category2_and_confirms_facets(monkeypatch):
    monkeypatch.setattr(operational, "PAGE_SIZE", 2)
    monkeypatch.setattr(operational, "CATEGORY2_PARTITIONS", set())
    capture = FakeCapture(membership_shift_responses())

    products, evidence, final_total, recovery = operational._capture_category(
        capture,
        seller="seller",
        category="electronica",
        expected_total=4,
        common={},
        facets_root="/facets/seller",
    )

    assert set(products) == {"1", "2", "3", "4"}
    assert len(evidence) == 2
    assert final_total == 4
    assert recovery is not None
    assert recovery["strategy"] == "category2_exact_membership_restart"
    assert recovery["trigger_tag"].endswith("page-002")
    assert recovery["duplicate_product_count"] == 1
    assert recovery["previous_total"] == recovery["recovered_total"] == 4
    assert capture.calls[-1].endswith("recovery-membership-confirmation")


def test_store_reports_membership_recovery_separately_from_total_recovery(monkeypatch):
    monkeypatch.setattr(operational, "PAGE_SIZE", 2)
    monkeypatch.setattr(operational, "CATEGORY2_PARTITIONS", set())
    monkeypatch.setattr(operational, "parse_products", _parsed_products)
    responses = membership_shift_responses()
    responses.update(
        {
            "seller/facets-before": facets("category-1", electronica=4),
            "seller/root-before": page(4, "root"),
            "seller/facets-after": facets("category-1", electronica=4),
            "seller/root-after": page(4, "root"),
        }
    )

    snapshot = operational.capture_store(
        FakeCapture(responses),
        seller="seller",
        location_id="location",
        city="city",
        store_name="store",
        home_sha="h" * 64,
    )

    assert snapshot["category_total_recoveries"] == []
    assert [item["strategy"] for item in snapshot["membership_recoveries"]] == [
        "category2_exact_membership_restart"
    ]


def test_membership_recovery_requires_exact_child_total(monkeypatch):
    monkeypatch.setattr(operational, "PAGE_SIZE", 2)
    monkeypatch.setattr(operational, "CATEGORY2_PARTITIONS", set())
    responses = membership_shift_responses()
    responses[
        "seller/electronica/category2-facets/recovery-membership-overlap"
    ] = facets("category-2", audio=2, video=1)

    with pytest.raises(RuntimeError, match="category2_membership_total_mismatch"):
        operational._capture_category(
            FakeCapture(responses),
            seller="seller",
            category="electronica",
            expected_total=4,
            common={},
            facets_root="/facets/seller",
        )


def test_membership_recovery_rejects_another_overlap(monkeypatch):
    monkeypatch.setattr(operational, "PAGE_SIZE", 2)
    monkeypatch.setattr(operational, "CATEGORY2_PARTITIONS", set())
    responses = membership_shift_responses()
    responses[
        "seller/category-1/electronica/category-2/video/recovery-membership-overlap/page-001"
    ] = page(2, "2", "3")

    with pytest.raises(RuntimeError, match="category_membership_changed_again"):
        operational._capture_category(
            FakeCapture(responses),
            seller="seller",
            category="electronica",
            expected_total=4,
            common={},
            facets_root="/facets/seller",
        )


def test_membership_recovery_rejects_partition_total_change(monkeypatch):
    monkeypatch.setattr(operational, "PAGE_SIZE", 2)
    monkeypatch.setattr(operational, "CATEGORY2_PARTITIONS", set())
    responses = membership_shift_responses()
    responses[
        "seller/category-1/electronica/category-2/video/recovery-membership-overlap/page-001"
    ] = page(3, "3", "4")

    with pytest.raises(RuntimeError, match="category_changed_during_membership_recovery"):
        operational._capture_category(
            FakeCapture(responses),
            seller="seller",
            category="electronica",
            expected_total=4,
            common={},
            facets_root="/facets/seller",
        )


def test_membership_recovery_rejects_changed_confirmation(monkeypatch):
    monkeypatch.setattr(operational, "PAGE_SIZE", 2)
    monkeypatch.setattr(operational, "CATEGORY2_PARTITIONS", set())
    responses = membership_shift_responses()
    responses[
        "seller/electronica/category2-facets/recovery-membership-confirmation"
    ] = facets("category-2", audio=1, video=3)

    with pytest.raises(RuntimeError, match="category2_changed_during_membership_recovery"):
        operational._capture_category(
            FakeCapture(responses),
            seller="seller",
            category="electronica",
            expected_total=4,
            common={},
            facets_root="/facets/seller",
        )


def _parsed_products(products: list[dict]) -> tuple[list[dict], dict[str, dict]]:
    rows = [
        {
            "source_key": item["productId"],
            "current_price": 1,
            "availability": "available",
            "is_promotion": False,
        }
        for item in products
    ]
    return rows, {row["source_key"]: {} for row in rows}


def final_facet_drift_responses(*, confirmation_electronica: int = 1) -> dict[str, dict]:
    responses = {
        "seller/facets-before": facets("category-1", electronica=2, abarrotes=2),
        "seller/root-before": page(4, "root"),
        "seller/category-1/electronica/page-001": page(2, "1", "2"),
        "seller/category-1/abarrotes/page-001": page(2, "3", "4"),
        "seller/facets-after": facets("category-1", electronica=1, abarrotes=2),
        "seller/category-1/electronica/recovery-final-facet-stabilization-1/page-001": page(
            1, "1"
        ),
        "seller/facets-after/recovery-confirmation": facets(
            "category-1", electronica=confirmation_electronica, abarrotes=2
        ),
        "seller/root-after": page(3, "root"),
    }
    responses.update(_stability_probe_responses(cycle=1, facet_total=1, search_total=1))
    return responses


def _stability_probe_responses(
    *, cycle: int, facet_total: int, search_total: int
) -> dict[str, dict]:
    prefix = f"seller/category-1/electronica/stabilization-cycle-{cycle}"
    return {
        f"{prefix}/probe-1/facets": facets(
            "category-1", electronica=facet_total, abarrotes=2
        ),
        f"{prefix}/probe-1/search": page(search_total, "1"),
        f"{prefix}/probe-2/facets": facets(
            "category-1", electronica=facet_total, abarrotes=2
        ),
        f"{prefix}/probe-2/search": page(search_total, "1"),
    }


def test_final_facet_drift_recaptures_only_changed_category_and_confirms(monkeypatch):
    monkeypatch.setattr(operational, "PAGE_SIZE", 2)
    monkeypatch.setattr(operational, "CATEGORY2_PARTITIONS", set())
    monkeypatch.setattr(operational, "parse_products", _parsed_products)
    waits: list[float] = []
    monkeypatch.setattr(operational.time, "sleep", waits.append)
    capture = FakeCapture(final_facet_drift_responses())

    snapshot = operational.capture_store(
        capture,
        seller="seller",
        location_id="location",
        city="city",
        store_name="store",
        home_sha="h" * 64,
    )

    assert snapshot["catalog_products_reported"] == 3
    assert snapshot["unique_products_extracted"] == 3
    assert {row["source_key"] for row in snapshot["products"]} == {"1", "3", "4"}
    assert [item["strategy"] for item in snapshot["category_total_recoveries"]] == [
        "final_facet_stabilized_category_restart"
    ]
    recovery = snapshot["category_total_recoveries"][0]
    assert recovery["previous_total"] == 2
    assert recovery["recovered_total"] == 1
    assert recovery["stabilization_cycles_used"] == 1
    assert len(recovery["stabilization_observations"]) == 2
    assert all(item["sources_agree"] for item in recovery["stabilization_observations"])
    assert waits == [120.0, 60.0]
    evidence_tags = {item["tag"] for item in snapshot["page_evidence"]}
    assert "seller/category-1/electronica/page-001" not in evidence_tags
    assert (
        "seller/category-1/electronica/"
        "recovery-final-facet-stabilization-1/page-001"
    ) in evidence_tags
    assert "seller/category-1/abarrotes/page-001" in evidence_tags
    assert snapshot["binding_evidence"]["facet_recovery_confirmation_sha256"]


def test_final_facet_recovery_fails_if_catalog_changes_again(monkeypatch):
    monkeypatch.setattr(operational, "PAGE_SIZE", 2)
    monkeypatch.setattr(operational, "CATEGORY2_PARTITIONS", set())
    monkeypatch.setattr(operational.time, "sleep", lambda _seconds: None)
    responses = final_facet_drift_responses(confirmation_electronica=2)

    with pytest.raises(RuntimeError, match="catalog_changed_during_final_recovery"):
        operational.capture_store(
            FakeCapture(responses),
            seller="seller",
            location_id="location",
            city="city",
            store_name="store",
            home_sha="h" * 64,
        )


def test_final_facet_recovery_fails_on_category_shape_change(monkeypatch):
    monkeypatch.setattr(operational, "PAGE_SIZE", 2)
    monkeypatch.setattr(operational, "CATEGORY2_PARTITIONS", set())
    responses = final_facet_drift_responses()
    responses["seller/facets-after"] = facets("category-1", electronica=1, nuevo=2)

    with pytest.raises(RuntimeError, match="catalog_category_shape_changed"):
        operational.capture_store(
            FakeCapture(responses),
            seller="seller",
            location_id="location",
            city="city",
            store_name="store",
            home_sha="h" * 64,
        )


def test_final_facet_category_restart_rejects_another_total_change(monkeypatch):
    monkeypatch.setattr(operational, "PAGE_SIZE", 2)
    monkeypatch.setattr(operational, "CATEGORY2_PARTITIONS", set())
    monkeypatch.setattr(operational.time, "sleep", lambda _seconds: None)
    responses = final_facet_drift_responses()
    responses[
        "seller/category-1/electronica/"
        "recovery-final-facet-stabilization-1/page-001"
    ] = page(2, "1")
    responses.update(_stability_probe_responses(cycle=2, facet_total=2, search_total=2))
    responses[
        "seller/category-1/electronica/"
        "recovery-final-facet-stabilization-2/page-001"
    ] = page(3, "1", "2")

    with pytest.raises(
        RuntimeError, match="category_unstable_after_bounded_stabilization"
    ):
        operational.capture_store(
            FakeCapture(responses),
            seller="seller",
            location_id="location",
            city="city",
            store_name="store",
            home_sha="h" * 64,
        )


def test_final_facet_recovery_waits_until_facets_and_search_agree(monkeypatch):
    monkeypatch.setattr(operational, "PAGE_SIZE", 2)
    monkeypatch.setattr(operational, "CATEGORY2_PARTITIONS", set())
    monkeypatch.setattr(operational, "parse_products", _parsed_products)
    waits: list[float] = []
    monkeypatch.setattr(operational.time, "sleep", waits.append)
    responses = final_facet_drift_responses(confirmation_electronica=3)
    responses["seller/facets-after"] = facets(
        "category-1", electronica=3, abarrotes=2
    )
    responses.update(_stability_probe_responses(cycle=1, facet_total=3, search_total=2))
    responses.update(_stability_probe_responses(cycle=2, facet_total=3, search_total=3))
    responses[
        "seller/category-1/electronica/"
        "recovery-final-facet-stabilization-2/page-001"
    ] = page(3, "1", "2")
    responses[
        "seller/category-1/electronica/"
        "recovery-final-facet-stabilization-2/page-002"
    ] = page(3, "5")
    responses["seller/root-after"] = page(5, "root")

    snapshot = operational.capture_store(
        FakeCapture(responses),
        seller="seller",
        location_id="location",
        city="city",
        store_name="store",
        home_sha="h" * 64,
    )

    assert snapshot["catalog_products_reported"] == 5
    assert {row["source_key"] for row in snapshot["products"]} == {
        "1",
        "2",
        "3",
        "4",
        "5",
    }
    recovery = snapshot["category_total_recoveries"][0]
    assert recovery["stabilization_cycles_used"] == 2
    assert recovery["recovered_total"] == 3
    assert [item["sources_agree"] for item in recovery["stabilization_observations"]] == [
        False,
        False,
        True,
        True,
    ]
    assert waits == [120.0, 60.0, 600.0, 60.0]


def _category2_facet_gap_responses() -> dict[str, dict]:
    """Seis productos en la categoría y sólo cinco con ``category-2`` (caso 19-nov-2026)."""
    parent = "seller/category-1/electronica"
    first = f"{parent}/recovery-category2-facet-gap"
    responses = {
        "seller/electronica/category2-facets": facets("category-2", audio=3, video=2),
    }
    for prefix in (first, f"{first}-confirmation"):
        responses[f"{prefix}/page-001"] = page(6, "1", "2")
        responses[f"{prefix}/page-002"] = page(6, "3", "4")
        responses[f"{prefix}/page-003"] = page(6, "5", "6")
    return responses


def test_category2_facet_gap_captures_parent_twice_with_exact_membership(monkeypatch):
    monkeypatch.setattr(operational, "PAGE_SIZE", 2)
    monkeypatch.setattr(operational, "CATEGORY2_PARTITIONS", {"electronica"})
    capture = FakeCapture(_category2_facet_gap_responses())

    products, evidence, final_total, recovery = operational._capture_category(
        capture,
        seller="seller",
        category="electronica",
        expected_total=6,
        common={},
        facets_root="/facets/seller",
    )

    assert set(products) == {"1", "2", "3", "4", "5", "6"}
    assert final_total == 6
    assert len(evidence) == 3
    assert recovery is not None
    assert recovery["strategy"] == "category2_facet_gap_parent_restart"
    assert recovery["previous_total"] == recovery["recovered_total"] == 6
    assert recovery["category2_total"] == 5
    assert recovery["confirmed_membership_sha256"]
    assert recovery["first_parent_evidence_sha256"]
    assert recovery["confirmed_parent_evidence_sha256"]
    # Ninguna petición a las subcategorías: no pueden reconstruir la categoría.
    assert not [call for call in capture.calls if "/category-2/" in call]
    assert len(capture.calls) == 7


def test_category2_facet_gap_rejects_unconfirmed_parent_membership(monkeypatch):
    monkeypatch.setattr(operational, "PAGE_SIZE", 2)
    monkeypatch.setattr(operational, "CATEGORY2_PARTITIONS", {"electronica"})
    responses = _category2_facet_gap_responses()
    responses[
        "seller/category-1/electronica/recovery-category2-facet-gap-confirmation/page-003"
    ] = page(6, "5", "7")

    with pytest.raises(RuntimeError, match="category2_parent_membership_not_confirmed"):
        operational._capture_category(
            FakeCapture(responses),
            seller="seller",
            category="electronica",
            expected_total=6,
            common={},
            facets_root="/facets/seller",
        )


def test_category2_facet_gap_fails_closed_if_parent_total_changes(monkeypatch):
    monkeypatch.setattr(operational, "PAGE_SIZE", 2)
    monkeypatch.setattr(operational, "CATEGORY2_PARTITIONS", {"electronica"})
    responses = _category2_facet_gap_responses()
    responses[
        "seller/category-1/electronica/recovery-category2-facet-gap/page-002"
    ] = page(7, "3", "4")

    with pytest.raises(RuntimeError, match="category2_parent_recovery_changed"):
        operational._capture_category(
            FakeCapture(responses),
            seller="seller",
            category="electronica",
            expected_total=6,
            common={},
            facets_root="/facets/seller",
        )


def test_category2_facet_gap_keeps_failing_when_parent_does_not_fit(monkeypatch):
    monkeypatch.setattr(operational, "PAGE_SIZE", 2)
    monkeypatch.setattr(operational, "MAX_SEARCH_PAGES", 2)
    monkeypatch.setattr(operational, "CATEGORY2_PARTITIONS", {"electronica"})
    capture = FakeCapture(_category2_facet_gap_responses())

    with pytest.raises(RuntimeError, match="category2_total_mismatch:seller:electronica"):
        operational._capture_category(
            capture,
            seller="seller",
            category="electronica",
            expected_total=6,
            common={},
            facets_root="/facets/seller",
        )
    assert capture.calls == ["seller/electronica/category2-facets"]


def test_store_reports_category2_facet_gap_as_total_recovery(monkeypatch):
    monkeypatch.setattr(operational, "PAGE_SIZE", 2)
    monkeypatch.setattr(operational, "CATEGORY2_PARTITIONS", {"electronica"})
    monkeypatch.setattr(operational, "parse_products", _parsed_products)
    responses = _category2_facet_gap_responses()
    responses.update(
        {
            "seller/facets-before": facets("category-1", electronica=6, abarrotes=2),
            "seller/root-before": page(8, "root"),
            "seller/category-1/abarrotes/page-001": page(2, "7", "8"),
            "seller/facets-after": facets("category-1", electronica=6, abarrotes=2),
            "seller/root-after": page(8, "root"),
        }
    )

    snapshot = operational.capture_store(
        FakeCapture(responses),
        seller="seller",
        location_id="location",
        city="city",
        store_name="store",
        home_sha="h" * 64,
    )

    assert snapshot["catalog_products_reported"] == 8
    assert snapshot["unique_products_extracted"] == 8
    assert snapshot["membership_recoveries"] == []
    assert [item["strategy"] for item in snapshot["category_total_recoveries"]] == [
        "category2_facet_gap_parent_restart"
    ]
    evidence_tags = {item["tag"] for item in snapshot["page_evidence"]}
    assert (
        "seller/category-1/electronica/recovery-category2-facet-gap-confirmation/page-001"
        in evidence_tags
    )


def test_final_facet_recovery_accepts_category2_facet_gap(monkeypatch):
    """La categoría con hueco cambia de total al final: la recaptura vuelve a usar el padre."""
    monkeypatch.setattr(operational, "PAGE_SIZE", 2)
    monkeypatch.setattr(operational, "CATEGORY2_PARTITIONS", {"electronica"})
    monkeypatch.setattr(operational, "parse_products", _parsed_products)
    monkeypatch.setattr(operational.time, "sleep", lambda _seconds: None)
    responses = _category2_facet_gap_responses()
    final = "seller/category-1/electronica/recovery-final-facet-stabilization-1"
    gap = f"{final}/recovery-category2-facet-gap"
    responses.update(
        {
            "seller/facets-before": facets("category-1", electronica=6, abarrotes=2),
            "seller/root-before": page(8, "root"),
            "seller/category-1/abarrotes/page-001": page(2, "7", "8"),
            "seller/facets-after": facets("category-1", electronica=4, abarrotes=2),
            "seller/electronica/category2-facets/recovery-final-facet-stabilization-1": facets(
                "category-2", audio=2, video=1
            ),
            "seller/facets-after/recovery-confirmation": facets(
                "category-1", electronica=4, abarrotes=2
            ),
            "seller/root-after": page(6, "root"),
        }
    )
    for prefix in (gap, f"{gap}-confirmation"):
        responses[f"{prefix}/page-001"] = page(4, "1", "2")
        responses[f"{prefix}/page-002"] = page(4, "3", "4")
    responses.update(_stability_probe_responses(cycle=1, facet_total=4, search_total=4))

    snapshot = operational.capture_store(
        FakeCapture(responses),
        seller="seller",
        location_id="location",
        city="city",
        store_name="store",
        home_sha="h" * 64,
    )

    assert snapshot["catalog_products_reported"] == 6
    assert {row["source_key"] for row in snapshot["products"]} == {"1", "2", "3", "4", "7", "8"}
    strategies = [item["strategy"] for item in snapshot["category_total_recoveries"]]
    assert strategies == [
        "category2_facet_gap_parent_restart",
        "final_facet_stabilized_category_restart",
    ]
    final_recovery = snapshot["category_total_recoveries"][1]
    assert final_recovery["recovered_total"] == 4
    assert final_recovery["category2_facet_gap"]["category2_total"] == 3
