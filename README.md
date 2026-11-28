# Errores de datos en Súper Compras

Actualizado automáticamente el 2026-11-28 a partir del catálogo publicado (San Pedro Sula: 2026-11-27T11:46:36.490814Z; Tegucigalpa: 2026-11-27T11:48:34.358630Z).

- [`errores_detectados.csv`](errores_detectados.csv): todo lo que la revisión automática encuentra hoy, una fila por producto y supermercado, con enlace al producto.
- [`errores_confirmados.csv`](errores_confirmados.csv): errores revisados a mano, de dónde vienen (súper o Súper Compras) y su estado.
- [`historial.csv`](historial.csv): cuántos errores había en cada publicación.
- [`frescura.csv`](frescura.csv): última captura aceptada de cada supermercado, día a día.
- [`fotos.csv`](fotos.csv): cobertura de fotos por ciudad y supermercado, día a día.

## Captura diaria

✅ Todos los supermercados se capturaron hoy.

| Ciudad | Supermercado | Sucursal | Última captura (UTC) | Horas | Estado |
|---|---|---|---|---:|---|
| San Pedro Sula | La Colonia | la_colonia_sps | 2026-11-27 11:28 | 0 | Al día |
| San Pedro Sula | Colonial | colonial_sps | 2026-11-27 11:25 | 0 | Al día |
| San Pedro Sula | Walmart | walmart_sps | 2026-11-27 11:25 | 0 | Al día |
| San Pedro Sula | PriceSmart | pricesmart_sps | 2026-11-27 11:17 | 0 | Al día |
| San Pedro Sula | Los Andes | comisariato_los_andes_sps | 2026-11-27 11:18 | 0 | Al día |
| Tegucigalpa | La Colonia | la_colonia_tgu | 2026-11-27 11:39 | 0 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_ffaa | 2026-11-27 11:32 | 0 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_el_sauce | 2026-11-27 11:39 | 0 | Al día |
| Tegucigalpa | PriceSmart | pricesmart_tgu | 2026-11-27 11:18 | 0 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_multiplaza | 2026-11-27 11:22 | 0 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_proceres | 2026-11-27 11:27 | 0 | Al día |

## Fotos de producto

52,122 de 56,399 productos con foto (92 %). Las fotos v2 se publican en dos tamaños; hd = detalle real para 800 px, sd = suficiente para tarjetas, low = foto chica.

| Ciudad | Supermercado | Productos | Con foto | Sin foto | % | hd | sd | low |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| San Pedro Sula | Total | 31288 | 28601 | 2687 | 91 % | 21563 | 6828 | 131 |
| San Pedro Sula | Colonial | 7904 | 6788 | 1116 | 86 % |  |  |  |
| San Pedro Sula | Los Andes | 6376 | 6125 | 251 | 96 % |  |  |  |
| San Pedro Sula | La Colonia | 7201 | 7121 | 80 | 99 % |  |  |  |
| San Pedro Sula | PriceSmart | 1834 | 1616 | 218 | 88 % |  |  |  |
| San Pedro Sula | Walmart | 14648 | 13614 | 1034 | 93 % |  |  |  |
| Tegucigalpa | Total | 25111 | 23521 | 1590 | 94 % | 20810 | 2645 | 4 |
| Tegucigalpa | La Colonia | 7668 | 7558 | 110 | 99 % |  |  |  |
| Tegucigalpa | Paiz | 9438 | 9209 | 229 | 98 % |  |  |  |
| Tegucigalpa | PriceSmart | 1866 | 1649 | 217 | 88 % |  |  |  |
| Tegucigalpa | Walmart | 16841 | 15632 | 1209 | 93 % |  |  |  |

## San Pedro Sula

| Problema | Colonial | La Colonia | Los Andes | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 0 | 0 | 7 | 13 | 20 |
| Precio por unidad: probable error | 7 | 8 | 6 | 7 | 23 | 51 |
| Precio por unidad: revisar | 21 | 23 | 16 | 3 | 25 | 88 |
| Sin presentación | 86 | 49 | 114 | 22 | 359 | 630 |
| Corregido en la web | 7 | 5 | 1 | 3 | 6 | 22 |

## Tegucigalpa

| Problema | La Colonia | Paiz | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 1 | 7 | 13 | 21 |
| Precio por unidad: probable error | 15 | 28 | 8 | 32 | 83 |
| Precio por unidad: revisar | 22 | 17 | 2 | 22 | 63 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 1 | 1 |
| Sin presentación | 29 | 39 | 24 | 356 | 448 |
| Corregido en la web | 5 | 27 | 3 | 25 | 60 |

## Qué significa cada problema

- **Peso o volumen imposible** (gravedad alta): La medida que se muestra no puede ser real (más de 50 kg o 60 L en un producto de súper): el súper escribió mal la medida o un código se leyó como peso.
- **Precio por unidad: probable error** (gravedad alta): El precio por kg/L es 100 veces mayor o 20 veces menor que el de su tipo de producto: el tamaño o el precio están mal publicados.
- **Precio por unidad: revisar** (gravedad media): El precio por kg/L es entre 20 y 100 veces mayor que el de su tipo: puede ser error o un producto muy pequeño (té en sobres, repuestos, pegamento).
- **Medida mal escrita por el súper** (gravedad media): El nombre trae un espacio dentro del número ("40 0Ml") y no se puede leer la medida.
- **Distinto entre ciudades** (gravedad media): El mismo producto del mismo súper sale por libra en una ciudad y no en la otra.
- **Sin presentación** (gravedad baja): El nombre termina en un número sin unidad ("Ensure 237"): no se sabe qué mide.
- **Corregido en la web** (gravedad info): El súper publica mal la medida ("1100 Lt", "397 Oz" que son gramos) y la web la corrige con un patrón seguro; sigue mal en el súper.
