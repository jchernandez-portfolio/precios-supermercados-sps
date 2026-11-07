# Errores de datos en Súper Compras

Actualizado automáticamente el 2026-11-07 a partir del catálogo publicado (San Pedro Sula: 2026-11-07T11:47:05.122148Z; Tegucigalpa: 2026-11-07T11:48:51.047666Z).

- [`errores_detectados.csv`](errores_detectados.csv): todo lo que la revisión automática encuentra hoy, una fila por producto y supermercado, con enlace al producto.
- [`errores_confirmados.csv`](errores_confirmados.csv): errores revisados a mano, de dónde vienen (súper o Súper Compras) y su estado.
- [`historial.csv`](historial.csv): cuántos errores había en cada publicación.
- [`frescura.csv`](frescura.csv): última captura aceptada de cada supermercado, día a día.
- [`fotos.csv`](fotos.csv): cobertura de fotos por ciudad y supermercado, día a día.

## Captura diaria

✅ Todos los supermercados se capturaron hoy.

| Ciudad | Supermercado | Sucursal | Última captura (UTC) | Horas | Estado |
|---|---|---|---|---:|---|
| San Pedro Sula | La Colonia | la_colonia_sps | 2026-11-07 11:26 | 0 | Al día |
| San Pedro Sula | Colonial | colonial_sps | 2026-11-06 11:26 | 24 | Al día |
| San Pedro Sula | Walmart | walmart_sps | 2026-11-07 11:25 | 0 | Al día |
| San Pedro Sula | PriceSmart | pricesmart_sps | 2026-11-07 11:18 | 0 | Al día |
| San Pedro Sula | Los Andes | comisariato_los_andes_sps | 2026-11-07 11:19 | 0 | Al día |
| Tegucigalpa | La Colonia | la_colonia_tgu | 2026-11-07 11:35 | 0 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_ffaa | 2026-11-07 11:33 | 0 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_el_sauce | 2026-11-07 11:41 | 0 | Al día |
| Tegucigalpa | PriceSmart | pricesmart_tgu | 2026-11-07 11:19 | 0 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_multiplaza | 2026-11-07 11:22 | 0 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_proceres | 2026-11-07 11:26 | 0 | Al día |

## Fotos de producto

40,140 de 67,947 productos con foto (59 %). Las fotos v2 se publican en dos tamaños; hd = detalle real para 800 px, sd = suficiente para tarjetas, low = foto chica.

| Ciudad | Supermercado | Productos | Con foto | Sin foto | % | hd | sd | low |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| San Pedro Sula | Total | 34099 | 23478 | 10621 | 69 % | 16145 | 6263 | 127 |
| San Pedro Sula | Colonial | 8465 | 7084 | 1381 | 84 % |  |  |  |
| San Pedro Sula | Los Andes | 6928 | 6055 | 873 | 87 % |  |  |  |
| San Pedro Sula | La Colonia | 7129 | 6185 | 944 | 87 % |  |  |  |
| San Pedro Sula | PriceSmart | 2768 | 1295 | 1473 | 47 % |  |  |  |
| San Pedro Sula | Walmart | 15268 | 9306 | 5962 | 61 % |  |  |  |
| Tegucigalpa | Total | 33848 | 16662 | 17186 | 49 % | 14691 | 1905 | 1 |
| Tegucigalpa | La Colonia | 7614 | 6498 | 1116 | 85 % |  |  |  |
| Tegucigalpa | Paiz | 10351 | 8322 | 2029 | 80 % |  |  |  |
| Tegucigalpa | PriceSmart | 2869 | 1270 | 1599 | 44 % |  |  |  |
| Tegucigalpa | Walmart | 23791 | 10558 | 13233 | 44 % |  |  |  |

## San Pedro Sula

| Problema | Colonial | La Colonia | Los Andes | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 0 | 0 | 8 | 6 | 14 |
| Precio por unidad: probable error | 7 | 7 | 8 | 8 | 26 | 56 |
| Precio por unidad: revisar | 25 | 23 | 17 | 3 | 28 | 96 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 0 | 1 | 1 |
| Sin presentación | 97 | 47 | 125 | 22 | 350 | 641 |
| Corregido en la web | 7 | 5 | 1 | 5 | 3 | 21 |

## Tegucigalpa

| Problema | La Colonia | Paiz | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 2 | 7 | 12 | 21 |
| Precio por unidad: probable error | 10 | 43 | 8 | 41 | 102 |
| Precio por unidad: revisar | 23 | 21 | 4 | 34 | 82 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 2 | 2 |
| Distinto entre ciudades | 10 | 0 | 0 | 1 | 11 |
| Sin presentación | 30 | 39 | 24 | 606 | 699 |
| Corregido en la web | 6 | 6 | 3 | 6 | 21 |

## Qué significa cada problema

- **Peso o volumen imposible** (gravedad alta): La medida que se muestra no puede ser real (más de 50 kg o 60 L en un producto de súper): el súper escribió mal la medida o un código se leyó como peso.
- **Precio por unidad: probable error** (gravedad alta): El precio por kg/L es 100 veces mayor o 20 veces menor que el de su tipo de producto: el tamaño o el precio están mal publicados.
- **Precio por unidad: revisar** (gravedad media): El precio por kg/L es entre 20 y 100 veces mayor que el de su tipo: puede ser error o un producto muy pequeño (té en sobres, repuestos, pegamento).
- **Medida mal escrita por el súper** (gravedad media): El nombre trae un espacio dentro del número ("40 0Ml") y no se puede leer la medida.
- **Distinto entre ciudades** (gravedad media): El mismo producto del mismo súper sale por libra en una ciudad y no en la otra.
- **Sin presentación** (gravedad baja): El nombre termina en un número sin unidad ("Ensure 237"): no se sabe qué mide.
- **Corregido en la web** (gravedad info): El súper publica mal la medida ("1100 Lt", "397 Oz" que son gramos) y la web la corrige con un patrón seguro; sigue mal en el súper.
