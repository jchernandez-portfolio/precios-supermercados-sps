# Diccionario de datos RPI

## Contratos vigentes

El estado comercial aceptado se proyecta en tres contratos separados. Ninguno hace matching nuevo y todos se reconstruyen desde datos aceptados:

| Contrato | Acceso | Propósito |
| --- | --- | --- |
| `rpi-business-mart/v1` | privado | analítica B2B / Power BI |
| `rpi-consumer-mart/v2` | público | comparación analítica segura y escenarios B2C |
| `rpi-consumer-catalog/v3` | público | navegación escalable de Compra Inteligente |

Los manifests asociados son `rpi-marts-manifest/v1` y `rpi-consumer-catalog-manifest/v3`. Registran versión, alcance, timestamps y SHA-256 para validar los archivos derivados.

El grano de una oferta es la identidad exacta:

```text
canonical_product_id + supermarket_id + location_id + source_product_id
```

Los nombres, marcas y presentaciones sirven para describir/navegar; nunca autorizan equivalencias por sí solos.

## Metadatos compartidos

| Campo | Semántica |
| --- | --- |
| `comparison_policy` | política fail-closed que autorizó la comparación |
| `comparison_status` | `COMPARABLE` o `INSUFFICIENT_FRESH_COMPARISON` |
| `blocked_reasons` | motivos auditables que suprimen ranking/PCI |
| `currency` | `HNL` |
| `scope` | retailer + ubicación exacta |
| `as_of` | instante UTC de evaluación |
| `freshness_window_hours` | ventana temporal permitida |
| `source_freshness` | último run válido, edad y estado por scope |
| `coverage` | comparables, precios válidos, exclusiones y porcentaje |

Una fuente `STALE` puede conservar su último precio válido visible como referencia, pero no puede producir ranking, PCI o recomendación competitiva nueva. Un run `REJECTED` nunca sustituye al last-known-good.

# Business Mart v1

`rpi-business-mart/v1` es el contrato B2B privado y reproducible. El exportador read-only `scripts/exportar_rpi_marts.py` lo materializa en JSON y CSV sin modificar Turso.

## Dimensiones

| Tabla | Grano / clave |
| --- | --- |
| `dim_product` | `canonical_product_id` |
| `dim_retailer` | `supermarket_id` |
| `dim_location` | `supermarket_id + location_id` |
| `dim_category` | `category` |
| `dim_brand` | `brand` |

## `fact_current_comparison`

Una fila por oferta segura actual. Incluye identidad, descriptores, `current_price`, referencia regular, promoción, disponibilidad, timestamps y freshness. Cuando el mercado es comparable también expone ranking, PCI, mínimo/máximo/media/mediana de mercado y spread.

`reported_regular_price` es sólo referencia y nunca reemplaza `current_price`.

## `fact_price_history`

Una fila por periodo comercial realmente persistido de cada oferta segura. No es una serie diaria sintética y no interpola días ausentes.

Campos principales:

- `period_start`;
- `current_price`;
- `reported_regular_price`;
- `is_promotion`;
- `previous_price`;
- `change_abs`, `change_pct`;
- `direction`: `initial`, `up`, `down`, `unchanged`;
- `is_current`;
- `source_last_successful_at`, `freshness_status`.

Un nuevo periodo puede existir por cambios de promoción/referencia aun si el precio efectivo no cambia; en ese caso `direction=unchanged`.

## `fact_promotion_analysis`

Una fila por oferta segura en el corte `as_of`. Mantiene separadas la promoción declarada y la reducción histórica observada.

Incluye:

- `source_reports_promotion`;
- `historical_price_reduction`;
- `source_discount_depth_pct`;
- `current_vs_previous_pct`;
- `current_vs_average_30d_pct`, `current_vs_average_90d_pct`;
- `current_vs_minimum_90d_pct`;
- `promotion_duration_days`;
- `promotion_event_count`;
- `promotion_share_pct`;
- `historical_position`;
- conteo de observaciones y freshness.

La clasificación histórica se calcula en Python. Power BI no reconstruye esta decisión.

## `fact_basket_cost`

Una fila por retailer/ubicación para exactamente el mismo universo común. Un universo vacío no produce ganador. Una canasta con faltantes queda incompleta y no imputa cero.

## `fact_metric_coverage`

Una fila por corte con `comparable_count`, `valid_price_count`, `excluded_count`, `coverage_pct`, `as_of` y `freshness_window_hours`.

# Consumer Mart v2

`rpi-consumer-mart/v2` es el contrato público analítico reducido. Contiene únicamente productos autorizados por Python para el alcance comparable y el contexto necesario para búsqueda, historial resumido y Mi Compra.

Cada oferta puede exponer:

| Campo | Uso |
| --- | --- |
| `canonical_product_id` | identidad segura compartida |
| `source_product_id` | oferta exacta |
| `supermarket_id`, `location_id` | contexto comercial |
| `category`, `product_type` | navegación/descripción |
| `product_name`, `brand`, `variant`, `presentation` | presentación, nunca matching |
| `current_price` | precio unitario efectivo |
| `reported_regular_price` | referencia visual opcional |
| `is_promotion` | declaración fuente; puede ser `null` si se desconoce |
| `rank`, `is_best_price` | comparación calculada por Python |
| `difference_vs_best_abs`, `difference_vs_best_pct` | diferencia contra el mínimo seguro |
| `availability` | disponibilidad publicada |
| `observed_at` | inicio del estado comercial observado |
| `last_successful_run`, `source_last_successful_at` | procedencia del último corte aceptado |
| `data_age_hours`, `freshness_status` | frescura visible |
| `historical_summary` | resumen histórico calculado por Python |

