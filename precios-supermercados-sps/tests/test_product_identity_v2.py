from __future__ import annotations

from decimal import Decimal

import pytest

from precios_supermercados.product_homologation import SourceProductRecord
from precios_supermercados.product_identity_v2 import (
    IDENTITY_NORMALIZATION_VERSION,
    audit_identity_quality,
    assign_taxonomy_v2,
    canonicalize_brand_key,
    canonical_presentation_fields,
    canonical_egg_size,
    candidate_presentations_compatible,
    homologate_products_v2,
    profile_product_v2,
    resolve_brand,
    resolve_presentation_v2,
    build_brand_lexicon,
    source_brand_role,
)


def product(
    record_id: str,
    supermarket: str,
    name: str,
    *,
    brand: str | None = None,
    presentation: str | None = None,
    barcode: str | None = None,
) -> SourceProductRecord:
    return SourceProductRecord(
        source_record_id=record_id,
        supermarket_id=supermarket,
        source_name=name,
        source_brand=brand,
        source_presentation=presentation,
        barcode=barcode,
    )


@pytest.mark.parametrize(
    "suffix",
    ["u", "Un", "UN", "ud", "uds", "und", "unds", "unidad", "unidades"],
)
def test_count_aliases_normalize_to_same_signature(suffix: str) -> None:
    signature, status = resolve_presentation_v2(
        product("a", "colonial", f"Huevos Bonovo Grande 30 {suffix}", brand="Bonovo")
    )
    assert status == "name_only"
    assert signature is not None
    assert signature.dimension == "count"
    assert signature.total_base == Decimal("30")
    assert signature.pack_count == 30


def test_mass_volume_and_imperial_aliases_are_normalized_for_comparison() -> None:
    mg, _ = resolve_presentation_v2(product("mg", "a", "Suplemento 500 mg"))
    gram, _ = resolve_presentation_v2(product("g", "b", "Suplemento 0.5 g"))
    libra, _ = resolve_presentation_v2(product("lb", "a", "Arroz 1 libra"))
    grams, _ = resolve_presentation_v2(product("gr", "b", "Arroz 453.59237 gramos"))
    liter, _ = resolve_presentation_v2(product("l", "a", "Leche 1 L"))
    ml, _ = resolve_presentation_v2(product("ml", "b", "Leche 1000 ml"))

    assert mg is not None and gram is not None and mg.total_base == gram.total_base == Decimal("0.5")
    assert libra is not None and grams is not None and libra.total_base == grams.total_base
    assert liter is not None and ml is not None and liter.total_base == ml.total_base == Decimal("1000")


def test_multipack_remains_structured_and_not_equal_to_single_unit() -> None:
    multi, _ = resolve_presentation_v2(
        product("a", "pricesmart", "Pepsi 6 x 355 ml", brand="Pepsi")
    )
    single, _ = resolve_presentation_v2(
        product("b", "walmart", "Pepsi 355 ml", brand="Pepsi")
    )
    assert multi is not None and single is not None
    assert multi.pack_count == 6
    assert multi.total_base == Decimal("2130")
    assert not candidate_presentations_compatible(multi, single)


def test_egg_grade_g_is_not_parsed_as_grams() -> None:
    record = product(
        "colonial:9884",
        "colonial",
        "Norteño Huevos 30G UND",
        brand="Norteño",
    )
    taxonomy = assign_taxonomy_v2(record)
    signature, status = resolve_presentation_v2(record, taxonomy)
    assert taxonomy.product_type == "Huevo"
    assert canonical_egg_size(record, taxonomy) == "Grande"
    assert signature is not None and signature.dimension == "count"
    assert signature.total_base == Decimal("30")
    assert status == "name_only"


