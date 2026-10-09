# Producto maestro v1 (MDM / golden record)

Estado: **implementado, vínculos GTIN activos; motor sólo en revisión**.
Código: `src/precios_supermercados/product_master.py` (builder, esquema,
sincronización), `src/precios_supermercados/matching/master_candidates.py`
(candidatos contra maestros) · Scripts: `backfill_homologacion_turso.py`
(refresco diario), `exportar_cola_maestro.py`, `importar_decisiones_maestro.py`
· Política: sección `product_master` de
`config/homologation/identity-policy-v1.yaml` · Pruebas:
`tests/test_product_master_*.py`, `tests/test_master_*.py`.

## 1. Problema

Hasta v2.6 el único "producto común" era una columna:
`product_homologation_profiles.canonical_product_id = prod_gtin_<14>`. Eso
mezcla tres cosas distintas:

- la **identidad** (¿qué productos fuente son el mismo artículo?),
- los **atributos** del artículo (marca, contenido, tipo… elegidos de alguna
  fuente), y
- la **decisión** que los une (GTIN, revisión humana, motor), con su autor,
  evidencia e historial.

Sin GTIN no hay dónde guardar una equivalencia revisada, ni dónde recordar un
"Distinto" humano, ni cómo cambiar una pertenencia sin perder historial.

## 2. Modelo (Turso/libSQL, tablas `STRICT`)

```text
products (fuente, intacta)
  └─ product_homologation_profiles (perfil derivado, 1:1)
  └─ master_product_links ──► master_products (golden record)
                                ▲
     master_link_rejections ────┘  ("Distinto": nunca se vuelve a proponer)
     master_sync_state (1 fila)   master_sync_dirty (maestros a recalcular)
```

### `master_products`

| Columna | Contenido |
| --- | --- |
| `master_product_id` | `mp_` + 20 hex. Maestro nacido de un GTIN: `sha256("gtin:<14>")` (determinista, reruns idempotentes, no expone el GTIN). Sin GTIN: semilla de la decisión que lo crea (`verified:<prod_verified_*>`). Nunca se reutiliza ni se borra. |
| `primary_gtin` | GTIN-14 del maestro GTIN (único entre maestros) o nulo. |
| `origin_method` | `gtin` · `reviewed_decision` · `manual_review` · `engine`. |
| golden | `brand`, `manufacturer`, `display_name`, `product_type`, `category`, `net_content_value` + `net_content_unit` (base canónica `g`/`ml`/`unit`; `oz` sólo si no hay métrica), `pack_count`, `variant`, `origin`. |
| `attribute_provenance_json` | por atributo: regla aplicada, soporte, cadenas y `product_id` que aportaron el valor. |
| `human_attributes_json` | atributos fijados por una persona (`value`, `decided_by`, `decided_at_utc`); el builder nunca los sobrescribe. |
| `status` / `merged_into` | `active` · `merged` (con destino) · `retired` (sin miembros; se reactiva si vuelve a tenerlos). |
| `record_hash`, `version`, `created_at_utc`, `updated_at_utc` | huella de atributos+procedencia; `version` sube sólo cuando cambia. |

### `master_product_links`

Una fila por decisión, con historial (`status`: `active` · `rejected` ·
`superseded`, `status_changed_at_utc`). Columnas: `product_id` +
`supermarket_id` (FK a `products`), `master_product_id`, `link_method`,
`confidence`, `evidence_json`, `decided_by` (`system` · `engine:<versión>` ·
`human:<id>`), `decided_at_utc`, `link_hash`.

| `link_method` | Quién | Activo en v1 |
| --- | --- | --- |
| `gtin_exact` | `system` — barcode explícito de la fuente | sí |
| `gtin_sku_derived` | `system` — GTIN derivado del SKU (Colonial) o del código (Comisariato) | sí |
| `reviewed_decision` | `human:<revisor>` — decisión aprobada y vigente de `reviewed-decisions-v1.json` | sí (registro hoy vacío) |
| `manual_review` | `human:<revisor>` — "Mismo" importado desde CSV | sí |
| `engine_auto` | `engine:<versión>` | sí, sólo la regla A por atributos (activo 2026-10-09) |

