from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "precios-supermercados-sps-homologation-refresh.yml"
PUBLICATION = ROOT / ".github" / "workflows" / "precios-supermercados-sps-safe-analytics-publication.yml"


def test_review_queue_is_generated_only_on_manual_homologation_runs() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "Generar cola privada de revisión" in workflow
    assert "if: ${{ github.event_name == 'workflow_dispatch' }}" in workflow
    assert "exportar_revision_homologacion.py" in workflow
    assert "review-queue.json" in workflow
    assert "path: precios-supermercados-sps/run-artifacts/homologation/" in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "persist-credentials: false" in workflow


def test_publication_contract_does_not_copy_review_queue() -> None:
    publication = PUBLICATION.read_text(encoding="utf-8")
    assert "review-queue.json" not in publication
    assert "'contains_review_queue': False" in publication
