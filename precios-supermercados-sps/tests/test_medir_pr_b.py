"""Medición del PR B (sólo lectura): g ≈ ml y peso sobre conteo con el motor real."""
from __future__ import annotations

import csv
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import medir_pr_b as pr_b  # noqa: E402
from precios_supermercados import product_identity_v2 as identity  # noqa: E402
from precios_supermercados.product_homologation_persistence import records_from_product_rows  # noqa: E402
from precios_supermercados.size_decisions import TABLE_SQL, decision_from_row, index_decisions  # noqa: E402

SPEC = importlib.util.spec_from_file_location("medir_reglas_identidad", ROOT / "scripts" / "medir_reglas_identidad.py")
rules = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = rules
SPEC.loader.exec_module(rules)


def gtin(base12: str) -> str:
    digits = [int(char) for char in base12]
    total = sum(digit * (3 if index % 2 else 1) for index, digit in enumerate(digits))
    return base12 + str((10 - total % 10) % 10)


G = {name: gtin(f"7421000000{index:02d}") for index, name in enumerate((
    "yogurt", "jugo", "shampoo", "te", "caprisun", "sula", "yogurt_1", "leche_pack", "aderezo", "margarina",
    "crema", "salsa_empeora", "decidido"), start=1)}
C = {name: "0" + code for name, code in G.items()}  # GTIN canónico (14 dígitos)
PRODUCTS = [
    # A: g ≈ ml exacto (yogurt) → empate a listo con 5 % y con 2 %.
    (1, "walmart", "Yogurt Yes Fresa - 1000 g", "Yes", None, "Lácteos", G["yogurt"]),
    (2, "la_colonia", "Yogurt Yes Fresa 1000 Ml", "Yes", "1000 Ml", "Yogurt", G["yogurt"]),
    # A: jugo 970 g contra 1000 ml (3 %) → sólo con 5 %.
    (3, "walmart", "Jugo Del Valle Naranja - 970 g", "Del Valle", None, "Jugos", G["jugo"]),
    (4, "paiz", "Jugo Del Valle Naranja - 1000 ml", "Del Valle", "1000 ml", "Jugos", G["jugo"]),
    # A: shampoo (fuera de lácteos/jugos/salsas) → sólo "todas las categorías".
    (5, "walmart", "Shampoo Sedal Rizos - 400 ml", "Sedal", None, "Cuidado personal", G["shampoo"]),
    (6, "paiz", "Shampoo Sedal Rizos - 400 g", "Sedal", "400 g", "Cuidado capilar", G["shampoo"]),
    # B: el título con peso menciona el conteo ("50 bolsas") → media y amplia.
    (7, "paiz", "Te Lipton Negro 50 bolsas - 100 g", "Lipton", "100 g", "Té", G["te"]),
    (8, "walmart", "Te Lipton Negro 50 Unidades", "Lipton", None, "Té", G["te"]),
    # B riesgoso: paquete contra unidad suelta → sólo amplia.
    (9, "walmart", "Jugo Capri Sun Pacific Cooler 10 Unidades", "Capri Sun", None, "Jugos", G["caprisun"]),
    (10, "la_colonia", "Jugo Capri Sun Pacific Cooler 177 Ml", "Capri Sun", "177 Ml", "Jugos", G["caprisun"]),
    (11, "walmart", "Leche Sula Maleteada 6 Unidades", "Sula", None, "Lácteos", G["sula"]),
    (12, "paiz", "Leche Saborizada Sula Maleteada - 200 ml", "Sula", "200 ml", "Leche", G["sula"]),
    # B seguro: conteo 1 y mismo pack.
    (13, "colonial", "Yogurt Yes Mora 1 Unidad", "Yes", None, "Yogurt", G["yogurt_1"]),
    (14, "walmart", "Yogurt Yes Mora - 150 g", "Yes", None, "Lácteos", G["yogurt_1"]),
    (15, "walmart", "Leche Dos Pinos Chocolate 6 Unidades", "Dos Pinos", None, "Lácteos", G["leche_pack"]),
    (16, "paiz", "Leche Dos Pinos Chocolate 6 x 200 ml", "Dos Pinos", None, "Leche", G["leche_pack"]),
    # A atípico: 180 g ×2 comparan y 180 ml queda fuera → atípico a listo.
    (17, "walmart", "Aderezo Kraft Mil Islas - 180 g", "Kraft", None, "Aderezos", G["aderezo"]),
    (18, "paiz", "Aderezo Kraft Mil Islas - 180 g", "Kraft", "180 g", "Aderezos", G["aderezo"]),
    (19, "la_colonia", "Aderezo Kraft Mil Islas 180 Ml", "Kraft", "180 Ml", "Aderezos", G["aderezo"]),
    # B atípico: 400 g ×2 comparan y "6 Unidades" queda fuera → amplia lo recupera (riesgoso).
    (20, "walmart", "Margarina Mazola Barra - 400 g", "Mazola", None, "Lácteos", G["margarina"]),
    (21, "la_colonia", "Margarina Mazola Barra 400 Gr", "Mazola", "400 Gr", "Lácteos", G["margarina"]),
    (22, "colonial", "MAZOLA Margarina Barra 6 Unidades", "Mazola", None, "Lácteos", G["margarina"]),
    # A empate a mayoría: 1000 g ≈ 1000 ml; 900 g sigue fuera.
    (23, "walmart", "Crema Dos Pinos Natural - 1000 g", "Dos Pinos", None, "Lácteos", G["crema"]),
    (24, "la_colonia", "Crema Dos Pinos Natural 1000 Ml", "Dos Pinos", "1000 Ml", "Cremas", G["crema"]),
    (25, "paiz", "Crema Dos Pinos Natural - 900 g", "Dos Pinos", "900 g", "Cremas", G["crema"]),
    # Nunca empeorar: 140 ml ≈ 145 g pero no 150 g → con la variante sería un empate; se conserva 150 g ×2.
    (26, "walmart", "Salsa Naturas Tomate - 150 g", "Naturas", None, "Salsas", G["salsa_empeora"]),
    (27, "paiz", "Salsa Naturas Tomate - 150 g", "Naturas", "150 g", "Salsas", G["salsa_empeora"]),
    (28, "la_colonia", "Salsa Naturas Tomate 145 Gr", "Naturas", "145 Gr", "Salsas", G["salsa_empeora"]),
    (29, "colonial", "NATURAS Salsa Tomate 140 ml", "Naturas", None, "Salsas", G["salsa_empeora"]),
    # Decidido por una persona: la variante no aplica.
    (30, "walmart", "Yogurt Yes Pina - 1000 g", "Yes", None, "Lácteos", G["decidido"]),
    (31, "la_colonia", "Yogurt Yes Pina 1000 Ml", "Yes", "1000 Ml", "Yogurt", G["decidido"]),
]
DECISIONS = [(C["decidido"], "different_products", None, None, None, None, None, "human:dueno", "2026-11-12T10:00:00Z")]


