"""Cliente fail-closed para la ruta edge de catálogo context-bound a SPS.

La ruta legacy ``/v1/execute`` permanece intacta para evidencia histórica. Esta
superficie exige siempre ``CatalogEdgeLocationContext`` y un receipt v3; un
response v2, un contexto omitido o cualquier mismatch de fingerprint se rechaza
antes de entregar evidencia a la capa criptográfica.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import NoReturn, TypeAlias

from precios_supermercados.catalog_context_provenance import (
    CatalogContextProvenanceError,
    context_bound_edge_receipt_payload_from_mapping,
)
from precios_supermercados.catalog_location_context import CatalogEdgeLocationContext
from precios_supermercados.edge_gateway_client import (
    MAX_RAW_BODY_BYTES,
    EdgeGatewayClient,
    EdgeGatewayClientError,
    EdgeGatewayDenied,
    EdgeGatewayEvidence,
    EdgeGatewayTransport,
    EdgeGatewayWait,
    EdgeRequestContext,
    _canonical_b64url_decode,
    _exact_keys,
    _integer,
    _opaque,
    _sha256,
    _text,
    _validate_origin_request,
    _worker_evidence_id,
)
from precios_supermercados.edge_provenance import SignedEdgeReceipt
from precios_supermercados.la_colonia_edge_request import (
    LA_COLONIA_HOST,
    LA_COLONIA_PATH,
    ValidatedLaColoniaEdgeRequest,
)

CATALOG_CONTEXT_EXECUTE_PATH = "/v1/catalog-execute"


class CatalogContextGatewayClientError(ValueError):
    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


def _fail(code: str, message: str | None = None) -> NoReturn:
    raise CatalogContextGatewayClientError(code, message)


def _translate_client_error(exc: EdgeGatewayClientError) -> CatalogContextGatewayClientError:
    return CatalogContextGatewayClientError(exc.code)


@dataclass(frozen=True, slots=True)
class ContextBoundCatalogExecutionRequest:
    origin_url: str
    context: EdgeRequestContext
    location_context: CatalogEdgeLocationContext
    _validated_origin: ValidatedLaColoniaEdgeRequest = dataclass_field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.context, EdgeRequestContext):
            _fail("catalog_request_context_invalid")
        if not isinstance(self.location_context, CatalogEdgeLocationContext):
            _fail("catalog_location_context_invalid")
        try:
            validated = _validate_origin_request(self.origin_url)
        except EdgeGatewayClientError as exc:
            raise _translate_client_error(exc) from exc
        if validated.canonical_request_sha256 != self.context.request_digest:
            _fail("catalog_request_digest_origin_mismatch")
        object.__setattr__(self, "origin_url", validated.source_url)
        object.__setattr__(self, "_validated_origin", validated)

    @property
    def validated_origin(self) -> ValidatedLaColoniaEdgeRequest:
        return self._validated_origin

    def wire_payload(self) -> dict[str, object]:
        return {
            "locationContext": self.location_context.wire_dict(),
            "originUrl": self.origin_url,
            "requestContext": self.context.wire_dict(),
        }


CatalogContextExecutionResult: TypeAlias = (
    EdgeGatewayEvidence | EdgeGatewayWait | EdgeGatewayDenied
)


class CatalogContextGatewayClient:
    """Ejecuta sólo requests de catálogo con contexto SPS obligatorio."""

    def __init__(self, transport: EdgeGatewayTransport) -> None:
        if transport is None or not callable(getattr(transport, "post_json", None)):
            _fail("catalog_transport_invalid")
        self._transport = transport

    @staticmethod
    def _bearer(value: object) -> str:
        try:
            token = _text(value, "bearer_token_invalid", max_length=20_000)
        except EdgeGatewayClientError as exc:
            raise _translate_client_error(exc) from exc
        if any(character.isspace() for character in token):
            _fail("bearer_token_invalid")
        return token

    @staticmethod
    def _root_or_error(response: object) -> Mapping[str, object]:
        try:
            return EdgeGatewayClient._root_or_error(response)
        except EdgeGatewayClientError as exc:
            raise _translate_client_error(exc) from exc

    @staticmethod
    def _wait(root: Mapping[str, object]) -> EdgeGatewayWait:
        try:
            return EdgeGatewayClient._parse_wait(root)
        except EdgeGatewayClientError as exc:
            raise _translate_client_error(exc) from exc

    @staticmethod
    def _deny(root: Mapping[str, object]) -> EdgeGatewayDenied:
        try:
            return EdgeGatewayClient._parse_deny(root)
        except EdgeGatewayClientError as exc:
            raise _translate_client_error(exc) from exc

    def execute(
        self,
        request: ContextBoundCatalogExecutionRequest,
        *,
        bearer_token: str,
    ) -> CatalogContextExecutionResult:
        if not isinstance(request, ContextBoundCatalogExecutionRequest):
            _fail("catalog_execution_request_invalid")
        try:
            response = self._transport.post_json(
                CATALOG_CONTEXT_EXECUTE_PATH,
                bearer_token=self._bearer(bearer_token),
                payload=request.wire_payload(),
            )
        except CatalogContextGatewayClientError:
            raise
        except Exception as exc:
            raise CatalogContextGatewayClientError("catalog_transport_error") from exc

        root = self._root_or_error(response)
        decision = root.get("decision")
        if decision == "WAIT":
            return self._wait(root)
        if decision == "DENY":
            return self._deny(root)
        if decision in {"ORIGIN_COMPLETED", "REPLAY_COMPLETED"}:
            return self._parse_evidence(root, request)
        _fail("catalog_execute_decision_invalid")

    @staticmethod
    def _parse_evidence(
        root: Mapping[str, object],
        request: ContextBoundCatalogExecutionRequest,
    ) -> EdgeGatewayEvidence:
        try:
            value = _exact_keys(
                root,
                {
                    "ok",
                    "decision",
                    "replayed",
                    "responseStatus",
                    "rawBodyB64Url",
                    "receiptPayload",
                    "signatureB64Url",
                    "signingKeyId",
                    "evidenceId",
                },
                "catalog_evidence_response_shape_invalid",
            )
        except EdgeGatewayClientError as exc:
            raise _translate_client_error(exc) from exc

        replayed = value["replayed"]
        if not isinstance(replayed, bool):
            _fail("catalog_evidence_replayed_invalid")
        if replayed is not (value["decision"] == "REPLAY_COMPLETED"):
            _fail("catalog_evidence_replay_decision_mismatch")

        try:
            status = _integer(
                value["responseStatus"],
                "catalog_evidence_status_invalid",
                minimum=100,
                maximum=599,
            )
            raw_body = _canonical_b64url_decode(
                value["rawBodyB64Url"],
                "catalog_evidence_raw_body_invalid",
            )
        except EdgeGatewayClientError as exc:
            raise _translate_client_error(exc) from exc
        if status != 200:
            _fail("catalog_evidence_status_not_success")
        if len(raw_body) > MAX_RAW_BODY_BYTES:
            _fail("catalog_evidence_raw_body_above_limit")

        receipt_source = value["receiptPayload"]
        try:
            payload = context_bound_edge_receipt_payload_from_mapping(receipt_source)
        except CatalogContextProvenanceError as exc:
            raise CatalogContextGatewayClientError(
                f"catalog_receipt_{exc.code}"
            ) from exc
        if payload.canonical_dict() != dict(receipt_source):  # type: ignore[arg-type]
            _fail("catalog_receipt_payload_noncanonical")

        try:
            signature = _text(
                value["signatureB64Url"],
                "catalog_receipt_signature_invalid",
                max_length=1024,
            )
            signature_bytes = _canonical_b64url_decode(
                signature,
                "catalog_receipt_signature_invalid",
            )
            signing_key_id = _opaque(
                value["signingKeyId"],
                "catalog_receipt_signing_key_id_invalid",
            )
        except EdgeGatewayClientError as exc:
            raise _translate_client_error(exc) from exc
        if len(signature_bytes) != 64:
            _fail("catalog_receipt_signature_length_invalid")
        if signing_key_id != payload.signing_key_id:
            _fail("catalog_receipt_signing_key_mismatch")
        try:
            receipt = SignedEdgeReceipt(
                payload=payload,  # type: ignore[arg-type]
                signature_b64url=signature,
            )
        except ValueError as exc:
            raise CatalogContextGatewayClientError(
                "catalog_signed_receipt_invalid"
            ) from exc

        if hashlib.sha256(raw_body).hexdigest() != payload.raw_response_sha256:
            _fail("catalog_receipt_raw_body_hash_mismatch")
        if len(raw_body) != payload.response_body_bytes:
            _fail("catalog_receipt_raw_body_size_mismatch")
        if status != payload.response_status:
            _fail("catalog_receipt_response_status_mismatch")
        if payload.canonical_request_sha256 != payload.request_digest:
            _fail("catalog_receipt_request_digest_mismatch")
        if payload.http_method != "GET":
            _fail("catalog_receipt_http_method_mismatch")
        if (
            payload.target_scheme != "https"
            or payload.target_host != LA_COLONIA_HOST
            or payload.target_path != LA_COLONIA_PATH
        ):
            _fail("catalog_receipt_target_mismatch")

        context = request.context
        expected_context = {
            "authorization_id": context.authorization_id,
            "run_id": context.run_id,
            "approved_commit_sha": context.approved_commit_sha,
            "reservation_id": context.reservation_id,
            "request_id": context.request_id,
            "request_digest": context.request_digest,
            "nonce": context.nonce,
            "traversal_role": context.traversal_role,
            "traversal_id": context.traversal_id,
            "partition_id": context.partition_id,
        }
        for name, expected in expected_context.items():
            if getattr(payload, name) != expected:
                _fail(f"catalog_receipt_{name}_context_mismatch")

        independent = request.validated_origin
        if payload.canonical_request_sha256 != independent.canonical_request_sha256:
            _fail("catalog_receipt_canonical_request_independent_mismatch")
        if payload.from_index != independent.from_index or payload.to_index != independent.to_index:
            _fail("catalog_receipt_range_independent_mismatch")
        if payload.order_by != independent.order_by:
            _fail("catalog_receipt_order_by_independent_mismatch")

        location = request.location_context.public_dict()
        expected_location = {
            "location_id": location["location_id"],
            "binding_source_key": location["binding_source_key"],
            "binding_evidence": location["binding_evidence"],
            "context_fingerprint": location["context_fingerprint"],
            "context_placement": location["placement"],
            "context_wire_key": location["wire_key"],
            "context_value_path": tuple(location["value_path"]),
            "wire_request_fingerprint": location["wire_request_fingerprint"],
        }
        for name, expected in expected_location.items():
            if getattr(payload, name) != expected:
                _fail(f"catalog_receipt_{name}_mismatch")

        if payload.collector_provider != "cloudflare_workers":
            _fail("catalog_receipt_collector_provider_mismatch")
        if payload.run_id != f"{payload.github_run_id}:{payload.github_run_attempt}":
            _fail("catalog_receipt_github_run_fence_mismatch")

        try:
            evidence_id = _sha256(value["evidenceId"], "catalog_evidence_id_invalid")
        except EdgeGatewayClientError as exc:
            raise _translate_client_error(exc) from exc
        if not isinstance(receipt_source, Mapping):
            _fail("catalog_receipt_payload_shape_invalid")
        if evidence_id != _worker_evidence_id(receipt_source, signature):
            _fail("catalog_evidence_id_mismatch")

        return EdgeGatewayEvidence(
            response_status=status,
            raw_body=raw_body,
            receipt=receipt,
            worker_evidence_id=evidence_id,
            replayed=replayed,
        )
