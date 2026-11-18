#!/usr/bin/env python3
"""Ficha de producto por GTIN con Claude y búsqueda web en vivo (prueba; no guarda decisiones).

Para cada GTIN pedido, Claude (SDK ``anthropic``, herramienta ``web_search``) busca en internet
**hoy** la ficha del producto y devuelve, con fuentes citadas:

    marca, fabricante, nombre_estandar (Marca + Producto + Variante + Contenido), variante,
    medida_oficial (valor + unidad), unidades, contenido_por_unidad, descripcion propia,
    categoria, url de la fuente y la frase citada.

Sólo se le dan marca, nombre y GTIN (no los tamaños de los súper, para no sesgarlo) y se le pide
ignorar los súper de Honduras (dato circular). Se guardan las páginas que devolvió la búsqueda
con su antigüedad (``page_age``) y las citas textuales, para revisar de dónde salió cada dato.
**No escribe en Turso**: el resultado es ``fichas-prueba.json`` (rama pública ``revision-tamanos``).

    python scripts/fichas_producto_web.py --queue /tmp/cola/cola.json \
        --gtins 00795893419311,00748757001566 --output /tmp/cola/fichas-prueba.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from buscar_tamanos_web import (  # noqa: E402
    DEFAULT_MODEL,
    ClaudeWebSearch,
    StopRun,
    _number_cited,
    clean_url,
    final_json,
    is_honduran_store,
    source_host,
)

SCHEMA = "rpi-product-sheet-test/v1"
MAX_GTINS = 50
MAX_USES = 5
MAX_TOKENS = 2500
DELAY = 2.0
_GTIN = re.compile(r"\d{8,14}")
_MODEL = re.compile(r"claude-[a-z0-9-]{1,60}")
FIELDS = ("marca", "fabricante", "nombre_estandar", "variante", "medida_valor", "medida_unidad", "unidades",
          "contenido_por_unidad", "descripcion", "categoria", "url", "texto_citado", "confianza")
PROMPT = """Eres un catalogador de productos de consumo (estilo GS1). Investiga en internet HOY el producto \
con código de barras GTIN {gtin} (marca según el súper: "{brand}"; nombre según el súper: "{name}").

Busca primero el GTIN exacto; luego la página del fabricante, GS1, Open Food Facts y tiendas de \
Centroamérica, México o EE. UU. Ignora los supermercados de Honduras (Walmart Honduras, Paiz Honduras, \
La Colonia, Colonial, Los Andes, PriceSmart Honduras, Maxi Despensa Honduras): su dato es el que estamos revisando.

Reglas:
- No copies el nombre del súper: arma nombre_estandar como «Marca Producto Variante Contenido» \
(ej. «Diana Elotitos Limón 157 g»), con mayúsculas normales.
- medida = contenido neto TOTAL del empaque tal como lo dice la fuente (Peso neto / Net Wt / Contenido neto).
- unidades = cuántas piezas trae el empaque (1 si es una sola); contenido_por_unidad si aplica (ej. «13 g»).
- descripcion: 1 o 2 frases propias, objetivas, sin marketing (qué es, presentación, sabor o variante).
- Si las fuentes se contradicen o no encuentras este producto exacto, dilo y usa confianza "baja".

