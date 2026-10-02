"""Calidad de catálogo 2026-10-01: tipo de producto con contexto y ruta de categoría."""
from __future__ import annotations

import pytest

from precios_supermercados.product_homologation import SourceProductRecord
from precios_supermercados.product_identity_v2 import (
    _hard_conflicts,
    assign_taxonomy_v2,
    homologate_products_v2,
    is_source_category_type,
    profile_product_v2,
)


def product(
    name: str,
    *,
    supermarket: str = "walmart",
    category: str | None = None,
    record_id: str | None = None,
    barcode: str | None = None,
    presentation: str | None = None,
) -> SourceProductRecord:
    return SourceProductRecord(
        source_record_id=record_id or f"{supermarket}:{name}",
        supermarket_id=supermarket,
        source_name=name,
        source_category=category,
        barcode=barcode,
        source_presentation=presentation,
    )


def product_type(name: str, **kwargs) -> str | None:
    return assign_taxonomy_v2(product(name, **kwargs)).product_type


@pytest.mark.parametrize(
    "name",
    [
        "Aceite Quaker State Advdur 20w50gl",
        "Aceite Super Tech 15W40 mineral de alta calidad - 1 L",
        "Aceite Premiun Atf Tipo A Automatico",
        "Aceite para moto Shell Advance 20W50 - 1 L",
        "Aceite Pennzoil P Motordiesel 15W40 - 1 Qt",
        "Pintura Clasica de Aceite Corona Entinta - galon",
        "Plancha alisadora Remington Keratin Therapy con Aceite de Argán",
        "Aceite Nutritivo Bioland Argán Humectación y Brillo - 110 ml",
        "Aceite para bebé Johnson's Original -200 ml",
        "Spray Sin Enjuague Hask 5 En 1 Con Aceite De Argan 6 Oz",
        "Llave de filtro de aceite Auto Drive ajustable",
    ],
)
def test_non_food_oils_are_not_cooking_oil(name: str) -> None:
    assert product_type(name) != "Aceite comestible"


@pytest.mark.parametrize(
    "name",
    [
        "Aceite Clover Brand 1,400 ml",
        "Aceite De Coco Cococare Spray 141ml",
        "Aceite Mazola de Maíz - 1.5 L",
        "Ideal aceite sin colesterol 3.750 ltrs",
    ],
)
def test_cooking_oils_keep_their_type(name: str) -> None:
    assert product_type(name) == "Aceite comestible"


@pytest.mark.parametrize(
    "name",
    [
        "SENSODYNE Pasta Repara/Protege 100G",
        "COLGATE Pasta MaxWhite 160ml",
        "ORAL B Pasta Bicarbonato 150ML",
        "CREST Pasta OutlasUltra 178GR",
    ],
)
def test_toothpaste_named_pasta_is_pasta_dental(name: str) -> None:
    assert product_type(name, supermarket="colonial") == "Pasta dental"


def test_food_pasta_is_unchanged() -> None:
    assert product_type("Pasta Roland Orzo 17.6 Oz") == "Pasta"


@pytest.mark.parametrize(
    "name",
    [
        "SILK Almendra S/Azucar 946ml",
        "Hatsu tea s/azucar star fruit 400 ml",
        "Bebida Energizante Monster Energy Zero Azúcar 473Ml",
    ],
)
def test_sugar_free_claims_are_not_sugar(name: str) -> None:
    assert product_type(name) != "Azúcar"


def test_sugar_free_soda_keeps_soda_type() -> None:
    assert product_type("Pepsi Gaseosa en Lata Zero Azúcar 24 Unidades / 355 mL / 12 oz") == "Refresco"
    assert product_type("Azúcar Morena Enerzucar + Estevia 450Gr") == "Azúcar"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("PURINA Felix Atun 85g", "Alimento para gato"),
        ("WHISKAS Pouch Atun 85gr", "Alimento para gato"),
        ("Alim Hum Dogui Dinner Ad Atun Lat 295 Gr", "Alimento para perro"),
        ("Atún Sardimar Trozos En Agua - 140 g", "Atún"),
    ],
)
def test_pet_food_with_fish_flavour_is_pet_food(name: str, expected: str) -> None:
    assert product_type(name) == expected


def test_hot_dog_bread_is_not_dog_food() -> None:
    assert product_type("Pan Bimbo para perro caliente 8 unidades") == "Pan"


@pytest.mark.parametrize(
    ("name", "unexpected"),
    [
        ("Taza de café Mainstays transparente de vidrio - 12 oz", "Café"),
        ("Sillón Mainstays reclinable color café", "Café"),
        ("Filtro Para Café Walton & Post 200 Un", "Café"),
        ("Gelatina Para Cabello Grisi Original 400 Ml", "Gelatina"),
        ("Gelatina Ego Power 250 Ml", "Gelatina"),
        ("Solución Sal Epsom Dteal Eucalipto Hierbabuena 3Lb", "Sal"),
    ],
)
def test_non_food_context_overrides_food_keyword(name: str, unexpected: str) -> None:
    assert product_type(name) != unexpected