Restricciones por índice único parcial: **un vínculo activo por producto** y
**un vínculo activo por cadena dentro de un maestro** (como un producto fuente
se ofrece en todas las tiendas de su cadena, esto implica ≤ 1 oferta por
contexto). `CHECK`: un GTIN sólo lo decide `system`, un manual/registro sólo
`human:*`, `engine_auto` sólo `engine:*`.

### `master_link_rejections`

PK `(product_id, master_product_id)`, `decided_by = human:*`, nota y evidencia.
La cola de revisión nunca vuelve a proponer un par rechazado.

## 3. Builder y supervivencia (`build_golden_record`)

Python puro y determinista (el orden de entrada no cambia el resultado).

- **Miembros de un maestro GTIN:** perfiles `ready` o `single_source` con ese
  GTIN canónico: exactamente la identidad vigente. Los `review_required`
  (miembro excluido, colisión, conflicto) quedan sin vínculo. Dos
  `single_source` del mismo supermercado con el mismo GTIN tampoco se
  vinculan (colisión). Un vínculo curado del producto tiene prioridad sobre su
  propio GTIN.
- **Marca:** reportada por la fuente > inferida del nombre; luego la más
  frecuente; empate por el miembro preferido.
- **Nombre:** el compartido por más cadenas (Walmart/Paiz publican el mismo),
  luego barcode explícito antes que GTIN derivado de SKU (nombres abreviados),
  luego el más completo.
- **Tipo / categoría:** regla por nombre > palabra clave de la ruta fuente;
  luego el más frecuente; la categoría sigue al tipo ganador.
- **Contenido neto:** métrico > onza; valor no convertido desde lb/oz (≤ 2
  decimales) > convertido (`453.59237`); luego el más frecuente; presentación
  confirmada > inferida. `pack_count` acompaña al valor elegido.
- **Variante:** sabor/aroma y formulación no estándar (`zero`, `sin azúcar`,
  `light`, entera/descremada) con el vocabulario del motor v2; gana el
  conjunto declarado más frecuente. "Original/Clásico" no es variante.
- **Fabricante / origen:** sin fuente hoy: nulos salvo atributo humano.
- **Humano:** un atributo en `human_attributes_json` gana siempre y queda con
  procedencia `human_set`.

## 4. Refresco diario (incremental, lecturas acotadas)

`backfill_homologacion_turso.py --apply` (mismo paso del workflow
`precios-supermercados-sps-homologation-refresh.yml`, sin cambios de workflow)
reutiliza los perfiles que ya calculó en memoria: **no relee `products` ni los
perfiles**. Luego `sync_product_master`:

1. `sqlite_master` (migración idempotente si faltan tablas/índices).
2. Lee 1 fila de `master_sync_state`, los vínculos curados activos
   (`idx_master_links_method`), los maestros con atributos humanos (índice
   parcial) y `master_sync_dirty`.
3. Construye el estado deseado completo en memoria y su huella.
4. Modo:
   - `noop` si la huella coincide con la guardada (lo normal sin cambios);
   - `incremental` si los perfiles estaban sincronizados con el maestro
     (`profile_digest` guardado = huella de los perfiles anteriores): lee sólo
     los vínculos de los productos cambiados (por `product_id`), los maestros
     afectados (por PK) y sus miembros (por `master_product_id`);
   - `full` en la primera corrida, ante un cambio de versión del builder, de
     la normalización o de la política, si los perfiles quedaron
     desincronizados (p. ej. una corrida anterior falló a mitad) o si cambió
     más del 25 % de los productos.
5. Escribe sólo el diff en lotes `json_each` (≤ 1000 filas) en orden seguro:
   maestros → vínculos reemplazados (`superseded`) → vínculos nuevos →
   maestros sin miembros (`retired`); al final el estado y limpia `dirty`.

Un error del maestro se reporta en `product_master.status = "error"` y **no
bloquea** el refresco de perfiles ni la publicación (capa auxiliar). Los
exportadores no dependen de que el maestro esté al día salvo para vínculos
curados, que escribe la herramienta de revisión.

| Corrida | Filas leídas (orden de magnitud) | Escrituras |
| --- | --- | --- |
| Sin cambios | ~15 (`sqlite_master` + estado + curados + humanos) | 0 |
| Con *C* productos cambiados | ~15 + 3–5 × *C* | sólo el diff |
| Primera / cambio de versión | maestros + vínculos activos (≈ 20 k + 32 k con los datos de septiembre; 0 la primera vez porque las tablas están vacías) | primera vez: todos los maestros y vínculos |

