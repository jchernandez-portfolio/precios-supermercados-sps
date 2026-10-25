#!/usr/bin/env python3
"""Construye una muestra de portafolio sólo con filas de publicación seguras.

El modo legado une ``publication.json`` con ``source-descriptors.json`` por
``source_record_id``. El modo RPI consume directamente ``rpi-consumer-mart/v2``,
que ya contiene los descriptores del universo seguro. Ningún modo hace matching.
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

PUBLICATION_SCHEMA = "precios-sps-publication/v1"
DESCRIPTOR_SCHEMA = "precios-sps-safe-source-descriptors/v1"
CONSUMER_SCHEMA = "rpi-consumer-mart/v2"
OUTPUT_SCHEMA = "precios-sps-safe-portfolio-sample/v1"
POLICY = "fail_closed_strong_identity_and_commercial_consistency"


class SampleError(ValueError):
    pass


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SampleError("sample_input_invalid") from exc
    if not isinstance(value, dict):
        raise SampleError("sample_input_not_object")
    return value


def _validate_limit(limit: int) -> None:
    if type(limit) is not int or limit < 1 or limit > 50:
        raise SampleError("sample_limit_invalid")


def _positive_money(value: object, *, error: str) -> str:
    if not isinstance(value, str):
        raise SampleError(error)
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise SampleError(error) from exc
    if not amount.is_finite() or amount <= 0:
        raise SampleError(error)
    return value


def _money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def _percentage(numerator: Decimal, denominator: Decimal) -> str:
    if denominator <= 0:
        raise SampleError("sample_percentage_denominator_invalid")
    return format(
        (numerator * Decimal(100) / denominator).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        ),
        "f",
    )


def _rank_rows(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    def rank(row: dict[str, Any]) -> tuple[Decimal, str, str]:
        return (
            -Decimal(str(row["savings_vs_highest"])),
            str(row["canonical_gtin"]),
            str(row["canonical_product_id"]),
        )

    rows.sort(key=rank)
    return rows[:limit]


def build_sample(publication: dict[str, Any], descriptors: dict[str, Any], *, limit: int) -> dict[str, Any]:
    """Build the established public sample from the legacy safe publication."""

    if publication.get("schema") != PUBLICATION_SCHEMA:
        raise SampleError("publication_schema_invalid")
    if descriptors.get("schema") != DESCRIPTOR_SCHEMA:
        raise SampleError("descriptor_schema_invalid")
    if publication.get("comparison_policy") != POLICY or descriptors.get("comparison_policy") != POLICY:
        raise SampleError("comparison_policy_invalid")
    _validate_limit(limit)

    offer_rows = publication.get("offers")
    product_rows = publication.get("products")
    descriptor_rows = descriptors.get("rows")
    if not isinstance(offer_rows, list) or not isinstance(product_rows, list) or not isinstance(descriptor_rows, list):
        raise SampleError("sample_rows_invalid")

    descriptor_by_source: dict[str, dict[str, Any]] = {}
    for row in descriptor_rows:
        if not isinstance(row, dict) or not isinstance(row.get("source_record_id"), str):
            raise SampleError("descriptor_row_invalid")
        source_id = row["source_record_id"]
        if source_id in descriptor_by_source:
            raise SampleError("descriptor_source_duplicate")
        descriptor_by_source[source_id] = row

    offers_by_product: dict[str, list[dict[str, Any]]] = {}
    gtin_by_product: dict[str, str] = {}
    safe_source_ids: set[str] = set()
    for offer in offer_rows:
        if not isinstance(offer, dict):
            raise SampleError("publication_offer_invalid")
        source_id = offer.get("source_record_id")
        canonical_id = offer.get("canonical_product_id")
        canonical_gtin = offer.get("canonical_gtin")
        supermarket_id = offer.get("supermarket_id")
        location_id = offer.get("location_id")
        if not all(isinstance(value, str) and value for value in (
            source_id,
            canonical_id,
            canonical_gtin,
            supermarket_id,
            location_id,
        )):
            raise SampleError("publication_offer_identity_invalid")
        if source_id in safe_source_ids:
            raise SampleError("publication_source_offer_duplicate")
        safe_source_ids.add(source_id)
        previous_gtin = gtin_by_product.setdefault(canonical_id, canonical_gtin)
        if previous_gtin != canonical_gtin:
            raise SampleError("publication_product_gtin_conflict")

        descriptor = descriptor_by_source.get(source_id)
        if descriptor is None:
            raise SampleError("descriptor_missing_for_safe_offer")
        if (
            descriptor.get("canonical_product_id") != canonical_id
            or descriptor.get("canonical_gtin") != canonical_gtin
            or descriptor.get("supermarket_id") != supermarket_id
        ):
            raise SampleError("descriptor_publication_identity_mismatch")
        if not isinstance(descriptor.get("source_name"), str) or not descriptor["source_name"].strip():
            raise SampleError("descriptor_source_name_invalid")
        current_price = _positive_money(offer.get("current_price"), error="publication_current_price_invalid")
        offers_by_product.setdefault(canonical_id, []).append({
            "supermarket_id": supermarket_id,
            "location_id": location_id,
            "source_record_id": source_id,
            "source_name": descriptor["source_name"].strip(),
            "source_brand": descriptor.get("source_brand"),
            "source_presentation": descriptor.get("source_presentation"),
            "source_category": descriptor.get("source_category"),
            "current_price": current_price,
            "is_best_price": offer.get("is_best_price") is True,
        })

    if set(descriptor_by_source) != safe_source_ids:
        raise SampleError("descriptor_set_not_exactly_safe_offers")

    rows: list[dict[str, Any]] = []
    seen_products: set[str] = set()
    for product in product_rows:
        if not isinstance(product, dict):
            raise SampleError("publication_product_invalid")
        canonical_id = product.get("canonical_product_id")
        canonical_gtin = product.get("canonical_gtin")
        if not isinstance(canonical_id, str) or not canonical_id or not isinstance(canonical_gtin, str) or not canonical_gtin:
            raise SampleError("publication_product_identity_invalid")
        if canonical_id in seen_products:
            raise SampleError("publication_product_duplicate")
        seen_products.add(canonical_id)
        if gtin_by_product.get(canonical_id) != canonical_gtin:
            raise SampleError("publication_product_gtin_conflict")
        offers = offers_by_product.get(canonical_id, [])
        expected_count = product.get("supermarket_count")
        if type(expected_count) is not int or len(offers) != expected_count or expected_count < 2:
            raise SampleError("safe_offer_count_mismatch")
        best_price = _positive_money(product.get("best_price"), error="publication_best_price_invalid")
        highest_price = _positive_money(product.get("highest_price"), error="publication_highest_price_invalid")
        savings = product.get("savings_vs_highest")
        if not isinstance(savings, str):
            raise SampleError("sample_savings_invalid")
        try:
            saving_amount = Decimal(savings)
        except InvalidOperation as exc:
            raise SampleError("sample_savings_invalid") from exc
        if not saving_amount.is_finite() or saving_amount < 0:
            raise SampleError("sample_savings_invalid")
        offers.sort(key=lambda item: (str(item["supermarket_id"]), str(item["location_id"])))
        rows.append({
            "canonical_product_id": canonical_id,
            "canonical_gtin": canonical_gtin,
            "best_supermarket_id": product.get("best_supermarket_id"),
            "best_price": best_price,
            "highest_price": highest_price,
            "savings_vs_highest": savings,
            "savings_vs_highest_pct": product.get("savings_vs_highest_pct"),
            "offers": offers,
        })

    if set(offers_by_product) != seen_products:
        raise SampleError("publication_offer_product_set_mismatch")

    selected = _rank_rows(rows, limit=limit)
    return {
        "schema": OUTPUT_SCHEMA,
        "comparison_policy": POLICY,
        "currency": publication.get("currency"),
        "scope": publication.get("scope"),
        "row_count": len(selected),
        "selection_rule": "highest_absolute_savings_within_safe_common_comparable_universe",
        "rows": selected,
    }


def build_sample_from_consumer(consumer: dict[str, Any], *, limit: int) -> dict[str, Any]:
    """Build the same public sample directly from the final Consumer Mart contract."""

    if consumer.get("schema") != CONSUMER_SCHEMA:
        raise SampleError("consumer_schema_invalid")
    if consumer.get("comparison_policy") != POLICY:
        raise SampleError("consumer_policy_invalid")
    if consumer.get("comparison_status") != "COMPARABLE":
        raise SampleError("consumer_comparison_not_available")
    if consumer.get("currency") != "HNL":
        raise SampleError("consumer_currency_invalid")
    _validate_limit(limit)

    products = consumer.get("products")
    scope = consumer.get("scope")
    if not isinstance(products, list) or not isinstance(scope, list):
        raise SampleError("consumer_rows_invalid")
    if consumer.get("product_count") != len(products):
        raise SampleError("consumer_product_count_mismatch")

    rows: list[dict[str, Any]] = []
    seen_products: set[str] = set()
    seen_sources: set[str] = set()
    for product in products:
        if not isinstance(product, dict):
            raise SampleError("consumer_product_invalid")
        canonical_id = product.get("canonical_product_id")
        canonical_gtin = product.get("canonical_gtin")
        offers = product.get("offers")
        recommended = product.get("recommended_source_product_ids")
        if not all(isinstance(value, str) and value for value in (canonical_id, canonical_gtin)):
            raise SampleError("consumer_product_identity_invalid")
        if canonical_id in seen_products:
            raise SampleError("consumer_product_duplicate")
        seen_products.add(canonical_id)
        if not isinstance(offers, list) or len(offers) < 2 or not isinstance(recommended, list):
            raise SampleError("consumer_offer_set_invalid")
        if not recommended or any(not isinstance(value, str) or not value for value in recommended):
            raise SampleError("consumer_recommendation_invalid")
        if len(set(recommended)) != len(recommended):
            raise SampleError("consumer_recommendation_duplicate")

        public_offers: list[dict[str, Any]] = []
        offer_by_source: dict[str, dict[str, Any]] = {}
        price_by_source: dict[str, Decimal] = {}
        for offer in offers:
            if not isinstance(offer, dict):
                raise SampleError("consumer_offer_invalid")
            source_id = offer.get("source_product_id")
            supermarket_id = offer.get("supermarket_id")
            location_id = offer.get("location_id")
            source_name = offer.get("product_name")
            if not all(isinstance(value, str) and value for value in (
                source_id, supermarket_id, location_id, source_name,
            )):
                raise SampleError("consumer_offer_identity_invalid")
            if offer.get("canonical_product_id") != canonical_id or offer.get("canonical_gtin") != canonical_gtin:
                raise SampleError("consumer_offer_product_mismatch")
            if source_id in seen_sources or source_id in offer_by_source:
                raise SampleError("consumer_source_offer_duplicate")
            seen_sources.add(source_id)
            current_price = _positive_money(
                offer.get("current_price"), error="consumer_current_price_invalid"
            )
            price = Decimal(current_price)
            offer_by_source[source_id] = offer
            price_by_source[source_id] = price
            public_offers.append({
                "supermarket_id": supermarket_id,
                "location_id": location_id,
                "source_record_id": source_id,
                "source_name": source_name.strip(),
                "source_brand": offer.get("brand"),
                "source_presentation": offer.get("presentation"),
                "source_category": offer.get("category"),
                "current_price": current_price,
                "is_best_price": offer.get("is_best_price") is True,
            })

        if any(source_id not in offer_by_source for source_id in recommended):
            raise SampleError("consumer_recommendation_unknown_offer")
        best_ids = {
            source_id for source_id, offer in offer_by_source.items()
            if offer.get("is_best_price") is True
        }
        if best_ids != set(recommended):
            raise SampleError("consumer_recommendation_best_set_mismatch")
        for source_id in recommended:
            offer = offer_by_source[source_id]
            if offer.get("difference_vs_best_abs") != "0.00" or offer.get("difference_vs_best_pct") != "0.00":
                raise SampleError("consumer_best_delta_invalid")
        best_prices = {price_by_source[source_id] for source_id in recommended}
        if len(best_prices) != 1:
            raise SampleError("consumer_best_price_tie_mismatch")
        best_price = next(iter(best_prices))
        if any(price < best_price for price in price_by_source.values()):
            raise SampleError("consumer_best_price_not_minimum")
        highest_price = max(price_by_source.values())
        savings = highest_price - best_price
        deterministic_best = min(
            (offer_by_source[source_id] for source_id in recommended),
            key=lambda offer: (
                str(offer["supermarket_id"]),
                str(offer["location_id"]),
                str(offer["source_product_id"]),
            ),
        )
        public_offers.sort(
            key=lambda item: (
                str(item["supermarket_id"]),
                str(item["location_id"]),
                str(item["source_record_id"]),
            )
        )
        rows.append({
            "canonical_product_id": canonical_id,
            "canonical_gtin": canonical_gtin,
            "best_supermarket_id": deterministic_best["supermarket_id"],
            "best_price": _money(best_price),
            "highest_price": _money(highest_price),
            "savings_vs_highest": _money(savings),
            "savings_vs_highest_pct": _percentage(savings, highest_price),
            "offers": public_offers,
        })

    selected = _rank_rows(rows, limit=limit)
    return {
        "schema": OUTPUT_SCHEMA,
        "comparison_policy": POLICY,
        "currency": consumer.get("currency"),
        "scope": scope,
        "row_count": len(selected),
        "selection_rule": "highest_absolute_savings_within_safe_common_comparable_universe",
        "source_schema": CONSUMER_SCHEMA,
        "source_as_of": consumer.get("as_of"),
        "rows": selected,
    }


def _write(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    source = result.add_mutually_exclusive_group(required=True)
    source.add_argument("--publication", type=Path)
    source.add_argument("--consumer-mart", type=Path)
    result.add_argument("--descriptors", type=Path)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--limit", type=int, default=10)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.consumer_mart is not None:
        if args.descriptors is not None:
            raise SampleError("consumer_mode_descriptors_forbidden")
        document = build_sample_from_consumer(_read(args.consumer_mart), limit=args.limit)
    else:
        if args.descriptors is None:
            raise SampleError("legacy_descriptors_required")
        document = build_sample(
            _read(args.publication),
            _read(args.descriptors),
            limit=args.limit,
        )
    _write(args.output, document)
    print(json.dumps({"schema": document["schema"], "row_count": document["row_count"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
