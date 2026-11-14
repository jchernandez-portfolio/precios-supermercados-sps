# Ofertas no vistas en la última captura aceptada ("ofertas fantasma")

Estado: **código listo, apagado por defecto**. Activarlo requiere permiso del dueño
porque crea una tabla derivada nueva en Turso (`capture_membership`) desde la
persistencia diaria. Interruptor: `config/catalog/unseen-offers-v1.json`.

## Problema

La persistencia compara sólo lo que llega (`delta` se carga con `JOIN incoming`). Un
producto que el súper deja de publicar no cambia de estado: su periodo en
`price_history` queda abierto para siempre y, como la frescura es por contexto, el
catálogo público lo muestra `FRESH` con su último precio. Ausencia no es
`out_of_stock` (AGENTS.md), así que no se puede cerrar ni marcar agotado.

Medición (sólo lectura: logs de la persistencia del 12–13 nov y manifiestos
publicados del 13 nov). Ofertas current que **no** salieron en la última captura
aceptada de su contexto = periodos abiertos − productos del snapshot:

| Contexto | Ofertas current | En el snapshot | No vistas | % |
|---|---:|---:|---:|---:|
| La Colonia SPS | 9,784 | 9,503 | 281 | 2.9 |
| Colonial SPS | 9,511 | 9,156 | 355 | 3.7 |
| Walmart SPS | 17,556 | 15,327 | 2,229 | 12.7 |
| PriceSmart SPS | 8,225 | 6,177 | 2,048 | 24.9 |
| Los Andes SPS | 6,944 | 6,353 | 591 | 8.5 |
| **SPS** | **42,145** | | **5,504** | **13.1** |
| La Colonia TGU | 9,814 | 9,536 | 278 | 2.8 |
| Walmart FFAA | 18,389 | 15,912 | 2,477 | 13.5 |
| Walmart El Sauce | 18,305 | 16,073 | 2,232 | 12.2 |
| PriceSmart TGU | 8,225 | 6,177 | 2,048 | 24.9 |
| Paiz Multiplaza | 10,043 | 9,255 | 788 | 7.8 |
| Paiz Próceres | 10,022 | 9,120 | 902 | 9.0 |
| **TGU** | **65,588** | | **8,725** | **13.3** |

Es un techo de **ofertas**: muchas de PriceSmart ya son agotadas sin precio y no se
publican (una fila exige una oferta comprable). El efecto exacto en filas visibles lo
da el paso de medición (abajo). Las ≈ 500 filas duplicadas del mismo súper por ciudad
de la auditoría del 13-nov son un subconjunto (código viejo + código nuevo).

## Opción elegida: membresía de la captura por contexto (1 fila por contexto)

En la **misma transacción** que registra el `scrape_runs` aceptado, la persistencia
guarda una fila por contexto (súper + ubicación) con el conjunto de llaves fuente
(`source_key_type` + `source_key`) del snapshot, ordenado, comprimido con zlib y en
base64, con su cantidad y su SHA-256. La fila se **sobrescribe** en cada captura
aceptada: la tabla tiene siempre ≤ 11 filas.

El exportador del catálogo público (SPS y TGU) lee esa tabla una vez y oculta las
ofertas current cuya llave no está en la membresía de la **última captura aceptada**
de su contexto. No se escribe nada por producto, no se cambia disponibilidad, precio
ni historia: la oferta vuelve sola en cuanto una captura aceptada la trae otra vez.

### Alternativas descartadas

