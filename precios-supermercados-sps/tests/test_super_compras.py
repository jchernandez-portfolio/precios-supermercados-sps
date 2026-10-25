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


def test_catalog_also_publishes_a_home_page_for_the_first_paint(tmp_path: Path) -> None:
    """La app pinta Inicio con home.json (ofertas, más comparados, departamentos) sin bajar el índice."""
    database = tmp_path / "source.sqlite"
    output = tmp_path / "public"
    build_db(database)
    manifest = export(database, output)
    on_disk = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    listed = {item["path"]: item for item in on_disk["files"]}
    search = _search(output)
    rows_by_id = {row[0]: row for row in search["rows"]}

    assert on_disk["home_file"] == manifest["home_file"] == "home.json"
    assert listed["home.json"]["sha256"] == hashlib.sha256((output / "home.json").read_bytes()).hexdigest()
    home = json.loads((output / "home.json").read_text(encoding="utf-8"))
    column = {name: index for index, name in enumerate(home["columns"])}
    assert home["schema"] == MODULE.HOME_SCHEMA == "rpi-consumer-home/v1"
    assert home["columns"] == search["columns"] and home["row_count"] == search["row_count"]
    assert home["partitions"] == search["partitions"] and home["retailers"] == search["retailers"]
    ids = [row[0] for row in home["rows"]]
    assert ids == home["sections"]["deals"] + home["sections"]["compared"]
    assert all(rows_by_id[item] == row for item, row in zip(ids, home["rows"]))
    assert home["sections"]["deals"] and all(rows_by_id[item][column["promo"]] == 1 for item in home["sections"]["deals"])
    assert all(len(rows_by_id[item][column["retailers"]]) >= 3 for item in home["sections"]["compared"])
    assert not set(home["sections"]["deals"]) & set(home["sections"]["compared"])
    counted: dict[str, int] = {}
    for row in search["rows"]:
        if row[column["category"]]:
            counted[row[column["category"]]] = counted.get(row[column["category"]], 0) + 1
    assert {item["name"]: item["count"] for item in home["departments"]} == counted
    assert [item["count"] for item in home["departments"]] == sorted(counted.values(), reverse=True)
    assert set(home["unit_basis"]) <= {row[column["product_type"]] for row in search["rows"]}
    assert isinstance(home["unit_medians"], dict)


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

    # Versión 2 (R2): huella de 10 caracteres; gana sobre la v1 producto por producto.
    v2 = tmp_path / "index-v2.json"
    v2.write_text(json.dumps({"schema": "super-compras-product-images/v2", "items": {
        second: {"sig": "c", "h": "0123456789", "sm": "colonial", "q": "hd"},
        first: {"sig": "d", "none": 1}, "yy": {"h": "NOT-A-HASH"},
    }}), encoding="utf-8")
    assert MODULE.load_photo_versions(v2) == {second: "0123456789"}
    merged = MODULE.load_photo_versions([index, v2])
    assert merged == {first: 3, second: "0123456789"}
    MODULE._PHOTO_VERSIONS.update(merged)
    try:
        MODULE.attach_search_index(output, manifest)
    finally:
        MODULE._PHOTO_VERSIONS.clear()
    photos = {row[0]: row[column["photo"]] for row in _search(output)["rows"]}
    assert photos[first] == 3 and photos[second] == "0123456789"


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
    for asset in re.findall(r'"([^"]+\.(?:js|css|png|svg|webmanifest|html|woff2))(?:\?[^"]*)?"', shell):
        assert (APP_DIR / asset).is_file(), asset
    # Sin terceros: la tipografía se sirve desde el sitio (SIL OFL) y la CSP no abre fonts.googleapis.
    assert "fonts.googleapis.com" not in html and "fonts.gstatic.com" not in html
    assert "font-src 'self'" in html and "style-src 'self';" in html
    styles = (APP_DIR / "styles.css").read_text(encoding="utf-8")
    for weight in (400, 500, 600, 700, 800):
        assert f'url("fonts/figtree-{weight}.woff2")' in styles and (APP_DIR / "fonts" / f"figtree-{weight}.woff2").is_file()
    assert (APP_DIR / "fonts" / "LICENSE-Figtree.txt").is_file()


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


