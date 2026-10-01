"""Captura de GTIN por fuente: sólo barcodes explícitos y válidos llegan a ``ean``.

Hallazgos 2026-09-30 sobre fixtures y capturas versionadas en ``reports/``:

- Colonial: ``barcode`` es ``null`` en las 9,205 variantes de la captura completa
  2026-08-30; el código tipo UPC/EAN vive en ``sku`` (92% supera el check digit).
  Desde 2026-10-01 (aprobado por el usuario) el SKU GS1 válido es ``ean``; el SKU
  sigue en ``reference`` y la procedencia se deriva (``ean == reference``).
- PriceSmart: Bloomreach no expone barcode/GTIN/UPC; las variantes ``<pid>-<dígitos>``
  son SKU fuente (y no coinciden con GTIN de otras cadenas).
- Comisariato Los Andes: ``code`` es un código de material interno.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from precios_supermercados.scrapers.colonial import (
    declared_barcode,
    ean_source,
    ean_with_source,
    gtin_from_sku,
    parse_products,
)
from precios_supermercados.scrapers.comisariato_los_andes import parse_catalog_page
from precios_supermercados.scrapers.pricesmart import parse_documents

FIXTURES = Path(__file__).parent / "fixtures"


def _colonial_payload() -> dict:
    return json.loads((FIXTURES / "colonial/products-40.json").read_bytes())


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("070404001491", "070404001491"),  # UPC-A con cero inicial: se conserva literal
        ("7424634300146", "7424634300146"),  # EAN-13 Honduras
        (" 7424634300146 ", "7424634300146"),
        ("00070404001491", "00070404001491"),  # GTIN-14 con relleno
        ("96385074", "96385074"),  # EAN-8 válido
        ("7424634300147", None),  # check digit inválido
        ("70404001491", None),  # 11 dígitos: no es longitud GS1
        ("7400051", None),  # código interno corto
        ("ABC123456789", None),
        ("", None),
        (None, None),
        (7424634300146, None),  # tipo no textual
    ],
)
def test_colonial_declared_barcode_requires_valid_gs1(value, expected) -> None:
    assert declared_barcode(value) == expected


@pytest.mark.parametrize(
    ("sku", "expected"),
    [
        ("894700010144", "894700010144"),  # UPC-A Chobani
        (" 7424634300146 ", "7424634300146"),  # recortado
        ("070404001491", "070404001491"),  # cero inicial conservado
        ("75076870", "75076870"),  # EAN-8 Rexona
        ("00070404001491", "00070404001491"),
        ("7424634300147", None),  # check digit inválido
        ("70404001491", None),  # 11 dígitos
        ("123", None),  # código interno corto
        ("7424634300146A", None),  # no todo dígitos
        ("7424-634300146", None),
        ("", None),
        (None, None),
        (894700010144, None),
    ],
)
def test_colonial_sku_becomes_gtin_only_when_gs1_valid(sku, expected) -> None:
    assert gtin_from_sku(sku) == expected


def test_colonial_explicit_barcode_wins_over_sku_and_provenance_is_recorded() -> None:
    assert ean_with_source("070404001491", "894700010144") == ("070404001491", "barcode")
    assert ean_with_source(None, "894700010144") == ("894700010144", "sku_gs1_valid")
    assert ean_with_source("7424634300147", "894700010144") == ("894700010144", "sku_gs1_valid")
    assert ean_with_source(None, "7424634300147") == (None, None)
    assert ean_with_source(None, None) == (None, None)


def test_colonial_rows_keep_closed_schema_and_derive_provenance() -> None:
    payload = _colonial_payload()
    payload["products"][0]["variants"][0]["barcode"] = "070404001491"
    payload["products"][1]["variants"][0]["sku"] = "7424634300147"  # inválido
    rows = parse_products(json.dumps(payload).encode())
    assert rows[0]["ean"] == "070404001491" and ean_source(rows[0]) == "barcode"
    assert rows[0]["reference"] == "894700010144"
    assert rows[1]["ean"] is None and ean_source(rows[1]) is None
    assert rows[1]["reference"] == "7424634300147"
    assert all(ean_source(row) == "sku_gs1_valid" for row in rows[2:])
    assert all("ean_source" not in row for row in rows)  # contrato de snapshot cerrado


def test_colonial_fixture_gtins_all_come_from_sku() -> None:
    rows = parse_products((FIXTURES / "colonial/products-40.json").read_bytes())
    assert [row["ean"] for row in rows] == [row["reference"] for row in rows]
    assert {ean_source(row) for row in rows} == {"sku_gs1_valid"}


def test_pricesmart_composite_sku_suffix_is_not_promoted_to_ean() -> None:
    payload = json.loads((FIXTURES / "pricesmart/documents_6603.json").read_text())
    rows, _ = parse_documents(copy.deepcopy(payload["documents"]), "6603")
    composite = [row for row in rows if "-" in row["source_key"]]
    assert composite, "fixture debe cubrir variantes <pid>-<dígitos>"
    assert {row["source_key"].split("-", 1)[1] for row in composite} >= {"8000500142943"}
    assert all(row["ean"] is None for row in rows)


def test_comisariato_internal_material_code_is_never_ean() -> None:
    payload = json.loads(
        (FIXTURES / "comisariato_los_andes/catalog_page_sample.json").read_text(encoding="utf-8")
    )
    parsed = parse_catalog_page(payload, expected_skip=0, take=6)
    assert parsed["rows"]
    assert all(row["ean"] is None for row in parsed["rows"])
    assert all(row["reference"].startswith("0001-") for row in parsed["rows"])
