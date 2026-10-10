"""Regla V "otra presentación" y su publicación aditiva en el catálogo B2C."""
from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from precios_supermercados.matching import size_variants as sv  # noqa: E402

SCRIPT = ROOT / "scripts" / "exportar_consumer_catalog.py"
SPEC = importlib.util.spec_from_file_location("exportar_consumer_catalog_size_variants_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

OK_LABELS = {
    "brand": "exact",
    "variant": "agree",
    "name_diff": "none",
    "type": "same",
    "codes": "none",
    "size": "conflict",
    "pack": "same_single",
}


def labels(**changes: str) -> dict[str, str]:
    return {**OK_LABELS, **changes}


# ----------------------------------------------------------------- regla V


def test_rule_v_accepts_same_family_in_another_size() -> None:
    assert sv.rule_v(labels(), 0.9, "unknown") == "ok"
    assert sv.rule_v(labels(size="exact", pack="different"), 0.9, "unknown") == "ok"


@pytest.mark.parametrize(
    ("changes", "score", "gtin", "reason"),
    [
        ({}, 0.9, "same", "same_gtin"),
        ({}, 0.73, "unknown", "name_low"),
        ({"brand": "close"}, 0.9, "unknown", "brand"),
        ({"variant": "one_sided"}, 0.9, "unknown", "variant"),
        ({"name_diff": "both_sides"}, 0.9, "unknown", "name_diff"),
        ({"type": "type_conflict"}, 0.9, "unknown", "type"),
        ({"codes": "conflict"}, 0.9, "unknown", "codes"),
        ({"size": "close", "pack": "same_single"}, 0.9, "unknown", "same_size"),
    ],
)
def test_rule_v_rejections(changes: dict[str, str], score: float, gtin: str, reason: str) -> None:
    assert sv.rule_v(labels(**changes), score, gtin) == reason


# ----------------------------------------------------------------- guardas


def guard(a: str, b: str, *, dimension: str = "mass_g", total_a: float = 125, total_b: float = 750, **prices):
    return sv.guard(a, b, dimension=dimension, total_a=total_a, total_b=total_b, **prices)


def test_guard_accepts_plain_size_variant() -> None:
    assert guard("YES Yogurt Fresa 125g", "Yogurt Yes Sabor Fresa 750 Gr") is None


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("YES Yogurt Banano Fresa 125g", "Yes yogurt liquido banano fresa 750 gr"),
        ("Sula jugo naranja 236ml", "SULA JugoNaranjaPremium 1.89L"),
        ("MAGIA BLANCA Cloro Gel 500ml", "MAGIA BLANCA Cloro 1L"),
        ("Galleta Gamesa Chokis Rellena - 90 g", "Galleta Gamesa Chokis Mix - 93 g"),
        ("Chocolate Hersheys Con Leche 198 Gr", "Chocolate Hersheys Con Leche Y Almendras 192 Gr"),
    ],
)
def test_guard_rejects_variant_word_on_one_side(a: str, b: str) -> None:
    assert guard(a, b) == "variant_word"


def test_guard_liq_abbreviation_matches_liquido() -> None:
    assert guard("Ariel Deterg Liq Revita 2.8L", "Detergente Líquido Ariel Revitacolor 400 Ml") is None


def test_guard_rejects_different_diaper_sizes_and_egg_sizes() -> None:
    assert guard("Pañales Huggies Etapa 3/G - 56 Unidades", "Pañales Huggies Etapa 1/P - 40 Unidades") == "talla"
    assert guard("Pampers Easy Ups Talla 4 - 5 / 20", "Pampers Easy Ups Talla 3-4 / 24") == "talla"
    assert guard("Bonovo Huevo G 30 und", "Huevos Bonovo Cartón P 15Un") == "talla"
    assert guard("Huevos Bonovo Grande Caja 60Un", "Bonovo Huevos Extra Grandes 90 Unidades") == "talla"


def test_guard_keeps_same_diaper_size_formula_stage_and_egg_size() -> None:
    assert guard("PAMPERS BabyDry Talla6 21UN", "PAMPERS BabyDry Talla#6 32Und") is None
    assert guard("Fórmula Enfagrow promental etapa 3 - 1650 g", "Fórmula Enfagrow promental etapa 3 - 440 g") is None
    assert guard("RICA YEMA Huevos 15u", "RICA YEMA Huevos 30u") is None


