#!/usr/bin/env python3
"""Catálogo Colonial HTTP secuencial, con RAW reutilizable y cobertura por sitemap."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from precios_supermercados.scrapers.colonial import (  # noqa: E402
    MAX_COMMERCIAL_RECHECKS, ORIGIN, SECTION, ColonialError, apply_commercial_recheck,
    commercial_disagreements, parse_cards, parse_product_detail, parse_products, reconcile, sitemap_urls,
)


class Download:
    def __init__(self, output: Path, until: datetime, cache: list[Path], max_requests: int, seconds: int):
        self.output, self.until, self.max_requests = output, until, max_requests
        self.started, self.last, self.deadline = time.monotonic(), 0., time.monotonic() + seconds
        self.records, self.cache, self.used = [], {}, {}
        self.offline = False
        self.metrics = Counter(total_requests=0, successful_requests=0, failed_requests=0, retries=0, duplicate_requests_avoided=0, cached_resources_reused=0)
        output.mkdir(parents=True, exist_ok=False)
        (output / "raw").mkdir()
        for directory in cache:
            for item in json.loads((directory / "requests.json").read_text()):
                if item.get("status") == 200 and item.get("file"):
                    path = Path(item["file"])
                    raw = (path if path.is_absolute() else directory / path).read_bytes()
                    if hashlib.sha256(raw).hexdigest() != item["sha256"]:
                        raise ColonialError("cache_sha_mismatch")
                    self.cache[item["url"]] = (raw, item)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "PreciosSupermercadosSPS/ColonialCatalog (public; sequential)"

    def save(self) -> None:
        (self.output / "requests.json").write_text(json.dumps(self.records, indent=2))

    def get(self, url: str) -> bytes:
        if not url.startswith(ORIGIN + "/"):
            raise ColonialError("origin_not_allowed")
        if url in self.used:
            self.metrics["duplicate_requests_avoided"] += 1
            return self.used[url]
        if url in self.cache:
            raw, prior = self.cache[url]
            row = {key: prior[key] for key in ("url", "status", "sha256", "observed_at")}
            row["reused"] = True
            self.metrics["cached_resources_reused"] += 1
        else:
            if self.offline:
                raise ColonialError("offline_cache_miss:" + url)
            for attempt in range(2):
                time.sleep(max(0, 1 - (time.monotonic() - self.last)))
                if datetime.now(timezone.utc) >= self.until or time.monotonic() >= self.deadline:
                    raise ColonialError("authorization_or_deadline_expired")
                if self.metrics["total_requests"] >= self.max_requests:
                    raise ColonialError("request_budget_exhausted")
                row = {"url": url, "observed_at": datetime.now(timezone.utc).isoformat(), "reused": False}
                self.metrics["total_requests"] += 1
                self.records.append(row)
                self.save()
                try:
                    response = self.session.get(url, timeout=20, allow_redirects=False)
                    row["status"] = response.status_code
                    raw = response.content
                    row["sha256"] = hashlib.sha256(raw).hexdigest()
                    row["file"] = "raw/" + row["sha256"] + ".raw"
                    (self.output / row["file"]).write_bytes(raw)
                    if response.status_code != 200:
                        raise requests.RequestException("http_" + str(response.status_code))
                    self.metrics["successful_requests"] += 1
                    break
                except requests.RequestException as exc:
                    self.metrics["failed_requests"] += 1
                    row["error"] = str(exc)
                    status = row.get("status")
                    if attempt or self.metrics["retries"] >= 5 or (status is not None and status not in (500, 502, 503, 504)):
                        raise ColonialError("source_request_failed:" + str(exc)) from exc
                    self.metrics["retries"] += 1
                    time.sleep(3)
                finally:
                    self.last = time.monotonic()
                    self.save()
            self.records.pop()  # La respuesta exitosa se registra con su RAW abajo.
        row["file"] = "raw/" + row["sha256"] + ".raw"
        (self.output / row["file"]).write_bytes(raw)
        self.records.append(row)
        self.used[url] = raw
        self.save()
        return raw


def html_page_url(page: int, *, recheck: bool = False) -> str:
    if page == 1:
        url = ORIGIN + "/collections/all"
        return url + "?recheck=1" if recheck else url
    url = f"{ORIGIN}/collections/all?section_id={SECTION}&page={page}"
    return url + "&recheck=1" if recheck else url


def recheck_commercial_disagreements(get, rows: list[dict], cards: list[dict], page_of: dict[str, int],
                                     total: int) -> tuple[list[dict], list[dict], dict]:
    """Relee sólo los productos cuyo precio JSON y de tarjeta no cuadran.

    Cada producto se vuelve a pedir en ``/products/<handle>.json`` y su tarjeta en
    la misma página HTML (URL distinta para no reutilizar la respuesta vieja ni la
    caché del CDN). Si las lecturas frescas siguen sin cuadrar, ``reconcile``
    rechaza el catálogo igual que antes.
    """
    handles = commercial_disagreements(rows, cards)
    recheck = {"disagreements": len(handles), "handles": handles}
    if not handles:
        return rows, cards, recheck
    print(json.dumps({"commercial_recheck": handles}, ensure_ascii=False), flush=True)
    if len(handles) > MAX_COMMERCIAL_RECHECKS:
        raise ColonialError("commercial_sources_disagree")
    for handle in handles:
        fresh_rows = parse_product_detail(get(f"{ORIGIN}/products/{handle}.json"), handle)
        page = page_of.get(handle)
        if page is None:
            raise ColonialError("commercial_recheck_card_missing")
        count, incoming = parse_cards(get(html_page_url(page, recheck=True)))
        if count != total:
            raise ColonialError("html_total_drift_or_page_incomplete")
        matches = [card for card in incoming if card["handle"] == handle]
        if len(matches) != 1:
            raise ColonialError("commercial_recheck_card_moved")
        rows, cards = apply_commercial_recheck(rows, cards, handle, fresh_rows, matches[0])
    still = commercial_disagreements(rows, cards)
    recheck["resolved"] = len(handles) - len(still)
    print(json.dumps({"commercial_recheck_resolved": recheck["resolved"], "unresolved": still}, ensure_ascii=False), flush=True)
    return rows, cards, recheck


def collect(get) -> dict:
    home = get(ORIGIN + "/").decode("utf-8")
    if not all(signal in home for signal in ('Shopify.shop = "bm1gbx-tm.myshopify.com"', '"active":"HNL"', 'San Pedro Sula')):
        raise ColonialError("storefront_scope_changed")
    total, first_cards = parse_cards(get(ORIGIN + "/collections/all"))
    if len(first_cards) != min(24, total):
        raise ColonialError("html_page_size_changed")
    preflight = {"expected_products": total, "json_pages": math.ceil(total / 250),
                 "html_pages": math.ceil(total / 24), "json_page_size": 250, "html_page_size": 24}
    print(json.dumps({"preflight": preflight}), flush=True)
    maps = sitemap_urls(get(ORIGIN + "/sitemap.xml"), index=True)
    membership = set()
    for url in sorted(maps):
        members = sitemap_urls(get(url))
        if membership & members:
            raise ColonialError("sitemap_overlap")
        membership.update(members)
    if len(membership) != total:
        raise ColonialError("sitemap_collection_count_mismatch")
    rows = []
    for page in range(1, preflight["json_pages"] + 1):
        incoming = parse_products(get(f"{ORIGIN}/products.json?limit=250&page={page}"))
        if len({r["product_id"] for r in incoming}) != min(250, total - (page - 1) * 250):
            raise ColonialError("json_page_incomplete")
        rows.extend(incoming)
    cards = list(first_cards)
    page_of = {card["handle"]: 1 for card in first_cards}
    for page in range(2, preflight["html_pages"] + 1):
        count, incoming = parse_cards(get(html_page_url(page)))
        if count != total or len(incoming) != min(24, total - (page - 1) * 24):
            raise ColonialError("html_total_drift_or_page_incomplete")
        cards.extend(incoming)
        page_of.update({card["handle"]: page for card in incoming})
        if page % 25 == 0:
            print(json.dumps({"html_pages_completed": page, "products": len(cards)}), flush=True)
    rows, cards, recheck = recheck_commercial_disagreements(get, rows, cards, page_of, total)
    products = reconcile(rows, cards, membership, total)
    return {"result": "success", "supermarket_id": "colonial", "location_id": "colonial_sps",
            "city": "San Pedro Sula", "currency": "HNL", "catalog_complete": True,
            "validation_passed": True, "location_verified_same_run": True,
            "scope": "public_ecommerce_sps_not_physical_branch_inventory",
            "observed_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "catalog_products_reported": total, "unique_products_extracted": total,
            "skus_extracted": len(products), "skus_with_price": len(products),
            "membership_sha256": hashlib.sha256("\n".join(sorted(membership)).encode()).hexdigest(),
            "membership_count": len(membership), "html_cards_count": len(cards),
            "availability_counts": dict(Counter(p["availability"] for p in products)),
            "preflight": preflight, "commercial_recheck": recheck, "products": products}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--offline", action="store_true", help="Sólo RAW; nunca permite requests nuevos")
    parser.add_argument("--authorized-until", help="Caducidad UTC de la autorización humana vigente")
    parser.add_argument("--reuse-cache", type=Path, action="append", default=[])
    parser.add_argument("--max-requests", type=int, default=450)
    parser.add_argument("--deadline-seconds", type=int, default=1200)
    args = parser.parse_args()
    until = datetime.fromisoformat((args.authorized_until or "1970-01-01T00:00:00Z").replace("Z", "+00:00"))
    if until.tzinfo is None or (not args.offline and until <= datetime.now(timezone.utc)) or not 1 <= args.max_requests <= 450 or not 1 <= args.deadline_seconds <= 1200:
        parser.error("Autorización/presupuesto inválido")
    downloader = Download(args.output, until, args.reuse_cache, args.max_requests, args.deadline_seconds)
    downloader.offline = args.offline
    try:
        snapshot = collect(downloader.get)
        times = [datetime.fromisoformat(row["observed_at"]) for row in downloader.records if row.get("status") == 200]
        snapshot["observation_started_at_utc"] = min(times).strftime("%Y-%m-%dT%H:%M:%SZ")
        snapshot["observed_at_utc"] = max(times).strftime("%Y-%m-%dT%H:%M:%SZ")
        snapshot["metrics"] = dict(downloader.metrics, elapsed_seconds=round(time.monotonic() - downloader.started, 3))
        (args.output / "full-catalog.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))
        print(json.dumps({key: value for key, value in snapshot.items() if key != "products"}, ensure_ascii=False))
    finally:
        downloader.save()
        downloader.session.close()


if __name__ == "__main__":
    main()
