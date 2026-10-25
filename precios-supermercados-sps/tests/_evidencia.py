"""Evidencia archivada fuera del repositorio (GitHub Release).

Las capturas crudas grandes de ``reports/`` viven en el release que indica
``reports/evidencia-archivada.json``. Las pruebas que las necesitan llaman a
``asegurar(...)`` al inicio del módulo: si el archivo está en su ruta original se
usa tal cual; si no, con ``PRECIOS_SPS_EVIDENCIA=descargar`` se baja del release,
se comprueba su SHA-256 y se deja en la ruta original (ignorada por git); en
cualquier otro caso el módulo se omite con un aviso claro.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "reports" / "evidencia-archivada.json"
REPOSITORY = "jchernandez-portfolio/precios-supermercados-sps"
ENV = "PRECIOS_SPS_EVIDENCIA"
ATTEMPTS = 3


def _index() -> dict[str, dict[str, object]]:
    document = json.loads(INDEX.read_text(encoding="utf-8"))
    return {str(item["path"]): {**item, "release": document["release"]} for item in document["files"]}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, target: Path, expected: str, size: int) -> None:
    last: Exception | None = None
    for attempt in range(ATTEMPTS):
        try:
            with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 - URL fija del release
                data = response.read()
            if len(data) != size or hashlib.sha256(data).hexdigest() != expected:
                raise ValueError(f"evidencia corrupta al descargar {url}")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(target.suffix + ".parcial")
            temporary.write_bytes(data)
            temporary.replace(target)
            return
        except (OSError, ValueError) as error:
            last = error
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"no se pudo descargar la evidencia: {last}")


def asegurar(*relatives: str) -> list[Path]:
    """Garantiza cada archivo de evidencia en su ruta original o omite el módulo."""
    index = _index()
    paths: list[Path] = []
    for relative in relatives:
        path = ROOT / relative
        item = index.get(relative)
        if path.is_file() and (item is None or _sha256(path) == item["sha256"]):
            paths.append(path)
            continue
        if item is None:
            pytest.skip(f"evidencia ausente y no archivada: {relative}", allow_module_level=True)
        if os.environ.get(ENV) != "descargar":
            pytest.skip(f"evidencia archivada no descargada: {relative} ({ENV}=descargar para bajarla)",
                        allow_module_level=True)
        url = f"https://github.com/{REPOSITORY}/releases/download/{item['release']}/{item['asset']}"
        _download(url, path, str(item["sha256"]), int(item["bytes"]))
        paths.append(path)
    return paths
