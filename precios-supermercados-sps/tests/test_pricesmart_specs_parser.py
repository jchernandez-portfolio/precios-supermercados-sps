"""Parser de especificaciones PriceSmart contra fixtures SINTÉTICOS.

Ningún HTML de este archivo es crudo de PriceSmart: reproducen la estructura de
texto visible observada el 2026-10-01 (ver el comentario del fixture Breadco) y
variantes de marcado plausibles. La primera corrida real guarda HTML crudo de una
muestra para endurecer el parser contra el DOM verdadero.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from precios_supermercados.product_homologation import SourceProductRecord, resolve_presentation
from precios_supermercados.scrapers.pricesmart_specs import (
    PARSER_VERSION,
    PriceSmartSpecsError,
    build_product_url,
    parse_number,
    parse_product_page,
    spec_fingerprint,
)

FIXTURE = Path(__file__).parent / "fixtures" / "pricesmart_specs" / "SYNTHETIC-breadco-415586.html"


def breadco() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def page(
    spec_block: str,
    *,
    pid: str = "500100",
    title: str = "Producto sintético",
    info: str | None = None,
    item_number: str | None = None,
    scripts: str = "",
    canonical: bool = False,
) -> str:
    """Página SINTÉTICA mínima con la misma anatomía visible que la ficha real."""

    number = pid if item_number is None else item_number
    info_html = (
        f"<h3>Información del producto</h3><p>{info}</p>" if info is not None else ""
    )
    link = (
        f'<link rel="canonical" href="https://www.pricesmart.com/es-hn/producto/x-{pid}/{pid}">'
        if canonical else ""
    )
    item_html = f"<div><span>Número de ítem</span> <span>{number}</span></div>" if number else ""
    return f"""<!DOCTYPE html><html><head>{link}</head><body>
    <nav aria-label="breadcrumb"><a>Inicio</a> › <a>Alimentos</a> › <a>Bebidas</a></nav>
    <h1>{title}</h1>{item_html}
    <section><h2>Detalles de producto y especificaciones</h2>{info_html}{spec_block}</section>
    {scripts}</body></html>"""


def test_synthetic_breadco_page_yields_observed_values() -> None:
    result = parse_product_page(breadco(), "415586")
    assert result.status == "parsed"
    assert result.extraction_method == "dom_structured"
    specs = result.specs
    assert specs is not None
    assert specs["parser_version"] == PARSER_VERSION
    assert specs["item_number"] == "415586"
    assert specs["title"] == "Breadco Pastelito de Piña 16 Unidades"
    assert specs["brand"] == "Breadco"
    assert specs["category_path"] == ["Alimentos", "Panadería y repostería"]
    assert specs["net_weight"] == {
        "value_g": "1450", "raw": "1.4500 kg", "unit": "kg",
        "imperial_derived": False, "source": "dom_structured",
    }
    assert specs["unit_weight"]["value_g"] == "90.63"
    assert specs["unit_weight"]["raw"] == "90.6300 g"
    assert specs["pack_count"] == 16
    assert specs["imported_or_national"] == "nacional"
    assert specs["origin_country"] == "Honduras"
    assert specs["storage"] == "Lugar fresco y seco"
    assert specs["allergens"] == ["Leche", "Huevos", "Trigo"]
    assert specs["trans_fat_free"] is False
    assert specs["net_volume"] is None
    assert specs["consistency"] == {
        "pack_x_unit_vs_net": "consistent",
        "product_information_vs_specifications_net_weight": "consistent",
    }
    assert specs["conflicts"] == []
    assert specs["presentation_hint"] == "16 x 90.63 g"
    assert "Peso Neto: 1.45 kg / 3.2 lb" in specs["product_information"]
    assert specs["ingredients"].startswith("Harina de trigo")
    raw_labels = [entry["label"] for entry in specs["raw_specifications"]]
    assert raw_labels == [
        "Almacenamiento", "Peso de la unidad (cada uno)", "Peso neto",
        "Libre de grasas trans", "Cantidad de paquetes (recuentos)",
        "Importado o Nacional", "Alérgenos", "Marca",
    ]


def test_item_number_is_never_treated_as_gtin_and_related_products_do_not_leak() -> None:
    specs = parse_product_page(breadco(), "415586").specs
    assert specs is not None
    # El __NEXT_DATA__ sintético trae un producto relacionado (pid 400001) con
    # "Peso neto 680 g" y un gtin: ninguno pertenece a la ficha pedida.
    assert specs["gtin"] is None
    assert specs["gtin_candidates"] == []
    assert specs["net_weight"]["value_g"] == "1450"
    assert all("680" not in json.dumps(entry) for entry in specs["raw_specifications"])


def test_presentation_hint_parses_in_homologation_engine() -> None:
    specs = parse_product_page(breadco(), "415586").specs
    record = SourceProductRecord(
        source_record_id="pricesmart:1",
        supermarket_id="pricesmart",
        source_name="Pastelito sintético",
        source_presentation=specs["presentation_hint"],
    )
    signature, status = resolve_presentation(record)
    assert status == "source_only"
    assert signature.dimension == "mass_g"
    assert signature.pack_count == 16
    assert signature.total_base == Decimal("1450.08")


def test_definition_list_variant_with_volume_in_ml() -> None:
    html = page(
        """<h3>Especificaciones</h3><dl>
        <dt>Volumen</dt><dd>946.0000 ml</dd>
        <dt>Marca</dt><dd>Sula</dd>
        <dt>Alérgenos</dt><dd>Leche</dd>
        <dt>Color</dt><dd>Blanco</dd>
        </dl>""",
        title="Sula Leche Entera",
    )
    result = parse_product_page(html, "500100")
    assert result.status == "parsed"
    specs = result.specs
    assert specs["net_volume"]["value_ml"] == "946"
    assert specs["net_weight"] is None
    assert specs["brand"] == "Sula"
    assert specs["allergens"] == ["Leche"]
    assert specs["presentation_hint"] == "946 ml"
    assert {"label": "Color", "value": "Blanco"} in specs["raw_specifications"]
    assert specs["category_path"] == ["Alimentos", "Bebidas"]


def test_table_variant_with_contenido_neto_in_liters() -> None:
    html = page(
        """<h4>Especificaciones</h4><table>
        <tr><th>Contenido neto</th><td>2 L</td></tr>
        <tr><th>Importado o Nacional</th><td>Importado</td></tr>
        <tr><th>Cantidad de paquetes (recuentos)</th><td>6.0000</td></tr>
        </table>""",
    )
    specs = parse_product_page(html, "500100").specs
    assert specs["net_volume"]["value_ml"] == "2000"
    assert specs["net_volume"]["raw"] == "2 L"
    assert specs["imported_or_national"] == "importado"
    assert specs["pack_count"] == 6
    assert specs["presentation_hint"] == "2000 ml"


def test_pound_only_net_weight_is_converted_and_flagged_imperial() -> None:
    html = page(
        """<h3>Especificaciones</h3><div>
        <div><span>Peso neto</span><span>3.0000 lb</span></div>
        <div><span>Marca</span><span>Member's Selection</span></div></div>""",
        info="Carne molida | Peso Neto: 3 lb | Hecho en Nicaragua.",
    )
    specs = parse_product_page(html, "500100").specs
    assert specs["net_weight"]["value_g"] == "1360.777"
    assert specs["net_weight"]["unit"] == "lb"
    assert specs["net_weight"]["imperial_derived"] is True
    assert specs["origin_country"] == "Nicaragua"
    assert specs["consistency"]["product_information_vs_specifications_net_weight"] == "consistent"
    assert specs["presentation_hint"] == "1360.777 g"


