"""Identidad con GTIN derivado del SKU Colonial (aprobado 2026-10-01).

Casos tomados de la captura Colonial 2026-08-30 frente a Walmart/Paiz. El SKU
GS1 válido es evidencia fuerte, pero conserva todos los conflictos del grupo
GTIN y además una marca claramente contradictoria lo invalida.
"""
from __future__ import annotations

import pytest
import yaml
from pathlib import Path

from precios_supermercados.gtin_policy import SKU_DERIVED_GTIN_SUPERMARKETS
from precios_supermercados.product_homologation import SourceProductRecord
from precios_supermercados.product_identity_v2 import (
    homologate_products_v2,
    normalize_variant_text,
    split_glued_words,
)

GTIN = "7501055300075"
POLICY = Path(__file__).resolve().parents[1] / "config/homologation/identity-policy-v1.yaml"


def product(record_id: str, name: str, *, brand: str | None = None, barcode: str = GTIN) -> SourceProductRecord:
    return SourceProductRecord(
        source_record_id=record_id,
        supermarket_id=record_id.split(":", 1)[0],
        source_name=name,
        source_brand=brand,
        barcode=barcode,
    )


def group_for(*records: SourceProductRecord):
    (group,) = homologate_products_v2(records).exact_gtin_groups
    return group


def test_policy_declares_colonial_as_sku_derived_source() -> None:
    policy = yaml.safe_load(POLICY.read_text(encoding="utf-8"))["restricted_gtin"]
    assert frozenset(policy["sku_derived_gtin_supermarkets"]) == SKU_DERIVED_GTIN_SUPERMARKETS >= {"colonial"}


def test_sku_gtin_matches_other_retailer_without_conflict() -> None:
    group = group_for(
        product("colonial:1", "SULA Jugo Manzana 473ml", brand="RMS"),
        product("walmart:1", "Jugo Sula De Manzana - 473Ml", brand="Sula"),
    )
    assert group.comparison_status == "ready"


def test_sku_gtin_with_contradictory_brand_is_excluded() -> None:
    group = group_for(
        product("colonial:1", "GLADE Limpiador Lavanda 275ml", brand="Glade"),
        product("walmart:1", "Limpiador Pledge Multisuperficies Lavanda -275ml", brand="Pledge"),
        product("paiz:1", "Limpiador Pledge Multisuperficies Lavanda -275ml", brand="Pledge"),
    )
    assert group.comparison_status == "ready"
    assert group.supermarket_ids == ("paiz", "walmart")
    assert dict(group.excluded_members) == {"colonial:1": ("sku_gtin_brand_conflict",)}


@pytest.mark.parametrize(
    ("colonial", "colonial_brand", "other", "other_brand"),
    [
        ("CHEETOS Crunchy 60.2g", "Cheetos", "Snacks Frito Lay Cheetos Crunchy - 60.2 g", "Frito Lay"),
        ("MARISELA Pinguino 80g", "Marisela", "Pastel Marinela Pinguino 80 g", "Marinela"),
        ("SPAM Jalapeño 340g", "Spam", "Spam Hormel jalapeño - 340 g", "Hormel"),
    ],
)
def test_manufacturer_line_or_typo_brand_is_not_a_contradiction(colonial, colonial_brand, other, other_brand) -> None:
    group = group_for(
        product("colonial:1", colonial, brand=colonial_brand),
        product("walmart:1", other, brand=other_brand),
    )
    assert group.comparison_status == "ready"


def test_brand_label_disagreement_without_sku_source_is_still_ignored() -> None:
    group = group_for(
        product("la_colonia:1", "GLADE Limpiador Lavanda 275ml", brand="Glade"),
        product("walmart:1", "Limpiador Pledge Multisuperficies Lavanda -275ml", brand="Pledge"),
    )
    assert group.comparison_status == "ready"


