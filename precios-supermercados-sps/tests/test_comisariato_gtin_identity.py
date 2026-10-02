"""GTIN reconstruido desde el `code` de Comisariato Los Andes (aprobado 2026-10-01).

`code` = `0001-` + 15 dígitos: GTIN SIN dígito de control, con ceros a la
izquierda. Verificación live 2026-10-01: en una muestra aleatoria de 55 códigos,
21 GTIN reconstruidos coinciden con el GTIN de otra cadena con el mismo producto.
Los GTIN y los nombres del otro lado salen de los snapshots Paiz versionados
(``reports/paiz/2026-09-04-full``); los nombres Comisariato de los casos Marinela
son los del fixture real y el resto son las descripciones de la verificación.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from precios_supermercados.gtin_policy import (
    SKU_DERIVED_GTIN_SUPERMARKETS,
    restricted_circulation_reason,
)
from precios_supermercados.product_homologation import SourceProductRecord
from precios_supermercados.product_identity_v2 import homologate_products_v2
from precios_supermercados.scrapers.comisariato_los_andes import (
    EAN_PROVENANCE,
    ean_provenance,
    gs1_check_digit,
    gtin_from_code,
    parse_products,
)

POLICY = Path(__file__).resolve().parents[1] / "config/homologation/identity-policy-v1.yaml"
PLACEHOLDER_BRAND = "Marca COMANDES"


def record(record_id: str, name: str, brand: str | None, gtin: str | None) -> SourceProductRecord:
    return SourceProductRecord(
        source_record_id=record_id,
        supermarket_id=record_id.split(":", 1)[0],
        source_name=name,
        source_brand=brand,
        barcode=gtin,
    )


def group_for(*records: SourceProductRecord):
    (group,) = homologate_products_v2(records).exact_gtin_groups
    return group


# --- Reconstrucción --------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("0001-000744102955677", "7441029556773"),  # Bimbo Pan Blanco 720 g (12 → EAN-13)
        ("0001-000742100091520", "7421000915201"),  # Delicia Bacon 397 g
        ("0001-000742160030024", "7421600300247"),  # Pepsi 2 L
        ("0001-000741100020423", "7411000204238"),  # McCormick Mostaza
        ("0001-000775149300644", "7751493006446"),  # Plenitud
        ("0001-000800715090299", "8007150902996"),  # Olitalia
        ("0001-000003800084673", "038000846731"),  # Pringles (10 → UPC-A con 0 inicial)
        ("0001-000007107203054", "071072030547"),  # Alessi breadsticks (10 → UPC-A)
        ("0001-000000007400065", "74000654"),  # Marinela Submarino (7 → EAN-8)
        (" 0001-000744102955677 ", "7441029556773"),  # recortado
    ],
)
def test_code_reconstructs_gtin_with_gs1_check_digit(code: str, expected: str) -> None:
    assert gtin_from_code(code) == expected


@pytest.mark.parametrize(
    "code",
    [
        "0001-000099001005224",  # interno `99…` (11 dígitos, daría un UPC-A "válido")
        "0001-000009900500123",  # interno `99…` de 10 dígitos (sintético)
        "0001-000024153000000",  # "Delicia jamon pollo lb plu 133": peso variable en tienda
        "0001-000029801000000",  # "Pan molido libra": peso variable en tienda
        "0001-000000000000123",  # base de 3 dígitos
        "0001-000000000001234",  # base de 4 dígitos
        "0001-000000012345678",  # base de 8 dígitos: no se interpreta
        "0001-000000123456789",  # base de 9 dígitos: no se interpreta
        "0001-000000002400051",  # 7 dígitos con 2 inicial → RCN-8 restringido
        "0001-000200000000001",  # EAN-13 2xx: uso en tienda
        "0001-000000000000000",
        "0001-1",
        "0001-00074410295567",  # 14 dígitos
        "0002-000744102955677",  # otro prefijo
        "744102955677",
        "0001-00074410295567A",
        "",
        None,
        744102955677,
    ],
)
def test_internal_restricted_or_malformed_codes_never_produce_gtin(code) -> None:
    assert gtin_from_code(code) is None


def test_variable_weight_plu_would_be_restricted_gtin() -> None:
    # Aunque se reconstruyera, `24153000000` cae en el rango UPC 2… (peso variable).
    body = "24153000000"
    assert restricted_circulation_reason(body + gs1_check_digit(body)) == "rcn_upc_variable_measure"


def test_restricted_reconstruction_never_crosses_retailers() -> None:
    gtin = "241530000003"
    group = group_for(
        record("comisariato_los_andes:1", "Delicia jamon pollo lb plu 133", PLACEHOLDER_BRAND, gtin),
        record("walmart:1", "Jamón de pollo Delicia por libra", "Delicia", gtin),
    )
    assert group.comparison_status == "review_required"
    assert "restricted_gtin_outside_shared_master" in group.conflict_reasons


def test_rows_keep_code_as_reference_and_derive_provenance() -> None:
    products = [
        {"code": "0001-000744102955677", "name": "Bimbo pan blanco 720 g", "newPrice": 50},
        {"code": "0001-000099001005224", "name": "Ready to go aderezo", "newPrice": 50},
        {"code": "0001-000024153000000", "name": "Delicia jamon pollo lb plu 133", "newPrice": 50},
    ]
    rows, _ = parse_products(products)
    by_reference = {row["reference"]: row for row in rows}
    gtin_row = by_reference["0001-000744102955677"]
    assert gtin_row["ean"] == "7441029556773"
    assert gtin_row["source_key"] == gtin_row["product_id"] == "0001-000744102955677"
    assert ean_provenance(gtin_row) == EAN_PROVENANCE == "sku_reconstructed_check_digit"
    assert by_reference["0001-000099001005224"]["ean"] is None
    assert by_reference["0001-000024153000000"]["ean"] is None
    assert all(ean_provenance(by_reference[key]) is None for key in by_reference if key != gtin_row["reference"])
    assert ean_provenance({**gtin_row, "ean": "7441029556774"}) is None  # no es la reconstrucción
    assert all("ean_source" not in row for row in rows)  # contrato de snapshot cerrado


# --- Política -------------------------------------------------------------------


def test_policy_declares_comisariato_as_sku_derived_source() -> None:
    policy = yaml.safe_load(POLICY.read_text(encoding="utf-8"))["restricted_gtin"]
    assert frozenset(policy["sku_derived_gtin_supermarkets"]) == SKU_DERIVED_GTIN_SUPERMARKETS
    assert SKU_DERIVED_GTIN_SUPERMARKETS == {"colonial", "comisariato_los_andes"}


@pytest.mark.parametrize(
    ("comisariato", "brand", "other", "other_brand", "gtin"),
    [
        # Nombres Comisariato reales (fixture 2026-09-04) vs Paiz 2026-09-04.
        ("Marisela submarino vainilla 64gr", PLACEHOLDER_BRAND, "Pastel Marinela Submarino Vainilla -  64 g", "MARINELA", "74000654"),
        ("Marisela submarino fresa 64g", PLACEHOLDER_BRAND, "Pastel Marinela Submarino Fresa - 64 g", "BIMBO", "74000661"),
        ("Marisela pinguinos", PLACEHOLDER_BRAND, "Pastel Marinela Pinguino 2 Uds - 80 g", "MARINELA", "74000685"),
        ("Marisela gansito 50 gr", PLACEHOLDER_BRAND, "Pastel Marinela Gansito 1 Ud - 50 g", "MARINELA", "74000708"),
        # Coincidencias de la verificación live 2026-10-01.
        ("Bimbo pan blanco 720 g", "Bimbo", "Pan Blanco Bimbo Extra Grande - 720 g", "BIMBO", "7441029556773"),
        ("Delicia bacon 397g", "Delicia", "Tocino Delicia - 397 g", "DELICIA", "7421000915201"),
        ("McCormick mostaza 180g", PLACEHOLDER_BRAND, "Mostaza McCormick - 180 g", "MCCORMICK", "7411000204238"),
        ("Plenitud protect G/XG 8 und", "Plenitud", "Pañales para Adultos Plenitud Protect G/XG - 8 Unidades", "PLENITUD", "7751493006446"),
        ("Olitalia aceite de oliva di sansa 500ml", "Olitalia", "Aceite De Oliva Di Sansa 500 Ml", "OLITALIA", "8007150902996"),
    ],
)
def test_reconstructed_gtin_matches_other_retailer(comisariato, brand, other, other_brand, gtin) -> None:
    group = group_for(
        record("comisariato_los_andes:1", comisariato, brand, gtin),
        record("paiz:1", other, other_brand, gtin),
    )
    assert group.comparison_status == "ready", group.conflict_reasons
    assert group.supermarket_ids == ("comisariato_los_andes", "paiz")


def test_mismatched_name_is_excluded_by_name_guard() -> None:
    # Sintético: un código Comisariato que reconstruye el GTIN del tocino Delicia
    # pero nombra otro producto. El miembro sale; Walmart+Paiz siguen comparables.
    gtin = "7421000915201"
    group = group_for(
        record("comisariato_los_andes:1", "Delicia jamon de pavo 397g", "Delicia", gtin),
        record("walmart:1", "Tocino Delicia - 397 g", "Delicia", gtin),
        record("paiz:1", "Tocino Delicia - 397 g", "DELICIA", gtin),
    )
    assert group.comparison_status == "ready"
    assert group.supermarket_ids == ("paiz", "walmart")
    (excluded_id, reasons), = group.excluded_members
    assert excluded_id == "comisariato_los_andes:1"
    assert "sku_gtin_name_disagreement" in reasons


def test_contradictory_brand_is_excluded() -> None:
    gtin = "7411000204238"
    group = group_for(
        record("comisariato_los_andes:1", "Naturas mostaza 180g", "Naturas", gtin),
        record("walmart:1", "Mostaza McCormick - 180 g", "McCormick", gtin),
        record("paiz:1", "Mostaza McCormick - 180 g", "MCCORMICK", gtin),
    )
    assert group.supermarket_ids == ("paiz", "walmart")
    assert dict(group.excluded_members)["comisariato_los_andes:1"] == ("sku_gtin_brand_conflict",)


def test_size_conflict_still_blocks_reconstructed_gtin() -> None:
    gtin = "7441029556773"
    group = group_for(
        record("comisariato_los_andes:1", "Bimbo pan blanco 480 g", "Bimbo", gtin),
        record("paiz:1", "Pan Blanco Bimbo Extra Grande - 720 g", "BIMBO", gtin),
    )
    assert group.comparison_status == "review_required"
    assert "cross_source_presentation_conflict" in group.conflict_reasons
