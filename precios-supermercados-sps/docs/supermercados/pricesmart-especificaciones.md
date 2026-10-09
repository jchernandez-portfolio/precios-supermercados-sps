# PriceSmart — especificaciones de producto (captura semanal)

Estado: implementado en la rama `rpi/pricesmart-specs`, **todavía sin corrida
real**. El parser se escribió contra un fixture sintético; la primera corrida
guarda HTML crudo de una muestra para endurecerlo.

## Por qué

PriceSmart no publica GTIN/EAN/UPC: ni la API de búsqueda (documentos tipo
Bloomreach: `pid`, `title`, `brand`, `master_sku`, `price_HN_<club>`…) ni la
ficha visible. El **"Número de ítem"** (p. ej. `415586`) es el número interno del
club y **nunca** se trata como GTIN. Sin código de barras, los productos
PriceSmart sólo pueden emparejarse por atributos (marca, tipo, presentación).
La ficha pública trae esos atributos en "Detalles de producto y especificaciones".

## Autorización y cadencia

- Aprobada por el responsable del proyecto el 2026-10-01 (hora de Honduras) con
  cadencia **semanal**. Registro:
  [`.automation/pricesmart-specs-capture-authorization.json`](../../.automation/pricesmart-specs-capture-authorization.json)
  (`precios-sps-pricesmart-specs-authorization/v1`, conjunto cerrado de llaves).
  Para revocar: `live_read_only_authorized: false`; el workflow y el script
  fallan antes de cualquier request.
- Workflow: `.github/workflows/precios-supermercados-sps-pricesmart-specs-weekly.yml`,
  sábado **10:37 Honduras** (`37 16 * * 6`, minuto con jitter; después del corte
  diario de las 05:17 para no leer PriceSmart en paralelo; la integridad semanal domingo 04:23) + `workflow_dispatch` (opción
  `force`). Permisos `actions: read` (descargar el artifact de la corrida diaria)
  y `contents: read`; secrets sólo `TURSO_DATABASE_URL`/`TURSO_AUTH_TOKEN`.

## Flujo

```text
última corrida diaria exitosa (≤ 8 días)
→ artifact daily-acquisition-pricesmart-<run>-attempt-<n> (handoff aceptado)
→ snapshots SPS 6603 + TGU 6602 validados con el contrato diario
→ catálogo único por pid (ambos clubes comparten catálogo)
→ estado Turso: sólo pids verificados hace < 28 días
→ GET https://www.pricesmart.com/es-hn/producto/<slug>/<pid> (ítems nuevos/stale)
→ parser fail-closed por página
→ artifact precios-sps-pricesmart-specs/v1 (+ HTML crudo de muestra/fallos)
→ Turso pricesmart_product_specs: sólo filas nuevas/cambiadas
→ refresco diario de homologación usa marca/presentación faltantes
```

Scripts:

- `scripts/obtener_especificaciones_pricesmart.py` — captura (requiere
  `--live-read-only` y la autorización).
- `scripts/persistir_especificaciones_pricesmart_turso.py state|apply` — estado
  incremental y upsert.
- Parser: `src/precios_supermercados/scrapers/pricesmart_specs.py`.
- Persistencia: `src/precios_supermercados/pricesmart_specs_persistence.py`.

## Política de tráfico

- Una sola conexión, **2 s** entre inicios de request (mínimo autorizado 1.5 s),
  `Accept-Encoding: gzip`, User-Agent
  `PreciosSupermercadosSPS-PriceSmartSpecs/1.0; read-only`, sin cookies.
- Presupuesto: ≤ 1200 requests y ≤ 900 ítems por corrida, deadline 100 min,
  reintentos sólo para 5xx/timeouts (2 por request, 30 por corrida, backoff).
- Aborta todo ante **429**, **403 repetido** (2 seguidos o 3 en total) o página de
  desafío anti-bot. No se evade nada.
- Incremental: un pid verificado hace < 28 días no se vuelve a pedir salvo
  `force`. Prioridad: nunca capturados → alimentos/consumo → más antiguos. Con
  900 ítems por semana, la primera pasada cubre Alimentos (~1.1 k pids) en ~2
  semanas y el catálogo completo de todos los departamentos en algunas más;
  después cada semana sólo baja nuevos/vencidos.
- Reanudable con `--checkpoint` (JSONL por ítem, ligado al hash del catálogo y a
  la versión del parser).

## Qué se extrae

Por página (`specs`, o `null` si falla):