def test_real_grams_are_not_reinterpreted_as_egg_grade() -> None:
    record = product("kinder", "colonial", "KINDER Huevo Sorpresa Niño 20 g")
    taxonomy = assign_taxonomy_v2(record)
    signature, _ = resolve_presentation_v2(record, taxonomy)
    assert taxonomy.product_type == "Chocolate"
    assert canonical_egg_size(record, taxonomy) is None
    assert signature is not None and signature.dimension == "mass_g"
    assert signature.total_base == Decimal("20")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Bonovo Huevo G 30 und", "Grande"),
        ("Huevos Bonovo Cartón M 30Un", "Mediano"),
        ("Huevos Bonovo Cartón P 15Un", "Pequeño"),
        ("Bonovo Huevo XL 10 uni", "Extra grande"),
        ("Bonovo Huevo Extra Grande 10 unidades", "Extra grande"),
    ],
)
def test_egg_sizes_have_canonical_semantics(name: str, expected: str) -> None:
    record = product("egg", "colonial", name, brand="Bonovo")
    assert canonical_egg_size(record, assign_taxonomy_v2(record)) == expected


def test_huevo_token_does_not_pollute_unrelated_product_types() -> None:
    assert assign_taxonomy_v2(product("a", "x", "LONG LIFE tallarin de huevo 400grs.")).product_type == "Pasta"
    assert assign_taxonomy_v2(product("b", "x", "Kraft Mayonesa de Huevos de Gallina Libres 887 ml")).product_type == "Mayonesa"
    assert assign_taxonomy_v2(product("c", "x", "KINDER Huevo Sorpresa Niño 20g")).product_type == "Chocolate"
    assert assign_taxonomy_v2(product("d", "x", "RES Huevos de Toro X Libra")).product_type is None


def test_generic_source_brand_can_be_recovered_only_from_known_brand_in_name() -> None:
    records = (
        product("trusted", "walmart", "Huevos Bonovo 30 unidades", brand="Bonovo"),
        product("generic", "colonial", "Bonovo Huevo G 30 und", brand="RMS"),
        product("unknown", "andes", "Huevos especiales 30 und", brand="Marca COMANDES"),
    )
    lexicon = build_brand_lexicon(records)
    generic = resolve_brand(records[1], brand_lexicon=lexicon)
    unknown = resolve_brand(records[2], brand_lexicon=lexicon)
    assert generic.canonical_brand == "bonovo"
    assert generic.source == "name_known_brand"
    assert unknown.canonical_brand is None
    assert unknown.source == "missing"


def test_nutri_yema_554g_vs_1_2lb_becomes_review_candidate_without_auto_merge() -> None:
    records = (
        product(
            "colonial:10593",
            "colonial",
            "Nutri Yema Claras de Huevo 554gr",
            brand="Nutri Yema",
            presentation=None,
        ),
        product(
            "comisariato_los_andes:46232",
            "comisariato_los_andes",
            "Claras de huevo liquidas doy pack 1.2 lb",
            brand="Marca COMANDES",
            presentation="UN",
        ),
    )
    result = homologate_products_v2(records)
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.status == "review_required"
    assert candidate.left_supermarket_id != candidate.right_supermarket_id
    profiles = {profile.record.source_record_id: profile for profile in result.profiles}
    left = profiles["colonial:10593"]
    right = profiles["comisariato_los_andes:46232"]
    assert left.presentation is not None and left.presentation.total_base == Decimal("554")
    assert right.presentation is not None
    assert abs(right.presentation.total_base - Decimal("544.310844")) < Decimal("0.000001")
    assert candidate_presentations_compatible(left.presentation, right.presentation)
    assert all(profile.canonical_product_id is None for profile in result.profiles)


def test_nutri_yema_equivalence_is_strong_only_when_brand_is_confirmed() -> None:
    records = (
        product("a", "colonial", "Nutri Yema Claras de Huevo 554gr", brand="Nutri Yema"),
        product("b", "los_andes", "Nutri Yema Claras de Huevo Líquidas 1.2 lb", brand="Nutri Yema"),
    )
    result = homologate_products_v2(records)
    assert len(result.candidates) == 1
    profiles = {profile.record.source_record_id: profile for profile in result.profiles}
    from precios_supermercados.product_identity_v2 import explain_candidate

    evidence = explain_candidate(profiles["a"], profiles["b"])
    assert evidence.confidence_level == "STRONG"
    assert evidence.decision_state == "review_required"


