from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = (
    REPO_ROOT
    / ".github/workflows/precios-supermercados-sps-la-colonia-dispatch-recovery.yml"
)
VALIDATOR_PATH = (
    REPO_ROOT
    / "precios-supermercados-sps/scripts/validar_resultado_controlador_la_colonia.js"
)
SOURCE_RUN_ID = "31048001628"
LIVE_WORKFLOW = ".github/workflows/precios-supermercados-sps-la-colonia-live.yml"
DIAGNOSTIC_WORKFLOW = (
    ".github/workflows/precios-supermercados-sps-la-colonia-diagnostic.yml"
)


def _workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def _artifact(**overrides):
    value = {
        "accepted": True,
        "request_id": "la-colonia-staged-test-001",
        "pr_number": 7,
        "head_sha": "a" * 40,
        "ref": "feature/la-colonia-full-crawl-validation",
        "dispatch_sent": True,
        "live_run_id": "31048012566",
        "live_run_url": "https://example.invalid/actions/runs/31048012566",
        "comment_published": True,
        "comment_method": "rest",
        "controller_run_id": SOURCE_RUN_ID,
        "controller_url": "https://example.invalid/actions/runs/31048001628",
        "reason": "",
        "warnings": [],
    }
    value.update(overrides)
    return value


def _run_validator(tmp_path: Path, artifact: object):
    node = shutil.which("node")
    assert node is not None, "Node.js es necesario para validar el observador offline"
    result_path = tmp_path / "dispatcher-result.json"
    summary_path = tmp_path / "summary.md"
    result_path.write_text(json.dumps(artifact), encoding="utf-8")
    env = {
        **os.environ,
        "RESULT_PATH": str(result_path),
        "SOURCE_RUN_ID": SOURCE_RUN_ID,
        "GITHUB_STEP_SUMMARY": str(summary_path),
    }
    completed = subprocess.run(
        [node, str(VALIDATOR_PATH)],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    summary = summary_path.read_text(encoding="utf-8") if summary_path.exists() else ""
    return completed, summary


def _new_artifact(mode: str, workflow: str, **overrides):
    return _artifact(mode=mode, workflow=workflow, **overrides)








def test_artefacto_normal_antiguo_sigue_siendom_valido(tmp_path):
    completed, summary = _run_validator(tmp_path, _artifact())
    assert completed.returncode == 0
    assert "legacy_artifact: `true`" in summary
    assert "mode: ``" in summary
    assert "workflow: ``" in summary


def test_artefacto_nuevo_staged_es_valido(tmp_path):
    completed, summary = _run_validator(
        tmp_path, _new_artifact("staged", LIVE_WORKFLOW)
    )
    assert completed.returncode == 0
    assert "mode: `staged`" in summary
    assert f"workflow: `{LIVE_WORKFLOW}`" in summary
    assert "legacy_artifact: `false`" in summary


def test_artefacto_smoke_es_valido(tmp_path):
    completed, summary = _run_validator(
        tmp_path,
        _new_artifact(
            "smoke",
            LIVE_WORKFLOW,
            request_id="la-colonia-smoke-test-001",
        ),
    )
    assert completed.returncode == 0
    assert "mode: `smoke`" in summary


def test_artefacto_diagnostico_es_valido(tmp_path):
    completed, summary = _run_validator(
        tmp_path,
        _new_artifact(
            "diagnostic_overlap",
            DIAGNOSTIC_WORKFLOW,
            request_id="la-colonia-window-diagnostic-test-001",
        ),
    )
    assert completed.returncode == 0
    assert "mode: `diagnostic_overlap`" in summary
    assert f"workflow: `{DIAGNOSTIC_WORKFLOW}`" in summary


@pytest.mark.parametrize("mode", ["smoke", "staged"])
def test_workflow_normal_correcto_para_modos_normales(tmp_path, mode):
    completed, _ = _run_validator(tmp_path, _new_artifact(mode, LIVE_WORKFLOW))
    assert completed.returncode == 0


def test_workflow_diagnostico_correcto(tmp_path):
    completed, _ = _run_validator(
        tmp_path, _new_artifact("diagnostic_overlap", DIAGNOSTIC_WORKFLOW)
    )
    assert completed.returncode == 0


def test_modo_desconocido_es_rechazado(tmp_path):
    completed, _ = _run_validator(
        tmp_path, _new_artifact("unknown", LIVE_WORKFLOW)
    )
    assert completed.returncode == 1
    assert "modo del controlador no está permitido" in completed.stderr


def test_workflow_desconocido_es_rechazado(tmp_path):
    completed, _ = _run_validator(
        tmp_path, _new_artifact("staged", ".github/workflows/arbitrary.yml")
    )
    assert completed.returncode == 1
    assert "workflow del controlador no está permitido" in completed.stderr


@pytest.mark.parametrize(
    ("mode", "workflow"),
    [
        ("smoke", DIAGNOSTIC_WORKFLOW),
        ("staged", DIAGNOSTIC_WORKFLOW),
        ("diagnostic_overlap", LIVE_WORKFLOW),
    ],
)
def test_relacion_mode_workflow_invalida_es_rechazada(tmp_path, mode, workflow):
    completed, _ = _run_validator(tmp_path, _new_artifact(mode, workflow))
    assert completed.returncode == 1
    assert "relación mode/workflow" in completed.stderr


def test_campo_extra_es_rechazado(tmp_path):
    completed, _ = _run_validator(tmp_path, _artifact(extra="not-allowed"))
    assert completed.returncode == 1
    assert "campos inesperados" in completed.stderr


@pytest.mark.parametrize(
    ("mode", "workflow"),
    [
        ([], LIVE_WORKFLOW),
        ({"value": "staged"}, LIVE_WORKFLOW),
        ("staged", []),
        ("staged", {"path": LIVE_WORKFLOW}),
        (1, LIVE_WORKFLOW),
        ("staged", 1),
    ],
)
def test_mode_o_workflow_con_tipo_incorrecto_es_rechazado(
    tmp_path, mode, workflow
):
    completed, _ = _run_validator(tmp_path, _new_artifact(mode, workflow))
    assert completed.returncode == 1
    assert "deben ser strings" in completed.stderr


@pytest.mark.parametrize(
    "artifact",
    [
        _artifact(mode="staged"),
        _artifact(workflow=LIVE_WORKFLOW),
    ],
)
def test_mode_y_workflow_deben_aparecer_juntos(tmp_path, artifact):
    completed, _ = _run_validator(tmp_path, artifact)
    assert completed.returncode == 1
    assert "ambos presentes o ambos ausentes" in completed.stderr


def test_controlador_aceptado_y_comentario_publicado_no_falla(tmp_path):
    completed, summary = _run_validator(
        tmp_path,
        _new_artifact(
            "staged",
            LIVE_WORKFLOW,
            accepted=True,
            dispatch_sent=True,
            comment_published=True,
        ),
    )
    assert completed.returncode == 0
    assert "comment_published: `true`" in summary
    assert "RECOVERY_REQUIRED" not in completed.stderr


def test_controlador_aceptado_y_comentario_bloqueado_falla_controladamente(
    tmp_path,
):
    completed, summary = _run_validator(
        tmp_path,
        _new_artifact(
            "diagnostic_overlap",
            DIAGNOSTIC_WORKFLOW,
            request_id="la-colonia-window-diagnostic-test-002",
            accepted=True,
            dispatch_sent=True,
            comment_published=False,
            live_run_id="31048012566",
        ),
    )
    assert completed.returncode == 1
    assert "RECOVERY_REQUIRED" in completed.stderr
    assert "live_run_id=31048012566" in completed.stderr
    assert "requiere recuperación controlada" in completed.stderr
    assert "comment_published: `false`" in summary


def test_recuperacion_requiere_live_run_id_valido(tmp_path):
    completed, _ = _run_validator(
        tmp_path,
        _new_artifact(
            "staged",
            LIVE_WORKFLOW,
            comment_published=False,
            live_run_id=None,
        ),
    )
    assert completed.returncode == 1
    assert "faltan identificadores válidos" in completed.stderr


def test_resumen_es_sanitizado_y_no_expone_urls_ni_datos_comerciales(tmp_path):
    completed, summary = _run_validator(
        tmp_path,
        _new_artifact(
            "staged",
            LIVE_WORKFLOW,
            live_run_url="https://secret.invalid/run/1",
            controller_url="https://secret.invalid/controller/1",
        ),
    )
    assert completed.returncode == 0
    for forbidden in (
        "https://secret.invalid",
        "productId",
        "productName",
        "itemId",
        "price",
        "brand",
    ):
        assert forbidden not in summary


def test_campos_comerciales_adicionales_son_rechazados(tmp_path):
    completed, summary = _run_validator(
        tmp_path,
        _new_artifact("staged", LIVE_WORKFLOW, productName="Synthetic")
    )
    assert completed.returncode == 1
    assert "campos inesperados" in completed.stderr
    assert summary == ""


def test_resumen_incluye_mode_y_workflow_solo_como_strings_validados(tmp_path):
    completed, summary = _run_validator(
        tmp_path, _new_artifact("diagnostic_overlap", DIAGNOSTIC_WORKFLOW)
    )
    assert completed.returncode == 0
    assert "mode: `diagnostic_overlap`" in summary
    assert f"workflow: `{DIAGNOSTIC_WORKFLOW}`" in summary
    assert "[object Object]" not in summary
