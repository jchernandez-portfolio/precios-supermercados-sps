from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github/workflows/precios-supermercados-sps-la-colonia-location-binding.yml"
REQUEST = REPO_ROOT / ".github/workflows/requests/la-colonia-location-binding-standing-request.json"
CLI = REPO_ROOT / "precios-supermercados-sps/scripts/diagnosticar_binding_ubicacion_la_colonia.py"
PROJECT_STATE = REPO_ROOT / "precios-supermercados-sps/docs/PROJECT_STATE.md"




def test_standing_location_binding_marker_is_absent() -> None:
    assert REQUEST.exists() is False


def test_location_binding_cli_requires_explicit_authorization_id() -> None:
    text = CLI.read_text(encoding="utf-8")

    assert '"--authorization-id"' in text
    assert "required=True" in text
    assert "--standing-public-read-only" not in text
    assert "standing_public_read_only=False" in text


def test_project_state_does_not_claim_open_ended_live_authority() -> None:
    text = PROJECT_STATE.read_text(encoding="utf-8")

    assert "no se interpreta como autorización abierta" in text
    assert "requiere autorización humana explícita vigente" in text
