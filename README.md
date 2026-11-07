# Errores de datos en Súper Compras

Actualizado automáticamente el 2026-11-07 a partir del catálogo publicado (San Pedro Sula: 2026-11-06T11:48:36.716458Z; Tegucigalpa: 2026-11-06T11:50:46.618018Z).

- [`errores_detectados.csv`](errores_detectados.csv): todo lo que la revisión automática encuentra hoy, una fila por producto y supermercado, con enlace al producto.
- [`errores_confirmados.csv`](errores_confirmados.csv): errores revisados a mano, de dónde vienen (súper o Súper Compras) y su estado.
- [`historial.csv`](historial.csv): cuántos errores había en cada publicación.
- [`frescura.csv`](frescura.csv): última captura aceptada de cada supermercado, día a día.
- [`fotos.csv`](fotos.csv): cobertura de fotos por ciudad y supermercado, día a día.

## Captura diaria

✅ Todos los supermercados se capturaron hoy.

| Ciudad | Supermercado | Sucursal | Última captura (UTC) | Horas | Estado |
|---|---|---|---|---:|---|
| San Pedro Sula | La Colonia | la_colonia_sps | 2026-11-06 11:26 | 0 | Al día |
| San Pedro Sula | Colonial | colonial_sps | 2026-11-06 11:26 | 0 | Al día |
| San Pedro Sula | Walmart | walmart_sps | 2026-11-06 11:25 | 0 | Al día |
| San Pedro Sula | PriceSmart | pricesmart_sps | 2026-11-06 11:18 | 0 | Al día |
| San Pedro Sula | Los Andes | comisariato_los_andes_sps | 2026-11-06 11:19 | 0 | Al día |
| Tegucigalpa | La Colonia | la_colonia_tgu | 2026-11-06 11:36 | 0 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_ffaa | 2026-11-06 11:33 | 0 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_el_sauce | 2026-11-06 11:41 | 0 | Al día |
| Tegucigalpa | PriceSmart | pricesmart_tgu | 2026-11-06 11:19 | 0 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_multiplaza | 2026-11-06 11:22 | 0 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_proceres | 2026-11-06 11:26 | 0 | Al día |

## Fotos de producto

40,130 de 67,716 productos con foto (59 %). Las fotos v2 se publican en dos tamaños; hd = detalle real para 800 px, sd = suficiente para tarjetas, low = foto chica.

| Ciudad | Supermercado | Productos | Con foto | Sin foto | % | hd | sd | low |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| San Pedro Sula | Total | 33951 | 23448 | 10503 | 69 % | 16143 | 6232 | 127 |
| San Pedro Sula | Colonial | 8465 | 7079 | 1386 | 84 % |  |  |  |
| San Pedro Sula | Los Andes | 6842 | 6031 | 811 | 88 % |  |  |  |
| San Pedro Sula | La Colonia | 7134 | 6182 | 952 | 87 % |  |  |  |
| San Pedro Sula | PriceSmart | 2713 | 1285 | 1428 | 47 % |  |  |  |
| San Pedro Sula | Walmart | 15256 | 9317 | 5939 | 61 % |  |  |  |
| Tegucigalpa | Total | 33765 | 16682 | 17083 | 49 % | 14708 | 1908 | 2 |
| Tegucigalpa | La Colonia | 7643 | 6505 | 1138 | 85 % |  |  |  |
| Tegucigalpa | Paiz | 10314 | 8308 | 2006 | 81 % |  |  |  |
| Tegucigalpa | PriceSmart | 2799 | 1267 | 1532 | 45 % |  |  |  |
| Tegucigalpa | Walmart | 23754 | 10569 | 13185 | 44 % |  |  |  |

## San Pedro Sula

| Problema | Colonial | La Colonia | Los Andes | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 0 | 0 | 8 | 6 | 14 |
| Precio por unidad: probable error | 7 | 7 | 8 | 7 | 26 | 55 |
| Precio por unidad: revisar | 25 | 23 | 17 | 3 | 28 | 96 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 0 | 1 | 1 |
| Sin presentación | 94 | 44 | 117 | 21 | 348 | 624 |
| Corregido en la web | 7 | 5 | 1 | 6 | 4 | 23 |

## Tegucigalpa

| Problema | La Colonia | Paiz | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 2 | 7 | 12 | 21 |
| Precio por unidad: probable error | 10 | 42 | 7 | 40 | 99 |
| Precio por unidad: revisar | 22 | 20 | 4 | 34 | 80 |
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
