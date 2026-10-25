from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from precios_supermercados.catalog_context_provenance import (
    CATALOG_CONTEXT_RECEIPT_SCHEMA_VERSION,
    CatalogContextProvenanceError,
    ContextBoundEdgeReceiptPayload,
    context_bound_edge_receipt_payload_from_mapping,
)
from precios_supermercados.edge_provenance import SignedEdgeReceipt

TESTS = Path(__file__).parent


def _helper(filename: str, module_name: str) -> ModuleType:
    path = TESTS / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


EDGE_HELPER = _helper(
    "test_edge_provenance.py",
    "precios_sps_edge_provenance_helper_for_catalog_context",
)
CONTEXT_HELPER = _helper(
    "test_catalog_location_context.py",
    "precios_sps_catalog_location_context_helper_for_receipt",
)


def _payload() -> ContextBoundEdgeReceiptPayload:
    proof = CONTEXT_HELPER._proof_for(
        CONTEXT_HELPER.PLAN_HELPER.FakeRequest(
            headers={
                "X-VTEX-Region": CONTEXT_HELPER.PLAN_HELPER.RAW_REGION,
            }
        )
    )
    request = CONTEXT_HELPER._catalog_request()
    location = CONTEXT_HELPER.catalog_edge_location_context_for_request(proof, request)
    base = EDGE_HELPER._receipt_payload(
        request_id="request-context-1",
        reservation_id="reservation-context-1",
        nonce="nonce-context-1",
        traversal_role="primary",
        traversal_id="primary-context",
        order_by=request.order_by,
        from_index=request.from_index,
    )
    # El helper histórico usa digests ficticios; aquí sólo probamos el contrato
    # contextual y no la reconciliación del request completo.
    return ContextBoundEdgeReceiptPayload(
        base=base,
        location_id=location.location_id,
        binding_source_key=location.binding_source_key,
        binding_evidence=location.binding_evidence,
        context_fingerprint=location.context_fingerprint,
        context_placement=location.placement.value,
        context_wire_key=location.wire_key,
        context_value_path=location.value_path,
        wire_request_fingerprint=location.wire_request_fingerprint,
    )


def test_v3_extiende_payload_v2_sin_region_raw() -> None:
    payload = _payload()

    assert payload.schema_version == CATALOG_CONTEXT_RECEIPT_SCHEMA_VERSION
    assert payload.location_context_bound is True
    assert payload.location_id == "la_colonia_sps"
    assert payload.base.schema_version == "2"
    assert payload.run_id == payload.base.run_id
    canonical = payload.canonical_dict()
    assert canonical["schema_version"] == "3"
    assert canonical["location_id"] == "la_colonia_sps"
    assert canonical["context_value_path"] == []
    assert "rawValue" not in canonical
    assert "raw_value" not in canonical
    assert CONTEXT_HELPER.PLAN_HELPER.RAW_REGION not in str(canonical)


def test_round_trip_mapping_v3_es_canónico() -> None:
    payload = _payload()
    parsed = context_bound_edge_receipt_payload_from_mapping(payload.canonical_dict())

    assert parsed.canonical_dict() == payload.canonical_dict()
    assert parsed.canonical_bytes() == payload.canonical_bytes()
    assert parsed.location_context_bound is True
    assert parsed.base.schema_version == "2"


def test_signed_edge_receipt_existente_liga_payload_contextual_en_digest() -> None:
    payload = _payload()
    signed = SignedEdgeReceipt(
        payload=payload,  # type: ignore[arg-type]
        signature_b64url=EDGE_HELPER.SIGNATURE,
    )
    changed = ContextBoundEdgeReceiptPayload(
        base=payload.base,
        location_id=payload.location_id,
        binding_source_key=payload.binding_source_key,
        binding_evidence=payload.binding_evidence,
        context_fingerprint=payload.context_fingerprint,
        context_placement=payload.context_placement,
        context_wire_key="regionId",
        context_value_path=payload.context_value_path,
        wire_request_fingerprint=payload.wire_request_fingerprint,
    )
    signed_changed = SignedEdgeReceipt(
        payload=changed,  # type: ignore[arg-type]
        signature_b64url=EDGE_HELPER.SIGNATURE,
    )

    assert signed.digest != signed_changed.digest


def test_no_acepta_downgrade_ni_contexto_incompleto() -> None:
    payload = _payload().canonical_dict()
    legacy = dict(payload)
    legacy["schema_version"] = "2"
    with pytest.raises(CatalogContextProvenanceError) as downgrade:
        context_bound_edge_receipt_payload_from_mapping(legacy)
    assert downgrade.value.code == "catalog_context_receipt_shape_invalid"

    incomplete = dict(payload)
    incomplete.pop("wire_request_fingerprint")
    with pytest.raises(CatalogContextProvenanceError) as missing:
        context_bound_edge_receipt_payload_from_mapping(incomplete)
    assert missing.value.code == "catalog_context_receipt_shape_invalid"


def test_binding_source_debe_corresponder_al_fingerprint() -> None:
    payload = _payload()
    with pytest.raises(CatalogContextProvenanceError) as captured:
        ContextBoundEdgeReceiptPayload(
            base=payload.base,
            location_id=payload.location_id,
            binding_source_key="request:regionid:sha256:" + "0" * 64,
            binding_evidence=payload.binding_evidence,
            context_fingerprint=payload.context_fingerprint,
            context_placement=payload.context_placement,
            context_wire_key=payload.context_wire_key,
            context_value_path=payload.context_value_path,
            wire_request_fingerprint=payload.wire_request_fingerprint,
        )
    assert captured.value.code == "catalog_context_binding_fingerprint_mismatch"
