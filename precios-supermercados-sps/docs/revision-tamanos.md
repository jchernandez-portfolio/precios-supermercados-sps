# Panel de revisión de tamaños

Mismo código de barras (GTIN) con tamaños distintos entre supermercados: el motor
no puede saber cuál es el correcto. Una persona revisa la etiqueta en la página del
súper y fija el **tamaño oficial**; el motor aplica la regla en cada refresco:

> Las ofertas que coinciden con el tamaño oficial forman el grupo; las demás se aíslan.
> Desde el motor v2.10 la foto de cada oferta puede corregir su tamaño (ver "Motor v2.10: corrige con evidencia").

## Piezas

| Pieza | Qué hace |
| --- | --- |
| `super-compras/admin.js` (`#/admin/revision`) | Lista la cola; GTIN-14 visible con "Copiar"; por oferta, el código tal como lo publica el súper (con "Copiar") y búsquedas de 14, 13 y 12 dígitos (★ = la forma que usa ese súper; probado: Walmart busca con 13 y Colonial con 12), botones rápidos con los tamaños publicados, tamaño a mano (g, ml, oz, lb, unidades), "No es el mismo producto" y nota. "Guardar y resolver" llama a la API de GitHub con la llave del dueño. |
| `precios-supermercados-sps-size-decisions.yml` | `workflow_dispatch` con el JSON del panel (`rpi-size-decisions/v1`). Sólo `main` y sólo la cuenta del dueño. El JSON pasa por variable de entorno. Guarda y lanza el refresco de homologación. |
| `scripts/aplicar_decisiones_tamano.py` | Valida (falla cerrado), exige que el GTIN exista en los perfiles y, en un lote atómico: `size_decisions`, `human_attributes_json` del maestro (`net_content_value`/`net_content_unit`, `source: size_decisions`) y `master_sync_dirty`. |
| `src/precios_supermercados/size_decisions.py` | Contrato, unidades (lb → g; la onza queda como onza) y comparación con el oficial (contenido total, 2 % o 1.5 unidades, onza líquida o de peso). Desde el motor v2.9, en grupos de lácteos, jugos y salsas (alguna oferta con categoría `yogurt`, `leche`, `queso`, `crema`, `jugo`, `nectar`, `salsa`, `aderezo`, `mayonesa` o `ketchup`) un oficial en g coincide con una oferta en ml y viceversa si difieren ≤ 5 % (`mass_volume_tolerance`); las onzas y las unidades no entran en esa equivalencia. |
| `product_identity_v2.resolve_gtin_group(..., decision)` | Con decisión de tamaño: ofertas distintas → `human_size_decision_mismatch`; con "no es el mismo producto" → `human_different_products` (el GTIN no se agrupa). |
| `precios-supermercados-sps-size-queue.yml` + `scripts/exportar_cola_tamanos.py` | Tras cada refresco recalcula los grupos (sólo SELECT) y publica `cola.json` en la rama pública `revision-tamanos`: `empate` (grupo bloqueado) y `atipico` (una cadena fuera). Omite los GTIN ya decididos. |
| `scripts/sugerir_tamanos_ia.py` (paso del workflow de la cola) | **Claude** (API, `ANTHROPIC_API_KEY`, modelo económico configurable, tope `claude_max` consultas por corrida) y de respaldo **Gemini** (`GEMINI_API_KEY`) leen el contenido neto en la foto de **cada oferta** (las mismas fotos R2 que muestra el panel; al modelo no se le dan títulos ni tamaños). Si todas las fotos legibles dicen lo mismo y ese tamaño es el de **más de la mitad** de las ofertas, se guarda sola la decisión (`engine:claude_auto` o `engine:gemini_auto` según quién leyó; nunca con Flash-Lite ni sobre una humana) y el código sale de la cola. A revisión manual: ilegible, fotos que no coinciden entre sí, lectura que coincide sólo con una minoría (Mazola 1 kg vs 908 g) o con ningún título. Cada oferta muestra "🤖 Claude leyó: X" / "🤖 Gemini leyó: X" (✓ coincide con su título, ⚠ no). Lo resuelto por la IA queda en la pestaña "Resueltos por IA" para auditarlo: una decisión humana reemplaza la de la IA. Si Claude falla (sin crédito, llave inválida, 429, 529/5xx tras reintentos, modelo no disponible o tope), el resto de la corrida usa Gemini y `cola.json → ia` anota el motivo, consultas y tokens por proveedor. Gemini: un 404/400 de modelo retirado pasa al siguiente candidato; ante 5xx reintenta y cambia de Flash; ante 429 espera 60 s, reintenta y pasa a Flash-Lite (sólo sugiere). |

