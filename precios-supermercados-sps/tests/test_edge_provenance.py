from __future__ import annotations

import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from precios_supermercados.edge_provenance import (
    EDGE_PROVENANCE_SCHEMA_VERSION,
    EdgeAttestationPayload,
    EdgeReceiptPayload,
    SignedEdgeAttestation,
    SignedEdgeReceipt,
    canonical_json_bytes,
    validate_edge_provenance_structure,
)


COMMIT = "a" * 40
REQUEST_DIGEST = "b" * 64
REQUEST_SHA = "c" * 64
RESPONSE_SHA = "d" * 64
CODE_SHA = "e" * 64
T0 = datetime(2026, 8, 21, 15, 0, tzinfo=timezone.utc)
SIGNATURE = base64.urlsafe_b64encode(b"offline-edge-signature").decode("ascii").rstrip("=")


def _receipt_payload(
    *,
    request_id: str,
    reservation_id: str,
    nonce: str,
    traversal_role: str,
    traversal_id: str,
    order_by: str,
    from_index: int,
    provider: str = "cloudflare_workers",
    principal: str = "cloudflare:worker:precios-sps-provenance",
    completed_offset: int = 1,
    http_method: str = "GET",
) -> EdgeReceiptPayload:
    return EdgeReceiptPayload(
        run_id="run-001",
        request_id=request_id,
        reservation_id=reservation_id,
        authorization_id="authorization-001",
        approved_commit_sha=COMMIT,
        request_digest=REQUEST_DIGEST,
        traversal_id=traversal_id,
        traversal_role=traversal_role,
        order_by=order_by,
        partition_id="root",
        from_index=from_index,
        to_index=from_index + 9,
        http_method=http_method,
        target_scheme="https",
        target_host="WWW.LACOLONIA.COM",
        target_path="/_v/segment/graphql/v1",
        canonical_request_sha256=REQUEST_SHA,
        raw_response_sha256=RESPONSE_SHA,
        response_status=200,
        response_body_bytes=1234,
        physical_started_at_utc=T0 + timedelta(seconds=from_index),
        response_completed_at_utc=T0 + timedelta(seconds=from_index + completed_offset),
        github_repository="jchernandez-portfolio/precios-supermercados-sps",
        github_repository_id="1282475205",
        github_ref="refs/heads/main",
        github_workflow_ref=(
            "jchernandez-portfolio/precios-supermercados-sps/.github/workflows/"
            "precios-supermercados-sps-la-colonia-live.yml@refs/heads/main"
        ),
        github_environment="la-colonia-live",
        github_run_id="32400000000",
        github_run_attempt=1,
        oidc_subject=(
            "repo:jchernandez-portfolio/precios-supermercados-sps:environment:la-colonia-live"
        ),
        oidc_jti="oidc-jti-001",
        collector_provider=provider,
        collector_principal=principal,
        collector_execution="cf-version-001",
        collector_release_id="release-001",
        collector_code_sha256=CODE_SHA,
        signing_algorithm="Ed25519",
        signing_key_id="edge-ed25519-v1",
        nonce=nonce,
    )


def _signed_receipts() -> tuple[SignedEdgeReceipt, SignedEdgeReceipt]:
    primary = SignedEdgeReceipt(
        _receipt_payload(
            request_id="request-primary-001",
            reservation_id="reservation-primary-001",
            nonce="nonce-primary-001",
            traversal_role="primary",
            traversal_id="traversal-primary",
            order_by="OrderByNameASC",
            from_index=0,
        ),
        SIGNATURE,
    )
    reconciliation = SignedEdgeReceipt(
        _receipt_payload(
            request_id="request-reconciliation-001",
            reservation_id="reservation-reconciliation-001",
            nonce="nonce-reconciliation-001",
            traversal_role="reconciliation",
            traversal_id="traversal-reconciliation",
            order_by="OrderByNameDESC",
            from_index=20,
        ),
        SIGNATURE,
    )
    return primary, reconciliation