def groups_and_products():
    products = records_from_product_rows(PRODUCTS)
    decided = index_decisions(decision_from_row(row) for row in DECISIONS)
    groups = rules.resolve_groups(rules.build_profiles([record for _, record in products]), size_decisions=decided)
    return groups, {record.source_record_id: product_id for product_id, record in products}


@pytest.fixture(scope="module")
def result() -> dict[str, object]:
    groups, product_of = groups_and_products()
    return pr_b.measure_pr_b(groups, product_of)


def changed(result, variant: str) -> dict[str, tuple[str, bool]]:
    return {example["gtin"]: (example["cambio"], example["riesgo"]) for example in result["variantes"][variant]["ejemplos"]}


def test_g_ml_variants(result) -> None:
    five = changed(result, "A_g_ml_5pct_categorias")
    assert five == {
        C["yogurt"]: ("empate_a_listo", False), C["jugo"]: ("empate_a_listo", False),
        C["aderezo"]: ("atipico_a_listo", False), C["crema"]: ("empate_a_mayoria", False),
    }
    assert set(changed(result, "A_g_ml_2pct_categorias")) == {C["yogurt"], C["aderezo"], C["crema"]}  # jugo: 3 %
    assert set(changed(result, "A_g_ml_5pct_todas")) == set(five) | {C["shampoo"]}
    summary = result["variantes"]["A_g_ml_5pct_categorias"]
    assert summary["grupos_desbloqueados"] == 3 and summary["atipico_a_listo"] == 1
    assert summary["empate_a_mayoria"] == 1 and summary["grupos_que_mejoran"] == 4
    assert summary["ofertas_que_entran_a_comparar"] == 7
    assert summary["palabras_clave_g_ml"] == {"crema": 1, "jugo": 1, "salsa": 1, "yogurt": 1}
    standard = {offer["product_id"]: offer["presentacion_estandar"] for example in summary["ejemplos"]
                for offer in example["ofertas"]}
    assert standard[2] == "1000 g (de ml)" and standard[1] is None  # estándar: gramos


