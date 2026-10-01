from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from precios_supermercados.canonical_authenticated_provenance_plan import (
    CANONICAL_CATALOG_PAGE_SIZE,
    CANONICAL_PRIMARY_ORDER_BY,
    CANONICAL_RECONCILIATION_ORDER_BY,
)
from precios_supermercados.edge_crypto_page import EdgeCatalogPageCryptoVerifier
from precios_supermercados.edge_gateway_client import EdgeGatewayClient, EdgeRequestContext
from precios_supermercados.edge_provenance import (
    EdgeReceiptPayload,
    SignedEdgeReceipt,
    canonical_json_bytes,
)
from precios_supermercados.edge_receipt_crypto import (
    Ed25519ReceiptVerifier,
    receipt_signing_bytes,
)
from precios_supermercados.scrapers.la_colonia_catalog_partitions import (
    build_structural_discovery_report,
)
from precios_supermercados.scrapers.la_colonia_verified_catalog_transport import (
    VerifiedCatalogEdgeCollector,
    VerifiedCatalogTransportError,
)
from precios_supermercados.structural_discovery_manifest import (
    StructuralObservationRecord,
    VerifiedStructuralDiscovery,
)

RUN = "32540000000:1"
AUTH = "authorization-catalog-edge-001"
COMMIT = "a" * 40
CODE_SHA = "d" * 64
KEY_ID = "cloudflare-ed25519-catalog-v1"
RELEASE = "release-catalog-edge-001"
TOTAL = 51
NOW = datetime(2026, 8, 21, 23, 0, 0, tzinfo=timezone.utc)


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _spki(private_key: Ed25519PrivateKey) -> tuple[str, str]:
    public_der = private_key.public_key().public_bytes(
        Encoding.DER,
        PublicFormat.SubjectPublicKeyInfo,
    )
    return _b64url(public_der), hashlib.sha256(public_der).hexdigest()


