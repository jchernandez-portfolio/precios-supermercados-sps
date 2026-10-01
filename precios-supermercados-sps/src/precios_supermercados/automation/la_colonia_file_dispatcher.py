"""Valida solicitudes live cerradas de La Colonia desde un archivo no confiable."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

EXPECTED_OWNER = "jchernandez-portfolio"
EXPECTED_REPOSITORY = "jchernandez-portfolio/precios-supermercados-sps"
COMMAND_PATH = "precios-supermercados-sps/.automation/la-colonia-live-command.json"
LIVE_WORKFLOW = ".github/workflows/precios-supermercados-sps-la-colonia-live.yml"
DIAGNOSTIC_WORKFLOW = ".github/workflows/precios-supermercados-sps-la-colonia-diagnostic.yml"
FACET_DISCOVERY_WORKFLOW = ".github/workflows/precios-supermercados-sps-la-colonia-facet-discovery.yml"
# Compatibilidad pública con las pruebas y consumidores anteriores.
ALLOWED_WORKFLOWS = frozenset({LIVE_WORKFLOW, DIAGNOSTIC_WORKFLOW})
# Allow-list efectiva del controlador confiable.
TRUSTED_WORKFLOWS = frozenset(
    {LIVE_WORKFLOW, DIAGNOSTIC_WORKFLOW, FACET_DISCOVERY_WORKFLOW}
)
DIAGNOSTIC_PLAN = "frontier_380_399_v1"
FACET_DISCOVERY_PLAN = "catalog_categories_v1"
FACET_DISCOVERY_REQUEST_ID = "la-colonia-facet-discovery-001"
ALLOWED_PAGE_SIZES = {10, 20, 30, 50}
NORMAL_COMMAND_FIELDS = frozenset(
    {
        "request_id",
        "supermarket",
        "mode",
        "page_size",
        "max_pages",
        "max_products",
        "delay_seconds",
        "profile",
        "thresholds",
        "allow_full",
    }
)
DIAGNOSTIC_COMMAND_FIELDS = frozenset(
    {
        "request_id",
        "supermarket",
        "mode",
        "diagnostic_plan",
        "delay_seconds",
        "allow_full",
    }
)
FACET_DISCOVERY_COMMAND_FIELDS = frozenset(
    {
        "request_id",
        "supermarket",
        "mode",
        "discovery_plan",
        "delay_seconds",
        "allow_full",
    }
)
THRESHOLD_KEYS = (
    "max_missing_price_ratio",
    "max_duplicate_sku_ratio",
    "max_duplicate_product_ratio",
    "max_total_change_ratio",
)
REQUEST_ID_RE = re.compile(r"[a-z0-9](?:[a-z0-9._-]{0,78}[a-z0-9])?\Z")
HEAD_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,254}\Z")


@dataclass(frozen=True)
class DispatchDecision:
    accepted: bool
    should_comment: bool
    reason: str
    pr_number: int | None = None
    request_id: str | None = None
    mode: str | None = None
    ref: str | None = None
    head_sha: str | None = None
    workflow: str | None = None
    inputs: dict[str, Any] | None = None

    def as_dict(self, *, comment: str) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "should_comment": self.should_comment,
            "reason": self.reason,
            "pr_number": self.pr_number,
            "request_id": self.request_id,
            "mode": self.mode,
            "ref": self.ref,
            "head_sha": self.head_sha,
            "inputs": self.inputs,
            "workflow": self.workflow,
            "comment": comment,
        }


def _rejected(
    reason: str,
    *,
    should_comment: bool = True,
    pr_number: int | None = None,
) -> DispatchDecision:
    return DispatchDecision(False, should_comment, reason, pr_number=pr_number)


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        parsed = Decimal(str(value))
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def _decimal_text(value: Any) -> str:
    parsed = _decimal(value)
    if parsed is None:
        raise ValueError("decimal inválido")
    text = format(parsed.normalize(), "f")
    return "0" if text == "-0" else text


def _valid_ref(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(REF_RE.fullmatch(value))
        and ".." not in value
        and "//" not in value
        and "@{" not in value
        and not value.endswith(("/", ".", ".lock"))
    )


def _base_inputs(*, mode: str, page_size: int, profile: str) -> dict[str, Any]:
    return {
        "mode": mode,
        "page_size": str(page_size),
        "max_pages": "0",
        "max_products": "0",
        "delay_seconds": "1.5",
        "profile": profile,
        "max_missing_price_ratio": "",
        "max_duplicate_sku_ratio": "",
        "max_duplicate_product_ratio": "",
        "max_total_change_ratio": "",
        "allow_full": False,
    }


def _validate_context(context: Mapping[str, Any]) -> DispatchDecision | None:
    pr_number = context.get("pr_number")
    safe_pr_number = pr_number if _is_integer(pr_number) and pr_number > 0 else None

    if context.get("repository_owner") != EXPECTED_OWNER:
        return _rejected("El propietario del repositorio no está autorizado.", pr_number=safe_pr_number)
    if context.get("repository_full_name") != EXPECTED_REPOSITORY:
        return _rejected("El repositorio no está autorizado.", pr_number=safe_pr_number)
    if context.get("state") != "open":
        return _rejected("El Pull Request debe estar abierto.", pr_number=safe_pr_number)
    if context.get("base_repo_full_name") != EXPECTED_REPOSITORY:
        return _rejected("El repositorio base no está autorizado.", pr_number=safe_pr_number)
    if context.get("head_repo_full_name") != EXPECTED_REPOSITORY or bool(context.get("head_repo_fork")):
        return _rejected("Los Pull Requests provenientes de forks no están autorizados.", pr_number=safe_pr_number)
    if safe_pr_number is None:
        return _rejected("El número del Pull Request no es válido.")

    head_ref = context.get("head_ref")
    if not _valid_ref(head_ref):
        return _rejected("La rama head no es válida.", pr_number=safe_pr_number)
    head_sha = context.get("head_sha")
    if not isinstance(head_sha, str) or not HEAD_SHA_RE.fullmatch(head_sha):
        return _rejected("El SHA head no es válido.", pr_number=safe_pr_number)

    if context.get("command_file_status") == "superseded":
        return _rejected(
            "El evento fue reemplazado por un commit posterior.",
            should_comment=False,
            pr_number=safe_pr_number,
        )
    if context.get("command_file_changed") is not True:
        return _rejected(
            "El último commit no modificó el archivo de comando.",
            should_comment=False,
            pr_number=safe_pr_number,
        )

    status = context.get("command_file_status")
    if status == "missing":
        return _rejected("El archivo de comando no existe en el SHA head.", pr_number=safe_pr_number)
    if status == "too_large":
        return _rejected("El archivo de comando excede el tamaño permitido.", pr_number=safe_pr_number)
    if status == "invalid_type":
        return _rejected("La ruta del comando no contiene un archivo normal.", pr_number=safe_pr_number)
    if status == "invalid_encoding":
        return _rejected("El archivo de comando no usa UTF-8 válido.", pr_number=safe_pr_number)
    if status != "ok":
        return _rejected("No fue posible verificar el archivo de comando.", pr_number=safe_pr_number)
    return None


def _parse_command(raw_command: str | None) -> tuple[dict[str, Any] | None, str | None]:
    if raw_command is None:
        return None, "El archivo de comando no pudo leerse."
    if len(raw_command.encode("utf-8")) > 16_384:
        return None, "El archivo de comando excede el tamaño permitido."
    try:
        command = json.loads(raw_command)
    except (json.JSONDecodeError, UnicodeError):
        return None, "El archivo de comando no contiene JSON válido."
    if not isinstance(command, dict):
        return None, "El JSON de comando debe ser un objeto."

    mode = command.get("mode")
    if mode == "diagnostic_overlap":
        expected_fields = DIAGNOSTIC_COMMAND_FIELDS
    elif mode == "facet_discovery":
        expected_fields = FACET_DISCOVERY_COMMAND_FIELDS
    else:
        expected_fields = NORMAL_COMMAND_FIELDS
    unknown = sorted(set(command) - expected_fields)
    if unknown:
        return None, "El JSON contiene un campo desconocido."
    missing = sorted(expected_fields - set(command))
    if missing:
        return None, "El JSON no contiene todos los campos obligatorios."
    return command, None


def _validate_thresholds(value: Any) -> tuple[dict[str, str] | None, str | None]:
    if not isinstance(value, Mapping):
        return None, "Validation exige los cuatro umbrales."
    if set(value) != set(THRESHOLD_KEYS):
        return None, "Validation exige exactamente los cuatro umbrales."

    normalized: dict[str, str] = {}
    for key in THRESHOLD_KEYS:
        parsed = _decimal(value[key])
        if parsed is None or not Decimal("0") <= parsed <= Decimal("1"):
            return None, "Todos los umbrales deben estar entre 0 y 1."
        normalized[key] = _decimal_text(value[key])
    return normalized, None


def _normalize_command(
    command: Mapping[str, Any],
) -> tuple[str | None, str | None, str | None, dict[str, Any] | None, str | None]:
    request_id = command.get("request_id")
    if not isinstance(request_id, str) or not REQUEST_ID_RE.fullmatch(request_id):
        return None, None, None, None, "request_id no cumple el formato permitido."
    if command.get("supermarket") != "la_colonia":
        return request_id, None, None, None, "El supermercado solicitado no está autorizado."

    mode = command.get("mode")
    delay = _decimal(command.get("delay_seconds"))
    if delay != Decimal("1.5"):
        return request_id, str(mode), None, None, "delay_seconds debe ser exactamente 1.5."
    if command.get("allow_full") is not False:
        return request_id, str(mode), None, None, "allow_full debe ser false."

    if mode == "diagnostic_overlap":
        if command.get("diagnostic_plan") != DIAGNOSTIC_PLAN:
            return request_id, mode, None, None, "El plan diagnóstico no está autorizado."
        return (
            request_id,
            mode,
            DIAGNOSTIC_WORKFLOW,
            {
                "request_id": request_id,
                "diagnostic_plan": DIAGNOSTIC_PLAN,
                "delay_seconds": "1.5",
            },
            None,
        )

    if mode == "facet_discovery":
        if request_id != FACET_DISCOVERY_REQUEST_ID:
            return request_id, mode, None, None, "request_id de facet discovery no autorizado."
        if command.get("discovery_plan") != FACET_DISCOVERY_PLAN:
            return request_id, mode, None, None, "El plan de facet discovery no está autorizado."
        return (
            request_id,
            mode,
            FACET_DISCOVERY_WORKFLOW,
            {
                "request_id": FACET_DISCOVERY_REQUEST_ID,
                "discovery_plan": FACET_DISCOVERY_PLAN,
                "delay_seconds": "1.5",
            },
            None,
        )

    if mode == "full":
        return request_id, mode, None, None, "El modo full está prohibido."
    if mode not in {"smoke", "staged"}:
        return request_id, str(mode), None, None, "El modo debe ser smoke o staged."

    page_size = command.get("page_size")
    if not _is_integer(page_size) or page_size not in ALLOWED_PAGE_SIZES:
        return request_id, mode, None, None, "page_size debe ser 10, 20, 30 o 50."

    max_pages = command.get("max_pages")
    max_products = command.get("max_products")
    if not _is_integer(max_pages) or not _is_integer(max_products):
        return request_id, mode, None, None, "max_pages y max_products deben ser enteros."

    profile = command.get("profile")
    thresholds = command.get("thresholds")

    if mode == "smoke":
        if (
            max_pages != 2
            or max_products != 0
            or profile != "baseline"
            or thresholds is not None
        ):
            return request_id, mode, None, None, "Smoke exige los valores fijos autorizados."
        inputs = _base_inputs(mode="smoke", page_size=page_size, profile="baseline")
        inputs["max_pages"] = "2"
        return request_id, mode, LIVE_WORKFLOW, inputs, None

    if profile not in {"baseline", "validation"}:
        return request_id, mode, None, None, "Staged requiere profile baseline o validation."

    has_pages = max_pages != 0
    has_products = max_products != 0
    if has_pages == has_products:
        return request_id, mode, None, None, "Staged requiere exactamente un límite activo."

    inputs = _base_inputs(mode="staged", page_size=page_size, profile=profile)
    if has_pages:
        if not 1 <= max_pages <= 10 or max_products != 0:
            return request_id, mode, None, None, "max_pages debe estar entre 1 y 10."
        inputs["max_pages"] = str(max_pages)
    else:
        if not 100 <= max_products <= 500 or max_pages != 0:
            return request_id, mode, None, None, "max_products debe estar entre 100 y 500."
        if max_products % page_size != 0:
            return request_id, mode, None, None, "max_products debe ser múltiplo de page_size."
        inputs["max_products"] = str(max_products)

    if profile == "baseline":
        if thresholds is not None:
            return request_id, mode, None, None, "Baseline exige thresholds=null."
    else:
        normalized_thresholds, threshold_error = _validate_thresholds(thresholds)
        if threshold_error:
            return request_id, mode, None, None, threshold_error
        assert normalized_thresholds is not None
        inputs.update(normalized_thresholds)

    return request_id, mode, LIVE_WORKFLOW, inputs, None


def request_marker(request_id: str) -> str:
    """Devuelve la marca idempotente para un request_id ya validado."""

    return f"<!-- la-colonia-file-dispatch:{request_id} -->"


def evaluate_file_request(
    context: Mapping[str, Any],
    raw_command: str | None,
    existing_comment_markers: Iterable[str] = (),
) -> DispatchDecision:
    """Evalúa contexto, archivo no confiable e idempotencia sin ejecutarlo."""

    # Los comentarios son observabilidad únicamente. Se conserva el parámetro
    # para compatibilidad, pero nunca participa en autorización ni replay.
    del existing_comment_markers

    context_error = _validate_context(context)
    if context_error is not None:
        return context_error

    pr_number = int(context["pr_number"])
    command, parse_error = _parse_command(raw_command)
    if parse_error:
        return _rejected(parse_error, pr_number=pr_number)
    assert command is not None

    request_id, mode, workflow, inputs, validation_error = _normalize_command(command)
    if validation_error:
        return _rejected(validation_error, pr_number=pr_number)
    assert request_id is not None and mode is not None
    assert workflow in TRUSTED_WORKFLOWS and inputs is not None

    return DispatchDecision(
        True,
        True,
        "",
        pr_number=pr_number,
        request_id=request_id,
        mode=mode,
        ref=str(context["head_ref"]),
        head_sha=str(context["head_sha"]),
        workflow=workflow,
        inputs=inputs,
    )


def build_controller_comment(
    decision: DispatchDecision,
    *,
    controller_run_id: int | str,
    controller_url: str,
) -> str:
    """Construye comentarios sanitizados de aceptación o rechazo."""

    if not decision.accepted:
        return (
            "## Solicitud rechazada\n\n"
            f"- Razón: {decision.reason}\n"
            "- Estado: **no se envió workflow_dispatch**"
        )

    assert decision.request_id is not None
    assert decision.mode is not None
    assert decision.workflow in TRUSTED_WORKFLOWS
    assert decision.head_sha is not None
    assert decision.ref is not None
    normalized = json.dumps(decision.inputs, ensure_ascii=False, indent=2, sort_keys=True)
    return (
        "## Solicitud válida pero bloqueada\n\n"
        f"- request_id: `{decision.request_id}`\n"
        f"- Modo: `{decision.mode}`\n"
        f"- Workflow: `{decision.workflow}`\n"
        f"- Commit SHA: `{decision.head_sha}`\n"
        f"- Rama: `{decision.ref}`\n"
        f"- Run ID del controlador: `{controller_run_id}`\n"
        f"- Enlace del controlador: {controller_url}\n"
        "- Estado: **no se envió workflow_dispatch; GLOBAL LIVE BLOCKED**\n\n"
        "### Parámetros normalizados\n\n"
        f"```json\n{normalized}\n```"
    )
