#!/usr/bin/env python3
"""Exporta catálogo B2C con normalización pública de marca y presentación.

El núcleo v3 se conserva en ``exportar_consumer_catalog_core.py``. Esta fachada
mantiene su API (incluidos los hooks usados por TGU) y aplica únicamente una
normalización de lectura antes de escribir archivos públicos.
"""
from __future__ import annotations

import json
import math
import re
import sys
import unicodedata
from collections.abc import Iterable, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import exportar_consumer_catalog_core as _core  # noqa: E402
from precios_supermercados import master_taxonomy  # noqa: E402
from precios_supermercados.consumer_catalog_display import (  # noqa: E402
    brand_from_prefix,
    canonical_brand,
    canonical_egg_size,
    canonical_presentation,
    multipack,
)
from precios_supermercados.gtin_policy import restricted_circulation_reason  # noqa: E402
from precios_supermercados.identifiers import canonicalize_gtin  # noqa: E402
from precios_supermercados.matching import size_variants  # noqa: E402

# Conserva la API histórica del script, incluidos helpers privados consumidos por
# el exportador TGU y por las pruebas del contrato v3.
for _name in dir(_core):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_core, _name)

_ORIGINAL_BUILD_ROWS = _core.build_rows
_ORIGINAL_EXPORT = _core.export_consumer_catalog
_ORIGINAL_MAIN = _core.main
# Tope de "otras presentaciones" por fila (las de tamaño más cercano).
MAX_OTHER_PRESENTATIONS = 12
# Onza de peso (avoirdupois) en gramos.
GRAMS_PER_OUNCE = Decimal("28.349523125")
# Subcategorías sin atributo ``ounce`` en el árbol cuyos productos se venden por
# peso: el huevo líquido (Egg Beaters) declara "NET WT 16 OZ (1 LB) 454 g".
EXPORT_OUNCE_MASS_SUBCATEGORIES = frozenset({"Huevos"})
# Productos pesados en tienda (código de barras de medida variable "2…"): el súper
# publica el precio por libra aunque el nombre diga el peso del paquete
# ("Pollo Entero Norteño ... - 5 lb" a L 42.00). Se llena en export_consumer_catalog.
WEIGHED_BY_POUND: set[str] = set()
WEIGHED_TYPE_WORDS = frozenset({
    "res", "cerdo", "pollo", "pavo", "carne", "carnes", "pescado", "pescados", "mariscos", "embutidos",
    "queso", "quesos", "jamon", "frutas", "verduras", "hierbas",
})
# Walmart, Paiz y La Colonia (VTEX) lo traen como ean; Colonial (Shopify) como sku =
# reference ("2063710000000" "NORTEÑO Pollo en Piezas 1/12" a L 46.99 la libra); Los
# Andes en su código ("0001-000028436000000" = PLU 2843600000 "carne para bistec").
_VARIABLE_CODE = "((length({c})=13 AND substr({c},1,1)='2') OR (length({c})=14 AND substr({c},1,2)='02'))"
_ANDES_BODY = "ltrim(substr(reference,6),'0')"
WEIGHED_SQL = (
    "SELECT supermarket_id, product_id FROM products WHERE (supermarket_id IN ('walmart','paiz','la_colonia','colonial') AND ("
    + _VARIABLE_CODE.format(c="ean") + " OR " + _VARIABLE_CODE.format(c="reference") + "))"
    " OR (supermarket_id='comisariato_los_andes' AND substr(reference,1,5)='0001-'"
    f" AND length({_ANDES_BODY})=11 AND substr({_ANDES_BODY},1,1)='2')"
)
# Los Andes publica la unidad de venta (unitMeasureCode): "LB" es precio por libra
# aunque el nombre no lo diga ("Alitas fritas en salsa barbacoa" a L 179).
SOURCE_POUND: set[str] = set()
SOURCE_POUND_SQL = (
    "SELECT supermarket_id, product_id FROM products WHERE supermarket_id='comisariato_los_andes'"
    " AND upper(trim(presentation)) IN ('LB','LBS','LIBRA')"
)
# El súper a veces aclara lo contrario en el nombre ("Ayote Sazon ... - Precio Por Unidad").
# (o "COLONIAL Encurtido Rojo und": termina en unidad).
# Un paquete cerrado sin libra en el nombre ("MH ShortRib AsadoTira CoreanoPaq" a L 719.99)
# o una bandeja con N unidades ("Torta p/hamburguesa bandeja 4und") también va por unidad.
_PER_UNIT_NAME = re.compile(
    r"precio\s+por\s+unidad|\s(?:und|unds|unid|unidad|un|c/u)\.?\s*$|paq(?:uete)?\.?\s*$|\d\s*(?:und|unds|unidades)\b",
    re.IGNORECASE,
)
_AEROSOL_RE = re.compile(r"(?<![a-z])(?:spray|aerosol|atomizador)(?![a-z])", re.IGNORECASE)


def _fold_public(value: object) -> str:
    """Normaliza texto sólo para reglas públicas explícitas y auditables."""
    if not isinstance(value, str):
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_text = "".join(char for char in decomposed if not unicodedata.combining(char))
    normalized = re.sub(r"[^0-9a-zA-Z]+", " ", ascii_text).casefold()
    return " ".join(normalized.split())


def _normalize_public_taxonomy(row: dict[str, object]) -> None:
    """Completa taxonomía pública cuando el nombre aporta evidencia inequívoca.

    No toca identidad canónica ni comparabilidad. ``Egg Beaters`` es una marca
    explícita y suficientemente distintiva para evitar que sus productos terminen
    en ``Sin categoría`` cuando la fuente trae ``RMS`` y un nombre en inglés.
    """
    name = _fold_public(row.get("product_name"))
    if re.search(r"(?<!\w)egg\s+beaters(?!\w)", name):
        row["brand"] = "Egg Beaters"
        if not row.get("category"):
            row["category"] = "Alimentos"
        if not row.get("product_type"):
            row["product_type"] = "Huevo"


