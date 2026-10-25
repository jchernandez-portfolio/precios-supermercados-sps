from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import _evidencia as ev

ROOT = Path(__file__).resolve().parents[1]


def test_index_matches_present_files_or_is_downloadable() -> None:
    index = ev._index()
    assert index, "índice vacío"
    for relative, item in index.items():
        path = ROOT / relative
        assert item["asset"] == relative[len("reports/"):].replace("/", "__")
        if path.is_file():
            assert path.stat().st_size == item["bytes"], relative
            assert ev._sha256(path) == item["sha256"], relative


def test_asegurar_skips_without_download_and_verifies_sha(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ev, "ROOT", tmp_path)
    monkeypatch.setattr(ev, "INDEX", tmp_path / "reports" / "evidencia-archivada.json")
    data = b"evidencia" * 100
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "evidencia-archivada.json").write_text(json.dumps({
        "schema": "precios-sps-evidencia-archivada/v1", "release": "evidencia-prueba",
        "files": [{"path": "reports/x/raw.tar.gz", "asset": "x__raw.tar.gz", "bytes": len(data),
                   "sha256": hashlib.sha256(data).hexdigest()}],
    }), encoding="utf-8")
    monkeypatch.delenv(ev.ENV, raising=False)
    with pytest.raises(pytest.skip.Exception):
        ev.asegurar("reports/x/raw.tar.gz")
    with pytest.raises(pytest.skip.Exception):
        ev.asegurar("reports/otro.bin")

    served: dict[str, bytes] = {"evidencia-prueba/x__raw.tar.gz": data}

    class Response:
        def __init__(self, body): self.body = body
        def read(self): return self.body
        def __enter__(self): return self
        def __exit__(self, *args): return False

    def fake_urlopen(url, timeout=0):
        key = url.split("/releases/download/")[1]
        return Response(served[key])

    monkeypatch.setattr(ev.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(ev.time, "sleep", lambda _: None)
    monkeypatch.setenv(ev.ENV, "descargar")
    [path] = ev.asegurar("reports/x/raw.tar.gz")
    assert path == tmp_path / "reports/x/raw.tar.gz" and path.read_bytes() == data
    # Un archivo presente pero alterado se vuelve a bajar; uno corrupto en el release falla.
    path.write_bytes(b"alterado")
    assert ev.asegurar("reports/x/raw.tar.gz")[0].read_bytes() == data
    served["evidencia-prueba/x__raw.tar.gz"] = b"otra cosa"
    path.unlink()
    with pytest.raises(RuntimeError, match="no se pudo descargar"):
        ev.asegurar("reports/x/raw.tar.gz")
