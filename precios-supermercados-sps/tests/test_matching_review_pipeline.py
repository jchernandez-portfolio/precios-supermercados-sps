"""Pipeline shadow end-to-end, cola de revisión, importador y golden set."""
from __future__ import annotations

import csv
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

from precios_supermercados.matching.config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_DECISIONS_PATH,
    DEFAULT_TAXONOMY_PATH,
    load_engine_config,
)
from precios_supermercados.matching.golden import (
    GoldenSetError,
    evaluate_golden,
    golden_csv,
    read_golden_csv,
    sample_golden_pairs,
)
from precios_supermercados.matching.pipeline import MatchingEngine, split_for_key
from precios_supermercados.matching.review import (
    CSV_FIELDS,
    ReviewImportError,
    decision_from_review_row,
    flatten_row,
    import_review_rows,
    merge_into_registry,
    read_review_csv,
)
from precios_supermercados.matching.taxonomy import load_source_taxonomy
from precios_supermercados.product_identity_decisions import load_reviewed_decisions

from _matching_fixtures import build_records

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "homologacion_motor_shadow.py"


def _fast_config(directory: Path) -> Path:
    """Config real con menos parejas aleatorias para u (pruebas rápidas)."""

    path = directory / "matching-engine-test.yaml"
    text = DEFAULT_CONFIG_PATH.read_text(encoding="utf-8").replace("u_random_pairs: 120000", "u_random_pairs: 2000")
    assert "u_random_pairs: 2000" in text
    path.write_text(text, encoding="utf-8")
    return path


