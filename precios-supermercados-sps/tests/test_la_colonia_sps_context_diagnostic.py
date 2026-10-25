from __future__ import annotations

import ast
import json
import socket
import threading
from pathlib import Path
from urllib.parse import urlencode

import pytest

from precios_supermercados.diagnostics.la_colonia_sps_context_diagnostic import (
    AmbiguousDomTarget,
    DiagnosticBudget,
    DiagnosticSafetyError,
    DomTargetNotFound,
    LogicalRequestBudgetExceeded,
    LogicalRequestCounter,
    SpsContextDiagnostic,
    build_minimal_graphql_replay,
    city_selector_plan,
    classify_facet,
    classify_graphql,
    compare_storage,
    find_unique_dom_target,
    parse_network_request,
    run_offline_fixture,
    sanitize_headers,
    sanitize_mapping,
    sanitize_url,
    store_option_plan,
    store_selector_plan,
    structural_fields,
    summarize_response,
    validate_live_authorization,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures"
DOM_FIXTURE = FIXTURE_ROOT / "la_colonia_sps_context_diagnostic.html"
NETWORK_FIXTURE = FIXTURE_ROOT / "la_colonia_sps_context_diagnostic.json"
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "src/precios_supermercados/diagnostics/la_colonia_sps_context_diagnostic.py"
)


@pytest.fixture
def dom_html() -> str:
    return DOM_FIXTURE.read_text(encoding="utf-8")


