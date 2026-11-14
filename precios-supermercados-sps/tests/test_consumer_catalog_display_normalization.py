from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from precios_supermercados.consumer_catalog_display import multipack

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "exportar_consumer_catalog.py"
SPEC = importlib.util.spec_from_file_location("exportar_consumer_catalog_display_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def offer(
    *,
    name: str,
    brand: str | None,
    presentation: str | None,
    dimension: str | None,
    total: str | None,
    status: str = "confirmed",
    product_type: str | None = "Huevo",
    category: str | None = "Alimentos",
    availability: str = "in_stock",
) -> object:
    return MODULE.VisibleOffer(
        source_product_id="colonial:1",
        supermarket_id="colonial",
        location_id="colonial_sps",
        product_name=name,
        brand=brand,
        presentation=presentation,
        current_price_minor=10000,
        reported_regular_price_minor=None,
        is_promotion=False,
        availability=availability,
        observed_at="2026-09-10T12:00:00Z",
        canonical_product_id=None,
        category=category,
        product_type=product_type,
        presentation_dimension=dimension,
        presentation_total_base=total,
        presentation_status=status,
        comparison_status="unmapped",
    )


def test_count_aliases_collapse_to_one_consumer_presentation() -> None:
    for raw in ("30 Un", "30Un", "30 unidades", "30 UND", "30u"):
        item = offer(
            name=f"Huevos Bonovo Cartón M {raw}",
            brand="Bonovo",
            presentation=raw,
            dimension="count",
            total="30",
        )
        assert MODULE._derived_presentation(item) == "30 unidades"


def test_mass_alias_is_normalized_without_changing_quantity() -> None:
    item = offer(
        name="Tallarin de Huevo Long Life 400 Gr",
        brand="LONG LIFE",
        presentation="400 Gr",
        dimension="mass_g",
        total="400",
        product_type="Pasta",
    )
    assert MODULE._derived_presentation(item) == "400 g"


def test_egg_grade_g_before_und_is_not_interpreted_as_grams() -> None:
    item = offer(
        name="Norteño Huevos 30G UND",
        brand="RMS",
        presentation=None,
        dimension="mass_g",
        total="30",
        status="name_only",
    )
    assert MODULE._derived_presentation(item) == "30 unidades"
    rows = MODULE.build_rows(
        (item,),
        {("colonial", "colonial_sps"): "FRESH"},
    )
    assert rows[0]["brand"] == "Norteño"
    assert rows[0]["presentation"] == "30 unidades"
    assert rows[0]["variant"] == "Grande"


def test_shell_egg_pack_is_normalized_as_count() -> None:
    item = offer(
        name="Nutri yema huevo grande 60 pack",
        brand="Marca COMANDES",
        presentation=None,
        dimension=None,
        total=None,
        status="missing",
    )
    rows = MODULE.build_rows(
        (item,),
        {("colonial", "colonial_sps"): "FRESH"},
    )
    assert rows[0]["brand"] == "Nutri Yema"
    assert rows[0]["presentation"] == "60 unidades"
    assert rows[0]["variant"] == "Grande"


def test_egg_beaters_placeholder_is_recovered_into_consumer_egg_taxonomy() -> None:
    item = offer(
        name="EGG Beaters Wht 16 oz",
        brand="RMS",
        presentation=None,
        dimension=None,
        total=None,
        status="missing",
        product_type=None,
        category=None,
    )
    rows = MODULE.build_rows(
        (item,),
        {("colonial", "colonial_sps"): "FRESH"},
    )
    assert len(rows) == 1
    assert rows[0]["brand"] == "Egg Beaters"
    assert rows[0]["category"] == "Alimentos"
    assert rows[0]["product_type"] == "Huevo"
    assert rows[0]["presentation"] == "16 oz"
    assert rows[0]["offers"][0]["current_price"] == "100.00"


def test_out_of_stock_only_row_is_not_visible_in_compra_inteligente() -> None:
    item = offer(
        name="Claras De Huevo Egg Beaters 16 Oz",
        brand="Egg Beaters",
        presentation="16 Oz",
        dimension="ounce",
        total="16",
        availability="out_of_stock",
    )
    rows = MODULE.build_rows(
        (item,),
        {("colonial", "colonial_sps"): "FRESH"},
    )
    assert rows == []


