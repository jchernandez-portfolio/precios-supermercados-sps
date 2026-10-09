"""Persistencia parcial del corte diario (aprobada 2026-10-09).

El 5-oct Paiz falló sus 3 intentos y `persist` se saltó para TODAS las cadenas:
ese día no se publicó nada. Ahora se persisten las cadenas aceptadas, la que
falló queda STALE en el catálogo y el operador sigue reintentándola.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT.parent / ".github" / "workflows"
sys.path.insert(0, str(ROOT / "scripts"))

import preflight_mvp_turso_daily as preflight  # noqa: E402


def _load(name: str) -> dict:
    return yaml.load((WORKFLOWS / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_persist_runs_when_some_retailer_failed_and_only_touches_accepted_ones() -> None:
    workflow = _load("precios-supermercados-sps-la-colonia-mvp-update.yml")
    persist = workflow["jobs"]["persist"]
    assert persist["if"] == "${{ !cancelled() && (needs.acquire.result == 'success' || needs.acquire.result == 'failure') }}"
    steps = {step.get("name"): step for step in persist["steps"]}
    assembly = steps["Ensamblar último handoff aceptado por supermercado"]["run"]
    assert "--allow-missing" in assembly and 'DAILY_RETAILERS=' in assembly and "Persistencia parcial" in assembly
    assert "--assembly run-artifacts/acquisition-assembly.json" in steps["Validar todos los destinos Turso antes de escribir"]["run"]
    persist_all = steps["Persistir La Colonia, Los Andes, Colonial, Walmart y PriceSmart"]["run"]
    for retailer in ("la_colonia", "los_andes", "colonial", "walmart", "pricesmart"):
        assert f"if accepted {retailer}; then" in persist_all
    assert steps["Persistir Paiz"]["if"] == "${{ contains(format(',{0},', env.DAILY_RETAILERS), ',paiz,') }}"
    for name in ("Aceptar sólo snapshots completos", "Verificar commits exactos en Turso", "Verificar nuevos retailers y Paiz en Turso"):
        assert "accepted = set(os.environ['DAILY_RETAILERS'].split(','))" in steps[name]["run"]


def test_publication_runs_after_a_partial_day_only_if_persist_succeeded() -> None:
    workflow = _load("precios-supermercados-sps-homologation-refresh.yml")
    gate = workflow["jobs"]["daily-gate"]
    assert gate["permissions"] == {"actions": "read"}
    script = gate["steps"][0]["with"]["script"]
    assert "listJobsForWorkflowRunAttempt" in script and "job.name === 'persist'" in script
    refresh = workflow["jobs"]["refresh"]
    assert refresh["needs"] == "daily-gate"
    assert "!cancelled()" in refresh["if"]
    assert "needs.daily-gate.outputs.proceed == 'true'" in refresh["if"]


def test_preflight_validates_only_accepted_retailers(tmp_path: Path) -> None:
    assembly = tmp_path / "assembly.json"
    assembly.write_text('{"retailers": {"walmart": {}, "pricesmart": {}}, "missing_retailers": ["paiz"]}', encoding="utf-8")
    assert preflight.accepted_retailers(assembly) == frozenset({"walmart", "pricesmart"})
    assert preflight.accepted_retailers(None) == frozenset(preflight.LOCATIONS_BY_RETAILER)
    assert sum(preflight.LOCATIONS_BY_RETAILER.values()) == 11
    assembly.write_text('{"retailers": {}}', encoding="utf-8")
    with pytest.raises(preflight.SnapshotError, match="assembly_invalid"):
        preflight.accepted_retailers(assembly)
