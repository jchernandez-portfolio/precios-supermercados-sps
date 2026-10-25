from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "precios-supermercados-sps/scripts/radiografiar_selector_ubicacion_la_colonia.py"


def load_module():
    spec = importlib.util.spec_from_file_location("full_location_radiography", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_network_urls_drop_query_values_and_fragments() -> None:
    module = load_module()
    rendered = module._safe_url(
        "https://www.lacolonia.com/path?orderFormId=secret-value&foo=bar#private"
    )
    assert rendered == "https://www.lacolonia.com/path?foo=&orderFormId="
    assert "secret-value" not in rendered
    assert "bar" not in rendered
    assert "private" not in rendered


def test_storage_values_are_never_rendered_directly() -> None:
    module = load_module()
    value = "opaque-sensitive-runtime-value"
    fingerprint = module._fingerprint(value)
    assert len(fingerprint) == 16
    assert value not in fingerprint
    assert fingerprint == module._fingerprint(value)


def test_target_and_click_budget_are_fixed_in_code() -> None:
    module = load_module()
    assert module.TARGET_URL == "https://www.lacolonia.com/"
    assert module.TARGET_CITY == "San Pedro Sula"
    assert module.OTHER_CITY == "Tegucigalpa"
    assert module.MAX_CLICK_ATTEMPTS == 8
