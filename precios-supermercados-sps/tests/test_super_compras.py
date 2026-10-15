"""Súper Compras: índice de búsqueda del catálogo v3 y contrato de la app estática."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from test_exportar_consumer_catalog import MODULE, build_db, export


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "super-compras"


def _search(output: Path) -> dict:
    return json.loads((output / "search.json").read_text(encoding="utf-8"))


def test_search_index_is_listed_with_hash_and_matches_visible_rows(tmp_path: Path) -> None:
    database = tmp_path / "source.sqlite"
    output = tmp_path / "public"
    build_db(database)
    manifest = export(database, output)

    on_disk = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert on_disk["search_file"] == manifest["search_file"] == "search.json"
    entry = next(item for item in on_disk["files"] if item["path"] == "search.json")
    assert entry["sha256"] == hashlib.sha256((output / "search.json").read_bytes()).hexdigest()

    search = _search(output)
    assert search["schema"] == MODULE.SEARCH_SCHEMA == "rpi-consumer-search/v1"
    assert search["columns"] == list(MODULE.SEARCH_COLUMNS)
    assert search["row_count"] == len(search["rows"]) == on_disk["visible_rows"]
    assert search["retailers"] == [item["supermarket_id"] for item in on_disk["scope"]]
    assert all(path.startswith("catalog/") for path in search["partitions"])
    ids = [row[0] for row in search["rows"]]
    assert len(set(ids)) == len(ids)


def test_search_row_carries_best_offer_retailers_promo_and_points_to_partition(tmp_path: Path) -> None:
    database = tmp_path / "source.sqlite"
    output = tmp_path / "public"
    build_db(database)
    export(database, output)
    search = _search(output)
    column = {name: index for index, name in enumerate(search["columns"])}
    milk = next(row for row in search["rows"] if row[column["product_name"]] == "Leche entera Sula 1 L")

    assert milk[column["best_price"]] == "10.00"
    assert milk[column["promo"]] == 1
    assert milk[column["comparability"]] == "c"
    assert sorted(search["retailers"][index] for index in milk[column["retailers"]]) == ["la_colonia", "walmart"]
    partition = json.loads((output / search["partitions"][milk[column["partition"]]]).read_text(encoding="utf-8"))
    row = next(item for item in partition["rows"] if item["row_id"].split("-", 1)[1] == milk[column["id"]])
    assert row["product_name"] == "Leche entera Sula 1 L"


def test_search_index_rejects_row_count_mismatch(tmp_path: Path) -> None:
    database = tmp_path / "source.sqlite"
    output = tmp_path / "public"
    build_db(database)
    manifest = export(database, output)
    manifest["visible_rows"] = int(manifest["visible_rows"]) + 1
    with pytest.raises(MODULE.ExportError, match="consumer_search_row_count_mismatch"):
        MODULE.attach_search_index(output, manifest)


def test_search_photo_column_comes_from_images_index(tmp_path: Path) -> None:
    database = tmp_path / "source.sqlite"
    output = tmp_path / "public"
    build_db(database)
    manifest = export(database, output)
    search = _search(output)
    column = {name: index for index, name in enumerate(search["columns"])}
    assert {row[column["photo"]] for row in search["rows"]} == {0}

    first, second = search["rows"][0][0], search["rows"][1][0]
    index = tmp_path / "index.json"
    index.write_text(json.dumps({"schema": "super-compras-product-images/v1", "items": {
        first: {"sig": "a", "v": 3, "sm": "walmart"}, second: {"sig": "b", "none": 1}, "zz": {"v": "x"},
    }}), encoding="utf-8")
    assert MODULE.load_photo_versions(index) == {first: 3}
    assert MODULE.load_photo_versions(tmp_path / "missing.json") == {}
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    assert MODULE.load_photo_versions(tmp_path / "bad.json") == {}
    MODULE._PHOTO_VERSIONS.update(MODULE.load_photo_versions(index))
    try:
        MODULE.attach_search_index(output, manifest)
    finally:
        MODULE._PHOTO_VERSIONS.clear()
    photos = {row[0]: row[column["photo"]] for row in _search(output)["rows"]}
    assert photos[first] == 3 and photos[second] == 0


def test_app_shell_is_static_safe_and_installable() -> None:
    html = (APP_DIR / "index.html").read_text(encoding="utf-8")
    assert "<title>Súper Compras" in html
    assert 'rel="manifest" href="manifest.webmanifest"' in html
    assert "Content-Security-Policy" in html and "script-src 'self'" in html
    assert "unsafe-inline" not in html and "<script>" not in html
    manifest = json.loads((APP_DIR / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert manifest["name"] == "Súper Compras" and manifest["display"] == "standalone"
    for icon in manifest["icons"]:
        assert (APP_DIR / icon["src"]).is_file()
    assert (APP_DIR / "icons" / "icon-180.png").is_file()
    shell = (APP_DIR / "sw.js").read_text(encoding="utf-8")
    for asset in re.findall(r'"([^"]+\.(?:js|css|png|svg|webmanifest|html))(?:\?[^"]*)?"', shell):
        assert (APP_DIR / asset).is_file(), asset


def test_app_never_injects_html_or_reads_private_sources() -> None:
    for name in ("app.js", "data.js", "illustration.js", "sw.js"):
        source = (APP_DIR / name).read_text(encoding="utf-8")
        assert "innerHTML" not in source and "outerHTML" not in source and "insertAdjacentHTML" not in source, name
        assert "eval(" not in source and "new Function" not in source, name
        assert "fetch(" not in source or name in {"data.js", "sw.js"}, name
    app = (APP_DIR / "app.js").read_text(encoding="utf-8")
    assert "raw.githubusercontent.com/jchernandez-portfolio/precios-supermercados-sps/portfolio-data/" in app
    # La anulación local del catálogo sólo acepta rutas relativas del mismo sitio.
    assert '!local.includes("..")' in app


def _node(script: str, tmp_path: Path) -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node_not_available_for_super_compras_contract")
    for name in ("data.js", "illustration.js"):
        (tmp_path / name).write_text((APP_DIR / name).read_text(encoding="utf-8"), encoding="utf-8")
    completed = subprocess.run(
        [node, "--input-type=module", "-e", script], cwd=tmp_path, check=False, capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def test_catalog_search_filters_sorts_and_alternatives(tmp_path: Path) -> None:
    script = """
