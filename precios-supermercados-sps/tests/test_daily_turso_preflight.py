from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import preflight_mvp_turso_daily as daily  # noqa: E402
from actualizar_mvp_sqlite_la_colonia import SnapshotError  # noqa: E402

RELATIVE_LOCATIONS = {
    "sps/full-catalog.json": "la_colonia_sps",
    "tgu/full-catalog.json": "la_colonia_tgu",
    "colonial/full-catalog.json": "colonial_sps",
    "walmart/snapshot-walmart-sps.json": "walmart_sps",
    "walmart/snapshot-walmart-tgu-ffaa.json": "walmart_tgu_ffaa",
    "walmart/snapshot-walmart-tgu-el-sauce.json": "walmart_tgu_el_sauce",
    "pricesmart/snapshot-pricesmart-sps.json": "pricesmart_sps",
    "pricesmart/snapshot-pricesmart-tgu.json": "pricesmart_tgu",
    "los-andes/full-catalog.json": "comisariato_los_andes_sps",
    "paiz/snapshot-paiz-multiplaza.json": "paiz_tgu_multiplaza",
    "paiz/snapshot-paiz-proceres.json": "paiz_tgu_proceres",
}


def _artifact_root(tmp_path: Path) -> Path:
    for relative, location_id in RELATIVE_LOCATIONS.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(location_id.encode())
    return tmp_path


def _install_validators(monkeypatch) -> None:
    validate = lambda raw, **_kwargs: {"location_id": raw.decode()}  # noqa: E731
    monkeypatch.setattr(daily, "validate_snapshot_bytes", validate)
    monkeypatch.setattr(daily.los_andes, "validate_snapshot_bytes", validate)
    monkeypatch.setattr(daily.paiz, "validate_snapshot_bytes", validate)


def test_preflight_checks_all_eleven_locations_without_mutation(tmp_path, monkeypatch):
    root = _artifact_root(tmp_path)
    _install_validators(monkeypatch)
    generic_calls = []
    los_andes_calls = []
    paiz_calls = []
    monkeypatch.setattr(
        daily.generic,
        "_preflight",
        lambda *args, **kwargs: generic_calls.append(kwargs) or None,
    )
    monkeypatch.setattr(
        daily.los_andes,
        "_preflight",
        lambda *args, **kwargs: los_andes_calls.append(kwargs) or None,
    )
    monkeypatch.setattr(
        daily.paiz,
        "_preflight",
        lambda *args, **kwargs: paiz_calls.append(kwargs) or None,
    )
    for module in (daily.generic, daily.los_andes, daily.paiz):
        monkeypatch.setattr(
            module,
            "_run_batch",
            lambda *_args, **_kwargs: pytest.fail("preflight attempted a mutation"),
        )

    result = daily.preflight(
        root,
        database_url="libsql://example",
        auth_token="token",
        run_id="run-1",
    )

    assert result["result"] == "ready"
    assert result["location_count"] == 11
    assert {item["location_id"] for item in result["locations"]} == set(
        RELATIVE_LOCATIONS.values()
    )
    assert {item["status"] for item in result["locations"]} == {"new"}
    assert len(generic_calls) == 8
    assert los_andes_calls == [{"run_id": "run-1-los-andes"}]
    assert {item["run_id"] for item in paiz_calls} == {
        "run-1-paiz-multiplaza",
        "run-1-paiz-proceres",
    }


def test_preflight_accepts_only_an_exact_successful_replay():
    raw = b"snapshot"
    digest = hashlib.sha256(raw).hexdigest()
    previous = {
        "location_id": "location",
        "run_status": "success",
        "sha": digest,
    }

    assert daily._accept_previous(
        previous, location_id="location", digest=digest
    ) == "exact_replay"
    with pytest.raises(SnapshotError, match="daily_preflight_run_conflict"):
        daily._accept_previous(
            previous | {"sha": "different"},
            location_id="location",
            digest=digest,
        )


def test_preflight_rejects_missing_credentials(tmp_path):
    with pytest.raises(SnapshotError, match="turso_credentials_missing"):
        daily.preflight(
            tmp_path,
            database_url="",
            auth_token="",
            run_id="run-1",
        )
