"""Finalización física del facet discovery autenticado de La Colonia.

Esta capa compone exclusivamente piezas ya verificadas:

1. ``VerifiedFacetDiscoveryEdgeTransport`` debe haber completado exactamente
   ``root_total`` y ``category_tree`` mediante el gateway edge;
2. cada observación se reconcilia contra Workers Observability usando un único
   ``CloudflareStructuralObservabilityVerifierClient``;
3. sólo entonces se construye ``VerifiedStructuralDiscovery``.

No implementa HTTP, no reintenta, no persiste bearer tokens y no concede
``production_authority``. Cualquier fallo en root corta antes de consultar tree.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import NoReturn

from precios_supermercados.cloudflare_structural_observability_verifier import (
    CloudflareStructuralObservabilityVerifierClient,
    CloudflareStructuralObservabilityVerifierError,
)
from precios_supermercados.edge_structural_observation import (
    CryptographicallyVerifiedStructuralObservation,
)
from precios_supermercados.scrapers.la_colonia_verified_facet_transport import (
    VerifiedFacetDiscoveryEdgeTransport,
)
from precios_supermercados.structural_discovery_manifest import (
    StructuralDiscoveryManifestError,
    VerifiedStructuralDiscovery,
    build_verified_structural_discovery,
)

_EXPECTED_KINDS = frozenset({"root_total", "category_tree"})


class VerifiedStructuralDiscoveryFinalizationError(RuntimeError):
    """El discovery no puede convertirse en evidencia estructural cerrada."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


def _fail(code: str, message: str | None = None) -> NoReturn:
    raise VerifiedStructuralDiscoveryFinalizationError(code, message)


def _bearer(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or len(value) > 20_000
        or any(character.isspace() for character in value)
    ):
        _fail("observability_bearer_token_invalid")
    return value


class VerifiedStructuralDiscoveryFinalizer:
    """Finalizador one-shot lógico para un transporte facet ya completado.

    El mismo bearer token efímero se usa para root y tree durante una única
    finalización. No se almacena en el objeto. Si root falla, tree no se consulta.
    """

    def __init__(
        self,
        verifier_client: CloudflareStructuralObservabilityVerifierClient,
        *,
        bearer_token_provider: Callable[[], str],
    ) -> None:
        if not isinstance(
            verifier_client,
            CloudflareStructuralObservabilityVerifierClient,
        ):
            _fail("structural_observability_verifier_client_invalid")
        if not callable(bearer_token_provider):
            _fail("observability_bearer_token_provider_invalid")
        self._verifier_client = verifier_client
        self._bearer_token_provider = bearer_token_provider
        self._finalized_transport_id: int | None = None
        self._result: VerifiedStructuralDiscovery | None = None

    @property
    def account_id(self) -> str:
        return self._verifier_client.account_id

    @property
    def finalized(self) -> bool:
        return self._result is not None

    def _snapshot(
        self,
        transport: VerifiedFacetDiscoveryEdgeTransport,
    ) -> Mapping[str, CryptographicallyVerifiedStructuralObservation]:
        if not isinstance(transport, VerifiedFacetDiscoveryEdgeTransport):
            _fail("verified_facet_transport_invalid")
        if not transport.complete:
            _fail("verified_facet_transport_incomplete")
        observations = transport.observations
        if set(observations) != _EXPECTED_KINDS or len(observations) != 2:
            _fail("verified_facet_observations_incomplete")
        for kind in _EXPECTED_KINDS:
            observation = observations.get(kind)
            if not isinstance(observation, CryptographicallyVerifiedStructuralObservation):
                _fail(f"verified_facet_{kind}_observation_invalid")
            if observation.request_kind != kind:
                _fail(f"verified_facet_{kind}_request_kind_mismatch")
            if observation.cryptographic_signature_verified is not True:
                _fail(f"verified_facet_{kind}_signature_unverified")
            if observation.structural_body_validated is not True:
                _fail(f"verified_facet_{kind}_body_unvalidated")
            if observation.production_authority is not False:
                _fail(f"verified_facet_{kind}_authority_forbidden")
        return observations

    def finalize(
        self,
        transport: VerifiedFacetDiscoveryEdgeTransport,
    ) -> VerifiedStructuralDiscovery:
        """Reconcilia root→tree y deriva el manifest estructural autenticado.

        Una segunda llamada con el mismo objeto devuelve el resultado cacheado y
        no vuelve a consultar Observability. Un transporte distinto se rechaza.
        """

        transport_id = id(transport)
        if self._result is not None:
            if self._finalized_transport_id != transport_id:
                _fail("finalizer_already_bound_to_other_transport")
            return self._result

        observations = self._snapshot(transport)
        try:
            token = _bearer(self._bearer_token_provider())
        except VerifiedStructuralDiscoveryFinalizationError:
            raise
        except Exception as exc:
            raise VerifiedStructuralDiscoveryFinalizationError(
                "observability_bearer_token_provider_failed"
            ) from exc

        root = observations["root_total"]
        tree = observations["category_tree"]

        try:
            root_reconciled = self._verifier_client.reconcile_observation(
                root,
                bearer_token=token,
            )
        except CloudflareStructuralObservabilityVerifierError as exc:
            raise VerifiedStructuralDiscoveryFinalizationError(
                f"root_total_observability_{exc.code}"
            ) from exc

        try:
            tree_reconciled = self._verifier_client.reconcile_observation(
                tree,
                bearer_token=token,
            )
        except CloudflareStructuralObservabilityVerifierError as exc:
            raise VerifiedStructuralDiscoveryFinalizationError(
                f"category_tree_observability_{exc.code}"
            ) from exc

        try:
            result = build_verified_structural_discovery(
                root_total=root_reconciled,
                category_tree=tree_reconciled,
            )
        except StructuralDiscoveryManifestError as exc:
            raise VerifiedStructuralDiscoveryFinalizationError(
                f"structural_manifest_{exc.code}"
            ) from exc

        if result.production_authority is not False:
            _fail("structural_discovery_authority_forbidden")

        self._finalized_transport_id = transport_id
        self._result = result
        return result
