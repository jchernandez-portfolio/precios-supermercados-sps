from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import pytest

from precios_supermercados.cloudflare_structural_trace_evidence import (
    PlatformReconciledStructuralObservation,
)
from precios_supermercados.edge_structural_observation import (
    CryptographicallyVerifiedStructuralObservation,
)
from precios_supermercados.scrapers.la_colonia_sps_structural_plan import (
    build_sps_structural_facet_plan,
)
from precios_supermercados.sps_context_bound_discovery import (
    SpsContextBoundDiscoveryError,
    VerifiedSpsStructuralContext,
    bind_verified_structural_discovery_to_sps,
)
from precios_supermercados.structural_discovery_manifest import (
    build_verified_structural_discovery,
)
from precios_supermercados.structural_provenance import SignedStructuralReceipt
from precios_supermercados.structural_receipt_crypto import VerifiedStructuralReceipt

TESTS = Path(__file__).parent


def _helper(filename: str, module_name: str) -> ModuleType:
    path = TESTS / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


MANIFEST_HELPER = _helper(
    "test_structural_discovery_manifest.py",
    "precios_sps_manifest_test_helper_for_context_binding",
)
PLAN_HELPER = _helper(
    "test_la_colonia_sps_structural_plan.py",
    "precios_sps_plan_test_helper_for_context_binding",
)


def _plan():
    binding, context = PLAN_HELPER.context_for(
        PLAN_HELPER.FakeRequest(headers={"X-VTEX-Region": PLAN_HELPER.RAW_REGION})
    )
    return binding, build_sps_structural_facet_plan(context, binding=binding)


def _contextual_platform(kind: str, plan) -> PlatformReconciledStructuralObservation:
    base = MANIFEST_HELPER._platform(kind)
    observation = base.observation
    verified = observation.verified_receipt
    payload = verified.receipt.payload
    request_index = 0 if kind == "root_total" else 1
    contextual_payload = replace(
        payload,
        schema_version="2",
        location_id=plan.location_id,
        binding_source_key=plan.binding_source_key,
        binding_evidence=plan.binding_evidence,
        context_fingerprint=plan.context_fingerprint,
        context_placement=plan.placement.value,
        context_wire_key=plan.wire_key,
        context_value_path=plan.value_path,
        wire_request_fingerprint=plan.requests[request_index].wire_request_fingerprint,
    )
    signed = SignedStructuralReceipt(
        payload=contextual_payload,
        signature_b64url=verified.receipt.signature_b64url,
    )
    contextual_verified = VerifiedStructuralReceipt(
        receipt=signed,
        signing_key_id=verified.signing_key_id,
        public_key_spki_sha256=verified.public_key_spki_sha256,
        receipt_digest=signed.digest,
    )
    contextual_observation = CryptographicallyVerifiedStructuralObservation(
        request=observation.request,
        body=observation.body,
        verified_receipt=contextual_verified,
        raw_body_sha256=observation.raw_body_sha256,
    )
    return PlatformReconciledStructuralObservation(
        observation=contextual_observation,
        trace_evidence=base.trace_evidence,
    )


def _fixture():
    binding, plan = _plan()
    root = _contextual_platform("root_total", plan)
    tree = _contextual_platform("category_tree", plan)
    discovery = build_verified_structural_discovery(
        root_total=root,
        category_tree=tree,
    )
    observations = {
        "root_total": root.observation,
        "category_tree": tree.observation,
    }
    return binding, plan, discovery, observations


def test_religa_manifest_a_receipts_contextuales_y_plan_sps() -> None:
    binding, plan, discovery, observations = _fixture()

    result = bind_verified_structural_discovery_to_sps(
        discovery,
        observations,
        plan,
        binding=binding,
    )

    assert isinstance(result, VerifiedSpsStructuralContext)
    assert result.discovery is discovery
    assert result.location_id == "la_colonia_sps"
    assert result.binding_source_key == plan.binding_source_key
    assert result.binding_evidence == plan.binding_evidence
    assert result.context_fingerprint == plan.context_fingerprint
    assert result.context_placement is plan.placement
    assert result.context_wire_key == plan.wire_key
    assert result.context_value_path == ()
    assert result.root_wire_request_fingerprint == plan.requests[0].wire_request_fingerprint
    assert (
        result.category_tree_wire_request_fingerprint
        == plan.requests[1].wire_request_fingerprint
    )
    assert result.plan_digest == plan.digest
    assert result.discovery_digest == discovery.digest
    assert result.production_authority is False
    assert result.catalog_accepted is False
    assert result.extraction_enabled is False

    rendered = json.dumps(result.public_dict(), ensure_ascii=False, sort_keys=True)
    assert result.public_dict()["raw_values_exposed"] is False
    assert PLAN_HELPER.RAW_REGION not in rendered
    assert PLAN_HELPER.RAW_REGION not in repr(result)


def test_receipt_legacy_sin_contexto_no_puede_promover_discovery_a_sps() -> None:
    binding, plan = _plan()
    root = MANIFEST_HELPER._platform("root_total")
    tree = MANIFEST_HELPER._platform("category_tree")
    discovery = build_verified_structural_discovery(
        root_total=root,
        category_tree=tree,
    )

    with pytest.raises(SpsContextBoundDiscoveryError) as captured:
        bind_verified_structural_discovery_to_sps(
            discovery,
            {
                "root_total": root.observation,
                "category_tree": tree.observation,
            },
            plan,
            binding=binding,
        )

    assert captured.value.code == "root_total_location_context_downgrade"


def test_wire_fingerprint_contextual_debe_corresponder_al_request_del_plan() -> None:
    binding, plan, discovery, observations = _fixture()
    root = observations["root_total"]
    verified = root.verified_receipt
    payload = replace(
        verified.receipt.payload,
        wire_request_fingerprint="0" * 64,
    )
    signed = SignedStructuralReceipt(
        payload=payload,
        signature_b64url=verified.receipt.signature_b64url,
    )
    changed_verified = VerifiedStructuralReceipt(
        receipt=signed,
        signing_key_id=verified.signing_key_id,
        public_key_spki_sha256=verified.public_key_spki_sha256,
        receipt_digest=signed.digest,
    )
    changed_root = CryptographicallyVerifiedStructuralObservation(
        request=root.request,
        body=root.body,
        verified_receipt=changed_verified,
        raw_body_sha256=root.raw_body_sha256,
    )

    with pytest.raises(SpsContextBoundDiscoveryError) as captured:
        bind_verified_structural_discovery_to_sps(
            discovery,
            {**observations, "root_total": changed_root},
            plan,
            binding=binding,
        )

    # Primero debe detectar que ya no es el receipt exacto que formó el manifest.
    assert captured.value.code == "root_total_receipt_digest_mismatch"


def test_manifest_y_observaciones_no_se_pueden_mezclar_entre_ejecuciones() -> None:
    binding, plan, discovery, observations = _fixture()
    foreign = MANIFEST_HELPER._platform(
        "root_total",
        request_id="request-foreign",
    ).observation

    with pytest.raises(SpsContextBoundDiscoveryError) as captured:
        bind_verified_structural_discovery_to_sps(
            discovery,
            {**observations, "root_total": foreign},
            plan,
            binding=binding,
        )

    assert captured.value.code == "root_total_request_id_mismatch"