def _row_is_shoppable(row: dict[str, object]) -> bool:
    """Compra Inteligente sólo indexa filas con al menos una oferta comprable."""
    offers = row.get("offers")
    if not isinstance(offers, list):
        return False
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        if offer.get("availability") == "out_of_stock" or offer.get("freshness_status") == "UNAVAILABLE":
            continue
        try:
            price = Decimal(str(offer.get("current_price")))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if price.is_finite() and price > 0:
            return True
    return False


def _derived_presentation(offer: VisibleOffer) -> str | None:
    if offer.presentation_status == "rejected_implausible":
        return None
    return canonical_presentation(
        source_presentation=offer.presentation,
        product_name=offer.product_name,
        product_type=offer.product_type,
        presentation_dimension=offer.presentation_dimension,
        presentation_total_base=offer.presentation_total_base,
        presentation_status=offer.presentation_status,
    )


# Medidas del nombre que el motor lee mal (sólo cambian la lectura pública):
# "CloroRegular 1/2G" es medio galón, no medio gramo; "Badia albahaca .75 oz" son
# 0.75 oz, no 75; "Dramamine 50 mg caja 24 tabletas" es la dosis, no el contenido;
# "Fitamin ... 60 Gomas" con 181 kg publicados son 60 gomitas.
ML_PER_GALLON = Decimal("3785.411784")
_HALF_GALLON_RE = re.compile(r"(?<![\d.])1\s*/\s*2\s*(?:g|gl|gal|gal[oó]n)(?![a-z])|medio\s+gal[oó]n", re.IGNORECASE)
_LEADING_DOT_OZ_RE = re.compile(r"(?<![\d.,])\.(\d+)\s*(?:oz|onz|onzas?)(?![a-z])", re.IGNORECASE)
_DOSE_RE = re.compile(r"\d+(?:[.,]\d+)?\s*(?:mg|mcg|µg|ui)(?![a-z])", re.IGNORECASE)
_PIECES_RE = re.compile(
    r"(\d+)\s*(?:tabletas|tabs?|c[aá]psulas|caps|comprimidos|gomas|gomitas|sobres|softgels|grageas)(?![a-z])",
    re.IGNORECASE,
)
# Conteo explícito de piezas en el nombre ("Toallas Saba ... - 40 piezas") y medida de
# peso/volumen en el nombre ("4 pzas/490 ml"): si el nombre trae conteo y ninguna
# medida, un peso que sólo publica el campo del súper ("40 piezas" con 3 g, "Rasuradoras
# ... 15Pzas" con 415 g) contradice el nombre (ver _hide_source_size_against_name_count).
_NAME_COUNT_RE = re.compile(r"(\d+)\s*(pzas?|piezas|pcs|unidades|uds)(?![a-z])", re.IGNORECASE)
_NAME_MEASURE_RE = re.compile(
    r"\d\s*(?:-\s*)?(?:kgs?|kilos?|g|gr|grs|grms?|gramos?|mg|ml|mls|l|lt|lts|litros?|oz|onz|onzas?|lbs?|libras?|gal|gl)(?![a-z])",
    re.IGNORECASE,
)
# Más de 50 kg no es un producto de súper, salvo equipo de gimnasio o sacos.
MAX_PLAUSIBLE_GRAMS = Decimal("50000")
MAX_PLAUSIBLE_ML = Decimal("60000")
_HEAVY_OK_RE = re.compile(r"m[aá]quina|estaci[oó]n|banco|pesa|mancuerna|disco|saco|cemento|alimento para|concentrado", re.IGNORECASE)


def fix_name_measures(offer: VisibleOffer) -> VisibleOffer:
    """Corrige lecturas del nombre que dan tamaños imposibles (ver constantes arriba)."""
    name = offer.product_name or ""
    if _HALF_GALLON_RE.search(name):
        half = (ML_PER_GALLON / 2).quantize(Decimal("0.01"))
        return _core.replace(offer, presentation_dimension="volume_ml", presentation_total_base=format(half, "f"),
                             presentation_status="name_only")
    dot = _LEADING_DOT_OZ_RE.search(name)
    if dot:
        return _core.replace(offer, presentation_dimension="ounce", presentation_total_base="0." + dot.group(1),
                             presentation_status="name_only")
    if offer.presentation_dimension not in {"mass_g", "volume_ml"}:
        return offer
    try:
        total = Decimal(str(offer.presentation_total_base))
    except (InvalidOperation, TypeError, ValueError):
        return offer
    mass = offer.presentation_dimension == "mass_g"
    pieces = _PIECES_RE.search(name)
    has_dose = mass and _DOSE_RE.search(name) is not None
    # "Now Magnesio 180 Cápsulas / 400 mg" (72 g = 180 × 400 mg): con dosis y conteo
    # el contenido es el conteo, sea cual sea el peso que sale de multiplicarlos.
    dose = has_dose and (total < 5 or pieces is not None)
    limit = MAX_PLAUSIBLE_GRAMS if mass else MAX_PLAUSIBLE_ML
    huge = total > limit and not _HEAVY_OK_RE.search(name)
    if not dose and not huge:
        return _hide_source_size_against_name_count(offer, name)
    if pieces and int(pieces.group(1)) > 0:
        return _core.replace(offer, presentation_dimension="count", presentation_total_base=pieces.group(1),
                             presentation_status="name_only", presentation=None)
    if dose:
        # Dosis sin conteo en el nombre: mejor sin tamaño que un tamaño falso.
        return _core.replace(offer, presentation_dimension=None, presentation_total_base=None,
                             presentation_status="rejected_implausible", presentation=None)
    # Tamaño imposible sin conteo ("Sprite 1100 Lt"): se publica tal cual; la app lo
    # corrige con un patrón seguro o lo esconde, y la auditoría lo reporta.
    return offer