def test_restricted_in_store_sku_gtin_never_crosses_retailers() -> None:
    group = group_for(
        product("colonial:1", "Repollo Morado Libra", barcode="2572480000002"),
        product("walmart:1", "Repollo Morado Libra", barcode="2572480000002"),
    )
    assert group.comparison_status == "review_required"
    assert group.conflict_reasons == ("restricted_gtin_outside_shared_master",)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("SILK AlmendVainiSinAzucar946ml", "silk almend vainilla sin azucar 946 ml"),
        ("Delisoy Almendras S/Azu 1L", "delisoy almendras sin azucar 1l"),
        ("ENSURE AdvanceVaini400g", "ensure advance vainilla 400g"),
        ("DEL MONTE Nectar Meloctn 330ml", "del monte nectar melocoton 330 ml"),
        ("HERSHEYS Sugar Free Chocol 85g", "hersheys sin azucar chocolate 85g"),
        ("HELLMANNS Oliva Ligth D/Pack 380g", "hellmanns oliva light d pack 380g"),
    ],
)
def test_variant_text_normalizes_glued_words_and_abbreviations(raw: str, expected: str) -> None:
    assert normalize_variant_text(raw) == expected


def test_glued_split_keeps_short_codes() -> None:
    assert split_glued_words("V8 B12 AbrazosVainilla Botell330ml") == "V8 B12 Abrazos Vainilla Botell 330 ml"


@pytest.mark.parametrize(
    ("colonial", "other"),
    [
        ("SILK AlmendVainiSinAzucar946ml", "Bebida de almendra Silk sin azúcar sabor vainilla - 946 ml"),
        ("Delisoy Almendras S/Azu 1L", "Bebida de Almendra Delisoya Uht Sin Azucar - 1 litro"),
        ("ENSURE AdvanceVaini400g", "Complemento Ensure Advance® Sabor Vainilla - 400 g"),
        ("DEL MONTE Nectar Meloctn 330ml", "Jugo Del Monte Nectar  De Melocoton- 330 ml"),
        ("FERRERO ROCHER Chocolates 100g", "Chocolate Ferrero Rocher 8 Uds - 100 g"),
        ("HELLMANNS Oliva Ligth D/Pack 380g", "Mayonesa Hellmann's Oliva Light en doypack - 380 g"),
    ],
)
def test_abbreviated_colonial_names_are_not_one_sided_variants(colonial: str, other: str) -> None:
    group = group_for(product("colonial:1", colonial), product("walmart:1", other))
    assert group.comparison_status == "ready", group.conflict_reasons


@pytest.mark.parametrize(
    ("colonial", "other"),
    [
        ("TROPICAL Uva 500 ml", "Gaseosa Tropical regular - 500 ml"),  # SKU genérico
        ("DAILYS Pina Colada Mix 1L", "P6 Bikini Algodon Dama Hanes Surtido 5"),  # colisión de SKU
        ("TANG Te Limon 13g", "Bebida en Polvo Tang de Te Frío- 13 g"),
    ],
)
def test_real_one_sided_variants_from_colonial_stay_blocked(colonial: str, other: str) -> None:
    group = group_for(product("colonial:1", colonial), product("walmart:1", other))
    assert group.comparison_status == "review_required"


# --- Acuerdo mínimo de nombre y número de tono/modelo (GTIN derivado de SKU) ---
# Casos reales del spot-check 2026-10-01 sobre los 2,359 grupos con Colonial.


