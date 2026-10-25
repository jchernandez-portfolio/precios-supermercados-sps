from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "b2c" / "catalog.js"


def test_basket_analysis_and_exact_refresh_fail_closed(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node_not_available_for_b2c_basket_contract")
    module = tmp_path / "catalog.mjs"
    module.write_text(CATALOG.read_text(encoding="utf-8"), encoding="utf-8")
    script = r'''
import assert from "node:assert/strict";
const c = await import(process.argv[1]);
function offer(id,retailer,price,state="neutral",fresh="FRESH") { return {source_product_id:id,supermarket_id:retailer,location_id:`${retailer}_sps`,current_price:price,reported_regular_price:null,is_promotion:false,availability:"in_stock",observed_at:"2026-09-10T00:00:00Z",freshness_status:fresh,relative_price_state:state}; }
const milk={row_id:"milk",canonical_product_id:"milk",comparability:"comparable",product_name:"Leche",brand:"Sula",presentation:"1 L",offers:[offer("w:milk","walmart","10.00","highest"),offer("lc:milk","la_colonia","9.00","best")]};
const bread={row_id:"bread",canonical_product_id:"bread",comparability:"comparable",product_name:"Pan",brand:"Bimbo",presentation:"1 u",offers:[offer("w:bread","walmart","20.00","best"),offer("lc:bread","la_colonia","22.00","highest")]};
const rows=new Map([["milk",milk],["bread",bread]]);
const cart=[c.lineFromOffer(milk,milk.offers[0],2,"catalog/a.json"),c.lineFromOffer(bread,bread.offers[1],1,"catalog/b.json")];
const analysis=c.analyzeBasketOptions(cart,rows);
assert.equal(analysis.status,"AVAILABLE");
assert.equal(analysis.manual.grand_total_minor,4200);
assert.equal(analysis.optimized.status,"COMPLETE");
assert.equal(analysis.optimized.total_minor,3800);
assert.equal(analysis.optimized.savings_vs_manual_minor,400);
assert.equal(analysis.optimized.retailer_count,2);
assert.deepEqual(analysis.optimized.selected.map(x=>x.source_product_id).sort(),["lc:milk","w:bread"]);
const walmart=analysis.single_retailer.find(x=>x.supermarket_id==="walmart");
assert.equal(walmart.status,"COMPLETE"); assert.equal(walmart.total_minor,4000);
const colonial=analysis.single_retailer.find(x=>x.supermarket_id==="colonial");
assert.equal(colonial.status,"INCOMPLETE"); assert.equal(colonial.total_minor,null); assert.equal(colonial.covered_count,0);

const changed=structuredClone(milk); changed.offers[0].current_price="11.00";
const changedRows=new Map([["milk",changed],["bread",bread]]);
assert.equal(c.detectCartUpdates(cart,changedRows)[0].status,"price_changed");
assert.equal(c.analyzeBasketOptions(cart,changedRows).status,"REFRESH_REQUIRED");
const refreshed=c.refreshCartPrices(cart,changedRows);
assert.equal(refreshed[0].source_product_id,"w:milk");
assert.equal(refreshed[0].supermarket_id,"walmart");
assert.equal(refreshed[0].quantity,2);
assert.equal(refreshed[0].unit_price_minor,1100);

const missingRows=new Map([["milk",{...milk,offers:[milk.offers[1]]}],["bread",bread]]);
const missing=c.refreshCartPrices(cart,missingRows);
assert.equal(missing[0].invalid,true);
assert.equal(missing[0].source_product_id,"w:milk");
assert.equal(c.cartSummary(missing).grand_total_minor,null);

const staleCart=structuredClone(cart); staleCart[0].freshness_status="STALE";
const staleRows=new Map([["milk",{...milk,offers:[{...milk.offers[0],freshness_status:"STALE"},milk.offers[1]]}],["bread",bread]]);
assert.equal(c.analyzeBasketOptions(staleCart,staleRows).status,"COMPARISON_BLOCKED");
'''
    completed = subprocess.run(
        [node, "--input-type=module", "-e", script, module.as_uri()],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
