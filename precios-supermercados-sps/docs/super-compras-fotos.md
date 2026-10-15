# Súper Compras: fotos de producto

Aprobado por el responsable del proyecto (octubre 2026). Registro:
`.automation/product-images-capture-authorization.json`.

## Flujo semanal

1. **Precios SPS - Fotos de producto semanal** (`contents: read`, secretos Turso de
   sólo lectura) corre los miércoles 11:29 (Honduras) o a mano.
   `scripts/actualizar_fotos_productos.py`:
   - lee el catálogo ya publicado en `portfolio-data` (una fila por producto);
   - resuelve en Turso la llave exacta de cada oferta (`source_item_id`, `source_key`,
     `source_catalog_product_id`);
   - pide direcciones de foto a endpoints públicos (`product_image_sources.py`):
     Walmart/La Colonia VTEX por `skuId` (lotes de 50), Colonial `products.json`,
     Los Andes catálogo paginado, PriceSmart búsqueda por categoría;
   - por fila pendiente descarga la foto principal de cada súper (la segunda sólo si
     la principal no alcanza), puntúa y elige (`product_images.py`) y normaliza a
     WebP 400×400, producto centrado al 80 %, fondo blanco;
   - deja un artifact con las fotos nuevas y el `index.json` completo.
2. **Precios SPS - Publicar fotos de producto** (`contents: write`, sin secretos)
   valida el artifact (rutas `img/<id[:2]>/<id>.webp`, cabecera WebP, ≤ 200 KB) y lo
   copia en un commit a la rama huérfana `product-images`.
3. La publicación del catálogo lee `product-images/index.json` (opcional) y agrega la
   columna `photo` (versión, 0 = sin foto) a `search.json`.
4. La app pide
   `raw.githubusercontent.com/.../product-images/img/<id[:2]>/<id>.webp?v=<versión>`
   con `mix-blend-mode: multiply`; si no hay foto o falla, dibuja la ilustración.

## Criterios de elección

- Fondo blanco, resolución, nitidez, encuadre, cuadrada; GTIN coincidente suma.
- Foto secundaria −20 (suele ser el reverso); franja lateral se recorta o penaliza.
- Fotos repetidas entre productos (logos, "sin imagen") quedan fuera.
- Si dos o más súper coinciden y una foto no se parece, se descarta (producto distinto).
- Empates (≤ 10 puntos): Colonial > La Colonia > Walmart > Los Andes > PriceSmart.
- Mínimo 60 puntos; sin foto aceptable la fila queda anotada (`none`) y no se
  reintenta mientras sus direcciones no cambien (`sig`).

## Límites

- 4 000 productos y 12 000 descargas por corrida; 1 s entre llamadas a los sitios y
  0,15 s entre fotos (CDN). Se procesan primero las filas con más súper.
- Una fila con algún súper que no respondió esta semana se pospone.

## Retiro

Si un supermercado pide retirar sus fotos: poner `live_read_only_authorized` en
`false`, borrar sus archivos de `product-images` y regenerar `index.json`; la app
vuelve a las ilustraciones sin cambios de código.
