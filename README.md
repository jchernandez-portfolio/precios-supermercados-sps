# Errores de datos en Súper Compras

Actualizado automáticamente el 2026-11-17 a partir del catálogo publicado (San Pedro Sula: 2026-11-17T11:37:59.705645Z; Tegucigalpa: 2026-11-17T11:40:57.788733Z).

- [`errores_detectados.csv`](errores_detectados.csv): todo lo que la revisión automática encuentra hoy, una fila por producto y supermercado, con enlace al producto.
- [`errores_confirmados.csv`](errores_confirmados.csv): errores revisados a mano, de dónde vienen (súper o Súper Compras) y su estado.
- [`historial.csv`](historial.csv): cuántos errores había en cada publicación.
- [`frescura.csv`](frescura.csv): última captura aceptada de cada supermercado, día a día.
- [`fotos.csv`](fotos.csv): cobertura de fotos por ciudad y supermercado, día a día.

## Captura diaria

✅ Todos los supermercados se capturaron hoy.

| Ciudad | Supermercado | Sucursal | Última captura (UTC) | Horas | Estado |
|---|---|---|---|---:|---|
| San Pedro Sula | La Colonia | la_colonia_sps | 2026-11-16 11:28 | 24 | Al día |
| San Pedro Sula | Colonial | colonial_sps | 2026-11-17 11:26 | 0 | Al día |
| San Pedro Sula | Walmart | walmart_sps | 2026-11-16 11:25 | 24 | Al día |
| San Pedro Sula | PriceSmart | pricesmart_sps | 2026-11-17 11:18 | 0 | Al día |
| San Pedro Sula | Los Andes | comisariato_los_andes_sps | 2026-11-17 11:19 | 0 | Al día |
| Tegucigalpa | La Colonia | la_colonia_tgu | 2026-11-16 11:40 | 24 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_ffaa | 2026-11-16 11:33 | 24 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_el_sauce | 2026-11-16 11:40 | 24 | Al día |
| Tegucigalpa | PriceSmart | pricesmart_tgu | 2026-11-17 11:19 | 0 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_multiplaza | 2026-11-16 11:23 | 24 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_proceres | 2026-11-16 11:27 | 24 | Al día |

## Fotos de producto

53,001 de 56,259 productos con foto (94 %). Las fotos v2 se publican en dos tamaños; hd = detalle real para 800 px, sd = suficiente para tarjetas, low = foto chica.

| Ciudad | Supermercado | Productos | Con foto | Sin foto | % | hd | sd | low |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| San Pedro Sula | Total | 31498 | 29222 | 2276 | 93 % | 22092 | 6925 | 130 |
| San Pedro Sula | Colonial | 8340 | 7180 | 1160 | 86 % |  |  |  |
| San Pedro Sula | Los Andes | 6357 | 6105 | 252 | 96 % |  |  |  |
| San Pedro Sula | La Colonia | 7248 | 7215 | 33 | 100 % |  |  |  |
| San Pedro Sula | PriceSmart | 1814 | 1639 | 175 | 90 % |  |  |  |
| San Pedro Sula | Walmart | 14546 | 13888 | 658 | 95 % |  |  |  |
| Tegucigalpa | Total | 24761 | 23779 | 982 | 96 % | 21035 | 2678 | 4 |
| Tegucigalpa | La Colonia | 7625 | 7591 | 34 | 100 % |  |  |  |
| Tegucigalpa | Paiz | 9445 | 9335 | 110 | 99 % |  |  |  |
| Tegucigalpa | PriceSmart | 1865 | 1680 | 185 | 90 % |  |  |  |
| Tegucigalpa | Walmart | 16537 | 15796 | 741 | 96 % |  |  |  |

## San Pedro Sula

| Problema | Colonial | La Colonia | Los Andes | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 0 | 0 | 7 | 10 | 17 |
| Precio por unidad: probable error | 8 | 9 | 6 | 6 | 25 | 54 |
| Precio por unidad: revisar | 25 | 26 | 16 | 4 | 28 | 99 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 0 | 1 | 1 |
| Sin presentación | 90 | 48 | 112 | 24 | 342 | 616 |
| Corregido en la web | 7 | 5 | 1 | 6 | 7 | 26 |

## Tegucigalpa

| Problema | La Colonia | Paiz | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 2 | 7 | 12 | 21 |
| Precio por unidad: probable error | 14 | 29 | 7 | 31 | 81 |
| Precio por unidad: revisar | 22 | 16 | 3 | 20 | 61 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 1 | 1 |
| Sin presentación | 31 | 39 | 23 | 340 | 433 |
| Corregido en la web | 5 | 28 | 2 | 28 | 63 |

## Qué significa cada problema

- **Peso o volumen imposible** (gravedad alta): La medida que se muestra no puede ser real (más de 50 kg o 60 L en un producto de súper): el súper escribió mal la medida o un código se leyó como peso.
- **Precio por unidad: probable error** (gravedad alta): El precio por kg/L es 100 veces mayor o 20 veces menor que el de su tipo de producto: el tamaño o el precio están mal publicados.
- **Precio por unidad: revisar** (gravedad media): El precio por kg/L es entre 20 y 100 veces mayor que el de su tipo: puede ser error o un producto muy pequeño (té en sobres, repuestos, pegamento).
- **Medida mal escrita por el súper** (gravedad media): El nombre trae un espacio dentro del número ("40 0Ml") y no se puede leer la medida.
- **Distinto entre ciudades** (gravedad media): El mismo producto del mismo súper sale por libra en una ciudad y no en la otra.
- **Sin presentación** (gravedad baja): El nombre termina en un número sin unidad ("Ensure 237"): no se sabe qué mide.
- **Corregido en la web** (gravedad info): El súper publica mal la medida ("1100 Lt", "397 Oz" que son gramos) y la web la corrige con un patrón seguro; sigue mal en el súper.