def test_food_type_in_non_food_source_department_is_dropped() -> None:
    taxonomy = assign_taxonomy_v2(
        product(
            "Pintura Clasica de Aceite Corona Entinta Cuarto galon",
            category="/Artículos para el hogar/Pintura/Pinturas y Aerosoles/",
        )
    )
    assert taxonomy.product_type is None
    taxonomy = assign_taxonomy_v2(product("Café Molido Maya 400 g", category="/Ropa y Zapatería/Mujer/"))
    assert taxonomy.product_type is None


def test_source_category_path_fills_missing_type_as_weak_evidence() -> None:
    taxonomy = assign_taxonomy_v2(
        product("Crisco Original Spray 170 g", category="/Abarrotes/Aceites de cocina/Aceite Spray/")
    )
    assert taxonomy.product_type == "Aceite comestible"
    assert is_source_category_type(taxonomy)
    # El departamento solo llena la categoría pública, sin tipo.
    department_only = assign_taxonomy_v2(
        product("Papas Pringles Original 149 g", category="/Abarrotes/Snacks y Fruta Seca/Papas y Frituras/")
    )
    assert (department_only.category, department_only.product_type) == ("Alimentos", None)


def test_name_type_has_priority_over_category_path() -> None:
    taxonomy = assign_taxonomy_v2(
        product("Salsa de Tomate Naturas 106 g", category="/Abarrotes/Pastas y Salsas/Pastas/")
    )
    assert taxonomy.product_type == "Salsa"
    assert not is_source_category_type(taxonomy)


def test_category_type_never_creates_a_type_conflict() -> None:
    left = profile_product_v2(
        product("Crisco Original Spray 170 g", category="/Abarrotes/Aceites de cocina/Aceite Spray/"),
        brand_lexicon=frozenset(),
    )
    right = profile_product_v2(product("Margarina Crisco Original 170 g", supermarket="paiz"), brand_lexicon=frozenset())
    assert left.taxonomy.product_type == "Aceite comestible"
    assert right.taxonomy.product_type == "Margarina"
    assert "product_type_conflict" not in _hard_conflicts(left, right)


def test_same_gtin_group_is_not_lost_by_category_type() -> None:
    gtin = "00051500255162"
    result = homologate_products_v2(
        [
            product("Crisco Original Spray 170 g", category="/Abarrotes/Aceites de cocina/Aceite Spray/", record_id="w:1", barcode=gtin, presentation="170 g"),
            product("Manteca Crisco Original Spray 170 g", supermarket="paiz", record_id="p:1", barcode=gtin, presentation="170 g"),
        ]
    )
    (group,) = result.exact_gtin_groups
    assert group.comparison_status == "ready"


def test_misrooted_pharmacy_path_does_not_drop_food_type() -> None:
    taxonomy = assign_taxonomy_v2(
        product(
            "Chocolate Hershey's Cookies N Cream - 43 g",
            category="/Anthistaminicos/Dulces y Chocolates/Chocolates/",
        )
    )
    assert taxonomy.product_type == "Chocolate"


def test_wine_brand_with_cat_word_is_still_wine() -> None:
    assert product_type("Vino Gato Negro 9 Vidas Cabernet Sauv 750ml", supermarket="paiz") == "Vino"


def test_department_segment_does_not_type_the_leaf() -> None:
    taxonomy = assign_taxonomy_v2(
        product("Whisky Johnnie Walker green label - 750 ml", supermarket="paiz", category="/Cervezas, Vinos y Licores/Licores/Whisky/")
    )
    assert (taxonomy.category, taxonomy.product_type) == ("Bebidas", None)


def test_coffee_followed_by_brewing_method_is_coffee() -> None:
    assert product_type("Café Molido Bella Vista Tipo Percoladora 350 Gr", supermarket="la_colonia") == "Café"
    assert product_type("Percoladora de café Black & Decker de 30 Tazas") != "Café"


def test_seasonal_department_does_not_drop_food_type() -> None:
    taxonomy = assign_taxonomy_v2(
        product(
            "Bauducco Pan Dulce Panettone con Frutas Confitadas y Pasas 750 g",
            supermarket="pricesmart",
            category="Productos de temporada",
        )
    )
    assert taxonomy.product_type == "Pan"


@pytest.mark.parametrize(
    ("name", "category", "expected"),
    [
        ("Detergen Liq Xedex Multiaccion Dp 1800ml", "/Limpieza/Lavandería/Detergente Líquido/", "Detergente"),
        ("Deligurt Dospinos Bebible Fresa 750 ml", "/Lácteos/Yogurt/Yogurt Bebible/", "Yogurt"),
        ("Enjuague Buc Listerine Cool Mint - 500ml", "/Higiene y Belleza/Cuidado Bucal/Enjuague bucal/", "Enjuague bucal"),
        # Familias amplias u hojas que sólo contienen la palabra no tipan.
        ("Flan Sabemas Vainilla Con Caramelo - 130 g", "/Abarrotes/Azúcar y Postres/Gelatinas y Flan/", None),
        ("Mini Pizzas Great Value Congelada 3 Quesos - 848 g", "/Alimentos Congelados/Comida Fácil/Tacos, Pizzas y Pastas/", None),
        ("Spray Sellador Wet N Wild D Maquill 45ml", "/Higiene y Belleza/Cuidado Facial/Desmaquillante y agua micelar/", None),
    ],
)
def test_category_leaf_keyword_typing(name: str, category: str, expected: str | None) -> None:
    assert product_type(name, category=category) == expected
