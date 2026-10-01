from __future__ import annotations

import base64
import hashlib
import inspect
import json
from datetime import datetime, timedelta, timezone

import pytest

from precios_supermercados.canonical_authenticated_provenance_plan import (
    CANONICAL_CATALOG_PAGE_SIZE,
    CANONICAL_CATALOG_REQUEST_LIMIT,
    CANONICAL_PRIMARY_ORDER_BY,
    CANONICAL_RECONCILIATION_ORDER_BY,
    CanonicalAuthenticatedProvenancePlanError,
    derive_canonical_authenticated_catalog_plan,
)
from precios_supermercados.cloudflare_structural_trace_evidence import (
    CLOUD_PLATFORM,
    CLOUD_PROVIDER,
    STRUCTURAL_EXECUTION_SPAN_NAME,
    STRUCTURAL_TRACE_CONTRACT_VERSION,
    CloudflareStructuralTraceEvidence,
    PlatformReconciledStructuralObservation,
)
from precios_supermercados.edge_structural_observation import (
    CryptographicallyVerifiedStructuralObservation,
)
from precios_supermercados.la_colonia_edge_structural_body import (
    validate_la_colonia_structural_body,
)
from precios_supermercados.la_colonia_edge_structural_request import (
    build_structural_discovery_url,
    validate_la_colonia_structural_request,
)
from precios_supermercados.structural_discovery_manifest import (
    build_verified_structural_discovery,
)
from precios_supermercados.structural_provenance import (
    SignedStructuralReceipt,
    StructuralReceiptPayload,
)
from precios_supermercados.structural_receipt_crypto import VerifiedStructuralReceipt

RUN = "32532000000:1"
AUTH = "authorization-canonical-plan-001"
RELEASE = "release-canonical-plan-001"
KEY_ID = "cloudflare-ed25519-v1"
BASE = datetime(2026, 8, 21, 23, 0, 0, tzinfo=timezone.utc)


def _signature(seed: int) -> str:
    raw = bytes((value + seed) % 256 for value in range(64))
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _body(kind: str, total: int) -> bytes:
    if kind == "root_total":
        value = {"data": {"productSearch": {"recordsFiltered": total}}}
    else:
        value = {
            "data": {
                "productSearch": {"recordsFiltered": total},
                "facets": {
                    "sampling": False,
                    "facets": [
                        {
                            "type": "CATEGORY",
                            "values": [
                                {
                                    "key": "category-1",
                                    "value": "supermercado",
                                    "quantity": total,
                                    "children": [],
                                }
                            ],
                        }
                    ],
                },
            }
        }
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def _platform(kind: str, total: int) -> PlatformReconciledStructuralObservation:
    start = BASE if kind == "root_total" else BASE + timedelta(seconds=2)
    completed = start + timedelta(milliseconds=200)
    raw = _body(kind, total)
    source_url = build_structural_discovery_url(kind)
    request = validate_la_colonia_structural_request(source_url)
    body = validate_la_colonia_structural_body(raw, request)
    payload = StructuralReceiptPayload(
        run_id=RUN,
        request_kind=kind,  # type: ignore[arg-type]
        request_id=f"request-{kind}",
        reservation_id=f"reservation-{kind}",
        authorization_id=AUTH,
        approved_commit_sha="a" * 40,
        request_digest=request.canonical_request_sha256,
        canonical_request_sha256=request.canonical_request_sha256,
        graphql_query_sha256=request.graphql_query_sha256,
        http_method="GET",
        target_scheme="https",
        target_host="www.lacolonia.com",
        target_path="/_v/segment/graphql/v1",
        raw_response_sha256=hashlib.sha256(raw).hexdigest(),
        response_status=200,
        response_body_bytes=len(raw),
        physical_started_at_utc=start,
        response_completed_at_utc=completed,
        github_repository="jchernandez-portfolio/precios-supermercados-sps",
        github_repository_id="1282475205",
        github_ref="refs/heads/main",
        github_workflow_ref="jchernandez-portfolio/precios-supermercados-sps/.github/workflows/precios-supermercados-sps-la-colonia-live.yml@refs/heads/main",
        github_environment="la-colonia-live",
        github_run_id="32532000000",
        github_run_attempt=1,
        oidc_subject="repo:jchernandez-portfolio/precios-supermercados-sps:environment:la-colonia-live",
        oidc_jti=f"oidc-jti-{kind}",
        collector_provider="cloudflare_workers",
        collector_principal="cloudflare-worker:precios-sps-provenance",
        collector_execution=f"execution-{kind}",
        collector_release_id=RELEASE,
        collector_code_sha256="d" * 64,
        signing_algorithm="Ed25519",
        signing_key_id=KEY_ID,
        nonce=f"nonce-{kind}",
    )
    signed = SignedStructuralReceipt(
        payload=payload,
        signature_b64url=_signature(1 if kind == "root_total" else 2),
    )
    verified = VerifiedStructuralReceipt(
        receipt=signed,
        signing_key_id=KEY_ID,
        public_key_spki_sha256="e" * 64,
        receipt_digest=signed.digest,
    )
    observation = CryptographicallyVerifiedStructuralObservation(
        request=request,
        body=body,
        verified_receipt=verified,
        raw_body_sha256=hashlib.sha256(raw).hexdigest(),
    )
    trace = CloudflareStructuralTraceEvidence(
        trace_id=f"trace-{kind}",
        custom_span_id=f"custom-{kind}",
        fetch_span_id=f"fetch-{kind}",
        fetch_parent_span_id=f"custom-{kind}",
        faas_invocation_id=f"invocation-{kind}",
        service_name="precios-sps-provenance",
        script_version_id=RELEASE,
        custom_span_name=STRUCTURAL_EXECUTION_SPAN_NAME,
        trace_contract_version=STRUCTURAL_TRACE_CONTRACT_VERSION,
        cloud_provider=CLOUD_PROVIDER,
        cloud_platform=CLOUD_PLATFORM,
        collector_provider="cloudflare_workers",
        authorization_id=AUTH,
        run_id=RUN,
        approved_commit_sha="a" * 40,
        reservation_id=payload.reservation_id,
        request_id=payload.request_id,
        request_digest=payload.request_digest,
        request_kind=kind,
        fetch_url=source_url,
        fetch_method="GET",
        fetch_status=200,
        fetch_response_body_size=len(raw),
        custom_started_at_utc=start,
        custom_completed_at_utc=completed,
        fetch_started_at_utc=start + timedelta(milliseconds=20),
        fetch_completed_at_utc=completed - timedelta(milliseconds=20),
    )
    return PlatformReconciledStructuralObservation(
        observation=observation,
        trace_evidence=trace,
    )


