# Estándar de identidad de producto v1

## Propósito

Este estándar separa el producto publicado por cada supermercado de la identidad
maestra usada para comparar precios. El título parecido nunca basta para declarar
que dos ofertas son el mismo producto.

## Entidades

- **Producto fuente:** fila preservada de `products`; mantiene nombre, marca,
  presentación, categoría, SKU/GTIN y demás evidencia tal como la reportó la
  fuente.
- **Perfil normalizado:** proyección reconstruible con marca, tipo, presentación,
  atributos y conflictos. No reemplaza el producto fuente.
- **Producto maestro:** identidad estable que puede reunir productos fuente sólo
  mediante `EXACT_TRADE_ITEM` o `VERIFIED_EQUIVALENT`.
- **Decisión:** relación auditada entre una pareja, ligada a las huellas de la
  evidencia que se revisó.

## Relaciones

| Relación | Significado | ¿Puede compartir precio comparado? |
| --- | --- | --- |
| `EXACT_TRADE_ITEM` | Mismo artículo comercial; GTIN válido común y sin contradicción material | Sí |
| `VERIFIED_EQUIVALENT` | Mismo artículo confirmado sin GTIN común disponible | Sólo después del gate de publicación |
| `PRODUCT_VARIANT` | Misma familia, pero cambia sabor, fórmula, talla, empaque u otro atributo material | No |
| `COMPARABLE_ALTERNATIVE` | Sustituto útil, no el mismo artículo | No como comparación exacta |
| `UNRESOLVED` | Evidencia insuficiente o revisión pendiente | No |
| `CONFLICT` | Existe una contradicción explícita | No |

## Orden de evidencia

1. GTIN válido común.
2. Catálogo del fabricante o marca.
3. Código de barras legible en el empaque.
4. Frente y reverso del empaque.
5. Página de detalle del supermercado.
6. Conversión explícita de unidades.
7. Título y campos del supermercado.

Imagen parecida, score textual o afirmación de IA sólo generan candidatos. No
confirman identidad por sí mismos.

## Reglas de decisión

1. Los datos fuente nunca se sobrescriben.
2. Dos GTIN válidos diferentes bloquean una identidad exacta.
3. Un conflicto explícito de marca, tipo, presentación, variante, sabor o empaque
   bloquea la unión. Un dato ausente no constituye conflicto.
4. `oz` no se convierte automáticamente a mililitros: debe demostrarse que el
   empaque declara `fl oz`.
5. Una equivalencia sin GTIN requiere decisión revisada, evidencia citada,
   producto maestro explícito y huellas vigentes de ambos registros.
6. Si cambia cualquiera de los registros fuente o la versión del motor, la
   decisión queda obsoleta y vuelve a `UNRESOLVED`.
7. La transitividad no se presume. Antes de formar un grupo, todas sus parejas
   materiales deben ser compatibles y estar respaldadas.
8. No puede haber dos productos fuente del mismo supermercado dentro de una
   identidad publicada sin resolver primero cuál variante/oferta representa.
   Desde v2.4 los registros en colisión se excluyen todos (no se elige uno) y
   el resto del grupo puede seguir comparable (regla 11).
9. **GTIN de circulación restringida (v2.4).** Un GTIN válido por check digit
   en un rango RCN de GS1 no es global: EAN-8 que empieza con 0 o 2, prefijos
   GTIN-13 020–029 y 040–049 (UPC-A 2… y 4…), 200–299 (peso variable / uso en
   tienda), 980–999 (devoluciones y cupones) y GTIN-14 con indicador 9. Sólo
   sostiene `prod_gtin_*` entre cadenas que comparten maestro de productos:
   `walmart_cam` = Walmart + Paiz (mismo catálogo VTEX de Walmart
   Centroamérica). La Colonia SPS/TGU es un único `supermarket_id`. Fuera del
   maestro la pareja queda `UNRESOLVED` (`restricted_gtin_outside_shared_master`).
   Política en `gtin_policy.py`, espejo en `identity-policy-v1.yaml`.
