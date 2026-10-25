"""Infraestructura mínima y segura para extractores de supermercados."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Any
from urllib.error import URLError
from urllib.parse import urlsplit

from precios_supermercados.models import RawProduct


class ScraperError(RuntimeError):
    """Error controlado durante una extracción."""


class RobotsPolicyError(ScraperError):
    """La URL solicitada está fuera de las rutas permitidas."""


class HttpStatusError(ScraperError):
    """La fuente respondió con un estado HTTP no aceptable."""

    def __init__(self, status_code: int, url: str) -> None:
        super().__init__(f"HTTP {status_code} al consultar {url}")
        self.status_code = status_code
        self.url = url


class BlockedResponseError(HttpStatusError):
    """La fuente respondió con bloqueo, CAPTCHA o control equivalente."""


class RateLimitedError(HttpStatusError):
    """La fuente mantuvo una respuesta 429 después de los reintentos permitidos."""


class EmptyResponseError(ScraperError):
    """La respuesta válida no contiene productos para la muestra esperada."""


class StructureChangedError(ScraperError):
    """La respuesta ya no contiene la estructura esperada."""


class ExternalNetworkDeniedError(ScraperError):
    """El transporte real permanece cerrado salvo una ruta explícitamente autorizada."""


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """Respuesta HTTP pequeña, fácil de sustituir en pruebas offline."""

    status_code: int
    url: str
    headers: Mapping[str, str]
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Mapping[str, Any]:
        try:
            value = json.loads(self.text)
        except json.JSONDecodeError as exc:
            raise StructureChangedError("La respuesta no es JSON válido") from exc
        if not isinstance(value, Mapping):
            raise StructureChangedError("La respuesta JSON no es un objeto")
        return value


@dataclass(slots=True)
class ExtractionMetrics:
    """Métricas separadas de páginas, productos y SKU."""

    pages_discovered: int = 0
    pages_processed: int = 0
    page_coverage: float = 0.0
    products_discovered: int = 0
    products_requested: int = 0
    products_returned: int = 0
    skus_returned: int = 0
    skus_extracted: int = 0
    duplicate_skus: int = 0
    skus_with_price: int = 0
    skus_pending_review: int = 0
    errors: int = 0
    structural_events: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """Resultado controlado sin persistencia ni historial."""

    products: tuple[RawProduct, ...]
    metrics: ExtractionMetrics
    quality_events: tuple[str, ...]
    accepted: bool
    source_url: str


Transport = Callable[[str, Mapping[str, str], float], HttpResponse]


@dataclass(frozen=True, slots=True)
class OfflineTestTransport:
    """Harness explícito sin autoridad; sólo se admite en pruebas offline."""

    handler: Transport

    def __post_init__(self) -> None:
        if not callable(self.handler):
            raise ValueError("handler debe ser callable")
        module = getattr(self.handler, "__module__", self.handler.__class__.__module__)
        if not str(module).split(".")[-1].startswith("test_"):
            raise ValueError("OfflineTestTransport sólo admite handlers de módulos test_*")

    def __call__(self, url: str, headers: Mapping[str, str], timeout: float) -> HttpResponse:
        return self.handler(url, headers, timeout)


class SafeHttpClient:
    """Cliente GET limitado a hosts y rutas expresamente permitidas."""

    _BLOCK_MARKERS = (
        "captcha",
        "cf-chl-",
        "access denied",
        "robot check",
        "unusual traffic",
    )

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("SafeHttpClient es inmutable después de inicializar")
        object.__setattr__(self, name, value)

    def __init__(
        self,
        *,
        allowed_hosts: set[str],
        forbidden_path_prefixes: tuple[str, ...],
        user_agent: str,
        timeout_seconds: float = 20.0,
        max_retries: int = 0,
        retry_delay_seconds: float = 1.5,
        transport: OfflineTestTransport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(float(timeout_seconds))
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds debe ser mayor que cero")
        if isinstance(max_retries, bool) or type(max_retries) is not int or max_retries != 0:
            raise ValueError("max_retries debe ser 0")
        if (
            isinstance(retry_delay_seconds, bool)
            or not isinstance(retry_delay_seconds, (int, float))
            or not math.isfinite(float(retry_delay_seconds))
            or retry_delay_seconds < 0
        ):
            raise ValueError("retry_delay_seconds no puede ser negativo")
        if transport is not None and type(transport) is not OfflineTestTransport:
            raise ValueError("transport requiere OfflineTestTransport explícito")
        self.allowed_hosts = frozenset(host.casefold() for host in allowed_hosts)
        self.forbidden_path_prefixes = tuple(
            prefix.casefold() for prefix in forbidden_path_prefixes
        )
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_delay_seconds = retry_delay_seconds
        self._transport = transport or self._deny_external_transport
        self.sleeper = sleeper
        self._sealed = True

    @property
    def transport(self) -> Transport:
        return self._transport

    def validate_url(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme != "https":
            raise RobotsPolicyError("Solo se permiten URLs HTTPS")
        if parsed.hostname is None or parsed.hostname.casefold() not in self.allowed_hosts:
            raise RobotsPolicyError(f"Host no permitido: {parsed.hostname or '<vacío>'}")
        path = parsed.path.casefold() or "/"
        if any(path.startswith(prefix) for prefix in self.forbidden_path_prefixes):
            raise RobotsPolicyError(f"Ruta excluida por política: {parsed.path}")

    def get(self, url: str) -> HttpResponse:
        self.validate_url(url)
        headers = {
            "Accept": "application/json,text/plain;q=0.9,*/*;q=0.1",
            "User-Agent": self.user_agent,
        }
        last_response: HttpResponse | None = None

        for attempt in range(self.max_retries + 1):
            if attempt:
                self.sleeper(self.retry_delay_seconds)
            try:
                response = self._transport(url, headers, self.timeout_seconds)
            except (TimeoutError, URLError) as exc:
                if attempt >= self.max_retries:
                    raise ScraperError(f"No fue posible consultar {url}: {exc}") from exc
                continue

            self.validate_url(response.url)
            last_response = response
            status = response.status_code

            if status == 403:
                raise BlockedResponseError(status, response.url)
            if status == 429:
                if attempt >= self.max_retries:
                    raise RateLimitedError(status, response.url)
                retry_after = _retry_after_seconds(response.headers)
                self.sleeper(min(retry_after, 30.0))
                continue
            if 300 <= status <= 399:
                raise HttpStatusError(status, response.url)
            if 500 <= status <= 599:
                if attempt >= self.max_retries:
                    raise HttpStatusError(status, response.url)
                continue
            if status >= 400:
                raise HttpStatusError(status, response.url)

            lowered = response.text.casefold()
            if any(marker in lowered for marker in self._BLOCK_MARKERS):
                raise BlockedResponseError(status, response.url)
            return response

        assert last_response is not None
        raise HttpStatusError(last_response.status_code, last_response.url)

    def get_json(self, url: str) -> Mapping[str, Any]:
        return self.get(url).json()

    @staticmethod
    def _deny_external_transport(
        url: str,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> HttpResponse:
        del url, headers, timeout_seconds
        raise ExternalNetworkDeniedError(
            "GLOBAL LIVE BLOCKED: use un fake explícito para pruebas offline"
        )


def _retry_after_seconds(headers: Mapping[str, str]) -> float:
    raw_value = next(
        (value for key, value in headers.items() if key.casefold() == "retry-after"),
        None,
    )
    if raw_value is None:
        return 1.0
    try:
        return max(float(raw_value), 0.0)
    except ValueError:
        return 1.0
