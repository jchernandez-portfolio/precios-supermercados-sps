from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import sys
from collections.abc import Mapping
from datetime import UTC, datetime, timezone
from pathlib import Path
from types import ModuleType

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from precios_supermercados.catalog_context_gateway_client import CatalogContextGatewayClient
from precios_supermercados.catalog_context_provenance import ContextBoundEdgeReceiptPayload
from precios_supermercados.cloudflare_structural_trace_evidence import (
    PlatformReconciledStructuralObservation,
)
from precios_supermercados.edge_crypto_page import EdgeCatalogPageCryptoVerifier
from precios_supermercados.edge_gateway_client import EdgeRequestContext
from precios_supermercados.edge_provenance import (
    EdgeReceiptPayload,
    SignedEdgeReceipt,
    canonical_json_bytes,
)
from precios_supermercados.edge_receipt_crypto import (
    Ed25519ReceiptVerifier,
    receipt_signing_bytes,
)
from precios_supermercados.edge_structural_observation import (
    CryptographicallyVerifiedStructuralObservation,
)
from precios_supermercados.scrapers.la_colonia_context_bound_catalog_transport import (
    ContextBoundVerifiedCatalogEdgeCollector,
    ContextBoundVerifiedCatalogTransportError,
)
from precios_supermercados.sps_context_bound_discovery import (
    bind_verified_structural_discovery_to_sps,
)
from precios_supermercados.structural_discovery_manifest import (
    build_verified_structural_discovery,
)
from precios_supermercados.structural_receipt_crypto import VerifiedStructuralReceipt

TESTS = Path(__file__).parent


def _helper(filename: str, module_name: str) -> ModuleType:
    path = TESTS / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


SPS_HELPER = _helper(
    "test_sps_context_bound_discovery.py",
    "precios_sps_context_bound_helper_for_catalog_collector",
)


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _spki(private_key: Ed25519PrivateKey) -> tuple[str, str]:
    public_der = private_key.public_key().public_bytes(
        Encoding.DER,
        PublicFormat.SubjectPublicKeyInfo,
    )
    return _b64url(public_der), hashlib.sha256(public_der).hexdigest()


def _with_public_key_hash(
    value: PlatformReconciledStructuralObservation,
    key_hash: str,
) -> PlatformReconciledStructuralObservation:
    observation = value.observation
    verified = observation.verified_receipt
    changed_verified = VerifiedStructuralReceipt(
        receipt=verified.receipt,
        signing_key_id=verified.signing_key_id,
        public_key_spki_sha256=key_hash,
        receipt_digest=verified.receipt_digest,
    )
    changed_observation = CryptographicallyVerifiedStructuralObservation(
        request=observation.request,
        body=observation.body,
        verified_receipt=changed_verified,
        raw_body_sha256=observation.raw_body_sha256,
    )
    return PlatformReconciledStructuralObservation(
        observation=changed_observation,
        trace_evidence=value.trace_evidence,
    )


def _proof(private_key: Ed25519PrivateKey):
    binding, plan = SPS_HELPER._plan()
    _public, key_hash = _spki(private_key)
    root = _with_public_key_hash(
        SPS_HELPER._contextual_platform("root_total", plan),
        key_hash,
    )
    tree = _with_public_key_hash(
        SPS_HELPER._contextual_platform("category_tree", plan),
        key_hash,
    )
    discovery = build_verified_structural_discovery(
        root_total=root,
        category_tree=tree,
    )
    proof = bind_verified_structural_discovery_to_sps(
        discovery,
        {
            "root_total": root.observation,
            "category_tree": tree.observation,
        },
        plan,
        binding=binding,
    )
    return proof


def _products(order_by: str, start: int, end: int, total: int) -> list[dict[str, object]]:
    identities = list(range(1, total + 1))
    if order_by == "OrderByNameDESC":
        identities.reverse()
    selected = identities[start : min(end + 1, total)]
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


