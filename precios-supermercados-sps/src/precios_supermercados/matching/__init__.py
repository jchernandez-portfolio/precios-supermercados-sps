"""Motor de resolución de entidades (shadow) para homologación de productos.

Pipeline: estandarización → blocking → generación de candidatos → vectores de
comparación → scoring probabilístico Fellegi–Sunter → clustering con
restricciones → umbrales (auto / revisión / no-match) → revisión humana →
evaluación.

El motor opera en modo ``shadow``: produce candidatos, scores, clusters, cola de
revisión y métricas, pero nunca cambia la comparabilidad publicada. Sólo las
decisiones humanas importadas al registro existente, y únicamente detrás del
gate de política (``config/homologation/identity-policy-v1.yaml``), pueden
llegar a persistencia. Ver ``docs/homologation/matching-engine-v1.md``.
"""

MATCHING_ENGINE_VERSION = "matching-engine-v1"
MATCHING_OUTPUT_SCHEMA = "precios-sps-matching-engine-shadow/v1"

__all__ = ["MATCHING_ENGINE_VERSION", "MATCHING_OUTPUT_SCHEMA"]
