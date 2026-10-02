#!/usr/bin/env python3
"""Captura semanal read-only de especificaciones PriceSmart HN desde la ficha pública.

Entrada: la última captura diaria **aceptada** de PriceSmart (handoff
``daily-acquisition-pricesmart-<run>-attempt-<n>`` con sus snapshots SPS 6603 y
TGU 6602). Ambos clubes comparten catálogo: cada ``pid`` se descarga una sola
vez desde ``/es-hn/producto/<slug>/<pid>``.

Política de tráfico (autorización del responsable del proyecto registrada en
``.automation/pricesmart-specs-capture-authorization.json``):

- una sola conexión, ≥ 1.5 s entre inicios de request (2 s por defecto);
- User-Agent identificable, sin cookies ni credenciales;
- presupuesto finito de requests y de reintentos (sólo 5xx/timeouts);
- aborta ante 429, 403 repetido o desafío anti-bot (no se evaden);
- incremental: omite ``pid`` verificados hace < 28 días salvo ``--force``;
- reanudable con ``--checkpoint`` (JSONL por ítem).

Fail-closed por página (``specs=None`` si no se puede parsear); la corrida falla
sólo si la tasa de fallos supera ``--max-failure-rate``. La primera corrida real
(sin estado previo) guarda HTML crudo de una muestra para endurecer el parser.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "scripts", ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from actualizar_mvp_sqlite_la_colonia import SnapshotError, validate_snapshot_bytes  # noqa: E402
from ensamblar_adquisicion_diaria import EXPECTED, _attempts, _complete  # noqa: E402
from precios_supermercados.pricesmart_specs_persistence import (  # noqa: E402
    ARTIFACT_SCHEMA,
    DEFAULT_STALE_DAYS,
    STATE_SCHEMA,
)
from precios_supermercados.scrapers.pricesmart_specs import (  # noqa: E402
    PARSER_VERSION,
    PRODUCT_BASE_URL,
    STATUS_PARSED,
    PriceSmartSpecsError,
    build_product_url,
    parse_product_page,
    spec_fingerprint,
)

AUTHORIZATION_SCHEMA = "precios-sps-pricesmart-specs-authorization/v1"
AUTHORIZATION_KEYS = frozenset({
    "schema", "authorization_id", "operation", "approved_by", "approved_at_utc",
    "cadence", "live_read_only_authorized", "persistence_target",
    "allowed_url_prefix", "max_requests_per_run", "min_delay_seconds",
    "concurrency", "requested_ref", "note",
})
CHECKPOINT_SCHEMA = "precios-sps-pricesmart-specs-checkpoint/v1"
UA = "Mozilla/5.0 (compatible; PreciosSupermercadosSPS-PriceSmartSpecs/1.0; read-only)"
MIN_DELAY = 1.5
DEFAULT_DELAY = 2.0
MAX_REQUESTS_HARD = 1500
DEFAULT_MAX_ITEMS = 900
DEFAULT_MAX_RETRIES = 2
MAX_RETRIES_HARD = 3
DEFAULT_RETRY_BUDGET = 30
DEFAULT_MAX_FAILURE_RATE = 0.20
DEFAULT_DEADLINE_SECONDS = 100 * 60
MAX_BODY_BYTES = 4 * 1024 * 1024
FIRST_RUN_RAW_SAMPLE = 10
RAW_FAILURE_CAP = 25
RETRYABLE_STATUSES = frozenset({500, 502, 503, 504})
NOT_FOUND_STATUSES = frozenset({404, 410})
ANTI_BOT_MARKERS = (
    "captcha", "cf-chl", "challenge-platform", "attention required",
    "access denied", "are you a robot", "px-captcha",
)
GROCERY_ROOTS = frozenset({
    "Alimentos", "Licor, cerveza y vino", "Salud y belleza", "Hogar", "Bebé",
    "Mascotas", "Suministros para restaurantes", "Productos de temporada",
})
FETCH_FAILED = "fetch_failed"
NOT_FOUND = "not_found"
FAILURE_STATUSES = frozenset({
    FETCH_FAILED, "parse_failed", "identity_unverified", "identity_mismatch", "no_specifications",
})


class CaptureError(RuntimeError):
    pass


class AbortRun(RuntimeError):
    """Señal de la fuente que obliga a detener todo el tráfico (429, 403, anti-bot)."""


def utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(value: object, reason: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value):
        raise CaptureError(reason)
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Autorización registrada
# ---------------------------------------------------------------------------


def load_authorization(path: Path, *, now: datetime, delay: float, max_requests: int) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CaptureError("authorization_unreadable") from exc
    if not isinstance(document, dict) or set(document) != AUTHORIZATION_KEYS:
        raise CaptureError("authorization_closed_set_mismatch")
    expected = {
        "schema": AUTHORIZATION_SCHEMA,
        "operation": "weekly_pricesmart_product_specs",
        "approved_by": "project_owner",
        "cadence": "weekly",
        "live_read_only_authorized": True,
        "persistence_target": "turso:pricesmart_product_specs",
        "allowed_url_prefix": PRODUCT_BASE_URL,
        "concurrency": 1,
        "requested_ref": "main",
    }
    for key, value in expected.items():
        if document[key] != value:
            raise CaptureError(f"authorization_{key}_invalid")
    if not isinstance(document["authorization_id"], str) or not re.fullmatch(
        r"[a-z0-9][a-z0-9._-]{7,79}", document["authorization_id"]
    ):
        raise CaptureError("authorization_id_invalid")
    if parse_utc(document["approved_at_utc"], "authorization_approved_at_invalid") > now + timedelta(minutes=5):
        raise CaptureError("authorization_not_yet_valid")
    min_delay = document["min_delay_seconds"]
    budget = document["max_requests_per_run"]
    if type(min_delay) not in {int, float} or min_delay < MIN_DELAY:
        raise CaptureError("authorization_min_delay_invalid")
    if type(budget) is not int or not 0 < budget <= MAX_REQUESTS_HARD:
        raise CaptureError("authorization_budget_invalid")
    if delay < min_delay:
        raise CaptureError("delay_below_authorized_minimum")
    if max_requests > budget:
        raise CaptureError("request_budget_above_authorization")
    return document


# ---------------------------------------------------------------------------
# Catálogo de entrada (captura diaria aceptada)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CatalogItem:
    product_id: str
    slug: str
    url: str
    title: str
    categories: tuple[str, ...]

    @property
    def grocery(self) -> bool:
        return any(category in GROCERY_ROOTS for category in self.categories)


def find_accepted_snapshots(daily_dir: Path, run_id: str) -> tuple[list[Path], int]:
    accepted = [
        (attempt, path)
        for attempt, path in _attempts(daily_dir, "pricesmart", run_id)
        if _complete(path, EXPECTED["pricesmart"])
    ]
    if not accepted:
        raise CaptureError("accepted_pricesmart_handoff_missing")
    attempt, path = accepted[-1]
    return [path / relative for relative in EXPECTED["pricesmart"]], attempt


def load_catalog(snapshot_paths: list[Path]) -> tuple[list[CatalogItem], dict[str, Any]]:
    by_pid: dict[str, CatalogItem] = {}
    evidence: dict[str, Any] = {"snapshots": [], "missing_slug": 0, "slug_conflicts": []}
    conflicts: set[str] = set()
    if not snapshot_paths:
        raise CaptureError("snapshot_paths_missing")
    for path in snapshot_paths:
        raw = path.read_bytes()
        try:
            snapshot = validate_snapshot_bytes(raw, supermarket_id="pricesmart")
        except SnapshotError as exc:
            raise CaptureError(f"daily_snapshot_invalid:{path.name}:{exc}") from exc
        if snapshot.get("catalog_complete") is not True or snapshot.get("location_verified_same_run") is not True:
            raise CaptureError(f"daily_snapshot_not_accepted:{path.name}")
        if snapshot.get("catalog_products_reported") != snapshot.get("unique_products_extracted"):
            raise CaptureError(f"daily_snapshot_count_mismatch:{path.name}")
        details = snapshot.get("source_details")
        if not isinstance(details, dict):
            raise CaptureError(f"daily_snapshot_source_details_missing:{path.name}")
        evidence["snapshots"].append({
            "file": path.name,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "location_id": snapshot.get("location_id"),
            "observed_at_utc": snapshot.get("observed_at_utc"),
            "products": snapshot.get("unique_products_extracted"),
        })
        for row in snapshot["products"]:
            pid = str(row["product_id"])
            detail = details.get(str(row["source_key"])) or {}
            slug = detail.get("slug") if isinstance(detail, dict) else None
            if not isinstance(slug, str) or not slug:
                evidence["missing_slug"] += 1
                continue
            try:
                url = build_product_url(slug, pid)
            except PriceSmartSpecsError:
                evidence["missing_slug"] += 1
                continue
            categories = tuple(
                part.strip() for part in str(row.get("category") or "").split("|") if part.strip()
            )
            current = by_pid.get(pid)
            if current is None:
                by_pid[pid] = CatalogItem(pid, slug, url, str(row["source_name"]), categories)
            elif current.slug != slug:
                conflicts.add(pid)
    for pid in sorted(conflicts):
        by_pid.pop(pid, None)
    evidence["slug_conflicts"] = sorted(conflicts)
    evidence["unique_products"] = len(by_pid)
    evidence["catalog_sha256"] = hashlib.sha256(
        "\n".join(f"{pid}:{item.slug}" for pid, item in sorted(by_pid.items())).encode()
    ).hexdigest()
    return [by_pid[pid] for pid in sorted(by_pid, key=int)], evidence


def load_state(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CaptureError("state_unreadable") from exc
    if not isinstance(state, dict) or state.get("schema") != STATE_SCHEMA or not isinstance(state.get("fresh"), dict):
        raise CaptureError("state_schema_invalid")
    fresh: dict[str, str] = {}
    for pid, verified in state["fresh"].items():
        parse_utc(verified, "state_verified_at_invalid")
        fresh[str(pid)] = str(verified)
    return fresh


def select_items(
    items: list[CatalogItem],
    fresh: dict[str, str],
    *,
    now: datetime,
    stale_days: int,
    force: bool,
    max_items: int,
) -> tuple[list[CatalogItem], int, int]:
    """Nuevos primero, luego más antiguos; alimentos/consumo antes que el resto."""

    cutoff = now - timedelta(days=stale_days)
    eligible: list[CatalogItem] = []
    skipped_fresh = 0
    for item in items:
        verified = fresh.get(item.product_id)
        if not force and verified is not None and parse_utc(verified, "state_verified_at_invalid") >= cutoff:
            skipped_fresh += 1
            continue
        eligible.append(item)
    eligible.sort(key=lambda item: (
        item.product_id in fresh,
        not item.grocery,
        fresh.get(item.product_id, ""),
        int(item.product_id),
    ))
    return eligible[:max_items], skipped_fresh, len(eligible)


# ---------------------------------------------------------------------------
# HTTP cortés
# ---------------------------------------------------------------------------


@dataclass
class Response:
    status: int | None
    final_url: str | None
    body: bytes
    error: str | None = None


Transport = Callable[[str], Response]


def urllib_transport(url: str) -> Response:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "es-HN,es;q=0.9",
            "Accept-Encoding": "gzip",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read(MAX_BODY_BYTES + 1)
            encoding = (response.headers.get("Content-Encoding") or "").casefold()
            status, final_url = response.status, response.geturl()
    except urllib.error.HTTPError as exc:
        return Response(exc.code, exc.geturl(), b"", f"http_{exc.code}")
    except Exception as exc:  # noqa: BLE001
        return Response(None, None, b"", f"{type(exc).__name__}:{str(exc)[:200]}")
    if len(body) > MAX_BODY_BYTES:
        return Response(status, final_url, b"", "body_too_large")
    if encoding == "gzip":
        try:
            body = gzip.decompress(body)
        except OSError:
            return Response(status, final_url, b"", "gzip_invalid")
        if len(body) > 4 * MAX_BODY_BYTES:
            return Response(status, final_url, b"", "body_too_large")
    return Response(status, final_url, body, None)


@dataclass
class Fetcher:
    transport: Transport
    delay: float
    max_requests: int
    max_retries: int = DEFAULT_MAX_RETRIES
    retry_budget: int = DEFAULT_RETRY_BUDGET
    sleep: Callable[[float], None] = time.sleep
    monotonic: Callable[[], float] = time.monotonic
    requests: int = 0
    retries: int = 0
    consecutive_403: int = 0
    total_403: int = 0
    request_starts: list[float] = field(default_factory=list)
    _last_start: float | None = None

    def _pace(self) -> None:
        if self._last_start is not None:
            wait = self._last_start + self.delay - self.monotonic()
            if wait > 0:
                self.sleep(wait)
        self._last_start = self.monotonic()
        self.request_starts.append(self._last_start)

    def get(self, url: str) -> tuple[Response, int]:
        attempts = 0
        while True:
            if self.requests >= self.max_requests:
                raise AbortRun("request_budget_exhausted")
            self._pace()
            self.requests += 1
            attempts += 1
            response = self.transport(url)
            if response.status == 429:
                raise AbortRun("http_429_rate_limited")
            if response.status == 403:
                self.consecutive_403 += 1
                self.total_403 += 1
                if self.consecutive_403 >= 2 or self.total_403 >= 3:
                    raise AbortRun("http_403_repeated")
                return response, attempts
            self.consecutive_403 = 0
            if response.status == 200 and response.error is None:
                if _looks_like_challenge(response.body):
                    raise AbortRun("anti_bot_challenge")
                return response, attempts
            retryable = response.status in RETRYABLE_STATUSES or (
                response.status is None and response.error is not None
            )
            if not retryable or attempts > self.max_retries or self.retries >= self.retry_budget:
                return response, attempts
            self.retries += 1
            self.sleep(self.delay * (2 ** attempts))


def _looks_like_challenge(body: bytes) -> bool:
    """Página de desafío/bloqueo servida con 200: pequeña, con marcador y sin ficha."""

    text = body.decode("utf-8", "replace").casefold()
    if not any(marker in text for marker in ANTI_BOT_MARKERS):
        return False
    product_markers = ("__next_data__", "mero de ítem", "mero de item", "especificaciones")
    return len(body) < 200_000 and not any(marker in text for marker in product_markers)


# ---------------------------------------------------------------------------
# Corrida
# ---------------------------------------------------------------------------


def _final_url_ok(final_url: str | None, product_id: str) -> bool:
    if not final_url:
        return True
    path = final_url.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    return path.startswith(PRODUCT_BASE_URL) and path.endswith(f"/{product_id}")


def capture_item(item: CatalogItem, fetcher: Fetcher, *, now: Callable[[], datetime]) -> tuple[dict[str, Any], bytes]:
    response, attempts = fetcher.get(item.url)
    result: dict[str, Any] = {
        "product_id": item.product_id,
        "url": item.url,
        "status": FETCH_FAILED,
        "http_status": response.status,
        "attempts": attempts,
        "fetched_at_utc": utc_text(now()),
        "html_sha256": None,
        "html_bytes": len(response.body),
        "extraction_method": None,
        "error": response.error,
        "specs": None,
        "spec_sha256": None,
    }
    if response.status in NOT_FOUND_STATUSES:
        result["status"] = NOT_FOUND
        return result, b""
    if response.status != 200 or response.error is not None or not response.body:
        result["error"] = response.error or f"http_{response.status}"
        return result, b""
    if not _final_url_ok(response.final_url, item.product_id):
        result["status"] = NOT_FOUND
        result["error"] = "redirected_off_product"
        return result, b""
    result["html_sha256"] = hashlib.sha256(response.body).hexdigest()
    try:
        html = response.body.decode("utf-8")
    except UnicodeDecodeError:
        html = response.body.decode("utf-8", "replace")
    parsed = parse_product_page(html, item.product_id)
    result["status"] = parsed.status
    result["extraction_method"] = parsed.extraction_method
    result["error"] = parsed.error
    if parsed.status == STATUS_PARSED and parsed.specs is not None:
        result["specs"] = parsed.specs
        result["spec_sha256"] = spec_fingerprint(parsed.specs)
    return result, response.body


def _load_checkpoint(path: Path | None, catalog_sha256: str) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return {}
    header = json.loads(lines[0])
    if header != {"schema": CHECKPOINT_SCHEMA, "catalog_sha256": catalog_sha256, "parser_version": PARSER_VERSION}:
        raise CaptureError("checkpoint_mismatch_use_new_path")
    result: dict[str, dict[str, Any]] = {}
    for line in lines[1:]:
        if not line.strip():
            continue
        item = json.loads(line)
        result[str(item["product_id"])] = item
    return result


def _append_checkpoint(path: Path | None, catalog_sha256: str, item: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        header = {"schema": CHECKPOINT_SCHEMA, "catalog_sha256": catalog_sha256, "parser_version": PARSER_VERSION}
        path.write_text(json.dumps(header, sort_keys=True) + "\n", encoding="utf-8")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")


FIELD_COVERAGE = {
    "brand": lambda specs: specs.get("brand") is not None,
    "category_path": lambda specs: bool(specs.get("category_path")),
    "net_weight": lambda specs: specs.get("net_weight") is not None,
    "net_volume": lambda specs: specs.get("net_volume") is not None,
    "unit_weight": lambda specs: specs.get("unit_weight") is not None,
    "pack_count": lambda specs: specs.get("pack_count") is not None,
    "imported_or_national": lambda specs: specs.get("imported_or_national") is not None,
    "origin_country": lambda specs: specs.get("origin_country") is not None,
    "allergens": lambda specs: bool(specs.get("allergens")),
    "presentation_hint": lambda specs: specs.get("presentation_hint") is not None,
    "gtin_valid": lambda specs: specs.get("gtin") is not None,
    "with_conflicts": lambda specs: bool(specs.get("conflicts")),
}


def coverage(results: list[dict[str, Any]], *, max_failure_rate: float) -> dict[str, Any]:
    counts = Counter(item["status"] for item in results)
    attempted = len(results)
    denominator = attempted - counts.get(NOT_FOUND, 0)
    failures = sum(counts.get(status, 0) for status in FAILURE_STATUSES)
    rate = round(failures / denominator, 4) if denominator else 0.0
    parsed_specs = [item["specs"] for item in results if item["status"] == STATUS_PARSED]
    return {
        "attempted": attempted,
        "status_counts": dict(sorted(counts.items())),
        "failures": failures,
        "failure_rate": rate,
        "max_failure_rate": max_failure_rate,
        "parsed": len(parsed_specs),
        "field_coverage": {
            name: sum(1 for specs in parsed_specs if check(specs))
            for name, check in FIELD_COVERAGE.items()
        },
    }


@dataclass
class RunConfig:
    delay: float = DEFAULT_DELAY
    max_requests: int = MAX_REQUESTS_HARD
    max_items: int = DEFAULT_MAX_ITEMS
    max_retries: int = DEFAULT_MAX_RETRIES
    retry_budget: int = DEFAULT_RETRY_BUDGET
    stale_days: int = DEFAULT_STALE_DAYS
    force: bool = False
    max_failure_rate: float = DEFAULT_MAX_FAILURE_RATE
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS
    raw_sample_size: int | None = None
    github_run_id: str | None = None
    authorization_id: str | None = None


def run_capture(
    items: list[CatalogItem],
    catalog_evidence: dict[str, Any],
    fresh: dict[str, str],
    config: RunConfig,
    *,
    transport: Transport,
    raw_directory: Path | None = None,
    checkpoint: Path | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc).replace(microsecond=0),
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    started_at = now()
    started = monotonic()
    selected, skipped_fresh, eligible = select_items(
        items, fresh, now=started_at, stale_days=config.stale_days,
        force=config.force, max_items=config.max_items,
    )
    done = _load_checkpoint(checkpoint, catalog_evidence["catalog_sha256"])
    fetcher = Fetcher(
        transport, config.delay, config.max_requests, config.max_retries,
        config.retry_budget, sleep=sleep, monotonic=monotonic,
    )
    sample_size = config.raw_sample_size
    if sample_size is None:
        sample_size = FIRST_RUN_RAW_SAMPLE if not fresh else 0
    raw_saved = 0
    raw_failures_saved = 0
    results: list[dict[str, Any]] = []
    resumed = 0
    aborted: str | None = None
    not_attempted = 0
    for item in selected:
        if item.product_id in done:
            results.append(done[item.product_id])
            resumed += 1
            continue
        if aborted is not None or monotonic() - started > config.deadline_seconds:
            not_attempted += 1
            continue
        try:
            result, body = capture_item(item, fetcher, now=now)
        except AbortRun as exc:
            aborted = str(exc)
            not_attempted += 1
            continue
        save = False
        if body and raw_directory is not None:
            if raw_saved < sample_size:
                save = True
                raw_saved += 1
            elif result["status"] in FAILURE_STATUSES and raw_failures_saved < RAW_FAILURE_CAP:
                save = True
                raw_failures_saved += 1
        if save and raw_directory is not None:
            raw_directory.mkdir(parents=True, exist_ok=True)
            (raw_directory / f"{item.product_id}.html.gz").write_bytes(gzip.compress(body, mtime=0))
        result["raw_saved"] = save
        results.append(result)
        _append_checkpoint(checkpoint, catalog_evidence["catalog_sha256"], result)

    stats = coverage(results, max_failure_rate=config.max_failure_rate)
    passed = aborted is None and stats["failure_rate"] <= config.max_failure_rate
    return {
        "schema": ARTIFACT_SCHEMA,
        "result": "success" if passed else "failed",
        "generated_at_utc": utc_text(now()),
        "parser_version": PARSER_VERSION,
        "policy": {
            "gtin": "PriceSmart no publica GTIN; 'Número de ítem' es interno y nunca es GTIN. Un gtin sólo se registra si aparece en JSON y supera el check digit GS1.",
            "per_page": "fail_closed_specs_none",
            "failure_statuses": sorted(FAILURE_STATUSES),
            "not_found_excluded_from_rate": True,
        },
        "source": {
            "daily_capture": catalog_evidence,
        },
        "run": {
            "github_run_id": config.github_run_id,
            "authorization_id": config.authorization_id,
            "started_at_utc": utc_text(started_at),
            "elapsed_seconds": round(monotonic() - started, 3),
            "delay_seconds": config.delay,
            "concurrency": 1,
            "user_agent": UA,
            "request_count": fetcher.requests,
            "request_budget": config.max_requests,
            "retry_count": fetcher.retries,
            "retry_budget": config.retry_budget,
            "aborted_reason": aborted,
            "stale_days": config.stale_days,
            "force": config.force,
            "max_items": config.max_items,
            "raw_sample_saved": raw_saved,
            "raw_failures_saved": raw_failures_saved,
        },
        "coverage": {
            "catalog_items": len(items),
            "skipped_fresh": skipped_fresh,
            "eligible": eligible,
            "selected": len(selected),
            "resumed_from_checkpoint": resumed,
            "not_attempted": not_attempted,
            **stats,
        },
        "items": sorted(results, key=lambda item: int(item["product_id"])),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-read-only", action="store_true")
    parser.add_argument("--authorization", type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--daily-artifact-dir", type=Path)
    source.add_argument("--snapshot", type=Path, action="append")
    parser.add_argument("--daily-run-id")
    parser.add_argument("--state", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--raw-directory", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--delay-seconds", type=float, default=DEFAULT_DELAY)
    parser.add_argument("--max-requests", type=int, default=MAX_REQUESTS_HARD)
    parser.add_argument("--max-items", type=int, default=DEFAULT_MAX_ITEMS)
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES)
    parser.add_argument("--stale-days", type=int, default=DEFAULT_STALE_DAYS)
    parser.add_argument("--max-failure-rate", type=float, default=DEFAULT_MAX_FAILURE_RATE)
    parser.add_argument("--deadline-seconds", type=float, default=DEFAULT_DEADLINE_SECONDS)
    parser.add_argument("--raw-sample-size", type=int)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--github-run-id")
    args = parser.parse_args()

    if not args.live_read_only:
        raise SystemExit("explicit_live_read_only_flag_required")
    if args.delay_seconds < MIN_DELAY:
        raise SystemExit("delay_too_small")
    if not 0 < args.max_requests <= MAX_REQUESTS_HARD:
        raise SystemExit("request_budget_invalid")
    if not 0 < args.max_items <= args.max_requests:
        raise SystemExit("max_items_invalid")
    if not 0 <= args.max_retries <= MAX_RETRIES_HARD:
        raise SystemExit("retry_budget_invalid")
    if not 0 < args.max_failure_rate < 1:
        raise SystemExit("max_failure_rate_invalid")
    if not 1 <= args.stale_days <= 365:
        raise SystemExit("stale_days_invalid")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    authorization = load_authorization(
        args.authorization, now=now, delay=args.delay_seconds, max_requests=args.max_requests,
    )
    if args.daily_artifact_dir is not None:
        if not args.daily_run_id or not args.daily_run_id.isdigit():
            raise SystemExit("daily_run_id_required")
        paths, attempt = find_accepted_snapshots(args.daily_artifact_dir, args.daily_run_id)
    else:
        paths, attempt = list(args.snapshot), None
    items, evidence = load_catalog(paths)
    evidence["daily_run_id"] = args.daily_run_id
    evidence["daily_run_attempt"] = attempt
    fresh = load_state(args.state)
    config = RunConfig(
        delay=args.delay_seconds,
        max_requests=args.max_requests,
        max_items=args.max_items,
        max_retries=args.max_retries,
        stale_days=args.stale_days,
        force=args.force,
        max_failure_rate=args.max_failure_rate,
        deadline_seconds=args.deadline_seconds,
        raw_sample_size=args.raw_sample_size,
        github_run_id=args.github_run_id,
        authorization_id=authorization["authorization_id"],
    )
    artifact = run_capture(
        items, evidence, fresh, config,
        transport=urllib_transport,
        raw_directory=args.raw_directory,
        checkpoint=args.checkpoint,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    summary = {key: artifact[key] for key in ("result", "generated_at_utc")} | {
        "coverage": {key: value for key, value in artifact["coverage"].items()},
        "aborted_reason": artifact["run"]["aborted_reason"],
        "request_count": artifact["run"]["request_count"],
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    if artifact["result"] != "success":
        raise SystemExit(
            f"pricesmart_specs_capture_failed:aborted={artifact['run']['aborted_reason']}:"
            f"failure_rate={artifact['coverage']['failure_rate']}"
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CaptureError, OSError) as exc:
        raise SystemExit(str(exc)) from exc
