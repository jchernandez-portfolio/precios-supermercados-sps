#!/usr/bin/env python3
"""Radiografía estructural de la selección de ciudad de La Colonia.

Esta herramienta es diagnóstica: abre únicamente la portada pública de La Colonia,
abre el selector de ubicación, inspecciona la superficie DOM visible de las ciudades
y prueba de forma acotada las superficies asociadas a ``San Pedro Sula``. No recorre
productos, no ejecuta GraphQL/facets y no concede autoridad comercial.

Nunca persiste cookies, storage values, headers ni query-string values. Los valores
opacos de storage se representan únicamente mediante fingerprints SHA-256 truncados.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.sync_api import Page, sync_playwright

TARGET_URL = "https://www.lacolonia.com/"
TARGET_CITY = "San Pedro Sula"
OTHER_CITY = "Tegucigalpa"
MAX_CLICK_ATTEMPTS = 8


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _safe_url(value: str) -> str:
    """Conserva host/path y nombres de query params, nunca sus valores."""

    try:
        parts = urlsplit(value)
        keys = sorted({key for key, _ in parse_qsl(parts.query, keep_blank_values=True)})
        query = urlencode([(key, "") for key in keys])
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))
    except Exception:
        return "invalid-url"


def _storage_snapshot(page: Page) -> dict[str, list[dict[str, str]]]:
    raw = page.evaluate(
        """
        () => {
          const read = (storage) => {
            const out = [];
            for (let i = 0; i < storage.length; i += 1) {
              const key = storage.key(i);
              out.push([key, storage.getItem(key)]);
            }
            return out;
          };
          return {local: read(localStorage), session: read(sessionStorage)};
        }
        """
    )
    result: dict[str, list[dict[str, str]]] = {"local": [], "session": []}
    for bucket in ("local", "session"):
        for key, value in raw.get(bucket, []):
            result[bucket].append(
                {
                    "key": str(key),
                    "value_fingerprint": _fingerprint("" if value is None else str(value)),
                }
            )
        result[bucket].sort(key=lambda item: item["key"].casefold())
    return result


def _cookie_names(page: Page) -> list[dict[str, str]]:
    cookies = page.context.cookies()
    return sorted(
        (
            {
                "name": str(cookie.get("name", "")),
                "domain": str(cookie.get("domain", "")),
                "path": str(cookie.get("path", "")),
            }
            for cookie in cookies
        ),
        key=lambda item: (item["domain"], item["name"], item["path"]),
    )


def _header_labels(page: Page) -> list[str]:
    labels: list[str] = []
    locator = page.locator("button.btn-modal-selector")
    for index in range(locator.count()):
        candidate = locator.nth(index)
        try:
            if candidate.is_visible():
                value = " ".join(candidate.inner_text().split()).strip()
                if value:
                    labels.append(value)
        except Exception:
            continue
    return labels


def _visible_body_text(page: Page, limit: int = 12000) -> str:
    try:
        text = page.locator("body").inner_text(timeout=5_000)
    except Exception:
        return ""
    normalized = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    return normalized[:limit]


def _dom_radiography(page: Page) -> dict[str, Any]:
    return page.evaluate(
        r"""
        ({targetCity, otherCity}) => {
          const norm = (value) => String(value || '')
            .normalize('NFD')
            .replace(/[\u0300-\u036f]/g, '')
            .replace(/\s+/g, ' ')
            .trim()
            .toLowerCase();
          const target = norm(targetCity);
          const other = norm(otherCity);
          const prompt = norm('¿Desde qué ciudad nos visita?');
          const visible = (el) => {
            const style = getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return style.display !== 'none' && style.visibility !== 'hidden' &&
              Number(style.opacity || 1) > 0 && rect.width > 0 && rect.height > 0 &&
              rect.right > 0 && rect.bottom > 0 && rect.left < innerWidth && rect.top < innerHeight;
          };
          const text = (el) => String(el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
          const ownText = (el) => Array.from(el.childNodes)
            .filter((node) => node.nodeType === Node.TEXT_NODE)
            .map((node) => node.textContent || '')
            .join(' ')
            .replace(/\s+/g, ' ')
            .trim();
          const attrs = (el) => {
            const out = {};
            for (const attr of Array.from(el.attributes || [])) {
              const name = attr.name.toLowerCase();
              if (name.startsWith('data-')) {
                out[name] = '<present>';
                continue;
              }
              if (
                ['id','class','role','type','name','for','tabindex','href','title','value'].includes(name) ||
                name.startsWith('aria-')
              ) {
                let value = attr.value;
                if (name === 'href') {
                  try {
                    const u = new URL(value, location.href);
                    value = `${u.origin}${u.pathname}`;
                  } catch (_) {
                    value = '<invalid>';
                  }
                }
                if (name === 'value' && value.length > 80) value = '<redacted-long-value>';
                out[name] = value;
              }
            }
            if (el.hasAttribute && el.hasAttribute('onclick')) out.onclick = '<present>';
            return out;
          };
          const descriptor = (el) => {
            const style = getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return {
              tag: el.tagName.toLowerCase(),
              text: text(el).slice(0, 180),
              own_text: ownText(el).slice(0, 180),
              attributes: attrs(el),
              visible: visible(el),
              rect: {
                x: Math.round(rect.x), y: Math.round(rect.y),
                width: Math.round(rect.width), height: Math.round(rect.height),
              },
              style: {
                display: style.display,
                visibility: style.visibility,
                opacity: style.opacity,
                pointer_events: style.pointerEvents,
                cursor: style.cursor,
                position: style.position,
                z_index: style.zIndex,
              },
              checked: typeof el.checked === 'boolean' ? el.checked : null,
              disabled: typeof el.disabled === 'boolean' ? el.disabled : null,
            };
          };
          const chain = (el, max = 10) => {
            const out = [];
            let current = el;
            for (let depth = 0; current && depth < max; depth += 1, current = current.parentElement) {
              out.push({depth, ...descriptor(current)});
            }
            return out;
          };
          const all = Array.from(document.body.querySelectorAll('*'));
          const exact = (needle) => all.filter((el) => visible(el) && norm(text(el)) === needle);
          const promptMatches = exact(prompt);
          const cityMatches = exact(target);
          const otherMatches = exact(other);

          const tagClicks = new Map();
          let clickId = 0;
          const clickableHint = (el) => {
            const role = String(el.getAttribute('role') || '').toLowerCase();
            const style = getComputedStyle(el);
            const tab = el.getAttribute('tabindex');
            return (
              ['button','a','label','input','option','select'].includes(el.tagName.toLowerCase()) ||
              ['button','radio','option','menuitem','link'].includes(role) ||
              el.hasAttribute('onclick') ||
              style.cursor === 'pointer' ||
              (tab !== null && Number(tab) >= 0)
            );
          };
          const candidates = [];
          for (const match of cityMatches) {
            let current = match;
            for (let depth = 0; current && depth < 9; depth += 1, current = current.parentElement) {
              if (!visible(current)) continue;
              const key = current;
              if (!tagClicks.has(key)) {
                const id = String(clickId++);
                current.setAttribute('data-oai-radiography-click', id);
                tagClicks.set(key, id);
              }
              const center = (() => {
                const r = current.getBoundingClientRect();
                const x = Math.max(0, Math.min(innerWidth - 1, r.left + r.width / 2));
                const y = Math.max(0, Math.min(innerHeight - 1, r.top + r.height / 2));
                return {x: Math.round(x), y: Math.round(y)};
              })();
              const hit = document.elementFromPoint(center.x, center.y);
              candidates.push({
                click_id: tagClicks.get(key),
                source_depth: depth,
                clickable_hint: clickableHint(current),
                center_hit_tag: hit ? hit.tagName.toLowerCase() : null,
                center_hit_text: hit ? text(hit).slice(0, 120) : null,
                ...descriptor(current),
              });
            }
          }
          const deduped = [];
          const seen = new Set();
          for (const candidate of candidates) {
            if (seen.has(candidate.click_id)) continue;
            seen.add(candidate.click_id);
            deduped.push(candidate);
          }
          deduped.sort((a, b) => {
            const ah = a.clickable_hint ? 0 : 1;
            const bh = b.clickable_hint ? 0 : 1;
            if (ah !== bh) return ah - bh;
            if (a.source_depth !== b.source_depth) return a.source_depth - b.source_depth;
            return (a.rect.width * a.rect.height) - (b.rect.width * b.rect.height);
          });

          const dialogLike = all.filter((el) => visible(el) && (
            ['dialog','alertdialog'].includes(String(el.getAttribute('role') || '').toLowerCase()) ||
            el.tagName.toLowerCase() === 'dialog'
          )).map(descriptor);

          return {
            viewport: {width: innerWidth, height: innerHeight},
            opener_buttons: Array.from(document.querySelectorAll('button.btn-modal-selector')).map(descriptor),
            prompt_matches: promptMatches.map((el) => ({...descriptor(el), ancestors: chain(el)})),
            target_city_matches: cityMatches.map((el) => ({...descriptor(el), ancestors: chain(el)})),
            other_city_matches: otherMatches.map((el) => ({...descriptor(el), ancestors: chain(el)})),
            click_candidates: deduped.slice(0, 30),
            dialog_like: dialogLike.slice(0, 20),
          };
        }
        """,
        {"targetCity": TARGET_CITY, "otherCity": OTHER_CITY},
    )


def _selection_observation(page: Page) -> dict[str, Any]:
    return {
        "header_labels": _header_labels(page),
        "storage": _storage_snapshot(page),
        "cookies": _cookie_names(page),
        "visible_body_text": _visible_body_text(page, 6000),
    }


def _changed_storage(before: dict[str, Any], after: dict[str, Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {"local": [], "session": []}
    for bucket in ("local", "session"):
        left = {item["key"]: item["value_fingerprint"] for item in before.get(bucket, [])}
        right = {item["key"]: item["value_fingerprint"] for item in after.get(bucket, [])}
        for key in sorted(set(left) | set(right), key=str.casefold):
            if left.get(key) != right.get(key):
                result[bucket].append(key)
    return result


def run(output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    network_requests: list[dict[str, str]] = []
    network_responses: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "target_url": TARGET_URL,
        "target_city": TARGET_CITY,
        "browser_started": False,
        "navigation_completed": False,
        "selector_opened": False,
        "selection_confirmed": False,
        "working_click_candidate": None,
        "click_attempts": [],
        "errors": [],
        "authority": False,
        "catalog_accepted": False,
        "extraction_enabled": False,
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        report["browser_started"] = True
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="es-HN")
        page = context.new_page()
        page.on(
            "request",
            lambda request: network_requests.append(
                {
                    "method": request.method,
                    "resource_type": request.resource_type,
                    "url": _safe_url(request.url),
                }
            ),
        )
        page.on(
            "response",
            lambda response: network_responses.append(
                {
                    "status": response.status,
                    "url": _safe_url(response.url),
                }
            ),
        )

        try:
            page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(3_000)
            report["navigation_completed"] = True
            report["home"] = _selection_observation(page)
            page.screenshot(path=str(output_dir / "01-home.png"), full_page=False)

            opener = page.locator("button.btn-modal-selector")
            visible_openers = []
            for index in range(opener.count()):
                candidate = opener.nth(index)
                try:
                    if candidate.is_visible():
                        visible_openers.append(candidate)
                except Exception:
                    continue
            report["visible_opener_count"] = len(visible_openers)
            if len(visible_openers) != 1:
                report["errors"].append("location_selector_not_unique_or_missing")
            else:
                visible_openers[0].click(timeout=8_000)
                report["selector_opened"] = True
                page.wait_for_timeout(1_500)
                page.screenshot(path=str(output_dir / "02-selector-open.png"), full_page=False)
                report["modal"] = _selection_observation(page)
                report["dom_radiography"] = _dom_radiography(page)

                before_click = _selection_observation(page)
                candidates = report["dom_radiography"].get("click_candidates", [])
                for candidate in candidates[:MAX_CLICK_ATTEMPTS]:
                    click_id = str(candidate.get("click_id"))
                    attempt: dict[str, Any] = {
                        "click_id": click_id,
                        "tag": candidate.get("tag"),
                        "source_depth": candidate.get("source_depth"),
                        "clickable_hint": candidate.get("clickable_hint"),
                        "method": "locator.click",
                        "clicked": False,
                        "error": None,
                    }
                    locator = page.locator(f'[data-oai-radiography-click="{click_id}"]')
                    try:
                        if locator.count() != 1:
                            attempt["error"] = f"candidate_count_{locator.count()}"
                        else:
                            locator.click(timeout=4_000)
                            attempt["clicked"] = True
                            page.wait_for_timeout(1_500)
                    except Exception as exc:
                        attempt["error"] = f"{type(exc).__name__}: {str(exc)[:220]}"
                    observation = _selection_observation(page)
                    attempt["header_labels_after"] = observation["header_labels"]
                    attempt["storage_keys_changed"] = _changed_storage(
                        before_click["storage"], observation["storage"]
                    )
                    body_fold = observation["visible_body_text"].casefold()
                    attempt["target_city_visible_after"] = TARGET_CITY.casefold() in body_fold
                    report["click_attempts"].append(attempt)

                    header_match = any(
                        re.fullmatch(r"\s*san\s+pedro\s+sula\s*", label, re.I)
                        for label in observation["header_labels"]
                    )
                    storage_changed = any(attempt["storage_keys_changed"][bucket] for bucket in ("local", "session"))
                    modal_prompt_visible = "desde qué ciudad nos visita" in body_fold or "desde que ciudad nos visita" in body_fold
                    if header_match or (storage_changed and not modal_prompt_visible):
                        report["selection_confirmed"] = True
                        report["working_click_candidate"] = candidate
                        report["after_selection"] = observation
                        page.screenshot(path=str(output_dir / "03-after-selection.png"), full_page=False)
                        break

                if not report["selection_confirmed"]:
                    report["after_attempts"] = _selection_observation(page)
                    page.screenshot(path=str(output_dir / "03-after-attempts.png"), full_page=False)
        except Exception as exc:
            report["errors"].append(f"{type(exc).__name__}: {str(exc)[:400]}")
        finally:
            report["network"] = {
                "requests": network_requests[:500],
                "responses": network_responses[:500],
                "request_count": len(network_requests),
                "response_count": len(network_responses),
            }
            context.close()
            browser.close()

    (output_dir / "radiography.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        default="precios-supermercados-sps/diagnostic-artifacts/full-location-radiography",
    )
    args = parser.parse_args()
    report = run(Path(args.output_dir))
    print(
        json.dumps(
            {
                "navigation_completed": report.get("navigation_completed"),
                "selector_opened": report.get("selector_opened"),
                "selection_confirmed": report.get("selection_confirmed"),
                "errors": report.get("errors", []),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if report.get("navigation_completed") and report.get("selector_opened") else 2


if __name__ == "__main__":
    raise SystemExit(main())
