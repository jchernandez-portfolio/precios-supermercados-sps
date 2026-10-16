# Calidad de datos de Súper Compras

Registro de lo que está mal o incompleto en los datos que muestra Súper Compras, por
ciudad y supermercado.

- **Rama pública [`calidad-datos`](https://github.com/jchernandez-portfolio/precios-supermercados-sps/tree/calidad-datos)**:
  se actualiza sola después de cada publicación del catálogo
  (workflow "Precios SPS - Auditoría de calidad de datos").
  - `README.md`: resumen por ciudad, supermercado y problema.
  - `errores_detectados.csv`: todo lo que la revisión automática encuentra hoy, con
    enlace al producto y la fecha en que apareció por primera vez (`detectado_desde`).
  - `errores_confirmados.csv`: copia de este registro manual.
  - `historial.csv`: cuántos errores había en cada publicación.
- **[`errores-confirmados.csv`](errores-confirmados.csv)** (aquí, en `main`): errores
  revisados a mano, de dónde vienen (`Súper` = el supermercado lo publica mal;
  `Súper Compras` = lo leemos o mostramos mal) y su estado (`Pendiente`, `Corregido`,
  `Sin cambio`), con el PR que lo corrigió. Se actualiza en cada PR que encuentra o
  corrige un error.

La revisión automática (`scripts/auditar_calidad_catalogo.mjs`) usa la misma lógica de
presentación que la app (`super-compras/data.js`) y busca: pesos o volúmenes
imposibles, precio por kg/L fuera de rango para su tipo, medidas mal escritas por el
súper, productos que salen distinto entre ciudades y productos sin presentación.