def test_placeholder_brand_is_recovered_only_from_explicit_alias() -> None:
    assert MODULE.canonical_brand("RMS", "Bonovo Huevo G 30 und") == "Bonovo"
    assert MODULE.canonical_brand("Marca COMANDES", "Nutri yema huevos 30 unds") == "Nutri Yema"
    assert MODULE.canonical_brand("RMS", "Producto sin marca reconocible 30 und") is None


def test_unique_explicit_name_brand_overrides_conflicting_source_brand_for_display() -> None:
    assert (
        MODULE.canonical_brand(
            "DON CRISTOBAL",
            "Huevo de Gallina Marketside Jumbo - 20 Unidades",
        )
        == "Marketside"
    )
    assert (
        MODULE.canonical_brand(
            "MARKETSIDE",
            "Huevo de Gallina Don Cristobal Marron - 15 Unidades",
        )
        == "Don Cristobal"
    )


def test_non_generic_source_brand_is_kept_without_an_explicit_conflicting_alias() -> None:
    assert MODULE.canonical_brand("DON CRISTOBAL", "Huevo de Gallina Marron - 15 Unidades") == "Don Cristobal"
    assert MODULE.canonical_brand("Marca Propia", "Producto sin marca reconocible 30 und") == "Marca Propia"


def test_brand_case_and_accent_aliases_are_canonical() -> None:
    pairs = {
        "KRAFT": "Kraft",
        "Kraft": "Kraft",
        "REGIA": "Regia",
        "Regia": "Regia",
        "MCCORMICK": "McCormick",
        "McCormick": "McCormick",
        "NORTENO": "Norteño",
        "NORTEÑO": "Norteño",
        "MARKETSIDE": "Marketside",
        "GREAT VALUE": "Great Value",
        "HELLMANN'S": "Hellmann's",
        "Hellmanns": "Hellmann's",
    }
    for raw, expected in pairs.items():
        assert MODULE.canonical_brand(raw, f"Producto {raw}") == expected


def test_egg_sizes_are_separate_from_pack_count() -> None:
    cases = {
        "Huevos Bonovo Cartón P 30Un": "Pequeño",
        "Huevos Bonovo Cartón M 30Un": "Mediano",
        "Bonovo Huevo G 30 und": "Grande",
        "Norteño Huevos 30XL UND": "Extra grande",
        "Bonovo jumbo huevos carton 10 und": "Jumbo",
        "Nutri yema huevo grande 60 pack": "Grande",
    }
    for name, expected in cases.items():
        assert MODULE.canonical_egg_size(name, "Huevo") == expected
        assert MODULE.canonical_presentation(
            source_presentation=None,
            product_name=name,
            product_type="Huevo",
            presentation_dimension=None,
            presentation_total_base=None,
            presentation_status="missing",
        ).endswith(" unidades")


def test_non_shell_egg_names_do_not_trigger_grade_override() -> None:
    assert MODULE.canonical_presentation(
        source_presentation="20 g",
        product_name="KINDER Huevo Sorpresa Niño 20g",
        product_type="Huevo",
        presentation_dimension="mass_g",
        presentation_total_base="20",
        presentation_status="confirmed",
    ) == "20 g"
    assert MODULE.canonical_presentation(
        source_presentation="1774 ml",
        product_name="Kraft Mayonesa de Huevos de Gallina Libres 2 Unidades / 887 mL / 30 oz",
        product_type="Huevo",
        presentation_dimension="volume_ml",
        presentation_total_base="1774",
        presentation_status="confirmed",
    ) == "1.774 L"


def test_common_unmodeled_unit_is_normalized_without_becoming_unknown() -> None:
    assert MODULE.canonical_presentation(
        source_presentation="1 Gl",
        product_name="Mayonesa Country Best 1 Gl",
        product_type="Mayonesa",
        presentation_dimension=None,
        presentation_total_base=None,
        presentation_status="missing",
    ) == "1 gal"


# ------------------------------------------------- pack = [conteo, "total"] (propuesta 4)

PACK_FRESH = {(sm, f"{sm}_sps"): "FRESH" for sm in ("la_colonia", "colonial", "walmart", "pricesmart")}