def test_candidate_tolerance_does_not_make_500g_equal_554g() -> None:
    five_hundred, _ = resolve_presentation_v2(product("a", "x", "Claras de huevo 500 g"))
    five_fifty_four, _ = resolve_presentation_v2(product("b", "y", "Claras de huevo 554 g"))
    assert five_hundred is not None and five_fifty_four is not None
    assert not candidate_presentations_compatible(five_hundred, five_fifty_four)


def test_different_flavor_or_variant_remains_separate() -> None:
    flavor = homologate_products_v2(
        (
            product("a", "a", "Leche Marca Vainilla 1 L", brand="Marca"),
            product("b", "b", "Leche Marca Chocolate 1 L", brand="Marca"),
        ),
        candidate_threshold=Decimal("0"),
    )
    variant = homologate_products_v2(
        (
            product("c", "a", "Refresco Cola Original 1 L", brand="Cola"),
            product("d", "b", "Refresco Cola Zero 1 L", brand="Cola"),
        ),
        candidate_threshold=Decimal("0"),
    )
    assert flavor.candidates == ()
    assert variant.candidates == ()


def test_different_valid_gtins_remain_hard_conflict() -> None:
    result = homologate_products_v2(
        (
            product("a", "a", "Arroz Progreso 1 lb", brand="Progreso", barcode="7590002040003"),
            product("b", "b", "Arroz Progreso 454 g", brand="Progreso", barcode="7501031311309"),
        ),
        candidate_threshold=Decimal("0"),
    )
    assert result.candidates == ()
    assert result.exact_gtin_groups == ()


def test_candidate_graph_does_not_create_transitive_canonical_identity() -> None:
    result = homologate_products_v2(
        (
            product("a", "a", "Leche Demo Entera 1000 ml", brand="Demo"),
            product("b", "b", "Demo Leche Entera 1 L", brand="Demo"),
            product("c", "c", "Leche Demo Entera 1000 ml", brand="Demo"),
        ),
        candidate_threshold=Decimal("0"),
    )
    assert len(result.candidates) == 3
    assert all(profile.canonical_product_id is None for profile in result.profiles)
    assert result.exact_gtin_groups == ()


def test_identity_v2_is_versioned_without_changing_persisted_v1() -> None:
    assert IDENTITY_NORMALIZATION_VERSION == "product-homologation-v2.7"


def test_canonical_presentation_fields_preserve_raw_and_separate_pack() -> None:
    record = product(
        "a",
        "pricesmart",
        "Jabón Demo 6 x 100 g",
        presentation="6 x 100 Gr",
    )
    fields = canonical_presentation_fields(record)
    assert fields.raw_presentation == "6 x 100 Gr"
    assert fields.normalized_quantity == Decimal("100")
    assert fields.normalized_unit == "g"
    assert fields.normalized_pack_count == 6
    assert fields.canonical_total == Decimal("600")
    assert fields.display_presentation == "6 × 100 g"


def test_count_fields_follow_quantity_unit_contract() -> None:
    fields = canonical_presentation_fields(product("a", "x", "Huevos Bonovo 30Un"))
    assert fields.normalized_quantity == Decimal("30")
    assert fields.normalized_unit == "unit"
    assert fields.normalized_pack_count == 1
    assert fields.display_presentation == "30 unidades"


def test_conflicting_real_brand_does_not_become_canonical_silently() -> None:
    record = product("a", "x", "Nutri Yema Claras 554 g", brand="Otra Marca")
    resolution = resolve_brand(
        record,
        brand_lexicon=frozenset({"nutri yema", "otra marca"}),
    )
    assert resolution.canonical_brand is None
    assert resolution.source == "source_conflict"
    assert resolution.conflict is True


def test_profile_v2_preserves_raw_evidence() -> None:
    record = product(
        "a",
        "colonial",
        "Bonovo Huevo G 30 und",
        brand="RMS",
        presentation="UN",
    )
    profile = profile_product_v2(record, brand_lexicon=frozenset({"bonovo"}))
    assert profile.record.source_brand == "RMS"
    assert profile.record.source_presentation == "UN"
    assert profile.normalized_brand == "bonovo"
    assert profile.presentation is not None and profile.presentation.total_base == Decimal("30")


