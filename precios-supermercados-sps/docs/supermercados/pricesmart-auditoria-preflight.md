# PriceSmart Honduras — auditoría y preflight

> Documento histórico de los probes iniciales. El estado operativo vigente está
> en [`PROJECT_STATE.md`](../PROJECT_STATE.md); el preflight del catálogo general
> está en [su reporte reproducible](../../reports/pricesmart/2026-09-02-general-catalog-preflight/README.md).

## Resultado de los probes autorizados

### Captura posterior de la petición real del navegador

Una sesión CDP anónima encontró y reprodujo la fuente pública que faltaba:
`POST https://www.pricesmart.com/api/br_discovery/getProductsByKeyword`. El
navegador y un replay con sólo `Accept`, `Content-Type` y `Referer`, sin cookie,
recibieron HTTP 200 y bodies idénticos. La muestra contiene 12 de 1,124 productos;
el SKU `479223` conserva identidad completa, `price_HN=35995` (HNL 359.95) y
availability `true/in stock`. No se devolvió precio regular y las etiquetas de
campaña no prueban promoción de precio.

La petición está vinculada a `HN`, no a un club: la página mostraba `Seleccionar
entrega`, no se envió `6603` y los productos no incluyeron valores `price_HN_6603`.
Los nombres de facets por club sólo demuestran dimensiones del índice. Además, el
replay terminó 27.919 s después del máximo de cinco minutos. Resultado: **fuente
HN demostrada, binding SPS no demostrado, protocolo no conforme**. No parser,
fixture, scraper, full, presupuesto, persistencia ni Turso. Ver
[captura CDP, RAW y decisión](../../reports/pricesmart/2026-09-01-browser-request-probe/README.md).

Las conclusiones GraphQL siguientes siguen siendo hechos históricos sobre otra
superficie, pero ya no justifican afirmar que PriceSmart carece de toda fuente
pública de precio.

La primera extensión POST envió `channels` al endpoint raíz y recibió HTTP 404
`Cannot POST /`: 1/8 POST, sin retry. La segunda extensión autorizó `/graphql`,
que sí validó GraphQL pero rechazó el contrato observado con HTTP 400: no existen
`channels`, `Locale` ni `Point` en ese esquema y el servidor sugiere
`findChannels`. Se consumió 1/7 POST, sin retry; 6 quedaron sin usar. Como
`findChannels` no estaba autorizado, el tráfico se cerró sin adaptar la consulta.
Ver [RAW y cierre de esquema](../../reports/pricesmart/2026-08-31-graphql-path-probe/README.md).

La tercera extensión autorizó introspección limitada y `findChannels`: 2/3 POST,
sin retry. Apollo Server bloqueó introspección y `findChannels` resolvió contra
`https://api.sphere.io/changeme/channels?offset=0&limit=500`, que devolvió 404.
El placeholder `changeme` demuestra que la ruta pública no está vinculada al
tenant PriceSmart. El último POST quedó sin usar porque cambiar argumentos no
puede corregir ese binding. Ver
[RAW y cierre de binding](../../reports/pricesmart/2026-08-31-graphql-schema-probe/README.md).

Autorización de 24 horas registrada 2026-08-31T21:53:50Z. El tramo GET se pausó
después de 8 intentos / 7 HTTP 200 / un retry / cuatro assets / 303.054 s. Demostró
Nuxt/Vue Storefront, GraphQL de comercio, esquema de precio/descuento/disponibilidad
y clubes `6602` Florencia, `6603` SPS, `6604` El Sauce. Los HTML de búsqueda
mostraron respuestas de caché incompatibles con sus queries y la ficha GET no
incluyó precio numérico.

El tramo GET había demostrado que el precio requiere un POST GraphQL externo, pero
ninguno de los dos paths probados expuso el contrato comercial observado.
[Reporte GET, RAW y contrato](../../reports/pricesmart/2026-08-31-probe/README.md).
Esos probes no crearon parser comercial, scraper, full, persistencia, modelo ni
Turso. Su bloqueo aplica a la superficie GraphQL externa; la captura CDP posterior
demostró una API pública HN distinta, pero todavía no una fuente por club. Las
secciones siguientes conservan el preflight histórico anterior a la autorización.

## Decisión de candidato