def test_catalog_paints_home_first_and_loads_the_index_once_in_the_background(tmp_path: Path) -> None:
    """Inicio sale de home.json; buscar, Mi compra y un enlace a producto esperan el índice."""
    script = """
import { Catalog } from './data.js';
const columns = ['id','product_name','brand','presentation','category','product_type','partition','best_price','unit_amount','unit_per','retailers','promo','other_presentations','comparability','photo'];
const row = (id, name, type, price, promo, stores) => [id, name, 'Marca', '1 L', 'Alimentos', type, 0, price, price, 'L', stores, promo, 0, 'c', 0];
const rows = [row('a1', 'Leche A', 'Leche', '30.00', 1, [0, 1, 2]), row('b2', 'Leche B', 'Leche', '28.00', 0, [0, 1, 2]), row('c3', 'Jugo C', 'Jugo', '20.00', 1, [0])];
const base = { schema: 'rpi-consumer-search/v1', columns, retailers: ['walmart', 'la_colonia', 'colonial'], partitions: ['catalog/p.json'] };
const files = {};
files['search.json'] = { ...base, rows };
files['home.json'] = { schema: 'rpi-consumer-home/v1', columns, retailers: base.retailers, partitions: base.partitions, row_count: 3,
  departments: [{ name: 'Alimentos', count: 3 }], unit_basis: { Leche: 'L' }, unit_medians: {}, sections: { deals: ['a1'], compared: ['b2'] }, rows: [rows[0], rows[1]] };
const sha = async (text) => [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text)))].map((b) => b.toString(16).padStart(2, '0')).join('');
const listed = await Promise.all(Object.entries(files).map(async ([path, body]) => ({ path, sha256: await sha(JSON.stringify(body)) })));
const manifest = { schema: 'rpi-consumer-catalog-manifest/v3', as_of: '2026-10-25T12:00:00Z', search_file: 'search.json', home_file: 'home.json', files: listed };
const fetched = [];
globalThis.fetch = async (url) => {
  const relative = url.replace('https://x.test/', '');
  fetched.push(relative);
  const body = relative === 'manifest.json' ? manifest : files[relative];
  if (!body) return { ok: false, status: 404 };
  const bytes = new TextEncoder().encode(JSON.stringify(body));
  return { ok: true, json: async () => body, arrayBuffer: async () => bytes.buffer };
};
const out = {};
const c = await new Catalog('https://x.test/manifest.json').load();
out.afterLoad = { ready: c.ready, fetched: [...fetched], deals: c.home.deals.map((p) => p.id), compared: c.home.compared.map((p) => p.id),
  count: c.productCount, departments: c.departments(), searchable: c.products.length };
fetched.length = 0;
const [product] = await Promise.all([c.product('c3'), c.loadIndex(), c.loadIndex()]);
out.index = { id: product?.id, fetched: [...fetched], ready: c.ready, ids: c.products.map((p) => p.id).sort(), found: c.search({ query: 'leche' }).total,
  departments: c.departments(), count: c.productCount };
fetched.length = 0;
delete manifest.home_file;
const legacy = await new Catalog('https://x.test/manifest.json').load();
out.legacy = { ready: legacy.ready, fetched: [...fetched], count: legacy.productCount, home: legacy.home ?? null };
console.log(JSON.stringify(out));
"""
    result = json.loads(_node(script, tmp_path))
    assert result["afterLoad"] == {
        "ready": False, "fetched": ["manifest.json", "home.json"], "deals": ["a1"], "compared": ["b2"],
        "count": 3, "departments": [{"name": "Alimentos", "count": 3}], "searchable": 0,
    }
    assert result["index"] == {"id": "c3", "fetched": ["search.json"], "ready": True, "ids": ["a1", "b2", "c3"], "found": 2,
                               "departments": [{"name": "Alimentos", "count": 3}], "count": 3}
    assert result["legacy"] == {"ready": True, "fetched": ["manifest.json", "search.json"], "count": 3, "home": None}


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
import { photoUrl, photoSrcset } from './data.js';
const v2 = { id: '675e7a84934e90e9d47c0f50', photo: 'a1b2c3d4e5' };
console.log(JSON.stringify({
  ok: photoUrl({ id: '675e7a84934e90e9d47c0f50', photo: 3 }),
  none: photoUrl({ id: '675e7a84', photo: 0 }),
  bad: photoUrl({ id: '../x', photo: 2 }),
  badHash: photoUrl({ id: '675e7a84', photo: '../../x' }),
  v2card: photoUrl(v2),
  v2detail: photoUrl(v2, 800),
  srcset: photoSrcset(v2),
  v1srcset: photoSrcset({ id: '675e7a84', photo: 3 }),
}));
"""
    result = json.loads(_node(script, tmp_path))
    assert result["ok"] == (
        "https://raw.githubusercontent.com/jchernandez-portfolio/precios-supermercados-sps/"
        "product-images/img/67/675e7a84934e90e9d47c0f50.webp?v=3"
    )
    assert result["none"] is None and result["bad"] is None and result["badHash"] is None
    r2 = "https://pub-0dcb2204775c484dbe6681e659eee236.r2.dev/fotos/v2/"
    assert result["v2card"] == r2 + "320/67/675e7a84934e90e9d47c0f50-a1b2c3d4e5.webp"
    assert result["v2detail"] == r2 + "800/67/675e7a84934e90e9d47c0f50-a1b2c3d4e5.webp"
    assert result["srcset"] == f"{result['v2card']} 320w, {result['v2detail']} 800w"
    assert result["v1srcset"] is None
    html = (APP_DIR / "index.html").read_text(encoding="utf-8")
    assert "img-src 'self' data: https://raw.githubusercontent.com https://pub-0dcb2204775c484dbe6681e659eee236.r2.dev;" in html
    app = (APP_DIR / "app.js").read_text(encoding="utf-8")
    assert 'image.addEventListener("error", () => image.replaceWith(productIllustration(' in app
    assert 'image.crossOrigin = "anonymous"' in app and 'image.loading = sizes ? "eager" : "lazy"' in app
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


def test_app_modules_are_versioned_with_the_shell() -> None:
    """Un data.js viejo en caché con un app.js nuevo dejaba tarjetas vacías (2026-10-15)."""
    html = (APP_DIR / "index.html").read_text(encoding="utf-8")
    version = re.search(r'app\.js\?v=([0-9-]+)', html).group(1)
    app = (APP_DIR / "app.js").read_text(encoding="utf-8")
    assert f'from "./data.js?v={version}"' in app and f'from "./illustration.js?v={version}"' in app
    shell = (APP_DIR / "sw.js").read_text(encoding="utf-8")
    for asset in (f"app.js?v={version}", f"styles.css?v={version}", f"data.js?v={version}", f"illustration.js?v={version}"):
        assert f'"{asset}"' in shell, asset


def test_size_from_name_reads_common_store_spellings(tmp_path: Path) -> None:
    script = """
