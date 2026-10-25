from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "precios-supermercados-sps" / "scripts" / "resumir_delta_auditoria_ramas.py"


def _load_script() -> ModuleType:
    name = "precios_sps_historical_branch_audit_delta_test_module"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_delta_separa_nuevos_carry_forward_stale_y_open() -> None:
    module = _load_script()
    rows = {
        "feat/precios-sps-old": {"category": "UNIQUE_UNMERGED"},
        "feat/precios-sps-new": {
            "category": "UNIQUE_UNMERGED",
            "tip_sha": "a" * 40,
            "unique_patch_count": 1,
            "closed_unmerged_prs": [99],
            "unique_commit_subjects": ["nuevo"],
            "changed_files": ["precios-supermercados-sps/new.py"],
        },
        "feat/precios-sps-merged": {"category": "MERGED_OR_SUBSUMED"},
        "feat/precios-sps-open": {"category": "OPEN_CURRENT", "open_prs": [100]},
    }
    rendered = module.render_delta(
        rows,
        "1" * 40,
        {
            "feat/precios-sps-old": "old decision",
            "feat/precios-sps-merged": "stale decision",
        },
        "2" * 40,
    )

    assert "decisiones previas todavía UNIQUE_UNMERGED: **1**" in rendered
    assert "candidatos UNIQUE_UNMERGED nuevos: **1**" in rendered
    assert "overrides previos que ya no son UNIQUE_UNMERGED: **1**" in rendered
    assert "ramas OPEN_CURRENT: **1**" in rendered
    assert "`feat/precios-sps-new`" in rendered
    assert "`feat/precios-sps-merged` -> `MERGED_OR_SUBSUMED`" in rendered
    assert "`feat/precios-sps-open` -> #100" in rendered