import { Catalog, fold, money, rowKey } from './data.js';
const c = Object.create(Catalog.prototype);
const p = (id, name, brand, type, price, unit, retailers, promo=false) => ({
  id, product_name: name, brand, product_type: type, category: 'Alimentos', best_price: price,
  unit_amount: unit, unit_per: 'L', retailers, promo, text: fold([brand, name, type].join(' ')) });
c.products = [
  p('a', 'Leche entera 1 L', 'Sula', 'Leche', '30.00', '30.00', ['walmart', 'la_colonia'], true),
  p('b', 'Leche deslactosada 1 L', 'Leyde', 'Leche', '28.00', '28.00', ['colonial']),
  p('c', 'Fórmula infantil con leche', 'Nan', 'Fórmula', '300.00', '600.00', ['walmart', 'colonial', 'la_colonia']),
  p('d', 'Leche entera 1100 Lt', 'Dos Pinos', 'Leche', '31.00', '0.03', ['walmart']),
];
const out = {};
out.relevance = c.search({ query: 'leche' }).items.map((x) => x.id);
out.unit = c.search({ query: 'leche', sort: 'unit' }).items.map((x) => x.id);
out.promo = c.search({ promoOnly: true }).items.map((x) => x.id);
out.store = c.search({ retailer: 'colonial', sort: 'price' }).items.map((x) => x.id);
out.accent = c.search({ query: 'FORMULA' }).total;
out.alts = c.alternatives(c.products[0]).map((x) => x.id);
out.money = money('1234.5');
out.key = rowKey('source-abc-def');
console.log(JSON.stringify(out));
"""
    result = json.loads(_node(script, tmp_path))
    assert result["relevance"][-1] == "c"  # la fórmula no es "leche" aunque la mencione
    assert result["unit"][0] == "d"
    assert result["promo"] == ["a"]
    assert result["store"] == ["b", "c"]
    assert result["accent"] == 1
    assert result["alts"] == ["b"]  # descarta el precio por unidad imposible (1100 Lt)
    assert result["money"].startswith("L ") and result["money"].endswith("1,234.50")
    assert result["key"] == "abc-def"


def test_illustration_kinds_follow_product_type(tmp_path: Path) -> None:
    script = """
