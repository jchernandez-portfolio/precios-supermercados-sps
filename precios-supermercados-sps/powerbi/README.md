# Power BI — Precios de Supermercados SPS

Esta carpeta contiene activos versionables para construir el dashboard sobre la capa analítica segura.

La evolución RPI B2B basada en `rpi-business-mart/v1` vive en [`rpi/`](rpi/).
Mantiene un origen local privado, freshness/PCI upstream y la especificación de
las nueve páginas objetivo. Los queries de esta carpeta raíz conservan el modelo
estático v1 vigente mientras la publicación RPI automática sigue pendiente.

## Fuente de verdad

La lógica de identidad, comparabilidad, ahorro, canasta común e histórico vive en Python y sus tests. Power BI consume el dataset de publicación; no vuelve a homologar productos por nombre, marca o presentación.

Documentación relacionada:

- `../docs/COMPARATOR-METHODOLOGY.md`
- `../docs/PUBLICATION-DATA-DICTIONARY.md`
- `../docs/BI-IMPLEMENTATION-GUIDE.md`

## Fuente estática para refresh

Después de cada publicación analítica válida, el workflow de sincronización reutiliza **el mismo artifact seguro ya generado** y materializa una copia pública estable en la rama `portfolio-data`.

Fuente Web recomendada para Power BI:

```text
https://raw.githubusercontent.com/jchernandez-portfolio/precios-supermercados-sps/portfolio-data/precios-supermercados-sps/published/bi/la-colonia-walmart-sps/dataset.json
```

Ese archivo contiene el contrato `precios-sps-static-bi-dataset/v1` con:

- `publication.offers`;
- `publication.products`;
- `publication.common_basket`;
- `publication.scope`;
- `source_descriptors` para nombres, marca, presentación y categoría ya verificados;
- `manifest` y procedencia (`source_workflow_run_id`, `source_head_sha`, timestamps).

La actualización de esta fuente **no ejecuta una consulta adicional a Turso**: copia y valida el artifact creado por `Precios SPS - Publicar analítica segura`. De esta forma Power BI y el portafolio reutilizan la misma publicación y las visitas o refresh del dashboard no consumen Turso.

## Activos reproducibles

- `theme.json`: tema base importable en Power BI.
- `queries/StaticDataset.pq`: única consulta Web; valida schema y política antes de exponer el documento.
- `queries/Offers.pq`: precios actuales seguros por producto, supermercado y ubicación.
- `queries/Products.pq`: mínimo, máximo y ahorro por producto comparable.
- `queries/CommonBasket.pq`: canasta común con denominador idéntico.
- `queries/Scope.pq`: alcance explícito y `scope_key`.
- `queries/SourceDescriptors.pq`: nombres, marca, presentación y categoría de las ofertas autorizadas.
- `queries/RefreshMetadata.pq`: run, SHA, timestamp y conteos del corte publicado.
- `measures.dax`: medidas base fail-closed para resumen, ahorro y canasta.

En Power BI, crear primero la consulta `StaticDataset` y después las consultas que la referencian con los nombres de archivo indicados. Sólo `StaticDataset` debe acceder a Web. Las demás transformaciones trabajan en memoria sobre el mismo documento descargado.

Relaciones base:

```text
Products[canonical_product_id] 1 ─── * Offers[canonical_product_id]
SourceDescriptors[source_record_id] 1 ─── * Offers[source_record_id]
Scope[scope_key] 1 ─── * Offers[scope_key]
Scope[scope_key] 1 ─── * CommonBasket[scope_key]
```

No crear relaciones por nombre, marca o presentación.

Los artefactos binarios `.pbix` no se consideran la definición reproducible del modelo. Cuando se publique un PBIX, debe poder reconstruirse usando el contrato, estas consultas y la guía conservados en Git.

## Páginas sugeridas

1. Resumen ejecutivo.
2. Comparador de producto.
3. Canasta común.
4. Cambios desde la ejecución anterior.
5. Histórico y variabilidad.
6. Cobertura y exclusiones del matching.

Las páginas 4 y 5 requieren que el contrato público incorpore explícitamente series/cambios históricos; no deben simularse usando sólo el snapshot actual.

## Regla visual crítica

Una selección con cero productos comparables debe mostrar un estado vacío. No se debe convertir un total cero en “supermercado más barato”.
