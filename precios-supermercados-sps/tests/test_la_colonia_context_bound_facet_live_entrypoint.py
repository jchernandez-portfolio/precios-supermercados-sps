from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "precios-supermercados-sps/scripts/ejecutar_facets_context_bound_la_colonia.py"
WORKFLOW = REPO_ROOT / ".github/workflows/precios-supermercados-sps-la-colonia-live.yml"


def _module():
    spec = importlib.util.spec_from_file_location("facet_live_entrypoint_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _trusted_env(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "jchernandez-portfolio/precios-supermercados-sps")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv(
        "GITHUB_WORKFLOW_REF",
        "jchernandez-portfolio/precios-supermercados-sps/.github/workflows/"
        "precios-supermercados-sps-la-colonia-live.yml@refs/heads/main",
    )
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    monkeypatch.setenv("GITHUB_RUN_ID", "32766000000")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")


def test_main_actual_rechaza_authorization_historica_antes_de_oidc_browser_o_edge(
    monkeypatch, tmp_path: Path
) -> None:
    module = _module()
    _trusted_env(monkeypatch)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("no debe alcanzarse I/O externo")

    monkeypatch.setattr(module, "_request_oidc_token", forbidden)
    monkeypatch.setattr(module, "_preflight_edge_configuration", forbidden)
    monkeypatch.setattr(module, "_observe_sps_context_and_execute", forbidden)

    code = module.main(
        [
            "--authorization-id",
            "SPS-context-and-root-facets-002",
            "--output-dir",
            str(tmp_path),
        ]
    )
    assert code == 3
    status = json.loads((tmp_path / "facet-entrypoint-status.json").read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert status["error_code"] == "human_live_authorization_rejected"
    assert status["raw_values_exposed"] is False
    assert status["production_authority"] is False
    assert status["catalog_accepted"] is False
    assert status["extraction_enabled"] is False
    assert "SPS-context-and-root-facets-002" not in json.dumps(status)


def test_contexto_github_no_main_falla_antes_del_gate_humano(monkeypatch, tmp_path: Path) -> None:
    module = _module()
    _trusted_env(monkeypatch)
    monkeypatch.setenv("GITHUB_REF", "refs/heads/feature/attacker")
    code = module.main(
        [
            "--authorization-id",
            "SPS-context-and-root-facets-999",
            "--output-dir",
            str(tmp_path),
        ]
    )
    assert code == 3
    status = json.loads((tmp_path / "facet-entrypoint-status.json").read_text(encoding="utf-8"))
    assert status["error_code"] == "github_ref_not_main"


@pytest.mark.parametrize(
    "value",
    [
        "http://token.actions.githubusercontent.com/oidc",
        "https://evil.invalid/oidc",
        "https://user:pass@token.actions.githubusercontent.com/oidc",
        "https://token.actions.githubusercontent.com:443/oidc",
        "https://token.actions.githubusercontent.com/oidc?audience=attacker",
        "https://token.actions.githubusercontent.com/oidc#fragment",
    ],
)
def test_oidc_request_url_es_exactamente_github_y_audience_no_es_caller_controlled(value: str) -> None:
    module = _module()
    with pytest.raises(module.FacetLiveEntrypointSafetyError):
        module._validate_oidc_url(value)


def test_oidc_request_url_agrega_audience_fijo() -> None:
    module = _module()
    result = module._validate_oidc_url(
        "https://token.actions.githubusercontent.com/oidc?foo=bar"
    )
    assert result.startswith("https://token.actions.githubusercontent.com/oidc?foo=bar&audience=")
    assert "urn%3Aprecios-sps%3Acloudflare%3Acollector%3Av1" in result


