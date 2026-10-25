#!/usr/bin/env python3
"""Radiografía estructural read-only del selector de ubicación de La Colonia.

Observa únicamente la portada pública y abre el selector de ubicación. Persiste
estructura DOM pública y evidencia técnica sanitizada; nunca conserva valores de
cookies/storage ni valores de query. No recorre catálogo, no ejecuta GraphQL manual
y no concede autoridad comercial.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.sync_api import Frame, Page, sync_playwright

from precios_supermercados.diagnostics.la_colonia_sps_context_diagnostic import (
    launch_compatible_chromium,
)
from precios_supermercados.diagnostics.location_binding_dom_controls import (
    LocationControlResolutionError,
    open_location_selector,
)

TARGET_URL = "https://www.lacolonia.com/"
TARGET_CITY = "San Pedro Sula"
OTHER_CITY = "Tegucigalpa"
DEFAULT_OUTPUT_DIR = Path(
    "precios-supermercados-sps/diagnostic-artifacts/location-radiography-v2"
)
MAX_NETWORK_EVENTS = 500
MAX_BODY_TEXT = 12_000
MAX_DOM_ROWS = 160


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _safe_url(value: str) -> str:
    """Conserva scheme/host/path y nombres de query; elimina valores y fragment."""
    try:
        parts = urlsplit(value)
        query_names = sorted(
            {name for name, _ in parse_qsl(parts.query, keep_blank_values=True)}
        )
        query = urlencode([(name, "") for name in query_names])
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))
    except Exception:
        return "invalid-url"


def _storage(page: Page) -> dict[str, list[dict[str, str]]]:
    raw = page.evaluate(
        """
        () => {
          const read = (storage) => {
            const rows = [];
            for (let i = 0; i < storage.length; i += 1) {
              const key = storage.key(i);
              rows.push([key, storage.getItem(key)]);
            }
            return rows;
          };
          return {local: read(localStorage), session: read(sessionStorage)};
        }
        """
    ) or {"local": [], "session": []}
    result: dict[str, list[dict[str, str]]] = {"local": [], "session": []}
    for bucket in ("local", "session"):
        for key, value in raw.get(bucket, []):
            result[bucket].append(
                {
                    "key": str(key),
                    "value_fingerprint": _fingerprint("" if value is None else str(value)),
                }
            )
        result[bucket].sort(key=lambda row: row["key"].casefold())
    return result


def _cookies(page: Page) -> list[dict[str, str]]:
    rows = [
        {
            "name": str(cookie.get("name", "")),
            "domain": str(cookie.get("domain", "")),
            "path": str(cookie.get("path", "")),
        }
        for cookie in page.context.cookies()
    ]
    return sorted(rows, key=lambda row: (row["domain"], row["name"], row["path"]))


def _body_text(frame: Frame, limit: int = MAX_BODY_TEXT) -> str:
    try:
        value = frame.locator("body").inner_text(timeout=5_000)
    except Exception:
        return ""
    return "\n".join(line.strip() for line in value.splitlines() if line.strip())[:limit]


_DOM_INVENTORY_JS = r"""
({targetCity, otherCity, maxRows}) => {
  const normalize = (value) => String(value || '')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase();
  const target = normalize(targetCity);
  const other = normalize(otherCity);
  const text = (el) => String(el.innerText || el.textContent || '')
    .replace(/\s+/g, ' ')
    .trim();
  const ownText = (el) => Array.from(el.childNodes || [])
    .filter((node) => node.nodeType === Node.TEXT_NODE)
    .map((node) => node.textContent || '')
    .join(' ')
    .replace(/\s+/g, ' ')
    .trim();
  const presented = (el) => {
    try {
      const style = getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden' &&
        Number(style.opacity || 1) > 0 && rect.width > 0 && rect.height > 0 &&
        rect.right > 0 && rect.bottom > 0 && rect.left < innerWidth && rect.top < innerHeight;
    } catch (_) { return false; }
  };
  const attrs = (el) => {
    const out = {};
    const safe = new Set([
      'id','class','role','type','name','for','tabindex','title',
      'aria-label','aria-selected','aria-checked','aria-expanded',
      'aria-controls','aria-current','aria-hidden','disabled'
    ]);
    for (const attr of Array.from(el.attributes || [])) {
      const name = String(attr.name || '').toLowerCase();
      if (safe.has(name)) out[name] = String(attr.value || '').slice(0, 240);
      else if (name.startsWith('data-')) out[name] = '<present>';
    }
    if (el.hasAttribute && el.hasAttribute('onclick')) out.onclick = '<present>';
    return out;
  };
  const describe = (el) => {
    const rect = el.getBoundingClientRect();
    let style = {};
    try {
      const computed = getComputedStyle(el);
      style = {
        display: computed.display, visibility: computed.visibility,
        opacity: computed.opacity, position: computed.position,
        z_index: computed.zIndex, cursor: computed.cursor,
        pointer_events: computed.pointerEvents,
      };
    } catch (_) {}
    return {
      tag: String(el.tagName || '').toLowerCase(),
      text: text(el).slice(0, 300), own_text: ownText(el).slice(0, 220),
      attributes: attrs(el), presented: presented(el),
      rect: {x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height)},
      style,
    };
  };
  const ancestry = (el, depthLimit = 7) => {
    const rows = [];
    let current = el;
    for (let depth = 0; current && depth < depthLimit; depth += 1) {
      rows.push({depth, ...describe(current)});
      current = current.parentElement;
    }
    return rows;
  };
  const all = Array.from(document.querySelectorAll('*'));
  const normalized = (el) => normalize(text(el));
  const classOrId = (el) => `${String(el.getAttribute('class') || '')} ${String(el.getAttribute('id') || '')}`;
  const targetExactAll = all.filter((el) => normalized(el) === target);
  const otherExactAll = all.filter((el) => normalized(el) === other);
  const related = all.filter((el) => {
    const value = normalized(el);
    const structural = classOrId(el);
    return value.includes(target) || value.includes(other) ||
      /desde\s+qu[eé]\s+ciudad|selecciona.*ciudad|selecciona.*tienda/i.test(text(el)) ||
      /ciudad|ubic|location|modal|selector|radio|store|tienda/i.test(structural);
  });
  const controls = all.filter((el) => {
    const tag = String(el.tagName || '').toLowerCase();
    const role = String(el.getAttribute('role') || '').toLowerCase();
    return ['button','input','select','option','a','label'].includes(tag) ||
      ['button','radio','option','menuitem','dialog','alertdialog'].includes(role);
  });
  const classCounts = {};
  for (const el of all) {
    for (const token of Array.from(el.classList || [])) {
      if (/ciudad|ubic|location|modal|selector|radio|store|tienda/i.test(token)) {
        classCounts[token] = (classCounts[token] || 0) + 1;
      }
    }
  }
  const shadowHosts = all.filter((el) => Boolean(el.shadowRoot)).map((el) => ({
    host: describe(el),
    child_count: el.shadowRoot ? el.shadowRoot.querySelectorAll('*').length : 0,
    text: el.shadowRoot ? String(el.shadowRoot.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 600) : '',
  }));
  return {
    document: {
      title: document.title, ready_state: document.readyState,
      element_count: all.length, presented_element_count: all.filter(presented).length,
      body_child_count: document.body ? document.body.children.length : 0,
    },
    viewport: {width: innerWidth, height: innerHeight},
    class_counts: classCounts,
    target_exact_all: targetExactAll.slice(0, maxRows).map((el) => ({descriptor: describe(el), ancestors: ancestry(el)})),
    other_exact_all: otherExactAll.slice(0, maxRows).map((el) => ({descriptor: describe(el), ancestors: ancestry(el)})),
    related_nodes: related.slice(0, maxRows).map((el) => ({descriptor: describe(el), ancestors: ancestry(el, 4)})),
    controls: controls.slice(0, maxRows).map(describe),
    iframes: Array.from(document.querySelectorAll('iframe')).slice(0, 30).map((frame) => ({
      title: String(frame.getAttribute('title') || '').slice(0, 160),
      name: String(frame.getAttribute('name') || '').slice(0, 160),
      presented: presented(frame),
      src_origin_path: (() => {
        try { const u = new URL(frame.src, location.href); return `${u.origin}${u.pathname}`; }
        catch (_) { return '<invalid>'; }
      })(),
    })),
    shadow_hosts: shadowHosts.slice(0, 30),
  };
}
"""


def _frame_inventory(frame: Frame) -> dict[str, Any]:
    result: dict[str, Any] = {
        "url": _safe_url(frame.url),
        "name": frame.name[:160],
        "body_text": _body_text(frame),
    }
    try:
        result["dom"] = frame.evaluate(
            _DOM_INVENTORY_JS,
            {"targetCity": TARGET_CITY, "otherCity": OTHER_CITY, "maxRows": MAX_DOM_ROWS},
        )
    except Exception as exc:
        result["dom_error"] = f"{exc.__class__.__name__}: {str(exc)[:240]}"
    return result


def _snapshot(page: Page) -> dict[str, Any]:
    return {
        "url": _safe_url(page.url),
        "storage": _storage(page),
        "cookies": _cookies(page),
        "frames": [_frame_inventory(frame) for frame in page.frames[:20]],
    }


def run(output_dir: Path = DEFAULT_OUTPUT_DIR) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    requests: list[dict[str, str]] = []
    responses: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "schema_version": "precios-sps-la-colonia-location-radiography/v2",
        "target_url": TARGET_URL,
        "target_city": TARGET_CITY,
        "selector_opened": False,
        "errors": [],
        "authority": False,
        "catalog_accepted": False,
        "extraction_enabled": False,
    }
    with sync_playwright() as playwright:
        browser, executable = launch_compatible_chromium(playwright)
        report["browser_executable_name"] = Path(executable).name if executable else "managed"
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000}, locale="es-HN", service_workers="block"
        )
        page = context.new_page()
        page.on(
            "request",
            lambda request: requests.append(
                {"method": request.method, "resource_type": request.resource_type, "url": _safe_url(request.url)}
            ),
        )
        page.on(
            "response",
            lambda response: responses.append(
                {"status": response.status, "url": _safe_url(response.url)}
            ),
        )
        try:
            page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(3_000)
            report["before_open"] = _snapshot(page)
            page.screenshot(path=str(output_dir / "01-before-open.png"), full_page=False)
            try:
                resolved = open_location_selector(page)
                report["selector_opened"] = True
                report["opener_source"] = resolved.source
                report["visible_location_before_open"] = resolved.visible_location
            except LocationControlResolutionError as exc:
                report["errors"].append(
                    {"stage": "open_selector", "reason": str(exc), "diagnostic": dict(getattr(exc, "diagnostic", {}) or {})}
                )
            page.wait_for_timeout(3_000)
            report["after_open"] = _snapshot(page)
            page.screenshot(path=str(output_dir / "02-after-open.png"), full_page=False)
        except Exception as exc:
            report["errors"].append(
                {"stage": "runtime", "reason": f"{exc.__class__.__name__}: {str(exc)[:240]}"}
            )
        finally:
            report["network"] = {
                "requests": requests[:MAX_NETWORK_EVENTS], "responses": responses[:MAX_NETWORK_EVENTS],
                "request_count": len(requests), "response_count": len(responses),
            }
            context.close()
            browser.close()
    (output_dir / "radiography.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def main() -> int:
    report = run()
    after = report.get("after_open")
    frames = after.get("frames", []) if isinstance(after, dict) else []
    target_exact_count = 0
    related_count = 0
    for frame in frames if isinstance(frames, list) else []:
        dom = frame.get("dom") if isinstance(frame, dict) else None
        if not isinstance(dom, dict):
            continue
        target_exact_count += len(dom.get("target_exact_all", []) or [])
        related_count += len(dom.get("related_nodes", []) or [])
    summary = {
        "selector_opened": report.get("selector_opened"),
        "frame_count": len(frames) if isinstance(frames, list) else 0,
        "target_exact_count": target_exact_count,
        "related_node_count": related_count,
        "errors": report.get("errors", []),
        "authority": False,
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
