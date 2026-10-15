# product-images

Fotos de producto normalizadas para Súper Compras (WebP 400×400, fondo blanco).
Rama generada por el workflow "Precios SPS - Publicar fotos de producto"; no editar a mano.

- `index.json`: `items[<id>] = {sig, v, sm, s}` o `{sig, none: 1}`.
- `img/<id[:2]>/<id>.webp`: foto elegida para la fila pública `<id>` del catálogo.

Las fotos provienen de las fichas públicas de cada supermercado. Si un supermercado
pide retirarlas se borran de esta rama y la app vuelve a las ilustraciones.
