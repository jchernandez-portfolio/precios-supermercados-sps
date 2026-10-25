"""Auditoría de calidad: detecta errores visibles en Súper Compras y conserva su fecha."""
from __future__ import annotations

import csv
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "auditar_calidad_catalogo.mjs"


def _node(script: str) -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node_not_available")
    completed = subprocess.run([node, "--input-type=module", "-e", script], cwd=ROOT, check=False,
                               capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def test_audit_finds_visible_problems_and_keeps_first_seen() -> None:
    script = f"""
import {{ auditProducts, auditBetweenCities, toRows, toCsv, previousFirstSeen }} from '{SCRIPT.as_uri()}';
const p = (id, product_name, presentation, unit_amount, unit_per, retailers = ['walmart'], product_type = 'Bebidas') => ({{
  id, product_name, presentation, unit_amount, unit_per, retailers, product_type, category: 'Bebidas y tabaco', best_price: '30.70' }});
const normal = Array.from({{ length: 10 }}, (_, i) => p('n' + i, 'Gaseosa ' + i + ' 2 L', '2 L', '25.00', 'L'));
const products = [...normal,
  p('a', 'Gaseosa Sprite 1100 Lt Pet', '1100 L', '0.03', 'L'),
  p('b', 'ENSURE Liquido Vainilla 237', null, null, null),
  p('c', 'Desinfectante Family Guard Lavanda 40 0Ml', '1 unidad', '126.36', 'unidad'),
  p('d', 'Agua saborizada 6 ml', '6 ml', '5116.67', 'L', ['walmart', 'la_colonia']),
];
const sps = {{ name: 'San Pedro Sula', slug: 'san-pedro-sula' }};
const found = auditProducts(products, sps);
const tgu = {{ name: 'Tegucigalpa', slug: 'tegucigalpa' }};
found.push(...auditBetweenCities(
  {{ city: sps, products: [p('e', 'Chipotle', 'por libra', '68.00', 'libra', ['la_colonia'], 'Cerdo')] }},
  {{ city: tgu, products: [p('f', 'Chipotle', '1 unidad', '68.00', 'unidad', ['la_colonia'], 'Cerdo')] }}));
const previous = previousFirstSeen('ciudad,supermercado,problema,id,detectado_desde\\nSan Pedro Sula,Walmart,Sin presentación,b,2026-10-01\\n', [sps, tgu]);
const rows = toRows(found, '2026-10-16', previous);
console.log(JSON.stringify({{ rows: rows.map(r => [r.ciudad, r.supermercado, r.problema, r.producto, r.detectado_desde]), csv: toCsv(rows) }}));
"""
    result = json.loads(_node(script))
    assert result["rows"] == [
        ["San Pedro Sula", "La Colonia", "Precio por unidad: probable error", "Agua saborizada 6 ml", "2026-10-16"],
        ["San Pedro Sula", "Walmart", "Peso o volumen imposible", "Gaseosa Sprite 1100 Lt Pet", "2026-10-16"],
        ["San Pedro Sula", "Walmart", "Precio por unidad: probable error", "Agua saborizada 6 ml", "2026-10-16"],
        ["San Pedro Sula", "Walmart", "Precio por unidad: probable error", "Gaseosa Sprite 1100 Lt Pet", "2026-10-16"],
        ["San Pedro Sula", "Walmart", "Medida mal escrita por el súper", "Desinfectante Family Guard Lavanda 40 0Ml", "2026-10-16"],
        ["San Pedro Sula", "Walmart", "Sin presentación", "ENSURE Liquido Vainilla 237", "2026-10-01"],
        ["Tegucigalpa", "La Colonia", "Distinto entre ciudades", "Chipotle", "2026-10-16"],
    ]
    parsed = list(csv.DictReader(result["csv"].splitlines()))
    assert len(parsed) == 7 and parsed[0]["enlace"].endswith("?ciudad=san-pedro-sula#/p/d")


def test_confirmed_registry_is_a_valid_table() -> None:
    rows = list(csv.reader((ROOT / "docs" / "calidad" / "errores-confirmados.csv").read_text(encoding="utf-8").splitlines()))
    header, body = rows[0], rows[1:]
    assert header == ["fecha", "ciudad", "supermercado", "producto", "problema", "detalle", "origen", "estado",
                      "solucion", "referencia"]
    assert body and all(len(row) == len(header) for row in body)
    assert {row[7] for row in body} <= {"Pendiente", "Corregido", "Sin cambio", "Oculto en la web", "Corregido en la web"}
    assert {row[6] for row in body} <= {"Súper", "Súper Compras", "Revisión"}


def test_freshness_rows_report_late_supermarkets() -> None:
    script = f"""
import {{ freshnessRows, summaryMarkdown }} from '{SCRIPT.as_uri()}';
const city = {{ name: 'San Pedro Sula', slug: 'san-pedro-sula', as_of: 'x', catalog: {{ manifest: {{ source_freshness: [
  {{ supermarket_id: 'colonial', location_id: 'colonial_sps', status: 'STALE', last_successful_at: '2026-10-22T11:26:53Z', data_age_hours: 57 }},
  {{ supermarket_id: 'walmart', location_id: 'walmart_sps', status: 'FRESH', last_successful_at: '2026-10-24T11:25:41Z', data_age_hours: 9 }},
] }} }} }};
const rows = freshnessRows([city], '2026-10-24');
console.log(JSON.stringify({{ rows: rows.map(r => [r.supermercado, r.estado, r.horas_de_atraso]), md: summaryMarkdown([], [city], '2026-10-24', rows) }}));
"""
    result = json.loads(_node(script))
    assert result["rows"] == [["Colonial", "Atrasado", 57], ["Walmart", "Al día", 9]]
    assert "1 sucursal(es) sin captura aceptada hoy" in result["md"]


def test_photo_rows_count_coverage_and_quality() -> None:
    script = f"""
import {{ photoRows, photosMarkdown, summaryMarkdown, PHOTO_COLUMNS, toCsv }} from '{SCRIPT.as_uri()}';
const city = {{ name: 'San Pedro Sula', slug: 'san-pedro-sula', as_of: 'x', products: [
  {{ id: 'a1', photo: '0123456789', retailers: ['walmart', 'colonial'] }},
  {{ id: 'b2', photo: 3, retailers: ['walmart'] }},
  {{ id: 'c3', photo: 0, retailers: ['colonial'] }},
  {{ id: 'd4', photo: 'ffffffffff', retailers: ['colonial'] }},
] }};
const index = {{ schema: 'super-compras-product-images/v2', items: {{ a1: {{ q: 'hd' }}, d4: {{ q: 'low' }} }} }};
const rows = photoRows([city], '2026-10-25', index);
const md = summaryMarkdown([], [city], '2026-10-25', [], rows);
console.log(JSON.stringify({{ rows: rows.map(r => [r.supermercado, r.productos, r.con_foto, r.sin_foto, r.hd, r.sd, r.low]), md, csv: toCsv(rows, PHOTO_COLUMNS).split('\\n')[0] }}));
"""
    result = json.loads(_node(script))
    assert result["rows"] == [
        ["Total", 4, 3, 1, 1, 0, 1], ["Colonial", 3, 2, 1, "", "", ""], ["Walmart", 2, 2, 0, "", "", ""],
    ]
    assert "## Fotos de producto" in result["md"] and "3 de 4 productos con foto (75 %)" in result["md"]
    assert result["csv"] == "fecha,ciudad,catalogo,supermercado,productos,con_foto,sin_foto,hd,sd,low"
