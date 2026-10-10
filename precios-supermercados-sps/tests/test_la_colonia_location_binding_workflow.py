from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github/workflows/precios-supermercados-sps-la-colonia-location-binding.yml"
SCRIPT = REPO_ROOT / "precios-supermercados-sps/scripts/diagnosticar_binding_ubicacion_la_colonia.py"
REQUEST = REPO_ROOT / ".github/workflows/requests/la-colonia-location-binding-standing-request.json"
RECONCILE_REQUEST = REPO_ROOT / ".github/workflows/requests/la-colonia-location-binding-reconcile-request.json"
CONSUMED_IDS = (
    "LC-location-binding-336",
    "LC-location-binding-331",
    "LC-location-binding-332",
    "LC-location-binding-333",
    "LC-location-binding-334",
    "LC-location-binding-335",
    "LC-location-binding-337",
)


def load_workflow() -> dict:
    value = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(value, dict)
    return value


def load_script_module():
    spec = importlib.util.spec_from_file_location("location_binding_cli", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module








def test_cli_requires_explicit_id_and_exposes_no_standing_or_runtime_overrides() -> None:
    raw = SCRIPT.read_text(encoding="utf-8")

    assert '"--authorization-id"' in raw
    assert "required=True" in raw
    assert "standing_public_read_only=False" in raw
    for forbidden in (
        "--standing-public-read-only",
        "--target-url",
        "--network-policy",
        "--live-execution-enabled",
        "--active-id",
        "active_ids=",
        "live_execution_enabled=",
        "network_policy=",
        "target_url=",
    ):
        assert forbidden not in raw


def test_legacy_consumed_authorizations_remain_blocked(tmp_path: Path) -> None:
    module = load_script_module()
    for authorization_id, expected_reason in (
        *((authorization_id, "authorization_id_consumed") for authorization_id in CONSUMED_IDS),
        ("LC-location-binding-777", "authorization_id_not_active"),
    ):
        output = tmp_path / f"{authorization_id}.json"
        exit_code = module.main(
            [
                "--authorization-id",
                authorization_id,
                "--output-path",
                str(output),
            ]
        )
        assert exit_code == 3
        rendered = output.read_text(encoding="utf-8")
        assert f'"stop_reason": "{expected_reason}"' in rendered
        assert '"browser_started": false' in rendered
        assert '"target_navigation_started": false' in rendered
        assert '"production_authority": false' in rendered
        assert '"catalog_accepted": false' in rendered
        assert '"extraction_enabled": false' in rendered


def test_standing_marker_must_remain_absent() -> None:
    assert REQUEST.exists() is False
