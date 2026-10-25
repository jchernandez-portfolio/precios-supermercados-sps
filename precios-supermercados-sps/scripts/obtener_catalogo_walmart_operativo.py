#!/usr/bin/env python3
"""Captura read-only completa de los tres contextos Walmart Honduras demostrados."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from precios_supermercados.scrapers.walmart import SCOPE, STORES, parse_products, verify_stores

BASE = "https://www.walmart.com.hn"
COUNTRY = "HND"
SALES_CHANNEL = "1"
PAGE_SIZE = 100
RECOVERY_PAGE_SIZE = 50
MAX_SEARCH_PAGES = 50
DEFAULT_DELAY = 1.0
DEFAULT_MAX_RETRIES = 2
MAX_RETRIES_HARD = 2
MAX_REQUESTS_HARD = 700
FINAL_DRIFT_STABILIZATION_DELAYS = (120.0, 600.0)
FINAL_DRIFT_CONFIRMATION_DELAY = 60.0
RETRYABLE_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})
UA = "Mozilla/5.0 (compatible; PreciosSupermercadosSPS-Walmart/1.0; read-only)"
CATEGORY2_PARTITIONS = {"articulos-para-el-hogar", "ropa-y-zapateria"}


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def region_id(seller: str) -> str:
    return base64.b64encode(("SW#" + seller).encode()).decode()


def facet_counts(doc: dict, key: str) -> dict[str, int]:
    facets = doc.get("facets")
    if not isinstance(facets, list):
        raise RuntimeError("facets_missing")
    matches = [facet for facet in facets if isinstance(facet, dict) and facet.get("key") == key]
    if len(matches) != 1 or not isinstance(matches[0].get("values"), list):
        raise RuntimeError(f"{key}_missing_or_ambiguous")
    result: dict[str, int] = {}
    for value in matches[0]["values"]:
        if not isinstance(value, dict):
            raise RuntimeError(f"{key}_value_invalid")
        name, quantity = value.get("value"), value.get("quantity")
        if not isinstance(name, str) or not name or type(quantity) is not int or quantity <= 0 or name in result:
            raise RuntimeError(f"{key}_value_invalid")
        result[name] = quantity
    if not result:
        raise RuntimeError(f"{key}_empty")
    return result


def validate_context_url(url: str, seller: str) -> None:
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    if parsed.scheme != "https" or parsed.netloc != "www.walmart.com.hn":
        raise RuntimeError("origin_not_allowed")
    if f"/accesscontrollist/{seller}" not in parsed.path:
        raise RuntimeError("selector_scope_changed")
    if query.get("regionId") != [region_id(seller)]:
        raise RuntimeError("region_scope_changed")
    if query.get("sc") != [SALES_CHANNEL] or query.get("country") != [COUNTRY]:
        raise RuntimeError("commercial_scope_changed")


def _retryable(status: int | None, error: str | None) -> bool:
    if status in RETRYABLE_HTTP_STATUSES:
        return True
    if status is not None or not error:
        return False
    return error.startswith(("URLError:", "TimeoutError:", "ConnectionError:", "OSError:"))


class Capture:
    def __init__(self, raw_dir: Path, delay: float, max_requests: int, max_retries: int = DEFAULT_MAX_RETRIES) -> None:
        self.raw_dir = raw_dir
        self.delay = delay
        self.max_requests = max_requests
        self.max_retries = max_retries
        self.records: list[dict] = []
        self.retry_count = 0
        self.started = time.monotonic()

    def _budget(self) -> None:
        if len(self.records) >= self.max_requests:
            raise RuntimeError("request_budget_exceeded")

    def _record(
        self,
        tag: str,
        method: str,
        url: str,
        response_url: str | None,
        status: int | None,
        body: bytes,
        error: str | None,
        start: float,
    ) -> dict:
        digest = hashlib.sha256(body).hexdigest() if body else None
        raw_path = None
        if body:
            target = self.raw_dir / (tag.replace("/", "__") + ".raw")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
            raw_path = str(target)
        record = {
            "index": len(self.records) + 1,
            "tag": tag,
            "method": method,
            "url": url,
            "response_url": response_url,
            "status": status,
            "observed_at": utc_now(),
            "elapsed_seconds": round(time.monotonic() - start, 3),
            "content_length_observed": len(body),
            "sha256": digest,
            "file": raw_path,
            "error": error,
        }
        self.records.append(record)
        return record

    def _request(
        self,
        tag: str,
        url: str,
        headers: dict[str, str],
        *,
        seller: str | None = None,
    ) -> tuple[bytes, dict]:
        for attempt in range(self.max_retries + 1):
            self._budget()
            request = urllib.request.Request(url, headers=headers, method="GET")
            body, status, response_url, error, start = b"", None, None, None, time.monotonic()
            try:
                with urllib.request.urlopen(request, timeout=45) as response:
                    status, response_url, body = response.status, response.geturl(), response.read()
            except urllib.error.HTTPError as exc:
                status, response_url, body, error = exc.code, exc.geturl(), exc.read(), f"http_{exc.code}"
            except Exception as exc:  # noqa: BLE001
                error = f"{type(exc).__name__}:{exc}"

            if response_url:
                if seller is None:
                    if urlsplit(response_url).netloc != "www.walmart.com.hn":
                        error = error or "home_redirect_outside_origin"
                else:
                    try:
                        validate_context_url(response_url, seller)
                    except RuntimeError as exc:
                        error = error or str(exc)

            record_tag = tag if attempt == 0 else f"{tag}/retry-{attempt}"
            record = self._record(record_tag, "GET", url, response_url, status, body, error, start)
            if self.delay:
                time.sleep(self.delay)
            if status == 200 and error is None:
                return body, record
            if attempt >= self.max_retries or not _retryable(status, error):
                raise RuntimeError(f"request_failed:{tag}:{status}:{error}")
            self.retry_count += 1
        raise AssertionError("retry_loop_exhausted")

    def home(self) -> tuple[str, dict]:
        body, record = self._request("home", BASE + "/", {"User-Agent": UA})
        try:
            return body.decode("utf-8"), record
        except UnicodeDecodeError as exc:
            raise RuntimeError("home_response_not_text") from exc

    def get(self, seller: str, tag: str, path: str, query: dict[str, str]) -> tuple[dict, dict]:
        url = BASE + path + "?" + urlencode(query)
        validate_context_url(url, seller)
        body, record = self._request(
            tag,
            url,
            {"User-Agent": UA, "Accept": "application/json"},
            seller=seller,
        )
        try:
            doc = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"response_not_json:{tag}") from exc
        if not isinstance(doc, dict):
            raise RuntimeError(f"response_shape_invalid:{tag}")
        return doc, record


def common_query(seller: str) -> dict[str, str]:
    return {"regionId": region_id(seller), "sc": SALES_CHANNEL, "country": COUNTRY}


class PartitionTotalChanged(RuntimeError):
    """The source changed a partition total while that partition was paged."""

    def __init__(
        self,
        tag: str,
        expected_total: int,
        observed_total: int,
        record: dict,
    ) -> None:
        self.tag = tag
        self.expected_total = expected_total
        self.observed_total = observed_total
        self.record = record
        super().__init__(f"page_total_changed:{tag}:{expected_total}:{observed_total}")


class PartitionMembershipOverlap(RuntimeError):
    """Stable product identities moved across pages while a partition was paged."""

    def __init__(self, tag: str, product_ids: set[str], record: dict) -> None:
        self.tag = tag
        self.product_ids = frozenset(product_ids)
        self.record = record
        super().__init__(f"product_membership_overlap:{tag}")


def _page_products(
    capture: Capture,
    seller: str,
    tag: str,
    path: str,
    common: dict[str, str],
    expected_total: int,
    page: int,
) -> tuple[list[dict], list[dict]]:
    expected_count = min(PAGE_SIZE, expected_total - (page - 1) * PAGE_SIZE)
    doc, record = capture.get(
        seller,
        tag,
        path,
        {**common, "count": str(PAGE_SIZE), "page": str(page)},
    )
    products = doc.get("products")
    observed_total = doc.get("recordsFiltered")
    if observed_total != expected_total or not isinstance(products, list):
        if type(observed_total) is int and observed_total > 0:
            raise PartitionTotalChanged(tag, expected_total, observed_total, record)
        raise RuntimeError(f"page_total_changed:{tag}")
    ids = [product.get("productId") for product in products if isinstance(product, dict)]
    page_valid = len(products) == expected_count and len(ids) == len(products) and len(set(ids)) == len(ids)
    if page_valid:
        return products, [record]
    if expected_count != PAGE_SIZE:
        raise RuntimeError(f"page_count_or_duplicate:{tag}")

    recovered: list[dict] = []
    evidence: list[dict] = []
    first_recovery_page = (page - 1) * (PAGE_SIZE // RECOVERY_PAGE_SIZE) + 1
    for offset in range(PAGE_SIZE // RECOVERY_PAGE_SIZE):
        recovery_page = first_recovery_page + offset
        part, part_record = capture.get(
            seller,
            f"{tag}/recovery-{offset + 1}",
            path,
            {**common, "count": str(RECOVERY_PAGE_SIZE), "page": str(recovery_page)},
        )
        part_products = part.get("products")
        if (
            part.get("recordsFiltered") != expected_total
            or not isinstance(part_products, list)
            or len(part_products) != RECOVERY_PAGE_SIZE
        ):
            raise RuntimeError(f"page_recovery_incomplete:{tag}")
        recovered.extend(part_products)
        evidence.append(part_record)
    recovered_ids = [product.get("productId") for product in recovered if isinstance(product, dict)]
    if len(recovered_ids) != PAGE_SIZE or len(set(recovered_ids)) != PAGE_SIZE:
        raise RuntimeError(f"page_recovery_duplicate:{tag}")
    return recovered, evidence


def _capture_partitions(
    capture: Capture,
    *,
    seller: str,
    common: dict[str, str],
    partitions: list[tuple[str, int]],
    tag_suffix: str = "",
) -> tuple[dict[str, dict], list[dict]]:
    category_products: dict[str, dict] = {}
    page_evidence: list[dict] = []
    for relative, partition_total in partitions:
        path = (
            f"/api/io/_v/api/intelligent-search/product_search/"
            f"accesscontrollist/{seller}/{relative}"
        )
        partition_products: dict[str, dict] = {}
        pages = math.ceil(partition_total / PAGE_SIZE)
        for page in range(1, pages + 1):
            tag = f"{seller}/{relative}{tag_suffix}/page-{page:03d}"
            products, records = _page_products(
                capture, seller, tag, path, common, partition_total, page
            )
            incoming_ids: set[str] = set()
            for product in products:
                if not isinstance(product, dict):
                    raise RuntimeError(f"product_id_invalid:{tag}")
                product_id = product.get("productId")
                if not isinstance(product_id, str) or not product_id:
                    raise RuntimeError(f"product_id_invalid:{tag}")
                incoming_ids.add(product_id)
            overlap = incoming_ids.intersection(
                set(partition_products).union(category_products)
            )
            if overlap:
                raise PartitionMembershipOverlap(tag, overlap, records[-1])
            for product in products:
                product_id = product["productId"]
                partition_products[product_id] = product
            page_evidence.extend(
                {
                    "tag": item["tag"],
                    "url": item["url"],
                    "sha256": item["sha256"],
                    "observed_at": item["observed_at"],
                }
                for item in records
            )
        if len(partition_products) != partition_total:
            raise RuntimeError(f"partition_membership_incomplete:{seller}:{relative}")
        category_products.update(partition_products)
    return category_products, page_evidence


def _capture_category(
    capture: Capture,
    *,
    seller: str,
    category: str,
    expected_total: int,
    common: dict[str, str],
    facets_root: str,
    tag_suffix: str = "",
    allow_total_recovery: bool = True,
) -> tuple[dict[str, dict], list[dict], int, dict | None]:
    partitions: list[tuple[str, int]] = [(f"category-1/{category}", expected_total)]
    facet_path: str | None = None
    children: dict[str, int] | None = None
    if category in CATEGORY2_PARTITIONS:
        facet_path = f"{facets_root}/category-1/{category}"
        child_doc, child_record = capture.get(
            seller,
            f"{seller}/{category}/category2-facets{tag_suffix}",
            facet_path,
            common,
        )
        children = facet_counts(child_doc, "category-2")
        if sum(children.values()) != expected_total:
            raise RuntimeError(f"category2_total_mismatch:{seller}:{category}")
        partitions = [
            (f"category-1/{category}/category-2/{child}", count)
            for child, count in children.items()
        ]

    try:
        products, evidence = _capture_partitions(
            capture,
            seller=seller,
            common=common,
            partitions=partitions,
            tag_suffix=tag_suffix,
        )
        if len(products) != expected_total:
            raise RuntimeError(f"category_membership_incomplete:{seller}:{category}")
        return products, evidence, expected_total, None
    except PartitionTotalChanged as drift:
        if not allow_total_recovery:
            raise RuntimeError(
                f"category_changed_during_final_recovery:{seller}:{category}:{drift.tag}"
            ) from drift
        if category in CATEGORY2_PARTITIONS:
            if facet_path is None or children is None:
                raise AssertionError("category2_partition_state_missing")
            prefix = f"{seller}/category-1/{category}/category-2/"
            if not drift.tag.startswith(prefix):
                raise RuntimeError(f"category2_drift_tag_invalid:{seller}:{category}") from drift
            drift_child = drift.tag[len(prefix):].split("/", 1)[0]
            rechecked_doc, rechecked_record = capture.get(
                seller,
                f"{seller}/{category}/category2-facets{tag_suffix}/recovery-total-drift",
                facet_path,
                common,
            )
            rechecked_children = facet_counts(rechecked_doc, "category-2")
            if rechecked_children.get(drift_child) != drift.observed_total:
                if rechecked_children != children:
                    raise RuntimeError(
                        f"category2_drift_not_confirmed:{seller}:{category}:{drift_child}"
                    ) from drift
                if math.ceil(expected_total / PAGE_SIZE) > MAX_SEARCH_PAGES:
                    raise RuntimeError(
                        f"category2_parent_recovery_too_large:{seller}:{category}"
                    ) from drift
                parent_partition = [(f"category-1/{category}", expected_total)]
                try:
                    first_products, first_evidence = _capture_partitions(
                        capture,
                        seller=seller,
                        common=common,
                        partitions=parent_partition,
                        tag_suffix=(
                            f"{tag_suffix}/recovery-parent-facet-disagreement"
                        ),
                    )
                    confirmed_products, confirmed_evidence = _capture_partitions(
                        capture,
                        seller=seller,
                        common=common,
                        partitions=parent_partition,
                        tag_suffix=(
                            f"{tag_suffix}/recovery-parent-facet-disagreement-confirmation"
                        ),
                    )
                except (PartitionTotalChanged, PartitionMembershipOverlap) as repeated:
                    raise RuntimeError(
                        f"category2_parent_recovery_changed:{seller}:{category}:"
                        f"{repeated.tag}"
                    ) from repeated
                if set(first_products) != set(confirmed_products):
                    raise RuntimeError(
                        f"category2_parent_membership_not_confirmed:{seller}:{category}"
                    ) from drift
                membership = hashlib.sha256(
                    "\n".join(sorted(confirmed_products)).encode()
                ).hexdigest()
                recovery = {
                    "category": category,
                    "strategy": "category2_stale_facet_parent_restart",
                    "trigger_tag": drift.tag,
                    "trigger_sha256": drift.record["sha256"],
                    "trigger_observed_at": drift.record["observed_at"],
                    "previous_total": expected_total,
                    "recovered_total": expected_total,
                    "previous_partition_total": drift.expected_total,
                    "observed_partition_total": drift.observed_total,
                    "category2_facet_sha256": child_record["sha256"],
                    "facet_recheck_sha256": rechecked_record["sha256"],
                    "first_parent_evidence_sha256": hashlib.sha256(
                        "\n".join(item["sha256"] for item in first_evidence).encode()
                    ).hexdigest(),
                    "confirmed_parent_evidence_sha256": hashlib.sha256(
                        "\n".join(item["sha256"] for item in confirmed_evidence).encode()
                    ).hexdigest(),
                    "confirmed_membership_sha256": membership,
                }
                return (
                    confirmed_products,
                    confirmed_evidence,
                    expected_total,
                    recovery,
                )
            recovered_total = sum(rechecked_children.values())
            recovered_partitions = [
                (f"category-1/{category}/category-2/{child}", count)
                for child, count in rechecked_children.items()
            ]
            try:
                products, evidence = _capture_partitions(
                    capture,
                    seller=seller,
                    common=common,
                    partitions=recovered_partitions,
                    tag_suffix=f"{tag_suffix}/recovery-total-drift",
                )
            except PartitionTotalChanged as repeated:
                raise RuntimeError(
                    f"category2_total_changed_again:{seller}:{category}:{repeated.tag}"
                ) from repeated
            if len(products) != recovered_total:
                raise RuntimeError(f"category2_recovery_incomplete:{seller}:{category}")
            confirmation_doc, confirmation_record = capture.get(
                seller,
                f"{seller}/{category}/category2-facets{tag_suffix}/recovery-confirmation",
                facet_path,
                common,
            )
            if facet_counts(confirmation_doc, "category-2") != rechecked_children:
                raise RuntimeError(f"category2_changed_during_recovery:{seller}:{category}")
            recovery = {
                "category": category,
                "strategy": "category2_exact_restart",
                "trigger_tag": drift.tag,
                "trigger_sha256": drift.record["sha256"],
                "trigger_observed_at": drift.record["observed_at"],
                "previous_total": expected_total,
                "recovered_total": recovered_total,
                "previous_partition_total": drift.expected_total,
                "recovered_partition_total": drift.observed_total,
                "facet_recheck_sha256": rechecked_record["sha256"],
                "facet_confirmation_sha256": confirmation_record["sha256"],
            }
            return products, evidence, recovered_total, recovery
        try:
            products, evidence = _capture_partitions(
                capture,
                seller=seller,
                common=common,
                partitions=[(f"category-1/{category}", drift.observed_total)],
                tag_suffix=f"{tag_suffix}/recovery-total-drift",
            )
        except PartitionTotalChanged as repeated:
            raise RuntimeError(
                f"category_total_changed_again:{seller}:{category}:{repeated.tag}"
            ) from repeated
        if len(products) != drift.observed_total:
            raise RuntimeError(f"category_recovery_incomplete:{seller}:{category}")
        recovery = {
            "category": category,
            "strategy": "parent_category_exact_restart",
            "trigger_tag": drift.tag,
            "trigger_sha256": drift.record["sha256"],
            "trigger_observed_at": drift.record["observed_at"],
            "previous_total": drift.expected_total,
            "recovered_total": drift.observed_total,
        }
        return products, evidence, drift.observed_total, recovery
    except PartitionMembershipOverlap as overlap:
        membership_facet_path = f"{facets_root}/category-1/{category}"
        rechecked_doc, rechecked_record = capture.get(
            seller,
            f"{seller}/{category}/category2-facets{tag_suffix}/recovery-membership-overlap",
            membership_facet_path,
            common,
        )
        rechecked_children = facet_counts(rechecked_doc, "category-2")
        if sum(rechecked_children.values()) != expected_total:
            raise RuntimeError(
                f"category2_membership_total_mismatch:{seller}:{category}"
            ) from overlap
        recovered_partitions = [
            (f"category-1/{category}/category-2/{child}", count)
            for child, count in rechecked_children.items()
        ]
        try:
            products, evidence = _capture_partitions(
                capture,
                seller=seller,
                common=common,
                partitions=recovered_partitions,
                tag_suffix=f"{tag_suffix}/recovery-membership-overlap",
            )
        except PartitionTotalChanged as repeated_total:
            raise RuntimeError(
                f"category_changed_during_membership_recovery:{seller}:{category}:"
                f"{repeated_total.tag}"
            ) from repeated_total
        except PartitionMembershipOverlap as repeated_overlap:
            raise RuntimeError(
                f"category_membership_changed_again:{seller}:{category}:"
                f"{repeated_overlap.tag}"
            ) from repeated_overlap
        if len(products) != expected_total:
            raise RuntimeError(f"category_membership_recovery_incomplete:{seller}:{category}")
        confirmation_doc, confirmation_record = capture.get(
            seller,
            f"{seller}/{category}/category2-facets{tag_suffix}/recovery-membership-confirmation",
            membership_facet_path,
            common,
        )
        if facet_counts(confirmation_doc, "category-2") != rechecked_children:
            raise RuntimeError(
                f"category2_changed_during_membership_recovery:{seller}:{category}"
            )
        duplicate_ids = sorted(overlap.product_ids)
        recovery = {
            "category": category,
            "strategy": "category2_exact_membership_restart",
            "trigger_tag": overlap.tag,
            "trigger_sha256": overlap.record["sha256"],
            "trigger_observed_at": overlap.record["observed_at"],
            "previous_total": expected_total,
            "recovered_total": expected_total,
            "duplicate_product_count": len(duplicate_ids),
            "duplicate_product_ids_sha256": hashlib.sha256(
                "\n".join(duplicate_ids).encode()
            ).hexdigest(),
            "category2_partitions": len(rechecked_children),
            "facet_recheck_sha256": rechecked_record["sha256"],
            "facet_confirmation_sha256": confirmation_record["sha256"],
        }
        return products, evidence, expected_total, recovery


def _probe_category_stability(
    capture: Capture,
    *,
    seller: str,
    category: str,
    common: dict[str, str],
    facets_root: str,
    search_root: str,
    expected_categories: set[str],
    cycle: int,
    probe: int,
) -> dict:
    tag_prefix = (
        f"{seller}/category-1/{category}/stabilization-cycle-{cycle}/probe-{probe}"
    )
    facet_doc, facet_record = capture.get(
        seller,
        f"{tag_prefix}/facets",
        facets_root,
        common,
    )
    counts = facet_counts(facet_doc, "category-1")
    if set(counts) != expected_categories:
        raise RuntimeError(f"catalog_category_shape_changed:{seller}")

    search_doc, search_record = capture.get(
        seller,
        f"{tag_prefix}/search",
        f"{search_root}/category-1/{category}",
        {**common, "count": "1", "page": "1"},
    )
    search_total = search_doc.get("recordsFiltered")
    products = search_doc.get("products")
    search_response_valid = (
        type(search_total) is int
        and search_total > 0
        and isinstance(products, list)
        and len(products) == 1
    )
    facet_total = counts[category]
    return {
        "cycle": cycle,
        "probe": probe,
        "facet_total": facet_total,
        "search_total": search_total if type(search_total) is int else None,
        "sources_agree": search_response_valid and facet_total == search_total,
        "facet_tag": facet_record["tag"],
        "facet_sha256": facet_record["sha256"],
        "facet_observed_at": facet_record["observed_at"],
        "search_tag": search_record["tag"],
        "search_sha256": search_record["sha256"],
        "search_observed_at": search_record["observed_at"],
    }


def _recover_final_facet_category(
    capture: Capture,
    *,
    seller: str,
    category: str,
    previous_total: int,
    common: dict[str, str],
    facets_root: str,
    search_root: str,
    expected_categories: set[str],
    trigger_record: dict,
) -> tuple[dict[str, dict], list[dict], int, dict]:
    observations: list[dict] = []
    for cycle, stabilization_delay in enumerate(
        FINAL_DRIFT_STABILIZATION_DELAYS, start=1
    ):
        if stabilization_delay:
            time.sleep(stabilization_delay)
        first = _probe_category_stability(
            capture,
            seller=seller,
            category=category,
            common=common,
            facets_root=facets_root,
            search_root=search_root,
            expected_categories=expected_categories,
            cycle=cycle,
            probe=1,
        )
        observations.append(first)
        if FINAL_DRIFT_CONFIRMATION_DELAY:
            time.sleep(FINAL_DRIFT_CONFIRMATION_DELAY)
        second = _probe_category_stability(
            capture,
            seller=seller,
            category=category,
            common=common,
            facets_root=facets_root,
            search_root=search_root,
            expected_categories=expected_categories,
            cycle=cycle,
            probe=2,
        )
        observations.append(second)

        stable_total = second["facet_total"]
        if not (
            first["sources_agree"]
            and second["sources_agree"]
            and first["facet_total"] == stable_total
        ):
            continue

        try:
            products, evidence, recovered_total, nested_recovery = _capture_category(
                capture,
                seller=seller,
                category=category,
                expected_total=stable_total,
                common=common,
                facets_root=facets_root,
                tag_suffix=f"/recovery-final-facet-stabilization-{cycle}",
                allow_total_recovery=False,
            )
        except RuntimeError as exc:
            if str(exc).startswith(
                (
                    "category_changed_during_final_recovery:",
                    "category2_total_mismatch:",
                )
            ):
                second["recapture_result"] = "source_changed_again"
                continue
            raise
        if nested_recovery is not None or recovered_total != stable_total:
            second["recapture_result"] = "nested_recovery_or_total_mismatch"
            continue

        second["recapture_result"] = "accepted"
        return (
            products,
            evidence,
            recovered_total,
            {
                "category": category,
                "strategy": "final_facet_stabilized_category_restart",
                "trigger_tag": trigger_record["tag"],
                "trigger_sha256": trigger_record["sha256"],
                "trigger_observed_at": trigger_record["observed_at"],
                "previous_total": previous_total,
                "recovered_total": recovered_total,
                "stabilization_cycles_used": cycle,
                "stabilization_delays_seconds": list(
                    FINAL_DRIFT_STABILIZATION_DELAYS[:cycle]
                ),
                "confirmation_delay_seconds": FINAL_DRIFT_CONFIRMATION_DELAY,
                "stabilization_observations": observations,
            },
        )

    raise RuntimeError(
        f"category_unstable_after_bounded_stabilization:{seller}:{category}"
    )


def capture_store(capture: Capture, seller: str, location_id: str, city: str, store_name: str, home_sha: str) -> dict:
    common = common_query(seller)
    facets_root = f"/api/io/_v/api/intelligent-search/facets/accesscontrollist/{seller}"
    search_root = f"/api/io/_v/api/intelligent-search/product_search/accesscontrollist/{seller}"
    before, before_record = capture.get(seller, f"{seller}/facets-before", facets_root, common)
    category1 = facet_counts(before, "category-1")
    root_total = sum(category1.values())
    root_before, root_before_record = capture.get(
        seller, f"{seller}/root-before", search_root, {**common, "count": "1", "page": "1"}
    )
    if root_before.get("recordsFiltered") != root_total or len(root_before.get("products", [])) != 1:
        raise RuntimeError(f"root_total_disagrees_with_facets:{seller}")

    products_by_category: dict[str, dict[str, dict]] = {}
    evidence_by_category: dict[str, list[dict]] = {}
    category_total_recoveries: list[dict] = []
    membership_recoveries: list[dict] = []
    final_category1 = dict(category1)

    for category, expected_total in category1.items():
        category_products, category_evidence, final_total, recovery = _capture_category(
            capture,
            seller=seller,
            category=category,
            expected_total=expected_total,
            common=common,
            facets_root=facets_root,
        )
        products_by_category[category] = category_products
        evidence_by_category[category] = category_evidence
        final_category1[category] = final_total
        if recovery is not None:
            if recovery["strategy"] == "category2_exact_membership_restart":
                membership_recoveries.append(recovery)
            else:
                category_total_recoveries.append(recovery)

    after, after_record = capture.get(seller, f"{seller}/facets-after", facets_root, common)
    after_category1 = facet_counts(after, "category-1")
    recovery_confirmation_record: dict | None = None
    if after_category1 != final_category1:
        if set(after_category1) != set(final_category1):
            raise RuntimeError(f"catalog_category_shape_changed:{seller}")
        changed_categories = [
            category
            for category in final_category1
            if final_category1[category] != after_category1[category]
        ]
        pending_recoveries: list[dict] = []
        for category in changed_categories:
            previous_total = final_category1[category]
            category_products, category_evidence, recovered_total, recovery = (
                _recover_final_facet_category(
                    capture,
                    seller=seller,
                    category=category,
                    previous_total=previous_total,
                    common=common,
                    facets_root=facets_root,
                    search_root=search_root,
                    expected_categories=set(final_category1),
                    trigger_record=after_record,
                )
            )
            products_by_category[category] = category_products
            evidence_by_category[category] = category_evidence
            final_category1[category] = recovered_total
            pending_recoveries.append(recovery)
        confirmation, recovery_confirmation_record = capture.get(
            seller,
            f"{seller}/facets-after/recovery-confirmation",
            facets_root,
            common,
        )
        if facet_counts(confirmation, "category-1") != final_category1:
            raise RuntimeError(f"catalog_changed_during_final_recovery:{seller}")
        for recovery in pending_recoveries:
            recovery["facet_confirmation_sha256"] = recovery_confirmation_record["sha256"]
        category_total_recoveries.extend(pending_recoveries)

    products_by_id: dict[str, dict] = {}
    page_evidence: list[dict] = []
    for category in final_category1:
        category_products = products_by_category[category]
        overlap = set(products_by_id).intersection(category_products)
        if overlap:
            raise RuntimeError(f"product_membership_overlap:{seller}:category-1/{category}")
        products_by_id.update(category_products)
        page_evidence.extend(evidence_by_category[category])
    final_root_total = sum(final_category1.values())
    root_after, root_after_record = capture.get(
        seller, f"{seller}/root-after", search_root, {**common, "count": "1", "page": "1"}
    )
    if root_after.get("recordsFiltered") != final_root_total or len(root_after.get("products", [])) != 1:
        raise RuntimeError(f"root_changed_during_capture:{seller}")
    if len(products_by_id) != final_root_total:
        raise RuntimeError(f"catalog_membership_incomplete:{seller}")

    source_products = [products_by_id[pid] for pid in sorted(products_by_id)]
    rows, source_details = parse_products(source_products)
    if len(rows) != len(source_details):
        raise RuntimeError(f"sku_membership_overlap:{seller}")
    product_ids = set(products_by_id)

    times = [datetime.fromisoformat(item["observed_at"].replace("Z", "+00:00")) for item in page_evidence]
    rows.sort(key=lambda row: row["source_key"])
    return {
        "result": "success",
        "supermarket_id": "walmart",
        "location_id": location_id,
        "city": city,
        "currency": "HNL",
        "scope": SCOPE,
        "seller_id": seller,
        "store_name": store_name,
        "region_id": region_id(seller),
        "sales_channel": SALES_CHANNEL,
        "catalog_complete": True,
        "validation_passed": True,
        "location_verified_same_run": True,
        "observed_at_utc": max(times).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "observation_started_at_utc": min(times).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "catalog_products_reported": final_root_total,
        "unique_products_extracted": len(product_ids),
        "skus_extracted": len(rows),
        "skus_with_price": sum(row["current_price"] is not None for row in rows),
        "membership_count": len(product_ids),
        "membership_sha256": hashlib.sha256("\n".join(sorted(product_ids)).encode()).hexdigest(),
        "availability_counts": dict(Counter(row["availability"] for row in rows)),
        "promotion_counts": {
            "promotion": sum(row["is_promotion"] is True for row in rows),
            "not_promotion": sum(row["is_promotion"] is False for row in rows),
            "unknown": sum(row["is_promotion"] is None for row in rows),
        },
        "products": rows,
        "source_details": source_details,
        "page_evidence": page_evidence,
        "category_total_recoveries": category_total_recoveries,
        "membership_recoveries": membership_recoveries,
        "binding_evidence": {
            "selector": seller,
            "region_id": region_id(seller),
            "sales_channel": SALES_CHANNEL,
            "country": COUNTRY,
            "home_configuration_sha256": home_sha,
            "facet_before_sha256": before_record["sha256"],
            "facet_after_sha256": after_record["sha256"],
            "facet_recovery_confirmation_sha256": (
                recovery_confirmation_record["sha256"]
                if recovery_confirmation_record is not None
                else None
            ),
            "root_before_sha256": root_before_record["sha256"],
            "root_after_sha256": root_after_record["sha256"],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-read-only", action="store_true")
    parser.add_argument("--allow-full-catalog", action="store_true")
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--raw-directory", type=Path, required=True)
    parser.add_argument("--evidence-output", type=Path, required=True)
    parser.add_argument("--delay-seconds", type=float, default=DEFAULT_DELAY)
    parser.add_argument("--max-requests", type=int, default=MAX_REQUESTS_HARD)
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES)
    args = parser.parse_args()
    if not args.live_read_only or not args.allow_full_catalog:
        raise SystemExit("explicit_live_full_catalog_authorization_required")
    if args.delay_seconds < 0.5:
        raise SystemExit("delay_too_small")
    if args.max_requests <= 0 or args.max_requests > MAX_REQUESTS_HARD:
        raise SystemExit("request_budget_invalid")
    if args.max_retries < 0 or args.max_retries > MAX_RETRIES_HARD:
        raise SystemExit("retry_budget_invalid")

    args.output_directory.mkdir(parents=True, exist_ok=True)
    args.raw_directory.mkdir(parents=True, exist_ok=True)
    args.evidence_output.parent.mkdir(parents=True, exist_ok=True)
    capture = Capture(args.raw_directory, args.delay_seconds, args.max_requests, args.max_retries)
    snapshots: list[dict] = []
    error = None
    try:
        home, home_record = capture.home()
        verify_stores(home)
        home_sha = str(home_record["sha256"])
        for seller, (location_id, city, store_name) in STORES.items():
            snapshots.append(capture_store(capture, seller, location_id, city, store_name, home_sha))
        names = {
            "walmart_sps": "sps",
            "walmart_tgu_ffaa": "tgu-ffaa",
            "walmart_tgu_el_sauce": "tgu-el-sauce",
        }
        for snapshot in snapshots:
            target = args.output_directory / f"snapshot-walmart-{names[snapshot['location_id']]}.json"
            target.write_text(
                json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}:{exc}"

    evidence = {
        "result": "success" if error is None else "failed",
        "scope": SCOPE,
        "concurrency": 1,
        "automatic_retry_count": capture.retry_count,
        "max_retries_per_request": args.max_retries,
        "request_budget": args.max_requests,
        "request_count": len(capture.records),
        "elapsed_seconds": round(time.monotonic() - capture.started, 3),
        "stores": [
            {
                "location_id": snapshot["location_id"],
                "seller_id": snapshot["seller_id"],
                "catalog_products_reported": snapshot["catalog_products_reported"],
                "skus_extracted": snapshot["skus_extracted"],
                "skus_with_price": snapshot["skus_with_price"],
                "category_total_recovery_count": len(snapshot["category_total_recoveries"]),
                "membership_recovery_count": len(snapshot["membership_recoveries"]),
            }
            for snapshot in snapshots
        ],
        "error": error,
    }
    args.evidence_output.write_text(
        json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
    )
    (args.evidence_output.parent / "requests.json").write_text(
        json.dumps({"records": capture.records}, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
    )
    if error is not None:
        raise SystemExit(error)
    print(json.dumps(evidence, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
