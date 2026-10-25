"""Identificadores deterministas y hash de estado para ofertas."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .enums import SourceKeyType
from .models import NormalizedOffer

_TRACKING_KEYS = {
    "gclid",
    "fbclid",
    "msclkid",
    "mc_cid",
    "mc_eid",
}
_TRACKING_PREFIXES = ("utm_",)
_WHITESPACE = re.compile(r"\s+")
_GTIN_LENGTHS = frozenset({8, 12, 13, 14})


def canonicalize_text(value: str | None) -> str | None:
    """Normaliza Unicode, espacios y mayúsculas para comparación cosmética."""

    if value is None:
        return None
    normalized = unicodedata.normalize("NFKC", str(value))
    collapsed = _WHITESPACE.sub(" ", normalized).strip().casefold()
    return collapsed or None


def _required_component(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} no puede estar vacío")
    return value.strip()


def canonicalize_url(value: str) -> str:
    """Quita solo tracking inequívoco; conserva parámetros funcionales."""

    cleaned = _required_component(value, "stable_url")
    parsed = urlsplit(cleaned)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("stable_url debe ser una URL absoluta http/https")

    filtered_query = [
        (key, item_value)
        for key, item_value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() not in _TRACKING_KEYS
        and not key.casefold().startswith(_TRACKING_PREFIXES)
    ]
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit(
        (
            parsed.scheme.casefold(),
            parsed.netloc.casefold(),
            path,
            urlencode(filtered_query, doseq=True),
            "",
        )
    )


def canonicalize_gtin(value: str | None) -> str | None:
    """Valida GTIN-8/12/13/14 y devuelve su representación GTIN-14.

    Un barcode fuente sólo se convierte en identidad canónica cuando supera el
    check digit GS1. UPC-A/EAN-13 válidos se rellenan a la izquierda hasta 14
    dígitos para que representaciones equivalentes compartan ``product_id``.
    Valores vacíos, no numéricos, de longitud no estándar o con check digit
    inválido no se fuerzan: devuelven ``None`` y permanecen en revisión.
    """

    if value is None:
        return None
    candidate = str(value).strip()
    if not candidate or not candidate.isdigit() or len(candidate) not in _GTIN_LENGTHS:
        return None

    digits = tuple(int(character) for character in candidate)
    check_digit = digits[-1]
    weighted_sum = sum(
        digit * (3 if position % 2 == 1 else 1)
        for position, digit in enumerate(reversed(digits[:-1]), start=1)
    )
    expected = (10 - (weighted_sum % 10)) % 10
    if check_digit != expected:
        return None
    return candidate.zfill(14)


def generate_gtin_product_id(gtin: str) -> str:
    """Genera un ``product_id`` cross-supermercado sólo desde un GTIN válido."""

    canonical = canonicalize_gtin(gtin)
    if canonical is None:
        raise ValueError("gtin no es válido")
    return f"prod_gtin_{canonical}"


def select_source_key(
    *,
    internal_id: str | None = None,
    sku: str | None = None,
    barcode: str | None = None,
    api_id: str | None = None,
    stable_url: str | None = None,
) -> tuple[SourceKeyType, str]:
    """Selecciona la primera llave disponible según la prioridad aprobada."""

    candidates = (
        (SourceKeyType.INTERNAL_ID, internal_id),
        (SourceKeyType.SKU, sku),
        (SourceKeyType.BARCODE, barcode),
        (SourceKeyType.API_ID, api_id),
        (SourceKeyType.STABLE_URL, stable_url),
    )
    for key_type, value in candidates:
        if value is not None and str(value).strip():
            selected = str(value).strip()
            if key_type is SourceKeyType.STABLE_URL:
                selected = canonicalize_url(selected)
            return key_type, selected
    raise ValueError("No existe una llave fuente estable disponible")


def _stable_digest(prefix: str, payload: Mapping[str, Any], length: int = 32) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return f"{prefix}{digest[:length]}"


def generate_source_product_id(
    supermarket_id: str,
    source_key_type: SourceKeyType | str,
    source_key: str,
) -> str:
    """Genera identidad fuente sin usar precio, promoción ni disponibilidad."""

    supermarket = _required_component(supermarket_id, "supermarket_id")
    selected_key = _required_component(source_key, "source_key")
    try:
        key_type = source_key_type if isinstance(source_key_type, SourceKeyType) else SourceKeyType(source_key_type)
    except (TypeError, ValueError) as exc:
        raise ValueError("source_key_type no es válido") from exc

    if key_type is SourceKeyType.STABLE_URL:
        selected_key = canonicalize_url(selected_key)

    return _stable_digest(
        "sp_",
        {
            "supermarket_id": canonicalize_text(supermarket),
            "source_key_type": key_type.value,
            "source_key": selected_key,
        },
    )


def generate_pending_product_id(source_product_id: str) -> str:
    """Crea un singleton provisional sin afirmar equivalencia entre supermercados.

    Mientras no exista un mapping normalizado explícito, cada producto fuente ocupa
    su propio grupo provisional. El ID puede reemplazarse después por un
    ``product_id`` revisado sin alterar ``source_product_id`` ni ``offer_id``.
    """

    source_product = _required_component(source_product_id, "source_product_id")
    return _stable_digest(
        "prod_pending_",
        {"source_product_id": canonicalize_text(source_product)},
    )


def generate_offer_id(supermarket_id: str, location_id: str, source_product_id: str) -> str:
    """Genera la identidad de una oferta por supermercado, ubicación y producto fuente."""

    supermarket = _required_component(supermarket_id, "supermarket_id")
    location = _required_component(location_id, "location_id")
    source_product = _required_component(source_product_id, "source_product_id")
    return _stable_digest(
        "of_",
        {
            "supermarket_id": canonicalize_text(supermarket),
            "location_id": canonicalize_text(location),
            "source_product_id": canonicalize_text(source_product),
        },
    )


def _decimal_string(value: Any) -> str | None:
    if value is None:
        return None
    decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    normalized = decimal_value.normalize()
    rendered = format(normalized, "f")
    return "0" if rendered in {"-0", ""} else rendered


def _integer_value(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _boolean_value(value: Any) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError("is_promotion debe ser booleano")
    return value


def _value(source: NormalizedOffer | Mapping[str, Any], field_name: str) -> Any:
    if isinstance(source, NormalizedOffer):
        return getattr(source, field_name)
    return source.get(field_name)


def generate_state_hash(offer: NormalizedOffer | Mapping[str, Any]) -> str:
    """Resume atributos comerciales y normalizados que abren o cierran periodos."""

    availability = _value(offer, "availability")
    if hasattr(availability, "value"):
        availability = availability.value

    state = {
        "current_price": _decimal_string(_value(offer, "current_price")),
        "reported_regular_price": _decimal_string(_value(offer, "reported_regular_price")),
        "is_promotion": _boolean_value(_value(offer, "is_promotion")),
        "availability": canonicalize_text(availability),
        "normalized_brand": canonicalize_text(_value(offer, "normalized_brand")),
        "category": canonicalize_text(_value(offer, "category")),
        "subcategory": canonicalize_text(_value(offer, "subcategory")),
        "variant": canonicalize_text(_value(offer, "variant")),
        "unit_count": _integer_value(_value(offer, "unit_count")),
        "content_per_unit": _decimal_string(_value(offer, "content_per_unit")),
        "measurement_unit": canonicalize_text(_value(offer, "measurement_unit")),
        "total_content": _decimal_string(_value(offer, "total_content")),
    }
    serialized = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