| Alternativa | Por qué no |
|---|---|
| `last_seen` por producto actualizado a diario | ≈ 120 mil filas escritas por día (todas las ofertas current de 11 contextos). Contra "escribir sólo deltas". |
| Tabla de ausencias escrita como delta | Necesita un anti-join de todos los periodos abiertos del contexto contra `incoming` en cada persistencia: ≈ 125 mil filas leídas extra por día y escrituras por cada alta/baja. |
| El exportador lee el snapshot/artefacto aceptado | La publicación corre en otro workflow; dependería de artefactos de Actions (retención 14 días, re-runs, zip con RAW) y de permisos nuevos. Frágil. |
| Derivarlo de `scrape_runs` | `scrape_runs` es por corrida, sin llaves por producto: no alcanza. Cambiarla sería migrar una tabla base. |
| Cerrar el periodo / marcar `out_of_stock` | Falsea la verdad comercial (ausencia ≠ agotado). |
| Supresión manual (propuesta #79, sección 3) | Útil para casos puntuales, pero no escala a miles de ofertas. Sigue disponible como complemento. |

### Costo Turso estimado (con el interruptor encendido)

| Paso | Filas leídas/día | Filas escritas/día |
|---|---:|---:|
| Persistencia: `CREATE TABLE IF NOT EXISTS` + upsert por PK + guarda por PK, 11 contextos | ≈ 22 | 11 |
| Exportador SPS + TGU: 4 consultas a `sqlite_master` (≈ 40 filas c/u) + 2 lecturas de la tabla (≤ 11 filas c/u) | ≈ 180 | 0 |
| Lectura de llaves fuente en las ofertas current | 0 (mismas filas ya leídas; 2 columnas más) | 0 |
| **Total** | **≈ 200** | **11** |

Referencia: la operación diaria lee ≈ 3–4 M filas (PROJECT_STATE). Bytes por fila:
≈ 30–120 KB de llaves comprimidas (25 mil llaves sintéticas = < 200 KB en base64);
almacenamiento constante (≈ 1 MB). Sin N+1, sin consultas sobre staging completo:
las llaves se calculan en Python desde el snapshot ya validado y viajan como
parámetro, igual que `incoming`.

## Garantías conservadoras

- **Sólo capturas aceptadas.** Un snapshot rechazado o incompleto falla en
  `validate_snapshot_bytes` antes de tocar Turso: la membresía no cambia.
- **Cadena sin captura hoy**: su membresía sigue siendo la de su última captura
  aceptada; la salida es la misma de ayer (también si queda `STALE`).
- **Consistencia**: la membresía sólo se usa si su `scrape_run_id` es exactamente la
  última corrida `success` del contexto (la que ya calcula `fetch_freshness`). Si
  hay una corrida aceptada más nueva sin membresía (re-run de código viejo,
  interruptor apagado después de encendido), no se oculta nada en ese contexto.
- **Anomalía**: si las no vistas superan `max_unseen_fraction` (50 %) de las ofertas
  current del contexto, no se oculta nada (`above_limit`). El máximo medido es
  24.9 % (PriceSmart).
- **Integridad**: fila corrupta (SHA-256 o cantidad que no cuadra) → `invalid_membership`,
  no se oculta nada. Una oferta sin llave cuenta como vista.
- **Sin tabla** (estado actual): el exportador hace sólo dos consultas extra a
  `sqlite_master` por ciudad y la salida es byte a byte la de hoy.
- La guarda de esquema diaria (`OPTIONAL_DERIVED_TABLES`) y la verificación semanal
  (`verificar_integridad_turso.py`) ya toleran la tabla nueva en este mismo cambio.

## Efecto público

- La oferta no vista no se publica en el catálogo v3 (SPS y TGU). Se quita **antes**
  de agrupar: si el grupo quedaba degradado por dos ofertas del mismo súper y su
  identidad es `ready`, vuelve a ser comparable. La llave de una tarjeta comparable
  es la del producto canónico y no cambia; una tarjeta individual del código viejo
  desaparece y "Mi compra" ya avisa productos que salieron del catálogo (#84).
- Las ofertas ocultas no compiten por mejor precio, ni cuentan en canastas o
  análisis del catálogo. `retailer_offer_counts` y `context_offer_counts` cuentan lo
  publicado.
- El manifest agrega `unseen_offers` (política, ofertas ocultas y, por contexto:
  estado, ofertas current, no vistas y corrida de la membresía) sólo cuando la tabla
  existe. No contiene llaves ni RAW.

## Activación (requiere al dueño)

1. **Registrar** (escribe en Turso desde la persistencia diaria): en
   `config/catalog/unseen-offers-v1.json`, `record_capture_membership: true`, por PR.
   La siguiente captura aceptada de cada contexto crea la tabla y su fila.
2. **Medir**: la siguiente publicación agrega `unseen_offers` al manifest con
   `policy: measure_only` y los números exactos por contexto, sin ocultar nada.
3. **Ocultar**: si la medición es razonable, `hide_unseen_offers: true`, por PR.
4. **Revertir**: `hide_unseen_offers: false` (vuelve todo en la siguiente
   publicación). Para dejar de escribir, `record_capture_membership: false`; la
   tabla puede quedarse (la ignora la corrida cuya membresía no coincide).

No hace falta cambiar workflows ni secretos.

## Límites y siguientes pasos

- La identidad no se toca: si el perfil de homologación marca colisión del mismo
  súper (`single_source`) por el código viejo, la fila no vuelve a ser comparable
  hasta que el refresco de homologación deje de verlo (fuera de este cambio).
- Business Mart v1 / Consumer Mart v2 (Power BI y comparación La Colonia–Walmart)
  no aplican el filtro todavía; mismo patrón, segundo paso tras medir.
- La cola de tamaños sigue viendo las ofertas no vistas.
