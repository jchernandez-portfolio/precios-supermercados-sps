from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
B2C = ROOT / "b2c"
BASE_CATALOG = B2C / "catalog.js"
TGU_CATALOG = B2C / "catalog-tgu.js"
TGU_HTML = B2C / "tegucigalpa" / "index.html"
SPS_HTML = B2C / "index.html"

sys.path.insert(0, str(SCRIPTS))
BASE_SPEC = importlib.util.spec_from_file_location("exportar_consumer_catalog", SCRIPTS / "exportar_consumer_catalog.py")
assert BASE_SPEC is not None and BASE_SPEC.loader is not None
BASE = importlib.util.module_from_spec(BASE_SPEC)
sys.modules[BASE_SPEC.name] = BASE
BASE_SPEC.loader.exec_module(BASE)

TGU_SPEC = importlib.util.spec_from_file_location("exportar_consumer_catalog_tgu", SCRIPTS / "exportar_consumer_catalog_tgu.py")
assert TGU_SPEC is not None and TGU_SPEC.loader is not None
TGU = importlib.util.module_from_spec(TGU_SPEC)
sys.modules[TGU_SPEC.name] = TGU
TGU_SPEC.loader.exec_module(TGU)


def offer(source: str, supermarket: str, location: str, canonical: str = "gtin:test"):
    return BASE.VisibleOffer(
        source_product_id=source,
        supermarket_id=supermarket,
        location_id=location,
        product_name="Producto",
        brand="Marca",
        presentation="1 L",
        current_price_minor=1000,
        reported_regular_price_minor=None,
        is_promotion=False,
        availability="in_stock",
        observed_at="2026-09-10T12:00:00Z",
        canonical_product_id=canonical,
        category="Alimentos",
        product_type="Producto",
        presentation_dimension="volume_ml",
        presentation_total_base="1000",
        presentation_status="confirmed",
        comparison_status="ready",
    )


def test_tgu_scope_accepts_multiple_locations_for_same_chain_and_rejects_exact_duplicates() -> None:
    scope = TGU.CityCatalogScope(TGU.TGU_SCOPE)
    assert scope.locations == TGU.TGU_SCOPE
    assert scope.supermarket_ids == ("la_colonia", "walmart", "pricesmart", "paiz")
    assert scope.locations.count(("walmart", "walmart_tgu_ffaa")) == 1
    assert scope.locations.count(("walmart", "walmart_tgu_el_sauce")) == 1
    assert scope.locations.count(("paiz", "paiz_tgu_multiplaza")) == 1
    assert scope.locations.count(("paiz", "paiz_tgu_proceres")) == 1

    with pytest.raises(TGU.ExportError, match="consumer_catalog_tgu_scope_context_duplicate"):
        TGU.CityCatalogScope((("walmart", "walmart_tgu_ffaa"), ("walmart", "walmart_tgu_ffaa")))


def test_tgu_same_chain_branches_do_not_create_cross_retailer_comparison() -> None:
    walmart_ffaa = offer("w:1", "walmart", "walmart_tgu_ffaa")
    walmart_sauce = offer("w:1", "walmart", "walmart_tgu_el_sauce")
    groups = TGU.identity_groups_exact_context((walmart_ffaa, walmart_sauce))
    assert len(groups) == 2
    assert all(mode == "individual" for mode, _ in groups)
    assert {group[0].location_id for _, group in groups} == {"walmart_tgu_ffaa", "walmart_tgu_el_sauce"}

    la_colonia = offer("lc:1", "la_colonia", "la_colonia_tgu")
    groups = TGU.identity_groups_exact_context((walmart_ffaa, walmart_sauce, la_colonia))
    assert len(groups) == 1
    assert groups[0][0] == "comparable"
    assert {item.supermarket_id for item in groups[0][1]} == {"walmart", "la_colonia"}


def test_tgu_public_offer_identity_includes_exact_location() -> None:
    assert TGU.public_source_id("walmart:123", "walmart_tgu_ffaa") == "walmart:123@walmart_tgu_ffaa"
    assert TGU.public_source_id("walmart:123", "walmart_tgu_el_sauce") == "walmart:123@walmart_tgu_el_sauce"
    assert TGU.public_source_id("walmart:123", "walmart_tgu_ffaa") != TGU.public_source_id(
        "walmart:123", "walmart_tgu_el_sauce"
    )