import { displayFields } from './data.js';
const d = (product_name, category = 'Alimentos', best_price = '100.00') => {
  const r = displayFields({ product_name, presentation: null, brand: null, category, product_type: 'Abarrotes',
    best_price, unit_amount: null, unit_per: null });
  return [r.presentation, r.unit_amount, r.unit_per];
};
console.log(JSON.stringify([
  d('ISOMIL 2 Plus 800 Grms'), d('MYTHOZ Café 12 onz'), d('Muellers mostaccioli pasta 16z'),
  d('ANCHOR Leche Entera Polvo 1.92k'), d('Pack Nectar Del Monte Variado 8Unx200Ml'), d('Chiky galletas 12 pk', 'Alimentos', '96.00'),
  d('KG Hummus de Kalamata'), d('Queso Gouda x 1/2 Lb', 'Alimentos', '50.00'), d('AYUNI Marmahon Medio Libra', 'Alimentos', '50.00'),
  d('RES Molida Especial X Libra'), d('AXION LavaPlatosLemo235g 3Pack', 'Limpieza'),
  d('Pantalla Samsung LED Smart 4K - 50 Pulgadas', 'Electrónica y electrodomésticos'),
  d('Juego De 4 Piezas Tamanio Queen', 'Hogar y cocina'), d('Pollo frito 4 pzs', 'Alimentos', '200.00'),
  d('EL CENTRO Pan Frances'), d('Ayote Sazon Libra Ayote Sazon Amarillo Hortifruti - Precio Por Unidad', 'Alimentos', '7.70'),
]));
"""
    assert json.loads(_node(script, tmp_path)) == [
        ["800 g", "125.00", "kg"], ["12 oz", "293.95", "kg"], ["16 oz", "220.46", "kg"],
        ["1.92 kg", "52.08", "kg"], ["8 × 200 ml", None, None], ["12 unidades", "8.00", "unidad"],
        ["por kg", "100.00", "kg"], ["226.8 g", "220.46", "kg"], ["226.8 g", "220.46", "kg"],
        ["por libra", "100.00", "libra"], ["3 × 235 g", "141.84", "kg"],
        ["1 unidad", "100.00", "unidad"], ["4 piezas", None, None], ["4 unidades", "50.00", "unidad"],
        ["1 unidad", "100.00", "unidad"], ["1 unidad", "7.70", "unidad"],
    ]


def test_gs1_net_content_rule_fills_missing_presentations(tmp_path: Path) -> None:
    """Regla GS1 de contenido neto: cantidad + unidad; sin medida se vende por pieza."""
    script = """