def test_information_text_prefers_metric_side_of_dual_label() -> None:
    html = page(
        """<h3>Especificaciones</h3><div>
        <div><span>Marca</span><span>Kirkland Signature</span></div></div>""",
        info="Almendras | Peso Neto: 1.36 kg / 3 lb | Hecho en Estados Unidos.",
    )
    specs = parse_product_page(html, "500100").specs
    assert specs["net_weight"]["value_g"] == "1360"
    assert specs["net_weight"]["source"] == "product_information"
    assert specs["consistency"]["product_information_vs_specifications_net_weight"] == "info_only"
    assert specs["origin_country"] == "Estados Unidos"


def test_missing_specifications_block_yields_none() -> None:
    html = page("<h3>Descripción</h3><p>Sin bloque de especificaciones.</p>", info="Peso Neto: 500 g")
    result = parse_product_page(html, "500100")
    assert result.status == "no_specifications"
    assert result.specs is None


def test_heading_without_recognized_labels_is_not_a_spec_block() -> None:
    html = page("<h3>Especificaciones</h3><div><span>Color</span><span>Rojo</span></div>")
    result = parse_product_page(html, "500100")
    assert result.status == "no_specifications"
    assert result.specs is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1.4500", Decimal("1.4500")),
        ("90.6300", Decimal("90.6300")),
        ("1,5", Decimal("1.5")),
        ("1.234,56", Decimal("1234.56")),
        ("1,234.56", Decimal("1234.56")),
        ("1,450", None),  # ¿miles o decimal? no se adivina
        ("1.450.000", None),
        ("0", None),
        ("abc", None),
    ],
)
def test_decimal_parsing_is_fail_closed(text: str, expected: Decimal | None) -> None:
    assert parse_number(text) == expected