Explica en 2 o 3 frases citando la fuente (con el número tal como lo escribe) y termina con UNA línea con este JSON:
{{"encontrado": true, "marca": "Diana", "fabricante": "Productos Alimenticios Diana", \
"nombre_estandar": "Diana Elotitos Limón 157 g", "variante": "Limón", "medida_valor": 157, \
"medida_unidad": "g", "unidades": 1, "contenido_por_unidad": null, \
"descripcion": "Boquitas de maíz con sabor a elote y limón en bolsa de 157 g.", \
"categoria": "Snacks > Boquitas de maíz", "url": "https://...", "texto_citado": "Elotito Con Limon Diana 157 g", \
"confianza": "alta"}}
medida_unidad es una de: g, kg, ml, l, oz, fl_oz, lb, unidades. confianza es alta, media o baja."""


def gtin14(code: str) -> str:
    return code.strip().zfill(14)


def queue_index(path: Path | None) -> dict[str, Mapping[str, object]]:
    if path is None or not path.is_file() or path.is_symlink():
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    found: dict[str, Mapping[str, object]] = {}
    for key in ("items", "auto_resueltas"):
        for item in document.get(key) or ():
            if isinstance(item, Mapping) and isinstance(item.get("gtin"), str):
                found.setdefault(item["gtin"], item)
    return found


def _clean(value: object, limit: int = 160) -> str | None:
    return " ".join(str(value).split())[:limit] if isinstance(value, (str, int, float)) and str(value).strip() else None


def sheet_from(read: Mapping[str, object]) -> dict[str, object]:
    """Respuesta de Claude (no confiable) → ficha con sus fuentes y verificación de la cita."""
    raw = final_json(str(read.get("text") or ""))
    results = list(read.get("results") or [])
    citations = list(read.get("citations") or [])
    independent = [c for c in citations if not is_honduran_store(source_host(c))]
    sheet: dict[str, object] = {"encontrado": False}
    if isinstance(raw, Mapping):
        sheet = {"encontrado": raw.get("encontrado") is True}
        for field in FIELDS:
            sheet[field] = _clean(raw.get(field), 300 if field == "descripcion" else 160)
        sheet["url"] = clean_url(raw.get("url"))
    known = {page["url"] for page in results} | {c["url"] for c in citations}
    sheet["url_de_busqueda"] = bool(sheet.get("url")) and sheet["url"] in known
    sheet["fuente_hondurena"] = bool(sheet.get("url")) and is_honduran_store(source_host({"url": sheet["url"]}))
    sheet["cita_con_numero"] = _number_cited(sheet.get("medida_valor"), independent)
    sheet["citas"] = [{"url": c["url"], "texto": c["cited_text"]} for c in citations][:8]
    sheet["paginas"] = results[:8]
    sheet["busquedas"] = int(read.get("searches") or 0)
    sheet["explicacion"] = _clean(str(read.get("text") or "").split('{"encontrado"')[0], 600)
    return sheet


def run(gtins: Sequence[str], index: Mapping[str, Mapping[str, object]], client: ClaudeWebSearch, *,
        delay: float = DELAY, sleep=time.sleep) -> tuple[list[dict[str, object]], str | None]:
    sheets: list[dict[str, object]] = []
    for position, gtin in enumerate(gtins):
        item = index.get(gtin, {"gtin": gtin})
        prompt = PROMPT.format(gtin=gtin.lstrip("0").rjust(12, "0"),
                               brand=_clean(item.get("brand"), 60) or "",
                               name=(_clean(item.get("name"), 140) or "").replace('"', "'"))
        if position:
            sleep(delay)
        try:
            read = client.search(item, prompt=prompt, max_uses=MAX_USES, max_tokens=MAX_TOKENS)
        except StopRun as error:
            return sheets, str(error)
        except Exception as error:  # noqa: BLE001 - un error no tumba la prueba
            sheets.append({"gtin": gtin, "error": type(error).__name__})
            continue
        sheets.append({"gtin": gtin, "nombre_super": item.get("name"), "marca_super": item.get("brand"),
                       "tamanos_super": [o.get("size") for o in item.get("offers") or ()], **sheet_from(read)})
    return sheets, None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--queue", type=Path, help="cola.json para tomar marca y nombre del súper")
    parser.add_argument("--gtins", required=True, help="GTIN separados por coma (máx. 50)")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default=os.environ.get("CLAUDE_MODEL", "").strip() or DEFAULT_MODEL)
    args = parser.parse_args(argv)
    codes = [code.strip() for code in args.gtins.split(",") if code.strip()]
    if not codes or len(codes) > MAX_GTINS or not all(_GTIN.fullmatch(code) for code in codes) \
            or not _MODEL.fullmatch(args.model):
        raise SystemExit("parametros_invalidos")
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise SystemExit("sin_llave")
    client = ClaudeWebSearch(key, model=args.model)
    sheets, stop = run([gtin14(code) for code in codes], queue_index(args.queue), client)
    document = {"schema": SCHEMA, "generated_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "modelo": client.model, "detenido_por": stop, "uso": client.usage,
                "ultimo_error_api": client.last_error, "fichas": sheets}
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"fichas": len(sheets), "detenido_por": stop, "uso": client.usage}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
