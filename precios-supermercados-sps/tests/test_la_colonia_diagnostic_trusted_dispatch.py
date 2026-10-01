from __future__ import annotations

import json
from pathlib import Path

import pytest

from precios_supermercados.automation.la_colonia_file_dispatcher import (
    ALLOWED_WORKFLOWS,
    DIAGNOSTIC_PLAN,
    DIAGNOSTIC_WORKFLOW,
    LIVE_WORKFLOW,
    build_controller_comment,
    evaluate_file_request,
    request_marker,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = REPO_ROOT / ".github/workflows/precios-supermercados-sps-la-colonia-command.yml"
DIAGNOSTIC = REPO_ROOT / ".github/workflows/precios-supermercados-sps-la-colonia-diagnostic.yml"


def valid_context():
    return {
        "repository_owner": "jchernandez-portfolio",
        "repository_full_name": "jchernandez-portfolio/precios-supermercados-sps",
        "pr_number": 7,
        "state": "open",
        "base_repo_full_name": "jchernandez-portfolio/precios-supermercados-sps",
        "head_repo_full_name": "jchernandez-portfolio/precios-supermercados-sps",
        "head_repo_fork": False,
        "head_ref": "feature/la-colonia-full-crawl-validation",
        "head_sha": "a" * 40,
        "command_file_changed": True,
        "command_file_status": "ok",
    }


def normal_command(**overrides):
    value = {
        "request_id": "la-colonia-smoke-10-001",
        "supermarket": "la_colonia",
        "mode": "smoke",
        "page_size": 10,
        "max_pages": 2,
        "max_products": 0,
        "delay_seconds": 1.5,
        "profile": "baseline",
        "thresholds": None,
        "allow_full": False,
    }
    value.update(overrides)
    return value


def diagnostic_command(**overrides):
    value = {
        "request_id": "la-colonia-window-diagnostic-380-399-001",
        "supermarket": "la_colonia",
        "mode": "diagnostic_overlap",
        "diagnostic_plan": "frontier_380_399_v1",
        "delay_seconds": 1.5,
        "allow_full": False,
    }
    value.update(overrides)
    return value


def decide(command, comments=()):
    raw = command if isinstance(command, str) else json.dumps(command)
    return evaluate_file_request(valid_context(), raw, comments)


def test_allow_list_contiene_exactamente_dos_workflows():
    assert ALLOWED_WORKFLOWS == frozenset({LIVE_WORKFLOW, DIAGNOSTIC_WORKFLOW})


def test_contrato_diagnostico_exacto_es_aceptado():
    decision = decide(diagnostic_command())
    assert decision.accepted is True
    assert decision.mode == "diagnostic_overlap"
    assert decision.workflow == DIAGNOSTIC_WORKFLOW
    assert decision.inputs == {
        "request_id": "la-colonia-window-diagnostic-380-399-001",
        "diagnostic_plan": DIAGNOSTIC_PLAN,
        "delay_seconds": "1.5",
    }


def test_request_id_diagnostico_invalido_se_rechaza():
    decision = decide(diagnostic_command(request_id="INVALID REQUEST"))
    assert decision.accepted is False
    assert "request_id" in decision.reason


def test_comentario_diagnostico_es_sanitizado_y_no_es_autoridad():
    decision = decide(diagnostic_command())
    comment = build_controller_comment(
        decision,
        controller_run_id=123,
        controller_url="https://github.com/jchernandez-portfolio/precios-supermercados-sps/actions/runs/123",
    )
    assert "diagnostic_overlap" in comment
    assert DIAGNOSTIC_WORKFLOW in comment
    assert request_marker("la-colonia-window-diagnostic-380-399-001") not in comment
    for forbidden in ("productId", "productName", "price", "payload"):
        assert forbidden not in comment

    repeated = decide(diagnostic_command(), [comment])
    assert repeated.accepted is True


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"diagnostic_plan": "otro"}, "plan diagnóstico"),
        ({"delay_seconds": 1.0}, "exactamente 1.5"),
        ({"allow_full": True}, "allow_full"),
    ],
)
def test_valores_diagnosticos_no_autorizados_se_rechazan(override, reason):
    decision = decide(diagnostic_command(**override))
    assert decision.accepted is False
    assert reason in decision.reason