@pytest.mark.parametrize(
    ("observed", "expected"),
    [
        ("Buchanan's", "buchanan"),
        ("Mott’s", "mott"),
        ("Wrigleys", "wrigley"),
        ("Elmigo", "el migo"),
    ],
)
def test_brand_aliases_require_observed_catalog_evidence(observed: str, expected: str) -> None:
    assert canonicalize_brand_key(observed) == expected


@pytest.mark.parametrize("placeholder", ["RMS", "Marca COMANDES", "COMANDES"])
def test_retailer_placeholders_are_not_promoted_to_commercial_brands(placeholder: str) -> None:
    assert canonicalize_brand_key(placeholder) is None
    assert source_brand_role(placeholder) == "retailer_placeholder"


def test_exact_gtin_brand_label_disagreement_is_measured_but_not_fragmented() -> None:
    result = homologate_products_v2(
        (
            product(
                "a",
                "colonial",
                "Purina Dentalife Snack Dental 198 g",
                brand="Purina",
                barcode="012656001065",
            ),
            product(
                "b",
                "walmart",
                "Purina Dentalife Snack Dental 198 g",
                brand="Purina Dentalife",
                barcode="012656001065",
            ),
        )
    )
    assert len(result.exact_gtin_groups) == 1
    assert result.exact_gtin_groups[0].comparison_status == "ready"
    assert result.exact_gtin_groups[0].conflict_reasons == ()
    assert audit_identity_quality(result)["exact_gtin_brand_label_disagreement_groups"] == 1


def test_exact_gtin_taxonomy_gap_is_measured_but_not_fragmented() -> None:
    # Barcode explícito (la_colonia): un GTIN derivado de SKU Colonial exigiría
    # además acuerdo mínimo de nombre (ver test_colonial_sku_gtin_identity.py).
    result = homologate_products_v2(
        (
            product(
                "a",
                "la_colonia",
                "Arroz Demo 1 lb",
                brand="Demo",
                barcode="012656001065",
            ),
            product(
                "b",
                "walmart",
                "Demo Especial 1 lb",
                brand="Demo",
                barcode="012656001065",
            ),
        )
    )
    assert len(result.exact_gtin_groups) == 1
    assert result.exact_gtin_groups[0].comparison_status == "ready"
    assert audit_identity_quality(result)["exact_gtin_taxonomy_disagreement_groups"] == 1


def test_exact_gtin_with_two_conflicting_known_types_stays_in_review() -> None:
    result = homologate_products_v2(
        (
            product(
                "a",
                "colonial",
                "Arroz Demo 1 lb",
                brand="Demo",
                barcode="012656001065",
            ),
            product(
                "b",
                "walmart",
                "Jugo Demo 1 lb",
                brand="Demo",
                barcode="012656001065",
            ),
        )
    )
    assert len(result.exact_gtin_groups) == 1
    assert result.exact_gtin_groups[0].comparison_status == "review_required"
    assert "product_type_conflict" in result.exact_gtin_groups[0].conflict_reasons


@pytest.mark.parametrize(
    ("left_name", "right_name"),
    [
        ("Jugo Sula Pera 1 L", "Jugo Sula Manzana 1 L"),
        ("Jugo Sula Naranja Mandarina 1 L", "Jugo Sula Naranja Zanahoria 1 L"),
        ("Jugo Zumo Limón 1 L", "Jugo Zumo Limón Rosa 1 L"),
        ("Yogur Sula Banano Fresa 1 L", "Yogur Sula Fresa 1 L"),
        ("Yogurt Yoplait Moras 145 g", "Yogurt Yoplait Fresas 145 g"),
        ("Baby Nutrine Pañal Talla L/G 30 unidades", "Baby Nutrine Pañal Talla M 30 unidades"),
        ("Plenitud Pañal Adulto XG 8 unidades", "Plenitud Pañal Adulto G/XG 8 unidades"),
        ("Nutrisse Tinte para Cabello Tono 6.60 50 ml", "Nutrisse Tinte para Cabello Tono 7.1 50 ml"),
    ],
)
def test_material_flavor_size_and_shade_differences_block_candidates(
    left_name: str,
    right_name: str,
) -> None:
    result = homologate_products_v2(
        (
            product("a", "colonial", left_name, brand="Sula" if "Sula" in left_name else "Demo"),
            product("b", "walmart", right_name, brand="Sula" if "Sula" in right_name else "Demo"),
        ),
        candidate_threshold=Decimal("0"),
    )
    assert result.candidates == ()


