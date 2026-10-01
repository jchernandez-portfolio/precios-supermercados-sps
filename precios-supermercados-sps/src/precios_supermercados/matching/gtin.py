"""GTIN como llave primaria (GS1) y detección de prefijos de circulación restringida.

Un GTIN válido y *no restringido* compartido entre cadenas es la evidencia más
fuerte de identidad comercial. Los prefijos de circulación restringida GS1
(peso variable/uso interno: 02x, 04x, 20–29; EAN-8 0xx/2xx; cupones 98x–99x)
se reutilizan entre tiendas para artículos distintos, así que nunca cuentan
como identidad ni como etiqueta silver.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..identifiers import canonicalize_gtin


@dataclass(frozen=True, slots=True)
class GtinInfo:
    gtin14: str
    kind: str
    restricted: bool


def classify_gtin(value: str | None) -> GtinInfo | None:
    """Clasifica un barcode fuente. Devuelve ``None`` si no supera el check digit."""

    gtin14 = canonicalize_gtin(value)
    if gtin14 is None:
        return None
    if gtin14[0] != "0":
        return GtinInfo(gtin14, "gtin14", False)
    ean13 = gtin14[1:]
    if ean13.startswith("00000"):
        ean8 = ean13[5:]
        return GtinInfo(gtin14, "ean8", ean8[0] in {"0", "2"})
    prefix = int(ean13[:3])
    if ean13.startswith("0"):
        kind = "upc_a"
    else:
        kind = "ean13"
    restricted = (
        20 <= prefix <= 29
        or 40 <= prefix <= 49
        or 200 <= prefix <= 299
        or 980 <= prefix <= 999
    )
    return GtinInfo(gtin14, kind, restricted)


def identity_gtin(value: str | None) -> str | None:
    """GTIN-14 utilizable como llave de identidad (válido y no restringido)."""

    info = classify_gtin(value)
    if info is None or info.restricted:
        return None
    return info.gtin14


def gtin_from_product_id(product_id: str | None) -> str | None:
    if not product_id or not product_id.startswith("prod_gtin_"):
        return None
    return canonicalize_gtin(product_id.removeprefix("prod_gtin_"))
