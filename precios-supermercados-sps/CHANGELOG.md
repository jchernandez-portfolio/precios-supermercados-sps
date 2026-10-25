# Historial de cambios

Un renglón por PR fusionado, del más reciente al más antiguo. Las entradas sin
número son cambios directos. El detalle de cada incidente vive en
[`docs/PROJECT_STATE.md`](docs/PROJECT_STATE.md) y el de cada error de datos en
[`docs/calidad/errores-confirmados.csv`](docs/calidad/errores-confirmados.csv).

## 2026-10-25

- #46 Fotos: leer las ofertas de Tegucigalpa (llevan la sucursal en el id).
- #45 Fotos v2: producto completo, nitidez real y dos tamaños (800 y 320 px) en Cloudflare R2.

## 2026-10-24

- #44 Documentación al día: Súper Compras, captura diaria y registro de errores.
- #43 Captura diaria robusta (Colonial y Los Andes), registro de errores diario y aviso de precios atrasados.

## 2026-10-16

- #42 Corregir errores del súper con patrón claro.
- #41 Medidas mal leídas y red de seguridad para datos absurdos.
- #40 Registro de errores de datos: auditoría automática y registro confirmado (rama `calidad-datos`).
- #39 Precio por libra en Los Andes, La Colonia y Paiz.
- #38 Presentaciones coherentes en todo el catálogo.
- #37 Presentación con la regla GS1 de contenido neto.

## 2026-10-15

- #36 Precio por unidad cuando el súper lo aclara en el nombre.
- #35 Catálogo: productos pesados de Colonial también por libra.
- #34 Catálogo: productos pesados de Walmart con precio por libra.
- #33 Súper Compras: tamaño leído del nombre cuando el súper no lo publica.
- #32 Súper Compras: versiona data.js e illustration.js con el resto de la app.
- #31 Súper Compras: "1 unidad" y "por libra" iguales entre súper; frutas y verduras.
- #30 Súper Compras: tamaños en onzas con gramos al lado ("16 oz (454 g)").
- #29 Catálogo público: onzas de peso a gramos con precio por kg.
- #28 Súper Compras: elegir súper, precio más bajo, búsqueda por tipo, presentaciones y ciudades.
- #27 Súper Compras: fotos reales de producto (corrida semanal incremental).

## 2026-10-10

- #26 Súper Compras: nueva app de consumidor (celular y PC) e índice de búsqueda.
- #25 Subcategoría por nombre y marcas de Colonial.
- #24 Otras presentaciones: regla V publicada en el catálogo v3.
- #23 Retirar 11 workflows que nunca corrieron en este repositorio.

## 2026-10-09

- #22 Estado del proyecto al 2026-10-09.
- #21 Corte diario a las 05:17 y especificaciones PriceSmart el sábado 10:37.
- #20 Persistencia parcial: una cadena fallida ya no bloquea a las demás.
- #19 Disparo puntual del corte diario: Worker de Cloudflare.
- #18 Vínculos por atributos (regla A, `engine_auto`) para PriceSmart y Los Andes.
- #17 Medidas estandarizadas v2.7: multipacks, onzas por categoría y precio unitario.
- #16 Tabla de equivalencias: 18 categorías nuevas avisadas por la primera publicación.
- #15 Árbol maestro de categorías v1 y tabla de equivalencias por supermercado.

## 2026-10-03

- #14 Refresco de homologación: instala PyYAML para el producto maestro.
- #13 PriceSmart: especificaciones con navegador headless y corte temprano.
- #12 Colonial: re-verifica productos cuyo precio cambia a media captura.

## 2026-10-02

- #11 Productos sin precio y no disponibles: no bloquean la corrida y no entran a comparaciones.
- #10 PriceSmart: captura semanal de especificaciones de la ficha pública.
- #9 Producto maestro: registro maestro, enlaces y rechazos para la homologación.
- #8 Comisariato: GTIN desde el código fuente (v2.6) y auditoría de ramas.
- #7 Calidad de catálogo: parser de presentación, marca desde el nombre y tipo de producto.

## 2026-10-01

- #6 Motor de homologación probabilístico (shadow).
- #5 Homologación v2.4: GTIN de SKU Colonial, prefijos GS1 y guardas de precisión.
- #4 Reduce lecturas de Turso y elimina workflows de Sheets/BigQuery.
- #3 Colonial: acepta el markup de tarjetas del 2026-09-24.
- #1 Documentación al estado del 2026-09-30.

## Antes del 2026-09-30

El proyecto vivía en el monorepo `Portafolio` (PR #1–#475 de ese repositorio); el
historial se conservó al separarlo.
