"""Manifest run-level de provenance edge verificada por página.

La unidad de confianza ya no es una página aislada. Este módulo exige que el
conjunto completo de páginas reconciliadas coincida exactamente con un plan
cerrado esperado, que todas pertenezcan a la misma ejecución/autorización y que
ninguna identidad física/criptográfica se reutilice.

Sigue siendo una frontera offline: ``production_authority`` permanece siempre
``False`` y este manifest por sí solo no elimina el gate
``trusted_collector_provenance_unavailable``.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import NoReturn

from precios_supermercados.cloudflare_trace_evidence import (
    CloudflareTraceEvidenceError,
    PlatformReconciledEdgePage,
    reconcile_cloudflare_origin_trace,
)
from precios_supermercados.edge_provenance import canonical_json_bytes

RUN_MANIFEST_SCHEMA_VERSION = "1"
_RUN_MANIFEST_DOMAIN = b"precios-sps/edge-provenance-run/v1\0"
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ALLOWED_ROLES = {"primary", "reconciliation"}


class EdgeProvenanceRunError(ValueError):
    """El conjunto de provenance no forma una ejecución cerrada y coherente."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


def _fail(code: str, message: str | None = None) -> NoReturn:
    raise EdgeProvenanceRunError(code, message)


def _text(value: object, code: str, *, maximum: int = 512) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or len(value) > maximum
        or any(character.isspace() for character in value)
    ):
        _fail(code)
    return value


def _sha1(value: object, code: str) -> str:
    text = _text(value, code, maximum=40)
    if not _SHA1.fullmatch(text):
        _fail(code)
    return text


def _sha256(value: object, code: str) -> str:
    text = _text(value, code, maximum=64)
    if not _SHA256.fullmatch(text):
        _fail(code)
    return text


def _index(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail(code)
    return value


def _utc(value: object, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(UTC)


def _iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True, order=True)
class ExpectedProvenancePage:
    """Una solicitud exacta del plan cerrado que debe tener evidencia física."""

    traversal_role: str
    traversal_id: str
    partition_id: str
    order_by: str
    from_index: int
    to_index: int
    request_digest: str

    def __post_init__(self) -> None:
        if self.traversal_role not in _ALLOWED_ROLES:
            _fail("expected_traversal_role_invalid")
        for name in ("traversal_id", "partition_id", "order_by"):
            object.__setattr__(self, name, _text(getattr(self, name), f"expected_{name}_invalid"))
        start = _index(self.from_index, "expected_from_index_invalid")
        end = _index(self.to_index, "expected_to_index_invalid")
        if end < start:
            _fail("expected_range_invalid")
        object.__setattr__(
            self,
            "request_digest",
            _sha256(self.request_digest, "expected_request_digest_invalid"),
        )

    @property
    def identity(self) -> tuple[object, ...]:
        return (
            self.traversal_role,
            self.traversal_id,
            self.partition_id,
            self.order_by,
            self.from_index,
            self.to_index,
            self.request_digest,
        )

    def canonical_dict(self) -> dict[str, object]:
        return {
            "from_index": self.from_index,
            "order_by": self.order_by,
            "partition_id": self.partition_id,
            "request_digest": self.request_digest,
            "to_index": self.to_index,
            "traversal_id": self.traversal_id,
            "traversal_role": self.traversal_role,
        }


@dataclass(frozen=True, slots=True)
class ProvenancePageRecord:
    """Referencia sanitizada a las dos fuentes independientes de evidencia."""

    expected: ExpectedProvenancePage
    request_id: str
    reservation_id: str
    nonce: str
    receipt_digest: str
    worker_evidence_id: str
    physical_evidence_id: str
    trace_id: str
    custom_span_id: str
    fetch_span_id: str
    raw_response_sha256: str
    physical_started_at_utc: datetime
    response_completed_at_utc: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.expected, ExpectedProvenancePage):
            _fail("page_record_expected_invalid")
        for name in (
            "request_id",
            "reservation_id",
            "nonce",
            "trace_id",
            "custom_span_id",
            "fetch_span_id",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), f"page_record_{name}_invalid"))
        for name in (
            "receipt_digest",
            "worker_evidence_id",
            "physical_evidence_id",
            "raw_response_sha256",
        ):
            object.__setattr__(self, name, _sha256(getattr(self, name), f"page_record_{name}_invalid"))
        started = _utc(self.physical_started_at_utc, "page_record_physical_started_at_invalid")
        completed = _utc(self.response_completed_at_utc, "page_record_response_completed_at_invalid")
        if completed < started:
            _fail("page_record_time_order_invalid")
        object.__setattr__(self, "physical_started_at_utc", started)
        object.__setattr__(self, "response_completed_at_utc", completed)

    def canonical_dict(self) -> dict[str, object]:
        return {
            "custom_span_id": self.custom_span_id,
            "expected": self.expected.canonical_dict(),
            "fetch_span_id": self.fetch_span_id,
            "nonce": self.nonce,
            "physical_evidence_id": self.physical_evidence_id,
            "physical_started_at_utc": _iso_z(self.physical_started_at_utc),
            "raw_response_sha256": self.raw_response_sha256,
            "receipt_digest": self.receipt_digest,
            "request_id": self.request_id,
            "reservation_id": self.reservation_id,
            "response_completed_at_utc": _iso_z(self.response_completed_at_utc),
            "trace_id": self.trace_id,
            "worker_evidence_id": self.worker_evidence_id,
        }


