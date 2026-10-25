"""Composición fail-closed del futuro entrypoint edge de facets bajo SPS.

Esta capa no descubre ni autoriza tráfico live. Consume un ``SpsStructuralFacetPlan``
ya ligado a una observación efímera de SPS, inicializa un ledger edge con presupuesto
exacto de dos requests y ejecuta únicamente ``root_total`` -> ``category_tree`` a
través del gateway estructural verificado.

La autorización humana que permita llegar a esta composición pertenece al workflow
privilegiado y no puede fabricarse aquí. ``edge_authorization_id`` identifica sólo
el ledger técnico de Cloudflare. El resultado conserva siempre
``production_authority=false``, ``catalog_accepted=false`` y
``extraction_enabled=false``.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import NoReturn

from precios_supermercados.edge_gateway_client import (
    EdgeAuthorizationRequest,
    EdgeGatewayClient,
    EdgeGatewayTransport,
)
from precios_supermercados.edge_structural_gateway_client import StructuralEdgeGatewayClient
from precios_supermercados.edge_structural_observation import EdgeStructuralObservationVerifier
from precios_supermercados.la_colonia_edge_structural_request import ValidatedLaColoniaStructuralRequest
from precios_supermercados.scrapers.la_colonia_facet_discovery import (
    FACET_DISCOVERY_CONCURRENCY,
    FACET_DISCOVERY_DELAY_SECONDS,
    FACET_DISCOVERY_MAX_ARTIFACT_BYTES,
    FACET_DISCOVERY_MAX_REQUESTS,
    FACET_DISCOVERY_PLAN_NAME,
    FACET_DISCOVERY_REQUEST_ID,
    FacetDiscoveryRequest,
)
from precios_supermercados.scrapers.la_colonia_facet_discovery_runtime import (
    FacetDiscoveryRuntime,
    serialize_facet_discovery_summary,
)
from precios_supermercados.scrapers.la_colonia_sps_structural_plan import SpsStructuralFacetPlan
from precios_supermercados.scrapers.la_colonia_verified_facet_transport import (
    VerifiedFacetDiscoveryEdgeTransport,
)
from precios_supermercados.structural_receipt_crypto import (
    Ed25519StructuralReceiptVerifier,
)

ENTRYPOINT_SCHEMA_VERSION = "context-bound-facet-entrypoint-1"
ENTRYPOINT_MAX_REQUESTS = 2
ENTRYPOINT_CONCURRENCY = 1
ENTRYPOINT_MAX_RETRIES = 0
ENTRYPOINT_MAX_LIFETIME_MS = 10 * 60 * 1000
_SIGNING_KEY_ID = "cloudflare-ed25519-v1"
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_DIGITS = re.compile(r"[0-9]+\Z")
_OPAQUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


class ContextBoundFacetEntrypointError(RuntimeError):
    """La composición no puede demostrar las invariantes del entrypoint."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


def _fail(code: str, message: str | None = None) -> NoReturn:
    raise ContextBoundFacetEntrypointError(code, message)


