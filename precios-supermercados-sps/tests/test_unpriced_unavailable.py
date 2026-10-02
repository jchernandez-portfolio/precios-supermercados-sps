"""Regla 2026-10-02: SKU listados sin precio y agotados no bloquean la corrida.

Usa los productos reales del incidente (La Colonia, run "Actualización MVP" del
2026-10-02: Dove/Rexona 18658-18661 en SPS y TGU y Dubois 18302 en TGU intento 1).
"""
from __future__ import annotations

import copy
import hashlib
from decimal import Decimal
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from test_colonial_persistence import database  # noqa: F401  (fixture Hrana offline)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import actualizar_mvp_sqlite_la_colonia as sqlite_updater  # noqa: E402
import actualizar_mvp_turso_la_colonia as turso_updater  # noqa: E402
import obtener_catalogo_sps_la_colonia_operativo as operational  # noqa: E402
import verificar_integridad_turso as integrity  # noqa: E402
from precios_supermercados.enums import RunStatus  # noqa: E402
from precios_supermercados.scrapers.la_colonia_operational_artifact import (  # noqa: E402
    assess_operational_catalog_artifact,
)
from precios_supermercados.unpriced_unavailable import (  # noqa: E402
    MAX_UNPRICED_UNAVAILABLE_RATIO,
    UNPRICED_OBSERVATIONS_TABLE,
    unpriced_unavailable_limit,
)

OBSERVED = "2026-10-02T08:30:00Z"

# Filas tal como las emitió el extractor el 2026-10-02 (evidencia del incidente).
EVIDENCE = [
    {"availability": "out_of_stock", "brand": "Dove", "category": "Supermercado > Belleza y Cuidado Personal > Desodorantes Mujer", "current_price": None, "ean": "7791293051659", "is_promotion": False, "item_id": "18658", "presentation": "150Ml", "product_id": "18658", "reference": "7791293051659", "reported_regular_price": None, "source_key": "18658", "source_key_type": "internal_id", "source_name": "Desodorante Corporal Spray Dove Lavender&Camomile 150Ml"},
    {"availability": "out_of_stock", "brand": "Dove", "category": "Supermercado > Belleza y Cuidado Personal > Desodorantes Mujer", "current_price": None, "ean": "7791293051666", "is_promotion": False, "item_id": "18659", "presentation": "150Ml", "product_id": "18659", "reference": "7791293051666", "reported_regular_price": None, "source_key": "18659", "source_key_type": "internal_id", "source_name": "Desodorante Corporal Spray Dove Rasperry & Rose 150Ml"},
    {"availability": "out_of_stock", "brand": "Rexona", "category": "Supermercado > Belleza y Cuidado Personal > Desodorantes Hombres", "current_price": None, "ean": "7791293051581", "is_promotion": False, "item_id": "18660", "presentation": "150 Ml", "product_id": "18660", "reference": "7791293051581", "reported_regular_price": None, "source_key": "18660", "source_key_type": "internal_id", "source_name": "Desodorante Corporal Spray Rexona AcItve Fresh 150 Ml"},
    {"availability": "out_of_stock", "brand": "Rexona", "category": "Supermercado > Belleza y Cuidado Personal > Desodorantes Mujer", "current_price": None, "ean": "7791293051550", "is_promotion": False, "item_id": "18661", "presentation": "150 Ml", "product_id": "18661", "reference": "7791293051550", "reported_regular_price": None, "source_key": "18661", "source_key_type": "internal_id", "source_name": "Desodorante Corporal Spray Rexona Fresh Citrus 150 Ml"},
]
DUBOIS = {"availability": "out_of_stock", "brand": "Dubois", "category": "Supermercado > Cervezas Licores y Vinos > Vinos", "current_price": None, "ean": "8410384005416", "is_promotion": False, "item_id": "18302", "presentation": "750 Ml", "product_id": "18302", "reference": "8410384005416", "reported_regular_price": None, "source_key": "18302", "source_key_type": "internal_id", "source_name": "Vino Espumante Dubois Semi Seco 750 Ml"}


