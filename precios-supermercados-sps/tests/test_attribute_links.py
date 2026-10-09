"""Regla A por atributos (engine_auto) para PriceSmart y Los Andes.

Aprobada 2026-10-09: 161/163 (98.8 %) en 387 pares etiquetados y revisión del
responsable 50/50. Vincula al maestro GTIN de OTRA cadena sólo cuando es el
mismo artículo (marca, nombre, variante, tamaño y paquete).
"""
from __future__ import annotations

import pytest

from precios_supermercados import product_master as pm
from precios_supermercados.matching import attribute_links as al
from precios_supermercados.product_homologation import SourceProductRecord
from precios_supermercados.product_homologation_persistence import build_homologation_rows


def ean13(body: str) -> str:
    digits = [int(char) for char in body]
    total = sum(digit * (3 if index % 2 else 1) for index, digit in enumerate(digits))
    return body + str((10 - total % 10) % 10)


GTIN_A = ean13("744100100001")
GTIN_B = ean13("744100100002")
GTIN_C = ean13("744100100003")


def build(*items: tuple[str, str, str | None, str | None, str | None]):
    """(supermarket, name, brand, presentation, barcode) → (products, members)."""
    products = []
    for index, (supermarket, name, brand, presentation, barcode) in enumerate(items, start=1):
        products.append(
            (
                index,
                SourceProductRecord(
                    source_record_id=f"{supermarket}:{index}",
                    supermarket_id=supermarket,
                    source_name=name,
                    source_brand=brand,
                    source_presentation=presentation,
                    barcode=barcode,
                ),
            )
        )
    rows = build_homologation_rows(products, updated_at_utc="2026-10-09T00:00:00Z")
    members = pm.member_profiles(rows, {pid: record.source_name for pid, record in products})
    return products, members


def links_for(*items):
    products, members = build(*items)
    links, diagnostics = al.engine_attribute_links(products, members)
    return {link.product_id: link for link in links}, diagnostics


def test_same_item_links_to_the_other_chain_gtin_master():
    # Nota: palabras extra relevantes ("Uht Bolsa") hacen que la regla no vincule
    # (name_diff mayor): la regla es conservadora a propósito.
    links, diagnostics = links_for(
        ("walmart", "Leche Semidescremada Leyde - 946 ml", "Leyde", "946 ml", GTIN_A),
        ("pricesmart", "Leyde Leche Semidescremada 946 ml", "Leyde", "946 ml", None),
    )
    link = links[2]
    assert link.link_method == "engine_auto" and link.decided_by == "engine:attribute-rule-a@1"
    assert link.master_product_id == pm.gtin_master_id(GTIN_A)
    assert link.evidence["rule"] == "attribute-rule-a" and link.evidence["partner"] == "walmart:1"
    # GTIN canónico de 14 dígitos (el mismo del maestro).
    assert link.evidence["primary_gtin"] == GTIN_A.zfill(14) and 0.74 <= link.confidence <= 1.0
    assert diagnostics["links"] == 1 and diagnostics["links_pricesmart"] == 1


@pytest.mark.parametrize(
    "target",
    [
        # Variante distinta (light vs regular).
        ("pricesmart", "Hellmann's Mayonesa Light 400 g", "Hellmann's", "400 g", None),
        # Otro tamaño: no es "mismo producto" (irá a "otras presentaciones").
        ("pricesmart", "Hellmann's Mayonesa 800 g", "Hellmann's", "800 g", None),
        # Paquete declarado en el nombre frente a una unidad suelta.
        ("pricesmart", "Hellmann's Mayonesa 12 Unidades 400 g", "Hellmann's", "400 g", None),
        # Marca propia: nunca se vincula por atributos.
        ("pricesmart", "Member's Selection Mayonesa 400 g", "Member's Selection", "400 g", None),
    ],
)
def test_rule_a_rejects_different_items(target):
    links, _ = links_for(("walmart", "Mayonesa Hellmann's Original - 400 g", "Hellmann's", "400 g", GTIN_A), target)
    assert links == {}


def test_two_valid_gtins_are_never_linked_by_attributes():
    links, _ = links_for(
        ("walmart", "Leche Semidescremada Leyde - 946 ml", "Leyde", "946 ml", GTIN_A),
        ("comisariato_los_andes", "Leyde leche semidescremada 946 ml", "Leyde", "946 ml", GTIN_B),
    )
    assert links == {}


def test_master_that_already_has_the_chain_is_not_touched():
    links, diagnostics = links_for(
        ("walmart", "Leche Semidescremada Leyde - 946 ml", "Leyde", "946 ml", GTIN_A),
        ("pricesmart", "Leyde Leche Semidescremada 946ml", "Leyde", "946 ml", GTIN_A),
        ("pricesmart", "Leyde Leche Semidescremada 946 ml", "Leyde", "946 ml", None),
    )
    assert 3 not in links
    assert diagnostics.get("target_already_multi_chain", 0) + diagnostics.get("master_has_same_chain", 0) >= 1


def test_two_products_of_one_chain_competing_for_a_master_link_neither():
    links, diagnostics = links_for(
        ("walmart", "Leche Semidescremada Leyde - 946 ml", "Leyde", "946 ml", GTIN_A),
        ("pricesmart", "Leyde Leche Semidescremada 946 ml", "Leyde", "946 ml", None),
        ("pricesmart", "Leyde Leche Semidescremada 946ml", "Leyde", "946 ml", None),
    )
    assert links == {}
    assert diagnostics["same_chain_contention"] == 2


def test_only_pricesmart_and_los_andes_are_targets():
    links, diagnostics = links_for(
        ("walmart", "Leche Semidescremada Leyde - 946 ml", "Leyde", "946 ml", GTIN_A),
        ("colonial", "LEYDE Leche Semidescremada 946ml", "Leyde", "946 ml", None),
        ("la_colonia", "Leche Leyde Semidescremada 946 Ml", "Leyde", "946 ml", GTIN_C),
    )
    assert links == {} and diagnostics == {"targets": 0}


def test_rule_a_thresholds():
    labels = {"brand": "exact", "name": "very_high", "name_diff": "none", "size": "exact", "pack": "same_single",
              "variant": "agree", "codes": "none", "type": "same_type"}
    assert al.rule_a_exact(labels, 0.80, "none")
    assert not al.rule_a_exact(labels, 0.73, "none")
    assert not al.rule_a_exact(labels, 0.95, "different")
    for field, value in (("variant", "one_sided"), ("name_diff", "both_minor"), ("size", "near"),
                         ("pack", "conflict"), ("type", "department_conflict")):
        assert not al.rule_a_exact({**labels, field: value}, 0.95, "none"), field


def test_declared_multipack_detection():
    assert al.declares_multipack("Barquillos 12 Unidades 60 g")
    assert al.declares_multipack("Tena lady normal 10unds")
    assert not al.declares_multipack("Pañal 1 unidad")
    assert not al.declares_multipack("Leche 946 ml")