def test_guard_rejects_objects_measured_by_capacity() -> None:
    assert guard("Hielera Portatil Ozark Trail 12 Litros", "Hielera Portatil Ozark Trail 16 Litros") == "object"
    assert guard("Mancuerna Athletic Works - 10 lb", "Mancuerna Athletic Works - 5 lb") == "object"


def test_guard_rejects_impossible_sizes_and_incoherent_unit_prices() -> None:
    assert guard("Ham 10 lb", "Ham 410 Lb", total_a=4535.92, total_b=185972.87) == "size_out_of_range"
    # 6 sobres a 20 g vs 6 sobres a 120 g: el "grande" sale 6.5x más barato por gramo.
    assert (
        guard("Nescafe Capuccino 6 Sobres", "Nescafe Cappuccino 6 Sobres", total_a=20, total_b=120,
              price_a=Decimal("117.50"), price_b=Decimal("90.00"))
        == "unit_price_incoherent"
    )
    # El grande no puede costar por unidad más del doble que el chico.
    assert (
        guard("Aceite 1 L", "Aceite 3 L", dimension="volume_ml", total_a=1000, total_b=3000,
              price_a=Decimal("50"), price_b=Decimal("400"))
        == "unit_price_incoherent"
    )
    assert (
        guard("Aceite 1 L", "Aceite 3 L", dimension="volume_ml", total_a=1000, total_b=3000,
              price_a=Decimal("50"), price_b=Decimal("130"))
        is None
    )


def test_parse_price() -> None:
    assert sv.parse_price("12.50") == Decimal("12.50")
    assert sv.parse_price("0") is None
    assert sv.parse_price(None) is None
    assert sv.parse_price("abc") is None


# ------------------------------------------------- motor real (pocos productos)


def item(key: str, sm: str, name: str, brand: str | None, presentation: str, price: str) -> sv.SizeVariantItem:
    return sv.SizeVariantItem(
        key=key, supermarket_id=sm, name=name, brand=brand, presentation=presentation,
        category="Bebidas", price=Decimal(price),
    )


def test_size_variant_links_with_real_engine() -> None:
    items = [
        item("a", "la_colonia", "Refresco Pepsi 500 Ml", "Pepsi", "500 ml", "20.00"),
        item("b", "walmart", "Gaseosa Pepsi - 3 L", "Pepsi", "3 L", "62.00"),
        item("c", "colonial", "PEPSI Gaseosa 500ml", "Pepsi", "500 ml", "21.00"),
        item("d", "walmart", "Gaseosa Mirinda Naranja - 3 L", "Mirinda", "3 L", "60.00"),
    ]
    links, diagnostics = sv.size_variant_links(items)
    pairs = {frozenset((link.key_a, link.key_b)) for link in links}
    assert frozenset(("a", "b")) in pairs
    assert frozenset(("c", "b")) in pairs
    assert frozenset(("a", "c")) not in pairs  # mismo tamaño: es el mismo producto
    assert not any("d" in pair for pair in pairs)  # otra marca
    assert diagnostics["links"] == len(links)
    link = next(link for link in links if {link.key_a, link.key_b} == {"a", "b"})
    assert link.dimension == "volume_ml"
    assert link.ratio == pytest.approx(6.0)


def test_size_variant_links_rejects_duplicate_keys_and_handles_tiny_input() -> None:
    assert sv.size_variant_links([]) == ([], {"items": 0})
    duplicate = item("a", "walmart", "Pepsi 1 L", "Pepsi", "1 L", "30")
    with pytest.raises(ValueError, match="size_variant_duplicate_key"):
        sv.size_variant_links([duplicate, duplicate])


# ---------------------------------------------------- publicación en el catálogo


def offer(source_id: str, supermarket: str, name: str, presentation: str, total: str, price_minor: int, **extra):
    location = f"{supermarket}_sps"
    return MODULE.VisibleOffer(
        source_product_id=source_id,
        supermarket_id=supermarket,
        location_id=location,
        product_name=name,
        brand="Pepsi",
        presentation=presentation,
        current_price_minor=price_minor,
        reported_regular_price_minor=None,
        is_promotion=False,
        availability=extra.get("availability", "in_stock"),
        observed_at="2026-10-09T12:00:00Z",
        canonical_product_id=None,
        category="Bebidas",
        product_type="Gaseosa",
        presentation_dimension="volume_ml",
        presentation_total_base=total,
        presentation_status="confirmed",
        comparison_status="unmapped",
        source_category="Bebidas",
    )


