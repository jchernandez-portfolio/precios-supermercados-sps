#!/usr/bin/env python3
"""Extiende el MVP SPS con evidencia efímera del BrowserContext ``vtexsession``.

El ``regionId`` canónico sigue siendo la identidad fuerte de San Pedro Sula. Esta
capa sólo usa la transición de ``vtexsegment`` o ``vtexsession`` como evidencia de
que el mismo BrowserContext adoptó el contexto seleccionado antes del único GET
GraphQL permitido. Los valores raw de cookies nunca se conservan ni se escriben.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import probar_muestra_sps_la_colonia_bound as bound  # noqa: E402
import probar_muestra_sps_la_colonia_resilient as resilient  # noqa: E402

SESSION_COOKIE_NAME = "vtexsession"
ORIGINAL_REGION_TRACKER = bound.RegionContextTracker


def _cookie_fingerprint_from_context(context: Any, cookie_name: str) -> str | None:
    try:
        cookies = context.cookies()
    except Exception:
        return None
    target = cookie_name.casefold()
    for cookie in cookies:
        if not isinstance(cookie, Mapping):
            continue
        if str(cookie.get("name") or "").casefold() != target:
            continue
        if not resilient._target_cookie_domain(cookie.get("domain")):
            continue
        value = cookie.get("value")
        if isinstance(value, str) and value:
            return bound._stable_fingerprint(value)
    return None


def _cookie_header_value(headers: Mapping[str, Any], cookie_name: str) -> str | None:
    cookie_header: str | None = None
    for key, value in headers.items():
        if str(key).casefold() == "cookie":
            cookie_header = str(value)
            break
    if not cookie_header:
        return None
    target = cookie_name.casefold()
    for item in cookie_header.split(";"):
        name, separator, value = item.strip().partition("=")
        if separator and name.casefold() == target:
            return value or None
    return None


class SharedBrowserSessionTracker(resilient.SharedSegmentCookieTracker):
    """Acepta el cookie jar compartido si cambia segmento o sesión VTEX."""

    shared_fallback_used = False
    current_instance: SharedBrowserSessionTracker | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._inactive_session_cookie_fingerprint: str | None = None
        self._active_session_cookie_fingerprints: list[str] = []
        type(self).current_instance = self

    def reset_and_enable(self) -> None:
        super().reset_and_enable()
        self._active_session_cookie_fingerprints.clear()
        type(self).shared_fallback_used = False

    def _observe_session_fingerprint(self, fingerprint: str | None) -> None:
        if fingerprint is None:
            return
        if self.active:
            if fingerprint not in self._active_session_cookie_fingerprints:
                self._active_session_cookie_fingerprints.append(fingerprint)
        else:
            self._inactive_session_cookie_fingerprint = fingerprint

    def snapshot_context(self, context: Any) -> None:
        super().snapshot_context(context)
        self._observe_session_fingerprint(
            _cookie_fingerprint_from_context(context, SESSION_COOKIE_NAME)
        )

    def observe_request(self, request: Any) -> None:
        try:
            context = request.frame.page.context
        except Exception:
            context = None
        if context is not None:
            self._observe_session_fingerprint(
                _cookie_fingerprint_from_context(context, SESSION_COOKIE_NAME)
            )
        try:
            value = _cookie_header_value(request.headers, SESSION_COOKIE_NAME)
        except Exception:
            value = None
        if value is not None:
            self._observe_session_fingerprint(bound._stable_fingerprint(value))
        super().observe_request(request)

    @property
    def session_cookie_baseline_observed(self) -> bool:
        return self._inactive_session_cookie_fingerprint is not None

    @property
    def session_cookie_active_observed(self) -> bool:
        return bool(self._active_session_cookie_fingerprints)

    @property
    def session_cookie_transition_observed(self) -> bool:
        baseline = self._inactive_session_cookie_fingerprint
        if baseline is None:
            return False
        return any(
            fingerprint != baseline
            for fingerprint in self._active_session_cookie_fingerprints
        )

    @property
    def shared_session_transition_observed(self) -> bool:
        return (
            self.segment_cookie_transition_observed
            or self.session_cookie_transition_observed
        )

    def replay_context(self) -> tuple[dict[str, str], tuple[tuple[str, str], ...]]:
        try:
            return ORIGINAL_REGION_TRACKER.replay_context(self)
        except bound.passive.MvpSampleError as exc:
            if str(exc) != "sps_region_binding_observed_but_not_replayable":
                raise
            if not self.shared_session_transition_observed:
                raise bound.passive.MvpSampleError(
                    "sps_region_binding_body_only_without_shared_session_transition"
                ) from exc
            type(self).shared_fallback_used = True
            return {}, ()


def _shared_diagnostics() -> dict[str, bool]:
    tracker = SharedBrowserSessionTracker.current_instance
    if tracker is None:
        return {
            "segment_cookie_baseline_observed": False,
            "segment_cookie_active_observed": False,
            "segment_cookie_transition_verified": False,
            "session_cookie_baseline_observed": False,
            "session_cookie_active_observed": False,
            "session_cookie_transition_verified": False,
        }
    return {
        "segment_cookie_baseline_observed": tracker.segment_cookie_baseline_observed,
        "segment_cookie_active_observed": tracker.segment_cookie_active_observed,
        "segment_cookie_transition_verified": tracker.segment_cookie_transition_observed,
        "session_cookie_baseline_observed": tracker.session_cookie_baseline_observed,
        "session_cookie_active_observed": tracker.session_cookie_active_observed,
        "session_cookie_transition_verified": tracker.session_cookie_transition_observed,
    }


def main(argv: list[str] | None = None) -> int:
    original_tracker = resilient.SharedSegmentCookieTracker
    original_diagnostics = resilient._segment_diagnostics
    resilient.SharedSegmentCookieTracker = SharedBrowserSessionTracker
    resilient._segment_diagnostics = _shared_diagnostics
    SharedBrowserSessionTracker.shared_fallback_used = False
    SharedBrowserSessionTracker.current_instance = None
    try:
        return resilient.main(argv)
    finally:
        resilient.SharedSegmentCookieTracker = original_tracker
        resilient._segment_diagnostics = original_diagnostics
        SharedBrowserSessionTracker.shared_fallback_used = False
        SharedBrowserSessionTracker.current_instance = None


if __name__ == "__main__":
    raise SystemExit(main())
