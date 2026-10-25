"""Diagnóstico offline-first del contexto SPS de La Colonia.

No toca contratos comerciales ni el scraper. ``offline_fixture`` no abre red.
``run_live`` exige --live + --authorization-id y una allow-list activa explícita.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TARGET_URL = "https://www.lacolonia.com/"
ACTIVE_AUTHORIZATION_IDS: frozenset[str] = frozenset()
CONSUMED_AUTHORIZATION_IDS = frozenset({"SPS-context-and-root-facets-001"})
AUTHORIZATION_PATTERN = re.compile(r"^SPS-context-and-root-facets-\d{3}$")
VTEX_CONTEXT_NAMES = (
    "vtex_session", "vtex_segment", "regionId", "salesChannel", "binding",
    "postalCode", "country", "pickupPoint", "store", "seller",
)
SENSITIVE_PARTS = (
    "authorization", "cookie", "token", "jwt", "secret", "password",
    "apikey", "api_key", "sessionid", "session_id", "orderform", "address",
    "coordinate", "latitude", "longitude", "postalcode", "postal_code",
    "email", "phone",
)
_ALLOWED_LOCAL_SCHEMES = frozenset({"about", "data", "file"})
_ALLOWED_SYNTHETIC_HOST = "synthetic.invalid"
_ALLOWED_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_BROWSER_CANDIDATES = (
    "google-chrome-stable", "google-chrome", "chromium", "chromium-browser",
)


class DiagnosticSafetyError(RuntimeError):
    pass


class DomTargetNotFound(LookupError):
    pass


class AmbiguousDomTarget(LookupError):
    pass


class LogicalRequestBudgetExceeded(DiagnosticSafetyError):
    pass


@dataclass(frozen=True, slots=True)
class DiagnosticBudget:
    max_logical_requests: int = 8
    concurrency: int = 1
    minimum_delay_seconds: float = 1.5
    max_retries: int = 0

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_logical_requests, bool)
            or type(self.max_logical_requests) is not int
            or not 1 <= self.max_logical_requests <= 8
        ):
            raise ValueError("max_logical_requests debe estar entre 1 y 8")
        if isinstance(self.concurrency, bool) or type(self.concurrency) is not int or self.concurrency != 1:
            raise ValueError("concurrency debe ser exactamente 1")
        if (
            isinstance(self.minimum_delay_seconds, bool)
            or not isinstance(self.minimum_delay_seconds, (int, float))
            or not math.isfinite(float(self.minimum_delay_seconds))
            or self.minimum_delay_seconds < 1.5
        ):
            raise ValueError("minimum_delay_seconds debe ser >= 1.5")
        if isinstance(self.max_retries, bool) or type(self.max_retries) is not int or self.max_retries != 0:
            raise ValueError("max_retries debe ser 0")


@dataclass(slots=True)
class LogicalRequestCounter:
    config: DiagnosticBudget
    count: int = 0
    labels: list[str] = field(default_factory=list)
    last_started_at: float | None = None
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def reserve(
        self,
        label: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> int:
        if not isinstance(label, str) or not label.strip():
            raise DiagnosticSafetyError("label de request inválido")
        with self._lock:
            if self.count >= self.config.max_logical_requests:
                raise LogicalRequestBudgetExceeded("presupuesto lógico agotado")
            now = clock()
            if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(float(now)):
                raise DiagnosticSafetyError("reloj no finito")
            if self.last_started_at is not None:
                elapsed = now - self.last_started_at
                if elapsed < 0:
                    raise DiagnosticSafetyError("reloj no monotónico")
                remaining = self.config.minimum_delay_seconds - elapsed
                if remaining > 0:
                    sleeper(remaining)
                    now = clock()
                    if (
                        isinstance(now, bool)
                        or not isinstance(now, (int, float))
                        or not math.isfinite(float(now))
                        or now - self.last_started_at < self.config.minimum_delay_seconds
                    ):
                        raise DiagnosticSafetyError("pacing físico no observado después de sleep")
            self.count += 1
            self.labels.append(label.strip())
            self.last_started_at = float(now)
            return self.count


@dataclass(frozen=True, slots=True)
class DomCandidate:
    tag: str
    role: str
    accessible_name: str
    text: str
    tier: int


@dataclass(frozen=True, slots=True)
class NetworkMetadata:
    url: str
    method: str
    resource_type: str
    operation_name: str | None
    classifications: tuple[str, ...]
    variables: Mapping[str, Any]
    headers: Mapping[str, Any]
    status: int | None = None
    content_type: str | None = None


@dataclass(slots=True)
class SpsContextDiagnostic:
    test_id: str = "la_colonia_sps_context_diagnostic"
    started_at: str | None = None
    completed_at: str | None = None
    mode: str = "offline_fixture"
    browser: str = "not_started"
    initial_location_text: str | None = None
    selected_city: str | None = None
    selected_store: str | None = None
    location_status: str = "fixture_only"
    context_evidence: list[Mapping[str, Any]] = field(default_factory=list)
    cookies_observed: list[Mapping[str, Any]] = field(default_factory=list)
    local_storage_observed: list[Mapping[str, Any]] = field(default_factory=list)
    session_storage_observed: list[Mapping[str, Any]] = field(default_factory=list)
    requests: list[Mapping[str, Any]] = field(default_factory=list)
    root: Mapping[str, Any] | None = None
    facets: Mapping[str, Any] | None = None
    stability: Mapping[str, Any] = field(default_factory=dict)
    redactions: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stop_reason: str | None = None
    logical_requests: int = 0
    authorization_checked: bool = False
    browser_started: bool = False
    target_navigation_started: bool = False
    target_navigation_completed: bool = False
    store_selector_opened: bool = False
    city_selected: bool = False
    store_selected: bool = False
    context_observed: bool = False
    root_observed: bool = False
    facets_observed: bool = False
    authorization_consumption_eligible: bool = False
    context_replay_verification: str = "pending_live"

    def sanitized_dict(self) -> dict[str, Any]:
        value = asdict(self)
        _validate_public_report(value)
        return value


@dataclass(slots=True)
class _Node:
    tag: str
    attrs: dict[str, str]
    text: list[str] = field(default_factory=list)


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[_Node] = []
        self.nodes: list[_Node] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.stack.append(_Node(tag.lower(), {k.lower(): v or "" for k, v in attrs}))

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if value:
            for node in self.stack:
                node.text.append(value)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index].tag == tag.lower():
                self.nodes.append(self.stack.pop(index))
                return

    def close(self) -> None:
        while self.stack:
            self.nodes.append(self.stack.pop())
        super().close()


def _norm(value: str) -> str:
    return " ".join(value.split()).strip()


def _pattern(label: str) -> re.Pattern[str]:
    words = [re.escape(word) for word in _norm(label).split()]
    return re.compile(r"\b" + r"\s+".join(words) + r"\b", re.I)


def find_unique_dom_target(html: str, label: str) -> DomCandidate:
    parser = _Parser()
    parser.feed(html)
    parser.close()
    pattern = _pattern(label)
    found: list[DomCandidate] = []
    for node in parser.nodes:
        text = _norm(" ".join(node.text))
        role = node.attrs.get("role", "").lower()
        accessible = _norm(node.attrs.get("aria-label", "") or node.attrs.get("title", ""))
        semantic = " ".join(
            node.attrs.get(key, "")
            for key in ("aria-label", "title", "name", "id", "data-testid", "data-test", "placeholder")
        )
        tier: int | None = None
        if role and pattern.search(accessible):
            tier = 1
        elif pattern.search(accessible):
            tier = 2
        elif node.tag == "label" and pattern.fullmatch(text):
            tier = 3
        elif pattern.fullmatch(text):
            tier = 4
        elif pattern.search(semantic):
            tier = 5
        elif node.tag == "select" and pattern.search(text):
            tier = 6
        elif node.tag == "button" and pattern.search(text):
            tier = 7
        elif role == "combobox" and pattern.search(text + " " + accessible):
            tier = 8
        elif role == "dialog" and pattern.search(text):
            tier = 9
        elif node.tag in {"div", "span", "a", "li", "option"} and pattern.search(text):
            tier = 10
        if tier is not None:
            found.append(DomCandidate(node.tag, role, accessible, text, tier))
    if not found:
        raise DomTargetNotFound(label)
    best_tier = min(item.tier for item in found)
    best = [item for item in found if item.tier == best_tier]
    if len(best) != 1:
        raise AmbiguousDomTarget(f"{label}: {len(best)} coincidencias")
    return best[0]


def _selector_plan(label: str, role: str) -> tuple[Mapping[str, str], ...]:
    return (
        {"kind": "role", "role": role, "name": label},
        {"kind": "accessible_name", "name": label},
        {"kind": "label", "name": label},
        {"kind": "text", "name": label},
        {"kind": "semantic_attribute", "name": label},
        {"kind": "select", "name": label},
        {"kind": "button", "name": label},
        {"kind": "combobox", "name": label},
        {"kind": "dialog", "name": label},
        {"kind": "structural_fallback", "name": label},
    )


def store_selector_plan() -> tuple[Mapping[str, str], ...]:
    return _selector_plan("Selecciona tu tienda", "button")


def city_selector_plan() -> tuple[Mapping[str, str], ...]:
    return _selector_plan("San Pedro Sula", "option")


def store_option_plan() -> tuple[Mapping[str, str], ...]:
    return _selector_plan("Plaza Pedregal", "option")


def _sensitive(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9_]", "", key.lower())
    return any(part in normalized for part in SENSITIVE_PARTS)


def _fingerprint(value: Any) -> tuple[int, str]:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    raw = text.encode()
    return len(raw), hashlib.sha256(raw).hexdigest()


def sanitize_mapping(value: Any, *, key_name: str = "") -> Any:
    if key_name and _sensitive(key_name):
        return "redacted"
    if isinstance(value, Mapping):
        return {str(k): sanitize_mapping(v, key_name=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_mapping(item) for item in value]
    return value


def sanitize_headers(headers: Mapping[str, Any]) -> dict[str, Any]:
    return {str(k): ("redacted" if _sensitive(str(k)) else v) for k, v in headers.items()}


def sanitize_url(url: str) -> str:
    parts = urlsplit(url)
    allowed = {"operationName", "workspace", "locale", "domain", "map", "from", "to", "orderBy"}
    query = [(k, v if k in allowed else "redacted") for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def sanitize_error(error: BaseException | str) -> str:
    """Sanitiza excepciones antes de incorporarlas al artefacto diagnóstico."""
    text = str(error)
    text = re.sub(r"-?\d{1,3}\.\d{4,}\s*,\s*-?\d{1,3}\.\d{4,}", "redacted_coordinates", text)
    text = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer redacted", text)
    text = re.sub(
        r"(?i)\b(authorization|cookie|token|jwt|secret|password|api[_-]?key|session(?:id|_id)?|orderform(?:id)?|address|coordinates?|latitude|longitude|postal(?:code|_code)?|email|phone)\b\s*[:=]\s*([^\s,;&]+)",
        lambda m: f"{m.group(1)}=redacted",
        text,
    )
    text = re.sub(r"\b[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\b", "redacted", text)
    url_pattern = re.compile(r"https?://[^\s'\"<>]+")
    text = url_pattern.sub(lambda m: sanitize_url(m.group(0)), text)
    return text[:2000]


def compare_storage(before: Mapping[str, Any], after: Mapping[str, Any], *, storage_type: str) -> list[dict[str, Any]]:
    result = []
    for name in sorted(set(before) | set(after)):
        value = after.get(name, before.get(name))
        observed = value is not None
        length, digest = _fingerprint(value if observed else "")
        result.append({
            "name": name,
            "storage_type": storage_type,
            "observed": observed,
            "value": "redacted" if observed else "",
            "length": length,
            "sha256": digest,
            "changed_after_store_selection": before.get(name) != after.get(name),
        })
    return result


def classify_graphql(operation_name: str | None, query: str) -> tuple[str, ...]:
    op, body = (operation_name or "").lower(), query.lower()
    kinds: list[str] = []
    tests = (
        ("productSearch", "productsearch"), ("facets", "facet"),
        ("session", "session"), ("segment", "segment"),
        ("region", "region"), ("store", "store"), ("checkout", "checkout"),
    )
    for output, needle in tests:
        if needle in op or needle in body:
            kinds.append(output)
    if "orderform" in body and "checkout" not in kinds:
        kinds.append("checkout")
    return tuple(dict.fromkeys(kinds or ["other"]))


def _json_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return loaded if isinstance(loaded, Mapping) else {}
    return {}


def _envelope(event: Mapping[str, Any]) -> tuple[str | None, str, Mapping[str, Any]]:
    payload = _json_mapping(event.get("post_data"))
    params = dict(parse_qsl(urlsplit(str(event.get("url", ""))).query, keep_blank_values=True))
    operation = event.get("operation_name") or payload.get("operationName") or params.get("operationName")
    query = str(payload.get("query") or event.get("query") or params.get("query") or "")
    variables: Any = payload.get("variables") or event.get("variables")
    if not isinstance(variables, Mapping) and params.get("variables"):
        try:
            variables = json.loads(params["variables"])
        except json.JSONDecodeError:
            variables = {}
    return (str(operation) if operation else None, query, dict(variables) if isinstance(variables, Mapping) else {})


def parse_network_request(event: Mapping[str, Any]) -> NetworkMetadata:
    operation, query, variables = _envelope(event)
    return NetworkMetadata(
        url=sanitize_url(str(event.get("url", ""))),
        method=str(event.get("method", "GET")).upper(),
        resource_type=str(event.get("resource_type", "")),
        operation_name=operation,
        classifications=classify_graphql(operation, query),
        variables=sanitize_mapping(variables),
        headers=sanitize_headers(event.get("headers") if isinstance(event.get("headers"), Mapping) else {}),
        status=int(event["status"]) if event.get("status") is not None else None,
        content_type=str(event["content_type"]) if event.get("content_type") is not None else None,
    )


def structural_fields(metadata: NetworkMetadata) -> dict[str, Any]:
    v = metadata.variables
    return {
        "endpoint": metadata.url, "method": metadata.method,
        "operationName": metadata.operation_name,
        "classifications": list(metadata.classifications),
        "from": v.get("from"), "to": v.get("to"), "orderBy": v.get("orderBy"),
        "selectedFacets": v.get("selectedFacets"), "map": v.get("map"),
        "status": metadata.status, "content_type": metadata.content_type,
    }


def build_minimal_graphql_replay(event: Mapping[str, Any], *, max_results: int = 5) -> dict[str, Any]:
    if not 1 <= max_results <= 5:
        raise ValueError("max_results debe estar entre 1 y 5")
    operation, query, variables = _envelope(event)
    if not operation and "graphql" not in str(event.get("url", "")).lower():
        raise ValueError("request no GraphQL")
    variables = dict(variables)
    variables["from"], variables["to"] = 0, max_results - 1
    method, raw_url = str(event.get("method", "GET")).upper(), str(event.get("url", ""))
    if method == "GET":
        parts = urlsplit(raw_url)
        pairs, replaced = [], False
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            if key == "variables":
                value, replaced = json.dumps(variables, separators=(",", ":")), True
            pairs.append((key, value))
        if not replaced:
            pairs.append(("variables", json.dumps(variables, separators=(",", ":"))))
        return {"method": "GET", "url": urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(pairs), "")), "post_data": None}
    payload = dict(_json_mapping(event.get("post_data")))
    payload.update({"operationName": operation, "variables": variables})
    if query:
        payload["query"] = query
    return {"method": method, "url": raw_url, "post_data": payload}


def classify_facet(key: str, name: str = "") -> str:
    value = f"{key} {name}".lower()
    if "category-1" in value or "department" in value:
        return "department"
    if "category-2" in value:
        return "category"
    if "category-3" in value:
        return "subcategory"
    if "brand" in value or "marca" in value:
        return "brand"
    if "landing" in value:
        return "landing"
    if "productcluster" in value or "collection" in value:
        return "collection"
    if "impuesto" in value or "tax" in value:
        return "tax"
    if "subcategoria" in value:
        return "specification"
    if "promo" in value or "oferta" in value:
        return "promotion"
    return "other"


def summarize_response(event: Mapping[str, Any], metadata: NetworkMetadata | None = None) -> dict[str, Any]:
    metadata = metadata or parse_network_request(event)
    payload, result = _json_mapping(event.get("response_body")), {
        "operationName": metadata.operation_name,
        "classifications": list(metadata.classifications),
        "status": metadata.status,
        "content_type": metadata.content_type,
    }
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return result
    search = data.get("productSearch")
    search = search if isinstance(search, Mapping) else {}
    if "productSearch" in metadata.classifications:
        result["recordsFiltered"], result["sampling"] = search.get("recordsFiltered"), search.get("sampling")
        products = search.get("products")
        materialized = [p for p in (products or []) if isinstance(p, Mapping)]
        items = [
            item
            for product in materialized
            for item in (product.get("items") or [])
            if isinstance(item, Mapping)
        ]
        result["products_returned"] = len(materialized)
        result["skus_returned"] = len(items)
        result["sellers_returned"] = sum(
            len([seller for seller in (item.get("sellers") or []) if isinstance(seller, Mapping)])
            for item in items
        )
    facets = search.get("facets") or data.get("facets")
    if "facets" in metadata.classifications and isinstance(facets, Sequence) and not isinstance(facets, (str, bytes)):
        result["sampling"] = search.get("sampling", data.get("sampling"))
        result["facets"] = []
        for facet in facets:
            if not isinstance(facet, Mapping):
                continue
            raw_values = facet.get("values")
            values = (
                raw_values
                if isinstance(raw_values, Sequence)
                and not isinstance(raw_values, (str, bytes))
                else []
            )
            result["facets"].append({
                "type": facet.get("type"),
                "classification": classify_facet(str(facet.get("key") or ""), str(facet.get("name") or "")),
                "values_returned": len(values),
                "positive_values": sum(
                    type(v.get("quantity")) is int and v.get("quantity") > 0
                    for v in values if isinstance(v, Mapping)
                ),
            })
    return result


_FORBIDDEN_REPORT_KEYS = frozenset({
    "productid", "itemid", "sellerid", "price", "listprice",
    "availablequantity", "productname", "linktext", "ean",
})


def _validate_public_report(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized_key = "".join(character for character in str(key).casefold() if character.isalnum())
            if normalized_key in _FORBIDDEN_REPORT_KEYS:
                raise DiagnosticSafetyError(f"Campo comercial prohibido en reporte: {key}")
            _validate_public_report(nested)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for nested in value:
            _validate_public_report(nested)


def validate_live_authorization(
    *,
    live: bool,
    authorization_id: str | None,
    active_ids: Iterable[str] = ACTIVE_AUTHORIZATION_IDS,
    consumed_ids: Iterable[str] = CONSUMED_AUTHORIZATION_IDS,
) -> str:
    if not live:
        if authorization_id:
            raise DiagnosticSafetyError("authorization-id no se acepta en offline_fixture")
        return "offline_fixture"
    if not authorization_id:
        raise DiagnosticSafetyError("--live requiere --authorization-id")
    if not AUTHORIZATION_PATTERN.fullmatch(authorization_id):
        raise DiagnosticSafetyError("authorization-id fuera de formato")
    consumed = set(consumed_ids)
    if authorization_id in consumed:
        raise DiagnosticSafetyError(f"authorization-id ya consumido: {authorization_id}")
    if authorization_id not in set(active_ids):
        raise DiagnosticSafetyError(f"authorization-id no autorizado: {authorization_id}")
    return "live"


def _persist_report(report: SpsContextDiagnostic, output_path: Path | None) -> None:
    if output_path is None:
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report.sanitized_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_offline_fixture(*, html: str, fixture: Mapping[str, Any], output_path: Path | None = None, budget: DiagnosticBudget | None = None) -> SpsContextDiagnostic:
    budget, report = budget or DiagnosticBudget(), SpsContextDiagnostic(
        started_at="fixture", completed_at="fixture", initial_location_text="Selecciona tu tienda"
    )
    counter = LogicalRequestCounter(budget)
    for label in ("Selecciona tu tienda", "San Pedro Sula", "Plaza Pedregal"):
        candidate = find_unique_dom_target(html, label)
        report.context_evidence.append({"kind": "dom", "label": label, "tier": candidate.tier})
    report.selected_city, report.selected_store = "San Pedro Sula", "Plaza Pedregal"
    context = fixture.get("context")
    if not isinstance(context, Mapping):
        raise ValueError("fixture.context inválido")
    groups = (("cookies", "cookie"), ("local_storage", "localStorage"), ("session_storage", "sessionStorage"))
    observations = {}
    for prefix, storage_type in groups:
        before, after = context.get(prefix + "_before"), context.get(prefix + "_after")
        if not isinstance(before, Mapping) or not isinstance(after, Mapping):
            raise ValueError(prefix)
        observations[prefix] = compare_storage(before, after, storage_type=storage_type)
    report.cookies_observed = observations["cookies"]
    report.local_storage_observed = observations["local_storage"]
    report.session_storage_observed = observations["session_storage"]
    all_obs = report.cookies_observed + report.local_storage_observed + report.session_storage_observed
    for name in VTEX_CONTEXT_NAMES:
        matches = [item for item in all_obs if str(item["name"]).lower() == name.lower()]
        report.context_evidence.append({
            "kind": "vtex_context_mechanism", "name": name, "observed": bool(matches),
            "changed_after_store_selection": any(item["changed_after_store_selection"] for item in matches),
        })
    events = fixture.get("network")
    if not isinstance(events, Sequence) or isinstance(events, (str, bytes)):
        raise ValueError("fixture.network inválido")
    pairs = [(event, parse_network_request(event)) for event in events if isinstance(event, Mapping)]
    report.requests = [structural_fields(metadata) for _, metadata in pairs]
    roots = [(event, meta) for event, meta in pairs if "productSearch" in meta.classifications]
    facets = [(event, meta) for event, meta in pairs if "facets" in meta.classifications]
    if roots:
        report.root = {**structural_fields(roots[0][1]), **summarize_response(*roots[0])}
    if facets:
        report.facets = {**structural_fields(facets[0][1]), **summarize_response(*facets[0])}
    now = [0.0]
    def clock() -> float:
        return now[0]
    for label in fixture.get("logical_requests", []):
        counter.reserve(str(label), clock=clock, sleeper=lambda _: None)
        now[0] += budget.minimum_delay_seconds
    report.logical_requests = counter.count
    report.redactions = ["cookies", "authorization", "tokens", "orderForm IDs", "session IDs", "addresses", "coordinates", "personal data", "JWT", "API keys"]
    _persist_report(report, output_path)
    return report


def _pw_locator(page: Any, strategy: Mapping[str, str]) -> Any:
    kind, name = strategy["kind"], strategy["name"]
    pattern = re.compile(re.escape(name), re.I)
    if kind == "role":
        return page.get_by_role(strategy["role"], name=pattern)
    if kind == "accessible_name":
        return page.locator(f'[aria-label*="{name}" i], [title*="{name}" i]')
    if kind == "label":
        return page.get_by_label(pattern)
    if kind == "text":
        return page.get_by_text(pattern, exact=False)
    if kind == "semantic_attribute":
        return page.locator(f'[data-testid*="{name}" i], [data-test*="{name}" i], [name*="{name}" i], [placeholder*="{name}" i]')
    if kind == "select":
        return page.locator("select").filter(has_text=pattern)
    if kind == "button":
        return page.locator("button").filter(has_text=pattern)
    if kind == "combobox":
        return page.get_by_role("combobox", name=pattern)
    if kind == "dialog":
        return page.get_by_role("dialog").filter(has_text=pattern)
    return page.locator("body").get_by_text(pattern, exact=False)


def _pw_unique(page: Any, plan: Sequence[Mapping[str, str]], label: str) -> Any:
    for strategy in plan:
        locator = _pw_locator(page, strategy)
        count = locator.count()
        if count == 1:
            return locator
        if count > 1:
            raise AmbiguousDomTarget(label)
    raise DomTargetNotFound(label)


def _pw_activate(locator: Any, label: str) -> None:
    tag = locator.evaluate("(el) => el.tagName.toLowerCase()")
    if tag == "select":
        locator.select_option(label=label)
    elif tag == "option":
        parent = locator.locator("xpath=ancestor::select[1]")
        if parent.count() != 1:
            raise AmbiguousDomTarget(label)
        parent.select_option(label=label)
    else:
        locator.click()


def _storage(page: Any) -> tuple[dict[str, str], dict[str, str]]:
    return (
        dict(page.evaluate("() => Object.fromEntries(Object.entries(localStorage))") or {}),
        dict(page.evaluate("() => Object.fromEntries(Object.entries(sessionStorage))") or {}),
    )


def _cookies(context: Any) -> dict[str, str]:
    return {str(item["name"]): str(item["value"]) for item in context.cookies() if item.get("name")}


def _raw_request(request: Any) -> dict[str, Any]:
    try:
        post_data = request.post_data_json
    except Exception:
        post_data = request.post_data
    return {
        "url": request.url,
        "method": request.method,
        "resource_type": request.resource_type,
        "headers": dict(request.headers),
        "post_data": post_data,
    }


def _safe_replay_headers(headers: Mapping[str, Any]) -> dict[str, str]:
    allowed = {"accept", "accept-language", "content-type", "x-vtex-locale"}
    return {
        str(key): str(value)
        for key, value in headers.items()
        if str(key).lower() in allowed and not _sensitive(str(key))
    }


def _execute_replay(
    context: Any,
    raw_event: Mapping[str, Any],
    counter: LogicalRequestCounter,
    label: str,
    *,
    network_policy: str,
) -> dict[str, Any]:
    replay = build_minimal_graphql_replay(raw_event, max_results=5)
    parts = urlsplit(replay["url"])
    host = (parts.hostname or "").lower()
    if network_policy != "local_only" or not (
        parts.scheme.lower() in _ALLOWED_LOCAL_SCHEMES
        or host in _ALLOWED_LOCAL_HOSTS
        or host == _ALLOWED_SYNTHETIC_HOST
    ):
        raise DiagnosticSafetyError("Replay externo bloqueado antes de red")
    counter.reserve(label)
    headers = _safe_replay_headers(raw_event.get("headers") if isinstance(raw_event.get("headers"), Mapping) else {})
    if replay["method"] == "GET":
        response = context.request.get(replay["url"], headers=headers)
    else:
        response = context.request.fetch(replay["url"], method=replay["method"], headers=headers, data=replay["post_data"])
    response_headers = dict(response.headers)
    content_type = response_headers.get("content-type")
    if not 200 <= int(response.status) < 300:
        raise DiagnosticSafetyError(f"HTTP inesperado {response.status} en {sanitize_url(replay['url'])}")
    if not content_type or "json" not in content_type.lower():
        raise DiagnosticSafetyError("respuesta GraphQL sin content-type JSON")
    try:
        body = response.json()
    except Exception as exc:
        raise DiagnosticSafetyError("respuesta GraphQL JSON inválida") from exc
    if not isinstance(body, Mapping):
        raise DiagnosticSafetyError("respuesta GraphQL JSON inválida")
    return {
        "url": replay["url"], "method": replay["method"], "resource_type": "fetch",
        "headers": headers, "post_data": replay["post_data"], "status": response.status,
        "content_type": content_type, "response_body": body,
    }


def _shape(event: Mapping[str, Any]) -> dict[str, Any]:
    return structural_fields(parse_network_request(event))


def _stop_reason(error: BaseException, report: SpsContextDiagnostic) -> str:
    message = str(error).lower()
    if isinstance(error, DomTargetNotFound):
        return "dom_target_not_found"
    if isinstance(error, AmbiguousDomTarget):
        return "ambiguous_dom_target"
    if isinstance(error, LogicalRequestBudgetExceeded):
        return "logical_request_budget_exceeded"
    if "productsearch" in message and "observ" in message:
        return "product_search_not_observed"
    if "facets" in message and "observ" in message:
        return "facets_not_observed"
    if "http inesperado" in message:
        return "unexpected_http_status"
    if "json" in message:
        return "invalid_json_response"
    if error.__class__.__name__ == "TimeoutError" and "playwright" in error.__class__.__module__:
        return "playwright_timeout"
    if report.target_navigation_started and not report.target_navigation_completed:
        return "target_navigation_failed"
    if report.target_navigation_completed and not report.store_selector_opened:
        return "store_selector_failed"
    if report.store_selector_opened and not report.city_selected:
        return "city_selection_failed"
    if report.city_selected and not report.store_selected:
        return "store_selection_failed"
    if isinstance(error, DiagnosticSafetyError):
        if report.authorization_checked and not report.browser_started and "authorization" in message:
            return "authorization_rejected"
        return "diagnostic_safety_error"
    return "unexpected_diagnostic_error"


def find_compatible_browser_executable() -> str | None:
    """Devuelve un Chromium/Chrome del sistema; no descarga browsers."""
    for name in _BROWSER_CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    return None


def install_local_network_guard(context: Any, *, events: list[Mapping[str, Any]] | None = None) -> None:
    """Bloquea toda red externa en checks de browser; synthetic.invalid se intercepta localmente."""
    sink = events if events is not None else []

    def guard(route: Any, request: Any) -> None:
        parts = urlsplit(request.url)
        scheme, host = parts.scheme.lower(), (parts.hostname or "").lower()
        if scheme in _ALLOWED_LOCAL_SCHEMES:
            route.continue_()
            return
        if host in _ALLOWED_LOCAL_HOSTS:
            route.continue_()
            return
        if host == _ALLOWED_SYNTHETIC_HOST:
            sink.append({"url": sanitize_url(request.url), "action": "fulfilled_locally"})
            route.fulfill(status=200, content_type="application/json", body='{"synthetic":true}')
            return
        if scheme in {"http", "https"}:
            sink.append({"url": sanitize_url(request.url), "action": "blocked_before_network"})
            route.abort("blockedbyclient")
            return
        route.abort("blockedbyclient")

    context.route("**/*", guard)
    route_web_socket = getattr(context, "route_web_socket", None)
    if not callable(route_web_socket):
        raise DiagnosticSafetyError(
            "runtime Playwright sin guard previo de WebSocket; diagnóstico denegado"
        )

    def guard_web_socket(web_socket: Any) -> None:
        sink.append({"url": sanitize_url(web_socket.url), "action": "websocket_blocked"})
        web_socket.close()

    route_web_socket("**/*", guard_web_socket)


def launch_compatible_chromium(pw: Any) -> tuple[Any, str]:
    """Lanza Chromium sin descargar: managed si existe, si no browser del runner."""
    launch_args = [
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-domain-reliability",
        "--disable-features=MediaRouter,OptimizationHints,AutofillServerCommunication",
        "--disable-sync",
        "--metrics-recording-only",
        "--no-first-run",
    ]
    managed = Path(str(pw.chromium.executable_path))
    if managed.exists():
        return pw.chromium.launch(headless=True, args=launch_args), str(managed)
    executable = find_compatible_browser_executable()
    if not executable:
        raise DiagnosticSafetyError("browser Chromium/Chrome compatible no disponible")
    return pw.chromium.launch(
        headless=True, executable_path=executable, args=launch_args
    ), executable


def _record_failure(report: SpsContextDiagnostic, error: BaseException, counter: LogicalRequestCounter) -> None:
    report.logical_requests = counter.count
    report.completed_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    report.stop_reason = _stop_reason(error, report)
    report.errors.append(f"{error.__class__.__name__}: {sanitize_error(error)}")
    report.redactions = [
        "cookies", "authorization", "tokens", "orderForm IDs", "session IDs",
        "addresses", "coordinates", "personal data", "JWT", "API keys",
    ]


def _diagnostic_labels(
    network_policy: str, *, changed_context_observed: bool | None = None
) -> tuple[str, str]:
    """Evita que evidencia sintética se serialice como confirmación live."""

    if network_policy == "local_only":
        return "synthetic_local", "offline_fixture"
    if changed_context_observed is None:
        return "live", "pending"
    return "live", "confirmed" if changed_context_observed else "ui_only"


def run_live(
    *,
    authorization_id: str | None,
    output_path: Path | None = None,
    budget: DiagnosticBudget | None = None,
    active_ids: Iterable[str] = ACTIVE_AUTHORIZATION_IDS,
    _network_policy: str = "live",
) -> SpsContextDiagnostic:
    """Live futuro; devuelve siempre reporte sanitizado ante fallo controlable.

    La autorización se considera elegible para consumo cuando se marca
    ``target_navigation_started`` inmediatamente antes de ``page.goto(TARGET_URL)``.
    No se persiste remotamente el consumo en esta etapa.
    """
    if _network_policy not in {"live", "local_only"}:
        raise ValueError("network policy inválida")
    budget = budget or DiagnosticBudget()
    counter = LogicalRequestCounter(budget)
    report_mode, initial_location_status = _diagnostic_labels(_network_policy)
    report = SpsContextDiagnostic(
        started_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"), mode=report_mode,
        browser="not_started", location_status=initial_location_status,
    )
    try:
        report.authorization_checked = True
        validate_live_authorization(
            live=True, authorization_id=authorization_id, active_ids=active_ids,
        )
        if _network_policy == "live":
            raise DiagnosticSafetyError(
                "GLOBAL LIVE BLOCKED mientras GATE-17 carezca de evidencia productiva"
            )

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise DiagnosticSafetyError("Playwright no instalado") from exc

        with sync_playwright() as pw:  # pragma: no cover - no se usa contra La Colonia en esta etapa
            browser, executable = launch_compatible_chromium(pw)
            report.browser = f"chromium:{Path(executable).name}"
            report.browser_started = True
            context = browser.new_context(service_workers="block")
            install_local_network_guard(context)
            page = context.new_page()
            templates: list[dict[str, Any]] = []
            capture_templates = {"enabled": False}

            def route_handler(route: Any, request: Any) -> None:
                parts = urlsplit(request.url)
                host = (parts.hostname or "").lower()
                if capture_templates["enabled"] and request.resource_type in {"xhr", "fetch"}:
                    raw = _raw_request(request)
                    metadata = parse_network_request(raw)
                    if {"productSearch", "facets"}.intersection(metadata.classifications):
                        templates.append(raw)
                        route.abort()
                        return
                if _network_policy == "local_only":
                    if parts.scheme.lower() in _ALLOWED_LOCAL_SCHEMES or host in _ALLOWED_LOCAL_HOSTS:
                        route.continue_()
                        return
                    if host == _ALLOWED_SYNTHETIC_HOST:
                        route.fulfill(status=200, content_type="application/json", body='{"synthetic":true}')
                        return
                    route.abort("blockedbyclient")
                    return
                route.continue_()

            page.route("**/*", route_handler)
            counter.reserve("open_home")
            report.target_navigation_started = True
            report.authorization_consumption_eligible = True
            page.goto(TARGET_URL, wait_until="domcontentloaded")
            report.target_navigation_completed = True
            local_before, session_before = _storage(page)
            cookies_before = _cookies(context)

            _pw_activate(_pw_unique(page, store_selector_plan(), "Selecciona tu tienda"), "Selecciona tu tienda")
            report.store_selector_opened = True
            counter.reserve("select_city")
            _pw_activate(_pw_unique(page, city_selector_plan(), "San Pedro Sula"), "San Pedro Sula")
            report.city_selected = True
            counter.reserve("select_store")
            _pw_activate(_pw_unique(page, store_option_plan(), "Plaza Pedregal"), "Plaza Pedregal")
            report.store_selected = True
            page.wait_for_timeout(500)

            local_after, session_after = _storage(page)
            cookies_after = _cookies(context)
            report.selected_city, report.selected_store = "San Pedro Sula", "Plaza Pedregal"
            report.cookies_observed = compare_storage(cookies_before, cookies_after, storage_type="cookie")
            report.local_storage_observed = compare_storage(local_before, local_after, storage_type="localStorage")
            report.session_storage_observed = compare_storage(session_before, session_after, storage_type="sessionStorage")
            combined = report.cookies_observed + report.local_storage_observed + report.session_storage_observed
            for name in VTEX_CONTEXT_NAMES:
                matches = [item for item in combined if str(item["name"]).lower() == name.lower()]
                report.context_evidence.append({
                    "kind": "vtex_context_mechanism", "name": name, "observed": bool(matches),
                    "changed_after_store_selection": any(item["changed_after_store_selection"] for item in matches),
                })
            report.context_observed = True
            _, report.location_status = _diagnostic_labels(
                _network_policy,
                changed_context_observed=any(
                    item.get("changed_after_store_selection")
                    for item in report.context_evidence
                ),
            )

            capture_templates["enabled"] = True
            counter.reserve("observe_catalog_request_shape")
            page.goto(TARGET_URL.rstrip("/") + "/supermercado", wait_until="domcontentloaded")
            page.wait_for_timeout(500)
            capture_templates["enabled"] = False

            def first(kind: str) -> dict[str, Any] | None:
                return next((raw for raw in templates if kind in parse_network_request(raw).classifications), None)

            root_template, facets_template = first("productSearch"), first("facets")
            if root_template is None:
                raise DiagnosticSafetyError("productSearch real no observado; no se inventa endpoint")
            if facets_template is None:
                raise DiagnosticSafetyError("facets reales no observadas; no se inventa endpoint")

            same = root_template is facets_template
            root_1 = _execute_replay(
                context, root_template, counter, "root_minimal", network_policy=_network_policy
            )
            report.root_observed = True
            facets_1 = root_1 if same else _execute_replay(
                context, facets_template, counter, "facets_minimal", network_policy=_network_policy
            )
            report.facets_observed = True
            root_2 = _execute_replay(
                context, root_template, counter, "root_minimal_repeat", network_policy=_network_policy
            )
            facets_2 = root_2 if same else _execute_replay(
                context, facets_template, counter, "facets_minimal_repeat", network_policy=_network_policy
            )

            root_meta, facets_meta = parse_network_request(root_1), parse_network_request(facets_1)
            report.root = {**structural_fields(root_meta), **summarize_response(root_1, root_meta)}
            report.facets = {**structural_fields(facets_meta), **summarize_response(facets_1, facets_meta)}
            report.requests = [structural_fields(parse_network_request(event)) for event in (root_1, facets_1, root_2, facets_2)]
            report.stability = {
                "root_request_shape_stable": _shape(root_1) == _shape(root_2),
                "facets_request_shape_stable": _shape(facets_1) == _shape(facets_2),
                "root_summary_stable": summarize_response(root_1) == summarize_response(root_2),
                "facets_summary_stable": summarize_response(facets_1) == summarize_response(facets_2),
            }
            report.logical_requests = counter.count
            report.completed_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            report.redactions = [
                "cookies", "authorization", "tokens", "orderForm IDs", "session IDs",
                "addresses", "coordinates", "personal data", "JWT", "API keys",
            ]
            report.stop_reason = "completed"
    except Exception as exc:
        _record_failure(report, exc, counter)
    finally:
        if report.completed_at is None:
            report.completed_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        report.logical_requests = counter.count
        _persist_report(report, output_path)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Diagnóstico SPS offline-first")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--authorization-id")
    parser.add_argument("--fixture-dom", type=Path)
    parser.add_argument("--fixture-network", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-logical-requests", type=int, default=8)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--minimum-delay-seconds", type=float, default=1.5)
    parser.add_argument("--max-retries", type=int, default=0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    budget = DiagnosticBudget(args.max_logical_requests, args.concurrency, args.minimum_delay_seconds, args.max_retries)
    if args.live:
        report = run_live(authorization_id=args.authorization_id, output_path=args.output, budget=budget)
    else:
        validate_live_authorization(live=False, authorization_id=args.authorization_id)
        if not args.fixture_dom or not args.fixture_network:
            raise DiagnosticSafetyError("offline_fixture requiere --fixture-dom y --fixture-network")
        report = run_offline_fixture(
            html=args.fixture_dom.read_text(encoding="utf-8"),
            fixture=json.loads(args.fixture_network.read_text(encoding="utf-8")),
            output_path=args.output,
            budget=budget,
        )
    if not args.output:
        print(json.dumps(report.sanitized_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not report.errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
