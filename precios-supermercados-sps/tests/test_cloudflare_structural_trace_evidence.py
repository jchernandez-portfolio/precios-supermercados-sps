from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from precios_supermercados.cloudflare_structural_trace_evidence import (
    CLOUD_PLATFORM,
    CLOUD_PROVIDER,
    STRUCTURAL_EXECUTION_SPAN_NAME,
    STRUCTURAL_TRACE_CONTRACT_VERSION,
    CloudflareStructuralTraceEvidence,
    CloudflareStructuralTraceEvidenceError,
    assert_distinct_structural_evidence,
    reconcile_cloudflare_structural_trace,
)
from precios_supermercados.edge_structural_observation import EdgeStructuralObservationVerifier
from precios_supermercados.la_colonia_edge_structural_request import (
    build_structural_discovery_url,
    validate_la_colonia_structural_request,
)
from precios_supermercados.structural_provenance import SignedStructuralReceipt, StructuralReceiptPayload
from precios_supermercados.structural_receipt_crypto import Ed25519StructuralReceiptVerifier


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _body(kind: str) -> bytes:
    if kind == "root_total":
        value = {"data": {"productSearch": {"recordsFiltered": 7}}}
    else:
        value = {
            "data": {
                "productSearch": {"recordsFiltered": 7},
                "facets": {"sampling": False, "facets": []},
            }
        }
    return json.dumps(value, separators=(",", ":")).encode()


def _observation(kind: str, private_key: Ed25519PrivateKey):
    raw = _body(kind)
    request = validate_la_colonia_structural_request(build_structural_discovery_url(kind))
    payload = StructuralReceiptPayload(
        run_id="32523000000:1",
        request_kind=kind,  # type: ignore[arg-type]
        request_id=f"request-{kind}",
        reservation_id=f"reservation-{kind}",
        authorization_id="authorization-structural-trace",
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
        physical_started_at_utc=datetime(2026, 8, 21, 20, 20, 0, 100_000, tzinfo=timezone.utc),
        response_completed_at_utc=datetime(2026, 8, 21, 20, 20, 0, 300_000, tzinfo=timezone.utc),
        github_repository="jchernandez-portfolio/precios-supermercados-sps",
        github_repository_id="1282475205",
        github_ref="refs/heads/main",
        github_workflow_ref="jchernandez-portfolio/precios-supermercados-sps/.github/workflows/precios-supermercados-sps-la-colonia-live.yml@refs/heads/main",
        github_environment="la-colonia-live",
        github_run_id="32523000000",
        github_run_attempt=1,
        oidc_subject="repo:jchernandez-portfolio/precios-supermercados-sps:environment:la-colonia-live",
        oidc_jti=f"jti-{kind}",
        collector_provider="cloudflare_workers",
        collector_principal="cloudflare-worker:precios-sps-provenance",
        collector_execution=f"execution-{kind}",
        collector_release_id="release-structural-001",
        collector_code_sha256="d" * 64,
        signing_algorithm="Ed25519",
        signing_key_id="cloudflare-ed25519-v1",
        nonce=f"nonce-{kind}",
    )
    placeholder = SignedStructuralReceipt(payload=payload, signature_b64url=_b64url(b"0" * 64))
    receipt = SignedStructuralReceipt(
        payload=payload,
        signature_b64url=_b64url(private_key.sign(placeholder.signing_bytes())),
    )
    public_der = private_key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    verifier = EdgeStructuralObservationVerifier(
        Ed25519StructuralReceiptVerifier({payload.signing_key_id: _b64url(public_der)})
    )
    return verifier.verify(source_url=request.source_url, raw_body=raw, signed_receipt=receipt)


