# Auditoría del catálogo público de Súper Compras — 2026-11-13

Qué ve el público en Súper Compras (San Pedro Sula y Tegucigalpa): fotos, títulos,
presentaciones, precios por unidad, frescura, disponibilidad, duplicados y categorías.
Sólo datos ya publicados (rama `portfolio-data`, `published/rpi/v3`) y fotos de R2; sin
Turso, sin sitios de los supermercados.

- Catálogo auditado: SPS `as_of` 2026-11-13T21:10:40Z, TGU 2026-11-13T21:12:31Z.
- Fotos: `fotos/v2/index.json` de R2, actualizado 2026-11-13T17:26:36Z.
- Script reproducible: `scripts/auditar_catalogo_publico.py --data <copia de published/rpi/v3>
  --photos-index <index.json>` (complementa `auditar_calidad_catalogo.mjs`, que audita lo
  que la app muestra después de sus correcciones; éste audita lo que sale del pipeline Python).

## Resumen

| | San Pedro Sula | Tegucigalpa |
|---|---:|---:|
| Filas públicas | 34,293 | 27,140 |
| Ofertas | 42,142 | 65,588 |
| Comparables / individuales / un solo súper | 4,723 / 14,423 / 15,147 | 8,790 / 7,160 / 11,190 |
| Ofertas FRESH | 100 % | 100 % |
| Ofertas **por libra** | 842 | **0** (error, corregido aquí) |
| Filas sin presentación | 10,952 (32 %) | 10,159 (37 %) |
| Filas sin foto | 4,021 (12 %) | 2,197 (8 %) |

Lo más grave que se encontró y se corrigió en este PR:

1. **Tegucigalpa nunca publicaba precio por libra** (0 ofertas contra 842 en SPS): 170 filas
   que dicen "Precio indicado por libra" salían por paquete o sin precio por unidad
   (p. ej. "Medallon Lomo Cerdo Don Cristobal 5 lb … Precio Indicado Por Libra": L 74.90
   la libra mostrado como "2.268 kg" a L 33.03/kg, cuando son ≈ L 165/kg).
2. **Fotos heredadas de la tarjeta por sucursal que son de otro producto** (51 filas; p. ej.
   "Refrigeradora Durabrand" con la foto del imán de moscas Terro).
3. **Búsqueda "café"**: hierbas e infusiones (Valeriana, Zacate de limón, Manzanilla Mazo) y
   cremoras tipificadas como "Café" (≈ 40 filas en SPS y ≈ 30 en TGU).
4. **"Precios de hoy"** con La Colonia del día anterior (FRESH por umbral de 48 h).

## 1. Fotos (revisión visual)

Muestra estratificada de **128 productos** mirados uno por uno (hojas de contacto de las
fotos de 320 px de R2 con título y presentación publicados): 56 de la portada (ofertas y
"más comparados" de ambas ciudades), 36 comparables al azar y 36 individuales (4 por súper
y ciudad). Más 24 fotos heredadas de sucursal al azar y los 52 pares de filas con la misma
foto y títulos distintos.

| Hallazgo | Muestra 128 | Heredadas (24) | Por tipo / súper |
|---|---:|---:|---|
| Foto de otro producto | **0** | **1** | Walmart TGU (heredada) |
| Foto de una variante cercana (otro sabor) | 0 | 1 | Walmart TGU |
| Tamaño publicado que contradice la foto/título | 4 | 1 | PriceSmart, Paiz, La Colonia, Colonial |
| Título ilegible o duplicado | 4 | 0 | Walmart (3), Paiz (1) |

Ejemplos (id público = llave de la foto en R2, `fotos/v2/320/<id[:2]>/<id>-<huella>.webp`):

- `36731dc93aee636bc7968cae` "Audifonos Durabrand Inalambricos" (TGU, Walmart): la foto
  es un muñeco de Toy Story (Lotso). Heredada de la tarjeta por sucursal `cf3bb314…`.
- `4618a53688a507674b1f1d1f` "Refrigeradora Durabrand DUSRF112CAM": foto del imán de moscas
  Terro; `4cc432e8…` "Premezcla Tres Leches": foto de "Detergente Ariel 5 L";
  `c5702ebe…` "Tomate Pera En Red": foto de "Maggi Paprika"; `caf002d8…` "Set de
  Individuales": foto de "Protector de cama". Todas heredadas (`de`) de la tarjeta por
  sucursal, cuya firma de fotos es idéntica a la del otro producto.
