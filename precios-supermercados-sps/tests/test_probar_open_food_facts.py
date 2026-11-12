"""Prueba de enriquecimiento con Open Food Facts: muestra, clasificación y reporte (sin red)."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("probar_open_food_facts", ROOT / "scripts" / "probar_open_food_facts.py")
off = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = off
SPEC.loader.exec_module(off)


class Backend:
    def __init__(self, rows):
        self.rows, self.sql = rows, []

    def query(self, sql, args=()):
        self.sql.append(sql)
        return self.rows


def test_codes_and_origin() -> None:
    assert off.off_code("00021000643592") == "0021000643592"
    assert off.off_code("07421001606009") == "7421001606009"
    assert off.origin("07421001606009") == "honduras_742"
    assert off.origin("00021000643592") == "otros_prefijos"


def test_fetch_gtins_keeps_only_valid_codes_once_and_only_selects() -> None:
    backend = Backend([("walmart", "7421001606009"), ("paiz", "7421001606009"), ("colonial", "123"),
                       ("la_colonia", "021000643592")])
    assert off.fetch_gtins(backend) == [("00021000643592", "la_colonia"), ("07421001606009", "walmart")]
    assert all(sql.startswith("SELECT") for sql in backend.sql)


def test_sample_is_stratified_and_reproducible() -> None:
    gtins = [(f"{index:014d}", "walmart" if index % 4 else "paiz") for index in range(400)]
    first, second = off.sample(gtins, 40, 7), off.sample(gtins, 40, 7)
    assert first == second and len(first) == 40
    assert sum(1 for _, store in first if store == "paiz") == 10
    with pytest.raises(off.ProbeError):
        off.sample([], 10, 1)


def test_probe_classifies_found_useful_missing_and_errors() -> None:
    answers = {
        "0021000643592": {"status": 1, "product": {"product_name": "Ranch", "brands": "Kraft", "quantity": "8 fl oz"}},
        "7421001606009": {"status": 1, "product": {"product_name": "Margarina", "brands": ""}},
        "7421000000013": {"status": 0},
    }
    waits = []

    def get(url):
        code = url.split("/product/")[1].split(".json")[0]
        if code == "0000000000017":
            raise TimeoutError
        return answers[code]
    items = [("00021000643592", "walmart"), ("07421001606009", "la_colonia"), ("07421000000013", "colonial"),
             ("00000000000017", "paiz")]
    rows = off.probe(items, get, delay=1.0, sleep=waits.append, log=lambda _: None)
    assert waits == [1.0, 1.0, 1.0]  # una consulta a la vez, con pausa
    summary = off.report(rows, universe=4, source="prueba")
    total = summary["total"]
    assert total["consultados"] == 4 and total["errores_de_red"] == 1
    assert total["encontrados"] == 2 and total["utiles_nombre_marca_y_cantidad"] == 1
    assert total["pct_encontrados"] == 66.7 and total["pct_utiles_nombre_marca_y_cantidad"] == 33.3
    assert summary["por_origen"]["honduras_742"]["encontrados"] == 1
    json.dumps(summary)  # serializable


def test_delay_below_one_second_is_refused(tmp_path: Path) -> None:
    gtins = tmp_path / "g.csv"
    gtins.write_text("gtin,supermercado\n0021000643592,walmart\n", encoding="utf-8")
    with pytest.raises(off.ProbeError, match="delay_minimo"):
        off.main(["--gtins-file", str(gtins), "--output-dir", str(tmp_path / "o"), "--delay", "0.2"])