class _ContextSigningTransport:
    def __init__(
        self,
        private_key: Ed25519PrivateKey,
        proof,
        *,
        response_mode: str = "v3",
    ) -> None:
        self.private_key = private_key
        self.proof = proof
        self.response_mode = response_mode
        self.calls: list[tuple[str, str, Mapping[str, object]]] = []

    def post_json(self, path, *, bearer_token, payload):
        from precios_supermercados.la_colonia_edge_request import (
            validate_la_colonia_edge_request,
        )

        assert path == "/v1/catalog-execute"
        assert bearer_token == "oidc-token"
        self.calls.append((path, bearer_token, payload))
        request = validate_la_colonia_edge_request(payload["originUrl"])
        context = payload["requestContext"]
        location = payload["locationContext"]
        discovery = self.proof.discovery
        total = discovery.structure.root_total
        raw_body = json.dumps(
            {
                "data": {
                    "productSearch": {
                        "recordsFiltered": total,
                        "products": _products(
                            request.order_by,
                            request.from_index,
                            request.to_index,
                            total,
                        ),
                    }
                }
            },
            separators=(",", ":"),
        ).encode("utf-8")
        ordinal = len(self.calls)
        started = datetime(2026, 8, 24, 16, 0, ordinal, tzinfo=UTC)
        completed = datetime(
            2026,
            8,
            24,
            16,
            0,
            ordinal,
            100_000,
            tzinfo=UTC,
        )
        base = EdgeReceiptPayload(
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
            physical_started_at_utc=started,
            response_completed_at_utc=completed,
            github_repository=discovery.github_repository,
            github_repository_id=discovery.github_repository_id,
            github_ref=discovery.github_ref,
            github_workflow_ref=discovery.github_workflow_ref,
            github_environment=discovery.github_environment,
            github_run_id=discovery.github_run_id,
            github_run_attempt=discovery.github_run_attempt,
            oidc_subject=discovery.oidc_subject,
            oidc_jti=f"oidc-jti-catalog-context-{ordinal}",
            collector_provider=discovery.collector_provider,
            collector_principal=discovery.collector_principal,
            collector_execution=f"execution-catalog-context-{ordinal}",
            collector_release_id=discovery.collector_release_id,
            collector_code_sha256=discovery.collector_code_sha256,
            signing_algorithm="Ed25519",
            signing_key_id=discovery.collector_signing_key_id,
            nonce=context["nonce"],
        )
        contextual = ContextBoundEdgeReceiptPayload(
            base=base,
            location_id=location["locationId"],
            binding_source_key=location["bindingSourceKey"],
            binding_evidence=location["bindingEvidence"],
            context_fingerprint=location["contextFingerprint"],
            context_placement=location["placement"],
            context_wire_key=location["wireKey"],
            context_value_path=tuple(location["valuePath"]),
            wire_request_fingerprint=location["wireRequestFingerprint"],
        )
        payload_dict = contextual.canonical_dict()
        if self.response_mode == "v2":
            payload_dict = base.canonical_dict()
        elif self.response_mode == "wrong_location":
            payload_dict["location_id"] = "la_colonia_tgu"

        provisional_payload = contextual if self.response_mode == "v3" else base
        provisional = SignedEdgeReceipt(
            payload=provisional_payload,  # type: ignore[arg-type]
            signature_b64url=_b64url(b"x" * 64),
        )
        if self.response_mode == "wrong_location":
            signature = _b64url(self.private_key.sign(
                b"precios-sps/edge-receipt-signature/v2\0"
                + canonical_json_bytes(payload_dict)
            ))
        else:
            signature = _b64url(
                self.private_key.sign(receipt_signing_bytes(provisional))
            )
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
            "signingKeyId": discovery.collector_signing_key_id,
            "evidenceId": evidence_id,
        }


class _StaticTransport:
    def __init__(self, response: Mapping[str, object]) -> None:
        self.response = response
        self.calls = 0

    def post_json(self, path, *, bearer_token, payload):
        self.calls += 1
        assert path == "/v1/catalog-execute"
        return self.response


def _crypto_verifier(private_key: Ed25519PrivateKey, proof) -> EdgeCatalogPageCryptoVerifier:
    public_b64, _hash = _spki(private_key)
    return EdgeCatalogPageCryptoVerifier(
        Ed25519ReceiptVerifier(
            {proof.discovery.collector_signing_key_id: public_b64}
        )
    )


def _context_provider(proof, *, reused_request_id: bool = False):
    discovery = proof.discovery

    def provider(expected, validated):
        suffix = (
            f"{expected.traversal_role}-{expected.partition_id}-"
            f"{expected.from_index}-{expected.to_index}"
        )
        return EdgeRequestContext(
            authorization_id=discovery.authorization_id,
            run_id=discovery.run_id,
            approved_commit_sha=discovery.approved_commit_sha,
            reservation_id=f"reservation-{suffix}",
            request_id="request-reused" if reused_request_id else f"request-{suffix}",
            request_digest=validated.canonical_request_sha256,
            nonce=f"nonce-{suffix}",
            traversal_role=expected.traversal_role,
            traversal_id=expected.traversal_id,
            partition_id=expected.partition_id,
        )

    return provider


def _collector(*, response_mode: str = "v3", reused_request_id: bool = False):
    private_key = Ed25519PrivateKey.generate()
    proof = _proof(private_key)
    transport = _ContextSigningTransport(
        private_key,
        proof,
        response_mode=response_mode,
    )
    collector = ContextBoundVerifiedCatalogEdgeCollector(
        CatalogContextGatewayClient(transport),
        _crypto_verifier(private_key, proof),
        proof,
        context_provider=_context_provider(
            proof,
            reused_request_id=reused_request_id,
        ),
        bearer_token_provider=lambda: "oidc-token",
    )
    return collector, transport, proof


