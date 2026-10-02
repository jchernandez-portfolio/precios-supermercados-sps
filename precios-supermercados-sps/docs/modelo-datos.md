# Modelo común de datos y almacenamiento

Este documento describe el modelo físico productivo y las capas derivadas RPI. El estado mutable vive en [`PROJECT_STATE.md`](PROJECT_STATE.md).

## Backend

- **Productivo:** Turso/libSQL.
- **Local/reproducible:** SQLite.
- Google Sheets y BigQuery permanecen sólo como componentes históricos/experimentales; no son la ruta productiva vigente.

La base comercial es única para todas las cadenas y ubicaciones.

# Identidades

```text
supermarket_id       = cadena
location_id          = contexto comercial demostrado
source_key           = identidad estable entregada por la fuente
product_id           = PK interna de producto fuente
canonical_gtin       = GTIN validado/canonizado cuando existe
canonical_product_id = identidad fuerte derivada para homologación/comparación
scrape_run_id        = ejecución persistida
```

Precio, promoción, disponibilidad y timestamps no forman parte de IDs estables.

`canonical_product_id` no sustituye `product_id`: sólo puede agrupar fuentes cuando la evidencia lo permite.

# Modelo físico principal

```text
supermarkets 1 ─── N locations
supermarkets 1 ─── N products
products     1 ─── N price_history
locations    1 ─── N price_history
scrape_runs  1 ─── N periodos originados/confirmados
products     1 ─── 0..1 product_homologation_profiles
```

## `supermarkets`

**Grain:** una fila por cadena.

## `locations`

**Grain:** una fila por ubicación/contexto comercial persistible. La ciudad es atributo de la ubicación, no identidad del producto.

## `products`

**Grain:** una identidad fuente estable dentro de un supermercado.

Puede conservar nombre, marca, presentación, categoría, GTIN/EAN y llaves fuente. El nombre no es clave y marca + presentación no forman una clave cross-source.

## `price_history`

**Grain:** un periodo comercial por `product_id + location_id`.

Campos conceptuales:

```text
product_id
supermarket_id
location_id
current_price_minor
reported_regular_price_minor
is_promotion
availability
currency
valid_from_utc
valid_to_utc
scrape_run_id
```

`valid_to_utc IS NULL` representa el estado vigente. No existe una segunda tabla current que pueda divergir del histórico.

Si un nuevo estado aceptado es idéntico, el periodo permanece abierto. Si cambia un atributo comercial relevante, se cierra y abre un nuevo periodo. No se generan snapshots diarios redundantes.

## `scrape_runs`

**Grain:** una ejecución persistida por supermercado/ubicación.

Conserva ID de run, scope, estado terminal, conteos, timestamps y digest del snapshot aceptado. El postflight comprueba que run y SHA correspondan a la evidencia persistida.

# Precio y ausencia

Se distinguen:

```text
current_price
reported_regular_price
historical_previous_price
```

- `current_price` es el precio efectivo observado.
- `reported_regular_price` es referencia declarada por la tienda.
- `historical_previous_price` proviene del `current_price` de un periodo aceptado anterior.

Sin baseline real no se inventa ahorro.

Ausencia de evidencia no equivale a `out_of_stock` ni a precio cero. Los estados de disponibilidad se conservan explícitamente.

# Integridad

Después de persistir se verifican, según el flujo:

- `PRAGMA integrity_check`;
- foreign keys;
- ausencia de periodos actuales duplicados;
- presencia de run IDs exactos;
- reconciliación de conteos con snapshots aceptados.

Un HTTP 200 del backend no sustituye estas verificaciones.

# Observaciones sin precio agotadas

`catalog_unpriced_observations` (STRICT, derivada/opcional; regla 2026-10-02)
registra SKU que un retailer lista **sin precio y agotados** ("NO DISPONIBLE"):
lanzamientos, primer precio y métricas de disponibilidad. No crea `products` ni
`price_history` (que exigen precio).

| Columna | Significado |
| --- | --- |
| `supermarket_id`, `location_id` | contexto exacto (FK a `locations`) |
| `source_key_type`, `source_key` | identidad fuente (misma que `products`) |
| `source_catalog_product_id`, `name`, `brand`, `category`, `presentation`, `ean` | descriptores fuente del último avistamiento |
| `first_seen_utc` | primera vez vista sin precio en ese contexto |
| `last_seen_utc` | último snapshot aceptado que la listó sin precio |
| `priced_since_utc` | primer snapshot posterior en que apareció con precio; `NULL` mientras siga pendiente |

