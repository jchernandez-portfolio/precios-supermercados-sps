"""Reconcilia tracing de catálogo ``query`` sin retener el URL físico sensible.

Workers Observability expone ``url.full`` del child fetch. Cuando ``regionId`` viaja
como query parameter, persistir ese URL violaría la frontera de sanitización. Esta
capa consume la traza raw sólo de forma transitoria, demuestra que corresponde al
receipt context-bound v3 y devuelve una evidencia que conserva únicamente hashes e
identidades no sensibles.

No abre red, no selecciona el placement real y nunca concede autoridad productiva.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import NoReturn
from urllib.parse import parse_qsl, urlsplit

from precios_supermercados.catalog_context_provenance import (
    CATALOG_CONTEXT_RECEIPT_SCHEMA_VERSION,
    ContextBoundEdgeReceiptPayload,
)
from precios_supermercados.cloudflare_trace_evidence import (
    MAX_CLOCK_SKEW,
    CloudflareOriginTraceEvidence,
)
from precios_supermercados.edge_crypto_page import CryptographicallyVerifiedEdgeCatalogPage
from precios_supermercados.edge_provenance import canonical_json_bytes
from precios_supermercados.scrapers.la_colonia_context_bound_catalog_transport import (
    ContextBoundVerifiedCatalogPageObservation,
)
from precios_supermercados.scrapers.la_colonia_sps_facet_context import (
    fingerprint_context_value,
)

QUERY_TRACE_REDACTION_SCHEMA_VERSION = "1"
_QUERY_TRACE_DOMAIN = b"precios-sps/context-bound-query-trace/v1\0"
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_OPAQUE = re.compile(r"[^\s]{1,512}\Z")


class ContextBoundQueryTraceError(ValueError):
    """La traza query no puede demostrarse sin persistir material sensible."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


def _fail(code: str, message: str | None = None) -> NoReturn:
    raise ContextBoundQueryTraceError(code, message)


def _text(value: object, code: str, *, maximum: int = 512) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or len(value) > maximum
        or not _OPAQUE.fullmatch(value)
    ):
        _fail(code)
    return value


def _sha1(value: object, code: str) -> str:
    text = _text(value, code, maximum=40)
    if _SHA1.fullmatch(text) is None:
        _fail(code)
    return text


def _sha256(value: object, code: str) -> str:
    text = _text(value, code, maximum=64)
    if _SHA256.fullmatch(text) is None:
        _fail(code)
    return text