def test_odd_decimals_normalize_and_ambiguous_values_are_not_invented() -> None:
    html = page(
        """<h3>Especificaciones</h3><div>
        <div><span>Peso neto</span><span>0.4540 kg</span></div>
        <div><span>Peso de la unidad (cada uno)</span><span>1,450 g</span></div>
        <div><span>Cantidad de paquetes (recuentos)</span><span>2.5000</span></div></div>""",
    )
    specs = parse_product_page(html, "500100").specs
    assert specs["net_weight"]["value_g"] == "454"
    assert specs["unit_weight"] is None  # "1,450 g" es ambiguo
    assert specs["pack_count"] is None  # conteo no entero
    assert {"label": "Peso de la unidad (cada uno)", "value": "1,450 g"} in specs["raw_specifications"]
    assert specs["presentation_hint"] == "454 g"


def test_pack_times_unit_mismatch_is_flagged_and_not_used_as_multipack() -> None:
    html = page(
        """<h3>Especificaciones</h3><div>
        <div><span>Peso neto</span><span>1.4500 kg</span></div>
        <div><span>Peso de la unidad (cada uno)</span><span>50.0000 g</span></div>
        <div><span>Cantidad de paquetes (recuentos)</span><span>16</span></div></div>""",
    )
    specs = parse_product_page(html, "500100").specs
    assert specs["consistency"]["pack_x_unit_vs_net"] == "mismatch"
    assert "pack_count:pack_x_unit_vs_net_weight" in specs["conflicts"]
    assert specs["presentation_hint"] == "1450 g"


def test_information_vs_specifications_conflict_blocks_net_weight_hint() -> None:
    html = page(
        """<h3>Especificaciones</h3><div>
        <div><span>Peso neto</span><span>2.0000 kg</span></div></div>""",
        info="Peso Neto: 1.45 kg / 3.2 lb",
    )
    specs = parse_product_page(html, "500100").specs
    assert "net_weight:specifications_vs_product_information" in specs["conflicts"]
    assert specs["presentation_hint"] is None


def test_flat_text_fallback_and_inline_labels() -> None:
    html = page(
        """<h3>Especificaciones</h3><div class="flat">
        <span>Almacenamiento</span><span>Refrigerado</span>
        <span>Alérgenos</span><span>Soya</span><span>Leche</span>
        <span>Peso neto: 500 g</span>
        <span>Marca</span><span>Delicia</span></div>""",
    )
    result = parse_product_page(html, "500100")
    assert result.extraction_method == "dom_text"
    specs = result.specs
    assert specs["storage"] == "Refrigerado"
    assert specs["allergens"] == ["Soya", "Leche"]
    assert specs["net_weight"]["value_g"] == "500"
    assert specs["brand"] == "Delicia"


def test_identity_mismatch_when_visible_item_number_is_other_product() -> None:
    result = parse_product_page(breadco(), "415587")
    assert result.status == "identity_mismatch"
    assert result.specs is None


def test_identity_unverified_without_any_evidence() -> None:
    html = page("<h3>Especificaciones</h3><dl><dt>Marca</dt><dd>X</dd></dl>", item_number="")
    result = parse_product_page(html, "500100")
    assert result.status == "identity_unverified"
    assert result.specs is None
    canonical = page(
        "<h3>Especificaciones</h3><dl><dt>Marca</dt><dd>X</dd></dl>", item_number="", canonical=True,
    )
    assert parse_product_page(canonical, "500100").status == "parsed"