`recommended_source_product_ids` contiene sólo IDs autorizados como mejor precio; puede haber más de uno por empate. Si comparar no es seguro, queda vacío.

## `historical_summary`

Se calcula para la misma oferta exacta; no hace matching histórico. Puede incluir:

- `observation_count`;
- `first_observed_at`, `last_observed_at`;
- `observed_minimum`, `observed_maximum`;
- `previous_price`;
- `current_vs_previous_pct`;
- `days_since_last_change`;
- `historical_position`;
- `historical_price_reduction`;
- `source_discount_depth_pct`;
- ventanas `30d` y `90d` con media, mediana, mínimo, máximo y posición actual.

Una ventana sólo queda `available` si existe baseline real suficiente. Si no, usa `insufficient_history`; no se acorta ni interpola.

# Consumer Catalog v3

`rpi-consumer-catalog/v3` es la capa pública de **serving/navegación** de Compra Inteligente. Está separada del Consumer Mart v2 para permitir un catálogo mucho mayor sin debilitar la comparabilidad.

El alcance vigente es exactamente cinco contextos SPS:

- La Colonia SPS;
- Colonial SPS;
- Walmart SPS;
- PriceSmart SPS;
- Comisariato Los Andes SPS.

Una fila puede ser:

- `comparable`: tiene equivalencia segura para comparación relativa;
- `single_source`: identidad segura presente en una sola fuente del grupo aplicable;
- `individual`: oferta visible sin autorización para comparación cross-retailer.

**Visible no significa comparable.** La UI puede mostrar productos individuales, pero sólo las filas autorizadas por Python reciben ranking/recomendación relativa.

`comparability` es **del día**: una identidad `comparable` sólo se publica así si
hoy quedan ≥2 cadenas con ofertas no `out_of_stock` y con precio positivo; si no,
la fila sale `individual` con el mismo `row_id`/`canonical_product_id`. Dentro de
una fila `comparable`, cada oferta `out_of_stock` queda con
`relative_price_state = "neutral"` y fuera de `analysis-*.json` (mejor precio,
victorias, brecha) y de Mi Compra. `availability = "unknown"` sigue comparable.
Ver `docs/COMPARATOR-METHODOLOGY.md` (regla 2026-10-02).

## Otras presentaciones (campo aditivo, 2026-10-09)

`other_presentations` es opcional: sólo aparece en filas que tienen el mismo
producto publicado en otro tamaño o paquete (regla V
`size-variant-rule-v@1`, `src/precios_supermercados/matching/size_variants.py`).
Es una relación de navegación, **no identidad**: nunca fusiona filas ni entra a
ranking, mejor precio, PCI, canastas ni Mi Compra. Cada elemento trae:

| Campo | Tipo | Definición |
| --- | --- | --- |
| `row_id` | texto | fila de la otra presentación |
| `product_name` | texto | nombre representativo de esa fila |
| `presentation` | texto | presentación pública de esa fila |
| `best_price` | decimal-texto | menor precio vigente no agotado de esa fila |
| `best_unit_price` | objeto | precio por unidad de referencia de esa oferta (`amount`, `per`) |
| `large_size` | booleano | la otra presentación es ≥ 5 veces más grande (mayoreo) |

Se listan hasta 12 (las de tamaño más cercano), ordenadas por tamaño. Precisión
validada: holdout ciego de 94 pares 98.5 % ponderado por estrato y revisión del
responsable de 50 pares al azar. Un fallo de la regla no bloquea la publicación:
el catálogo sale sin el campo y el motivo queda en stderr del exportador.

## Serving particionado

La publicación contiene:

```text
manifest.json
facets.json
indexes/...
partitions/...
```

- el arranque carga únicamente manifest + facetas;
- los índices se cargan bajo demanda según la navegación;
- las particiones tienen como máximo 250 filas;
- cada archivo tiene tamaño y SHA-256 verificables;
- el navegador valida el contrato antes de usar los datos;
- no hay lecturas directas a Turso;
- no hay matching en JavaScript.

La partición física sigue los grupos de navegación para evitar fan-out innecesario. Categorías conocidas se organizan por tipo de producto y el fallback no clasificado usa prefijos normalizados.

## Promoción y precio

`is_promotion` puede ser `true`, `false` o `null`. `null` significa que la fuente no aporta evidencia suficiente; la UI no debe convertirlo a “no está en promoción”.

`current_price` es el precio efectivo observado usado para comprar/calcular. `reported_regular_price` sólo es referencia y no entra en totales ni historia como si fuera una observación anterior.

# Contrato monetario B2C

```text
unit_price = current_price
line_total = unit_price * quantity
retailer_subtotal = sum(line_total)
grand_total = sum(retailer_subtotal)
```

No se agrega ISV ni cargos de shipping, delivery, service o membership. Si una línea no tiene precio utilizable, su total y los totales dependientes quedan nulos/incompletos; nunca cero.

# Frontera pública y atomicidad

Los artifacts públicos no contienen credenciales, URLs privadas de base de datos, RAW, cookies, tokens ni colas de revisión. La publicación valida schema, scope, hashes, tamaños y ausencia de secretos antes de reemplazar el último corte válido.

Consumer Mart v2 y Consumer Catalog v3 se publican en `portfolio-data`. Business Mart v1 permanece privado dentro del artifact analítico para el flujo B2B.