import { illustrationKind, brandColor } from './illustration.js';
const k = (product_type, product_name) => illustrationKind({ product_type, product_name, category: '' });
console.log(JSON.stringify({
  soda: k('Refresco', 'PEPSI Light Gaseosa 500ml'), milk: k('Leche', 'Sula leche entera 946 ml'),
  rice: k('Arroz', 'Arroz Progreso 5 lb'), paste: k('Cuidado bucal', 'Colgate pasta dental 75 ml'),
  tuna: k('Enlatados', 'Atún en agua lata 140 g'), cereal: k('Cereal', "Kellogg's Corn Flakes 500 g"),
  same: brandColor('Pepsi') === brandColor('PEPSI'),
}));
"""
    result = json.loads(_node(script, tmp_path))
    assert result == {
        "soda": "bottle", "milk": "carton", "rice": "bag", "paste": "tube",
        "tuna": "can", "cereal": "box", "same": True,
    }


def test_photo_url_uses_versioned_public_branch_and_falls_back(tmp_path: Path) -> None:
    script = """
import { photoUrl } from './data.js';
console.log(JSON.stringify({
  ok: photoUrl({ id: '675e7a84934e90e9d47c0f50', photo: 3 }),
  none: photoUrl({ id: '675e7a84', photo: 0 }),
  bad: photoUrl({ id: '../x', photo: 2 }),
}));
"""
    result = json.loads(_node(script, tmp_path))
    assert result["ok"] == (
        "https://raw.githubusercontent.com/jchernandez-portfolio/precios-supermercados-sps/"
        "product-images/img/67/675e7a84934e90e9d47c0f50.webp?v=3"
    )
    assert result["none"] is None and result["bad"] is None
    html = (APP_DIR / "index.html").read_text(encoding="utf-8")
    assert "img-src 'self' data: https://raw.githubusercontent.com;" in html
    app = (APP_DIR / "app.js").read_text(encoding="utf-8")
    assert 'image.addEventListener("error", () => image.replaceWith(productIllustration(' in app
    assert 'image.crossOrigin = "anonymous"' in app and 'image.loading = "lazy"' in app
    assert "mix-blend-mode: multiply" in (APP_DIR / "styles.css").read_text(encoding="utf-8")


def test_search_puts_the_searched_product_first_and_related_mentions_after(tmp_path: Path) -> None:
    script = """
import { Catalog, fold } from './data.js';
const c = Object.create(Catalog.prototype);
const p = (id, name, brand, type, price) => ({
  id, product_name: name, brand, product_type: type, category: 'Alimentos', best_price: price,
  retailers: ['walmart'], promo: false, text: fold([brand, name, type].join(' ')) });
c.products = [
  p('toro', 'RES Huevos de Toro X Libra', 'Res', 'Res', '54.99'),
  p('fideo', 'Fideos De Huevo Extra Anchos', 'Essential', 'Pasta', '20.00'),
  p('rica', 'RICA YEMA Huevos 15u', 'Rica Yema', 'Huevo', '57.00'),
  p('bonovo', 'Huevos Bonovo Cartón M 15Un', 'Bonovo', 'Abarrotes', '56.00'),
  p('caro', 'Huevo de Gallina Jumbo 20 Unidades', 'Marketside', 'Huevo', '98.00'),
];
const r = c.search({ query: 'huevos' });
console.log(JSON.stringify({ ids: r.items.map((x) => x.id), tiers: r.items.map((x) => x.tier), related: r.related }));
"""
    result = json.loads(_node(script, tmp_path))
    assert result["ids"][:3] == ["bonovo", "rica", "caro"]  # huevos de verdad, del más barato al más caro
    assert set(result["ids"][3:]) == {"toro", "fideo"} and result["related"] == 2
    assert result["tiers"][:3] == [1, 0, 0]


def test_presentation_labels_are_comparable(tmp_path: Path) -> None:
    script = """
