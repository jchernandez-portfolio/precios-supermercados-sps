# Presentación del proyecto en el portafolio

Este documento define cómo presentar públicamente `precios-supermercados-sps` sin reducirlo a “un scraper” ni exagerar lo que los datos permiten afirmar.

## Identidad del proyecto

Nombre recomendado:

**Retail Price Intelligence — Compra Inteligente**

Descripción corta:

> Plataforma de inteligencia de precios que captura, valida e historiza catálogos públicos de supermercados y los transforma en comparación segura, análisis histórico y una experiencia de compra para San Pedro Sula.

El **web scraping** sigue siendo una capacidad técnica importante y demostrable, pero es la capa de adquisición, no el producto completo.

## Producto público actual

Compra Inteligente permite navegar precios publicados en dos ciudades:

- **San Pedro Sula:** La Colonia, Supermercados Colonial, Walmart, PriceSmart y Comisariato Los Andes.
- **Tegucigalpa:** La Colonia, Walmart FFAA, Walmart El Sauce, PriceSmart Florencia, Paiz Multiplaza y Paiz Próceres.

La publicación Consumer Catalog v3 verificada el **2026-09-21** contiene, como snapshot medido y no como cifra permanente:

| Métrica | San Pedro Sula | Tegucigalpa |
| --- | ---: | ---: |
| Filas visibles | 36,886 | 33,370 |
| Ofertas fuente | 39,498 | 59,786 |
| Filas comparables | 2,612 | 8,321 |
| `single_source` | 10,242 | 17,504 |
| Ofertas individuales | 24,032 | 7,545 |
| Particiones públicas | 453 | 384 |
| Máximo por partición | 250 filas | 250 filas |
| Ofertas con resumen histórico | 39,362 | 57,826 |

El payload inicial de SPS medido en ese corte es 37,203 bytes sin comprimir / 4,947 bytes gzip con dos requests iniciales. Los índices y particiones se cargan bajo demanda. La pestaña **Análisis** resume promociones, movimientos de precio y liderazgo por categoría de cada ciudad.

## Qué puede hacer el usuario

- filtrar por categoría, producto, marca y presentación;
- buscar productos;
- elegir ciudad y revisar la matriz de supermercados/sucursales;
- elegir manualmente la oferta exacta que quiere comprar;
- indicar cantidades;
- agregar varios productos por lote;
- conservar `Mi Compra` en el dispositivo;
- ver la lista agrupada por supermercado;
- detectar cambios de precio y actualizarlos sólo de forma explícita;
- ver faltantes/no disponibles sin sustitución silenciosa;
- comparar su selección con escenarios de un solo supermercado o split optimizado seguro;
- consultar contexto histórico por oferta;
- exportar la compra a CSV/PDF.

## Historia y promociones

Cuando existe evidencia suficiente, cada oferta puede mostrar:

- precio observado anterior;
- promedio 30/90 días;
- mínimo/máximo de ventana;
- posición histórica;
- promoción declarada por la fuente;
- reducción histórica observada.

Promoción declarada y reducción real histórica son señales distintas. `reported_regular_price` es referencia y no se convierte en un precio histórico inventado.

## Comparación segura

La cobertura visible no equivale a cobertura comparable.

Una oferta puede mostrarse como `individual` o `single_source` aunque no exista evidencia suficiente para compararla cross-retailer. Sólo Python autoriza equivalencias y recomendaciones; la web no hace matching por nombre, marca o presentación.

Reglas públicas:

- no afirmar equivalencia por marca + presentación;
- no mostrar ranking/“mejor precio” si la comparación está bloqueada;
- conservar empates reales;
- no convertir ausencia en precio cero;
- no esconder staleness;
- no presentar una canasta incompleta como total completo.

Metodología: [`COMPARATOR-METHODOLOGY.md`](COMPARATOR-METHODOLOGY.md).

## Contrato de Mi Compra

```text
unit_price = current_price
line_total = unit_price * quantity
retailer_subtotal = sum(line_total)
grand_total = sum(retailer_subtotal)
```

No se infieren impuestos, ISV, delivery, service fees o membership fees. Los precios pueden cambiar en tienda.

## Evidencia técnica

La plataforma conserva seis cadenas / once contextos productivos en el pipeline recurrente general, y el producto B2C público cubre cinco contextos en San Pedro Sula y seis en Tegucigalpa.

El portafolio puede enlazar:

1. la experiencia Compra Inteligente;
2. el código del proyecto;
3. evidencia versionada de captura y validación;
4. documentación de metodología.

La prueba histórica de Comisariato Los Andes permanece válida como evidencia de adquisición web, pero no debe dominar la narrativa del producto.

## Producto B2B

El proyecto también demuestra una capa **Retail Price Intelligence B2B** mediante `rpi-business-mart/v1` y activos reproducibles de Power BI.

El Business Mart incluye:

- comparación actual;
- histórico de precios;
- promociones;
- canastas;
- cobertura;
- freshness.

Power BI consume estas métricas ya calculadas por Python; no reconstruye matching ni reglas críticas en DAX.

## Narrativa recomendada

```text
Catálogos públicos
  ↓
Captura automatizada
  ↓
Validación / último dato válido
  ↓
Histórico
  ↓
Homologación conservadora
  ↓
Inteligencia de precios en Python
  ↓
Business Mart + Consumer Mart/Catalog
  ↓
Power BI + Compra Inteligente
```

El valor a comunicar es la transformación completa de información pública dispersa en un producto de datos confiable y utilizable.

## Capacidades demostradas

- Web scraping multi-fuente.
- Automatización con GitHub Actions.
- Persistencia e histórico de precios.
- Control de calidad fail-closed y last-known-good.
- Homologación conservadora de productos.
- Analítica competitiva e histórica.
- Diseño de data marts B2B/B2C.
- Power BI reproducible.
- Aplicación web responsive con estado local y exportaciones.
- Publicación estática validada por hashes y sin secretos.

## Reglas para el portafolio

1. Presentar primero el producto y el valor, luego la tecnología.
2. Usar `Retail Price Intelligence` / `Compra Inteligente` como identidad principal.
3. Mostrar web scraping como capacidad, no como límite del proyecto.
4. Cualquier cifra debe incluir o derivar de un corte aceptado; las cifras anteriores son el snapshot publicado del 2026-09-21.
5. Distinguir “visible” de “comparable”.
6. No inventar ahorro, impuestos, tiempo real ni disponibilidad.
7. No publicar secretos, RAW, cookies, tokens o colas de revisión.
8. No presentar funciones futuras como terminadas.
9. Si el último ciclo productivo falla, conservar el último corte válido en vez de reemplazarlo con datos parciales.

## Archivos coordinados

Cuando cambie el producto público, revisar conjuntamente:

- `docs/PROJECT_STATE.md`;
- `docs/RPI-PRODUCT-SPEC.md`;
- `docs/RPI-DATA-MART-DICTIONARY.md`;
- `portfolio/`;
- `b2c/`;
- README del proyecto;
- README raíz si cambia la descripción pública.
