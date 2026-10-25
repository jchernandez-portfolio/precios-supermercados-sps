"""Normalización offline del catálogo de productos de La Colonia.

Esta capa trabaja exclusivamente con filas ya extraídas/sanitizadas. No hace red,
no concede autoridad comercial y nunca reescribe silenciosamente el valor fuente.

Regla principal:
- una presentación fuente explícita y parseable se normaliza de forma determinista;
- interpretaciones que requirieron revisión se conservan en un registro versionado,
  ligado a una firma de los campos fuente para no aplicar una corrección obsoleta;
- un producto nuevo o cambiado sólo queda ``pending``/``review_required`` cuando la
  evidencia no permite una interpretación segura.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

OUTPUT_COLUMNS = (
    "supermarket_id",
    "source_key_type",
    "source_key",
    "source_catalog_product_id",
    "source_item_id",
    "source_sku",
    "ean",
    "source_name",
    "normalized_name",
    "source_brand",
    "normalized_brand",
    "source_category",
    "category",
    "subcategory",
    "source_presentation",
    "presentation_normalized",
    "presentation_kind",
    "unit_count",
    "content_per_unit",
    "measurement_unit",
    "declared_content",
    "content_scope",
    "total_content",
    "normalization_status",
    "normalization_method",
    "field_normalization_method",
)

_UNIT_ALIASES = {
    "lb": "lb",
    "libra": "lb",
    "kg": "kg",
    "g": "g",
    "gr": "g",
    "ml": "ml",
    "l": "l",
    "lt": "l",
    "oz": "oz",
    "ft": "ft",
}
_COUNT_ALIASES = {"un", "und", "uds", "unidad", "unidades", "ct"}
_COMMERCIAL_UNITS = {
    "mazo": ("bunch", "mazo"),
    "bandeja": ("tray", "bandeja"),
    "bolsa": ("bag", "bolsa"),
    "red": ("net", "red"),
    "rollo": ("roll", "rollo"),
    "pack": ("pack", "pack"),
    "kit": ("kit", "kit"),
    "set": ("set", "set"),
    "combo": ("combo", "combo"),
    "par": ("pair", "par"),
}
_NUMBER = r"\d+(?:[.,]\d+)?"
_MEASURE = r"(?:lb|libra|kg|g|gr|ml|l|lt|oz|ft|gl|gal(?:o|ó)n(?:es)?|m|mts?)"
_DIRECT_PRESENTATION_RE = re.compile(
    r"^\s*(?P<weighted>x\s*)?(?:(?P<amount>\d+(?:[.,]\d+)?)\s*)?"
    r"(?P<unit>lb|libra|kg|g|gr|ml|l|lt|oz|ft|un|und|uds|unidad|unidades)\s*$",
    re.IGNORECASE,
)


class ProductNormalizationError(ValueError):
    """La fila o el registro de normalización no cumple el contrato esperado."""


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        result = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not result.is_finite() or result <= 0:
        return None
    return result


def _decimal_text(value: Decimal) -> str:
    rendered = format(value.normalize(), "f")
    return "0" if rendered in {"", "-0"} else rendered


def _subcategory(category: str) -> str:
    parts = [part.strip() for part in category.split(">") if part.strip()]
    return parts[-1] if parts else ""


def _unit_display(unit: str, amount: Decimal) -> str:
    value = _decimal_text(amount)
    if unit == "gal":
        return f"{value} galón" if amount == 1 else f"{value} galones"
    if unit == "fl_oz":
        return f"{value} fl oz"
    if unit == "sq_ft":
        return f"{value} sq ft"
    return f"{value} {unit}"


def _count_display(count: int, unit: str = "unit") -> str:
    if unit == "roll":
        return f"{count} rollo" if count == 1 else f"{count} rollos"
    return f"{count} unidad" if count == 1 else f"{count} unidades"


def _presentation_result(
    *,
    normalized: str,
    kind: str,
    unit_count: int | str = "",
    content_per_unit: str = "",
    measurement_unit: str = "",
    declared_content: str = "",
    content_scope: str = "",
    total_content: str = "",
    method: str,
) -> dict[str, str]:
    return {
        "presentation_normalized": normalized,
        "presentation_kind": kind,
        "unit_count": str(unit_count),
        "content_per_unit": str(content_per_unit),
        "measurement_unit": measurement_unit,
        "declared_content": str(declared_content),
        "content_scope": content_scope,
        "total_content": str(total_content),
        "normalization_method": method,
    }


def _source_signature(row: Mapping[str, Any]) -> str:
    payload = {
        "source_key_type": _text(row.get("source_key_type")),
        "source_key": _text(row.get("source_key")),
        "source_name": _text(row.get("source_name")),
        "source_brand": _text(row.get("brand", row.get("source_brand"))),
        "source_category": _text(row.get("category", row.get("source_category"))),
        "source_presentation": _text(row.get("presentation", row.get("source_presentation"))),
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def load_override_registry(path: str | Path) -> Mapping[str, Mapping[str, Any]]:
    root = Path(path)
    files = sorted(root.glob("*.json")) if root.is_dir() else [root]
    if not files:
        raise ProductNormalizationError("override_registry_empty")
    expected = [
        "source_signature",
        "presentation_normalized",
        "presentation_kind",
        "unit_count",
        "content_per_unit",
        "measurement_unit",
        "declared_content",
        "content_scope",
        "total_content",
        "normalization_method",
        "normalized_brand",
        "category",
        "subcategory",
    ]
    expanded: dict[str, Mapping[str, Any]] = {}
    for file_path in files:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != "2" or payload.get("supermarket_id") != "la_colonia":
            raise ProductNormalizationError("override_registry_header_invalid")
        fields = payload.get("fields")
        entries = payload.get("entries")
        if fields != expected or not isinstance(entries, Mapping):
            raise ProductNormalizationError("override_registry_entries_invalid")
        for source_key, values in entries.items():
            if source_key in expanded:
                raise ProductNormalizationError("override_registry_duplicate_source_key")
            if not isinstance(values, list) or len(values) != len(fields):
                raise ProductNormalizationError("override_registry_row_invalid")
            item = dict(zip(fields, values, strict=True))
            expanded[str(source_key)] = {
                "source_signature": item["source_signature"],
                "presentation": {
                    key: item[key]
                    for key in (
                        "presentation_normalized",
                        "presentation_kind",
                        "unit_count",
                        "content_per_unit",
                        "measurement_unit",
                        "declared_content",
                        "content_scope",
                        "total_content",
                        "normalization_method",
                    )
                },
                "field_overrides": {
                    key: item[key]
                    for key in ("normalized_brand", "category", "subcategory")
                    if item[key]
                },
            }
    return expanded


def _parse_direct_source_presentation(value: str) -> dict[str, str] | None:
    match = _DIRECT_PRESENTATION_RE.fullmatch(value)
    if match is None:
        return None

    token = match.group("unit").casefold()
    weighted = match.group("weighted") is not None
    amount = _decimal(match.group("amount")) or Decimal("1")

    if token in _COUNT_ALIASES:
        if amount != amount.to_integral_value():
            return None
        count = int(amount)
        return _presentation_result(
            normalized=_count_display(count),
            kind="count",
            unit_count=count,
            content_per_unit="1",
            measurement_unit="unit",
            declared_content="",
            content_scope="not_applicable",
            total_content=str(count),
            method="source_presentation",
        )

    unit = _UNIT_ALIASES.get(token)
    if unit is None:
        return None
    amount_text = _decimal_text(amount)
    if weighted:
        return _presentation_result(
            normalized=_unit_display(unit, amount),
            kind="weighted",
            unit_count=1,
            content_per_unit=amount_text,
            measurement_unit=unit,
            declared_content=amount_text,
            content_scope="sale_basis",
            total_content=amount_text,
            method="source_presentation",
        )
    return _presentation_result(
        normalized=_unit_display(unit, amount),
        kind="physical",
        unit_count=1,
        content_per_unit=amount_text,
        measurement_unit=unit,
        declared_content=amount_text,
        content_scope="total",
        total_content=amount_text,
        method="source_presentation",
    )


def _fallback_from_name(name: str) -> dict[str, str] | None:
    """Reglas conservadoras para productos nuevos sin excepción conocida."""

    match = re.search(
        rf"\b(?P<count>\d+)\s*[x×]\s*(?P<amount>{_NUMBER})\s*(?P<unit>{_MEASURE})\b",
        name,
        re.IGNORECASE,
    )
    if match is not None:
        unit_token = match.group("unit").casefold().replace("ó", "o")
        unit = {**_UNIT_ALIASES, "gl": "gal", "galon": "gal", "galones": "gal", "m": "m", "mt": "m", "mts": "m"}.get(unit_token)
        amount = _decimal(match.group("amount"))
        count = int(match.group("count"))
        if unit and amount and count > 0:
            return _presentation_result(
                normalized=f"{count} x {_unit_display(unit, amount)}",
                kind="physical",
                unit_count=count,
                content_per_unit=_decimal_text(amount),
                measurement_unit=unit,
                declared_content=_decimal_text(amount),
                content_scope="per_unit",
                total_content=_decimal_text(amount * count),
                method="name_explicit_multipack",
            )

    match = re.search(
        rf"(?<!\d)(?P<num>\d+)\s*/\s*(?P<den>\d+)\s*(?P<unit>{_MEASURE})\b",
        name,
        re.IGNORECASE,
    )
    if match is not None and int(match.group("den")) > 0:
        unit_token = match.group("unit").casefold().replace("ó", "o")
        unit = {**_UNIT_ALIASES, "gl": "gal", "galon": "gal", "galones": "gal", "m": "m", "mt": "m", "mts": "m"}.get(unit_token)
        if unit:
            amount = Decimal(match.group("num")) / Decimal(match.group("den"))
            amount_text = _decimal_text(amount)
            return _presentation_result(
                normalized=_unit_display(unit, amount),
                kind="physical",
                unit_count=1,
                content_per_unit=amount_text,
                measurement_unit=unit,
                declared_content=amount_text,
                content_scope="total",
                total_content=amount_text,
                method="name_fraction_measure",
            )

    counts = list(
        re.finditer(
            r"\b(?P<count>\d+)\s*(?:ct|un|und|unds|uds|uni|unidad|unidades)\b",
            name,
            re.IGNORECASE,
        )
    )
    if counts:
        count = int(counts[-1].group("count"))
        return _presentation_result(
            normalized=_count_display(count),
            kind="count",
            unit_count=count,
            content_per_unit="1",
            measurement_unit="unit",
            declared_content="",
            content_scope="not_applicable",
            total_content=str(count),
            method="name_explicit_count",
        )

    if re.search(r"\b(?:un|und|unidad)\s*$", name, re.IGNORECASE):
        return _presentation_result(
            normalized="1 unidad",
            kind="count",
            unit_count=1,
            content_per_unit="1",
            measurement_unit="unit",
            declared_content="",
            content_scope="not_applicable",
            total_content="1",
            method="name_unit",
        )

    for token, (unit, label) in _COMMERCIAL_UNITS.items():
        if re.search(rf"\b{re.escape(token)}\s*$", name, re.IGNORECASE):
            return _presentation_result(
                normalized=f"1 {label}",
                kind="commercial",
                unit_count=1,
                content_per_unit="1",
                measurement_unit=unit,
                declared_content="",
                content_scope="not_applicable",
                total_content="1",
                method="name_commercial_unit",
            )

    if re.search(r"\b(?:x\s*)?(?:lb|libra)\s*$", name, re.IGNORECASE):
        return _presentation_result(
            normalized="1 lb",
            kind="weighted",
            unit_count=1,
            content_per_unit="1",
            measurement_unit="lb",
            declared_content="1",
            content_scope="sale_basis",
            total_content="1",
            method="name_weight_basis",
        )

    matches = list(
        re.finditer(
            rf"(?<![\w.])(?P<amount>{_NUMBER})\s*(?P<unit>{_MEASURE})\b",
            name,
            re.IGNORECASE,
        )
    )
    parsed: list[tuple[Decimal, str]] = []
    aliases = {**_UNIT_ALIASES, "gl": "gal", "galon": "gal", "galones": "gal", "m": "m", "mt": "m", "mts": "m"}
    for match in matches:
        unit = aliases.get(match.group("unit").casefold().replace("ó", "o"))
        amount = _decimal(match.group("amount"))
        if unit and amount and (amount, unit) not in parsed:
            parsed.append((amount, unit))
    if len(parsed) == 1:
        amount, unit = parsed[0]
        amount_text = _decimal_text(amount)
        return _presentation_result(
            normalized=_unit_display(unit, amount),
            kind="physical",
            unit_count=1,
            content_per_unit=amount_text,
            measurement_unit=unit,
            declared_content=amount_text,
            content_scope="total",
            total_content=amount_text,
            method="name_unique_measure",
        )
    return None


def normalize_product_row(
    row: Mapping[str, Any],
    *,
    registry: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, str]:
    source_key = _text(row.get("source_key"))
    if not source_key:
        raise ProductNormalizationError("source_key_missing")
    source_name = _text(row.get("source_name"))
    if not source_name:
        raise ProductNormalizationError("source_name_missing")

    source_brand = _text(row.get("brand", row.get("source_brand")))
    source_category = _text(row.get("category", row.get("source_category")))
    source_presentation = _text(row.get("presentation", row.get("source_presentation")))
    normalized_brand = source_brand
    category = source_category
    subcategory = _subcategory(source_category)
    field_method = "source"

    entry = (registry or {}).get(source_key)
    entry_matches = bool(entry and entry.get("source_signature") == _source_signature(row))
    if entry_matches:
        presentation_payload = entry.get("presentation")
        if not isinstance(presentation_payload, Mapping):
            raise ProductNormalizationError("override_presentation_invalid")
        presentation = {key: _text(presentation_payload.get(key)) for key in (
            "presentation_normalized",
            "presentation_kind",
            "unit_count",
            "content_per_unit",
            "measurement_unit",
            "declared_content",
            "content_scope",
            "total_content",
            "normalization_method",
        )}
        field_overrides = entry.get("field_overrides")
        if isinstance(field_overrides, Mapping):
            normalized_brand = _text(field_overrides.get("normalized_brand")) or normalized_brand
            category = _text(field_overrides.get("category")) or category
            subcategory = _text(field_overrides.get("subcategory")) or _subcategory(category)
            field_method = "manual_override"
        status = "ready"
    else:
        presentation = _parse_direct_source_presentation(source_presentation) if source_presentation else None
        if presentation is None:
            presentation = _fallback_from_name(source_name)
        if presentation is None:
            presentation = _presentation_result(
                normalized="",
                kind="",
                method="pending",
            )
            status = "review_required" if entry else "pending"
        else:
            status = "review_required" if entry else "ready"

    return {
        "supermarket_id": "la_colonia",
        "source_key_type": _text(row.get("source_key_type")),
        "source_key": source_key,
        "source_catalog_product_id": _text(row.get("product_id", row.get("source_catalog_product_id"))),
        "source_item_id": _text(row.get("item_id", row.get("source_item_id"))),
        "source_sku": _text(row.get("reference", row.get("source_sku"))),
        "ean": _text(row.get("ean")),
        "source_name": source_name,
        "normalized_name": source_name,
        "source_brand": source_brand,
        "normalized_brand": normalized_brand,
        "source_category": source_category,
        "category": category,
        "subcategory": subcategory,
        "source_presentation": source_presentation,
        **presentation,
        "normalization_status": status,
        "field_normalization_method": field_method,
    }


def normalize_catalog_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    registry: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[dict[str, str], ...]:
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        item = normalize_product_row(row, registry=registry)
        source_key = item["source_key"]
        if source_key in seen:
            raise ProductNormalizationError("duplicate_source_key")
        seen.add(source_key)
        normalized.append(item)
    return tuple(normalized)
