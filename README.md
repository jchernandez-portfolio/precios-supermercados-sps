# Errores de datos en Súper Compras

Actualizado automáticamente el 2026-10-27 a partir del catálogo publicado (San Pedro Sula: 2026-10-27T11:28:24.181932Z; Tegucigalpa: 2026-10-27T11:30:29.429417Z).

- [`errores_detectados.csv`](errores_detectados.csv): todo lo que la revisión automática encuentra hoy, una fila por producto y supermercado, con enlace al producto.
- [`errores_confirmados.csv`](errores_confirmados.csv): errores revisados a mano, de dónde vienen (súper o Súper Compras) y su estado.
- [`historial.csv`](historial.csv): cuántos errores había en cada publicación.
- [`frescura.csv`](frescura.csv): última captura aceptada de cada supermercado, día a día.
- [`fotos.csv`](fotos.csv): cobertura de fotos por ciudad y supermercado, día a día.

## Captura diaria

⚠️ 1 sucursal(es) sin captura aceptada hoy; la web muestra la fecha de esos precios.

| Ciudad | Supermercado | Sucursal | Última captura (UTC) | Horas | Estado |
|---|---|---|---|---:|---|
| San Pedro Sula | La Colonia | la_colonia_sps | 2026-10-26 11:29 | 23 | Al día |
| San Pedro Sula | Colonial | colonial_sps | 2026-10-26 11:26 | 24 | Al día |
| San Pedro Sula | Walmart | walmart_sps | 2026-10-26 11:25 | 24 | Al día |
| San Pedro Sula | PriceSmart | pricesmart_sps | 2026-10-26 11:18 | 24 | Al día |
| San Pedro Sula | Los Andes | comisariato_los_andes_sps | 2026-10-21 11:18 | 144 | Atrasado |
| Tegucigalpa | La Colonia | la_colonia_tgu | 2026-10-26 11:40 | 23 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_ffaa | 2026-10-26 11:34 | 23 | Al día |
| Tegucigalpa | Walmart | walmart_tgu_el_sauce | 2026-10-26 11:42 | 23 | Al día |
| Tegucigalpa | PriceSmart | pricesmart_tgu | 2026-10-26 11:18 | 24 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_multiplaza | 2026-10-26 11:22 | 24 | Al día |
| Tegucigalpa | Paiz | paiz_tgu_proceres | 2026-10-26 11:27 | 24 | Al día |

## Fotos de producto

30,652 de 66,981 productos con foto (46 %). Las fotos v2 se publican en dos tamaños; hd = detalle real para 800 px, sd = suficiente para tarjetas, low = foto chica.

| Ciudad | Supermercado | Productos | Con foto | Sin foto | % | hd | sd | low |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| San Pedro Sula | Total | 33557 | 17036 | 16521 | 51 % | 11831 | 4168 | 74 |
| San Pedro Sula | Colonial | 8433 | 5656 | 2777 | 67 % |  |  |  |
| San Pedro Sula | Los Andes | 6740 | 4626 | 2114 | 69 % |  |  |  |
| San Pedro Sula | La Colonia | 7010 | 4948 | 2062 | 71 % |  |  |  |
| San Pedro Sula | PriceSmart | 2613 | 807 | 1806 | 31 % |  |  |  |
| San Pedro Sula | Walmart | 15191 | 7417 | 7774 | 49 % |  |  |  |
| Tegucigalpa | Total | 33424 | 13616 | 19808 | 41 % | 12168 | 1377 | 3 |
| Tegucigalpa | La Colonia | 7449 | 4892 | 2557 | 66 % |  |  |  |
| Tegucigalpa | Paiz | 10271 | 8145 | 2126 | 79 % |  |  |  |
| Tegucigalpa | PriceSmart | 2627 | 827 | 1800 | 31 % |  |  |  |
| Tegucigalpa | Walmart | 23698 | 9625 | 14073 | 41 % |  |  |  |

## San Pedro Sula

| Problema | Colonial | La Colonia | Los Andes | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 0 | 0 | 9 | 7 | 16 |
| Precio por unidad: probable error | 7 | 6 | 8 | 7 | 26 | 54 |
| Precio por unidad: revisar | 18 | 17 | 15 | 3 | 21 | 74 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 0 | 1 | 1 |
| Sin presentación | 95 | 48 | 109 | 19 | 343 | 614 |
| Corregido en la web | 7 | 5 | 1 | 5 | 5 | 23 |

## Tegucigalpa

| Problema | La Colonia | Paiz | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 2 | 8 | 12 | 22 |
| Precio por unidad: probable error | 14 | 45 | 7 | 37 | 103 |
| Precio por unidad: revisar | 22 | 21 | 3 | 34 | 80 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 2 | 2 |
| Distinto entre ciudades | 10 | 0 | 0 | 1 | 11 |
| Sin presentación | 28 | 37 | 21 | 596 | 682 |
| Corregido en la web | 6 | 6 | 2 | 6 | 20 |

## Qué significa cada problema

- **Peso o volumen imposible** (gravedad alta): La medida que se muestra no puede ser real (más de 50 kg o 60 L en un producto de súper): el súper escribió mal la medida o un código se leyó como peso.
- **Precio por unidad: probable error** (gravedad alta): El precio por kg/L es 100 veces mayor o 20 veces menor que el de su tipo de producto: el tamaño o el precio están mal publicados.
- **Precio por unidad: revisar** (gravedad media): El precio por kg/L es entre 20 y 100 veces mayor que el de su tipo: puede ser error o un producto muy pequeño (té en sobres, repuestos, pegamento).
- **Medida mal escrita por el súper** (gravedad media): El nombre trae un espacio dentro del número ("40 0Ml") y no se puede leer la medida.
- **Distinto entre ciudades** (gravedad media): El mismo producto del mismo súper sale por libra en una ciudad y no en la otra.
- **Sin presentación** (gravedad baja): El nombre termina en un número sin unidad ("Ensure 237"): no se sabe qué mide.
- **Corregido en la web** (gravedad info): El súper publica mal la medida ("1100 Lt", "397 Oz" que son gramos) y la web la corrige con un patrón seguro; sigue mal en el súper.
