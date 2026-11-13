"""Medidas del nombre que el motor leía mal: medio galón, .75 oz, dosis y tamaños imposibles."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "exportar_consumer_catalog.py"
SPEC = importlib.util.spec_from_file_location("exportar_consumer_catalog_name_measures", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def offer(name, dimension, total, presentation=None, product_type="Abarrotes"):
    return MODULE.VisibleOffer(
        source_product_id="walmart:1", supermarket_id="walmart", location_id="walmart_sps", product_name=name,
        brand=None, presentation=presentation, current_price_minor=10000, reported_regular_price_minor=None,
        is_promotion=False, availability="in_stock", observed_at="2026-10-16T00:00:00Z", canonical_product_id=None,
        category="Alimentos", product_type=product_type, presentation_dimension=dimension,
        presentation_total_base=total, presentation_status="confirmed", comparison_status="individual",
        master_group_key=None, source_category=None,
    )


def shown(item):
    fixed = MODULE.fix_name_measures(item)
    return MODULE._derived_presentation(fixed), fixed.presentation_dimension, fixed.presentation_total_base


def test_half_gallon_and_leading_dot_ounces() -> None:
    assert shown(offer("MAGIA BLANCA CloroRegular 1/2G", "mass_g", "0.5")) == ("1.893 L", "volume_ml", "1892.71")
    assert shown(offer("Badia albahaca .75 oz", "mass_g", "2126.214")) == ("0.75 oz", "ounce", "0.75")


def test_dose_and_impossible_sizes_use_the_count_or_hide_the_size() -> None:
    assert shown(offer("Dramamine 50 mg caja 24 tabletas", "mass_g", "0.05")) == ("24 unidades", "count", "24")
    assert shown(offer("Fitamin Regulación del Sueño 60 Gomas", "mass_g", "181437")) == ("60 unidades", "count", "60")
    assert shown(offer("Vitamina D 1000 UI", "mass_g", "0.025")) == (None, None, None)
    # Sin conteo: se publica tal cual para que la app lo corrija o lo esconda.
    assert shown(offer("Gaseosa Sprite 1100 Lt Pet", "volume_ml", "1100000"))[1:] == ("volume_ml", "1100000")


def test_plausible_and_heavy_items_are_untouched() -> None:
    assert shown(offer("Harina Maseca 1 kg", "mass_g", "1000"))[2] == "1000"
    assert shown(offer("Estación de Fuerza Capacidad 100 kg", "mass_g", "100000"))[2] == "100000"
    assert shown(offer("Vitamina C 500 mg 100 tabletas", "count", "100"))[2] == "100"


def test_dose_with_count_uses_the_count_even_when_the_total_looks_plausible() -> None:
    # PriceSmart: 180 cápsulas de 400 mg salían como 72 g (L 7,221 por kg).
    assert shown(offer("Now Magnesio 180 Cápsulas / 400 mg / 0.01 oz", "mass_g", "72")) == (
        "180 unidades", "count", "180")


def source_only(name, dimension, total):
    return MODULE._core.replace(offer(name, dimension, total), presentation_status="source_only")


def test_source_weight_against_a_name_count_is_not_shown() -> None:
    # Paiz publica en su campo un peso que el nombre contradice.
    assert shown(source_only("Protectores Diarios Saba Largos - 40 piezas", "mass_g", "3")) == (None, None, None)
    assert shown(source_only("Rasuradoras Desechables Equate 2 Hojas Mango Reforzado Antidesliz - 15Pzas",
                             "mass_g", "415")) == (None, None, None)
    # "N unidades" del nombre es el contenido (conteo), no el peso del súper.
    assert shown(source_only("Tapete Antifatiga 12 Unidades", "mass_g", "270")) == ("12 unidades", "count", "12")


def test_name_count_with_a_measure_or_confirmed_size_is_untouched() -> None:
    assert shown(source_only("Vasos altos Mainstays de vidrio-4 pzas/490 ml", "volume_ml", "490"))[1:] == (
        "volume_ml", "490")
    assert shown(source_only("Jabón Neutro Skin Surtido 6 Pack - 660 g", "mass_g", "660"))[1:] == ("mass_g", "660")
    assert shown(offer("Cepillo Dental Colgate 360 - 3 Pzas", "mass_g", "61"))[1:] == ("mass_g", "61")
    assert shown(source_only("Ambiental Pinito Fresa 1 Pack", "mass_g", "45"))[1:] == ("mass_g", "45")
    assert shown(source_only("Vela Numero 1", "mass_g", "450"))[1:] == ("mass_g", "450")
