# Errores de datos en Súper Compras

Actualizado automáticamente el 2026-11-03 a partir del catálogo publicado (San Pedro Sula: 2026-11-03T01:07:24.964431Z; Tegucigalpa: 2026-11-03T01:09:11.735260Z).

- [`errores_detectados.csv`](errores_detectados.csv): todo lo que la revisión automática encuentra hoy, una fila por producto y supermercado, con enlace al producto.
- [`errores_confirmados.csv`](errores_confirmados.csv): errores revisados a mano, de dónde vienen (súper o Súper Compras) y su estado.
- [`historial.csv`](historial.csv): cuántos errores había en cada publicación.
- [`frescura.csv`](frescura.csv): última captura aceptada de cada supermercado, día a día.
- [`fotos.csv`](fotos.csv): cobertura de fotos por ciudad y supermercado, día a día.

## Captura diaria

⚠️ 1 sucursal(es) sin captura aceptada hoy; la web muestra la fecha de esos precios.

| Ciudad | Supermercado | Sucursal | Última captura (UTC) | Horas | Estado |
|---|---|---|---|---:|---|
| San Pedro Sula | La Colonia | la_colonia_sps | 2026-11-02 11:27 | 13 | Al día |
| San Pedro Sula | Colonial | colonial_sps | 2026-11-02 11:26 | 13 | Al día |
| San Pedro Sula | Walmart | walmart_sps | 2026-11-02 11:24 | 13 | Al día |
| San Pedro Sula | PriceSmart | pricesmart_sps | 2026-11-02 11:18 | 13 | Al día |
| San Pedro Sula | Los Andes | comisariato_los_andes_sps | 2026-10-21 11:18 | 301 | Atrasado |
| Tegucigalpa | La Colonia | la_colonia_tgu | 2026-11-02 11:36 | 13 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_ffaa | 2026-11-02 11:31 | 13 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_el_sauce | 2026-11-02 11:39 | 13 | Al día |
| Tegucigalpa | PriceSmart | pricesmart_tgu | 2026-11-02 11:18 | 13 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_multiplaza | 2026-11-02 11:21 | 13 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_proceres | 2026-11-02 11:25 | 13 | Al día |

## Fotos de producto

40,224 de 67,495 productos con foto (60 %). Las fotos v2 se publican en dos tamaños; hd = detalle real para 800 px, sd = suficiente para tarjetas, low = foto chica.

| Ciudad | Supermercado | Productos | Con foto | Sin foto | % | hd | sd | low |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| San Pedro Sula | Total | 33787 | 23464 | 10323 | 69 % | 16151 | 6230 | 128 |
| San Pedro Sula | Colonial | 8432 | 7050 | 1382 | 84 % |  |  |  |
| San Pedro Sula | Los Andes | 6740 | 6006 | 734 | 89 % |  |  |  |
| San Pedro Sula | La Colonia | 7176 | 6219 | 957 | 87 % |  |  |  |
| San Pedro Sula | PriceSmart | 2616 | 1270 | 1346 | 49 % |  |  |  |
| San Pedro Sula | Walmart | 15274 | 9357 | 5917 | 61 % |  |  |  |
| Tegucigalpa | Total | 33708 | 16760 | 16948 | 50 % | 14759 | 1928 | 2 |
| Tegucigalpa | La Colonia | 7691 | 6562 | 1129 | 85 % |  |  |  |
| Tegucigalpa | Paiz | 10380 | 8328 | 2052 | 80 % |  |  |  |
| Tegucigalpa | PriceSmart | 2707 | 1260 | 1447 | 47 % |  |  |  |
| Tegucigalpa | Walmart | 23705 | 10593 | 13112 | 45 % |  |  |  |

## San Pedro Sula

| Problema | Colonial | La Colonia | Los Andes | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 0 | 0 | 8 | 6 | 14 |
| Precio por unidad: probable error | 7 | 7 | 8 | 7 | 25 | 54 |
| Precio por unidad: revisar | 26 | 23 | 15 | 3 | 29 | 96 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 0 | 1 | 1 |
| Sin presentación | 89 | 42 | 109 | 21 | 346 | 607 |
| Corregido en la web | 7 | 5 | 1 | 3 | 4 | 20 |

## Tegucigalpa

| Problema | La Colonia | Paiz | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 2 | 7 | 12 | 21 |
| Precio por unidad: probable error | 14 | 45 | 8 | 41 | 108 |
| Precio por unidad: revisar | 23 | 21 | 3 | 34 | 81 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 2 | 2 |
| Distinto entre ciudades | 11 | 0 | 0 | 1 | 12 |
| Sin presentación | 30 | 39 | 22 | 603 | 694 |
| Corregido en la web | 6 | 6 | 2 | 6 | 20 |

## Qué significa cada problema

- **Peso o volumen imposible** (gravedad alta): La medida que se muestra no puede ser real (más de 50 kg o 60 L en un producto de súper): el súper escribió mal la medida o un código se leyó como peso.
- **Precio por unidad: probable error** (gravedad alta): El precio por kg/L es 100 veces mayor o 20 veces menor que el de su tipo de producto: el tamaño o el precio están mal publicados.
- **Precio por unidad: revisar** (gravedad media): El precio por kg/L es entre 20 y 100 veces mayor que el de su tipo: puede ser error o un producto muy pequeño (té en sobres, repuestos, pegamento).
- **Medida mal escrita por el súper** (gravedad media): El nombre trae un espacio dentro del número ("40 0Ml") y no se puede leer la medida.
- **Distinto entre ciudades** (gravedad media): El mismo producto del mismo súper sale por libra en una ciudad y no en la otra.
- **Sin presentación** (gravedad baja): El nombre termina en un número sin unidad ("Ensure 237"): no se sabe qué mide.
- **Corregido en la web** (gravedad info): El súper publica mal la medida ("1100 Lt", "397 Oz" que son gramos) y la web la corrige con un patrón seguro; sigue mal en el súper.