def test_json_ld_gtin_is_captured_only_with_valid_gs1_check_digit() -> None:
    ld = {
        "@context": "https://schema.org", "@type": "Product", "name": "Sula Leche",
        "sku": "500100", "gtin13": "7421000915201", "brand": {"@type": "Brand", "name": "Sula"},
    }
    html = page(
        "<h3>Especificaciones</h3><dl><dt>Marca</dt><dd>Sula</dd></dl>",
        scripts=f'<script type="application/ld+json">{json.dumps(ld)}</script>',
    )
    specs = parse_product_page(html, "500100").specs
    assert specs["gtin"] == "07421000915201"
    assert specs["gtin_candidates"] == [{
        "source": "json_ld:gtin13", "raw": "7421000915201", "valid_gs1": True,
        "canonical": "07421000915201",
    }]
    assert "json_ld_sku" in specs["identity_evidence"]

    ld["gtin13"] = "7421000915202"  # check digit inválido
    html = page(
        "<h3>Especificaciones</h3><dl><dt>Marca</dt><dd>Sula</dd></dl>",
        scripts=f'<script type="application/ld+json">{json.dumps(ld)}</script>',
    )
    specs = parse_product_page(html, "500100").specs
    assert specs["gtin"] is None
    assert specs["gtin_candidates"][0]["valid_gs1"] is False


def test_json_ld_sku_of_other_product_is_identity_mismatch() -> None:
    ld = {"@context": "https://schema.org", "@type": "Product", "sku": "999999"}
    html = page(
        "<h3>Especificaciones</h3><dl><dt>Marca</dt><dd>Sula</dd></dl>",
        item_number="",
        scripts=f'<script type="application/ld+json">{json.dumps(ld)}</script>',
    )
    assert parse_product_page(html, "500100").status == "identity_mismatch"


def test_embedded_next_data_pairs_for_same_pid_and_conflict_with_dom() -> None:
    data = {"props": {"pageProps": {"product": {
        "pid": "500100",
        "specifications": [
            {"name": "Peso neto", "value": "500 g"},
            {"name": "Alérgenos", "value": ["Maní", "Soya"]},
            {"name": "gtin", "value": "x"},
        ],
        "related": [{"pid": "777777", "attributes": [{"name": "Marca", "value": "Otra"}]}],
    }}}}
    script = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'
    only_json = page("<p>sin bloque visible</p>", scripts=script)
    result = parse_product_page(only_json, "500100")
    assert result.status == "parsed"
    assert result.extraction_method == "embedded_json"
    assert result.specs["net_weight"]["value_g"] == "500"
    assert result.specs["allergens"] == ["Maní", "Soya"]
    assert result.specs["brand"] is None  # la marca "Otra" es de otro pid
    assert "embedded_json_pid" in result.specs["identity_evidence"]

    agreeing = page(
        "<h3>Especificaciones</h3><dl><dt>Peso neto</dt><dd>0.5000 kg</dd></dl>", scripts=script,
    )
    result = parse_product_page(agreeing, "500100")
    assert result.specs["net_weight"]["value_g"] == "500"
    assert result.specs["attribute_sources"]["net_weight"] == "embedded_json+dom"

    conflicting = page(
        "<h3>Especificaciones</h3><dl><dt>Peso neto</dt><dd>750 g</dd></dl>", scripts=script,
    )
    specs = parse_product_page(conflicting, "500100").specs
    assert specs["net_weight"] is None
    assert "net_weight:embedded_json_vs_dom" in specs["conflicts"]


def test_parser_never_raises_on_garbage() -> None:
    assert parse_product_page("", "1").status == "parse_failed"
    assert parse_product_page("<<<>>><div><dt>", "1").status in {"identity_unverified", "parse_failed"}
    assert parse_product_page("<script>{bad json</script><h1>x</h1>", "1").specs is None


def test_build_product_url_and_fingerprint() -> None:
    assert build_product_url("breadco-pastelito-de-pina-16-unidades-415586", "415586") == (
        "https://www.pricesmart.com/es-hn/producto/breadco-pastelito-de-pina-16-unidades-415586/415586"
    )
    for slug, pid in (("../x", "1"), ("ok", "01"), ("ok", "abc"), ("a b", "1")):
        with pytest.raises(PriceSmartSpecsError):
            build_product_url(slug, pid)
    specs = parse_product_page(breadco(), "415586").specs
    assert spec_fingerprint(specs) == spec_fingerprint(json.loads(json.dumps(specs)))
    assert len(spec_fingerprint(specs)) == 64
