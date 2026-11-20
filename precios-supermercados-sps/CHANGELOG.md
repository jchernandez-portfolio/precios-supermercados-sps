# Historial de cambios

Un renglón por PR fusionado, del más reciente al más antiguo. Las entradas sin
número son cambios directos. El detalle de cada incidente vive en
[`docs/PROJECT_STATE.md`](docs/PROJECT_STATE.md) y el de cada error de datos en
[`docs/calidad/errores-confirmados.csv`](docs/calidad/errores-confirmados.csv).

## 2026-11-20

- Walmart: si las subcategorías (`category-2`) de una categoría partida no suman su total (un producto sin subcategoría; «ropa y zapatería» en SPS falló cuatro intentos el 19-nov con `category2_total_mismatch`), la captura lee la categoría completa dos veces y exige la misma membresía exacta (`category2_facet_gap_parent_restart`); si no cabe en la ventana de búsqueda sigue fallando cerrado.

## 2026-11-18

- Búsqueda por GTIN con Open Food Facts (gratis, sin llave) como fuente por defecto: aprueba sola (`engine:off_auto`) sólo si el contenido neto del código exacto coincide con algún súper. Gemini con búsqueda queda opcional: su plan gratuito no incluye Google Search.
- Búsqueda directa por GTIN (Gemini con Google Search, sin leer fotos): aprueba sola sólo con una fuente independiente (no un súper de Honduras) que cita el número y coincide con algún súper; re-audita lo resuelto con fotos y devuelve a la cola como «Corrección de IA» lo que la web contradice. La lectura de fotos queda apagada por defecto.
- Motor de IA con Claude (API de Anthropic): lee las fotos y, para lo que siga en la cola, busca el peso oficial en la web (SDK `anthropic`, `web_search`); aprueba solo (`engine:claude_auto`) lo que una fuente citada confirma y coincide con algún súper. El README principal muestra insignias, estado y diagramas.
- Cola de tamaños: las ofertas de «Member's Selection» (marca blanca de PriceSmart) no entran a la cola; si sin ellas no hay conflicto, el código no se lista.
- README: insignias, estado por pieza (🟢🟡🔴), diagramas Mermaid (flujo, secuencia de revisión y modelo de datos) y tablas de la Ley de la Mayoría y la búsqueda con Grounding.
- #122 Búsqueda web: el resumen `web` de la cola anota el error de cada modelo de Gemini (`errores_por_modelo`).
- #121 IA de tamaños sólo con Gemini y búsqueda web del peso oficial (Gemini con Google Search) sólo para la cola; el panel muestra «🌐 Sugerencia Web» con su fuente y nunca la guarda sola.
- #120 Panel de tamaños: contador en cada pestaña, «Resueltos por IA» sólo con decisiones del motor y vuelta al inicio al cambiar de pestaña.
- #119 Súper Compras: se quita el modo oscuro; queda sólo la paleta Lima clara.
- #118 Cola de tamaños: oculta lo ya enviado, reintenta el índice de fotos (429/5xx) y reusa las fotos de la cola anterior; el panel de decisiones regenera la cola.

## 2026-11-16

- Historial de 30 días: el catálogo v3 publica `history_30d` (puntos de cambio `[días_atrás, "precio"]`, sólo en ofertas que cambiaron de precio en 30 días, desde los mismos periodos de `historical_summary`) y la ficha dibuja una sparkline SVG de la oferta más barata. Crecimiento medido: +1.6–2.0 % gzip del catálogo SPS.
- PriceSmart: las variantes (SKU) sin identidad comparable con el mismo `pid`, ubicación y título salen en una sola tarjeta con la oferta más barata y el campo aditivo `variants` (la ficha dice «N variantes con este nombre»); títulos distintos siguen separados.

## 2026-11-14

- Motor de identidad v2.10 "corrige con evidencia": las decisiones de tamaño traen la lectura de la foto de cada súper (`evidence`, tabla derivada `size_offer_evidence`) y `apply: "evidence" | "all"`; con ≥ 2 fotos que confirman y son mayoría el tamaño oficial se aplica a todas las ofertas del GTIN, con una sola se corrigen las de foto propia y un empate no corrige nada. El catálogo publica `published_as` (lo que publica el súper) y `other_presentation`; la medición de identidad lista las fichas que cambian (`evidencia-grupos.csv`).

## 2026-11-13

- #85 Motor de identidad v2.9: g ≈ ml (≤ 5 %) en lácteos, jugos y salsas y peso sobre conteo (conteo 1, mismo pack o título con el conteo) en grupos GTIN, con "nunca empeorar"; el tamaño oficial de `size_decisions` compara g con ml en esas categorías.

## 2026-11-12

- IA de tamaños: lectura por oferta, auto-resolución cuando la foto confirma el tamaño de la mayoría (engine:gemini_auto), fotos del sistema en el panel y pestaña "Resueltos por IA".
- Panel de tamaños: sugerencia de IA de visión (Gemini) por código, "🤖 No legible" cuando la foto no sirve.
- Panel de tamaños: código visible con "Copiar", búsquedas de 14/13/12 dígitos marcando la forma de cada súper y tamaños de la cola redondeados a 2 decimales.
- Panel de revisión de tamaños (`#/admin/revision`): tabla `size_decisions`, workflow que guarda las decisiones del panel y relanza el refresco, cola pública `revision-tamanos/cola.json` y motor que agrupa sólo las ofertas con el tamaño oficial.
- #66 Prueba de enriquecimiento con Open Food Facts (sólo lectura).
- Regla de onzas en grupos GTIN (motor v2.8): 8 oz = 237 ml / 24 oz = 680 g con el mismo código, "nunca empeorar" y precio por unidad para la onza convertida.
- #64 Medición (sólo lectura): simular la regla de onzas.
- #63 Tegucigalpa: una tarjeta por producto con todas sus sucursales.

## 2026-11-11

- #62 Paso 0: medición de reglas de identidad (sólo lectura).
- #61 Auditoría de identidad de producto (sólo lectura).

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