def priced(index: int, *, price: str = "45.50", availability: str = "in_stock") -> dict[str, object]:
    key = str(100000 + index)
    return {
        "availability": availability, "brand": "Marca", "category": "Supermercado > Pruebas",
        "current_price": price, "ean": f"74{index:011d}", "is_promotion": False, "item_id": key,
        "presentation": "500 G", "product_id": key, "reference": f"ref-{key}",
        "reported_regular_price": None, "source_key": key, "source_key_type": "internal_id",
        "source_name": f"Producto {key}",
    }


def unpriced_like(index: int) -> dict[str, object]:
    row = copy.deepcopy(EVIDENCE[index % len(EVIDENCE)])
    key = str(200000 + index)
    row.update(item_id=key, product_id=key, source_key=key, source_name=f"{row['source_name']} #{index}")
    return row


def snapshot_from_capture(
    captured: list[dict[str, object]],
    *,
    location: str = "la_colonia_sps",
    observed_at: str = OBSERVED,
    reported_override: int | None = None,
) -> dict[str, object]:
    """Reproduce el armado del artifact operativo a partir de las filas capturadas."""
    fields = operational.catalog_rows_fields(captured, observed_at_utc=observed_at)
    reported = len({str(row["product_id"]) for row in captured})
    return {
        "result": "success", "supermarket_id": "la_colonia", "location_id": location,
        "city": sqlite_updater.LOCATIONS[location], "catalog_complete": True,
        "validation_passed": True, "location_verified_same_run": True,
        "observed_at_utc": observed_at,
        "catalog_products_reported": reported if reported_override is None else reported_override,
        "unique_products_extracted": reported if reported_override is None else reported_override,
        **fields,
    }


def raw(snapshot: dict[str, object]) -> bytes:
    return json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode()


# --- Adquisición + validación del snapshot ----------------------------------


def test_incident_snapshot_with_real_unpriced_products_is_accepted() -> None:
    captured = [priced(index) for index in range(9_596)] + copy.deepcopy(EVIDENCE)
    snapshot = snapshot_from_capture(captured)

    assert snapshot["skus_extracted"] == 9_596
    assert snapshot["skus_with_price"] == 9_596
    assert snapshot["skus_without_price"] == 0
    assert snapshot["skus_unpriced_unavailable"] == 4
    assert [entry["source_key"] for entry in snapshot["unpriced_unavailable"]] == ["18658", "18659", "18660", "18661"]
    assert all(row["current_price"] is not None for row in snapshot["products"])
    validated = sqlite_updater.validate_snapshot_bytes(raw(snapshot))
    # La completitud del catálogo incluye los productos sin precio.
    assert validated["catalog_products_reported"] == 9_600


def test_incident_tgu_attempt_one_with_dubois_is_accepted() -> None:
    captured = [priced(index) for index in range(9_590)] + copy.deepcopy(EVIDENCE) + [copy.deepcopy(DUBOIS)]
    snapshot = snapshot_from_capture(captured, location="la_colonia_tgu")
    validated = sqlite_updater.validate_snapshot_bytes(raw(snapshot))
    assert validated["skus_unpriced_unavailable"] == 5


def test_legacy_shape_with_unpriced_rows_inside_products_still_fails_closed() -> None:
    """Es exactamente el fallo del 2026-10-02: sin separar, sigue bloqueando."""
    rows = [priced(index) for index in range(200)] + copy.deepcopy(EVIDENCE)
    snapshot = {
        "result": "success", "supermarket_id": "la_colonia", "location_id": "la_colonia_sps",
        "city": "San Pedro Sula", "catalog_complete": True, "validation_passed": True,
        "location_verified_same_run": True, "observed_at_utc": OBSERVED,
        "catalog_products_reported": 204, "unique_products_extracted": 204,
        "skus_extracted": 204, "skus_with_price": 200, "products": rows,
    }
    with pytest.raises(sqlite_updater.SnapshotError, match="snapshot_sku_count_mismatch"):
        sqlite_updater.validate_snapshot_bytes(raw(snapshot))


