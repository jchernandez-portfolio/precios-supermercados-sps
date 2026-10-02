"""Captura semanal de especificaciones PriceSmart con HTTP simulado (sin red).

Las páginas servidas por el transporte falso son SINTÉTICAS (mismo armazón que
``test_pricesmart_specs_parser``). El catálogo de entrada sí es la captura diaria
real aceptada del 2026-09-01 versionada en ``reports/pricesmart``.
"""
from __future__ import annotations

import copy
import gzip
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import obtener_especificaciones_pricesmart as capture  # noqa: E402
from ensamblar_adquisicion_diaria import HANDOFF_FILE, HANDOFF_SCHEMA  # noqa: E402
from precios_supermercados.pricesmart_specs_persistence import STATE_SCHEMA  # noqa: E402

REPORT = ROOT / "reports" / "pricesmart" / "2026-09-01-full"
AUTHORIZATION = ROOT / ".automation" / "pricesmart-specs-capture-authorization.json"
NOW = datetime(2026, 10, 3, 11, 40, tzinfo=timezone.utc)


def synthetic_page(pid: str, *, specs: bool = True) -> bytes:
    block = (
        "<h3>Especificaciones</h3><div>"
        "<div><span>Peso neto</span><span>0.5000 kg</span></div>"
        "<div><span>Marca</span><span>Sintética</span></div></div>"
        if specs else "<h3>Descripción</h3><p>sin especificaciones</p>"
    )
    return (
        f"<html><body><h1>Producto {pid}</h1><div><span>Número de ítem</span> <span>{pid}</span></div>"
        f"<section>{block}</section></body></html>"
    ).encode()


class Clock:
    def __init__(self) -> None:
        self.value = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


class FakeTransport:
    """Devuelve respuestas por URL (cola) y registra cada request en orden."""

    def __init__(self, clock: Clock, responses: dict[str, list[capture.Response]] | None = None,
                 default=None) -> None:
        self.clock = clock
        self.responses = responses or {}
        self.default = default
        self.calls: list[tuple[str, float]] = []

    def __call__(self, url: str) -> capture.Response:
        self.calls.append((url, self.clock.value))
        self.clock.value += 0.3  # latencia simulada
        queue = self.responses.get(url)
        if queue:
            return queue.pop(0)
        pid = url.rsplit("/", 1)[1]
        if self.default is not None:
            return self.default(pid, url)
        return capture.Response(200, url, synthetic_page(pid))


