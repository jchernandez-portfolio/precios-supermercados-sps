# Súper Compras: fotos de producto

Aprobado por el responsable del proyecto (octubre 2026). Registro:
`.automation/product-images-capture-authorization.json`.

## Dónde viven

| Pieza | Lugar |
| --- | --- |
| Fotos v2 (800 y 320 px) | Cloudflare R2, bucket público `super-compras-fotos`, `fotos/v2/<tamaño>/<id[:2]>/<id>-<huella>.webp` |
| Índice v2 | `fotos/v2/index.json` en el mismo bucket (`items[id]`: `sig`, `h` huella, `sm` súper, `s` puntaje, `q` nivel, `px` píxeles útiles, `c` versión de criterios; o `none`) |
| Revisión a ojo de la última corrida | `fotos/v2/revision/latest.html` (y una por corrida) |
| Dirección pública | `https://pub-0dcb2204775c484dbe6681e659eee236.r2.dev` (variable `R2_PUBLIC_BASE_URL`) |
| Fotos v1 (400 px, en retiro) | rama `product-images` |

El nombre de cada foto lleva la huella de su contenido: una foto publicada nunca
cambia, se guarda en caché un año (`immutable`) y el repositorio no carga imágenes.
Mientras dura la migración, el catálogo usa la foto v2 si existe y si no la v1.

## Flujo diario

1. **Precios SPS - Fotos de producto** (`contents: read`, secretos de Turso de sólo
   lectura, sin llaves de R2) corre a las 02:29 y 14:29 de Honduras o a mano.
   `scripts/actualizar_fotos_productos.py`:
   - lee el catálogo publicado de **todas las ciudades** (`cities.json`) y une las
     filas del mismo producto;
   - resuelve en Turso la llave exacta de cada oferta;
   - pide direcciones de foto a endpoints públicos (`product_image_sources.py`):
     Walmart, Paiz y La Colonia (VTEX, 1000 px), Colonial (Shopify, hasta 1200 px),
     Los Andes y PriceSmart (1024 px);
   - descarga la foto principal de cada súper (la segunda sólo si la principal no
     alcanza), puntúa, elige (`product_images.py`) y genera WebP de 800 y 320 px;
   - deja un artifact con las fotos nuevas, el `index.json` completo y `revision.html`.
2. **Precios SPS - Publicar fotos de producto en R2** (`contents: read`, sólo las
   llaves del bucket) valida el artifact con `scripts/subir_fotos_r2.py` (rutas,
   cabecera WebP, tamaño, índice) y sube las fotos; el índice va al final.
3. La publicación del catálogo lee los dos índices y llena la columna `photo` de
   `search.json`: huella v2, versión v1 o 0.
4. La app usa 320 px en tarjetas y deja que el navegador elija 320 u 800 px en el
   detalle (`srcset`); si no hay foto o falla, dibuja la ilustración.

## Criterios de calidad (versión 2, 2026-10-25)

- **Producto completo.** Sólo se recortan franjas angostas pegadas a los lados
  (texto de Walmart). Los bloques separados por un hueco —la tapa de una botella
  transparente, dos piezas de un paquete— son parte del producto. Sólo se vuelve
  blanco el fondo conectado al borde; el interior del producto no se toca.
  (Caso Agua Zen 600 ml: antes sólo quedaba la etiqueta.)
- **Detalle real.** Se mide la resolución efectiva (cuánto detalle se pierde al
  reducir y volver a agrandar). Una foto de 3000 px que en realidad es una foto chica
  agrandada sale marcada como `agrandada` y pierde contra una nítida.
- **Nivel** de cada foto elegida en el índice: `hd` (el producto tiene al menos
  560 px útiles: 800 px nítido), `sd` (≥ 260 px) o `low`.
- **Fondo blanco obligatorio** (criterios v3, 2026-10-25). Una foto con fondo que
  no es blanco tras el balance de blancos (producto sobre una mesa, foto de ambiente,
  fondo oscuro, ilustración con regla) se descarta: no se distingue el producto del
  entorno. En la primera corrida pasaban con 55 puntos justos (2 % de las elegidas);
  las ya publicadas con menos de 70 puntos se reevalúan una vez (campo `c` del índice).
- Puntaje: fondo blanco 25, píxeles útiles del producto 35, nitidez 10, encuadre 15,
  código de barras 5, foto principal 10; foto secundaria −20. Mínimo 55.
- Elección: primero el nivel de detalle, después el puntaje; en empates (≤ 6
  puntos) Colonial > La Colonia > Walmart > Paiz > Los Andes > PriceSmart.
- Fotos repetidas entre productos (logos, "sin imagen") quedan fuera; si dos o más
  súper coinciden y una foto no se parece, se descarta (producto distinto).

## Límites

- Hasta 12 000 productos y 30 000 descargas por corrida (dos corridas al día: el
  catálogo completo se cubre en unos tres días; después sólo se procesa lo nuevo o
  lo que cambió); 1 s entre llamadas a los sitios y 0,15 s entre fotos. Se procesan
  primero las filas con más súper.
- Una fila con algún súper que no respondió ese día se pospone.
- Productos sin foto en ningún súper siguen con ilustración.

## Revisión

Cada corrida publica `fotos/v2/revision/latest.html`: las 120 fotos elegidas con
menor puntaje, una muestra de 80 y productos sin foto aceptable, con los datos de
cada candidata (súper, puntaje, nivel, píxeles útiles, factor de detalle, tamaño y
enlace a la foto original).

## Informe

La auditoría de calidad (tres veces al día) publica en la rama `calidad-datos` la
cobertura de fotos por ciudad y supermercado (`fotos.csv` y sección "Fotos de
producto" del README) con los niveles hd/sd/low leídos del índice del bucket.

## Retiro

Si un supermercado pide retirar sus fotos: poner `live_read_only_authorized` en
`false`, borrar sus archivos del bucket y regenerar el índice; la app vuelve a las
ilustraciones sin cambios de código.
