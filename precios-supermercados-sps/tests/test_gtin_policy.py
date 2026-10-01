"""Política GS1 de circulación restringida: identidad sólo dentro de un maestro."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from precios_supermercados.gtin_policy import (
    RESTRICTED_GTIN13_PREFIX_RANGES,
    RESTRICTED_GTIN14_INDICATORS,
    RESTRICTED_GTIN8_FIRST_DIGITS,
    RESTRICTED_GTIN_POLICY_VERSION,
    SHARED_PRODUCT_MASTERS,
    product_master_of,
    restricted_circulation_reason,
    restricted_gtin_master_partition,
    restricted_gtin_shared_master_ok,
)
from precios_supermercados.product_homologation import SourceProductRecord, homologate_products
from precios_supermercados.product_identity_decisions import assess_product_relation
from precios_supermercados.product_identity_v2 import homologate_products_v2

POLICY = Path(__file__).resolve().parents[1] / "config/homologation/identity-policy-v1.yaml"


def with_check_digit(body: str) -> str:
    total = sum(int(digit) * (3 if index % 2 == 0 else 1) for index, digit in enumerate(reversed(body)))
    return body + str((10 - total % 10) % 10)


def product(record_id: str, supermarket: str, name: str, barcode: str) -> SourceProductRecord:
    return SourceProductRecord(
        source_record_id=record_id,
        supermarket_id=supermarket,
        source_name=name,
        source_brand="Demo",
        barcode=barcode,
    )


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("2572480000002", "rcn_in_store"),  # Walmart: repollo por libra (peso variable)
        ("0000000001083", "rcn8"),  # Paiz: croissant, PLU interno rellenado
        (with_check_digit("21234567890"), "rcn_upc_variable_measure"),  # UPC-A 2…
        (with_check_digit("41234567890"), "rcn_upc_company_internal"),  # UPC-A 4…
        (with_check_digit("021234567890"), "rcn_upc_variable_measure"),  # EAN-13 020-029
        (with_check_digit("041234567890"), "rcn_upc_company_internal"),  # EAN-13 040-049
        (with_check_digit("0123456"), "rcn8"),  # EAN-8 0…
        (with_check_digit("2123456"), "rcn8"),  # EAN-8 2…
        (with_check_digit("980123456789"), "coupon_or_refund"),
        (with_check_digit("991234567890"), "coupon_or_refund"),
        (with_check_digit("9742863010079"), "variable_measure_gtin14"),
        ("7428630100793", None),  # Arroz Suli: GS1 Honduras 742
        ("785331778506", None),  # Gwaltney UPC-A global
        ("070404001491", None),
        ("96385074", None),  # EAN-8 global
        (with_check_digit("1742863010079"), None),  # GTIN-14 indicador 1
        ("7428630100794", None),  # check digit inválido: nunca es identidad
        (None, None),
    ],
)
def test_restricted_circulation_ranges(code, expected) -> None:
    assert restricted_circulation_reason(code) == expected


def test_policy_yaml_mirrors_code() -> None:
    policy = yaml.safe_load(POLICY.read_text(encoding="utf-8"))["restricted_gtin"]
    assert policy["policy_version"] == RESTRICTED_GTIN_POLICY_VERSION
    assert set(policy["gtin8_first_digits"]) == set(RESTRICTED_GTIN8_FIRST_DIGITS)
    assert set(policy["gtin14_indicators"]) == set(RESTRICTED_GTIN14_INDICATORS)
    assert [
        (item["low"], item["high"], item["reason"]) for item in policy["gtin13_prefix_ranges"]
    ] == list(RESTRICTED_GTIN13_PREFIX_RANGES)
    assert {
        name: frozenset(members) for name, members in policy["shared_product_masters"].items()
    } == SHARED_PRODUCT_MASTERS


def test_master_partition_is_fail_closed() -> None:
    assert product_master_of("walmart") == product_master_of("paiz") == "walmart_cam"
    assert product_master_of("la_colonia") == "retailer:la_colonia"
    assert restricted_gtin_master_partition({"walmart", "paiz", "la_colonia"}) == (
        frozenset({"walmart", "paiz"}),
        frozenset({"la_colonia"}),
    )
    assert restricted_gtin_master_partition({"walmart", "la_colonia"}) == (
        None,
        frozenset({"walmart", "la_colonia"}),
    )
    assert restricted_gtin_shared_master_ok("2572480000002", {"walmart", "paiz"})
    assert not restricted_gtin_shared_master_ok("2572480000002", {"walmart", "la_colonia"})
    assert restricted_gtin_shared_master_ok("7428630100793", {"walmart", "la_colonia"})


@pytest.mark.parametrize("engine", [homologate_products, homologate_products_v2])
def test_restricted_gtin_across_masters_is_not_ready(engine) -> None:
    result = engine(
        (
            product("la_colonia:1", "la_colonia", "Croissant Sin Relleno 1 unidad", "0000000001083"),
            product("walmart:1", "walmart", "Croissant Sin Relleno 1 unidad", "0000000001083"),
        )
    )
    (group,) = result.exact_gtin_groups
    assert group.comparison_status == "review_required"
    assert "restricted_gtin_outside_shared_master" in group.conflict_reasons


@pytest.mark.parametrize("engine", [homologate_products, homologate_products_v2])
def test_restricted_gtin_within_walmart_cam_master_stays_ready(engine) -> None:
    result = engine(
        (
            product("paiz:1", "paiz", "Croissant Sin Relleno 1 unidad", "0000000001083"),
            product("walmart:1", "walmart", "Croissant Sin Relleno 1 unidad", "0000000001083"),
        )
    )
    (group,) = result.exact_gtin_groups
    assert group.comparison_status == "ready"
    assert group.conflict_reasons == ()


@pytest.mark.parametrize("engine", [homologate_products, homologate_products_v2])
def test_global_gtin_across_masters_stays_ready(engine) -> None:
    result = engine(
        (
            product("la_colonia:1", "la_colonia", "Arroz Suli Blanco 1500 g", "7428630100793"),
            product("walmart:1", "walmart", "Arroz Suli Blanco 1500 g", "7428630100793"),
        )
    )
    (group,) = result.exact_gtin_groups
    assert group.comparison_status == "ready"


def test_restricted_gtin_pair_is_not_rule_verified_identity() -> None:
    result = homologate_products_v2(
        (
            product("la_colonia:1", "la_colonia", "Croissant Sin Relleno 1 unidad", "0000000001083"),
            product("walmart:1", "walmart", "Croissant Sin Relleno 1 unidad", "0000000001083"),
            product("paiz:1", "paiz", "Croissant Sin Relleno 1 unidad", "0000000001083"),
        )
    )
    profiles = {profile.record.source_record_id: profile for profile in result.profiles}
    cross = assess_product_relation(profiles["la_colonia:1"], profiles["walmart:1"])
    assert cross.relation == "UNRESOLVED"
    assert cross.reasons == ("restricted_gtin_outside_shared_master",)
    shared = assess_product_relation(profiles["paiz:1"], profiles["walmart:1"])
    assert shared.relation == "EXACT_TRADE_ITEM"
    assert shared.decision_state == "rule_verified"