FRESH = {(sm, f"{sm}_sps"): "FRESH" for sm in ("la_colonia", "colonial", "walmart", "pricesmart", "comisariato_los_andes")}


def test_build_rows_publishes_other_presentations_both_ways() -> None:
    small = offer("la_colonia:1", "la_colonia", "Refresco Pepsi 500 Ml", "500 ml", "500", 2000)
    large = offer("walmart:2", "walmart", "Gaseosa Pepsi - 3 L", "3 L", "3000", 6200)
    rows = {row["product_name"]: row for row in MODULE.build_rows((small, large), FRESH)}
    small_row, large_row = rows["Refresco Pepsi 500 Ml"], rows["Gaseosa Pepsi - 3 L"]
    [to_large] = small_row["other_presentations"]
    assert to_large["row_id"] == large_row["row_id"]
    assert to_large["presentation"] == large_row["presentation"]
    assert to_large["best_price"] == "62.00"
    assert to_large["large_size"] is True  # 6x más grande
    [to_small] = large_row["other_presentations"]
    assert to_small["row_id"] == small_row["row_id"]
    assert to_small["large_size"] is False
    # Aditivo: identidad y ofertas intactas.
    assert small_row["comparability"] != "comparable"
    assert len(small_row["offers"]) == 1


def test_rows_without_links_have_no_field() -> None:
    single = offer("la_colonia:1", "la_colonia", "Refresco Pepsi 500 Ml", "500 ml", "500", 2000)
    [row] = MODULE.build_rows((single,), FRESH)
    assert "other_presentations" not in row


def _rows_and_index():
    offers = [
        offer(f"walmart:{n}", "walmart", f"Pepsi {n} ml", f"{n} ml", str(n), n * 4)
        for n in (355, 500, 1000, 1500, 3000)
    ]
    index = {(item.source_product_id, item.location_id): item for item in offers}
    rows = [
        {
            "row_id": f"row-{item.presentation_total_base}",
            "product_name": item.product_name,
            "presentation": item.presentation,
            "offers": [{
                "source_product_id": item.source_product_id,
                "location_id": item.location_id,
                "current_price": f"{item.current_price_minor / 100:.2f}",
                "availability": "in_stock",
                "unit_price": {"amount": "1.00", "per": "100 ml"},
            }],
        }
        for item in offers
    ]
    return rows, index


def test_attach_orders_by_size_caps_and_flags_large(monkeypatch) -> None:
    rows, index = _rows_and_index()
    totals = {f"row-{n}": float(n) for n in (355, 500, 1000, 1500, 3000)}

    def fake_links(items):
        keys = [entry.key for entry in items]
        links = [
            sv.SizeVariantLink(a, b, "volume_ml", totals[a], totals[b], 0.9)
            for i, a in enumerate(keys) for b in keys[i + 1:]
        ]
        return links, {"links": len(links)}

    monkeypatch.setattr(MODULE, "MAX_OTHER_PRESENTATIONS", 3)
    summary = MODULE.attach_other_presentations(rows, index, links_function=fake_links)
    assert summary["links"] == 10 and summary["rows_with_other_presentations"] == 5
    first = rows[0]["other_presentations"]
    # 355 ml: las 3 más cercanas (500, 1000, 1500), ordenadas por tamaño.
    assert [entry["row_id"] for entry in first] == ["row-500", "row-1000", "row-1500"]
    assert [entry["large_size"] for entry in first] == [False, False, False]
    last = rows[-1]["other_presentations"]
    assert [entry["row_id"] for entry in last] == ["row-500", "row-1000", "row-1500"]
    assert all(entry["best_unit_price"] == {"amount": "1.00", "per": "100 ml"} for entry in last)


def test_attach_failure_never_blocks_the_catalog(capsys) -> None:
    rows, index = _rows_and_index()

    def broken(items):
        raise RuntimeError("boom")

    summary = MODULE.attach_other_presentations(rows, index, links_function=broken)
    assert summary["status"] == "error" and summary["error_type"] == "RuntimeError"
    assert all("other_presentations" not in row for row in rows)
    assert "other_presentations" in capsys.readouterr().err