import { presentationLabel } from './data.js';
const l = (presentation, unit_per) => presentationLabel({ presentation, unit_per });
console.log(JSON.stringify([l('453.59 g'), l('907.18 g'), l('226.8 g'), l('2.268 kg'), l('500 g'), l('1 kg'),
  l('16 oz'), l('16 oz', '100 g'), l('14.2 g'), l('15 unidades'), l('1.361 kg'), l('340.19 g'), l('170.1 g'),
  l('1.134 kg'), l('2 kg'), l('454 g'), l('440 g'), l('680.39 g')]));
"""
    assert json.loads(_node(script, tmp_path)) == [
        "16 oz (454 g)", "2 lb (907 g)", "8 oz (227 g)", "5 lb (2.27 kg)", "500 g", "1 kg",
        "16 oz", "16 oz (454 g)", "14.2 g", "15 unidades", "3 lb (1.36 kg)", "12 oz (340 g)", "6 oz (170 g)",
        "2.5 lb (1.13 kg)", "2 kg", "16 oz (454 g)", "440 g", "24 oz (680 g)",
    ]


def test_store_labels_name_branches_only_when_a_chain_has_several(tmp_path: Path) -> None:
    script = """
import { storeLabel } from './data.js';
const offers = [
  { supermarket_id: 'walmart', location_id: 'walmart_tgu_ffaa' },
  { supermarket_id: 'walmart', location_id: 'walmart_tgu_el_sauce' },
  { supermarket_id: 'la_colonia', location_id: 'la_colonia_tgu' },
];
console.log(JSON.stringify([storeLabel(offers[0], offers), storeLabel(offers[2], offers), storeLabel(offers[0], [offers[0]])]));
"""
    assert json.loads(_node(script, tmp_path)) == ["Walmart FF.AA.", "La Colonia", "Walmart"]


def test_app_offers_city_selector_store_choice_and_cheapest_default() -> None:
    app = (APP_DIR / "app.js").read_text(encoding="utf-8")
    data = (APP_DIR / "data.js").read_text(encoding="utf-8")
    assert 'params.get("orden") || "price"' in app
    assert "loadCities(url)" in app and "switchCity(event.target.value)" in app
    assert "addToCart(product.id, offer.location_id, label)" in app
    assert '`${base}cities.json`' in data and '!city.manifest.includes("..")' in data
    assert "data-city" in (APP_DIR / "index.html").read_text(encoding="utf-8")


def test_display_fields_unify_units_and_produce(tmp_path: Path) -> None:
    script = """
import { displayFields } from './data.js';
const d = (product_name, extra = {}) => displayFields({ product_name, presentation: null, brand: null,
  product_type: 'Verduras', best_price: '23.90', unit_amount: null, unit_per: null, ...extra });
console.log(JSON.stringify([
  d('Aguacate indio und'), d('Aguacate Liso Un', { brand: 'La Colonia', product_type: 'Frutas' }),
  d('AGUACATE Hass Unidad', { product_type: 'Frutas y verduras' }), d('Aguacate hass libra'),
  d('Aguacate hass xlb en red'), d('Leche Sula 1 L', { presentation: '1 L', brand: 'Sula', product_type: 'Leche' }),
  d('Papel Un', { product_type: 'Papel higiénico', brand: 'La Colonia' }),
]));
"""
    result = json.loads(_node(script, tmp_path))
    assert [(r["display_name"], r["presentation"], r["brand_label"], r["unit_amount"], r["unit_per"]) for r in result] == [
        ("Aguacate indio", "1 unidad", "Frutas y verduras", "23.90", "unidad"),
        ("Aguacate Liso", "1 unidad", "Frutas y verduras", "23.90", "unidad"),
        ("AGUACATE Hass", "1 unidad", "Frutas y verduras", "23.90", "unidad"),
        ("Aguacate hass", "por libra", "Frutas y verduras", "23.90", "libra"),
        ("Aguacate hass xlb en red", "por libra", "Frutas y verduras", "23.90", "libra"),
        ("Leche Sula 1 L", "1 L", "Sula", None, None),
        ("Papel", "1 unidad", "La Colonia", "23.90", "unidad"),
    ]