## 5. Exportadores (paridad)

`exportar_consumer_catalog_core.py` (SPS y, vía la fachada, TGU) lee además
`sqlite_master` y los vínculos curados servibles
(`serving_link_methods`, hoy `manual_review` y `reviewed_decision`) por
`idx_master_links_method`. Con sólo vínculos GTIN no hay nada que superponer:
la comparabilidad sale de los perfiles, que los vínculos GTIN materializan 1:1
(prueba `test_gtin_links_materialize_exactly_the_persisted_identity`), y la
salida es **byte a byte idéntica** (prueba de paridad SPS + TGU sobre SQLite
realista). Con un vínculo curado, la oferta toma la llave del maestro
(`prod_gtin_*` si el maestro tiene GTIN; `mp_*` si no) y los `single_source`
de ese maestro se promueven al mismo grupo; el grupo es comparable si tiene ≥ 2
cadenas sin colisión de cadena/contexto (las reglas vigentes). Con
`serving_link_methods: []` los vínculos curados se guardan pero no cambian el
catálogo.

## 6. Matching contra maestros (shadow) y revisión

`exportar_cola_maestro.py` puntúa cada producto **no homologado** (sin
vínculo, o vinculado sólo a su propio maestro de un miembro) contra los
maestros con presencia en la misma ciudad:

- estandarización del motor (`standardize.py`) y niveles de
  `comparison.py`; marca, tamaño (exacto ≤ 0.5 %, `close` ≤ 2 %), pack,
  variante y tipo contra el golden; nombre contra todos los miembros;
- score = 0.25 marca + 0.30 nombre + 0.20 tamaño + 0.05 pack + 0.10 variante +
  0.10 tipo; bandas `high` ≥ 0.85, `medium` ≥ 0.70, `low` ≥ 0.55; piso de
  nombre 0.45 y 0.60 si la marca no coincide (marca + tipo + tamaño no basta);
  `high` exige marca exacta/difusa, nombre ≥ 0.60 y ni tamaño ni variante
  dudosos;
- conflicto duro (marca, tamaño, pack, variante, códigos, tipo) → descartado;
- **otro GTIN válido** distinto del GTIN del maestro → bandera
  `different_valid_gtin`, sólo revisión aunque todos los atributos coincidan
  (`exact_attribute_match`); `decision_policy = review_only` en todas las filas;
- pares rechazados y maestros que ya tienen esa cadena no ocupan cupo; top-2
  por producto; una pareja entre dos maestros de un miembro aparece una vez.

Salida: `review-queue.csv`/`.jsonl` (`review_id = mr_<hash>`, maestro, producto,
score, niveles, banderas y columnas vacías `decision`, `reviewer`, `note`) y
`summary.json`. Fuentes: `--records` (offline, reconstruye perfiles y maestros
con las reglas diarias y mide completitud), `--sqlite`, `--turso` (puntual:
lee `products`, maestros, vínculos y rechazos ≈ P + M + L filas; con
`--with-cities` además las ofertas current por índice parcial).

### Flujo de revisión

1. Generar la cola (`exportar_cola_maestro.py --turso --output-dir …`).
2. El responsable revisa (PDF/páginas/empaques) y devuelve un CSV:
   `review_id,decision,reviewer,note` (o `product_key,master_product_id,…`),
   con `decision` = `Mismo` / `Distinto`.
3. `importar_decisiones_maestro.py --turso --csv revisado.csv --queue
   review-queue.jsonl` (dry-run) y luego `--apply`.
   - `Mismo` → vínculo `manual_review` (`human:<revisor>`); reemplaza un
     manual previo o el GTIN de un maestro de un solo miembro (ese maestro se
     retira en el siguiente refresco); nunca un GTIN de grupo multi-cadena ni
     un vínculo de registro; respeta 1 vínculo por cadena y maestro.
   - `Distinto` → `master_link_rejections`; un manual activo del par pasa a
     `rejected`; un GTIN no se rechaza desde aquí
     (`gtin_link_requires_identity_review`).
   - Cualquier error rechaza todo el lote salvo `--skip-invalid`; escritura en
     un único lote atómico; marca los maestros en `master_sync_dirty`.
