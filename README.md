# Errores de datos en Súper Compras

Actualizado automáticamente el 2026-11-06 a partir del catálogo publicado (San Pedro Sula: 2026-11-05T11:48:15.528313Z; Tegucigalpa: 2026-11-05T11:50:44.155709Z).

- [`errores_detectados.csv`](errores_detectados.csv): todo lo que la revisión automática encuentra hoy, una fila por producto y supermercado, con enlace al producto.
- [`errores_confirmados.csv`](errores_confirmados.csv): errores revisados a mano, de dónde vienen (súper o Súper Compras) y su estado.
- [`historial.csv`](historial.csv): cuántos errores había en cada publicación.
- [`frescura.csv`](frescura.csv): última captura aceptada de cada supermercado, día a día.
- [`fotos.csv`](fotos.csv): cobertura de fotos por ciudad y supermercado, día a día.

## Captura diaria

✅ Todos los supermercados se capturaron hoy.

| Ciudad | Supermercado | Sucursal | Última captura (UTC) | Horas | Estado |
|---|---|---|---|---:|---|
| San Pedro Sula | La Colonia | la_colonia_sps | 2026-11-05 11:26 | 0 | Al día |
| San Pedro Sula | Colonial | colonial_sps | 2026-11-05 11:26 | 0 | Al día |
| San Pedro Sula | Walmart | walmart_sps | 2026-11-05 11:25 | 0 | Al día |
| San Pedro Sula | PriceSmart | pricesmart_sps | 2026-11-05 11:18 | 0 | Al día |
| San Pedro Sula | Los Andes | comisariato_los_andes_sps | 2026-11-05 11:19 | 0 | Al día |
| Tegucigalpa | La Colonia | la_colonia_tgu | 2026-11-05 11:35 | 0 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_ffaa | 2026-11-05 11:32 | 0 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_el_sauce | 2026-11-05 11:39 | 0 | Al día |
| Tegucigalpa | PriceSmart | pricesmart_tgu | 2026-11-05 11:19 | 0 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_multiplaza | 2026-11-05 11:22 | 0 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_proceres | 2026-11-05 11:26 | 0 | Al día |

## Fotos de producto

39,711 de 67,298 productos con foto (59 %). Las fotos v2 se publican en dos tamaños; hd = detalle real para 800 px, sd = suficiente para tarjetas, low = foto chica.

| Ciudad | Supermercado | Productos | Con foto | Sin foto | % | hd | sd | low |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| San Pedro Sula | Total | 33706 | 23207 | 10499 | 69 % | 15947 | 6184 | 127 |
| San Pedro Sula | Colonial | 8440 | 7027 | 1413 | 83 % |  |  |  |
| San Pedro Sula | Los Andes | 6777 | 6005 | 772 | 89 % |  |  |  |
| San Pedro Sula | La Colonia | 7007 | 6067 | 940 | 87 % |  |  |  |
| San Pedro Sula | PriceSmart | 2695 | 1269 | 1426 | 47 % |  |  |  |
| San Pedro Sula | Walmart | 15195 | 9234 | 5961 | 61 % |  |  |  |
| Tegucigalpa | Total | 33592 | 16504 | 17088 | 49 % | 14544 | 1893 | 2 |
| Tegucigalpa | La Colonia | 7497 | 6372 | 1125 | 85 % |  |  |  |
| Tegucigalpa | Paiz | 10321 | 8290 | 2031 | 80 % |  |  |  |
| Tegucigalpa | PriceSmart | 2742 | 1241 | 1501 | 45 % |  |  |  |
| Tegucigalpa | Walmart | 23750 | 10520 | 13230 | 44 % |  |  |  |

## San Pedro Sula

| Problema | Colonial | La Colonia | Los Andes | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 0 | 0 | 8 | 6 | 14 |
| Precio por unidad: probable error | 7 | 7 | 8 | 7 | 26 | 55 |
| Precio por unidad: revisar | 23 | 22 | 17 | 4 | 29 | 95 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 0 | 1 | 1 |
| Sin presentación | 90 | 43 | 111 | 21 | 348 | 613 |
| Corregido en la web | 7 | 5 | 1 | 6 | 4 | 23 |

## Tegucigalpa

| Problema | La Colonia | Paiz | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 2 | 7 | 12 | 21 |
| Precio por unidad: probable error | 14 | 44 | 8 | 41 | 107 |
| Precio por unidad: revisar | 23 | 20 | 4 | 34 | 81 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 2 | 2 |
| Distinto entre ciudades | 11 | 0 | 0 | 1 | 12 |
| Sin presentación | 30 | 39 | 24 | 602 | 695 |
| Corregido en la web | 6 | 6 | 3 | 6 | 21 |

## Qué significa cada problema

- **Peso o volumen imposible** (gravedad alta): La medida que se muestra no puede ser real (más de 50 kg o 60 L en un producto de súper): el súper escribió mal la medida o un código se leyó como peso.
- **Precio por unidad: probable error** (gravedad alta): El precio por kg/L es 100 veces mayor o 20 veces menor que el de su tipo de producto: el tamaño o el precio están mal publicados.
- **Precio por unidad: revisar** (gravedad media): El precio por kg/L es entre 20 y 100 veces mayor que el de su tipo: puede ser error o un producto muy pequeño (té en sobres, repuestos, pegamento).
- **Medida mal escrita por el súper** (gravedad media): El nombre trae un espacio dentro del número ("40 0Ml") y no se puede leer la medida.
- **Distinto entre ciudades** (gravedad media): El mismo producto del mismo súper sale por libra en una ciudad y no en la otra.
- **Sin presentación** (gravedad baja): El nombre termina en un número sin unidad ("Ensure 237"): no se sabe qué mide.
- **Corregido en la web** (gravedad info): El súper publica mal la medida ("1100 Lt", "397 Oz" que son gramos) y la web la corrige con un patrón seguro; sigue mal en el súper.