def daily_dir(tmp_path: Path, run_id: str = "123", attempts=(1,)) -> Path:
    root = tmp_path / "daily"
    for attempt in attempts:
        target = root / f"daily-acquisition-pricesmart-{run_id}-attempt-{attempt}"
        (target / "pricesmart").mkdir(parents=True)
        for location, suffix in (("pricesmart_sps", "sps"), ("pricesmart_tgu", "tgu")):
            raw = gzip.decompress((REPORT / f"{location}.json.gz").read_bytes())
            (target / "pricesmart" / f"snapshot-pricesmart-{suffix}.json").write_bytes(raw)
        (target / HANDOFF_FILE).write_text(json.dumps({
            "schema": HANDOFF_SCHEMA, "retailer": "pricesmart", "run_id": run_id,
            "run_attempt": attempt, "accepted": True,
        }), encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def catalog(tmp_path_factory: pytest.TempPathFactory):
    root = daily_dir(tmp_path_factory.mktemp("catalog"))
    paths, attempt = capture.find_accepted_snapshots(root, "123")
    assert attempt == 1
    return capture.load_catalog(paths)


def run(items, evidence, fresh=None, *, tmp_path: Path, transport=None, clock=None, **config):
    clock = clock or Clock()
    transport = transport or FakeTransport(clock)
    defaults = {"delay": 2.0, "max_items": 5}
    defaults.update(config)
    artifact = capture.run_capture(
        items, evidence, fresh or {}, capture.RunConfig(**defaults),
        transport=transport,
        raw_directory=tmp_path / "raw",
        now=lambda: NOW,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    return artifact, transport, clock


def test_catalog_is_deduplicated_across_clubs_with_public_urls(catalog) -> None:
    items, evidence = catalog
    assert len(items) == 1124  # mismo catálogo en 6603 y 6602; cada pid una vez
    assert evidence["unique_products"] == 1124
    assert evidence["missing_slug"] == 0
    assert evidence["slug_conflicts"] == []
    assert len(evidence["snapshots"]) == 2
    first = items[0]
    assert first.url == f"https://www.pricesmart.com/es-hn/producto/{first.slug}/{first.product_id}"
    variant = next(item for item in items if item.product_id == "317825")
    assert variant.url.endswith("/317825")
    assert len({item.product_id for item in items}) == len(items)


def test_highest_accepted_attempt_is_selected_and_missing_handoff_fails(tmp_path: Path) -> None:
    root = daily_dir(tmp_path, attempts=(1, 2))
    _, attempt = capture.find_accepted_snapshots(root, "123")
    assert attempt == 2
    with pytest.raises(capture.CaptureError, match="accepted_pricesmart_handoff_missing"):
        capture.find_accepted_snapshots(root, "999")


def test_rate_limit_single_connection_and_identifiable_agent(catalog, tmp_path: Path) -> None:
    items, evidence = catalog
    artifact, transport, clock = run(items, evidence, tmp_path=tmp_path, max_items=4)
    starts = [at for _, at in transport.calls]
    assert len(starts) == 4
    assert all(later - earlier >= 2.0 for earlier, later in zip(starts, starts[1:]))
    assert artifact["run"]["concurrency"] == 1
    assert "PreciosSupermercadosSPS-PriceSmartSpecs" in artifact["run"]["user_agent"]
    assert artifact["result"] == "success"
    assert artifact["coverage"]["parsed"] == 4
    assert artifact["coverage"]["field_coverage"]["net_weight"] == 4
    item = artifact["items"][0]
    assert item["specs"]["net_weight"]["value_g"] == "500"
    assert len(item["spec_sha256"]) == 64
    with pytest.raises(capture.CaptureError):
        capture.load_authorization(AUTHORIZATION, now=NOW, delay=1.0, max_requests=10)


def test_incremental_skips_fresh_items_unless_forced(catalog, tmp_path: Path) -> None:
    items, evidence = catalog
    fresh_ids = [item.product_id for item in items[:3]]
    fresh = {
        fresh_ids[0]: "2026-10-01T00:00:00Z",
        fresh_ids[1]: "2026-09-20T00:00:00Z",
        fresh_ids[2]: "2026-08-01T00:00:00Z",  # > 28 días: se vuelve a capturar
    }
    artifact, transport, _ = run(items, evidence, fresh, tmp_path=tmp_path, max_items=2000, max_requests=2000)
    fetched = {url.rsplit("/", 1)[1] for url, _ in transport.calls}
    assert artifact["coverage"]["skipped_fresh"] == 2
    assert fresh_ids[0] not in fetched and fresh_ids[1] not in fetched
    assert fresh_ids[2] in fetched
    assert artifact["coverage"]["eligible"] == len(items) - 2

    forced, transport, _ = run(items[:3], evidence, fresh, tmp_path=tmp_path / "f", force=True)
    assert forced["coverage"]["skipped_fresh"] == 0
    assert len(transport.calls) == 3


def test_new_and_grocery_items_are_prioritized(catalog) -> None:
    items, _ = catalog
    fresh = {items[0].product_id: "2026-08-01T00:00:00Z"}
    selected, _, _ = capture.select_items(items, fresh, now=NOW, stale_days=28, force=False, max_items=len(items))
    assert selected[-1].product_id == items[0].product_id  # ya capturado: al final
    assert selected[0].grocery


def test_resume_from_checkpoint_does_not_refetch(catalog, tmp_path: Path) -> None:
    items, evidence = catalog
    checkpoint = tmp_path / "checkpoint.jsonl"
    clock = Clock()
    transport = FakeTransport(clock, default=lambda pid, url: capture.Response(429, url, b"", "http_429")
                              if pid == items[2].product_id else capture.Response(200, url, synthetic_page(pid)))
    first = capture.run_capture(
        items[:4], evidence, {}, capture.RunConfig(delay=2.0, max_items=4), transport=transport,
        checkpoint=checkpoint, now=lambda: NOW, sleep=clock.sleep, monotonic=clock.monotonic,
    )
    assert first["result"] == "failed"
    assert first["run"]["aborted_reason"] == "http_429_rate_limited"
    assert len(transport.calls) == 3  # aborta en el 429 sin más tráfico
    assert len(checkpoint.read_text().splitlines()) == 3  # header + 2 ítems

    clock = Clock()
    transport = FakeTransport(clock)
    second = capture.run_capture(
        items[:4], evidence, {}, capture.RunConfig(delay=2.0, max_items=4), transport=transport,
        checkpoint=checkpoint, now=lambda: NOW, sleep=clock.sleep, monotonic=clock.monotonic,
    )
    fetched = [url.rsplit("/", 1)[1] for url, _ in transport.calls]
    assert fetched == [items[2].product_id, items[3].product_id]
    assert second["coverage"]["resumed_from_checkpoint"] == 2
    assert second["result"] == "success"
    assert [item["product_id"] for item in second["items"]] == sorted(
        (item.product_id for item in items[:4]), key=int
    )
    other = copy.deepcopy(evidence)
    other["catalog_sha256"] = "0" * 64
    with pytest.raises(capture.CaptureError, match="checkpoint_mismatch"):
        capture.run_capture(
            items[:4], other, {}, capture.RunConfig(), transport=transport,
            checkpoint=checkpoint, now=lambda: NOW, sleep=clock.sleep, monotonic=clock.monotonic,
        )


@pytest.mark.parametrize(("bad", "total", "expected"), [(2, 5, "failed"), (1, 10, "success"), (2, 10, "success"), (3, 10, "failed")])
def test_run_fails_only_above_failure_threshold(catalog, tmp_path: Path, bad: int, total: int, expected: str) -> None:
    items, evidence = catalog
    bad_ids = {item.product_id for item in items[:bad]}
    clock = Clock()
    transport = FakeTransport(clock, default=lambda pid, url: capture.Response(200, url, synthetic_page(pid, specs=pid not in bad_ids)))
    artifact, _, _ = run(items[:total], evidence, tmp_path=tmp_path, transport=transport, clock=clock, max_items=total)
    assert artifact["coverage"]["status_counts"].get("no_specifications", 0) == bad
    assert artifact["coverage"]["failure_rate"] == round(bad / total, 4)
    assert artifact["result"] == expected
    for item in artifact["items"]:
        if item["product_id"] in bad_ids:
            assert item["specs"] is None and item["spec_sha256"] is None


def test_repeated_403_aborts_and_single_403_is_a_page_failure(catalog, tmp_path: Path) -> None:
    items, evidence = catalog
    clock = Clock()
    transport = FakeTransport(clock, default=lambda pid, url: capture.Response(403, url, b"", "http_403"))
    artifact, transport, _ = run(items, evidence, tmp_path=tmp_path, transport=transport, clock=clock, max_items=10)
    assert artifact["run"]["aborted_reason"] == "http_403_repeated"
    assert len(transport.calls) == 2
    assert artifact["result"] == "failed"
    assert artifact["coverage"]["not_attempted"] == 9


def test_retries_only_transient_errors_with_backoff_and_budget(catalog, tmp_path: Path) -> None:
    items, evidence = catalog
    clock = Clock()
    url = items[0].url
    transport = FakeTransport(clock, {url: [
        capture.Response(503, url, b"", "http_503"),
        capture.Response(None, None, b"", "TimeoutError:timed out"),
    ]})
    artifact, transport, clock = run(items[:1], evidence, tmp_path=tmp_path, transport=transport, clock=clock, max_items=1)
    assert len(transport.calls) == 3
    assert artifact["run"]["retry_count"] == 2
    assert artifact["items"][0]["attempts"] == 3
    assert artifact["items"][0]["status"] == "parsed"
    assert 4.0 in clock.sleeps and 8.0 in clock.sleeps

    clock = Clock()
    transport = FakeTransport(clock, default=lambda pid, u: capture.Response(503, u, b"", "http_503"))
    artifact, transport, _ = run(items[:3], evidence, tmp_path=tmp_path / "b", transport=transport, clock=clock,
                                 max_items=3, retry_budget=1)
    assert artifact["run"]["retry_count"] == 1
    assert len(transport.calls) == 4  # 1 reintento global y luego sin reintentos
    assert artifact["coverage"]["status_counts"] == {"fetch_failed": 3}
    assert artifact["result"] == "failed"


def test_not_found_and_off_product_redirects_are_excluded_from_rate(catalog, tmp_path: Path) -> None:
    items, evidence = catalog
    clock = Clock()

    def respond(pid, url):
        if pid == items[0].product_id:
            return capture.Response(404, url, b"", "http_404")
        if pid == items[1].product_id:
            return capture.Response(200, "https://www.pricesmart.com/es-hn/", synthetic_page(pid))
        return capture.Response(200, url, synthetic_page(pid))

    artifact, _, _ = run(items[:4], evidence, tmp_path=tmp_path, transport=FakeTransport(clock, default=respond),
                         clock=clock, max_items=4)
    assert artifact["coverage"]["status_counts"] == {"not_found": 2, "parsed": 2}
    assert artifact["coverage"]["failure_rate"] == 0.0
    assert artifact["result"] == "success"


def test_anti_bot_challenge_aborts(catalog, tmp_path: Path) -> None:
    items, evidence = catalog
    clock = Clock()
    challenge = b"<html><title>Attention Required!</title><div id='cf-chl'>captcha</div></html>"
    transport = FakeTransport(clock, default=lambda pid, url: capture.Response(200, url, challenge))
    artifact, transport, _ = run(items, evidence, tmp_path=tmp_path, transport=transport, clock=clock)
    assert artifact["run"]["aborted_reason"] == "anti_bot_challenge"
    assert len(transport.calls) == 1


def test_request_budget_is_finite(catalog, tmp_path: Path) -> None:
    items, evidence = catalog
    artifact, transport, _ = run(items, evidence, tmp_path=tmp_path, max_items=5, max_requests=3)
    assert len(transport.calls) == 3
    assert artifact["run"]["aborted_reason"] == "request_budget_exhausted"
    assert artifact["result"] == "failed"


def test_deadline_stops_new_requests_without_failing(catalog, tmp_path: Path) -> None:
    items, evidence = catalog
    artifact, transport, _ = run(items, evidence, tmp_path=tmp_path, max_items=10, deadline_seconds=5)
    assert 0 < len(transport.calls) < 10
    assert artifact["coverage"]["not_attempted"] == 10 - len(transport.calls)
    assert artifact["result"] == "success"


def test_first_run_saves_raw_sample_and_later_runs_only_failures(catalog, tmp_path: Path) -> None:
    items, evidence = catalog
    artifact, _, _ = run(items, evidence, tmp_path=tmp_path, max_items=12)
    saved = sorted((tmp_path / "raw").glob("*.html.gz"))
    assert len(saved) == capture.FIRST_RUN_RAW_SAMPLE == artifact["run"]["raw_sample_saved"]
    pid = saved[0].name.split(".")[0]
    assert gzip.decompress(saved[0].read_bytes()) == synthetic_page(pid)

    fresh = {"1": "2026-10-01T00:00:00Z"}  # estado previo no vacío
    bad = items[0].product_id
    clock = Clock()
    transport = FakeTransport(clock, default=lambda p, u: capture.Response(200, u, synthetic_page(p, specs=p != bad)))
    later, _, _ = run(items, evidence, fresh, tmp_path=tmp_path / "later", transport=transport, clock=clock, max_items=5)
    assert later["run"]["raw_sample_saved"] == 0
    assert [path.name for path in (tmp_path / "later" / "raw").glob("*.html.gz")] == [f"{bad}.html.gz"]


def test_committed_authorization_is_valid_and_tampering_fails(tmp_path: Path) -> None:
    document = capture.load_authorization(AUTHORIZATION, now=NOW, delay=2.0, max_requests=1200)
    assert document["live_read_only_authorized"] is True
    assert document["allowed_url_prefix"] == "https://www.pricesmart.com/es-hn/producto/"
    with pytest.raises(capture.CaptureError, match="request_budget_above_authorization"):
        capture.load_authorization(AUTHORIZATION, now=NOW, delay=2.0, max_requests=1500)
    original = json.loads(AUTHORIZATION.read_text(encoding="utf-8"))
    for key, value in (
        ("live_read_only_authorized", False),
        ("allowed_url_prefix", "https://www.pricesmart.com/"),
        ("concurrency", 2),
        ("min_delay_seconds", 0.5),
        ("approved_at_utc", "2099-01-01T00:00:00Z"),
    ):
        tampered = dict(original, **{key: value})
        path = tmp_path / f"{key}.json"
        path.write_text(json.dumps(tampered), encoding="utf-8")
        with pytest.raises(capture.CaptureError):
            capture.load_authorization(path, now=NOW, delay=2.0, max_requests=100)
    extra = dict(original, extra_scope="all")
    path = tmp_path / "extra.json"
    path.write_text(json.dumps(extra), encoding="utf-8")
    with pytest.raises(capture.CaptureError, match="closed_set"):
        capture.load_authorization(path, now=NOW, delay=2.0, max_requests=100)


def test_state_file_contract(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"schema": STATE_SCHEMA, "fresh": {"415586": "2026-10-01T00:00:00Z"}}))
    assert capture.load_state(path) == {"415586": "2026-10-01T00:00:00Z"}
    path.write_text(json.dumps({"schema": "other", "fresh": {}}))
    with pytest.raises(capture.CaptureError, match="state_schema_invalid"):
        capture.load_state(path)
    assert capture.load_state(None) == {}