def _hide_source_size_against_name_count(offer: VisibleOffer, name: str) -> VisibleOffer:
    """Peso/volumen sólo del campo del súper que contradice un conteo del nombre.

    No reparte el peso ni lo adivina: "N unidades" del nombre es el contenido (GS1,
    conteo); "N piezas" se deja sin tamaño publicado (la app lo lee del nombre como
    piezas de un juego o unidades de comida, igual que cuando el súper no publica nada).
    """
    if offer.presentation_status != "source_only" or _NAME_MEASURE_RE.search(name):
        return offer
    counted = _NAME_COUNT_RE.search(name)
    if counted is None or int(counted.group(1)) < 2:
        return offer
    if counted.group(2).casefold() in {"unidades", "uds"}:
        return _core.replace(offer, presentation_dimension="count", presentation_total_base=counted.group(1),
                             presentation_status="name_only", presentation=None)
    return _core.replace(offer, presentation_dimension=None, presentation_total_base=None,
                         presentation_status="rejected_implausible", presentation=None)


def ounce_as_mass(offer: VisibleOffer) -> VisibleOffer:
    """Onza sin convertir → gramos cuando la subcategoría pública es de peso.

    El motor sólo convierte la onza cuando la categoría del súper lleva a una
    subcategoría ``ounce: mass``; la asignación pública usa además el nombre y
    la categoría, y suele llegar a ella (Colonial "EGG Beaters 16 oz" → Huevos).
    Sólo cambia la lectura pública: presentación en g y precio por kg/100 g. No
    toca identidad, perfiles ni comparabilidad. Aerosoles quedan sin convertir.
    """
    if offer.presentation_dimension != "ounce" or _AEROSOL_RE.search(offer.product_name or ""):
        return offer
    try:
        ounces = Decimal(str(offer.presentation_total_base))
    except (InvalidOperation, TypeError, ValueError):
        return offer
    if not ounces.is_finite() or ounces <= 0:
        return offer
    node = master_taxonomy.assign_offer(
        offer.supermarket_id, offer.source_category, offer.product_type, offer.category,
        product_name=offer.product_name,
    ).node
    if node is not None and node.subcategory is not None:
        mass = master_taxonomy.node_attribute(node, "ounce") == "mass" or node.subcategory in EXPORT_OUNCE_MASS_SUBCATEGORIES
    else:
        # Misma evidencia explícita que _normalize_public_taxonomy: Egg Beaters es huevo.
        mass = re.search(r"(?<!\w)egg\s+beaters(?!\w)", _fold_public(offer.product_name)) is not None
    if not mass:
        return offer
    grams = (ounces * GRAMS_PER_OUNCE).quantize(Decimal("0.00001"))
    return _core.replace(offer, presentation_dimension="mass_g", presentation_total_base=format(grams.normalize(), "f"))


def build_rows(
    offers: Iterable[VisibleOffer],
    freshness_by_scope: dict[tuple[str, str], str],
    history_by_offer: dict[tuple[str, str], tuple[HistoricalPoint, ...]] | None = None,
    *,
    as_of_utc=None,
) -> list[dict[str, object]]:
    """Construye filas v3, limpia atributos públicos y añade otras presentaciones."""
    _core._derived_presentation = _derived_presentation
    offers = tuple(ounce_as_mass(fix_name_measures(offer)) for offer in offers)
    offers_by_key = {(offer.source_product_id, offer.location_id): offer for offer in offers}
    rows = _ORIGINAL_BUILD_ROWS(
        offers,
        freshness_by_scope,
        history_by_offer,
        as_of_utc=as_of_utc,
    )
    visible: list[dict[str, object]] = []
    for row in rows:
        row["brand"] = canonical_brand(row.get("brand"), row.get("product_name"))
        if not row["brand"]:
            row["brand"] = _prefix_brand(row, offers_by_key)
        _normalize_public_taxonomy(row)
        size = canonical_egg_size(row.get("product_name"), row.get("product_type"))
        if size is not None:
            # Campo aditivo: la presentación sigue siendo el conteo; el tamaño de
            # huevo no vuelve a fragmentar el selector de presentación.
            row["variant"] = size
        if _row_is_shoppable(row):
            visible.append(row)
    for row in visible:
        _price_per_pound(row)
        _attach_pack(row, offers_by_key)
    confirm_packs(visible)
    attach_other_presentations(visible, offers_by_key)
    return visible


# ``pack`` sólo cuando el nombre dice "N envases de X cada uno" y el precio no lo
# contradice. "Pozuelo -12 uds/265g" o "Snickers ... 30 Unidades / 1.5 kg" se leen
# hoy como "12 × 265 g" y "30 × 1.5 kg", pero X es el total: con el total entre
# paréntesis ("(3.18 kg)", "(45 kg)") el error se vería más. Esas filas quedan sin pack.
_PACK_UNIT = r"(?:kgs?|kilos?|grms?|grs?|gramos?|g|mls?|lts?|litros?|l|oz|onzas?|lbs?|libras?)"
_PACK_AMOUNT = r"\d+(?:[.,]\d+)?\s*"
# Un total N veces mayor que el real da un precio por kg/L N veces menor: por debajo
# de 1/4 de la mediana de su tipo (o más cerca de la lectura "X es el total"), X
# puede ser el total y no se emite ``pack`` (ver pack_verdict).
PACK_MIN_MEDIAN_RATIO = Decimal("0.25")
# Igual que la portada (home.json): mediana sólo con ≥ 8 precios del tipo y base.
PACK_MEDIAN_MIN_ROWS = 8
# Vasos, termos, botellas para agua, bolsas de basura: la medida es la capacidad del
# envase, no su contenido ("Vasos 240 Unidades / 532 mL"); un total no tiene sentido.
_PACK_CAPACITY_RE = re.compile(
    r"\b(?:vasos?|termos?|botellas? (?:para|de) (?:agua|bebidas?)|bolsas? para (?:la |las )?basura|"
    r"contenedores?|recipientes?|tazas?)\b"
)