import { displayFields } from './data.js';
const d = (product_name, category = 'Alimentos', extra = {}) => {
  const r = displayFields({ product_name, presentation: null, brand: null, category, product_type: 'Abarrotes',
    best_price: '100.00', unit_amount: null, unit_per: null, ...extra });
  return [r.presentation, r.unit_amount, r.unit_per];
};
console.log(JSON.stringify([
  d('Rabano Rojo Mazo'), d('Cuna Mainstays de madera', 'Bebé'), d('BIGELOW Botanic BlackRaspHib 18ct'),
  d('TWININGS Wild Berries 20 ct'), d('Set Snapclips Bandan Y Ligas Equate 15ea', 'Cuidado personal y belleza'),
  d('Candy assortment 100 ct 40.7 oz'), d('Cucharas Para Bebe 2 Pz', 'Bebé'),
  d('Organic oatmeal single grain cereal 8 o'), d('Chicharrones picositos del rancho 18 grx'),
  d('ENSURE Liquido Vainilla 237'), d('Silla De Comer Hudson Graco 5 En 1', 'Bebé'),
  d('Kit Todo en 1', 'Cuidado personal y belleza'), d('Tajo Res Molido 90/10 Corral Fresco Paquete-Lb'),
  d('Mora En Fruta Juicy Fruits', 'Alimentos', { unit_amount: '200.00', unit_per: 'kg' }),
]));
"""
    assert json.loads(_node(script, tmp_path)) == [
        ["1 unidad", "100.00", "unidad"], ["1 unidad", "100.00", "unidad"], ["18 unidades", "5.56", "unidad"],
        ["20 unidades", "5.00", "unidad"], ["15 unidades", "6.67", "unidad"],
        ["40.7 oz", "86.67", "kg"], ["2 piezas", None, None],
        ["8 oz", "440.92", "kg"], ["18 g", "5555.56", "kg"],
        [None, None, None], ["1 unidad", "100.00", "unidad"],
        ["1 unidad", "100.00", "unidad"], ["por libra", "100.00", "libra"],
        [None, "200.00", "kg"],
    ]


def test_size_from_name_uses_the_type_price_basis(tmp_path: Path) -> None:
    script = """
import { displayFields } from './data.js';
const r = displayFields({ product_name: 'Jamón Pechuga De Pavo x 1/2 Lb', presentation: null, brand: null,
  category: 'Alimentos', product_type: 'Embutidos y carnes frías', best_price: '64.90', unit_amount: null, unit_per: null }, '100 g');
