"""Medidas estandarizadas para el catálogo público (v2.7, aprobado 2026-10-09).

- Tamaño: g/ml bajo 1000, kg/L desde 1000 (hasta 3 decimales), paquetes como
  "12 × 946 ml".
- Precio unitario con referencia fija por subcategoría del árbol maestro
  (por 100 g/ml, por kg/L o por unidad), igual para todas las marcas.
- Tipo de onza (peso o líquida) desde el atributo ``ounce`` del árbol.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import exportar_consumer_catalog_core as core  # noqa: E402
from precios_supermercados import master_taxonomy as mt  # noqa: E402
from precios_supermercados.consumer_catalog_display import canonical_presentation, unit_price  # noqa: E402


def present(dimension, total, display=None, status="confirmed"):
    return canonical_presentation(
        source_presentation=display, product_name="Producto", product_type=None,
        presentation_dimension=dimension, presentation_total_base=total, presentation_status=status,
    )


@pytest.mark.parametrize(
    ("dimension", "total", "display", "expected"),
    [
        ("mass_g", "142", "142 g", "142 g"),
        ("mass_g", "1880", "1880 g", "1.88 kg"),
        ("mass_g", "1000", None, "1 kg"),
        ("volume_ml", "1774", None, "1.774 L"),
        ("volume_ml", "11352", "12 × 946 ml", "12 × 946 ml"),
        ("volume_ml", "18000", "6 × 3000 ml", "6 × 3 L"),
        ("mass_g", "2529.6", "48 × 52.7 g", "48 × 52.7 g"),
        ("count", "30", None, "30 unidades"),
    ],
)
def test_public_size_uses_one_format_for_every_store(dimension, total, display, expected):
    assert present(dimension, total, display) == expected


def test_multipack_display_must_match_the_total():
    # Un desglose que no cuadra con el total no se publica como paquete.
    assert present("volume_ml", "12000", "12 × 946 ml") == "12 L"


def test_unit_price_uses_the_same_reference_for_every_brand():
    reference = mt.unit_reference(mt.Node("Alimentos", "Abarrotes", "Enlatados y conservas"), "mass_g")
    assert reference == (100, "100 g")
    big = unit_price(price="249.70", presentation_dimension="mass_g", presentation_total_base="1880",
                     presentation_status="confirmed", reference=reference)
    small = unit_price(price="62.99", presentation_dimension="mass_g", presentation_total_base="142",
                       presentation_status="confirmed", reference=reference)
    assert big == {"amount": "13.28", "per": "100 g"}
    assert small == {"amount": "44.36", "per": "100 g"}


@pytest.mark.parametrize(
    ("node", "dimension", "expected"),
    [
        (mt.Node("Bebidas y tabaco", "Bebidas sin alcohol", "Gaseosas"), "volume_ml", (1000, "L")),
        (mt.Node("Alimentos", "Abarrotes", "Arroz, granos y legumbres"), "mass_g", (1000, "kg")),
        (mt.Node("Alimentos", "Abarrotes", "Especias y condimentos"), "mass_g", (100, "100 g")),
        (mt.Node("Cuidado personal y belleza", "Cuidado del cabello", "Shampoo"), "volume_ml", (100, "100 ml")),
        (mt.Node("Alimentos", "Lácteos y huevos", "Huevos"), "count", (1, "unidad")),
        (mt.Node("Alimentos", "Snacks y dulces"), "mass_g", (100, "100 g")),  # todas sus subcategorías por 100 g
        (None, "mass_g", (1000, "kg")),
        (mt.Node("Alimentos"), None, None),
    ],
)
def test_unit_reference_is_fixed_by_subcategory(node, dimension, expected):
    assert mt.unit_reference(node, dimension) == expected


def test_unit_price_needs_an_accepted_metric_presentation():
    reference = (1000, "kg")
    for dimension, status in (("ounce", "confirmed"), ("mass_g", "conflict"), ("mass_g", "missing")):
        assert unit_price(price="10.00", presentation_dimension=dimension, presentation_total_base="100",
                          presentation_status=status, reference=reference) is None
    assert unit_price(price="10.00", presentation_dimension="mass_g", presentation_total_base="0",
                      presentation_status="confirmed", reference=reference) is None


def test_tree_attributes_are_valid_and_cover_liquids_and_solids():
    tree = json.loads(mt.TREE_PATH.read_text(encoding="utf-8"))
    assert set(tree["attributes"]) == {"ounce", "unit_reference"}
    tax = mt.load()
    assert tax.attributes[("Bebidas y tabaco", "Bebidas alcohólicas", "Cerveza")]["ounce"] == "fluid"
    assert tax.attributes[("Alimentos", "Snacks y dulces", "Galletas")]["ounce"] == "mass"
    # Mixtas: sin atributo, la onza no se convierte.
    assert "ounce" not in tax.attributes[("Alimentos", "Abarrotes", "Salsas, aderezos y vinagres")]
    assert "ounce" not in tax.attributes[("Limpieza", "Lavandería", "Detergentes")]


def test_ounce_basis_from_supermarket_category():
    assert mt.ounce_basis("comisariato_los_andes", "LICORES", None) == "fluid"
    assert mt.ounce_basis("colonial", "Snack", None) == "mass"
    assert mt.ounce_basis("colonial", "Abarrotes", None) is None  # categoría mixta
    assert mt.ounce_basis("colonial", "Abarrotes", "Pasta") == "mass"


def test_exporter_publishes_unit_price_per_offer():
    offer = core.VisibleOffer(
        source_product_id="colonial:1", supermarket_id="colonial", location_id="colonial_sps",
        product_name="BUMBLE BEE Atun En Agua 142g", brand="Bumble Bee", presentation="142 g",
        current_price_minor=6299, reported_regular_price_minor=None, is_promotion=False,
        availability="in_stock", observed_at="2026-10-09T10:00:00Z", canonical_product_id=None,
        category=None, product_type="Atún", presentation_dimension="mass_g", presentation_total_base="142",
        presentation_status="confirmed", comparison_status="single_source", source_category="Abarrotes",
    )
    rows = core.build_rows([offer], {("colonial", "colonial_sps"): "FRESH"},
                           as_of_utc=core._parse_utc("2026-10-09T12:00:00Z", "x"))
    assert rows[0]["offers"][0]["unit_price"] == {"amount": "44.36", "per": "100 g"}