@pytest.mark.parametrize(
    "field",
    [
        "from",
        "to",
        "windows",
        "order_by",
        "orderBy",
        "URL",
        "url",
        "query",
        "selectedFacets",
        "max_requests",
        "page_size",
        "max_pages",
        "max_products",
        "profile",
        "thresholds",
        "workflow",
        "full",
    ],
)
def test_campos_arbitrarios_en_diagnostico_se_rechazan(field):
    command = diagnostic_command()
    command[field] = "forbidden"
    decision = decide(command)
    assert decision.accepted is False
    assert "campo desconocido" in decision.reason


@pytest.mark.parametrize(
    "field",
    [
        "request_id",
        "supermarket",
        "mode",
        "diagnostic_plan",
        "delay_seconds",
        "allow_full",
    ],
)
def test_campos_diagnosticos_faltantes_se_rechazan(field):
    command = diagnostic_command()
    command.pop(field)
    decision = decide(command)
    assert decision.accepted is False


def test_json_no_objeto_se_rechaza():
    decision = decide("[]")
    assert decision.accepted is False
    assert "objeto" in decision.reason


def test_modo_desconocido_se_rechaza():
    decision = decide(normal_command(mode="otro"))
    assert decision.accepted is False
    assert "modo" in decision.reason.lower()


def test_smoke_conserva_workflow_normal():
    decision = decide(normal_command())
    assert decision.accepted is True
    assert decision.mode == "smoke"
    assert decision.workflow == LIVE_WORKFLOW


def test_staged_baseline_por_paginas_conserva_compatibilidad():
    decision = decide(
        normal_command(
            request_id="staged-pages-001",
            mode="staged",
            page_size=20,
            max_pages=5,
            max_products=0,
        )
    )
    assert decision.accepted is True
    assert decision.workflow == LIVE_WORKFLOW
    assert decision.inputs["max_pages"] == "5"


def test_staged_baseline_por_productos_conserva_compatibilidad():
    decision = decide(
        normal_command(
            request_id="staged-products-001",
            mode="staged",
            page_size=20,
            max_pages=0,
            max_products=100,
        )
    )
    assert decision.accepted is True
    assert decision.workflow == LIVE_WORKFLOW
    assert decision.inputs["max_products"] == "100"


def test_staged_validation_conserva_compatibilidad():
    thresholds = {
        "max_missing_price_ratio": 0.05,
        "max_duplicate_sku_ratio": 0.01,
        "max_duplicate_product_ratio": 0.01,
        "max_total_change_ratio": 0.005,
    }
    decision = decide(
        normal_command(
            request_id="staged-validation-001",
            mode="staged",
            page_size=20,
            max_pages=0,
            max_products=100,
            profile="validation",
            thresholds=thresholds,
        )
    )
    assert decision.accepted is True
    assert decision.workflow == LIVE_WORKFLOW
    assert decision.inputs["max_total_change_ratio"] == "0.005"


def test_full_sigue_prohibido():
    decision = decide(normal_command(mode="full"))
    assert decision.accepted is False
    assert "full" in decision.reason


def test_controlador_privilegiado_esta_bloqueado_y_usa_checkout_inmutable():
    text = CONTROLLER.read_text(encoding="utf-8")
    assert "actions: write" not in text
    assert "ref: ${{ github.workflow_sha }}" in text
    assert "persist-credentials: false" in text
    assert "github.event.pull_request.head" not in text


def test_controlador_no_contiene_dispatch_ni_autoridad_por_comentario():
    text = CONTROLLER.read_text(encoding="utf-8")
    assert "/actions/workflows/" not in text
    assert "listComments" not in text
    assert "issues: write" not in text


def test_workflow_diagnostico_es_manual_y_cerrado():
    text = DIAGNOSTIC.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    for trigger in (
        "schedule:",
        "push:",
        "pull_request:",
        "pull_request_target:",
        "issue_comment:",
    ):
        assert trigger not in text
    assert "timeout-minutes: 15" in text
    assert "contents: read" in text
    assert "frontier_380_399_v1" in text
    assert '"1.5"' in text


def test_workflow_diagnostico_no_expone_inputs_arbitrarios():
    text = DIAGNOSTIC.read_text(encoding="utf-8")
    for forbidden in (
        "from:",
        "to:",
        "windows:",
        "order_by:",
        "max_requests:",
        "URL:",
        "query:",
        "selectedFacets:",
        "profile:",
        "thresholds:",
        "allow_full:",
        "full:",
    ):
        assert forbidden not in text


def test_workflow_diagnostico_mapea_codigos_tecnicos():
    text = DIAGNOSTIC.read_text(encoding="utf-8")
    assert '"0"|"2"' in text
    assert '"3"|"4"|"5"' in text
    assert "diagnostic-summary.json" in text
    assert "diagnostic-summary.md" in text