def test_cli_refuses_network_without_explicit_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "argv", [
        "obtener_especificaciones_pricesmart.py",
        "--authorization", str(AUTHORIZATION),
        "--snapshot", str(tmp_path / "x.json"),
        "--output", str(tmp_path / "out.json"),
    ])
    monkeypatch.setattr(capture, "urllib_transport", lambda url: pytest.fail("no network"))
    with pytest.raises(SystemExit, match="explicit_live_read_only_flag_required"):
        capture.main()
    monkeypatch.setattr(sys, "argv", sys.argv + ["--live-read-only", "--delay-seconds", "1.0"])
    with pytest.raises(SystemExit, match="delay_too_small"):
        capture.main()


def test_snapshot_not_accepted_is_rejected(tmp_path: Path) -> None:
    raw = json.loads(gzip.decompress((REPORT / "pricesmart_sps.json.gz").read_bytes()))
    raw["catalog_complete"] = False
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(capture.CaptureError):
        capture.load_catalog([path])


def test_artifact_contract_round_trips_into_persistence_rows(catalog, tmp_path: Path) -> None:
    from precios_supermercados.pricesmart_specs_persistence import rows_from_artifact

    items, evidence = catalog
    artifact, _, _ = run(items, evidence, tmp_path=tmp_path, max_items=3, github_run_id="42")
    assert artifact["schema"] == "precios-sps-pricesmart-specs/v1"
    rows = rows_from_artifact(json.loads(json.dumps(artifact)))
    assert len(rows) == 3
    assert all(row.values["capture_run_id"] == "42" for row in rows)
    assert all(row.values["net_weight_g"] == "500" for row in rows)
    assert artifact["generated_at_utc"] == NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
