"""Escáner de Súper Compras: índice público de códigos de barras (barcodes.json)."""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from test_exportar_consumer_catalog import MODULE, build_db, export

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "super-compras"
MILK = "7411000230701"  # EAN-13 impreso; GTIN-14 = 07411000230701
COFFEE = "7501019032424"


def _with_codes(database: Path, codes: dict[int, str | None]) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute("ALTER TABLE products ADD COLUMN ean TEXT")
        connection.executemany("UPDATE products SET ean=? WHERE product_id=?", [(code, pid) for pid, code in codes.items()])


def _search_ids(output: Path) -> dict[str, list[object]]:
    search = json.loads((output / "search.json").read_text(encoding="utf-8"))
    column = {name: index for index, name in enumerate(search["columns"])}
    return {row[column["id"]]: row for row in search["rows"]}, column


def _resolve(document: dict, ids: list[str], gtin: str) -> list[str]:
    value = document["codes"].get(gtin)
    prefixes = value if isinstance(value, list) else [value] if value else []
    length = document["id_prefix_length"]
    return sorted(key for key in ids if key[:length] in prefixes)


def test_barcode_index_maps_printed_codes_to_public_rows(tmp_path: Path) -> None:
    database = tmp_path / "source.sqlite"
    output = tmp_path / "public"
    build_db(database)
    _with_codes(database, {
        1: MILK,                     # La Colonia (EAN-13)
        2: f"000{MILK}",             # Walmart con ceros de relleno: mismo GTIN
        3: "7411000230702",          # dígito de control malo: no se publica
        4: COFFEE, 5: f"0{COFFEE}",  # mismo código en dos fichas no comparables
        6: "7401234567892",          # agotado: la fila no es visible
        7: "2012345678900",          # peso variable / uso en tienda: no se publica
        8: None,
    })
    manifest = export(database, output)

    on_disk = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert on_disk["barcodes_file"] == manifest["barcodes_file"] == "barcodes.json"
    listed = next(item for item in on_disk["files"] if item["path"] == "barcodes.json")
    content = (output / "barcodes.json").read_bytes()
    assert listed["sha256"] == hashlib.sha256(content).hexdigest() and listed["bytes"] == len(content)
    document = json.loads(content)
    assert set(document) == {"schema", "as_of", "gtin_format", "id_prefix_length", "code_count", "row_count", "codes"}
    assert document["schema"] == "rpi-consumer-barcodes/v1" and document["gtin_format"] == "gtin14"
    assert document["as_of"] == on_disk["as_of"]
    assert sorted(document["codes"]) == ["07411000230701", "07501019032424"]
    assert document["code_count"] == 2 and document["row_count"] == 3

    rows, column = _search_ids(output)
    ids = list(rows)
    length = document["id_prefix_length"]
    assert length >= MODULE.MIN_BARCODE_ID_PREFIX and len({key[:length] for key in ids}) == len(ids)
    [milk] = _resolve(document, ids, "07411000230701")
    assert rows[milk][column["product_name"]] == "Leche entera Sula 1 L" and rows[milk][column["comparability"]] == "c"
    assert isinstance(document["codes"]["07411000230701"], str)
    coffees = _resolve(document, ids, "07501019032424")
    assert len(coffees) == 2 and document["codes"]["07501019032424"] == sorted(key[:length] for key in coffees)
    # Frontera pública: sólo código → prefijo de fila; nada de nombres internos ni fuentes.
    text = content.decode("utf-8")
    assert "supermarket" not in text and "source" not in text and "product_id" not in text


def test_barcode_index_is_empty_but_listed_without_ean_column(tmp_path: Path) -> None:
    database = tmp_path / "source.sqlite"
    output = tmp_path / "public"
    build_db(database)
    export(database, output)
    document = json.loads((output / "barcodes.json").read_text(encoding="utf-8"))
    assert document["codes"] == {} and document["code_count"] == 0 and document["row_count"] == 0


