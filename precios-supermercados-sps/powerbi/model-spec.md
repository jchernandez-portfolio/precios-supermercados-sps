# Especificación reproducible del modelo Power BI

Esta especificación completa los activos versionables de `powerbi/` y define cómo construir el modelo semántico sin introducir una segunda implementación de la lógica de negocio.

## Fuente única

La única consulta que puede acceder a Web es `StaticDataset` y debe usar el dataset público estático de `portfolio-data`:

```text
https://raw.githubusercontent.com/jchernandez-portfolio/precios-supermercados-sps/portfolio-data/precios-supermercados-sps/published/bi/la-colonia-walmart-sps/dataset.json
```

`StaticDataset` valida el schema `precios-sps-static-bi-dataset/v1` y la política `fail_closed_strong_identity_and_commercial_consistency`. Las demás consultas se derivan de ese objeto en memoria y no consultan Turso ni los sitios de supermercados.

## Tablas

| Tabla | Grano | Clave funcional |
| --- | --- | --- |
| `Products` | un producto canónico comparable | `canonical_product_id` |
| `Offers` | una oferta segura por producto canónico y alcance | `canonical_product_id + supermarket_id + location_id` |
| `SourceDescriptors` | descriptor fuente de una oferta homologada | `source_record_id` |
| `Scope` | una ubicación incluida en el alcance analítico | `scope_key` |
| `CommonBasket` | total de la misma canasta común por ubicación | `scope_key` |
| `RefreshMetadata` | una fila de procedencia de la publicación | una fila |

## Relaciones

Crear únicamente estas relaciones activas:

```text
Products[canonical_product_id] 1 ─── * Offers[canonical_product_id]
SourceDescriptors[source_record_id] 1 ─── * Offers[source_record_id]
Scope[scope_key] 1 ─── * Offers[scope_key]
Scope[scope_key] 1 ─── * CommonBasket[scope_key]
```

No crear relaciones por nombre, marca, presentación, categoría ni GTIN textual no validado. La homologación cross-source ya ocurrió antes de publicar el dataset.

## Direcciones de filtro

Usar filtro simple desde las dimensiones hacia los hechos. Evitar relaciones bidireccionales salvo una necesidad demostrada en un visual concreto. `Products`, `SourceDescriptors` y `Scope` filtran `Offers`; `Scope` filtra `CommonBasket`.

## Medidas

Importar las medidas versionadas de `measures.dax`. Las medidas de canasta deben devolver `BLANK()` cuando `Productos canasta común` sea cero. Un universo vacío no produce supermercado ganador, ahorro ni diferencia de canasta.

## Refresh

El refresh diario del modelo consume sólo la copia estática publicada después de una ejecución analítica segura. El pipeline es:

```text
scraping productivo
→ persistencia validada
→ homologación
→ analítica fail-closed
→ artifact seguro
→ copia estática en portfolio-data
→ Power BI
```

Un refresh de Power BI no dispara scraping ni consulta Turso. Si la publicación segura no avanza, Power BI conserva la última copia estática válida disponible.

## Alcance actual

El contrato analítico cross-source publicado está limitado a:

- `la_colonia_sps`;
- `walmart_sps`.

La cobertura productiva del scraper es más amplia que la cobertura comparable. No ampliar el alcance de Power BI a otras cadenas hasta que exista identidad cross-source fuerte y el workflow de publicación las incluya explícitamente.

## Histórico

No construir gráficos históricos desde el dataset estático actual: el contrato publicado contiene el estado comparable actual, no una serie temporal completa. Los visuales históricos se habilitan sólo cuando exista un contrato público histórico versionado y probado. Esto evita reconstruir historia a partir de snapshots o inferencias.
