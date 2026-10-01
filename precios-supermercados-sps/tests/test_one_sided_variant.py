"""Variante declarada sólo por un lado: no es identidad automática (v2.4).

Caso real: el catálogo publicado 2026-09-21 agrupaba bajo el GTIN 785331778506
"Salchichas Tradicional Gwaltney" (Walmart/Paiz) con "Salchicha Gwaltney De
Pollo Bun Size 454 Gr" (La Colonia), con precios L 95-228 (ratio 2.4).
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from precios_supermercados.product_homologation import SourceProductRecord
from precios_supermercados.product_identity_decisions import assess_product_relation
from precios_supermercados.product_identity_v2 import homologate_products_v2

GWALTNEY = "785331778506"
GTIN = "7501055300075"


def product(record_id: str, name: str, *, barcode: str = GTIN, brand: str | None = None, presentation: str | None = None) -> SourceProductRecord:
    supermarket = record_id.split(":", 1)[0]
    return SourceProductRecord(
        source_record_id=record_id,
        supermarket_id=supermarket,
        source_name=name,
        source_brand=brand,
        source_presentation=presentation,
        barcode=barcode,
    )


GWALTNEY_LA_COLONIA = product(
    "la_colonia:1", "Salchicha Gwaltney De Pollo Bun Size 454 Gr", barcode=GWALTNEY, brand="Gwaltney"
)
GWALTNEY_WALMART = product(
    "walmart:1", "Salchichas Tradicional Gwaltney", barcode=GWALTNEY, brand="Gwaltney", presentation="453.59237 g"
)
GWALTNEY_PAIZ = product(
    "paiz:1", "Salchichas Tradicional Gwaltney", barcode=GWALTNEY, brand="Gwaltney", presentation="453.59237 g"
)


def test_gwaltney_sps_pair_is_no_longer_grouped() -> None:
    result = homologate_products_v2((GWALTNEY_LA_COLONIA, GWALTNEY_WALMART))
    (group,) = result.exact_gtin_groups
    assert group.comparison_status == "review_required"
    assert group.conflict_reasons == ("one_sided_flavor_declared",)


def test_gwaltney_tgu_group_excludes_only_the_flavored_member() -> None:
    result = homologate_products_v2((GWALTNEY_LA_COLONIA, GWALTNEY_WALMART, GWALTNEY_PAIZ))
    (group,) = result.exact_gtin_groups
    assert group.comparison_status == "ready"
    assert group.supermarket_ids == ("paiz", "walmart")
    assert group.excluded_members == (("la_colonia:1", ("one_sided_flavor_declared",)),)


def test_gwaltney_pair_is_not_rule_verified_exact_trade_item() -> None:
    result = homologate_products_v2((GWALTNEY_LA_COLONIA, GWALTNEY_WALMART))
    left, right = result.profiles
    assessment = assess_product_relation(left, right)
    assert assessment.relation == "UNRESOLVED"
    assert assessment.reasons == ("one_sided_flavor_declared",)


@pytest.mark.parametrize(
    ("declared", "plain", "reason"),
    [
        ("Gaseosa Coca Cola Zero Lata 355 ml", "Gaseosa Coca Cola Lata 355 ml", "one_sided_variant_declared"),
        ("Gaseosa Coca Cola Sin Azúcar Lata 355 ml", "Gaseosa Coca Cola Lata 355 ml", "one_sided_variant_declared"),
        ("Cerveza Toña Light Lata 350 ml", "Cerveza Toña Lata 350 ml", "one_sided_variant_declared"),
        ("Yogurt Yoplait Fresa 145 g", "Yogurt Yoplait 145 g", "one_sided_flavor_declared"),
        ("Suavizante Downy Lavanda 800 ml", "Suavizante Downy 800 ml", "one_sided_flavor_declared"),
        # publicado SPS 2026-09-21 bajo un mismo GTIN: aromas distintos de vela.
        ("Vela Glade Mc3 Relaxing Lavender 192 Gr", "Vela Aromática Glade Sweet Citrus 192 Gr", "one_sided_flavor_declared"),
    ],
)
def test_one_sided_declared_variant_blocks_same_gtin_pair(declared: str, plain: str, reason: str) -> None:
    result = homologate_products_v2((product("walmart:1", declared), product("la_colonia:1", plain)))
    (group,) = result.exact_gtin_groups
    assert group.comparison_status == "review_required"
    assert reason in group.conflict_reasons


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("Gaseosa Coca Cola Original Lata 355 ml", "Gaseosa Coca Cola Lata 355 ml"),  # estándar
        ("Yogurt Yoplait Strawberry 145 g", "Yogurt Yoplait Fresa 145 g"),  # mismo sabor traducido
        ("Gaseosa Coca Cola Zero 355 ml", "Gaseosa Coca Cola Sin Azúcar 355 ml"),  # mismo grupo
        ("Leche Sula Entera 1 L", "Leche Sula 1 L"),  # descriptor de leche, no variante especial
        (  # publicado TGU 2026-09-21: "Zero Alcohol" no es formulación sin azúcar
            "Enjuague Bucal Colgate Plax Ice Glacial Zero Alcohol 500 ml",
            "Enjuague Bucal Colgate Plax Ice Glacial 500 ml",
        ),
    ],
)
def test_declared_on_both_sides_or_standard_formulation_stays_ready(left: str, right: str) -> None:
    result = homologate_products_v2((product("walmart:1", left), product("la_colonia:1", right)))
    (group,) = result.exact_gtin_groups
    assert group.comparison_status == "ready"
    assert group.conflict_reasons == ()


def test_one_sided_variant_without_gtin_remains_a_review_candidate() -> None:
    result = homologate_products_v2(
        (
            product("walmart:1", "Yogurt Yoplait Fresa 145 g", barcode=None, brand="Yoplait"),
            product("colonial:1", "Yogurt Yoplait 145 g", barcode=None, brand="Yoplait"),
        ),
        candidate_threshold=Decimal("0"),
    )
    assert len(result.candidates) == 1
    assert result.candidates[0].status == "review_required"
