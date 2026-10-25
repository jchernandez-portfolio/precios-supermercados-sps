from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "subir_fotos_r2", Path(__file__).resolve().parents[1] / "scripts" / "subir_fotos_r2.py"
)
r2 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = r2
SPEC.loader.exec_module(r2)

WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 64
HASH = "0123456789"


def artifact(tmp_path: Path, items=None, files=None) -> Path:
    root = tmp_path / "artifact"
    root.mkdir(parents=True)
    index = {"schema": r2.INDEX_SCHEMA, "with_photo": 1,
             "items": items if items is not None else {"aa11": {"sig": "x", "h": HASH, "sm": "colonial"}}}
    (root / "index.json").write_text(json.dumps(index), encoding="utf-8")
    (root / "revision.html").write_text("<!doctype html>", encoding="utf-8")
    for relative, data in (files if files is not None else {
        f"800/aa/aa11-{HASH}.webp": WEBP, f"320/aa/aa11-{HASH}.webp": WEBP,
    }).items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


class Client:
    def __init__(self):
        self.calls = []

    def put_object(self, **kwargs):
        self.calls.append(kwargs)


def test_valid_artifact_uploads_photos_first_and_index_last(tmp_path: Path) -> None:
    root = artifact(tmp_path)
    index, uploads = r2.validate(root)
    assert [key for _, key in uploads] == [f"fotos/v2/320/aa/aa11-{HASH}.webp", f"fotos/v2/800/aa/aa11-{HASH}.webp"]
    client = Client()
    assert r2.upload(client, root, uploads, run_id="123", log=lambda _: None) == 2
    keys = [call["Key"] for call in client.calls]
    assert keys[-1] == "fotos/v2/index.json"
    assert "fotos/v2/revision/123.html" in keys and "fotos/v2/revision/latest.html" in keys
    photo = client.calls[0]
    assert photo["Bucket"] == "super-compras-fotos" and photo["ContentType"] == "image/webp"
    assert photo["CacheControl"] == "public, max-age=31536000, immutable"
    assert client.calls[-1]["CacheControl"] == "public, max-age=300"


@pytest.mark.parametrize("files, items, code", [
    ({"800/aa/aa11-0123456789.webp": b"not webp"}, None, "file_invalid"),
    ({"800/aa/aa11-0123456789.webp": WEBP + b"\x00" * 300_000}, None, "file_invalid"),
    ({"aa11-0123456789.webp": WEBP}, None, "path_invalid"),
    ({"800/bb/aa11-0123456789.webp": WEBP}, None, "path_invalid"),
    ({"800/aa/aa11-0123456789.png": WEBP}, None, "path_invalid"),
    ({"800/aa/aa11-0123456789.webp": WEBP}, {"aa11": {"h": "ffffffffff"}}, "unindexed"),
    ({"extra.txt": b"x"}, None, "path_invalid"),
])
def test_invalid_artifacts_are_rejected_before_any_upload(tmp_path: Path, files, items, code) -> None:
    root = artifact(tmp_path, items=items, files=files)
    with pytest.raises(r2.UploadError, match=code):
        r2.validate(root)


def test_index_schema_and_symlinks_are_checked(tmp_path: Path) -> None:
    root = artifact(tmp_path)
    (root / "index.json").write_text(json.dumps({"schema": "x", "items": {}}), encoding="utf-8")
    with pytest.raises(r2.UploadError, match="index_schema_invalid"):
        r2.validate(root)
    other = artifact(tmp_path / "b")
    (other / "320" / "aa" / f"link-{HASH}.webp").symlink_to(other / "index.json")
    with pytest.raises(r2.UploadError, match="symlink"):
        r2.validate(other)


def test_credentials_are_required(monkeypatch) -> None:
    pytest.importorskip("boto3")
    for name in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(r2.UploadError, match="r2_credentials_missing"):
        r2.r2_client()


def test_dry_run_only_validates(tmp_path: Path, capsys) -> None:
    root = artifact(tmp_path)
    assert r2.main([str(root), "--dry-run", "--run-id", "42"]) == 0
    assert json.loads(capsys.readouterr().out) == {"photos": 2, "indexed": 1, "with_photo": 1, "dry_run": True}
    with pytest.raises(r2.UploadError, match="run_id_invalid"):
        r2.main([str(root), "--dry-run", "--run-id", "../x"])
