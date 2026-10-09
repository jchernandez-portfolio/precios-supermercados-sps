"""Worker de Cloudflare que dispara el corte diario a las 01:43 Honduras.

El cron de GitHub llegaba 5–9 h tarde (2026-10-02 a 10-09). Las pruebas del
Worker son de Node (``node --test``); los runners de GitHub traen Node.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

TRIGGER = Path(__file__).resolve().parents[1] / "edge" / "daily-trigger"


def test_wrangler_config_runs_once_a_day_at_0743_utc_without_public_route() -> None:
    config = json.loads((TRIGGER / "wrangler.json").read_text(encoding="utf-8"))
    assert config["name"] == "precios-sps-daily-trigger"
    assert config["main"] == "worker.mjs"
    assert config["triggers"] == {"crons": ["43 7 * * *"]}
    assert config["workers_dev"] is False
    assert "routes" not in config and "vars" not in config


def test_worker_only_dispatches_the_daily_workflow_with_its_own_secret() -> None:
    source = (TRIGGER / "worker.mjs").read_text(encoding="utf-8")
    assert "precios-supermercados-sps-la-colonia-mvp-update.yml" in source
    assert "env.GITHUB_DISPATCH_TOKEN" in source
    assert 'ref: "main"' in source
    assert source.count("fetchImpl(") == 1  # una sola llamada saliente: el dispatch


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible")
def test_worker_node_suite() -> None:
    result = subprocess.run(
        ["node", "--test", "test/worker.test.mjs"], cwd=TRIGGER, capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stdout + result.stderr