def pack_name_kind(name: object, count: int) -> str | None:
    """Cómo declara el nombre el multipack de ``count`` envases (``None`` si no lo dice).

    - ``"cada_uno"``: la medida es explícitamente por envase: "12x500ml", "4X11g",
      "21 grx12 pack", "4/10 g", "2 Uds 67 ml c/u".
    - ``"unidades"``: formato "6 Unidades / 250 mL" de PriceSmart; casi siempre es por
      envase, pero a veces es el total ("30 Unidades / 1.5 kg"): exige el precio.
    - ``None``: "12 uds/265g", "3u X 1kg", "15W-40 / 4.73 L" o un conteo que el
      nombre no declara como paquete; X puede ser el total o no ser contenido.
    """
    text = " ".join(str(name or "").split()).casefold()
    n = re.escape(str(count))
    each = (
        rf"(?<![\w.,]){n}\s*[x×]\s*{_PACK_AMOUNT}{_PACK_UNIT}(?![a-z])",
        rf"(?<![\w.,]){_PACK_AMOUNT}{_PACK_UNIT}\s*x\s*{n}(?![\d.,])",
        rf"(?<![\w/.,-]){n}\s*/\s*{_PACK_AMOUNT}{_PACK_UNIT}(?![a-z])",
    )
    if any(re.search(pattern, text) for pattern in each):
        return "cada_uno"
    if re.search(rf"(?<![\w.,]){n}\s*(?:u|un|und|uds?|unid|unidades)(?![a-z])", text) and re.search(
        rf"{_PACK_AMOUNT}{_PACK_UNIT}\.?\s*c\s*/\s*u(?![a-z])", text
    ):
        return "cada_uno"
    if re.search(rf"(?<![\w.,]){n}\s+unidades\s*/\s*{_PACK_AMOUNT}{_PACK_UNIT}(?![a-z])", text):
        return "unidades"
    return None


def _best_unit_price(row: dict[str, object]) -> tuple[Decimal, str] | None:
    """Precio por unidad de la oferta más barata (el mismo que muestra la tarjeta)."""
    prices = _eligible_prices(row)
    if not prices:
        return None
    unit = min(prices, key=lambda item: item[0])[1].get("unit_price")
    if not isinstance(unit, dict) or unit.get("per") in {None, "unidad", "libra"}:
        return None
    amount = size_variants.parse_price(unit.get("amount"))
    return None if amount is None or amount <= 0 else (amount, str(unit["per"]))


