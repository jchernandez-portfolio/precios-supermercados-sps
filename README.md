# Errores de datos en Súper Compras

Actualizado automáticamente el 2026-11-14 a partir del catálogo publicado (San Pedro Sula: 2026-11-14T16:32:18.034862Z; Tegucigalpa: 2026-11-14T16:34:20.878125Z).

- [`errores_detectados.csv`](errores_detectados.csv): todo lo que la revisión automática encuentra hoy, una fila por producto y supermercado, con enlace al producto.
- [`errores_confirmados.csv`](errores_confirmados.csv): errores revisados a mano, de dónde vienen (súper o Súper Compras) y su estado.
- [`historial.csv`](historial.csv): cuántos errores había en cada publicación.
- [`frescura.csv`](frescura.csv): última captura aceptada de cada supermercado, día a día.
- [`fotos.csv`](fotos.csv): cobertura de fotos por ciudad y supermercado, día a día.

## Captura diaria

✅ Todos los supermercados se capturaron hoy.

| Ciudad | Supermercado | Sucursal | Última captura (UTC) | Horas | Estado |
|---|---|---|---|---:|---|
| San Pedro Sula | La Colonia | la_colonia_sps | 2026-11-14 11:26 | 5 | Al día |
| San Pedro Sula | Colonial | colonial_sps | 2026-11-14 15:49 | 0 | Al día |
| San Pedro Sula | Walmart | walmart_sps | 2026-11-14 16:09 | 0 | Al día |
| San Pedro Sula | PriceSmart | pricesmart_sps | 2026-11-14 11:17 | 5 | Al día |
| San Pedro Sula | Los Andes | comisariato_los_andes_sps | 2026-11-14 11:18 | 5 | Al día |
| Tegucigalpa | La Colonia | la_colonia_tgu | 2026-11-14 11:36 | 4 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_ffaa | 2026-11-14 16:17 | 0 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_el_sauce | 2026-11-14 16:24 | 0 | Al día |
| Tegucigalpa | PriceSmart | pricesmart_tgu | 2026-11-14 11:18 | 5 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_multiplaza | 2026-11-14 15:46 | 0 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_proceres | 2026-11-14 15:51 | 0 | Al día |

## Fotos de producto

54,993 de 61,327 productos con foto (90 %). Las fotos v2 se publican en dos tamaños; hd = detalle real para 800 px, sd = suficiente para tarjetas, low = foto chica.

| Ciudad | Supermercado | Productos | Con foto | Sin foto | % | hd | sd | low |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| San Pedro Sula | Total | 34208 | 30197 | 4011 | 88 % | 22870 | 7095 | 130 |
| San Pedro Sula | Colonial | 8493 | 7204 | 1289 | 85 % |  |  |  |
| San Pedro Sula | Los Andes | 6944 | 6252 | 692 | 90 % |  |  |  |
| San Pedro Sula | La Colonia | 7250 | 7204 | 46 | 99 % |  |  |  |
| San Pedro Sula | PriceSmart | 2791 | 1734 | 1057 | 62 % |  |  |  |
| San Pedro Sula | Walmart | 15654 | 14723 | 931 | 94 % |  |  |  |
| Tegucigalpa | Total | 27119 | 24796 | 2323 | 91 % | 21951 | 2760 | 4 |
| Tegucigalpa | La Colonia | 7625 | 7578 | 47 | 99 % |  |  |  |
| Tegucigalpa | Paiz | 9825 | 9658 | 167 | 98 % |  |  |  |
| Tegucigalpa | PriceSmart | 2917 | 1761 | 1156 | 60 % |  |  |  |
| Tegucigalpa | Walmart | 17808 | 16732 | 1076 | 94 % |  |  |  |

## San Pedro Sula

| Problema | Colonial | La Colonia | Los Andes | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 0 | 0 | 7 | 12 | 19 |
| Precio por unidad: probable error | 8 | 7 | 7 | 7 | 26 | 55 |
| Precio por unidad: revisar | 24 | 30 | 18 | 6 | 33 | 111 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 0 | 1 | 1 |
| Sin presentación | 90 | 47 | 122 | 23 | 363 | 645 |
| Corregido en la web | 7 | 4 | 1 | 8 | 5 | 25 |

## Tegucigalpa

| Problema | La Colonia | Paiz | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 2 | 8 | 15 | 25 |
| Precio por unidad: probable error | 13 | 28 | 7 | 30 | 78 |
| Precio por unidad: revisar | 23 | 16 | 3 | 21 | 63 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 1 | 1 |
| Sin presentación | 29 | 41 | 24 | 366 | 460 |
| Corregido en la web | 5 | 6 | 3 | 6 | 20 |

## Qué significa cada problema

- **Peso o volumen imposible** (gravedad alta): La medida que se muestra no puede ser real (más de 50 kg o 60 L en un producto de súper): el súper escribió mal la medida o un código se leyó como peso.
- **Precio por unidad: probable error** (gravedad alta): El precio por kg/L es 100 veces mayor o 20 veces menor que el de su tipo de producto: el tamaño o el precio están mal publicados.
- **Precio por unidad: revisar** (gravedad media): El precio por kg/L es entre 20 y 100 veces mayor que el de su tipo: puede ser error o un producto muy pequeño (té en sobres, repuestos, pegamento).
- **Medida mal escrita por el súper** (gravedad media): El nombre trae un espacio dentro del número ("40 0Ml") y no se puede leer la medida.
- **Distinto entre ciudades** (gravedad media): El mismo producto del mismo súper sale por libra en una ciudad y no en la otra.
- **Sin presentación** (gravedad baja): El nombre termina en un número sin unidad ("Ensure 237"): no se sabe qué mide.
- **Corregido en la web** (gravedad info): El súper publica mal la medida ("1100 Lt", "397 Oz" que son gramos) y la web la corrige con un patrón seguro; sigue mal en el súper.
