# Errores de datos en Súper Compras

Actualizado automáticamente el 2026-11-08 a partir del catálogo publicado (San Pedro Sula: 2026-11-08T19:21:39.579312Z; Tegucigalpa: 2026-11-08T19:24:07.402269Z).

- [`errores_detectados.csv`](errores_detectados.csv): todo lo que la revisión automática encuentra hoy, una fila por producto y supermercado, con enlace al producto.
- [`errores_confirmados.csv`](errores_confirmados.csv): errores revisados a mano, de dónde vienen (súper o Súper Compras) y su estado.
- [`historial.csv`](historial.csv): cuántos errores había en cada publicación.
- [`frescura.csv`](frescura.csv): última captura aceptada de cada supermercado, día a día.
- [`fotos.csv`](fotos.csv): cobertura de fotos por ciudad y supermercado, día a día.

## Captura diaria

✅ Todos los supermercados se capturaron hoy.

| Ciudad | Supermercado | Sucursal | Última captura (UTC) | Horas | Estado |
|---|---|---|---|---:|---|
| San Pedro Sula | La Colonia | la_colonia_sps | 2026-11-08 11:26 | 7 | Al día |
| San Pedro Sula | Colonial | colonial_sps | 2026-11-08 11:26 | 7 | Al día |
| San Pedro Sula | Walmart | walmart_sps | 2026-11-08 18:57 | 0 | Al día |
| San Pedro Sula | PriceSmart | pricesmart_sps | 2026-11-08 11:18 | 8 | Al día |
| San Pedro Sula | Los Andes | comisariato_los_andes_sps | 2026-11-08 11:19 | 8 | Al día |
| Tegucigalpa | La Colonia | la_colonia_tgu | 2026-11-08 11:35 | 7 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_ffaa | 2026-11-08 19:05 | 0 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_el_sauce | 2026-11-08 19:13 | 0 | Al día |
| Tegucigalpa | PriceSmart | pricesmart_tgu | 2026-11-08 11:19 | 8 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_multiplaza | 2026-11-08 18:54 | 0 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_proceres | 2026-11-08 18:59 | 0 | Al día |

## Fotos de producto

39,988 de 67,537 productos con foto (59 %). Las fotos v2 se publican en dos tamaños; hd = detalle real para 800 px, sd = suficiente para tarjetas, low = foto chica.

| Ciudad | Supermercado | Productos | Con foto | Sin foto | % | hd | sd | low |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| San Pedro Sula | Total | 33828 | 23349 | 10479 | 69 % | 16050 | 6234 | 127 |
| San Pedro Sula | Colonial | 8143 | 6825 | 1318 | 84 % |  |  |  |
| San Pedro Sula | Los Andes | 6928 | 6072 | 856 | 88 % |  |  |  |
| San Pedro Sula | La Colonia | 7129 | 6192 | 937 | 87 % |  |  |  |
| San Pedro Sula | PriceSmart | 2734 | 1296 | 1438 | 47 % |  |  |  |
| San Pedro Sula | Walmart | 15270 | 9329 | 5941 | 61 % |  |  |  |
| Tegucigalpa | Total | 33709 | 16639 | 17070 | 49 % | 14671 | 1906 | 1 |
| Tegucigalpa | La Colonia | 7614 | 6500 | 1114 | 85 % |  |  |  |
| Tegucigalpa | Paiz | 10361 | 8336 | 2025 | 80 % |  |  |  |
| Tegucigalpa | PriceSmart | 2844 | 1267 | 1577 | 45 % |  |  |  |
| Tegucigalpa | Walmart | 23663 | 10515 | 13148 | 44 % |  |  |  |

## San Pedro Sula

| Problema | Colonial | La Colonia | Los Andes | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 0 | 0 | 8 | 6 | 14 |
| Precio por unidad: probable error | 7 | 7 | 8 | 7 | 27 | 56 |
| Precio por unidad: revisar | 23 | 23 | 17 | 3 | 28 | 94 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 0 | 1 | 1 |
| Sin presentación | 95 | 47 | 125 | 22 | 351 | 640 |
| Corregido en la web | 7 | 5 | 1 | 5 | 4 | 22 |

## Tegucigalpa

| Problema | La Colonia | Paiz | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 2 | 7 | 12 | 21 |
| Precio por unidad: probable error | 10 | 43 | 7 | 41 | 101 |
| Precio por unidad: revisar | 23 | 22 | 4 | 33 | 82 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 2 | 2 |
| Distinto entre ciudades | 10 | 0 | 0 | 0 | 10 |
| Sin presentación | 30 | 41 | 24 | 597 | 692 |
| Corregido en la web | 6 | 6 | 3 | 6 | 21 |

## Qué significa cada problema

- **Peso o volumen imposible** (gravedad alta): La medida que se muestra no puede ser real (más de 50 kg o 60 L en un producto de súper): el súper escribió mal la medida o un código se leyó como peso.
- **Precio por unidad: probable error** (gravedad alta): El precio por kg/L es 100 veces mayor o 20 veces menor que el de su tipo de producto: el tamaño o el precio están mal publicados.
- **Precio por unidad: revisar** (gravedad media): El precio por kg/L es entre 20 y 100 veces mayor que el de su tipo: puede ser error o un producto muy pequeño (té en sobres, repuestos, pegamento).
- **Medida mal escrita por el súper** (gravedad media): El nombre trae un espacio dentro del número ("40 0Ml") y no se puede leer la medida.
- **Distinto entre ciudades** (gravedad media): El mismo producto del mismo súper sale por libra en una ciudad y no en la otra.
- **Sin presentación** (gravedad baja): El nombre termina en un número sin unidad ("Ensure 237"): no se sabe qué mide.
- **Corregido en la web** (gravedad info): El súper publica mal la medida ("1100 Lt", "397 Oz" que son gramos) y la web la corrige con un patrón seguro; sigue mal en el súper.