def _utc(value: object, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(UTC)


def _iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _wire_fingerprint(url: str) -> str:
    rendered = json.dumps(
        {"headers": {}, "method": "GET", "url": url},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _receipt_identity_matches(
    trace: CloudflareOriginTraceEvidence,
    payload: ContextBoundEdgeReceiptPayload,
) -> bool:
    return (
        trace.authorization_id == payload.authorization_id
        and trace.run_id == payload.run_id
        and trace.approved_commit_sha == payload.approved_commit_sha
        and trace.reservation_id == payload.reservation_id
        and trace.request_id == payload.request_id
        and trace.request_digest == payload.request_digest
        and trace.traversal_role == payload.traversal_role
        and trace.traversal_id == payload.traversal_id
        and trace.partition_id == payload.partition_id
    )


def _extract_direct_query_context(
    *,
    base_url: str,
    fetch_url: str,
    wire_key: str,
) -> str:
    """Exige base URL byte-for-byte y exactamente un parámetro directo adicional."""

    try:
        base = urlsplit(base_url)
        fetch = urlsplit(fetch_url)
        base_port = base.port
        fetch_port = fetch.port
    except ValueError as exc:
        raise ContextBoundQueryTraceError("query_trace_url_invalid") from exc

    if (
        base.scheme != fetch.scheme
        or base.netloc != fetch.netloc
        or base.path != fetch.path
        or base_port != fetch_port
        or base.fragment
        or fetch.fragment
    ):
        _fail("query_trace_base_url_mismatch")

    base_pairs = parse_qsl(base.query, keep_blank_values=True)
    if any(key.casefold() == wire_key.casefold() for key, _ in base_pairs):
        _fail("query_trace_context_already_in_base")

    expected_prefix = f"{base.query}&" if base.query else ""
    if not fetch.query.startswith(expected_prefix):
        _fail("query_trace_base_query_changed")
    suffix = fetch.query[len(expected_prefix):]
    if not suffix or "&" in suffix:
        _fail("query_trace_extra_query_material")

    appended = parse_qsl(suffix, keep_blank_values=True)
    if len(appended) != 1:
        _fail("query_trace_context_parameter_invalid")
    key, raw_value = appended[0]
    if key.casefold() != wire_key.casefold():
        _fail("query_trace_context_key_mismatch")
    if not raw_value or len(raw_value) > 4096:
        _fail("query_trace_context_value_invalid")

    # El parseo no basta: ninguna reserialización de la query base es aceptable.
    reconstructed_prefix = f"{base.scheme}://{base.netloc}{base.path}"
    if base.query:
        reconstructed_prefix += f"?{base.query}"
    separator = "&" if base.query else "?"
    if not fetch_url.startswith(reconstructed_prefix + separator):
        _fail("query_trace_base_url_not_preserved")
    return raw_value


@dataclass(frozen=True, slots=True)
class RedactedContextBoundQueryTraceEvidence:
    """Compromiso criptográfico a una traza raw que ya no se conserva."""

    trace_id: str
    custom_span_id: str
    fetch_span_id: str
    faas_invocation_id: str
    service_name: str
    script_version_id: str
    authorization_id: str
    run_id: str
    approved_commit_sha: str
    reservation_id: str
    request_id: str
    request_digest: str
    traversal_role: str
    traversal_id: str
    partition_id: str
    context_fingerprint: str
    wire_request_fingerprint: str
    base_fetch_url_sha256: str
    raw_fetch_url_sha256: str
    raw_trace_evidence_sha256: str
    fetch_status: int
    fetch_response_body_size: int
    custom_started_at_utc: datetime
    custom_completed_at_utc: datetime
    fetch_started_at_utc: datetime
    fetch_completed_at_utc: datetime
    schema_version: str = QUERY_TRACE_REDACTION_SCHEMA_VERSION
    platform_evidence_reconciled: bool = True
    production_authority: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != QUERY_TRACE_REDACTION_SCHEMA_VERSION:
            _fail("query_trace_schema_version_invalid")
        for name in (
            "trace_id",
            "custom_span_id",
            "fetch_span_id",
            "faas_invocation_id",
            "service_name",
            "script_version_id",
            "authorization_id",
            "run_id",
            "reservation_id",
            "request_id",
            "traversal_role",
            "traversal_id",
            "partition_id",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), f"query_trace_{name}_invalid"))
        object.__setattr__(
            self,
            "approved_commit_sha",
            _sha1(self.approved_commit_sha, "query_trace_approved_commit_sha_invalid"),
        )
        for name in (
            "request_digest",
            "context_fingerprint",
            "wire_request_fingerprint",
            "base_fetch_url_sha256",
            "raw_fetch_url_sha256",
            "raw_trace_evidence_sha256",
        ):
            object.__setattr__(self, name, _sha256(getattr(self, name), f"query_trace_{name}_invalid"))
        if isinstance(self.fetch_status, bool) or not isinstance(self.fetch_status, int) or self.fetch_status != 200:
            _fail("query_trace_fetch_status_invalid")
        if (
            isinstance(self.fetch_response_body_size, bool)
            or not isinstance(self.fetch_response_body_size, int)
            or self.fetch_response_body_size < 0
        ):
            _fail("query_trace_fetch_body_size_invalid")
        for name in (
            "custom_started_at_utc",
            "custom_completed_at_utc",
            "fetch_started_at_utc",
            "fetch_completed_at_utc",
        ):
            object.__setattr__(self, name, _utc(getattr(self, name), f"query_trace_{name}_invalid"))
        if self.custom_completed_at_utc < self.custom_started_at_utc:
            _fail("query_trace_custom_time_order_invalid")
        if self.fetch_completed_at_utc < self.fetch_started_at_utc:
            _fail("query_trace_fetch_time_order_invalid")
        if (
            self.fetch_started_at_utc < self.custom_started_at_utc
            or self.fetch_completed_at_utc > self.custom_completed_at_utc
        ):
            _fail("query_trace_fetch_outside_custom_span")
        if self.platform_evidence_reconciled is not True:
            _fail("query_trace_platform_reconciliation_required")
        if self.production_authority is not False:
            _fail("query_trace_production_authority_forbidden")

    def canonical_dict(self) -> dict[str, object]:
        return {
            "approved_commit_sha": self.approved_commit_sha,
            "authorization_id": self.authorization_id,
            "base_fetch_url_sha256": self.base_fetch_url_sha256,
            "context_fingerprint": self.context_fingerprint,
            "custom_completed_at_utc": _iso_z(self.custom_completed_at_utc),
            "custom_span_id": self.custom_span_id,
            "custom_started_at_utc": _iso_z(self.custom_started_at_utc),
            "faas_invocation_id": self.faas_invocation_id,
            "fetch_completed_at_utc": _iso_z(self.fetch_completed_at_utc),
            "fetch_response_body_size": self.fetch_response_body_size,
            "fetch_span_id": self.fetch_span_id,
            "fetch_started_at_utc": _iso_z(self.fetch_started_at_utc),
            "fetch_status": self.fetch_status,
            "partition_id": self.partition_id,
            "raw_fetch_url_sha256": self.raw_fetch_url_sha256,
            "raw_trace_evidence_sha256": self.raw_trace_evidence_sha256,
            "request_digest": self.request_digest,
            "request_id": self.request_id,
            "reservation_id": self.reservation_id,
            "run_id": self.run_id,
            "schema_version": self.schema_version,
            "script_version_id": self.script_version_id,
            "service_name": self.service_name,
            "trace_id": self.trace_id,
            "traversal_id": self.traversal_id,
            "traversal_role": self.traversal_role,
            "wire_request_fingerprint": self.wire_request_fingerprint,
        }

    @property
    def physical_evidence_id(self) -> str:
        return hashlib.sha256(
            _QUERY_TRACE_DOMAIN + canonical_json_bytes(self.canonical_dict())
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class RedactedContextBoundQueryPage:
    """Página criptográfica reconciliada con tracing query ya redactado."""

    page: CryptographicallyVerifiedEdgeCatalogPage
    trace_evidence: RedactedContextBoundQueryTraceEvidence
    platform_evidence_reconciled: bool = True
    production_authority: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.page, CryptographicallyVerifiedEdgeCatalogPage):
            _fail("query_trace_crypto_page_invalid")
        if not isinstance(self.trace_evidence, RedactedContextBoundQueryTraceEvidence):
            _fail("query_trace_redacted_evidence_invalid")
        if self.page.cryptographic_signature_verified is not True:
            _fail("query_trace_page_signature_unverified")
        if self.page.production_authority is not False:
            _fail("query_trace_page_authority_forbidden")
        if self.platform_evidence_reconciled is not True:
            _fail("query_trace_page_reconciliation_required")
        if self.production_authority is not False:
            _fail("query_trace_page_authority_forbidden")

    @property
    def physical_evidence_id(self) -> str:
        return self.trace_evidence.physical_evidence_id


