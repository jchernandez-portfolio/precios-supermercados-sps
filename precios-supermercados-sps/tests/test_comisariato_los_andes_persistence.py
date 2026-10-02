from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import actualizar_mvp_turso_comisariato_los_andes as persistence  # noqa: E402


def product(
    key: str, *, promo: bool = False, ean: str | None = None
) -> tuple[dict[str, object], dict[str, object]]:
    current = "90.00" if promo else "100.00"
    row = {
        "availability": "unknown",
        "brand": "Marca COMANDES",
        "category": "ABARROTES",
        "current_price": current,
        "ean": ean,
        "is_promotion": promo,
        "item_id": key,
        "presentation": "UN",
        "product_id": key,
        "reference": key,
        "reported_regular_price": "100.00" if promo else None,
        "source_key": key,
        "source_key_type": "sku",
        "source_name": f"Producto {key}",
    }
    detail = {
        "price_status": "observed",
        "source_new_price": 90 if promo else 100,
        "source_price": 90 if promo else 100,
        "source_old_price": 100 if promo else None,
        "source_list_price": "PD",
        "source_discount": 10 if promo else None,
        "source_discount_two": None,
        "regular_price_evidence": ["oldPrice"] if promo else [],
        "source_availibility_count": 0,
        "availability_signal": "0",
        "availability_interpretation": "not_proven",
        "source_stock": None,
        "source_brand_id": 1,
        "source_material_group_code": "100",
        "source_material_group_name": "ABARROTES",
        "source_unit_measure_code": "UN",
        "source_unit_measure_name": "Unidad",
        "source_is_adult": "0",
        "image_url": None,
    }
    return row, detail