@dataclass(frozen=True, slots=True)
class EdgeProvenanceRunManifest:
    """Conjunto cerrado de páginas con provenance física reconciliada."""

    run_id: str
    authorization_id: str
    approved_commit_sha: str
    github_repository: str
    github_repository_id: str
    github_ref: str
    github_workflow_ref: str
    github_environment: str
    collector_provider: str
    collector_principal: str
    collector_release_id: str
    collector_code_sha256: str
    collector_signing_key_id: str
    primary_traversal_id: str
    reconciliation_traversal_id: str
    primary_order_by: str
    reconciliation_order_by: str
    pages: tuple[ProvenancePageRecord, ...]
    schema_version: str = RUN_MANIFEST_SCHEMA_VERSION
    production_authority: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != RUN_MANIFEST_SCHEMA_VERSION:
            _fail("run_manifest_schema_version_invalid")
        for name in (
            "run_id",
            "authorization_id",
            "github_repository",
            "github_repository_id",
            "github_ref",
            "github_workflow_ref",
            "github_environment",
            "collector_provider",
            "collector_principal",
            "collector_release_id",
            "collector_signing_key_id",
            "primary_traversal_id",
            "reconciliation_traversal_id",
            "primary_order_by",
            "reconciliation_order_by",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), f"run_manifest_{name}_invalid"))
        object.__setattr__(
            self,
            "approved_commit_sha",
            _sha1(self.approved_commit_sha, "run_manifest_approved_commit_sha_invalid"),
        )
        object.__setattr__(
            self,
            "collector_code_sha256",
            _sha256(self.collector_code_sha256, "run_manifest_collector_code_sha256_invalid"),
        )
        if self.primary_traversal_id == self.reconciliation_traversal_id:
            _fail("run_manifest_traversal_ids_not_distinct")
        if self.primary_order_by == self.reconciliation_order_by:
            _fail("run_manifest_order_by_not_distinct")
        if not self.pages:
            _fail("run_manifest_pages_empty")
        if any(not isinstance(page, ProvenancePageRecord) for page in self.pages):
            _fail("run_manifest_page_invalid")
        if self.production_authority is not False:
            _fail("run_manifest_production_authority_forbidden")

    def canonical_dict(self) -> dict[str, object]:
        return {
            "approved_commit_sha": self.approved_commit_sha,
            "authorization_id": self.authorization_id,
            "collector_code_sha256": self.collector_code_sha256,
            "collector_principal": self.collector_principal,
            "collector_provider": self.collector_provider,
            "collector_release_id": self.collector_release_id,
            "collector_signing_key_id": self.collector_signing_key_id,
            "github_environment": self.github_environment,
            "github_ref": self.github_ref,
            "github_repository": self.github_repository,
            "github_repository_id": self.github_repository_id,
            "github_workflow_ref": self.github_workflow_ref,
            "pages": [page.canonical_dict() for page in self.pages],
            "primary_order_by": self.primary_order_by,
            "primary_traversal_id": self.primary_traversal_id,
            "reconciliation_order_by": self.reconciliation_order_by,
            "reconciliation_traversal_id": self.reconciliation_traversal_id,
            "run_id": self.run_id,
            "schema_version": self.schema_version,
        }

    @property
    def digest(self) -> str:
        material = _RUN_MANIFEST_DOMAIN + canonical_json_bytes(self.canonical_dict())
        return hashlib.sha256(material).hexdigest()

    @property
    def request_count(self) -> int:
        return len(self.pages)

    @property
    def latest_response_completed_at_utc(self) -> datetime:
        return max(page.response_completed_at_utc for page in self.pages)