def _structure(total: int = TOTAL):
    return build_structural_discovery_report(
        [
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
        run_id=RUN,
        root_total=total,
        sampling=False,
        max_partitions=250,
        max_category_level=8,
    )


def _record(
    kind: str,
    *,
    key_hash: str,
    digest_char: str,
    total: int = TOTAL,
) -> StructuralObservationRecord:
    return StructuralObservationRecord(
        request_kind=kind,
        request_digest=digest_char * 64,
        request_id=f"request-{kind}",
        reservation_id=f"reservation-{kind}",
        nonce=f"nonce-{kind}",
        receipt_digest=("1" if kind == "root_total" else "2") * 64,
        public_key_spki_sha256=key_hash,
        physical_evidence_id=("3" if kind == "root_total" else "4") * 64,
        trace_id=f"trace-{kind}",
        custom_span_id=f"custom-{kind}",
        fetch_span_id=f"fetch-{kind}",
        raw_response_sha256=("5" if kind == "root_total" else "6") * 64,
        response_body_bytes=100 if kind == "root_total" else 200,
        records_filtered=total,
        physical_started_at_utc=NOW,
        response_completed_at_utc=NOW,
    )


def _discovery(
    private_key: Ed25519PrivateKey,
    *,
    total: int = TOTAL,
) -> VerifiedStructuralDiscovery:
    _public_b64, key_hash = _spki(private_key)
    structure = _structure(total)
    return VerifiedStructuralDiscovery(
        run_id=RUN,
        authorization_id=AUTH,
        approved_commit_sha=COMMIT,
        github_repository="jchernandez-portfolio/precios-supermercados-sps",
        github_repository_id="1282475205",
        github_ref="refs/heads/main",
        github_workflow_ref="jchernandez-portfolio/precios-supermercados-sps/.github/workflows/precios-supermercados-sps-la-colonia-live.yml@refs/heads/main",
        github_environment="la-colonia-live",
        github_run_id="32540000000",
        github_run_attempt=1,
        oidc_subject="repo:jchernandez-portfolio/precios-supermercados-sps:environment:la-colonia-live",
        collector_provider="cloudflare_workers",
        collector_principal="cloudflare-worker:precios-sps-provenance",
        collector_release_id=RELEASE,
        collector_code_sha256=CODE_SHA,
        collector_signing_key_id=KEY_ID,
        root_total=_record(
            "root_total",
            key_hash=key_hash,
            digest_char="7",
            total=total,
        ),
        category_tree=_record(
            "category_tree",
            key_hash=key_hash,
            digest_char="8",
            total=total,
        ),
        tree_digest=structure.tree_digest,
        leaf_partitions_count=1,
        positive_leaf_partitions=1,
        structure=structure,
    )


def _products(
    order_by: str,
    start: int,
    end: int,
    *,
    total: int = TOTAL,
) -> list[dict[str, object]]:
    identities = list(range(1, total + 1))
    if order_by == CANONICAL_RECONCILIATION_ORDER_BY:
        identities.reverse()
    selected = identities[start : min(end + 1, len(identities))]
    return [
        {
            "productId": f"product-{value}",
            "productReference": f"reference-{value}",
            "linkText": f"product-{value}",
            "categories": ["supermercado"],
            "items": [{"itemId": f"sku-{value}"}],
        }
        for value in selected
    ]


class _SigningTransport:
    def __init__(
        self,
        private_key: Ed25519PrivateKey,
        *,
        release_id: str = RELEASE,
        total: int = TOTAL,
    ) -> None:
        self.private_key = private_key
        self.release_id = release_id
        self.total = total
        self.calls: list[dict[str, object]] = []

    def post_json(self, path, *, bearer_token, payload):
        from precios_supermercados.la_colonia_edge_request import (
            validate_la_colonia_edge_request,
        )

        assert path == "/v1/execute"
        assert bearer_token == "oidc-token"
        self.calls.append(dict(payload))
        request = validate_la_colonia_edge_request(payload["originUrl"])
        context = payload["requestContext"]
        raw_body = json.dumps(
            {
                "data": {
                    "productSearch": {
                        "recordsFiltered": self.total,
                        "products": _products(
                            request.order_by,
                            request.from_index,
                            request.to_index,
                            total=self.total,
                        ),
                    }
                }
            },
            separators=(",", ":"),
        ).encode("utf-8")
        call_number = len(self.calls)
        receipt_payload = EdgeReceiptPayload(
            run_id=context["runId"],
            request_id=context["requestId"],
            reservation_id=context["reservationId"],
            authorization_id=context["authorizationId"],
            approved_commit_sha=context["approvedCommitSha"],
            request_digest=context["requestDigest"],
            traversal_id=context["traversalId"],
            traversal_role=context["traversalRole"],
            order_by=request.order_by,
            partition_id=context["partitionId"],
            from_index=request.from_index,
            to_index=request.to_index,
            http_method="GET",
            target_scheme="https",
            target_host="www.lacolonia.com",
            target_path="/_v/segment/graphql/v1",
            canonical_request_sha256=request.canonical_request_sha256,
            raw_response_sha256=hashlib.sha256(raw_body).hexdigest(),
            response_status=200,
            response_body_bytes=len(raw_body),
            physical_started_at_utc=datetime(
                2026, 8, 21, 23, 0, call_number,
                tzinfo=timezone.utc,
            ),
            response_completed_at_utc=datetime(
                2026, 8, 21, 23, 0, call_number, 100_000,
                tzinfo=timezone.utc,
            ),
            github_repository="jchernandez-portfolio/precios-supermercados-sps",
            github_repository_id="1282475205",
            github_ref="refs/heads/main",
            github_workflow_ref="jchernandez-portfolio/precios-supermercados-sps/.github/workflows/precios-supermercados-sps-la-colonia-live.yml@refs/heads/main",
            github_environment="la-colonia-live",
            github_run_id="32540000000",
            github_run_attempt=1,
            oidc_subject="repo:jchernandez-portfolio/precios-supermercados-sps:environment:la-colonia-live",
            oidc_jti="oidc-jti-catalog-001",
            collector_provider="cloudflare_workers",
            collector_principal="cloudflare-worker:precios-sps-provenance",
            collector_execution=f"execution-catalog-{call_number}",
            collector_release_id=self.release_id,
            collector_code_sha256=CODE_SHA,
            signing_algorithm="Ed25519",
            signing_key_id=KEY_ID,
            nonce=context["nonce"],
        )
        provisional = SignedEdgeReceipt(
            payload=receipt_payload,
            signature_b64url=_b64url(b"x" * 64),
        )
        signature = _b64url(
            self.private_key.sign(receipt_signing_bytes(provisional))
        )
        payload_dict = receipt_payload.canonical_dict()
        evidence_id = hashlib.sha256(
            canonical_json_bytes(payload_dict)
            + b"\0"
            + signature.encode("ascii")
        ).hexdigest()
        return {
            "ok": True,
            "decision": "ORIGIN_COMPLETED",
            "replayed": False,
            "responseStatus": 200,
            "rawBodyB64Url": _b64url(raw_body),
            "receiptPayload": payload_dict,
            "signatureB64Url": signature,
            "signingKeyId": KEY_ID,
            "evidenceId": evidence_id,
        }


class _StaticTransport:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls = 0

    def post_json(self, path, *, bearer_token, payload):
        self.calls += 1
        return self.response


def _crypto_verifier(private_key: Ed25519PrivateKey) -> EdgeCatalogPageCryptoVerifier:
    public_b64, _hash = _spki(private_key)
    return EdgeCatalogPageCryptoVerifier(
        Ed25519ReceiptVerifier({KEY_ID: public_b64})
    )


def _context_provider(expected, validated):
    suffix = (
        f"{expected.traversal_role}-{expected.partition_id}-"
        f"{expected.from_index}-{expected.to_index}"
    )
    return EdgeRequestContext(
        authorization_id=AUTH,
        run_id=RUN,
        approved_commit_sha=COMMIT,
        reservation_id=f"reservation-{suffix}",
        request_id=f"request-{suffix}",
        request_digest=validated.canonical_request_sha256,
        nonce=f"nonce-{suffix}",
        traversal_role=expected.traversal_role,
        traversal_id=expected.traversal_id,
        partition_id=expected.partition_id,
    )


def _collector(
    discovery_key: Ed25519PrivateKey | None = None,
    *,
    signing_key: Ed25519PrivateKey | None = None,
    release_id: str = RELEASE,
    context_provider=_context_provider,
):
    trusted_discovery_key = discovery_key or Ed25519PrivateKey.generate()
    signer = signing_key or trusted_discovery_key
    discovery = _discovery(trusted_discovery_key)
    transport = _SigningTransport(signer, release_id=release_id)
    collector = VerifiedCatalogEdgeCollector(
        EdgeGatewayClient(transport),
        _crypto_verifier(signer),
        discovery,
        context_provider=context_provider,
        bearer_token_provider=lambda: "oidc-token",
    )
    return collector, transport, discovery


def test_deriva_plan_canonico_y_cierra_primary_reconciliation() -> None:
    collector, transport, discovery = _collector()
    plan = collector.authenticated_plan
    result = collector.collect_all()

    assert plan.discovery is discovery
    assert plan.plan.page_size == CANONICAL_CATALOG_PAGE_SIZE == 50
    assert plan.plan.primary_order_by == CANONICAL_PRIMARY_ORDER_BY == "OrderByNameASC"
    assert plan.plan.reconciliation_order_by == CANONICAL_RECONCILIATION_ORDER_BY == "OrderByNameDESC"
    assert collector.complete is True
    assert collector.requests_completed == plan.request_count == 4
    assert len(result.primary.pages) == 2
    assert len(result.reconciliation.pages) == 2
    assert result.production_authority is False
    assert all(
        observation.page.cryptographic_signature_verified is True
        and observation.production_authority is False
        for observation in result.observations
    )
    identities = {
        product.identity
        for observation in result.observations
        for product in observation.raw_evidence.products
    }
    assert {"productId:product-1", "productId:product-51"} <= identities
    assert len(transport.calls) == 4


def test_requests_se_reconstruyen_desde_leaf_verificada_sin_url_del_caller() -> None:
    collector, transport, discovery = _collector()
    plan = collector.authenticated_plan
    leaf = discovery.structure.valid_leaves[0]

    for expected in plan.pages:
        observed = collector.collect_next()
        assert observed.expected == expected

    from precios_supermercados.la_colonia_edge_request import (
        validate_la_colonia_edge_request,
    )

    for call in transport.calls:
        request = validate_la_colonia_edge_request(call["originUrl"])
        assert request.selected_facets == ((leaf.facet_key, leaf.facet_value),)
        assert request.query_value == leaf.facet_value
    with pytest.raises(VerifiedCatalogTransportError) as captured:
        collector.collect_next()
    assert captured.value.code == "catalog_plan_already_complete"


def test_contexto_no_puede_cambiar_traversal_del_plan() -> None:
    def wrong_context(expected, validated):
        base = _context_provider(expected, validated)
        return EdgeRequestContext(
            authorization_id=base.authorization_id,
            run_id=base.run_id,
            approved_commit_sha=base.approved_commit_sha,
            reservation_id=base.reservation_id,
            request_id=base.request_id,
            request_digest=base.request_digest,
            nonce=base.nonce,
            traversal_role=base.traversal_role,
            traversal_id="traversal-attacker",
            partition_id=base.partition_id,
        )

    collector, transport, _discovery_value = _collector(context_provider=wrong_context)
    with pytest.raises(VerifiedCatalogTransportError) as captured:
        collector.collect_next()
    assert captured.value.code == "catalog_request_context_traversal_id_mismatch"
    assert transport.calls == []


def test_release_firmado_distinto_del_discovery_falla_despues_de_crypto() -> None:
    collector, transport, _discovery_value = _collector(release_id="release-attacker")
    with pytest.raises(VerifiedCatalogTransportError) as captured:
        collector.collect_next()
    assert captured.value.code == "catalog_receipt_collector_release_id_mismatch"
    assert len(transport.calls) == 1
    assert collector.requests_completed == 0


def test_clave_publica_distinta_de_discovery_no_se_acepta() -> None:
    discovery_key = Ed25519PrivateKey.generate()
    signing_key = Ed25519PrivateKey.generate()
    collector, transport, _discovery_value = _collector(
        discovery_key,
        signing_key=signing_key,
    )
    with pytest.raises(VerifiedCatalogTransportError) as captured:
        collector.collect_next()
    assert captured.value.code == "catalog_receipt_public_key_mismatch"
    assert len(transport.calls) == 1
    assert collector.requests_completed == 0


def test_request_id_no_puede_reutilizarse_entre_paginas() -> None:
    def reused_request_id(expected, validated):
        base = _context_provider(expected, validated)
        return EdgeRequestContext(
            authorization_id=base.authorization_id,
            run_id=base.run_id,
            approved_commit_sha=base.approved_commit_sha,
            reservation_id=base.reservation_id,
            request_id="request-reused",
            request_digest=base.request_digest,
            nonce=base.nonce,
            traversal_role=base.traversal_role,
            traversal_id=base.traversal_id,
            partition_id=base.partition_id,
        )

    collector, transport, _discovery_value = _collector(context_provider=reused_request_id)
    collector.collect_next()
    with pytest.raises(VerifiedCatalogTransportError) as captured:
        collector.collect_next()
    assert captured.value.code == "catalog_request_id_reused"
    assert len(transport.calls) == 2
    assert collector.requests_completed == 1


def test_wait_detiene_sin_retry_oculto_y_sin_avanzar_plan() -> None:
    key = Ed25519PrivateKey.generate()
    discovery = _discovery(key)
    transport = _StaticTransport(
        {
            "ok": True,
            "decision": "WAIT",
            "reason": "pacing",
            "notBeforeMs": 123,
            "inFlightReservationId": None,
        }
    )
    collector = VerifiedCatalogEdgeCollector(
        EdgeGatewayClient(transport),
        _crypto_verifier(key),
        discovery,
        context_provider=_context_provider,
        bearer_token_provider=lambda: "oidc-token",
    )
    expected = collector.next_expected
    with pytest.raises(VerifiedCatalogTransportError) as captured:
        collector.collect_next()
    assert captured.value.code == "catalog_edge_gateway_wait"
    assert transport.calls == 1
    assert collector.requests_completed == 0
    assert collector.next_expected == expected


def test_no_construye_collection_si_falta_una_sola_pagina() -> None:
    collector, _transport, _discovery_value = _collector()
    collector.collect_next()
    with pytest.raises(VerifiedCatalogTransportError) as captured:
        collector.build_collection()
    assert captured.value.code == "catalog_plan_incomplete"