- `a1a77ccc23ac8d0e1e753159` "Vela Numero 1" (TGU, comparable): **450 g** (Paiz publica ese
  peso en su campo de presentación; es una vela de cumpleaños).
- `0457f3da53aa2ac405b7e3be` "Snickers, Twix, M&M's … 30 Unidades / 1.5 kg": **"30 × 1.5 kg"**
  (la app lo corrige a 1.5 kg con su patrón de precio por kg).
- `fb7bb05ac6dff12d4952de5d` "Esencia Francelia Vainilla Negra 16 Oz": **453.59 g** (es líquido:
  16 fl oz ≈ 473 ml).
- `e10b1b6c31effde6552f39fb` "KLEENEX PañuelosDíaADía 4Pax10UN": **10 unidades** (son 4 × 10).
- `114dbe597b818a9430aacab5` "Ttaio Equate 1Ea Relx Men Esp Tel Stretc" (título POS de Walmart).

Fotos a escala (índice completo cruzado con el catálogo):

| | SPS | TGU |
|---|---:|---:|
| Sin foto: PriceSmart | 1,069 / 2,770 (39 %) | 1,096 / 2,826 (39 %) |
| Sin foto: Colonial (individuales / un súper) | 442 / 2,376 · 844 / 2,899 | — |
| Sin foto: Walmart individuales | 563 / 5,768 | 469 / 994 (47 %) |
| Sin foto: comparables | 4 / 4,723 | 107 / 8,790 |
| Foto heredada de sucursal | 144 | 3,913 |
| Foto elegida de un súper que ya no está en la fila | 514 | 1,658 |

"Foto de un súper que no está en la fila" no es error en sí: la llave de una fila comparable
es la misma en las dos ciudades (en TGU se ven 1,254 fotos de Colonial, que sólo está en SPS).

## 2. Títulos

| Señal | SPS | TGU |
|---|---:|---:|
| Palabras pegadas ("ChileHabanExtrHot", "PañuelosDíaADía") | 1,541 (Colonial 1,070 = 21 % de Colonial) | 235 |
| Todo en mayúsculas | 169 | 53 |
| Termina en número sin unidad y sin presentación ("4VIENTOS ChileHabanExtrHot 140") | 632 | 473 |
| Título duplicado (VTEX `nameComplete` = nombre del producto + nombre del SKU) | 403 (La Colonia 281) | 439 (La Colonia 322) |
| Título vacío | 0 | 0 |

"4VIENTOS ChileHabanExtrHot 140" es el nombre abreviado del POS de Colonial; el "140" no
dice qué mide, por eso no hay presentación (correcto: no se inventa la unidad). Los títulos
duplicados ("Martini Extra Dry de 750ML Martini Extra Dry 750 ML") vienen de `nameComplete`
de VTEX (La Colonia, Walmart, Paiz); en algunos el SKU describe **otro producto**
("Maybell Mascar Colosal … Blk9.2ml Tinte Para Cejas Maybelline …", fila comparable
`product-22f33b74adfd3aeb656db306`).

## 3. Presentación y precio por unidad

- Título que contradice la presentación: 13 en SPS, 17 en TGU (casi todos del súper:
  "Helado Breyers … 1/2 Gl 48 Oz" = 1.893 L; "Gas Butano … 300mL/165g"). Uno era del
  pipeline: "Now Magnesio 180 Cápsulas / 400 mg" → **72 g** (180 × 400 mg; L 7,221/kg).
- Peso del campo del súper que contradice un conteo del nombre (Paiz, PriceSmart):
  "Protectores Diarios Saba … - 40 piezas" → **3 g**; "Rasuradoras Equate … - 15Pzas" →
  **415 g**; "Tapete Antifatiga 12 Piezas" → 270 g. ≈ 17 filas en TGU y 2 en SPS.
- Precio por unidad 100 veces mayor o 20 veces menor que la mediana de su tipo: 102 en SPS,
  216 en TGU (la app ya los corrige o esconde con `hideImplausible`; siguen en
  `errores_detectados.csv` de la rama `calidad-datos`). En TGU 170 eran productos por libra
  mostrados por paquete (punto 1 del resumen).