@pytest.fixture
def diagnostic_fixture() -> dict:
    return json.loads(NETWORK_FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def root_event(diagnostic_fixture: dict) -> dict:
    return diagnostic_fixture["network"][0]


@pytest.fixture
def facets_event(diagnostic_fixture: dict) -> dict:
    return diagnostic_fixture["network"][1]


def test_store_selector_plan_has_requested_fallback_order():
    kinds = [item["kind"] for item in store_selector_plan()]
    assert kinds == [
        "role", "accessible_name", "label", "text", "semantic_attribute",
        "select", "button", "combobox", "dialog", "structural_fallback",
    ]


def test_city_and_store_plans_do_not_use_positional_nth():
    serialized = json.dumps([*city_selector_plan(), *store_option_plan()], ensure_ascii=False)
    assert "nth(" not in serialized
    assert "San Pedro Sula" in serialized
    assert "Plaza Pedregal" in serialized


def test_detector_finds_select_store_control(dom_html: str):
    candidate = find_unique_dom_target(dom_html, "Selecciona tu tienda")
    assert candidate.role == "button"
    assert candidate.tier == 1


def test_detector_finds_san_pedro_sula(dom_html: str):
    candidate = find_unique_dom_target(dom_html, "San Pedro Sula")
    assert candidate.role == "option"
    assert candidate.accessible_name == "San Pedro Sula"


def test_detector_finds_plaza_pedregal(dom_html: str):
    candidate = find_unique_dom_target(dom_html, "Plaza Pedregal")
    assert candidate.role == "option"
    assert candidate.accessible_name == "Plaza Pedregal"


def test_detector_fails_safe_when_city_is_missing():
    html = '<button aria-label="Selecciona tu tienda">Selecciona tu tienda</button>'
    with pytest.raises(DomTargetNotFound):
        find_unique_dom_target(html, "San Pedro Sula")


def test_detector_fails_safe_when_city_is_ambiguous():
    html = """
    <div>
      <button role="option" aria-label="San Pedro Sula">San Pedro Sula</button>
      <button role="option" aria-label="San Pedro Sula">San Pedro Sula</button>
    </div>
    """
    with pytest.raises(AmbiguousDomTarget):
        find_unique_dom_target(html, "San Pedro Sula")


def test_product_search_request_is_identified(root_event: dict):
    assert "productSearch" in parse_network_request(root_event).classifications


def test_facets_request_is_identified(facets_event: dict):
    assert "facets" in parse_network_request(facets_event).classifications


def test_graphql_operation_name_is_extracted(root_event: dict):
    assert parse_network_request(root_event).operation_name == "productSearchV3"


def test_graphql_from_to_are_extracted(root_event: dict):
    fields = structural_fields(parse_network_request(root_event))
    assert fields["from"] == 0
    assert fields["to"] == 4


def test_graphql_selected_facets_are_extracted(root_event: dict):
    fields = structural_fields(parse_network_request(root_event))
    assert fields["selectedFacets"] == [{"key": "category-1", "value": "supermercado"}]


def test_get_graphql_variables_are_extracted_from_real_url_shape():
    variables = {
        "from": 7,
        "to": 11,
        "selectedFacets": [{"key": "category-1", "value": "supermercado"}],
    }
    event = {
        "url": "https://synthetic.invalid/graphql?" + urlencode({
            "operationName": "productSearchV3",
            "query": "query productSearchV3 { productSearch { products { productId } } }",
            "variables": json.dumps(variables),
        }),
        "method": "GET", "resource_type": "xhr", "headers": {},
    }
    metadata = parse_network_request(event)
    assert metadata.operation_name == "productSearchV3"
    assert metadata.variables["from"] == 7
    assert metadata.variables["to"] == 11


def test_minimal_replay_preserves_request_and_caps_to_five(root_event: dict):
    replay = build_minimal_graphql_replay(root_event, max_results=5)
    assert replay["method"] == root_event["method"]
    assert replay["url"] == root_event["url"]
    assert replay["post_data"]["operationName"] == "productSearchV3"
    variables = replay["post_data"]["variables"]
    assert variables["from"] == 0
    assert variables["to"] == 4
    assert variables["selectedFacets"] == [{"key": "category-1", "value": "supermercado"}]


def test_root_response_is_summarized_without_full_payload(root_event: dict):
    summary = summarize_response(root_event)
    assert summary["recordsFiltered"] == 123
    assert summary["sampling"] is False
    assert summary["products_returned"] == 2
    assert summary["skus_returned"] == 2
    rendered = json.dumps(summary)
    assert "synthetic-product" not in rendered
    assert "synthetic-sku" not in rendered
    assert "productId" not in rendered
    assert "itemId" not in rendered
    for forbidden in ("sellerId", "Price", "ListPrice", "AvailableQuantity"):
        assert forbidden not in rendered


@pytest.mark.parametrize("field", ["Price", "price", "PRODUCTID", "product_id", "item-id"])
def test_public_report_rejects_commercial_fields_before_persistence(field):
    report = SpsContextDiagnostic(root={field: 1})
    with pytest.raises(DiagnosticSafetyError, match="Campo comercial prohibido"):
        report.sanitized_dict()


def test_facets_response_is_classified(facets_event: dict):
    summary = summarize_response(facets_event)
    classes = {item["classification"] for item in summary["facets"]}
    assert {"department", "category", "subcategory", "brand", "specification"} <= classes
    assert summary["sampling"] is False
    assert all("key" not in item and "values" not in item for item in summary["facets"])


@pytest.mark.parametrize(
    ("key", "name", "expected"),
    [
        ("Landing", "Landing", "landing"),
        ("productClusterIds", "Colección", "collection"),
        ("Impuestos", "Impuestos", "tax"),
        ("promotion", "Oferta", "promotion"),
        ("unknown", "Otro", "other"),
    ],
)
def test_other_facet_classifications(key: str, name: str, expected: str):
    assert classify_facet(key, name) == expected


def test_cookie_values_are_always_redacted(diagnostic_fixture: dict):
    context = diagnostic_fixture["context"]
    observations = compare_storage(context["cookies_before"], context["cookies_after"], storage_type="cookie")
    vtex = next(item for item in observations if item["name"] == "vtex_session")
    assert vtex["value"] == "redacted"
    assert vtex["changed_after_store_selection"] is True
    assert len(vtex["sha256"]) == 64


def test_authorization_header_is_redacted(root_event: dict):
    assert parse_network_request(root_event).headers["Authorization"] == "redacted"


def test_token_fields_are_redacted(root_event: dict):
    metadata = parse_network_request(root_event)
    assert metadata.variables["sessionId"] == "redacted"
    assert "fake-token" not in metadata.url
    assert "redacted" in metadata.url


def test_generic_sanitizer_redacts_nested_tokens():
    sanitized = sanitize_mapping({"safe": 1, "access_token": "secret", "nested": {"jwt": "secret2"}})
    assert sanitized == {"safe": 1, "access_token": "redacted", "nested": {"jwt": "redacted"}}


def test_headers_sanitize_cookie_and_authorization():
    assert sanitize_headers({
        "Authorization": "Bearer secret", "Cookie": "secret-cookie", "Accept": "application/json",
    }) == {"Authorization": "redacted", "Cookie": "redacted", "Accept": "application/json"}


def test_order_form_id_never_appears_in_offline_report(dom_html: str, diagnostic_fixture: dict):
    report = run_offline_fixture(html=dom_html, fixture=diagnostic_fixture)
    serialized = json.dumps(report.sanitized_dict(), ensure_ascii=False)
    assert "synthetic-order-form-before" not in serialized
    assert "synthetic-order-form-after" not in serialized
    assert "orderFormId" in serialized
    assert "redacted" in serialized


def test_storage_change_is_detected(diagnostic_fixture: dict):
    context = diagnostic_fixture["context"]
    observations = compare_storage(
        context["local_storage_before"], context["local_storage_after"], storage_type="localStorage"
    )
    region = next(item for item in observations if item["name"] == "regionId")
    assert region["observed"] is True
    assert region["changed_after_store_selection"] is True
    assert region["value"] == "redacted"


def test_vtex_context_observed_is_evidence_not_expectation(dom_html: str, diagnostic_fixture: dict):
    report = run_offline_fixture(html=dom_html, fixture=diagnostic_fixture)
    mechanisms = {
        item["name"]: item["observed"]
        for item in report.context_evidence
        if item.get("kind") == "vtex_context_mechanism"
    }
    assert mechanisms["vtex_session"] is True
    assert mechanisms["vtex_segment"] is True
    assert mechanisms["regionId"] is True
    assert mechanisms["salesChannel"] is True
    assert mechanisms["country"] is False


def test_logical_requests_are_counted(dom_html: str, diagnostic_fixture: dict):
    assert run_offline_fixture(html=dom_html, fixture=diagnostic_fixture).logical_requests == 7


def test_budget_stops_at_limit():
    counter = LogicalRequestCounter(DiagnosticBudget(max_logical_requests=2))
    current = [0.0]
    def clock() -> float:
        return current[0]
    def reserve(label: str) -> None:
        counter.reserve(label, clock=clock, sleeper=lambda _: None)
        current[0] += 1.5
    reserve("one")
    reserve("two")
    with pytest.raises(LogicalRequestBudgetExceeded):
        reserve("three")


def test_concurrency_must_be_one():
    with pytest.raises(ValueError, match="concurrency"):
        DiagnosticBudget(concurrency=2)


def test_minimum_delay_is_enforced():
    current = [0.0]
    slept: list[float] = []
    counter = LogicalRequestCounter(DiagnosticBudget())
    def clock() -> float:
        return current[0]
    def sleeper(seconds: float) -> None:
        slept.append(seconds)
        current[0] += seconds
    counter.reserve("first", clock=clock, sleeper=sleeper)
    counter.reserve("second", clock=clock, sleeper=sleeper)
    assert slept == [pytest.approx(1.5)]
    assert counter.count == 2


def test_retry_budget_is_closed():
    with pytest.raises(ValueError, match="max_retries"):
        DiagnosticBudget(max_retries=2)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf"), True])
def test_diagnostic_budget_rejects_nonfinite_or_boolean_delay(invalid):
    with pytest.raises(ValueError, match="minimum_delay_seconds"):
        DiagnosticBudget(minimum_delay_seconds=invalid)


def test_counter_rejects_when_sleeper_does_not_advance_physical_clock():
    counter = LogicalRequestCounter(DiagnosticBudget())
    counter.reserve("first", clock=lambda: 0.0, sleeper=lambda _: None)
    with pytest.raises(DiagnosticSafetyError, match="pacing físico no observado"):
        counter.reserve("second", clock=lambda: 0.0, sleeper=lambda _: None)
    assert counter.count == 1
    assert counter.labels == ["first"]


def test_counter_budget_is_atomic_under_concurrent_reservations():
    counter = LogicalRequestCounter(DiagnosticBudget(max_logical_requests=8))
    clock_value = [0.0]
    clock_lock = threading.Lock()
    outcomes: list[str] = []

    def clock() -> float:
        with clock_lock:
            return clock_value[0]

    def sleeper(seconds: float) -> None:
        with clock_lock:
            clock_value[0] += seconds

    def reserve_one(index: int) -> None:
        try:
            counter.reserve(f"request-{index}", clock=clock, sleeper=sleeper)
            outcomes.append("accepted")
        except LogicalRequestBudgetExceeded:
            outcomes.append("denied")

    threads = [threading.Thread(target=reserve_one, args=(index,)) for index in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert outcomes.count("accepted") == 8
    assert outcomes.count("denied") == 8
    assert counter.count == 8


def test_live_mode_requires_authorization_id():
    with pytest.raises(DiagnosticSafetyError, match="authorization-id"):
        validate_live_authorization(live=True, authorization_id=None)


def test_consumed_authorization_id_is_rejected():
    with pytest.raises(DiagnosticSafetyError, match="ya consumido"):
        validate_live_authorization(live=True, authorization_id="SPS-context-and-root-facets-001")


def test_offline_mode_rejects_authorization_id():
    with pytest.raises(DiagnosticSafetyError):
        validate_live_authorization(live=False, authorization_id="SPS-context-and-root-facets-999")


def test_offline_fixture_never_opens_socket(monkeypatch: pytest.MonkeyPatch, dom_html: str, diagnostic_fixture: dict):
    calls: list[str] = []
    def fail_connect(*args, **kwargs):
        calls.append("socket")
        raise AssertionError("network access attempted from offline_fixture")
    monkeypatch.setattr(socket.socket, "connect", fail_connect)
    monkeypatch.setattr(socket, "create_connection", fail_connect)
    report = run_offline_fixture(html=dom_html, fixture=diagnostic_fixture)
    assert report.mode == "offline_fixture"
    assert report.browser == "not_started"
    assert calls == []


def test_playwright_import_is_lazy_inside_live_function_only():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    top_level_playwright_imports = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = getattr(node, "module", "") or ""
            names = [alias.name for alias in getattr(node, "names", [])]
            if "playwright" in module or any(name.startswith("playwright") for name in names):
                top_level_playwright_imports.append(node)
    assert top_level_playwright_imports == []


def test_offline_report_persistence_is_sanitized(tmp_path: Path, dom_html: str, diagnostic_fixture: dict):
    output = tmp_path / "diagnostic.json"
    report = run_offline_fixture(html=dom_html, fixture=diagnostic_fixture, output_path=output)
    text = output.read_text(encoding="utf-8")
    assert report.root["recordsFiltered"] == 123
    assert report.facets["sampling"] is False
    for secret in (
        "synthetic-session-before", "synthetic-session-after", "synthetic-jwt-token",
        "synthetic-order-form-before", "synthetic-order-form-after",
        "synthetic-token-before", "synthetic-token-after",
    ):
        assert secret not in text
    assert '"value": "redacted"' in text


def test_url_sanitizer_preserves_structural_query_and_redacts_opaque_values():
    url = "https://synthetic.invalid/graphql?operationName=productSearchV3&workspace=master&token=secret"
    sanitized = sanitize_url(url)
    assert "operationName=productSearchV3" in sanitized
    assert "workspace=master" in sanitized
    assert "secret" not in sanitized
    assert "token=redacted" in sanitized


def test_classify_graphql_unknown_is_other():
    assert classify_graphql("Unrelated", "query Unrelated { ping }") == ("other",)
