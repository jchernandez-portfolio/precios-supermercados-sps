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
  mediante `EXACT_TRADE_ITEM` o `VERIFIED_EQUIVALENT`. Desde 2026-10-01 se
  persiste en `master_products` (id `mp_*`, atributos golden con procedencia)
  y la pertenencia en `master_product_links` (método, evidencia, autor,
  historial); ver regla 20 y [`product-master-v1.md`](product-master-v1.md).
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
   empaque declara `fl oz`. Desde v2.5 sí se convierte a gramos (onza de peso)
   cuando el tipo de producto es sólido (regla 15).
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
    También exige acuerdo mínimo de nombre tras quitar marca, tamaño y
    palabras de empaque y expandir abreviaturas/traducciones: al menos un token
    significativo común y que coincida ≥1/3 de los tokens del lado más corto
    (`sku_gtin_name_disagreement`; sin tokens significativos también falla). En
    maquillaje y tinte, números de tono/modelo distintos ("Light 20" vs "Light
    Honey 120") son `model_number_conflict`. Estas guardas no aplican a barcodes
    explícitos.
    PriceSmart no expone barcode; Comisariato reconstruye el GTIN desde su
    código fuente (regla 19).
14. **Abreviaturas (v2.4).** Para detectar variantes se separan palabras
    pegadas ("AlmendVainiSinAzucar") y se expande una tabla cerrada de
    abreviaturas observadas (`vaini`→vainilla, `meloctn`→melocotón, `s azu`/
    `sugar free`→sin azúcar…). "S/A" no se expande (choca con "S.A.").

15. **Presentación (v2.5).**
    - *Separador de miles:* `1,400 ml`, `1.750 ml`, `1,230 g`, `1.000 unidades`
      son miles cuando la parte entera tiene 1–3 dígitos sin empezar en 0, el
      separador va seguido de **exactamente 3 dígitos** y la unidad es pequeña
      (ml, cc, mg, g/gr/gramos o conteo). Con l/kg/lb/oz el separador es
      decimal (`1.892 L`, `2.268 Kg`, `3.125 oz`); `1,5 L` (un dígito) y
      `0.946 ml` (parte entera 0) tampoco cambian. Ambiguo residual: un `1.500 g`
      que de verdad fuera 1.5 g (azafrán) se leería 1500 g; no se observó en el
      catálogo. Una presentación fuente idéntica a la lectura decimal errónea
      del nombre (`1.4 ml` para `1,400 ml`) no es evidencia independiente.
    - *Imperial vs métrico:* con libras y gramos en el mismo texto
      (`623.7 g / 1.37 lb`) la métrica explícita es canónica; si difieren más de
      8 % no se resuelve. Una fuente que repite la conversión imperial de una
      etiqueta dual coherente se acepta (`1.3 kg / 3 lb` con fuente 1360.8 g).
      Sólo-libras se convierte a gramos (1 lb = 453.59237 g); sólo-onzas, a
      gramos (28.3495 g) **únicamente** en tipos sólidos (arroz, avena, azúcar,
      café, cereal, chocolate, frijol, galleta, gelatina, harinas, leche en
      polvo, mantequilla(s), margarina, pan, pasta, pasta de tomate, queso, sal,
      sardina, atún, yogurt). En cualquier otro tipo la onza queda como `oz`:
      sin conversión no crea conflictos nuevos contra fuentes en ml.
    - *Tolerancia imperial:* una cantidad derivada de libras/onzas es compatible
      con una métrica hasta 2 % (redondeo del fabricante: `1 lb` ≈ `450 g`);
      entre dos etiquetas métricas sigue 0.5 % o 1.5 unidades.
    - *Display:* se redondea a 2 decimales (3 cifras significativas bajo 1);
      el total canónico exacto se conserva para comparar.
16. **Marca desde el nombre (v2.5).** Sólo cuando la fuente no envía marca (o
    envía un placeholder). El léxico son las marcas fuente de todas las cadenas,
    con alias por forma compacta (la grafía más frecuente gana: `loreal`/`l
    oreal`, `kelloggs`/`kellogg s`, `ORALB`) y posesivo unido. Nunca se extraen
    palabras comunes de una lista cerrada (`original`, `premium`, `pan`, `sin`,
    `xl`…); una marca-palabra que aparece ≥10 veces y ≥3× más en nombres de
    otras marcas que como marca propia sólo vale como primera palabra y sin otra
    marca. Con varias marcas gana la que abre el nombre; si ninguna abre, queda
    ausente. Una marca fuente sólo se anula (`source_conflict`) si el nombre
    contiene una única marca, no genérica, que no es de la misma familia
    (contención o prefijo de 4 letras). Procedencia: `brand_resolution_source`
    = `name_known_brand`.
17. **Marca inferida en guardas (v2.5).** `brand_conflict` entre candidatos no
    aplica cuando una de las marcas salió del nombre y cualquiera de las dos
    aparece en el otro nombre (fabricante vs línea: Nestlé vs Nesquik). En el
    acuerdo de nombre de un GTIN derivado de SKU (regla 13) siempre se excluyen
    las marcas declaradas; una marca inferida se acepta excluida o como token.
18. **Tipo de producto (v2.5).** Un tipo alimenticio asignado por palabra clave
    se retira (o pasa al tipo no alimenticio correcto) ante contexto
    contradictorio: aceite de motor (`20W50`, `ATF`, moto), pintura/esmalte,
    aceite cosmético (argán, cabello, bebé), pasta dental (`Pasta Repara`,
    Colgate → Pasta dental), alimento de mascota con sabor (Felix atún →
    Alimento para gato; no aplica a bebidas: "Vino Gato Negro"), objeto o color
    café antes del sustantivo, gel para cabello, sal de baño, leche corporal o
    de magnesia. "S/Azúcar"/"Zero Azúcar" no es Azúcar. Si el departamento de la
    ruta fuente es General/Hogar/Limpieza/Cuidado personal, un tipo alimenticio
    por nombre se descarta (Salud y temporada no cuentan). Sin tipo por nombre,
    una palabra clave al inicio de la **hoja** de la ruta tipa como evidencia
    débil (`taxonomy_rule_id = source_category_keyword`): nunca genera
    `product_type_conflict`; si no hay tipo, el departamento llena la categoría.

19. **Comisariato: `code` = GTIN sin dígito de control (v2.6, aprobado
    2026-10-01).** El `code` fuente (`0001-` + 15 dígitos) es el GTIN sin su
    dígito de control, rellenado con ceros. Se quita `0001-` y los ceros, y según
    la longitud de la base se agrega el dígito de control GS1: 7 → EAN-8;
    10 → UPC-A cuyo 0 inicial también se quitó (se rellena a 11:
    `7107203054` → `071072030547`); 11 → UPC-A; 12 → EAN-13
    (`0001-000744102955677` → `7441029556773`). Exclusiones: bases de otras
    longitudes (3–4, 8–9 dígitos), bases que empiezan con `99` (códigos
    internos, p. ej. `99001005224`) y cualquier resultado en rango GS1
    restringido de la regla 9 (peso variable/PLU en tienda como `24153000000`
    "Delicia jamon pollo lb plu 133" o `29801000000` "Pan molido libra"): en
    esos casos `ean` queda nulo. `reference` conserva el código fuente y la
    procedencia se deriva (`ean == gtin_from_code(reference)` ⇒
    `sku_reconstructed_check_digit`) sin llaves nuevas en el snapshot. El GTIN
    reconstruido se trata igual que un GTIN derivado de SKU Colonial (regla 13):
    sólo crea identidad si coincide con el GTIN de otra cadena y supera las
    guardas de marca, acuerdo mínimo de nombre, tono/modelo y los conflictos de
    tamaño/variante/tipo. La marca placeholder `Marca COMANDES` nunca contradice.

20. **Producto maestro y vínculos (v1, 2026-10-01).** La identidad deja de ser
    sólo `canonical_product_id`: cada producto fuente tiene a lo sumo un
    vínculo activo a un `master_product_id` estable (`mp_*`; el de un maestro
    nacido de un GTIN se deriva del GTIN, el de uno sin GTIN de la decisión
    que lo crea). Métodos: `gtin_exact` y `gtin_sku_derived`
    (`EXACT_TRADE_ITEM`, decididos por `system` y equivalentes 1:1 a los
    perfiles `ready`/`single_source`), `reviewed_decision` y `manual_review`
    (`VERIFIED_EQUIVALENT`, `human:<id>`) y `engine_auto` (deshabilitado: el
    motor sólo alimenta la cola de revisión). Un maestro admite un solo
    vínculo activo por cadena. Un "Distinto" humano se guarda en
    `master_link_rejections` y no se vuelve a proponer. Un producto con otro
    GTIN válido nunca se vincula automáticamente a un maestro, aunque todos
    los atributos coincidan. Los atributos golden siguen reglas de
    supervivencia documentadas (marca fuente > inferida, métrico > imperial,
    más frecuente) y un atributo fijado por una persona nunca se sobrescribe.
    Política en la sección `product_master` de `identity-policy-v1.yaml`.

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
