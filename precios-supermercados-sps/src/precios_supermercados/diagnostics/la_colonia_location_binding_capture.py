"""Captura controlada del binding de ubicación de La Colonia.

Las autorizaciones históricas por ID se conservan únicamente para compatibilidad y
evidencia. La antigua ruta ``standing_public_read_only`` se conserva como parámetro
compatibilidad para que callers viejos fallen de forma explícita, pero está
permanentemente cerrada y no puede conceder una ejecución live.

Una captura live sólo puede avanzar mediante el contrato explícito de
``authorization_id`` y los gates versionados en este módulo. Con el estado actual
(``ACTIVE_AUTHORIZATION_IDS`` vacío y ``LIVE_EXECUTION_ENABLED=False``), cualquier
intento live se detiene antes de abrir navegador.

El artefacto sólo contiene nombres públicos de ciudades/tiendas, nombres de
mecanismos de contexto, contadores DOM sanitizados y SHA-256 de valores opacos. No
guarda precios, productos, HTML, selectores dinámicos, URLs con parámetros, cookies
en claro ni payloads GraphQL.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from precios_supermercados.diagnostics.la_colonia_sps_context_diagnostic import (
    DiagnosticBudget,
    DiagnosticSafetyError,
    LogicalRequestCounter,
    install_local_network_guard,
    launch_compatible_chromium,
    sanitize_error,
)
from precios_supermercados.diagnostics.location_binding_dom_controls import (
    CITY_CONTROL_ROLES,
    CITY_STATE_SELECTED,
    LocationControlResolutionError,
    ResolvedCityControl,
    activate_city_control,
    open_location_selector,
    resolve_exact_city_control,
    verify_structural_city_selection,
)
from precios_supermercados.location_binding_radiography import (
    ContextStage,
    LocationBindingReport,
    analyze_location_binding,
    report_dict,
)

TARGET_URL = "https://www.lacolonia.com/"
TARGET_CITY = "San Pedro Sula"
LIVE_EXECUTION_ENABLED = False
STANDING_PUBLIC_READ_ONLY_AUTHORIZED = False
STANDING_PUBLIC_READ_ONLY_AUTHORIZED_AT = "2026-08-23T21:02:02Z"
ACTIVE_AUTHORIZATION_IDS: frozenset[str] = frozenset()
CONSUMED_AUTHORIZATION_IDS: frozenset[str] = frozenset(
    {
        "LC-location-binding-336",
        "LC-location-binding-331",
        "LC-location-binding-332",
        "LC-location-binding-333",
        "LC-location-binding-334",
        "LC-location-binding-335",
        "LC-location-binding-337",
    }
)
AUTHORIZATION_PATTERN = re.compile(r"^LC-location-binding-\d{3}$")

_CONTEXT_ALIASES = {
    "binding": "binding",
    "pickup-point": "pickupPoint",
    "pickuppoint": "pickupPoint",
    "region": "regionId",
    "region-id": "regionId",
    "regionid": "regionId",
    "sales-channel": "salesChannel",
    "saleschannel": "salesChannel",
    "seller": "seller",
    "store": "store",
    "store-id": "storeId",
    "storeid": "storeId",
    "vtex-segment": "vtex_segment",
    "vtex_segment": "vtex_segment",
    "vtex-session": "vtex_session",
    "vtex_session": "vtex_session",
    "x-vtex-binding": "binding",
    "x-vtex-pickup-point": "pickupPoint",
    "x-vtex-region": "regionId",
    "x-vtex-sales-channel": "salesChannel",
    "x-vtex-seller": "seller",
    "x-vtex-store": "store",
    "x-vtex-store-id": "storeId",
    "x-vtex-segment": "vtex_segment",
    "x-vtex-session": "vtex_session",
}
_IGNORED_OPTION_LABELS = frozenset(
    {
        "selecciona tu tienda",
        "selecciona una tienda",
        "selecciona tu ciudad",
        "selecciona una ciudad",
        "ciudad",
        "tienda",
        "sucursal",
    }
)
_ALLOWED_CITY_CONTROL_DIAGNOSTIC_STAGES = frozenset(
    {"prompt", "prompt_scope", "role", "role_scan"}
)
_ALLOWED_CITY_CONTROL_DIAGNOSTIC_KEYS = frozenset(
    {"stage", "role", "candidate_count", "effective_count"}
)


class LocationBindingCaptureError(RuntimeError):
    """Fallo controlado del capturador de ubicación."""

    def __init__(
        self,
        reason: str,
        *,
        diagnostic: Mapping[str, str | int] | None = None,
    ) -> None:
        super().__init__(reason)
        self.diagnostic = dict(diagnostic or {})


@dataclass(slots=True)
class LocationBindingCaptureResult:
    mode: str
    started_at: str
    completed_at: str | None = None
    target_host: str = "www.lacolonia.com"
    target_city: str = TARGET_CITY
    visible_location: str | None = None
    available_cities: list[str] = field(default_factory=list)
    store_selection_observed: bool = False
    available_stores: list[str] = field(default_factory=list)
    selected_store: str | None = None
    binding_report: Mapping[str, Any] | None = None
    city_control_diagnostic: Mapping[str, Any] | None = None
    logical_actions: int = 0
    stop_reason: str | None = None
    errors: list[str] = field(default_factory=list)
    browser_started: bool = False
    target_navigation_started: bool = False
    target_navigation_completed: bool = False
    production_authority: bool = False
    catalog_accepted: bool = False
    extraction_enabled: bool = False
    raw_values_exposed: bool = False

    def public_dict(self) -> dict[str, Any]:
        value = asdict(self)
        if value["raw_values_exposed"]:
            raise LocationBindingCaptureError("raw_values_must_never_be_exposed")
        if (
            value["production_authority"]
            or value["catalog_accepted"]
            or value["extraction_enabled"]
        ):
            raise LocationBindingCaptureError("capture_cannot_grant_commercial_authority")
        diagnostic = value["city_control_diagnostic"]
        if diagnostic is not None:
            if not isinstance(diagnostic, dict):
                raise LocationBindingCaptureError("city_control_diagnostic_invalid")
            if not set(diagnostic).issubset(_ALLOWED_CITY_CONTROL_DIAGNOSTIC_KEYS):
                raise LocationBindingCaptureError("city_control_diagnostic_invalid")
            if diagnostic.get("stage") not in _ALLOWED_CITY_CONTROL_DIAGNOSTIC_STAGES:
                raise LocationBindingCaptureError("city_control_diagnostic_invalid")
            role = diagnostic.get("role")
            if role is not None and role not in CITY_CONTROL_ROLES:
                raise LocationBindingCaptureError("city_control_diagnostic_invalid")
            for key in ("candidate_count", "effective_count"):
                count = diagnostic.get(key)
                if count is not None and (
                    not isinstance(count, int) or isinstance(count, bool) or not 0 <= count <= 30
                ):
                    raise LocationBindingCaptureError("city_control_diagnostic_invalid")
        return value


class RequestContextCollector:
    """Acumula sólo valores de claves de contexto; nunca conserva requests completos."""

    def __init__(self) -> None:
        self._values: dict[str, list[Any]] = {}

    def reset(self) -> None:
        self._values.clear()

    def add(self, key: str, value: Any) -> None:
        alias = _context_alias(key)
        if alias is None:
            return
        bucket = self._values.setdefault(alias, [])
        marker = _stable_marker(value)
        if all(_stable_marker(existing) != marker for existing in bucket):
            bucket.append(value)

    def observe_request(self, request: Any) -> None:
        try:
            for key, value in request.headers.items():
                self.add(str(key), value)
        except Exception:
            pass
        try:
            for key, value in parse_qsl(
                urlsplit(str(request.url)).query,
                keep_blank_values=True,
            ):
                self.add(key, value)
        except Exception:
            pass
        payload: Any = None
        try:
            payload = request.post_data_json
        except Exception:
            try:
                raw = request.post_data
                payload = json.loads(raw) if raw else None
            except Exception:
                payload = None
        _collect_nested_context(payload, self)

    def snapshot(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key in sorted(self._values):
            values = self._values[key]
            result[key] = values[0] if len(values) == 1 else list(values)
        return result


def _stable_marker(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        return repr(value)


def _context_alias(key: str) -> str | None:
    normalized = re.sub(r"[^a-z0-9_-]", "", str(key).strip().casefold())
    compact = normalized.replace("_", "-")
    return _CONTEXT_ALIASES.get(normalized) or _CONTEXT_ALIASES.get(compact)


def _collect_nested_context(value: Any, collector: RequestContextCollector) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            collector.add(str(key), nested)
            _collect_nested_context(nested, collector)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for nested in value:
            _collect_nested_context(nested, collector)


def _known_context(values: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values.items():
        alias = _context_alias(str(key))
        if alias is not None:
            result[alias] = value
    return result


def _storage(page: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    local = page.evaluate("() => Object.fromEntries(Object.entries(localStorage))") or {}
    session = page.evaluate("() => Object.fromEntries(Object.entries(sessionStorage))") or {}
    if not isinstance(local, Mapping) or not isinstance(session, Mapping):
        raise LocationBindingCaptureError("browser_storage_shape_invalid")
    return dict(local), dict(session)


def _cookies(context: Any) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for item in context.cookies():
        if isinstance(item, Mapping) and item.get("name") is not None:
            values[str(item["name"])] = item.get("value")
    return values


def _stage(
    page: Any,
    context: Any,
    collector: RequestContextCollector,
    name: str,
) -> ContextStage:
    local, session = _storage(page)
    return ContextStage(
        name=name,
        channels={
            "cookie": _known_context(_cookies(context)),
            "localStorage": _known_context(local),
            "sessionStorage": _known_context(session),
            "request": collector.snapshot(),
        },
    )


def _clean_label(value: str) -> str | None:
    text = " ".join(str(value).split()).strip()
    if not text or text.casefold() in _IGNORED_OPTION_LABELS:
        return None
    return text


def _option_label(locator: Any) -> str | None:
    try:
        aria = locator.get_attribute("aria-label")
    except Exception:
        aria = None
    if aria:
        return _clean_label(aria)
    try:
        return _clean_label(locator.inner_text())
    except Exception:
        return None


def _open_location_selector(page: Any) -> str | None:
    try:
        resolved = open_location_selector(page)
    except LocationControlResolutionError as exc:
        raise LocationBindingCaptureError(
            str(exc), diagnostic=getattr(exc, "diagnostic", None)
        ) from exc
    return resolved.visible_location


def _city_select_and_options(
    page: Any,
    city_name: str,
) -> tuple[ResolvedCityControl, list[str]]:
    try:
        resolved = resolve_exact_city_control(page, city_name)
    except LocationControlResolutionError as exc:
        raise LocationBindingCaptureError(
            str(exc), diagnostic=getattr(exc, "diagnostic", None)
        ) from exc
    return resolved, list(resolved.available_cities)


def _verify_structural_city_selection(page: Any, city_name: str) -> str | None:
    try:
        return verify_structural_city_selection(page, city_name)
    except LocationControlResolutionError as exc:
        raise LocationBindingCaptureError(
            str(exc), diagnostic=getattr(exc, "diagnostic", None)
        ) from exc


def _activate_option(option: Any, label: str) -> None:
    parent = option.locator("xpath=ancestor::select[1]")
    if parent.count() == 1:
        parent.select_option(label=label)
    else:
        option.click()


def _discover_store_options(
    page: Any,
    known_cities: Sequence[str],
) -> tuple[Any | None, list[str]]:
    city_names = {name.casefold() for name in known_cities}
    options = page.get_by_role("option")
    found: list[tuple[Any, str]] = []
    for index in range(options.count()):
        option = options.nth(index)
        try:
            if not option.is_visible():
                continue
            parent_select = option.locator("xpath=ancestor::select[1]")
            if parent_select.count() == 1:
                continue
            label = _option_label(option)
        except Exception:
            continue
        if not label or label.casefold() in city_names:
            continue
        found.append((option, label))

    if not found:
        store_combobox = page.get_by_role(
            "combobox",
            name=re.compile(r"tienda|sucursal", re.I),
        )
        visible_controls = [
            store_combobox.nth(index)
            for index in range(store_combobox.count())
            if store_combobox.nth(index).is_visible()
        ]
        if visible_controls:
            raise LocationBindingCaptureError(
                "store_selector_present_but_options_unresolved"
            )
        return None, []

    labels = sorted({label for _, label in found}, key=str.casefold)
    if len(labels) > 30:
        raise LocationBindingCaptureError("store_option_count_unreasonable")
    selected_name = labels[0]
    matches = [option for option, label in found if label == selected_name]
    if len(matches) != 1:
        raise LocationBindingCaptureError("store_option_not_unique")
    return matches[0], labels


def _validate_live_target(target_url: str) -> None:
    parts = urlsplit(target_url)
    try:
        port = parts.port
    except ValueError as exc:
        raise LocationBindingCaptureError("live_target_not_exact_la_colonia_home") from exc
    if (
        parts.scheme.casefold() != "https"
        or (parts.hostname or "").casefold() != "www.lacolonia.com"
        or port not in {None, 443}
        or parts.username is not None
        or parts.password is not None
        or parts.path not in {"", "/"}
        or bool(parts.query)
        or bool(parts.fragment)
    ):
        raise LocationBindingCaptureError("live_target_not_exact_la_colonia_home")


def validate_capture_authorization(
    *,
    authorization_id: str | None,
    network_policy: str,
    active_ids: Iterable[str] | None = None,
    consumed_ids: Iterable[str] | None = None,
    live_execution_enabled: bool | None = None,
    standing_public_read_only: bool = False,
) -> None:
    if network_policy not in {"live", "local_only"}:
        raise LocationBindingCaptureError("network_policy_invalid")

    if network_policy == "live":
        if (
            active_ids is not None
            or consumed_ids is not None
            or live_execution_enabled is not None
        ):
            raise LocationBindingCaptureError("live_runtime_overrides_forbidden")
        if standing_public_read_only:
            if authorization_id is not None:
                raise LocationBindingCaptureError("authorization_mode_conflict")
            raise LocationBindingCaptureError("standing_public_read_only_not_authorized")
        effective_active_ids = ACTIVE_AUTHORIZATION_IDS
        effective_consumed_ids = CONSUMED_AUTHORIZATION_IDS
        effective_live_enabled = LIVE_EXECUTION_ENABLED
    else:
        if standing_public_read_only:
            raise LocationBindingCaptureError("standing_public_read_only_live_only")
        effective_active_ids = frozenset(active_ids or ())
        effective_consumed_ids = frozenset(consumed_ids or ())
        effective_live_enabled = False

    if not authorization_id:
        raise LocationBindingCaptureError("authorization_id_required")
    if not AUTHORIZATION_PATTERN.fullmatch(authorization_id):
        raise LocationBindingCaptureError("authorization_id_invalid_format")
    if authorization_id in effective_consumed_ids:
        raise LocationBindingCaptureError("authorization_id_consumed")
    if authorization_id not in effective_active_ids:
        raise LocationBindingCaptureError("authorization_id_not_active")
    if network_policy == "live" and not effective_live_enabled:
        raise LocationBindingCaptureError("live_execution_disabled")


def _persist(result: LocationBindingCaptureResult, output_path: Path | None) -> None:
    if output_path is None:
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result.public_dict(), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )


def _stop_reason(error: BaseException) -> str:
    message = str(error)
    known = {
        "authorization_id_required",
        "authorization_id_invalid_format",
        "authorization_id_consumed",
        "authorization_id_not_active",
        "authorization_mode_conflict",
        "standing_public_read_only_not_authorized",
        "standing_public_read_only_live_only",
        "live_execution_disabled",
        "live_runtime_overrides_forbidden",
        "live_target_not_exact_la_colonia_home",
        "network_policy_invalid",
        "location_selector_not_found",
        "location_selector_not_unique",
        "location_selector_label_missing",
        "target_city_not_found",
        "target_city_not_unique",
        "target_city_state_invalid",
        "target_city_not_selected",
        "target_city_selection_unverified",
        "visible_location_mismatch",
        "store_selector_present_but_options_unresolved",
        "store_option_count_unreasonable",
        "store_option_not_unique",
        "browser_storage_shape_invalid",
        "playwright_not_installed",
    }
    if message in known:
        return message
    if isinstance(error, DiagnosticSafetyError):
        return "diagnostic_safety_error"
    if error.__class__.__name__ == "TimeoutError":
        return "playwright_timeout"
    return "unexpected_capture_error"


def run_capture(
    *,
    authorization_id: str | None,
    output_path: Path | None = None,
    budget: DiagnosticBudget | None = None,
    active_ids: Iterable[str] | None = None,
    consumed_ids: Iterable[str] | None = None,
    live_execution_enabled: bool | None = None,
    standing_public_read_only: bool = False,
    network_policy: str = "live",
    target_url: str = TARGET_URL,
    city_name: str = TARGET_CITY,
) -> LocationBindingCaptureResult:
    """Ejecuta una captura bounded de binding; ``local_only`` sirve a CI.

    La ruta live sólo puede avanzar con un authorization-id activo y el fuse live
    habilitado en código. ``standing_public_read_only`` existe únicamente como
    compatibilidad fail-closed y nunca autoriza red. La función no realiza replay
    GraphQL ni visita páginas de producto. El máximo son cuatro acciones lógicas:
    abrir home, abrir selector, elegir ciudad sólo si hace falta y, únicamente si
    aparece, elegir una tienda.
    """

    budget = budget or DiagnosticBudget(max_logical_requests=4)
    counter = LogicalRequestCounter(budget)
    result = LocationBindingCaptureResult(
        mode="live" if network_policy == "live" else "synthetic_local",
        started_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        target_host=(urlsplit(target_url).hostname or ""),
        target_city=city_name,
    )
    browser = None
    context = None
    try:
        if network_policy == "live":
            _validate_live_target(target_url)
        validate_capture_authorization(
            authorization_id=authorization_id,
            network_policy=network_policy,
            active_ids=active_ids,
            consumed_ids=consumed_ids,
            live_execution_enabled=live_execution_enabled,
            standing_public_read_only=standing_public_read_only,
        )

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise LocationBindingCaptureError("playwright_not_installed") from exc

        with sync_playwright() as pw:
            browser, _executable = launch_compatible_chromium(pw)
            result.browser_started = True
            context = browser.new_context(service_workers="block")
            if network_policy == "local_only":
                install_local_network_guard(context)
            page = context.new_page()
            collector = RequestContextCollector()
            page.on("request", collector.observe_request)

            counter.reserve("open_home")
            result.target_navigation_started = True
            page.goto(target_url, wait_until="domcontentloaded", timeout=20_000)
            result.target_navigation_completed = True
            page.wait_for_timeout(250)
            before = _stage(page, context, collector, "before")

            counter.reserve("open_location_selector")
            result.visible_location = _open_location_selector(page)
            page.wait_for_timeout(150)
            city_control, cities = _city_select_and_options(page, city_name)
            result.available_cities = cities

            collector.reset()
            city_action_performed = False
            if city_control.state != CITY_STATE_SELECTED:
                counter.reserve("select_city")
                try:
                    city_action_performed = activate_city_control(city_control, city_name)
                except LocationControlResolutionError as exc:
                    raise LocationBindingCaptureError(str(exc)) from exc

            if city_action_performed:
                page.wait_for_timeout(500)
            elif city_control.state == CITY_STATE_SELECTED:
                page.wait_for_timeout(100)

            if city_control.state is not None:
                result.visible_location = _verify_structural_city_selection(page, city_name)

            after_city = _stage(page, context, collector, "after_city")

            store_option, stores = _discover_store_options(page, cities)
            result.available_stores = stores
            after_store: ContextStage | None = None
            if store_option is not None:
                result.store_selection_observed = True
                result.selected_store = stores[0]
                collector.reset()
                counter.reserve("select_store")
                _activate_option(store_option, stores[0])
                page.wait_for_timeout(500)
                after_store = _stage(page, context, collector, "after_store")

            analysis: LocationBindingReport = analyze_location_binding(
                city_name=city_name,
                before=before,
                after_city=after_city,
                store_selection_observed=result.store_selection_observed,
                after_store=after_store,
                store_name=result.selected_store,
            )
            result.binding_report = report_dict(analysis)
            result.logical_actions = counter.count
            result.completed_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            _persist(result, output_path)
            return result
    except Exception as exc:
        result.logical_actions = counter.count
        result.completed_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        result.stop_reason = _stop_reason(exc)
        diagnostic = getattr(exc, "diagnostic", None)
        if diagnostic:
            result.city_control_diagnostic = dict(diagnostic)
        result.errors.append(f"{exc.__class__.__name__}: {sanitize_error(exc)}")
        _persist(result, output_path)
        return result
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
