# Errores de datos en Súper Compras

Actualizado automáticamente el 2026-11-11 a partir del catálogo publicado (San Pedro Sula: 2026-11-10T23:36:19.578852Z; Tegucigalpa: 2026-11-10T23:39:24.065652Z).

- [`errores_detectados.csv`](errores_detectados.csv): todo lo que la revisión automática encuentra hoy, una fila por producto y supermercado, con enlace al producto.
- [`errores_confirmados.csv`](errores_confirmados.csv): errores revisados a mano, de dónde vienen (súper o Súper Compras) y su estado.
- [`historial.csv`](historial.csv): cuántos errores había en cada publicación.
- [`frescura.csv`](frescura.csv): última captura aceptada de cada supermercado, día a día.
- [`fotos.csv`](fotos.csv): cobertura de fotos por ciudad y supermercado, día a día.

## Captura diaria

✅ Todos los supermercados se capturaron hoy.

| Ciudad | Supermercado | Sucursal | Última captura (UTC) | Horas | Estado |
|---|---|---|---|---:|---|
| San Pedro Sula | La Colonia | la_colonia_sps | 2026-11-10 11:28 | 12 | Al día |
| San Pedro Sula | Colonial | colonial_sps | 2026-11-10 23:29 | 0 | Al día |
| San Pedro Sula | Walmart | walmart_sps | 2026-11-10 11:23 | 12 | Al día |
| San Pedro Sula | PriceSmart | pricesmart_sps | 2026-11-10 11:17 | 12 | Al día |
| San Pedro Sula | Los Andes | comisariato_los_andes_sps | 2026-11-10 11:18 | 12 | Al día |
| Tegucigalpa | La Colonia | la_colonia_tgu | 2026-11-10 11:38 | 12 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_ffaa | 2026-11-10 11:30 | 12 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_el_sauce | 2026-11-10 11:37 | 12 | Al día |
| Tegucigalpa | PriceSmart | pricesmart_tgu | 2026-11-10 11:18 | 12 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_multiplaza | 2026-11-10 11:21 | 12 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_proceres | 2026-11-10 11:25 | 12 | Al día |

## Fotos de producto

58,713 de 67,444 productos con foto (87 %). Las fotos v2 se publican en dos tamaños; hd = detalle real para 800 px, sd = suficiente para tarjetas, low = foto chica.

| Ciudad | Supermercado | Productos | Con foto | Sin foto | % | hd | sd | low |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| San Pedro Sula | Total | 33858 | 29681 | 4177 | 88 % | 22314 | 7116 | 131 |
| San Pedro Sula | Colonial | 8420 | 7109 | 1311 | 84 % |  |  |  |
| San Pedro Sula | Los Andes | 6944 | 6195 | 749 | 89 % |  |  |  |
| San Pedro Sula | La Colonia | 7131 | 7010 | 121 | 98 % |  |  |  |
| San Pedro Sula | PriceSmart | 2663 | 1646 | 1017 | 62 % |  |  |  |
| San Pedro Sula | Walmart | 15109 | 14128 | 981 | 94 % |  |  |  |
| Tegucigalpa | Total | 33586 | 29032 | 4554 | 86 % | 25865 | 3154 | 4 |
| Tegucigalpa | La Colonia | 7572 | 7417 | 155 | 98 % |  |  |  |
| Tegucigalpa | Paiz | 10362 | 9190 | 1172 | 89 % |  |  |  |
| Tegucigalpa | PriceSmart | 2820 | 1696 | 1124 | 60 % |  |  |  |
| Tegucigalpa | Walmart | 23543 | 21365 | 2178 | 91 % |  |  |  |

## San Pedro Sula

| Problema | Colonial | La Colonia | Los Andes | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 0 | 0 | 8 | 6 | 14 |
| Precio por unidad: probable error | 8 | 7 | 8 | 7 | 27 | 57 |
| Precio por unidad: revisar | 22 | 23 | 17 | 3 | 27 | 92 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 0 | 1 | 1 |
| Sin presentación | 95 | 50 | 125 | 22 | 352 | 644 |
| Corregido en la web | 7 | 5 | 1 | 7 | 4 | 24 |

## Tegucigalpa

| Problema | La Colonia | Paiz | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 2 | 7 | 12 | 21 |
| Precio por unidad: probable error | 10 | 42 | 7 | 39 | 98 |
| Precio por unidad: revisar | 22 | 22 | 4 | 33 | 81 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 2 | 2 |
| Distinto entre ciudades | 10 | 0 | 0 | 1 | 11 |
| Sin presentación | 30 | 41 | 24 | 593 | 688 |
| Corregido en la web | 6 | 5 | 3 | 5 | 19 |

## Qué significa cada problema

- **Peso o volumen imposible** (gravedad alta): La medida que se muestra no puede ser real (más de 50 kg o 60 L en un producto de súper): el súper escribió mal la medida o un código se leyó como peso.
- **Precio por unidad: probable error** (gravedad alta): El precio por kg/L es 100 veces mayor o 20 veces menor que el de su tipo de producto: el tamaño o el precio están mal publicados.
- **Precio por unidad: revisar** (gravedad media): El precio por kg/L es entre 20 y 100 veces mayor que el de su tipo: puede ser error o un producto muy pequeño (té en sobres, repuestos, pegamento).
- **Medida mal escrita por el súper** (gravedad media): El nombre trae un espacio dentro del número ("40 0Ml") y no se puede leer la medida.
- **Distinto entre ciudades** (gravedad media): El mismo producto del mismo súper sale por libra en una ciudad y no en la otra.
- **Sin presentación** (gravedad baja): El nombre termina en un número sin unidad ("Ensure 237"): no se sabe qué mide.
- **Corregido en la web** (gravedad info): El súper publica mal la medida ("1100 Lt", "397 Oz" que son gramos) y la web la corrige con un patrón seguro; sigue mal en el súper.
