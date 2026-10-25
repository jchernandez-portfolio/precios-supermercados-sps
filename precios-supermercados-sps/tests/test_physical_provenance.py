from __future__ import annotations

import base64
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from precios_supermercados.physical_provenance import (
    PhysicalReceiptPayload,
    ProvenanceAttestationPayload,
    SignedPhysicalReceipt,
    SignedProvenanceAttestation,
    validate_provenance_structure,
)

COMMIT = "a" * 40
IMAGE = "sha256:" + "b" * 64
REQUEST_DIGEST = "c" * 64
REQUEST_SHA = "d" * 64
RESPONSE_SHA = "e" * 64
T0 = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
SIGNATURE = base64.b64encode(b"offline-test-signature").decode("ascii")


def _receipt_payload(
    *,
    request_id: str,
    traversal_role: str,
    traversal_id: str,
    order_by: str,
    nonce: str,
    from_index: int,
) -> PhysicalReceiptPayload:
    return PhysicalReceiptPayload(
        run_id="run-001",
        request_id=request_id,
        reservation_id="reservation-001",
        authorization_id="authorization-001",
        approved_commit_sha=COMMIT,
        immutable_image_digest=IMAGE,
        request_digest=REQUEST_DIGEST,
        traversal_id=traversal_id,
        traversal_role=traversal_role,  # type: ignore[arg-type]
        order_by=order_by,
        partition_id="category-1:abarrotes",
        from_index=from_index,
        to_index=from_index + 9,
        http_method="POST",
        target_scheme="https",
        target_host="www.lacolonia.com",
        target_path="/_v/segment/graphql/v1",
        canonical_request_sha256=REQUEST_SHA,
        raw_response_sha256=RESPONSE_SHA,
        response_status=200,
        response_body_bytes=1234,
        physical_started_at_utc=T0 + timedelta(seconds=from_index),
        response_completed_at_utc=T0 + timedelta(seconds=from_index + 1),
        collector_service_account="collector@precios-supermercados.iam.gserviceaccount.com",
        collector_execution="jobs/collector/executions/001",
        kms_key_version="projects/p/locations/global/keyRings/r/cryptoKeys/k/cryptoKeyVersions/1",
        nonce=nonce,
    )


def _receipts() -> tuple[SignedPhysicalReceipt, SignedPhysicalReceipt]:
    return (
        SignedPhysicalReceipt(
            _receipt_payload(
                request_id="request-primary",
                traversal_role="primary",
                traversal_id="traversal-primary",
                order_by="OrderByNameASC",
                nonce="nonce-primary",
                from_index=0,
            ),
            SIGNATURE,
        ),
        SignedPhysicalReceipt(
            _receipt_payload(
                request_id="request-reconciliation",
                traversal_role="reconciliation",
                traversal_id="traversal-reconciliation",
                order_by="OrderByPriceASC",
                nonce="nonce-reconciliation",
                from_index=0,
            ),
            SIGNATURE,
        ),
    )


def _attestation(
    receipts: tuple[SignedPhysicalReceipt, ...],
    **changes: object,
) -> SignedProvenanceAttestation:
    values: dict[str, object] = {
        "run_id": "run-001",
        "authorization_id": "authorization-001",
        "approved_commit_sha": COMMIT,
        "immutable_image_digest": IMAGE,
        "collector_receipt_digests": tuple(item.digest for item in receipts),
        "swp_log_insert_ids": tuple(f"insert-{index}" for index in range(len(receipts))),
        "primary_traversal_id": "traversal-primary",
        "reconciliation_traversal_id": "traversal-reconciliation",
        "primary_order_by": "OrderByNameASC",
        "reconciliation_order_by": "OrderByPriceASC",
        "verified_request_count": len(receipts),
        "verification_started_at_utc": T0,
        "verification_completed_at_utc": T0 + timedelta(seconds=10),
        "verdict": "VERIFIED",
        "reasons": (),
        "verifier_kms_key_version": "projects/p/locations/global/keyRings/r/cryptoKeys/v/cryptoKeyVersions/1",
    }
    values.update(changes)
    return SignedProvenanceAttestation(
        ProvenanceAttestationPayload(**values),  # type: ignore[arg-type]
        SIGNATURE,
    )


def test_receipt_canonical_bytes_are_deterministic() -> None:
    payload = _receipt_payload(
        request_id="request-primary",
        traversal_role="primary",
        traversal_id="traversal-primary",
        order_by="OrderByNameASC",
        nonce="nonce-primary",
        from_index=0,
    )

    assert payload.canonical_bytes() == payload.canonical_bytes()
    assert b'"schema_version":"1"' in payload.canonical_bytes()
    assert b"offline-test-signature" not in payload.canonical_bytes()


def test_receipt_digest_binds_signature() -> None:
    payload = _receipt_payload(
        request_id="request-primary",
        traversal_role="primary",
        traversal_id="traversal-primary",
        order_by="OrderByNameASC",
        nonce="nonce-primary",
        from_index=0,
    )
    first = SignedPhysicalReceipt(payload, SIGNATURE)
    second = SignedPhysicalReceipt(
        payload,
        base64.b64encode(b"different-signature").decode("ascii"),
    )

    assert first.digest != second.digest


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("approved_commit_sha", "bad"),
        ("immutable_image_digest", "bad"),
        ("request_digest", "bad"),
        ("canonical_request_sha256", "bad"),
        ("raw_response_sha256", "bad"),
        ("http_method", "GET"),
        ("target_scheme", "http"),
        ("target_path", "relative"),
        ("collector_service_account", "not-an-account"),
    ],
)
def test_receipt_rejects_invalid_security_fields(field: str, value: object) -> None:
    payload = _receipt_payload(
        request_id="request-primary",
        traversal_role="primary",
        traversal_id="traversal-primary",
        order_by="OrderByNameASC",
        nonce="nonce-primary",
        from_index=0,
    )

    with pytest.raises(ValueError):
        replace(payload, **{field: value})


