# Retail Price Intelligence Platform — especificación de producto

## Propósito

Precios de Supermercados SPS evolucionó a una plataforma **Retail Price Intelligence (RPI)** con una sola adquisición confiable y dos productos derivados:

- **Retail Price Intelligence B2B**, servido por `rpi-business-mart/v1` y preparado para Power BI;
- **Compra Inteligente B2C**, servida por `rpi-consumer-mart/v2` para comparación analítica y `rpi-consumer-catalog/v3` para navegación pública escalable.

Compra Inteligente es un **planificador de compra** basado en precios públicos observados. No es una tienda, no procesa pagos y no representa un checkout de los supermercados.

`PROJECT_STATE.md` describe el estado operativo vigente. Este documento define el contrato funcional del producto.

## Arquitectura autoritativa

```text
supermercados
  → extracción por fuente/ubicación autorizada
  → RAW + hashes + provenance
  → completeness específico de fuente
  → health signals
  → aceptación comercial / last-known-good
  → Turso: estado actual + periodos históricos compactos
  → homologación conservadora
  → comparabilidad + freshness
  → Python analytics
       ├─→ Business Mart v1 → Power BI B2B
       ├─→ Consumer Mart v2 → comparación/escenarios B2C
       └─→ Consumer Catalog v3 → navegación pública → Compra Inteligente
```

Los tres contratos reutilizan el mismo estado comercial aceptado. Ningún refresh de Power BI o de la web ejecuta scraping. No existe matching en DAX, Power Query o JavaScript.

## Calidad y último dato válido

Completeness y health son decisiones distintas:

- **completeness** demuestra páginas, particiones, binding, membership, conteos y reconciliación necesarios para aceptar un catálogo;
- **health** registra señales operativas como volumen, precios, promociones, categorías, disponibilidad, duración, requests y retries.

Cada run se clasifica como:

- `ACCEPTED`: completo y sin alertas materiales;
- `DEGRADED`: completo con warnings auditables;
- `REJECTED`: incompleto o inválido.

Un run `REJECTED` no sustituye el último dato válido ni modifica current/history. La ausencia de una fila nunca se convierte automáticamente en precio cero ni en agotado.

## Resiliencia del corte diario

La adquisición productiva diaria está separada por cadena. Cada cadena sólo produce un handoff reutilizable después de superar sus validaciones específicas de ubicación y completitud. La persistencia sigue siendo global y fail-closed: no comienza hasta que todas las cadenas requeridas tienen un handoff aceptado.

Los handoffs quedan ligados al `run_id` y `run_attempt`. Si el run programado termina en `failure` o `timed_out`, las ventanas de recuperación pueden volver a ejecutar los jobs fallidos y sus dependencias, conservando los resultados aceptados de cadenas que ya terminaron bien. El límite operativo es un intento inicial más hasta dos recuperaciones.

Una recuperación no crea un crawl nuevo si no existe el run programado del día y no convierte un resultado parcial en estado comercial aceptado.

## Homologación y comparabilidad

La comparación cross-retailer exige identidad fuerte y consistencia comercial. GTIN/EAN válido común puede sostener una equivalencia si no existen contradicciones; nombres, marca y presentación por sí solos no bastan.

Una métrica competitiva sólo se calcula si existe:

1. identidad fuerte y consistencia comercial;
2. una oferta válida por retailer/ubicación requerida;
3. `current_price > 0`;
4. disponibilidad no explícitamente `out_of_stock`;
5. fuentes dentro de la ventana temporal comparable.

Toda salida competitiva declara cobertura, `as_of` y freshness. Empates reales se conservan.

Los candidatos por similitud, conflictos comerciales con un mismo GTIN y gaps de taxonomía pueden materializarse en una cola privada de revisión humana. Esa cola no autoriza comparación por sí sola y no forma parte de la publicación B2C.

## Freshness

Cada fuente/ubicación expone `observed_at`, último run aceptado, edad del dato y `freshness_status` (`FRESH`, `STALE`, `UNAVAILABLE`).

Si una fuente está stale/unavailable o las observaciones no son temporalmente compatibles, la comparación queda `INSUFFICIENT_FRESH_COMPARISON`. El último precio aceptado puede mostrarse con su estado, pero no genera ranking, PCI ni recomendación nueva.

## Python como fuente de verdad analítica

Python calcula las métricas compartidas antes de publicar:

- precio actual/anterior y cambios absoluto/porcentual;
- dirección y días desde último cambio;
- mínimos, máximos, media, mediana, rango, frecuencia y volatilidad;
- ventanas históricas con estado explícito `insufficient_history` cuando falta baseline;
- promoción declarada separada de reducción histórica observada;
- PCI, ranking, spread, cobertura y ganadores con empates;
- escenarios de canasta y totales monetarios.

El precio regular declarado nunca sustituye una observación histórica real.

# Producto B2B — Business Mart / Power BI

`rpi-business-mart/v1` es privado, derivado y reconstruible. Incluye:

- dimensiones de producto, retailer, ubicación, categoría y marca;
- comparación actual;
- periodos históricos reales;
- análisis promocional;
- canasta común;
- cobertura y freshness.

Las nueve superficies especificadas para Power BI son:

1. Executive Market Overview;
2. Competitive Pricing;
3. Category Intelligence;
4. Price Movements;
5. Promotion Intelligence;
6. Brand Intelligence;
7. Geographic Intelligence;
8. Assortment / Coverage;
9. Opportunities & Alerts.

