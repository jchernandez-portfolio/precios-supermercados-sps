# Snapshot histórico — estado al 2026-09-10

> Copia congelada de `PROJECT_STATE.md` tal como estaba el 2026-09-10. El estado vigente está en [`PROJECT_STATE.md`](PROJECT_STATE.md).

# Estado actual — Retail Price Intelligence / Precios de Supermercados SPS (2026-09-10)

GitHub `main`, GitHub Actions, los artifacts productivos, Turso y la rama `portfolio-data` son la fuente de verdad técnica. Este archivo contiene únicamente el estado **vigente**; los hitos e incidentes anteriores se conservan en los snapshots y documentos históricos del proyecto.

## Checkpoint vigente — 2026-09-10

El producto está operativo como **Retail Price Intelligence (RPI)** con dos superficies principales:

- **Compra Inteligente B2C**, una experiencia web responsive para navegar precios públicos, comparar ofertas seguras y organizar una compra;
- **Business Mart B2B**, un contrato analítico reproducible para Power BI con comparación actual, histórico de precios, promociones, cobertura y freshness.

La adquisición, normalización, histórico y homologación siguen siendo una sola base compartida. La lógica monetaria y de comparabilidad autoritativa permanece en Python; ni Power BI ni el navegador crean equivalencias nuevas.

## Contratos vigentes

| Contrato | Estado | Uso |
| --- | --- | --- |
| `rpi-business-mart/v1` | vigente, privado | Power BI / analítica B2B |
| `rpi-consumer-mart/v2` | vigente, público | comparación analítica segura y escenarios B2C |
| `rpi-consumer-catalog/v3` | vigente, público | navegación escalable de Compra Inteligente |
| `rpi-marts-manifest/v1` | vigente | integridad, schemas, scope y SHA-256 |
| `rpi-consumer-catalog-manifest/v3` | vigente | integridad y serving del catálogo particionado |

Los contratos `precios-sps-publication/v1` y `precios-sps-static-bi-dataset/v1` se conservan por compatibilidad/historial, pero ya no son la arquitectura RPI principal.

## Publicación B2C verificada

La publicación posterior al PR #451 está materializada en `portfolio-data`. El Consumer Catalog v3 publicado el **2026-09-10** declara:

- `as_of = 2026-09-10T16:21:00.121554Z`;
- **44,042 filas visibles**;
- **46,680 ofertas fuente**;
- 2,638 filas comparables;
- 11,846 filas `single_source`;
- 29,558 filas individuales;
- 484 particiones, con máximo de 250 filas por partición;
- 45,420 ofertas con resumen histórico;
- payload inicial de 37,796 bytes sin comprimir / 4,957 bytes gzip;
- dos requests iniciales: manifest + facetas.

El alcance público B2C es exactamente:

| Cadena | Contexto SPS |
| --- | --- |
| La Colonia | `la_colonia_sps` |
| Colonial | `colonial_sps` |
| Walmart | `walmart_sps` |
| PriceSmart | `pricesmart_sps` |
| Comisariato Los Andes | `comisariato_los_andes_sps` |

Las cinco fuentes figuraban `FRESH` en ese corte. La publicación separa **visibilidad** de **comparabilidad**: un producto puede mostrarse individualmente aunque no exista evidencia suficiente para compararlo con otra cadena.

El Consumer Mart v2 publicado en el mismo ciclo quedó `COMPARABLE`, con 92 productos en el universo analítico seguro de La Colonia SPS + Walmart SPS y política `fail_closed_strong_identity_and_commercial_consistency`.

## Compra Inteligente

La interfaz pública consume únicamente archivos estáticos publicados y valida tamaños/SHA-256. No consulta Turso y no hace matching en JavaScript. Se presenta explícitamente como **planificador de compra**, no como tienda ni checkout.

Está implementado:

- navegación por facetas dependientes;
- matriz de cinco supermercados SPS;
- búsqueda y tarjetas responsive;
- selección manual de oferta exacta;
- cantidades por producto;
- alta por lote con confirmación de conflictos;
- `Mi Compra` persistida localmente y agrupada por supermercado;
- actualización explícita de precios sin sustitución silenciosa de retailer;
- manejo visible de precios cambiados, faltantes o no disponibles;
- indicador de fecha/estado del último corte público aceptado;
- escenarios de canasta manual, por un solo supermercado y optimización por mejor precio seguro;
- contexto histórico por oferta: precio anterior, 30/90 días, mínimos, máximos y posición histórica;
- exportación local CSV y PDF;
- compartir la lista por WhatsApp con cantidades, precios, promociones, subtotales, total y advertencias aplicables.

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

## Homologación y revisión privada

La homologación sigue siendo conservadora: GTIN/evidencia fuerte puede establecer identidad, mientras los matches por similitud quedan fuera de la comparación automática.

Desde el PR **#460** existe una cola privada de revisión que puede materializar, bajo ejecución manual y read-only:

- candidatos fuzzy `review_required` con ambos productos y score;
- conflictos comerciales entre registros que comparten GTIN;
- productos sin taxonomía/tipo normalizado suficiente.

