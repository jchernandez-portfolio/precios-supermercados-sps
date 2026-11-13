"""Motor de identidad v2.9: g ≈ ml en lácteos/jugos/salsas y peso sobre conteo (PR B, "A5 + B media")."""
from __future__ import annotations

import pytest

from precios_supermercados.consumer_catalog_display import unit_price
from precios_supermercados.product_homologation_persistence import build_homologation_rows, records_from_product_rows
from precios_supermercados.product_identity_v2 import (
    IDENTITY_NORMALIZATION_VERSION,
    build_brand_lexicon,
    mentions_count,
    profile_product_v2,
    resolve_gtin_group,
    size_rule_kind,
)
from precios_supermercados.size_decisions import decision_from_row, index_decisions

NOW = "2026-11-13T00:00:00Z"


def gtin(base12: str) -> str:
    digits = [int(char) for char in base12]
    total = sum(digit * (3 if index % 2 else 1) for index, digit in enumerate(digits))
    return base12 + str((10 - total % 10) % 10)


G = {name: gtin(f"7429900000{index:02d}") for index, name in enumerate((
    "coco", "salsa", "shampoo", "conteo_1", "te", "caprisun", "sula", "empeora", "decidido_distinto",
    "decidido_tamano", "jugo_6"), start=1)}
C = {name: "0" + code for name, code in G.items()}
ROWS = [
    # A: leche de coco 400 ml contra 382.72 g (4.3 %), categoría con "leche" → comparable, estándar g.
    (1, "walmart", "Leche de Coco Goya - 400 ml", "Goya", None, "Leche de coco", G["coco"]),
    (2, "paiz", "Leche de Coco Goya - 382.72 g", "Goya", "382.72 g", "Leche de coco", G["coco"]),
    # A: salsa 150 g contra 150 ml.
    (3, "walmart", "Salsa Naturas Tomate - 150 g", "Naturas", None, "Salsas", G["salsa"]),
    (4, "colonial", "NATURAS Salsa Tomate 150 ml", "Naturas", None, "Salsas", G["salsa"]),
    # A fuera de categoría: shampoo 400 ml contra 400 g → sigue bloqueado.
    (5, "walmart", "Shampoo Sedal Rizos - 400 ml", "Sedal", None, "Cuidado personal", G["shampoo"]),
    (6, "paiz", "Shampoo Sedal Rizos - 400 g", "Sedal", "400 g", "Cuidado capilar", G["shampoo"]),
    # B: conteo 1 contra 500 g.
    (7, "colonial", "Queso Crema Philadelphia 1 Unidad", "Philadelphia", None, "Quesos", G["conteo_1"]),
    (8, "walmart", "Queso Crema Philadelphia - 500 g", "Philadelphia", None, "Quesos", G["conteo_1"]),
    # B: el título con peso menciona el conteo ("50 bolsas") → comparable; 50 × 1 g.
    (9, "paiz", "Te Lipton Negro 50 bolsas - 50 g", "Lipton", "50 g", "Té", G["te"]),
    (10, "walmart", "Te Lipton Negro 50 Unidades", "Lipton", None, "Té", G["te"]),
    # B riesgoso: paquete contra unidad suelta → NO se une.
    (11, "walmart", "Jugo Capri Sun Pacific Cooler 10 Unidades", "Capri Sun", None, "Jugos", G["caprisun"]),
    (12, "la_colonia", "Jugo Capri Sun Pacific Cooler 177 Ml", "Capri Sun", "177 Ml", "Jugos", G["caprisun"]),
    (13, "walmart", "Leche Sula Maleteada 6 Unidades", "Sula", None, "Lácteos", G["sula"]),
    (14, "paiz", "Leche Saborizada Sula Maleteada - 200 ml", "Sula", "200 ml", "Leche", G["sula"]),
    # Nunca empeorar: 140 ml ≈ 145 g pero no 150 g → se conserva 150 g ×2 (Walmart y Paiz).
    (15, "walmart", "Salsa Del Monte Tomate - 150 g", "Del Monte", None, "Salsas", G["empeora"]),
    (16, "paiz", "Salsa Del Monte Tomate - 150 g", "Del Monte", "150 g", "Salsas", G["empeora"]),
    (17, "la_colonia", "Salsa Del Monte Tomate 145 Gr", "Del Monte", "145 Gr", "Salsas", G["empeora"]),
    (18, "colonial", "DEL MONTE Salsa Tomate 140 ml", "Del Monte", None, "Salsas", G["empeora"]),
    # Decisiones: mandan sobre las reglas.
    (19, "walmart", "Yogurt Yes Pina - 1000 g", "Yes", None, "Lácteos", G["decidido_distinto"]),
    (20, "la_colonia", "Yogurt Yes Pina 1000 Ml", "Yes", "1000 Ml", "Yogurt", G["decidido_distinto"]),
    (21, "colonial", "Yogurt Yes Mora 1 Unidad", "Yes", None, "Yogurt", G["decidido_tamano"]),
    (22, "walmart", "Yogurt Yes Mora - 150 g", "Yes", None, "Lácteos", G["decidido_tamano"]),
    (23, "paiz", "Yogurt Yes Mora - 150 g", "Yes", "150 g", "Lácteos", G["decidido_tamano"]),
    # B mismo pack: "6 Unidades" contra 6 × 200 ml.
    (24, "walmart", "Jugo Del Valle Durazno 6 Unidades", "Del Valle", None, "Jugos", G["jugo_6"]),
    (25, "paiz", "Jugo Del Valle Durazno 6 x 200 ml", "Del Valle", None, "Jugos", G["jugo_6"]),
]
DECISIONS = [
    (C["decidido_distinto"], "different_products", None, None, None, None, None, "human:dueno", NOW),
    (C["decidido_tamano"], "size", "150", "g", "150", "g", None, "human:dueno", NOW),
]


