# Errores de datos en Súper Compras

Actualizado automáticamente el 2026-11-16 a partir del catálogo publicado (San Pedro Sula: 2026-11-16T19:56:01.422357Z; Tegucigalpa: 2026-11-16T19:59:05.714065Z).

- [`errores_detectados.csv`](errores_detectados.csv): todo lo que la revisión automática encuentra hoy, una fila por producto y supermercado, con enlace al producto.
- [`errores_confirmados.csv`](errores_confirmados.csv): errores revisados a mano, de dónde vienen (súper o Súper Compras) y su estado.
- [`historial.csv`](historial.csv): cuántos errores había en cada publicación.
- [`frescura.csv`](frescura.csv): última captura aceptada de cada supermercado, día a día.
- [`fotos.csv`](fotos.csv): cobertura de fotos por ciudad y supermercado, día a día.

## Captura diaria

✅ Todos los supermercados se capturaron hoy.

| Ciudad | Supermercado | Sucursal | Última captura (UTC) | Horas | Estado |
|---|---|---|---|---:|---|
| San Pedro Sula | La Colonia | la_colonia_sps | 2026-11-16 11:28 | 8 | Al día |
| San Pedro Sula | Colonial | colonial_sps | 2026-11-16 11:26 | 8 | Al día |
| San Pedro Sula | Walmart | walmart_sps | 2026-11-16 11:25 | 8 | Al día |
| San Pedro Sula | PriceSmart | pricesmart_sps | 2026-11-16 11:18 | 8 | Al día |
| San Pedro Sula | Los Andes | comisariato_los_andes_sps | 2026-11-16 11:19 | 8 | Al día |
| Tegucigalpa | La Colonia | la_colonia_tgu | 2026-11-16 11:40 | 8 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_ffaa | 2026-11-16 11:33 | 8 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_el_sauce | 2026-11-16 11:40 | 8 | Al día |
| Tegucigalpa | PriceSmart | pricesmart_tgu | 2026-11-16 11:19 | 8 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_multiplaza | 2026-11-16 11:23 | 8 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_proceres | 2026-11-16 11:27 | 8 | Al día |

## Fotos de producto

52,640 de 56,282 productos con foto (94 %). Las fotos v2 se publican en dos tamaños; hd = detalle real para 800 px, sd = suficiente para tarjetas, low = foto chica.

| Ciudad | Supermercado | Productos | Con foto | Sin foto | % | hd | sd | low |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| San Pedro Sula | Total | 31524 | 29101 | 2423 | 92 % | 22026 | 6870 | 130 |
| San Pedro Sula | Colonial | 8375 | 7166 | 1209 | 86 % |  |  |  |
| San Pedro Sula | Los Andes | 6353 | 6073 | 280 | 96 % |  |  |  |
| San Pedro Sula | La Colonia | 7248 | 7157 | 91 | 99 % |  |  |  |
| San Pedro Sula | PriceSmart | 1817 | 1644 | 173 | 90 % |  |  |  |
| San Pedro Sula | Walmart | 14546 | 13866 | 680 | 95 % |  |  |  |
| Tegucigalpa | Total | 24758 | 23539 | 1219 | 95 % | 20829 | 2641 | 4 |
| Tegucigalpa | La Colonia | 7625 | 7551 | 74 | 99 % |  |  |  |
| Tegucigalpa | Paiz | 9445 | 9192 | 253 | 97 % |  |  |  |
| Tegucigalpa | PriceSmart | 1862 | 1676 | 186 | 90 % |  |  |  |
| Tegucigalpa | Walmart | 16537 | 15736 | 801 | 95 % |  |  |  |

## San Pedro Sula

| Problema | Colonial | La Colonia | Los Andes | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 0 | 0 | 7 | 10 | 17 |
| Precio por unidad: probable error | 8 | 9 | 6 | 7 | 25 | 55 |
| Precio por unidad: revisar | 25 | 26 | 16 | 3 | 28 | 98 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 0 | 1 | 1 |
| Sin presentación | 90 | 48 | 112 | 24 | 342 | 616 |
| Corregido en la web | 7 | 5 | 1 | 6 | 7 | 26 |

## Tegucigalpa

| Problema | La Colonia | Paiz | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 2 | 8 | 12 | 22 |
| Precio por unidad: probable error | 14 | 29 | 7 | 31 | 81 |
| Precio por unidad: revisar | 22 | 16 | 3 | 20 | 61 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 1 | 1 |
| Sin presentación | 31 | 39 | 24 | 340 | 434 |
| Corregido en la web | 5 | 28 | 2 | 28 | 63 |

## Qué significa cada problema

- **Peso o volumen imposible** (gravedad alta): La medida que se muestra no puede ser real (más de 50 kg o 60 L en un producto de súper): el súper escribió mal la medida o un código se leyó como peso.
- **Precio por unidad: probable error** (gravedad alta): El precio por kg/L es 100 veces mayor o 20 veces menor que el de su tipo de producto: el tamaño o el precio están mal publicados.
- **Precio por unidad: revisar** (gravedad media): El precio por kg/L es entre 20 y 100 veces mayor que el de su tipo: puede ser error o un producto muy pequeño (té en sobres, repuestos, pegamento).
- **Medida mal escrita por el súper** (gravedad media): El nombre trae un espacio dentro del número ("40 0Ml") y no se puede leer la medida.
- **Distinto entre ciudades** (gravedad media): El mismo producto del mismo súper sale por libra en una ciudad y no en la otra.
- **Sin presentación** (gravedad baja): El nombre termina en un número sin unidad ("Ensure 237"): no se sabe qué mide.
- **Corregido en la web** (gravedad info): El súper publica mal la medida ("1100 Lt", "397 Oz" que son gramos) y la web la corrige con un patrón seguro; sigue mal en el súper.