def _discovery(total: int = 7):
    return build_verified_structural_discovery(
        root_total=_platform("root_total", total),
        category_tree=_platform("category_tree", total),
    )


def test_plan_canonico_no_expone_overrides_y_fija_politica() -> None:
    signature = inspect.signature(derive_canonical_authenticated_catalog_plan)
    assert tuple(signature.parameters) == ("discovery",)

    plan = derive_canonical_authenticated_catalog_plan(_discovery())

    assert plan.plan.page_size == CANONICAL_CATALOG_PAGE_SIZE == 50
    assert plan.plan.primary_order_by == CANONICAL_PRIMARY_ORDER_BY == "OrderByNameASC"
    assert (
        plan.plan.reconciliation_order_by
        == CANONICAL_RECONCILIATION_ORDER_BY
        == "OrderByNameDESC"
    )
    assert plan.plan.primary_traversal_id == f"{RUN}:primary"
    assert plan.plan.reconciliation_traversal_id == f"{RUN}:reconciliation"
    assert plan.request_count == 2
    assert len(plan.primary_pages) == len(plan.reconciliation_pages) == 1
    assert plan.production_authority is False


def test_mismo_discovery_produce_mismo_plan() -> None:
    discovery = _discovery()
    first = derive_canonical_authenticated_catalog_plan(discovery)
    second = derive_canonical_authenticated_catalog_plan(discovery)
    assert first.digest == second.digest
    assert first.plan.digest == second.plan.digest


def test_plan_que_supera_limite_global_falla_cerrado() -> None:
    # 13,000 productos / 50 = 260 páginas por traversal = 520 requests.
    with pytest.raises(CanonicalAuthenticatedProvenancePlanError) as captured:
        derive_canonical_authenticated_catalog_plan(_discovery(total=13_000))
    assert captured.value.code == "canonical_plan_request_limit_exceeded"
    assert CANONICAL_CATALOG_REQUEST_LIMIT == 500


def test_tipo_de_discovery_invalido_falla() -> None:
    with pytest.raises(CanonicalAuthenticatedProvenancePlanError) as captured:
        derive_canonical_authenticated_catalog_plan(object())  # type: ignore[arg-type]
    assert captured.value.code == "canonical_plan_discovery_invalid"
