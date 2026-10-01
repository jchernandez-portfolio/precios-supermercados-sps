# Auditoría de ramas históricas `precios-sps`

Fecha de corte: 2026-08-22  
Repositorio: `jchernandez-portfolio/precios-supermercados-sps`  
Snapshot de `main`: `94bf90671d92046515e0820894d083826d676358`  
Workflow de evidencia: `Precios Supermercados SPS - Pruebas base`, run `32606702456`

## Resultado

Se inventariaron todas las ramas remotas cuyo nombre contiene `precios-sps` contra el snapshot anterior de `main`.

| Categoría | Cantidad |
|---|---:|
| `MERGED_OR_SUBSUMED` | 102 |
| `CLOSED_SUPERSEDED` | 55 |
| `OPEN_CURRENT` | 1 |
| `UNIQUE_UNMERGED` | 0 |
| **Total** | **158** |

La única rama `OPEN_CURRENT` durante la auditoría fue `audit/precios-sps-historical-branches`, correspondiente al PR #160 que produce este inventario. No quedaron ramas con patches únicos sin inspección.

## Método reproducible

El script `scripts/auditar_ramas_historicas.py` clasifica primero mediante evidencia Git:

1. `git merge-base --is-ancestor` para tips ya contenidos por `main`;
2. comparación de tree para contenido idéntico;
3. `git cherry` para detectar patch-equivalence cuando el historial diverge;
4. consulta del PR del head exacto sólo cuando quedan patches únicos;
5. captura de subjects y archivos de los commits únicos para inspección.

Las ramas que seguían siendo `UNIQUE_UNMERGED` se inspeccionaron individualmente. La decisión manual queda versionada en `docs/audits/precios-sps-historical-branch-overrides.json`, ligada al SHA exacto de `main`. El script falla cerrado si:

- cambia el snapshot de `main` sin renovar la inspección;
- aparece una rama nueva no resuelta;
- un override apunta a una rama ausente;
- un override intenta reclasificar algo que ya no es `UNIQUE_UNMERGED`;
- queda cualquier `UNIQUE_UNMERGED` después de aplicar las decisiones versionadas.

## Hallazgos relevantes

- Los marcadores de Observability `diag/precios-sps-observability-request-001` y `diag/precios-sps-observability-request-007` eran artefactos diagnósticos históricos; sus PR #107 y #117 fueron cerrados deliberadamente sin merge.
- `fix/precios-sps-observability-ci-route` corresponde al experimento del PR #119 que aisló el uso inválido de `runner.temp` en `env` a nivel de job; no debe revivirse como ruta operativa.
- `feat/precios-sps-verified-catalog-transport` fue cerrado explícitamente porque PR #77 incorporó la frontera equivalente y cobertura adicional.
- `feat/precios-sps-catalog-acceptance-readiness` quedó sustituido por PR #83, ya integrado.
- `feat/precios-sps-catalog-physical-finalizer` contenía hardening útil posterior al cierre del PR #80; ese hardening fue recuperado de forma focalizada e integrado en PR #158 antes de cerrar esta auditoría.
- Las ramas comerciales antiguas de identidad, snapshots, replay, pricing y evidencia latest corresponden a estados intermedios de capacidades que ya están presentes y endurecidas en `main`.
- La documentación del collector GCP/Cloud Run/SWP/KMS es histórica; la arquitectura canónica posterior migró a Cloudflare y no se recupera ese diseño obsoleto.

## Validación

El run `32606702456` ejecutó el inventario contra todas las ramas remotas y obtuvo:

- `CLOSED_SUPERSEDED=55`;
- `MERGED_OR_SUBSUMED=102`;
- `OPEN_CURRENT=1`;
- `UNIQUE_UNMERGED=0`.

En el mismo merge-ref, la suite completa terminó con **1473/1473 pruebas aprobadas** en Python 3.12.14 / pytest 9.0.3.

## Seguridad

Esta auditoría sólo leyó Git/GitHub. No desplegó infraestructura, no consumió secretos del producto, no modificó ramas históricas y no realizó requests a La Colonia.

Los invariantes productivos permanecen sin cambio:

- `ACTIVE_AUTHORIZATION_IDS=[]`;
- `LIVE_REQUESTS_CURRENT_RUN=0`;
- `READY_FOR_LIVE=NO`;
- `SPS_TECHNICAL_CONTEXT=UNCONFIRMED`;
- `production_authority=false`;
- `catalog_accepted=false`.