@pytest.fixture(scope="module")
def rows() -> dict[int, object]:
    decided = index_decisions(decision_from_row(row) for row in DECISIONS)
    built = build_homologation_rows(records_from_product_rows(ROWS), updated_at_utc=NOW, size_decisions=decided)
    return {row.product_id: row for row in built}


def members(name: str):
    records = [record for _, record in records_from_product_rows(ROWS)]
    lexicon = build_brand_lexicon(records)
    profiles = [profile_product_v2(record, brand_lexicon=lexicon) for record in records]
    return [profile for profile in profiles if profile.canonical_gtin == C[name]]


def chains(resolution) -> set[str]:
    return {member.record.supermarket_id for member in resolution[0][0]}


def test_version_is_v2_9(rows) -> None:
    assert IDENTITY_NORMALIZATION_VERSION == "product-homologation-v2.9"
    assert {row.normalization_version for row in rows.values()} == {"product-homologation-v2.9"}


def test_g_ml_in_dairy_category_joins_and_standardizes_to_grams(rows) -> None:
    assert rows[1].comparison_status == rows[2].comparison_status == "ready"
    assert (rows[1].presentation_dimension, rows[1].presentation_total_base) == ("mass_g", "400")
    assert rows[1].display_presentation == "400 g" and rows[1].raw_presentation is None
    assert (rows[2].presentation_dimension, rows[2].presentation_total_base) == ("mass_g", "382.72")
    assert unit_price(price="40.00", presentation_dimension=rows[1].presentation_dimension,
                      presentation_total_base=rows[1].presentation_total_base,
                      presentation_status=rows[1].presentation_status, reference=(100, "100 g")) == {
        "amount": "10.00", "per": "100 g"}


def test_g_ml_in_sauce_category(rows) -> None:
    assert rows[3].comparison_status == rows[4].comparison_status == "ready"
    assert rows[4].presentation_dimension == "mass_g"


