"""Regla de onzas en grupos GTIN (v2.8): conversión dual, "nunca empeorar" y precio por unidad."""
from __future__ import annotations

from decimal import Decimal

from precios_supermercados.consumer_catalog_display import unit_price
from precios_supermercados.product_homologation import PresentationSignature
from precios_supermercados.product_homologation_persistence import build_homologation_rows, records_from_product_rows
from precios_supermercados.product_identity_v2 import converted_ounce, ounce_reading

ROWS = [
    # Kraft Ranch: 8 oz contra 237 ml → líquido (236.6 ml): listo y con precio por unidad.
    (1, "la_colonia", "Aderezo Kraft Ranch Classic 8 Oz", "Kraft", "8 Oz", "Aderezos", "0021000643592"),
    (2, "walmart", "Aderezo Kraft Ranch - 237 ml", "Kraft", None, "Aderezos", "0021000643592"),
    # Cholula: 150 ml + 5 oz contra 150 g + 150 g. Con la regla sería un empate 2 contra 2
    # (todo bloqueado); "nunca empeorar" mantiene la resolución de hoy (Walmart y Paiz listos).
    (3, "colonial", "CHOLULA Hot Chipotle 150 mL", "Cholula", None, "Salsas", "0049733830119"),
    (4, "comisariato_los_andes", "Cholula hot sauce chipotle 5 oz", "Cholula", "UN", "Salsas", "0049733830119"),
    (5, "walmart", "Salsa Cholula picante chipotle - 150 g", "Cholula", None, "Salsas", "0049733830119"),
    (6, "paiz", "Salsa Cholula picante chipotle - 150 g", "Cholula", "150 g", "Salsas", "0049733830119"),
    # Crema agria Daisy: 16 oz contra 454 g → peso (453.6 g).
    (7, "colonial", "DAISY Sour Cream Light 454g", "Daisy", None, "Lácteos", "0073420000158"),
    (8, "comisariato_los_andes", "Daisy light sour cream 16 oz", "Daisy", "UN", "Lácteos", "0073420000158"),
    # 16.9 oz contra 300 ml: ninguna lectura coincide → sigue bloqueado y sin convertir.
    (9, "comisariato_los_andes", "Lee kum kee salsa soya 16.9 oz", "Lee Kum Kee", "UN", "Salsas", "0078895128895"),
    (10, "walmart", "Salsa Lee Kum Kee Soya 300 ml", "Lee Kum Kee", None, "Salsas", "0078895128895"),
]


def profiles() -> dict[int, object]:
    rows = build_homologation_rows(records_from_product_rows(ROWS), updated_at_utc="2026-11-12T00:00:00Z")
    return {row.product_id: row for row in rows}


def sig(dimension: str, total: str, pack: int = 1) -> PresentationSignature:
    return PresentationSignature(dimension=dimension, total_base=Decimal(total), pack_count=pack,
                                 unit_amount_base=Decimal(total) / pack)


def test_ounce_reading_tries_fluid_and_weight_with_imperial_tolerance() -> None:
    assert ounce_reading(sig("ounce", "8"), sig("volume_ml", "237")) == "fluid"
    assert ounce_reading(sig("mass_g", "680"), sig("ounce", "24")) == "mass"
    assert ounce_reading(sig("ounce", "8"), sig("mass_g", "241")) is None  # 226.8 g: 6 %
    assert ounce_reading(sig("ounce", "16", 2), sig("volume_ml", "473")) is None  # otro paquete
    assert ounce_reading(sig("mass_g", "100"), sig("volume_ml", "100")) is None  # sin onzas no aplica
    assert ounce_reading(sig("ounce", "8"), None) is None


def test_matching_ounces_make_the_group_comparable_and_convert_the_ounce() -> None:
    rows = profiles()
    assert rows[1].comparison_status == rows[2].comparison_status == "ready"
    assert (rows[1].presentation_dimension, rows[1].normalized_unit, rows[1].display_presentation) == (
        "volume_ml", "ml", "236.59 ml")
    assert rows[1].raw_presentation == "8 Oz"  # la evidencia fuente se conserva
    assert rows[7].comparison_status == rows[8].comparison_status == "ready"
    assert (rows[8].presentation_dimension, rows[8].display_presentation) == ("mass_g", "453.59 g")


def test_never_worse_keeps_todays_resolution_for_ties() -> None:
    rows = profiles()
    assert rows[5].comparison_status == rows[6].comparison_status == "ready"
    assert rows[3].comparison_status == rows[4].comparison_status == "review_required"
    assert rows[4].presentation_dimension == "ounce"  # no se convierte fuera de un grupo comparable


def test_non_matching_ounces_stay_blocked_and_unconverted() -> None:
    rows = profiles()
    assert rows[9].comparison_status == rows[10].comparison_status == "review_required"
    assert rows[9].presentation_dimension == "ounce"


def test_converted_ounce_now_has_unit_price() -> None:
    row = profiles()[1]
    before = unit_price(price="45.00", presentation_dimension="ounce", presentation_total_base="8",
                        presentation_status=row.presentation_status, reference=(100, "100 ml"))
    after = unit_price(price="45.00", presentation_dimension=row.presentation_dimension,
                       presentation_total_base=row.presentation_total_base,
                       presentation_status=row.presentation_status, reference=(100, "100 ml"))
    assert before is None
    assert after == {"amount": "19.02", "per": "100 ml"}
    assert converted_ounce(sig("mass_g", "10"), "fluid") == sig("mass_g", "10")  # sólo onzas