def pack_offer(supermarket: str = "pricesmart", number: int = 1, **fields: object) -> object:
    base = offer(
        # "4x92 g": medida explícita por envase; sin mediana del tipo basta el nombre.
        name="Snack Pack Pudín de Chocolate 4x92 g",
        brand="Snack Pack",
        presentation="4 × 92 g",
        dimension="mass_g",
        total="368",
        status="name_only",
        product_type="Postre",
    )
    return replace(
        base,
        **{
            "source_product_id": f"{supermarket}:{number}",
            "supermarket_id": supermarket,
            "location_id": f"{supermarket}_sps",
            **fields,
        },
    )


def test_multipack_returns_count_and_total_only_for_accepted_arithmetic() -> None:
    def pack(display: str | None, dimension: str | None, total: str | None, status: str = "confirmed") -> object:
        return multipack(
            source_presentation=display,
            presentation_dimension=dimension,
            presentation_status=status,
            presentation_total_base=total,
        )

    assert pack("4 × 92 g", "mass_g", "368", "name_only") == [4, "368 g"]
    assert pack("6 × 200 ml", "volume_ml", "1200") == [6, "1.2 L"]
    assert pack("2 × 7 oz", "ounce", "14") == [2, "14 oz"]
    assert pack("25 × 453.59 g", "mass_g", "11339.75") == [25, "11.34 kg"]
    # Sin pack: un solo envase, conteo, aritmética que no cuadra o estado excluido.
    assert pack("368 g", "mass_g", "368") is None
    assert pack("1 × 368 g", "mass_g", "368") is None
    assert pack("12 unidades", "count", "12") is None
    assert pack("4 × 92 g", "mass_g", "92") is None
    assert pack("4 × 92 g", "volume_ml", "368") is None
    for status in ("ambiguous_multipack", "conflict", "source_only", "name_preferred_source_conflict", "missing"):
        assert pack("4 × 92 g", "mass_g", "368", status) is None


def test_build_rows_adds_pack_without_touching_presentation_order_or_unit_price() -> None:
    [row] = MODULE.build_rows((pack_offer(),), PACK_FRESH)
    [ambiguous] = MODULE.build_rows((pack_offer(presentation_status="confirmed", presentation="368 g"),), PACK_FRESH)
    assert row["pack"] == [4, "368 g"]
    assert row["presentation"] == "4 × 92 g"
    assert "pack" not in ambiguous and ambiguous["presentation"] == "368 g"
    # El precio por unidad sale del mismo total con o sin pack.
    assert row["offers"][0]["unit_price"] == ambiguous["offers"][0]["unit_price"]


def test_build_rows_omits_pack_for_single_sizes_and_excluded_states() -> None:
    single = pack_offer(presentation="368 g", product_name="Pudín 368 g")
    conflict = pack_offer(number=2, presentation_status="conflict", product_name="Pudín B 4 Unidades / 92 g")
    ambiguous = pack_offer(number=3, presentation_status="ambiguous_multipack", product_name="4 Pack Pudding - 368 g")
    rows = MODULE.build_rows((single, conflict, ambiguous), PACK_FRESH)
    assert len(rows) == 3 and all("pack" not in row for row in rows)


def test_build_rows_omits_pack_when_a_group_member_contradicts_it() -> None:
    def member(supermarket: str, number: int, **fields: object) -> object:
        return pack_offer(supermarket, number, canonical_product_id="gtin:pudin", comparison_status="ready", **fields)

    agree = (member("pricesmart", 1), member("walmart", 2, presentation="368 g", presentation_status="confirmed"))
    [row] = MODULE.build_rows(agree, PACK_FRESH)
    assert row["comparability"] == "comparable" and row["pack"] == [4, "368 g"]

    other_count = (member("pricesmart", 1), member("walmart", 2, presentation="2 × 184 g"))
    [row] = MODULE.build_rows(other_count, PACK_FRESH)
    assert row["comparability"] == "comparable" and "pack" not in row

    other_size = (
        member("pricesmart", 1),
        member("walmart", 2, presentation=None, product_name="4 Pack Pudding - 92 g", presentation_status="ambiguous_multipack"),
    )
    [row] = MODULE.build_rows(other_size, PACK_FRESH)
    assert "pack" not in row


