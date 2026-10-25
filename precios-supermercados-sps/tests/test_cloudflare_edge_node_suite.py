from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

from precios_supermercados.scrapers.la_colonia_facet_discovery_adapter import (
    CATEGORY_TREE_QUERY,
    ROOT_TOTAL_QUERY,
)
from precios_supermercados.scrapers.la_colonia_graphql import (
    PRODUCT_SEARCH_QUERY,
    build_product_search_url,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EDGE_ROOT = PROJECT_ROOT / "edge" / "cloudflare"


def _node() -> str:
    executable = shutil.which("node")
    assert executable is not None, "Node.js es obligatorio para validar el gateway edge"
    version = subprocess.run(
        [executable, "--version"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    major = int(version.removeprefix("v").split(".", maxsplit=1)[0])
    assert major >= 22, f"Node.js 22+ requerido; encontrado {version}"
    return executable


def _npm() -> str:
    executable = shutil.which("npm")
    assert executable is not None, "npm es obligatorio para validar la suite edge canónica"
    return executable


def test_cloudflare_node_suite() -> None:
    # package.json is the single source of truth for the complete Node suite.
    # Do not duplicate its test-file list here: drift would silently exclude tests from CI.
    _node()
    result = subprocess.run(
        [_npm(), "--prefix", str(EDGE_ROOT), "test"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, (
        "La suite Node canónica del gateway Cloudflare falló.\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


def test_python_graphql_url_is_accepted_by_cloudflare_core() -> None:
    source_url = build_product_search_url(
        page=3,
        page_size=50,
        order_by="OrderByPriceDESC",
    )
    payload = {
        "url": source_url,
        "expectedGraphqlQuerySha256": hashlib.sha256(
            PRODUCT_SEARCH_QUERY.encode("utf-8")
        ).hexdigest(),
    }
    result = subprocess.run(
        [_node(), str(EDGE_ROOT / "test" / "fixture-cli.mjs")],
        cwd=PROJECT_ROOT,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    parsed = json.loads(result.stdout)
    assert parsed["ok"] is True
    assert parsed["from"] == 100
    assert parsed["to"] == 149
    assert parsed["orderBy"] == "OrderByPriceDESC"
    assert len(parsed["canonicalRequestSha256"]) == 64


def test_worker_fixed_graphql_hash_matches_python_query() -> None:
    policy_source = (EDGE_ROOT / "src" / "worker-policy.mjs").read_text(
        encoding="utf-8"
    )
    match = re.search(
        r'FIXED_GRAPHQL_QUERY_SHA256\s*=\s*"([0-9a-f]{64})"',
        policy_source,
    )
    assert match is not None, "El Worker debe fijar el hash GraphQL en código"
    assert match.group(1) == hashlib.sha256(
        PRODUCT_SEARCH_QUERY.encode("utf-8")
    ).hexdigest()


def test_worker_structural_hashes_match_python_queries() -> None:
    policy_source = (EDGE_ROOT / "src" / "worker-policy.mjs").read_text(
        encoding="utf-8"
    )
    root_match = re.search(
        r'root_total:\s*"([0-9a-f]{64})"',
        policy_source,
    )
    tree_match = re.search(
        r'category_tree:\s*"([0-9a-f]{64})"',
        policy_source,
    )
    assert root_match is not None and tree_match is not None
    assert root_match.group(1) == hashlib.sha256(ROOT_TOTAL_QUERY.encode("utf-8")).hexdigest()
    assert tree_match.group(1) == hashlib.sha256(CATEGORY_TREE_QUERY.encode("utf-8")).hexdigest()


def test_wrangler_config_declares_sqlite_do_and_only_secret_names() -> None:
    config = json.loads((EDGE_ROOT / "wrangler.json").read_text(encoding="utf-8"))
    assert config["main"] == "src/index.mjs"
    assert config["preview_urls"] is False
    assert "migrations" not in config
    assert config["durable_objects"]["bindings"] == [
        {
            "name": "AUTHORIZATION_GATEWAY",
            "class_name": "AuthorizationGateway",
        }
    ]
    assert config["exports"]["AuthorizationGateway"] == {
        "type": "durable-object",
        "storage": "sqlite",
    }
    assert config["version_metadata"]["binding"] == "CF_VERSION_METADATA"
    assert set(config["secrets"]["required"]) == {
        "EDGE_RECEIPT_PRIVATE_KEY_PKCS8_B64URL",
        "EDGE_RECEIPT_PUBLIC_KEY_SPKI_B64URL",
        "EDGE_COLLECTOR_CODE_SHA256",
    }
    serialized = json.dumps(config)
    assert "BEGIN PRIVATE KEY" not in serialized
    assert "PRIVATE_KEY_PKCS8_B64URL\":" not in serialized


def test_controlled_probe_configs_are_isolated_from_productive_worker() -> None:
    productive = json.loads((EDGE_ROOT / "wrangler.json").read_text(encoding="utf-8"))
    probe = json.loads((EDGE_ROOT / "wrangler.probe.json").read_text(encoding="utf-8"))
    origin = json.loads((EDGE_ROOT / "wrangler.probe-origin.json").read_text(encoding="utf-8"))

    assert productive["name"] == "precios-sps-provenance"
    assert productive["main"] == "src/index.mjs"
    assert "PROBE_LEDGER" not in json.dumps(productive)
    assert "PROBE_ORIGIN_URL" not in json.dumps(productive)

    assert probe["name"] == "precios-sps-controlled-probe"
    assert probe["main"] == "src/probe-worker.mjs"
    assert probe["preview_urls"] is False
    assert "migrations" not in probe
    assert probe["durable_objects"]["bindings"] == [
        {
            "name": "PROBE_LEDGER",
            "class_name": "ProbeLedger",
        }
    ]
    assert probe["exports"]["ProbeLedger"] == {
        "type": "durable-object",
        "storage": "sqlite",
    }
    assert probe["version_metadata"]["binding"] == "CF_VERSION_METADATA"
    assert set(probe["secrets"]["required"]) == {
        "PROBE_RECEIPT_PRIVATE_KEY_PKCS8_B64URL",
        "PROBE_RECEIPT_PUBLIC_KEY_SPKI_B64URL",
        "PROBE_ORIGIN_URL",
    }
    assert "EDGE_RECEIPT_PRIVATE_KEY_PKCS8_B64URL" not in json.dumps(probe)

    assert origin["name"] == "precios-sps-controlled-origin"
    assert origin["main"] == "src/probe-origin.mjs"
    assert origin["preview_urls"] is False
    assert "durable_objects" not in origin
    assert "secrets" not in origin

    serialized = json.dumps({"probe": probe, "origin": origin})
    assert "BEGIN PRIVATE KEY" not in serialized
    assert "www.lacolonia.com" not in serialized
