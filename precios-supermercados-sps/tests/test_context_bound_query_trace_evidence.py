from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from precios_supermercados.catalog_context_provenance import ContextBoundEdgeReceiptPayload
from precios_supermercados.cloudflare_trace_evidence import (
    CLOUD_PLATFORM,
    CLOUD_PROVIDER,
    ORIGIN_EXECUTION_SPAN_NAME,
    TRACE_CONTRACT_VERSION,
    CloudflareOriginTraceEvidence,
)
from precios_supermercados.context_bound_query_trace_evidence import (
    ContextBoundQueryTraceError,
    RedactedContextBoundQueryPage,
    reconcile_context_bound_query_trace,
)
from precios_supermercados.edge_crypto_page import CryptographicallyVerifiedEdgeCatalogPage
from precios_supermercados.edge_provenance import canonical_json_bytes
from precios_supermercados.scrapers.la_colonia_context_bound_catalog_transport import (
    ContextBoundVerifiedCatalogPageObservation,
)
from precios_supermercados.scrapers.la_colonia_sps_facet_context import fingerprint_context_value

RAW_REGION = "opaque-SPS-region-value-never-persist"
BASE_URL = (
    "https://www.lacolonia.com/_v/segment/graphql/v1"
    "?workspace=master&maxAge=short&appsEtag=remove"
)
WIRE_KEY = "regionId"
FETCH_URL = f"{BASE_URL}&{WIRE_KEY}={quote(RAW_REGION, safe='-._~')}"
RUN_ID = "32770000000:1"
SHA = "a" * 40
START = datetime(2026, 8, 24, 19, 30, 0, tzinfo=UTC)
END = START + timedelta(milliseconds=800)
BODY_SIZE = 1234
CONTEXT_FP = fingerprint_context_value(RAW_REGION)


