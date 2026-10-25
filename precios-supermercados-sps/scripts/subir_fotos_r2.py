#!/usr/bin/env python3
"""Valida las fotos de una corrida y las sube al bucket R2 de Súper Compras.

Corre en un workflow aparte que sólo tiene la llave de escritura del bucket (sin
Turso y sin permiso de escritura en el repositorio). Antes de subir nada revisa
el artifact completo:

* sólo rutas ``800|320/<id[:2]>/<id>-<huella>.webp``, ``index.json`` y
  ``revision.html``; nada de enlaces simbólicos ni archivos extra;
* cada WebP tiene cabecera válida, tamaño acotado y está en el índice con la
  misma huella;
* el índice tiene el esquema esperado.

Sube primero las fotos (nombres inmutables, caché de un año) y al final el
índice: el catálogo nunca apunta a una foto que todavía no existe.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Callable, Iterable, Mapping

INDEX_SCHEMA = "super-compras-product-images/v2"
PREFIX = "fotos/v2/"
BUCKET = "super-compras-fotos"
MAX_BYTES = {800: 250_000, 320: 80_000}
PATH_PATTERN = re.compile(r"(800|320)/([0-9a-z]{2})/([0-9a-z_-]+)-([0-9a-f]{10})\.webp")
IMMUTABLE = "public, max-age=31536000, immutable"
SHORT = "public, max-age=300"


class UploadError(RuntimeError):
    """El artifact no cumple el contrato; no se sube nada."""


def validate(root: Path) -> tuple[dict[str, object], list[tuple[Path, str]]]:
    """Devuelve el índice y la lista (archivo, llave) de fotos a subir."""
    index_path = root / "index.json"
    if not index_path.is_file() or index_path.is_symlink():
        raise UploadError("index_missing")
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise UploadError("index_unreadable") from error
    if not isinstance(index, dict) or index.get("schema") != INDEX_SCHEMA or not isinstance(index.get("items"), dict):
        raise UploadError("index_schema_invalid")
    items: Mapping[str, object] = index["items"]
    uploads: list[tuple[Path, str]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise UploadError(f"symlink:{relative}")
        if path.is_dir() or relative in {"index.json", "revision.html"}:
            continue
        match = PATH_PATTERN.fullmatch(relative)
        if not match:
            raise UploadError(f"path_invalid:{relative}")
        size, folder, key, digest = int(match.group(1)), match.group(2), match.group(3), match.group(4)
        if not key.startswith(folder):
            raise UploadError(f"path_invalid:{relative}")
        data = path.read_bytes()
        if not (data[:4] == b"RIFF" and data[8:12] == b"WEBP") or len(data) > MAX_BYTES[size]:
            raise UploadError(f"file_invalid:{relative}")
        item = items.get(key)
        if not isinstance(item, dict) or item.get("h") != digest:
            raise UploadError(f"unindexed:{relative}")
        uploads.append((path, PREFIX + relative))
    return index, uploads


def r2_client():
    import boto3
    from botocore.config import Config

    account = os.environ.get("R2_ACCOUNT_ID", "").strip()
    key_id = os.environ.get("R2_ACCESS_KEY_ID", "").strip()
    secret = os.environ.get("R2_SECRET_ACCESS_KEY", "").strip()
    if not re.fullmatch(r"[0-9a-f]{32}", account) or not key_id or not secret:
        raise UploadError("r2_credentials_missing")
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
        config=Config(region_name="auto", signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"},
                      request_checksum_calculation="when_required", response_checksum_validation="when_required"),
    )


def upload(client, root: Path, uploads: Iterable[tuple[Path, str]], *, run_id: str,
           log: Callable[[str], None] = print) -> int:
    count = 0
    for path, key in uploads:
        client.put_object(Bucket=BUCKET, Key=key, Body=path.read_bytes(), ContentType="image/webp",
                          CacheControl=IMMUTABLE)
        count += 1
        if count % 500 == 0:
            log(f"subidas {count} fotos")
    review = root / "revision.html"
    if review.is_file():
        body = review.read_bytes()
        for key in (f"{PREFIX}revision/{run_id}.html", f"{PREFIX}revision/latest.html"):
            client.put_object(Bucket=BUCKET, Key=key, Body=body, ContentType="text/html; charset=utf-8",
                              CacheControl="no-cache")
    client.put_object(Bucket=BUCKET, Key=f"{PREFIX}index.json", Body=(root / "index.json").read_bytes(),
                      ContentType="application/json", CacheControl=SHORT)
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("directory", type=Path)
    parser.add_argument("--run-id", default="manual")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[0-9A-Za-z_-]{1,40}", args.run_id):
        raise UploadError("run_id_invalid")
    index, uploads = validate(args.directory)
    summary = {"photos": len(uploads), "indexed": len(index["items"]), "with_photo": index.get("with_photo")}
    if args.dry_run:
        print(json.dumps({**summary, "dry_run": True}))
        return 0
    uploaded = upload(r2_client(), args.directory, uploads, run_id=args.run_id)
    print(json.dumps({**summary, "uploaded": uploaded}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UploadError as error:
        print(f"r2_upload_rejected:{error}", file=sys.stderr)
        raise SystemExit(1)
