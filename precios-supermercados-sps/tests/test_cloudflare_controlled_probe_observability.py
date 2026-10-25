from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from precios_supermercados.cloudflare_controlled_probe_observability import (
    CONTROLLED_PROBE_SERVICE,
    CONTROLLED_PROBE_SPAN_NAME,
    ControlledProbeObservabilityError,
    ControlledProbeObservabilityVerifierClient,
    build_controlled_probe_trace_discovery_query,
    parse_controlled_probe_trace_detail_response,
    reconcile_controlled_probe_trace,
)
from precios_supermercados.cloudflare_controlled_probe_verifier import (
    CONTROLLED_PROBE_ENVIRONMENT,
    CONTROLLED_PROBE_ORIGIN_PATH,
    CONTROLLED_PROBE_PURPOSE,
    CONTROLLED_PROBE_REF,
    CONTROLLED_PROBE_REPOSITORY,
    CONTROLLED_PROBE_REPOSITORY_ID,
    CONTROLLED_PROBE_SCHEMA_VERSION,
    CONTROLLED_PROBE_SIGNATURE_DOMAIN,
    CONTROLLED_PROBE_SIGNING_KEY_ID,
    CONTROLLED_PROBE_SUBJECT,
    CONTROLLED_PROBE_WORKFLOW_REF,
    verify_controlled_probe_artifact,
)
from precios_supermercados.edge_provenance import canonical_json_bytes

SHA = "a" * 40
RUN_ID = "32550000000"
RUN_ATTEMPT = 1
PROBE_ID = f"github-{RUN_ID}-{RUN_ATTEMPT}"
HOST = "precios-sps-controlled-origin.example-account.workers.dev"
ORIGIN_URL = f"https://{HOST}{CONTROLLED_PROBE_ORIGIN_PATH}"
CHALLENGE = "runtime-challenge-001"
VERSION_ID = "cf-probe-version-001"
TRACE_ID = "trace-probe-001"
CUSTOM_SPAN_ID = "span-probe-custom-001"
FETCH_SPAN_ID = "span-probe-fetch-001"
INVOCATION_ID = "probe-invocation-001"
START_MS = 2_000_000_000_000


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _raw_body() -> bytes:
    return json.dumps(
        {"ok": True, "purpose": CONTROLLED_PROBE_PURPOSE, "challenge": CHALLENGE},
        separators=(",", ":"),
    ).encode("utf-8")