PK `(supermarket_id, location_id, source_key_type, source_key)` e índice parcial
`idx_catalog_unpriced_pending(supermarket_id, location_id) WHERE priced_since_utc IS NULL`.
La persistencia diaria hace upsert sólo de las entradas del snapshot (≤2 % del
catálogo) y marca `priced_since_utc` recorriendo sólo las pendientes del contexto.
Si la oferta vuelve a quedar sin precio se reabre el episodio (`priced_since_utc =
NULL`, `first_seen_utc` se conserva). Si un producto con periodo current con precio
se lista hoy sin precio y agotado, ese periodo se cierra sin abrir uno nuevo.

# Homologación derivada

`product_homologation_profiles` es una proyección reconstruible de `products`; no altera la fuente comercial.

Puede contener:

```text
product_id
supermarket_id
normalized_name
normalized_brand
canonical_gtin
canonical_product_id
category
subcategory
product_type
taxonomy_rule_id
presentation_dimension
presentation_total_base
presentation_pack_count
presentation_unit_amount_base
presentation_status
comparison_status
conflict_reasons_json
normalization_version
profile_hash
updated_at_utc
```

`comparison_status` descriptivo no autoriza por sí solo una comparación. La autorización final pertenece al comparador seguro sobre el grupo completo.

# Identidad cross-source

Un GTIN sólo es fuerte cuando su formato/check digit es válido y la normalización es determinista. Sin identidad fuerte, el registro puede permanecer visible/revisable pero no entra automáticamente en ahorro o canasta.

Incluso con GTIN común, contradicciones de marca, tipo, presentación o variante pueden bloquear la comparación.

Regresión explícita:

```text
Passion Jaguar 1 lb != Passion Especial 1 lb
```

# Capa analítica

La analítica consume únicamente grupos autorizados y estado comercial aceptado.

Conceptos principales:

- observación actual por oferta;
- comparación por producto canónico;
- mercado comparable con una ubicación explícita por retailer;
- canastas con denominador común;
- historial por oferta exacta;
- promoción declarada vs reducción histórica;
- freshness y cobertura.

Un faltante no se imputa. Un universo vacío no tiene ganador.

# Contratos RPI derivados

## `rpi-business-mart/v1`

Contrato B2B privado. Incluye:

- `dim_product`;
- `dim_retailer`;
- `dim_location`;
- `dim_category`;
- `dim_brand`;
- `fact_current_comparison`;
- `fact_price_history`;
- `fact_promotion_analysis`;
- `fact_basket_cost`;
- `fact_metric_coverage`;
- `source_freshness`.

Los periodos de `fact_price_history` son estados comerciales reales, no una serie diaria sintética.

## `rpi-consumer-mart/v2`

Contrato público analítico reducido. Publica sólo identidades/ofertas autorizadas para comparación segura, deltas, recomendaciones, freshness e historia resumida.

## `rpi-consumer-catalog/v3`

Contrato público de navegación para Compra Inteligente. Separa visibilidad de comparabilidad y permite publicar filas `comparable`, `single_source` o `individual` sin convertirlas automáticamente en equivalencias cross-retailer.

Se distribuye como manifest, facetas, índices demand-loaded y particiones de máximo 250 filas.

# Exportación RPI

`scripts/exportar_rpi_marts.py` lee el estado confiable de forma read-only y genera los marts, CSVs B2B y manifests con SHA-256.

La exportación no hace scraping ni escribe Turso.

Los contratos históricos `precios-sps-publication/v1` y `precios-sps-static-bi-dataset/v1`, junto con `scripts/exportar_modelo_analitico.py`, permanecen para compatibilidad/evidencia de etapas anteriores; no representan el serving RPI principal actual.

# Consumidores

## Power BI

Consume Business Mart v1 mediante los activos de `powerbi/rpi/`. No resuelve identidad, no hace matching textual y no consulta Turso.

## Compra Inteligente

Consume Consumer Catalog v3 para navegación y Consumer Mart v2 para la frontera analítica aplicable. El navegador no crea identidades ni recomendaciones por sí mismo.

# Frontera public/private

Público:

- Consumer Mart v2;
- Consumer Catalog v3;
- muestra de portafolio.

Privado:

- Business Mart v1;
- RAW;
- colas/revisión interna;
- secretos/credenciales.

La publicación valida schemas, scope, hashes, tamaños y secretos y reemplaza el último corte público de forma atómica.

# Fuente de verdad

- arquitectura: [`arquitectura.md`](arquitectura.md);
- estado operativo: [`PROJECT_STATE.md`](PROJECT_STATE.md);
- producto: [`RPI-PRODUCT-SPEC.md`](RPI-PRODUCT-SPEC.md);
- marts/catalog: [`RPI-DATA-MART-DICTIONARY.md`](RPI-DATA-MART-DICTIONARY.md);
- metodología: [`COMPARATOR-METHODOLOGY.md`](COMPARATOR-METHODOLOGY.md).