console.log(JSON.stringify([r.presentation, r.unit_amount, r.unit_per]));
"""
    assert json.loads(_node(script, tmp_path)) == ["226.8 g", "28.62", "100 g"]


def test_presentations_are_coherent_across_sources(tmp_path: Path) -> None:
    """Misma forma y equivalente métrico venga la medida del súper o del nombre."""
    script = """
import { displayFields, presentationLabel } from './data.js';
const d = (product_name, basis = 'kg', extra = {}) => {
  const p = { product_name, presentation: null, brand: null, category: 'Alimentos', product_type: 'Abarrotes',
    best_price: '100.00', unit_amount: null, unit_per: null, ...extra };
  const r = { ...p, ...displayFields(p, basis) };
  return [presentationLabel(r), r.unit_amount, r.unit_per];
};
console.log(JSON.stringify([
  d('Kame plain noodles 8z.'), d('Louana aceite vegetal 48z', 'L'), d('BLUE RIBBON ChocolateChip1Gal', 'L'),
  d('Gomitas Guandy Gusanitos Ácidos 100 - g', '100 g'), d('Pasta Dental Colgate 50-ml', '100 ml'),
  d('Aceite Quaker State Advdur 20w50gl', 'L'), d('Al dente fettucine', 'kg', { presentation: '10 oz' }),
  d('LA CORPENITA Semilla Marañon 1u', 'kg', { presentation: '1 unidades' }),
  d('Camarón Panamei Precocido 16/20 X 2 Lb', 'kg', { presentation: '2 lb', unit_amount: '50.00', unit_per: 'libra' }),
]));
"""
    assert json.loads(_node(script, tmp_path)) == [
        ["8 oz (227 g)", "440.92", "kg"], ["48 fl oz (1.42 L)", "70.45", "L"], ["1 gal (3.79 L)", "26.42", "L"],
        ["100 g", "100.00", "100 g"], ["50 ml", "200.00", "100 ml"],
        ["1 unidad", "100.00", "unidad"], ["10 oz (283 g)", "352.74", "kg"],
        ["1 unidad", None, None],
        ["2 lb (907 g)", "50.00", "libra"],
    ]


def test_implausible_sizes_and_unit_prices_are_hidden_but_kept(tmp_path: Path) -> None:
    """Red de seguridad: la web no muestra "Sprite 1100 L" ni L 318,000 por 100 g."""
    script = """
import { hideImplausible } from './data.js';
const p = (id, product_name, presentation, unit_amount, unit_per, product_type = 'Refresco') => ({
  id, product_name, presentation, unit_amount, unit_per, product_type, best_price: '30.70' });
const products = [
  ...Array.from({ length: 10 }, (_, i) => p('n' + i, 'Gaseosa ' + i, '2 L', '25.00', 'L')),
  p('a', 'Gaseosa Sprite 1100 Lt Pet', '1100 L', '0.03', 'L'),
  p('b', 'Gaseosa barata', '500 ml', '0.50', 'L'),
  p('c', 'Gaseosa cara', '355 ml', '600.00', 'L'),
  p('d', 'Estación de Fuerza Capacidad 100 kg', '100 kg', null, null, 'Gimnasio'),
  p('e', 'Gaseosa unidad', '1 unidad', '30.70', 'unidad'),
];
hideImplausible(products);
console.log(JSON.stringify(products.slice(10).map((x) => [x.id, x.presentation, x.unit_amount, !!x.suspect, x.hidden?.presentation ?? null])));
"""
    assert json.loads(_node(script, tmp_path)) == [
        ["a", "1.1 L", "27.91", False, None],
        ["b", None, None, True, "500 ml"],
        ["c", "355 ml", "600.00", False, None],
        ["d", "100 kg", None, False, None],
        ["e", "1 unidad", "30.70", False, None],
    ]


def test_store_measure_errors_with_a_clear_pattern_are_repaired(tmp_path: Path) -> None:
    """ "1100 Lt", "3 78lt", "397 Oz" (gramos), "3.78 Ml", "48 Unidades / 960 g" y capacidad."""
    script = """