def test_count_variants_and_risky_cases(result) -> None:
    wide = changed(result, "B_conteo_amplia")
    assert wide[C["caprisun"]] == ("empate_a_listo", True)  # 10 pack contra 177 ml
    assert wide[C["sula"]] == ("empate_a_listo", True)  # 6 pack contra 200 ml
    assert wide[C["margarina"]] == ("atipico_a_listo", True)
    assert {C["te"], C["yogurt_1"], C["leche_pack"]} <= set(wide)
    assert result["variantes"]["B_conteo_amplia"]["grupos_riesgosos_paquete_vs_unidad"] == 3
    # Los riesgosos van primero en los ejemplos.
    assert [example["riesgo"] for example in result["variantes"]["B_conteo_amplia"]["ejemplos"]][:3] == [True] * 3
    assert set(changed(result, "B_conteo_media")) == {C["te"], C["yogurt_1"], C["leche_pack"]}
    assert set(changed(result, "B_conteo_segura")) == {C["yogurt_1"], C["leche_pack"]}
    assert result["variantes"]["B_conteo_media"]["reglas_aplicadas_por_grupo"] == {
        "conteo_1": 1, "mismo_pack": 1, "titulo_menciona_conteo": 1}
    for variant in ("B_conteo_media", "B_conteo_segura"):
        assert result["variantes"][variant]["grupos_riesgosos_paquete_vs_unidad"] == 0
    count_offer = next(offer for example in result["variantes"]["B_conteo_segura"]["ejemplos"]
                       for offer in example["ofertas"] if offer["product_id"] == 15)
    assert count_offer["presentacion_estandar"] == "pack_count=6 (tamaño = peso del grupo)"


def test_combination_and_summary(result) -> None:
    combo = set(changed(result, "A5_mas_B_media"))
    assert combo == set(changed(result, "A_g_ml_5pct_categorias")) | set(changed(result, "B_conteo_media"))
    assert result["resumen"]["A5_mas_B_media"]["grupos_que_mejoran"] == 7
    assert result["referencia_aproximada_del_dueno"]["B_conteo_amplia"] == 174
    assert result["schema"] == "rpi-medicion-pr-b/v1"


def test_never_worse_guard_keeps_the_current_resolution(result) -> None:
    for name, summary in result["variantes"].items():
        guard = summary["nunca_empeorar"]
        assert guard["grupos_que_empeoran_con_guarda"] == 0 and guard["verificado_ninguno_empeora"], name
        assert C["salsa_empeora"] not in changed(result, name)
    # Con g ≈ ml 140 ml empata con 145 g y el grupo listo (150 g ×2) se bloquearía: la guarda lo evita.
    assert result["variantes"]["A_g_ml_5pct_categorias"]["nunca_empeorar"]["menos_cadenas_o_bloqueo"] == 1
    assert result["variantes"]["B_conteo_segura"]["nunca_empeorar"]["grupos_que_empeorarian_sin_guarda"] == 0


