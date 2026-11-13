# Propuestas de diseño: revisión de tamaños (FASE 7)

Documento de diseño, **sin código**. Cubre cuatro mejoras pedidas tras usar el panel
`#/admin/revision`. Estado del código leído: `main` en `2bed7a2` (13-nov-2026).
Nada de esto está implementado; cada propuesta es un PR aparte y necesita aprobación.

Reglas que todas respetan: homologación conservadora (ante la duda, aislar), la decisión
humana manda sobre la IA, toda tabla derivada nueva entra en `OPTIONAL_DERIVED_TABLES`
(`actualizar_mvp_turso_la_colonia.py` y `verificar_integridad_turso.py`), el frontend no
decide identidad y Turso se lee por alcance y se escribe por deltas.

## Resumen

| # | Propuesta | Esfuerzo | Orden | Depende de |
| --- | --- | --- | --- | --- |
| 4 | Etiqueta "4 Pack (368 g)" con `pack_count` | **S** | 1.º | nada |
| 3 | Ofertas "zombi": marcar, ocultar y reaparecer | **M** | 2.º | nada |
| 2 | Una foto por supermercado | **M** | 3.º | nada |
| 1 | "Separar por tamaño" (un GTIN, dos grupos) | **L** | 4.º | 2 (fotos) y llaves viejas en SPS |

Por qué ese orden: 4 es pequeña, visible y no toca identidad. 3 limpia el catálogo y la
cola antes de invertir en lo demás. 2 mejora la evidencia con la que se decide (hoy varias
ofertas comparten una sola foto) y deja lista la foto de cada oferta, que 1 necesita para
que las tarjetas nuevas no nazcan sin foto. 1 es la única que cambia identidad, maestros y
llaves públicas; conviene hacerla al final y con medición previa.

---

## 1. "Separar por tamaño": un GTIN reutilizado para dos presentaciones

### Problema

Un fabricante reutiliza el mismo código de barras para dos presentaciones reales. Casos
vistos en la cola: Mazola 1 kg y 908 g, Naturas 106 / 100 / 90 g, Bimbo hamburguesa 350 g
y Súper Hamburguesa 320 g, Lactolac 230 y 240 g.

Hoy sólo hay dos salidas, y ninguna sirve:

- **Tamaño oficial** (`decision = size`): `_resolve_with_decision` forma el grupo con las
  ofertas que coinciden y aísla el resto con `human_size_decision_mismatch`. Las ofertas
  de 908 g quedan cada una en su tarjeta suelta aunque dos cadenas vendan lo mismo.
- **No es el mismo producto** (`different_products`): nadie se agrupa.

La causa es estructural: todo cuelga del GTIN.

| Pieza | Llave actual |
| --- | --- |
| `size_decisions` | `canonical_gtin` (PK): una sola decisión y un solo tamaño |
| Identidad (`_exact_groups`) | un `ExactGtinGroup` por GTIN, id `prod_gtin_<14>` |
| Maestro (`build_desired_state`) | un maestro por GTIN (`gtin_master_id`); `primary_gtin` con índice **único** |
| Tarjeta pública | `row_id = product-sha256(canonical_product_id)[:24]` o `source-sha256("súper:id")[:24]` |
| Carrito (`localStorage`) | `line.id = rowKey(row_id)` más `store` opcional |
| Foto (R2) | `fotos/v2/<px>/<id[:2]>/<id>-<huella>.webp`, `id` = llave de la tarjeta |
| Cola | un elemento por GTIN |

### Alternativas consideradas

| Alternativa | Por qué no |
| --- | --- |
| GTIN sintético (sufijo o dígito extra) por tamaño | Falsifica un identificador real; rompe `CHECK length = 14`, búsquedas por código y la regla "GTIN distintos no se fusionan". |
| Cambiar la PK de `size_decisions` a (GTIN, grupo) | Reconstruir una tabla STRICT en Turso con decisiones humanas ya guardadas; si falla, se pierde lo que más cuesta recuperar. |
| Añadir `split` al `CHECK` de `decision` | SQLite no altera un `CHECK`: misma reconstrucción de tabla. |
| Vínculos curados manuales por `product_id` (`manual_review`) | Ya existe y el exportador los respeta, pero son estáticos: una oferta nueva de 908 g no entraría sola. La regla debe vivir como regla, no como lista de productos. |
| Dejarlo como está y elegir el tamaño mayoritario | Es lo vigente. Pierde comparación real y es justo lo que se quiere arreglar. |

