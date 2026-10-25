from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "probar_muestra_sps_la_colonia_resilient.py"
SPEC = importlib.util.spec_from_file_location("probar_muestra_sps_la_colonia_resilient", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def _control(page: object) -> SimpleNamespace:
    return SimpleNamespace(locator=SimpleNamespace(page=page))


class _CookieContext:
    def __init__(self, cookies):
        self._cookies = list(cookies)

    def cookies(self):
        return list(self._cookies)

    def set_segment(self, value: str, domain: str = ".lacolonia.com") -> None:
        self._cookies = [
            {
                "name": "vtexsegment",
                "value": value,
                "domain": domain,
            }
        ]


def _cookie_context(
    value: str | None,
    *,
    domain: str = ".lacolonia.com",
) -> _CookieContext:
    cookies = []
    if value is not None:
        cookies.append({"name": "vtexsegment", "value": value, "domain": domain})
    return _CookieContext(cookies)


def _request(
    *,
    cookie: str | None = None,
    payload=None,
    context_cookie: str | None = None,
    context_cookie_domain: str = ".lacolonia.com",
    context: _CookieContext | None = None,
) -> SimpleNamespace:
    headers = {"cookie": cookie} if cookie is not None else {}
    if context is None:
        context = _cookie_context(context_cookie, domain=context_cookie_domain)
    page = SimpleNamespace(context=context)
    frame = SimpleNamespace(page=page)
    return SimpleNamespace(
        headers=headers,
        url="https://www.lacolonia.com/_v/segment/graphql/v1",
        post_data_json=payload,
        frame=frame,
    )


def test_timeout_does_not_repeat_click_when_city_was_already_selected() -> None:
    page = object()
    control = _control(page)
    calls = {"activate": 0, "resolve": 0, "wait": 0}

    def activate(_control, _city):
        calls["activate"] += 1
        raise TimeoutError("detached")

    def wait_for_city(observed_page):
        assert observed_page is page
        calls["wait"] += 1
        return "San Pedro Sula"

    def resolve(_page, _city):
        calls["resolve"] += 1
        raise AssertionError("must not re-resolve after verified selection")

    assert module.activate_city_control_resilient(
        control,
        "San Pedro Sula",
        activate_fn=activate,
        resolve_fn=resolve,
        wait_for_city_fn=wait_for_city,
    ) is True
    assert calls == {"activate": 1, "resolve": 0, "wait": 1}


def test_timeout_reresolves_same_city_once_when_selection_did_not_land() -> None:
    page = object()
    first = _control(page)
    refreshed = _control(page)
    calls = {"activate": 0, "resolve": 0}

    def activate(control, city):
        assert city == "San Pedro Sula"
        calls["activate"] += 1
        if control is first:
            raise TimeoutError("detached")
        assert control is refreshed
        return True

    def wait_for_city(_page):
        raise module.bound.passive.MvpSampleError("sps_city_selection_unverified")

    def resolve(observed_page, city):
        assert observed_page is page
        assert city == "San Pedro Sula"
        calls["resolve"] += 1
        return refreshed

    assert module.activate_city_control_resilient(
        first,
        "San Pedro Sula",
        activate_fn=activate,
        resolve_fn=resolve,
        wait_for_city_fn=wait_for_city,
    ) is True
    assert calls == {"activate": 2, "resolve": 1}


def test_second_timeout_stops_fail_closed_without_more_refreshes() -> None:
    page = object()
    first = _control(page)
    refreshed = _control(page)
    calls = {"activate": 0, "resolve": 0}

    def activate(_control, _city):
        calls["activate"] += 1
        raise TimeoutError("detached")

    def wait_for_city(_page):
        raise module.bound.passive.MvpSampleError("sps_city_selection_unverified")

    def resolve(_page, _city):
        calls["resolve"] += 1
        return refreshed

    with pytest.raises(
        module.bound.passive.MvpSampleError,
        match="sps_city_control_activation_timeout",
    ):
        module.activate_city_control_resilient(
            first,
            "San Pedro Sula",
            activate_fn=activate,
            resolve_fn=resolve,
            wait_for_city_fn=wait_for_city,
        )

    assert calls == {"activate": 2, "resolve": 1}


def test_non_timeout_activation_error_is_not_retried() -> None:
    page = object()
    first = _control(page)

    def activate(_control, _city):
        raise RuntimeError("unexpected")

    with pytest.raises(RuntimeError, match="unexpected"):
        module.activate_city_control_resilient(
            first,
            "San Pedro Sula",
            activate_fn=activate,
            resolve_fn=lambda *_: pytest.fail("must not resolve"),
            wait_for_city_fn=lambda *_: pytest.fail("must not wait"),
        )


def test_reset_defers_activation_until_exact_preclick_snapshot() -> None:
    region = "opaque-sps-region"
    context = _cookie_context("segment-before")
    tracker = module.SharedSegmentCookieTracker(
        expected_fingerprint=module.bound._stable_fingerprint(region)
    )

    tracker.reset_and_enable()
    assert tracker.active is False

    tracker.begin_city_activation(context)
    assert tracker.active is True
    assert tracker.segment_cookie_transition_observed is False

    context.set_segment("segment-after")
    tracker.snapshot_context(context)
    assert tracker.segment_cookie_transition_observed is True

    diagnostic_repr = repr(tracker.__dict__)
    assert "segment-before" not in diagnostic_repr
    assert "segment-after" not in diagnostic_repr


def test_body_only_region_uses_exact_context_cookie_snapshots() -> None:
    region = "opaque-sps-region"
    context = _cookie_context("segment-before")
    tracker = module.SharedSegmentCookieTracker(
        expected_fingerprint=module.bound._stable_fingerprint(region)
    )

    tracker.reset_and_enable()
    tracker.begin_city_activation(context)
    context.set_segment("segment-after")
    tracker.snapshot_context(context)
    tracker.observe_request(
        _request(
            context=context,
            payload={"variables": {"regionId": region}},
        )
    )

    headers, query = tracker.replay_context()

    assert tracker.fingerprint_verified is True
    assert tracker.body_only_match_observed is True
    assert tracker.replayable_count == 0
    assert tracker.segment_cookie_transition_observed is True
    assert headers == {}
    assert query == ()
    assert module.SharedSegmentCookieTracker.shared_fallback_used is True
    diagnostic_repr = repr(tracker.__dict__)
    assert "segment-before" not in diagnostic_repr
    assert "segment-after" not in diagnostic_repr
    assert region not in diagnostic_repr


def test_header_cookie_transition_remains_compatible_secondary_evidence() -> None:
    region = "opaque-sps-region"
    context = _cookie_context(None)
    tracker = module.SharedSegmentCookieTracker(
        expected_fingerprint=module.bound._stable_fingerprint(region)
    )

    tracker.observe_request(
        _request(cookie="vtexsegment=segment-before; other=1", context=context)
    )
    tracker.reset_and_enable()
    tracker.begin_city_activation(context)
    tracker.observe_request(
        _request(
            cookie="vtexsegment=segment-after; other=1",
            context=context,
            payload={"variables": {"regionId": region}},
        )
    )

    headers, query = tracker.replay_context()

    assert tracker.segment_cookie_transition_observed is True
    assert headers == {}
    assert query == ()


def test_body_only_region_without_context_cookie_transition_stays_fail_closed() -> None:
    region = "opaque-sps-region"
    context = _cookie_context("same-segment")
    tracker = module.SharedSegmentCookieTracker(
        expected_fingerprint=module.bound._stable_fingerprint(region)
    )

    tracker.reset_and_enable()
    tracker.begin_city_activation(context)
    tracker.snapshot_context(context)
    tracker.observe_request(
        _request(
            context=context,
            payload={"variables": {"regionId": region}},
        )
    )

    with pytest.raises(
        module.bound.passive.MvpSampleError,
        match="sps_region_binding_body_only_without_segment_cookie_transition",
    ):
        tracker.replay_context()

    assert tracker.segment_cookie_transition_observed is False
    assert module.SharedSegmentCookieTracker.shared_fallback_used is False


def test_body_only_region_without_preselection_cookie_baseline_stays_fail_closed() -> None:
    region = "opaque-sps-region"
    context = _cookie_context(None)
    tracker = module.SharedSegmentCookieTracker(
        expected_fingerprint=module.bound._stable_fingerprint(region)
    )

    tracker.reset_and_enable()
    tracker.begin_city_activation(context)
    context.set_segment("segment-after")
    tracker.snapshot_context(context)
    tracker.observe_request(
        _request(
            context=context,
            payload={"variables": {"regionId": region}},
        )
    )

    with pytest.raises(
        module.bound.passive.MvpSampleError,
        match="sps_region_binding_body_only_without_segment_cookie_transition",
    ):
        tracker.replay_context()

    assert tracker.segment_cookie_transition_observed is False


def test_explicit_header_region_still_has_priority_over_cookie_fallback() -> None:
    region = "opaque-sps-region"
    context = _cookie_context("segment-before")
    tracker = module.SharedSegmentCookieTracker(
        expected_fingerprint=module.bound._stable_fingerprint(region)
    )
    tracker.reset_and_enable()
    tracker.begin_city_activation(context)
    request = _request(context=context)
    request.headers["x-vtex-region"] = region
    tracker.observe_request(request)

    headers, query = tracker.replay_context()

    assert headers == {"x-vtex-region": region}
    assert query == ()
    assert module.SharedSegmentCookieTracker.shared_fallback_used is False


def test_context_cookie_snapshot_ignores_unrelated_domains_and_never_returns_raw() -> None:
    unrelated = _cookie_context("must-not-return", domain="example.com")
    assert module._segment_cookie_fingerprint_from_cookie_context(unrelated) is None

    target = _cookie_context("opaque", domain=".lacolonia.com")
    fingerprint = module._segment_cookie_fingerprint_from_cookie_context(target)
    assert fingerprint == module.bound._stable_fingerprint("opaque")
    assert fingerprint != "opaque"

    request = _request(context=target)
    assert module._segment_cookie_fingerprint_from_context(request) == fingerprint


def test_begin_city_activation_cannot_be_reused_without_new_reset() -> None:
    tracker = module.SharedSegmentCookieTracker()
    context = _cookie_context("segment-before")
    tracker.reset_and_enable()
    tracker.begin_city_activation(context)

    with pytest.raises(
        module.bound.passive.MvpSampleError,
        match="sps_city_activation_tracker_not_pending",
    ):
        tracker.begin_city_activation(context)


def test_cookie_parser_is_case_insensitive_and_never_returns_other_cookie() -> None:
    assert module._segment_cookie_value(
        {"Cookie": "foo=1; VTEXSEGMENT=opaque; bar=2"}
    ) == "opaque"
    assert module._segment_cookie_value({"cookie": "foo=1; bar=2"}) is None