def _attestation_payload(
    receipts: tuple[SignedEdgeReceipt, ...],
    *,
    verdict: str = "VERIFIED",
    reasons: tuple[str, ...] = (),
    provider: str = "cloudflare_workers",
    principal: str = "cloudflare:worker:precios-sps-provenance",
) -> EdgeAttestationPayload:
    latest = max(item.payload.response_completed_at_utc for item in receipts)
    return EdgeAttestationPayload(
        run_id="run-001",
        authorization_id="authorization-001",
        approved_commit_sha=COMMIT,
        github_repository="jchernandez-portfolio/precios-supermercados-sps",
        github_repository_id="1282475205",
        github_ref="refs/heads/main",
        github_workflow_ref=(
            "jchernandez-portfolio/precios-supermercados-sps/.github/workflows/"
            "precios-supermercados-sps-la-colonia-live.yml@refs/heads/main"
        ),
        github_environment="la-colonia-live",
        collector_provider=provider,
        collector_principal=principal,
        collector_receipt_digests=tuple(item.digest for item in receipts),
        physical_evidence_ids=tuple(
            f"durable-object:evidence-{index}"
            for index, _ in enumerate(receipts, start=1)
        ),
        primary_traversal_id="traversal-primary",
        reconciliation_traversal_id="traversal-reconciliation",
        primary_order_by="OrderByNameASC",
        reconciliation_order_by="OrderByNameDESC",
        verified_request_count=len(receipts),
        verification_started_at_utc=latest + timedelta(seconds=1),
        verification_completed_at_utc=latest + timedelta(seconds=2),
        verdict=verdict,
        reasons=reasons,
        verifier_provider="cloudflare_workers",
        verifier_principal="cloudflare:worker:precios-sps-verifier",
        verifier_execution="cf-version-verifier-001",
        signing_algorithm="Ed25519",
        verifier_signing_key_id="verifier-ed25519-v1",
    )


def test_cloudflare_receipt_normalizes_host_and_schema() -> None:
    payload = _receipt_payload(
        request_id="request-1",
        reservation_id="reservation-1",
        nonce="nonce-1",
        traversal_role="primary",
        traversal_id="primary",
        order_by="asc",
        from_index=0,
    )

    assert payload.target_host == "www.lacolonia.com"
    assert payload.schema_version == EDGE_PROVENANCE_SCHEMA_VERSION
    assert payload.collector_provider == "cloudflare_workers"
    assert payload.http_method == "GET"


def test_google_cloud_provider_is_supported_without_service_account_field() -> None:
    payload = _receipt_payload(
        request_id="request-1",
        reservation_id="reservation-1",
        nonce="nonce-1",
        traversal_role="primary",
        traversal_id="primary",
        order_by="asc",
        from_index=0,
        provider="google_cloud_run",
        principal="collector@precios-supermercados.iam.gserviceaccount.com",
    )

    assert payload.collector_provider == "google_cloud_run"
    assert payload.collector_principal.endswith("gserviceaccount.com")
    assert payload.signing_key_id == "edge-ed25519-v1"


def test_unknown_provider_fails_closed() -> None:
    with pytest.raises(ValueError, match="collector_provider no soportado"):
        _receipt_payload(
            request_id="request-1",
            reservation_id="reservation-1",
            nonce="nonce-1",
            traversal_role="primary",
            traversal_id="primary",
            order_by="asc",
            from_index=0,
            provider="local_python",
        )


def test_non_ed25519_signature_algorithm_is_rejected() -> None:
    payload = _receipt_payload(
        request_id="request-1",
        reservation_id="reservation-1",
        nonce="nonce-1",
        traversal_role="primary",
        traversal_id="primary",
        order_by="asc",
        from_index=0,
    )

    with pytest.raises(ValueError, match="signing_algorithm no soportado"):
        replace(payload, signing_algorithm="HMAC-SHA256")