def _exact_int(value: object, code: str, *, minimum: int = 0, maximum: int = 2**53 - 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        _fail(code)
    return value


def _opaque(value: object, code: str) -> str:
    if not isinstance(value, str) or not _OPAQUE.fullmatch(value):
        _fail(code)
    return value


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ContextBoundFacetEntrypointError("facet_entrypoint_json_invalid") from exc


@dataclass(frozen=True, slots=True)
class FacetEdgeRunContext:
    """Identidad técnica de un run GitHub/Cloudflare; no es autoridad humana."""

    edge_authorization_id: str
    github_run_id: str
    github_run_attempt: int
    approved_commit_sha: str
    created_at_ms: int
    expires_at_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "edge_authorization_id",
            _opaque(self.edge_authorization_id, "facet_edge_authorization_id_invalid"),
        )
        if not isinstance(self.github_run_id, str) or not _DIGITS.fullmatch(self.github_run_id):
            _fail("facet_github_run_id_invalid")
        _exact_int(self.github_run_attempt, "facet_github_run_attempt_invalid", minimum=1, maximum=100)
        if not isinstance(self.approved_commit_sha, str) or not _SHA1.fullmatch(self.approved_commit_sha):
            _fail("facet_approved_commit_sha_invalid")
        created = _exact_int(self.created_at_ms, "facet_created_at_ms_invalid", minimum=1)
        expires = _exact_int(self.expires_at_ms, "facet_expires_at_ms_invalid", minimum=created + 1)
        if expires - created > ENTRYPOINT_MAX_LIFETIME_MS:
            _fail("facet_authorization_lifetime_above_limit")

    @property
    def run_id(self) -> str:
        return f"{self.github_run_id}:{self.github_run_attempt}"

    def authorization_request(self) -> EdgeAuthorizationRequest:
        return EdgeAuthorizationRequest(
            authorization_id=self.edge_authorization_id,
            run_id=self.run_id,
            approved_commit_sha=self.approved_commit_sha,
            created_at_ms=self.created_at_ms,
            expires_at_ms=self.expires_at_ms,
            max_requests=ENTRYPOINT_MAX_REQUESTS,
        )


@dataclass(frozen=True, slots=True)
class ContextBoundFacetEntrypointResult:
    """Resultado sanitizado del par estructural; nunca contiene el contexto raw."""

    artifact: Mapping[str, object]
    discovery_summary: Mapping[str, object]
    network_executed: bool
    production_authority: bool = False
    catalog_accepted: bool = False
    extraction_enabled: bool = False

    def __post_init__(self) -> None:
        if self.production_authority is not False:
            _fail("facet_entrypoint_authority_forbidden")
        if self.catalog_accepted is not False:
            _fail("facet_entrypoint_catalog_acceptance_forbidden")
        if self.extraction_enabled is not False:
            _fail("facet_entrypoint_extraction_forbidden")
        if not isinstance(self.network_executed, bool):
            _fail("facet_entrypoint_network_flag_invalid")
        object.__setattr__(self, "artifact", MappingProxyType(dict(self.artifact)))
        object.__setattr__(self, "discovery_summary", MappingProxyType(dict(self.discovery_summary)))

    def artifact_bytes(self) -> bytes:
        encoded = _canonical_bytes(dict(self.artifact)) + b"\n"
        if len(encoded) > FACET_DISCOVERY_MAX_ARTIFACT_BYTES:
            _fail("facet_entrypoint_artifact_above_limit")
        return encoded


def _derived_id(run: FacetEdgeRunContext, plan: SpsStructuralFacetPlan, kind: str, purpose: str) -> str:
    material = (
        f"precios-sps/facet-entrypoint/v1\0{run.edge_authorization_id}\0{run.run_id}"
        f"\0{run.approved_commit_sha}\0{plan.digest}\0{kind}\0{purpose}"
    ).encode()
    return f"facet-{purpose}-{hashlib.sha256(material).hexdigest()[:40]}"


def _validate_plan(plan: object) -> SpsStructuralFacetPlan:
    if not isinstance(plan, SpsStructuralFacetPlan):
        _fail("sps_structural_plan_required")
    if tuple(item.request_kind for item in plan.requests) != ("root_total", "category_tree"):
        _fail("facet_entrypoint_request_pair_invalid")
    if tuple(item.sequence for item in plan.requests) != (1, 2):
        _fail("facet_entrypoint_request_sequence_invalid")
    if plan.requires_same_browser_context is not True:
        _fail("facet_entrypoint_browser_context_required")
    if (
        plan.network_executed is not False
        or plan.production_authority is not False
        or plan.catalog_accepted is not False
        or plan.extraction_enabled is not False
    ):
        _fail("facet_entrypoint_plan_state_invalid")
    if FACET_DISCOVERY_MAX_REQUESTS != ENTRYPOINT_MAX_REQUESTS:
        _fail("facet_entrypoint_budget_contract_changed")
    if FACET_DISCOVERY_CONCURRENCY != ENTRYPOINT_CONCURRENCY:
        _fail("facet_entrypoint_concurrency_contract_changed")
    return plan


