"""Paso 0: medición de reglas de identidad (sólo lectura) y sus simulaciones."""
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
SPEC = importlib.util.spec_from_file_location("medir_reglas_identidad", ROOT / "scripts" / "medir_reglas_identidad.py")
rules = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = rules
SPEC.loader.exec_module(rules)

BASE = "https://example.test/v3/"
PRODUCTS = [
    # Empate 1 contra 1: mismo GTIN, 148 g contra 240 g → hoy bloqueado.
    (1, "walmart", "Maíz Dulce En Granos El Migo - 148 g", "El Migo", None, "Enlatados", "0012656001065"),
    (2, "la_colonia", "Maiz Elmigo Dulce En Grano Lata 240 Gr", "El Migo", "240 Gr", "Enlatados", "012656001065"),
    # Mayoría 2 contra 1: Paiz (450 g) sale hoy de la tarjeta.
    (3, "walmart", "Margarina Mazola Reducida en Grasa - 400 g", "Mazola", None, "Lácteos", "750894621330"),
    (4, "la_colonia", "Margarina Mazola Reducida En Grasa 400 Gr", "Mazola", "400 Gr", "Lácteos", "750894621330"),
    (5, "paiz", "Margarina Mazola Reducida en Grasa - 450 g", "Mazola", "450 g", "Lácteos", "750894621330"),
    # Conflicto de tipo: sigue bloqueado.
    (6, "walmart", "Shampoo Sedal Ceramidas 340 ml", "Sedal", None, "Cuidado personal", "7891150000001"),
    (7, "la_colonia", "Galletas Sedal Chocolate 340 Gr", "Sedal", "340 Gr", "Galletas", "7891150000001"),
    # Una sola cadena (dos sucursales en TGU).
    (8, "walmart", "Desodorante Rexona Clinical Barra - 58 g", "Rexona", None, "Cuidado personal", "7501019032424"),
]


class Backend:
    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(path)
        self.sql: list[str] = []

    def query(self, sql, args=()):
        self.sql.append(sql)
        return [tuple(row) for row in self.connection.execute(sql, tuple(args)).fetchall()]


