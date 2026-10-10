from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "precios-supermercados-sps/scripts/radiografiar_ubicacion_la_colonia_v2.py"
WORKFLOW = REPO_ROOT / ".github/workflows/precios-supermercados-sps-la-colonia-location-binding.yml"


def load_module():
    spec = importlib.util.spec_from_file_location("location_radiography_v2", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_radiography_target_is_fixed_and_has_no_commercial_authority() -> None:
    module = load_module()
    assert module.TARGET_URL == "https://www.lacolonia.com/"
    assert module.TARGET_CITY == "San Pedro Sula"
    assert module.OTHER_CITY == "Tegucigalpa"
    assert module.MAX_NETWORK_EVENTS == 500
    assert module.MAX_DOM_ROWS == 160
    raw = SCRIPT.read_text(encoding="utf-8")
    assert '"authority": False' in raw
    assert '"catalog_accepted": False' in raw
    assert '"extraction_enabled": False' in raw
    assert "graphql" in raw.casefold()
    assert "catálogo" in raw.casefold()


def test_safe_url_keeps_only_query_names() -> None:
    module = load_module()
    rendered = module._safe_url(
        "https://example.test/path?token=secret&regionId=opaque#private"
    )
    assert rendered == "https://example.test/path?regionId=&token="
    assert "secret" not in rendered
    assert "opaque" not in rendered
    assert "private" not in rendered


def test_fingerprints_are_stable_and_do_not_expose_storage_values() -> None:
    module = load_module()
    raw = "opaque-sensitive-value"
    fingerprint = module._fingerprint(raw)
    assert len(fingerprint) == 16
    assert raw not in fingerprint
    assert fingerprint == module._fingerprint(raw)




def test_radiography_persists_only_sanitized_context_shapes() -> None:
    raw = SCRIPT.read_text(encoding="utf-8")
    assert '"value_fingerprint"' in raw
    assert '"name": str(cookie.get("name", ""))' in raw
    assert 'cookie.get("value"' not in raw
    assert "request.post_data" not in raw
    assert "request.headers" not in raw
    assert "page.content(" not in raw
    assert "document.documentElement.innerHTML" not in raw