### Diseño elegido: grupo principal intacto + grupos adicionales

**Idea**: la fila de `size_decisions` no cambia y sigue definiendo el **grupo principal**
(conserva `prod_gtin_<gtin>`, su maestro, su tarjeta, sus fotos y sus carritos). Los
tamaños adicionales viven en una tabla hija y forman **grupos secundarios** con id propio.
Si la tabla nueva no existe o está vacía, el resultado es idéntico al de hoy.

**Tabla nueva `size_decision_groups`** (STRICT, derivada, en `OPTIONAL_DERIVED_TABLES`):

| Campo | Notas |
| --- | --- |
| `canonical_gtin` | FK lógica a `size_decisions` (que debe ser `decision = 'size'`) |
| `group_no` | 2, 3, 4 (el 1 es el principal); PK (`canonical_gtin`, `group_no`) |
| `net_content_value`, `net_content_unit` | canónico (g, ml, oz, unit), igual que la tabla madre |
| `declared_value`, `declared_unit` | lo que escribió la persona |
| `decided_by`, `decided_at_utc`, `note` | mismos formatos (`human:<cuenta>`, `engine:claude_sesion`) |

**Contrato del panel**: `rpi-size-decisions/v2` añade `decision: "split"` con
`sizes: [{value, unit}, …]` (2 a 4; el primero es el principal). El validador sigue
aceptando v1 y sigue fallando cerrado. Validaciones nuevas: los tamaños deben ser
distinguibles con la tolerancia vigente (2 % o 1.5 unidades; Lactolac 230/240 g pasa con
4.2 %), todos en dimensiones comparables y cada uno publicado por al menos una oferta.

**Motor** (`_resolve_with_decision`): cada miembro se compara con la lista de oficiales
con el mismo `match_official`. Coincide con uno → va a ese grupo. No coincide con ninguno
→ `human_size_decision_mismatch` (como hoy). **Sin presentación** → hoy se queda en el
grupo; con separación no se puede saber a cuál pertenece, así que se aísla
(`human_size_split_unassigned`). Los demás bloqueos (colisión por cadena, tipo, sabor,
multipack ambiguo) no cambian. Un grupo con menos de dos cadenas no compara.

**Identidad**: el grupo principal conserva `prod_gtin_<gtin>`. Cada grupo secundario usa
un id determinista con forma de maestro, que el contrato público ya admite
(`public_canonical_id` devuelve `mp_*` cuando no hay `primary_gtin`):

```text
mp_ + sha256("gtin-size:<gtin>:<valor canónico><unidad>")[:20]
```

Depende del tamaño y no del número de grupo: reordenar los tamaños no cambia llaves.
`canonical_gtin` del perfil sigue siendo el GTIN real (búsqueda y código de barras).

**Maestro**: `build_desired_state` pasa a agrupar por el id canónico del perfil y no sólo
por `canonical_gtin`. El maestro principal no cambia. El secundario nace con
`primary_gtin = NULL` (el índice único lo exige), `origin_method = 'reviewed_decision'`,
contenido neto humano del tamaño de su grupo y el GTIN real en la evidencia de sus
vínculos. `aplicar_decisiones_tamano.py` escribe ambas tablas en el mismo lote atómico y
marca sucios todos los maestros afectados.

**Exportadores**: `exportar_consumer_catalog_core` ya agrupa por `canonical_product_id`
con estado `ready`; no necesita lógica nueva. La cola deja de listar el GTIN (decidido) y
la pestaña de resueltos muestra los grupos.

**Panel**: botón "Separar por tamaño". Cada tamaño publicado se asigna a un grupo con los
botones rápidos que ya existen; vista previa de qué ofertas caen en cada grupo y cuáles
quedan aisladas antes de guardar.

**IA**: nunca separa sola. `*_auto` sólo resuelve por mayoría de un tamaño; puede
*sugerir* separar cuando las lecturas se agrupan en dos tamaños y cada uno coincide con
los títulos de sus ofertas. `claude_sesion` sí puede proponerlo, con aprobación.

### Carritos, fotos y migración

