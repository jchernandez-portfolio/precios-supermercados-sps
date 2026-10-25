"""Contrato estático para la auditoría privada de identidad de producto."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (
    REPO_ROOT
    / ".github"
    / "workflows"
    / "precios-supermercados-sps-historical-branch-audit.yml"
)
REQUEST = (
    REPO_ROOT
    / "precios-supermercados-sps"
    / ".automation"
    / "product-identity-audit-request.json"
)


def _workflow() -> dict:
    value = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(value, dict)
    return value


def test_request_is_closed_read_only_and_versioned() -> None:
    document = json.loads(REQUEST.read_text(encoding="utf-8"))
    assert set(document) == {"schema", "action", "read_only", "reason", "sequence"}
    assert document == {
        "schema": "precios-sps-product-identity-audit-request/v1",
        "action": "export_private_product_identity_audit",
        "read_only": True,
        "reason": "baseline_quality_audit",
        "sequence": 5,
    }
    validation = next(
        step
        for step in _workflow()["jobs"]["audit"]["steps"]
        if step.get("name") == "Validar solicitud cerrada de auditoría de identidad"
    )
    assert repr(document["reason"]) in validation["run"]


def test_identity_audit_uses_registered_read_only_entrypoint() -> None:
    workflow = _workflow()
    assert workflow["permissions"] == {"contents": "read", "pull-requests": "read"}
    assert workflow["on"]["push"] == {
        "branches": ["main"],
        "paths": [
            "precios-supermercados-sps/scripts/auditar_ramas_historicas.py",
            "precios-supermercados-sps/scripts/auditar_ramas_historicas_v2.py",
            "precios-supermercados-sps/scripts/resumir_delta_auditoria_ramas_v2.py",
            "precios-supermercados-sps/scripts/exportar_revision_homologacion.py",
            "precios-supermercados-sps/src/precios_supermercados/product_identity_v2.py",
            "precios-supermercados-sps/.automation/product-identity-audit-request.json",
            "precios-supermercados-sps/docs/audits/precios-sps-historical-branch-overrides.json",
            "precios-supermercados-sps/docs/audits/precios-sps-historical-branch-decisions-v2.json",
            ".github/workflows/precios-supermercados-sps-historical-branch-audit.yml",
        ],
    }
    assert set(workflow["jobs"]) == {"audit"}
    audit = workflow["jobs"]["audit"]
    assert "permissions" not in audit
    assert "environment" not in audit
    identity_step = next(
        step
        for step in audit["steps"]
        if step.get("name")
        == "Generar auditoría privada de identidad sin mutaciones ni scraping"
    )
    assert identity_step["if"] == "${{ !cancelled() }}"


def test_identity_audit_is_private_fail_closed_and_non_mutating() -> None:
    raw = WORKFLOW.read_text(encoding="utf-8")
    assert "product_identity_audit_request_closed_set_mismatch" in raw
    assert "product_identity_audit_request_read_only_required" in raw
    assert "scripts/exportar_revision_homologacion.py" in raw
    assert "run-artifacts/product-identity-audit/review-queue.json" in raw
    assert "TURSO_DATABASE_URL: ${{ secrets.TURSO_DATABASE_URL }}" in raw
    assert "TURSO_AUTH_TOKEN: ${{ secrets.TURSO_AUTH_TOKEN }}" in raw
    assert "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a" in raw
    assert "retention-days: 30" in raw
    assert "contents: write" not in raw
    assert "git push" not in raw
    assert "INSERT INTO" not in raw
    assert "UPDATE " not in raw
    assert "DELETE FROM" not in raw
    assert "scripts/probar_la_colonia.py" not in raw
    assert "scripts/descubrir_facets_la_colonia.py" not in raw
