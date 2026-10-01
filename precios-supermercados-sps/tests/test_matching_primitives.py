"""Primitivas del motor de matching: texto, GTIN, registros, taxonomía, política."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from precios_supermercados.matching.config import (
    DEFAULT_POLICY_PATH,
    DEFAULT_TAXONOMY_PATH,
    MatchingConfigError,
    load_engine_config,
)
from precios_supermercados.matching.gtin import classify_gtin, gtin_from_product_id, identity_gtin
from precios_supermercados.matching.policy import IdentityPolicyError, gate_from_mapping, load_policy_gate
from precios_supermercados.matching.records import (
    MatchRecord,
    MatchRecordError,
    read_records,
    split_by_city,
    write_records,
)
from precios_supermercados.matching.taxonomy import (
    departments_compatible,
    load_source_taxonomy,
)
from precios_supermercados.matching.text import (
    TfidfModel,
    char_ngrams,
    cosine,
    jaro_winkler,
    soft_token_overlap,
    token_set_ratio,
    tokens_match,
)


def _with_check_digit(body: str) -> str:
    # GS1: pesos 3,1,3... desde la derecha del cuerpo.
    weighted = sum(int(digit) * (3 if position % 2 == 1 else 1) for position, digit in enumerate(reversed(body), start=1))
    return body + str((10 - weighted % 10) % 10)


# -- texto -----------------------------------------------------------------------
def test_jaro_winkler_reference_values() -> None:
    assert jaro_winkler("martha", "marhta") == pytest.approx(0.9611, abs=1e-4)
    assert jaro_winkler("dwayne", "duane") == pytest.approx(0.84, abs=1e-4)
    assert jaro_winkler("", "abc") == 0.0
    assert jaro_winkler("abc", "abc") == 1.0


def test_token_set_ratio_and_soft_overlap_handle_order_and_abbreviations() -> None:
    assert token_set_ratio(["arroz", "blanco"], ["blanco", "arroz", "precocido"]) == 1.0
    assert token_set_ratio([], ["a"]) == 0.0
    assert tokens_match("acondi", "acondicionador")
    assert tokens_match("shampoo", "shampoos")
    assert not tokens_match("pan", "pantalon")
    assert soft_token_overlap(["deterg", "polvo"], ["detergente", "polvo"]) == 1.0
    assert soft_token_overlap(["a"], []) == 0.0


def test_tfidf_cosine_is_normalized_and_rewards_rare_ngrams() -> None:
    model = TfidfModel(["jugo naranja", "jugo manzana", "jugo uva", "shampoo dove"])
    left = model.vector("jugo naranja")
    assert cosine(left, left) == pytest.approx(1.0)
    assert cosine(left, model.vector("jugo naranjas")) > cosine(left, model.vector("jugo manzana"))
    assert cosine({}, left) == 0.0
    assert char_ngrams("ab", (3,)) == {"#ab": 1, "ab#": 1}


# -- GTIN ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("body", "kind", "restricted"),
    [
        ("741100188909", "ean13", False),
        ("257248000000", "ean13", True),  # 2xx: peso variable / uso interno
        ("000000000108", "ean8", True),  # EAN-8 con prefijo 0
        ("20000000000", "upc_a", True),  # UPC sistema 2 (peso variable)
        ("40000000000", "upc_a", True),  # UPC sistema 4 (uso interno)
        ("980000000000", "ean13", True),  # cupones
        ("07894700010", "upc_a", False),
    ],
)
def test_classify_gtin_restricted_prefixes(body: str, kind: str, restricted: bool) -> None:
    info = classify_gtin(_with_check_digit(body))
    assert info is not None
    assert (info.kind, info.restricted) == (kind, restricted)
    assert (identity_gtin(_with_check_digit(body)) is None) is restricted


def test_invalid_gtin_and_product_id_helpers() -> None:
    assert classify_gtin("7411001889091") is None
    assert classify_gtin("abc") is None
    assert gtin_from_product_id("prod_gtin_07411001889090") == "07411001889090"
    assert gtin_from_product_id("source-123") is None


# -- registros -------------------------------------------------------------------
def test_match_record_validation_and_roundtrip(tmp_path: Path) -> None:
    record = MatchRecord(
        source_record_id="walmart:1",
        supermarket_id="walmart",
        city="SPS",
        source_name="Jugo Sula Naranja - 1890 ml",
        current_price="60.00",
        location_ids=("walmart_sps",),
        extra={"published_comparability": "individual"},
    )
    path = tmp_path / "records.jsonl"
    assert write_records(path, [record]) == 1
    loaded = read_records(path)
    assert loaded == (record,)
    assert split_by_city(loaded) == {"SPS": (record,)}
    with pytest.raises(MatchRecordError):
        MatchRecord(source_record_id="", supermarket_id="walmart", city="SPS", source_name="x")
    with pytest.raises(MatchRecordError):
        MatchRecord(source_record_id="a", supermarket_id="walmart", city="SPS", source_name="x", current_price="-1")
    with pytest.raises(MatchRecordError):
        MatchRecord(source_record_id="a", supermarket_id="w", city="SPS", source_name="x", fingerprint_authority="otro")
    payload = record.to_json()
    payload["unexpected"] = 1
    with pytest.raises(MatchRecordError):
        MatchRecord.from_json(payload)
    path.write_text(path.read_text(encoding="utf-8") * 2, encoding="utf-8")
    with pytest.raises(MatchRecordError):
        read_records(path)


# -- configuración y taxonomía -----------------------------------------------------
def test_engine_config_is_shadow_and_versioned(tmp_path: Path) -> None:
    config = load_engine_config()
    assert config.raw["mode"] == "shadow"
    assert "sugar_line" in config.variant_vocabulary
    assert "original" in config.implicit_defaults["sugar_line"]
    broken = tmp_path / "engine.yaml"
    broken.write_text(
        Path(DEFAULT_TAXONOMY_PATH.parent / "matching-engine-v1.yaml").read_text(encoding="utf-8").replace("mode: shadow", "mode: live"),
        encoding="utf-8",
    )
    with pytest.raises(MatchingConfigError):
        load_engine_config(broken)


def test_source_taxonomy_departments_rules_and_leaf_learning() -> None:
    taxonomy = load_source_taxonomy(DEFAULT_TAXONOMY_PATH)
    assert taxonomy.department_for("/Abarrotes/Aceites de cocina/Aceite Vegetal/") == "Alimentos"
    assert taxonomy.type_for("/Abarrotes/Aceites de cocina/Aceite Vegetal/") == "Aceite comestible"
    # "Agua micelar" en Higiene y Belleza no es Agua (bebida).
    assert taxonomy.type_for("/Higiene y Belleza/Cuidado Facial/Agua micelar/") is None
    assert taxonomy.department_for("Hogar y Limpieza") == "Limpieza"
    assert departments_compatible("Alimentos", "Bebidas") is True
    assert departments_compatible("Mascotas", "Alimentos") is False
    assert departments_compatible(None, "Alimentos") is None
    leaf = "/Lacteos/Yogurt/Bebible/"
    # 3/5 = 60 % de acuerdo: no se propaga.
    assert taxonomy.learn_leaf_types([(leaf, "Yogurt")] * 3 + [(leaf, "Leche")] * 2) == {}
    # Un departamento solo es demasiado amplio para propagar.
    assert taxonomy.learn_leaf_types([("Abarrotes", "Arroz")] * 10) == {}
    learned = taxonomy.learn_leaf_types([(leaf, "Yogurt")] * 9 + [(leaf, "Leche")])
    mapping = learned["lacteos/yogurt/bebible"]
    assert (mapping.product_type, mapping.support, mapping.agreement) == ("Yogurt", 10, 0.9)


# -- política --------------------------------------------------------------------
def test_policy_gate_blocks_publication_in_shadow() -> None:
    gate = load_policy_gate(DEFAULT_POLICY_PATH)
    assert gate.mode == "shadow"
    assert gate.engine_publication_allowed(measured_precision=1.0) is False
    enabled = gate_from_mapping(
        {
            "schema": "precios-sps-product-identity-policy/v1",
            "automatic_identity": {"allowed": ["probabilistic_auto_match_with_measured_precision"]},
            "publication": {"mode": "enforced", "public_serving_allowed": True, "require_offline_precision_before_enable": True},
        }
    )
    assert enabled.engine_publication_allowed(measured_precision=0.99) is True
    assert enabled.engine_publication_allowed(measured_precision=0.95) is False
    assert enabled.engine_publication_allowed() is False
    with pytest.raises(IdentityPolicyError):
        gate_from_mapping({"schema": "otro"})
    assert json.loads(json.dumps(gate.to_json()))["engine_publication_allowed"] is False