- Filas comparables cuyas ofertas usan tamaños distintos para el precio por unidad: 8 en SPS,
  22 en TGU. La mayoría es **helado Sarita** agrupado por GTIN con "1/2 gal" (1.893 L, La
  Colonia y Los Andes) y "1000 g" (Walmart, Paiz): es el caso g≈ml del motor (pendiente, otro
  trabajo); el resto son cosméticos con 0.5–1 ml de diferencia entre súper.
- Sin presentación: Walmart 6,527 de 12,090 filas propias en SPS (ropa, hogar, ferretería
  sin contenido neto); la app muestra "1 unidad" (regla GS1) salvo número suelto.

## 4. Frescura: `observed_at` viejo con FRESH

`observed_at` es **el inicio del periodo de precio vigente** (`price_history.valid_from_utc`),
no la última vez que se vio el producto. `freshness_status` es **por súper y sucursal**: FRESH
si su última captura aceptada tiene menos de 48 h (`--freshness-hours 48`). Por eso una
oferta con `observed_at` 2026-08-30 y FRESH es correcta: Colonial tiene precio sin cambios
desde su primera captura (30-ago) y la captura del 13-nov se aceptó. Distribución
("precio sin cambio desde"): en SPS 20,374 de 42,142 ofertas (48 %) llevan 60 días o más con
el mismo precio; en TGU 26,548 de 65,588 (40 %).

Riesgo real detrás de esto (**pendiente**): una oferta que el súper deja de publicar conserva
su periodo abierto (ausencia ≠ agotado, por contrato) y sigue saliendo FRESH con su precio
viejo. No hay `last_seen` por oferta para distinguirlo. Señal medida: **filas duplicadas del
mismo súper** con el mismo nombre y presentación (SPS 488, TGU 554; PriceSmart ≈ 165 por
ciudad, Walmart 250–350): p. ej. PriceSmart TGU `pricesmart:35606` (precio desde 01-sep) y
`pricesmart:63198` (desde 02-nov) "Mini Panceta de Cerdo … 907 g"; Colonial `colonial:13211`
y `colonial:62703` "ENFAGROW Premium 3 Lata 1000g", mismo precio. Los Colonial 62xxx y
PriceSmart 63xxx son códigos nuevos del súper; el viejo probablemente ya no está publicado.
Además, Colonial publica ≈ 9,137 productos en su sitio (PROJECT_STATE) y SPS tiene 9,511
ofertas current de Colonial.

La Colonia SPS del 12-nov (33 h) era FRESH y la portada decía "Precios de hoy". El umbral de
48 h es de diseño (permite que un día fallido no saque al súper de la comparación), pero
"de hoy" debe ser la fecha de Honduras del catálogo. Corregido en el texto de la app (abajo).

## 5. Disponibilidad

- Los Andes: 6,944 de 6,944 `unknown` (no publica existencias; correcto).
- Agotadas visibles: La Colonia SPS 779, Walmart TGU 2,727, Paiz 1,406. Una fila sólo se
  publica si tiene al menos una oferta no agotada con precio; las agotadas nunca entran a
  ranking (correcto).
- PriceSmart: 8,225 ofertas current por ciudad, 2,802–2,847 publicadas: el resto no tiene
  ninguna oferta comprable (agotadas sin precio).

## 6. Categorías

- "Café": La Colonia mapea su categoría "Café, Tés e Infusiones" sólo a nivel categoría y el
  motor deduce el tipo "Café" de la ruta. Resultado: Valeriana Mazo, Manzanilla Mazo, Zacate
  de limón, hojas de guayaba/guanábana, Linaza, Chía, Orégano, cremoras, "Crema Agria Daisy",
  "Hot Cake Fibra" y hasta "Calcetín Hombre American Legend" salían como "Café" (42 filas
  en SPS, 31 en TGU). La búsqueda "café" ordenada por precio empezaba por las hierbas.
- Alternativas: "DEL CHEF Tortilla Maiz a Mano" (Colonial, tipo genérico "Abarrotes", sin
  tamaño publicado → la app muestra "1 unidad") proponía semitas de Los Andes "por unidad".

## Arreglado en este PR

