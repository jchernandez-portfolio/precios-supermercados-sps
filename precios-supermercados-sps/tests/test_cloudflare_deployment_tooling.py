from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EDGE_ROOT = PROJECT_ROOT / "edge" / "cloudflare"
PROBE_RUNBOOK = PROJECT_ROOT / "docs" / "cloudflare-controlled-probe-runbook.md"
PRODUCTION_RUNBOOK = PROJECT_ROOT / "docs" / "cloudflare-production-deploy-runbook.md"
WRANGLER_VERSION = "4.125.0"
PINNED_WRANGLER = f"npx --yes wrangler@{WRANGLER_VERSION}"


def _bash_commands(markdown: str) -> tuple[str, ...]:
    commands: list[str] = []
    in_bash = False
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if line == "```bash":
            assert not in_bash
            in_bash = True
            continue
        if line == "```" and in_bash:
            in_bash = False
            continue
        if in_bash and line and not line.startswith("#"):
            commands.append(line)
    assert not in_bash, "bloque bash sin cerrar en runbook"
    return tuple(commands)


def test_wrangler_cli_is_pinned_and_no_productive_deploy_script_exists() -> None:
    package = json.loads((EDGE_ROOT / "package.json").read_text(encoding="utf-8"))
    scripts = package["scripts"]

    assert scripts["wrangler"] == PINNED_WRANGLER
    assert scripts["deploy:probe-origin"] == (
        f"{PINNED_WRANGLER} deploy --config wrangler.probe-origin.json"
    )
    assert "deploy:probe" not in scripts
    assert "deploy:production" not in scripts
    assert all(" --config wrangler.json" not in value for value in scripts.values())


def test_probe_runbook_uses_only_the_pinned_wrangler_entrypoint() -> None:
    raw = PROBE_RUNBOOK.read_text(encoding="utf-8")
    commands = _bash_commands(raw)

    assert f"wrangler = {PINNED_WRANGLER}" in raw
    assert "npm run wrangler -- --version" in commands
    assert "npm run deploy:probe-origin" in commands
    assert any(
        command.startswith(
            "npm run wrangler -- deploy --config wrangler.probe.json --secrets-file "
        )
        for command in commands
    )
    assert all(not command.startswith("npx wrangler") for command in commands)
    assert all("wrangler@latest" not in command for command in commands)
    assert all("--config wrangler.json" not in command for command in commands)
    assert WRANGLER_VERSION in raw


def test_productive_runbook_is_manual_pinned_and_keeps_live_closed() -> None:
    raw = PRODUCTION_RUNBOOK.read_text(encoding="utf-8")
    commands = _bash_commands(raw)

    assert f"wrangler = {PINNED_WRANGLER}" in raw
    assert "npm run wrangler -- --version" in commands
    assert any(
        command.startswith(
            "npm run wrangler -- deploy --config wrangler.json --secrets-file /ruta/fuera-del-repo/"
        )
        for command in commands
    )
    assert "npm run wrangler -- deployments status --config wrangler.json --json" in commands
    assert all(not command.startswith("npx wrangler") for command in commands)
    assert all("wrangler@latest" not in command for command in commands)
    assert "deploy:production" in raw
    assert "no expone un script automático `deploy:production`" in raw
    assert "ACTIVE_AUTHORIZATION_IDS = []" in raw
    assert "no autoriza ninguna solicitud a la colonia" in raw.casefold()
    assert "fuera del repositorio" in raw
    assert WRANGLER_VERSION in raw


def test_cloudflare_local_secret_and_state_files_are_ignored() -> None:
    ignored = {
        line.strip()
        for line in (EDGE_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert {
        "node_modules/",
        ".wrangler/",
        ".dev.vars",
        ".dev.vars.*",
        "*.secrets.json",
        "*.secrets.env",
        ".wrangler-secrets-*",
    }.issubset(ignored)