def _wire_fingerprint(url: str) -> str:
    return hashlib.sha256(
        json.dumps(
            {"headers": {}, "method": "GET", "url": url},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _payload(**overrides: object) -> ContextBoundEdgeReceiptPayload:
    base_values: dict[str, object] = {
        "authorization_id": "auth-query-001",
        "run_id": RUN_ID,
        "approved_commit_sha": SHA,
        "reservation_id": "reservation-query-001",
        "request_id": "request-query-001",
        "request_digest": "b" * 64,
        "traversal_role": "primary",
        "traversal_id": "traversal-primary-001",
        "partition_id": "root",
        "response_status": 200,
        "response_body_bytes": BODY_SIZE,
        "collector_release_id": "worker-release-001",
        "physical_started_at_utc": START,
        "response_completed_at_utc": END,
    }
    context_values: dict[str, object] = {
        "location_id": "la_colonia_sps",
        "binding_source_key": f"request:regionid:sha256:{CONTEXT_FP}",
        "binding_evidence": "location_binding_radiography:sha256:" + "c" * 64,
        "context_fingerprint": CONTEXT_FP,
        "context_placement": "query",
        "context_wire_key": WIRE_KEY,
        "context_value_path": (),
        "wire_request_fingerprint": _wire_fingerprint(FETCH_URL),
    }
    for key, value in overrides.items():
        if key in context_values:
            context_values[key] = value
        else:
            base_values[key] = value

    payload = object.__new__(ContextBoundEdgeReceiptPayload)
    object.__setattr__(payload, "base", SimpleNamespace(**base_values))
    for key, value in context_values.items():
        object.__setattr__(payload, key, value)
    return payload


def _page(payload: ContextBoundEdgeReceiptPayload) -> CryptographicallyVerifiedEdgeCatalogPage:
    page = object.__new__(CryptographicallyVerifiedEdgeCatalogPage)
    object.__setattr__(page, "request", SimpleNamespace(source_url=BASE_URL))
    object.__setattr__(page, "body", SimpleNamespace(payload={}))
    object.__setattr__(
        page,
        "verified_receipt",
        SimpleNamespace(
            receipt=SimpleNamespace(payload=payload),
            receipt_digest="d" * 64,
        ),
    )
    object.__setattr__(page, "worker_evidence_id", "e" * 64)
    object.__setattr__(page, "replayed", False)
    object.__setattr__(page, "cryptographic_signature_verified", True)
    object.__setattr__(page, "production_authority", False)
    return page


def _observation(payload: ContextBoundEdgeReceiptPayload | None = None):
    effective = payload or _payload()
    observation = object.__new__(ContextBoundVerifiedCatalogPageObservation)
    object.__setattr__(observation, "expected", SimpleNamespace())
    object.__setattr__(observation, "page", _page(effective))
    object.__setattr__(observation, "raw_evidence", SimpleNamespace())
    object.__setattr__(observation, "location_id", "la_colonia_sps")
    object.__setattr__(observation, "context_fingerprint", effective.context_fingerprint)
    object.__setattr__(observation, "wire_request_fingerprint", effective.wire_request_fingerprint)
    object.__setattr__(observation, "production_authority", False)
    return observation


def _trace(fetch_url: str = FETCH_URL, **overrides: object) -> CloudflareOriginTraceEvidence:
    values: dict[str, object] = {
        "trace_id": "trace-query-001",
        "custom_span_id": "custom-query-001",
        "fetch_span_id": "fetch-query-001",
        "fetch_parent_span_id": "custom-query-001",
        "faas_invocation_id": "invocation-query-001",
        "service_name": "precios-sps-provenance",
        "script_version_id": "worker-release-001",
        "custom_span_name": ORIGIN_EXECUTION_SPAN_NAME,
        "trace_contract_version": TRACE_CONTRACT_VERSION,
        "cloud_provider": CLOUD_PROVIDER,
        "cloud_platform": CLOUD_PLATFORM,
        "collector_provider": "cloudflare_workers",
        "authorization_id": "auth-query-001",
        "run_id": RUN_ID,
        "approved_commit_sha": SHA,
        "reservation_id": "reservation-query-001",
        "request_id": "request-query-001",
        "request_digest": "b" * 64,
        "traversal_role": "primary",
        "traversal_id": "traversal-primary-001",
        "partition_id": "root",
        "fetch_url": fetch_url,
        "fetch_method": "GET",
        "fetch_status": 200,
        "fetch_response_body_size": BODY_SIZE,
        "custom_started_at_utc": START - timedelta(milliseconds=100),
        "custom_completed_at_utc": END + timedelta(milliseconds=100),
        "fetch_started_at_utc": START + timedelta(milliseconds=50),
        "fetch_completed_at_utc": END - timedelta(milliseconds=50),
    }
    values.update(overrides)
    return CloudflareOriginTraceEvidence(**values)  # type: ignore[arg-type]


def _expect_code(code: str):
    def check(error: BaseException) -> bool:
        return isinstance(error, ContextBoundQueryTraceError) and error.code == code

    return check


def test_query_trace_se_reconcilia_y_salida_no_retiene_url_ni_region_raw() -> None:
    observation = _observation()
    raw_trace = _trace()
    result = reconcile_context_bound_query_trace(observation, [raw_trace])

    assert isinstance(result, RedactedContextBoundQueryPage)
    assert result.page is observation.page
    assert result.platform_evidence_reconciled is True
    assert result.production_authority is False
    evidence = result.trace_evidence
    assert evidence.context_fingerprint == CONTEXT_FP
    assert evidence.wire_request_fingerprint == _wire_fingerprint(FETCH_URL)
    assert evidence.base_fetch_url_sha256 == hashlib.sha256(BASE_URL.encode()).hexdigest()
    assert evidence.raw_fetch_url_sha256 == hashlib.sha256(FETCH_URL.encode()).hexdigest()
    assert evidence.raw_trace_evidence_sha256 == hashlib.sha256(
        canonical_json_bytes(raw_trace.canonical_dict())
    ).hexdigest()
    assert len(result.physical_evidence_id) == 64

    rendered = json.dumps(evidence.canonical_dict(), sort_keys=True, default=str)
    # Los nombres de campos hash pueden contener `fetch_url`; lo que nunca puede
    # persistir es el URL físico real ni el valor raw que lleva regionId.
    for forbidden in (RAW_REGION, FETCH_URL, "url.full"):
        assert forbidden not in rendered
        assert forbidden not in repr(evidence)
        assert forbidden not in repr(result)
    assert not hasattr(evidence, "fetch_url")
    assert not hasattr(evidence, "raw_context")


def test_hash_de_traza_cambia_si_cambia_identidad_fisica_no_sensible() -> None:
    first = reconcile_context_bound_query_trace(_observation(), [_trace()]).trace_evidence
    second = reconcile_context_bound_query_trace(
        _observation(),
        [_trace(trace_id="trace-query-002", custom_span_id="custom-query-002", fetch_span_id="fetch-query-002", fetch_parent_span_id="custom-query-002")],
    ).trace_evidence
    assert first.raw_trace_evidence_sha256 != second.raw_trace_evidence_sha256
    assert first.physical_evidence_id != second.physical_evidence_id


def test_header_receipt_no_puede_entrar_por_ruta_query() -> None:
    payload = _payload(context_placement="header")
    with pytest.raises(ContextBoundQueryTraceError, match="query_trace_query_placement_required") as captured:
        reconcile_context_bound_query_trace(_observation(payload), [_trace()])
    assert captured.value.code == "query_trace_query_placement_required"


def test_receipt_legacy_no_puede_entrar_por_ruta_query() -> None:
    observation = _observation()
    observation.page.verified_receipt.receipt.payload = SimpleNamespace()
    with pytest.raises(ContextBoundQueryTraceError) as captured:
        reconcile_context_bound_query_trace(observation, [_trace()])
    assert captured.value.code == "query_trace_receipt_downgrade"


def test_query_anidada_permanece_fuera_del_contrato() -> None:
    payload = _payload(context_value_path=("variables", "regionId"))
    with pytest.raises(ContextBoundQueryTraceError) as captured:
        reconcile_context_bound_query_trace(_observation(payload), [_trace()])
    assert captured.value.code == "query_trace_nested_context_forbidden"


@pytest.mark.parametrize(
    ("fetch_url", "code"),
    [
        (f"{BASE_URL}&other=x&{WIRE_KEY}={RAW_REGION}", "query_trace_extra_query_material"),
        (f"{BASE_URL}&other={RAW_REGION}", "query_trace_context_key_mismatch"),
        (f"{BASE_URL}&{WIRE_KEY}=wrong", "query_trace_context_value_fingerprint_mismatch"),
        (
            "https://www.lacolonia.com/_v/segment/graphql/v1?workspace=changed"
            f"&{WIRE_KEY}={RAW_REGION}",
            "query_trace_base_query_changed",
        ),
        (
            "https://evil.invalid/_v/segment/graphql/v1?workspace=master&maxAge=short"
            f"&appsEtag=remove&{WIRE_KEY}={RAW_REGION}",
            "query_trace_base_url_mismatch",
        ),
    ],
)
def test_url_fisico_debe_ser_base_exacta_mas_un_solo_region_directo(fetch_url: str, code: str) -> None:
    with pytest.raises(ContextBoundQueryTraceError) as captured:
        reconcile_context_bound_query_trace(_observation(), [_trace(fetch_url)])
    assert captured.value.code == code


def test_wire_fingerprint_del_receipt_debe_comprometer_el_url_fisico_exacto() -> None:
    payload = _payload(wire_request_fingerprint="f" * 64)
    observation = _observation(payload)
    with pytest.raises(ContextBoundQueryTraceError) as captured:
        reconcile_context_bound_query_trace(observation, [_trace()])
    assert captured.value.code == "query_trace_wire_request_fingerprint_mismatch"


@pytest.mark.parametrize(
    ("trace_override", "code"),
    [
        ({"response_status": 201}, "query_trace_fetch_status_mismatch"),
        ({"response_body_bytes": BODY_SIZE + 1}, "query_trace_fetch_body_size_mismatch"),
        ({"collector_release_id": "other-release"}, "query_trace_script_version_mismatch"),
    ],
)
def test_receipt_y_traza_fisica_deben_reconciliar(trace_override: dict[str, object], code: str) -> None:
    payload = _payload(**trace_override)
    with pytest.raises(ContextBoundQueryTraceError) as captured:
        reconcile_context_bound_query_trace(_observation(payload), [_trace()])
    assert captured.value.code == code


def test_identidad_no_coincidente_no_se_acepta_como_candidata() -> None:
    with pytest.raises(ContextBoundQueryTraceError) as captured:
        reconcile_context_bound_query_trace(
            _observation(),
            [_trace(request_id="request-other")],
        )
    assert captured.value.code == "query_trace_matching_trace_missing"


def test_dos_trazas_con_misma_identidad_fallan_por_ambiguedad() -> None:
    second = _trace(
        trace_id="trace-query-002",
        custom_span_id="custom-query-002",
        fetch_span_id="fetch-query-002",
        fetch_parent_span_id="custom-query-002",
        faas_invocation_id="invocation-query-002",
    )
    with pytest.raises(ContextBoundQueryTraceError) as captured:
        reconcile_context_bound_query_trace(_observation(), [_trace(), second])
    assert captured.value.code == "query_trace_matching_trace_not_unique"


def test_timing_fuera_del_receipt_mas_skew_falla_cerrado() -> None:
    trace = _trace(
        fetch_started_at_utc=START - timedelta(seconds=11),
        custom_started_at_utc=START - timedelta(seconds=12),
    )
    with pytest.raises(ContextBoundQueryTraceError) as captured:
        reconcile_context_bound_query_trace(_observation(), [trace])
    assert captured.value.code == "query_trace_fetch_started_too_early"
