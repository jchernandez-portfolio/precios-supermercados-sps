"""Paso 4b: subcategoría por palabras clave del nombre (2026-10-09)."""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import exportar_consumer_catalog_core as core  # noqa: E402
from precios_supermercados import master_taxonomy as mt  # noqa: E402

TAX = mt.load()
RULES = mt.load_name_rules()


def sub(name: str, within: mt.Node | None = None) -> str | None:
    node = mt.name_subcategory(name, within)
    return None if node is None else node.subcategory


def test_rules_file_is_valid_and_points_to_tree_subcategories() -> None:
    assert len(RULES) >= 90
    for rule in RULES:
        assert (rule.node.department, rule.node.category, rule.node.subcategory) in TAX.paths
        assert rule.keywords
    with mt.NAME_RULES_PATH.open(encoding="utf-8", newline="") as handle:
        header = next(csv.reader(handle))
    assert header == ["department", "category", "subcategory", "keywords", "excludes"]


def test_invalid_rule_node_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "rules.csv"
    path.write_text("department,category,subcategory,keywords,excludes\nAlimentos,Abarrotes,No existe,x,\n", encoding="utf-8")
    with pytest.raises(mt.MasterTaxonomyError, match="name_rule_node_invalid"):
        mt.load_name_rules.__wrapped__(path)


ALIMENTOS = mt.Node("Alimentos")
ROPA = mt.Node("Ropa, calzado y accesorios")


@pytest.mark.parametrize(
    ("name", "within", "expected"),
    [
        ("Construct Camisa Manga Corta para Hombre", ROPA, "Hombre"),
        ("Dalia Pantalón Corte Recto para Mujer", ROPA, "Mujer"),
        ("Disney Set de Pijama para Niña 4 Piezas", ROPA, "Niñas"),
        ("Pekkle Conjunto para Niño 4 Piezas", ROPA, "Niños"),
        ("Perry Ellis Tenis Casuales con Cordones para Hombre", ROPA, "Calzado"),
        ("SM CAMARON 21-25 Fresco X LB", ALIMENTOS, "Pescados y mariscos"),
        ("Dicarne Salchicha Mañanera 12 onz", ALIMENTOS, "Embutidos y carnes frías"),
        ("Progcarne Chuleta Corte Central Fresca", ALIMENTOS, "Cerdo"),
        ("Arrachera Fresca Caja", ALIMENTOS, "Res"),
        ("Planters salted peanuts 52 oz", ALIMENTOS, "Frutos secos y fruta deshidratada"),
        ("Nabisco chips ahoy 9.5 oz", ALIMENTOS, "Galletas"),
        ("CASA & CAMPO Tajadas Natur192g", ALIMENTOS, "Papas, frituras y boquitas"),
        ("BADIA Canela En Polvo 0.50oz", ALIMENTOS, "Especias y condimentos"),
        ("Mokra Crema De Maní 8Oz", ALIMENTOS, "Untables, mermeladas y miel"),
        ("Member's Selection Set de Sábanas 6 Piezas King", None, "Sábanas, edredones y cobijas"),
        ("Member's Selection Ángel Decorativo Navideño con Luces LED", None, "Decoración de temporada"),
    ],
)
def test_name_rules_assign_subcategory(name: str, within: mt.Node | None, expected: str) -> None:
    assert sub(name, within) == expected


def test_glued_colonial_names_are_split() -> None:
    assert mt.name_text("SULA JugoNaranjaPremium") == " sula jugo naranja premium "
    assert sub("SCOTCHB EsponjaCocina LimpProf", mt.Node("Limpieza")) == "Accesorios de limpieza"


def test_excludes_and_branch_compatibility() -> None:
    # "Pollo" en una sopa o consomé no es carne de pollo.
    assert sub("Sopa Maggi de Pollo con Fideos", ALIMENTOS) != "Pollo y pavo"
    # Dulce de Halloween con peso no es decoración de temporada.
    assert sub("Halloween fun size 10.72 oz", None) != "Decoración de temporada"
    # La regla sólo aplica dentro de la rama que dio la equivalencia.
    assert sub("Construct Camisa Manga Corta para Hombre", ALIMENTOS) is None
    assert sub("Producto sin palabras clave", ALIMENTOS) is None
    assert sub("", None) is None


def test_assign_offer_refines_only_shallow_assignments() -> None:
    # Colonial "Carnes y Refrigerados" es equivalencia a nivel departamento.
    shallow = mt.assign_offer("colonial", "Carnes y Refrigerados", None, product_name="ZAMORANO Chorizo Parrill 890g")
    assert shallow.node == mt.Node("Alimentos", "Carnes, aves y mariscos", "Embutidos y carnes frías")
    assert shallow.source == "crosswalk+name" and shallow.crosswalk_level == "department"
    # Sin nombre el comportamiento es el de siempre (homologación, onzas).
    assert mt.assign_offer("colonial", "Carnes y Refrigerados", None).node == mt.Node("Alimentos")
    # PriceSmart "Hogar" se decide por nombre (by_name): queda en Hogar y cocina.
    by_name = mt.assign_offer("pricesmart", "Hogar", None, product_name="Member's Selection Set de Sábanas 6 Piezas King")
    assert by_name.source == "name" and by_name.node.subcategory == "Sábanas, edredones y cobijas"
    # Una equivalencia a nivel subcategoría nunca se reemplaza.
    deep_key = next(key for key, entry in TAX.crosswalk.items() if entry.level == "subcategory")
    deep = mt.assign_offer(deep_key[0], deep_key[1], None, product_name="Camisa para Hombre")
    assert deep.node == TAX.crosswalk[deep_key].node
    # Excluidos siguen excluidos.
    excluded = mt.assign_offer("pricesmart", "Tarjetas de Regalo", None, product_name="Tarjeta para Hombre")
    assert excluded.source == "excluded"


def test_public_assignment_uses_product_name() -> None:
    item = core.VisibleOffer(
        source_product_id="colonial:1", supermarket_id="colonial", location_id="colonial_sps",
        product_name="ZAMORANO Chorizo Parrill 890g", brand=None, presentation=None, current_price_minor=1000,
        reported_regular_price_minor=None, is_promotion=False, availability="in_stock",
        observed_at="2026-10-08T10:00:00Z", canonical_product_id=None, category=None, product_type=None,
        presentation_dimension=None, presentation_total_base=None, presentation_status="missing",
        comparison_status="single_source", source_category="Carnes y Refrigerados",
    )
    assert mt.public_fields(core._public_assignment([item]).node) == ("Alimentos", "Embutidos y carnes frías")
