"""Estandarización y vectores de comparación del motor de matching."""
from __future__ import annotations

import pytest

from precios_supermercados.matching.comparison import (
    FIELD_NAMES,
    ComparisonSettings,
    compare,
)
from precios_supermercados.matching.config import DEFAULT_TAXONOMY_PATH, load_engine_config
from precios_supermercados.matching.records import MatchRecord
from precios_supermercados.matching.standardize import (
    Standardizer,
    VariantLexicon,
    split_glued_words,
)
from precios_supermercados.matching.taxonomy import load_source_taxonomy
from precios_supermercados.matching.text import TfidfModel
from precios_supermercados.matching.unit_price import comparable_alternatives

CONFIG = load_engine_config()
SETTINGS = ComparisonSettings.from_config(CONFIG.section("comparison"), CONFIG.implicit_defaults)


def _record(record_id: str, name: str, *, brand: str | None = None, barcode: str | None = None, price: str | None = None, category: str | None = None) -> MatchRecord:
    supermarket = record_id.split(":")[0]
    return MatchRecord(
        source_record_id=record_id,
        supermarket_id=supermarket,
        city="SPS",
        source_name=name,
        source_brand=brand,
        source_category=category,
        barcode=barcode,
        current_price=price,
        location_ids=(f"{supermarket}_sps",),
    )


def _standardize(*records: MatchRecord):  # type: ignore[no-untyped-def]
    standardizer = Standardizer(
        records,
        vocabulary=CONFIG.variant_vocabulary,
        taxonomy=load_source_taxonomy(DEFAULT_TAXONOMY_PATH),
        synonyms=CONFIG.token_synonyms,
    )
    items = standardizer.run()
    tfidf = TfidfModel(item.core_text for item in items)
    for item in items:
        item.tfidf = tfidf.vector(item.core_text)
    return {item.source_record_id: item for item in items}


def _labels(left: MatchRecord, right: MatchRecord, *extra: MatchRecord) -> tuple[dict[str, str], tuple[str, ...], tuple[str, ...]]:
    items = _standardize(left, right, *extra)
    vector = compare(items[left.source_record_id], items[right.source_record_id], SETTINGS)
    return vector.as_labels(), vector.hard_conflicts, vector.auto_caps


def test_split_glued_words_keeps_short_codes() -> None:
    assert split_glued_words("GLADE AbrazosVainilla 400ml") == "GLADE Abrazos Vainilla 400 ml"
    assert split_glued_words("SCHOLFFE CerveGrapeBotell330ml") == "SCHOLFFE Cerve Grape Botell 330 ml"
    assert split_glued_words("Rexona V8 45g") == "Rexona V8 45g"


def test_variant_lexicon_prefers_long_phrases_and_skips_brand() -> None:
    lexicon = VariantLexicon(CONFIG.variant_vocabulary)
    assert lexicon.extract("leche sin azucar light".split()) == {"sugar_line": frozenset({"zero", "light"})}
    assert lexicon.extract("jugo la fresa naranja".split(), excluded=[("la", "fresa")]) == {"flavor": frozenset({"naranja"})}
    assert lexicon.extract("jugo naranja sin pulpa".split())["pulp"] == frozenset({"without_pulp"})


def test_standardized_record_fields() -> None:
    items = _standardize(
        _record("colonial:1", "DEL MONTE NectarPera 330ml", brand="RMS", price="11.99", category="Bebidas No Alcohólicas"),
        _record("walmart:2", "Néctar Del Monte Pera Lata - 330 ml", brand="Del Monte", price="11.40"),
        _record("walmart:3", "Pasta Zara Codito No.55 - 500 g", brand="Zara", barcode="7411001889090"),
    )
    colonial = items["colonial:1"]
    assert colonial.brand == "del monte"
    assert colonial.core_tokens == ("nectar", "pera")
    assert colonial.size is not None and colonial.size.total == 330.0
    assert colonial.unit_price == pytest.approx(11.99 / 330 * 1000)
    assert colonial.unit_price_basis == "HNL/L"
    assert colonial.department == "Bebidas"
    pasta = items["walmart:3"]
    assert "55" in pasta.codes and "500" not in pasta.codes
    assert pasta.identity_gtin == "07411001889090"
    assert pasta.silver_gtin == "07411001889090"


def test_same_product_different_naming_conventions() -> None:
    labels, hard, caps = _labels(
        _record("colonial:1", "DEL MONTE NectarPera 330ml", brand="RMS", price="11.99"),
        _record("walmart:2", "Néctar Del Monte Pera Lata - 330 ml", brand="Del Monte", price="11.40"),
    )
    assert labels["brand"] in {"exact", "in_other_name"}
    assert labels["size"] == "exact"
    assert labels["name"] in {"very_high", "high"}
    assert labels["variant"] == "agree"
    assert labels["price"] == "ratio_le_1_3"
    assert hard == ()


