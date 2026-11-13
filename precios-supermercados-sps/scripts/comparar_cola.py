#!/usr/bin/env python3
"""¿La cola nueva cambia algo más que su fecha? (para no publicar commits vacíos).

Compara ``cola.json`` publicada con la recién calculada ignorando sólo las marcas de
tiempo de la corrida (``generated_utc`` y ``ia.actualizado_utc``). Sale con 0 si hay
un cambio relevante (códigos, ofertas, lecturas, resumen de la IA o historial) y con
1 si sólo cambiaron las fechas. Si la publicada no existe o no se puede leer, cuenta
como cambio.

    python scripts/comparar_cola.py --anterior cola/cola.json --nueva /tmp/cola/cola.json
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

RUN_STAMPS = ("generated_utc",)
IA_STAMPS = ("actualizado_utc",)


def _relevant(document: object) -> object:
    if not isinstance(document, dict):
        return document
    clean = {key: value for key, value in document.items() if key not in RUN_STAMPS}
    ia = clean.get("ia")
    if isinstance(ia, dict):
        clean["ia"] = {key: value for key, value in ia.items() if key not in IA_STAMPS}
    return clean


def changed(previous: Path, current: Path) -> bool:
    if not previous.is_file() or previous.is_symlink():
        return True
    try:
        before = json.loads(previous.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    after = json.loads(current.read_text(encoding="utf-8"))
    return _relevant(before) != _relevant(after)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--anterior", type=Path, required=True)
    parser.add_argument("--nueva", type=Path, required=True)
    args = parser.parse_args(argv)
    relevant = changed(args.anterior, args.nueva)
    print("cola_cambio_relevante=" + ("true" if relevant else "false"))
    return 0 if relevant else 1


if __name__ == "__main__":
    raise SystemExit(main())
