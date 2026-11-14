"""Permite ejecutar pytest desde la raíz del monorepositorio."""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import pytest  # noqa: E402

from precios_supermercados import capture_membership  # noqa: E402

_REAL_LOAD_POLICY = capture_membership.load_policy


@pytest.fixture(autouse=True)
def _unseen_offers_policy_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Las pruebas no dependen del interruptor versionado (config/catalog/unseen-offers-v1.json).

    Sin ruta explícita la política queda apagada; las pruebas que la necesitan la
    parchean o leen el archivo real con ``load_policy(POLICY_PATH)``.
    """

    def load_policy(path=None):  # type: ignore[no-untyped-def]
        return _REAL_LOAD_POLICY(path) if path is not None else capture_membership.UnseenOffersPolicy()

    monkeypatch.setattr(capture_membership, "load_policy", load_policy)
