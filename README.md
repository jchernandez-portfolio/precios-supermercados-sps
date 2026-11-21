# Errores de datos en Súper Compras

Actualizado automáticamente el 2026-11-21 a partir del catálogo publicado (San Pedro Sula: 2026-11-20T11:44:34.345848Z; Tegucigalpa: 2026-11-20T11:47:03.044520Z).

- [`errores_detectados.csv`](errores_detectados.csv): todo lo que la revisión automática encuentra hoy, una fila por producto y supermercado, con enlace al producto.
- [`errores_confirmados.csv`](errores_confirmados.csv): errores revisados a mano, de dónde vienen (súper o Súper Compras) y su estado.
- [`historial.csv`](historial.csv): cuántos errores había en cada publicación.
- [`frescura.csv`](frescura.csv): última captura aceptada de cada supermercado, día a día.
- [`fotos.csv`](fotos.csv): cobertura de fotos por ciudad y supermercado, día a día.

## Captura diaria

✅ Todos los supermercados se capturaron hoy.

| Ciudad | Supermercado | Sucursal | Última captura (UTC) | Horas | Estado |
|---|---|---|---|---:|---|
| San Pedro Sula | La Colonia | la_colonia_sps | 2026-11-20 11:26 | 0 | Al día |
| San Pedro Sula | Colonial | colonial_sps | 2026-11-20 11:25 | 0 | Al día |
| San Pedro Sula | Walmart | walmart_sps | 2026-11-20 11:23 | 0 | Al día |
| San Pedro Sula | PriceSmart | pricesmart_sps | 2026-11-20 11:17 | 0 | Al día |
| San Pedro Sula | Los Andes | comisariato_los_andes_sps | 2026-11-20 11:18 | 0 | Al día |
| Tegucigalpa | La Colonia | la_colonia_tgu | 2026-11-20 11:35 | 0 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_ffaa | 2026-11-20 11:30 | 0 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_el_sauce | 2026-11-20 11:37 | 0 | Al día |
| Tegucigalpa | PriceSmart | pricesmart_tgu | 2026-11-20 11:18 | 0 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_multiplaza | 2026-11-20 11:21 | 0 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_proceres | 2026-11-20 11:26 | 0 | Al día |

## Fotos de producto

53,005 de 56,367 productos con foto (94 %). Las fotos v2 se publican en dos tamaños; hd = detalle real para 800 px, sd = suficiente para tarjetas, low = foto chica.

| Ciudad | Supermercado | Productos | Con foto | Sin foto | % | hd | sd | low |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| San Pedro Sula | Total | 31477 | 29158 | 2319 | 93 % | 22032 | 6903 | 130 |
| San Pedro Sula | Colonial | 8333 | 7172 | 1161 | 86 % |  |  |  |
| San Pedro Sula | Los Andes | 6366 | 6122 | 244 | 96 % |  |  |  |
| San Pedro Sula | La Colonia | 7199 | 7162 | 37 | 99 % |  |  |  |
| San Pedro Sula | PriceSmart | 1862 | 1688 | 174 | 91 % |  |  |  |
| San Pedro Sula | Walmart | 14524 | 13818 | 706 | 95 % |  |  |  |
| Tegucigalpa | Total | 24890 | 23847 | 1043 | 96 % | 21083 | 2677 | 4 |
| Tegucigalpa | La Colonia | 7647 | 7611 | 36 | 100 % |  |  |  |
| Tegucigalpa | Paiz | 9444 | 9326 | 118 | 99 % |  |  |  |
| Tegucigalpa | PriceSmart | 1904 | 1721 | 183 | 90 % |  |  |  |
| Tegucigalpa | Walmart | 16590 | 15794 | 796 | 95 % |  |  |  |

## San Pedro Sula

| Problema | Colonial | La Colonia | Los Andes | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 0 | 0 | 8 | 13 | 21 |
| Precio por unidad: probable error | 8 | 9 | 6 | 7 | 26 | 56 |
| Precio por unidad: revisar | 24 | 25 | 16 | 3 | 26 | 94 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 0 | 1 | 1 |
| Sin presentación | 92 | 50 | 112 | 25 | 344 | 623 |
| Corregido en la web | 7 | 5 | 1 | 5 | 6 | 24 |

## Tegucigalpa

| Problema | La Colonia | Paiz | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 2 | 6 | 13 | 21 |
| Precio por unidad: probable error | 14 | 30 | 7 | 32 | 83 |
| Precio por unidad: revisar | 22 | 18 | 3 | 22 | 65 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 1 | 1 |
| Sin presentación | 29 | 37 | 25 | 340 | 431 |
| Corregido en la web | 5 | 29 | 3 | 28 | 65 |

## Qué significa cada problema

- **Peso o volumen imposible** (gravedad alta): La medida que se muestra no puede ser real (más de 50 kg o 60 L en un producto de súper): el súper escribió mal la medida o un código se leyó como peso.
- **Precio por unidad: probable error** (gravedad alta): El precio por kg/L es 100 veces mayor o 20 veces menor que el de su tipo de producto: el tamaño o el precio están mal publicados.
- **Precio por unidad: revisar** (gravedad media): El precio por kg/L es entre 20 y 100 veces mayor que el de su tipo: puede ser error o un producto muy pequeño (té en sobres, repuestos, pegamento).
- **Medida mal escrita por el súper** (gravedad media): El nombre trae un espacio dentro del número ("40 0Ml") y no se puede leer la medida.
- **Distinto entre ciudades** (gravedad media): El mismo producto del mismo súper sale por libra en una ciudad y no en la otra.
- **Sin presentación** (gravedad baja): El nombre termina en un número sin unidad ("Ensure 237"): no se sabe qué mide.
- **Corregido en la web** (gravedad info): El súper publica mal la medida ("1100 Lt", "397 Oz" que son gramos) y la web la corrige con un patrón seguro; sigue mal en el súper.