| # | Qué | Dónde | Efecto medido / esperado |
|---|---|---|---|
| 1 | Precio por libra en Tegucigalpa: la regla de producto pesado comparaba `walmart:24134@walmart_tgu_ffaa` con `walmart:24134` | `scripts/exportar_consumer_catalog.py` (`_pound_key`) | 0 → ≈ 400 ofertas por libra en TGU (las mismas que en SPS) |
| 2 | Dosis con conteo: "180 Cápsulas / 400 mg" se publica como 180 unidades, no 72 g | `fix_name_measures` | 1 fila por ciudad |
| 3 | Peso del campo del súper (`source_only`) contra conteo del nombre sin medida: "N unidades" → conteo; "N piezas" → sin tamaño (la app lo lee del nombre) | `fix_name_measures` | ≈ 17 filas TGU, 2 SPS (las de estado `source_only`) |
| 4 | Foto heredada de sucursal que es la foto propia de otro producto publicado (sin productos en común): se quita, se marca `nh` (no heredar otra vez) y la fila se reevalúa con sus propias fotos | `scripts/actualizar_fotos_productos.py` (`drop_foreign_inherited`) | 51 filas hoy (≈ 12 claramente de otro producto; el resto son SKU duplicados o tallas que recuperan su foto al reevaluarse) |
| 5 | Tipo "Café" sólo con evidencia en el nombre; si no, la oferta queda en la categoría del súper y la subcategoría sale por nombre. Reglas nuevas por nombre: "Té e infusiones" (manzanilla, valeriana, zacate de limón, tilo, …) y "Cremoras y endulzantes para café" | `master_taxonomy.assign_offer` + `name-subcategory-rules-v1.csv` (sólo superficie pública; el motor de identidad no se toca) | Valeriana/Manzanilla/Zacate → Té e infusiones; cremoras → Cremoras; linaza, chía, orégano → "Café, té e infusiones" |
| 6 | "Precios de hoy" sólo si todas las capturas son de la fecha de Honduras del catálogo; si no, "Actualizado … · La Colonia del 12 nov" y la nota "puede haber cambiado" en sus ofertas | `super-compras/data.js` (`staleRetailers`, `hondurasDay`) | texto; el umbral FRESH de 48 h no cambia |
| 7 | Alternativas: no se proponen por precio "por unidad" de una pieza sin conteo publicado ("1 unidad") ni en tipos genéricos (Abarrotes) | `super-compras/data.js` (`alternatives`) | tortilla ya no propone semitas; huevos de 15 y 30 siguen comparándose |
| 8 | Auditoría reproducible | `scripts/auditar_catalogo_publico.py` | — |

Las correcciones 1–5 se ven en la próxima publicación del catálogo y en la próxima corrida
de fotos; no escriben en Turso ni cambian identidad, perfiles ni comparabilidad.

## Pendiente (priorizado)

1. **Ofertas fantasma** (alta): registrar la última captura que vio cada oferta (p. ej.
   `last_seen_run` por contexto, escrito como delta) y publicar `last_seen_at`; después,
   ocultar o marcar ofertas que el súper dejó de publicar hace N días. Requiere cambio de
   persistencia (tabla derivada opcional o columna) y revisión de costo en Turso. Señal
   actual: ≈ 500 filas duplicadas del mismo súper por ciudad.
2. **Fotos heredadas sin verificar** (media): 3,913 filas de TGU usan la foto de su tarjeta por
   sucursal; la muestra visual dio 1 de 24 de otro producto (Audífonos → Toy Story), que el
   arreglo 4 no detecta porque esa foto no es de otra fila publicada. Propuesta: reevaluar
   por tandas las filas con `de` cuyas firmas no coinciden con las de su producto actual.
3. **g≈ml en grupos GTIN** (media, motor de identidad — no tocado): helado Sarita "1/2 gal"
   contra "1000 g" en la misma fila comparable; vainilla "16 Oz" en gramos.
4. **Títulos duplicados de VTEX** (media): La Colonia/Walmart/Paiz usan `nameComplete`
   (producto + SKU). Evaluar en la captura guardar también `productName` y `name` del SKU
   para elegir el título sin perder evidencia; hay casos donde el SKU es otro producto.
5. **Peso de Paiz en artículos sin contenido neto** (baja): "Vela Numero 1" 450 g; requiere
   regla por tipo (velas, juguetes) o decisión humana en la cola de tamaños.
6. **Títulos abreviados de Colonial** (baja): 1,070 filas con palabras pegadas; separar
   palabras sólo para mostrar es posible pero no resuelve las abreviaturas.
7. **"Kleenex 4Pax10UN" → 10 unidades** (baja): el patrón "NPax" no se lee como paquete.
8. **Café residual** (baja): linaza, chía, orégano, crema agria quedan en "Café, té e
   infusiones"; con reglas por nombre más amplias podrían ir a su subcategoría real.