def test_unpriced_ratio_at_two_percent_passes_and_above_fails() -> None:
    assert MAX_UNPRICED_UNAVAILABLE_RATIO == Decimal("0.02")
    total = 1_000
    limit = unpriced_unavailable_limit(total)
    assert limit == 20
    at_limit = [priced(index) for index in range(total - limit)] + [unpriced_like(i) for i in range(limit)]
    sqlite_updater.validate_snapshot_bytes(raw(snapshot_from_capture(at_limit)))

    above = [priced(index) for index in range(total - limit - 1)] + [unpriced_like(i) for i in range(limit + 1)]
    with pytest.raises(sqlite_updater.SnapshotError, match="snapshot_unpriced_unavailable_above_threshold"):
        sqlite_updater.validate_snapshot_bytes(raw(snapshot_from_capture(above)))


@pytest.mark.parametrize("availability", ["in_stock", "unknown"])
def test_unpriced_sku_that_is_not_out_of_stock_fails_closed(availability: str) -> None:
    captured = [priced(index) for index in range(500)] + copy.deepcopy(EVIDENCE)
    captured[-1]["availability"] = availability
    snapshot = snapshot_from_capture(captured)
    # Acquisition no la separa: queda en products y la validación falla.
    assert snapshot["skus_without_price"] == 1
    with pytest.raises(sqlite_updater.SnapshotError, match="snapshot_sku_count_mismatch"):
        sqlite_updater.validate_snapshot_bytes(raw(snapshot))

    # Una entrada inyectada en la sección que no está agotada también falla.
    forged = snapshot_from_capture([priced(index) for index in range(500)] + copy.deepcopy(EVIDENCE))
    forged["unpriced_unavailable"][0]["availability"] = availability
    with pytest.raises(sqlite_updater.SnapshotError, match="snapshot_unpriced_not_unavailable"):
        sqlite_updater.validate_snapshot_bytes(raw(forged))


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda s: s["unpriced_unavailable"][0].update(current_price=None), "snapshot_unpriced_unavailable_schema_invalid"),
        (lambda s: s.update(skus_unpriced_unavailable=3), "snapshot_unpriced_unavailable_count_mismatch"),
        (lambda s: s.pop("skus_unpriced_unavailable"), "snapshot_unpriced_unavailable_count_mismatch"),
        (lambda s: s["unpriced_unavailable"][0].update(observed_at_utc="2026-10-01T08:30:00Z"), "snapshot_unpriced_unavailable_observed_at_invalid"),
        (lambda s: s["unpriced_unavailable"][0].update(source_key=s["products"][0]["source_key"]), "snapshot_unpriced_unavailable_identity_duplicate"),
        (lambda s: s["unpriced_unavailable"].append(dict(s["unpriced_unavailable"][0])) or s.update(skus_unpriced_unavailable=5), "snapshot_unpriced_unavailable_identity_duplicate"),
        (lambda s: s["unpriced_unavailable"][0].update(source_name=" "), "snapshot_unpriced_unavailable_identity_invalid"),
        # La completitud no puede ignorar los productos sin precio.
        (lambda s: s.update(catalog_products_reported=500, unique_products_extracted=500), "snapshot_product_count_mismatch"),
    ],
)
def test_unpriced_section_schema_is_closed_and_fail_closed(mutate, code: str) -> None:
    snapshot = snapshot_from_capture([priced(index) for index in range(500)] + copy.deepcopy(EVIDENCE))
    mutate(snapshot)
    with pytest.raises(sqlite_updater.SnapshotError, match=code):
        sqlite_updater.validate_snapshot_bytes(raw(snapshot))


def test_snapshots_without_the_section_keep_validating() -> None:
    snapshot = snapshot_from_capture([priced(index) for index in range(10)])
    snapshot.pop("unpriced_unavailable")
    snapshot.pop("skus_unpriced_unavailable")
    sqlite_updater.validate_snapshot_bytes(raw(snapshot))


# --- Artifact operativo y runner ------------------------------------------------