def test_receipt_rejects_invalid_time_order() -> None:
    payload = _receipt_payload(
        request_id="request-primary",
        traversal_role="primary",
        traversal_id="traversal-primary",
        order_by="OrderByNameASC",
        nonce="nonce-primary",
        from_index=0,
    )

    with pytest.raises(ValueError, match="precede"):
        replace(
            payload,
            response_completed_at_utc=payload.physical_started_at_utc - timedelta(seconds=1),
        )


def test_signed_envelopes_reject_invalid_base64() -> None:
    payload = _receipt_payload(
        request_id="request-primary",
        traversal_role="primary",
        traversal_id="traversal-primary",
        order_by="OrderByNameASC",
        nonce="nonce-primary",
        from_index=0,
    )

    with pytest.raises(ValueError, match="base64"):
        SignedPhysicalReceipt(payload, "%%%")


def test_attestation_requires_distinct_receipts_and_logs() -> None:
    receipts = _receipts()
    with pytest.raises(ValueError, match="repetir receipts"):
        _attestation(
            receipts,
            collector_receipt_digests=(receipts[0].digest, receipts[0].digest),
        )
    with pytest.raises(ValueError, match="repetir transacciones"):
        _attestation(receipts, swp_log_insert_ids=("same", "same"))


def test_attestation_requires_distinct_traversals_and_orderings() -> None:
    receipts = _receipts()
    with pytest.raises(ValueError, match="traversals distintas"):
        _attestation(receipts, reconciliation_traversal_id="traversal-primary")
    with pytest.raises(ValueError, match="order_by distintos"):
        _attestation(receipts, reconciliation_order_by="OrderByNameASC")


def test_attestation_verified_cannot_carry_reasons() -> None:
    receipts = _receipts()

    with pytest.raises(ValueError, match="VERIFIED"):
        _attestation(receipts, reasons=("unexpected",))


def test_attestation_rejected_requires_reason() -> None:
    receipts = _receipts()

    with pytest.raises(ValueError, match="REJECTED"):
        _attestation(receipts, verdict="REJECTED", reasons=())


def test_structural_validation_accepts_consistent_shape_but_never_grants_authority() -> None:
    receipts = _receipts()
    attestation = _attestation(receipts)

    result = validate_provenance_structure(receipts, attestation)

    assert result.consistent is True
    assert result.reasons == ()
    assert result.production_authority is False


def test_structural_validation_rejects_receipt_digest_reordering() -> None:
    receipts = _receipts()
    attestation = _attestation(receipts)

    result = validate_provenance_structure(tuple(reversed(receipts)), attestation)

    assert result.consistent is False
    assert "receipt_digest_sequence_mismatch" in result.reasons
    assert result.production_authority is False


def test_structural_validation_rejects_duplicate_request_and_nonce() -> None:
    receipts = _receipts()
    duplicated_payload = replace(
        receipts[1].payload,
        request_id=receipts[0].payload.request_id,
        nonce=receipts[0].payload.nonce,
    )
    altered = (receipts[0], SignedPhysicalReceipt(duplicated_payload, SIGNATURE))
    attestation = _attestation(altered)

    result = validate_provenance_structure(altered, attestation)

    assert result.consistent is False
    assert "duplicate_request_id" in result.reasons
    assert "duplicate_receipt_nonce" in result.reasons


def test_structural_validation_rejects_mismatched_context() -> None:
    receipts = _receipts()
    altered = (
        receipts[0],
        SignedPhysicalReceipt(
            replace(receipts[1].payload, authorization_id="authorization-other"),
            SIGNATURE,
        ),
    )
    attestation = _attestation(altered)
    attestation = SignedProvenanceAttestation(
        replace(attestation.payload, authorization_id="authorization-001"),
        SIGNATURE,
    )

    result = validate_provenance_structure(altered, attestation)

    assert result.consistent is False
    assert "receipt_authorization_id_mismatch" in result.reasons


def test_structural_validation_rejects_missing_reconciliation_role() -> None:
    receipts = _receipts()
    second = SignedPhysicalReceipt(
        replace(
            receipts[1].payload,
            traversal_role="primary",
            traversal_id="traversal-primary",
            order_by="OrderByNameASC",
        ),
        SIGNATURE,
    )
    altered = (receipts[0], second)
    attestation = _attestation(altered)

    result = validate_provenance_structure(altered, attestation)

    assert result.consistent is False
    assert "reconciliation_receipts_missing" in result.reasons


def test_rejected_attestation_never_becomes_structurally_accepted() -> None:
    receipts = _receipts()
    attestation = _attestation(
        receipts,
        verdict="REJECTED",
        reasons=("proxy_log_missing",),
    )

    result = validate_provenance_structure(receipts, attestation)

    assert result.consistent is False
    assert "attestation_not_verified" in result.reasons
    assert result.production_authority is False
