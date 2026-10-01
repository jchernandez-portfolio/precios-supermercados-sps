#!/usr/bin/env python3
"""Motor de homologación probabilístico en modo SHADOW (manual, no diario).

Subcomandos:

``run``            estandariza, bloquea, compara, entrena Fellegi–Sunter con
                   etiquetas silver GTIN, elige umbrales precision-first,
                   agrupa con restricciones y escribe salidas privadas
                   (resumen, modelo, cola de revisión CSV/JSONL, clusters,
                   alternativas por precio unitario y conjunto golden).
``evaluate-golden`` evalúa un CSV golden etiquetado por humanos.
``import-review``  convierte una cola revisada a decisiones del registro
                   ``reviewed-decisions-v1.json`` (dry-run por defecto).

Nunca modifica catálogos, marts ni comparabilidad publicada. Entradas: un
JSONL ``precios-sps-matching-record/v1`` (``--records``) o Turso
(``--turso``, mismas credenciales de solo lectura que la exportación de
revisión).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from precios_supermercados.matching import MATCHING_ENGINE_VERSION, MATCHING_OUTPUT_SCHEMA  # noqa: E402
from precios_supermercados.matching.config import (  # noqa: E402
    DEFAULT_CONFIG_PATH,
    DEFAULT_DECISIONS_PATH,
    DEFAULT_POLICY_PATH,
    DEFAULT_TAXONOMY_PATH,
    load_engine_config,
)
from precios_supermercados.matching.golden import (  # noqa: E402
    evaluate_golden,
    golden_csv,
    read_golden_csv,
    sample_golden_pairs,
)
from precios_supermercados.matching.pipeline import (  # noqa: E402
    CityRun,
    MatchingEngine,
    model_report,
    pair_candidate_id,
    retailer_pair,
    summarize_metrics,
)
from precios_supermercados.matching.policy import load_policy_gate  # noqa: E402
from precios_supermercados.matching.records import MatchRecord, read_records, split_by_city  # noqa: E402
from precios_supermercados.matching.review import (  # noqa: E402
    decision_to_mapping,
    import_review_rows,
    merge_into_registry,
    read_review_csv,
    review_metadata,
    review_row,
    write_review_queue,
)
from precios_supermercados.matching.taxonomy import load_source_taxonomy  # noqa: E402

DEFAULT_REVIEW_LIMIT = 20000


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _write_jsonl(path: Path, rows: Iterable[object]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


def _cluster_lookup(run: CityRun) -> dict[str, str]:
    return {member: cluster.cluster_id for cluster in run.clusters for member in cluster.members}


def _review_rows_for_city(  # type: ignore[no-untyped-def]
    engine: MatchingEngine,
    run: CityRun,
    model,
    *,
    include_bands: set[str],
    selected: set[tuple[str, str]] | None = None,
) -> list[dict[str, object]]:
    published: dict[str, str] = {
        record.source_record_id: record.record.published_canonical_product_id
        for record in run.records
        if record.record.published_canonical_product_id
    }
    clusters = _cluster_lookup(run)
    rows = []
    for key, scored in run.scored.items():
        if scored.vector.same_retailer or scored.decision_source == "gtin_rule":
            continue
        if scored.band not in include_bands:
            continue
        if selected is not None and scored.band in {"auto_match", "review"} and key not in selected:
            continue
        if published.get(key[0]) is not None and published.get(key[0]) == published.get(key[1]):
            continue
        cluster = clusters.get(key[0]) if clusters.get(key[0]) == clusters.get(key[1]) else None
        rows.append(
            review_row(
                city=run.city,
                left=run.by_id[key[0]],
                right=run.by_id[key[1]],
                vector=scored.vector,
                model=model,
                band=scored.band,
                decision_source=scored.decision_source,
                probability=scored.probability,
                weight=scored.weight,
                cluster_id=cluster,
            )
        )
    rows.sort(key=lambda row: (-float(row["match_probability"]), str(row["candidate_id"])))  # type: ignore[arg-type]
    return rows


def _golden_entries(run: CityRun, selected: set[tuple[str, str]]) -> list[dict[str, object]]:
    """Entradas livianas para muestreo golden (la fila completa se arma después)."""

    entries = []
    for key, scored in run.scored.items():
        if scored.vector.same_retailer or scored.decision_source in {"gtin_rule", "gtin_rule_with_conflict"}:
            continue
        if scored.band in {"auto_match", "review"} and key not in selected:
            continue
        if scored.band == "non_match" and (scored.vector.hard_conflicts or scored.probability < 0.01):
            continue
        entries.append(
            {
                "_key": key,
                "city": run.city,
                "band": scored.band,
                "match_probability": scored.probability,
                "candidate_id": pair_candidate_id(key),
                "left": {"supermarket_id": run.by_id[key[0]].supermarket_id},
                "right": {"supermarket_id": run.by_id[key[1]].supermarket_id},
            }
        )
    return entries


def _examples(rows: Sequence[dict[str, object]], band: str, limit: int = 15) -> list[dict[str, object]]:
    selected = [row for row in rows if row["band"] == band][:limit]
    return [
        {
            "p": row["match_probability"],
            "left": f"{row['left']['supermarket_id']}: {row['left']['source_name']}",  # type: ignore[index]
            "right": f"{row['right']['supermarket_id']}: {row['right']['source_name']}",  # type: ignore[index]
        }
        for row in selected
    ]


def run_shadow(
    records: Sequence[MatchRecord],
    out_dir: Path,
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    taxonomy_path: Path = DEFAULT_TAXONOMY_PATH,
    policy_path: Path = DEFAULT_POLICY_PATH,
    golden_size: int = 600,
    review_limit: int = DEFAULT_REVIEW_LIMIT,
    generated_at_utc: str | None = None,
    log: Callable[[str], None] = lambda message: None,
) -> dict[str, object]:
    generated = generated_at_utc or _utc_now()
    config = load_engine_config(config_path)
    taxonomy = load_source_taxonomy(taxonomy_path)
    gate = load_policy_gate(policy_path)
    engine = MatchingEngine(config, taxonomy)
    timings: dict[str, float] = {}

    runs: list[CityRun] = []
    for city, city_records in split_by_city(records).items():
        started = time.monotonic()
        runs.append(engine.prepare_city(city, city_records))
        timings[f"prepare_{city}"] = round(time.monotonic() - started, 2)
        log(f"prepared {city}: {len(city_records)} records, {len(runs[-1].candidates)} candidate pairs")

    started = time.monotonic()
    model = engine.train(runs)
    em_model = engine.train_em_variant(runs, model)
    thresholds = engine.calibrate(runs, model)
    em_thresholds = engine.calibrate(runs, em_model)
    evaluation = engine.evaluate(runs, model, thresholds, "test")
    em_evaluation = engine.evaluate(runs, em_model, em_thresholds, "test")
    timings["train_calibrate_evaluate"] = round(time.monotonic() - started, 2)
    log(f"thresholds {thresholds.to_json()}")

    all_review_rows: list[dict[str, object]] = []
    golden_pool: list[dict[str, object]] = []
    city_summaries = {}
    examples = {}
    for run in runs:
        started = time.monotonic()
        engine.score_city(run, model, thresholds)
        timings[f"score_{run.city}"] = round(time.monotonic() - started, 2)
        city_summaries[run.city] = engine.city_summary(run)
        selected = engine.review_selection(run)
        review_rows = _review_rows_for_city(engine, run, model, include_bands={"auto_match", "review"}, selected=selected)
        all_review_rows.extend(review_rows)
        golden_pool.extend(_golden_entries(run, selected))
        examples[run.city] = {
            "auto_match": _examples(review_rows, "auto_match"),
            "review": _examples(review_rows, "review"),
        }

    gate_json = gate.to_json()
    metadata = review_metadata(generated_at_utc=generated, thresholds=thresholds.to_json(), policy_gate=gate_json)
    queue = all_review_rows[:review_limit]
    write_review_queue(queue, out_dir / "review-queue.jsonl", out_dir / "review-queue.csv", metadata=metadata)

    clusters_written = _write_jsonl(
        out_dir / "clusters.jsonl",
        (
            {
                "city": run.city,
                "cluster_id": cluster.cluster_id,
                "members": list(cluster.members),
                "contexts": list(cluster.contexts),
                "supermarkets": sorted({run.by_id[item].supermarket_id for item in cluster.members}),
                "gtin_only": cluster.gtin_only,
                "relation": "EXACT_TRADE_ITEM" if cluster.gtin_only else "PROBABLE_EQUIVALENT_SHADOW",
                "public_serving_allowed": False,
            }
            for run in runs
            for cluster in run.clusters
        ),
    )
    alternatives_written = _write_jsonl(
        out_dir / "comparable-alternatives.jsonl",
        ({"city": run.city, **row} for run in runs for row in run.alternatives),
    )
    golden = sample_golden_pairs(golden_pool, size=golden_size)
    runs_by_city = {run.city: run for run in runs}
    for item in golden:
        entry = item["row"]
        run = runs_by_city[str(entry["city"])]
        key = entry["_key"]
        scored = run.scored[key]  # type: ignore[index]
        item["row"] = review_row(
            city=run.city,
            left=run.by_id[key[0]],  # type: ignore[index]
            right=run.by_id[key[1]],  # type: ignore[index]
            vector=scored.vector,
            model=model,
            band=scored.band,
            decision_source=scored.decision_source,
            probability=scored.probability,
            weight=scored.weight,
        )
    (out_dir / "golden-set.csv").write_text(golden_csv(golden), encoding="utf-8")
    silver_key = {}
    labels_by_city = {run.city: run.labels for run in runs}
    for item in golden:
        row = item["row"]
        city_labels = labels_by_city[str(row["city"])]
        left_id, right_id = row["left"]["source_record_id"], row["right"]["source_record_id"]  # type: ignore[index]
        key = (left_id, right_id) if left_id <= right_id else (right_id, left_id)
        label = city_labels.get(key)
        silver_key[str(row["candidate_id"])] = None if label is None else ("match" if label.is_match else "no_match")
    _write_json(out_dir / "golden-set-silver-key.json", silver_key)
    _write_json(out_dir / "model.json", model.to_json())
    _write_json(out_dir / "model-em.json", em_model.to_json())
    _write_json(
        out_dir / "leaf-type-map.json",
        {run.city: run.leaf_types for run in runs},
    )

    summary = {
        "schema": MATCHING_OUTPUT_SCHEMA,
        "engine_version": MATCHING_ENGINE_VERSION,
        "generated_at_utc": generated,
        "mode": "shadow",
        "public_serving_allowed": False,
        "policy_gate": gate_json,
        "records": len(records),
        "thresholds": thresholds.to_json(),
        "model": model_report(model),
        "evaluation_test": evaluation,
        "evaluation_test_summary": summarize_metrics(evaluation),
        "em_variant": {
            "thresholds": em_thresholds.to_json(),
            "evaluation_test_summary": summarize_metrics(em_evaluation),
            "prior_lambda": em_model.prior,
        },
        "cities": city_summaries,
        "examples": examples,
        "outputs": {
            "review_queue_rows": len(queue),
            "review_queue_rows_total": len(all_review_rows),
            "clusters": clusters_written,
            "comparable_alternatives": alternatives_written,
            "golden_set_rows": len(golden),
        },
        "timings_seconds": timings,
    }
    _write_json(out_dir / "summary.json", summary)
    return summary


# -- Turso (solo lectura) ------------------------------------------------------
_CITY_CODES = {"san pedro sula": "SPS", "tegucigalpa": "TGU"}


def load_turso_records(url: str, token: str) -> list[MatchRecord]:
    """Lee ``products`` + contextos (``price_history``/``locations``) de Turso."""

    from backfill_homologacion_turso import _fetch_products, _query  # noqa: PLC0415

    from precios_supermercados.product_homologation import fold_text  # noqa: PLC0415

    products = _fetch_products(url, token)
    city_by_location = {
        str(location_id): _CITY_CODES.get(fold_text(str(city)) or "", fold_text(str(city)) or "unknown")
        for location_id, city in _query(url, token, "SELECT location_id, city_name FROM locations")
    }
    contexts: dict[int, set[str]] = defaultdict(set)
    cursor = 0
    while True:
        rows = _query(
            url,
            token,
            "SELECT product_id, location_id FROM price_history WHERE product_id>? "
            "GROUP BY product_id, location_id ORDER BY product_id LIMIT 5000",
            (cursor,),
        )
        if not rows:
            break
        for product_id, location_id in rows:
            contexts[int(product_id)].add(str(location_id))
        cursor = int(rows[-1][0])
        if len(rows) < 5000:
            break
    return records_from_turso(products, contexts, city_by_location)


def records_from_turso(products, contexts, city_by_location) -> list[MatchRecord]:  # type: ignore[no-untyped-def]
    records: list[MatchRecord] = []
    for product_id, source in products:
        by_city: dict[str, list[str]] = defaultdict(list)
        for location in sorted(contexts.get(product_id, ())):
            by_city[city_by_location.get(location, "unknown")].append(location)
        for city, locations in sorted(by_city.items()):
            records.append(
                MatchRecord(
                    source_record_id=source.source_record_id,
                    supermarket_id=source.supermarket_id,
                    city=city,
                    source_name=source.source_name,
                    source_brand=source.source_brand,
                    source_presentation=source.source_presentation,
                    source_category=source.source_category,
                    barcode=source.barcode,
                    location_ids=tuple(locations),
                    fingerprint_authority="turso_products",
                )
            )
    return records


# -- CLI -------------------------------------------------------------------------
def _cmd_run(args: argparse.Namespace) -> int:
    if args.turso:
        url = os.environ.get("TURSO_DATABASE_URL", "")
        token = os.environ.get("TURSO_AUTH_TOKEN", "")
        if not url.strip() or not token.strip():
            raise SystemExit("turso_credentials_missing")
        records = load_turso_records(url, token)
    elif args.records is not None:
        records = list(read_records(args.records))
    else:
        raise SystemExit("records_or_turso_required")
    if args.cities:
        wanted = set(args.cities.split(","))
        records = [record for record in records if record.city in wanted]
    summary = run_shadow(
        records,
        args.output_dir,
        config_path=args.config,
        taxonomy_path=args.taxonomy,
        policy_path=args.policy,
        golden_size=args.golden_size,
        review_limit=args.review_limit,
        log=lambda message: print(message, file=sys.stderr, flush=True),
    )
    print(
        json.dumps(
            {
                "summary": str(args.output_dir / "summary.json"),
                "thresholds": summary["thresholds"],
                "evaluation_test_summary": summary["evaluation_test_summary"],
                "cities": {
                    city: {
                        "coverage": value["coverage"],  # type: ignore[index]
                        "new_vs_published": value["new_vs_published"],  # type: ignore[index]
                    }
                    for city, value in summary["cities"].items()  # type: ignore[union-attr]
                },
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    return 0


def _cmd_evaluate_golden(args: argparse.Namespace) -> int:
    rows = read_golden_csv(args.golden.read_text(encoding="utf-8"))
    report = evaluate_golden(rows)
    rendered = json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True)
    if args.output is not None:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


def _cmd_import_review(args: argparse.Namespace) -> int:
    rows = read_review_csv(args.reviewed.read_text(encoding="utf-8"))
    result = import_review_rows(rows, allow_offline_fingerprints=args.allow_offline_fingerprints)
    gate = load_policy_gate(args.policy)
    report: dict[str, object] = {
        "decisions": len(result.decisions),
        "skipped": result.skipped,
        "errors": list(result.errors),
        "policy_gate": gate.to_json(),
        "public_serving_allowed": False,
    }
    if args.output is not None:
        _write_json(
            args.output,
            {"decisions": [decision_to_mapping(decision) for decision in result.decisions]},
        )
    if args.write_registry:
        if result.errors and not args.allow_partial:
            report["registry"] = "not_written_errors_present"
            print(json.dumps(report, ensure_ascii=False, indent=1))
            return 1
        report["registry"] = merge_into_registry(args.registry, result.decisions, replace_existing=args.replace_existing)
    else:
        report["registry"] = "dry_run"
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if not result.errors else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="ejecuta el motor shadow")
    source = run.add_mutually_exclusive_group(required=True)
    source.add_argument("--records", type=Path, help="JSONL precios-sps-matching-record/v1")
    source.add_argument("--turso", action="store_true", help="lee products de Turso (solo lectura)")
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--cities", help="filtra ciudades, p. ej. SPS,TGU")
    run.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    run.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY_PATH)
    run.add_argument("--policy", type=Path, default=DEFAULT_POLICY_PATH)
    run.add_argument("--golden-size", type=int, default=600)
    run.add_argument("--review-limit", type=int, default=DEFAULT_REVIEW_LIMIT)
    run.set_defaults(handler=_cmd_run)

    golden = sub.add_parser("evaluate-golden", help="evalúa un golden set etiquetado")
    golden.add_argument("--golden", type=Path, required=True)
    golden.add_argument("--output", type=Path)
    golden.set_defaults(handler=_cmd_evaluate_golden)

    review = sub.add_parser("import-review", help="convierte revisión humana en decisiones")
    review.add_argument("--reviewed", type=Path, required=True, help="review-queue.csv con columnas de revisor llenas")
    review.add_argument("--output", type=Path, help="escribe las decisiones convertidas (JSON)")
    review.add_argument("--registry", type=Path, default=DEFAULT_DECISIONS_PATH)
    review.add_argument("--policy", type=Path, default=DEFAULT_POLICY_PATH)
    review.add_argument("--write-registry", action="store_true", help="fusiona en el registro privado")
    review.add_argument("--replace-existing", action="store_true")
    review.add_argument("--allow-partial", action="store_true")
    review.add_argument(
        "--allow-offline-fingerprints",
        action="store_true",
        help="sólo pruebas: acepta huellas reconstruidas offline (quedarán obsoletas frente a Turso)",
    )
    review.set_defaults(handler=_cmd_import_review)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