def _operational_artifact(captured: list[dict[str, object]]) -> dict[str, object]:
    snapshot = snapshot_from_capture(captured)
    return {
        **snapshot,
        "schema_version": "7",
        "catalog_type": "la_colonia_sps_full_read_only",
        "capture_strategy": "operational_city_url_safe_brand_buckets_recovery_productSearchV3",
        "partition_strategy": "brand_buckets_authoritative_transport_safe_reverse_recovery",
        "location_verification_method": "structural_exact_city_control",
        "catalog_product_coverage": 1.0,
        "page_size": 50,
        "planned_product_requests": 200,
        "product_requests_completed": 200,
        "partitions_detected": 3,
        "partitions_completed": 3,
        "partition_observed_total_sum": snapshot["catalog_products_reported"],
        "duplicate_skus_across_partitions": 0,
        "catalog_accepted": False,
        "commercial_persistence": False,
        "production_authority": False,
        "extraction_enabled": False,
        "raw_context_persisted": False,
    }


def test_operational_artifact_accepts_unpriced_unavailable_within_threshold() -> None:
    artifact = _operational_artifact([priced(index) for index in range(1_000)] + copy.deepcopy(EVIDENCE))
    assessment = assess_operational_catalog_artifact(artifact)
    assert assessment.blockers == ()
    assert assessment.technical_catalog_complete is True
    assert assessment.unpriced_unavailable == 4
    assert assessment.unique_products == 1_004
    assert "unpriced_unavailable_present" in assessment.warnings
    assert assessment.run_status is RunStatus.WARNING


def test_operational_artifact_blocks_above_threshold_and_unpriced_in_products() -> None:
    above = _operational_artifact([priced(index) for index in range(100)] + [unpriced_like(i) for i in range(3)])
    assert "unpriced_unavailable_above_threshold" in assess_operational_catalog_artifact(above).blockers

    in_products = _operational_artifact([priced(index) for index in range(1_000)])
    in_products["products"][0]["current_price"] = None
    in_products.update(skus_with_price=999, skus_without_price=1)
    blockers = assess_operational_catalog_artifact(in_products).blockers
    assert "current_price_invalid" in blockers
    assert "skus_with_price_mismatch" in blockers
    assert "skus_without_price_nonzero" in blockers

    forged = _operational_artifact([priced(index) for index in range(1_000)] + copy.deepcopy(EVIDENCE))
    forged["unpriced_unavailable"][0]["availability"] = "unknown"
    assert "unpriced_not_unavailable" in assess_operational_catalog_artifact(forged).blockers


def test_runner_counts_unpriced_unavailable_and_bounds_it() -> None:
    import test_la_colonia_runner as harness

    def plans(unpriced: int) -> dict[int, object]:
        products = [harness.make_product(index, price=0 if index < unpriced else 10) for index in range(100)]
        return {
            0: harness.make_payload(0, 50, 100, products=products[:50]),
            50: harness.make_payload(50, 50, 100, products=products[50:]),
        }

    within, _ = harness.run_catalog(plans(2), page_size=50)
    assert within.metrics.skus_without_price == 2
    assert within.metrics.skus_unpriced_unavailable == 2
    assert within.metrics.missing_price_ratio == 0.0
    assert "unpriced_unavailable_above_threshold" not in within.metrics.rejection_reasons

    above, _ = harness.run_catalog(plans(3), page_size=50)
    assert above.metrics.skus_unpriced_unavailable == 3
    assert "unpriced_unavailable_above_threshold" in above.metrics.rejection_reasons


# --- Persistencia Turso -------------------------------------------------------


def _apply_turso_locally(path: Path, snapshot: dict[str, object], *, run_id: str) -> dict[str, int]:
    content = raw(snapshot)
    snap = sqlite_updater.validate_snapshot_bytes(content)
    steps = turso_updater._mutation_steps(
        turso_updater._normalised_json(snap),
        location_id=str(snap["location_id"]),
        observed_at=str(snap["observed_at_utc"]),
        run_id=run_id,
        sku_count=len(snap["products"]),
        catalog_count=int(snap["catalog_products_reported"]),
        artifact_id=None,
        digest=hashlib.sha256(content).hexdigest(),
        unpriced_incoming=turso_updater._normalised_unpriced_json(snap),
    )
    changes: dict[str, int] = {}
    con = sqlite3.connect(path, isolation_level=None)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        for name, sql, args in steps:
            con.execute(sql, args)
            if name in {"close_unpriced_history", "upsert_unpriced_observations", "mark_unpriced_priced", "open_history", "close_history"}:
                changes[name] = int(con.execute("SELECT changes()").fetchone()[0])
        assert con.execute("SELECT COUNT(*) FROM temp.guard_ok WHERE value<>0").fetchone() == (0,)
    finally:
        con.close()
    return changes


