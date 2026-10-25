"""Frontera offline entre el gateway edge y el payload consumible por el scraper.

Esta capa sólo entrega una página cuando el gateway produjo evidencia estructural
coherente y los bytes firmados pasan la validación GraphQL estricta. No verifica
la firma contra una clave pública productiva y nunca concede autoridad comercial.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, NoReturn

from precios_supermercados.edge_gateway_client import (
    EdgeExecutionRequest,
    EdgeGatewayClient,
    EdgeGatewayDenied,
    EdgeGatewayEvidence,
    EdgeGatewayWait,
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


class EdgeCatalogPageError(RuntimeError):
    """La ejecución no produjo una página apta para el parser comercial."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


def _fail(code: str, message: str | None = None) -> NoReturn:
    raise EdgeCatalogPageError(code, message)


@dataclass(frozen=True, slots=True)
class ValidatedEdgeCatalogPage:
    """Página estructuralmente validada, todavía sin autoridad productiva."""

    request: ValidatedLaColoniaEdgeRequest
    evidence: EdgeGatewayEvidence
    body: ValidatedLaColoniaEdgeBody
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


class EdgeCatalogPageFetcher:
    """Convierte un execute del gateway en una página GraphQL validada."""

    def __init__(self, client: EdgeGatewayClient) -> None:
        if not isinstance(client, EdgeGatewayClient):
            _fail("edge_gateway_client_invalid")
        self._client = client

    def fetch(
        self,
        request: EdgeExecutionRequest,
        *,
        bearer_token: str,
    ) -> ValidatedEdgeCatalogPage:
        if not isinstance(request, EdgeExecutionRequest):
            _fail("edge_execution_request_invalid")

        result = self._client.execute(request, bearer_token=bearer_token)
        if isinstance(result, EdgeGatewayWait):
            _fail(
                "edge_gateway_wait",
                f"gateway WAIT: {result.reason}; not_before_ms={result.not_before_ms}",
            )
        if isinstance(result, EdgeGatewayDenied):
            _fail("edge_gateway_denied", f"gateway DENY: {result.reason}")
        if not isinstance(result, EdgeGatewayEvidence):
            _fail("edge_gateway_result_invalid")

        try:
            validated_request = validate_la_colonia_edge_request(request.origin_url)
        except LaColoniaEdgeRequestError as exc:
            raise EdgeCatalogPageError(f"edge_request_{exc.code}") from exc
        if validated_request.canonical_request_sha256 != request.context.request_digest:
            _fail("edge_request_digest_mismatch")

        try:
            validated_body = validate_la_colonia_edge_body(
                result.raw_body,
                validated_request,
            )
        except LaColoniaEdgeBodyError as exc:
            raise EdgeCatalogPageError(f"edge_body_{exc.code}") from exc

        return ValidatedEdgeCatalogPage(
            request=validated_request,
            evidence=result,
            body=validated_body,
        )
