"""Calidad de catálogo 2026-10-01: marca desde el nombre con léxico de marcas fuente."""
from __future__ import annotations

from precios_supermercados.product_homologation import SourceProductRecord
from precios_supermercados.product_homologation_persistence import build_homologation_rows
from precios_supermercados.product_identity_v2 import (
    BrandLexicon,
    _hard_conflicts,
    build_brand_lexicon,
    canonicalize_brand_key,
    homologate_products_v2,
    profile_product_v2,
    resolve_brand,
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


REFERENCE = (
    product("w:1", "walmart", "Cereal Kellogg's Corn Flakes Original - 500 g", brand="Kellogg's"),
    product("p:1", "paiz", "Cereal Corn Flakes Kelloggs - 300 g", brand="Kelloggs"),
    product("w:2", "walmart", "Cepillo Dental Oral-B Indicator Cerdas Medias - 2 Uds", brand="Oral-B"),
    product("w:3", "walmart", "Cloro Magia Blanca Galón Regular - 3.785 L", brand="Magia Blanca"),
    product("lc:1", "la_colonia", "Cereal Nesquik Nestle 330 Gr", brand="Nesquik"),
    product("lc:2", "la_colonia", "Fórmula Infantil Nestle Nan 1 800 Gr", brand="Nestle"),
    product("lc:3", "la_colonia", "Colado Gerber Manzana 113 Gr", brand="Gerber"),
    product("p:2", "paiz", "Jugo Del Valle Apple 1 L", brand="Apple"),
    product("w:4", "walmart", "Galleta Original Pozuelo - 400 g", brand="Original"),
    product("w:5", "walmart", "Suavizante Suavitel Primavera - 850 ml", brand="Suavitel"),
    product("w:6", "walmart", "Suavizante Downy Libre Enjuague - 800 ml", brand="Downy"),
    product("p:3", "paiz", "Atún Sardimar Trozos En Agua - 140 g", brand="Sardimar"),
    product("p:4", "paiz", "Atún Calvo Lomitos En Aceite - 140 g", brand="Calvo"),
    product("w:7", "walmart", "Shampoo Johnson's Baby Original - 200 ml", brand="Johnson's Baby"),
)


def lexicon() -> BrandLexicon:
    return build_brand_lexicon(REFERENCE)


def test_possessive_and_compact_spellings_share_one_brand_key() -> None:
    assert canonicalize_brand_key("Kellogg's") == canonicalize_brand_key("Kelloggs") == "kelloggs"
    assert canonicalize_brand_key("Hellmann's") == "hellmanns"
    assert canonicalize_brand_key("L'Oréal") == "l oreal"
    assert canonicalize_brand_key("Buchanan's") == "buchanan"


def test_brand_is_extracted_from_name_when_source_sends_none() -> None:
    lex = lexicon()
    cases = {
        "KELLOGGS Corn Flakes Orginal 1.22kg": "kelloggs",
        "ORALB Cepillo Ultra Fino 2x1 S35": "oral b",
        "MagiaBlanca Desinf ManzanaVer 900ml": "magia blanca",
        "Gerber apple pear peach 3.5 oz": "gerber",
        "NESTLE Nesquik Cereal 330g": "nestle",
    }
    for name, expected in cases.items():
        resolution = resolve_brand(product("c:1", "colonial", name), brand_lexicon=lex)
        assert (resolution.canonical_brand, resolution.source) == (expected, "name_known_brand"), name


def test_common_words_never_become_a_brand_from_the_name() -> None:
    lex = lexicon()
    for name in ("Original Galleta de Avena 200 g", "Jugo de Apple y Pera 1 L"):
        assert resolve_brand(product("c:1", "comisariato_los_andes", name), brand_lexicon=lex).canonical_brand is None


def test_two_brands_without_a_leading_one_stay_missing() -> None:
    resolution = resolve_brand(
        product("c:1", "comisariato_los_andes", "Atun en agua tipo Sardimar o Calvo 140 gr"),
        brand_lexicon=lexicon(),
    )
    assert (resolution.canonical_brand, resolution.source) == (None, "missing")


def test_generic_word_brand_counts_only_as_first_word() -> None:
    lex = BrandLexicon({"ideal", "clover brand"}, generic={"ideal"})
    first = resolve_brand(product("a:1", "comisariato_los_andes", "Ideal aceite sin colesterol 3.750 ltrs"), brand_lexicon=lex)
    assert first.canonical_brand == "ideal"
    later = resolve_brand(product("a:2", "comisariato_los_andes", "Aceite para freir ideal 750 ml"), brand_lexicon=lex)
    assert later.canonical_brand is None
    specific = resolve_brand(product("a:3", "walmart", "Aceite Clover Brand ideal para freir 750 ml"), brand_lexicon=lex)
    assert specific.canonical_brand == "clover brand"


def test_data_driven_generic_brand_words() -> None:
    # "Fiesta" es marca de una cadena pero aparece en 10+ nombres de otras marcas.
    records = [product("f:1", "walmart", "Platos Fiesta Desechables 10 Uds", brand="Fiesta")]
    records += [
        product(f"x:{index}", "walmart", f"Tomate Cherry Fiesta La Carreta Bandeja {index}", brand="La Carreta")
        for index in range(12)
    ]
    lex = build_brand_lexicon(records)
    assert "fiesta" in lex.generic
    resolution = resolve_brand(product("c:1", "colonial", "Pastel Fiesta Mediano Vainilla"), brand_lexicon=lex)
    assert resolution.canonical_brand is None


def test_source_brand_is_kept_when_name_cites_the_same_family_or_is_ambiguous() -> None:
    lex = lexicon()
    line = resolve_brand(product("x:1", "colonial", "NESTLE Nesquik Cereal 330g", brand="Nesquik"), brand_lexicon=lex)
    assert (line.canonical_brand, line.source) == ("nesquik", "source")
    family = resolve_brand(
        product("x:2", "paiz", "Shampoo Bebé Johnson's Original - 200 ml", brand="JOHNSON'S BABY"),
        brand_lexicon=lex,
    )
    assert (family.canonical_brand, family.source) == ("johnsons baby", "source")


def test_unambiguous_contradicting_name_brand_still_blocks_source_brand() -> None:
    resolution = resolve_brand(
        product("x:1", "la_colonia", "Suavizante Downy Concentrado Aroma Floral 4.8 Lt", brand="Suavitel"),
        brand_lexicon=lexicon(),
    )
    assert (resolution.canonical_brand, resolution.source, resolution.conflict) == (None, "source_conflict", True)


def test_name_brand_of_manufacturer_is_not_a_conflict_with_line_brand() -> None:
    lex = lexicon()
    left = profile_product_v2(product("c:1", "colonial", "NESTLE Nesquik Cereal 330g"), brand_lexicon=lex)
    right = profile_product_v2(product("lc:9", "la_colonia", "Cereal Nesquik 330 Gr", brand="Nesquik"), brand_lexicon=lex)
    assert left.normalized_brand == "nestle"
    assert "brand_conflict" not in _hard_conflicts(left, right)
    other = profile_product_v2(product("w:9", "walmart", "Cereal Zucaritas 330 g", brand="Kelloggs"), brand_lexicon=lex)
    assert "brand_conflict" in _hard_conflicts(left, other)


def test_name_lexicon_brand_persists_with_existing_provenance_value() -> None:
    rows = build_homologation_rows(
        [(index + 1, record) for index, record in enumerate((*REFERENCE, product("c:1", "colonial", "KELLOGGS Corn Flakes Orginal 1.22kg")))],
        updated_at_utc="2026-10-01T00:00:00Z",
    )
    colonial = rows[-1]
    assert colonial.normalized_brand == "kelloggs"
    assert colonial.brand_resolution_source == "name_known_brand"
    assert colonial.raw_brand is None


def test_inferred_brand_does_not_break_sku_gtin_name_agreement() -> None:
    gtin = "00760861006398"
    result = homologate_products_v2(
        [
            product("colonial:1", "colonial", "DELISOYA Leche S/Lactosa 360g", presentation="360 g", barcode=gtin),
            product("walmart:1", "walmart", "Bebida en polvo Delisoy sin lactosa bolsa - 360 g", brand="Delisoy", presentation="360 g", barcode=gtin),
            product("lc:1", "la_colonia", "Bebida Delisoya Original 1 L", brand="Delisoya"),
        ]
    )
    (group,) = result.exact_gtin_groups
    assert group.comparison_status == "ready"


def test_compact_match_never_glues_units_or_connectors() -> None:
    lex = BrandLexicon({"limpiox", "oral b"}, squashed={"limpiox": "limpiox", "oralb": "oral b"})
    resolution = resolve_brand(product("c:1", "colonial", "SM CALAMAR Anillo Limpio x LB"), brand_lexicon=lex)
    assert resolution.canonical_brand is None
    assert resolve_brand(product("c:2", "colonial", "ORAL B Pasta Bicarbonato 150ML"), brand_lexicon=lex).canonical_brand == "oral b"