def _observations(path: Path) -> list[tuple[object, ...]]:
    with sqlite3.connect(path) as con:
        return con.execute(
            f"""SELECT location_id,source_key,name,first_seen_utc,last_seen_utc,priced_since_utc
            FROM {UNPRICED_OBSERVATIONS_TABLE} ORDER BY location_id,source_key"""
        ).fetchall()


def _db(tmp_path: Path, name: str = "mvp.db") -> Path:
    path = tmp_path / name
    sqlite_updater.initialize_database(path)
    return path


def test_unpriced_observations_are_stored_without_products_or_history(tmp_path: Path) -> None:
    path = _db(tmp_path)
    base = [priced(index) for index in range(300)]
    snapshot = snapshot_from_capture(base + copy.deepcopy(EVIDENCE))
    changes = _apply_turso_locally(path, snapshot, run_id="run-1")

    assert changes["upsert_unpriced_observations"] == 4
    assert changes["close_unpriced_history"] == 0
    rows = _observations(path)
    assert [row[1] for row in rows] == ["18658", "18659", "18660", "18661"]
    assert all(row[3] == row[4] == OBSERVED and row[5] is None for row in rows)
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT COUNT(*) FROM products").fetchone() == (300,)
        assert con.execute("SELECT COUNT(*) FROM price_history").fetchone() == (300,)
        assert con.execute("SELECT COUNT(*) FROM products WHERE source_key IN ('18658','18659','18660','18661')").fetchone() == (0,)


def test_unpriced_observation_upsert_is_idempotent_and_ordered(tmp_path: Path) -> None:
    path = _db(tmp_path)
    base = [priced(index) for index in range(300)]
    _apply_turso_locally(path, snapshot_from_capture(base + copy.deepcopy(EVIDENCE)), run_id="run-1")
    first = _observations(path)

    # Mismo estado al día siguiente: sólo avanza last_seen; nada más cambia.
    later = "2026-10-03T08:30:00Z"
    changes = _apply_turso_locally(
        path, snapshot_from_capture(base + copy.deepcopy(EVIDENCE), observed_at=later), run_id="run-2"
    )
    assert changes["open_history"] == 0 and changes["close_history"] == 0
    second = _observations(path)
    assert [row[:4] for row in second] == [row[:4] for row in first]
    assert {row[4] for row in second} == {later}
    with sqlite3.connect(path) as con:
        assert con.execute(f"SELECT COUNT(*) FROM {UNPRICED_OBSERVATIONS_TABLE}").fetchone() == (4,)

    # Una observación más antigua no retrocede last_seen.
    changes = _apply_turso_locally(
        path, snapshot_from_capture(base + copy.deepcopy(EVIDENCE), observed_at="2026-10-02T09:00:00Z"), run_id="run-3"
    )
    assert changes["upsert_unpriced_observations"] == 0
    assert _observations(path) == second


def test_product_that_gets_a_price_flows_normally_and_sets_priced_since(tmp_path: Path) -> None:
    path = _db(tmp_path)
    base = [priced(index) for index in range(300)]
    _apply_turso_locally(path, snapshot_from_capture(base + copy.deepcopy(EVIDENCE)), run_id="run-1")

    launched = copy.deepcopy(EVIDENCE[0])
    launched.update(current_price="129.90", availability="in_stock")
    later = "2026-10-03T08:30:00Z"
    snapshot = snapshot_from_capture(base + [launched] + copy.deepcopy(EVIDENCE[1:]), observed_at=later)
    changes = _apply_turso_locally(path, snapshot, run_id="run-2")

    assert changes["open_history"] == 1
    assert changes["mark_unpriced_priced"] == 1
    rows = {row[1]: row for row in _observations(path)}
    assert rows["18658"][5] == later
    assert all(rows[key][5] is None for key in ("18659", "18660", "18661"))
    with sqlite3.connect(path) as con:
        assert con.execute(
            """SELECT h.current_price_minor,h.availability,h.valid_from_utc FROM price_history h
            JOIN products p USING(product_id) WHERE p.source_key='18658' AND h.valid_to_utc IS NULL"""
        ).fetchall() == [(12990, "in_stock", later)]

    # Idempotente: otra corrida igual no vuelve a marcar.
    again = _apply_turso_locally(
        path, snapshot_from_capture(base + [launched] + copy.deepcopy(EVIDENCE[1:]), observed_at="2026-10-04T08:30:00Z"),
        run_id="run-3",
    )
    assert again["mark_unpriced_priced"] == 0