Los assets reproducibles están en `powerbi/rpi/`. DAX agrega/presenta; no redefine matching, PCI, freshness ni clasificación histórica. El repositorio no usa un `.pbix` opaco como fuente de verdad.

# Producto B2C — Compra Inteligente

## Consumer Mart v2

`rpi-consumer-mart/v2` contiene el universo público analítico seguro para comparación y escenarios. Expone sólo identidades/ofertas autorizadas por Python, con precios, diferencias, recomendación, freshness e historia resumida.

No publica secretos, RAW, colas de revisión ni grupos ambiguos.

## Consumer Catalog v3

`rpi-consumer-catalog/v3` es el contrato público de navegación de gran volumen. Está separado del Consumer Mart para que **visibilidad no implique comparabilidad**.

Alcance vigente: un catálogo por ciudad, enumeradas en `rpi-consumer-city-index/v1` (`v3/cities.json`).

- **San Pedro Sula** (predeterminada): La Colonia, Colonial, Walmart, PriceSmart y Comisariato Los Andes.
- **Tegucigalpa**: La Colonia, Walmart FFAA, Walmart El Sauce, PriceSmart Florencia, Paiz Multiplaza y Paiz Próceres. Las sucursales de una misma cadena se mantienen separadas.

Una fila puede ser:

- `comparable`;
- `single_source`;
- `individual`.

Sólo una identidad autorizada y suficientemente fresca puede recibir comparación relativa.

El catálogo se sirve como manifest, facetas, índices bajo demanda y particiones de máximo 250 filas. Cada archivo tiene hash/tamaño verificable. El navegador no consulta Turso y no reconstruye identidades.

`is_promotion=null` significa promoción desconocida; no debe mostrarse como `false`.

## Análisis B2C

`rpi-consumer-analysis/v1` publica un archivo por ciudad, calculado en Python sobre el mismo corte que el catálogo (el `as_of` debe coincidir). Incluye:

- resumen;
- cobertura y mejores precios por supermercado;
- oportunidades: bajadas, subidas, promociones y mínimos recientes;
- liderazgo por categoría.

La pestaña **Análisis** lo muestra sin recalcular identidades.

## Flujo de Compra Inteligente

La aplicación responsive permite:

- navegación por facetas dependientes y búsqueda;
- selector de ciudad;
- matriz de supermercados/sucursales de la ciudad en escritorio y tarjetas adaptativas en móvil;
- selección manual exacta de oferta;
- cantidades positivas;
- alta por lote con confirmación de conflictos;
- lista `Mi Compra` persistida en el dispositivo;
- agrupación por supermercado;
- actualización explícita de precios;
- faltantes/no disponibles visibles sin sustitución silenciosa;
- estado visible del último corte público aceptado;
- comparación de escenario manual, un solo supermercado y split optimizado seguro;
- historial resumido por oferta;
- exportación local CSV/PDF;
- compartir `Mi Compra` por WhatsApp con cantidades, precios, promociones, subtotales, total y advertencias aplicables;
- checklist comprado/pendiente.

## Contrato monetario

```text
unit_price = current_price
line_total = unit_price * quantity
retailer_subtotal = sum(line_total)
grand_total = sum(retailer_subtotal)
```

Los cálculos usan minor units/`Decimal`. `reported_regular_price` puede mostrarse como referencia, pero no entra al total. No se inventan ISV, impuestos, shipping, delivery, service ni membership fees.

Si una línea no tiene precio utilizable, el total dependiente queda incompleto; nunca se imputa cero.

La nota de salida es:

> Total estimado calculado con los precios públicos observados en cada supermercado. Los precios pueden cambiar en tienda.

## Historial visible

Cada oferta puede mostrar, cuando existe evidencia suficiente:

- precio anterior;
- promedio 30/90 días;
- mínimo/máximo de ventana;
- posición histórica humana;
- reducción real observada y/o promoción declarada, siempre separadas.

La web consume estas métricas ya calculadas; no reconstruye series.

# Actualización derivada y publicación

```text
accepted commercial update
  → homologation refresh
  → Python analytics
  → Business Mart v1 + Consumer Mart v2 + Consumer Catalog v3
  → validación de schema/scope/hash/secretos
  → publicación atómica de los contratos públicos
```

Un fallo conserva el último corte válido. Business Mart permanece privado; Consumer Mart y Consumer Catalog son las fronteras públicas B2C.

# Seguridad y límites

- Turso y los sitios fuente no se consultan desde el navegador.
- Ningún secreto, cookie o endpoint privilegiado llega a artifacts públicos.
- `ABSENT`, `OUT_OF_STOCK` y `UNKNOWN` son estados distintos.
- No se fuerza matching para aumentar cobertura.
- No se infiere ciudad cuando el contexto fuente no la demuestra.
- Cuentas, pagos, forecasting, elasticidad y productos multi-tenant no forman parte del MVP actual.

# Madurez del producto

| Nivel | Estado actual |
| --- | --- |
| Data Foundation | implementada: seis cadenas / once contextos productivos y histórico persistido |
| Analytics Foundation | implementada: quality, LKG, freshness, historia, promociones, PCI y canastas |
| B2B MVP | activos reproducibles de Business Mart + Power BI implementados |
| B2C MVP | Compra Inteligente + Consumer Mart v2 + Consumer Catalog v3 implementados y publicados |
| Advanced Market Intelligence | futuro, sujeto a historia/uso real |
| Commercial Product | futuro, sujeto a clientes/requisitos reales |
| Advanced Models | futuro, sujeto a cobertura e historia suficientes |
