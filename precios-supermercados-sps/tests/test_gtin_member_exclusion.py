"""Exclusión por miembro en grupos GTIN exactos (motor v2.4).

Un conflicto atribuible a un solo miembro lo retira del grupo; el resto sigue
``ready`` sólo si conserva al menos dos cadenas y ninguna colisión por cadena.
Si el conflicto no se puede atribuir (empate), se retiran todos los implicados.
"""
from __future__ import annotations

import json

from precios_supermercados.product_homologation import SourceProductRecord
from precios_supermercados.product_homologation_persistence import build_homologation_rows
from precios_supermercados.product_identity_v2 import homologate_products_v2

GTIN = "7501055300075"  # EAN-13 global (México 750)


def product(
    record_id: str,
    supermarket: str,
    name: str,
    *,
    barcode: str = GTIN,
    presentation: str | None = None,
) -> SourceProductRecord:
    return SourceProductRecord(
        source_record_id=record_id,
        supermarket_id=supermarket,
        source_name=name,
        source_brand="Jumex",
        source_presentation=presentation,
        barcode=barcode,
    )


def only_group(records):
    result = homologate_products_v2(records)
    (group,) = result.exact_gtin_groups
    return group


def test_odd_member_with_flavor_conflict_is_excluded_and_rest_stays_ready() -> None:
    group = only_group(
        (
            product("la_colonia:1", "la_colonia", "Jugo Jumex Mango 1 L"),
            product("walmart:1", "walmart", "Jugo Jumex Mango 1 L"),
            product("paiz:1", "paiz", "Jugo Jumex Durazno 1 L"),
        )
    )
    assert group.comparison_status == "ready"
    assert group.source_record_ids == ("la_colonia:1", "walmart:1")
    assert group.supermarket_ids == ("la_colonia", "walmart")
    assert group.excluded_members == (("paiz:1", ("flavor_conflict",)),)


def test_odd_member_with_presentation_conflict_is_excluded() -> None:
    group = only_group(
        (
            product("la_colonia:1", "la_colonia", "Jugo Jumex Mango 1 L"),
            product("walmart:1", "walmart", "Jugo Jumex Mango 1 L"),
            product("colonial:1", "colonial", "Jugo Jumex Mango 473 ml"),
        )
    )
    assert group.comparison_status == "ready"
    assert dict(group.excluded_members) == {"colonial:1": ("cross_source_presentation_conflict",)}


def test_two_member_conflict_cannot_be_attributed_and_blocks_group() -> None:
    group = only_group(
        (
            product("walmart:1", "walmart", "Jugo Jumex Mango 1 L"),
            product("paiz:1", "paiz", "Jugo Jumex Durazno 1 L"),
        )
    )
    assert group.comparison_status == "review_required"
    assert group.conflict_reasons == ("flavor_conflict",)
    assert set(group.source_record_ids) == {"walmart:1", "paiz:1"}
    assert group.excluded_members == ()


def test_tied_conflict_excludes_both_sides_and_fails_closed_when_one_retailer_left() -> None:
    group = only_group(
        (
            product("walmart:1", "walmart", "Jugo Jumex Mango 1 L"),
            product("paiz:1", "paiz", "Jugo Jumex Durazno 1 L"),
            product("la_colonia:1", "la_colonia", "Jugo Jumex 1 L"),
        )
    )
    # Mango/Durazno chocan entre sí y cada uno declara sabor frente al nombre sin
    # sabor: empate de grado 2 en los tres, todos se retiran.
    assert group.comparison_status == "review_required"
    assert group.conflict_reasons == ("flavor_conflict", "one_sided_flavor_declared")


def test_tied_conflict_keeps_untouched_core_with_two_retailers() -> None:
    group = only_group(
        (
            product("walmart:1", "walmart", "Jugo Jumex Mango 1 L"),
            product("paiz:1", "paiz", "Jugo Jumex Durazno 1 L"),
            product("la_colonia:1", "la_colonia", "Jugo Jumex Néctar 1 L"),
            product("colonial:1", "colonial", "Jugo Jumex Néctar 1 L"),
        )
    )
    assert group.comparison_status == "ready"
    assert group.source_record_ids == ("colonial:1", "la_colonia:1")
    assert set(dict(group.excluded_members)) == {"walmart:1", "paiz:1"}


def test_retailer_collision_excludes_every_record_of_that_retailer() -> None:
    group = only_group(
        (
            product("walmart:1", "walmart", "Jugo Jumex Mango 1 L"),
            product("walmart:2", "walmart", "Jugo Jumex Mango 1 Litro"),
            product("paiz:1", "paiz", "Jugo Jumex Mango 1 L"),
            product("la_colonia:1", "la_colonia", "Jugo Jumex Mango 1 L"),
        )
    )
    assert group.comparison_status == "ready"
    assert group.supermarket_ids == ("la_colonia", "paiz")
    assert dict(group.excluded_members) == {
        "walmart:1": ("retailer_collision",),
        "walmart:2": ("retailer_collision",),
    }


def test_retailer_collision_without_two_remaining_retailers_is_review() -> None:
    group = only_group(
        (
            product("walmart:1", "walmart", "Jugo Jumex Mango 1 L"),
            product("walmart:2", "walmart", "Jugo Jumex Mango 1 Litro"),
            product("paiz:1", "paiz", "Jugo Jumex Mango 1 L"),
        )
    )
    assert group.comparison_status == "review_required"
    assert group.conflict_reasons == ("retailer_collision",)


def test_member_with_ambiguous_multipack_is_excluded_not_the_group() -> None:
    group = only_group(
        (
            product("walmart:1", "walmart", "Jugo Jumex Mango 1 L"),
            product("paiz:1", "paiz", "Jugo Jumex Mango 1 L"),
            product("la_colonia:1", "la_colonia", "Jugo Jumex Mango 3 Pack 1 L"),
        )
    )
    assert group.comparison_status == "ready"
    assert dict(group.excluded_members) == {"la_colonia:1": ("ambiguous_multipack_presentation",)}


def test_restricted_gtin_excludes_retailer_outside_shared_master() -> None:
    group = only_group(
        (
            product("walmart:1", "walmart", "Croissant Sin Relleno", barcode="0000000001083"),
            product("paiz:1", "paiz", "Croissant Sin Relleno", barcode="0000000001083"),
            product("la_colonia:1", "la_colonia", "Croissant Sin Relleno", barcode="0000000001083"),
        )
    )
    assert group.comparison_status == "ready"
    assert group.supermarket_ids == ("paiz", "walmart")
    assert dict(group.excluded_members) == {
        "la_colonia:1": ("restricted_gtin_outside_shared_master",)
    }


def test_persistence_marks_only_excluded_member_for_review() -> None:
    rows = build_homologation_rows(
        (
            (1, product("la_colonia:1", "la_colonia", "Jugo Jumex Mango 1 L")),
            (2, product("walmart:2", "walmart", "Jugo Jumex Mango 1 L")),
            (3, product("paiz:3", "paiz", "Jugo Jumex Durazno 1 L")),
        ),
        updated_at_utc="2026-09-30T00:00:00Z",
    )
    by_id = {row.product_id: row for row in rows}
    assert by_id[1].comparison_status == by_id[2].comparison_status == "ready"
    assert json.loads(by_id[1].conflict_reasons_json) == []
    assert by_id[3].comparison_status == "review_required"
    assert json.loads(by_id[3].conflict_reasons_json) == [
        "flavor_conflict",
        "member_excluded_from_ready_group",
    ]
    assert by_id[1].canonical_product_id == by_id[2].canonical_product_id == by_id[3].canonical_product_id
