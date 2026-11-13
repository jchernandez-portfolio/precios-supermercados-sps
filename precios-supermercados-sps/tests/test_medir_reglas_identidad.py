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


def database(path: Path, stored: dict[int, str] | None = None, products=None) -> Backend:
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE products (product_id INTEGER PRIMARY KEY, supermarket_id TEXT NOT NULL, name TEXT NOT NULL,
            brand TEXT, presentation TEXT, category TEXT, ean TEXT);
        CREATE TABLE product_homologation_profiles (product_id INTEGER PRIMARY KEY, comparison_status TEXT NOT NULL);
    """)
    con.executemany("INSERT INTO products VALUES (?,?,?,?,?,?,?)", products or PRODUCTS)
    statuses = {1: "review_required", 2: "review_required", 3: "ready", 4: "ready", 5: "review_required",
                6: "review_required", 7: "review_required", 8: "single_source"} | (stored or {})
    if products is not None:
        statuses = stored or {}
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


OUNCE_PRODUCTS = [
    # Kraft Ranch: 8 oz contra 237 ml (8 × 29.57 = 236.6 ml) → hoy bloqueado; con la regla, listo.
    (21, "la_colonia", "Aderezo Kraft Ranch Classic 8 Oz", "Kraft", "8 Oz", "Aderezos", "0021000643592"),
    (22, "walmart", "Aderezo Kraft Ranch - 237 ml", "Kraft", None, "Aderezos", "0021000643592"),
    # 16.9 oz contra 300 ml: ninguna lectura coincide → sigue bloqueado.
    (23, "comisariato_los_andes", "Lee kum kee salsa soya 16.9 oz", "Lee Kum Kee", "UN", "Salsas", "0078895128895"),
    (24, "walmart", "Salsa Lee Kum Kee Soya 300 ml", "Lee Kum Kee", None, "Salsas", "0078895128895"),
]


def test_ounce_rule_resolves_only_matching_ounces(tmp_path: Path) -> None:
    # Con la regla vigente Kraft ya está listo; Lee Kum Kee (16.9 oz vs 300 ml) sigue bloqueado.
    stored = {21: "ready", 22: "ready", 23: "review_required", 24: "review_required"}
    backend = database(tmp_path / "db.sqlite", stored, OUNCE_PRODUCTS)
    summary, _ = rules.measure(backend, catalog().__getitem__, BASE)
    assert summary["verificacion_contra_perfiles_guardados"] == {"coincide": 4}
    ounce = summary["simulacion_regla_de_onzas"]
    assert ounce["grupos_con_onza_y_conflicto_de_tamano"] == 2
    assert ounce["bloqueados_a_listos"] == 1 and ounce["siguen_igual"] == 1
    assert ounce["base_fluid"] == 1 and ounce["productos_en_onzas_que_ganan_precio_unitario"] == 1
    assert "grupos_que_empeoran" not in ounce
    lines = summary["_lineas_onzas"]
    assert {(line["product_id"], line["presentacion_convertida"]) for line in lines} == {(21, "236.6 ml"), (22, None)}
    rules.write_outputs(tmp_path / "out", summary, [])
    with (tmp_path / "out" / "onzas-grupos.csv").open(encoding="utf-8") as handle:
        assert len(list(csv.DictReader(handle))) == 2
    assert "_lineas_onzas" not in json.loads((tmp_path / "out" / "medicion-reglas.json").read_text(encoding="utf-8"))


def test_ounce_basis_uses_real_conversions() -> None:
    from decimal import Decimal

    from precios_supermercados.product_homologation import PresentationSignature

    def sig(dimension, total, pack=1):
        return PresentationSignature(dimension=dimension, total_base=Decimal(total), pack_count=pack,
                                     unit_amount_base=Decimal(total) / pack)
    assert rules.ounce_basis(sig("ounce", "8"), sig("volume_ml", "237")) == "fluid"
    assert rules.ounce_basis(sig("mass_g", "680"), sig("ounce", "24")) == "mass"
    assert rules.ounce_basis(sig("ounce", "8"), sig("mass_g", "241")) is None  # 226.8 g: 6 % de diferencia
    assert rules.ounce_basis(sig("ounce", "16", 2), sig("volume_ml", "473")) is None  # distinto paquete
    assert rules.ounce_basis(sig("mass_g", "100"), sig("volume_ml", "100")) is None  # sin onzas no aplica


# ------------------------------------------------- decisiones de tamaño

MCCORMICK = "07411000204139"
CHOLULA = "00049733830119"
DECIDED_PRODUCTS = [
    # Cholula: 150 ml + 5 oz contra 150 g + 150 g → con v2.9 (g ≈ ml en salsas) comparan
    # Colonial, Walmart y Paiz; Los Andes (5 oz = 141.7 g) queda fuera.
    (3, "colonial", "CHOLULA Hot Chipotle 150 mL", "Cholula", None, "Salsas", "0049733830119"),
    (4, "comisariato_los_andes", "Cholula hot sauce chipotle 5 oz", "Cholula", "UN", "Salsas", "0049733830119"),
    (5, "walmart", "Salsa Cholula picante chipotle - 150 g", "Cholula", None, "Salsas", "0049733830119"),
    (6, "paiz", "Salsa Cholula picante chipotle - 150 g", "Cholula", "150 g", "Salsas", "0049733830119"),
    # McCormick: 180 ml ×2 contra 180 g ×2 → empate bloqueado en v2.8; listo en v2.9 (aderezos).
    (11, "colonial", "McCORMICK Caesar 180ml", "McCormick", None, "Aderezos", "7411000204139"),
    (12, "la_colonia", "Aderezo Mccormick Caesar 180 Ml", "McCormick", None, "Aderezos", "7411000204139"),
    (13, "paiz", "Aderezo Mccormick Caesar Dp - 180 g", "McCormick", "180 g", "Aderezos", "7411000204139"),
    (14, "walmart", "Aderezo Mccormick Caesar Dp - 180 g", "McCormick", None, "Aderezos", "7411000204139"),
]
DECISION_ROWS = [
    # Una persona: Cholula son 5 fl oz (las ofertas en g quedan aisladas: la onza no entra en g ≈ ml).
    (CHOLULA, "size", "5", "oz", "5", "oz", None, "human:Jchernand3z19", "2026-11-12T10:00:00Z"),
    # La IA: McCormick son 180 ml; desde v2.9 las ofertas en 180 g coinciden (g ≈ ml en aderezos).
    (MCCORMICK, "size", "180", "ml", "180", "ml", "Cont. neto 180 ml", "engine:gemini_auto", "2026-11-12T11:00:00Z"),
]


def decided_database(path: Path, *, with_table: bool) -> Backend:
    """Base con perfiles guardados como los deja producción (refresco con decisiones)."""
    from precios_supermercados.product_homologation_persistence import (
        build_homologation_rows,
        records_from_product_rows,
    )
    from precios_supermercados.size_decisions import TABLE_SQL, decision_from_row, index_decisions

    production = index_decisions(decision_from_row(row) for row in DECISION_ROWS)
    rows = build_homologation_rows(records_from_product_rows(DECIDED_PRODUCTS), updated_at_utc="2026-11-13T00:00:00Z",
                                   size_decisions=production)
    backend = database(path, {row.product_id: row.comparison_status for row in rows}, DECIDED_PRODUCTS)
    if with_table:
        backend.connection.execute(TABLE_SQL)
        backend.connection.executemany("INSERT INTO size_decisions VALUES (?,?,?,?,?,?,?,?,?)", DECISION_ROWS)
        backend.connection.commit()
    return backend


def test_measure_applies_size_decisions_like_production(tmp_path: Path) -> None:
    backend = decided_database(tmp_path / "db.sqlite", with_table=True)
    summary, _ = rules.measure(backend, catalog().__getitem__, BASE)
    # Con las decisiones el recálculo coincide con los perfiles guardados.
    assert summary["verificacion_contra_perfiles_guardados"] == {"coincide": 8}
    assert summary["decisiones_de_tamano"] == {
        "tabla_size_decisions": "presente",
        "decisiones_leidas": 2,
        "por_tipo": {"size": 2},
        "por_autor": {"engine:gemini_auto": 1, "human": 1},
        "aplicadas_a_grupos_en_2_o_mas_cadenas": 2,
        "grupos_decididos_listos": 2,
        "grupos_decididos_en_revision": 0,
        "decisiones_sin_grupo_vigente": 0,
    }
    assert "Jchernand3z19" not in json.dumps(summary, ensure_ascii=False)  # sin cuentas en el artifact
    assert summary["grupos_gtin_en_2_o_mas_cadenas"]["bloqueados_review_required"] == 0
    reasons = summary["motivos"]["human_size_decision_mismatch"]
    assert reasons["ofertas_excluidas_por_este_motivo"] == 2  # las 2 de Cholula en g; McCormick ya no aísla
    # Sólo SELECT, también sobre size_decisions.
    assert any("FROM size_decisions" in sql for sql in backend.sql)
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in backend.sql)


def test_measure_without_decisions_table_reports_false_differences(tmp_path: Path) -> None:
    backend = decided_database(tmp_path / "db.sqlite", with_table=False)
    summary, _ = rules.measure(backend, catalog().__getitem__, BASE)
    assert summary["decisiones_de_tamano"]["tabla_size_decisions"] == "ausente"
    assert summary["decisiones_de_tamano"]["decisiones_leidas"] == 0
    # Sin la tabla el motor resuelve sin decisiones: los GTIN decididos ya no coinciden.
    assert summary["verificacion_contra_perfiles_guardados"] != {"coincide": 8}
    assert not any("FROM size_decisions" in sql for sql in backend.sql)


def test_resolve_groups_keeps_the_decision_with_and_without_the_ounce_rule() -> None:
    from precios_supermercados.product_homologation_persistence import records_from_product_rows
    from precios_supermercados.size_decisions import decision_from_row, index_decisions

    profiles = rules.build_profiles([record for _, record in records_from_product_rows(DECIDED_PRODUCTS)])
    decided = index_decisions(decision_from_row(row) for row in DECISION_ROWS)
    for ounce_rule in (True, False):
        groups = {group["gtin"]: group for group in rules.resolve_groups(profiles, ounce_rule=ounce_rule,
                                                                         size_decisions=decided)}
        assert groups[MCCORMICK]["status"] == "ready" and groups[MCCORMICK]["decision"] is decided[MCCORMICK]
        # v2.9: el oficial 180 ml coincide con las ofertas en 180 g (g ≈ ml en aderezos).
        assert {m.record.supermarket_id for m in groups[MCCORMICK]["remaining"]} == {
            "colonial", "la_colonia", "paiz", "walmart"}
        base = {group["gtin"]: group for group in rules.resolve_groups(
            profiles, ounce_rule=ounce_rule, size_decisions=decided, size_rules=False)}
        assert {m.record.supermarket_id for m in base[MCCORMICK]["remaining"]} == {"colonial", "la_colonia"}
    plain = {group["gtin"]: group for group in rules.resolve_groups(profiles)}
    assert plain[MCCORMICK]["status"] == "ready" and plain[MCCORMICK]["decision"] is None  # regla g ≈ ml
    v2_8 = {group["gtin"]: group for group in rules.resolve_groups(profiles, size_rules=False)}
    assert v2_8[MCCORMICK]["status"] == "review_required"
