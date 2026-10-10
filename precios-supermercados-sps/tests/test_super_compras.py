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
