import json
from pathlib import Path

import pytest

from precios_supermercados.automation.la_colonia_file_dispatcher import (
    build_controller_comment,
    evaluate_file_request,
    request_marker,
)


def valid_context(**overrides):
    context = {
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
    context.update(overrides)
    return context


def valid_command(**overrides):
    command = {
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
    command.update(overrides)
    return command


def decide(command=None, *, context=None, comments=()):
    command = valid_command() if command is None else command
    context = valid_context() if context is None else context
    raw = command if isinstance(command, str) else json.dumps(command)
    return evaluate_file_request(context, raw, comments)


@pytest.mark.parametrize("size", [10, 20, 30, 50])
def test_smoke_valido(size):
    decision = decide(valid_command(page_size=size, request_id=f"smoke-{size}-001"))
    assert decision.accepted
    assert decision.inputs["page_size"] == str(size)
    assert decision.inputs["max_pages"] == "2"
    assert decision.inputs["max_products"] == "0"
    assert decision.inputs["allow_full"] is False


def test_request_id_invalido():
    decision = decide(valid_command(request_id="INVALID REQUEST"))
    assert not decision.accepted
    assert "request_id" in decision.reason


def test_comentarios_no_controlan_replay_ni_autorizacion():
    marker = request_marker("la-colonia-smoke-10-001")
    decision = decide(comments=[f"resultado anterior\n{marker}"])
    assert decision.accepted
    assert decision.should_comment


def test_archivo_no_modificado_en_ultimo_commit():
    decision = decide(context=valid_context(command_file_changed=False, command_file_status="not_modified"))
    assert not decision.accepted
    assert not decision.should_comment


def test_archivo_inexistente():
    decision = evaluate_file_request(
        valid_context(command_file_status="missing"),
        None,
    )
    assert not decision.accepted
    assert decision.should_comment
    assert "no existe" in decision.reason


def test_json_invalido():
    decision = decide('{"request_id":')
    assert not decision.accepted
    assert "JSON válido" in decision.reason


def test_campo_desconocido():
    decision = decide(valid_command(extra="x"))
    assert not decision.accepted
    assert "desconocido" in decision.reason


def test_supermercado_invalido():
    decision = decide(valid_command(supermarket="otro"))
    assert not decision.accepted


def test_fork_rechazado():
    decision = decide(
        context=valid_context(
            head_repo_full_name="otro/Portafolio",
            head_repo_fork=True,
        )
    )
    assert not decision.accepted
    assert "forks" in decision.reason


def test_pr_cerrado():
    decision = decide(context=valid_context(state="closed"))
    assert not decision.accepted
    assert "abierto" in decision.reason


def test_repositorio_diferente():
    decision = decide(context=valid_context(repository_full_name="otro/Portafolio"))
    assert not decision.accepted


def test_propietario_diferente():
    decision = decide(context=valid_context(repository_owner="otro"))
    assert not decision.accepted


def test_full_rechazado():
    decision = decide(valid_command(mode="full"))
    assert not decision.accepted
    assert "full" in decision.reason


def test_allow_full_rechazado():
    decision = decide(valid_command(allow_full=True))
    assert not decision.accepted


def test_page_size_invalido():
    decision = decide(valid_command(page_size=40))
    assert not decision.accepted


def test_staged_con_paginas():
    command = valid_command(
        request_id="staged-pages-001",
        mode="staged",
        page_size=20,
        max_pages=10,
        max_products=0,
        profile="baseline",
    )
    decision = decide(command)
    assert decision.accepted
    assert decision.inputs["max_pages"] == "10"


def test_staged_con_productos():
    command = valid_command(
        request_id="staged-products-001",
        mode="staged",
        page_size=20,
        max_pages=0,
        max_products=100,
        profile="baseline",
    )
    decision = decide(command)
    assert decision.accepted
    assert decision.inputs["max_products"] == "100"


def test_ambos_limites_presentes():
    command = valid_command(
        request_id="staged-both-001",
        mode="staged",
        max_pages=5,
        max_products=100,
    )
    assert not decide(command).accepted


def test_ningun_limite_presente():
    command = valid_command(
        request_id="staged-none-001",
        mode="staged",
        max_pages=0,
        max_products=0,
    )
    assert not decide(command).accepted


def test_productos_no_divisibles():
    command = valid_command(
        request_id="staged-products-div-001",
        mode="staged",
        page_size=30,
        max_pages=0,
        max_products=100,
    )
    assert not decide(command).accepted


def test_validation_sin_umbrales():
    command = valid_command(
        request_id="validation-no-thresholds-001",
        mode="staged",
        page_size=20,
        max_pages=0,
        max_products=100,
        profile="validation",
        thresholds=None,
    )
    assert not decide(command).accepted


def test_umbrales_validos():
    thresholds = {
        "max_missing_price_ratio": 0.05,
        "max_duplicate_sku_ratio": 0.01,
        "max_duplicate_product_ratio": 0.01,
        "max_total_change_ratio": 0.005,
    }
    command = valid_command(
        request_id="validation-thresholds-001",
        mode="staged",
        page_size=20,
        max_pages=0,
        max_products=100,
        profile="validation",
        thresholds=thresholds,
    )
    decision = decide(command)
    assert decision.accepted
    assert decision.inputs["max_total_change_ratio"] == "0.005"


def test_umbral_fuera_de_rango():
    thresholds = {
        "max_missing_price_ratio": 1.1,
        "max_duplicate_sku_ratio": 0.01,
        "max_duplicate_product_ratio": 0.01,
        "max_total_change_ratio": 0.005,
    }
    command = valid_command(
        request_id="validation-bad-threshold-001",
        mode="staged",
        page_size=20,
        max_pages=0,
        max_products=100,
        profile="validation",
        thresholds=thresholds,
    )
    assert not decide(command).accepted


def test_intento_de_inyeccion():
    decision = decide(valid_command(request_id="x;$(touch-pwned)"))
    assert not decision.accepted
    assert decision.inputs is None


def test_comentario_de_aceptacion():
    decision = decide()
    comment = build_controller_comment(
        decision,
        controller_run_id=12345,
        controller_url="https://github.com/jchernandez-portfolio/precios-supermercados-sps/actions/runs/12345",
    )
    assert "Solicitud válida pero bloqueada" in comment
    assert "la-colonia-smoke-10-001" in comment
    assert "a" * 40 in comment
    assert "feature/la-colonia-full-crawl-validation" in comment
    assert "no se envió workflow_dispatch" in comment
    assert request_marker("la-colonia-smoke-10-001") not in comment


def test_comentario_de_rechazo_no_publica_contenido_inseguro():
    unsafe = "x;$(curl evil)"
    decision = decide(valid_command(request_id=unsafe))
    comment = build_controller_comment(
        decision,
        controller_run_id=12345,
        controller_url="https://github.com/jchernandez-portfolio/precios-supermercados-sps/actions/runs/12345",
    )
    assert "Solicitud rechazada" in comment
    assert "no se envió workflow_dispatch" in comment
    assert unsafe not in comment






def test_evento_reemplazado_no_despacha():
    decision = decide(context=valid_context(command_file_status="superseded"))
    assert not decision.accepted
    assert not decision.should_comment
