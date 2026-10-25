from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "versionar_super_compras", Path(__file__).resolve().parents[1] / "scripts" / "versionar_super_compras.py"
)
ver = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ver
SPEC.loader.exec_module(ver)


def app(tmp_path: Path, version: str = "20261025-1") -> Path:
    folder = tmp_path / "super-compras"
    folder.mkdir()
    (folder / "index.html").write_text(f'<script src="app.js?v={version}"></script><link href="styles.css?v={version}">')
    (folder / "app.js").write_text(f'import {{ a }} from "./data.js?v={version}";\nimport b from "./illustration.js?v={version}";')
    (folder / "data.js").write_text('export const x = `${PHOTO_BASE}${id}.webp?v=${version}`;')
    (folder / "sw.js").write_text(f'const VERSION = "super-compras-{version}";\nconst SHELL = ["app.js?v={version}", "data.js?v={version}"];')
    return folder


def test_real_app_has_one_version() -> None:
    assert ver.VERSION.fullmatch(ver.check())


def test_set_rewrites_every_token_and_check_reports_it(tmp_path: Path) -> None:
    folder = app(tmp_path)
    assert ver.apply("20261101-3", folder) == ["index.html", "app.js", "sw.js"]
    assert ver.check(folder) == "20261101-3"
    # Las fotos v1 llevan ?v=${version} (plantilla), que no es una versión de la app.
    assert "${version}" in (folder / "data.js").read_text()
    with pytest.raises(ver.VersionError):
        ver.apply("v2", folder)


def test_mixed_versions_fail_check(tmp_path: Path) -> None:
    folder = app(tmp_path)
    (folder / "sw.js").write_text('const VERSION = "super-compras-20260101-9";')
    with pytest.raises(ver.VersionError, match="distintas"):
        ver.check(folder)


def test_auto_and_stamp_versions() -> None:
    assert ver.next_version(date(2026, 10, 25), "20261025-1") == "20261025-2"
    assert ver.next_version(date(2026, 10, 26), "20261025-7") == "20261026-1"
    assert ver.next_version(date(2026, 10, 26), None) == "20261026-1"


def test_cli_stamp_uses_commit_prefix(tmp_path: Path, capsys) -> None:
    folder = app(tmp_path)
    assert ver.main(["--stamp", "ABCDEF1234567", "--app-dir", str(folder)]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"{date.today().strftime('%Y%m%d')}-abcdef1 (")
    assert ver.main(["--stamp", "zz", "--app-dir", str(folder)]) == 1
