"""Auditoría del catálogo público publicado (scripts/auditar_catalogo_publico.py)."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "auditar_catalogo_publico.py"
SPEC = importlib.util.spec_from_file_location("auditar_catalogo_publico", SCRIPT)
audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)


def test_title_flags() -> None:
    assert audit.title_flags("4VIENTOS ChileHabanExtrHot 140", None) == ["palabras_pegadas", "numero_sin_unidad"]
    assert audit.title_flags("TABORA PAN DE YEMA 4 UNIDADES", "4 unidades") == ["mayusculas"]
    assert "titulo_duplicado" in audit.title_flags(
        "Martini Extra Dry de 750ML Martini Extra Dry 750 ML Botella", "750 ml")
    assert audit.title_flags("Leche Sula Entera 946 ml", "946 ml") == []


def test_title_against_presentation() -> None:
    assert audit.title_contradicts_presentation("Now Magnesio 180 Cápsulas / 400 mg / 0.01 oz", "72 g")
    assert not audit.title_contradicts_presentation("Frijol La Chula 48 Oz", "1.361 kg")
    assert not audit.title_contradicts_presentation("Aceite Clover Brand 1,400 ml", "1.4 L")
    assert not audit.title_contradicts_presentation("FLOR De Jamaica 1/2 Libra", "226.8 g")
    assert not audit.title_contradicts_presentation("Atún 6 Unidades / 120 g", "6 × 120 g")
    assert not audit.title_contradicts_presentation("Galleta María", "200 g")


def test_implied_total_reads_the_size_used_for_the_unit_price() -> None:
    assert audit.implied_total({"current_price": "50.00", "unit_price": {"amount": "5.00", "per": "100 g"}}) == (
        "100 g", 1000.0)
    assert audit.implied_total({"current_price": "50.00", "unit_price": None}) is None


def _write(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


def test_run_reports_per_pound_duplicates_and_foreign_inherited_photos(tmp_path: Path) -> None:
    offer = {"supermarket_id": "walmart", "location_id": "walmart_tgu_ffaa", "availability": "in_stock",
             "freshness_status": "FRESH", "observed_at": "2026-08-31T00:00:00Z", "current_price": "74.90",
             "unit_price": {"amount": "33.03", "per": "kg"}}
    rows = [
        {"row_id": "source-aaa", "product_name": "Medallon Lomo Cerdo 5 lb - Precio Indicado Por Libra",
         "presentation": "2.268 kg", "comparability": "individual", "product_type": "Cerdo",
         "offers": [{**offer, "source_product_id": "walmart:1@walmart_tgu_ffaa"}]},
        {"row_id": "source-bbb", "product_name": "Medallon Lomo Cerdo 5 lb - Precio Indicado Por Libra",
         "presentation": "2.268 kg", "comparability": "individual", "product_type": "Cerdo",
         "offers": [{**offer, "source_product_id": "walmart:2@walmart_tgu_ffaa"}]},
    ]
    _write(tmp_path / "cities.json", {"cities": [{"slug": "tegucigalpa", "manifest": "c/t/manifest.json"}]})
    _write(tmp_path / "c/t/manifest.json", {"as_of": "2026-11-13T21:00:00Z", "search_file": "search.json",
                                            "files": [{"path": "catalog/x/part-001.json"}]})
    _write(tmp_path / "c/t/catalog/x/part-001.json", {"rows": rows})
    _write(tmp_path / "c/t/search.json", {
        "columns": ["id", "retailers", "comparability", "photo"], "retailers": ["walmart"],
        "rows": [["aaa", [0], "i", "a" * 10], ["bbb", [0], "i", 0]]})
    photos = tmp_path / "index.json"
    _write(photos, {"items": {"aaa": {"h": "a" * 10, "sm": "walmart", "de": "zzz"},
                              "ccc": {"h": "a" * 10, "sm": "walmart"}}})
    report = audit.run(tmp_path, photos)
    counts = report["ciudades"]["tegucigalpa"]["conteos"]
    assert counts["dice_precio_por_libra_y_no_va_por_libra"] == {"walmart": 2}
    assert counts["filas_duplicadas_mismo_super"] == {"walmart": 1}
    assert counts["precio_sin_cambio_desde"] == {"walmart:>=60 d": 2}
    assert report["fotos"]["sin_foto"] == {"tegucigalpa:walmart:i": 1}
    assert report["fotos"]["foto_heredada_de_sucursal"] == {"tegucigalpa": 1}
    # "ccc" no está publicado: no cuenta como dueño de la foto.
    assert "foto_heredada_que_es_de_otro_producto" not in report["fotos"]
