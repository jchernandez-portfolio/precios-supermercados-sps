#!/usr/bin/env python3
"""Runner futuro, manual y fail-closed para facets context-bound de La Colonia.

El runner sólo puede avanzar después de validar el contexto confiable de GitHub y
una autorización humana presente en ``ACTIVE_AUTHORIZATION_IDS``. En ``main`` esa
allow-list permanece vacía, por lo que hoy siempre termina antes de pedir OIDC,
abrir navegador o contactar cualquier destino externo.

Cuando exista una autorización nueva, el proceso observa ``regionId`` únicamente
en memoria después de seleccionar San Pedro Sula, construye el plan estructural
cerrado y ejecuta exactamente ``root_total`` + ``category_tree`` mediante el
gateway Cloudflare autenticado con OIDC. Ningún valor raw de ubicación se escribe
en disco.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import NoReturn
from urllib.parse import parse_qsl, quote, urlsplit, urlunsplit

import requests

from precios_supermercados.cloudflare_edge_http_transport import CloudflareEdgeHttpTransport
from precios_supermercados.diagnostics.la_colonia_sps_context_diagnostic import (
    ACTIVE_AUTHORIZATION_IDS,
    DiagnosticBudget,
    LogicalRequestCounter,
    TARGET_URL,
    launch_compatible_chromium,
    sanitize_error,
    validate_live_authorization,
)
from precios_supermercados.diagnostics.location_binding_dom_controls import (
    CITY_STATE_SELECTED,
    activate_city_control,
    open_location_selector,
    resolve_exact_city_control,
    verify_structural_city_selection,
)
from precios_supermercados.scrapers.la_colonia_context_bound_facet_entrypoint import (
    FacetEdgeRunContext,
    run_context_bound_facet_entrypoint,
)
from precios_supermercados.scrapers.la_colonia_facet_discovery_runtime import (
    serialize_facet_discovery_summary,
)
from precios_supermercados.scrapers.la_colonia_sps_facet_context import (
    EphemeralSpsRequestContextCollector,
    confirmed_sps_facet_binding,
)
from precios_supermercados.scrapers.la_colonia_sps_structural_plan import (
    build_sps_structural_facet_plan,
)
from precios_supermercados.structural_receipt_crypto import Ed25519StructuralReceiptVerifier

EXPECTED_REPOSITORY = "jchernandez-portfolio/precios-supermercados-sps"
EXPECTED_REF = "refs/heads/main"
EXPECTED_WORKFLOW_REF = (
    "jchernandez-portfolio/precios-supermercados-sps/.github/workflows/"
    "precios-supermercados-sps-la-colonia-live.yml@refs/heads/main"
)
EXPECTED_EVENT = "workflow_dispatch"
OIDC_AUDIENCE = "urn:precios-sps:cloudflare:collector:v1"
SIGNING_KEY_ID = "cloudflare-ed25519-v1"
TARGET_CITY = "San Pedro Sula"
TARGET_CATALOG_URL = "https://www.lacolonia.com/supermercado"
MAX_OIDC_RESPONSE_BYTES = 64 * 1024
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_DIGITS = re.compile(r"[0-9]+\Z")


class FacetLiveEntrypointSafetyError(RuntimeError):
    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


def _fail(code: str, message: str | None = None) -> NoReturn:
    raise FacetLiveEntrypointSafetyError(code, message)


def _required_env(name: str, *, maximum: int = 20_000) -> str:
    value = os.environ.get(name, "")
    if not value or value.strip() != value or len(value) > maximum:
        _fail(f"env_{name.casefold()}_invalid")
    return value


def _trusted_github_context() -> tuple[str, int, str]:
    if os.environ.get("GITHUB_REPOSITORY") != EXPECTED_REPOSITORY:
        _fail("github_repository_not_trusted")
    if os.environ.get("GITHUB_REF") != EXPECTED_REF:
        _fail("github_ref_not_main")
    if os.environ.get("GITHUB_EVENT_NAME") != EXPECTED_EVENT:
        _fail("github_event_not_workflow_dispatch")
    if os.environ.get("GITHUB_WORKFLOW_REF") != EXPECTED_WORKFLOW_REF:
        _fail("github_workflow_ref_not_trusted")

    sha = _required_env("GITHUB_SHA", maximum=40)
    if _SHA1.fullmatch(sha) is None:
        _fail("github_sha_invalid")
    run_id = _required_env("GITHUB_RUN_ID", maximum=32)
    if _DIGITS.fullmatch(run_id) is None:
        _fail("github_run_id_invalid")
    attempt_raw = _required_env("GITHUB_RUN_ATTEMPT", maximum=3)
    if _DIGITS.fullmatch(attempt_raw) is None:
        _fail("github_run_attempt_invalid")
    attempt = int(attempt_raw)
    if not 1 <= attempt <= 100:
        _fail("github_run_attempt_invalid")
    return run_id, attempt, sha


def _validate_human_authorization(authorization_id: str) -> None:
    try:
        validate_live_authorization(
            live=True,
            authorization_id=authorization_id,
            active_ids=ACTIVE_AUTHORIZATION_IDS,
        )
    except Exception as exc:
        raise FacetLiveEntrypointSafetyError("human_live_authorization_rejected") from exc


def _validate_oidc_url(raw_url: str) -> str:
    parsed = urlsplit(raw_url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise FacetLiveEntrypointSafetyError("oidc_request_url_invalid") from exc
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").casefold() != "token.actions.githubusercontent.com"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or not parsed.path.startswith("/")
        or parsed.fragment
    ):
        _fail("oidc_request_url_invalid")
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if any(key.casefold() == "audience" for key, _ in pairs):
        _fail("oidc_request_url_audience_preexisting")
    query = parsed.query + ("&" if parsed.query else "") + f"audience={quote(OIDC_AUDIENCE, safe='-._~')}"
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))


def _read_response_limited(response: requests.Response, *, maximum: int) -> bytes:
    declared = response.headers.get("Content-Length")
    if declared is not None:
        try:
            declared_bytes = int(declared)
        except (TypeError, ValueError) as exc:
            raise FacetLiveEntrypointSafetyError("oidc_content_length_invalid") from exc
        if declared_bytes < 0 or declared_bytes > maximum:
            _fail("oidc_response_above_limit")
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=16 * 1024):
        if not isinstance(chunk, bytes):
            _fail("oidc_response_chunk_invalid")
        if not chunk:
            continue
        total += len(chunk)
        if total > maximum:
            _fail("oidc_response_above_limit")
        chunks.append(chunk)
    if total == 0:
        _fail("oidc_response_empty")
    return b"".join(chunks)


def _request_oidc_token() -> str:
    request_url = _validate_oidc_url(_required_env("ACTIONS_ID_TOKEN_REQUEST_URL"))
    request_token = _required_env("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    session = requests.Session()
    response: requests.Response | None = None
    try:
        try:
            response = session.get(
                request_url,
                headers={"Authorization": f"bearer {request_token}", "Accept": "application/json"},
                timeout=(5.0, 15.0),
                allow_redirects=False,
                stream=True,
            )
        except requests.RequestException as exc:
            raise FacetLiveEntrypointSafetyError("oidc_transport_failed") from exc

        if 300 <= int(response.status_code) <= 399 or response.headers.get("Location"):
            _fail("oidc_redirect_forbidden")
        if int(response.status_code) != 200:
            _fail("oidc_http_status_invalid")
        content_type = str(response.headers.get("Content-Type", "")).casefold()
        if "application/json" not in content_type:
            _fail("oidc_content_type_invalid")
        raw = _read_response_limited(response, maximum=MAX_OIDC_RESPONSE_BYTES)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FacetLiveEntrypointSafetyError("oidc_json_invalid") from exc
        if not isinstance(payload, dict) or set(payload) != {"value"}:
            _fail("oidc_response_shape_invalid")
        token = payload.get("value")
        if (
            not isinstance(token, str)
            or not token
            or token.strip() != token
            or len(token) > 20_000
            or any(character.isspace() for character in token)
        ):
            _fail("oidc_token_invalid")
        return token
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
        # La sesión no se reutiliza para Cloudflare ni La Colonia y se cierra
        # sólo después de consumir/cerrar la respuesta streamed.
        session.close()


def _preflight_edge_configuration() -> tuple[CloudflareEdgeHttpTransport, str]:
    gateway_origin = _required_env("CLOUDFLARE_EDGE_GATEWAY_URL", maximum=2048)
    public_key = _required_env("CLOUDFLARE_EDGE_RECEIPT_PUBLIC_KEY_SPKI_B64URL", maximum=4096)
    # Valida destino y clave antes de cualquier navegación al supermercado.
    transport = CloudflareEdgeHttpTransport(gateway_origin)
    try:
        Ed25519StructuralReceiptVerifier({SIGNING_KEY_ID: public_key})
    except Exception as exc:
        raise FacetLiveEntrypointSafetyError("edge_public_key_invalid") from exc
    return transport, public_key


def _observe_sps_context_and_execute(
    *,
    transport: CloudflareEdgeHttpTransport,
    public_key: str,
    oidc_token: str,
    run_id: str,
    run_attempt: int,
    approved_sha: str,
):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - dependencia fijada en requirements
        raise FacetLiveEntrypointSafetyError("playwright_not_installed") from exc

    binding = confirmed_sps_facet_binding()
    budget = DiagnosticBudget(
        max_logical_requests=4,
        concurrency=1,
        minimum_delay_seconds=1.5,
        max_retries=0,
    )
    counter = LogicalRequestCounter(budget)
    with sync_playwright() as pw:
        browser, _executable = launch_compatible_chromium(pw)
        context = None
        try:
            context = browser.new_context(service_workers="block")
            page = context.new_page()
            collector = EphemeralSpsRequestContextCollector()
            page.on("request", collector.observe_request)

            counter.reserve("open_home")
            page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=20_000)
            page.wait_for_timeout(250)

            counter.reserve("open_location_selector")
            open_location_selector(page)
            city_control = resolve_exact_city_control(page, TARGET_CITY)
            if city_control.state != CITY_STATE_SELECTED:
                counter.reserve("select_city")
                activated = activate_city_control(city_control, TARGET_CITY)
                if activated is not True:
                    _fail("sps_city_selection_not_activated")
                page.wait_for_timeout(500)
            verify_structural_city_selection(page, TARGET_CITY)

            # Descarta cualquier request previo a la confirmación estructural de SPS.
            collector.reset()
            counter.reserve("observe_contextual_catalog_request")
            page.goto(TARGET_CATALOG_URL, wait_until="domcontentloaded", timeout=20_000)
            page.wait_for_timeout(750)
            ephemeral_context = collector.resolve(binding)
            plan = build_sps_structural_facet_plan(ephemeral_context, binding=binding)

            now_ms = int(time.time() * 1000)
            edge_run = FacetEdgeRunContext(
                edge_authorization_id=f"facet-edge-{run_id}-{run_attempt}",
                github_run_id=run_id,
                github_run_attempt=run_attempt,
                approved_commit_sha=approved_sha,
                created_at_ms=now_ms,
                expires_at_ms=now_ms + 10 * 60 * 1000,
            )
            return run_context_bound_facet_entrypoint(
                plan=plan,
                run=edge_run,
                transport=transport,
                bearer_token=oidc_token,
                trusted_public_key_spki_b64url=public_key,
            )
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
            try:
                browser.close()
            except Exception:
                pass


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _failure_status(error: BaseException) -> dict[str, object]:
    code = getattr(error, "code", None)
    return {
        "schema_version": "context-bound-facet-live-status-1",
        "status": "failed",
        "error_code": code if isinstance(code, str) else "unexpected_failure",
        "error": sanitize_error(error),
        "raw_values_exposed": False,
        "production_authority": False,
        "catalog_accepted": False,
        "extraction_enabled": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Entrypoint futuro de facets context-bound bajo SPS")
    parser.add_argument("--authorization-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir: Path = args.output_dir
    try:
        run_id, run_attempt, approved_sha = _trusted_github_context()
        # Gate humano primero: con main actual se detiene aquí, antes de OIDC/red/browser.
        _validate_human_authorization(args.authorization_id)
        transport, public_key = _preflight_edge_configuration()
        oidc_token = _request_oidc_token()
        result = _observe_sps_context_and_execute(
            transport=transport,
            public_key=public_key,
            oidc_token=oidc_token,
            run_id=run_id,
            run_attempt=run_attempt,
            approved_sha=approved_sha,
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "facet-entrypoint-evidence.json").write_bytes(result.artifact_bytes())
        (output_dir / "facet-discovery-summary.json").write_bytes(
            serialize_facet_discovery_summary(result.discovery_summary)
        )
        _write_json(
            output_dir / "facet-entrypoint-status.json",
            {
                "schema_version": "context-bound-facet-live-status-1",
                "status": "completed",
                "requests_attempted": result.discovery_summary.get("requests_attempted"),
                "requests_completed": result.discovery_summary.get("requests_completed"),
                "discovery_outcome": result.discovery_summary.get("discovery_outcome"),
                "raw_values_exposed": False,
                "production_authority": False,
                "catalog_accepted": False,
                "extraction_enabled": False,
            },
        )
        return 0 if result.discovery_summary.get("discovery_completed") is True else 2
    except BaseException as exc:
        try:
            _write_json(output_dir / "facet-entrypoint-status.json", _failure_status(exc))
        except Exception:
            pass
        print(f"FACET_ENTRYPOINT_DENIED: {getattr(exc, 'code', 'unexpected_failure')}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