def unit_price_medians(rows: Sequence[dict[str, object]]) -> dict[tuple[str, str, str], Decimal]:
    """Mediana del precio por unidad por (tipo público, base), como la portada, y por
    (categoría, base) para las filas sin tipo o de tipos con pocos precios."""
    groups: dict[tuple[str, str, str], list[Decimal]] = {}
    for row in rows:
        unit = _best_unit_price(row)
        if unit is None:
            continue
        for level in ("product_type", "category"):
            if row.get(level):
                groups.setdefault((level, str(row[level]), unit[1]), []).append(unit[0])
    return {
        key: sorted(values)[len(values) // 2]
        for key, values in groups.items()
        if len(values) >= PACK_MEDIAN_MIN_ROWS
    }


def pack_verdict(row: dict[str, object], medians: dict[tuple[str, str, str], Decimal]) -> str:
    """``"a"`` = N envases de X cada uno (lleva pack); ``"b"`` = X puede ser el total;
    ``"c"`` = el nombre no declara ese conteo como paquete."""
    pack = row.get("pack")
    if not isinstance(pack, list) or _PACK_CAPACITY_RE.search(_fold_public(f"{row.get('product_name')} {row.get('product_type')}")):
        return "c"
    kind = pack_name_kind(row.get("product_name"), int(pack[0]))
    if kind is None:
        return "b" if re.search(rf"(?<![\w.,]){pack[0]}\s*[a-z]", str(row.get("product_name") or "").casefold()) else "c"
    unit = _best_unit_price(row)
    median = None if unit is None else next(
        (
            medians[key]
            for key in (("product_type", str(row.get("product_type")), unit[1]), ("category", str(row.get("category")), unit[1]))
            if key in medians
        ),
        None,
    )
    if unit is None or median is None:
        return "a" if kind == "cada_uno" else "b"
    # r = precio por unidad leyendo X por envase ÷ mediana; leyendo X como total sería
    # r × N. Sólo (a) si r no es absurdo (≥ 1/4) y queda más cerca de la mediana que
    # r × N (en escala logarítmica: r ≥ 1/√N). "8 Unidades / 475 g" de pan con r = 0.25
    # es un paquete de 475 g, no 3.8 kg.
    ratio = unit[0] / median
    return "a" if ratio >= PACK_MIN_MEDIAN_RATIO and ratio * ratio * int(pack[0]) >= 1 else "b"


def confirm_packs(rows: list[dict[str, object]]) -> dict[str, int]:
    """Deja ``pack`` sólo en las filas del tipo (a); (b) y (c) quedan como hoy."""
    medians = unit_price_medians(rows)
    counts = {"a": 0, "b": 0, "c": 0}
    for row in rows:
        if "pack" not in row:
            continue
        verdict = pack_verdict(row, medians)
        counts[verdict] += 1
        if verdict != "a":
            row.pop("pack")
    print(json.dumps({"pack": counts}, sort_keys=True), file=sys.stderr)
    return counts


def _offer_pack(offer: VisibleOffer) -> list[object] | None:
    return multipack(
        source_presentation=offer.presentation,
        presentation_dimension=offer.presentation_dimension,
        presentation_status=offer.presentation_status,
        presentation_total_base=offer.presentation_total_base,
    )


def _attach_pack(row: dict[str, object], offers_by_key: dict[tuple[str, str], VisibleOffer]) -> None:
    """Campo opcional ``pack`` = ``[conteo, "total"]`` para la etiqueta "4 × 92 g (368 g)".

    Sólo si la presentación publicada es ese multipack ("por libra" o huevos no) y
    ningún miembro del grupo lo contradice: cada oferta trae el mismo ``pack``, el
    mismo multipack o el mismo total, o no trae tamaño. Ante la duda no se emite.
    No cambia ``presentation``, facetas, orden ni precio por unidad.
    """
    group = [
        offers_by_key[(str(offer["source_product_id"]), str(offer["location_id"]))]
        for offer in row.get("offers") or ()  # type: ignore[union-attr]
        if isinstance(offer, dict)
    ]
    if not group:
        return
    pack = _offer_pack(_core._representative(group))
    presentation = row.get("presentation")
    if pack is None or not isinstance(presentation, str) or not presentation.startswith(f"{pack[0]} × "):
        return
    for member in group:
        member_pack = _offer_pack(member)
        if member_pack is not None and member_pack != pack:
            return
        if member_pack is None and _derived_presentation(member) not in {None, presentation, pack[1]}:
            return
    row["pack"] = pack


def _weighed_type(row: dict[str, object]) -> bool:
    words = set(_fold_public(" ".join(str(row.get(key) or "") for key in ("category", "product_type"))).split())
    return bool(words & WEIGHED_TYPE_WORDS)


def _pound_key(offer: dict[str, object]) -> str:
    """Producto de la oferta sin sucursal: Tegucigalpa publica "walmart:51@walmart_tgu_ffaa"
    y los conjuntos de producto pesado se leen de ``products`` ("walmart:51")."""
    return _core.public_product_id(str(offer.get("source_product_id")))


def _price_per_pound(row: dict[str, object]) -> None:
    """Ofertas de producto pesado en tienda: precio por libra, no por paquete."""
    offers = [offer for offer in row.get("offers") or () if isinstance(offer, dict)]
    source = [offer for offer in offers if _pound_key(offer) in SOURCE_POUND]
    coded = [offer for offer in offers if _pound_key(offer) in WEIGHED_BY_POUND
             and offer not in source] if _weighed_type(row) else []
    weighed = source + coded
    if not weighed or _PER_UNIT_NAME.search(str(row.get("product_name") or "")):
        return
    for offer in weighed:
        price = offer.get("current_price")
        offer["unit_price"] = {"amount": price, "per": "libra"} if price else None
    if len(weighed) == len(offers):
        row["presentation"] = "por libra"


def _prefix_brand(row: dict[str, object], offers_by_key: dict[tuple[str, str], VisibleOffer]) -> str | None:
    """Marca curada por prefijo del nombre (p. ej. Colonial) para filas sin marca."""
    for offer in row.get("offers") or ():
        source = offers_by_key.get((str(offer.get("source_product_id")), str(offer.get("location_id"))))
        if source is not None:
            brand = brand_from_prefix(source.supermarket_id, source.product_name)
            if brand:
                return brand
    return None


def _eligible_prices(row: dict[str, object]) -> list[tuple[Decimal, dict[str, object]]]:
    prices: list[tuple[Decimal, dict[str, object]]] = []
    for offer in row.get("offers") or ():
        if not isinstance(offer, dict) or offer.get("availability") == "out_of_stock":
            continue
        price = size_variants.parse_price(offer.get("current_price"))
        if price is not None:
            prices.append((price, offer))
    return prices


def _presentation_entry(row: dict[str, object], *, large_size: bool) -> dict[str, object]:
    prices = _eligible_prices(row)
    best_price, best_offer = min(prices, key=lambda item: item[0]) if prices else (None, {})
    return {
        "row_id": row["row_id"],
        "product_name": row["product_name"],
        "presentation": row["presentation"],
        "best_price": None if best_price is None else format(best_price, "f"),
        "best_unit_price": best_offer.get("unit_price"),
        "large_size": large_size,
        **({"pack": row["pack"]} if row.get("pack") else {}),
    }


def attach_other_presentations(
    rows: list[dict[str, object]],
    offers_by_key: dict[tuple[str, str], VisibleOffer],
    *,
    links_function=None,
) -> dict[str, object]:
    """Añade ``other_presentations`` (regla V) a las filas con otro tamaño publicado.

    Campo aditivo y opcional: sólo aparece en filas con al menos un vínculo. No
    toca identidad, ofertas ni comparabilidad. Un fallo de la regla nunca bloquea
    la publicación: se informa por stderr y el catálogo sale sin el campo.
    """

    links_function = links_function or size_variants.size_variant_links
    try:
        items = []
        totals: dict[str, float] = {}
        for row in rows:
            group = [
                offers_by_key[(str(offer["source_product_id"]), str(offer["location_id"]))]
                for offer in row["offers"]  # type: ignore[union-attr]
            ]
            representative = _core._representative(group)
            prices = [price for price, _ in _eligible_prices(row)]
            items.append(
                size_variants.SizeVariantItem(
                    key=str(row["row_id"]),
                    supermarket_id=representative.supermarket_id,
                    name=str(row["product_name"]),
                    brand=_text(row.get("brand")) or representative.brand,
                    presentation=representative.presentation or _text(row.get("presentation")),
                    category=representative.source_category,
                    price=min(prices) if prices else None,
                )
            )
        links, diagnostics = links_function(items)
        by_id = {str(row["row_id"]): row for row in rows}
        related: dict[str, list[tuple[float, str, bool]]] = {}
        for link in links:
            for this, other, this_total, other_total in (
                (link.key_a, link.key_b, link.total_a, link.total_b),
                (link.key_b, link.key_a, link.total_b, link.total_a),
            ):
                totals[other] = other_total
                closeness = abs(math.log(other_total / this_total)) if this_total > 0 and other_total > 0 else math.inf
                related.setdefault(this, []).append(
                    (closeness, other, other_total >= size_variants.LARGE_SIZE_RATIO * this_total)
                )
        for row_id, entries in related.items():
            chosen = sorted(entries, key=lambda entry: (entry[0], entry[1]))[:MAX_OTHER_PRESENTATIONS]
            chosen.sort(key=lambda entry: (totals[entry[1]], entry[1]))
            by_id[row_id]["other_presentations"] = [
                _presentation_entry(by_id[other], large_size=large) for _, other, large in chosen
            ]
        summary = {
            "rule": f"{size_variants.RULE_ID}@{size_variants.RULE_VERSION}",
            "links": len(links),
            "rows_with_other_presentations": len(related),
            "diagnostics": diagnostics,
        }
    except Exception as exc:  # noqa: BLE001 - la relación nunca bloquea el catálogo
        for row in rows:
            row.pop("other_presentations", None)
        summary = {"status": "error", "error_type": type(exc).__name__, "error": str(exc)[:300]}
    print(json.dumps({"other_presentations": summary}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
    return summary


def _sync_core() -> None:
    """Sincroniza hooks mutables para preservar el contrato del exportador TGU."""
    for name in (
        "EXPECTED_SCOPE",
        "RETAILER_NAMES",
        "MAX_PARTITION_ROWS",
        "PUBLIC_COMPARABILITY",
        "fetch_visible_offers",
        "fetch_historical_points",
        "_identity_groups",
    ):
        setattr(_core, name, globals()[name])
    _core._derived_presentation = _derived_presentation
    _core.build_rows = build_rows


def export_consumer_catalog(
    backend: QueryBackend,
    scope: ComparisonScope,
    output_directory: Path,
    *,
    as_of_utc,
    freshness_window,
    require_products: bool = False,
) -> dict[str, object]:
    _sync_core()
    WEIGHED_BY_POUND.clear()
    SOURCE_POUND.clear()
    SOURCE_GTINS.clear()
    columns = {str(row[0]) for row in backend.query("SELECT name FROM pragma_table_info('products')")}
    if {"ean", "reference"} <= columns:  # bases mínimas de prueba no traen ean/reference
        WEIGHED_BY_POUND.update(f"{row[0]}:{row[1]}" for row in backend.query(WEIGHED_SQL))
    if "ean" in columns:
        SOURCE_GTINS.update(fetch_source_gtins(backend, [supermarket for supermarket, _ in scope.locations]))
    if "presentation" in columns:
        SOURCE_POUND.update(f"{row[0]}:{row[1]}" for row in backend.query(SOURCE_POUND_SQL))
    manifest = _ORIGINAL_EXPORT(
        backend,
        scope,
        output_directory,
        as_of_utc=as_of_utc,
        freshness_window=freshness_window,
        require_products=require_products,
    )
    manifest = attach_search_index(output_directory, manifest)
    return attach_barcode_index(output_directory, manifest)


# Escáner de Súper Compras: código de barras impreso (GTIN público) → fila del catálogo.
BARCODES_SCHEMA = "rpi-consumer-barcodes/v1"
BARCODES_FILE = "barcodes.json"
# Las filas se nombran por el prefijo más corto (mínimo 8) que no se repite entre
# las llaves publicadas (8 con las 34 mil filas de SPS del 2026-11-13). Peor caso
# medido, un código por oferta con EAN en SPS: ≈950 KB, ≈350 KB gzip; con la llave
# completa serían ≈600 KB gzip. La app lo resuelve contra search.json, que siempre
# tiene cargado para mostrar la ficha.
MIN_BARCODE_ID_PREFIX = 8
# "supermarket_id:product_id" → GTIN-14 válido y de circulación global.
SOURCE_GTINS: dict[str, str] = {}


def scan_gtin(value: object) -> str | None:
    """GTIN-14 de un código fuente; ceros de relleno más allá de 14 dígitos no cuentan."""
    text = str(value or "").strip()
    if len(text) > 14 and text.isdigit() and not text[:-14].strip("0"):
        text = text[-14:]
    return canonicalize_gtin(text)


def fetch_source_gtins(backend: QueryBackend, supermarket_ids: Iterable[str]) -> dict[str, str]:
    """Una sola lectura de ``products.ean`` acotada a las cadenas del scope.

    Sólo GTIN válidos por dígito de control (GTIN-14 canónico: 7411000230701,
    07411000230701 y 0007411000230701 son el mismo) y de circulación global: los
    rangos GS1 restringidos (peso variable "2…", uso interno, cupones) se repiten
    entre tiendas con productos distintos y no sirven para escanear.
    """
    supermarkets = sorted(set(supermarket_ids))
    if not supermarkets:
        return {}
    placeholders = ",".join("?" for _ in supermarkets)
    rows = backend.query(
        f"SELECT supermarket_id, product_id, ean FROM products WHERE ean IS NOT NULL AND supermarket_id IN ({placeholders})",
        tuple(supermarkets),
    )
    result: dict[str, str] = {}
    for supermarket_id, product_id, ean in rows:
        gtin = scan_gtin(ean)
        if gtin is None or restricted_circulation_reason(gtin) is not None:
            continue
        result[f"{supermarket_id}:{product_id}"] = gtin
    return result


def _unique_prefix_length(keys: Sequence[str], minimum: int = MIN_BARCODE_ID_PREFIX) -> int:
    longest = max((len(key) for key in keys), default=minimum)
    for length in range(minimum, longest + 1):
        if len({key[:length] for key in keys}) == len(keys):
            return length
    raise ExportError("consumer_barcodes_row_id_collision")


def attach_barcode_index(output_directory: Path, manifest: dict[str, object]) -> dict[str, object]:
    """Índice de códigos de barras (``barcodes.json``): GTIN-14 → filas públicas.

    Se deriva de las particiones ya escritas (sólo filas visibles) y de los GTIN
    leídos en ``fetch_source_gtins``: no publica nada que no esté ya en el
    catálogo salvo el código impreso en el empaque. Una fila puede tener varios
    códigos (ofertas de varias cadenas) y un código varias filas (la misma
    identidad publicada aparte por no ser comparable); la app muestra una lista.
    Queda listado con SHA-256 en el manifest como el resto de archivos.
    """
    partitions = [
        str(item["path"])
        for item in manifest.get("files", [])  # type: ignore[union-attr]
        if isinstance(item, dict) and str(item.get("path", "")).startswith("catalog/")
    ]
    keys: list[str] = []
    codes: dict[str, set[str]] = {}
    for relative in partitions:
        document = json.loads((output_directory / relative).read_text(encoding="utf-8"))
        for row in document.get("rows", []):
            key = str(row["row_id"]).split("-", 1)[-1]
            keys.append(key)
            for offer in row.get("offers") or ():
                gtin = SOURCE_GTINS.get(public_product_id(str(offer.get("source_product_id", ""))))
                if gtin:
                    codes.setdefault(gtin, set()).add(key)
    if len(set(keys)) != len(keys) or len(keys) != manifest.get("visible_rows"):
        raise ExportError("consumer_barcodes_row_count_mismatch")
    length = _unique_prefix_length(keys)
    rows_with_code = {key for found in codes.values() for key in found}
    document = {
        "schema": BARCODES_SCHEMA,
        "as_of": manifest.get("as_of"),
        "gtin_format": "gtin14",
        "id_prefix_length": length,
        "code_count": len(codes),
        "row_count": len(rows_with_code),
        "codes": {
            gtin: (lambda prefixes: prefixes[0] if len(prefixes) == 1 else prefixes)(sorted(key[:length] for key in found))
            for gtin, found in sorted(codes.items())
        },
    }
    _core._atomic_bytes(output_directory / BARCODES_FILE, _compact_json(document))
    files = [item for item in manifest.get("files", []) if not (isinstance(item, dict) and item.get("path") == BARCODES_FILE)]  # type: ignore[union-attr]
    files.append(_core._file_metadata(output_directory, BARCODES_FILE))
    manifest["files"] = files
    manifest["barcodes_file"] = BARCODES_FILE
    _core._atomic_bytes(output_directory / "manifest.json", _core._json_bytes(manifest))
    return manifest


SEARCH_SCHEMA = "rpi-consumer-search/v1"
SEARCH_FILE = "search.json"
# Portada: lo que Inicio necesita sin bajar el índice (≈11 KB gzip contra ≈1.4 MB).
# Se midió partir el índice en trozos por id: 16 trozos pesan 35 % más en total
# que el archivo entero y buscar los necesita todos, así que el índice sigue siendo uno.
HOME_SCHEMA = "rpi-consumer-home/v1"
HOME_FILE = "home.json"
# Filas por sección de la portada (la app muestra 12; el resto es margen).
HOME_ROWS = 16
SEARCH_COLUMNS = (
    "id", "product_name", "brand", "presentation", "category", "product_type",
    "partition", "best_price", "unit_amount", "unit_per", "retailers", "promo",
    "other_presentations", "comparability", "photo", "pack",
)
# id = hash del row_id sin prefijo; comparabilidad en una letra; photo = foto del
# producto: huella de 10 caracteres de la versión 2 (R2, 800 y 320 px), número de
# versión de la rama ``product-images`` (v1, 400 px) o 0 (la app dibuja la ilustración);
# pack = ``[conteo, "total"]`` de un multipack (``[4, "368 g"]``) o 0. La app lee las
# columnas por nombre: una versión en caché ignora ``pack``.
PHOTO_INDEX_SCHEMA = "super-compras-product-images/v1"
PHOTO_INDEX_SCHEMA_V2 = "super-compras-product-images/v2"
_PHOTO_VERSIONS: dict[str, int | str] = {}
_COMPARABILITY_CODES = {"comparable": "c", "single_source": "s", "individual": "i"}


_PHOTO_HASH = re.compile(r"[0-9a-f]{10}")


def load_photo_versions(path: Path | None | Sequence[Path | None]) -> dict[str, int | str]:
    """Lee los ``index.json`` de fotos; si falta o no cuadra, el catálogo sale sin fotos.

    Acepta varios índices: el último gana. Así la versión 2 (R2) reemplaza a la 1
    producto por producto mientras dura la migración.
    """
    if isinstance(path, (list, tuple)):
        merged: dict[str, int | str] = {}
        for item in path:
            merged.update(load_photo_versions(item))
        return merged
    if path is None or not path.is_file():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    schema = document.get("schema") if isinstance(document, dict) else None
    items = document.get("items") if schema in {PHOTO_INDEX_SCHEMA, PHOTO_INDEX_SCHEMA_V2} else None
    if not isinstance(items, dict):
        return {}
    if schema == PHOTO_INDEX_SCHEMA_V2:
        return {
            str(key): value["h"]
            for key, value in items.items()
            if isinstance(value, dict) and not value.get("none") and isinstance(value.get("h"), str)
            and _PHOTO_HASH.fullmatch(value["h"])
        }
    return {
        str(key): value["v"]
        for key, value in items.items()
        if isinstance(value, dict) and not value.get("none") and type(value.get("v")) is int and value["v"] > 0
    }


def _search_entry(row: dict[str, object], partition: int, retailer_index: dict[str, int]) -> list[object]:
    prices = _eligible_prices(row)
    best_price, best_offer = min(prices, key=lambda item: item[0]) if prices else (None, {})
    unit = best_offer.get("unit_price") if isinstance(best_offer, dict) else None
    retailers = sorted({
        retailer_index[str(offer["supermarket_id"])]
        for _, offer in prices
        if str(offer.get("supermarket_id")) in retailer_index
    })
    promo = any(
        offer.get("is_promotion") is True
        or (
            size_variants.parse_price(offer.get("reported_regular_price")) is not None
            and size_variants.parse_price(offer.get("reported_regular_price")) > price
        )
        for price, offer in prices
    )
    key = str(row["row_id"]).split("-", 1)[-1]
    return [
        key, row["product_name"], row.get("brand"), row.get("presentation"),
        row.get("category"), row.get("product_type"), partition,
        None if best_price is None else format(best_price, "f"),
        unit.get("amount") if isinstance(unit, dict) else None,
        unit.get("per") if isinstance(unit, dict) else None,
        retailers, 1 if promo else 0, len(row.get("other_presentations") or ()),
        _COMPARABILITY_CODES.get(str(row.get("comparability")), "i"),
        _PHOTO_VERSIONS.get(key, 0),
        row.get("pack") or 0,
    ]


def attach_search_index(output_directory: Path, manifest: dict[str, object]) -> dict[str, object]:
    """Índice compacto de búsqueda para Súper Compras (``search.json``).

    Una fila por producto público con lo que necesita una tarjeta de resultados
    (nombre, marca, presentación, categoría, mejor precio, precio por unidad,
    súper con oferta, oferta y otras presentaciones) y la partición donde está
    la ficha completa. Se deriva de las particiones ya escritas: no cambia el
    contrato v3 ni la identidad. Queda listado con SHA-256 en el manifest.
    """
    partitions = [
        str(item["path"])
        for item in manifest.get("files", [])  # type: ignore[union-attr]
        if isinstance(item, dict) and str(item.get("path", "")).startswith("catalog/")
    ]
    retailers: list[str] = []
    for item in manifest.get("scope", []):  # type: ignore[union-attr]
        supermarket = str(item["supermarket_id"])
        if supermarket not in retailers:
            retailers.append(supermarket)
    retailer_index = {supermarket: index for index, supermarket in enumerate(retailers)}
    entries: list[list[object]] = []
    for number, relative in enumerate(partitions):
        document = json.loads((output_directory / relative).read_text(encoding="utf-8"))
        for row in document.get("rows", []):
            entries.append(_search_entry(row, number, retailer_index))
    if len(entries) != manifest.get("visible_rows"):
        raise ExportError("consumer_search_row_count_mismatch")
    document = {
        "schema": SEARCH_SCHEMA,
        "as_of": manifest.get("as_of"),
        "columns": list(SEARCH_COLUMNS),
        "retailers": retailers,
        "partitions": partitions,
        "row_count": len(entries),
        "rows": entries,
    }
    written = {SEARCH_FILE: _compact_json(document), HOME_FILE: _compact_json(_home_document(document))}
    for relative, content in written.items():
        _core._atomic_bytes(output_directory / relative, content)
    files = [item for item in manifest.get("files", []) if not (isinstance(item, dict) and str(item.get("path")) in written)]  # type: ignore[union-attr]
    files.extend(_core._file_metadata(output_directory, relative) for relative in written)
    manifest["files"] = files
    manifest["search_file"] = SEARCH_FILE
    manifest["home_file"] = HOME_FILE
    _core._atomic_bytes(output_directory / "manifest.json", _core._json_bytes(manifest))
    return manifest


def _compact_json(document: dict[str, object]) -> bytes:
    return json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=False).encode("utf-8")


def _home_document(document: dict[str, object]) -> dict[str, object]:
    """Portada (``home.json``): lo que la app necesita para pintar Inicio sin el índice.

    Lleva las ofertas y los productos más comparados con el mismo criterio que la
    app, los departamentos con su conteo, la base de precio unitario más común por
    tipo y las medianas de precio por unidad por tipo y base (para esconder
    medidas imposibles igual que con el índice completo).
    """
    columns = {name: index for index, name in enumerate(SEARCH_COLUMNS)}
    rows: list[list[object]] = document["rows"]  # type: ignore[assignment]
    deals = sorted(
        (row for row in rows if row[columns["promo"]] == 1 and row[columns["best_price"]]),
        key=lambda row: (-len(row[columns["retailers"]]), -int(row[columns["other_presentations"]] or 0)),
    )[:HOME_ROWS]
    shown = {row[0] for row in deals}
    compared = sorted(
        (row for row in rows if len(row[columns["retailers"]]) >= 3 and row[0] not in shown),
        key=lambda row: (-len(row[columns["retailers"]]), len(str(row[columns["product_name"]]))),
    )[:HOME_ROWS]
    departments: dict[str, int] = {}
    bases: dict[str, dict[str, int]] = {}
    groups: dict[str, list[float]] = {}
    for row in rows:
        category = row[columns["category"]]
        if category:
            departments[str(category)] = departments.get(str(category), 0) + 1
        product_type, unit_per, unit_amount = row[columns["product_type"]], row[columns["unit_per"]], row[columns["unit_amount"]]
        if not product_type or not unit_per:
            continue
        counts = bases.setdefault(str(product_type), {})
        counts[str(unit_per)] = counts.get(str(unit_per), 0) + 1
        try:
            amount = float(unit_amount) if unit_amount is not None else 0.0
        except (TypeError, ValueError):
            amount = 0.0
        if amount > 0 and unit_per != "unidad":
            groups.setdefault(f"{product_type}|{unit_per}", []).append(amount)
    medians = {}
    for key, values in groups.items():
        if len(values) < 8:
            continue
        values.sort()
        medians[key] = values[len(values) // 2]
    return {
        "schema": HOME_SCHEMA,
        "as_of": document["as_of"],
        "columns": document["columns"],
        "retailers": document["retailers"],
        "partitions": document["partitions"],
        "row_count": document["row_count"],
        "departments": [
            {"name": name, "count": count}
            for name, count in sorted(departments.items(), key=lambda item: (-item[1], item[0]))
        ],
        "unit_basis": {
            product_type: max(counts.items(), key=lambda item: item[1])[0]
            for product_type, counts in sorted(bases.items())
        },
        "unit_medians": medians,
        "sections": {"deals": [row[0] for row in deals], "compared": [row[0] for row in compared]},
        "rows": deals + compared,
    }


def main(argv: Sequence[str] | None = None) -> int:
    # ``--images-index`` es opcional y propio de este envoltorio: el núcleo no lo ve.
    arguments = list(sys.argv[1:] if argv is None else argv)
    images_indexes: list[Path] = []
    while "--images-index" in arguments:
        position = arguments.index("--images-index")
        if position + 1 >= len(arguments):
            raise SystemExit("--images-index requiere una ruta")
        images_indexes.append(Path(arguments[position + 1]))
        del arguments[position:position + 2]
    _PHOTO_VERSIONS.clear()
    _PHOTO_VERSIONS.update(load_photo_versions(images_indexes))
    argv = arguments
    _sync_core()
    previous_export = _core.export_consumer_catalog
    _core.export_consumer_catalog = export_consumer_catalog
    try:
        return _ORIGINAL_MAIN(argv)
    finally:
        _core.export_consumer_catalog = previous_export


if __name__ == "__main__":
    raise SystemExit(main())
