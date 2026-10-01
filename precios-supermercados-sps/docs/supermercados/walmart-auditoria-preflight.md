# Walmart Honduras — auditoría y avance del full

Actualización: 2026-08-31 UTC. **GO técnico: tres catálogos aceptados y SQL productivo
validado offline.** El [reporte full](../../reports/walmart/2026-08-31-full/README.md)
sustituye la frontera anterior: autorización propia de 24 horas desde 00:48:01 UTC,
514 GET/0 retries/concurrencia 1/1,327.921 s, dentro de 1,000 GET/45 minutos.
SPS 13,656 productos; FFAA 14,083; El Sauce 13,989. Se conservan ambos TGU por la
diferencia comercial demostrada antes del full. RAW y tres snapshots reproducibles,
41,752 observaciones SKU, 1,597 ofertas sin precio conservadas como NULL.

El updater existente admite Walmart después de una migración puntual de dos
restricciones reales: dos TGU por ciudad y ofertas agotadas sin precio/promoción.
La migración y el SQL productivo pasaron offline junto a La Colonia y Colonial,
sin alterar sus filas. Cinco tablas, delta único, costo lineal e independiente de
histórico cerrado. No SQL Turso, billing, segunda observación ni workflow Walmart.
[Estado y frontera de primera carga](../PROJECT_STATE.md).

Revisión posterior solicitada por el usuario: comparación de los fulls TGU sin
nuevo tráfico. 12,867 SKU compartidos / 12,042 comparables; 255 diferencias
comerciales (218 efectivo, 197 regular, 57 promoción) frente a 331 casos sólo de
disponibilidad. **Se conservan los dos contextos para persistencia productiva**;
las ofertas sin precio y la disponibilidad no se usan para justificar separarlos.
[Informe exhaustivo, RAW y límite de atribución](../../reports/walmart/2026-08-31-full/TGU-COMPARISON.md).

Las secciones siguientes conservan la **auditoría y preflight históricos anteriores
al full**. Sus menciones a autorización, catálogo o SQL pendientes describen ese
momento, no el estado actual ni permiso para repetir el adquirente archivado.

## Auditoría inicial contra GitHub — 2026-08-30