def _context_provider(run: FacetEdgeRunContext, plan: SpsStructuralFacetPlan):
    from precios_supermercados.edge_structural_gateway_client import StructuralEdgeRequestContext

    def provide(
        logical_request: FacetDiscoveryRequest,
        validated: ValidatedLaColoniaStructuralRequest,
    ) -> StructuralEdgeRequestContext:
        if logical_request.name not in {"root_total", "category_tree"}:
            _fail("facet_entrypoint_request_kind_invalid")
        expected = plan.requests[logical_request.sequence - 1]
        if (
            expected.request_kind != logical_request.name
            or expected.sequence != logical_request.sequence
            or expected.canonical_request_digest != validated.canonical_request_sha256
        ):
            _fail("facet_entrypoint_request_plan_mismatch")
        return StructuralEdgeRequestContext(
            authorization_id=run.edge_authorization_id,
            run_id=run.run_id,
            approved_commit_sha=run.approved_commit_sha,
            reservation_id=_derived_id(run, plan, logical_request.name, "reservation"),
            request_id=_derived_id(run, plan, logical_request.name, "request"),
            request_digest=validated.canonical_request_sha256,
            nonce=_derived_id(run, plan, logical_request.name, "nonce"),
            request_kind=logical_request.name,
        )

    return provide


def _artifact(
    *,
    run: FacetEdgeRunContext,
    plan: SpsStructuralFacetPlan,
    summary: Mapping[str, object],
    observations: Mapping[str, object],
) -> dict[str, object]:
    evidence: dict[str, object] = {}
    for kind in ("root_total", "category_tree"):
        observation = observations.get(kind)
        if observation is None:
            continue
        try:
            verified = observation.verified_receipt
            receipt = verified.receipt
            payload = receipt.payload
            evidence[kind] = {
                "receipt_digest": receipt.digest,
                "public_key_spki_sha256": verified.public_key_spki_sha256,
                "raw_body_sha256": observation.raw_body_sha256,
                "wire_request_fingerprint": payload.wire_request_fingerprint,
                "context_fingerprint": payload.context_fingerprint,
                "location_id": payload.location_id,
                "signature_verified": observation.cryptographic_signature_verified is True,
                "structural_body_validated": observation.structural_body_validated is True,
            }
        except AttributeError as exc:
            raise ContextBoundFacetEntrypointError("facet_entrypoint_observation_invalid") from exc

    artifact = {
        "schema_version": ENTRYPOINT_SCHEMA_VERSION,
        "edge_authorization_id": run.edge_authorization_id,
        "run_id": run.run_id,
        "approved_commit_sha": run.approved_commit_sha,
        "location_id": plan.location_id,
        "binding_source_key": plan.binding_source_key,
        "binding_evidence": plan.binding_evidence,
        "context_fingerprint": plan.context_fingerprint,
        "context_placement": plan.placement.value,
        "context_wire_key": plan.wire_key,
        "context_value_path": list(plan.value_path),
        "plan_digest": plan.digest,
        "requests_planned": ["root_total", "category_tree"],
        "max_requests": ENTRYPOINT_MAX_REQUESTS,
        "concurrency": ENTRYPOINT_CONCURRENCY,
        "max_retries": ENTRYPOINT_MAX_RETRIES,
        "delay_seconds": FACET_DISCOVERY_DELAY_SECONDS,
        "requests_attempted": summary.get("requests_attempted"),
        "requests_completed": summary.get("requests_completed"),
        "discovery_completed": summary.get("discovery_completed"),
        "discovery_outcome": summary.get("discovery_outcome"),
        "stop_reason": summary.get("stop_reason"),
        "evidence": evidence,
        "raw_values_exposed": False,
        "production_authority": False,
        "catalog_accepted": False,
        "extraction_enabled": False,
    }
    rendered = _canonical_bytes(artifact)
    if len(rendered) + 1 > FACET_DISCOVERY_MAX_ARTIFACT_BYTES:
        _fail("facet_entrypoint_artifact_above_limit")
    return artifact