Cambiar de opinión: se envía otra decisión del mismo GTIN; reemplaza la anterior.

### Motor v2.9 y la cola

Las reglas g ≈ ml (lácteos, jugos y salsas, ≤ 5 %) y peso sobre conteo (conteo 1,
mismo pack o título con el conteo) resuelven solas parte de los empates y
atípicos, que salen de la cola (p. ej. McCormick 180 ml ×2 contra 180 g ×2). Las
decisiones guardadas siguen mandando: las reglas no corren en un GTIN decidido,
pero el oficial ya compara g con ml en esas categorías, así que un grupo decidido
puede **ganar** ofertas (un oficial "180 ml" acepta ahora las de "180 g"; nunca
pierde ninguna). La medición `medir_reglas_identidad.py --pr-b` lo cuenta en
`motor_vigente.grupos_decididos_que_cambian_por_g_ml`.

### Motor v2.10: corrige con evidencia

Antes, una decisión de tamaño sólo agrupaba las ofertas cuyo **título** ya coincidía; las
demás quedaban aisladas con su tamaño equivocado (El Migo: Walmart y Paiz publican 148 g,
el peso escurrido, aunque la lata dice "PESO NETO 8.5 oz (240 g)"). Orden de confianza
por producto: (1) la decisión (humana, o de Claude aprobada por el dueño), (2) lo impreso
en la foto **de esa oferta**, (3) el título del súper. El título solo nunca es evidencia.

**Evidencia**: cada decisión puede traer `evidence: [{store, value, unit, text}]` (lo leído
en la foto propia de cada súper) y `apply: "evidence" | "all"` (por omisión `evidence`).
Se guarda en la tabla derivada `size_offer_evidence` (`canonical_gtin`, `supermarket_id`,
`value`, `unit` canónica g/ml/oz/unit, `source` ∈ human/claude_sesion/claude_auto/gemini_auto,
`text` ≤ 60, `decided_at_utc`; `apply: "all"` es una fila de alcance con súper `*` y sin
valor). Sólo la escribe `aplicar_decisiones_tamano.py` (workflow de decisiones y guardado
automático de la IA), en el mismo lote atómico que la decisión, como delta de ese GTIN y
sólo si la fila guardada de `size_decisions` es del mismo autor (la IA nunca toca la
evidencia de una decisión humana). Un envío con `evidence`/`apply` reemplaza la evidencia
del GTIN; uno sin ellos (el panel actual) borra sólo el alcance `all` y conserva las
lecturas. Está en `OPTIONAL_DERIVED_TABLES` y `OPTIONAL_COUNTED_TABLES`.

**Regla** (`product_identity_v2.size_evidence_plan`), contando por súper con oferta en el
GTIN las fotos que confirman el oficial (`match_official`, con g ≈ ml donde aplica) y las
que lo contradicen:

| Caso | Efecto |
| --- | --- |
| ≥ 2 súper confirman con foto y son más que los que contradicen (`mayoria`) | El oficial se aplica a **todas** las ofertas del GTIN, también sin foto o con foto/título distinto (probablemente empaque anterior). Nada queda aparte por tamaño. |
| Alguna foto confirma pero las que contradicen son tantas o más (`empate`) | No se corrige nada (como v2.9): queda para revisión humana. |
| 1 o 0 fotos confirman (`propia`) | Se corrigen las ofertas cuya foto propia confirma; la que su foto contradice se aísla como presentación distinta (`human_size_decision_contradicted`); las sin foto quedan como hoy, salvo `apply: "all"`. |

Una oferta corregida entra al grupo con el tamaño oficial (`presentation_*` y
`display_presentation`; precio por unidad con el oficial) y conserva lo que publica el súper
en los campos canónicos del perfil (`normalized_*`, `canonical_total`, `raw_presentation`).
La corrección también levanta el "multipack ambiguo" de esa oferta; los demás bloqueos
(tipo, sabor, colisión por cadena) siguen. "Nunca empeorar": si aislar una oferta
contradicha bloquea el grupo o se pierde una oferta que su foto no contradice, se conserva
la resolución v2.9. `different_products` no cambia y no acepta evidencia.