def test_barcode_index_rejects_row_count_mismatch(tmp_path: Path) -> None:
    database = tmp_path / "source.sqlite"
    output = tmp_path / "public"
    build_db(database)
    manifest = export(database, output)
    manifest["visible_rows"] = int(manifest["visible_rows"]) + 1
    with pytest.raises(MODULE.ExportError, match="consumer_barcodes_row_count_mismatch"):
        MODULE.attach_barcode_index(output, manifest)


def test_scan_gtin_canonicalizes_like_the_identity_engine() -> None:
    expected = "07411000230701"
    for value in (MILK, f"0{MILK}", f"000{MILK}", f" {MILK} ", int(MILK)):
        assert MODULE.scan_gtin(value) == expected
    assert MODULE.scan_gtin("7411000230702") is None
    assert MODULE.scan_gtin(f"100{MILK}") is None  # sólo ceros de relleno, nunca otros dígitos
    assert MODULE.scan_gtin(None) is None


def test_unique_prefix_grows_until_keys_do_not_collide() -> None:
    assert MODULE._unique_prefix_length(["aaaaaaaa1111", "bbbbbbbb2222"]) == 8
    assert MODULE._unique_prefix_length(["aaaaaaaaa111", "aaaaaaaab222"]) == 9
    with pytest.raises(MODULE.ExportError, match="consumer_barcodes_row_id_collision"):
        MODULE._unique_prefix_length(["abcdefgh", "abcdefgh"])


def test_tgu_and_sps_both_publish_barcodes_from_product_eans(tmp_path: Path) -> None:
    import _product_master_fixtures as fx
    from test_product_master_exporter_parity import _export_all, _rows

    db = tmp_path / "db.sqlite"
    fx.build_database(db)
    with sqlite3.connect(db) as connection:
        eans = {f"{s}:{p}": e for p, s, e in connection.execute("SELECT product_id, supermarket_id, ean FROM products")}
    _export_all(db, tmp_path / "out")
    for city in ("sps", "tgu"):
        root = tmp_path / "out" / city
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["barcodes_file"] == "barcodes.json"
        assert any(item["path"] == "barcodes.json" for item in manifest["files"])
        document = json.loads((root / "barcodes.json").read_text(encoding="utf-8"))
        assert document["codes"], city
        rows = {str(row["row_id"]).split("-", 1)[1]: row for row in _rows(tmp_path / "out", city)}
        for gtin in document["codes"]:
            matched = _resolve(document, list(rows), gtin)
            assert matched, (city, gtin)
            for key in matched:
                sources = {MODULE.public_product_id(str(o["source_product_id"])) for o in rows[key]["offers"]}
                assert any(MODULE.scan_gtin(eans.get(source)) == gtin for source in sources), (city, gtin)
        # Todo GTIN global de una oferta visible queda en el índice.
        for key, row in rows.items():
            for offer in row["offers"]:
                gtin = MODULE.scan_gtin(eans.get(MODULE.public_product_id(str(offer["source_product_id"]))))
                if gtin and MODULE.restricted_circulation_reason(gtin) is None:
                    assert key in _resolve(document, list(rows), gtin)