- Base auditada: `e8da4737476c8166728d320c87c2c471679d0878`, con
  [PR #351](https://github.com/jchernandez-portfolio/precios-supermercados-sps/pull/351) fusionado y
  [CI de main verde](https://github.com/jchernandez-portfolio/precios-supermercados-sps/actions/runs/33337389512).
- Cero PRs abiertos al comenzar; búsqueda de Walmart en archivos, contenido,
  configuración y workflows sin implementación o fixtures existentes.
- Leídos AGENTS aplicables, PROJECT_STATE, workflow diario, schema, validador,
  updater SQL, pruebas de costo y evidencia Colonial. La continuación explícita
  del usuario inicia Walmart; se actualizan referencias de fase obsoletas sin
  ampliar autoridad live, billing ni recurrencia.
- La Colonia conserva SPS/TGU y operación implementada. Los últimos schedules
  observados siguen siendo 33260860123 y 33319436863, fallidos y ya documentados.
  No se repite su extracción ni se declara éxito de schedule inexistente.
- Colonial conserva 9,199 productos / 9,205 variantes y RAW íntegro. Faltan primera
  carga Turso, segunda observación y cierre operativo; no duplicar ese trabajo.
- `turso plan show` reconfirma Starter, overages deshabilitados, 713.7 M / 500 M
  lecturas (143%) y reset anunciado 31/8/2026 18:00 CST (Honduras). Sólo API de
  cuenta: ningún SQL remoto, tuning, cambio de plan ni intento de sortear cuota.

Biblioteca reusable auditada en
`252b245e0f416b57c324db97bc9cee868fc8124d`. Se revisó íntegramente la actualización
de `production-data-engineering`, especialmente acceso por scope, delta único,
writes sólo ante cambios, planes, N/2N/4N y evidencia facturada separada.
Las seis skills web solicitadas y las complementarias de seguridad, pruebas,
pipeline, provenance, auditoría, diseño, CI, debugging y entrega no cambiaron
respecto a la revisión anterior. Se aplica también `documentation-state` para
separar estado actual de evidencia histórica. No se copiaron skills al proyecto.

## Reconocimiento anterior al probe — evidencia histórica

La [portada oficial](https://www.walmart.com.hn/) muestra enlaces a recursos de
`walmarthn.vtexassets.com` y categorías de ecommerce. Esto es una **pista VTEX**,
no prueba de que una API determinada esté habilitada ni de su contexto comercial.
Se utilizó investigación web permitida; no se lanzó runner HTTP ni browser
automatizado contra Walmart, ni se capturó RAW de una sesión propia.

La [documentación oficial de VTEX](https://developers.vtex.com/docs/api-reference/search-api)
describe un listado público candidato `/api/catalog_system/pub/products/search`.
Sólo se probará después de autorización y de contrastar la plataforma en RAW.
No asumir de antemano page size, límite total, campos, seller, región ni store ID.

Una [publicación oficial de Walmart de 2024](https://www.walmartcentroamerica.com/noticias/2024/08/world-vision-honduras-y-walmart-recolectaran--toallas-sanitarias)
nombra El Sauce y Fuerzas Armadas en Cascadas Mall. Es evidencia histórica de
nombres, no un mapa técnico actual zona→tienda. La correspondencia Las Uvas/El
Sauce, unicidad del formato en SPS y selector Departamento/Municipio/Zona siguen
pendientes de comprobación técnica. No se consultaron términos ni documentos legales.

| Cuestión | Estado antes del probe |
| --- | --- |
| Plataforma | Pista VTEX; verificar HTML/estado propio |
| Producto, variant/item, SKU/GTIN | No demostrados en RAW propio |
| Precio efectivo, regular y promoción | Contrato Walmart no validado |
| Disponibilidad | Semántica no validada; no inferir ausencia→agotado |
| SPS | Boulevard del Norte candidato; scope/identificador desconocido |
| TGU | Cascadas/Fuerzas Armadas vs Las Uvas/El Sauce pendientes |
| Zona→tienda, cookies/storage/headers/variables | No inspeccionados en sesión propia |
| Total, paginación y membership | Desconocidos |
| Primera muestra y comparación TGU | No ejecutadas |

## Primer probe: presupuesto autorizado y ejecutado, conservado como referencia

Autorización del usuario registrada 2026-08-31 00:17:55 UTC, válida por 24 horas.
Se agotaron las 20 solicitudes durante una sola ejecución; no queda saldo live.
La especificación siguiente es la que limitó esa captura, no permiso para repetirla.

Objetivo: confirmar fuente pública, obtener primero un producto correcto, luego
una muestra inicial de 20–50 cuando sea posible, y descubrir el mecanismo que
vincula contexto visible con precio/stock. No intentar full ni decidir ubicación
por nombre de zona sin evidencia.

Límite: **20 solicitudes GET en total**, incluidos hasta **dos retries** globales,
máximo uno por recurso; concurrencia 1, sesión/conexión reutilizada, pausa mínima
de un segundo, timeout individual 20 segundos y deadline total **10 minutos**.
No ampliar el presupuesto durante la ejecución. No seguir redirects automáticamente;
un redirect permitido cuenta como otra solicitud dentro del mismo máximo.

Superficie: `https://www.walmart.com.hn/`, catálogo público y configuración pública
necesaria de su frontend. Como máximo dos scripts/configuraciones enlazados por
ese HTML, sólo si resuelven un dato indispensable, incluyendo el CDN público
`walmarthn.vtexassets.com`. No imágenes, CSS, fuentes, analytics ni otros assets.
No browser, login, carrito, checkout, registro, mutaciones de cuenta, API privada,
credenciales administrativas ni identidad alternativa. Una necesidad de interacción
o escritura de contexto anónimo no cubierta se documenta antes de ampliar el probe.

Secuencia adaptativa, sin ejecutar todas las alternativas por defecto:

1. HTML inicial: plataforma, estado embebido, selector/contexto y URLs públicas.
2. Primera fuente estructurada prometedora: una solicitud para un producto;
   contrastar identidad, nombre y precio con evidencia fuente.
3. Si es válida: página de 20–50 con normales/promociones y distintas categorías,
   sin abrir una página por producto. Preservar variantes sin deduplicar por nombre.
4. Reusar RAW para semántica y contexto. Leer sólo configuración pública necesaria
   para identificar departamento/zona, seller/store, segmento y binding.
5. Si el contexto se puede reproducir con GET públicos demostrados: contrastar
   SPS y ambos candidatos TGU dentro del presupuesto. Si no, dejar el binding
   pendiente y describir la interacción exacta requerida; no fabricar store IDs.
6. Usar saldo únicamente para señal de paginación/total/membership, o una alternativa
   pública simple ante una hipótesis incorrecta. No recorrer el catálogo completo.

Retry sólo ante timeout o 5xx transitorio; stop ante 401/403/429, CAPTCHA, login,
control anti-bot o degradación sostenida. Un parser fallido se corrige offline;
no provoca otra descarga. No repetir URLs ya recibidas y aceptadas.

Evidencia esperada: URL/método/status/fecha/SHA, RAW, identidad fuente, campos
comerciales y unknowns, nombres de variables de contexto sin exponer secretos,
relación visible→técnica→scope cuando se pueda demostrar, métricas y residual.
La muestra sin binding no se aceptará como catálogo de SPS ni de TGU.

La autorización de este probe no cubre full, segunda observación ni recurrencia.
El preflight full ya se calculó desde las respuestas: 41,728 observaciones de
producto entre los tres contextos, aproximadamente 890 páginas a tamaño 50.
Son estimaciones para captura y reconciliación, no catálogos completos obtenidos.

## Decisión de Tegucigalpa antes del full

**Decisión ejecutada:** separar FFAA y El Sauce. El SKU `68100` reproduce regular
2,195 frente a 1,895, mismo efectivo 1,895; cambiar sólo `regionId` reproduce el
cambio. Las reglas originales siguientes se mantienen para revisar esa decisión.

Comparar las mismas 20–50 identidades compartidas, varias categorías y promociones,
en ambos contextos técnicos demostrados. Precio efectivo, regular y promoción
se comparan separadamente de disponibilidad. Registrar cobertura de la intersección,
RAW por contexto y productos que no pudieron compararse; no tratar faltantes como
equivalentes ni como agotados.

- Equivalencia comercial observada y sin evidencia técnica de diferenciación de
  precios relevante: un contexto lógico TGU, documentando el representativo.
- Una diferencia comercial reproducible atribuible a tienda: contextos separados,
  conservando evidencia antes del full.
- Sólo diferencias de stock: mantener inicialmente un TGU representativo.
- Binding o comparación insuficiente: decisión pendiente, sin consolidación forzada.

Nunca denominar el resultado «precio universal de Tegucigalpa». La granularidad
aceptada será la del contexto seleccionado demostrado; no todas las zonas de TGU.

## Persistencia reutilizable: comprobación offline del PR #351

Se mantiene la única base y cinco tablas. El updater actual sólo acepta La Colonia
y Colonial; Walmart no se puede introducir cambiando el nombre del snapshot ni
llamando a helpers internos sin validar. Primero fuente y contrato, luego extensión
específica mínima del mismo hot path. No crear un updater paralelo ni sexta tabla.

Revalidación: **48 tests pasaron** de costo, SQLite/Turso, Colonial, captura completa
y auditoría de workflows. Benchmark usando los helpers existentes, SQLite 3.53.4,
datos sintéticos La Colonia y mismo SQL productivo:

| N | Instrucciones VM | Crecimiento |
| ---: | ---: | ---: |
| 128 | 31,800 | — |
| 256 | 63,400 | 1.99× |
| 512 | 127,300 | 2.01× |

Run sin cambios: `upsert_products=0`, `close_history=0`, `open_history=0`,
`insert_run=1`. EXPLAIN usa `idx_price_history_current` para periodos actuales por
ubicación/producto y el índice único de identidad en `incoming`. `delta` se
calcula una vez; sus scans son del conjunto efímero, no del histórico completo.
El join de productos usa el índice por supermercado/identidad. No se añadió índice.

Esto verifica reutilización del camino actual, **no** persistencia Walmart ni
ahorro facturado en Turso. Cuando Walmart modifique SQL se exigirán de nuevo
N/2N/4N, writes cero ante estado idéntico, metadata aislada, rollback, replay y
aislamiento Walmart↔La Colonia y Walmart↔Colonial con IDs fuente coincidentes.

No se detectó un blocker compartido que justifique más tuning antes de demostrar
Walmart. Tampoco se diseña workflow Walmart antes de catálogo aceptado,
persistencia validada y segunda observación real posterior.

Tras reset y autorización vigente: medir consumo inicial, una carga controlada,
medir consumo final y verificar scope afectado. No usar la producción para ajustar
iterativamente consultas comprobables offline.
