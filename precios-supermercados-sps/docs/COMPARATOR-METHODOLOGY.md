# Metodología del comparador de precios

## Objetivo

El comparador sólo publica diferencias de precio cuando existe evidencia suficiente para afirmar que dos registros representan el mismo producto comercial. La prioridad es evitar falsos positivos: si la identidad es ambigua, la comparación se bloquea.

## Principio fail-closed

La homologación descriptiva y la autorización para comparar precios son capas distintas.

- `homologate_products()` conserva perfiles, grupos por GTIN y candidatos para revisión.
- `safe_comparator` decide si un grupo puede entrar a cálculos de precio.
- `price_analytics` consume únicamente grupos con estado `comparable`.
- `publication_dataset` no publica precios de grupos bloqueados.

Un producto puede existir en la homologación y aun así quedar fuera del comparador.

## Estados

### `comparable`

Autoriza comparación automática. Requiere identidad fuerte compartida y ausencia de contradicciones comerciales.

### `review_required`

La evidencia no es suficiente para comparar automáticamente. No participa en ahorro, mejor precio ni canasta común.

### `not_comparable`

Existe una contradicción explícita. No participa en métricas de comparación.

## Disponibilidad: agotado no compara (regla 2026-10-02)

La identidad decide *qué* puede compararse; la disponibilidad del día decide *si*
participa hoy.

- Una oferta `out_of_stock` (con o sin precio) **nunca** entra a ranking
  (`relative_price_state`), mejor precio, diferencia/ahorro, PCI, escenarios,
  "Mejor precio seguro" ni recomendaciones de Mi Compra.
- `unknown` sigue siendo comparable: muchas cadenas no publican stock.
- Un grupo con identidad `comparable` sólo se publica como `comparable` ese día si
  quedan **≥2 cadenas** con ofertas no agotadas y con precio positivo. Si no, la
  fila se publica como `individual` conservando `row_id` y `canonical_product_id`
  (Mi Compra y enlaces compartidos siguen resolviendo).
- La oferta agotada sigue visible como información individual del día (catálogo
  sin cambios de visibilidad) y permanece en el histórico/Turso.
- Cuando vuelve `in_stock`/`unknown` con precio, participa de nuevo
  automáticamente; no hay estado manual.

Implementación única en Python: `price_analytics.CurrentPriceObservation.priced`
(Business Mart v1 / Consumer Mart v2 / canasta común), `shopping_analytics`
(`buyable`, escenarios) y `exportar_consumer_catalog_core._comparison_eligible` /
`_comparability_today` (Consumer Catalog v3 SPS y TGU, `analysis-*.json`). El
frontend sólo consume `comparability`, `availability` y `relative_price_state`.

En TGU la regla cuenta cadenas, no sucursales: dos tiendas Walmart disponibles
con La Colonia agotada no forman una comparación.

## Productos listados sin precio

Un SKU listado sin precio y declarado agotado ("NO DISPONIBLE") no tiene precio
que comparar: no crea `products`/`price_history`, se registra en
`catalog_unpriced_observations` y, si tenía un periodo current con precio en esa
ubicación, ese periodo se cierra (deja de ser oferta current). Reaparece por el
camino normal cuando vuelve a tener precio.

## Reglas de identidad

La marca y la presentación nunca bastan por sí solas.

Para una comparación automática se exige:

1. supermercados distintos;
2. GTIN canónico válido en ambos registros;
3. mismo GTIN y mismo `canonical_product_id`;
4. marca normalizada coherente;
5. tipo de producto coherente cuando está disponible;
6. presentación compatible, incluyendo dimensión, cantidad total y multipack;
7. ausencia de conflicto o multipack ambiguo;
8. descriptores comerciales remanentes coherentes.

Si falta GTIN, el resultado es `review_required`, aunque marca y presentación coincidan.

## Variantes comerciales

Los nombres se normalizan sin destruir el texto fuente. Después de retirar marca, tipo y unidades de presentación, quedan tokens que representan familia, sabor, variante, línea u otros descriptores comerciales.

Una contradicción entre esos descriptores bloquea la comparación. La regresión `Passion Jaguar` frente a `Passion Especial` existe expresamente para impedir que una coincidencia de marca y presentación produzca un falso ahorro.

## Presentaciones

Dos registros no son comparables directamente cuando su presentación difiere. El motor conserva una firma estructurada con dimensión, contenido total y conteo de unidades. Las equivalencias toleran únicamente diferencias numéricas mínimas atribuibles a redondeo de unidades equivalentes.

Los multipacks ambiguos no se reducen a una unidad arbitraria.

## Canasta común

La canasta común utiliza la intersección de productos que cumplen simultáneamente:

- grupo `comparable`;
- exactamente un registro fuente por supermercado del alcance;
- ubicación explícita por supermercado;
- precio actual positivo y no `out_of_stock` en cada ubicación.

No se imputan precios y no se sustituyen productos ausentes por productos parecidos.

El denominador publicado es:

`products_comparable_and_priced_in_every_supermarket_in_scope`

Por eso una canasta entre dos supermercados puede contener más productos que una canasta entre seis: ampliar el alcance reduce la intersección válida.

## Ahorro

Para cada producto comparable:

- mejor precio = mínimo precio actual dentro del alcance;
- ahorro absoluto = precio máximo del mismo producto dentro del alcance menos el mejor precio;
- ahorro porcentual = ahorro absoluto / precio máximo del mismo producto.

Para la canasta:

- total por supermercado = suma de los mismos productos del denominador común;
- ahorro de canasta = total máximo menos total mínimo;
- ahorro porcentual = ahorro / total máximo.

No se usa `reported_regular_price` como baseline para afirmar ahorro real entre periodos. Esa referencia declarada por la fuente permanece separada del histórico observado.

## Cambios entre ejecuciones

`price_change_analytics` compara dos resultados sólo si comparten el mismo alcance de supermercados y ubicaciones. Los totales de canasta entre ejecuciones se comparan únicamente cuando el universo común es idéntico; si cambia la composición, se reportan altas/bajas del universo pero no un delta de total que mezcle cambio de precio con cambio de composición.

## Historial

`price_history_analytics` resume únicamente observaciones aceptadas de una misma identidad canónica, supermercado y ubicación. No interpola periodos inexistentes.

## Regla de publicación

Ningún consumidor —Power BI, portafolio, JSON público o CSV— debe reconstruir equivalencias por su cuenta. Todos deben consumir la salida del gate seguro. Si un producto no está en `comparable`, no se debe calcular ni mostrar un “mejor precio” cross-source.