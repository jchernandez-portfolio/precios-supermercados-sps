"""Gate de política: el motor nunca publica identidad por sí mismo.

Lee ``config/homologation/identity-policy-v1.yaml``. Mientras
``publication.mode`` sea ``shadow`` o ``public_serving_allowed`` sea falso, las
salidas del motor quedan marcadas como privadas y el importador de revisión
sólo puede escribir el registro privado de decisiones.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import yaml

POLICY_SCHEMA = "precios-sps-product-identity-policy/v1"


class IdentityPolicyError(ValueError):
    """La política de identidad no se pudo leer o no es válida."""


@dataclass(frozen=True, slots=True)
class PolicyGate:
    mode: str
    public_serving_allowed: bool
    require_offline_precision_before_enable: bool
    automatic_identity_allowed: tuple[str, ...]

    def engine_publication_allowed(self, *, measured_precision: float | None = None, target: float = 0.98) -> bool:
        """Sólo verdadero con política explícitamente habilitada y precisión medida."""

        if self.mode == "shadow" or not self.public_serving_allowed:
            return False
        if "probabilistic_auto_match_with_measured_precision" not in self.automatic_identity_allowed:
            return False
        if self.require_offline_precision_before_enable:
            return measured_precision is not None and measured_precision >= target
        return True

    def to_json(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "public_serving_allowed": self.public_serving_allowed,
            "require_offline_precision_before_enable": self.require_offline_precision_before_enable,
            "automatic_identity_allowed": list(self.automatic_identity_allowed),
            "engine_publication_allowed": self.engine_publication_allowed(),
        }


def gate_from_mapping(raw: Mapping[str, object]) -> PolicyGate:
    if raw.get("schema") != POLICY_SCHEMA:
        raise IdentityPolicyError("policy_schema_invalid")
    publication = raw.get("publication")
    automatic = raw.get("automatic_identity")
    if not isinstance(publication, Mapping) or not isinstance(automatic, Mapping):
        raise IdentityPolicyError("policy_shape_invalid")
    allowed = automatic.get("allowed") or []
    if not isinstance(allowed, list):
        raise IdentityPolicyError("policy_automatic_identity_invalid")
    return PolicyGate(
        mode=str(publication.get("mode")),
        public_serving_allowed=publication.get("public_serving_allowed") is True,
        require_offline_precision_before_enable=publication.get("require_offline_precision_before_enable") is not False,
        automatic_identity_allowed=tuple(str(item) for item in allowed),
    )


def load_policy_gate(path: Path) -> PolicyGate:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise IdentityPolicyError("policy_unreadable") from exc
    if not isinstance(raw, Mapping):
        raise IdentityPolicyError("policy_shape_invalid")
    return gate_from_mapping(raw)
