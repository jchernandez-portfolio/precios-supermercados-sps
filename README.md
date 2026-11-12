# Errores de datos en Súper Compras

Actualizado automáticamente el 2026-11-12 a partir del catálogo publicado (San Pedro Sula: 2026-11-12T00:59:40.506938Z; Tegucigalpa: 2026-11-12T01:02:11.731230Z).

- [`errores_detectados.csv`](errores_detectados.csv): todo lo que la revisión automática encuentra hoy, una fila por producto y supermercado, con enlace al producto.
- [`errores_confirmados.csv`](errores_confirmados.csv): errores revisados a mano, de dónde vienen (súper o Súper Compras) y su estado.
- [`historial.csv`](historial.csv): cuántos errores había en cada publicación.
- [`frescura.csv`](frescura.csv): última captura aceptada de cada supermercado, día a día.
- [`fotos.csv`](fotos.csv): cobertura de fotos por ciudad y supermercado, día a día.

## Captura diaria

✅ Todos los supermercados se capturaron hoy.

| Ciudad | Supermercado | Sucursal | Última captura (UTC) | Horas | Estado |
|---|---|---|---|---:|---|
| San Pedro Sula | La Colonia | la_colonia_sps | 2026-11-11 11:27 | 13 | Al día |
| San Pedro Sula | Colonial | colonial_sps | 2026-11-12 00:51 | 0 | Al día |
| San Pedro Sula | Walmart | walmart_sps | 2026-11-11 11:25 | 13 | Al día |
| San Pedro Sula | PriceSmart | pricesmart_sps | 2026-11-11 11:18 | 13 | Al día |
| San Pedro Sula | Los Andes | comisariato_los_andes_sps | 2026-11-11 11:19 | 13 | Al día |
| Tegucigalpa | La Colonia | la_colonia_tgu | 2026-11-11 11:37 | 13 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_ffaa | 2026-11-11 11:33 | 13 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_el_sauce | 2026-11-11 11:40 | 13 | Al día |
| Tegucigalpa | PriceSmart | pricesmart_tgu | 2026-11-11 11:19 | 13 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_multiplaza | 2026-11-11 11:23 | 13 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_proceres | 2026-11-11 11:27 | 13 | Al día |

## Fotos de producto

59,324 de 67,877 productos con foto (87 %). Las fotos v2 se publican en dos tamaños; hd = detalle real para 800 px, sd = suficiente para tarjetas, low = foto chica.

| Ciudad | Supermercado | Productos | Con foto | Sin foto | % | hd | sd | low |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| San Pedro Sula | Total | 33939 | 29852 | 4087 | 88 % | 22446 | 7156 | 131 |
| San Pedro Sula | Colonial | 8417 | 7114 | 1303 | 85 % |  |  |  |
| San Pedro Sula | Los Andes | 6944 | 6209 | 735 | 89 % |  |  |  |
| San Pedro Sula | La Colonia | 7131 | 7010 | 121 | 98 % |  |  |  |
| San Pedro Sula | PriceSmart | 2620 | 1661 | 959 | 63 % |  |  |  |
| San Pedro Sula | Walmart | 15245 | 14273 | 972 | 94 % |  |  |  |
| Tegucigalpa | Total | 33938 | 29472 | 4466 | 87 % | 26226 | 3233 | 4 |
| Tegucigalpa | La Colonia | 7626 | 7472 | 154 | 98 % |  |  |  |
| Tegucigalpa | Paiz | 10365 | 9198 | 1167 | 89 % |  |  |  |
| Tegucigalpa | PriceSmart | 2756 | 1695 | 1061 | 62 % |  |  |  |
| Tegucigalpa | Walmart | 23938 | 21781 | 2157 | 91 % |  |  |  |

## San Pedro Sula

| Problema | Colonial | La Colonia | Los Andes | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 0 | 0 | 6 | 6 | 12 |
| Precio por unidad: probable error | 8 | 7 | 8 | 7 | 28 | 58 |
| Precio por unidad: revisar | 22 | 23 | 17 | 3 | 26 | 91 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 0 | 1 | 1 |
| Sin presentación | 96 | 49 | 125 | 22 | 354 | 646 |
| Corregido en la web | 7 | 5 | 1 | 6 | 6 | 25 |

## Tegucigalpa

| Problema | La Colonia | Paiz | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 2 | 7 | 12 | 21 |
| Precio por unidad: probable error | 10 | 42 | 7 | 39 | 98 |
| Precio por unidad: revisar | 22 | 21 | 4 | 33 | 80 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 2 | 2 |
| Distinto entre ciudades | 10 | 0 | 0 | 1 | 11 |
| Sin presentación | 30 | 41 | 24 | 598 | 693 |
| Corregido en la web | 6 | 6 | 2 | 6 | 20 |

## Qué significa cada problema

- **Peso o volumen imposible** (gravedad alta): La medida que se muestra no puede ser real (más de 50 kg o 60 L en un producto de súper): el súper escribió mal la medida o un código se leyó como peso.
- **Precio por unidad: probable error** (gravedad alta): El precio por kg/L es 100 veces mayor o 20 veces menor que el de su tipo de producto: el tamaño o el precio están mal publicados.
- **Precio por unidad: revisar** (gravedad media): El precio por kg/L es entre 20 y 100 veces mayor que el de su tipo: puede ser error o un producto muy pequeño (té en sobres, repuestos, pegamento).
- **Medida mal escrita por el súper** (gravedad media): El nombre trae un espacio dentro del número ("40 0Ml") y no se puede leer la medida.
- **Distinto entre ciudades** (gravedad media): El mismo producto del mismo súper sale por libra en una ciudad y no en la otra.
- **Sin presentación** (gravedad baja): El nombre termina en un número sin unidad ("Ensure 237"): no se sabe qué mide.
- **Corregido en la web** (gravedad info): El súper publica mal la medida ("1100 Lt", "397 Oz" que son gramos) y la web la corrige con un patrón seguro; sigue mal en el súper.