def reconcile_context_bound_query_trace(
    observation: ContextBoundVerifiedCatalogPageObservation,
    candidates: Sequence[CloudflareOriginTraceEvidence],
    *,
    clock_skew: timedelta = MAX_CLOCK_SKEW,
) -> RedactedContextBoundQueryPage:
    """Valida una única traza raw y la destruye conceptualmente al devolver hashes."""

    if not isinstance(observation, ContextBoundVerifiedCatalogPageObservation):
        _fail("query_trace_observation_invalid")
    page = observation.page
    if not isinstance(page, CryptographicallyVerifiedEdgeCatalogPage):
        _fail("query_trace_crypto_page_invalid")
    if page.cryptographic_signature_verified is not True:
        _fail("query_trace_page_signature_unverified")
    if page.production_authority is not False or observation.production_authority is not False:
        _fail("query_trace_page_authority_forbidden")

    payload = page.verified_receipt.receipt.payload
    if not isinstance(payload, ContextBoundEdgeReceiptPayload):
        _fail("query_trace_receipt_downgrade")
    if payload.schema_version != CATALOG_CONTEXT_RECEIPT_SCHEMA_VERSION or payload.location_context_bound is not True:
        _fail("query_trace_receipt_schema_invalid")
    if payload.location_id != "la_colonia_sps" or observation.location_id != payload.location_id:
        _fail("query_trace_location_mismatch")
    if payload.context_placement != "query":
        _fail("query_trace_query_placement_required")
    if payload.context_value_path != ():
        _fail("query_trace_nested_context_forbidden")
    if observation.context_fingerprint != payload.context_fingerprint:
        _fail("query_trace_context_fingerprint_mismatch")
    if observation.wire_request_fingerprint != payload.wire_request_fingerprint:
        _fail("query_trace_wire_fingerprint_mismatch")

    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes, bytearray)):
        _fail("query_trace_candidates_invalid")
    if not isinstance(clock_skew, timedelta) or clock_skew < timedelta(0) or clock_skew > timedelta(minutes=1):
        _fail("query_trace_clock_skew_invalid")
    typed: list[CloudflareOriginTraceEvidence] = []
    for candidate in candidates:
        if not isinstance(candidate, CloudflareOriginTraceEvidence):
            _fail("query_trace_candidate_invalid")
        if _receipt_identity_matches(candidate, payload):
            typed.append(candidate)
    if not typed:
        _fail("query_trace_matching_trace_missing")
    if len(typed) != 1:
        _fail("query_trace_matching_trace_not_unique")

    trace = typed[0]
    raw_context = _extract_direct_query_context(
        base_url=page.source_url,
        fetch_url=trace.fetch_url,
        wire_key=payload.context_wire_key,
    )
    if fingerprint_context_value(raw_context) != payload.context_fingerprint:
        _fail("query_trace_context_value_fingerprint_mismatch")
    if _wire_fingerprint(trace.fetch_url) != payload.wire_request_fingerprint:
        _fail("query_trace_wire_request_fingerprint_mismatch")

    if trace.fetch_method != "GET":
        _fail("query_trace_fetch_method_mismatch")
    if trace.fetch_status != payload.response_status or trace.fetch_status != 200:
        _fail("query_trace_fetch_status_mismatch")
    if trace.fetch_response_body_size != payload.response_body_bytes:
        _fail("query_trace_fetch_body_size_mismatch")
    if trace.script_version_id != payload.collector_release_id:
        _fail("query_trace_script_version_mismatch")

    physical_start = payload.physical_started_at_utc.astimezone(UTC)
    response_end = payload.response_completed_at_utc.astimezone(UTC)
    if trace.fetch_started_at_utc < physical_start - clock_skew:
        _fail("query_trace_fetch_started_too_early")
    if trace.fetch_started_at_utc > response_end + clock_skew:
        _fail("query_trace_fetch_started_too_late")
    if trace.fetch_completed_at_utc < physical_start - clock_skew:
        _fail("query_trace_fetch_completed_too_early")
    if trace.fetch_completed_at_utc > response_end + clock_skew:
        _fail("query_trace_fetch_completed_too_late")

    raw_trace_hash = hashlib.sha256(canonical_json_bytes(trace.canonical_dict())).hexdigest()
    redacted = RedactedContextBoundQueryTraceEvidence(
        trace_id=trace.trace_id,
        custom_span_id=trace.custom_span_id,
        fetch_span_id=trace.fetch_span_id,
        faas_invocation_id=trace.faas_invocation_id,
        service_name=trace.service_name,
        script_version_id=trace.script_version_id,
        authorization_id=trace.authorization_id,
        run_id=trace.run_id,
        approved_commit_sha=trace.approved_commit_sha,
        reservation_id=trace.reservation_id,
        request_id=trace.request_id,
        request_digest=trace.request_digest,
        traversal_role=trace.traversal_role,
        traversal_id=trace.traversal_id,
        partition_id=trace.partition_id,
        context_fingerprint=payload.context_fingerprint,
        wire_request_fingerprint=payload.wire_request_fingerprint,
        base_fetch_url_sha256=_hash_text(page.source_url),
        raw_fetch_url_sha256=_hash_text(trace.fetch_url),
        raw_trace_evidence_sha256=raw_trace_hash,
        fetch_status=trace.fetch_status,
        fetch_response_body_size=trace.fetch_response_body_size,
        custom_started_at_utc=trace.custom_started_at_utc,
        custom_completed_at_utc=trace.custom_completed_at_utc,
        fetch_started_at_utc=trace.fetch_started_at_utc,
        fetch_completed_at_utc=trace.fetch_completed_at_utc,
    )
    return RedactedContextBoundQueryPage(page=page, trace_evidence=redacted)