def test_previously_priced_offer_listed_unpriced_closes_current_period_matching_sqlite(tmp_path: Path) -> None:
    reference = _db(tmp_path, "reference.db")
    candidate = _db(tmp_path, "candidate.db")
    base = [priced(index) for index in range(300)]
    dubois_priced = copy.deepcopy(DUBOIS)
    dubois_priced.update(current_price="389.00", availability="in_stock")
    observations = [
        snapshot_from_capture(base + [dubois_priced], location="la_colonia_tgu", observed_at="2026-10-01T08:30:00Z"),
        snapshot_from_capture(base + [copy.deepcopy(DUBOIS)], location="la_colonia_tgu", observed_at=OBSERVED),
        snapshot_from_capture(base + [dubois_priced], location="la_colonia_tgu", observed_at="2026-10-03T08:30:00Z"),
    ]
    closed = []
    for index, snapshot in enumerate(observations, start=1):
        sqlite_updater.apply_snapshot(reference, raw(snapshot), run_id=f"run-{index}")
        closed.append(_apply_turso_locally(candidate, snapshot, run_id=f"run-{index}").get("close_unpriced_history"))
        dumps = []
        for path in (reference, candidate):
            with sqlite3.connect(path) as con:
                dumps.append({
                    table: sorted(con.execute(f"SELECT * FROM {table}").fetchall(), key=repr)
                    for table in turso_updater.EXPECTED_TABLES
                })
        assert dumps[0] == dumps[1]
    assert closed == [0, 1, 0]
    with sqlite3.connect(candidate) as con:
        periods = con.execute(
            """SELECT h.current_price_minor,h.valid_from_utc,h.valid_to_utc FROM price_history h
            JOIN products p USING(product_id) WHERE p.source_key='18302' ORDER BY h.valid_from_utc"""
        ).fetchall()
    # El periodo con precio se cierra al listarse sin precio; no se inventa un
    # periodo sin precio y vuelve por el camino normal cuando reaparece el precio.
    assert periods == [
        (38900, "2026-10-01T08:30:00Z", OBSERVED),
        (38900, "2026-10-03T08:30:00Z", None),
    ]
    assert _observations(candidate)[0][5] == "2026-10-03T08:30:00Z"


def test_unpriced_steps_read_only_affected_scope(tmp_path: Path) -> None:
    """Presupuesto de rows read: los pasos nuevos usan índices y no crecen con N."""

    def work(count: int) -> int:
        path = _db(tmp_path, f"budget-{count}.db")
        base = [priced(index) for index in range(count)]
        _apply_turso_locally(path, snapshot_from_capture(base + copy.deepcopy(EVIDENCE)), run_id="run-1")
        snapshot = snapshot_from_capture(base + copy.deepcopy(EVIDENCE), observed_at="2026-10-03T08:30:00Z")
        content = raw(snapshot)
        snap = sqlite_updater.validate_snapshot_bytes(content)
        steps = turso_updater._mutation_steps(
            turso_updater._normalised_json(snap), location_id="la_colonia_sps",
            observed_at=str(snap["observed_at_utc"]), run_id="run-2", sku_count=len(snap["products"]),
            catalog_count=int(snap["catalog_products_reported"]), artifact_id=None,
            digest=hashlib.sha256(content).hexdigest(),
            unpriced_incoming=turso_updater._normalised_unpriced_json(snap),
        )
        measured = {
            "guard_unpriced_out_of_order", "close_unpriced_history",
            "upsert_unpriced_observations", "mark_unpriced_priced",
        }
        instructions = 0

        def progress() -> int:
            nonlocal instructions
            instructions += 1
            return 0

        con = sqlite3.connect(path, isolation_level=None)
        try:
            for name, sql, args in steps:
                if name in measured:
                    plan = " ".join(str(row) for row in con.execute("EXPLAIN QUERY PLAN " + sql, args))
                    for table in ("price_history", "products", UNPRICED_OBSERVATIONS_TABLE):
                        assert f"SCAN {table}" not in plan, (name, plan)
                    # Nunca recorrer todos los productos del supermercado.
                    assert "(supermarket_id=?)" not in plan, (name, plan)
                    con.set_progress_handler(progress, 10)
                con.execute(sql, args)
                con.set_progress_handler(None, 0)
        finally:
            con.close()
        return instructions

    small, large = work(200), work(3_200)
    assert small > 0
    # 16x más catálogo con las mismas 4 entradas sin precio: trabajo ~constante.
    assert large < small * 2