def test_variant_and_size_conflicts_are_hard() -> None:
    labels, hard, _ = _labels(
        _record("la_colonia:1", "Aderezo Kraft Ranch Classic 237 ml", brand="Kraft"),
        _record("walmart:2", "Aderezo Kraft Ranch Light 237 ml", brand="Kraft"),
    )
    assert labels["variant"] == "conflict"
    assert "variant_conflict" in hard
    labels, hard, _ = _labels(
        _record("la_colonia:1", "Arroz Progreso Blanco 1000 Gr", brand="Progreso"),
        _record("walmart:2", "Arroz Progreso Blanco - 2000 g", brand="Progreso"),
    )
    assert labels["size"] == "conflict" and "size_conflict" in hard


def test_ounce_bridge_and_near_sizes_cap_auto_match() -> None:
    labels, hard, caps = _labels(
        _record("la_colonia:1", "Aderezo Kraft Ranch 8 Oz", brand="Kraft"),
        _record("walmart:2", "Aderezo Kraft Ranch - 237 ml", brand="Kraft"),
    )
    assert labels["size"] == "ounce_bridge"
    assert "size_ounce_bridge" in caps and hard == ()
    labels, _, caps = _labels(
        _record("colonial:1", "NUTRI YEMA Claras de Huevo 554gr", brand="Nutri Yema"),
        _record("walmart:2", "Claras de huevo Nutri Yema - 580 g", brand="Nutri Yema"),
    )
    assert labels["size"] == "near" and "size_near" in caps


def test_brand_cross_name_and_conflict() -> None:
    labels, hard, _ = _labels(
        _record("colonial:1", "PURINA DogChow Cachorro 4Kg", brand="Purina"),
        _record("walmart:2", "Comida para perro Purina Dog Chow Cachorro - 4 kg", brand="Dog Chow"),
    )
    assert labels["brand"] == "cross_name"
    assert "brand_conflict" not in hard
    labels, hard, _ = _labels(
        _record("colonial:1", "COLGATE Pasta Dental Total 75ml", brand="Colgate"),
        _record("walmart:2", "Pasta Dental Crest Total - 75 ml", brand="Crest"),
    )
    assert labels["brand"] == "conflict" and "brand_conflict" in hard


def test_implicit_default_variant_is_not_one_sided() -> None:
    labels, _, caps = _labels(
        _record("la_colonia:1", "Mayonesa Hellmanns Original 380 Gr", brand="Hellmanns"),
        _record("walmart:2", "Mayonesa Hellmanns - 380 g", brand="Hellmanns"),
    )
    assert labels["variant"] == "none"
    labels, _, caps = _labels(
        _record("la_colonia:1", "Mayonesa Hellmanns Light 380 Gr", brand="Hellmanns"),
        _record("walmart:2", "Mayonesa Hellmanns - 380 g", brand="Hellmanns"),
    )
    assert labels["variant"] == "one_sided" and "variant_one_sided" in caps


def test_gtin_levels_and_same_retailer_flag() -> None:
    items = _standardize(
        _record("walmart:1", "Jugo Sula Naranja - 1890 ml", brand="Sula", barcode="7411001889090"),
        _record("la_colonia:2", "Jugo Sula Naranja 1890 Ml", brand="Sula", barcode="7411001889090"),
        _record("colonial:3", "SULA Jugo Naranja 1890ml", barcode="7421000811633"),
        _record("walmart:4", "Jugo Sula Naranja - 1890 ml", brand="Sula", barcode="2572480000002"),
    )
    assert compare(items["walmart:1"], items["la_colonia:2"], SETTINGS).gtin_level == "same"
    different = compare(items["walmart:1"], items["colonial:3"], SETTINGS)
    assert different.gtin_level == "different" and "gtin_different" in different.hard_conflicts
    restricted = compare(items["walmart:4"], items["la_colonia:2"], SETTINGS)
    assert restricted.gtin_level == "restricted"
    twin = compare(items["walmart:1"], items["walmart:4"], SETTINGS)
    assert twin.same_retailer and twin.exact_name
    assert len(twin.levels) == len(FIELD_NAMES)


def test_comparable_alternative_uses_unit_price_never_identity() -> None:
    items = _standardize(
        _record("la_colonia:1", "Arroz Progreso Blanco 1000 Gr", brand="Progreso", price="40"),
        _record("walmart:2", "Arroz Progreso Blanco - 2000 g", brand="Progreso", price="70"),
    )
    vector = compare(items["la_colonia:1"], items["walmart:2"], SETTINGS)
    rows = comparable_alternatives([vector], items)
    assert len(rows) == 1
    row = rows[0]
    assert row["relation"] == "COMPARABLE_ALTERNATIVE" and row["identity"] is False
    assert row["unit_price_basis"] == "HNL/kg"
    assert row["cheaper_per_unit_source_record_id"] == "walmart:2"
    assert row["unit_price_ratio"] == pytest.approx(40 / 35, rel=1e-3)
