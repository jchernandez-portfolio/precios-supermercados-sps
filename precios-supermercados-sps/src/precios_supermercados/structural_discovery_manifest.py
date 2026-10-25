"""Manifest cerrado para las dos observaciones estructurales de La Colonia.

Une `root_total` y `category_tree` sólo después de que ambas tengan receipt
Ed25519 verificado, body GraphQL validado y evidencia física de Cloudflare
reconciliada. El resultado puede alimentar la derivación del universo del
catálogo, pero continúa sin conceder autoridad productiva.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import NoReturn

from precios_supermercados.cloudflare_structural_trace_evidence import (
    CloudflareStructuralTraceEvidenceError,
    PlatformReconciledStructuralObservation,
    assert_distinct_structural_evidence,
    reconcile_cloudflare_structural_trace,
)
from precios_supermercados.edge_provenance import canonical_json_bytes
from precios_supermercados.scrapers.la_colonia_catalog_coverage import (
    StructuralDiscoveryReport,
)
from precios_supermercados.scrapers.la_colonia_catalog_partitions import (
    build_structural_discovery_report,
)
from precios_supermercados.scrapers.la_colonia_facet_discovery import (
    CATALOG_CATEGORIES_V1,
    FACET_DISCOVERY_DELAY_SECONDS,
)

STRUCTURAL_DISCOVERY_MANIFEST_SCHEMA_VERSION = "1"
_STRUCTURAL_DISCOVERY_MANIFEST_DOMAIN = b"precios-sps/structural-discovery-manifest/v1\0"
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class StructuralDiscoveryManifestError(ValueError):
    """Las dos observaciones no forman una ejecución estructural cerrada."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


def _fail(code: str, message: str | None = None) -> NoReturn:
    raise StructuralDiscoveryManifestError(code, message)


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


