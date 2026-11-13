# Panel de revisión de tamaños

Mismo código de barras (GTIN) con tamaños distintos entre supermercados: el motor
no puede saber cuál es el correcto. Una persona revisa la etiqueta en la página del
súper y fija el **tamaño oficial**; el motor aplica la regla en cada refresco:

> Las ofertas que coinciden con el tamaño oficial forman el grupo; las demás se aíslan.

## Piezas

| Pieza | Qué hace |
| --- | --- |
| `super-compras/admin.js` (`#/admin/revision`) | Lista la cola; GTIN-14 visible con "Copiar"; por oferta, el código tal como lo publica el súper (con "Copiar") y búsquedas de 14, 13 y 12 dígitos (★ = la forma que usa ese súper; probado: Walmart busca con 13 y Colonial con 12), botones rápidos con los tamaños publicados, tamaño a mano (g, ml, oz, lb, unidades), "No es el mismo producto" y nota. "Guardar y resolver" llama a la API de GitHub con la llave del dueño. |
| `precios-supermercados-sps-size-decisions.yml` | `workflow_dispatch` con el JSON del panel (`rpi-size-decisions/v1`). Sólo `main` y sólo la cuenta del dueño. El JSON pasa por variable de entorno. Guarda y lanza el refresco de homologación. |
| `scripts/aplicar_decisiones_tamano.py` | Valida (falla cerrado), exige que el GTIN exista en los perfiles y, en un lote atómico: `size_decisions`, `human_attributes_json` del maestro (`net_content_value`/`net_content_unit`, `source: size_decisions`) y `master_sync_dirty`. |
| `src/precios_supermercados/size_decisions.py` | Contrato, unidades (lb → g; la onza queda como onza) y comparación con el oficial (contenido total, 2 % o 1.5 unidades, onza líquida o de peso). |
| `product_identity_v2.resolve_gtin_group(..., decision)` | Con decisión de tamaño: ofertas distintas → `human_size_decision_mismatch`; con "no es el mismo producto" → `human_different_products` (el GTIN no se agrupa). |
| `precios-supermercados-sps-size-queue.yml` + `scripts/exportar_cola_tamanos.py` | Tras cada refresco recalcula los grupos (sólo SELECT) y publica `cola.json` en la rama pública `revision-tamanos`: `empate` (grupo bloqueado) y `atipico` (una cadena fuera). Omite los GTIN ya decididos. |
| `scripts/sugerir_tamanos_ia.py` (paso del workflow de la cola) | **Claude** (API, `ANTHROPIC_API_KEY`, modelo económico configurable, tope `claude_max` consultas por corrida) y de respaldo **Gemini** (`GEMINI_API_KEY`) leen el contenido neto en la foto de **cada oferta** (las mismas fotos R2 que muestra el panel; al modelo no se le dan títulos ni tamaños). Si todas las fotos legibles dicen lo mismo y ese tamaño es el de **más de la mitad** de las ofertas, se guarda sola la decisión (`engine:claude_auto` o `engine:gemini_auto` según quién leyó; nunca con Flash-Lite ni sobre una humana) y el código sale de la cola. A revisión manual: ilegible, fotos que no coinciden entre sí, lectura que coincide sólo con una minoría (Mazola 1 kg vs 908 g) o con ningún título. Cada oferta muestra "🤖 Claude leyó: X" / "🤖 Gemini leyó: X" (✓ coincide con su título, ⚠ no). Lo resuelto por la IA queda en la pestaña "Resueltos por IA" para auditarlo: una decisión humana reemplaza la de la IA. Si Claude falla (sin crédito, llave inválida, 429, 529/5xx tras reintentos, modelo no disponible o tope), el resto de la corrida usa Gemini y `cola.json → ia` anota el motivo, consultas y tokens por proveedor. Gemini: un 404/400 de modelo retirado pasa al siguiente candidato; ante 5xx reintenta y cambia de Flash; ante 429 espera 60 s, reintenta y pasa a Flash-Lite (sólo sugiere). |

Cambiar de opinión: se envía otra decisión del mismo GTIN; reemplaza la anterior.

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