def _load_cli():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("homologacion_motor_shadow", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["homologacion_motor_shadow"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def shadow_run(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    cli = _load_cli()
    out = tmp_path_factory.mktemp("shadow")
    registry_before = DEFAULT_DECISIONS_PATH.read_bytes()
    config = _fast_config(tmp_path_factory.mktemp("config"))
    summary = cli.run_shadow(build_records(), out, config_path=config, golden_size=40, generated_at_utc="2026-09-30T00:00:00Z")
    assert DEFAULT_DECISIONS_PATH.read_bytes() == registry_before
    return cli, out, summary, config


def test_split_is_deterministic_and_balanced() -> None:
    assert split_for_key("07411001889090") == split_for_key("07411001889090")
    splits = [split_for_key(f"{index:014d}") for index in range(2000)]
    assert 0.5 < splits.count("train") / 2000 < 0.7
    assert 0.1 < splits.count("test") / 2000 < 0.3


def test_shadow_run_outputs_are_private_and_complete(shadow_run) -> None:  # type: ignore[no-untyped-def]
    _, out, summary, _ = shadow_run
    assert summary["mode"] == "shadow"
    assert summary["public_serving_allowed"] is False
    assert summary["policy_gate"]["engine_publication_allowed"] is False
    for name in (
        "summary.json",
        "model.json",
        "model-em.json",
        "review-queue.jsonl",
        "review-queue.csv",
        "clusters.jsonl",
        "comparable-alternatives.jsonl",
        "golden-set.csv",
        "golden-set-silver-key.json",
        "leaf-type-map.json",
    ):
        assert (out / name).exists(), name
    persisted = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert persisted["engine_version"] == "matching-engine-v1"
    city = persisted["cities"]["SPS"]
    assert city["silver_labels"]["positive_pairs"] > 0
    assert city["coverage"]["projected_comparable_records"] >= city["coverage"]["current_comparable_records"]
    assert city["coverage"]["projected_retailer_collision_components"] == 0
    clusters = [json.loads(line) for line in (out / "clusters.jsonl").read_text(encoding="utf-8").splitlines()]
    assert clusters and all(row["public_serving_allowed"] is False for row in clusters)
    for row in clusters:
        assert len(row["contexts"]) == len(set(row["contexts"]))
    header, *rows = (out / "review-queue.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(header)["metadata"]["public_serving_allowed"] is False
    for line in rows:
        row = json.loads(line)
        assert row["band"] in {"auto_match", "review"}
        assert row["waterfall"][0]["field"] == "prior"
        assert row["left"]["evidence_fingerprint"] and len(row["left"]["evidence_fingerprint"]) == 64


def test_shadow_run_is_deterministic(shadow_run, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    cli, out, summary, config = shadow_run
    again = cli.run_shadow(build_records(), tmp_path, config_path=config, golden_size=40, generated_at_utc="2026-09-30T00:00:00Z")
    assert again["thresholds"] == summary["thresholds"]
    assert (tmp_path / "clusters.jsonl").read_text(encoding="utf-8") == (out / "clusters.jsonl").read_text(encoding="utf-8")
    assert (tmp_path / "golden-set.csv").read_text(encoding="utf-8") == (out / "golden-set.csv").read_text(encoding="utf-8")


def test_engine_scores_gtin_pairs_by_rule(tmp_path: Path) -> None:
    config = load_engine_config(_fast_config(tmp_path))
    engine = MatchingEngine(config, load_source_taxonomy(DEFAULT_TAXONOMY_PATH))
    run = engine.prepare_city("SPS", build_records())
    model = engine.train([run])
    thresholds = engine.calibrate([run], model)
    engine.score_city(run, model, thresholds)
    gtin_pairs = [scored for scored in run.scored.values() if scored.decision_source == "gtin_rule"]
    assert gtin_pairs and all(scored.band == "auto_match" and scored.probability == 1.0 for scored in gtin_pairs)
    evaluation = engine.evaluate([run], model, thresholds, "test")
    assert evaluation["split"] == "test"
    assert evaluation["blocking_recall_gtin_blind"] is None or 0 <= evaluation["blocking_recall_gtin_blind"] <= 1


# -- importador ------------------------------------------------------------------
def _review_row(shadow_run, **overrides: str) -> dict[str, str]:  # type: ignore[no-untyped-def]
    _, out, _, _ = shadow_run
    rows = read_review_csv((out / "review-queue.csv").read_text(encoding="utf-8"))
    assert rows, "la cola de revisión del fixture no debe estar vacía"
    row = dict(rows[0])
    row["left_fingerprint_authority"] = "turso_products"
    row["right_fingerprint_authority"] = "turso_products"
    row.update(overrides)
    return row


def test_import_rejects_offline_fingerprints_and_weak_identity_evidence(shadow_run) -> None:  # type: ignore[no-untyped-def]
    row = _review_row(shadow_run, review_decision="same_product", reviewed_by="ana", rationale="ok")
    offline = dict(row, left_fingerprint_authority="fixture")
    with pytest.raises(ReviewImportError, match="fingerprint_authority_not_registry_grade"):
        decision_from_review_row(offline, now_utc="2026-09-30T00:00:00Z")
    with pytest.raises(ReviewImportError, match="approved_identity_evidence_insufficient|decision_evidence_codes_invalid"):
        decision_from_review_row(row, now_utc="2026-09-30T00:00:00Z")
    assert decision_from_review_row(dict(row, review_decision=""), now_utc="2026-09-30T00:00:00Z") is None
    with pytest.raises(ReviewImportError):
        decision_from_review_row(dict(row, review_decision="quizas"), now_utc="2026-09-30T00:00:00Z")


def test_import_builds_registry_compatible_decisions(shadow_run, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    approved = _review_row(
        shadow_run,
        review_decision="same_product",
        evidence_codes="human_verified;barcode_visible",
        evidence_references="foto-empaque-123",
        rationale="Mismo código de barras en el empaque.",
        reviewed_by="ana",
        reviewed_at_utc="2026-09-30T12:00:00Z",
    )
    result = import_review_rows([approved, dict(approved)], now_utc="2026-09-30T13:00:00Z")
    assert len(result.decisions) == 1 and result.errors[0]["error"] == "review_duplicate_candidate"
    decision = result.decisions[0]
    assert decision.relation in {"VERIFIED_EQUIVALENT", "EXACT_TRADE_ITEM"}
    assert decision.master_product_id is not None and decision.master_product_id.startswith("prod_")
    registry = tmp_path / "reviewed-decisions-v1.json"
    registry.write_text(DEFAULT_DECISIONS_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    assert merge_into_registry(registry, result.decisions) == {"added": 1, "replaced": 0, "kept_existing": 0, "total": 1}
    assert load_reviewed_decisions(registry)[0].candidate_id == decision.candidate_id
    assert merge_into_registry(registry, result.decisions)["kept_existing"] == 1
    conflict = import_review_rows(
        [dict(approved, review_decision="different_products", evidence_codes="")],
        now_utc="2026-09-30T13:00:00Z",
    )
    assert conflict.decisions[0].relation == "CONFLICT"
    assert conflict.decisions[0].evidence_codes == ("human_verified", "source_title")
    pending = import_review_rows([dict(approved, review_decision="pending")], now_utc="2026-09-30T13:00:00Z")
    assert pending.decisions[0].status == "pending" and pending.decisions[0].relation == "UNRESOLVED"


def test_cli_import_review_is_dry_run_by_default(shadow_run, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:  # type: ignore[no-untyped-def]
    cli, _, _, _ = shadow_run
    row = _review_row(shadow_run, review_decision="variant", rationale="Cambia el sabor.", reviewed_by="ana")
    reviewed = tmp_path / "reviewed.csv"
    with reviewed.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    before = DEFAULT_DECISIONS_PATH.read_bytes()
    assert cli.main(["import-review", "--reviewed", str(reviewed), "--output", str(tmp_path / "decisions.json")]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["registry"] == "dry_run" and report["decisions"] == 1
    assert report["public_serving_allowed"] is False
    assert DEFAULT_DECISIONS_PATH.read_bytes() == before
    registry = tmp_path / "registry.json"
    registry.write_text(before.decode("utf-8"), encoding="utf-8")
    assert cli.main(["import-review", "--reviewed", str(reviewed), "--registry", str(registry), "--write-registry"]) == 0
    capsys.readouterr()
    assert load_reviewed_decisions(registry)[0].relation == "PRODUCT_VARIANT"


def test_flatten_row_matches_csv_header(shadow_run) -> None:  # type: ignore[no-untyped-def]
    _, out, _, _ = shadow_run
    header, *rows = (out / "review-queue.jsonl").read_text(encoding="utf-8").splitlines()
    flat = flatten_row(json.loads(rows[0]))
    assert tuple(flat) == CSV_FIELDS
    assert "prior:lambda" in flat["waterfall"]
    with pytest.raises(ReviewImportError):
        read_review_csv("a,b\n1,2\n")


# -- golden set --------------------------------------------------------------------
def _queue_rows(shadow_run) -> list[dict[str, object]]:  # type: ignore[no-untyped-def]
    _, out, _, _ = shadow_run
    return [json.loads(line) for line in (out / "review-queue.jsonl").read_text(encoding="utf-8").splitlines()[1:]]


def test_golden_sampling_is_stratified_and_deterministic(shadow_run) -> None:  # type: ignore[no-untyped-def]
    rows = _queue_rows(shadow_run)
    first = sample_golden_pairs(rows, size=10, seed=1)
    second = sample_golden_pairs(rows, size=10, seed=1)
    assert [item["row"]["candidate_id"] for item in first] == [item["row"]["candidate_id"] for item in second]
    assert len(first) <= 10
    for item in first:
        assert item["stratum_sample"] <= item["stratum_population"]
    text = golden_csv(first)
    parsed = read_golden_csv(text)
    assert len(parsed) == len(first) and all(row["label"] == "" for row in parsed)


def test_golden_evaluation_metrics() -> None:
    rows = [
        {"golden_id": "g1", "band": "auto_match", "label": "match", "stratum_band": "auto_match", "stratum_retailer_pair": "a|b", "stratum_population": "100", "stratum_sample": "2"},
        {"golden_id": "g2", "band": "auto_match", "label": "no_match", "stratum_band": "auto_match", "stratum_retailer_pair": "a|b", "stratum_population": "100", "stratum_sample": "2"},
        {"golden_id": "g3", "band": "review", "label": "match", "stratum_band": "review", "stratum_retailer_pair": "a|c", "stratum_population": "10", "stratum_sample": "1"},
        {"golden_id": "g4", "band": "non_match", "label": "unsure", "stratum_band": "non_match", "stratum_retailer_pair": "a|c", "stratum_population": "10", "stratum_sample": "1"},
    ]
    report = evaluate_golden(rows)
    assert report["labelled_rows"] == 3 and report["unlabelled_or_unsure_rows"] == 1
    overall = report["overall"]
    assert overall["bands"]["auto_match"]["precision"] == 0.5
    assert overall["auto_match_classifier"]["recall"] == 0.5
    assert overall["auto_match_weighted"]["recall_within_candidates"] == pytest.approx(50 / 60, abs=1e-3)
    assert report["by_retailer_pair"]["a|c"]["bands"]["review"]["matches"] == 1
    bad = io.StringIO()
    writer = csv.DictWriter(bad, fieldnames=["golden_id", "stratum_band", "stratum_retailer_pair", "stratum_population", "stratum_sample", "band", "label"])
    writer.writeheader()
    writer.writerow({"golden_id": "g", "stratum_band": "review", "stratum_retailer_pair": "a|b", "stratum_population": "1", "stratum_sample": "1", "band": "review", "label": "si"})
    with pytest.raises(GoldenSetError):
        read_golden_csv(bad.getvalue())


def test_cli_evaluate_golden(shadow_run, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:  # type: ignore[no-untyped-def]
    cli, out, _, _ = shadow_run
    rows = read_golden_csv((out / "golden-set.csv").read_text(encoding="utf-8"))
    for row in rows:
        row["label"] = "match" if row["band"] == "auto_match" else "no_match"
    labelled = tmp_path / "golden.csv"
    with labelled.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    assert cli.main(["evaluate-golden", "--golden", str(labelled), "--output", str(tmp_path / "eval.json")]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["labelled_rows"] == len(rows)
    assert (tmp_path / "eval.json").exists()


def test_records_from_turso_assigns_city_and_registry_grade_authority() -> None:
    cli = _load_cli()
    from precios_supermercados.product_homologation import SourceProductRecord

    products = (
        (1, SourceProductRecord("walmart:1", "walmart", "Jugo Sula Naranja - 1890 ml", barcode="7411001889090")),
        (2, SourceProductRecord("walmart:2", "walmart", "Jugo Sula Naranja - 1890 ml")),
    )
    records = cli.records_from_turso(products, {1: {"walmart_sps"}, 2: {"walmart_tgu_ffaa", "walmart_tgu_el_sauce"}}, {"walmart_sps": "SPS", "walmart_tgu_ffaa": "TGU", "walmart_tgu_el_sauce": "TGU"})
    assert [(record.source_record_id, record.city, record.location_ids) for record in records] == [
        ("walmart:1", "SPS", ("walmart_sps",)),
        ("walmart:2", "TGU", ("walmart_tgu_el_sauce", "walmart_tgu_ffaa")),
    ]
    assert {record.fingerprint_authority for record in records} == {"turso_products"}


def test_silver_labels_use_hard_negatives_and_skip_duplicate_listings(tmp_path: Path) -> None:
    from precios_supermercados.matching.records import MatchRecord

    def record(record_id: str, name: str, gtin: str | None, brand: str = "Sula") -> MatchRecord:
        supermarket = record_id.split(":")[0]
        return MatchRecord(
            source_record_id=record_id,
            supermarket_id=supermarket,
            city="SPS",
            source_name=name,
            source_brand=brand,
            silver_gtin=gtin,
            location_ids=(f"{supermarket}_sps",),
        )

    records = build_records() + [
        # Walmart lista dos veces el mismo jugo con GTIN distinto (origen distinto).
        record("walmart:900", "Jugo Sula Naranja Premium - 1890 ml", "7400000009004"),
        record("walmart:901", "Jugo de Naranja Sula Premium 1890 ml", "7400000009011"),
        record("la_colonia:900", "Jugo Sula Naranja Premium 1890 Ml", "7400000009004"),
        # Otro sabor de la misma marca con su propio GTIN: negativo duro válido.
        record("walmart:902", "Jugo Sula Toronja - 1890 ml", "7400000009028"),
    ]
    engine = MatchingEngine(load_engine_config(_fast_config(tmp_path)), load_source_taxonomy(DEFAULT_TAXONOMY_PATH))
    run = engine.prepare_city("SPS", records)
    assert run.labels[("la_colonia:900", "walmart:900")].is_match
    assert ("la_colonia:900", "walmart:901") not in run.labels
    if ("la_colonia:900", "walmart:902") in run.vectors:
        assert run.labels[("la_colonia:900", "walmart:902")].is_match is False
