# Errores de datos en Súper Compras

Actualizado automáticamente el 2026-11-26 a partir del catálogo publicado (San Pedro Sula: 2026-11-26T11:47:57.680587Z; Tegucigalpa: 2026-11-26T11:50:26.631440Z).

- [`errores_detectados.csv`](errores_detectados.csv): todo lo que la revisión automática encuentra hoy, una fila por producto y supermercado, con enlace al producto.
- [`errores_confirmados.csv`](errores_confirmados.csv): errores revisados a mano, de dónde vienen (súper o Súper Compras) y su estado.
- [`historial.csv`](historial.csv): cuántos errores había en cada publicación.
- [`frescura.csv`](frescura.csv): última captura aceptada de cada supermercado, día a día.
- [`fotos.csv`](fotos.csv): cobertura de fotos por ciudad y supermercado, día a día.

## Captura diaria

✅ Todos los supermercados se capturaron hoy.

| Ciudad | Supermercado | Sucursal | Última captura (UTC) | Horas | Estado |
|---|---|---|---|---:|---|
| San Pedro Sula | La Colonia | la_colonia_sps | 2026-11-26 11:26 | 0 | Al día |
| San Pedro Sula | Colonial | colonial_sps | 2026-11-26 11:25 | 0 | Al día |
| San Pedro Sula | Walmart | walmart_sps | 2026-11-26 11:24 | 0 | Al día |
| San Pedro Sula | PriceSmart | pricesmart_sps | 2026-11-26 11:17 | 0 | Al día |
| San Pedro Sula | Los Andes | comisariato_los_andes_sps | 2026-11-26 11:18 | 0 | Al día |
| Tegucigalpa | La Colonia | la_colonia_tgu | 2026-11-26 11:37 | 0 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_ffaa | 2026-11-26 11:32 | 0 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_el_sauce | 2026-11-26 11:39 | 0 | Al día |
| Tegucigalpa | PriceSmart | pricesmart_tgu | 2026-11-26 11:18 | 0 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_multiplaza | 2026-11-25 23:09 | 12 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_proceres | 2026-11-25 23:13 | 12 | Al día |

## Fotos de producto

52,338 de 56,593 productos con foto (92 %). Las fotos v2 se publican en dos tamaños; hd = detalle real para 800 px, sd = suficiente para tarjetas, low = foto chica.

| Ciudad | Supermercado | Productos | Con foto | Sin foto | % | hd | sd | low |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| San Pedro Sula | Total | 31471 | 28782 | 2689 | 91 % | 21732 | 6841 | 130 |
| San Pedro Sula | Colonial | 8095 | 6956 | 1139 | 86 % |  |  |  |
| San Pedro Sula | Los Andes | 6375 | 6130 | 245 | 96 % |  |  |  |
| San Pedro Sula | La Colonia | 7208 | 7134 | 74 | 99 % |  |  |  |
| San Pedro Sula | PriceSmart | 1805 | 1591 | 214 | 88 % |  |  |  |
| San Pedro Sula | Walmart | 14688 | 13661 | 1027 | 93 % |  |  |  |
| Tegucigalpa | Total | 25122 | 23556 | 1566 | 94 % | 20869 | 2622 | 4 |
| Tegucigalpa | La Colonia | 7639 | 7543 | 96 | 99 % |  |  |  |
| Tegucigalpa | Paiz | 9448 | 9225 | 223 | 98 % |  |  |  |
| Tegucigalpa | PriceSmart | 1872 | 1651 | 221 | 88 % |  |  |  |
| Tegucigalpa | Walmart | 16875 | 15678 | 1197 | 93 % |  |  |  |

## San Pedro Sula

| Problema | Colonial | La Colonia | Los Andes | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 0 | 0 | 7 | 13 | 20 |
| Precio por unidad: probable error | 7 | 8 | 6 | 7 | 23 | 51 |
| Precio por unidad: revisar | 24 | 27 | 16 | 3 | 30 | 100 |
| Sin presentación | 90 | 50 | 116 | 22 | 362 | 640 |
| Corregido en la web | 7 | 5 | 1 | 5 | 6 | 24 |

## Tegucigalpa

| Problema | La Colonia | Paiz | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 1 | 7 | 13 | 21 |
| Precio por unidad: probable error | 15 | 27 | 8 | 32 | 82 |
| Precio por unidad: revisar | 22 | 17 | 2 | 23 | 64 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 1 | 1 |
| Sin presentación | 29 | 39 | 24 | 358 | 450 |
| Corregido en la web | 5 | 28 | 3 | 25 | 61 |

## Qué significa cada problema

- **Peso o volumen imposible** (gravedad alta): La medida que se muestra no puede ser real (más de 50 kg o 60 L en un producto de súper): el súper escribió mal la medida o un código se leyó como peso.
- **Precio por unidad: probable error** (gravedad alta): El precio por kg/L es 100 veces mayor o 20 veces menor que el de su tipo de producto: el tamaño o el precio están mal publicados.
- **Precio por unidad: revisar** (gravedad media): El precio por kg/L es entre 20 y 100 veces mayor que el de su tipo: puede ser error o un producto muy pequeño (té en sobres, repuestos, pegamento).
- **Medida mal escrita por el súper** (gravedad media): El nombre trae un espacio dentro del número ("40 0Ml") y no se puede leer la medida.
- **Distinto entre ciudades** (gravedad media): El mismo producto del mismo súper sale por libra en una ciudad y no en la otra.
- **Sin presentación** (gravedad baja): El nombre termina en un número sin unidad ("Ensure 237"): no se sabe qué mide.
- **Corregido en la web** (gravedad info): El súper publica mal la medida ("1100 Lt", "397 Oz" que son gramos) y la web la corrige con un patrón seguro; sigue mal en el súper.