def test_build_rows_drops_pack_when_the_row_is_sold_by_pound() -> None:
    weighed = pack_offer("walmart", 7, product_name="Pudín de chocolate")
    MODULE.SOURCE_POUND.add("walmart:7")
    try:
        [row] = MODULE.build_rows((weighed,), PACK_FRESH)
    finally:
        MODULE.SOURCE_POUND.discard("walmart:7")
    assert row["presentation"] == "por libra" and "pack" not in row


# Nombres reales del catálogo público (SPS/TGU, 13-nov-2026).
def test_pack_name_kind_accepts_only_explicit_per_unit_measures() -> None:
    kind = MODULE.pack_name_kind
    assert kind("Red Bull Bebida Energizante Vitaliza Mente y Cuerpo 6 Unidades / 250 mL", 6) == "unidades"
    assert kind("Progreso Arroz Blanco 25 Unidades / 1 lb", 25) == "unidades"
    assert kind("Enterex Diabetex Suplemento Nutricional 16 Unidades/ 237 mL", 16) == "unidades"
    assert kind("Doraditas queso 21 grx12 pack", 12) == "cada_uno"
    assert kind("Cheetos crunchy extra queso 28 grx12 pac", 12) == "cada_uno"
    assert kind("Nabisco oreo vainilla 12x36gr", 12) == "cada_uno"
    assert kind("GLADE Ae Lavanda 12X400 ML", 12) == "cada_uno"
    assert kind("Maggi gallina mi sazon 4/10 g", 4) == "cada_uno"
    assert kind("Barra Desodorante Old Spice Fresh 2 Uds - 50 g c/u", 2) == "cada_uno"
    assert kind("Pasta Dental Oral-B 3D White Blanqueadora - 2 Uds 67 ml c/u", 2) == "cada_uno"
    # X puede ser el total, no es contenido o el conteo no es un paquete.
    assert kind("Galletas de Vainilla Pozuelo con relleno sabor a Vainilla -12 uds/265g", 12) is None
    assert kind("Galleta Chiky Pozuelo - 12uds/ 80 g", 12) is None
    assert kind("Paquetes de Palitos de burbujas 3 Uds - 29 ml", 3) is None
    assert kind("Gancho Adhesivo Negro 3u X 1kg", 3) is None
    assert kind("Member's Selection Aceite para Motor SAE 15W-40 / 4.73 L", 40) is None
    assert kind("Kern's Salsa de Tomate 1 000 Unidades / 8 g / 0.28 oz", 1000) is None
    assert kind("Gillette ... en Gel Cool Wave5 Unidades / 107 mL / 3.8 oz", 5) is None
    assert kind("Nabisco oreo vainilla 12x36gr", 6) is None  # otro conteo


def _priced_row(name: str, presentation: str, unit: str | None, per: str = "100 g", **fields: object) -> dict:
    row = {
        "product_name": name,
        "presentation": presentation,
        "product_type": "Galletas",
        "category": "Alimentos",
        "offers": [{"current_price": "100.00", "availability": "in_stock",
                    "unit_price": {"amount": unit, "per": per} if unit else None}],
    }
    if " × " in presentation:
        row["pack"] = [int(presentation.split(" × ")[0]), "total"]
    return {**row, **fields}


