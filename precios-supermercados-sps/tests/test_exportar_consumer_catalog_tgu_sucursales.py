"""Tegucigalpa: una tarjeta por producto (no por sucursal) y llaves viejas publicadas."""
from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import exportar_consumer_catalog_core as core  # noqa: E402
import exportar_consumer_catalog_tgu as tgu  # noqa: E402
from exportar_modelo_analitico import ExportError  # noqa: E402

BASE_OFFER = core.VisibleOffer(
    source_product_id="walmart:51@walmart_tgu_ffaa", supermarket_id="walmart", location_id="walmart_tgu_ffaa",
    product_name="Desodorante Rexona Clinical - 58 g", brand="Rexona", presentation="58 g",
    current_price_minor=10_000, reported_regular_price_minor=None, is_promotion=False, availability="in_stock",
    observed_at="2026-11-11T12:00:00Z", canonical_product_id=None, category="Cuidado personal",
    product_type="Desodorante", presentation_dimension="mass_g", presentation_total_base="58",
    presentation_status="name_only", comparison_status="unmapped", source_category=None,
)


def offer(source: str, location: str, **changes) -> core.VisibleOffer:
    supermarket = source.split(":", 1)[0]
    return replace(BASE_OFFER, source_product_id=f"{source}@{location}", supermarket_id=supermarket,
                   location_id=location, **changes)


def test_branches_of_a_product_share_one_card() -> None:
    groups = tgu.identity_groups_exact_context([
        offer("walmart:51", "walmart_tgu_ffaa"), offer("walmart:51", "walmart_tgu_el_sauce", current_price_minor=11_000),
        offer("paiz:9", "paiz_tgu_proceres", comparison_status="single_source"),
        offer("paiz:9", "paiz_tgu_multiplaza", comparison_status="single_source"),
    ])
    assert [(mode, sorted(o.location_id for o in group)) for mode, group in groups] == [
        ("individual", ["walmart_tgu_el_sauce", "walmart_tgu_ffaa"]),
        ("single_source", ["paiz_tgu_multiplaza", "paiz_tgu_proceres"]),
    ]


def test_identity_with_one_chain_is_one_card_with_its_branches() -> None:
    ready = {"comparison_status": "ready", "canonical_product_id": "prod_gtin_07501019032424"}
    groups = tgu.identity_groups_exact_context([
        offer("walmart:51", "walmart_tgu_ffaa", **ready), offer("walmart:51", "walmart_tgu_el_sauce", **ready),
    ])
    assert [(mode, len(group)) for mode, group in groups] == [("individual", 2)]


def test_comparable_groups_and_ambiguous_contexts_keep_their_rules() -> None:
    ready = {"comparison_status": "ready", "canonical_product_id": "prod_gtin_07501019032424"}
    comparable = tgu.identity_groups_exact_context([
        offer("walmart:51", "walmart_tgu_ffaa", **ready), offer("paiz:9", "paiz_tgu_proceres", **ready),
    ])
    assert [mode for mode, _ in comparable] == ["comparable"]
    # Dos productos con la misma identidad en una sucursal: no se elige ninguno;
    # cada producto queda en su propia tarjeta con todas sus sucursales.
    ambiguous = tgu.identity_groups_exact_context([
        offer("walmart:51", "walmart_tgu_ffaa", **ready), offer("walmart:52", "walmart_tgu_ffaa", **ready),
        offer("walmart:51", "walmart_tgu_el_sauce", **ready), offer("paiz:9", "paiz_tgu_proceres", **ready),
    ])
    assert sorted((mode, len(group)) for mode, group in ambiguous) == [
        ("individual", 1), ("individual", 1), ("individual", 2)]


def test_duplicate_offer_for_one_branch_fails_closed() -> None:
    with pytest.raises(ExportError, match="branch_offer_duplicate"):
        tgu.identity_groups_exact_context([offer("walmart:51", "walmart_tgu_ffaa")] * 2)


def test_card_id_drops_the_branch_and_matches_sps() -> None:
    assert core.public_product_id("walmart:51@walmart_tgu_ffaa") == "walmart:51"
    assert core.public_product_id("walmart:51") == "walmart:51"
    expected = "source-" + hashlib.sha256(b"walmart:51").hexdigest()[:24]
    assert core._row_id("source", core.public_product_id("walmart:51@walmart_tgu_el_sauce")) == expected


def test_legacy_ids_map_new_cards_to_their_old_branch_keys(tmp_path: Path) -> None:
    new = hashlib.sha256(b"walmart:51").hexdigest()[:24]
    (tmp_path / "catalog").mkdir()
    (tmp_path / "catalog" / "p0.json").write_text(json.dumps({"rows": [
        {"row_id": f"source-{new}", "offers": [{"source_product_id": "walmart:51@walmart_tgu_ffaa"},
                                               {"source_product_id": "walmart:51@walmart_tgu_el_sauce"}]},
        {"row_id": "product-abc", "offers": [{"source_product_id": "paiz:9@paiz_tgu_proceres"}]},
    ]}), encoding="utf-8")
    legacy = tgu.legacy_row_ids(tmp_path, {"files": [{"path": "search.json"}, {"path": "catalog/p0.json"}]})
    old = sorted(hashlib.sha256(f"walmart:51@{place}".encode()).hexdigest()[:12]
                 for place in ("walmart_tgu_ffaa", "walmart_tgu_el_sauce"))
    assert legacy == {new: old}


def test_tgu_export_has_one_card_per_product_and_publishes_old_keys(tmp_path: Path) -> None:
    import _product_master_fixtures as fx
    from test_product_master_exporter_parity import _export_all, _rows

    db = tmp_path / "db.sqlite"
    fx.build_database(db)
    _export_all(db, tmp_path / "out")
    rows = _rows(tmp_path / "out", "tgu")
    # Cada producto de cada súper aparece en una sola tarjeta (con todas sus sucursales).
    cards_per_product: dict[str, set[str]] = {}
    for row in rows:
        for item in row["offers"]:
            cards_per_product.setdefault(core.public_product_id(str(item["source_product_id"])), set()).add(row["row_id"])
    assert cards_per_product and all(len(cards) == 1 for cards in cards_per_product.values())
    assert any(len({o["location_id"] for o in row["offers"] if o["supermarket_id"] == "walmart"}) == 2 for row in rows)
    for row in rows:
        if str(row["row_id"]).startswith("source-"):
            [product] = {core.public_product_id(str(o["source_product_id"])) for o in row["offers"]}
            assert row["row_id"] == core._row_id("source", product)
    manifest = json.loads((tmp_path / "out" / "tgu" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["legacy_ids_file"] == tgu.LEGACY_IDS_FILE
    entry = next(item for item in manifest["files"] if item["path"] == tgu.LEGACY_IDS_FILE)
    content = (tmp_path / "out" / "tgu" / tgu.LEGACY_IDS_FILE).read_bytes()
    assert entry["sha256"] == hashlib.sha256(content).hexdigest()
    legacy = json.loads(content)
    assert legacy["schema"] == tgu.LEGACY_IDS_SCHEMA and legacy["prefix_length"] == 12
    assert set(legacy["rows"]) == {str(row["row_id"]).split("-", 1)[1] for row in rows
                                   if str(row["row_id"]).startswith("source-")}
    sps = json.loads((tmp_path / "out" / "sps" / "manifest.json").read_text(encoding="utf-8"))
    assert "legacy_ids_file" not in sps
