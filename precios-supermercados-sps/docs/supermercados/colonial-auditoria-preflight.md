# Colonial — fuente, catálogo completo y frontera Turso

Fecha: 2026-08-30. Estado: `CATALOG_COMPLETE`, `BLOCKED_EXTERNAL_TURSO_QUOTA`.
**No es MVP cerrado:** faltan primera carga Turso, segunda observación real y,
únicamente después de demostrar ambas, workflow mínimo. Sin recurrencia autorizada.

La propuesta inicial del PR #347 no se ejecutó sin permiso. El usuario autorizó
24 horas en la tarea; se registró conservadoramente de 2026-08-30 20:10:18 UTC a
2026-08-31 20:10:18 UTC. Esa autorización permitió probes, full y persistencia
validada/segunda observación; no cambia facturación ni habilita ejecución diaria.

## Base auditada

- `jchernandez-portfolio/precios-supermercados-sps/main`:
  `f34a324b2cb177baa77ce788c360476268af0f01`.
- Leídos `precios-supermercados-sps/AGENTS.md`, `.github/workflows/AGENTS.md`,
  `docs/PROJECT_STATE.md`, extractor vigente SPS/TGU, schema y updaters SQLite/Turso.
- Cero PRs abiertos al auditar. PRs #340, #341, #343–#346 fusionados; #342 cerrado
  sin merge. Búsqueda de PRs con `Colonial`: cero resultados.
