#!/usr/bin/env python3
"""Entrypoint MVP bound con recuperación DOM y contexto de segmento compartido.

Los runs live previos confirmaron el binding SPS exacto con ``regionId`` sólo en
body. El run 32862196684 mostró que observar el cookie jar únicamente cuando pasan
requests no garantiza una baseline previa a la selección. Este wrapper toma ahora
snapshots explícitos —sólo fingerprints SHA256— justo antes de activar San Pedro
Sula y justo después de verificarla.

``context.request`` comparte el cookie jar del mismo BrowserContext. Por ello el
único GET GraphQL puede conservar el contexto de sesión sin copiar ni persistir
cookies o ``regionId`` raw. La recuperación DOM sigue limitada a una re-resolución
del mismo control y la consulta explícita sigue limitada a un máximo de una por
intento.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import probar_muestra_sps_la_colonia_bound as bound  # noqa: E402

SEGMENT_COOKIE_NAME = "vtexsegment"
TARGET_COOKIE_DOMAIN = "lacolonia.com"


def _is_playwright_timeout(exc: BaseException) -> bool:
    return exc.__class__.__name__ == "TimeoutError"


def _segment_cookie_value(headers: Mapping[str, Any]) -> str | None:
    cookie_header: str | None = None
    for key, value in headers.items():
        if str(key).casefold() == "cookie":
            cookie_header = str(value)
            break
    if not cookie_header:
        return None
    for item in cookie_header.split(";"):
        name, separator, value = item.strip().partition("=")
        if separator and name.casefold() == SEGMENT_COOKIE_NAME:
            return value or None
    return None


def _target_cookie_domain(value: Any) -> bool:
    domain = str(value or "").strip().lstrip(".").casefold()
    return domain == TARGET_COOKIE_DOMAIN or domain.endswith(f".{TARGET_COOKIE_DOMAIN}")


def _segment_cookie_fingerprint_from_cookie_context(context: Any) -> str | None:
    """Devuelve sólo el fingerprint del vtexsegment del contexto objetivo."""

    try:
        cookies = context.cookies()
    except Exception:
        return None

    for cookie in cookies:
        if not isinstance(cookie, Mapping):
            continue
        if str(cookie.get("name") or "").casefold() != SEGMENT_COOKIE_NAME:
            continue
        if not _target_cookie_domain(cookie.get("domain")):
            continue
        value = cookie.get("value")
        if isinstance(value, str) and value:
            return bound._stable_fingerprint(value)
    return None


def _segment_cookie_fingerprint_from_context(request: Any) -> str | None:
    """Lee el cookie jar asociado a una request y devuelve sólo un fingerprint."""

    try:
        context = request.frame.page.context
    except Exception:
        return None
    return _segment_cookie_fingerprint_from_cookie_context(context)


class SharedSegmentCookieTracker(bound.RegionContextTracker):
    """Autoriza cookie-jar compartido sólo tras transición observable de segmento."""

    shared_fallback_used = False
    current_instance: SharedSegmentCookieTracker | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._inactive_segment_cookie_fingerprint: str | None = None
        self._active_segment_cookie_fingerprints: list[str] = []
        self._city_activation_pending = False
        type(self).current_instance = self

    def reset_and_enable(self) -> None:
        # El runner bound llama este método inmediatamente antes del click. Para
        # obtener una baseline exacta necesitamos mantener el tracker inactivo hasta
        # que el wrapper reciba el control y pueda leer su BrowserContext.
        super().reset_and_enable()
        self.active = False
        self._active_segment_cookie_fingerprints.clear()
        self._city_activation_pending = True
        type(self).shared_fallback_used = False

    def _observe_segment_fingerprint(self, fingerprint: str | None) -> None:
        if fingerprint is None:
            return
        if self.active:
            if fingerprint not in self._active_segment_cookie_fingerprints:
                self._active_segment_cookie_fingerprints.append(fingerprint)
        else:
            # Conservar sólo el SHA256 más reciente observado antes de SPS.
            self._inactive_segment_cookie_fingerprint = fingerprint

    def snapshot_context(self, context: Any) -> None:
        self._observe_segment_fingerprint(
            _segment_cookie_fingerprint_from_cookie_context(context)
        )

    def begin_city_activation(self, context: Any) -> None:
        """Fija baseline pre-click y habilita tracking de región/segmento."""

        if not self._city_activation_pending:
            raise bound.passive.MvpSampleError("sps_city_activation_tracker_not_pending")
        self.snapshot_context(context)
        self.active = True
        self._city_activation_pending = False

    def observe_request(self, request: Any) -> None:
        # Señal continua secundaria. Los snapshots exactos antes/después del click
        # no dependen de que el navegador emita una request en un momento concreto.
        self._observe_segment_fingerprint(
            _segment_cookie_fingerprint_from_context(request)
        )
        try:
            value = _segment_cookie_value(request.headers)
        except Exception:
            value = None
        if value is not None:
            self._observe_segment_fingerprint(bound._stable_fingerprint(value))
        super().observe_request(request)

    @property
    def segment_cookie_baseline_observed(self) -> bool:
        return self._inactive_segment_cookie_fingerprint is not None

    @property
    def segment_cookie_active_observed(self) -> bool:
        return bool(self._active_segment_cookie_fingerprints)

    @property
    def segment_cookie_transition_observed(self) -> bool:
        baseline = self._inactive_segment_cookie_fingerprint
        if baseline is None:
            return False
        return any(
            fingerprint != baseline
            for fingerprint in self._active_segment_cookie_fingerprints
        )

    def replay_context(self) -> tuple[dict[str, str], tuple[tuple[str, str], ...]]:
        try:
            return super().replay_context()
        except bound.passive.MvpSampleError as exc:
            if str(exc) != "sps_region_binding_observed_but_not_replayable":
                raise
            if not self.segment_cookie_transition_observed:
                raise bound.passive.MvpSampleError(
                    "sps_region_binding_body_only_without_segment_cookie_transition"
                ) from exc
            # No se copia ningún valor raw. El APIRequestContext asociado al
            # BrowserContext enviará por sí mismo el cookie jar de esa sesión.
            type(self).shared_fallback_used = True
            return {}, ()


def activate_city_control_resilient(
    control: Any,
    city_name: str,
    *,
    activate_fn: Callable[[Any, str], bool],
    resolve_fn: Callable[[Any, str], Any],
    wait_for_city_fn: Callable[[Any], str],
) -> bool:
    """Activa la misma ciudad con como máximo una re-resolución por detach/timeout."""

    try:
        return activate_fn(control, city_name)
    except Exception as exc:
        if not _is_playwright_timeout(exc):
            raise

        try:
            page = control.locator.page
        except Exception as page_exc:
            raise bound.passive.MvpSampleError(
                "sps_city_control_activation_timeout"
            ) from page_exc

        # El click puede haber disparado el cambio antes de que el nodo se desmontara.
        # Verificar primero evita duplicar una selección que ya ocurrió.
        try:
            wait_for_city_fn(page)
            return True
        except bound.passive.MvpSampleError:
            pass

        refreshed = resolve_fn(page, city_name)
        try:
            return activate_fn(refreshed, city_name)
        except Exception as retry_exc:
            if _is_playwright_timeout(retry_exc):
                raise bound.passive.MvpSampleError(
                    "sps_city_control_activation_timeout"
                ) from retry_exc
            raise


def _segment_diagnostics() -> dict[str, bool]:
    tracker = SharedSegmentCookieTracker.current_instance
    if tracker is None:
        return {
            "segment_cookie_baseline_observed": False,
            "segment_cookie_active_observed": False,
            "segment_cookie_transition_verified": False,
        }
    return {
        "segment_cookie_baseline_observed": tracker.segment_cookie_baseline_observed,
        "segment_cookie_active_observed": tracker.segment_cookie_active_observed,
        "segment_cookie_transition_verified": tracker.segment_cookie_transition_observed,
    }


def main(argv: list[str] | None = None) -> int:
    original_activate = bound.activate_city_control
    original_tracker = bound.RegionContextTracker
    original_success_artifact = bound._success_artifact
    original_failure_artifact = bound._failure_artifact

    def _resilient(control: Any, city_name: str) -> bool:
        try:
            page = control.locator.page
            context = page.context
        except Exception as exc:
            raise bound.passive.MvpSampleError(
                "sps_city_control_context_unavailable"
            ) from exc

        tracker = SharedSegmentCookieTracker.current_instance
        if tracker is None:
            raise bound.passive.MvpSampleError("sps_segment_tracker_unavailable")

        tracker.begin_city_activation(context)
        result = activate_city_control_resilient(
            control,
            city_name,
            activate_fn=original_activate,
            resolve_fn=bound.resolve_exact_city_control,
            wait_for_city_fn=bound.passive._wait_for_city,
        )

        # El snapshot activo ocurre sólo después de confirmar visualmente SPS.
        bound.passive._wait_for_city(page)
        tracker.snapshot_context(context)
        return result

    def _success_artifact(**kwargs: Any) -> dict[str, Any]:
        if (
            kwargs.get("capture_mode") == "single_explicit_bound_fallback"
            and SharedSegmentCookieTracker.shared_fallback_used
        ):
            kwargs["capture_mode"] = "single_explicit_shared_segment_cookie"
        artifact = original_success_artifact(**kwargs)
        artifact.update(_segment_diagnostics())
        bound.passive._validate_artifact_shape(artifact)
        return artifact

    def _failure_artifact(reason: str, diagnostic: Mapping[str, Any]) -> dict[str, Any]:
        artifact = original_failure_artifact(reason, diagnostic)
        artifact.update(_segment_diagnostics())
        bound.passive._validate_artifact_shape(artifact)
        return artifact

    SharedSegmentCookieTracker.shared_fallback_used = False
    SharedSegmentCookieTracker.current_instance = None
    bound.activate_city_control = _resilient
    bound.RegionContextTracker = SharedSegmentCookieTracker
    bound._success_artifact = _success_artifact
    bound._failure_artifact = _failure_artifact
    try:
        return bound.main(argv)
    finally:
        bound.activate_city_control = original_activate
        bound.RegionContextTracker = original_tracker
        bound._success_artifact = original_success_artifact
        bound._failure_artifact = original_failure_artifact
        SharedSegmentCookieTracker.shared_fallback_used = False
        SharedSegmentCookieTracker.current_instance = None


if __name__ == "__main__":
    raise SystemExit(main())