def test_g_ml_does_not_apply_outside_the_categories(rows) -> None:
    assert rows[5].comparison_status == rows[6].comparison_status == "review_required"
    assert "cross_source_presentation_conflict" in rows[5].conflict_reasons_json
    assert rows[5].presentation_dimension == "volume_ml"


def test_count_one_takes_the_group_weight(rows) -> None:
    assert rows[7].comparison_status == rows[8].comparison_status == "ready"
    assert (rows[7].presentation_dimension, rows[7].presentation_total_base, rows[7].presentation_pack_count) == (
        "mass_g", "500", 1)


def test_title_mentioning_the_count_joins_and_count_goes_to_pack(rows) -> None:
    assert rows[9].comparison_status == rows[10].comparison_status == "ready"
    assert (rows[10].presentation_dimension, rows[10].presentation_total_base, rows[10].presentation_pack_count) == (
        "mass_g", "50", 50)
    assert rows[10].display_presentation == "50 × 1 g"


def test_same_pack_count_joins(rows) -> None:
    assert rows[24].comparison_status == rows[25].comparison_status == "ready"
    assert (rows[24].presentation_dimension, rows[24].presentation_total_base, rows[24].presentation_pack_count) == (
        "volume_ml", "1200", 6)


@pytest.mark.parametrize(("left", "right"), [(11, 12), (13, 14)])
def test_pack_without_size_against_a_single_unit_stays_blocked(rows, left: int, right: int) -> None:
    # Caprisun "10 Unidades" contra 177 ml; Sula "6 Unidades" contra 200 ml: paquete contra unidad suelta.
    assert rows[left].comparison_status == rows[right].comparison_status == "review_required"
    assert rows[left].presentation_dimension == "count"


def test_never_worse_keeps_the_v2_8_resolution(rows) -> None:
    assert rows[15].comparison_status == rows[16].comparison_status == "ready"
    assert rows[17].comparison_status == rows[18].comparison_status == "review_required"
    group = members("empeora")
    before = resolve_gtin_group(C["empeora"], group, size_rules=False)
    after = resolve_gtin_group(C["empeora"], group)
    assert chains(before) == chains(after) == {"walmart", "paiz"}


def test_rules_only_add_chains_compared_with_v2_8() -> None:
    for name in ("coco", "salsa", "conteo_1", "te", "jugo_6", "shampoo", "caprisun", "sula", "empeora"):
        group = members(name)
        before = resolve_gtin_group(C[name], group, size_rules=False)[0][0]
        after = resolve_gtin_group(C[name], group)[0][0]
        before_ids = {m.record.source_record_id for m in before}
        after_ids = {m.record.source_record_id for m in after}
        if len({m.record.supermarket_id for m in before}) >= 2:
            assert before_ids <= after_ids, name


def test_decisions_rule_over_the_size_rules(rows) -> None:
    # different_products: g ≈ ml no une un código que una persona separó.
    assert rows[19].comparison_status == rows[20].comparison_status == "review_required"
    assert "human_different_products" in rows[20].conflict_reasons_json
    # Tamaño oficial 150 g: el conteo 1 (que la regla B aceptaría) no coincide y queda aislado.
    assert rows[22].comparison_status == rows[23].comparison_status == "ready"
    assert rows[21].comparison_status == "review_required"
    assert "human_size_decision_mismatch" in rows[21].conflict_reasons_json
    assert rows[21].presentation_dimension == "count"


def test_size_rule_kind_and_count_mentions() -> None:
    coco = members("coco")
    assert size_rule_kind(coco[0], coco[1]) == "g_ml"
    shampoo = members("shampoo")
    assert size_rule_kind(shampoo[0], shampoo[1]) is None
    caprisun = members("caprisun")
    assert size_rule_kind(caprisun[0], caprisun[1]) is None
    te = members("te")
    assert size_rule_kind(te[0], te[1]) == "titulo_menciona_conteo"
    assert mentions_count("Te Lipton Negro 50 bolsas - 50 g", 50)
    assert not mentions_count("Jugo Capri Sun Pacific Cooler 177 Ml", 10)
