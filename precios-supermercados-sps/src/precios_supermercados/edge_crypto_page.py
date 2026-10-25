"""Página edge con request, body y receipt verificados criptográficamente.

La capa vuelve a derivar request/body desde los bytes de evidencia y verifica la
firma Ed25519 con un keyring público confiable. Aun así, una firma válida no
prueba por sí sola independencia física/productiva, por lo que
``production_authority`` permanece siempre en ``False``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, NoReturn

from precios_supermercados.edge_catalog_page import ValidatedEdgeCatalogPage
from precios_supermercados.edge_provenance import canonical_json_bytes
from precios_supermercados.edge_receipt_crypto import (
    CryptographicallyVerifiedEdgeReceipt,
    Ed25519ReceiptVerifier,
    EdgeReceiptCryptoError,
)
from precios_supermercados.la_colonia_edge_body import (
    LaColoniaEdgeBodyError,
    ValidatedLaColoniaEdgeBody,
    validate_la_colonia_edge_body,
)
from precios_supermercados.la_colonia_edge_request import (
    LaColoniaEdgeRequestError,
    ValidatedLaColoniaEdgeRequest,
    validate_la_colonia_edge_request,
)


class EdgeCryptoPageError(ValueError):
    """Fallo al convertir una página estructural en evidencia criptográfica."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


def _fail(code: str, message: str | None = None) -> NoReturn:
    raise EdgeCryptoPageError(code, message)


def _worker_evidence_id(payload: Mapping[str, object], signature_b64url: str) -> str:
    material = canonical_json_bytes(dict(payload)) + b"\0" + signature_b64url.encode("ascii")
    return hashlib.sha256(material).hexdigest()


@dataclass(frozen=True, slots=True)
class CryptographicallyVerifiedEdgeCatalogPage:
    """Página íntegra respecto de una clave pública permitida, no productiva."""

    request: ValidatedLaColoniaEdgeRequest
    body: ValidatedLaColoniaEdgeBody
    verified_receipt: CryptographicallyVerifiedEdgeReceipt
    worker_evidence_id: str
    replayed: bool
    cryptographic_signature_verified: bool = True
    production_authority: bool = False

    @property
    def payload(self) -> Mapping[str, Any]:
        return self.body.payload

    @property
    def source_url(self) -> str:
        return self.request.source_url

    @property
    def page_size(self) -> int:
        return self.request.page_size


class EdgeCatalogPageCryptoVerifier:
    """Revalida toda la frontera antes de aceptar la autenticidad del receipt."""

    def __init__(self, receipt_verifier: Ed25519ReceiptVerifier) -> None:
        if not isinstance(receipt_verifier, Ed25519ReceiptVerifier):
            _fail("receipt_verifier_invalid")
        self._receipt_verifier = receipt_verifier

    def verify(
        self,
        page: ValidatedEdgeCatalogPage,
    ) -> CryptographicallyVerifiedEdgeCatalogPage:
        if not isinstance(page, ValidatedEdgeCatalogPage):
            _fail("edge_catalog_page_invalid")

        evidence = page.evidence
        receipt = evidence.receipt
        payload = receipt.payload

        try:
            request = validate_la_colonia_edge_request(page.source_url)
        except LaColoniaEdgeRequestError as exc:
            raise EdgeCryptoPageError(f"request_{exc.code}") from exc

        try:
            verified_receipt = self._receipt_verifier.verify(receipt)
        except EdgeReceiptCryptoError as exc:
            raise EdgeCryptoPageError(f"receipt_crypto_{exc.code}") from exc

        expected_evidence_id = _worker_evidence_id(
            payload.canonical_dict(),
            receipt.signature_b64url,
        )
        if evidence.worker_evidence_id != expected_evidence_id:
            _fail("worker_evidence_id_mismatch")

        body_hash = hashlib.sha256(evidence.raw_body).hexdigest()
        if body_hash != payload.raw_response_sha256:
            _fail("receipt_raw_body_hash_mismatch")
        if len(evidence.raw_body) != payload.response_body_bytes:
            _fail("receipt_raw_body_size_mismatch")
        if evidence.response_status != 200 or payload.response_status != 200:
            _fail("receipt_response_status_invalid")

        expected_digest = request.canonical_request_sha256
        if payload.request_digest != expected_digest:
            _fail("receipt_request_digest_mismatch")
        if payload.canonical_request_sha256 != expected_digest:
            _fail("receipt_canonical_request_digest_mismatch")
        if payload.from_index != request.from_index or payload.to_index != request.to_index:
            _fail("receipt_request_range_mismatch")
        if payload.order_by != request.order_by:
            _fail("receipt_request_order_mismatch")
        if payload.http_method != "GET":
            _fail("receipt_http_method_mismatch")
        if (
            payload.target_scheme != "https"
            or payload.target_host != "www.lacolonia.com"
            or payload.target_path != "/_v/segment/graphql/v1"
        ):
            _fail("receipt_target_mismatch")

        try:
            body = validate_la_colonia_edge_body(evidence.raw_body, request)
        except LaColoniaEdgeBodyError as exc:
            raise EdgeCryptoPageError(f"body_{exc.code}") from exc

        return CryptographicallyVerifiedEdgeCatalogPage(
            request=request,
            body=body,
            verified_receipt=verified_receipt,
            worker_evidence_id=evidence.worker_evidence_id,
            replayed=evidence.replayed,
        )