import { hideImplausible, repairedUnitPrice, displayFields } from './data.js';
const p = (id, product_name, presentation, unit_amount, unit_per, product_type, best_price, category = 'Alimentos') => ({
  id, product_name, presentation, unit_amount, unit_per, product_type, best_price, category });
const filler = (type, per, amount, n = 10) => Array.from({ length: n }, (_, i) => p(type + i, type + ' ' + i, '1 L', amount, per, type, amount));
const products = [
  ...filler('Jugo', 'L', '45.00'), ...filler('Enlatados', '100 g', '25.00'), ...filler('Snacks', '100 g', '60.00'),
  ...filler('Ponche', 'L', '40.00'),
  p('a', 'Jugo Naranja Sula Clasico 3 78lt', '78 L', '1.99', 'L', 'Jugo', '155.00', 'Bebidas y tabaco'),
  p('b', 'Maíz Miguels Dulce Enlatado 425 Oz', '12.049 kg', '0.39', '100 g', 'Enlatados', '46.95'),
  p('c', 'Pringles Surtidos 48 Unidades /960 g', '48 × 960 g', '1.63', '100 g', 'Snacks', '749.95'),
  p('d', 'Bebida Fristy Ponche De Frutas 3.78 Ml', '3.78 ml', '15066.14', 'L', 'Ponche', '56.95', 'Bebidas y tabaco'),
  p('e', 'Refrigeradora Oster 409 L Dos puertas', '409 L', '30.00', 'L', 'Línea blanca', '12279.00', 'Electrónica y electrodomésticos'),
  p('f', 'Ms Vela Surf y Coco 240g o 8oz', '240 g', '45.00', 'kg', 'Decoración del hogar', '10.80', 'Hogar y cocina'),
];
hideImplausible(products);
const shown = products.slice(-6).map((x) => [x.id, x.presentation, x.unit_amount, x.unit_per, x.repaired?.reason ?? null, !!x.suspect]);
const name = displayFields({ product_name: 'Desinfectante Family Guard Lavanda 40 0Ml', presentation: null, category: 'Limpieza',
  product_type: 'Desinfectante', best_price: '126.36', unit_amount: null, unit_per: null }).presentation;
console.log(JSON.stringify({ shown, offer: repairedUnitPrice(products.at(-6), '160.00'), name }));
"""
    result = json.loads(_node(script, tmp_path))
    assert result["shown"] == [
        ["a", "3.78 L", "41.01", "L", "decimal escrito con espacio", False],
        ["b", "425 g", "11.05", "100 g", "gramos escritos como onzas", False],
        ["c", "960 g", "78.12", "100 g", "el total es el del paquete, no por unidad", False],
        ["d", "3.78 L", "15.07", "L", "litros escritos como mililitros", False],
        ["e", "1 unidad", "12279.00", "unidad", "capacidad, no contenido", False],
        ["f", "240 g", "45.00", "kg", None, False],
    ]
    assert result["offer"] == {"amount": "42.33", "per": "L"}
    assert result["name"] == "400 ml"


def test_stale_retailers_come_from_source_freshness(tmp_path: Path) -> None:
    """La web no dice "precios de hoy" de un súper que no se capturó hoy (Los Andes 2026-10-21)."""
    script = """
import { Catalog } from './data.js';
const c = Object.create(Catalog.prototype);
c.manifest = { source_freshness: [
  { supermarket_id: 'la_colonia', location_id: 'la_colonia_sps', status: 'FRESH', last_successful_at: '2026-10-24T17:39:24Z' },
  { supermarket_id: 'comisariato_los_andes', location_id: 'comisariato_los_andes_sps', status: 'STALE', last_successful_at: '2026-10-21T11:18:55Z' },
] };
console.log(JSON.stringify([c.staleRetailers().map((x) => x.id),
  c.staleSince({ supermarket_id: 'comisariato_los_andes', location_id: 'comisariato_los_andes_sps' }),
  c.staleSince({ supermarket_id: 'la_colonia', location_id: 'la_colonia_sps' })]));
"""
    assert json.loads(_node(script, tmp_path)) == [["comisariato_los_andes"], "2026-10-21T11:18:55Z", None]
