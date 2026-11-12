from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import backfill_homologacion_turso as turso  # noqa: E402
from precios_supermercados.product_homologation_persistence import (  # noqa: E402
    NORMALIZATION_VERSION,
    ProductHomologationRow,
)


@pytest.fixture(autouse=True)
def _no_size_decisions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(turso, "_fetch_size_decisions", lambda *_: {})


def row(product_id: int, hash_char: str) -> ProductHomologationRow:
    return ProductHomologationRow(
        product_id=product_id,
        supermarket_id="la_colonia",
        normalized_name=f"producto {product_id}",
        normalized_brand=None,
        canonical_gtin=None,
        canonical_product_id=None,
        category=None,
        subcategory=None,
        product_type=None,
        taxonomy_rule_id=None,
        presentation_dimension=None,
        presentation_total_base=None,
        presentation_pack_count=None,
        presentation_unit_amount_base=None,
        presentation_status="missing",
        comparison_status="unmapped",
        conflict_reasons_json="[]",
        normalization_version=NORMALIZATION_VERSION,
        profile_hash=hash_char * 64,
        updated_at_utc="2026-09-05T05:00:00Z",
    )


def postflight(profiles: int) -> dict[str, object]:
    return {
        "profiles": profiles,
        "comparison_status": {"unmapped": profiles},
        "classified_product_type": 0,
        "foreign_key_violations": 0,
        "duplicate_open_periods": 0,
        "integrity_check": "ok",
        "products_unchanged": True,
        "price_history_unchanged": True,
        "scrape_runs_unchanged": True,
    }


def test_refresh_is_true_noop_when_all_profile_hashes_match(monkeypatch: pytest.MonkeyPatch) -> None:
    before = {"products": 2, "price_history": 7, "scrape_runs": 3, "profiles": 2}
    derived = (row(1, "a"), row(2, "b"))

    monkeypatch.setattr(turso, "_source_preflight", lambda *_: before)
    monkeypatch.setattr(turso, "_fetch_products", lambda *_: ((1, object()), (2, object())))
    monkeypatch.setattr(turso, "build_homologation_rows", lambda *_, **__: derived)
    monkeypatch.setattr(
        turso,
        "_fetch_profile_state",
        lambda *_: {1: ("a" * 64, NORMALIZATION_VERSION), 2: ("b" * 64, NORMALIZATION_VERSION)},
    )
    monkeypatch.setattr(turso, "_postflight", lambda *_, **__: postflight(2))
    monkeypatch.setattr(
        turso,
        "_stage_rows",
        lambda *_: pytest.fail("no-op refresh must not create/write staging"),
    )
    monkeypatch.setattr(
        turso,
        "_ensure_schema",
        lambda *_: pytest.fail("existing populated schema must not be recreated"),
    )

    result = turso.backfill_turso("https://db.example", "token", updated_at_utc="2026-09-05T05:00:00Z")

    assert result["processed"] == 2
    assert result["inserted"] == 0
    assert result["updated"] == 0
    assert result["unchanged"] == 2
    assert result["no_op"] is True
    assert result["staging_written"] is False


def test_refresh_stages_only_new_or_changed_profiles(monkeypatch: pytest.MonkeyPatch) -> None:
    before = {"products": 3, "price_history": 7, "scrape_runs": 3, "profiles": 2}
    derived = (row(1, "a"), row(2, "b"), row(3, "c"))
    captured: dict[str, object] = {}

    monkeypatch.setattr(turso, "_source_preflight", lambda *_: before)
    monkeypatch.setattr(turso, "_fetch_products", lambda *_: ((1, object()), (2, object()), (3, object())))
    monkeypatch.setattr(turso, "build_homologation_rows", lambda *_, **__: derived)
    monkeypatch.setattr(
        turso,
        "_fetch_profile_state",
        lambda *_: {1: ("a" * 64, NORMALIZATION_VERSION), 2: ("x" * 64, NORMALIZATION_VERSION)},
    )

    def stage(_url: str, _token: str, rows: tuple[ProductHomologationRow, ...]) -> None:
        captured["staged_ids"] = [item.product_id for item in rows]

    def apply(_url: str, _token: str, _before: dict[str, int], *, staged_expected: int, profile_expected: int) -> None:
        captured["staged_expected"] = staged_expected
        captured["profile_expected"] = profile_expected

    monkeypatch.setattr(turso, "_stage_rows", stage)
    monkeypatch.setattr(turso, "_ensure_schema", lambda *_: captured.__setitem__("schema_checked", True))
    monkeypatch.setattr(turso, "_preflight", lambda *_: before)
    monkeypatch.setattr(turso, "_delta_counts", lambda *_: {"inserted": 1, "updated": 1, "unchanged": 0})
    monkeypatch.setattr(turso, "_apply_stage", apply)
    monkeypatch.setattr(turso, "_postflight", lambda *_, **__: postflight(3))
    monkeypatch.setattr(turso, "_drop_stage", lambda *_: captured.__setitem__("stage_dropped", True))

    result = turso.backfill_turso("https://db.example", "token", updated_at_utc="2026-09-05T05:00:00Z")

    assert captured == {
        "staged_ids": [2, 3],
        "schema_checked": True,
        "staged_expected": 2,
        "profile_expected": 3,
        "stage_dropped": True,
    }
    assert result["inserted"] == 1
    assert result["updated"] == 1
    assert result["unchanged"] == 1
    assert result["no_op"] is False
    assert result["staging_written"] is True