def test_signature_requires_canonical_unpadded_base64url() -> None:
    payload = _receipt_payload(
        request_id="request-1",
        reservation_id="reservation-1",
        nonce="nonce-1",
        traversal_role="primary",
        traversal_id="primary",
        order_by="asc",
        from_index=0,
    )

    with pytest.raises(ValueError, match="sin padding"):
        SignedEdgeReceipt(payload, SIGNATURE + "=")


def test_bool_cannot_bypass_integer_fields() -> None:
    payload = _receipt_payload(
        request_id="request-1",
        reservation_id="reservation-1",
        nonce="nonce-1",
        traversal_role="primary",
        traversal_id="primary",
        order_by="asc",
        from_index=0,
    )

    with pytest.raises(ValueError, match="github_run_attempt"):
        replace(payload, github_run_attempt=True)


def test_response_chronology_is_fail_closed() -> None:
    payload = _receipt_payload(
        request_id="request-1",
        reservation_id="reservation-1",
        nonce="nonce-1",
        traversal_role="primary",
        traversal_id="primary",
        order_by="asc",
        from_index=0,
    )

    with pytest.raises(ValueError, match="precede al inicio físico"):
        replace(
            payload,
            response_completed_at_utc=payload.physical_started_at_utc - timedelta(seconds=1),
        )


def test_target_transport_accepts_get_or_post_but_rejects_other_methods() -> None:
    payload = _receipt_payload(
        request_id="request-1",
        reservation_id="reservation-1",
        nonce="nonce-1",
        traversal_role="primary",
        traversal_id="primary",
        order_by="asc",
        from_index=0,
    )

    assert replace(payload, http_method="GET").http_method == "GET"
    assert replace(payload, http_method="POST").http_method == "POST"
    with pytest.raises(ValueError, match="GET o POST"):
        replace(payload, http_method="PUT")
    with pytest.raises(ValueError, match="target_scheme"):
        replace(payload, target_scheme="http")
    with pytest.raises(ValueError, match="target_path"):
        replace(payload, target_path="_v/segment/graphql/v1")


def test_canonical_json_is_sorted_utf8_and_compact() -> None:
    assert canonical_json_bytes({"z": "á", "a": 1}) == b'{"a":1,"z":"\xc3\xa1"}'


def test_receipt_digest_binds_payload_and_signature() -> None:
    payload = _receipt_payload(
        request_id="request-1",
        reservation_id="reservation-1",
        nonce="nonce-1",
        traversal_role="primary",
        traversal_id="primary",
        order_by="asc",
        from_index=0,
    )
    receipt = SignedEdgeReceipt(payload, SIGNATURE)
    changed = SignedEdgeReceipt(replace(payload, nonce="nonce-2"), SIGNATURE)

    assert receipt.digest != changed.digest
    assert len(receipt.digest) == 64


def test_verified_attestation_requires_no_reasons() -> None:
    receipts = _signed_receipts()

    with pytest.raises(ValueError, match="VERIFIED no puede contener reasons"):
        _attestation_payload(receipts, verdict="VERIFIED", reasons=("unexpected",))


def test_rejected_attestation_requires_reason() -> None:
    receipts = _signed_receipts()

    with pytest.raises(ValueError, match="REJECTED requiere"):
        _attestation_payload(receipts, verdict="REJECTED", reasons=())


def test_attestation_requires_one_physical_evidence_per_receipt() -> None:
    receipts = _signed_receipts()
    payload = _attestation_payload(receipts)

    with pytest.raises(ValueError, match="una evidencia física por receipt"):
        replace(payload, physical_evidence_ids=("evidence-1",))


def test_attestation_requires_distinct_traversals_and_orderings() -> None:
    receipts = _signed_receipts()
    payload = _attestation_payload(receipts)

    with pytest.raises(ValueError, match="traversals distintas"):
        replace(payload, reconciliation_traversal_id=payload.primary_traversal_id)
    with pytest.raises(ValueError, match="order_by distintos"):
        replace(payload, reconciliation_order_by=payload.primary_order_by)