4. El siguiente refresco diario recalcula el golden de esos maestros y los
   exportadores ya muestran la fila comparable.

## 7. Medición offline (registros reconstruidos 2026-09-21)

`exportar_cola_maestro.py --records records-2026-09-21.jsonl` (99,284 registros
→ 52,974 productos fuente deduplicados como en Turso; GTIN = barcode o GTIN
del snapshot; salidas privadas, no versionadas). Contraste con el catálogo
publicado 2026-10-01 (`portfolio-data`).

| Medida | SPS | TGU |
| --- | ---: | ---: |
| Maestros con presencia en la ciudad (total 18,900; 31,754 vínculos: 25,822 `gtin_exact`, 5,932 `gtin_sku_derived`) | 15,706 | 14,240 |
| — multi-cadena en la ciudad (= grupos GTIN comparables medidos con v2.5/v2.6) | 3,033 | 8,503 |
| Publicado 2026-10-01: filas comparables + single_source (cota del número de maestros) | 4,063 + 12,504 | 8,876 + 17,014 |
| Golden multi-cadena: marca / tamaño / tipo / variante declarada | 99.7 / 97.7 / 61.7 / 22.2 % | 97.6 / 87.6 / 49.1 / 19.3 % |
| Filas miembro de esos maestros: marca / tamaño / tipo / variante | 97.8 / 93.6 / 51.9 / 21.8 % | 97.3 / 87.9 / 48.9 / 19.6 % |
| Filas miembro sin el atributo que el golden completa: marca / tamaño / tipo | 142 de 158 / 298 de 449 / 718 de 3,394 | 126 de 532 / 194 de 2,344 / 386 de 9,900 |
| Todas las filas fuente: marca / tamaño / tipo | 88.4 / 68.8 / 32.5 % | 97.2 / 70.3 / 39.7 % |
| Productos no homologados (sin vínculo o maestro de un miembro) | 27,006 | 15,457 |
| — con candidato a maestro (top-1) | 5,109 (18.9 %) | 1,582 (10.2 %) |
| — banda alta / media / baja | 1,194 / 2,480 / 1,435 | 259 / 783 / 540 |
| — con otro GTIN válido (sólo revisión) | 36 | 72 |
| Filas de la cola con atributos exactos (registros por tienda) | 309 | 122 |

Por cadena (SPS, no homologados → con candidato; alta/media/baja):
Comisariato 6,714 → 2,444 (706/1,121/617), La Colonia 5,059 → 1,475
(347/781/347), Colonial 5,783 → 608 (51/335/222), PriceSmart 1,830 → 309
(18/152/139), Walmart 7,620 → 273 (72/91/110). TGU: La Colonia 5,145 → 907
(149/516/242), Walmart 7,463 → 270 (100/56/114), PriceSmart 1,856 → 287
(8/143/136), Paiz 993 → 118 (2/68/48).

Revisión manual de 40 filas aleatorias de banda alta: 34 el mismo producto, 4
dudosas (variante no declarada: "Reducida en grasa", "Reserva") y 2 distintas
(Knorr Gallina vs Costilla, Plenitud G/XG vs M). La banda media/baja mezcla
aciertos con variantes y tamaños distintos: es cola de revisión, no identidad.

Sesgos conocidos: en los registros reconstruidos La Colonia sólo trae GTIN en
las filas que ya eran comparables y Comisariato no trae su código (v2.6
reconstruye el GTIN en el scraper), así que en producción una parte de esos
candidatos ya quedará vinculada por GTIN y los conteos de no homologados de
esas cadenas están sobrestimados.

## 8. Límites y pendientes

- `engine_auto` activo desde 2026-10-09 sólo para la regla A por atributos (regla 23 del estándar de identidad): golden set de 387 pares etiquetados (98.8 %) y revisión del responsable 50/50. Cualquier otra regla automática exige su propio golden set y aprobación.
- No hay herramienta de *merge* de maestros (el esquema la soporta:
  `status='merged'`, `merged_into`).
- `exportar_rpi_marts.py`/`exportar_modelo_analitico.py` siguen usando sólo
  la identidad GTIN de los perfiles; los vínculos curados llegan hoy sólo a
  los catálogos B2C.
- Las decisiones del registro son por pareja: un `PRODUCT_VARIANT`/`CONFLICT`
  no se traduce todavía a rechazos producto→maestro.
