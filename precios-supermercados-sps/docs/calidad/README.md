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
  `Corregido en la web`, `Oculto en la web`, `Sin cambio`), con el PR que lo corrigió. Se actualiza en cada PR que encuentra o
  corrige un error.

La revisión automática (`scripts/auditar_calidad_catalogo.mjs`) usa la misma lógica de
presentación que la app (`super-compras/data.js`) y busca: pesos o volúmenes
imposibles, precio por kg/L fuera de rango para su tipo, medidas mal escritas por el
súper, productos que salen distinto entre ciudades y productos sin presentación.

La web esconde el tamaño y el precio por unidad de los productos con un tamaño imposible
o un precio por kg/L 100 veces mayor o 20 veces menor que el de su tipo (`hideImplausible`
en `super-compras/data.js`); la auditoría los sigue listando con el dato original.

Antes de esconder, la web intenta corregir errores del súper con un patrón claro ("1100 Lt" =
1.1 L, "397 Oz" = 397 g, "3 78lt" = 3.78 L, "48 Unidades / 960 g" = 960 g en total) y sólo acepta la
corrección si el precio por kg/L queda en el rango normal de su tipo. Esos casos salen en la
auditoría como "Corregido en la web" para reportarlos al súper.
