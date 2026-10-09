# Estado actual — Retail Price Intelligence / Precios de Supermercados SPS

GitHub `main`, GitHub Actions, los artifacts productivos, Turso y la rama `portfolio-data` son la fuente de verdad técnica. Este archivo contiene únicamente el estado **vigente**; los estados anteriores se conservan como snapshots (el más reciente: [`PROJECT_STATE_HISTORY_2026-09-10.md`](PROJECT_STATE_HISTORY_2026-09-10.md)).

## Checkpoint vigente — 2026-09-30

| Tema | Estado |
| --- | --- |
| Producto | Implementado: Compra Inteligente B2C en **dos ciudades** (San Pedro Sula y Tegucigalpa) con pestañas **Análisis** y **Compra Inteligente**, Business Mart B2B para Power BI, identidad de producto v2.3 auditada. |
| Último corte público aceptado | **2026-09-21** (run `35612789207`, `as_of = 2026-09-21T19:56Z`). |
| Operación diaria | **Detenida desde el 2026-09-22.** Nueve corridas programadas seguidas (22–30 sep) terminaron en `failure` tras sus tres intentos. Ver [Incidente vigente](#incidente-vigente--sin-cortes-aceptados-desde-el-2026-09-22). |
| Repositorio | Desde el 2026-09-30 el proyecto vive en `jchernandez-portfolio/precios-supermercados-sps`, separado del antiguo monorepo `Jchernand3z19/Portafolio` con su historial. Ver [Migración de repositorio](#migración-de-repositorio-2026-09-30). |

## Incidente 2026-10-02 — La Colonia lista productos sin precio

La corrida diaria "La Colonia - Actualización MVP" falló dos veces en `acquire (la_colonia)` con `SnapshotError: snapshot_sku_count_mismatch`: La Colonia publicó productos nuevos **sin precio y agotados** ("NO DISPONIBLE"), y la validación exigía precio en todo SKU. SPS y TGU: Dove 18658/18659 y Rexona 18660/18661 (~9.600 SKU por ciudad); TGU intento 1 además Dubois 18302. Como la persistencia exige las seis cadenas, ese día no se publicó nada.

Regla nueva (decisión del dueño):

- **Adquisición/snapshot:** los SKU sin precio y `out_of_stock` salen de `products` a `unpriced_unavailable` (+ `skus_unpriced_unavailable`). Se aceptan si son ≤2 % del catálogo (`MAX_UNPRICED_UNAVAILABLE_RATIO`, `src/precios_supermercados/unpriced_unavailable.py`) y todos están agotados; un SKU sin precio `in_stock`/`unknown` sigue fallando cerrado. Aplica a La Colonia (SPS/TGU) y al validador compartido con Colonial; Walmart/PriceSmart/Paiz ya representaban sus agotados sin precio dentro de `products` y Los Andes publica siempre `unknown` (sin precio sigue fallando).
- **Persistencia:** tabla STRICT `catalog_unpriced_observations` (first/last seen, `priced_since_utc`), creada de forma idempotente por la persistencia; sin `products`/`price_history` sin precio. Si el producto tenía un periodo current con precio, se cierra. Lecturas acotadas a las entradas del día (índices/PK) y a las pendientes del contexto (índice parcial).
- **Comparaciones:** una oferta `out_of_stock` nunca compara (ranking, mejor precio, diferencia, análisis, Mi Compra); `unknown` sí. Un grupo sólo es `comparable` ese día con ≥2 cadenas con ofertas no agotadas con precio; si no, se publica `individual` con el mismo `row_id`. Ver `COMPARATOR-METHODOLOGY.md`.

Impacto medido sobre el corte publicado del 2026-10-01 (`portfolio-data` 84722dc0): SPS 640 de 4.063 filas `comparable` pasan a `individual` (ya excluidas del `analysis`), 225 siguen comparables sin su oferta agotada; TGU 252 de 8.876 pasan a `individual`, 230 ofertas de dos sucursales de una misma cadena (Walmart 154, Paiz 76) pierden un ranking intra-cadena y `analysis` TGU baja de 8.739 a 8.624 productos comparables.

## Incidente 2026-10-03 — Colonial y primera corrida de especificaciones PriceSmart

- **Colonial `commercial_sources_disagree` (2026-10-02 relanzada y 2026-10-03 programada).** El JSON `products.json` se lee al inicio y las ~140 páginas de tarjetas HTML durante ~8 minutos; un cambio de precio en ese intervalo rechazaba todo Colonial y, como la persistencia exige las seis cadenas, ese día no se publicaba nada. Arreglo (rama `rpi/colonial-recheck`, aprobado por el responsable): se releen sólo los productos en desacuerdo (`/products/<handle>.json` y su página HTML con `recheck=1`) y, si las fuentes frescas coinciden, se usa el precio actual. Sigue fallando cerrado si no coinciden, si cambió la identidad, si la tarjeta se movió de página o si hay más de 25 en desacuerdo (`MAX_COMMERCIAL_RECHECKS`).
- **Especificaciones PriceSmart: 0 de 900 fichas parseadas** (run `37132418500`, 889 `no_specifications`, 10 `not_found`). El HTML del servidor (Nuxt SSR) no trae los valores: la página los carga después en el navegador. Nada se persistió (fail-closed). Arreglo aprobado por el responsable (2026-10-03): abrir la ficha pública con Chromium headless (Playwright), una página a la vez, cookies borradas por ficha, sólo requests a `www.pricesmart.com`, sin imágenes/fuentes/medios. El parser existente lee la tabla `td.specification`/`td.specification-data` del DOM renderizado (fixture real reducido `RENDERED-breadco-415586.html`); se quita la raíz "PriceSmart" del breadcrumb (`pricesmart-specs-parser/v2`). Corte temprano: si las primeras 30 fichas evaluables fallan > 80 %, se detiene (antes gastó 913 requests). 600 ítems por corrida.

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

Plan de optimización priorizado (puntos 1–3 implementados y 4 parcialmente en la rama `rpi/turso`; ver [Optimización Turso](#optimización-turso-2026-09-30)):

1. Paginar por comparación de tuplas o por contexto (`WHERE location_id = ? AND valid_to_utc IS NULL AND product_id > ?`), agregar `idx_price_history (location_id, product_id, valid_from_utc)` y no releer ofertas en TGU (ahorro ~20 M/día).
2. Sacar `integrity_check`, `foreign_key_check` y `COUNT(*)` globales del camino diario: verificar sólo por `scrape_run_id`/contexto y mover las verificaciones completas a un workflow semanal o manual.
3. Ejecutar `migrar_mvp_paiz.py --turso` sólo cuando cambie la solicitud de migración.
4. Homologación incremental: procesar sólo los `product_id` insertados/editados por `persist` y eliminar el dry-run separado.
5. Publicar desde artifacts del día y leer de Turso sólo el delta de historial.

Con 1–4 la estimación baja a ~75–100 M/mes (15–20 % de la cuota).

### Optimización Turso (2026-09-30)

Cambios de la rama `rpi/turso` (sin cambiar resultados: los exportadores producen los mismos archivos byte a byte, cubierto por `tests/test_turso_rows_read_optimization.py`):

- **Exportadores** (`exportar_consumer_catalog_core.py`, `exportar_modelo_analitico.py`): las ofertas current se paginan por contexto exacto (`supermarket_id=? AND location_id=? AND valid_to_utc IS NULL AND product_id>?`) sobre `idx_price_history_current` y se reordenan en Python por `(product_id, location_id)`; cada fila current se lee una vez. El histórico se pagina por contexto con `(product_id, valid_from_utc) > (?, ?)` sobre el índice nuevo `idx_ph_loc_hist`; si el índice aún no existe usa el keyset de fila `(product_id, location_id, valid_from_utc) > (?, ?, ?)` sobre la PK (una pasada lineal).
- **Índice nuevo** `idx_ph_loc_hist ON price_history(location_id, product_id, valid_from_utc)`: definido junto al esquema (`generar_mvp_sqlite_la_colonia.HISTORY_INDEX_SQL`, fuera de `create_schema` para no alterar las huellas congeladas de Walmart/PriceSmart) y creado de forma idempotente por `migrar_mvp_paiz.py` (SQLite y Turso). Su creación en Turso lee `price_history` una sola vez.
- **TGU** (`exportar_consumer_catalog_tgu.py`): reutiliza la misma lectura de ofertas para los conteos por contexto y para el export.
- **Migración Paiz diaria** (`migrar_mvp_paiz.py --turso`): si el esquema, los contextos y el índice ya están aplicados es un no-op que sólo lee `sqlite_master` y las filas Paiz; los `COUNT(*)`, `integrity_check` y `foreign_key_check` quedan sólo para una migración estructural real.
- **Workflow diario** (`la-colonia-mvp-update`): conserva como sanity check barato la confirmación de runs y los conteos current por contexto; los periodos abiertos duplicados globales, `foreign_key_check` e `integrity_check` pasaron al workflow semanal **`precios-supermercados-sps-turso-weekly-integrity.yml`** (domingo 04:23 Honduras + manual, `scripts/verificar_integridad_turso.py`, sólo lectura, secrets `TURSO_DATABASE_URL`/`TURSO_AUTH_TOKEN`).
- **Homologación** (`backfill_homologacion_turso.py`): ya no lee `price_history` ni corre `integrity_check`/`foreign_key_check` global; verifica FK sólo de `product_homologation_profiles`. El `--dry-run` remoto ya no corre en el refresco diario encadenado (`workflow_run`), sólo en ejecuciones manuales o por solicitud.
- **Persistencia por SKU** (`actualizar_mvp_turso_la_colonia.py`, ya set-based): las guardas de cardinalidad usan `changes()` del INSERT anterior en vez de `COUNT(*)` sobre la tabla recién cargada y la verificación final sólo busca en `price_history` los productos que cambiaron (~3 de ~13 lecturas por SKU menos).

Estimación: los exportadores pasan de leer ~páginas × filas del scope (cuadrático) a leer cada fila del scope una vez; en un sintético a escala (~35 k filas) el trabajo de la VM baja 4–5× con 2000 filas/página y crece con el número de páginas en producción. Las verificaciones globales diarias (≈4 M/día) y la mitad de la homologación (dry-run) desaparecen del ciclo diario.

TODO (no implementado por riesgo semántico):

- Homologación incremental real (sólo `product_id` cuyos datos fuente cambiaron, con bandera de corrida completa): `products` no tiene marca de modificación, así que hoy se releen `products` y perfiles completos (≈2×P filas/día, ya sin `price_history`).
- Publicar desde los artifacts del día y leer de Turso sólo el delta de historial.
- `fetch_products` de los exportadores sigue recorriendo `products` por PK (lineal) sin filtrar por cadena con índice.

## Frecuencia de actualización

La información se actualiza **una sola vez al día**:

| Paso | Veces por día |
| --- | --- |
| Scraping (6 cadenas, 11 contextos) | 1, a las **05:17** de Honduras, disparado por el Worker de Cloudflare `edge/daily-trigger` (`17 11 * * *` UTC, `workflow_dispatch`); el cron de GitHub se quitó porque llegaba 5–9 h tarde. Respaldo: si a las 08:17 no hay corte del día, el operador lo arranca. Sólo si una cadena falla, el operador re-ejecuta **esa** cadena a las 08:17 y 12:17 (máximo 3 intentos en total). |
| Persistencia en Turso | 1 (sólo cuando todas las cadenas tienen handoff aceptado) |
| Homologación + publicación RPI + `portfolio-data` | 1, encadenadas por `workflow_run`; los intentos fallidos sólo generan ejecuciones `skipped` |

Además, `precios-supermercados-sps-turso-weekly-integrity.yml` lee Turso una vez por semana (domingo 04:23 de Honduras, `23 10 * * 0`) para las verificaciones completas de integridad, y `precios-supermercados-sps-pricesmart-specs-weekly.yml` (rama `rpi/pricesmart-specs`) captura especificaciones de fichas PriceSmart una vez por semana (sábado 10:37 de Honduras, `37 16 * * 6`, después del corte para no leer PriceSmart en paralelo; ver [Especificaciones PriceSmart](#especificaciones-pricesmart-semanal-2026-10-01)). No existen otros crons de este proyecto que hagan scraping o lean Turso. Los crons de GitHub `17 11` y `30 12` que aparecían en el monorepo pertenecen al proyecto Mundial 2026 (el `17 11 * * *` del corte diario vive en Cloudflare, no en GitHub).

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

## Homologación — mejoras rápidas 2026-09-30

Rama `rpi/homolog-quick` (no fusionada). Motor `product-homologation-v2.4`; al
fusionarse, el refresh diario de homologación reescribe los perfiles derivados
con la nueva versión. Política y casos en
[`homologation/product-identity-standard-v1.md`](homologation/product-identity-standard-v1.md) (reglas 8–14).

- **Captura de GTIN:** Colonial no publica `barcode` (null en las 9,205
  variantes de 2026-08-30); el UPC/EAN vive en `sku`. Con aprobación del
  usuario (2026-10-01), un `sku` recortado, todo dígitos, de 8/12/13/14 dígitos
  y GS1 válido pasa a `ean` (8,501 de 9,205); `reference` sigue siendo el SKU y
  la procedencia se deriva (`ean == reference` ⇒ `sku_gs1_valid`) porque el
  contrato de snapshot tiene llaves cerradas. Ese GTIN conserva todos los
  conflictos del grupo y, como un SKU GS1 válido puede ser el código
  equivocado, además exige: marca no contradictoria (`sku_gtin_brand_conflict`),
  acuerdo mínimo de nombre sin marca ni tamaño —al menos un token común y ≥1/3
  de los tokens del lado más corto— (`sku_gtin_name_disagreement`) y, en
  maquillaje/tinte, el mismo número de tono/modelo (`model_number_conflict`).
  PriceSmart (Bloomreach) y Comisariato no exponen barcode (Comisariato: el
  GTIN se reconstruye desde `code` desde v2.6, ver sección 2026-10-01).
- **GTIN restringidos GS1:** sólo forman identidad dentro de un maestro
  compartido (`walmart_cam` = Walmart + Paiz). En el corte 2026-09-21, las 315
  filas comparables TGU con GTIN restringido son Walmart+Paiz: 0 degradadas.
- **Exclusión por miembro:** el miembro en conflicto sale del grupo; el resto
  sigue comparable si conserva dos cadenas sin colisión.
- **Parser:** métrica preferida en etiquetas duales, multipacks invertidos y por
  envase, "1 Pack" no ambiguo, combos/kits como bundle no comparable.
- **Variante de un solo lado:** bloquea la identidad automática por GTIN.
  Elimina el falso positivo Gwaltney (TGU: sale La Colonia, Walmart+Paiz
  siguen) y uno nuevo (Glade Lavender vs Sweet Citrus, SPS). Para nombres
  abreviados (Colonial) se separan palabras pegadas y se expande una tabla de
  abreviaturas (`Vaini`, `Meloctn`, `S/Azu`, `Sugar Free`…); el sustantivo del
  tipo ("Chocolate Ferrero") no cuenta como sabor.

Medición (offline; el catálogo publicado sólo expone GTIN de filas comparables):

| Medida | Antes | Después |
| --- | --- | --- |
| Comparables publicados SPS / TGU | 7.1 % / 24.9 % | sin degradación por GTIN restringido; muestra de variante: 1 grupo SPS y 3 TGU de 246 con nombres visibles |
| Falsos positivos confirmados en la muestra de 246 grupos | 2 | 0 |
| Grupos Walmart+Paiz comparables (snapshots 2026-08-31/09-04) | 8,327 | 8,336 |
| Colonial `sku`→GTIN (snapshots): grupos comparables / productos Colonial en ellos | 8,327 / 0 | 8,579 / 2,100 |
| SPS estimado (Colonial+Walmart SPS sobre el corte 2026-09-21) | 7.1 % filas comparables; 0 % ofertas Colonial | ≈9.3 %; ≈21 % de ofertas Colonial |

Colonial: 1,921 grupos con Walmart SPS (813 nuevos, 1,108 amplían filas La
Colonia+Walmart). Es cota inferior: no hay snapshot de La Colonia. La guarda de
nombre/modelo retiró 236 productos Colonial (de 2,359 a 2,123; 2,100 tras leer
cantidades pegadas como "Sab550ml"). De la lista sospechosa del spot-check quedan
excluidos L'Oréal Blackest Black, Diana Favori Criollo, D'Olancho Chile Añejo,
Del Rancho Picosit y Maybelline Light 20; **Evenflo Campestre vs Acuario sigue
agrupado**: excluirlo exige ≥0.51 de coincidencia y retiraría 285 grupos más,
casi todos correctos. Muestras aleatorias: 27/30 de la anterior se conservan
(se pierden Carozzi Espaghettini, Purina Beneful abreviado y Always Anti Bun) y
en una nueva de 30 quedan 1-2 dudosos (Milpa Real tortilla maíz vs trigo, Pond's
limpiadora vs pepino).

## Calidad de catálogo — parser, marca y tipo (2026-10-01)

Rama `rpi/catalog-quality` (no fusionada). Motor `product-homologation-v2.5`:
al fusionarse, el refresh de homologación reescribe los perfiles derivados.
Reglas 15–18 en
[`homologation/product-identity-standard-v1.md`](homologation/product-identity-standard-v1.md).
La identidad entre cadenas sigue siendo sólo por GTIN; estos cambios mejoran
los atributos públicos y las guardas, no crean comparables sin GTIN.

- **Presentación:** separador de miles en unidades pequeñas ("Aceite Clover
  Brand 1,400 ml" → 1400 ml, antes 1.4 ml; l/kg/lb/oz conservan el decimal),
  `ltr`/`ltrs`, métrica explícita en etiquetas duales con libras ("623.7 g /
  1.37 lb" → 623.7 g), onzas → gramos sólo en tipos sólidos (arroz, queso,
  cereal…; en el resto la onza no se convierte), tolerancia imperial 2 % y
  display redondeado (907.18474 g → 907.18 g) con total canónico exacto.
- **Marca:** léxico de marcas fuente con alias compactos (`Kellogg's`/`Kelloggs`,
  `ORALB`, `MagiaBlanca`), lista de palabras comunes que nunca son marca desde
  el nombre (`Original`, `Premium`, `Pan`, `Sin`, `XL`…), marcas-palabra
  genéricas medidas en datos (sólo como primera palabra) y preferencia por la
  marca que abre el nombre. Procedencia interna en `brand_resolution_source`
  (`source` / `name_known_brand`), sin llaves nuevas en filas públicas.
- **Tipo:** contexto no alimenticio (aceite de motor 20W50/ATF, pintura,
  aceite cosmético/argán, pasta dental, alimento de mascota, objetos color
  café, gel para cabello, sal de baño, leche corporal); "S/Azúcar"/"Zero
  Azúcar" no es Azúcar; un tipo alimenticio en un departamento fuente
  General/Hogar/Limpieza/Cuidado personal se descarta; sin tipo por nombre, la
  hoja de la ruta de categoría tipa como evidencia débil (nunca conflicto) y el
  departamento llena la categoría pública.

Medición offline (registros reconstruidos 2026-09-21, 52,974 productos fuente
deduplicados como en Turso; una corrida para SPS+TGU; grupos comparables =
`ready` con ≥2 cadenas y un contexto por tienda):

| Medida | Antes (v2.4) | Después (v2.5) |
| --- | ---: | ---: |
| Grupos GTIN comparables SPS / TGU | 3,016 / 8,503 | 3,033 / 8,503 |
| Grupos perdidos | — | 1 SPS (conflicto real: Clover Brand 2,750 ml vs 3 L) |
| Ofertas en grupos comparables SPS / TGU | 7,009 / 19,381 | 7,059 / 19,382 |
| Presentaciones distintas (productos) | — | 2,040: 1,029 sólo redondeo, 696 oz→g, 223 métrica dual (antes valor imperial), 81 `ltr(s)` antes sin parsear, 11 miles (antes 1.4 ml) |
| Marca resuelta por nombre / ausente / conflicto fuente | 2,212 / 5,602 / 412 | 2,979 / 4,835 / 160 |
| Filas publicadas SPS `individual` sin marca que obtienen marca | — | 1,221 de 4,064 con registro (4,158 en total) |
| Filas publicadas con categoría SPS / TGU (de 33,815 / 31,706 con registro) | 10,122 / 9,452 | 19,778 / 18,449 |
| Filas publicadas con tipo SPS / TGU | 10,122 / 9,452 | 9,957 / 9,229 (productos: 426 tipos alimenticios erróneos retirados, 54 corregidos —pasta dental, alimento de mascota—, +319 por hoja de ruta) |

Muestras: marca extraída 56/60 exacta, 2 de familia ("Bakers Secrets" →
Bakers) y 2 errores (Always Save → Always, Castillo de Adas → Castillo); tipos
retirados por departamento 39/40 correctos (el error: un atún que Paiz cuelga
de una ruta de cosméticos); tipos por hoja de ruta ≈85 % correctos (errores:
productos mal ubicados por la cadena, p. ej. loción en "Jabón y gel corporal").
Riesgo conocido: un miembro Colonial ("ZIBAS Anillitos") sale de su grupo porque la marca inferida
(Zibas) contradice la declarada (Yummies) — fail-closed.

Motor shadow (`homologacion_motor_shadow.py run`, mismos registros, offline):

| Medida | Antes | Después |
| --- | ---: | ---: |
| Parejas candidatas SPS / TGU | 304,744 / 482,153 | 307,006 / 482,196 |
| Auto-match nuevos SPS / TGU | 416 / 2,346 | 79 / 2,369 |
| — sin Colonial–La Colonia | 74 / 2,346 | 79 / 2,369 |
| Revisión SPS / TGU | 8,360 / 7,819 | 8,748 / 7,843 |
| Recall del blocking (silver, ciego al GTIN) | 0.976 | 0.980 |
| Auto-match prueba silver: precisión / recall | 0.987 / 0.667 | 0.986 / 0.673 |

El segmento Colonial–La Colonia (342 auto-match SPS) sólo estaba habilitado por
el umbral de soporte bajo (27 etiquetas, Wilson 95 % = 0.875); con una etiqueta
silver más en contra (31, precisión 0.968) la política lo deshabilita y esas
parejas pasan a revisión. Fuera de ese segmento los auto-match suben (+5 SPS,
+23 TGU). Los "falsos positivos" silver nuevos son mayormente el mismo producto
con GTIN distinto que ahora concuerda en tamaño ("Café Dorao 454 g" vs "Café
Dorao Kraft 16 oz"). Sin GTIN nada de esto cambia la comparabilidad publicada.

## Comisariato — GTIN desde el código (2026-10-01)

Rama `rpi/comisariato-gtin` (no fusionada). Motor `product-homologation-v2.6`:
al fusionarse, el refresh de homologación reescribe los perfiles derivados.
Regla 19 en
[`homologation/product-identity-standard-v1.md`](homologation/product-identity-standard-v1.md).

Decisión aprobada por el responsable del proyecto: el `code` de Comisariato Los
Andes (`0001-` + 15 dígitos) codifica el GTIN del producto **sin su dígito de
control**, rellenado con ceros. Evidencia recogida live el 2026-10-01:

- `0001-000744102955677` → `744102955677` → `7441029556773` (Bimbo Pan Blanco
  720 g, el mismo GTIN que Colonial y La Colonia).
- En una muestra aleatoria de 55 códigos del catálogo, 21 GTIN reconstruidos
  coinciden con el GTIN de otra cadena con nombre concordante (Delicia Bacon
  397 g `7421000915201`, Pringles `038000846731`, Pepsi 2 L `7421600300247`,
  McCormick Mostaza `7411000204238`, Plenitud `7751493006446`, Olitalia
  `8007150902996`…). El fixture versionado 2026-09-04 lo confirma offline: 4
  de sus 5 códigos de 7 dígitos (Marinela Submarino Vainilla/Fresa, Pingüino,
  Gansito) reconstruyen el mismo EAN-8 que publica Paiz.
- Longitud de la base sin ceros en el catálogo completo (6,687 códigos): 10
  dígitos 2,601; 11 dígitos 1,604; 12 dígitos 2,427; 7–9 dígitos ~47; 3–4
  dígitos 8.
- Patrones que no son GTIN: internos `99…` (`99001005224`, `9900500…`) y
  códigos de peso variable en tienda (`24153000000` "Delicia jamon pollo lb plu
  133", vendido por libra; `29801000000` "Pan molido libra").

Implementación: `gtin_from_code` (scraper) reconstruye sólo bases de 7 (EAN-8),
10 (UPC-A con 0 inicial), 11 (UPC-A) y 12 (EAN-13) dígitos, excluye `99…` y
cualquier resultado en rango GS1 restringido; `reference` conserva el `code` y
`ean_provenance(row)` devuelve `sku_reconstructed_check_digit` sin llaves nuevas.
La persistencia Turso exige `ean == gtin_from_code(reference)`.
`comisariato_los_andes` entra en `sku_derived_gtin_supermarkets`: el GTIN sólo
crea identidad entre cadenas si coincide con otro y supera las guardas de marca,
nombre mínimo, tono/modelo y conflictos de tamaño/variante/tipo (como Colonial).
Se agregó la traducción `bacon`→`tocino` al acuerdo de nombre. Sin snapshot
completo de Comisariato en el repositorio, el impacto en grupos comparables se
mide tras el primer refresh.

## Árbol maestro de categorías v1 (2026-10-08)

Aprobado por el responsable. Detalle: [`homologation/master-category-tree-v1.md`](homologation/master-category-tree-v1.md).

- Problema: un 24 % de los productos de Walmart, 55 % de Comisariato, 48 % de PriceSmart y 17 % de La Colonia se publicaban sin categoría, aunque todos traen la categoría de su supermercado: la tabla interna sólo conocía unas pocas categorías y sólo 7 categorías públicas (sin ropa, juguetes, electrónica, etc.).
- Solución: árbol propio de 4 niveles alineado a GS1 GPC (14 departamentos, 180 subcategorías) + tabla de equivalencias versionada con las 1,311 categorías que publican los seis súper. Sólo con la tabla, el 98 % de los productos queda al menos en un departamento y el 72 % llega a subcategoría; el resto lo refina el tipo por nombre. Lo mezclado (p. ej. "Hogar" de PriceSmart) se decide por nombre; las tarjetas de regalo salen del catálogo.
- Publicación: `category` = departamento; `product_type` = tipo de producto o, si no hay, subcategoría/categoría del árbol. Un grupo comparable comparte el nodo más específico de sus ofertas. La identidad y la comparabilidad no cambian.
- Gobierno: categorías nuevas del súper aparecen como `taxonomy_unmapped_source_categories` en el log de publicación y se agregan al CSV.
- Primera publicación (PR #15, corrida 37884482010): ofertas con departamento SPS Walmart 76→100 %, Comisariato 45→97 %, La Colonia 83→100 %, PriceSmart 52→88 %, Colonial 99→100 %; TGU Paiz 93→100 %, Walmart 74→100 %. Segundo nivel navegable: Walmart/Paiz 100 %, La Colonia 98 %, Comisariato 93 %, Colonial 76 %, PriceSmart 33 %. De 7 a 14 departamentos y de 72 a 257 filtros de segundo nivel.
- El aviso de esa corrida listó 18 categorías nuevas (verduras, pruebas de embarazo, macetas, etc.); se agregaron al CSV (1,329 filas) con una prueba que exige que Paiz y Walmart mapeen igual la misma ruta.
- Pendiente: PriceSmart sigue con ~12 % sin departamento y 33 % sin segundo nivel porque sus categorías "Hogar" y "Productos de temporada" son mixtas y el nombre no siempre trae un tipo conocido.

## Incidente 2026-10-09 — el corte diario arrancaba 5–9 h tarde

Revisión de las 11 corridas del 1 al 9 de octubre (aprobada la corrección por el responsable):

- **Retraso del cron de GitHub:** programado 01:43, arrancó entre 06:42 y 10:21 (Honduras). El operador de reintentos (08:17) solía correr antes que el propio corte.
- **Fallas del primer intento** (sólo el 9-oct salió limpio): Paiz 8 (catálogo cambió durante la lectura: `page_count_changed`, `catalog_changed_during_capture`, totales de categoría; HTTP 500/504 de VTEX), La Colonia 6 (`partition_total_changed_mid_run`, `snapshot_sku_count_mismatch`, timeout, HTTP 500 TGU), Colonial 2 (`commercial_sources_disagree`, ya corregido en #12), Walmart 1 (`category2_membership_total_mismatch`), persistencia 2 (`snapshot_out_of_order` tras reruns manuales). PriceSmart y Los Andes: 0.
- **Día sin datos:** 5-oct, Paiz falló los 3 intentos y `persist` se saltó para todas las cadenas.
- **Corrección 1 (este cambio):** Worker de Cloudflare `edge/daily-trigger` dispara a las 07:43 UTC (`workflow_dispatch`); sin cron de GitHub en el corte; el operador arranca el corte si falta y reconoce `workflow_dispatch` y `schedule`.
- **Corrección 3 (persistencia parcial):** `persist` corre aunque una cadena haya fallado (`acquire` en `failure`); ensambla sólo los handoffs aceptados (`--allow-missing`, `missing_retailers` en la evidencia y `::warning`), y preflight, persistencia y verificaciones se limitan a esas cadenas (`DAILY_RETAILERS`). La fallida queda STALE (frescura por cadena ya la excluye de comparaciones) y el operador la reintenta; el reintento re-persiste las demás como `exact_replay`. La publicación (`homologation-refresh`) corre tras un día parcial sólo si el job `persist` del intento terminó en éxito (job `daily-gate`, `actions: read`).
- **Horario (aprobado por el responsable):** el corte vuelve a las **05:17** Honduras (`17 11 * * *` en Cloudflare), su hora original del 2026-08-28; la 01:43 sólo compensaba el retraso de GitHub. Especificaciones PriceSmart pasan al sábado 10:37 (`37 16 * * 6`); integridad semanal sin cambio (domingo 04:23).
- **Pendiente:** reintentos cortos por página ante HTTP 5xx o catálogo cambiante; operador disparado al terminar el corte.

## Medidas estandarizadas v2.7 (2026-10-09)

Aprobado por el responsable ("tenemos que estandarizar esto y hacerlo fácil para todos y que las marcas no se vean afectadas").

- **Motor `product-homologation-v2.7`:** multipacks de PriceSmart "N Unidades / X" con fuente = total (regla 22 del estándar) y onza de peso o líquida según el atributo `ounce` del árbol maestro (regla 21). Registro de decisiones revisadas vacío: el cambio de versión no invalida decisiones. Reescribe los perfiles una vez (cambio de versión).
- **Medición offline (catálogo publicado 2026-10-08, SPS):** PriceSmart con candidato por marca pasa de 233 a 414 productos; onzas que pasan a g/ml (estimado con la categoría publicada): Los Andes ~1,130 de 1,790, La Colonia ~430 de 840, Colonial ~110 de 680.
- **Catálogo B2C:** `presentation` en un solo formato (`1.88 kg`, `12 × 946 ml`, `1.774 L`) y `unit_price` por oferta con referencia fija por subcategoría (`unit_reference` del árbol v1.1.0: por 100 g/ml, por kg/L o por unidad).
- **Vínculos por atributos (regla A, `engine_auto`):** activos y servibles en la política; se recalculan en cada refresco diario desde los perfiles en memoria (sin lecturas extra salvo ~2 filas por vínculo) y el diff los escribe/retira (`engine_auto` pasa a ser método gestionado por el refresco). Medición offline sobre el catálogo 2026-10-08: **46 vínculos nuevos** (29 Los Andes, 17 PriceSmart); +13 maestros multi-cadena. Corrección: la estimación previa de ~580 incluía productos ya unidos por GTIN (no comparables ese día por stock); la mayor ganancia para PriceSmart es "otras presentaciones" (PR siguiente). Pendiente opcional: vincular a socios sin GTIN (maestro sembrado), ~20 más en SPS.
- **Homologación sin GTIN (validación 2026-10-09):** 387 pares de PriceSmart/Los Andes etiquetados; la regla "banda alta + sin conflicto de variante + a lo sumo una palabra menor distinta" acierta 161/163 (98.8 %, IC95 95.6–99.7 %); revisión del responsable de 50 pares al azar: 50/50 de acuerdo. Su publicación (vínculos por atributos y "otras presentaciones") va en PRs siguientes.

## Especificaciones PriceSmart (semanal, 2026-10-01)

Rama `rpi/pricesmart-specs` (no fusionada, sin corrida real). Detalle:
[`supermercados/pricesmart-especificaciones.md`](supermercados/pricesmart-especificaciones.md).

- **Cadencia semanal aprobada por el responsable** (registro en
  `.automation/pricesmart-specs-capture-authorization.json`): sábado 10:37
  Honduras + manual; read-only, una conexión, 2 s entre requests, ≤ 1200
  requests/≤ 900 ítems por corrida, aborta ante 429/403 repetido/anti-bot.
- Entrada: handoff PriceSmart de la última corrida diaria exitosa; cada pid una
  vez (SPS y TGU comparten catálogo); sólo ítems nuevos o verificados hace ≥ 28
  días.
- Captura de la ficha pública `/es-hn/producto/<slug>/<pid>`: marca, breadcrumb,
  peso/volumen neto (canónico g/ml), peso unitario, conteo, importado/nacional,
  origen, almacenamiento, alérgenos y crudo. **Sin GTIN**: el "Número de ítem" es
  interno; un GTIN sólo se registraría si apareciera en JSON y superara GS1.
- Fail-closed por página; la corrida falla si > 20 % de páginas no parsean.
- Turso: tabla derivada STRICT `pricesmart_product_specs` con upsert de filas
  cambiadas; aceptada como tabla opcional por las guardas de esquema.
- Homologación: completa marca/presentación faltantes de perfiles PriceSmart
  (`source_only`); no crea comparables.
- 2026-10-03: la primera corrida real mostró que el HTML del servidor no trae
  las especificaciones; la captura pasa a navegador headless (ver
  [Incidente 2026-10-03](#incidente-2026-10-03--colonial-y-primera-corrida-de-especificaciones-pricesmart)).

## Producto maestro v1 (2026-10-01)

Rama `rpi/product-master` (no fusionada). Diseño, reglas de supervivencia,
métodos de vínculo y flujo de revisión en
[`homologation/product-master-v1.md`](homologation/product-master-v1.md);
regla 20 del estándar de identidad.

- **Modelo:** `master_products` (id estable `mp_*`, atributos golden con
  procedencia, estado/merge/versión), `master_product_links` (método,
  evidencia, autor, historial; 1 vínculo activo por producto y por cadena en
  cada maestro) y `master_link_rejections` ("Distinto" humano). La
  persistencia diaria y la integridad semanal toleran/cuentan las tablas.
- **Refresco diario:** `backfill_homologacion_turso.py --apply` escribe
  maestros + vínculos GTIN (`gtin_exact`/`gtin_sku_derived`, 1:1 con los
  perfiles `ready`/`single_source`) reutilizando los perfiles en memoria. Sin
  cambios lee ~15 filas; con cambios, ~3–5 filas por producto cambiado; la
  lectura completa del estado maestro sólo ocurre la primera vez (tablas
  vacías) o ante cambio de versión/política. Un error del maestro no bloquea
  perfiles ni publicación. Sin cambios de workflow.
- **Exportadores:** byte a byte idénticos mientras sólo existan vínculos
  GTIN; un vínculo curado (`manual_review`/`reviewed_decision`) vuelve
  comparable la fila (`serving_link_methods` en la política).
- **Motor:** `engine_auto` deshabilitado. `exportar_cola_maestro.py` genera la
  cola producto→maestro (otro GTIN válido = sólo revisión; rechazos nunca se
  reproponen) e `importar_decisiones_maestro.py` importa el CSV
  Mismo/Distinto del responsable.
- **Medición offline (registros 2026-09-21):** 18,900 maestros (SPS 15,706, de
  ellos 3,033 multi-cadena; TGU 14,240 / 8,503). Golden multi-cadena SPS:
  marca 99.7 %, tamaño 97.7 %, tipo 61.7 % (filas miembro 97.8 / 93.6 /
  51.9 %). Productos no homologados con candidato: SPS 5,109 de 27,006
  (alta 1,194, media 2,480, baja 1,435), TGU 1,582 de 15,457; 34/40 de banda
  alta revisados a mano son el mismo producto.
- **Primer despliegue:** crea 5 tablas + 7 índices y escribe ~19–25 k
  maestros y ~32–38 k vínculos en lotes de 1000 (una sola vez).

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
  - volver a crear los secrets; GitHub no los copia entre repos. Para la operación diaria sólo se necesitan **`TURSO_DATABASE_URL`** y **`TURSO_AUTH_TOKEN`** (también los usan la homologación, la publicación RPI y la integridad semanal). `CLOUDFLARE_PROBE_GATEWAY_URL` y `CLOUDFLARE_PROBE_OBSERVABILITY_TOKEN` (environment `cloudflare-probe`) son sólo para la sonda manual; el environment `la-colonia-live` es manual. Este proyecto **no** usa BigQuery ni Google Sheets: sus workflows (`*-bigquery-first-load.yml`, `*-google-sheets-storage.yml`) pertenecían a otro proyecto y se eliminaron;
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
2. Revisar y fusionar la [Optimización Turso](#optimización-turso-2026-09-30); tras el primer ciclo diario confirmar en Turso que existe `idx_ph_loc_hist` y vigilar las filas leídas del mes. Pendiente: homologación incremental.
3. Con autorización explícita, diagnosticar y corregir el extractor de Colonial (`card_shape_invalid`).
4. Revisar la homologación entre supermercados (cobertura de identidades comparables y prácticas de matching).
5. Tras fusionar el producto maestro: confirmar en el primer refresco `product_master.mode = full` y luego `noop`/`incremental`, generar la cola contra maestros y empezar la revisión humana por la banda alta.

## Motor de homologación (shadow) 2026-09-30

Motor de resolución de entidades probabilístico en **modo shadow** (no cambia la
comparabilidad publicada ni corre en el workflow diario). Diseño y referencias
(Fellegi–Sunter, Splink, Dedupe, GS1, MinHash-LSH): [`homologation/matching-engine-v1.md`](homologation/matching-engine-v1.md).
CLI manual: `scripts/homologacion_motor_shadow.py` (`run`, `evaluate-golden`, `import-review`).

Corrida offline sobre el corte publicado **2026-09-21** (registros reconstruidos
desde el catálogo v3 + snapshots de retailers; salidas privadas, no versionadas):

| Métrica | SPS | TGU |
| --- | ---: | ---: |
| Registros fuente / parejas candidatas | 39 498 / 304 835 | 59 786 / 482 194 |
| Parejas silver (GTIN compartido) | 4 999 | 37 376 |
| Nuevas parejas auto-match vs publicado | 413 | 2 348 |
| Nuevas parejas a revisión (cola top-2) | 8 272 | 6 318 |
| Registros en identidad multi-cadena: actual → auto | 13,2 % → 14,4 % | 58,1 % → 61,5 % |
| Cota si la cola revisada se confirmara | 30,2 % | 65,5 % |

- Prueba silver ciega al GTIN (split 20 %): auto-match precisión **0,987**
  (Wilson 95 % ≥ 0,984), recall 0,669; recall del blocking 0,976. Por par:
  Paiz–Walmart 0,988, La Colonia–Walmart 0,982, La Colonia–Paiz 0,981,
  Colonial–La Colonia 0,971; Colonial–Walmart queda sin auto-match (no llega a
  0,98).
- Comisariato y PriceSmart no tienen GTIN: sin auto-match hasta etiquetar el
  golden set (`golden-set.csv`, 600 parejas estratificadas).
- Hallazgo: el campo `reference` de Colonial es un GTIN válido en ~92 % de los
  productos y coincide con EAN de Walmart en ~2,9 k casos; hoy no se persiste.
- Pendiente para cualquier promoción: etiquetar ≥ 500 parejas golden, correr
  `--turso` para huellas vigentes, revisar la cola y cambiar la política en un
  PR explícito.
