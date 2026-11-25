# Errores de datos en Súper Compras

Actualizado automáticamente el 2026-11-25 a partir del catálogo publicado (San Pedro Sula: 2026-11-25T11:43:22.466983Z; Tegucigalpa: 2026-11-25T11:46:28.065504Z).

- [`errores_detectados.csv`](errores_detectados.csv): todo lo que la revisión automática encuentra hoy, una fila por producto y supermercado, con enlace al producto.
- [`errores_confirmados.csv`](errores_confirmados.csv): errores revisados a mano, de dónde vienen (súper o Súper Compras) y su estado.
- [`historial.csv`](historial.csv): cuántos errores había en cada publicación.
- [`frescura.csv`](frescura.csv): última captura aceptada de cada supermercado, día a día.
- [`fotos.csv`](fotos.csv): cobertura de fotos por ciudad y supermercado, día a día.

## Captura diaria

✅ Todos los supermercados se capturaron hoy.

| Ciudad | Supermercado | Sucursal | Última captura (UTC) | Horas | Estado |
|---|---|---|---|---:|---|
| San Pedro Sula | La Colonia | la_colonia_sps | 2026-11-25 11:27 | 0 | Al día |
| San Pedro Sula | Colonial | colonial_sps | 2026-11-25 11:25 | 0 | Al día |
| San Pedro Sula | Walmart | walmart_sps | 2026-11-24 11:24 | 24 | Al día |
| San Pedro Sula | PriceSmart | pricesmart_sps | 2026-11-25 11:17 | 0 | Al día |
| San Pedro Sula | Los Andes | comisariato_los_andes_sps | 2026-11-25 11:18 | 0 | Al día |
| Tegucigalpa | La Colonia | la_colonia_tgu | 2026-11-25 11:36 | 0 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_ffaa | 2026-11-24 11:30 | 24 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_el_sauce | 2026-11-24 11:37 | 24 | Al día |
| Tegucigalpa | PriceSmart | pricesmart_tgu | 2026-11-25 11:18 | 0 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_multiplaza | 2026-11-24 11:21 | 24 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_proceres | 2026-11-24 11:25 | 24 | Al día |

## Fotos de producto

52,527 de 56,038 productos con foto (94 %). Las fotos v2 se publican en dos tamaños; hd = detalle real para 800 px, sd = suficiente para tarjetas, low = foto chica.

| Ciudad | Supermercado | Productos | Con foto | Sin foto | % | hd | sd | low |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| San Pedro Sula | Total | 31310 | 28925 | 2385 | 92 % | 21836 | 6882 | 130 |
| San Pedro Sula | Colonial | 8365 | 7163 | 1202 | 86 % |  |  |  |
| San Pedro Sula | Los Andes | 6374 | 6132 | 242 | 96 % |  |  |  |
| San Pedro Sula | La Colonia | 7181 | 7126 | 55 | 99 % |  |  |  |
| San Pedro Sula | PriceSmart | 1813 | 1606 | 207 | 89 % |  |  |  |
| San Pedro Sula | Walmart | 14334 | 13650 | 684 | 95 % |  |  |  |
| Tegucigalpa | Total | 24728 | 23602 | 1126 | 95 % | 20892 | 2644 | 4 |
| Tegucigalpa | La Colonia | 7639 | 7561 | 78 | 99 % |  |  |  |
| Tegucigalpa | Paiz | 9328 | 9188 | 140 | 98 % |  |  |  |
| Tegucigalpa | PriceSmart | 1874 | 1658 | 216 | 88 % |  |  |  |
| Tegucigalpa | Walmart | 16442 | 15652 | 790 | 95 % |  |  |  |

## San Pedro Sula

| Problema | Colonial | La Colonia | Los Andes | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 0 | 0 | 7 | 13 | 20 |
| Precio por unidad: probable error | 8 | 8 | 6 | 7 | 24 | 53 |
| Precio por unidad: revisar | 23 | 26 | 16 | 3 | 27 | 95 |
| Sin presentación | 92 | 49 | 116 | 24 | 344 | 625 |
| Corregido en la web | 7 | 5 | 1 | 5 | 7 | 25 |

## Tegucigalpa

| Problema | La Colonia | Paiz | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 1 | 7 | 12 | 20 |
| Precio por unidad: probable error | 14 | 29 | 7 | 34 | 84 |
| Precio por unidad: revisar | 22 | 15 | 4 | 21 | 62 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 1 | 1 |
| Sin presentación | 29 | 37 | 24 | 338 | 428 |
| Corregido en la web | 5 | 29 | 3 | 27 | 64 |

## Qué significa cada problema

- **Peso o volumen imposible** (gravedad alta): La medida que se muestra no puede ser real (más de 50 kg o 60 L en un producto de súper): el súper escribió mal la medida o un código se leyó como peso.
- **Precio por unidad: probable error** (gravedad alta): El precio por kg/L es 100 veces mayor o 20 veces menor que el de su tipo de producto: el tamaño o el precio están mal publicados.
- **Precio por unidad: revisar** (gravedad media): El precio por kg/L es entre 20 y 100 veces mayor que el de su tipo: puede ser error o un producto muy pequeño (té en sobres, repuestos, pegamento).
- **Medida mal escrita por el súper** (gravedad media): El nombre trae un espacio dentro del número ("40 0Ml") y no se puede leer la medida.
- **Distinto entre ciudades** (gravedad media): El mismo producto del mismo súper sale por libra en una ciudad y no en la otra.
- **Sin presentación** (gravedad baja): El nombre termina en un número sin unidad ("Ensure 237"): no se sabe qué mide.
- **Corregido en la web** (gravedad info): El súper publica mal la medida ("1100 Lt", "397 Oz" que son gramos) y la web la corrige con un patrón seguro; sigue mal en el súper.