La cola no se publica a Compra Inteligente, no modifica precios ni perfiles por sí sola y no añade una lectura completa diaria de Turso.

## Operación recurrente

El workflow `.github/workflows/precios-supermercados-sps-la-colonia-mvp-update.yml` está configurado diariamente a `43 7 * * *` (01:43 `America/Tegucigalpa`) y cubre seis cadenas / once contextos productivos demostrados. Este adelanto compensa el retraso sistemático observado en el scheduler de GitHub y reduce la probabilidad de que la captura real coincida con el tráfico comercial de la mañana:

| Cadena | Ubicaciones/contextos productivos |
| --- | --- |
| La Colonia | SPS, Tegucigalpa |
| Colonial | SPS |
| Walmart | SPS, TGU FFAA, TGU El Sauce |
| PriceSmart | SPS 6603, TGU Florencia 6602 |
| Comisariato Los Andes | SPS |
| Paiz | TGU Multiplaza, TGU Próceres |

La última corrida programada completamente verde fue el run `34368332245` del **2026-09-09**.

El run programado `34492865834` del **2026-09-10** falló de forma segura durante la captura de Comisariato Los Andes por un timeout de transporte en `page-00200.json`. La compuerta global bloqueó la persistencia, por lo que ese intento no reemplazó el último estado comercial válido con datos parciales.

El PR **#455** (`[RPI] Retry transient Los Andes timeouts safely`) fue fusionado el 2026-09-10. Los Andes permite **un único reintento por solicitud sólo para errores transitorios de transporte**, con máximo diez reintentos acumulados y dentro del presupuesto existente de intentos. No se reintentan errores HTTP y las reglas de completitud/fail-closed no se relajaron.

Los PR **#461** y **#462** completaron la recuperación operativa posterior al incidente:

- la adquisición diaria se ejecuta de forma aislada por cadena, con `fail-fast` desactivado para permitir que las demás fuentes terminen aunque una falle;
- cada cadena sólo entrega un handoff reutilizable después de superar sus validaciones de completitud/ubicación;
- la persistencia global sigue siendo fail-closed y sólo comienza cuando existe un handoff aceptado de todas las cadenas;
- los artifacts quedan ligados a `run_id` + `run_attempt`, por lo que una recuperación puede reutilizar las capturas válidas de intentos anteriores;
- el operador revisa el run programado del mismo día a las **08:17** y **12:17** de Honduras;
- sólo `failure`/`timed_out` son recuperables automáticamente y el límite es **tres intentos totales** (inicial + hasta dos recuperaciones);
- la recuperación vuelve a ejecutar los jobs fallidos y sus dependencias, no crea un crawl programado nuevo si el run diario no existe.

Walmart aplica además una recuperación local y acotada cuando el total de una categoría cambia durante la comprobación final. El extractor espera 120 segundos, exige dos lecturas concordantes —facetas y búsqueda— separadas por 60 segundos y vuelve a descargar únicamente la categoría afectada. Si las fuentes aún discrepan o cambian durante la recaptura, realiza un segundo y último ciclo después de 600 segundos. Nunca mezcla páginas anteriores y posteriores al cambio; si no logra una membresía exacta y una confirmación final estable, conserva el último snapshot válido y deja que el operador global reintente sólo el job fallido.

No se provocó scraping live adicional para probar estos cambios. La siguiente corrida programada será la primera evidencia productiva del nuevo esquema; hasta entonces, el último estado aceptado continúa siendo la fuente válida.

Los correos de fallo de GitHub Actions son una preferencia de la cuenta del usuario y no una propiedad versionada del repositorio. Para recibirlos debe estar habilitada la entrega por email para Actions; puede limitarse a workflows fallidos.

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
captura validada
→ Turso / histórico aceptado
→ refresh de homologación derivada
→ exportación RPI read-only
→ validación de schemas, scope, hashes y secretos
→ publicación atómica en portfolio-data
→ Compra Inteligente consume sólo archivos estáticos
```

La publicación pública incluye Consumer Mart v2, Consumer Catalog v3 y la muestra de portafolio. **Business Mart permanece privado** dentro del artifact analítico y no se copia al namespace público.

## Límites vigentes

- Paiz no tiene un contexto SPS aceptado; sus contextos demostrados son Multiplaza y Próceres en Tegucigalpa.
- PriceSmart El Sauce 6604 permanece excluido.
- Maxi Despensa y Despensa Familiar continúan en **NO-GO TEMPORAL PARA PRICE TRACKING WEB**.
- Un dato `STALE`, `UNAVAILABLE`, ambiguo o sin suficiente cobertura no puede producir ranking/PCI/recomendación competitiva nueva.
- Los documentos históricos de incidentes se preservan tal como fueron emitidos; este archivo es el único resumen mutable del estado presente.

## Criterio de cierre del producto actual

El producto funcional, los contratos B2B/B2C, Compra Inteligente, la publicación estática, la cola privada de homologación y los activos reproducibles de Power BI están implementados. El mecanismo de recuperación del corte diario también está implementado y validado por CI. La única evidencia operativa todavía no disponible es observar una siguiente ejecución programada real usando #455 + #461 + #462; no se debe provocar scraping adicional únicamente para producir esa evidencia.
