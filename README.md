# Errores de datos en Súper Compras

Actualizado automáticamente el 2026-11-13 a partir del catálogo publicado (San Pedro Sula: 2026-11-13T11:48:56.424598Z; Tegucigalpa: 2026-11-13T11:51:47.324279Z).

- [`errores_detectados.csv`](errores_detectados.csv): todo lo que la revisión automática encuentra hoy, una fila por producto y supermercado, con enlace al producto.
- [`errores_confirmados.csv`](errores_confirmados.csv): errores revisados a mano, de dónde vienen (súper o Súper Compras) y su estado.
- [`historial.csv`](historial.csv): cuántos errores había en cada publicación.
- [`frescura.csv`](frescura.csv): última captura aceptada de cada supermercado, día a día.
- [`fotos.csv`](fotos.csv): cobertura de fotos por ciudad y supermercado, día a día.

## Captura diaria

✅ Todos los supermercados se capturaron hoy.

| Ciudad | Supermercado | Sucursal | Última captura (UTC) | Horas | Estado |
|---|---|---|---|---:|---|
| San Pedro Sula | La Colonia | la_colonia_sps | 2026-11-12 11:27 | 24 | Al día |
| San Pedro Sula | Colonial | colonial_sps | 2026-11-13 11:26 | 0 | Al día |
| San Pedro Sula | Walmart | walmart_sps | 2026-11-13 11:26 | 0 | Al día |
| San Pedro Sula | PriceSmart | pricesmart_sps | 2026-11-13 11:18 | 0 | Al día |
| San Pedro Sula | Los Andes | comisariato_los_andes_sps | 2026-11-13 11:19 | 0 | Al día |
| Tegucigalpa | La Colonia | la_colonia_tgu | 2026-11-12 11:37 | 24 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_ffaa | 2026-11-13 11:34 | 0 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_el_sauce | 2026-11-13 11:42 | 0 | Al día |
| Tegucigalpa | PriceSmart | pricesmart_tgu | 2026-11-13 11:19 | 0 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_multiplaza | 2026-11-13 11:22 | 0 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_proceres | 2026-11-13 11:26 | 0 | Al día |

## Fotos de producto

54,553 de 61,435 productos con foto (89 %). Las fotos v2 se publican en dos tamaños; hd = detalle real para 800 px, sd = suficiente para tarjetas, low = foto chica.

| Ciudad | Supermercado | Productos | Con foto | Sin foto | % | hd | sd | low |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| San Pedro Sula | Total | 34294 | 29966 | 4328 | 87 % | 22647 | 7084 | 130 |
| San Pedro Sula | Colonial | 8486 | 7186 | 1300 | 85 % |  |  |  |
| San Pedro Sula | Los Andes | 6944 | 6222 | 722 | 90 % |  |  |  |
| San Pedro Sula | La Colonia | 7143 | 7099 | 44 | 99 % |  |  |  |
| San Pedro Sula | PriceSmart | 2795 | 1691 | 1104 | 61 % |  |  |  |
| San Pedro Sula | Walmart | 15598 | 14436 | 1162 | 93 % |  |  |  |
| Tegucigalpa | Total | 27141 | 24587 | 2554 | 91 % | 21740 | 2759 | 4 |
| Tegucigalpa | La Colonia | 7614 | 7572 | 42 | 99 % |  |  |  |
| Tegucigalpa | Paiz | 9803 | 9650 | 153 | 98 % |  |  |  |
| Tegucigalpa | PriceSmart | 2844 | 1724 | 1120 | 61 % |  |  |  |
| Tegucigalpa | Walmart | 17797 | 16433 | 1364 | 92 % |  |  |  |

## San Pedro Sula

| Problema | Colonial | La Colonia | Los Andes | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 0 | 0 | 6 | 12 | 18 |
| Precio por unidad: probable error | 8 | 9 | 8 | 7 | 26 | 58 |
| Precio por unidad: revisar | 21 | 23 | 17 | 3 | 27 | 91 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 0 | 1 | 1 |
| Sin presentación | 95 | 50 | 125 | 23 | 364 | 657 |
| Corregido en la web | 7 | 5 | 1 | 7 | 6 | 26 |

## Tegucigalpa

| Problema | La Colonia | Paiz | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 2 | 7 | 15 | 24 |
| Precio por unidad: probable error | 13 | 31 | 7 | 33 | 84 |
| Precio por unidad: revisar | 23 | 18 | 4 | 22 | 67 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 1 | 1 |
| Distinto entre ciudades | 10 | 0 | 0 | 1 | 11 |
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
