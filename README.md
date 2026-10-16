# Errores de datos en Súper Compras

Actualizado automáticamente el 2026-10-16 a partir del catálogo publicado (San Pedro Sula: 2026-10-16T04:58:08.045602Z; Tegucigalpa: 2026-10-16T04:59:51.795922Z).

- [`errores_detectados.csv`](errores_detectados.csv): todo lo que la revisión automática encuentra hoy, una fila por producto y supermercado, con enlace al producto.
- [`errores_confirmados.csv`](errores_confirmados.csv): errores revisados a mano, de dónde vienen (súper o Súper Compras) y su estado.
- [`historial.csv`](historial.csv): cuántos errores había en cada publicación.

## San Pedro Sula

| Problema | Colonial | La Colonia | Los Andes | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 0 | 0 | 18 | 29 | 47 |
| Precio por unidad: probable error | 13 | 11 | 13 | 3 | 31 | 71 |
| Precio por unidad: revisar | 22 | 24 | 15 | 6 | 33 | 100 |
| Medida mal escrita por el súper | 0 | 1 | 1 | 0 | 1 | 3 |
| Sin presentación | 83 | 44 | 109 | 20 | 332 | 588 |

## Tegucigalpa

| Problema | La Colonia | Paiz | PriceSmart | Walmart | Total |
|---|---:|---:|---:|---:|---:|
| Peso o volumen imposible | 0 | 7 | 18 | 47 | 72 |
| Precio por unidad: probable error | 16 | 48 | 4 | 45 | 113 |
| Precio por unidad: revisar | 21 | 25 | 4 | 37 | 87 |
| Medida mal escrita por el súper | 0 | 0 | 0 | 2 | 2 |
| Distinto entre ciudades | 12 | 0 | 0 | 2 | 14 |
| Sin presentación | 27 | 36 | 22 | 586 | 671 |

## Qué significa cada problema

- **Peso o volumen imposible** (gravedad alta): La medida que se muestra no puede ser real (más de 50 kg o 60 L en un producto de súper): el súper escribió mal la medida o un código se leyó como peso.
- **Precio por unidad: probable error** (gravedad alta): El precio por kg/L es 100 veces mayor o 20 veces menor que el de su tipo de producto: el tamaño o el precio están mal publicados.
- **Precio por unidad: revisar** (gravedad media): El precio por kg/L es entre 20 y 100 veces mayor que el de su tipo: puede ser error o un producto muy pequeño (té en sobres, repuestos, pegamento).
- **Medida mal escrita por el súper** (gravedad media): El nombre trae un espacio dentro del número ("40 0Ml") y no se puede leer la medida.
- **Distinto entre ciudades** (gravedad media): El mismo producto del mismo súper sale por libra en una ciudad y no en la otra.
- **Sin presentación** (gravedad baja): El nombre termina en un número sin unidad ("Ensure 237"): no se sabe qué mide.