- Sin código, fixtures ni documentación Colonial en el árbol auditado.
- [CI de main](https://github.com/jchernandez-portfolio/precios-supermercados-sps/actions/runs/33314520368)
  verde, incluida suite completa. Verificados triggers de PR y push a main.
- Workflow permanente de actualización: `precios-supermercados-sps-la-colonia-mvp-update.yml`,
  con dispatch y schedule. El listado Actions conserva workflows históricos
  marcados `active` cuyas rutas ya no existen en main; ese estado no acredita
  permiso live ni operación vigente. No se habilitó ni despachó ninguno.
- Metodología leída directamente del repositorio privado
  `reusable-engineering-skills`, main
  `47d465c0f9f572bde957204c4caf9c648af983fa`: scraping-fast-path, web-source-recon,
  api-discovery, web-data-extraction, browser-automation, extraction-completeness,
  auditoría/autonomía, seguridad de efectos, provenance, entrega/CI/testing y
  calidad, normalización, histórico, replay y pipeline. No se copiaron skills.

La instrucción actual inicia Colonial sin ampliar La Colonia. Se mantienen las
fronteras de autorización y de persistencia del proyecto. El bloqueo de Turso y
los snapshots recuperables del schedule se detallan en [PROJECT_STATE](../PROJECT_STATE.md).

## Fuente y scope demostrados

Tras confirmar en RAW `Shopify.shop = "bm1gbx-tm.myshopify.com"`, se probó un
producto en `/products.json?limit=1&page=1`, luego 40 productos y dos páginas de
250 sin overlap. Muestra: cinco categorías, diez promociones, vendor literal
`RMS`. El listado general anuncia 9,199 productos. Las pruebas pequeñas preceden
al full; sus respuestas útiles se reutilizaron.

| Responsabilidad | Evidencia utilizada |
| --- | --- |
| Identidad, descripción, precios por variante | `/products.json?limit=250&page=N` |
| Stock ecommerce | Botón y variante de cada tarjeta de `/collections/all` |
| Páginas HTML ligeras | `/collections/all?section_id=template--25869947109668__banner&page=N` |
| Total | `9199 productos` en cada página HTML |
| Membership independiente | Diez sitemaps de productos listados por `/sitemap.xml` |
| Contexto | Portada: Shopify, HNL, Delivery/Pick Up San Pedro Sula |

`colonial_sps` representa el catálogo ecommerce público de San Pedro Sula.
No se observó selección de sucursal ni binding de precio/stock por tienda en el
HTML/estado y requests usados. No se enviaron parámetros de sucursal ni cookies
privadas. No se afirma inexistencia absoluta de estados ocultos no inspeccionados;
no hay evidencia que justifique cuatro ubicaciones físicas. Navegador y storage
no fueron necesarios para reproducir los datos públicos observados.

No se descargaron imágenes, CSS, fuentes, analytics ni JavaScript separado.
Concurrencia 1, conexión reutilizada, pausa mínima 1 segundo; sin evasión de
controles, login, carrito ni checkout. `/search?...&view=json` sólo devuelve diez
coincidencias y no stock; el `limit=250` HTML se ignora y entrega 24 tarjetas.
No hay un batch de stock más eficiente demostrado en el reconocimiento acotado.

## Semántica comercial y límites

- `product_id` = ID Shopify; `item_id` y `source_key` = ID de variante;
  `source_key_type=item_id`. No deduplicar por SKU: dos variantes pueden compartirlo.
- `reference` = SKU fuente; `ean` sólo si existe barcode explícito. No reinterpretar
  un SKU numérico como GTIN. `brand` conserva vendor literal; no inferir marca del
  nombre. `presentation=NULL`: las opciones numéricas observadas no prueban formato.
- `current_price` = `variant.price`; `reported_regular_price` = `compare_at_price`
  nullable; promoción si regular > efectivo. Precios HNL exactos en centavos.
  `previous_price` sigue perteneciendo al periodo histórico anterior.
- Shopify JSON/JS puede decir `available=true` mientras el botón propio del
  comercio muestra **Agotado**, con `cp-sold-out` y `disabled`. Ese botón determina
  `out_of_stock`; el botón habilitado determina `in_stock`; evidencia ambigua,
  `unknown`. No se infiere cantidad ni inventario de una sucursal física.
- Hay seis productos con dos variantes. El stock del botón se asigna sólo a su
  variante; las otras seis quedan `unknown`. CANADA DRY Fruit Splash 12 Oz anuncia
  L 22.49 / L 24.99 tachado pero el botón apunta a una variante de L 24.99 sin
  regular. La otra variante tiene L 22.49 / L 24.99. Se comprueba que el par
  comercial de la tarjeta exista en alguna variante **del mismo producto**, sin
  copiarlo sobre la variante del botón ni propagar stock.

## Preflight y primer full

Plan previo: 37 páginas JSON de 250 (última 199), 384 páginas HTML de 24 (última 7),
10 sitemaps de producto, índice y portada: **433 recursos**. Siete ya capturados;
426 GET adicionales previstos. Items esperados desconocidos antes de recorrer
las variantes; no asumir un item por producto. Presupuesto: 450 GET nuevos,
1,200 segundos, concurrencia 1, hasta un retry por recurso y cinco totales sólo
para fallos transitorios; stop 401/403/429 y cero redirects automáticos. Duración
estimada 9–12 minutos. RAW permite recuperar sólo recursos faltantes del mismo run.

Resultado: **9,199 productos / 9,205 variantes con precio**, sin identidad duplicada
ni residual. Coinciden exactamente handles de JSON, tarjetas y sitemaps; todas las
páginas HTML mantienen total 9,199 y tamaños esperados. Stock: 7,726 disponibles,
1,473 agotados, 6 unknown. Esta comprobación detecta deriva estructural/membership;
no convierte una captura secuencial en un snapshot atómico del servidor.

426 GET nuevos exitosos, cero fallos/retries; 7 reutilizados. 433 recursos por
catálogo, 0.047070 recursos/producto; 21.594 productos por GET nuevo. Intervalo
entre inicio del primer/último GET nuevo: 541.385 s. Con los 13 GET de investigación,
el trabajo completo usó **439 GET nuevos**. Cuerpos nuevos: 51,038,453 bytes.

Tras la descarga, el primer parser rechazó el caso multivariante descrito arriba.
Se corrigió contra RAW y se aceptó offline, **sin repetir el crawl**. El snapshot
conserva fechas fuente 20:11:03–20:31:00 UTC; no se fecha con la hora del parseo.
Sus métricas de cero requests pertenecen a esa validación offline, no al crawl.

La [evidencia versionada](../../reports/colonial/2026-08-30/README.md) incluye RAW
completo comprimido, manifest URL/fecha/status/SHA, ejecutor inicial, snapshot,
preflight/métricas y resultado SQL offline. El test de captura completa reproduce
los 9,205 registros y falla si intenta HTTP. SHA-256 del snapshot original:
`2f7861ff6decd0f7e95a82c321d71e1cd7fe2e6440b6794bbe94c6457b41e2fd`.

## Persistencia compartida y pruebas

Dos archivos productivos nuevos: parser específico y downloader específico.
Sin framework, nueva dependencia, sexta tabla, base Colonial ni workflow nuevo.
El validador/updater existente admite Colonial mediante selección explícita;
La Colonia sigue siendo el valor predeterminado. Registro inicial de supermercado
+ ubicación dentro de la misma transacción y con guardas de identidad/contexto.
Se conserva el índice temporal de PR #348 para evitar el scan cuadrático.

Pruebas offline: producto nuevo, sin cambios, cambios de cada uno de los cuatro
campos comerciales, replay, inválido, incompleto, duplicado, desaparición sin OOS,
aislamiento Colonial→La Colonia y La Colonia→Colonial aun con IDs idénticos,
orden temporal y rollback de contexto incorrecto. Parser probado con RAW exacto
y casos sintéticos explícitos; controles de presupuesto/expiry/403/429/cache.

También se aplicaron los **9,205 items reales** al SQL remoto ejecutado sobre
SQLite local de las cinco tablas, junto a SPS/TGU del artifact 9734740995. Resultado:
18,714 productos, 28,171 periodos, tres runs, dos cadenas y tres ubicaciones;
integrity_check ok, foreign keys sin fallos, cero periodos abiertos duplicados,
replay sin cambios y La Colonia intacta. Es evidencia offline, no carga Turso.

## Frontera operativa pendiente

Turso rechaza lecturas por cuota de cuenta: 713.7 M / 500 M (143%), plan Starter,
overages deshabilitados. `turso plan show` lo reconfirmó después de la autorización;
no faltan credenciales. No se intentó el batch Colonial con el preflight bloqueado.
El reinicio anunciado es **31/8/2026 18:00 CST** (1/9 00:00 UTC), posterior al fin
de la autorización de 24 horas (31/8 14:10:18 CST).

No eludir ese bloqueo, habilitar cobros ni modificar el plan. Restauradas las
lecturas: validar los bytes originales, cargar primera observación con run-id
estable, verificar cinco tablas/aislamiento/replay; luego nueva captura real
**sin reutilizar datos comerciales de la primera**, segunda persistencia y
verificación. Sólo entonces construir el workflow mínimo. Si se espera al
reinicio, renovar autorización para solicitudes Colonial posteriores al vencimiento.

Desde la raíz, con `TURSO_DATABASE_URL` y `TURSO_AUTH_TOKEN` ya disponibles sin
imprimirlos, el comando de primera carga preparado es:

```bash
gunzip -c precios-supermercados-sps/reports/colonial/2026-08-30/full-catalog.json.gz > /tmp/colonial-first.json
python precios-supermercados-sps/scripts/actualizar_mvp_turso_la_colonia.py \
  /tmp/colonial-first.json --supermarket colonial --run-id colonial-20260830T203100Z
```

No ejecutar hasta comprobar restablecimiento de lecturas. Una nueva captura
requiere `obtener_catalogo_colonial.py --output CARPETA_NUEVA --authorized-until
CADUCIDAD_UTC_REAL`; ese argumento registra una autorización humana existente,
no la genera. El modo `--offline` no requiere tráfico ni reautoriza producción.

Los aprendizajes de contraste API/UI e identidad por variante ya están cubiertos
por api-discovery, web-data-extraction y normalization-governance. Se documenta
la aplicación concreta aquí sin duplicar una skill ni introducir reglas Colonial
en el repositorio reusable.
