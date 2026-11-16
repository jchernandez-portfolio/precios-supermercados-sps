# Diagnóstico: frutas y verduras, duplicados (16-nov-2026)

Fuentes: captura diaria 43856470870 (16-nov, artifacts por súper) y catálogo
publicado en `portfolio-data` (as_of 2026-11-16T11:49Z). Sólo lectura.

## 1. Frutas y verduras

No se pierden en el scraper ni por el GTIN. De la categoría «frutas y verduras» de cada súper:

| Súper | Capturadas | Publicadas (directas + agrupadas por GTIN) | Fuera por agotadas |
|---|---:|---:|---:|
| La Colonia | 160 | 125 | 35 |
| Colonial | 286 | 239 | 47 |
| Walmart | 164 | 117 | 47 |
| Los Andes | 303 | 303 | 0 (Los Andes no publica agotado) |
| PriceSmart | sin categoría propia | 16 (en «Alimentos») | — |

Lo que sí pasa:

- **No se comparan**: lo suelto (por libra o unidad) no trae GTIN; cada súper queda en su
  propia tarjeta (592 «individual» + 88 de una fuente vs 65 comparables, que son los
  empacados con código: La Carreta, Pyflor).
- **Walmart «N Unidades Por Lb. Aproximadamente»** salía como «3 unidades · L 2.93 por
  unidad» cuando el precio es por libra (23 filas). **Arreglado en este PR**.
- Colonial «X Libra» sin presentación publicada: sin precio por libra en algunas filas.

**Propuesta (no implementada)**: agrupar lo suelto por nombre normalizado + unidad de venta
(`libra` | `unidad`), nunca mezclando unidades. Prototipo: 435 filas sueltas, 346 con
unidad clara, **45 grupos en 2+ súper** (96 filas). Riesgos vistos y guardas necesarias:
- precio fuera de 0.5×–2× la mediana del grupo (p. ej. «Brocoli Importado Lb» L 148.70
  vs L 29–40 en otros) → fuera del grupo;
- «importado» vs nacional y variedades (tomate manzano ≠ pera) se mantienen separados;
- nombres con peso de paquete («Limon Amarillo 33 Libras») fuera.

## 2. Duplicados

**Queso Mozzarella Zamorano 440 g**: ya resuelto en `main`; La Colonia y Colonial
comparten GTIN 07422901300158 y salen en una sola tarjeta comparable.

**Chorizo Campeño Zamorano (PriceSmart, L 244.95)**: dos ofertas internas
(`pricesmart:35393`, vigente desde 1-sep, y `pricesmart:63187`, desde 2-nov) para un
artículo que hoy PriceSmart publica una sola vez (pid 452138). Una es una **oferta no
vista** que nunca se cerró. Se arregla con `hide_unseen_offers=true` (pendiente del dueño;
PriceSmart tiene 2,061 ofertas no vistas).

**Variantes de PriceSmart**: PriceSmart captura una fila por SKU; 391 productos tienen 2+
variantes con precio y **el mismo nombre** (p. ej. Delafaille Bombones, 2 SKU `26181-…`
a L 499.95). En el catálogo hay 159 parejas nombre+precio repetidas en PriceSmart (mezcla
de variantes y ofertas no vistas). Propuesta: una sola tarjeta por `pid` cuando las
variantes comparten título (precio más bajo, «N variantes»); requiere llevar el `pid` de
PriceSmart hasta el exportador.