- **Carritos**. Quien tiene una línea de la tarjeta principal no nota nada: misma llave.
  El problema son las tarjetas sueltas (`source-…`) que pasan a un grupo: su llave
  desaparece y la app descarta la línea sin avisar. Esto **ya ocurre hoy** con cada
  decisión que agrupa. Solución sin estado: generalizar `legacy-ids` (hoy sólo
  Tegucigalpa) a todas las ciudades. Para cada fila comparable, el exportador publica el
  prefijo de `sha256("súper:id")` de cada oferta → llave actual + sucursal. Se calcula
  con el catálogo del día, sin guardar historia, y `migrateCartLines` ya lo entiende:
  mueve la línea y deja fijada la tienda, así el precio que vio la persona no cambia solo.
- **Camino inverso** (una oferta sale del grupo): la línea sigue existiendo y la app la
  marca con `gone`. Punto a revisar al implementar: en "Mi compra" el total usa la oferta
  más barata cuando la tienda elegida ya no está (`used = picked || best`); `AGENTS.md`
  prohíbe sustituciones silenciosas.
- **Fotos**. La tarjeta secundaria es nueva y nace sin foto hasta la corrida siguiente
  (dos al día). Con la propuesta 2 cada oferta ya tiene su foto y la tarjeta la adopta del
  propio bucket, como hoy `adopt_branch_photos`. La foto de la tarjeta principal pudo
  venir de un súper que ahora está en el otro grupo: la firma `sig` cambia al cambiar las
  ofertas, así que la corrida siguiente la reevalúa sola.
- **Migración de datos**: ninguna. Las 10 decisiones guardadas siguen valiendo; separar
  un GTIN ya decidido es enviar otra decisión, que reemplaza la anterior y borra sus
  grupos viejos en el mismo lote.

### Riesgos y mitigaciones