def snapshot_bytes(pairs: list[tuple[dict[str, object], dict[str, object]]] | None = None) -> bytes:
    pairs = pairs or [product("0001-1"), product("0001-2", promo=True)]
    rows = [pair[0] for pair in pairs]
    details = {pair[0]["source_key"]: pair[1] for pair in pairs}
    keys = sorted(str(row["source_key"]) for row in rows)
    return json.dumps(
        {
            "result": "success",
            "supermarket_id": persistence.SUPERMARKET_ID,
            "location_id": persistence.LOCATION_ID,
            "city": persistence.CITY,
            "currency": "HNL",
            "scope": persistence.SCOPE,
            "store_id": 1,
            "store_name": "COMISARIATO LOS ANDES",
            "office_code": "00",
            "location_one_code": "COR",
            "location_two_code": "501",
            "location_verified_same_run": True,
            "catalog_complete": True,
            "validation_passed": True,
            "observed_at_utc": "2026-09-04T01:44:36Z",
            "catalog_products_reported": 2,
            "unique_products_extracted": 2,
            "membership_count": 2,
            "membership_sha256": hashlib.sha256("\n".join(keys).encode()).hexdigest(),
            "skus_extracted": 2,
            "skus_with_price": 2,
            "availability_counts": {"unknown": 2},
            "promotion_counts": {"promotion": 1, "not_promotion": 1, "unknown": 0},
            "products": rows,
            "source_details": details,
            "page_evidence": [],
            "request_count": 3,
            "retry_count": 0,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()


def test_los_andes_snapshot_validator_accepts_proven_contract() -> None:
    data = persistence.validate_snapshot_bytes(snapshot_bytes())
    assert data["supermarket_id"] == "comisariato_los_andes"
    assert data["location_id"] == "comisariato_los_andes_sps"
    assert data["promotion_counts"] == {"promotion": 1, "not_promotion": 1, "unknown": 0}


@pytest.mark.parametrize(
    "mutation,reason",
    [
        (lambda d: d.__setitem__("catalog_complete", False), "metadata_invalid:catalog_complete"),
        (lambda d: d["products"][0].__setitem__("availability", "out_of_stock"), "product_identity_invalid"),
        (lambda d: d["source_details"]["0001-1"].__setitem__("source_list_price", 0), "source_semantics_invalid"),
        (lambda d: d["source_details"]["0001-2"].__setitem__("source_discount", 9), "promotion_invalid"),
        (lambda d: d.__setitem__("membership_sha256", "0" * 64), "membership_hash_invalid"),
        # `ean` sólo puede ser la reconstrucción del `code`; "0001-1" no tiene GTIN.
        (lambda d: d["products"][0].__setitem__("ean", "7441029556773"), "product_identity_invalid"),
    ],
)
def test_los_andes_snapshot_validator_fails_closed(mutation, reason) -> None:
    data = json.loads(snapshot_bytes())
    mutation(data)
    with pytest.raises(persistence.SnapshotError, match=reason):
        persistence.validate_snapshot_bytes(json.dumps(data).encode())


GTIN_CODE = "0001-000744102955677"  # Bimbo Pan Blanco 720 g → 7441029556773


def test_los_andes_snapshot_validator_accepts_reconstructed_gtin() -> None:
    raw = snapshot_bytes([product(GTIN_CODE, ean="7441029556773"), product("0001-2", promo=True)])
    data = persistence.validate_snapshot_bytes(raw)
    assert {row["reference"]: row["ean"] for row in data["products"]} == {
        GTIN_CODE: "7441029556773",
        "0001-2": None,
    }


@pytest.mark.parametrize("ean", [None, "744102955677", "07441029556773", "7441029556774"])
def test_los_andes_snapshot_validator_rejects_ean_other_than_reconstruction(ean) -> None:
    raw = snapshot_bytes([product(GTIN_CODE, ean=ean), product("0001-2", promo=True)])
    with pytest.raises(persistence.SnapshotError, match="product_identity_invalid"):
        persistence.validate_snapshot_bytes(raw)


def test_persist_snapshot_uses_los_andes_scope_and_is_idempotent(monkeypatch) -> None:
    raw = snapshot_bytes()
    calls: dict[str, object] = {}

    monkeypatch.setattr(persistence, "_preflight", lambda *a, **k: None)
    monkeypatch.setattr(
        persistence,
        "_register_scope",
        lambda database_url, auth_token: calls.update(register=(database_url, auth_token)),
    )

    def fake_steps(incoming, **kwargs):
        calls["mutation"] = kwargs
        assert "0001-1" in incoming and "0001-2" in incoming
        return [
            ("begin", "BEGIN", ()),
            ("close_history", "UPDATE x", ()),
            ("open_history", "INSERT x", ()),
            ("commit", "COMMIT", ()),
        ]

    monkeypatch.setattr(persistence, "_mutation_steps", fake_steps)
    monkeypatch.setattr(persistence, "_run_batch", lambda *a, **k: [{}, {}, {}, {}])
    monkeypatch.setattr(
        persistence,
        "_affected",
        lambda results, steps, name: 2 if name == "open_history" else 0,
    )

    result = persistence.persist_snapshot(
        raw,
        database_url="https://db.example",
        auth_token="token",
        run_id="run-1",
        source_artifact_id="9920279680",
    )
    assert calls["register"] == ("https://db.example", "token")
    mutation = calls["mutation"]
    assert mutation["supermarket_id"] == persistence.SUPERMARKET_ID
    assert mutation["location_id"] == persistence.LOCATION_ID
    assert mutation["sku_count"] == 2
    assert result["history_opened"] == 2
    assert result["history_closed"] == 0

    digest = hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(
        persistence,
        "_preflight",
        lambda *a, **k: {
            "location_id": persistence.LOCATION_ID,
            "run_status": "success",
            "sha": digest,
        },
    )
    replay = persistence.persist_snapshot(
        raw,
        database_url="https://db.example",
        auth_token="token",
        run_id="run-1",
    )
    assert replay["replayed"] is True
    assert replay["products_processed"] == 0


def test_verify_accepts_extra_last_observed_products(monkeypatch) -> None:
    raw = snapshot_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    results = [
        [[persistence.LOCATION_ID, "success", 2, 2, digest]],
        [[0]],
        [[3]],
    ]
    monkeypatch.setattr(persistence, "_pipeline", lambda *args, **kwargs: {"results": results})
    monkeypatch.setattr(persistence, "_execute_rows", lambda result: result)

    verified = persistence.verify_committed_run(
        database_url="https://db.example",
        auth_token="token",
        run_id="run-1",
        raw=raw,
    )

    assert verified["open_price_history"] == 3


def test_verify_rejects_fewer_open_products_than_snapshot(monkeypatch) -> None:
    raw = snapshot_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    results = [
        [[persistence.LOCATION_ID, "success", 2, 2, digest]],
        [[0]],
        [[1]],
    ]
    monkeypatch.setattr(persistence, "_pipeline", lambda *args, **kwargs: {"results": results})
    monkeypatch.setattr(persistence, "_execute_rows", lambda result: result)

    with pytest.raises(
        persistence.SnapshotError,
        match="los_andes_turso_current_state_incomplete",
    ):
        persistence.verify_committed_run(
            database_url="https://db.example",
            auth_token="token",
            run_id="run-1",
            raw=raw,
        )
