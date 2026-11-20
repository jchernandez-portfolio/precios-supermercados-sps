# Errores de datos en Súper Compras

Actualizado automáticamente el 2026-11-20 a partir del catálogo publicado (San Pedro Sula: 2026-11-20T01:23:47.226613Z; Tegucigalpa: 2026-11-20T01:26:10.660625Z).

- [`errores_detectados.csv`](errores_detectados.csv): todo lo que la revisión automática encuentra hoy, una fila por producto y supermercado, con enlace al producto.
- [`errores_confirmados.csv`](errores_confirmados.csv): errores revisados a mano, de dónde vienen (súper o Súper Compras) y su estado.
- [`historial.csv`](historial.csv): cuántos errores había en cada publicación.
- [`frescura.csv`](frescura.csv): última captura aceptada de cada supermercado, día a día.
- [`fotos.csv`](fotos.csv): cobertura de fotos por ciudad y supermercado, día a día.

## Captura diaria

✅ Todos los supermercados se capturaron hoy.

| Ciudad | Supermercado | Sucursal | Última captura (UTC) | Horas | Estado |
|---|---|---|---|---:|---|
| San Pedro Sula | La Colonia | la_colonia_sps | 2026-11-19 21:34 | 3 | Al día |
| San Pedro Sula | Colonial | colonial_sps | 2026-11-19 11:25 | 13 | Al día |
| San Pedro Sula | Walmart | walmart_sps | 2026-11-18 11:24 | 37 | Al día |
| San Pedro Sula | PriceSmart | pricesmart_sps | 2026-11-19 11:18 | 14 | Al día |
| San Pedro Sula | Los Andes | comisariato_los_andes_sps | 2026-11-19 11:18 | 14 | Al día |
| Tegucigalpa | La Colonia | la_colonia_tgu | 2026-11-19 21:43 | 3 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_ffaa | 2026-11-18 11:30 | 37 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_el_sauce | 2026-11-18 11:37 | 37 | Al día |
| Tegucigalpa | PriceSmart | pricesmart_tgu | 2026-11-19 11:18 | 14 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_multiplaza | 2026-11-19 21:30 | 3 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_proceres | 2026-11-19 21:34 | 3 | Al día |

## Fotos de producto

52,908 de 56,215 productos con foto (94 %). Las fotos v2 se publican en dos tamaños; hd = detalle real para 800 px, sd = suficiente para tarjetas, low = foto chica.

| Ciudad | Supermercado | Productos | Con foto | Sin foto | % | hd | sd | low |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| San Pedro Sula | Total | 31398 | 29111 | 2287 | 93 % | 22019 | 6890 | 130 |
| San Pedro Sula | Colonial | 8332 | 7175 | 1157 | 86 % |  |  |  |
| San Pedro Sula | Los Andes | 6364 | 6112 | 252 | 96 % |  |  |  |
| San Pedro Sula | La Colonia | 7199 | 7138 | 61 | 99 % |  |  |  |
| San Pedro Sula | PriceSmart | 1834 | 1666 | 168 | 91 % |  |  |  |
| San Pedro Sula | Walmart | 14462 | 13811 | 651 | 95 % |  |  |  |
| Tegucigalpa | Total | 24817 | 23797 | 1020 | 96 % | 21067 | 2664 | 4 |
| Tegucigalpa | La Colonia | 7647 | 7571 | 76 | 99 % |  |  |  |
| Tegucigalpa | Paiz | 9444 | 9329 | 115 | 99 % |  |  |  |
| Tegucigalpa | PriceSmart | 1870 | 1691 | 179 | 90 % |  |  |  |
| Tegucigalpa | Walmart | 16544 | 15805 | 739 | 96 % |  |  |  |

## San Pedro Sula

| Problema | Colonial | La Colonia | Los Andes | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 0 | 0 | 8 | 11 | 19 |
| Precio por unidad: probable error | 8 | 9 | 6 | 7 | 25 | 55 |
| Precio por unidad: revisar | 24 | 26 | 16 | 3 | 29 | 98 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 0 | 1 | 1 |
| Sin presentación | 91 | 48 | 112 | 25 | 340 | 616 |
| Corregido en la web | 7 | 5 | 1 | 5 | 4 | 22 |

## Tegucigalpa

| Problema | La Colonia | Paiz | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 2 | 6 | 13 | 21 |
| Precio por unidad: probable error | 14 | 29 | 7 | 32 | 82 |
| Precio por unidad: revisar | 21 | 17 | 3 | 21 | 62 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 1 | 1 |
| Sin presentación | 29 | 37 | 24 | 340 | 430 |
| Corregido en la web | 5 | 29 | 2 | 26 | 62 |

## Qué significa cada problema

- **Peso o volumen imposible** (gravedad alta): La medida que se muestra no puede ser real (más de 50 kg o 60 L en un producto de súper): el súper escribió mal la medida o un código se leyó como peso.
- **Precio por unidad: probable error** (gravedad alta): El precio por kg/L es 100 veces mayor o 20 veces menor que el de su tipo de producto: el tamaño o el precio están mal publicados.
- **Precio por unidad: revisar** (gravedad media): El precio por kg/L es entre 20 y 100 veces mayor que el de su tipo: puede ser error o un producto muy pequeño (té en sobres, repuestos, pegamento).
- **Medida mal escrita por el súper** (gravedad media): El nombre trae un espacio dentro del número ("40 0Ml") y no se puede leer la medida.
- **Distinto entre ciudades** (gravedad media): El mismo producto del mismo súper sale por libra en una ciudad y no en la otra.
- **Sin presentación** (gravedad baja): El nombre termina en un número sin unidad ("Ensure 237"): no se sabe qué mide.
- **Corregido en la web** (gravedad info): El súper publica mal la medida ("1100 Lt", "397 Oz" que son gramos) y la web la corrige con un patrón seguro; sigue mal en el súper.