def database(path: Path, stored: dict[int, str] | None = None) -> Backend:
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE products (product_id INTEGER PRIMARY KEY, supermarket_id TEXT NOT NULL, name TEXT NOT NULL,
            brand TEXT, presentation TEXT, category TEXT, ean TEXT);
        CREATE TABLE product_homologation_profiles (product_id INTEGER PRIMARY KEY, comparison_status TEXT NOT NULL);
    """)
    con.executemany("INSERT INTO products VALUES (?,?,?,?,?,?,?)", PRODUCTS)
    statuses = {1: "review_required", 2: "review_required", 3: "ready", 4: "ready", 5: "review_required",
                6: "review_required", 7: "review_required", 8: "single_source"} | (stored or {})
    con.executemany("INSERT INTO product_homologation_profiles VALUES (?,?)", sorted(statuses.items()))
    con.commit()
    con.close()
    return Backend(path)


def catalog() -> dict[str, object]:
    def offer(source):
        return {"source_product_id": source}
    return {
        BASE + "cities.json": {"cities": [
            {"slug": "san-pedro-sula", "manifest": "sps/manifest.json"},
            {"slug": "tegucigalpa", "manifest": "tgu/manifest.json"},
        ]},
        BASE + "sps/manifest.json": {"files": [{"path": "catalog/a.json"}]},
        BASE + "sps/catalog/a.json": {"rows": [
            {"row_id": "source-c1", "offers": [offer("walmart:1")]},
            {"row_id": "source-c2", "offers": [offer("la_colonia:2")]},
            {"row_id": "product-m1", "offers": [offer("walmart:3"), offer("la_colonia:4")]},
            {"row_id": "source-s1", "offers": [offer("walmart:6")]},
            {"row_id": "source-s2", "offers": [offer("la_colonia:7")]},
        ]},
        BASE + "tgu/manifest.json": {"files": [{"path": "catalog/a.json"}]},
        BASE + "tgu/catalog/a.json": {"rows": [
            {"row_id": "product-m1", "offers": [offer("walmart:3@walmart_tgu_ffaa"), offer("walmart:3@walmart_tgu_el_sauce"),
                                                offer("la_colonia:4@la_colonia_tgu")]},
            {"row_id": "source-p5a", "offers": [offer("paiz:5@paiz_tgu_multiplaza")]},
            {"row_id": "source-p5b", "offers": [offer("paiz:5@paiz_tgu_proceres")]},
            {"row_id": "source-r1", "offers": [offer("walmart:8@walmart_tgu_ffaa")]},
            {"row_id": "source-r2", "offers": [offer("walmart:8@walmart_tgu_el_sauce")]},
        ]},
    }


def test_measure_reasons_and_simulations(tmp_path: Path) -> None:
    backend = database(tmp_path / "db.sqlite")
    summary, lines = rules.measure(backend, catalog().__getitem__, BASE)

    assert summary["verificacion_contra_perfiles_guardados"] == {"coincide": 8}
    assert summary["grupos_gtin_en_2_o_mas_cadenas"] == {
        "total": 3, "listos_sin_exclusiones": 0, "listos_con_ofertas_excluidas": 1, "bloqueados_review_required": 2}
    reasons = summary["motivos"]
    assert list(reasons)[:5] == list(rules.ASKED_REASONS)
    assert reasons["cross_source_presentation_conflict"]["grupos_bloqueados_con_este_motivo"] == 2
    assert reasons["cross_source_presentation_conflict"]["grupos_bloqueados_solo_por_este_motivo"] == 1
    assert reasons["cross_source_presentation_conflict"]["grupos_listos_con_ofertas_excluidas_por_este_motivo"] == 1
    assert reasons["product_type_conflict"]["grupos_bloqueados_con_este_motivo"] == 1

    scenario = summary["simulacion_paso_3"]["A_solo_tamano"]
    assert scenario == {
        "maestros_desbloqueados_total": 1,  # el maíz (empate); el de tipo sigue bloqueado
        "maestros_desbloqueados_con_mayoria_de_tamano": 0,
        "maestros_desbloqueados_en_empate": 1,
        "grupos_listos_que_recuperan_ofertas": 1,  # margarina: Paiz vuelve
        "ofertas_que_vuelven_a_su_tarjeta": 1,
    }

    sps = summary["tarjetas_por_ciudad"]["san-pedro-sula"]
    assert sps["actual"]["tarjetas"] == 5
    assert sps["paso_1_sucursales_unidas"]["tarjetas_que_desaparecen"] == 0
    assert sps["paso_1_y_3_solo_mayoria"]["tarjetas"] == 5  # el empate no se une
    assert sps["paso_1_y_3_con_empates"]["tarjetas"] == 4
    tgu = summary["tarjetas_por_ciudad"]["tegucigalpa"]
    step1 = tgu["paso_1_sucursales_unidas"]
    assert step1["tarjetas_que_desaparecen"] == 2 and step1["productos_partidos_por_sucursal"] == 2
    assert step1["por_supermercado"] == {"paiz": 1, "walmart": 1}
    assert tgu["paso_1_y_3_solo_mayoria"]["tarjetas"] == 2  # Paiz vuelve a la margarina
    assert tgu["paso_1_y_3_solo_mayoria"]["tarjetas_con_2_o_mas_supermercados"] == 1

    roles = {(line["product_id"], line["rol"]) for line in lines}
    assert {(5, "vuelve_a_la_tarjeta"), (3, "nucleo"), (6, "sigue_fuera")} <= roles
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in backend.sql)


def test_disagreement_with_stored_profiles_is_reported(tmp_path: Path) -> None:
    backend = database(tmp_path / "db.sqlite", {8: "unmapped"})
    summary, _ = rules.measure(backend, catalog().__getitem__, BASE)
    assert summary["verificacion_contra_perfiles_guardados"] == {"coincide": 7, "unmapped->single_source": 1}


def test_only_reads(tmp_path: Path) -> None:
    reader = rules.ReadOnly(database(tmp_path / "db.sqlite"))
    with pytest.raises(rules.AuditError, match="solo_lectura"):
        reader.query("UPDATE products SET name='x'")


def test_outputs(tmp_path: Path) -> None:
    summary, lines = rules.measure(database(tmp_path / "db.sqlite"), catalog().__getitem__, BASE)
    rules.write_outputs(tmp_path / "out", summary, lines)
    assert json.loads((tmp_path / "out" / "medicion-reglas.json").read_text(encoding="utf-8"))["motivos"]
    with (tmp_path / "out" / "grupos-reglas.csv").open(encoding="utf-8") as handle:
        read = list(csv.DictReader(handle))
    assert list(read[0]) == list(rules.CSV_COLUMNS) and len(read) == 7
