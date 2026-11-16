"""Multipacks de PriceSmart: "N Unidades / X" y "N Unidades X" con fuente = total.

PriceSmart publica el contenido total en la presentación ("11352 ml") y el
desglose en el nombre ("12 Unidades / 946 ml"). Antes ambos se leían como un
conflicto y el producto quedaba sin tamaño ni precio unitario.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from precios_supermercados.product_homologation import SourceProductRecord
from precios_supermercados.product_identity_v2 import resolve_presentation_v2


def resolve(name: str, presentation: str | None, supermarket: str = "pricesmart"):
    return resolve_presentation_v2(
        SourceProductRecord(
            source_record_id=f"{supermarket}:{name}",
            supermarket_id=supermarket,
            source_name=name,
            source_presentation=presentation,
        )
    )


@pytest.mark.parametrize(
    ("name", "presentation", "pack", "unit", "total"),
    [
        ("Sula Leche Entera 12 Unidades / 946 ml / 32 oz", "11352 ml", 12, "946", "11352"),
        ("Pepsi Regular 6 Unidades / 3 L", "18 L", 6, "3000", "18000"),
        ("Snickers Barras de Chocolate 48 Unidades / 52.7 g / 1.86 oz", "2529.6 g", 48, "52.7", "2529.6"),
        ("Bumble Bee Atún en Aceite 6 Unidades / 120 g / 4.2 oz", "720 g", 6, "120", "720"),
        ("Well's Sidra de Manzana 3 Unidades 750 mL", "2250 ml", 3, "750", "2250"),
        ("Gullón Barquillos de Vainilla 12 Unidades 60 g / 2.1 oz", "720 g", 12, "60", "720"),
    ],
)
def test_multipack_name_with_total_source_is_confirmed(name, presentation, pack, unit, total):
    signature, status = resolve(name, presentation)
    assert status == "confirmed"
    assert signature.pack_count == pack
    assert signature.unit_amount_base == Decimal(unit)
    assert signature.total_base == Decimal(total)


def test_count_without_separator_needs_the_source_to_confirm_the_total():
    # La fuente dice 750 ml: el "3 Unidades" no se convierte en 3 × 750 ml.
    signature, status = resolve("Sidra 3 Unidades 750 mL", "750 ml")
    assert status == "confirmed"
    assert (signature.pack_count, signature.total_base) == (1, Decimal("750"))


def test_total_that_does_not_match_the_breakdown_is_still_a_conflict():
    signature, status = resolve("Pepsi Regular 6 Unidades / 3 L", "12 L")
    assert (signature, status) == (None, "conflict")


def test_dual_label_single_unit_is_unchanged():
    signature, status = resolve("Hormel Tabla de Quesos 794 g / 28 oz", "794 g")
    assert status == "confirmed"
    assert (signature.pack_count, signature.total_base) == (1, Decimal("794"))


def test_zamorano_butter_three_pack_uses_the_total_weight_for_the_unit_price():
    # Caso real (captura PriceSmart SPS, 14-nov-2026): L 139.95, sin presentación en la fuente.
    from precios_supermercados.consumer_catalog_display import unit_price

    signature, status = resolve("Zamorano Mantequilla Crema 3 Unidades / 439 g / 15.5 oz", None)
    assert status == "name_only"
    assert (signature.pack_count, signature.unit_amount_base, signature.total_base, signature.dimension) == (
        3, Decimal("439"), Decimal("1317"), "mass_g")
    price = unit_price(price="139.95", presentation_dimension=signature.dimension,
                       presentation_total_base=str(signature.total_base), presentation_status=status,
                       reference=(1000, "kg"))
    assert price == {"amount": "106.26", "per": "kg"}  # 139.95 / 1.317 kg, no / 439 g