| Campo | Fuente / regla |
| --- | --- |
| `item_number` | "Número de ítem"; debe ser exactamente el pid pedido (si no: `identity_mismatch`) |
| `brand` | etiqueta "Marca" (o JSON-LD `brand`; si difieren → conflicto, `null`) |
| `category_path` | JSON-LD `BreadcrumbList` o el breadcrumb del DOM |
| `net_weight` | "Peso neto" (o "Contenido neto" con unidad de masa); si falta, "Peso Neto: x kg / y lb" de *Información del producto*, prefiriendo la métrica. Canónico en gramos: `1.4500 kg` → `1450` |
| `net_volume` | "Volumen"/"Contenido neto"/"Capacidad" con ml/L/cl/fl oz/gal → mililitros |
| `unit_weight` | "Peso de la unidad (cada uno)" → gramos |
| `pack_count` | "Cantidad de paquetes (recuentos)" (entero; `2.5` → `null`) |
| `imported_or_national` | "Importado o Nacional" → `importado`/`nacional` |
| `origin_country` | "País de origen"/"Origen" o "Hecho en …" |
| `storage`, `allergens`, `trans_fat_free` | "Almacenamiento", "Alérgenos" (lista), "Libre de grasas trans" |
| `gtin` | sólo si algún JSON del mismo pid trae `gtin*/ean/upc` **y** supera el check digit GS1; los candidatos se guardan aparte con `valid_gs1` |
| `raw_specifications` | todos los pares etiqueta/valor crudos, incluso etiquetas desconocidas |
| `presentation_hint` | `"16 x 90.63 g"` si conteo × unitario cuadra con el neto (±2 %), si no `"1450 g"`/`"946 ml"`, o `"16 unidades"`; nunca con un atributo en conflicto |

Decimales fail-closed: `1.4500` y `1,5` se aceptan; `1,450` (¿miles o
decimal?) y `1.450.000` no se adivinan. Contradicciones (JSON vs DOM, neto vs
información, conteo × unitario vs neto) quedan en `conflicts`.

Estados por página: `parsed`, `no_specifications`, `identity_unverified`,
`identity_mismatch`, `parse_failed`, `fetch_failed`, `not_found` (404/410 o
redirección fuera de `/producto/.../<pid>`). La corrida falla si
`(fallos) / (intentados − not_found) > 20 %` o si abortó; entonces no persiste
nada y el artifact queda para diagnóstico.

## Supuestos del HTML a verificar en la primera corrida real

Sin acceso al HTML crudo (~630 KB, renderizado en servidor; no se vio una API de
producto separada), el parser asume:

1. El bloque tiene un elemento cuyo texto exacto es **"Especificaciones"** y las
   filas viven en un ancestro cercano (≤ 8 niveles).
2. Las filas son `dl/dt/dd`, filas de tabla o elementos con **dos hijos**
   (etiqueta, valor); si no, se usa la secuencia de textos hoja
   etiqueta → valor. Los alérgenos llegan como varios elementos hoja.
3. Las etiquetas visibles son las observadas el 2026-10-01 (mapa
   `SPEC_LABELS`); las desconocidas sólo quedan en `raw_specifications`.
4. "Número de ítem <pid>" aparece como texto visible; alternativamente sirven
   JSON-LD `sku`, el pid en `__NEXT_DATA__` o `link rel=canonical`.
5. Si existe `__NEXT_DATA__`/JSON-LD, sus pares sólo se aceptan dentro del
   subárbol del mismo pid (los productos relacionados se ignoran).
6. "Información del producto" es un párrafo con separadores `|` que contiene
   "Peso Neto: … / …" y "Hecho en …".

Endurecimiento: descargar el artifact `pricesmart-specs-<run>-attempt-<n>`,
revisar `raw/*.html.gz` (10 páginas en la primera corrida + hasta 25 fallos por
corrida), versionar 2–3 páginas reales recortadas como fixtures y ajustar
`SPEC_LABELS`/la localización del bloque. Si el HTML real trae un JSON de
producto completo, conviene preferirlo.

## Persistencia Turso

Tabla STRICT `pricesmart_product_specs` (PK `product_id` = pid PriceSmart, igual
a `products.source_catalog_product_id`), con atributos normalizados, crudo
(`spec_json`), `spec_sha256`, `first_captured_at_utc`, `verified_at_utc`,
`changed_at_utc` e índice cubriente `(verified_at_utc, product_id)`.

Coste por corrida: estado = sólo filas frescas por índice (`INDEXED BY`); la
comparación y la verificación leen por PK sólo los pids capturados; escribe
completo sólo lo nuevo/cambiado y sólo `verified_at_utc` en lo re-verificado. El
refresco diario de homologación lee ≈ 2 × productos PriceSmart filas
(products por su índice UNIQUE + PK de specs). La integridad semanal cuenta la
tabla. Las guardas de esquema (`OPTIONAL_DERIVED_TABLES`) la aceptan como tabla
derivada opcional.

## Homologación

`apply_source_spec_attributes` (en el refresco diario) sólo completa lo que
falta en perfiles **PriceSmart**: marca vacía ← marca de especificaciones;
presentación con estado `missing` ← `presentation_hint` (queda `source_only`).
Si el nombre ya declara presentación, nada cambia. No toca el barcode: sin GTIN
no hay `comparison_status` comparable; los atributos alimentan las guardas y el
motor de matching (candidatos `review_required`).

## Riesgos

- El DOM real puede diferir del sintético → la primera corrida puede fallar por
  umbral (fail-closed, sin persistir) hasta endurecer el parser con el HTML crudo.
- Cuota Turso: la homologación diaria suma ≈ 2.3 k filas leídas/día.
- Si PriceSmart cambia slugs, el sitio debería redirigir a `/producto/<nuevo>/<pid>`
  (aceptado); otra redirección cuenta como `not_found`.
- La tabla `pricesmart_product_specs` y otras tablas nuevas en Turso deben
  registrarse en `OPTIONAL_DERIVED_TABLES`, o los persistores diarios fallan con
  `turso_schema_mismatch`.