def test_tgu_duplicate_offer_in_same_exact_context_degrades_to_individual_rows() -> None:
    first = offer("w:1", "walmart", "walmart_tgu_ffaa")
    duplicate = offer("w:2", "walmart", "walmart_tgu_ffaa")
    groups = TGU.identity_groups_exact_context((first, duplicate))
    assert len(groups) == 2
    assert all(mode == "individual" and len(group) == 1 for mode, group in groups)
    assert {group[0].source_product_id for _, group in groups} == {"w:1", "w:2"}


def test_tgu_ambiguous_context_blocks_entire_canonical_group_from_comparison() -> None:
    first = offer("w:1", "walmart", "walmart_tgu_ffaa")
    duplicate = offer("w:2", "walmart", "walmart_tgu_ffaa")
    other_chain = offer("lc:1", "la_colonia", "la_colonia_tgu")
    groups = TGU.identity_groups_exact_context((first, duplicate, other_chain))
    assert len(groups) == 3
    assert all(mode == "individual" and len(group) == 1 for mode, group in groups)
    assert {group[0].source_product_id for _, group in groups} == {"w:1", "w:2", "lc:1"}


def test_tgu_frontend_contract_keeps_cities_and_carts_separate(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node_not_available_for_b2c_tgu_contract")

    base = tmp_path / "catalog.js"
    tgu = tmp_path / "catalog-tgu.js"
    base.write_text(BASE_CATALOG.read_text(encoding="utf-8"), encoding="utf-8")
    tgu.write_text(TGU_CATALOG.read_text(encoding="utf-8"), encoding="utf-8")
    script = r'''
import assert from "node:assert/strict";
const c = await import(process.argv[1]);
assert.equal(c.CART_KEY,"rpi-mi-compra/tegucigalpa/v1");
assert.equal(c.RETAILERS.length,6);
assert.equal(new Set(c.RETAILERS.map(x=>x.supermarket_id)).size,6);
const scope=c.RETAILERS.map(x=>({supermarket_id:x.source_supermarket_id,location_id:x.location_id}));
const manifest={schema:"rpi-consumer-catalog-manifest/v3",catalog_schema:"rpi-consumer-catalog/v3",location:{city:"Tegucigalpa",country_code:"HN"},scope,retailers:c.RETAILERS.map(x=>({supermarket_id:x.source_supermarket_id,location_id:x.location_id,name:x.name})),files:[{path:"facets-tgu.json",sha256:"a".repeat(64),bytes:1}],initial_payload:{request_count:2}};
assert.equal(c.manifestIsCompatible(manifest),true);
assert.equal(c.manifestIsCompatible({...manifest,location:{city:"San Pedro Sula",country_code:"HN"}}),false);
const row={offers:[
 {source_product_id:"w:1@walmart_tgu_ffaa",supermarket_id:"walmart",location_id:"walmart_tgu_ffaa"},
 {source_product_id:"w:1@walmart_tgu_el_sauce",supermarket_id:"walmart",location_id:"walmart_tgu_el_sauce"},
]};
const grouped=c.offersByRetailer(row);
assert.equal(grouped.get("walmart_tgu_ffaa").location_id,"walmart_tgu_ffaa");
assert.equal(grouped.get("walmart_tgu_el_sauce").location_id,"walmart_tgu_el_sauce");
const lines=[
 {source_product_id:"w:1@walmart_tgu_ffaa",supermarket_id:"walmart",location_id:"walmart_tgu_ffaa",quantity:1,unit_price_minor:1000,freshness_status:"FRESH",invalid:false},
 {source_product_id:"w:1@walmart_tgu_el_sauce",supermarket_id:"walmart",location_id:"walmart_tgu_el_sauce",quantity:1,unit_price_minor:1100,freshness_status:"FRESH",invalid:false},
];
const summary=c.cartSummary(lines);
assert.equal(summary.retailer_count,2);
assert.equal(summary.retailers.get("walmart_tgu_ffaa").subtotal_minor,1000);
assert.equal(summary.retailers.get("walmart_tgu_el_sauce").subtotal_minor,1100);
'''
    completed = subprocess.run(
        [node, "--input-type=module", "-e", script, tgu.as_uri()],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    sps_html = SPS_HTML.read_text(encoding="utf-8")
    tgu_html = TGU_HTML.read_text(encoding="utf-8")
    assert 'data-city-name="San Pedro Sula"' in sps_html
    assert 'data-city-name="Tegucigalpa"' in tgu_html
    assert 'value="tegucigalpa"' in sps_html and 'value="san-pedro-sula"' in tgu_html
    assert "cities/tegucigalpa/manifest.json" in tgu_html
    assert '"../catalog.js":"../catalog-tgu.js"' in tgu_html