def _expected_from_page(page: PlatformReconciledEdgePage) -> ExpectedProvenancePage:
    payload = page.page.verified_receipt.receipt.payload
    return ExpectedProvenancePage(
        traversal_role=payload.traversal_role,
        traversal_id=payload.traversal_id,
        partition_id=payload.partition_id,
        order_by=payload.order_by,
        from_index=payload.from_index,
        to_index=payload.to_index,
        request_digest=payload.request_digest,
    )


def _record_from_page(page: PlatformReconciledEdgePage) -> ProvenancePageRecord:
    if not isinstance(page, PlatformReconciledEdgePage):
        _fail("platform_page_invalid")
    if page.platform_evidence_reconciled is not True:
        _fail("platform_page_unreconciled")
    if page.page.cryptographic_signature_verified is not True:
        _fail("platform_page_signature_unverified")
    try:
        page = reconcile_cloudflare_origin_trace(page.page, [page.trace_evidence])
    except CloudflareTraceEvidenceError as exc:
        raise EdgeProvenanceRunError(f"platform_page_{exc.code}") from exc
    payload = page.page.verified_receipt.receipt.payload
    trace = page.trace_evidence
    return ProvenancePageRecord(
        expected=_expected_from_page(page),
        request_id=payload.request_id,
        reservation_id=payload.reservation_id,
        nonce=payload.nonce,
        receipt_digest=page.page.verified_receipt.receipt_digest,
        worker_evidence_id=page.page.worker_evidence_id,
        physical_evidence_id=trace.physical_evidence_id,
        trace_id=trace.trace_id,
        custom_span_id=trace.custom_span_id,
        fetch_span_id=trace.fetch_span_id,
        raw_response_sha256=payload.raw_response_sha256,
        physical_started_at_utc=payload.physical_started_at_utc,
        response_completed_at_utc=payload.response_completed_at_utc,
    )


def _assert_unique(records: Sequence[ProvenancePageRecord], attribute: str, code: str) -> None:
    values = [getattr(record, attribute) for record in records]
    if len(set(values)) != len(values):
        _fail(code)


