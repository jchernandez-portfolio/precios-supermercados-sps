"""Modo sesión: preparar tandas para Claude, guardar sólo lo aprobado y mostrarlo como resuelto por IA."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image  # noqa: E402

import guardar_revision_claude as guardar  # noqa: E402
import preparar_revision_claude as preparar  # noqa: E402
import sugerir_tamanos_ia as ia  # noqa: E402

A, B = ("a" * 24) + "-1111111111", ("b" * 24) + "-2222222222"
MIGO = "00012656001065"


def offer(store: str, size: str | None, foto: str | None, foto_de: str | None = None) -> dict:
    return {"store": store, "name": f"Maíz {store} 2 lb", "code": "12656001065", "size": size, "value": None,
            "unit": "g", "foto": foto, "foto_de": foto_de or store}


def item(gtin: str, kind: str = "empate", status: str | None = None) -> dict:
    data = {"gtin": gtin, "kind": kind, "name": "Maíz El Migo", "offers": [
        offer("walmart", "907.18 g", A), offer("paiz", "1000 g", A, "walmart"), offer("colonial", "908 g", None)]}
    if status:
        data["ia"] = {"status": status}
    return data


def webp() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (800, 600), "orange").save(output, "WEBP")
    return output.getvalue()


def test_pending_puts_requested_codes_first_then_ties_and_skips_auto_and_seen() -> None:
    queue = {"items": [item("00000000000001", "atipico"), item("00000000000002"), item(MIGO, "atipico"),
                       item("00000000000003", status="auto"), item("00000000000004"), {"gtin": "x"}]}
    chosen = preparar.pending(queue, {"00000000000004": {}}, 10, first=[MIGO])
    assert [entry["gtin"] for entry in chosen] == [MIGO, "00000000000002", "00000000000001"]
    assert len(preparar.pending(queue, {}, 2)) == 2


def test_labels_mark_shared_photos_and_missing_ones() -> None:
    walmart, paiz, colonial = item(MIGO)["offers"]
    assert preparar.offer_label(walmart)[0] == "Walmart · publicado: 907.18 g"
    assert preparar.offer_label(paiz)[0] == "Paiz · publicado: 1000 g · foto compartida (de Walmart)"
    assert preparar.offer_label(colonial)[0] == "Colonial · publicado: 908 g · sin foto"


def test_prepare_downloads_each_photo_once_and_writes_one_jpg_per_code(tmp_path: Path) -> None:
    urls: list[str] = []

    def fetch(url: str) -> bytes:
        urls.append(url)
        return webp()

    queue = {"generated_utc": "T", "items": [item(MIGO), item("00000000000002")]}
    document = preparar.prepare(queue, tmp_path, n=1, first=[MIGO], fetch=fetch, sleep=lambda _: None)
    assert [entry["gtin"] for entry in document["items"]] == [MIGO]
    assert urls == [preparar.photo_url(A)]  # foto compartida: una sola descarga
    image = Image.open(tmp_path / f"{MIGO}.jpg")
    assert image.format == "JPEG" and image.width == 3 * preparar.TILE
    assert json.loads((tmp_path / preparar.BATCH_FILE).read_text())["items"][0]["offers"][1]["foto_de"] == "walmart"


def test_only_public_urls_are_fetched() -> None:
    with pytest.raises(ValueError, match="url_no_permitida"):
        preparar.fetch_bytes("https://example.com/x")


def results(*rows: dict) -> dict:
    return {"items": list(rows)}


def test_save_builds_only_approved_rows_and_marks_every_reviewed_code_as_seen(tmp_path: Path) -> None:
    batch = {"items": [{"gtin": MIGO}, {"gtin": "00000000000002"}, {"gtin": "00000000000003"},
                       {"gtin": "00000000000004"}]}
    rows = results(
        {"gtin": MIGO, "propuesta": "size", "value": "907.18", "unit": "g", "aprobado": True,
         "lecturas": {"walmart": "2 lb (907 g)", "paiz": "2 lb"}, "razon": "dos fotos legibles dicen 2 lb"},
        {"gtin": "00000000000002", "propuesta": "different_products", "aprobado": True, "razon": "otra marca"},
        {"gtin": "00000000000003", "propuesta": "size", "value": "500", "unit": "g", "aprobado": False},
        {"gtin": "00000000000004", "propuesta": "no_legible", "aprobado": True},
    )
    document, reviewed = guardar.build(rows, batch)
    assert [d["gtin"] for d in document["decisions"]] == [MIGO, "00000000000002"]
    assert document["decisions"][0] == {
        "gtin": MIGO, "decision": "size", "value": "907.18", "unit": "g",
        "note": "Claude (sesión): Walmart 2 lb (907 g); Paiz 2 lb — dos fotos legibles dicen 2 lb"}
    assert reviewed == [MIGO, "00000000000002", "00000000000003", "00000000000004"]
    guardar.mark_seen(tmp_path, rows, reviewed, "T")
    seen = preparar.load_seen(tmp_path / preparar.SEEN_FILE)
    assert seen["00000000000003"]["estado"] == "descartado" and seen["00000000000004"]["estado"] == "no_legible"


def test_save_refuses_codes_outside_the_batch() -> None:
    with pytest.raises(SystemExit, match="resultado_invalido"):
        guardar.build(results({"gtin": "00000000000009", "propuesta": "size", "aprobado": True}),
                      {"items": [{"gtin": MIGO}]})


def test_main_without_send_only_validates(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / preparar.BATCH_FILE).write_text(json.dumps({"items": [{"gtin": MIGO}]}))
    (tmp_path / guardar.RESULTS_FILE).write_text(json.dumps(results(
        {"gtin": MIGO, "propuesta": "size", "value": "907.18", "unit": "g", "aprobado": True})))
    monkeypatch.setattr(guardar, "send", lambda path: pytest.fail("sin --enviar no se lanza nada"))
    assert guardar.main(["--dir", str(tmp_path)]) == 0
    assert json.loads((tmp_path / guardar.SEND_FILE).read_text())["decisions"][0]["gtin"] == MIGO
    assert not (tmp_path / preparar.SEEN_FILE).exists()


def test_engine_items_from_the_exporter_join_the_ai_history() -> None:
    history = [{"gtin": "1", "resuelto_utc": "2026-11-12"}]
    extra = [{"gtin": "1", "resuelto_utc": "2026-11-14"},
             {"gtin": "2", "resuelto_utc": "2026-11-13", "ia": {"status": "auto", "fuente": "claude_sesion"}}]
    merged = ia.merge_engine_items(history, extra)
    assert [entry["gtin"] for entry in merged] == ["2", "1"]
    assert ia.merge_engine_items(history, None) == history
