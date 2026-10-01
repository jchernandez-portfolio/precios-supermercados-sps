"""Configuración versionada del motor (YAML) con validación mínima."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

from . import MATCHING_ENGINE_VERSION

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "homologation" / "matching-engine-v1.yaml"
DEFAULT_TAXONOMY_PATH = PROJECT_ROOT / "config" / "homologation" / "source-category-taxonomy-v1.yaml"
DEFAULT_POLICY_PATH = PROJECT_ROOT / "config" / "homologation" / "identity-policy-v1.yaml"
DEFAULT_DECISIONS_PATH = PROJECT_ROOT / "config" / "homologation" / "reviewed-decisions-v1.json"

CONFIG_SCHEMA = "precios-sps-matching-engine-config/v1"


class MatchingConfigError(ValueError):
    """La configuración del motor no es válida."""


@dataclass(frozen=True)
class EngineConfig:
    raw: Mapping[str, Any]
    variant_vocabulary: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    implicit_defaults: Mapping[str, frozenset[str]] = field(default_factory=dict)
    token_synonyms: Mapping[str, str] = field(default_factory=dict)

    def section(self, name: str) -> Mapping[str, Any]:
        value = self.raw.get(name)
        if not isinstance(value, Mapping):
            raise MatchingConfigError(f"config_section_missing:{name}")
        return value

    def value(self, section: str, key: str) -> Any:
        container = self.section(section)
        if key not in container:
            raise MatchingConfigError(f"config_value_missing:{section}.{key}")
        return container[key]


def _validate(raw: Mapping[str, Any]) -> None:
    if raw.get("schema") != CONFIG_SCHEMA:
        raise MatchingConfigError("config_schema_invalid")
    if raw.get("engine_version") != MATCHING_ENGINE_VERSION:
        raise MatchingConfigError("config_engine_version_invalid")
    if raw.get("mode") != "shadow":
        raise MatchingConfigError("config_mode_must_be_shadow")
    thresholds = raw.get("thresholds") or {}
    target = thresholds.get("target_precision")
    if not isinstance(target, (int, float)) or not 0.5 <= float(target) <= 1.0:
        raise MatchingConfigError("config_target_precision_invalid")
    name_levels = (raw.get("comparison") or {}).get("name_levels")
    if not isinstance(name_levels, list) or sorted(name_levels, reverse=True) != name_levels:
        raise MatchingConfigError("config_name_levels_invalid")
    vocabulary = raw.get("variant_vocabulary")
    if not isinstance(vocabulary, Mapping) or not vocabulary:
        raise MatchingConfigError("config_variant_vocabulary_invalid")


def load_engine_config(path: Path = DEFAULT_CONFIG_PATH) -> EngineConfig:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise MatchingConfigError("config_unreadable") from exc
    if not isinstance(raw, Mapping):
        raise MatchingConfigError("config_shape_invalid")
    _validate(raw)
    vocabulary = {
        str(family): {str(term): str(value) for term, value in (terms or {}).items()}
        for family, terms in raw["variant_vocabulary"].items()
    }
    defaults_raw = raw.get("implicit_defaults") or {}
    if not isinstance(defaults_raw, Mapping):
        raise MatchingConfigError("config_implicit_defaults_invalid")
    implicit = {str(family): frozenset(str(value) for value in values or ()) for family, values in defaults_raw.items()}
    synonyms_raw = raw.get("token_synonyms") or {}
    if not isinstance(synonyms_raw, Mapping):
        raise MatchingConfigError("config_token_synonyms_invalid")
    synonyms = {str(key): str(value) for key, value in synonyms_raw.items()}
    return EngineConfig(raw=raw, variant_vocabulary=vocabulary, implicit_defaults=implicit, token_synonyms=synonyms)