PriceSmart Honduras es el siguiente candidato para price tracking web. La selección
se basa en evidencia pública oficial que ya muestra precio digital en HNL, identidad
de artículo y disponibilidad por club. Este documento es reconocimiento técnico;
**no se ejecutó probe automatizado, browser, scraper, full crawl, persistencia ni
SQL Turso**.

La auditoría partió de `main` `140025336b08efc13cf4f16d3c545a4859f1f6ef`, sin
PRs abiertos. El workflow base del proyecto
[`33358007557`](https://github.com/jchernandez-portfolio/precios-supermercados-sps/actions/runs/33358007557)
estaba verde con 1,993 pruebas. Paiz permanece excluido por la instrucción vigente;
la elección de PriceSmart es el primer candidato oficial con señales suficientes,
no un censo exhaustivo de supermercados hondureños.

## Evidencia pública observada

- La [búsqueda oficial Honduras](https://www.pricesmart.com/es-hn/busqueda?q=Bolsas)
  publica nombres, disponibilidad, precios `L` y paginación.
- Otra [búsqueda oficial de productos](https://www.pricesmart.com/es-hn/busqueda?page=3&q=Vegetables)
  confirma artículos de consumo con precios en HNL y varias páginas.
- La [página oficial del artículo 516411](https://www.pricesmart.com/en-hn/product/mountain-dew-soda-cans-24-units-355-ml-516411/516411)
  muestra `L 407.95` y disponibilidad separada para Florencia, San Pedro Sula y
  El Sauce.
- La [página oficial de clubes](https://www.pricesmart.com/es-hn/puertas-abiertas)
  identifica Club Tegucigalpa, Club San Pedro Sula y Club El Sauce. Es evidencia
  nominal de clubes, no prueba por sí sola el binding técnico de cada respuesta.

Esto demuestra una superficie pública de precio actual por artículo y señales de
club. No demuestra todavía una fuente estructurada reproducible, catálogo completo,
precio regular, promoción, selector de ubicación ni equivalencia Florencia =
Tegucigalpa. Un precio repetido o tachado en HTML no se interpretará como
`reported_regular_price` o `is_promotion` sin contrato técnico.

## Preguntas del primer probe

1. ¿El sitio consume API, JSON, GraphQL o estado embebido público con identidad,
   precio y contexto de club?
2. ¿Cómo se fija y verifica el club para San Pedro Sula, Florencia y El Sauce sin
   cuenta, carrito ni mutaciones?
3. ¿Qué campos representan precio efectivo, precio regular y promoción?
4. ¿Hay total/paginación reproducible y suficiente para estimar completitud?
5. ¿Se pueden comparar las mismas 20–50 identidades entre los clubes relevantes?

Availability se registrará aparte y no justificará por sí sola conservar dos
contextos de Tegucigalpa. No se asumirá que la disponibilidad visible implica
inventario exacto.

## Presupuesto propuesto, pendiente de autorización

```text
dominios = pricesmart.com y www.pricesmart.com
locale = Honduras
método = GET público únicamente
GET total máximo = 30
redirects incluidos = 2 máximo
retries transitorios incluidos = 3 máximo
concurrencia = 1
pausa mínima = 1 segundo
timeout por request = 20 segundos
duración máxima = 15 minutos
muestra objetivo = 20–50 identidades compartidas si la fuente lo permite
contextos candidatos = San Pedro Sula, Florencia, El Sauce
```

El alcance incluye HTML inicial, búsqueda/producto, primera identidad válida,
paginación/total y hasta cuatro assets o configuraciones esenciales enlazados por
el HTML observado. Excluye login, membresía, cuenta, carrito, checkout, escritura,
browser pesado, imágenes, PDF, OCR, otros países, full crawl y recurrencia.

Si una llamada POST/GraphQL de sólo lectura o una mutación de estado anónimo resulta
indispensable, el probe se detiene antes de ejecutarla y se pide autorización con
método, endpoint, payload, finalidad y costo. El ledger cerrará al agotar el límite,
probar inviabilidad o responder las preguntas, lo que ocurra primero.

## Gate posterior

No crear código productivo ni persistencia con este preflight. Sólo si el probe
demuestra fuente, identidad, semántica comercial, club y muestra comparable se
podrá proponer un full separado con presupuesto medido. Turso permanece fuera de
esta fase mientras siga bloqueado.
