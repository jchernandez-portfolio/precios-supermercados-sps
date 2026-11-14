# Errores de datos en Súper Compras

Actualizado automáticamente el 2026-11-14 a partir del catálogo publicado (San Pedro Sula: 2026-11-14T02:30:23.772992Z; Tegucigalpa: 2026-11-14T02:33:08.868163Z).

- [`errores_detectados.csv`](errores_detectados.csv): todo lo que la revisión automática encuentra hoy, una fila por producto y supermercado, con enlace al producto.
- [`errores_confirmados.csv`](errores_confirmados.csv): errores revisados a mano, de dónde vienen (súper o Súper Compras) y su estado.
- [`historial.csv`](historial.csv): cuántos errores había en cada publicación.
- [`frescura.csv`](frescura.csv): última captura aceptada de cada supermercado, día a día.
- [`fotos.csv`](fotos.csv): cobertura de fotos por ciudad y supermercado, día a día.

## Captura diaria

✅ Todos los supermercados se capturaron hoy.

| Ciudad | Supermercado | Sucursal | Última captura (UTC) | Horas | Estado |
|---|---|---|---|---:|---|
| San Pedro Sula | La Colonia | la_colonia_sps | 2026-11-12 11:27 | 39 | Al día |
| San Pedro Sula | Colonial | colonial_sps | 2026-11-13 11:26 | 15 | Al día |
| San Pedro Sula | Walmart | walmart_sps | 2026-11-13 11:26 | 15 | Al día |
| San Pedro Sula | PriceSmart | pricesmart_sps | 2026-11-13 11:18 | 15 | Al día |
| San Pedro Sula | Los Andes | comisariato_los_andes_sps | 2026-11-13 11:19 | 15 | Al día |
| Tegucigalpa | La Colonia | la_colonia_tgu | 2026-11-12 11:37 | 38 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_ffaa | 2026-11-13 11:34 | 14 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_el_sauce | 2026-11-13 11:42 | 14 | Al día |
| Tegucigalpa | PriceSmart | pricesmart_tgu | 2026-11-13 11:19 | 15 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_multiplaza | 2026-11-13 11:22 | 15 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_proceres | 2026-11-13 11:26 | 15 | Al día |

## Fotos de producto

55,033 de 61,269 productos con foto (90 %). Las fotos v2 se publican en dos tamaños; hd = detalle real para 800 px, sd = suficiente para tarjetas, low = foto chica.

| Ciudad | Supermercado | Productos | Con foto | Sin foto | % | hd | sd | low |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| San Pedro Sula | Total | 34193 | 30192 | 4001 | 88 % | 22859 | 7102 | 130 |
| San Pedro Sula | Colonial | 8486 | 7202 | 1284 | 85 % |  |  |  |
| San Pedro Sula | Los Andes | 6944 | 6250 | 694 | 90 % |  |  |  |
| San Pedro Sula | La Colonia | 7143 | 7097 | 46 | 99 % |  |  |  |
| San Pedro Sula | PriceSmart | 2795 | 1723 | 1072 | 62 % |  |  |  |
| San Pedro Sula | Walmart | 15598 | 14688 | 910 | 94 % |  |  |  |
| Tegucigalpa | Total | 27076 | 24841 | 2235 | 92 % | 21976 | 2780 | 4 |
| Tegucigalpa | La Colonia | 7614 | 7568 | 46 | 99 % |  |  |  |
| Tegucigalpa | Paiz | 9803 | 9653 | 150 | 98 % |  |  |  |
| Tegucigalpa | PriceSmart | 2844 | 1743 | 1101 | 61 % |  |  |  |
| Tegucigalpa | Walmart | 17797 | 16745 | 1052 | 94 % |  |  |  |

## San Pedro Sula

| Problema | Colonial | La Colonia | Los Andes | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 0 | 0 | 6 | 12 | 18 |
| Precio por unidad: probable error | 8 | 9 | 7 | 7 | 26 | 57 |
| Precio por unidad: revisar | 24 | 25 | 17 | 3 | 28 | 97 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 0 | 1 | 1 |
| Sin presentación | 94 | 50 | 124 | 23 | 363 | 654 |
| Corregido en la web | 7 | 5 | 1 | 8 | 5 | 26 |

## Tegucigalpa

| Problema | La Colonia | Paiz | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 2 | 7 | 15 | 24 |
| Precio por unidad: probable error | 13 | 28 | 7 | 30 | 78 |
| Precio por unidad: revisar | 23 | 17 | 3 | 22 | 65 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 1 | 1 |
| Sin presentación | 30 | 41 | 24 | 367 | 462 |
| Corregido en la web | 6 | 5 | 2 | 5 | 18 |

## Qué significa cada problema

- **Peso o volumen imposible** (gravedad alta): La medida que se muestra no puede ser real (más de 50 kg o 60 L en un producto de súper): el súper escribió mal la medida o un código se leyó como peso.
- **Precio por unidad: probable error** (gravedad alta): El precio por kg/L es 100 veces mayor o 20 veces menor que el de su tipo de producto: el tamaño o el precio están mal publicados.
- **Precio por unidad: revisar** (gravedad media): El precio por kg/L es entre 20 y 100 veces mayor que el de su tipo: puede ser error o un producto muy pequeño (té en sobres, repuestos, pegamento).
- **Medida mal escrita por el súper** (gravedad media): El nombre trae un espacio dentro del número ("40 0Ml") y no se puede leer la medida.
- **Distinto entre ciudades** (gravedad media): El mismo producto del mismo súper sale por libra en una ciudad y no en la otra.
- **Sin presentación** (gravedad baja): El nombre termina en un número sin unidad ("Ensure 237"): no se sabe qué mide.
- **Corregido en la web** (gravedad info): El súper publica mal la medida ("1100 Lt", "397 Oz" que son gramos) y la web la corrige con un patrón seguro; sigue mal en el súper.