def build_edge_provenance_run_manifest(
    *,
    expected_pages: Sequence[ExpectedProvenancePage],
    reconciled_pages: Sequence[PlatformReconciledEdgePage],
) -> EdgeProvenanceRunManifest:
    """Cierra el plan esperado contra evidencia exacta, sin extras ni faltantes."""

    if not isinstance(expected_pages, Sequence) or isinstance(expected_pages, (str, bytes)):
        _fail("expected_pages_invalid")
    if not isinstance(reconciled_pages, Sequence) or isinstance(reconciled_pages, (str, bytes)):
        _fail("reconciled_pages_invalid")
    expected = tuple(expected_pages)
    if not expected:
        _fail("expected_pages_empty")
    if any(not isinstance(item, ExpectedProvenancePage) for item in expected):
        _fail("expected_page_invalid")
    expected_identities = [item.identity for item in expected]
    if len(set(expected_identities)) != len(expected_identities):
        _fail("expected_page_duplicate")

    pages = tuple(reconciled_pages)
    if not pages:
        _fail("reconciled_pages_empty")
    records = tuple(_record_from_page(page) for page in pages)
    observed_by_identity = {record.expected.identity: record for record in records}
    if len(observed_by_identity) != len(records):
        _fail("observed_page_identity_duplicate")
    expected_set = set(expected_identities)
    observed_set = set(observed_by_identity)
    if observed_set - expected_set:
        _fail("unexpected_provenance_page")
    if expected_set - observed_set:
        _fail("missing_provenance_page")

    _assert_unique(records, "request_id", "request_id_reused")
    _assert_unique(records, "reservation_id", "reservation_id_reused")
    _assert_unique(records, "nonce", "nonce_reused")
    _assert_unique(records, "receipt_digest", "receipt_reused")
    _assert_unique(records, "worker_evidence_id", "worker_evidence_reused")
    _assert_unique(records, "physical_evidence_id", "physical_evidence_reused")
    _assert_unique(records, "fetch_span_id", "fetch_span_reused")

    first_payload = pages[0].page.verified_receipt.receipt.payload
    invariant_names = (
        "run_id",
        "authorization_id",
        "approved_commit_sha",
        "github_repository",
        "github_repository_id",
        "github_ref",
        "github_workflow_ref",
        "github_environment",
        "collector_provider",
        "collector_principal",
        "collector_release_id",
        "collector_code_sha256",
        "signing_key_id",
    )
    for page in pages[1:]:
        payload = page.page.verified_receipt.receipt.payload
        for name in invariant_names:
            if getattr(payload, name) != getattr(first_payload, name):
                _fail(f"run_context_{name}_mismatch")

    primary_ids = {item.traversal_id for item in expected if item.traversal_role == "primary"}
    reconciliation_ids = {
        item.traversal_id for item in expected if item.traversal_role == "reconciliation"
    }
    if len(primary_ids) != 1:
        _fail("primary_traversal_not_unique")
    if len(reconciliation_ids) != 1:
        _fail("reconciliation_traversal_not_unique")
    primary_id = next(iter(primary_ids))
    reconciliation_id = next(iter(reconciliation_ids))
    if primary_id == reconciliation_id:
        _fail("traversal_ids_not_distinct")

    primary_orders = {item.order_by for item in expected if item.traversal_role == "primary"}
    reconciliation_orders = {
        item.order_by for item in expected if item.traversal_role == "reconciliation"
    }
    if len(primary_orders) != 1:
        _fail("primary_order_by_not_unique")
    if len(reconciliation_orders) != 1:
        _fail("reconciliation_order_by_not_unique")
    primary_order = next(iter(primary_orders))
    reconciliation_order = next(iter(reconciliation_orders))
    if primary_order == reconciliation_order:
        _fail("traversal_order_by_not_distinct")

    ordered_records = tuple(sorted(records, key=lambda record: record.expected.identity))
    return EdgeProvenanceRunManifest(
        run_id=first_payload.run_id,
        authorization_id=first_payload.authorization_id,
        approved_commit_sha=first_payload.approved_commit_sha,
        github_repository=first_payload.github_repository,
        github_repository_id=first_payload.github_repository_id,
        github_ref=first_payload.github_ref,
        github_workflow_ref=first_payload.github_workflow_ref,
        github_environment=first_payload.github_environment,
        collector_provider=first_payload.collector_provider,
        collector_principal=first_payload.collector_principal,
        collector_release_id=first_payload.collector_release_id,
        collector_code_sha256=first_payload.collector_code_sha256,
        collector_signing_key_id=first_payload.signing_key_id,
        primary_traversal_id=primary_id,
        reconciliation_traversal_id=reconciliation_id,
        primary_order_by=primary_order,
        reconciliation_order_by=reconciliation_order,
        pages=ordered_records,
    )
