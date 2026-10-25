from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from actualizar_mvp_turso_paiz import _normalised_json, validate_snapshot_bytes  # noqa: E402
from generar_mvp_sqlite_la_colonia import create_schema  # noqa: E402
from migrar_mvp_paiz import (  # noqa: E402
    locations_index_ready_sql,
    migrate_sqlite,
    schema_ready_sql,
)

from _evidencia import asegurar  # noqa: E402

asegurar("reports/paiz/2026-09-04-full/snapshot-paiz-multiplaza.json", "reports/paiz/2026-09-04-full/snapshot-paiz-proceres.json")

REPORT = ROOT / "reports" / "paiz" / "2026-09-04-full"

@pytest.mark.parametrize(
    ("filename", "location_id", "skus", "priced"),
    [
        ("snapshot-paiz-multiplaza.json", "paiz_tgu_multiplaza", 8868, 8663),
        ("snapshot-paiz-proceres.json", "paiz_tgu_proceres", 8571, 8366),
    ],
)
def test_accepted_paiz_snapshots_validate(filename: str, location_id: str, skus: int, priced: int) -> None:
    snapshot = validate_snapshot_bytes((REPORT / filename).read_bytes())
    assert snapshot["location_id"] == location_id
    assert snapshot["skus_extracted"] == skus
    assert snapshot["skus_with_price"] == priced
    assert snapshot["availability_counts"]["out_of_stock"] == 205

def test_normalizer_preserves_nullable_unpriced_offer() -> None:
    snapshot = validate_snapshot_bytes((REPORT / "snapshot-paiz-multiplaza.json").read_bytes())
    unpriced = next(row for row in snapshot["products"] if row["current_price"] is None)
    normalized = json.loads(_normalised_json({**snapshot, "products": [unpriced]}))
    assert normalized == [{
        "source_key_type": unpriced["source_key_type"],
        "source_key": unpriced["source_key"],
        "source_catalog_product_id": unpriced["product_id"],
        "source_item_id": unpriced["item_id"],
        "reference": unpriced["reference"],
        "ean": unpriced["ean"],
        "name": unpriced["source_name"],
        "brand": unpriced["brand"],
        "presentation": unpriced["presentation"],
        "category": unpriced["category"],
        "current_price_minor": None,
        "reported_regular_price_minor": None,
        "is_promotion": None,
        "availability": "out_of_stock",
    }]

def test_paiz_migration_preserves_existing_rows_and_allows_unpriced(tmp_path: Path) -> None:
    db = tmp_path / "mvp.sqlite"
    con = sqlite3.connect(db)
    create_schema(con)
    con.execute("INSERT INTO supermarkets VALUES('la_colonia','La Colonia','HN')")
    con.execute("INSERT INTO locations VALUES('la_colonia_sps','la_colonia','San Pedro Sula','HN')")
    con.execute("INSERT INTO products VALUES(1,'la_colonia','sku','1','1','1',NULL,NULL,'Producto',NULL,NULL,NULL)")
    con.execute("INSERT INTO scrape_runs VALUES('existing','la_colonia','la_colonia_sps','2026-09-04T00:00:00Z','success',1,1,NULL,NULL,NULL)")
    con.execute("INSERT INTO price_history VALUES(1,'la_colonia','la_colonia_sps',100,NULL,0,'in_stock','HNL','2026-09-04T00:00:00Z',NULL,'existing')")
    con.commit()
    con.close()

    result = migrate_sqlite(db)
    assert result["migrated"] is True

    con = sqlite3.connect(db)
    try:
        sql = con.execute("SELECT sql FROM sqlite_master WHERE name='price_history'").fetchone()[0]
        location_index_sql = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_locations_city_legacy'"
        ).fetchone()[0]
        assert schema_ready_sql(sql)
        assert locations_index_ready_sql(location_index_sql)
        assert con.execute("SELECT COUNT(*) FROM price_history").fetchone() == (1,)
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        assert con.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert con.execute("SELECT location_id FROM locations WHERE supermarket_id='paiz' ORDER BY location_id").fetchall() == [
            ("paiz_tgu_multiplaza",), ("paiz_tgu_proceres",)
        ]
        con.execute("INSERT INTO products VALUES(2,'paiz','item_id','2','2','2',NULL,NULL,'Agotado',NULL,NULL,NULL)")
        con.execute("INSERT INTO scrape_runs VALUES('paiz-test','paiz','paiz_tgu_multiplaza','2026-09-04T01:00:00Z','success',1,1,NULL,NULL,NULL)")
        con.execute("INSERT INTO price_history VALUES(2,'paiz','paiz_tgu_multiplaza',NULL,NULL,NULL,'out_of_stock','HNL','2026-09-04T01:00:00Z',NULL,'paiz-test')")
        con.commit()
    finally:
        con.close()

def test_paiz_migration_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "mvp.sqlite"
    con = sqlite3.connect(db)
    create_schema(con)
    con.commit()
    con.close()
    assert migrate_sqlite(db)["migrated"] is True
    assert migrate_sqlite(db)["migrated"] is False