def test_pack_verdict_keeps_only_unit_prices_that_read_as_per_unit() -> None:
    """Mediana de "Galletas" 110/100 g: un total N veces mayor da un precio N veces menor."""
    medians = {
        ("product_type", "Galletas", "100 g"): Decimal("110"),
        ("product_type", "Bebida energizante", "100 ml"): Decimal("1.00"),
    }
    rows = {
        # (a) por envase: el precio por unidad está cerca del de su tipo.
        "redbull": _priced_row("Red Bull Bebida Energizante 6 Unidades / 250 mL", "6 × 250 ml", "0.90", "100 ml",
                               product_type="Bebida energizante", category="Alimentos"),
        "oreo": _priced_row("Nabisco oreo vainilla 12x36gr", "12 × 36 g", "35.00"),
        "bonobon": _priced_row("ARCOR Bon o ChocoFresa 5x15gr", "5 × 15 g", "128.00"),
        # (b) X es el total: 12× o 30× más barato que la mediana.
        "pozuelo": _priced_row("Galletas de Vainilla Pozuelo -12 uds/265g", "12 × 265 g", "4.00"),
        "snickers": _priced_row("Snickers, Twix, M&M's 30 Unidades / 1.5 kg / 3.3 lb", "30 × 1.5 kg", "2.44"),
        # (b) r = 0.27 con 8 envases: más cerca de "475 g en total" (r × 8 = 2.2) que de 3.8 kg.
        "brioche": _priced_row("Breadco Pan Brioche para Hot Dog 8 Unidades / 475 g", "8 × 475 g", "30.00"),
        # (b) formato PriceSmart sin mediana de su tipo ni de su categoría.
        "sin_mediana": _priced_row("Niagara Almidón en Aerosol 4 Unidades / 567 g / 20 oz", "4 × 567 g", "9.00",
                                   product_type="Almidón", category="Limpieza"),
        # (c) capacidad del envase o conteo que el nombre no declara como paquete.
        "vasos": _priced_row("Kirkland Signature Vasos Desechables 240 Unidades / 532 mL / 18 oz", "240 × 532 ml", "4.00"),
        "termo": _priced_row("Manna Termo para Bebida en Acero Inoxidable 2 Unidades / 1.18 L / 40 oz", "2 × 1.18 L", "90.00"),
        "kerns": _priced_row("Kern's Salsa de Tomate 1 000 Unidades / 8 g / 0.28 oz", "1000 × 8 g", "35.00"),
    }
    verdicts = {key: MODULE.pack_verdict(row, medians) for key, row in rows.items()}
    assert verdicts == {
        "redbull": "a", "oreo": "a", "bonobon": "a",
        "pozuelo": "b", "snickers": "b", "brioche": "b", "sin_mediana": "b",
        "vasos": "c", "termo": "c", "kerns": "c",
    }
    # Sin mediana, un nombre con medida explícita por envase basta (Chiquita "2x7 oz").
    assert MODULE.pack_verdict(_priced_row("Chiquita pulpa de maracuya 2x7 oz", "2 × 7 oz", None), medians) == "a"


def test_confirm_packs_uses_type_then_category_medians_and_leaves_b_and_c_as_today() -> None:
    peers = [_priced_row(f"Galleta {index} 200 g", "200 g", str(100 + index)) for index in range(8)]
    other_type = [_priced_row(f"Cereal {index} 300 g", "300 g", str(100 + index), product_type="Cereal") for index in range(8)]
    medians = MODULE.unit_price_medians([*peers, *other_type])
    assert medians[("product_type", "Galletas", "100 g")] == Decimal("104")
    assert medians[("category", "Alimentos", "100 g")] == Decimal("104")
    assert ("product_type", "Cereal", "100 g") in medians
    oreo = _priced_row("Nabisco oreo vainilla 12x36gr", "12 × 36 g", "60.00")
    pozuelo = _priced_row("Galletas de Vainilla Pozuelo -12 uds/265g", "12 × 265 g", "4.00")
    # Sin tipo: compara con la mediana de su categoría (104): 30 × 1.5 kg es el total.
    snickers = _priced_row("Snickers 30 Unidades / 1.5 kg / 3.3 lb", "30 × 1.5 kg", "2.44", product_type=None)
    counts = MODULE.confirm_packs([*peers, *other_type, oreo, pozuelo, snickers])
    assert counts == {"a": 1, "b": 2, "c": 0}
    assert "pack" in oreo and "pack" not in pozuelo and "pack" not in snickers
    # (b) quedan como hoy: misma presentación, sin total.
    assert pozuelo["presentation"] == "12 × 265 g"


def test_build_rows_drops_pack_from_ambiguous_names_end_to_end() -> None:
    pozuelo = pack_offer(
        "walmart", 3, product_name="Galletas de Vainilla Pozuelo con relleno sabor a Vainilla -12 uds/265g",
        presentation="12 × 265 g", presentation_total_base="3180",
    )
    snickers = pack_offer(
        "pricesmart", 4, product_name="Snickers, Twix, M&M’s, Mix de Barras de Chocolate 30 Unidades / 1.5 kg / 3.3 lb",
        presentation="30 × 1500 g", presentation_total_base="45000",
    )
    rows = {row["product_name"][:8]: row for row in MODULE.build_rows((pozuelo, snickers), PACK_FRESH)}
    assert all("pack" not in row for row in rows.values())
    assert rows["Galletas"]["presentation"] == "12 × 265 g"
    assert rows["Snickers"]["presentation"] == "30 × 1.5 kg"