10. **Variante declarada de un solo lado (v2.4).** Un sabor/aroma (`fresa`,
    `lavanda`, `pollo`) o una formulación no estándar (`zero`, `sin azúcar`,
    `light`, `diet`) presente en un nombre y ausente en el otro impide la
    identidad automática aunque compartan GTIN (`one_sided_flavor_declared`,
    `one_sided_variant_declared`). Sin GTIN la pareja sigue como candidato de
    revisión y nunca recibe confianza STRONG.
11. **Exclusión por miembro (v2.4).** En un grupo GTIN, un conflicto atribuible
    a un miembro lo retira (`member_excluded_from_ready_group`) y el resto sigue
    `ready` si conserva dos o más cadenas y ninguna colisión. Orden: GTIN
    restringido fuera del maestro, colisión por cadena, conflicto intrínseco
    (presentación en conflicto, multipack ambiguo) y conflictos por pareja; en
    estos se retiran por rondas los miembros con más conflictos y un empate
    retira a todos los empatados. Sin núcleo de dos cadenas, todo el grupo queda
    en revisión.
12. **Presentación (v2.4).** Con etiqueta dual (`16 oz (454 g)`) la firma canónica
    es la métrica declarada por el propio envase; las onzas se conservan como
    evidencia auxiliar y sólo se comparan contra onzas (regla 4 intacta). Si las
    dos etiquetas difieren más de 8 % la presentación no se resuelve. Multipacks
    (`6x355ml`, `355 ml x 6`, `12 latas de 355 ml`) exponen conteo y total; un
    multipack nunca equivale a la unidad. Combos, kits, "gratis", `2x1` y
    "cantidad + cantidad" son bundles: `bundle_vs_single_conflict` frente a un
    individual.
13. **Captura de GTIN por fuente.** `ean` recibe un barcode explícito de la
    fuente que supera el check digit GS1. **Colonial (aprobado 2026-10-01):**
    no publica `barcode`, así que un `sku` recortado, sólo dígitos, de 8/12/13/14
    dígitos y GS1 válido se acepta como GTIN con procedencia `sku_gs1_valid`
    (derivable: `ean == reference`). Un SKU no numérico o con check digit
    inválido nunca produce GTIN. Ese GTIN sólo crea identidad si coincide con el
    de otra cadena, respeta la regla 9 y no tiene conflicto material; además, una
    marca contradictoria (ninguna aparece en el nombre del otro y no son
    variantes ortográficas) excluye al miembro (`sku_gtin_brand_conflict`).
    PriceSmart y Comisariato no exponen barcode.
14. **Abreviaturas (v2.4).** Para detectar variantes se separan palabras
    pegadas ("AlmendVainiSinAzucar") y se expande una tabla cerrada de
    abreviaturas observadas (`vaini`→vainilla, `meloctn`→melocotón, `s azu`/
    `sugar free`→sin azúcar…). "S/A" no se expande (choca con "S.A.").

## Casos iniciales

- **Nutri Yema 554 g vs 1.2 lb:** candidato fuerte, pero `UNRESOLVED` con los
  campos actualmente persistidos. Las cantidades difieren por aproximadamente
  1.8 % y una fuente omite la marca estructurada. Frente/reverso o código de
  barras deben confirmar la declaración comercial antes de aprobarlo.
- **Kraft Ranch Classic 8 oz vs 237 ml:** `UNRESOLVED`. La equivalencia numérica
  sólo es válida si `8 oz` significa `8 fl oz`, y `Classic` no puede ignorarse si
  el segundo producto declara otra variante.
- **Classic vs Light:** `CONFLICT`; no se fusiona aunque marca y contenido sean
  iguales.
- **Gwaltney GTIN 785331778506 (v2.4):** "Salchichas Tradicional" (Walmart/Paiz)
  vs "Salchicha … De Pollo Bun Size" (La Colonia), ratio de precio 2.4: el sabor
  declarado sólo por La Colonia la excluye; Walmart+Paiz siguen comparables.
- **Croissant `0000000001083` (v2.4):** PLU interno (RCN-8). Walmart+Paiz es
  `EXACT_TRADE_ITEM`; con La Colonia queda `UNRESOLVED`.

## Despliegue

La versión v1 opera inicialmente en `shadow`: crea y valida decisiones privadas,
pero no cambia el catálogo B2C. Para habilitar publicación se exige un conjunto
de verdad base, precisión medida, cero regresiones críticas y una corrida shadow
sobre el catálogo completo.
