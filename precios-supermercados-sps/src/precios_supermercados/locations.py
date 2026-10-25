"""Catálogo común y offline de supermercados, ciudades y ubicaciones.

Este módulo no descubre ubicaciones en red ni concede autoridad live. Separa tres
conceptos que no deben confundirse:

- un contexto fuente raw que todavía no identifica ciudad/tienda comercial;
- una ubicación que el supermercado declara disponible;
- una ubicación cuyo binding técnico ya está demostrado y puede habilitarse para
  extracción.

La separación permite registrar desde hoy todas las ciudades conocidas sin
etiquetar precios con una ciudad que todavía no está ligada técnicamente a la
sesión/request de la fuente. La granularidad comercial también es explícita: una
fuente no puede habilitar extracción mientras no sepamos si el contexto efectivo
varía por ciudad, tienda u otro nivel.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from .enums import LocationStatus


class LocationConfigError(ValueError):
    """La configuración de supermercado/ubicación viola una invariante."""


class LocationGranularity(StrEnum):
    """Nivel al que una fuente puede variar precio o inventario."""

    CITY = "city"
    STORE = "store"
    ONLINE = "online"
    UNKNOWN = "unknown"


class LocationSelectionMode(StrEnum):
    """Cómo se determina la ubicación efectiva de una fuente."""

    FIXED_SINGLE_CITY = "fixed_single_city"
    SOURCE_SELECTION_REQUIRED = "source_selection_required"
    UNKNOWN = "unknown"


def _required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LocationConfigError(f"{field_name} no puede estar vacío")
    return value.strip()


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


@dataclass(frozen=True, slots=True)
class SourceLocationContext:
    """Contexto raw explícito que deliberadamente no es una ciudad comercial."""

    supermarket_id: str
    location_id: str
    evidence: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "supermarket_id",
            _required_text(self.supermarket_id, "supermarket_id"),
        )
        object.__setattr__(
            self,
            "location_id",
            _required_text(self.location_id, "location_id"),
        )
        object.__setattr__(self, "evidence", _required_text(self.evidence, "evidence"))


@dataclass(frozen=True, slots=True)
class SupermarketConfig:
    """Identidad estable y política de ubicación de un supermercado."""

    supermarket_id: str
    supermarket_name: str
    location_selection_mode: LocationSelectionMode | str
    country_code: str = "HN"
    is_active: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "supermarket_id",
            _required_text(self.supermarket_id, "supermarket_id"),
        )
        object.__setattr__(
            self,
            "supermarket_name",
            _required_text(self.supermarket_name, "supermarket_name"),
        )
        country = _required_text(self.country_code, "country_code").upper()
        if len(country) != 2 or not country.isalpha():
            raise LocationConfigError("country_code debe ser ISO alpha-2")
        object.__setattr__(self, "country_code", country)
        try:
            mode = (
                self.location_selection_mode
                if isinstance(self.location_selection_mode, LocationSelectionMode)
                else LocationSelectionMode(self.location_selection_mode)
            )
        except (TypeError, ValueError) as exc:
            raise LocationConfigError("location_selection_mode no es válido") from exc
        object.__setattr__(self, "location_selection_mode", mode)
        if not isinstance(self.is_active, bool):
            raise LocationConfigError("is_active debe ser booleano")


@dataclass(frozen=True, slots=True)
class LocationConfig:
    """Ubicación común reutilizable por current/history y futuros backends."""

    location_id: str
    supermarket_id: str
    city_id: str
    city_name: str
    granularity: LocationGranularity | str
    is_available: bool
    in_scope: bool
    extraction_enabled: bool
    technical_binding_confirmed: bool = False
    source_location_key: str | None = None
    evidence: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("location_id", "supermarket_id", "city_id", "city_name"):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name),
            )
        try:
            granularity = (
                self.granularity
                if isinstance(self.granularity, LocationGranularity)
                else LocationGranularity(self.granularity)
            )
        except (TypeError, ValueError) as exc:
            raise LocationConfigError("granularity no es válida") from exc
        object.__setattr__(self, "granularity", granularity)
        for field_name in (
            "is_available",
            "in_scope",
            "extraction_enabled",
            "technical_binding_confirmed",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise LocationConfigError(f"{field_name} debe ser booleano")
        object.__setattr__(
            self,
            "source_location_key",
            _optional_text(self.source_location_key),
        )
        object.__setattr__(self, "evidence", _optional_text(self.evidence))

        if self.technical_binding_confirmed and self.source_location_key is None:
            raise LocationConfigError(
                "technical_binding_confirmed requiere source_location_key"
            )
        if self.extraction_enabled and not self.is_available:
            raise LocationConfigError(
                "una ubicación no disponible no puede habilitar extracción"
            )
        if self.extraction_enabled and not self.in_scope:
            raise LocationConfigError(
                "una ubicación fuera de alcance no puede habilitar extracción"
            )
        if self.extraction_enabled and self.granularity is LocationGranularity.UNKNOWN:
            raise LocationConfigError(
                "una ubicación con granularidad desconocida no puede habilitar extracción"
            )


@dataclass(frozen=True, slots=True)
class LocationCatalog:
    """Registro validado y común para todos los supermercados."""

    supermarkets: Mapping[str, SupermarketConfig]
    locations: tuple[LocationConfig, ...]

    def __post_init__(self) -> None:
        supermarket_values = dict(self.supermarkets)
        if not supermarket_values:
            raise LocationConfigError("el catálogo requiere supermercados")
        if any(key != value.supermarket_id for key, value in supermarket_values.items()):
            raise LocationConfigError("la llave de supermercado no coincide con supermarket_id")

        by_location: dict[str, LocationConfig] = {}
        for location in self.locations:
            if location.location_id in by_location:
                raise LocationConfigError(
                    f"location_id duplicado: {location.location_id}"
                )
            supermarket = supermarket_values.get(location.supermarket_id)
            if supermarket is None:
                raise LocationConfigError(
                    f"supermercado inexistente para {location.location_id}"
                )
            if location.extraction_enabled and not supermarket.is_active:
                raise LocationConfigError(
                    "un supermercado inactivo no puede tener extracción habilitada"
                )
            if (
                location.extraction_enabled
                and location.granularity is LocationGranularity.UNKNOWN
            ):
                raise LocationConfigError(
                    "una ubicación requiere granularidad comercial confirmada antes de habilitar extracción"
                )
            if (
                location.extraction_enabled
                and supermarket.location_selection_mode
                is LocationSelectionMode.SOURCE_SELECTION_REQUIRED
                and not location.technical_binding_confirmed
            ):
                raise LocationConfigError(
                    "una fuente multiubicación requiere binding técnico confirmado "
                    "antes de habilitar extracción"
                )
            by_location[location.location_id] = location

        object.__setattr__(
            self,
            "supermarkets",
            MappingProxyType(supermarket_values),
        )
        object.__setattr__(self, "locations", tuple(self.locations))

    def supermarket(self, supermarket_id: str) -> SupermarketConfig:
        try:
            return self.supermarkets[supermarket_id]
        except KeyError as exc:
            raise LocationConfigError(
                f"supermarket_id desconocido: {supermarket_id}"
            ) from exc

    def location(self, location_id: str) -> LocationConfig:
        for location in self.locations:
            if location.location_id == location_id:
                return location
        raise LocationConfigError(f"location_id desconocido: {location_id}")

    def locations_for_supermarket(self, supermarket_id: str) -> tuple[LocationConfig, ...]:
        self.supermarket(supermarket_id)
        return tuple(
            location
            for location in self.locations
            if location.supermarket_id == supermarket_id
        )

    def in_scope_locations(self, supermarket_id: str) -> tuple[LocationConfig, ...]:
        return tuple(
            location
            for location in self.locations_for_supermarket(supermarket_id)
            if location.in_scope
        )

    def enabled_locations(self, supermarket_id: str) -> tuple[LocationConfig, ...]:
        return tuple(
            location
            for location in self.locations_for_supermarket(supermarket_id)
            if location.extraction_enabled
        )

    def extraction_block_reason(self, location_id: str) -> str | None:
        location = self.location(location_id)
        supermarket = self.supermarket(location.supermarket_id)
        if not supermarket.is_active:
            return "supermarket_inactive"
        if not location.is_available:
            return "location_unavailable"
        if not location.in_scope:
            return "location_out_of_scope"
        if location.granularity is LocationGranularity.UNKNOWN:
            return "location_granularity_unconfirmed"
        if (
            supermarket.location_selection_mode
            is LocationSelectionMode.SOURCE_SELECTION_REQUIRED
            and not location.technical_binding_confirmed
        ):
            return "technical_location_binding_unconfirmed"
        if not location.extraction_enabled:
            return "extraction_disabled"
        return None

    def require_extraction_ready(self, location_id: str) -> LocationConfig:
        location = self.location(location_id)
        reason = self.extraction_block_reason(location_id)
        if reason is not None:
            raise LocationConfigError(reason)
        return location


LA_COLONIA_ONLINE_SOURCE_CONTEXT = SourceLocationContext(
    supermarket_id="la_colonia",
    location_id="la_colonia_online",
    evidence="Catálogo público en línea sin selección obligatoria de ciudad o sucursal.",
)

SOURCE_LOCATION_CONTEXTS: Mapping[str, SourceLocationContext] = MappingProxyType(
    {LA_COLONIA_ONLINE_SOURCE_CONTEXT.location_id: LA_COLONIA_ONLINE_SOURCE_CONTEXT}
)


def validate_source_location_context(
    *,
    supermarket_id: str,
    location_id: str,
    location_status: LocationStatus | str,
    location_evidence: str | None,
    location_confidence: object,
) -> None:
    """Impide promover un contexto raw conocido a ciudad/tienda por inferencia.

    La validación sólo actúa sobre IDs registrados como contextos fuente. Esos IDs
    no pertenecen al catálogo comercial y deben permanecer ``UNKNOWN`` hasta que
    una frontera de binding separada produzca una ubicación comercial distinta.
    """

    context = SOURCE_LOCATION_CONTEXTS.get(location_id)
    if context is None:
        return
    if supermarket_id != context.supermarket_id:
        raise LocationConfigError("source_location_supermarket_mismatch")
    try:
        status = (
            location_status
            if isinstance(location_status, LocationStatus)
            else LocationStatus(location_status)
        )
    except (TypeError, ValueError) as exc:
        raise LocationConfigError("source_location_status_invalid") from exc
    if status is not LocationStatus.UNKNOWN:
        raise LocationConfigError("source_location_cannot_claim_commercial_binding")
    if location_evidence != context.evidence:
        raise LocationConfigError("source_location_evidence_mismatch")
    if location_confidence is not None:
        raise LocationConfigError("source_location_confidence_forbidden")


LA_COLONIA_SUPERMARKET = SupermarketConfig(
    supermarket_id="la_colonia",
    supermarket_name="La Colonia",
    location_selection_mode=LocationSelectionMode.SOURCE_SELECTION_REQUIRED,
)

# La observación live read-only del 2026-08-24 (run 32677568208) demostró que
# seleccionar San Pedro Sula cambia un `regionId` de request con señal fuerte y
# también cambia cookies VTEX de sesión/segmento. El artifact sanitizado se conserva
# en reports/discovery/la-colonia-location-binding-2026-08-24.json. La granularidad
# observada es ciudad; no apareció selector de tienda. Esto confirma binding técnico
# de `la_colonia_sps`, pero no concede aceptación de catálogo ni habilita extracción.
LA_COLONIA_SPS = LocationConfig(
    location_id="la_colonia_sps",
    supermarket_id="la_colonia",
    city_id="sps",
    city_name="San Pedro Sula",
    granularity=LocationGranularity.CITY,
    is_available=True,
    in_scope=True,
    extraction_enabled=False,
    technical_binding_confirmed=True,
    source_location_key="request:regionid:sha256:d7732eccc99c8530a6d29cce4244920e65e85c1d5492facb05469dc3589cb8b7",
    evidence="location_binding_radiography:sha256:80f2e4d333043a38954603c9c72086d241ac9b5a1cc1f10b71a9fde772588d95",
)

# Tegucigalpa continúa fuera del alcance inicial y no ha sido promovida a binding
# comercial. Su presencia en el selector sólo demuestra disponibilidad declarada.
LA_COLONIA_TGU = LocationConfig(
    location_id="la_colonia_tgu",
    supermarket_id="la_colonia",
    city_id="tgu",
    city_name="Tegucigalpa",
    granularity=LocationGranularity.UNKNOWN,
    is_available=True,
    in_scope=False,
    extraction_enabled=False,
    technical_binding_confirmed=False,
    source_location_key=None,
    evidence="website_city_selector",
)

DEFAULT_LOCATION_CATALOG = LocationCatalog(
    supermarkets={LA_COLONIA_SUPERMARKET.supermarket_id: LA_COLONIA_SUPERMARKET},
    locations=(LA_COLONIA_SPS, LA_COLONIA_TGU),
)


def build_location_catalog(
    supermarkets: Iterable[SupermarketConfig],
    locations: Iterable[LocationConfig],
) -> LocationCatalog:
    """Construye un catálogo validado sin depender de almacenamiento externo."""

    supermarket_values = tuple(supermarkets)
    registry: dict[str, SupermarketConfig] = {}
    for supermarket in supermarket_values:
        if supermarket.supermarket_id in registry:
            raise LocationConfigError(
                f"supermarket_id duplicado: {supermarket.supermarket_id}"
            )
        registry[supermarket.supermarket_id] = supermarket
    return LocationCatalog(registry, tuple(locations))
