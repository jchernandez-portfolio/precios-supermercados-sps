#!/usr/bin/env python3
"""Pone la misma versión en todos los archivos de Súper Compras.

La app lleva la versión en cuatro archivos (``index.html``, ``app.js``, ``sw.js`` y
las importaciones entre módulos) para que el navegador y el service worker
renueven la caché a la vez. Este script la cambia en todos de una sola vez:

    python scripts/versionar_super_compras.py --set 20261025-2
    python scripts/versionar_super_compras.py --auto      # fecha de hoy + número
    python scripts/versionar_super_compras.py --stamp abc1234   # fecha + commit (despliegue)
    python scripts/versionar_super_compras.py --check     # sólo verifica

El despliegue a GitHub Pages usa ``--stamp`` con el commit publicado, así nadie
tiene que acordarse de subir la versión a mano.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "super-compras"
FILES = ("index.html", "app.js", "data.js", "sw.js", "illustration.js")
VERSION = re.compile(r"\d{8}-[0-9a-z]+")
TOKEN = re.compile(r"(\?v=|super-compras-)(\d{8}-[0-9a-z]+)")


class VersionError(ValueError):
    """Las versiones no coinciden o el valor pedido no es válido."""


def current_versions(app_dir: Path = APP_DIR) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for name in FILES:
        path = app_dir / name
        if path.is_file():
            found[name] = {match.group(2) for match in TOKEN.finditer(path.read_text(encoding="utf-8"))}
    return found


def check(app_dir: Path = APP_DIR) -> str:
    """Devuelve la versión única o falla si hay más de una o ninguna."""
    versions = {version for found in current_versions(app_dir).values() for version in found}
    if len(versions) != 1:
        raise VersionError(f"versiones distintas: {sorted(versions) or 'ninguna'}")
    return versions.pop()


def apply(version: str, app_dir: Path = APP_DIR) -> list[str]:
    if not VERSION.fullmatch(version):
        raise VersionError(f"versión inválida: {version!r} (formato AAAAMMDD-n)")
    changed = []
    for name in FILES:
        path = app_dir / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        updated = TOKEN.sub(lambda match: f"{match.group(1)}{version}", text)
        if updated != text:
            path.write_text(updated, encoding="utf-8")
            changed.append(name)
    return changed


def next_version(today: date, previous: str | None) -> str:
    stamp = today.strftime("%Y%m%d")
    if previous and previous.startswith(stamp + "-") and previous.split("-", 1)[1].isdigit():
        return f"{stamp}-{int(previous.split('-', 1)[1]) + 1}"
    return f"{stamp}-1"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--set", dest="version")
    group.add_argument("--auto", action="store_true")
    group.add_argument("--stamp", metavar="COMMIT")
    group.add_argument("--check", action="store_true")
    parser.add_argument("--app-dir", type=Path, default=APP_DIR)
    args = parser.parse_args(argv)
    try:
        if args.check:
            print(check(args.app_dir))
            return 0
        if args.auto:
            try:
                previous = check(args.app_dir)
            except VersionError:
                previous = None
            version = next_version(date.today(), previous)
        elif args.stamp:
            commit = args.stamp.strip().lower()
            if not re.fullmatch(r"[0-9a-f]{7,40}", commit):
                raise VersionError("commit inválido")
            version = f"{date.today().strftime('%Y%m%d')}-{commit[:7]}"
        else:
            version = args.version
        changed = apply(version, args.app_dir)
        print(f"{version} ({', '.join(changed) or 'sin cambios'})")
        return 0
    except VersionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