| Riesgo | Mitigación |
| --- | --- |
| Código que asume `prod_gtin_` en el id canónico (`matching/gtin.py`, `tabular_persistence.py`, `matching/review.py`, `product_master.py`) | Auditar esos cuatro puntos antes de escribir; prueba que recorre marts B2B y catálogo con un grupo `mp_*`. |
| Tabla nueva rompe la captura diaria (pasó con `size_decisions`, #72) | Añadirla a las dos listas de la guarda en el mismo PR, con prueba. |
| Dos tamaños demasiado cercanos reparten mal las ofertas | Validación de solapamiento al guardar; vista previa en el panel. |
| Onzas sin convertir caen en el grupo equivocado | Misma lectura doble de `match_official`; si coincide con dos oficiales, se aísla. |
| Maestro secundario huérfano al cambiar de opinión | La sincronización ya retira maestros que salen del estado deseado. |
| Power BI: aparecen productos comparables nuevos | Es el efecto buscado; medir antes cuántos grupos y avisar en `PROJECT_STATE.md`. |

### Pruebas

Motor: dos y tres grupos, oferta sin tamaño, onza ambigua, grupo de una sola cadena,
reemplazo de decisión, humana sobre IA. Contrato: v1 intacto, v2 válido e inválido.
Maestro: ids deterministas, índice único de `primary_gtin`, retiro. Exportador: salida
byte a byte igual sin tabla; `row_id` del principal sin cambio. Frontend: migración de
línea suelta a grupo. Guarda de esquema. Medición previa de sólo lectura (como la fase 4):
cuántos GTIN de la cola tienen dos tamaños con al menos dos cadenas cada uno.

### Esfuerzo: **L**

Toca motor, contrato, script de guardado, maestro, cola, panel y exportador de llaves
viejas; cambia identidad pública y necesita medición antes y verificación en producción
después. Divisible en tres PR: (a) llaves viejas en todas las ciudades, (b) motor +
tabla + maestro detrás de tabla vacía, (c) panel.

---

## 2. Una foto por supermercado

### Problema

R2 guarda **una foto por tarjeta**. El índice (`fotos/v2/index.json`) anota en `sm` de qué
súper salió. En la cola, `photo_names` reparte esa única foto a todas las ofertas de la
tarjeta y marca `foto_de` cuando no es la del súper de la oferta.

Consecuencias: en un grupo comparable (casos `atipico`) el revisor ve la misma foto
repetida y no puede leer la etiqueta de cada cadena, que es justo lo que se discute
(Mazola: ¿cuál súper muestra 908 g?). La IA recibe una sola imagen distinta por tarjeta,
así que "todas las fotos legibles coinciden" pesa menos de lo que parece. Y una oferta
que se aísla o se separa (propuesta 1) estrena tarjeta sin foto.

### Alternativas consideradas

| Alternativa | Por qué no |
| --- | --- |
| Enlazar en el panel la foto original del súper | Añade orígenes a la CSP, depende de URLs que cambian y la IA no puede usarla igual. |
| Bajar las fotos al vuelo en el workflow de la cola | Tráfico nuevo a las tiendas en cada corrida; hoy la cola no les pide nada. |
| Guardar todas las candidatas de todo el catálogo | Multiplica el bucket sin uso: una tarjeta de un solo súper ya es "su" foto. |

### Diseño elegido: la foto de la oferta es la foto de su tarjeta suelta

La llave de una tarjeta suelta es `sha256("súper:id")[:24]`. Se guarda la foto de cada
oferta de un grupo comparable **bajo esa misma llave y con la misma ruta**:

```text
fotos/v2/<px>/<llave oferta[:2]>/<llave oferta>-<huella>.webp
index.json → items[<llave oferta>] = { h, sm, s, q, px, c, of: 1 }
```

- **Sin tráfico nuevo**: `actualizar_fotos_productos.py` ya descarga y puntúa la foto
  principal de cada súper para elegir una; hoy descarta las demás. Sólo cambia qué se
  conserva y se sube.
- **Filtro**: se guarda si no es genérica ("sin imagen", logo) ni escena. No se exige el
  mínimo de 55 puntos de la tarjeta: aquí importa que la etiqueta se lea.
- **Cola**: `foto` de cada oferta = su foto propia si existe; si no, la de la tarjeta con
  `foto_de`, como hoy. El panel y `preparar_revision_claude.py` no cambian de contrato.
- **Catálogo público**: sin cambios (`search.json` sigue con una foto por tarjeta).
- **Efecto útil**: si la oferta se aísla o se separa, su tarjeta ya tiene foto.
- **Alcance por etapas**: (a) sólo los GTIN de la cola (~1,065 códigos); (b) todas las
  tarjetas comparables (~4,600 en SPS) si (a) demuestra valor.

Fuera de alcance, para decidir después con datos: exigir a la IA al menos dos fotos
**distintas** que coincidan para auto-resolver. Es un cambio de regla, no de fotos.

### Riesgos y mitigaciones

| Riesgo | Mitigación |
| --- | --- |
| La autorización de fotos (`.automation/product-images-capture-authorization.json`) aprobó una foto por producto | Confirmar con el dueño antes de guardar más; mismo procedimiento de retiro por súper. |
| Crece el bucket y el índice | Etapa (a) acotada; medir bytes en la primera corrida. El índice sólo lo leen los scripts. |
| La regla de genéricas cuenta dueños por huella | Las entradas `of` no cuentan como otro dueño, igual que las heredadas (`de`). |
| Límite de tiempo de la corrida (250 min) | Las fotos ya están descargadas; sólo se añade el recorte y la subida. Medir. |

### Pruebas

Índice con entradas `of` (validación en `subir_fotos_r2.py`), no contaminan genéricas,
`photo_names` prefiere la foto propia, cola sin `foto_de` cuando hay foto propia, panel
con fotos distintas por oferta, artifact reanudable.

### Esfuerzo: **M**

Un script principal y dos consumidores, sin tablas ni identidad. Lo que lo saca de S es
la validación de subida, el índice compartido y la verificación en una corrida real larga.

---

## 3. Ofertas "zombi"

### Problema

"Zombi" no está definido en el repo; se usa aquí esta definición, **a confirmar**: una
oferta que el súper ya no publica pero sigue en el catálogo con su último precio.

Ocurre porque la persistencia sólo compara lo que llega (`delta` se carga con `JOIN
incoming`): un producto que deja de aparecer en el snapshot no cambia de estado y su
periodo en `price_history` queda abierto para siempre. No hay marca de "visto por última
vez" por producto, y la frescura es por contexto (súper + ubicación), así que la oferta
sale como `FRESH`. El revisor la descubre en el panel: busca el código en el súper y ya
no existe. Efectos: precio viejo que compite por "mejor precio", grupos comparables con
una cadena que no vende el producto y elementos de la cola que no se pueden resolver.

### Alternativas consideradas

| Alternativa | Por qué no (ahora) |
| --- | --- |
| Cerrar el periodo cuando el producto falta en N snapshots aceptados | Es el arreglo de fondo, pero toca la persistencia base y la verdad comercial ("ausencia no es agotado"). Requiere medir primero; queda como siguiente paso. |
| Tabla `last_seen` por producto actualizada a diario | Decenas de miles de escrituras al día: contra "escribir sólo deltas". |
| Ocultar en el frontend | El navegador no decide catálogo y el precio seguiría en los marts. |
| Marcar `out_of_stock` | Falsea el estado: no hay evidencia de agotado. |

### Diseño elegido: supresión manual que se levanta sola

**Tabla nueva `offer_suppressions`** (STRICT, derivada, en `OPTIONAL_DERIVED_TABLES`):

| Campo | Notas |
| --- | --- |
| `supermarket_id`, `product_id` | PK; FK a `products` |
| `reason` | `zombie` (lista cerrada, ampliable) |
| `note`, `decided_by`, `decided_at_utc` | sólo `human:<cuenta>`; la IA no marca zombis |

**Marcar** (panel): botón "Ya no está en el súper" en cada oferta y "Deshacer". Viaja en
un contrato propio `rpi-offer-flags/v1` (`[{ref: "walmart:51", flag: "zombie" | "clear",
note}]`) por el mismo workflow de decisiones, como entrada opcional adicional; el script
valida que la oferta exista y escribe en lote atómico. El registro de seguridad de
workflows se actualiza en el mismo PR.

**Efecto en el catálogo público**: el exportador lee la tabla una vez (pequeña; si no
existe, conjunto vacío y salida idéntica) y quita esas ofertas **después** de formar los
grupos, no antes. Así la tarjeta conserva su `row_id` aunque quede con una sola cadena
(pasa a `individual`, igual que hoy con los agotados) y los carritos no pierden la línea;
la app ya muestra la tienda elegida como no disponible. Si todas las ofertas de una
tarjeta están suprimidas, la tarjeta no se publica.

**Efecto en la cola**: el tipo (`empate` / `atipico`) se calcula sin contar ofertas
suprimidas; si el conflicto desaparece, el código sale de la cola. La identidad en Turso
no se toca: suprimir es visibilidad, no homologación.

**Cuándo vuelve a aparecer**: cuando el súper la vuelve a publicar. La persistencia
diaria ya tiene en memoria lo que llegó (`incoming`); un único `DELETE` borra las
supresiones cuyos productos están en el snapshot aceptado de ese contexto. Escribe sólo
si hay coincidencias. Consecuencias:

- un zombi real nunca llega en el snapshot → sigue oculto;
- si el revisor se equivocó, la oferta reaparece sola en la captura siguiente;
- no depende de que cambie el precio;
- también se levanta a mano con "Deshacer".

### Riesgos y mitigaciones

| Riesgo | Mitigación |
| --- | --- |
| Se añade una sentencia al camino crítico diario | Sólo si la tabla existe; una sentencia, sin lecturas globales; prueba de que sin tabla el lote es idéntico. Alternativa si se prefiere no tocarlo: reaparecer cuando se abre un periodo nuevo posterior a la marca (más débil: exige cambio de precio). |
| Un súper con captura fallida ese día | La supresión no se levanta: sólo actúan snapshots aceptados. |
| Marcas olvidadas | Pestaña "Ocultas" en el panel con fecha, autor y nota. |
| B2B sigue viendo el zombi (PCI, ranking) | Fuera del primer PR a propósito; segundo paso con el mismo filtro en los marts, tras medir. |
| Tabla nueva rompe la guarda de esquema | Las dos listas en el mismo PR. |

### Pruebas

Contrato (falla cerrado, oferta inexistente, duplicados), exportador (grupo de 3 a 2, de
2 a 1 con el mismo `row_id`, tarjeta vacía, sin tabla = byte a byte), persistencia
(levanta sólo lo presente, sólo en snapshot aceptado), cola, panel, guarda de esquema y
registro de workflows. Medición previa de sólo lectura: cuántos periodos abiertos no
aparecen en el último snapshot aceptado de cada cadena; ese número decide si vale la
pena el arreglo automático.

### Esfuerzo: **M**

Tabla, contrato, workflow, exportador, cola, panel y una sentencia en persistencia. No
cambia identidad, pero toca el camino diario y un workflow que escribe: necesita permiso
explícito y verificación en producción.

---

## 4. "Pudding Hunts 4 Pack (368 g)" con `pack_count`

### Problema

Un multipack se publica como "368 g" (o "4 × 92 g") y la tarjeta no dice que son cuatro
unidades; al lado de un pudín suelto parece otro tamaño del mismo producto.

El dato existe: el perfil guarda `presentation_pack_count`, `normalized_pack_count` y
`display_presentation`. Pero el catálogo público sólo lleva el texto `presentation`:
`canonical_presentation` devuelve "N × X" sólo cuando `display_presentation` cuadra con
el total (`_multipack`) y, si no, el total a secas. `presentationLabel` (`data.js`) deja
pasar cualquier texto que no sea una medida simple.

### Alternativas consideradas

| Alternativa | Por qué no |
| --- | --- |
| Que el frontend lea "4 pack" del nombre | Más heurística en JavaScript; ya hay expresiones de respaldo y la regla es no decidir ahí. |
| Escribir "4 Pack (368 g)" dentro de `presentation` | Ese texto es faceta, criterio de orden y entrada de los conversores a libras/onzas; cambiar su forma fragmenta filtros y rompe formatos. |
| Calcular el total en el navegador desde "4 × 92 g" | Aritmética y redondeo duplicados fuera de Python. |

### Diseño elegido: dato estructurado opcional, formato en el frontend

**Exportador**: campo opcional `pack` por fila, `[conteo, "total"]` (p. ej.
`[4, "368 g"]`), y la misma columna en `search.json` (0 cuando no aplica). `presentation`
no cambia: facetas, orden y precio por unidad quedan igual. Se emite sólo si:

- `pack_count ≥ 2` y dimensión de masa, volumen u onza (un conteo "12 unidades" no es pack);
- estado de presentación `confirmed` o `name_only` (nunca `ambiguous_multipack` ni `conflict`);
- conteo × unidad cuadra con el total (misma tolerancia de `_multipack`);
- en un grupo comparable, ningún miembro lo contradice; si discrepan, no se emite.

**Frontend** (`presentationLabel`): si hay `pack`, devuelve
`"<conteo> Pack (<etiqueta del total>)"`, reutilizando el formato vigente para el total
(equivalentes en libras u onzas incluidos). Sin `pack`, comportamiento actual. Es sólo
formato: ni cálculo ni identidad.

**Compatibilidad**: las columnas de `search.json` se leen por nombre (como `photo`), así
que una app en caché ignora la columna nueva y una app nueva con catálogo viejo no ve
`pack`. Sube la versión de la app con `versionar_super_compras.py --auto`.

### Riesgos y mitigaciones

| Riesgo | Mitigación |
| --- | --- |
| Conteo mal leído ("Pack 6" que es sabor o modelo) | Sólo estados aceptados y aritmética que cuadra; ante contradicción, no se muestra. |
| Tamaño de `search.json` | Una columna, 0 en casi todas las filas; medir bytes con gzip en el PR (hoy ≈1.3 MB). |
| "Otras presentaciones" muestra el texto viejo | Pasar `pack` también en esas entradas. |
| El caso Hunts no cumple las condiciones | Verificar su perfil antes de implementar; si el conteo sólo está en el nombre, es un ajuste del parser en Python, no del frontend. |

### Pruebas

Exportador: con y sin pack, discrepancia en grupo, estados excluidos, `search.json` con
la columna. Frontend: etiqueta con gramos, mililitros y onzas, catálogo sin columna.
Catálogo sin multipacks: salida idéntica salvo la columna.

### Esfuerzo: **S**

Un campo en el exportador, una rama en una función del frontend y sus pruebas. Sin
tablas, sin identidad, sin workflows.

---

## Preguntas abiertas para el dueño

1. **Zombi**: ¿la definición de la sección 3 es la correcta?
2. **Fotos**: ¿la autorización vigente cubre guardar la foto de cada súper, o hace falta
   ampliarla?
3. **Separar**: ¿máximo de grupos por código? Se propone 4 (Naturas tiene 3).
4. **Zombi en B2B**: ¿se oculta también en Power BI en un segundo paso?