def test_structural_validation_is_consistent_but_never_authoritative() -> None:
    receipts = _signed_receipts()
    attestation = SignedEdgeAttestation(_attestation_payload(receipts), SIGNATURE)

    result = validate_edge_provenance_structure(receipts, attestation)

    assert result.consistent is True
    assert result.reasons == ()
    assert result.production_authority is False


def test_structural_validation_detects_provider_mismatch() -> None:
    receipts = _signed_receipts()
    attestation_payload = _attestation_payload(
        receipts,
        provider="google_cloud_run",
        principal="collector@precios-supermercados.iam.gserviceaccount.com",
    )
    attestation = SignedEdgeAttestation(attestation_payload, SIGNATURE)

    result = validate_edge_provenance_structure(receipts, attestation)

    assert result.consistent is False
    assert "receipt_collector_provider_mismatch" in result.reasons
    assert "receipt_collector_principal_mismatch" in result.reasons


def test_structural_validation_detects_oidc_context_mismatch() -> None:
    primary, reconciliation = _signed_receipts()
    changed = SignedEdgeReceipt(
        replace(primary.payload, github_environment="other-environment"),
        SIGNATURE,
    )
    receipts = (changed, reconciliation)
    attestation = SignedEdgeAttestation(_attestation_payload((primary, reconciliation)), SIGNATURE)

    result = validate_edge_provenance_structure(receipts, attestation)

    assert result.consistent is False
    assert "receipt_environment_mismatch" in result.reasons
    assert "receipt_digest_sequence_mismatch" in result.reasons


def test_structural_validation_detects_duplicate_nonce() -> None:
    primary, reconciliation = _signed_receipts()
    duplicate = SignedEdgeReceipt(
        replace(reconciliation.payload, nonce=primary.payload.nonce),
        SIGNATURE,
    )
    receipts = (primary, duplicate)
    attestation = SignedEdgeAttestation(_attestation_payload(receipts), SIGNATURE)

    result = validate_edge_provenance_structure(receipts, attestation)

    assert result.consistent is False
    assert "duplicate_nonce" in result.reasons


def test_structural_validation_requires_primary_and_reconciliation() -> None:
    primary, _ = _signed_receipts()
    attestation_payload = _attestation_payload((primary,))
    attestation_payload = replace(
        attestation_payload,
        reconciliation_traversal_id="traversal-unused",
        reconciliation_order_by="unused-desc",
    )
    attestation = SignedEdgeAttestation(attestation_payload, SIGNATURE)

    result = validate_edge_provenance_structure((primary,), attestation)

    assert result.consistent is False
    assert "reconciliation_receipts_missing" in result.reasons


def test_verification_cannot_start_before_last_receipt_finishes() -> None:
    receipts = _signed_receipts()
    payload = _attestation_payload(receipts)
    early = replace(
        payload,
        verification_started_at_utc=max(
            item.payload.response_completed_at_utc for item in receipts
        ) - timedelta(microseconds=1),
    )
    attestation = SignedEdgeAttestation(early, SIGNATURE)

    result = validate_edge_provenance_structure(receipts, attestation)

    assert result.consistent is False
    assert "verification_started_before_receipts_completed" in result.reasons


def test_non_utc_input_is_normalized_not_rejected() -> None:
    offset = timezone(timedelta(hours=-6))
    payload = _receipt_payload(
        request_id="request-1",
        reservation_id="reservation-1",
        nonce="nonce-1",
        traversal_role="primary",
        traversal_id="primary",
        order_by="asc",
        from_index=0,
    )
    localized = replace(
        payload,
        physical_started_at_utc=payload.physical_started_at_utc.astimezone(offset),
        response_completed_at_utc=payload.response_completed_at_utc.astimezone(offset),
    )

    assert localized.physical_started_at_utc.tzinfo == timezone.utc
    assert localized.response_completed_at_utc.tzinfo == timezone.utc