def _positive_int(value: object, code: str, *, maximum: int = 2**53 - 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        _fail(code)
    return value


def _utc(value: object, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class StructuralObservationRecord:
    request_kind: str
    request_digest: str
    request_id: str
    reservation_id: str
    nonce: str
    receipt_digest: str
    public_key_spki_sha256: str
    physical_evidence_id: str
    trace_id: str
    custom_span_id: str
    fetch_span_id: str
    raw_response_sha256: str
    response_body_bytes: int
    records_filtered: int
    physical_started_at_utc: datetime
    response_completed_at_utc: datetime

    def __post_init__(self) -> None:
        if self.request_kind not in {"root_total", "category_tree"}:
            _fail("structural_record_request_kind_invalid")
        for name in (
            "request_id",
            "reservation_id",
            "nonce",
            "trace_id",
            "custom_span_id",
            "fetch_span_id",
        ):
            object.__setattr__(
                self,
                name,
                _text(getattr(self, name), f"structural_record_{name}_invalid"),
            )
        for name in (
            "request_digest",
            "receipt_digest",
            "public_key_spki_sha256",
            "physical_evidence_id",
            "raw_response_sha256",
        ):
            object.__setattr__(
                self,
                name,
                _sha256(getattr(self, name), f"structural_record_{name}_invalid"),
            )
        _positive_int(self.response_body_bytes, "structural_record_response_body_bytes_invalid")
        _positive_int(self.records_filtered, "structural_record_records_filtered_invalid")
        started = _utc(
            self.physical_started_at_utc,
            "structural_record_physical_started_at_invalid",
        )
        completed = _utc(
            self.response_completed_at_utc,
            "structural_record_response_completed_at_invalid",
        )
        if completed < started:
            _fail("structural_record_time_order_invalid")
        object.__setattr__(self, "physical_started_at_utc", started)
        object.__setattr__(self, "response_completed_at_utc", completed)

    def canonical_dict(self) -> dict[str, object]:
        return {
            "custom_span_id": self.custom_span_id,
            "fetch_span_id": self.fetch_span_id,
            "nonce": self.nonce,
            "physical_evidence_id": self.physical_evidence_id,
            "physical_started_at_utc": _timestamp(self.physical_started_at_utc),
            "public_key_spki_sha256": self.public_key_spki_sha256,
            "raw_response_sha256": self.raw_response_sha256,
            "receipt_digest": self.receipt_digest,
            "records_filtered": self.records_filtered,
            "request_digest": self.request_digest,
            "request_id": self.request_id,
            "request_kind": self.request_kind,
            "reservation_id": self.reservation_id,
            "response_body_bytes": self.response_body_bytes,
            "response_completed_at_utc": _timestamp(self.response_completed_at_utc),
            "trace_id": self.trace_id,
        }


@dataclass(frozen=True, slots=True)
class VerifiedStructuralDiscovery:
    """Par root/tree físicamente reconciliado y estructura derivada internamente."""

    run_id: str
    authorization_id: str
    approved_commit_sha: str
    github_repository: str
    github_repository_id: str
    github_ref: str
    github_workflow_ref: str
    github_environment: str
    github_run_id: str
    github_run_attempt: int
    oidc_subject: str
    collector_provider: str
    collector_principal: str
    collector_release_id: str
    collector_code_sha256: str
    collector_signing_key_id: str
    root_total: StructuralObservationRecord
    category_tree: StructuralObservationRecord
    tree_digest: str
    leaf_partitions_count: int
    positive_leaf_partitions: int
    structure: StructuralDiscoveryReport = field(repr=False, compare=False)
    schema_version: str = STRUCTURAL_DISCOVERY_MANIFEST_SCHEMA_VERSION
    production_authority: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != STRUCTURAL_DISCOVERY_MANIFEST_SCHEMA_VERSION:
            _fail("structural_manifest_schema_version_invalid")
        for name in (
            "run_id",
            "authorization_id",
            "github_repository",
            "github_repository_id",
            "github_ref",
            "github_workflow_ref",
            "github_environment",
            "github_run_id",
            "oidc_subject",
            "collector_provider",
            "collector_principal",
            "collector_release_id",
            "collector_signing_key_id",
        ):
            object.__setattr__(
                self,
                name,
                _text(getattr(self, name), f"structural_manifest_{name}_invalid"),
            )
        object.__setattr__(
            self,
            "approved_commit_sha",
            _sha1(self.approved_commit_sha, "structural_manifest_approved_commit_sha_invalid"),
        )
        object.__setattr__(
            self,
            "collector_code_sha256",
            _sha256(
                self.collector_code_sha256,
                "structural_manifest_collector_code_sha256_invalid",
            ),
        )
        object.__setattr__(
            self,
            "tree_digest",
            _sha256(self.tree_digest, "structural_manifest_tree_digest_invalid"),
        )
        _positive_int(
            self.github_run_attempt,
            "structural_manifest_github_run_attempt_invalid",
            maximum=100,
        )
        if not isinstance(self.root_total, StructuralObservationRecord):
            _fail("structural_manifest_root_record_invalid")
        if not isinstance(self.category_tree, StructuralObservationRecord):
            _fail("structural_manifest_tree_record_invalid")
        if self.root_total.request_kind != "root_total":
            _fail("structural_manifest_root_kind_invalid")
        if self.category_tree.request_kind != "category_tree":
            _fail("structural_manifest_tree_kind_invalid")
        if not isinstance(self.structure, StructuralDiscoveryReport) or not self.structure.valid:
            _fail("structural_manifest_structure_invalid")
        if self.structure.run_id != self.run_id:
            _fail("structural_manifest_structure_run_id_mismatch")
        if self.structure.tree_digest != self.tree_digest:
            _fail("structural_manifest_structure_digest_mismatch")
        if self.structure.root_total != self.root_total.records_filtered:
            _fail("structural_manifest_structure_root_total_mismatch")
        if self.root_total.records_filtered != self.category_tree.records_filtered:
            _fail("structural_manifest_total_mismatch")
        if self.leaf_partitions_count != len(self.structure.valid_leaves):
            _fail("structural_manifest_leaf_count_mismatch")
        expected_positive = sum(
            leaf.expected_products > 0 for leaf in self.structure.valid_leaves
        )
        if self.positive_leaf_partitions != expected_positive:
            _fail("structural_manifest_positive_leaf_count_mismatch")
        if self.production_authority is not False:
            _fail("structural_manifest_production_authority_forbidden")

    def canonical_dict(self) -> dict[str, object]:
        return {
            "approved_commit_sha": self.approved_commit_sha,
            "authorization_id": self.authorization_id,
            "category_tree": self.category_tree.canonical_dict(),
            "collector_code_sha256": self.collector_code_sha256,
            "collector_principal": self.collector_principal,
            "collector_provider": self.collector_provider,
            "collector_release_id": self.collector_release_id,
            "collector_signing_key_id": self.collector_signing_key_id,
            "github_environment": self.github_environment,
            "github_ref": self.github_ref,
            "github_repository": self.github_repository,
            "github_repository_id": self.github_repository_id,
            "github_run_attempt": self.github_run_attempt,
            "github_run_id": self.github_run_id,
            "github_workflow_ref": self.github_workflow_ref,
            "leaf_partitions_count": self.leaf_partitions_count,
            "oidc_subject": self.oidc_subject,
            "positive_leaf_partitions": self.positive_leaf_partitions,
            "root_total": self.root_total.canonical_dict(),
            "run_id": self.run_id,
            "schema_version": self.schema_version,
            "tree_digest": self.tree_digest,
        }

    @property
    def digest(self) -> str:
        return hashlib.sha256(
            _STRUCTURAL_DISCOVERY_MANIFEST_DOMAIN
            + canonical_json_bytes(self.canonical_dict())
        ).hexdigest()


_INVARIANTS = (
    "run_id",
    "authorization_id",
    "approved_commit_sha",
    "github_repository",
    "github_repository_id",
    "github_ref",
    "github_workflow_ref",
    "github_environment",
    "github_run_id",
    "github_run_attempt",
    "oidc_subject",
    "collector_provider",
    "collector_principal",
    "collector_release_id",
    "collector_code_sha256",
    "signing_key_id",
)


def _rereconcile(
    value: PlatformReconciledStructuralObservation,
    *,
    expected_kind: str,
) -> PlatformReconciledStructuralObservation:
    if not isinstance(value, PlatformReconciledStructuralObservation):
        _fail(f"{expected_kind}_platform_observation_invalid")
    if value.platform_evidence_reconciled is not True:
        _fail(f"{expected_kind}_platform_evidence_unreconciled")
    try:
        reconciled = reconcile_cloudflare_structural_trace(
            value.observation,
            [value.trace_evidence],
        )
    except CloudflareStructuralTraceEvidenceError as exc:
        raise StructuralDiscoveryManifestError(
            f"{expected_kind}_platform_{exc.code}"
        ) from exc
    if reconciled.observation.request_kind != expected_kind:
        _fail(f"{expected_kind}_request_kind_mismatch")
    return reconciled


def _record(value: PlatformReconciledStructuralObservation) -> StructuralObservationRecord:
    observation = value.observation
    verified = observation.verified_receipt
    payload = verified.receipt.payload
    trace = value.trace_evidence
    return StructuralObservationRecord(
        request_kind=payload.request_kind,
        request_digest=payload.request_digest,
        request_id=payload.request_id,
        reservation_id=payload.reservation_id,
        nonce=payload.nonce,
        receipt_digest=verified.receipt_digest,
        public_key_spki_sha256=verified.public_key_spki_sha256,
        physical_evidence_id=trace.physical_evidence_id,
        trace_id=trace.trace_id,
        custom_span_id=trace.custom_span_id,
        fetch_span_id=trace.fetch_span_id,
        raw_response_sha256=payload.raw_response_sha256,
        response_body_bytes=payload.response_body_bytes,
        records_filtered=observation.records_filtered,
        physical_started_at_utc=payload.physical_started_at_utc,
        response_completed_at_utc=payload.response_completed_at_utc,
    )


def _require_distinct(root: StructuralObservationRecord, tree: StructuralObservationRecord) -> None:
    for attribute, code in (
        ("request_id", "structural_request_id_reused"),
        ("reservation_id", "structural_reservation_id_reused"),
        ("nonce", "structural_nonce_reused"),
        ("receipt_digest", "structural_receipt_reused"),
        ("physical_evidence_id", "structural_physical_evidence_reused"),
        ("fetch_span_id", "structural_fetch_span_reused"),
    ):
        if getattr(root, attribute) == getattr(tree, attribute):
            _fail(code)


def build_verified_structural_discovery(
    *,
    root_total: PlatformReconciledStructuralObservation,
    category_tree: PlatformReconciledStructuralObservation,
) -> VerifiedStructuralDiscovery:
    """Exige exactamente root + tree coherentes y deriva la estructura privada."""

    root = _rereconcile(root_total, expected_kind="root_total")
    tree = _rereconcile(category_tree, expected_kind="category_tree")
    try:
        assert_distinct_structural_evidence(root, tree)
    except CloudflareStructuralTraceEvidenceError as exc:
        raise StructuralDiscoveryManifestError(f"structural_pair_{exc.code}") from exc

    root_payload = root.observation.verified_receipt.receipt.payload
    tree_payload = tree.observation.verified_receipt.receipt.payload
    for name in _INVARIANTS:
        if getattr(root_payload, name) != getattr(tree_payload, name):
            _fail(f"structural_run_context_{name}_mismatch")
    if (
        root.observation.verified_receipt.public_key_spki_sha256
        != tree.observation.verified_receipt.public_key_spki_sha256
    ):
        _fail("structural_run_public_key_mismatch")

    root_record = _record(root)
    tree_record = _record(tree)
    _require_distinct(root_record, tree_record)
    if root_record.records_filtered != tree_record.records_filtered:
        _fail("structural_total_changed")

    minimum_tree_start = root_record.response_completed_at_utc + timedelta(
        seconds=FACET_DISCOVERY_DELAY_SECONDS
    )
    if tree_record.physical_started_at_utc < minimum_tree_start:
        _fail("structural_request_pacing_not_demonstrated")

    normalized = tree.observation.normalized_payload
    sampling = normalized.get("sampling")
    facets = normalized.get("facets")
    if sampling is not False:
        _fail("structural_facets_sampling_detected")
    if not isinstance(facets, (tuple, list)):
        _fail("structural_facets_sequence_invalid")

    structure = build_structural_discovery_report(
        facets,
        run_id=root_payload.run_id,
        root_total=root_record.records_filtered,
        sampling=False,
        max_partitions=CATALOG_CATEGORIES_V1.max_partitions,
        max_category_level=CATALOG_CATEGORIES_V1.max_category_level,
    )
    if not structure.valid:
        reasons = ",".join(structure.errors) or "unknown"
        _fail("structural_report_invalid", f"structural_report_invalid:{reasons}")

    return VerifiedStructuralDiscovery(
        run_id=root_payload.run_id,
        authorization_id=root_payload.authorization_id,
        approved_commit_sha=root_payload.approved_commit_sha,
        github_repository=root_payload.github_repository,
        github_repository_id=root_payload.github_repository_id,
        github_ref=root_payload.github_ref,
        github_workflow_ref=root_payload.github_workflow_ref,
        github_environment=root_payload.github_environment,
        github_run_id=root_payload.github_run_id,
        github_run_attempt=root_payload.github_run_attempt,
        oidc_subject=root_payload.oidc_subject,
        collector_provider=root_payload.collector_provider,
        collector_principal=root_payload.collector_principal,
        collector_release_id=root_payload.collector_release_id,
        collector_code_sha256=root_payload.collector_code_sha256,
        collector_signing_key_id=root_payload.signing_key_id,
        root_total=root_record,
        category_tree=tree_record,
        tree_digest=structure.tree_digest,
        leaf_partitions_count=len(structure.valid_leaves),
        positive_leaf_partitions=sum(
            leaf.expected_products > 0 for leaf in structure.valid_leaves
        ),
        structure=structure,
    )