def test_new_table_is_an_allowed_optional_table_and_counted_weekly() -> None:
    base = turso_updater.EXPECTED_TABLES
    turso_updater._validate_table_names(base | {UNPRICED_OBSERVATIONS_TABLE})
    assert UNPRICED_OBSERVATIONS_TABLE in turso_updater.OPTIONAL_DERIVED_TABLES
    assert UNPRICED_OBSERVATIONS_TABLE in integrity.OPTIONAL_COUNTED_TABLES


def test_snapshot_without_section_adds_no_unpriced_steps() -> None:
    snapshot = snapshot_from_capture([priced(index) for index in range(10)])
    snapshot.pop("unpriced_unavailable")
    snapshot.pop("skus_unpriced_unavailable")
    snap = sqlite_updater.validate_snapshot_bytes(raw(snapshot))
    assert turso_updater._normalised_unpriced_json(snap) is None
    steps = turso_updater._mutation_steps(
        turso_updater._normalised_json(snap), location_id="la_colonia_sps", observed_at=OBSERVED,
        run_id="run", sku_count=10, catalog_count=10, artifact_id=None, digest="x",
    )
    assert not any("unpriced" in name for name, _, _ in steps)


def test_persist_snapshot_end_to_end_reports_unpriced_and_replays(database: Path) -> None:
    from test_colonial_persistence import apply

    base = [priced(index) for index in range(300)]
    content = raw(snapshot_from_capture(base + copy.deepcopy(EVIDENCE)))
    summary = apply(content, "run-e2e", market="la_colonia")
    assert summary["replayed"] is False
    assert summary["products_processed"] == 300
    assert summary["unpriced_unavailable"] == 4
    assert summary["history_closed_unpriced"] == 0
    assert len(_observations(database)) == 4
    assert apply(content, "run-e2e", market="la_colonia")["replayed"] is True
    assert len(_observations(database)) == 4


def test_operational_page_with_only_unpriced_unavailable_skus_is_accepted() -> None:
    import obtener_catalogo_sps_la_colonia_operativo_v2 as operational_v2
    import test_la_colonia_runner as harness

    extractor = operational_v2.RecoveryAwareLaColoniaExtractor()
    url = "https://www.lacolonia.com/_v/segment/graphql/v1"
    unpriced_page = harness.make_payload(0, 2, 2, price=0)
    result = extractor.parse_payload(unpriced_page, scrape_run_id="offline", source_url=url, page_size=50)
    assert result.accepted is True
    assert {product.raw_values["availability"] for product in result.products} == {"out_of_stock"}

    # Sin precio pero sin evidencia de agotado (cantidad > 0): sigue rechazada.
    products = [harness.make_product(index, price=0) for index in range(2)]
    for product in products:
        product["items"][0]["sellers"][0]["commercialOffer"]["AvailableQuantity"] = 5
    unknown_page = harness.make_payload(0, 2, 2, products=products)
    rejected = extractor.parse_payload(unknown_page, scrape_run_id="offline", source_url=url, page_size=50)
    assert rejected.accepted is False