def test_decided_groups_are_left_to_the_decision(result) -> None:
    for summary in result["variantes"].values():
        assert summary["grupos_con_decision_omitidos"] == 1
    assert all(C["decidido"] not in changed(result, name) for name in result["variantes"])


def test_engine_is_restored_after_each_variant() -> None:
    original = identity._gtin_pair_conflicts
    with pr_b.engine_variant(pr_b.VARIANTS[0]):
        assert identity._gtin_pair_conflicts is not original
    assert identity._gtin_pair_conflicts is original
    with pytest.raises(RuntimeError), pr_b.engine_variant(pr_b.VARIANTS[0]):
        raise RuntimeError
    assert identity._gtin_pair_conflicts is original


@pytest.mark.parametrize(("title", "count", "expected"), [
    ("Te Lipton Negro 50 bolsas - 100 g", 50, True),
    ("Leche Sula 200 ml x 6", 6, True),
    ("Leche Sula Pack de 6 - 200 ml", 6, True),
    ("Jugo Capri Sun Pacific Cooler 177 Ml", 10, False),
    ("Coca Cola 2 L", 2, False),
    ("Galletas x 200 g", 200, False),
    ("Te 150 bolsas", 50, False),
])
def test_mentions_count(title: str, count: int, expected: bool) -> None:
    assert pr_b.mentions_count(title, count) is expected


def test_measure_with_pr_b_writes_its_outputs_read_only(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE products (product_id INTEGER PRIMARY KEY, supermarket_id TEXT NOT NULL, name TEXT NOT NULL,
            brand TEXT, presentation TEXT, category TEXT, ean TEXT);
        CREATE TABLE product_homologation_profiles (product_id INTEGER PRIMARY KEY, comparison_status TEXT NOT NULL);
    """)
    con.execute(TABLE_SQL)
    con.executemany("INSERT INTO products VALUES (?,?,?,?,?,?,?)", PRODUCTS)
    con.executemany("INSERT INTO size_decisions VALUES (?,?,?,?,?,?,?,?,?)", DECISIONS)
    con.commit()

    class Backend:
        sql: list[str] = []

        def query(self, sql, args=()):
            self.sql.append(sql)
            return [tuple(row) for row in con.execute(sql, tuple(args)).fetchall()]

    base = "https://example.test/v3/"
    catalog = {
        base + "cities.json": {"cities": [{"slug": "san-pedro-sula", "manifest": "sps/manifest.json"}]},
        base + "sps/manifest.json": {"files": [{"path": "catalog/a.json"}]},
        base + "sps/catalog/a.json": {"rows": [{"row_id": "source-c1", "offers": [{"source_product_id": "walmart:1"}]}]},
    }
    backend = Backend()
    summary, lines = rules.measure(backend, catalog.__getitem__, base, pr_b=True)
    assert summary["medicion_pr_b_resumen"]["B_conteo_amplia"]["riesgosos"] == 3
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in backend.sql)
    rules.write_outputs(tmp_path / "out", summary, lines)
    written = json.loads((tmp_path / "out" / "medicion-pr-b.json").read_text(encoding="utf-8"))
    assert "_lineas" not in written and set(written["variantes"]) == {variant.name for variant in pr_b.VARIANTS}
    with (tmp_path / "out" / "pr-b-grupos.csv").open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert list(rows[0]) == list(pr_b.CSV_COLUMNS)
    assert {row["riesgo"] for row in rows if row["gtin"] == C["caprisun"]} == {"paquete_vs_unidad"}
    assert "_pr_b" not in json.loads((tmp_path / "out" / "medicion-reglas.json").read_text(encoding="utf-8"))

    # Sin --pr-b no hay archivos del PR B.
    plain, plain_lines = rules.measure(backend, catalog.__getitem__, base)
    rules.write_outputs(tmp_path / "plain", plain, plain_lines)
    assert not (tmp_path / "plain" / "medicion-pr-b.json").exists()
