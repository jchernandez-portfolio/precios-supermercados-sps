# Estado actual — Retail Price Intelligence / Precios de Supermercados SPS

GitHub `main`, GitHub Actions, los artifacts productivos, Turso y la rama `portfolio-data` son la fuente de verdad técnica. Este archivo contiene únicamente el estado **vigente**; los estados anteriores se conservan como snapshots (el más reciente: [`PROJECT_STATE_HISTORY_2026-09-10.md`](PROJECT_STATE_HISTORY_2026-09-10.md)).

## Checkpoint vigente — 2026-09-30

| Tema | Estado |
| --- | --- |
| Producto | Implementado: Compra Inteligente B2C en **dos ciudades** (San Pedro Sula y Tegucigalpa) con pestañas **Análisis** y **Compra Inteligente**, Business Mart B2B para Power BI, identidad de producto v2.3 auditada. |
| Último corte público aceptado | **2026-09-21** (run `35612789207`, `as_of = 2026-09-21T19:56Z`). |
| Operación diaria | **Detenida desde el 2026-09-22.** Nueve corridas programadas seguidas (22–30 sep) terminaron en `failure` tras sus tres intentos. Ver [Incidente vigente](#incidente-vigente--sin-cortes-aceptados-desde-el-2026-09-22). |
| Repositorio | Desde el 2026-09-30 el proyecto vive en `jchernandez-portfolio/precios-supermercados-sps`, separado del antiguo monorepo `Jchernand3z19/Portafolio` con su historial. Ver [Migración de repositorio](#migración-de-repositorio-2026-09-30). |

## Incidente vigente — sin cortes aceptados desde el 2026-09-22

El diseño fail-closed funcionó: ninguna corrida fallida reemplazó el último estado válido, por eso el sitio sigue mostrando el corte del 21 de septiembre con su fecha. Las causas observadas en los logs de GitHub Actions son dos y se suman:

1. **Cuota de lecturas de Turso agotada (22–23 sep).** El job `persist` falló en el paso *Asegurar esquema Paiz en Turso* con `SQL read operations are forbidden (reads are blocked, do you need to upgrade your plan?)` (código `BLOCKED`). El plan Starter no tiene excedentes: al agotar las filas leídas del mes, Turso bloquea toda lectura. Ya había ocurrido en agosto (ver `PROJECT_STATE_HISTORY_2026-09-02.md`).
2. **Extractor de Colonial roto (desde el 24 sep) — corregido en código el 2026-09-30.** El job `acquire (colonial)` fallaba en los tres intentos con `ColonialError: card_shape_invalid`. Causa observada en vivo (con autorización del usuario): el botón disponible de la tarjeta cambió de `addtocart-btn` a `add_to_cart_btn_cls`, el agotado quedó sólo con `cp-sold-out` (`disabled`, `aria-disabled="true"`) y el precio regular pasó de `<del>` a `<s class="lc-pcard__price-was">`. El total (`9137 productos`), la grilla, el input `id` y el enlace `/products/` no cambiaron. En 40 páginas (960 tarjetas) cada tarjeta tiene exactamente un botón de acción. El extractor acepta el markup nuevo y el anterior y sigue siendo fail-closed (fixture `collection-section-2026-09-30.html`). Como la persistencia exige handoffs de **todas** las cadenas, mientras estuvo roto bloqueó el corte completo.

El punto 1 requiere reducir lecturas (sección siguiente) o un plan de Turso con más cuota.

### Consumo de lecturas de Turso (diagnóstico 2026-09-30)

Auditoría estática del código (sin consultar Turso). Las cifras son estimaciones de filas leídas:

| Paso del ciclo diario | Lecturas estimadas/día | Causa principal |
| --- | ---: | --- |
| Exportación del catálogo TGU | ~10–14 M | paginación por `(product_id > ? OR (product_id = ? AND location_id > ?))`, que obliga a recorrer desde el inicio en cada página; además lee las ofertas visibles dos veces |
| Homologación derivada (dry-run + apply) | ~6–8 M | relee todos los productos y perfiles dos veces aunque sólo cambien los nuevos |
| Exportación del catálogo SPS | ~5–7 M | misma paginación cuadrática sobre ofertas abiertas y `price_history` |
| Verificaciones globales en `persist` y en el workflow | ~4 M | `PRAGMA integrity_check` (×5), `foreign_key_check` (×6), `COUNT(*)` de `price_history` (×7) y la migración de Paiz, ya aplicada, que corre cada día |
| Exportación RPI y escritura de 11 contextos | ~3–4 M | paginación con ordenamiento temporal; ~13 lecturas de guarda por SKU |

Total estimado **25–35 M filas/día (~0,8–1 B/mes)** contra una cuota de 500 M/mes, coherente con el bloqueo del 22 de septiembre. El costo crece cada día porque `price_history` crece.

Plan de optimización priorizado (pendiente de implementar):

1. Paginar por comparación de tuplas o por contexto (`WHERE location_id = ? AND valid_to_utc IS NULL AND product_id > ?`), agregar `idx_price_history (location_id, product_id, valid_from_utc)` y no releer ofertas en TGU (ahorro ~20 M/día).
2. Sacar `integrity_check`, `foreign_key_check` y `COUNT(*)` globales del camino diario: verificar sólo por `scrape_run_id`/contexto y mover las verificaciones completas a un workflow semanal o manual.
3. Ejecutar `migrar_mvp_paiz.py --turso` sólo cuando cambie la solicitud de migración.
4. Homologación incremental: procesar sólo los `product_id` insertados/editados por `persist` y eliminar el dry-run separado.
5. Publicar desde artifacts del día y leer de Turso sólo el delta de historial.

Con 1–4 la estimación baja a ~75–100 M/mes (15–20 % de la cuota).

## Frecuencia de actualización

La información se actualiza **una sola vez al día**:

| Paso | Veces por día |
| --- | --- |
| Scraping (6 cadenas, 11 contextos) | 1, a las **01:43** de Honduras (`43 7 * * *`, puede retrasarse por el scheduler de GitHub). Sólo si una cadena falla, el operador re-ejecuta **esa** cadena a las 08:17 y 12:17 (máximo 3 intentos en total). |
| Persistencia en Turso | 1 (sólo cuando todas las cadenas tienen handoff aceptado) |
| Homologación + publicación RPI + `portfolio-data` | 1, encadenadas por `workflow_run`; los intentos fallidos sólo generan ejecuciones `skipped` |

No existen otros crons de este proyecto que hagan scraping o lean Turso. Los crons `17 11` y `30 12` que aparecían en el monorepo pertenecen al proyecto Mundial 2026.

Evidencia del esquema de recuperación (#455 + #461 + #462): entre el **12 y el 21 de septiembre** hubo **10 cortes programados aceptados seguidos**. Sólo 3 pasaron en el primer intento; 6 necesitaron la primera recuperación y 1 la segunda. El mecanismo funciona, y a la vez muestra que el intento inicial falla con frecuencia por inestabilidad de las fuentes.

Recuperación vigente:

- la adquisición diaria se ejecuta de forma aislada por cadena, con `fail-fast` desactivado para permitir que las demás fuentes terminen aunque una falle;
- cada cadena sólo entrega un handoff reutilizable después de superar sus validaciones de completitud/ubicación;
- la persistencia global sigue siendo fail-closed y sólo comienza cuando existe un handoff aceptado de todas las cadenas;
- los artifacts quedan ligados a `run_id` + `run_attempt`, por lo que una recuperación puede reutilizar las capturas válidas de intentos anteriores;
- el operador revisa el run programado del mismo día a las **08:17** y **12:17** de Honduras;
- sólo `failure`/`timed_out` son recuperables automáticamente y el límite es **tres intentos totales** (inicial + hasta dos recuperaciones);
- la recuperación vuelve a ejecutar los jobs fallidos y sus dependencias, no crea un crawl programado nuevo si el run diario no existe.

Walmart aplica además una recuperación local y acotada cuando el total de una categoría cambia durante la comprobación final. El extractor espera 120 segundos, exige dos lecturas concordantes —facetas y búsqueda— separadas por 60 segundos y vuelve a descargar únicamente la categoría afectada. Si las fuentes aún discrepan o cambian durante la recaptura, realiza un segundo y último ciclo después de 600 segundos. Nunca mezcla páginas anteriores y posteriores al cambio; si no logra una membresía exacta y una confirmación final estable, conserva el último snapshot válido y deja que el operador global reintente sólo el job fallido.

Los correos de fallo de GitHub Actions son una preferencia de la cuenta de GitHub y no una propiedad del repositorio.

## Compra Inteligente B2C

La interfaz pública consume únicamente archivos estáticos publicados y valida tamaños/SHA-256. No consulta Turso y no hace matching en JavaScript. Es un **planificador de compra**, no una tienda ni un checkout. Se publica en el sitio del portafolio (`jchernandez-portfolio.github.io/Portafolio/precios-supermercados-sps/b2c/`) como copia de [`b2c/`](../b2c/) y lee los datos de la rama `portfolio-data` de este repositorio.

### Ciudades y alcance

`rpi-consumer-city-index/v1` (`v3/cities.json`) publica dos ciudades; San Pedro Sula es la predeterminada:

| Ciudad | Contextos públicos |
| --- | --- |
| San Pedro Sula | La Colonia, Colonial, Walmart, PriceSmart, Comisariato Los Andes |
| Tegucigalpa | La Colonia, Walmart FFAA, Walmart El Sauce, PriceSmart Florencia, Paiz Multiplaza, Paiz Próceres |

En Tegucigalpa las sucursales de una misma cadena se mantienen separadas; no se fusionan precios entre sucursales.

### Corte publicado vigente (2026-09-21)

| Métrica | San Pedro Sula | Tegucigalpa |
| --- | ---: | ---: |
| Filas visibles | 36,886 | 33,370 |
| Ofertas fuente | 39,498 | 59,786 |
| Filas comparables | 2,612 | 8,321 |
| `single_source` | 10,242 | 17,504 |
| Individuales | 24,032 | 7,545 |
| Particiones (máx. 250 filas) | 453 | 384 |
| Ofertas con resumen histórico | 39,362 | 57,826 |
| Promociones activas | 2,502 | 3,438 |
| Productos comparables con diferencia de precio | 1,962 | 6,611 |

Frente al corte del 2026-09-10 (44,042 filas visibles en SPS) la reducción es esperada: desde el 2026-09-10 se ocultan filas que no se pueden comprar y la identidad v2.3 cambió cómo se agrupan marca y presentación. Las cinco fuentes SPS y las seis de TGU figuraban `FRESH` en ese corte.

Política de comparación vigente: `persisted_ready_identity_without_retailer_collision_and_fresh_prices` (identidad persistida *ready*, sin dos ofertas del mismo comercio en la fila y con precios frescos). Política de visibilidad: `accepted_current_source_offers_in_sps_scope` / `..._in_city_scope`.

El Consumer Mart v2 sigue `COMPARABLE` con 92 productos en el universo analítico seguro de La Colonia SPS + Walmart SPS.

### Funciones

- pestaña **Análisis** (`rpi-consumer-analysis/v1`, `analysis-sps.json` por ciudad): productos visibles y comparables, promociones, ahorro unitario observado, movimientos de precio contra el corte anterior, cobertura y "mejores precios" por supermercado, oportunidades (bajadas, subidas, promociones, mínimos recientes) y liderazgo por categoría;
- pestaña **Compra Inteligente**: selector de ciudad, navegación por facetas, búsqueda, matriz por supermercado/sucursal, selección manual de oferta exacta, cantidades y alta por lote;
- `Mi Compra` persistida localmente por ciudad, agrupada por supermercado, con actualización explícita de precios y sin sustitución silenciosa;
- escenarios de canasta manual, por un solo supermercado y optimización por mejor precio seguro;
- contexto histórico por oferta, exportación CSV/PDF y compartir por WhatsApp (incluye la ciudad);
- indicador de fecha/estado del último corte aceptado;
- assets versionados (`?v=`) para que GitHub Pages no sirva una mezcla de versiones.

Contrato monetario:

```text
unit_price = current_price
line_total = unit_price * quantity
retailer_subtotal = sum(line_total)
grand_total = sum(retailer_subtotal)
```

`reported_regular_price` es sólo referencia. No se inventan ISV, impuestos, delivery, service fees, membership fees ni otros cargos de checkout. Si una línea no tiene precio utilizable, los totales dependientes quedan incompletos; nunca se imputa cero.

## Business Mart y Power BI

`rpi-business-mart/v1` incluye:

- dimensiones de producto, retailer, ubicación, categoría y marca;
- `fact_current_comparison`;
- `fact_price_history` sobre periodos comerciales realmente persistidos;
- `fact_promotion_analysis`;
- `fact_basket_cost`;
- `fact_metric_coverage`;
- `source_freshness`.

Los cambios de precio, PCI, ranking, deltas, freshness y semántica promocional/histórica se calculan en Python. Los activos reproducibles de `powerbi/rpi/` contienen Power Query, DAX, relaciones, tema y especificación de las nueve páginas. El repositorio **no fabrica ni versiona un `.pbix` simulado**: un PBIX final, si se desea como archivo binario de presentación, se construye en Power BI Desktop a partir de esos activos.

## Homologación e identidad de producto

La homologación sigue siendo conservadora: GTIN/evidencia fuerte puede establecer identidad, mientras los matches por similitud quedan fuera de la comparación automática.

Avances posteriores al 2026-09-10:

- **Motor de identidad v2 auditado** (#478–#483): bloquea contradicciones demostradas de sabor, tamaño, etapa y tono; interpreta packs comerciales y multipacks compactos; normaliza alias de marca con evidencia; corre como auditoría privada read-only sin cambiar el serving.
- **Identidad v2.3 persistida y desplegada** (#484–#485): perfiles derivados con campos canónicos que preservan el dato fuente, plan dry-run y guardas transaccionales; el B2C consume marca y presentación normalizadas.
- **Estándar de identidad v1 y decisiones revisadas** (#490): relaciones `EXACT_TRADE_ITEM`, `VERIFIED_EQUIVALENT`, `PRODUCT_VARIANT`, `COMPARABLE_ALTERNATIVE`, `UNRESOLVED` y `CONFLICT`; decisiones humanas ligadas a la huella de la evidencia, pares *golden* para evaluación y política `identity-policy-v1.yaml`. Ver [`homologation/product-identity-standard-v1.md`](homologation/product-identity-standard-v1.md).
- **En borrador:** evidencia de imagen de producto para la revisión humana (PR borrador `rpi/product-image-evidence-v1`, aún no fusionado).

La cola privada de revisión (#460) materializa, bajo ejecución manual y read-only, candidatos fuzzy `review_required`, conflictos entre registros con el mismo GTIN y productos sin taxonomía suficiente. No se publica en Compra Inteligente ni modifica precios por sí sola.

## Autoridad live y binding SPS

La evidencia histórica o una autorización temporal consumida **no se interpreta como autorización abierta**. Cualquier nueva observación live fuera de los workflows productivos ya autorizados por su ejecución recurrente **requiere autorización humana explícita vigente** para ese alcance.

El entrypoint manual de binding de ubicación permanece cerrado por defecto y no tiene autorizaciones activas:

```text
ACTIVE_AUTHORIZATION_IDS = []
```

El fingerprint canónico de la evidencia de región SPS que debe seguir coincidiendo con el contrato productivo es:

```text
d7732eccc99c8530a6d29cce4244920e65e85c1d5492facb05469dc3589cb8b7
```

Ese fingerprint demuestra continuidad de la evidencia técnica de binding; no concede por sí mismo autoridad para iniciar tráfico live.

## Cadena de publicación vigente

Tras una actualización aceptada:

```text
captura validada (por cadena)
→ persistencia global fail-closed en Turso / histórico aceptado
→ refresh de homologación derivada
→ exportación RPI read-only (marts, catálogos por ciudad, análisis)
→ validación de schemas, scope, hashes y secretos
→ publicación atómica en portfolio-data
→ Compra Inteligente consume sólo archivos estáticos
```

La publicación pública incluye Consumer Mart v2, Consumer Catalog v3 por ciudad, análisis por ciudad, índice de ciudades y la muestra de portafolio. **Business Mart permanece privado**.

## Migración de repositorio (2026-09-30)

- Nuevo repositorio: `jchernandez-portfolio/precios-supermercados-sps`, con historial; el código sigue en `precios-supermercados-sps/` para no cambiar rutas de workflows, tests ni datos.
- Ramas: sólo `main` y `portfolio-data`. El historial completo de ramas del monorepo permanece en `jchernandez-portfolio/Portafolio`.
- Las referencias a `Jchernand3z19/Portafolio` (gates `github.repository`, OIDC de Cloudflare, URLs raw de `portfolio-data`, tests) se cambiaron a `jchernandez-portfolio/precios-supermercados-sps`.
- **Pendiente para reanudar la operación en el repo nuevo:**
  - volver a crear los secrets y environments (Turso, Cloudflare, BigQuery, Google Sheets, `la-colonia-live`, `cloudflare-probe`); GitHub no los copia entre repos;
  - actualizar en Cloudflare la confianza OIDC y redesplegar el Worker de `edge/cloudflare` con el nuevo nombre del repo;
  - habilitar notificaciones de Actions para este repo.
- Hasta completar esos pasos, las corridas programadas del repo nuevo fallarán por falta de credenciales.

## Límites vigentes

- Paiz no tiene un contexto SPS aceptado; sus contextos demostrados son Multiplaza y Próceres en Tegucigalpa.
- PriceSmart El Sauce 6604 permanece excluido.
- Maxi Despensa y Despensa Familiar continúan en **NO-GO TEMPORAL PARA PRICE TRACKING WEB**.
- Un dato `STALE`, `UNAVAILABLE`, ambiguo o sin suficiente cobertura no puede producir ranking/PCI/recomendación competitiva nueva.
- Los documentos históricos de incidentes se preservan tal como fueron emitidos; este archivo es el único resumen mutable del estado presente.

## Próximos pasos

1. Configurar secrets/environments y Cloudflare en el repo nuevo.
2. Reducir lecturas de Turso (plan de optimización) o ampliar el plan.
3. Con autorización explícita, diagnosticar y corregir el extractor de Colonial (`card_shape_invalid`).
4. Revisar la homologación entre supermercados (cobertura de identidades comparables y prácticas de matching).