def test_dry_run_computes_delta_without_schema_or_staging_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = {"products": 2, "price_history": 7, "scrape_runs": 3, "profiles": 1}
    derived = (row(1, "a"), row(2, "b"))
    monkeypatch.setattr(turso, "_source_preflight", lambda *_: before)
    monkeypatch.setattr(turso, "_fetch_products", lambda *_: ((1, object()), (2, object())))
    monkeypatch.setattr(turso, "build_homologation_rows", lambda *_, **__: derived)
    monkeypatch.setattr(
        turso,
        "_fetch_profile_state",
        lambda *_: {1: ("x" * 64, NORMALIZATION_VERSION)},
    )
    monkeypatch.setattr(
        turso,
        "_ensure_schema",
        lambda *_: pytest.fail("dry-run must not mutate schema"),
    )
    monkeypatch.setattr(
        turso,
        "_stage_rows",
        lambda *_: pytest.fail("dry-run must not stage rows"),
    )

    result = turso.backfill_turso(
        "https://db.example",
        "token",
        updated_at_utc="2026-09-05T05:00:00Z",
        apply=False,
    )

    assert result["dry_run"] is True
    assert result["inserted"] == 1
    assert result["updated"] == 1
    assert result["staging_written"] is False


def test_refresh_fails_closed_if_profile_exists_without_product(monkeypatch: pytest.MonkeyPatch) -> None:
    before = {"products": 2, "price_history": 7, "scrape_runs": 3, "profiles": 2}
    derived = (row(1, "a"), row(2, "b"))

    monkeypatch.setattr(turso, "_source_preflight", lambda *_: before)
    monkeypatch.setattr(turso, "_fetch_products", lambda *_: ((1, object()), (2, object())))
    monkeypatch.setattr(turso, "build_homologation_rows", lambda *_, **__: derived)
    monkeypatch.setattr(
        turso,
        "_fetch_profile_state",
        lambda *_: {1: ("a" * 64, NORMALIZATION_VERSION), 99: ("z" * 64, NORMALIZATION_VERSION)},
    )
    monkeypatch.setattr(
        turso,
        "_stage_rows",
        lambda *_: pytest.fail("stale profile state must fail before staging"),
    )

    with pytest.raises(turso.SnapshotError, match="homologation_profile_ids_not_in_products"):
        turso.backfill_turso("https://db.example", "token", updated_at_utc="2026-09-05T05:00:00Z")


def test_refresh_workflow_runs_after_successful_daily_update() -> None:
    workflow = (
        ROOT.parent
        / ".github"
        / "workflows"
        / "precios-supermercados-sps-homologation-refresh.yml"
    ).read_text(encoding="utf-8")

    assert '"La Colonia - Actualización MVP"' in workflow
    assert "workflow_run:" in workflow
    assert "push:" in workflow
    assert '"precios-supermercados-sps/.automation/product-identity-persistence-request.json"' in workflow
    assert "github.event_name == 'push' && github.ref == 'refs/heads/main'" in workflow
    assert "github.event.workflow_run.conclusion == 'success'" in workflow
    assert "github.event.workflow_run.head_branch == 'main'" in workflow
    assert "github.event.workflow_run.head_sha" in workflow
    assert "python scripts/backfill_homologacion_turso.py" in workflow
    assert "--dry-run" in workflow
    assert "--apply" in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "persist-credentials: false" in workflow
    assert "schedule:" not in workflow


def test_rollout_request_is_closed_and_scoped_to_derived_state() -> None:
    request = json.loads(
        (ROOT / ".automation" / "product-identity-persistence-request.json").read_text(
            encoding="utf-8"
        )
    )
    assert request == {
        "schema": "precios-sps-product-identity-persistence-request/v1",
        "action": "apply_audited_product_identity_v23",
        "normalization_version": "product-homologation-v2.3",
        "source_tables_read_only": True,
        "derived_write": True,
        "reason": "audited_identity_v23_rollout",
        "sequence": 1,
    }
    workflow = (
        ROOT.parent
        / ".github"
        / "workflows"
        / "precios-supermercados-sps-homologation-refresh.yml"
    ).read_text(encoding="utf-8")
    assert "product_identity_persistence_request_closed_set_mismatch" in workflow
    assert "product_identity_persistence_source_read_only_required" in workflow
    assert "product_identity_persistence_derived_write_required" in workflow
