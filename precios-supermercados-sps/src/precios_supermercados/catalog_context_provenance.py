"""Receipt de catálogo v3 que extiende el contrato edge v2 con contexto SPS.

El envelope criptográfico existente sigue firmando bytes canónicos del payload;
``schema_version=3`` hace que un receipt contextual no sea intercambiable con un
payload v2 aunque comparta la misma familia de dominio de firma. La representación
v3 nunca contiene el ``regionId`` raw: sólo binding, evidencia y fingerprints.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import fields
from datetime import datetime
from typing import NoReturn

from precios_supermercados.edge_provenance import EdgeReceiptPayload, canonical_json_bytes

CATALOG_CONTEXT_RECEIPT_SCHEMA_VERSION = "3"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SOURCE = re.compile(r"request:regionid:sha256:(?P<digest>[0-9a-f]{64})\Z")
_EVIDENCE = re.compile(r"location_binding_radiography:sha256:[0-9a-f]{64}\Z")
_REGION_KEY = re.compile(r"[^a-z0-9]")
_ALLOWED_REGION_KEYS = {"region", "regionid", "xvtexregion"}
_CONTEXT_FIELDS = {
    "location_id",
    "binding_source_key",
    "binding_evidence",
    "context_fingerprint",
    "context_placement",
    "context_wire_key",
    "context_value_path",
    "wire_request_fingerprint",
}


class CatalogContextProvenanceError(ValueError):
    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


def _fail(code: str, message: str | None = None) -> NoReturn:
    raise CatalogContextProvenanceError(code, message)


def _text(value: object, code: str, *, maximum: int = 512) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or len(value) > maximum
    ):
        _fail(code)
    return value


def _sha256(value: object, code: str) -> str:
    text = _text(value, code, maximum=64)
    if not _SHA256.fullmatch(text):
        _fail(code)
    return text


def _canonical_region_key(value: str) -> str:
    return _REGION_KEY.sub("", value.casefold())


def _timestamp(value: object, code: str) -> datetime:
    text = _text(value, code, maximum=64)
    if not text.endswith("Z"):
        _fail(code)
    try:
        parsed = datetime.fromisoformat(f"{text[:-1]}+00:00")
    except ValueError as exc:
        raise CatalogContextProvenanceError(code) from exc
    if parsed.tzinfo is None:
        _fail(code)
    return parsed


class ContextBoundEdgeReceiptPayload:
    """Vista plana v3 sobre un ``EdgeReceiptPayload`` v2 ya validado."""

    __slots__ = (
        "base",
        "location_id",
        "binding_source_key",
        "binding_evidence",
        "context_fingerprint",
        "context_placement",
        "context_wire_key",
        "context_value_path",
        "wire_request_fingerprint",
    )

    def __init__(
        self,
        *,
        base: EdgeReceiptPayload,
        location_id: str,
        binding_source_key: str,
        binding_evidence: str,
        context_fingerprint: str,
        context_placement: str,
        context_wire_key: str,
        context_value_path: tuple[str, ...],
        wire_request_fingerprint: str,
    ) -> None:
        if not isinstance(base, EdgeReceiptPayload):
            _fail("catalog_context_base_receipt_invalid")
        if base.schema_version != "2":
            _fail("catalog_context_base_schema_invalid")
        if location_id != "la_colonia_sps":
            _fail("catalog_context_location_id_invalid")
        source = _text(
            binding_source_key,
            "catalog_context_binding_source_key_invalid",
        )
        match = _SOURCE.fullmatch(source)
        if match is None:
            _fail("catalog_context_binding_source_key_invalid")
        evidence = _text(
            binding_evidence,
            "catalog_context_binding_evidence_invalid",
        )
        if _EVIDENCE.fullmatch(evidence) is None:
            _fail("catalog_context_binding_evidence_invalid")
        fingerprint = _sha256(
            context_fingerprint,
            "catalog_context_fingerprint_invalid",
        )
        if match.group("digest") != fingerprint:
            _fail("catalog_context_binding_fingerprint_mismatch")
        placement = _text(
            context_placement,
            "catalog_context_placement_invalid",
            maximum=16,
        )
        if placement not in {"query", "header"}:
            _fail("catalog_context_placement_invalid")
        wire_key = _text(
            context_wire_key,
            "catalog_context_wire_key_invalid",
            maximum=160,
        )
        if _canonical_region_key(wire_key) not in _ALLOWED_REGION_KEYS:
            _fail("catalog_context_wire_key_invalid")
        if not isinstance(context_value_path, tuple) or context_value_path != ():
            _fail("catalog_context_value_path_invalid")
        wire_fingerprint = _sha256(
            wire_request_fingerprint,
            "catalog_context_wire_request_fingerprint_invalid",
        )

        self.base = base
        self.location_id = location_id
        self.binding_source_key = source
        self.binding_evidence = evidence
        self.context_fingerprint = fingerprint
        self.context_placement = placement
        self.context_wire_key = wire_key
        self.context_value_path = context_value_path
        self.wire_request_fingerprint = wire_fingerprint

    def __getattr__(self, name: str):
        return getattr(self.base, name)

    @property
    def schema_version(self) -> str:
        return CATALOG_CONTEXT_RECEIPT_SCHEMA_VERSION

    @property
    def location_context_bound(self) -> bool:
        return True

    def canonical_dict(self) -> dict[str, object]:
        value = self.base.canonical_dict()
        value["schema_version"] = CATALOG_CONTEXT_RECEIPT_SCHEMA_VERSION
        value.update(
            {
                "binding_evidence": self.binding_evidence,
                "binding_source_key": self.binding_source_key,
                "context_fingerprint": self.context_fingerprint,
                "context_placement": self.context_placement,
                "context_value_path": list(self.context_value_path),
                "context_wire_key": self.context_wire_key,
                "location_id": self.location_id,
                "wire_request_fingerprint": self.wire_request_fingerprint,
            }
        )
        return value

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.canonical_dict())


def context_bound_edge_receipt_payload_from_mapping(
    value: object,
) -> ContextBoundEdgeReceiptPayload:
    """Parsea un payload plano v3 y exige round-trip canónico exacto."""

    if not isinstance(value, Mapping):
        _fail("catalog_context_receipt_shape_invalid")
    base_names = {item.name for item in fields(EdgeReceiptPayload)}
    expected = base_names | _CONTEXT_FIELDS
    if set(value) != expected or value.get("schema_version") != CATALOG_CONTEXT_RECEIPT_SCHEMA_VERSION:
        _fail("catalog_context_receipt_shape_invalid")

    base_data = {name: value[name] for name in base_names}
    base_data["schema_version"] = "2"
    base_data["physical_started_at_utc"] = _timestamp(
        base_data["physical_started_at_utc"],
        "catalog_context_physical_started_at_invalid",
    )
    base_data["response_completed_at_utc"] = _timestamp(
        base_data["response_completed_at_utc"],
        "catalog_context_response_completed_at_invalid",
    )
    try:
        base = EdgeReceiptPayload(**base_data)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise CatalogContextProvenanceError(
            "catalog_context_base_receipt_invalid"
        ) from exc

    path = value["context_value_path"]
    if not isinstance(path, list) or any(not isinstance(item, str) for item in path):
        _fail("catalog_context_value_path_invalid")
    payload = ContextBoundEdgeReceiptPayload(
        base=base,
        location_id=value["location_id"],  # type: ignore[arg-type]
        binding_source_key=value["binding_source_key"],  # type: ignore[arg-type]
        binding_evidence=value["binding_evidence"],  # type: ignore[arg-type]
        context_fingerprint=value["context_fingerprint"],  # type: ignore[arg-type]
        context_placement=value["context_placement"],  # type: ignore[arg-type]
        context_wire_key=value["context_wire_key"],  # type: ignore[arg-type]
        context_value_path=tuple(path),
        wire_request_fingerprint=value["wire_request_fingerprint"],  # type: ignore[arg-type]
    )
    if payload.canonical_dict() != dict(value):
        _fail("catalog_context_receipt_noncanonical")
    return payload
