# Guía de implementación en Power BI — Retail Price Intelligence

## Fuente autoritativa

El modelo B2B vigente consume `rpi-business-mart/v1`, generado de forma read-only por:

```text
scripts/exportar_rpi_marts.py
```

Business Mart es un artifact privado/reproducible. **No se publica en el namespace B2C de `portfolio-data`** y Power BI no debe consultar directamente sitios de supermercados ni Turso.

Los activos versionados están en:

```text
precios-supermercados-sps/powerbi/rpi/
```

`BusinessMart.pq` valida el schema `rpi-business-mart/v1` y la política de comparación antes de exponer tablas al modelo.

## Modelo vigente

Tablas esperadas:

### Dimensiones

- `DimProduct`
- `DimRetailer`
- `DimLocation`
- `DimCategory`
- `DimBrand`

### Hechos / estado

- `FactCurrentComparison`
- `FactPriceHistory`
- `FactPromotionAnalysis`
- `FactBasketCost`
- `FactMetricCoverage`
- `SourceFreshness`
- `BusinessMetadata`

Las relaciones se construyen por IDs estables, nunca por nombres o similitud textual. El detalle exacto de cardinalidad y grain está en `powerbi/rpi/model-spec.md`.

## Responsabilidades de Python y Power BI

Python es la fuente de verdad para:

- homologación/comparabilidad;
- precio actual y anterior;
- cambios absolutos y porcentuales;
- PCI y ranking;
- mínimo/máximo/media/mediana de mercado;
- spread;
- freshness y bloqueo de comparación;
- clasificación de promociones e historia;
- cobertura;
- canastas y totales críticos.

Power BI puede:

- agregar;
- filtrar;
- ordenar;
- presentar medidas ya autorizadas;
- construir visuales y navegación.

Power BI **no debe** volver a hacer matching, recalcular identidades, inventar históricos o usar `reported_regular_price` como si fuera un precio observado anterior.

## Facts principales

### `FactCurrentComparison`

Una fila por oferta segura actual. Permite analizar precio, posición competitiva, rank, PCI, diferencia contra mínimo, disponibilidad y freshness.

### `FactPriceHistory`

Una fila por periodo comercial realmente observado, no una fila diaria sintética. Soporta movimientos, dirección, cambio absoluto/porcentual y precio anterior.

### `FactPromotionAnalysis`

Una fila por oferta segura en el corte analítico. Mantiene separadas la promoción declarada por la fuente y la reducción histórica observada.

### `FactBasketCost`

Costos de canasta sobre denominadores comparables explícitos. Una canasta incompleta no debe presentarse como total válido.

### `FactMetricCoverage`

Cobertura del corte: comparables, precios válidos, exclusiones, porcentaje y ventana de freshness.

## Refresh

El flujo correcto es:

```text
actualización productiva aceptada
→ Turso / histórico
→ homologación derivada
→ exportación RPI read-only
→ Business Mart v1 validado
→ Power BI
```

Refrescar Power BI **no ejecuta scraping** ni dispara el pipeline productivo. Si el procesamiento RPI falla, se conserva el último artifact válido.

## Tipos de datos

- IDs y GTIN: texto.
- importes: Decimal fijo / moneda HNL.
- porcentajes: decimal porcentual ya expresado como porcentaje (`16.67` = 16.67%).
- timestamps: datetime UTC.
- booleanos: verdadero/falso; no convertir `null` semántico a falso.

## Páginas especificadas

El paquete RPI define nueve páginas:

1. Executive Market Overview
2. Competitive Pricing
3. Category Intelligence
4. Price Movements
5. Promotion Intelligence
6. Brand Intelligence
7. Geographic Intelligence
8. Assortment / Coverage
9. Opportunities & Alerts

La definición funcional de cada página está en `powerbi/rpi/page-spec.md`. El tema y las medidas versionables también están en ese directorio.

## Reglas visuales

- Mostrar `as_of` y freshness de forma visible.
- Reconocer empates reales; no forzar un único ganador visual.
- Un universo vacío debe mostrar “Sin productos comparables para este alcance”, no ahorro L 0.
- No comparar fuentes `STALE`/`UNAVAILABLE` como si fueran actuales.
- No inferir ciudad desde texto cuando existe `location_id` explícito.
- No sumar mínimos de distintos supermercados para presentarlos como canasta de una sola cadena.

## Seguridad

No incluir en PBIX/PBIP, parámetros, consultas o archivos públicos:

- `TURSO_AUTH_TOKEN`;
- URL privada de Turso;
- cookies o headers de autenticación;
- secretos de GitHub Actions;
- RAW o colas de revisión.

## Contrato legado y compatibilidad

`precios-sps-static-bi-dataset/v1` y su refresh histórico se conservan como compatibilidad/evidencia de la fase anterior. La URL estable de ese dataset sigue siendo:

```text
https://raw.githubusercontent.com/jchernandez-portfolio/precios-supermercados-sps/portfolio-data/precios-supermercados-sps/published/bi/la-colonia-walmart-sps/dataset.json
```

Para reconstruir el modelo legado, el mapeo contractual original permanece explícito:

- `Offers` ← `publication.offers`;
- `Products` ← `publication.products`;
- `CommonBasket` ← `publication.common_basket`;
- `SourceDescriptors` ← `source_descriptors`.

`SourceDescriptors[source_record_id]` se relaciona con las ofertas por la identidad fuente ya publicada; tampoco en este flujo legado se autoriza matching por texto.

Esa ruta permanece disponible para el modelo legado, pero **no es el modelo RPI B2B principal actual**. El desarrollo nuevo debe usar `rpi-business-mart/v1` desde el artifact privado validado.

## Reproducibilidad y `.pbix`

El repositorio conserva consultas Power Query, DAX, tema, relaciones, grains y especificación de páginas. Esa es la fuente de verdad reproducible.

Un `.pbix` final puede construirse en Power BI Desktop para presentación, pero no se debe fabricar un binario opaco ni afirmar que existe si no ha sido generado con Power BI Desktop. La ausencia del binario no elimina la reproducibilidad del modelo versionado.