def _artifact() -> tuple[dict[str, object], str]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    raw_body = _raw_body()
    request = {
        "challenge": CHALLENGE,
        "method": "GET",
        "probe_id": PROBE_ID,
        "purpose": CONTROLLED_PROBE_PURPOSE,
        "target_host": HOST,
        "target_path": CONTROLLED_PROBE_ORIGIN_PATH,
        "target_scheme": "https",
    }
    receipt: dict[str, object] = {
        "approved_commit_sha": SHA,
        "canonical_request_sha256": hashlib.sha256(canonical_json_bytes(request)).hexdigest(),
        "collector_provider": "cloudflare_workers",
        "collector_principal": "cloudflare-worker:precios-sps-controlled-probe",
        "collector_release_id": VERSION_ID,
        "durable_object_name": f"github-run:{RUN_ID}:{RUN_ATTEMPT}",
        "github_environment": CONTROLLED_PROBE_ENVIRONMENT,
        "github_ref": CONTROLLED_PROBE_REF,
        "github_repository": CONTROLLED_PROBE_REPOSITORY,
        "github_repository_id": CONTROLLED_PROBE_REPOSITORY_ID,
        "github_run_attempt": RUN_ATTEMPT,
        "github_run_id": RUN_ID,
        "github_workflow_ref": CONTROLLED_PROBE_WORKFLOW_REF,
        "oidc_jti": "probe-jti-001",
        "oidc_subject": CONTROLLED_PROBE_SUBJECT,
        "physical_started_at_utc": datetime.fromtimestamp(
            START_MS / 1000, tz=UTC
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "probe_id": PROBE_ID,
        "purpose": CONTROLLED_PROBE_PURPOSE,
        "raw_response_sha256": hashlib.sha256(raw_body).hexdigest(),
        "response_body_bytes": len(raw_body),
        "response_completed_at_utc": datetime.fromtimestamp(
            (START_MS + 700) / 1000, tz=UTC
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "response_status": 200,
        "schema_version": CONTROLLED_PROBE_SCHEMA_VERSION,
        "signing_algorithm": "Ed25519",
        "signing_key_id": CONTROLLED_PROBE_SIGNING_KEY_ID,
        "target_host": HOST,
        "target_path": CONTROLLED_PROBE_ORIGIN_PATH,
        "target_scheme": "https",
    }
    receipt_bytes = canonical_json_bytes(receipt)
    signature = _b64url(
        private_key.sign(CONTROLLED_PROBE_SIGNATURE_DOMAIN + receipt_bytes)
    )
    evidence_id = hashlib.sha256(
        CONTROLLED_PROBE_SIGNATURE_DOMAIN
        + receipt_bytes
        + b"\0"
        + signature.encode("ascii")
    ).hexdigest()
    return (
        {
            "ok": True,
            "decision": "PROBE_COMPLETED",
            "replayed": False,
            "purpose": CONTROLLED_PROBE_PURPOSE,
            "rawBodyB64Url": _b64url(raw_body),
            "receiptPayload": receipt,
            "signatureB64Url": signature,
            "signingKeyId": CONTROLLED_PROBE_SIGNING_KEY_ID,
            "evidenceId": evidence_id,
        },
        _b64url(public_key),
    )


def _standard_source(*, version: str = VERSION_ID) -> dict[str, object]:
    return {
        "cloud.provider": "cloudflare",
        "cloud.platform": "cloudflare.workers",
        "faas.invocation_id": INVOCATION_ID,
        "service.name": CONTROLLED_PROBE_SERVICE,
        "cloudflare.script_version.id": version,
    }


def _workers(*, version: str = VERSION_ID) -> dict[str, object]:
    return {
        "eventType": "rpc",
        "requestId": "probe-worker-request-001",
        "scriptName": CONTROLLED_PROBE_SERVICE,
        "scriptVersion": {"id": version},
        "truncated": False,
    }


def _custom_event(*, version: str = VERSION_ID) -> dict[str, object]:
    source = _standard_source(version=version)
    source.update(
        {
            "precios.probe_contract_version": "1",
            "precios.probe_purpose": CONTROLLED_PROBE_PURPOSE,
            "precios.probe_id": PROBE_ID,
            "precios.approved_commit_sha": SHA,
            "precios.github_run_id": RUN_ID,
            "precios.github_run_attempt": str(RUN_ATTEMPT),
            "precios.target_kind": "controlled_workers_dev_origin",
        }
    )
    return {
        "$metadata": {
            "id": "event-probe-custom",
            "service": CONTROLLED_PROBE_SERVICE,
            "traceId": TRACE_ID,
            "spanId": CUSTOM_SPAN_ID,
            "spanName": CONTROLLED_PROBE_SPAN_NAME,
            "startTime": START_MS - 50,
            "endTime": START_MS + 800,
        },
        "dataset": "cloudflare-workers",
        "source": source,
        "timestamp": START_MS,
        "$workers": _workers(version=version),
    }


def _fetch_event(
    *,
    url: str = ORIGIN_URL,
    version: str = VERSION_ID,
) -> dict[str, object]:
    source = _standard_source(version=version)
    source.update(
        {
            "url.full": url,
            "url.scheme": "https",
            "url.path": CONTROLLED_PROBE_ORIGIN_PATH,
            "http.request.method": "GET",
            "http.response.status_code": 200,
            "http.response.body.size": len(_raw_body()),
        }
    )
    return {
        "$metadata": {
            "id": "event-probe-fetch",
            "service": CONTROLLED_PROBE_SERVICE,
            "traceId": TRACE_ID,
            "spanId": FETCH_SPAN_ID,
            "parentSpanId": CUSTOM_SPAN_ID,
            "spanName": "fetch",
            "origin": "fetch",
            "startTime": START_MS + 50,
            "endTime": START_MS + 600,
        },
        "dataset": "cloudflare-workers",
        "source": source,
        "timestamp": START_MS + 50,
        "$workers": _workers(version=version),
    }


def _response(events: list[dict[str, object]]) -> dict[str, object]:
    return {
        "success": True,
        "errors": [],
        "messages": [],
        "result": {"events": {"events": events, "count": len(events)}},
    }


def _verified():
    artifact, public_key = _artifact()
    verified = verify_controlled_probe_artifact(
        artifact,
        public_key_spki_b64url=public_key,
        expected_commit_sha=SHA,
        expected_run_id=RUN_ID,
        expected_run_attempt=RUN_ATTEMPT,
    )
    return artifact, public_key, verified


def test_discovery_query_filtra_identidad_completa_de_sonda() -> None:
    start = datetime.fromtimestamp((START_MS - 1_000) / 1000, tz=UTC)
    query = build_controlled_probe_trace_discovery_query(
        from_utc=start,
        to_utc=start + timedelta(minutes=1),
        probe_id=PROBE_ID,
        approved_commit_sha=SHA,
        github_run_id=RUN_ID,
        github_run_attempt=RUN_ATTEMPT,
    )
    filters = query["parameters"]["filters"]
    assert {item["key"] for item in filters} == {
        "$metadata.service",
        "$metadata.spanName",
        "precios.probe_contract_version",
        "precios.probe_id",
        "precios.approved_commit_sha",
        "precios.github_run_id",
        "precios.github_run_attempt",
    }


def test_detail_y_reconciliacion_exigen_un_fetch_fisico_exacto() -> None:
    artifact, _public_key, verified = _verified()
    candidates = parse_controlled_probe_trace_detail_response(
        _response([_custom_event(), _fetch_event()]),
        expected_trace_id=TRACE_ID,
    )
    reconciled = reconcile_controlled_probe_trace(verified, artifact, candidates)
    assert reconciled.platform_evidence_reconciled is True
    assert reconciled.production_authority is False
    assert reconciled.catalog_accepted is False
    assert reconciled.trace_evidence.fetch_url == ORIGIN_URL
    assert reconciled.trace_evidence.script_version_id == VERSION_ID
    assert len(reconciled.physical_evidence_id) == 64


def test_detail_rechaza_fetch_duplicado() -> None:
    duplicate = _fetch_event()
    duplicate["$metadata"]["id"] = "event-probe-fetch-2"
    duplicate["$metadata"]["spanId"] = "span-probe-fetch-002"
    with pytest.raises(ControlledProbeObservabilityError) as exc:
        parse_controlled_probe_trace_detail_response(
            _response([_custom_event(), _fetch_event(), duplicate]),
            expected_trace_id=TRACE_ID,
        )
    assert exc.value.code == "probe_origin_fetch_span_not_unique"


def test_detail_rechaza_version_interna_distinta_entre_custom_y_fetch() -> None:
    with pytest.raises(ControlledProbeObservabilityError) as exc:
        parse_controlled_probe_trace_detail_response(
            _response([_custom_event(), _fetch_event(version="cf-probe-other")]),
            expected_trace_id=TRACE_ID,
        )
    assert exc.value.code == "probe_fetch_execution_identity_mismatch"


def test_reconciliacion_rechaza_destino_o_release_distinto_del_receipt() -> None:
    artifact, _public_key, verified = _verified()
    wrong_url = parse_controlled_probe_trace_detail_response(
        _response(
            [
                _custom_event(),
                _fetch_event(
                    url="https://other.example.workers.dev/v1/probe-origin"
                ),
            ]
        ),
        expected_trace_id=TRACE_ID,
    )
    with pytest.raises(ControlledProbeObservabilityError) as url_exc:
        reconcile_controlled_probe_trace(verified, artifact, wrong_url)
    assert url_exc.value.code == "probe_trace_fetch_url_mismatch"

    wrong_version = parse_controlled_probe_trace_detail_response(
        _response(
            [
                _custom_event(version="cf-probe-other"),
                _fetch_event(version="cf-probe-other"),
            ]
        ),
        expected_trace_id=TRACE_ID,
    )
    with pytest.raises(ControlledProbeObservabilityError) as version_exc:
        reconcile_controlled_probe_trace(verified, artifact, wrong_version)
    assert version_exc.value.code == "probe_trace_script_version_mismatch"


def test_client_reverifica_firma_y_consulta_discovery_mas_detail_sin_autoridad() -> None:
    artifact, public_key = _artifact()
    calls: list[tuple[str, str, dict[str, object]]] = []

    class Transport:
        def post_json(self, path, *, bearer_token, payload):
            calls.append((path, bearer_token, dict(payload)))
            if payload["queryId"].endswith("discovery-v1"):
                return _response([_custom_event()])
            return _response([_custom_event(), _fetch_event()])

    client = ControlledProbeObservabilityVerifierClient("a" * 32, Transport())
    result = client.reconcile_artifact(
        artifact,
        public_key_spki_b64url=public_key,
        expected_commit_sha=SHA,
        expected_run_id=RUN_ID,
        expected_run_attempt=RUN_ATTEMPT,
        bearer_token="observability-token-test",
    )
    assert len(calls) == 2
    assert all(
        call[0]
        == "/accounts/" + "a" * 32 + "/workers/observability/telemetry/query"
        for call in calls
    )
    assert result.production_authority is False
    assert result.catalog_accepted is False


def test_client_no_confia_en_filtros_del_servidor() -> None:
    artifact, public_key = _artifact()
    forged = _custom_event()
    forged["source"]["precios.approved_commit_sha"] = "b" * 40

    class Transport:
        def post_json(self, path, *, bearer_token, payload):
            del path, bearer_token, payload
            return _response([forged])

    client = ControlledProbeObservabilityVerifierClient("a" * 32, Transport())
    with pytest.raises(ControlledProbeObservabilityError) as exc:
        client.reconcile_artifact(
            artifact,
            public_key_spki_b64url=public_key,
            expected_commit_sha=SHA,
            expected_run_id=RUN_ID,
            expected_run_attempt=RUN_ATTEMPT,
            bearer_token="observability-token-test",
        )
    assert exc.value.code == "probe_discovery_commit_mismatch"
