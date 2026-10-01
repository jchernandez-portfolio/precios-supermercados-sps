from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

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
    StructuralDiscoveryManifestError,
    VerifiedStructuralDiscovery,
    build_verified_structural_discovery,
)
from precios_supermercados.structural_provenance import (
    SignedStructuralReceipt,
    StructuralReceiptPayload,
)
from precios_supermercados.structural_receipt_crypto import VerifiedStructuralReceipt

BASE = datetime(2026, 8, 21, 20, 30, 0, tzinfo=timezone.utc)
AUTH = "authorization-structural-manifest"
RUN = "32527000000:1"
RELEASE = "release-structural-manifest-001"
KEY_ID = "cloudflare-ed25519-v1"


def _signature(seed: int) -> str:
    raw = bytes((value + seed) % 256 for value in range(64))
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _body(kind: str, *, total: int = 7, sampling: bool = False, complete: bool = True) -> bytes:
    if kind == "root_total":
        value = {"data": {"productSearch": {"recordsFiltered": total}}}
    else:
        children: list[dict[str, object]] = []
        quantity = total
        if not complete:
            quantity = max(total - 1, 1)
        value = {
            "data": {
                "productSearch": {"recordsFiltered": total},
                "facets": {
                    "sampling": sampling,
                    "facets": [
                        {
                            "type": "CATEGORY",
                            "values": [
                                {
                                    "key": "category-1",
                                    "value": "supermercado",
                                    "quantity": quantity,
                                    "children": children,
                                }
                            ],
                        }
                    ],
                },
            }
        }
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _platform(
    kind: str,
    *,
    total: int = 7,
    sampling: bool = False,
    complete: bool = True,
    start: datetime | None = None,
    request_id: str | None = None,
    reservation_id: str | None = None,
    nonce: str | None = None,
    release_id: str = RELEASE,
    public_key_hash: str = "e" * 64,
    fetch_span_id: str | None = None,
) -> PlatformReconciledStructuralObservation:
    if start is None:
        start = BASE if kind == "root_total" else BASE + timedelta(seconds=2)
    completed = start + timedelta(milliseconds=200)
    raw = _body(kind, total=total, sampling=sampling, complete=complete)
    source_url = build_structural_discovery_url(kind)
    request = validate_la_colonia_structural_request(source_url)
    body = validate_la_colonia_structural_body(raw, request)
    suffix = "root" if kind == "root_total" else "tree"
    payload = StructuralReceiptPayload(
        run_id=RUN,
        request_kind=kind,  # type: ignore[arg-type]
        request_id=request_id or f"request-{suffix}",
        reservation_id=reservation_id or f"reservation-{suffix}",
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
        github_run_id="32527000000",
        github_run_attempt=1,
        oidc_subject="repo:jchernandez-portfolio/precios-supermercados-sps:environment:la-colonia-live",
        oidc_jti=f"oidc-jti-{suffix}",
        collector_provider="cloudflare_workers",
        collector_principal="cloudflare-worker:precios-sps-provenance",
        collector_execution=f"execution-{suffix}",
        collector_release_id=release_id,
        collector_code_sha256="d" * 64,
        signing_algorithm="Ed25519",
        signing_key_id=KEY_ID,
        nonce=nonce or f"nonce-{suffix}",
    )
    signed = SignedStructuralReceipt(
        payload=payload,
        signature_b64url=_signature(1 if kind == "root_total" else 2),
    )
    verified = VerifiedStructuralReceipt(
        receipt=signed,
        signing_key_id=KEY_ID,
        public_key_spki_sha256=public_key_hash,
        receipt_digest=signed.digest,
    )
    observation = CryptographicallyVerifiedStructuralObservation(
        request=request,
        body=body,
        verified_receipt=verified,
        raw_body_sha256=hashlib.sha256(raw).hexdigest(),
    )
    trace = CloudflareStructuralTraceEvidence(
        trace_id=f"trace-{suffix}",
        custom_span_id=f"custom-{suffix}",
        fetch_span_id=fetch_span_id or f"fetch-{suffix}",
        fetch_parent_span_id=f"custom-{suffix}",
        faas_invocation_id=f"invocation-{suffix}",
        service_name="precios-sps-provenance",
        script_version_id=release_id,
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


def test_cierra_root_y_tree_y_deriva_estructura_sin_filtrar_categoria() -> None:
    result = build_verified_structural_discovery(
        root_total=_platform("root_total"),
        category_tree=_platform("category_tree"),
    )

    assert isinstance(result, VerifiedStructuralDiscovery)
    assert result.structure.valid is True
    assert result.structure.root_total == 7
    assert result.leaf_partitions_count == 1
    assert result.positive_leaf_partitions == 1
    assert result.root_total.request_kind == "root_total"
    assert result.category_tree.request_kind == "category_tree"
    assert len(result.tree_digest) == 64
    assert len(result.digest) == 64
    assert result.production_authority is False
    public = json.dumps(result.canonical_dict(), sort_keys=True)
    assert '"value": "supermercado"' not in public
    assert '"facet_value"' not in public
    assert '"facet_key"' not in public
    assert "category-1" not in public


def test_digest_es_reproducible_para_misma_evidencia() -> None:
    root = _platform("root_total")
    tree = _platform("category_tree")
    first = build_verified_structural_discovery(root_total=root, category_tree=tree)
    second = build_verified_structural_discovery(root_total=root, category_tree=tree)
    assert first.digest == second.digest
    assert first.tree_digest == second.tree_digest


def test_total_cambiado_entre_root_y_tree_falla() -> None:
    with pytest.raises(StructuralDiscoveryManifestError) as captured:
        build_verified_structural_discovery(
            root_total=_platform("root_total", total=7),
            category_tree=_platform("category_tree", total=8),
        )
    assert captured.value.code == "structural_total_changed"


def test_release_distinto_falla_aunque_cada_trace_reconcilie() -> None:
    with pytest.raises(StructuralDiscoveryManifestError) as captured:
        build_verified_structural_discovery(
            root_total=_platform("root_total"),
            category_tree=_platform("category_tree", release_id="release-other"),
        )
    assert captured.value.code == "structural_run_context_collector_release_id_mismatch"


def test_public_key_distinta_falla() -> None:
    with pytest.raises(StructuralDiscoveryManifestError) as captured:
        build_verified_structural_discovery(
            root_total=_platform("root_total", public_key_hash="e" * 64),
            category_tree=_platform("category_tree", public_key_hash="f" * 64),
        )
    assert captured.value.code == "structural_run_public_key_mismatch"


def test_reutilizar_request_id_reservation_o_nonce_falla() -> None:
    root = _platform("root_total")
    with pytest.raises(StructuralDiscoveryManifestError) as request_id:
        build_verified_structural_discovery(
            root_total=root,
            category_tree=_platform("category_tree", request_id="request-root"),
        )
    assert request_id.value.code == "structural_request_id_reused"

    with pytest.raises(StructuralDiscoveryManifestError) as reservation:
        build_verified_structural_discovery(
            root_total=root,
            category_tree=_platform("category_tree", reservation_id="reservation-root"),
        )
    assert reservation.value.code == "structural_reservation_id_reused"

    with pytest.raises(StructuralDiscoveryManifestError) as nonce:
        build_verified_structural_discovery(
            root_total=root,
            category_tree=_platform("category_tree", nonce="nonce-root"),
        )
    assert nonce.value.code == "structural_nonce_reused"


def test_reutilizar_fetch_span_falla_como_evidencia_no_independiente() -> None:
    with pytest.raises(StructuralDiscoveryManifestError) as captured:
        build_verified_structural_discovery(
            root_total=_platform("root_total", fetch_span_id="fetch-shared"),
            category_tree=_platform("category_tree", fetch_span_id="fetch-shared"),
        )
    assert captured.value.code == "structural_pair_structural_fetch_span_reused"


def test_pacing_de_1_5_segundos_debe_quedar_demostrado() -> None:
    root = _platform("root_total", start=BASE)
    too_soon = BASE + timedelta(seconds=1)
    with pytest.raises(StructuralDiscoveryManifestError) as captured:
        build_verified_structural_discovery(
            root_total=root,
            category_tree=_platform("category_tree", start=too_soon),
        )
    assert captured.value.code == "structural_request_pacing_not_demonstrated"


def test_sampling_falla_antes_de_derivar_universo() -> None:
    with pytest.raises(StructuralDiscoveryManifestError) as captured:
        build_verified_structural_discovery(
            root_total=_platform("root_total"),
            category_tree=_platform("category_tree", sampling=True),
        )
    assert captured.value.code == "structural_facets_sampling_detected"


def test_arbol_que_no_cubre_root_falla_cerrado() -> None:
    with pytest.raises(StructuralDiscoveryManifestError) as captured:
        build_verified_structural_discovery(
            root_total=_platform("root_total", total=7),
            category_tree=_platform("category_tree", total=7, complete=False),
        )
    assert captured.value.code == "structural_report_invalid"


def test_parametros_no_permiten_intercambiar_root_y_tree() -> None:
    with pytest.raises(StructuralDiscoveryManifestError) as captured:
        build_verified_structural_discovery(
            root_total=_platform("category_tree"),
            category_tree=_platform("root_total"),
        )
    assert captured.value.code == "root_total_request_kind_mismatch"