def test_deriva_plan_desde_el_mismo_proof_y_cierra_ambos_traversals() -> None:
    collector, transport, proof = _collector()

    result = collector.collect_all()

    assert collector.authenticated_plan.discovery is proof.discovery
    assert collector.sps_context is proof
    assert collector.complete is True
    assert collector.requests_completed == collector.requests_expected == 2
    assert len(result.primary.pages) == 1
    assert len(result.reconciliation.pages) == 1
    assert result.discovery_digest == proof.discovery_digest
    assert result.structural_context_plan_digest == proof.plan_digest
    assert result.location_id == "la_colonia_sps"
    assert result.context_fingerprint == proof.context_fingerprint
    assert result.production_authority is False
    assert len(transport.calls) == 2
    assert {call[0] for call in transport.calls} == {"/v1/catalog-execute"}
    assert all("locationContext" in call[2] for call in transport.calls)
    assert all(
        observation.page.verified_receipt.receipt.payload.schema_version == "3"
        and observation.production_authority is False
        for observation in result.observations
    )
    wire_fingerprints = [item.wire_request_fingerprint for item in result.observations]
    assert len(set(wire_fingerprints)) == len(wire_fingerprints) == 2
    assert {item.context_fingerprint for item in result.observations} == {
        proof.context_fingerprint
    }

    raw_region = SPS_HELPER.PLAN_HELPER.RAW_REGION
    rendered = json.dumps(
        {
            "location_id": result.location_id,
            "context_fingerprint": result.context_fingerprint,
            "observations": [
                {
                    "location_id": item.location_id,
                    "context_fingerprint": item.context_fingerprint,
                    "wire_request_fingerprint": item.wire_request_fingerprint,
                }
                for item in result.observations
            ],
        },
        sort_keys=True,
    )
    assert raw_region not in rendered
    assert raw_region not in repr(result)


def test_discovery_suelto_no_puede_reemplazar_el_proof_sps() -> None:
    private_key = Ed25519PrivateKey.generate()
    proof = _proof(private_key)
    transport = _ContextSigningTransport(private_key, proof)

    with pytest.raises(ContextBoundVerifiedCatalogTransportError) as captured:
        ContextBoundVerifiedCatalogEdgeCollector(
            CatalogContextGatewayClient(transport),
            _crypto_verifier(private_key, proof),
            proof.discovery,  # type: ignore[arg-type]
            context_provider=_context_provider(proof),
            bearer_token_provider=lambda: "oidc-token",
        )
    assert captured.value.code == "verified_sps_structural_context_required"
    assert transport.calls == []


def test_receipt_v2_es_downgrade_y_falla_antes_de_crypto_usable() -> None:
    collector, transport, _proof_value = _collector(response_mode="v2")

    with pytest.raises(ContextBoundVerifiedCatalogTransportError) as captured:
        collector.collect_next()

    assert "catalog_context_receipt_shape_invalid" in captured.value.code
    assert len(transport.calls) == 1
    assert collector.requests_completed == 0


def test_receipt_con_otra_ubicacion_falla_cerrado() -> None:
    collector, transport, _proof_value = _collector(response_mode="wrong_location")

    with pytest.raises(ContextBoundVerifiedCatalogTransportError) as captured:
        collector.collect_next()

    assert "catalog_context_location_id_invalid" in captured.value.code
    assert len(transport.calls) == 1
    assert collector.requests_completed == 0


def test_request_id_no_puede_reutilizarse_entre_paginas() -> None:
    collector, transport, _proof_value = _collector(reused_request_id=True)
    collector.collect_next()

    with pytest.raises(ContextBoundVerifiedCatalogTransportError) as captured:
        collector.collect_next()

    assert captured.value.code == "catalog_context_request_id_reused"
    assert len(transport.calls) == 2
    assert collector.requests_completed == 1


def test_wait_detiene_sin_retry_y_sin_avanzar_plan() -> None:
    private_key = Ed25519PrivateKey.generate()
    proof = _proof(private_key)
    transport = _StaticTransport(
        {
            "ok": True,
            "decision": "WAIT",
            "reason": "pacing_not_elapsed",
            "notBeforeMs": 123,
            "inFlightReservationId": None,
        }
    )
    collector = ContextBoundVerifiedCatalogEdgeCollector(
        CatalogContextGatewayClient(transport),
        _crypto_verifier(private_key, proof),
        proof,
        context_provider=_context_provider(proof),
        bearer_token_provider=lambda: "oidc-token",
    )
    expected = collector.next_expected

    with pytest.raises(ContextBoundVerifiedCatalogTransportError) as captured:
        collector.collect_next()

    assert captured.value.code == "catalog_context_gateway_wait"
    assert transport.calls == 1
    assert collector.requests_completed == 0
    assert collector.next_expected == expected


def test_no_cierra_collection_si_falta_una_pagina() -> None:
    collector, _transport, _proof_value = _collector()
    collector.collect_next()

    with pytest.raises(ContextBoundVerifiedCatalogTransportError) as captured:
        collector.build_collection()

    assert captured.value.code == "catalog_context_plan_incomplete"
