# Errores de datos en Súper Compras

Actualizado automáticamente el 2026-11-12 a partir del catálogo publicado (San Pedro Sula: 2026-11-12T04:43:40.014840Z; Tegucigalpa: 2026-11-12T04:45:20.266347Z).

- [`errores_detectados.csv`](errores_detectados.csv): todo lo que la revisión automática encuentra hoy, una fila por producto y supermercado, con enlace al producto.
- [`errores_confirmados.csv`](errores_confirmados.csv): errores revisados a mano, de dónde vienen (súper o Súper Compras) y su estado.
- [`historial.csv`](historial.csv): cuántos errores había en cada publicación.
- [`frescura.csv`](frescura.csv): última captura aceptada de cada supermercado, día a día.
- [`fotos.csv`](fotos.csv): cobertura de fotos por ciudad y supermercado, día a día.

## Captura diaria

✅ Todos los supermercados se capturaron hoy.

| Ciudad | Supermercado | Sucursal | Última captura (UTC) | Horas | Estado |
|---|---|---|---|---:|---|
| San Pedro Sula | La Colonia | la_colonia_sps | 2026-11-11 11:27 | 17 | Al día |
| San Pedro Sula | Colonial | colonial_sps | 2026-11-12 00:51 | 3 | Al día |
| San Pedro Sula | Walmart | walmart_sps | 2026-11-11 11:25 | 17 | Al día |
| San Pedro Sula | PriceSmart | pricesmart_sps | 2026-11-11 11:18 | 17 | Al día |
| San Pedro Sula | Los Andes | comisariato_los_andes_sps | 2026-11-11 11:19 | 17 | Al día |
| Tegucigalpa | La Colonia | la_colonia_tgu | 2026-11-11 11:37 | 17 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_ffaa | 2026-11-11 11:33 | 17 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_el_sauce | 2026-11-11 11:40 | 17 | Al día |
| Tegucigalpa | PriceSmart | pricesmart_tgu | 2026-11-11 11:19 | 17 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_multiplaza | 2026-11-11 11:23 | 17 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_proceres | 2026-11-11 11:27 | 17 | Al día |

## Fotos de producto

53,559 de 60,458 productos con foto (89 %). Las fotos v2 se publican en dos tamaños; hd = detalle real para 800 px, sd = suficiente para tarjetas, low = foto chica.

| Ciudad | Supermercado | Productos | Con foto | Sin foto | % | hd | sd | low |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| San Pedro Sula | Total | 33753 | 29691 | 4062 | 88 % | 22398 | 7059 | 130 |
| San Pedro Sula | Colonial | 8417 | 7058 | 1359 | 84 % |  |  |  |
| San Pedro Sula | Los Andes | 6944 | 6156 | 788 | 89 % |  |  |  |
| San Pedro Sula | La Colonia | 7131 | 6968 | 163 | 98 % |  |  |  |
| San Pedro Sula | PriceSmart | 2620 | 1664 | 956 | 64 % |  |  |  |
| San Pedro Sula | Walmart | 15245 | 14344 | 901 | 94 % |  |  |  |
| Tegucigalpa | Total | 26705 | 23868 | 2837 | 89 % | 21058 | 2721 | 4 |
| Tegucigalpa | La Colonia | 7626 | 7464 | 162 | 98 % |  |  |  |
| Tegucigalpa | Paiz | 9690 | 9045 | 645 | 93 % |  |  |  |
| Tegucigalpa | PriceSmart | 2756 | 1706 | 1050 | 62 % |  |  |  |
| Tegucigalpa | Walmart | 17438 | 16354 | 1084 | 94 % |  |  |  |

## San Pedro Sula

| Problema | Colonial | La Colonia | Los Andes | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 0 | 0 | 6 | 6 | 12 |
| Precio por unidad: probable error | 8 | 9 | 8 | 7 | 27 | 59 |
| Precio por unidad: revisar | 22 | 23 | 17 | 3 | 26 | 91 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 0 | 1 | 1 |
| Sin presentación | 96 | 49 | 125 | 22 | 354 | 646 |
| Corregido en la web | 7 | 5 | 1 | 6 | 6 | 25 |

## Tegucigalpa

| Problema | La Colonia | Paiz | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 2 | 7 | 8 | 17 |
| Precio por unidad: probable error | 13 | 31 | 7 | 35 | 86 |
| Precio por unidad: revisar | 22 | 18 | 4 | 22 | 66 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 1 | 1 |
| Distinto entre ciudades | 10 | 0 | 0 | 1 | 11 |
| Sin presentación | 30 | 41 | 24 | 356 | 451 |
| Corregido en la web | 6 | 5 | 3 | 5 | 19 |

## Qué significa cada problema

- **Peso o volumen imposible** (gravedad alta): La medida que se muestra no puede ser real (más de 50 kg o 60 L en un producto de súper): el súper escribió mal la medida o un código se leyó como peso.
- **Precio por unidad: probable error** (gravedad alta): El precio por kg/L es 100 veces mayor o 20 veces menor que el de su tipo de producto: el tamaño o el precio están mal publicados.
- **Precio por unidad: revisar** (gravedad media): El precio por kg/L es entre 20 y 100 veces mayor que el de su tipo: puede ser error o un producto muy pequeño (té en sobres, repuestos, pegamento).
- **Medida mal escrita por el súper** (gravedad media): El nombre trae un espacio dentro del número ("40 0Ml") y no se puede leer la medida.
- **Distinto entre ciudades** (gravedad media): El mismo producto del mismo súper sale por libra en una ciudad y no en la otra.
- **Sin presentación** (gravedad baja): El nombre termina en un número sin unidad ("Ensure 237"): no se sabe qué mide.
- **Corregido en la web** (gravedad info): El súper publica mal la medida ("1100 Lt", "397 Oz" que son gramos) y la web la corrige con un patrón seguro; sigue mal en el súper.