def _trace(observation, **overrides: object) -> CloudflareStructuralTraceEvidence:
    payload = observation.verified_receipt.receipt.payload
    suffix = payload.request_kind
    values: dict[str, object] = {
        "trace_id": f"trace-{suffix}",
        "custom_span_id": f"custom-{suffix}",
        "fetch_span_id": f"fetch-{suffix}",
        "fetch_parent_span_id": f"custom-{suffix}",
        "faas_invocation_id": f"invocation-{suffix}",
        "service_name": "precios-sps-provenance",
        "script_version_id": payload.collector_release_id,
        "custom_span_name": STRUCTURAL_EXECUTION_SPAN_NAME,
        "trace_contract_version": STRUCTURAL_TRACE_CONTRACT_VERSION,
        "cloud_provider": CLOUD_PROVIDER,
        "cloud_platform": CLOUD_PLATFORM,
        "collector_provider": "cloudflare_workers",
        "authorization_id": payload.authorization_id,
        "run_id": payload.run_id,
        "approved_commit_sha": payload.approved_commit_sha,
        "reservation_id": payload.reservation_id,
        "request_id": payload.request_id,
        "request_digest": payload.request_digest,
        "request_kind": payload.request_kind,
        "fetch_url": observation.source_url,
        "fetch_method": "GET",
        "fetch_status": 200,
        "fetch_response_body_size": payload.response_body_bytes,
        "custom_started_at_utc": datetime(2026, 8, 21, 20, 20, 0, 90_000, tzinfo=timezone.utc),
        "custom_completed_at_utc": datetime(2026, 8, 21, 20, 20, 0, 310_000, tzinfo=timezone.utc),
        "fetch_started_at_utc": datetime(2026, 8, 21, 20, 20, 0, 110_000, tzinfo=timezone.utc),
        "fetch_completed_at_utc": datetime(2026, 8, 21, 20, 20, 0, 290_000, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return CloudflareStructuralTraceEvidence(**values)  # type: ignore[arg-type]


def test_reconcilia_unica_evidencia_fisica_con_receipt() -> None:
    key = Ed25519PrivateKey.generate()
    observation = _observation("root_total", key)
    trace = _trace(observation)

    result = reconcile_cloudflare_structural_trace(observation, [trace])

    assert result.observation is observation
    assert result.trace_evidence is trace
    assert result.platform_evidence_reconciled is True
    assert result.production_authority is False
    assert len(result.physical_evidence_id) == 64


def test_matching_trace_debe_ser_unico() -> None:
    key = Ed25519PrivateKey.generate()
    observation = _observation("root_total", key)
    left = _trace(observation)
    right = replace(left, trace_id="trace-root-total-copy", custom_span_id="custom-copy", fetch_span_id="fetch-copy", fetch_parent_span_id="custom-copy")

    with pytest.raises(CloudflareStructuralTraceEvidenceError) as exc:
        reconcile_cloudflare_structural_trace(observation, [left, right])
    assert exc.value.code == "matching_trace_not_unique"


def test_url_status_size_y_release_deben_reconciliar() -> None:
    key = Ed25519PrivateKey.generate()
    observation = _observation("root_total", key)
    cases = (
        ("fetch_url", "https://www.lacolonia.com/otro", "trace_fetch_url_mismatch"),
        ("fetch_status", 201, "trace_fetch_status_mismatch"),
        ("fetch_response_body_size", 999, "trace_fetch_body_size_mismatch"),
        ("script_version_id", "otra-release", "trace_script_version_mismatch"),
    )
    for field, value, code in cases:
        trace = replace(_trace(observation), **{field: value})
        with pytest.raises(CloudflareStructuralTraceEvidenceError) as exc:
            reconcile_cloudflare_structural_trace(observation, [trace])
        assert exc.value.code == code


def test_parent_span_y_nombre_estructural_son_obligatorios() -> None:
    key = Ed25519PrivateKey.generate()
    observation = _observation("category_tree", key)
    with pytest.raises(CloudflareStructuralTraceEvidenceError) as parent:
        _trace(observation, fetch_parent_span_id="otro-parent")
    assert parent.value.code == "fetch_parent_span_mismatch"

    with pytest.raises(CloudflareStructuralTraceEvidenceError) as name:
        _trace(observation, custom_span_name="precios_sps_origin_execution")
    assert name.value.code == "custom_span_name_invalid"


def test_request_kind_no_puede_reetiquetarse() -> None:
    key = Ed25519PrivateKey.generate()
    root = _observation("root_total", key)
    mislabeled = _trace(root, request_kind="category_tree")
    with pytest.raises(CloudflareStructuralTraceEvidenceError) as exc:
        reconcile_cloudflare_structural_trace(root, [mislabeled])
    assert exc.value.code == "matching_trace_missing"


def test_root_y_tree_exigen_evidencia_fisica_distinta() -> None:
    key = Ed25519PrivateKey.generate()
    root_observation = _observation("root_total", key)
    tree_observation = _observation("category_tree", key)
    root = reconcile_cloudflare_structural_trace(root_observation, [_trace(root_observation)])
    tree = reconcile_cloudflare_structural_trace(tree_observation, [_trace(tree_observation)])
    assert_distinct_structural_evidence(root, tree)

    reused_span = replace(
        _trace(tree_observation),
        fetch_span_id=root.trace_evidence.fetch_span_id,
    )
    tree_reused = reconcile_cloudflare_structural_trace(tree_observation, [reused_span])
    with pytest.raises(CloudflareStructuralTraceEvidenceError) as exc:
        assert_distinct_structural_evidence(root, tree_reused)
    assert exc.value.code == "structural_fetch_span_reused"


def test_evidencia_fuera_de_ventana_temporal_falla() -> None:
    key = Ed25519PrivateKey.generate()
    observation = _observation("root_total", key)
    trace = _trace(
        observation,
        custom_started_at_utc=datetime(2026, 8, 21, 20, 19, 0, tzinfo=timezone.utc),
        custom_completed_at_utc=datetime(2026, 8, 21, 20, 19, 1, tzinfo=timezone.utc),
        fetch_started_at_utc=datetime(2026, 8, 21, 20, 19, 0, 100_000, tzinfo=timezone.utc),
        fetch_completed_at_utc=datetime(2026, 8, 21, 20, 19, 0, 200_000, tzinfo=timezone.utc),
    )
    with pytest.raises(CloudflareStructuralTraceEvidenceError) as exc:
        reconcile_cloudflare_structural_trace(observation, [trace])
    assert exc.value.code == "trace_fetch_started_too_early"
