"""Política GS1 de números de circulación restringida (RCN) para identidad.

Un GTIN válido por check digit no siempre es globalmente único. GS1 reserva
rangos para uso interno de una empresa o de una tienda (peso variable, PLU,
cupones). Dos cadenas distintas pueden asignar el mismo número a productos
distintos, así que esos códigos sólo sostienen identidad ``prod_gtin_*`` entre
cadenas que comparten maestro de productos.

Maestros compartidos demostrados:

- ``walmart_cam``: Walmart y Paiz publican el mismo catálogo VTEX de Walmart
  Centroamérica (sellers ``walmarthnwm*`` / ``walmarthnsp*``); en las capturas
  2026-08-31/2026-09-04, 7,651 de 8,868 productos Paiz Multiplaza comparten
  ``item_id`` y ``ean`` con Walmart SPS.
- La Colonia SPS y TGU son contextos de un mismo ``supermarket_id``: su
  producto fuente ya es único por cadena y nunca forma grupo consigo mismo.

La copia declarativa vive en ``config/homologation/identity-policy-v1.yaml`` y un
test exige que ambas coincidan.
"""
from __future__ import annotations

from typing import Iterable

from .identifiers import canonicalize_gtin

RESTRICTED_GTIN_POLICY_VERSION = "gs1-restricted-circulation-v1"

# Rangos sobre la vista GTIN-13 (14 dígitos sin el indicador inicial).
RESTRICTED_GTIN13_PREFIX_RANGES: tuple[tuple[int, int, str], ...] = (
    (20, 29, "rcn_upc_variable_measure"),  # 020-029 = UPC-A que empieza con 2
    (40, 49, "rcn_upc_company_internal"),  # 040-049 = UPC-A que empieza con 4
    (200, 299, "rcn_in_store"),  # EAN-13 200-299: peso variable / uso en tienda
    (980, 999, "coupon_or_refund"),  # 980 recibos de devolución, 981-999 cupones
)
RESTRICTED_GTIN8_FIRST_DIGITS = frozenset({"0", "2"})  # RCN-8
RESTRICTED_GTIN14_INDICATORS = frozenset({"9"})  # unidad comercial de medida variable

SHARED_PRODUCT_MASTERS: dict[str, frozenset[str]] = {
    "walmart_cam": frozenset({"walmart", "paiz"}),
}


def restricted_circulation_reason(gtin: str | None) -> str | None:
    """Devuelve la clase RCN de un GTIN válido o ``None`` si es global.

    Acepta cualquier representación válida (8/12/13/14 dígitos). Un valor
    inválido devuelve ``None`` porque nunca llega a ser identidad canónica.
    """

    canonical = canonicalize_gtin(gtin)
    if canonical is None:
        return None
    if canonical[0] in RESTRICTED_GTIN14_INDICATORS:
        return "variable_measure_gtin14"
    gtin13 = canonical[1:]
    if gtin13.startswith("00000"):
        gtin8 = gtin13[5:]
        return "rcn8" if gtin8[0] in RESTRICTED_GTIN8_FIRST_DIGITS else None
    prefix3 = int(gtin13[:3])
    for low, high, reason in RESTRICTED_GTIN13_PREFIX_RANGES:
        if low <= prefix3 <= high:
            return reason
    return None


def product_master_of(supermarket_id: str) -> str:
    """Maestro de productos de una cadena; por defecto la cadena misma."""

    for master, members in sorted(SHARED_PRODUCT_MASTERS.items()):
        if supermarket_id in members:
            return master
    return f"retailer:{supermarket_id}"


def restricted_gtin_master_partition(
    supermarket_ids: Iterable[str],
) -> tuple[frozenset[str] | None, frozenset[str]]:
    """Separa cadenas que pueden compartir un GTIN restringido.

    Devuelve ``(permitidas, excluidas)``. ``permitidas`` es el único maestro
    compartido con al menos dos cadenas presentes; si no existe exactamente uno,
    devuelve ``None`` y todas las cadenas quedan excluidas (fail-closed).
    """

    present = frozenset(supermarket_ids)
    by_master: dict[str, set[str]] = {}
    for supermarket_id in present:
        by_master.setdefault(product_master_of(supermarket_id), set()).add(supermarket_id)
    eligible = [frozenset(members) for members in by_master.values() if len(members) >= 2]
    if len(eligible) != 1:
        return None, present
    allowed = eligible[0]
    return allowed, present - allowed


def restricted_gtin_shared_master_ok(gtin: str | None, supermarket_ids: Iterable[str]) -> bool:
    """``True`` si el GTIN es global o todas las cadenas comparten maestro."""

    if restricted_circulation_reason(gtin) is None:
        return True
    present = frozenset(supermarket_ids)
    allowed, excluded = restricted_gtin_master_partition(present)
    return allowed is not None and not excluded
