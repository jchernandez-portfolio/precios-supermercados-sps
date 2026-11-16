"""Conteos sueltos de Colonial ("10und", "50un"): N unidades, nunca "N×N".

La firma de conteo guarda ``pack_count = N`` y ``unit_amount_base = 1`` (así compara
y así entra en las huellas de las decisiones humanas). La medición del PR B la
mostraba como "10×10 count" y la cola de tamaños como "10×1 unit".
"""
from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from precios_supermercados.product_homologation import PresentationSignature, SourceProductRecord
from precios_supermercados.product_identity_v2 import (
    canonical_presentation_from_signature,
    resolve_presentation_v2,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import exportar_cola_tamanos as cola  # noqa: E402
import medir_pr_b as pr_b  # noqa: E402

SPEC = importlib.util.spec_from_file_location("medir_reglas_identidad", ROOT / "scripts" / "medir_reglas_identidad.py")
rules = sys.modules.get(SPEC.name)
if rules is None:
    rules = importlib.util.module_from_spec(SPEC)
    sys.modules[SPEC.name] = rules
    SPEC.loader.exec_module(rules)

# Casos reales de la medición del PR B (16-nov-2026).
CASOS = [
    ("KOOL AID Grape 10und", 10),
    ("SPLENDA Azucar Sobres 1g 50un", 50),
    ("SPLENDA Azucar Sobres 1g 200un", 200),
]


def resolve(name: str) -> tuple[PresentationSignature | None, str]:
    return resolve_presentation_v2(
        SourceProductRecord(
            source_record_id=f"colonial:{name}",
            supermarket_id="colonial",
            source_name=name,
            source_presentation=None,
        )
    )


@pytest.mark.parametrize(("name", "units"), CASOS)
def test_loose_count_is_n_units_in_one_package(name, units):
    signature, status = resolve(name)
    assert status == "name_only"
    assert signature is not None
    # La firma no cambia (comparación y huellas de decisiones intactas)...
    assert (signature.dimension, signature.total_base) == ("count", Decimal(units))
    assert (signature.pack_count, signature.unit_amount_base) == (units, Decimal("1"))
    # ...pero se muestra como un envase con N unidades.
    assert signature.display_pack_count == 1
    fields = canonical_presentation_from_signature(None, signature, status)
    assert (fields.normalized_pack_count, fields.display_presentation) == (1, f"{units} unidades")


@pytest.mark.parametrize(("name", "units"), CASOS)
def test_measurement_scripts_never_show_n_by_n(name, units):
    profile = SimpleNamespace(presentation=resolve(name)[0])
    assert pr_b._presentation_text(profile) == f"{units} count"
    assert rules._presentation_text(profile) == f"{units} count"


@pytest.mark.parametrize(("name", "units"), CASOS)
def test_size_queue_shows_the_count_without_pack(name, units):
    profile = SimpleNamespace(presentation=resolve(name)[0])
    assert cola.offer_size(profile) == {"value": str(units), "unit": "unit", "text": f"{units} unit"}


def test_real_multipacks_still_render_with_pack():
    six = PresentationSignature(dimension="volume_ml", total_base=Decimal("2130"), pack_count=6,
                                unit_amount_base=Decimal("355"))
    assert six.display_pack_count == 6
    profile = SimpleNamespace(presentation=six)
    assert pr_b._presentation_text(profile) == "6×2130 volume_ml"
    assert cola.offer_size(profile)["text"] == "6×355 ml"