def run_context_bound_facet_entrypoint(
    *,
    plan: SpsStructuralFacetPlan,
    run: FacetEdgeRunContext,
    transport: EdgeGatewayTransport,
    bearer_token: str,
    trusted_public_key_spki_b64url: str,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], datetime] | None = None,
) -> ContextBoundFacetEntrypointResult:
    """Inicializa presupuesto=2 y ejecuta exactamente el par estructural verificado.

    El caller debe llegar aquí sólo después del gate humano del workflow. Esta función
    valida el contrato técnico, no decide si existe autorización humana live vigente.
    """

    effective_plan = _validate_plan(plan)
    if not isinstance(run, FacetEdgeRunContext):
        _fail("facet_edge_run_context_required")
    if transport is None or not callable(getattr(transport, "post_json", None)):
        _fail("facet_edge_transport_invalid")
    if (
        not isinstance(bearer_token, str)
        or not bearer_token
        or bearer_token.strip() != bearer_token
        or any(character.isspace() for character in bearer_token)
    ):
        _fail("facet_edge_bearer_invalid")
    if not isinstance(trusted_public_key_spki_b64url, str) or not trusted_public_key_spki_b64url:
        _fail("facet_structural_public_key_missing")
    if not callable(sleeper):
        _fail("facet_sleeper_invalid")
    runtime_clock = clock or (lambda: datetime.now(UTC))
    if not callable(runtime_clock):
        _fail("facet_clock_invalid")

    gateway = EdgeGatewayClient(transport)
    initialized = gateway.initialize(run.authorization_request(), bearer_token=bearer_token)
    if (
        initialized.state != "active"
        or initialized.requests_used != 0
        or initialized.remaining_requests != ENTRYPOINT_MAX_REQUESTS
        or initialized.production_authority is not False
    ):
        _fail("facet_edge_initialization_not_fresh")

    try:
        crypto_verifier = Ed25519StructuralReceiptVerifier(
            {_SIGNING_KEY_ID: trusted_public_key_spki_b64url}
        )
    except Exception as exc:
        raise ContextBoundFacetEntrypointError("facet_structural_public_key_invalid") from exc
    observation_verifier = EdgeStructuralObservationVerifier(crypto_verifier)
    structural_client = StructuralEdgeGatewayClient(transport)

    # El token OIDC se resuelve fuera y se reutiliza de forma exacta en initialize/root/tree.
    # No existe refresh/retry oculto dentro de esta composición.
    edge_transport = VerifiedFacetDiscoveryEdgeTransport(
        structural_client,
        observation_verifier,
        sps_plan=effective_plan,
        context_provider=_context_provider(run, effective_plan),
        bearer_token_provider=lambda: bearer_token,
    )
    runtime = FacetDiscoveryRuntime(
        edge_transport,
        sleeper=sleeper,
        clock=runtime_clock,
        max_retries=ENTRYPOINT_MAX_RETRIES,
        max_requests=ENTRYPOINT_MAX_REQUESTS,
    )
    command = {
        "request_id": FACET_DISCOVERY_REQUEST_ID,
        "supermarket": "la_colonia",
        "mode": "facet_discovery",
        "discovery_plan": FACET_DISCOVERY_PLAN_NAME,
        "delay_seconds": FACET_DISCOVERY_DELAY_SECONDS,
        "allow_full": False,
    }
    result = runtime.run(command)
    summary = dict(result.summary)
    # Revalida el contrato sanitizado antes de incorporarlo a una evidencia durable.
    serialize_facet_discovery_summary(summary)

    artifact = _artifact(
        run=run,
        plan=effective_plan,
        summary=summary,
        observations=edge_transport.observations,
    )
    return ContextBoundFacetEntrypointResult(
        artifact=artifact,
        discovery_summary=summary,
        network_executed=summary.get("requests_attempted", 0) > 0,
    )