def test_flavor_translation_is_compatible_without_confirming_identity() -> None:
    result = homologate_products_v2(
        (
            product("a", "colonial", "Jugo Sula Fresa 1 L", brand="Sula"),
            product("b", "walmart", "Jugo Sula Strawberry 1 L", brand="Sula"),
        ),
        candidate_threshold=Decimal("0"),
    )
    assert len(result.candidates) == 1
    assert result.candidates[0].status == "review_required"
    assert all(profile.canonical_product_id is None for profile in result.profiles)


@pytest.mark.parametrize(
    ("name", "expected_total"),
    [
        ("Mantequilla 1/2 lb", Decimal("226.796185")),
        ("Mantequilla 1/4 lb", Decimal("113.3980925")),
        ("Mantequilla 3/4 lb", Decimal("340.1942775")),
    ],
)
def test_common_unit_fractions_are_parsed_as_fractions(
    name: str,
    expected_total: Decimal,
) -> None:
    signature, status = resolve_presentation_v2(product("a", "x", name))
    assert status == "name_only"
    assert signature is not None
    assert signature.dimension == "mass_g"
    assert signature.total_base == expected_total


def test_compact_slash_pack_notation_keeps_pack_structure() -> None:
    signature, _ = resolve_presentation_v2(product("a", "x", "Sazonador 4/8gr"))
    assert signature is not None
    assert signature.pack_count == 4
    assert signature.unit_amount_base == Decimal("8")
    assert signature.total_base == Decimal("32")


def test_natural_language_multipack_keeps_pack_structure() -> None:
    multi, _ = resolve_presentation_v2(
        product("a", "x", "Cerveza Imperial por 6 unid de 12oz")
    )
    single, _ = resolve_presentation_v2(product("b", "y", "Cerveza Imperial 12oz"))
    assert multi is not None and single is not None
    # Regla 21 (v2.7): la cerveza es subcategoría líquida; 12 oz = 354.88 ml.
    assert multi.dimension == "volume_ml"
    assert multi.pack_count == 6
    assert multi.declared_ounces == Decimal("12")
    assert round(multi.total_base, 1) == Decimal("2129.3")
    assert not candidate_presentations_compatible(multi, single)


def test_compact_count_alias_multipack_keeps_pack_structure() -> None:
    multi, _ = resolve_presentation_v2(
        product("a", "x", "Pack Néctar variado 8Unx200Ml")
    )
    assert multi is not None
    assert multi.dimension == "volume_ml"
    assert multi.pack_count == 8
    assert multi.unit_amount_base == Decimal("200")
    assert multi.total_base == Decimal("1600")


@pytest.mark.parametrize(
    ("name", "expected_type"),
    [
        ("El Panal Miel de Abeja 1000grs", "Miel"),
        ("Ross Abrillantador Café Calzado 9ml", "Abrillantador de calzado"),
        ("Yogur Sula Fresa 125g", "Yogurt"),
        ("Tallarin Roma 400 g", "Pasta"),
    ],
)
def test_head_nouns_outrank_brand_or_color_tokens(name: str, expected_type: str) -> None:
    assert assign_taxonomy_v2(product("a", "x", name)).product_type == expected_type