**Quién manda evidencia**: `guardar_revision_claude.py` convierte `lecturas` a
valor/unidad sólo si el texto es inequívoco (`size_decisions.evidence_from_text`: prefiere
la medida métrica, "N × X" cuenta el total, descarta el peso escurrido y rechaza
"parcial", "banner", "c/u", "(1 sobre)" u "otro diseño") y sólo de las fotos **propias**
de cada súper (una foto compartida de otro súper no cuenta); `apply: "all"` sólo si la fila
aprobada lo trae. La IA de la cola (`sugerir_tamanos_ia.py`) manda la lectura de la foto
propia de cada oferta. El panel puede seguir enviando sin esos campos.

**Catálogo público (contrato para el frontend; SPS y TGU)**, campos opcionales en cada
oferta de `offers[]` de las particiones (no van en `search.json`):

- `published_as` (texto, p. ej. `"148 g"`, `"16 unidades"`, `"8.5 oz"`): la evidencia
  corrigió el tamaño de esa oferta; `presentation` de la fila y su precio por unidad ya usan
  el oficial. Sugerencia: "Walmart lo publica como 148 g".
- `other_presentation: true`: la oferta quedó aparte porque su propia foto muestra otro
  tamaño (presentación distinta o anterior). Va en una tarjeta individual.

Sin esos campos la oferta se muestra como hoy; una app vieja los ignora.

**Medición** (sólo lectura): el workflow "Auditoría de identidad de producto" (sin inputs)
deja en el artifact `identidad-productos-<run>/reglas/` la sección
`correccion_con_evidencia` de `medicion-reglas.json` (resumen y fichas por GTIN: modo, fotos
que confirman/contradicen, rol de cada oferta sin evidencia, con evidencia y con
`apply: "all"`) y `evidencia-grupos.csv` (una línea por oferta de cada GTIN decidido).

## Llave de GitHub

Token *fine-grained* del dueño con acceso sólo a este repositorio y el permiso
**Actions: Read and write** (Metadata: read se agrega solo), con vencimiento
corto. Se pega en el panel; vive sólo en esa pestaña (`sessionStorage`) y sólo
viaja en el encabezado `Authorization` a `api.github.com`. Los secretos de Turso
nunca salen de GitHub.

## Puesta en marcha (una vez)

1. Crear la rama pública `revision-tamanos` (huérfana, con un README).
2. Correr a mano "Precios SPS - Cola de revisión de tamaños".

## Modo sesión: Claude Code revisa las fotos (aprobación humana obligatoria)

1. `python scripts/preparar_revision_claude.py --n 25 [--primero <GTIN>]` (sólo lectura): baja la
   cola pública y arma en `revision-claude/` (ignorada por git) una imagen JPG por código con la foto
   800 px de cada súper, rotulada con súper, título y tamaño publicado ("foto compartida" si la foto
   vino de otro súper), más `lote.json`. Salta lo resuelto por la IA y lo ya visto (`vistos.json`).
2. Claude mira cada imagen y propone tamaño oficial, "no es el mismo producto", "no legible" o "a
   revisión humana" (fotos contradictorias o empate sin evidencia). Lo muestra en una tabla.
3. El dueño aprueba, cambia o descarta filas en el chat. Claude escribe `resultados.json`
   (`aprobado: true` sólo en lo aprobado).
4. `python scripts/guardar_revision_claude.py --enviar`: arma el envío (máx. 300 decisiones /
   60,000 caracteres), lo valida y lanza el workflow de decisiones con `fuente=claude_sesion`
   (lista cerrada `panel | claude_sesion`; el job sólo corre para la cuenta del dueño). Se guarda
   como `engine:claude_sesion` con "aprobado por <cuenta>" en la nota; nunca reemplaza una decisión
   humana y una humana posterior la reemplaza. Marca como vistos todos los códigos de la tanda.
5. La cola publica en `resueltas_motor` los códigos decididos por cualquier motor de IA (con sus
   ofertas y fotos); la pestaña "Resueltos por IA" los muestra con "🤖 Resuelto por Claude (sesión)"
   y lo leído por súper en la nota.