def _node(script: str, tmp_path: Path) -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node_not_available_for_super_compras_contract")
    shutil.copytree(APP_DIR, tmp_path / "app")
    completed = subprocess.run(
        [node, "--input-type=module", "-e", script], cwd=tmp_path / "app", check=False, capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def test_app_normalizes_scanned_and_typed_codes(tmp_path: Path) -> None:
    script = """
import { barcodeCandidates, expandUpcE, gtinValid } from './data.js';
console.log(JSON.stringify({
  same: ['7411000230701', '07411000230701', '0007411000230701', '7411 0002 3070 1'].map((c) => barcodeCandidates(c)),
  upca: barcodeCandidates('036000291452', 'upc_a'),
  ean8: barcodeCandidates('96385074', 'ean_8'),
  upce: [expandUpcE('04252614'), expandUpcE('425261'), barcodeCandidates('04252614', 'upc_e'), barcodeCandidates('04252614')],
  bad: [barcodeCandidates('7411000230702'), barcodeCandidates(''), barcodeCandidates('123'), barcodeCandidates('1007411000230701')],
  valid: [gtinValid('96385074'), gtinValid('96385075')],
}));
"""
    result = json.loads(_node(script, tmp_path))
    assert result["same"] == [["07411000230701"]] * 4
    assert result["upca"] == ["00036000291452"] and result["ean8"] == ["00000096385074"]
    # UPC-E 0425261 4 → UPC-A 042100005264; a mano también se prueba como EAN-8.
    assert result["upce"][0] == "042100005264" and result["upce"][1] == "042100005264"
    assert result["upce"][2] == ["00042100005264"] and "00042100005264" in result["upce"][3]
    assert result["bad"] == [[], [], [], []]
    assert result["valid"] == [True, False]


def test_catalog_finds_products_by_barcode_with_verified_index(tmp_path: Path) -> None:
    script = """
import { Catalog } from './data.js';
const enc = (value) => new TextEncoder().encode(JSON.stringify(value));
const sha = async (bytes) => [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map((b) => b.toString(16).padStart(2, '0')).join('');
const columns = ['id', 'product_name', 'brand', 'presentation', 'category', 'product_type', 'partition', 'best_price', 'unit_amount', 'unit_per', 'retailers', 'promo', 'other_presentations', 'comparability', 'photo'];
const row = (id, name, price) => [id, name, 'Sula', '1 L', 'Alimentos', 'Leche', 0, price, null, null, [0], 0, 0, 'c', 0];
const search = enc({ schema: 'rpi-consumer-search/v1', columns, retailers: ['walmart'], partitions: ['catalog/p.json'], row_count: 3,
  rows: [row('aaaaaaaa1111', 'Leche A', '30.00'), row('bbbbbbbb2222', 'Leche B', '20.00'), row('cccccccc3333', 'Leche C', '25.00')] });
const barcodes = enc({ schema: 'rpi-consumer-barcodes/v1', gtin_format: 'gtin14', id_prefix_length: 8,
  codes: { '07411000230701': 'aaaaaaaa', '07501019032424': ['aaaaaaaa', 'bbbbbbbb'] } });
const fetched = [];
let tamper = false;
const files = { 'search.json': search, 'barcodes.json': barcodes };
const manifest = async (withCodes) => ({ schema: 'rpi-consumer-catalog-manifest/v3', search_file: 'search.json',
  ...(withCodes ? { barcodes_file: 'barcodes.json' } : {}),
  files: [{ path: 'search.json', sha256: await sha(search) }, ...(withCodes ? [{ path: 'barcodes.json', sha256: await sha(barcodes) }] : [])] });
let current = await manifest(true);
globalThis.fetch = async (url) => {
  const relative = url.replace('https://example.test/', '');
  fetched.push(relative);
  if (relative === 'manifest.json') return { ok: true, json: async () => current };
  const body = tamper && relative === 'barcodes.json' ? enc({ schema: 'rpi-consumer-barcodes/v1', codes: {} }) : files[relative];
  return { ok: true, status: 200, arrayBuffer: async () => body.buffer.slice(body.byteOffset, body.byteOffset + body.byteLength) };
};
const out = {};
const catalog = await new Catalog('https://example.test/manifest.json').load();
const one = await catalog.findBarcode('7411000230701', 'ean_13');
out.one = one.products.map((p) => p.id);
out.fetched = [...fetched];
out.many = (await catalog.findBarcode('07501019032424')).products.map((p) => p.id);
out.none = await catalog.findBarcode('96385074');
out.invalid = await catalog.findBarcode('7411000230702');
out.fetchedOnce = fetched.filter((f) => f === 'barcodes.json').length;
current = await manifest(false);
const old = await new Catalog('https://example.test/manifest.json').load();
out.unavailable = await old.findBarcode('7411000230701');
current = await manifest(true);
tamper = true;
const altered = await new Catalog('https://example.test/manifest.json').load();
try { await altered.findBarcode('7411000230701'); } catch (error) { out.tampered = error.message; }
console.log(JSON.stringify(out));
"""
    result = json.loads(_node(script, tmp_path))
    assert result["one"] == ["aaaaaaaa1111"]
    assert result["fetched"] == ["manifest.json", "search.json", "barcodes.json"]
    assert result["many"] == ["bbbbbbbb2222", "aaaaaaaa1111"]  # más barato primero
    assert result["none"] == {"candidates": ["00000096385074"], "invalid": False, "available": True, "products": []}
    assert result["invalid"]["invalid"] is True and result["invalid"]["products"] == []
    assert result["fetchedOnce"] == 1
    assert result["unavailable"]["available"] is False and result["unavailable"]["products"] == []
    assert "alterado" in result["tampered"]


# Dibuja EAN-13 / EAN-8 / UPC-A como luminancia (módulos GS1) para probar el lector propio.
EAN_DRAW = """
const L = ['0001101','0011001','0010011','0111101','0100011','0110001','0101111','0111011','0110111','0001011'];
const R = L.map((c) => [...c].map((b) => (b === '1' ? '0' : '1')).join(''));
const G = R.map((c) => [...c].reverse().join(''));
const PARITY = ['LLLLLL','LLGLGG','LLGGLG','LLGGGL','LGLLGG','LGGLLG','LGGGLL','LGLGLG','LGLGGL','LGGLGL'];
function modules(code) {
  if (code.length === 8) return '101' + [...code.slice(0, 4)].map((d) => L[d]).join('') + '01010' + [...code.slice(4)].map((d) => R[d]).join('') + '101';
  const digits = code.length === 12 ? '0' + code : code;
  const parity = PARITY[digits[0]];
  return '101' + [...digits.slice(1, 7)].map((d, i) => (parity[i] === 'L' ? L[d] : G[d])).join('') + '01010'
    + [...digits.slice(7)].map((d) => R[d]).join('') + '101';
}
function draw(code, { width = 640, height = 480, vertical = false, noise = 0 } = {}) {
  const bars = modules(code), unit = 3, span = bars.length * unit;
  const luma = new Uint8ClampedArray(width * height);
  const start = Math.floor(((vertical ? height : width) - span) / 2);
  let seed = 11;
  for (let y = 0; y < height; y += 1) for (let x = 0; x < width; x += 1) {
    const along = (vertical ? y : x) - start, across = vertical ? x : y, size = vertical ? width : height;
    const bar = along >= 0 && along < span && across > size * 0.2 && across < size * 0.8 && bars[Math.floor(along / unit)] === '1';
    seed = (seed * 1103515245 + 12345) & 0x7fffffff;
    luma[y * width + x] = (bar ? 25 : 235) + (noise ? (seed % (2 * noise + 1)) - noise : 0);
  }
  return luma;
}
"""


def test_bundled_reader_decodes_ean_upc_without_network_or_eval(tmp_path: Path) -> None:
    script = EAN_DRAW + """
import * as lib from './vendor/zxing-upcean.js';
import { zxingReader, createDetector, cameraErrorMessage } from './scanner.js';
const reader = zxingReader(lib);
const out = {};
out.reads = [
  reader.decode(draw('7411000230701'), 640, 480),
  reader.decode(draw('7411000230701', { vertical: true, width: 480, height: 640 }), 480, 640),
  reader.decode(draw('96385074'), 640, 480),
  reader.decode(draw('036000291452', { noise: 40 }), 640, 480),
  reader.decode(new Uint8ClampedArray(640 * 480).fill(200), 640, 480),
];
// BarcodeDetector nativo con los formatos de súper; sin él, el lector propio.
class FakeDetector {
  static async getSupportedFormats() { return ['qr_code', 'ean_13', 'upc_a']; }
  constructor(options) { FakeDetector.options = options; }
  async detect() { return [{ format: 'qr_code', rawValue: 'x' }, { format: 'ean_13', rawValue: '7411000230701' }]; }
}
const native = await createDetector({ Native: FakeDetector, importLibrary: async () => { throw new Error('no debía cargarse'); } });
out.native = { kind: native.kind, formats: FakeDetector.options.formats, hit: await native.detect({}), confirmations: native.confirmations };
class QrOnly { static async getSupportedFormats() { return ['qr_code']; } }
let loaded = 0;
const fallback = await createDetector({ Native: QrOnly, importLibrary: async () => { loaded += 1; return lib; } });
out.fallback = { kind: fallback.kind, loaded, confirmations: fallback.confirmations };
out.none = (await createDetector({ Native: undefined, importLibrary: async () => lib })).kind;
out.errors = ['NotAllowedError', 'NotFoundError', 'NotReadableError', 'Unsupported', 'Raro'].map((name) => cameraErrorMessage({ name }));
console.log(JSON.stringify(out));
"""
    result = json.loads(_node(script, tmp_path))
    assert result["reads"] == [
        {"text": "7411000230701", "format": "ean_13"},
        {"text": "7411000230701", "format": "ean_13"},
        {"text": "96385074", "format": "ean_8"},
        {"text": "036000291452", "format": "upc_a"},
        None,
    ]
    assert result["native"] == {"kind": "native", "formats": ["ean_13", "upc_a"],
                                "hit": {"text": "7411000230701", "format": "ean_13"}, "confirmations": 1}
    assert result["fallback"] == {"kind": "zxing", "loaded": 1, "confirmations": 2}
    assert result["none"] == "zxing"
    denied, missing, busy, unsupported, other = result["errors"]
    assert "permiso" in denied and "No encontramos una cámara" in missing and "Otra app" in busy
    assert "navegador" in unsupported and all("código abajo" in text for text in result["errors"])
    vendor = (APP_DIR / "vendor" / "zxing-upcean.js").read_text(encoding="utf-8")
    header, code = vendor.split("*/\n", 1)
    for forbidden in ("fetch(", "XMLHttpRequest", "eval(", "new Function", "WebAssembly", "innerHTML", "importScripts"):
        assert forbidden not in code, forbidden
    assert "@zxing/library 0.21.3" in header and "Apache License 2.0" in header
    assert (APP_DIR / "vendor" / "LICENSE-zxing.txt").read_text(encoding="utf-8").lstrip().startswith("Apache License")
    assert "MIT License" in (APP_DIR / "vendor" / "LICENSE-ts-custom-error.txt").read_text(encoding="utf-8")


def test_scanner_is_lazy_accessible_and_keeps_the_csp() -> None:
    html = (APP_DIR / "index.html").read_text(encoding="utf-8")
    assert 'class="scan-button" data-scan aria-label="Escanear código de barras"' in html
    # La CSP no cambia: el lector se sirve desde 'self' y no necesita WebAssembly.
    assert "script-src 'self';" in html and "wasm-unsafe-eval" not in html and "unsafe-eval" not in html
    app = (APP_DIR / "app.js").read_text(encoding="utf-8")
    version = app.split('from "./data.js?v=', 1)[1].split('"', 1)[0]
    assert f'await import("./scanner.js?v={version}")' in app
    assert 'path.startsWith("/codigo/")' in app and "No tenemos ese código todavía" in app
    assert "getUserMedia" not in app  # la cámara se pide sólo dentro del escáner
    scanner = (APP_DIR / "scanner.js").read_text(encoding="utf-8")
    assert f'from "./data.js?v={version}"' in scanner and f'"./vendor/zxing-upcean.js?v={version}"' in scanner
    assert 'facingMode: { ideal: "environment" }' in scanner and "track.stop()" in scanner
    for hook in ('"hashchange"', '"pagehide"', '"visibilitychange"', '"cancel"', '"Escape"', "showModal", "aria-labelledby"):
        assert hook in scanner, hook
    assert 'inputmode: "numeric"' in scanner
    shell = (APP_DIR / "sw.js").read_text(encoding="utf-8")
    assert f'"scanner.js?v={version}"' in shell and f'"vendor/zxing-upcean.js?v={version}"' in shell
