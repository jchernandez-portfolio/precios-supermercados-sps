# Presentación del proyecto en el portafolio

Cómo se presenta públicamente `precios-supermercados-sps` en https://jchernandez-portfolio.github.io/Portafolio/ sin reducirlo a “un scraper” ni exagerar lo que los datos permiten afirmar.

## Identidad

- **Nombre:** Súper Compras.
- **Descripción corta:** web app que compara los precios de 6 cadenas de supermercados en San Pedro Sula y Tegucigalpa, con captura diaria automatizada, homologación GS1, control de calidad y un registro público de errores.
- **Posición:** proyecto principal (01) del portafolio.

El web scraping es la capa de adquisición, no el producto completo. Lo que se destaca es el sistema de punta a punta: captura → validación → homologación → publicación → auditoría, corriendo solo todos los días.

## Dónde vive

| Pieza | Ubicación (repo `Portafolio`) |
| --- | --- |
| Tarjeta y detalle | `precios-supermercados-sps/portfolio/precios-portfolio.js` y `.css` |
| Cifras en vivo | `precios-supermercados-sps/portfolio/super-compras-live.js` |
| Página del caso | `precios-supermercados-sps/portfolio/caso/` |
| Capturas de la app | `precios-supermercados-sps/portfolio/assets/*.webp` |
| App desplegada | `precios-supermercados-sps/super-compras/` (copia de `super-compras/` de este repo) |

## Cifras en vivo

La tarjeta, el detalle y la página del caso leen al cargar:

- `portfolio-data/.../published/rpi/v3/cities.json` y el `manifest.json` de cada ciudad: productos visibles, cadenas, sucursales, sucursales al día (`source_freshness`) y hora de publicación.
- `calidad-datos/historial.csv` y `errores_confirmados.csv`: alertas en seguimiento, datos corregidos en la web y errores revisados a mano.

Si GitHub no responde, se muestran cifras verificadas fijas (24-oct-2026) y se dice que no son en vivo. Nunca se inventan cifras ni se muestran como de hoy si no lo son.

## Reglas de comunicación

1. Las cifras son mediciones de un corte, no valores permanentes; se leen en vivo o se fechan.
2. “Visible” no significa “comparable”: solo se comparan productos con identidad fuerte.
3. Los errores del súper se muestran como tales (registro público), no se esconden.
4. Si una sucursal no se capturó hoy, se dice.
5. Solo datos públicos; no se mencionan ni usan APIs privadas.

## Actualizar las capturas

Las capturas se toman de la app real (390×844 @2x para celular, 1280×800 @1.5x para escritorio), se reducen a 520 px y 1440 px de ancho y se guardan como WebP (calidad ~80). Mantener cada archivo por debajo de ~70 KB.
