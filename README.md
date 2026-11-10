# Errores de datos en Súper Compras

Actualizado automáticamente el 2026-11-10 a partir del catálogo publicado (San Pedro Sula: 2026-11-09T11:44:31.541199Z; Tegucigalpa: 2026-11-09T11:47:07.796569Z).

- [`errores_detectados.csv`](errores_detectados.csv): todo lo que la revisión automática encuentra hoy, una fila por producto y supermercado, con enlace al producto.
- [`errores_confirmados.csv`](errores_confirmados.csv): errores revisados a mano, de dónde vienen (súper o Súper Compras) y su estado.
- [`historial.csv`](historial.csv): cuántos errores había en cada publicación.
- [`frescura.csv`](frescura.csv): última captura aceptada de cada supermercado, día a día.
- [`fotos.csv`](fotos.csv): cobertura de fotos por ciudad y supermercado, día a día.

## Captura diaria

✅ Todos los supermercados se capturaron hoy.

| Ciudad | Supermercado | Sucursal | Última captura (UTC) | Horas | Estado |
|---|---|---|---|---:|---|
| San Pedro Sula | La Colonia | la_colonia_sps | 2026-11-09 11:26 | 0 | Al día |
| San Pedro Sula | Colonial | colonial_sps | 2026-11-09 11:25 | 0 | Al día |
| San Pedro Sula | Walmart | walmart_sps | 2026-11-09 11:24 | 0 | Al día |
| San Pedro Sula | PriceSmart | pricesmart_sps | 2026-11-09 11:17 | 0 | Al día |
| San Pedro Sula | Los Andes | comisariato_los_andes_sps | 2026-11-09 11:18 | 0 | Al día |
| Tegucigalpa | La Colonia | la_colonia_tgu | 2026-11-09 11:35 | 0 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_ffaa | 2026-11-09 11:31 | 0 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_el_sauce | 2026-11-09 11:38 | 0 | Al día |
| Tegucigalpa | PriceSmart | pricesmart_tgu | 2026-11-09 11:18 | 0 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_multiplaza | 2026-11-09 11:22 | 0 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_proceres | 2026-11-09 11:26 | 0 | Al día |

## Fotos de producto

48,205 de 67,646 productos con foto (71 %). Las fotos v2 se publican en dos tamaños; hd = detalle real para 800 px, sd = suficiente para tarjetas, low = foto chica.

| Ciudad | Supermercado | Productos | Con foto | Sin foto | % | hd | sd | low |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| San Pedro Sula | Total | 33953 | 25851 | 8102 | 76 % | 18929 | 6640 | 128 |
| San Pedro Sula | Colonial | 8469 | 7108 | 1361 | 84 % |  |  |  |
| San Pedro Sula | Los Andes | 6928 | 6124 | 804 | 88 % |  |  |  |
| San Pedro Sula | La Colonia | 7129 | 6505 | 624 | 91 % |  |  |  |
| San Pedro Sula | PriceSmart | 2685 | 1398 | 1287 | 52 % |  |  |  |
| San Pedro Sula | Walmart | 15180 | 11152 | 4028 | 73 % |  |  |  |
| Tegucigalpa | Total | 33693 | 22354 | 11339 | 66 % | 19782 | 2499 | 1 |
| Tegucigalpa | La Colonia | 7614 | 6955 | 659 | 91 % |  |  |  |
| Tegucigalpa | Paiz | 10365 | 9121 | 1244 | 88 % |  |  |  |
| Tegucigalpa | PriceSmart | 2823 | 1423 | 1400 | 50 % |  |  |  |
| Tegucigalpa | Walmart | 23639 | 15528 | 8111 | 66 % |  |  |  |

## San Pedro Sula

| Problema | Colonial | La Colonia | Los Andes | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 0 | 0 | 8 | 6 | 14 |
| Precio por unidad: probable error | 8 | 7 | 8 | 7 | 27 | 57 |
| Precio por unidad: revisar | 23 | 23 | 17 | 3 | 28 | 94 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 0 | 1 | 1 |
| Sin presentación | 96 | 48 | 125 | 22 | 350 | 641 |
| Corregido en la web | 7 | 5 | 1 | 6 | 4 | 23 |

## Tegucigalpa

| Problema | La Colonia | Paiz | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 2 | 7 | 12 | 21 |
| Precio por unidad: probable error | 10 | 43 | 7 | 41 | 101 |
| Precio por unidad: revisar | 23 | 22 | 4 | 33 | 82 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 2 | 2 |
| Distinto entre ciudades | 10 | 0 | 0 | 1 | 11 |
| Sin presentación | 30 | 41 | 24 | 597 | 692 |
| Corregido en la web | 6 | 6 | 3 | 5 | 20 |

## Qué significa cada problema

- **Peso o volumen imposible** (gravedad alta): La medida que se muestra no puede ser real (más de 50 kg o 60 L en un producto de súper): el súper escribió mal la medida o un código se leyó como peso.
- **Precio por unidad: probable error** (gravedad alta): El precio por kg/L es 100 veces mayor o 20 veces menor que el de su tipo de producto: el tamaño o el precio están mal publicados.
- **Precio por unidad: revisar** (gravedad media): El precio por kg/L es entre 20 y 100 veces mayor que el de su tipo: puede ser error o un producto muy pequeño (té en sobres, repuestos, pegamento).
- **Medida mal escrita por el súper** (gravedad media): El nombre trae un espacio dentro del número ("40 0Ml") y no se puede leer la medida.
- **Distinto entre ciudades** (gravedad media): El mismo producto del mismo súper sale por libra en una ciudad y no en la otra.
- **Sin presentación** (gravedad baja): El nombre termina en un número sin unidad ("Ensure 237"): no se sabe qué mide.
- **Corregido en la web** (gravedad info): El súper publica mal la medida ("1100 Lt", "397 Oz" que son gramos) y la web la corrige con un patrón seguro; sigue mal en el súper.
