# Errores de datos en Súper Compras

Actualizado automáticamente el 2026-11-19 a partir del catálogo publicado (San Pedro Sula: 2026-11-19T11:38:07.435910Z; Tegucigalpa: 2026-11-19T11:39:52.709231Z).

- [`errores_detectados.csv`](errores_detectados.csv): todo lo que la revisión automática encuentra hoy, una fila por producto y supermercado, con enlace al producto.
- [`errores_confirmados.csv`](errores_confirmados.csv): errores revisados a mano, de dónde vienen (súper o Súper Compras) y su estado.
- [`historial.csv`](historial.csv): cuántos errores había en cada publicación.
- [`frescura.csv`](frescura.csv): última captura aceptada de cada supermercado, día a día.
- [`fotos.csv`](fotos.csv): cobertura de fotos por ciudad y supermercado, día a día.

## Captura diaria

✅ Todos los supermercados se capturaron hoy.

| Ciudad | Supermercado | Sucursal | Última captura (UTC) | Horas | Estado |
|---|---|---|---|---:|---|
| San Pedro Sula | La Colonia | la_colonia_sps | 2026-11-18 11:27 | 24 | Al día |
| San Pedro Sula | Colonial | colonial_sps | 2026-11-19 11:25 | 0 | Al día |
| San Pedro Sula | Walmart | walmart_sps | 2026-11-18 11:24 | 24 | Al día |
| San Pedro Sula | PriceSmart | pricesmart_sps | 2026-11-19 11:18 | 0 | Al día |
| San Pedro Sula | Los Andes | comisariato_los_andes_sps | 2026-11-19 11:18 | 0 | Al día |
| Tegucigalpa | La Colonia | la_colonia_tgu | 2026-11-18 11:36 | 24 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_ffaa | 2026-11-18 11:30 | 24 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_el_sauce | 2026-11-18 11:37 | 24 | Al día |
| Tegucigalpa | PriceSmart | pricesmart_tgu | 2026-11-19 11:18 | 0 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_multiplaza | 2026-11-18 22:02 | 13 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_proceres | 2026-11-18 22:07 | 13 | Al día |

## Fotos de producto

52,832 de 56,082 productos con foto (94 %). Las fotos v2 se publican en dos tamaños; hd = detalle real para 800 px, sd = suficiente para tarjetas, low = foto chica.

| Ciudad | Supermercado | Productos | Con foto | Sin foto | % | hd | sd | low |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| San Pedro Sula | Total | 31366 | 29096 | 2270 | 93 % | 22001 | 6892 | 130 |
| San Pedro Sula | Colonial | 8332 | 7172 | 1160 | 86 % |  |  |  |
| San Pedro Sula | Los Andes | 6364 | 6112 | 252 | 96 % |  |  |  |
| San Pedro Sula | La Colonia | 7137 | 7104 | 33 | 100 % |  |  |  |
| San Pedro Sula | PriceSmart | 1834 | 1658 | 176 | 90 % |  |  |  |
| San Pedro Sula | Walmart | 14462 | 13811 | 651 | 95 % |  |  |  |
| Tegucigalpa | Total | 24716 | 23736 | 980 | 96 % | 21024 | 2644 | 4 |
| Tegucigalpa | La Colonia | 7503 | 7468 | 35 | 100 % |  |  |  |
| Tegucigalpa | Paiz | 9435 | 9323 | 112 | 99 % |  |  |  |
| Tegucigalpa | PriceSmart | 1870 | 1685 | 185 | 90 % |  |  |  |
| Tegucigalpa | Walmart | 16544 | 15806 | 738 | 96 % |  |  |  |

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
| Sin presentación | 30 | 38 | 24 | 341 | 433 |
| Corregido en la web | 5 | 29 | 2 | 26 | 62 |

## Qué significa cada problema

- **Peso o volumen imposible** (gravedad alta): La medida que se muestra no puede ser real (más de 50 kg o 60 L en un producto de súper): el súper escribió mal la medida o un código se leyó como peso.
- **Precio por unidad: probable error** (gravedad alta): El precio por kg/L es 100 veces mayor o 20 veces menor que el de su tipo de producto: el tamaño o el precio están mal publicados.
- **Precio por unidad: revisar** (gravedad media): El precio por kg/L es entre 20 y 100 veces mayor que el de su tipo: puede ser error o un producto muy pequeño (té en sobres, repuestos, pegamento).
- **Medida mal escrita por el súper** (gravedad media): El nombre trae un espacio dentro del número ("40 0Ml") y no se puede leer la medida.
- **Distinto entre ciudades** (gravedad media): El mismo producto del mismo súper sale por libra en una ciudad y no en la otra.
- **Sin presentación** (gravedad baja): El nombre termina en un número sin unidad ("Ensure 237"): no se sabe qué mide.
- **Corregido en la web** (gravedad info): El súper publica mal la medida ("1100 Lt", "397 Oz" que son gramos) y la web la corrige con un patrón seguro; sigue mal en el súper.