@pytest.mark.parametrize(
    ("colonial", "colonial_brand", "other", "other_brand", "reason"),
    [
        ("LOREAL Vol Blackest Black 200", "L'Oreal", "Máscara Para Pestañas L'Oréal Paris Lash Paradise Lavable - 8.5ml", "L'Oreal", "sku_gtin_name_disagreement"),
        ("DIANA Favori Mix Criollo 128g", "Diana", "Chicharrón Diana con yuca - 128 g", "Diana", "sku_gtin_name_disagreement"),
        ("D OLANCHO Chile Añejo 500ml", "D Olancho", "Salsa Riberenas Picante Inglesa - 500 ml", "Riberenas", "sku_gtin_name_disagreement"),
        # Colonial reporta el placeholder "RMS" como vendor (dato real).
        ("DEL RANCHO Chicharron Picosit 100g", "RMS", "Boquita Yummies Chicharrón Picante - 100 g", "Yummies", "sku_gtin_name_disagreement"),
        ("MAYBELLINE Age Rewind Light 20", "Maybelline", "Corrector Maybelline NY Age Rewind Light Honey 120 -0.2 Oz", "Maybelline", "model_number_conflict"),
    ],
)
def test_wrong_colonial_sku_code_is_excluded(colonial, colonial_brand, other, other_brand, reason) -> None:
    group = group_for(
        product("colonial:1", colonial, brand=colonial_brand),
        product("walmart:1", other, brand=other_brand),
        product("paiz:1", other, brand=other_brand),
    )
    assert group.comparison_status == "ready"
    assert group.supermarket_ids == ("paiz", "walmart")
    (excluded_id, reasons), = group.excluded_members
    assert excluded_id == "colonial:1"
    assert reason in reasons


@pytest.mark.parametrize(
    ("colonial", "colonial_brand", "other", "other_brand"),
    [
        ("Bic Boli Prec Suave 3 Negro", "Bic", "Bic Bol Precision Y Suavida Neg Bl 3e", "Bic"),
        ("ORAL-B CepillloDentalCompl2und", "Oral-B", "Cepillo de Dientes Oral-B 5 Acciones Cerdas Suaves Inteligentes - 2 Uds", "Oral-B"),
        ("PURINA Dog Show Beef&Chicken 100g", "Purina", "Comida húmeda para perro Purina Dog Chow con pollo y carne 100 g", "Purina"),
        ("Trident Tibra Yerbabuena 30.6g", "Trident", "Goma De Mascar Trident Sabor Yerbabuena 18 unidades - 30.6 g", "Trident"),
        ("LACTOLAC Dip Cebolla 230g", "Lactolac", "Queso Lactosa para untar con cebolla y hierbas - 230 g", "Lactolac"),
        ("CARBONELL Olive Extra Virgen oil 500ml", "Carbonell", "Aceite Oliva Carbonell Xv Vidrio 500 Ml", "Carbonell"),
        ("NUVYS EsmalteRosaExhuberan#002", "Nuvys", "Nuvys Esmalte Para Unas No 02 Rosa Fusia", "Nuvys"),
    ],
)
def test_abbreviated_true_matches_pass_name_guard(colonial, colonial_brand, other, other_brand) -> None:
    group = group_for(
        product("colonial:1", colonial, brand=colonial_brand),
        product("walmart:1", other, brand=other_brand),
    )
    assert group.comparison_status == "ready", group.conflict_reasons


def test_name_guard_applies_only_to_sku_derived_gtins() -> None:
    group = group_for(
        product("la_colonia:1", "DIANA Favori Mix Criollo 128g", brand="Diana"),
        product("walmart:1", "Chicharrón Diana con yuca - 128 g", brand="Diana"),
    )
    assert group.comparison_status == "ready"


def test_colonial_name_without_significant_tokens_fails_closed() -> None:
    group = group_for(
        product("colonial:1", "ROYAL 80g", brand="Royal"),
        product("walmart:1", "Gelatina Royal Sabor Cereza - 80g", brand="Royal"),
    )
    assert group.comparison_status == "review_required"
    assert "sku_gtin_name_disagreement" in group.conflict_reasons


def test_glued_colonial_quantity_is_parsed_and_can_conflict() -> None:
    group = group_for(
        product("colonial:1", "SAVILE ShampAguac&Sab550ml", brand="Savile"),
        product("walmart:1", "Shampoo Savilé Aguacate y Sábila en Líquido - 510 ml", brand="Savile"),
    )
    assert group.comparison_status == "review_required"
    assert "cross_source_presentation_conflict" in group.conflict_reasons