@pytest.mark.parametrize(
    ("left_name", "right_name", "brand"),
    [
        ("Aceite Elmigo Canola 1 L", "Aceite Elmigo Girasol 1 L", "Elmigo"),
        ("Pasta Ina Tornillo 200 g", "Pasta Ina Penne 200 g", "Ina"),
        ("Frijoles Goya Negros 400 g", "Frijoles Goya Rojos 400 g", "Goya"),
        ("Salsa Pace Medium 453 g", "Salsa Pace Hot 453 g", "Pace"),
        ("Salsa Don Julio Habanero 165 ml", "Salsa Don Julio Chile Cabro 165 ml", "Don Julio"),
        ("Vino Barefoot Blanco Moscato 750 ml", "Vino Barefoot Red Moscato 750 ml", "Barefoot"),
        ("Cerveza Coors Light Botella 354 ml", "Cerveza Coors Light Lata 354 ml", "Coors"),
        ("Cerveza Tucher Weizen Clara 500 ml", "Cerveza Tucher Weizen Dunkel 500 ml", "Tucher"),
        ("Jugo Sula Naranja Con Pulpa 473 ml", "Jugo Sula Naranja Sin Pulpa 473 ml", "Sula"),
        ("Galleta Gamesa Florentina Fresa 83 g", "Galleta Gamesa Florentina Cajeta 83 g", "Gamesa"),
        ("Sopa Elmigo Pollo 64 g", "Sopa Elmigo Camarón 64 g", "Elmigo"),
        ("Sopa Issima Camarón 64 g", "Sopa Issima Camarón Vegetales 64 g", "Issima"),
        ("Pañal Plenitud Protect G/XG 20 unidades", "Pañal Plenitud Classic G/XG 20 unidades", "Plenitud"),
    ],
)
def test_observed_commercial_attributes_block_false_candidates(
    left_name: str,
    right_name: str,
    brand: str,
) -> None:
    result = homologate_products_v2(
        (
            product("a", "colonial", left_name, brand=brand),
            product("b", "walmart", right_name, brand=brand),
        ),
        candidate_threshold=Decimal("0"),
    )
    assert result.candidates == ()


def test_pasta_shape_aliases_remain_compatible_review_candidates() -> None:
    result = homologate_products_v2(
        (
            product("a", "colonial", "Pasta Ina Pluma 200 g", brand="Ina"),
            product("b", "walmart", "Pasta Ina Penne 200 g", brand="Ina"),
        ),
        candidate_threshold=Decimal("0"),
    )
    assert len(result.candidates) == 1
    assert result.candidates[0].status == "review_required"


def test_different_declared_cheese_kinds_block_candidates() -> None:
    result = homologate_products_v2(
        (
            product("a", "colonial", "Queso Leyde Mozzarella 227 g", brand="Leyde"),
            product("b", "walmart", "Queso Leyde Parmesano 227 g", brand="Leyde"),
        ),
        candidate_threshold=Decimal("0"),
    )
    assert result.candidates == ()


@pytest.mark.parametrize(
    ("left_name", "right_name", "brand"),
    [
        ("Té Lipton Frío 1 L", "Té Lipton Frío Limón 1 L", "Lipton"),
        ("Huevos Rica Yema 30 unidades", "Huevos Rica Yema Medianos 30 unidades", "Rica Yema"),
        ("Cerveza Toña Lata 350 ml", "Cerveza Toña Light Lata 350 ml", "Toña"),
        ("Pasta Ina 200 g", "Pasta Ina Tornillo 200 g", "Ina"),
        ("Queso Leyde Procesado 227 g", "Queso Leyde Procesado Suizo 227 g", "Leyde"),
        ("Aceite Mazola Oliva 750 ml", "Aceite Mazola Blend Oliva 750 ml", "Mazola"),
        ("Atún Bumble Bee En Aceite 142 g", "Atún Bumble Bee En Aceite Ahumado 142 g", "Bumble Bee"),
        ("Sopa Issima Pollo 64 g", "Sopa Issima Pollo Con Chile 64 g", "Issima"),
        ("Sardina La Sirena Salsa Tomate 425 g", "Sardina La Sirena Salsa Tomate Picante 425 g", "La Sirena"),
    ],
)
def test_missing_salient_attribute_cannot_receive_strong_confidence(
    left_name: str,
    right_name: str,
    brand: str,
) -> None:
    result = homologate_products_v2(
        (
            product("a", "colonial", left_name, brand=brand),
            product("b", "walmart", right_name, brand=brand),
        ),
        candidate_threshold=Decimal("0"),
    )
    assert len(result.candidates) == 1
    profiles = {profile.record.source_record_id: profile for profile in result.profiles}
    from precios_supermercados.product_identity_v2 import explain_candidate

    assert explain_candidate(profiles["a"], profiles["b"]).confidence_level != "STRONG"
