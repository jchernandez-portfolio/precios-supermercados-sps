# Instrucciones para agentes — Retail Price Intelligence

## PROJECT / PULL REQUEST SCOPE CONTRACT

```text
PROJECT_ID=RPI
PROJECT_ROOT=precios-supermercados-sps/
PR_TITLE_PREFIX=[RPI]
FUTURE_BRANCH_PREFIX=rpi/
```

RPI es dueño de `precios-supermercados-sps/**` y de los workflows RPI registrados
en `/.github/project-scopes.yml`. Un cambio RPI puede tocar una integración compartida
sólo cuando el cambio RPI la necesita y el registry lo permite. No puede tocar
PAGOS ni MUNDIAL ni usar paths compartidos como bypass.

Este archivo es gobernanza del monorepo. Cambiarlo, cambiar el registry o cambiar
las fronteras de proyecto requiere un PR `[MONOREPO]` separado.

## Fuente de verdad

- Repositorio: `jchernandez-portfolio/precios-supermercados-sps`.
- Proyecto: `precios-supermercados-sps/`.
- GitHub `main`, PRs, Actions, artifacts, Turso y `portfolio-data` mandan sobre
  prompts, recuerdos y snapshots antiguos.
- `docs/PROJECT_STATE.md` describe el estado operativo mutable.
- `docs/RPI-PRODUCT-SPEC.md` define el contrato funcional del producto.
- Antes de modificar: auditar `main`, PRs abiertos, CI, artifacts y buscar si la
  capacidad ya existe.

## Fase activa — operar y cerrar el producto RPI

La adquisición de nuevas cadenas está cerrada. No buscar ni integrar un séptimo
supermercado salvo instrucción explícita posterior.

El producto actual es:

```text
fuentes públicas aceptadas
→ captura especializada por retailer/ubicación
→ validación de completitud + health
→ last-known-good / histórico compacto en Turso
→ homologación conservadora
→ comparabilidad + freshness
→ Python analytics
   ├─ Business Mart v1 → Power BI B2B
   ├─ Consumer Mart v2 → comparación/escenarios
   └─ Consumer Catalog v3 → Compra Inteligente B2C
```

La prioridad es mantener la operación diaria, calidad/homologación, publicación
segura y las superficies B2B/B2C. No volver a una fase de “un supermercado a la
vez” salvo mantenimiento de una fuente ya productiva.

## Cobertura productiva

La operación vigente cubre seis cadenas y once contextos demostrados:

- La Colonia: SPS + Tegucigalpa;
- Colonial: SPS;
- Walmart: SPS + TGU FFAA + TGU El Sauce;
- PriceSmart: SPS 6603 + TGU Florencia 6602;
- Comisariato Los Andes: SPS;
- Paiz: TGU Multiplaza + TGU Próceres.

Límites vigentes:

- Paiz no tiene contexto SPS aceptado;
- PriceSmart El Sauce 6604 permanece excluido;
- Maxi Despensa y Despensa Familiar siguen **NO-GO TEMPORAL PARA PRICE TRACKING
  WEB** y no deben reabrirse sin una nueva fuente pública demostrada y una
  instrucción explícita.

Los conteos exactos de productos, periodos, runs y filas públicas cambian con la
operación y deben leerse de `PROJECT_STATE.md`, artifacts o Turso; no fijarlos aquí.

## Arquitectura y responsabilidades

- **Turso/libSQL** = verdad comercial e histórica persistida.
- **Python** = verdad analítica, homologación, comparabilidad, rankings, historia,
  freshness y métricas compartidas.
- **Business Mart v1** = contrato privado B2B reproducible.
- **Consumer Mart v2** = contrato público de comparación analítica segura.
- **Consumer Catalog v3** = contrato público, particionado y escalable para
  navegación B2C.
- **Power BI** = presentación/consumo B2B; no decide identidad nueva.
- **Compra Inteligente** = web responsive; no hace scraping, no consulta Turso y
  no hace matching en JavaScript.

No duplicar lógica crítica en DAX, Power Query o frontend.

## Persistencia y estado comercial

La identidad fuente base es:

```text
supermarket_id + source_key_type + source_key
```

Las tablas comerciales base siguen siendo:

```text
supermarkets
locations
products
price_history
scrape_runs
```

La homologación derivada usa `product_homologation_profiles`; recalcular perfiles,
taxonomía o normalización no debe crear periodos comerciales falsos.

Estado comercial mínimo:

```text
current_price
reported_regular_price
is_promotion
availability
```

Semántica:

```text
current_price          = precio efectivo observado
reported_regular_price = precio normal/tachado declarado por la fuente
previous_price         = current_price del periodo histórico aceptado anterior
```

`reported_regular_price` nunca sustituye historia real.

Para producto + ubicación:

```text
mismo estado comercial
→ no abrir historia nueva

estado cambió
→ cerrar periodo actual
→ abrir periodo nuevo

producto nuevo
→ crear producto
→ abrir primer periodo
```

Cada ejecución aceptada registra `scrape_runs`. Un snapshot incompleto/rechazado no
modifica el last-known-good.

Disponibilidad:

```text
in_stock
out_of_stock
unknown
```

Ausencia del catálogo no implica `out_of_stock` salvo evidencia específica de la
fuente.

## Turso y coste

Turso ya tuvo un incidente de lecturas excesivas. No reintroducir N+1, N×N,
correlaciones sobre staging completo ni verificaciones globales repetidas.

Principio obligatorio:

```text
READ NECESSARY SCOPE
→ COMPARE ONCE
→ COMPUTE DELTA
→ WRITE CHANGES ONLY
→ VERIFY AFFECTED SCOPE
```

Las publicaciones analíticas son read-only respecto a Turso.

## Homologación y comparabilidad

**Visibilidad de catálogo no equivale a comparabilidad cross-retailer.**

Un producto puede ser visible y usable en Compra Inteligente aunque no tenga un
match seguro en otra cadena. Sólo identidades suficientemente fuertes pueden
recibir ranking, best price, savings o recomendaciones competitivas.

Reglas mínimas:

- mismo GTIN/EAN válido es evidencia fuerte, pero conflictos descriptivos pueden
  bloquear;
- GTIN distintos no se fusionan como el mismo producto;
- marca + tipo genérico + tamaño no bastan;
- Los Andes `code` es SKU, no EAN;
- placeholders de marca no cuentan como marca de consumidor;
- imágenes son evidencia auxiliar, no autoridad de matching;
- candidatos por similitud quedan en `review_required`, nunca se auto-publican
  como equivalentes sólo por score.

La cola privada de revisión puede materializar candidatos fuzzy, conflictos de
GTIN y gaps de taxonomía. No se publica al B2C y no muta perfiles automáticamente.

## Freshness y métricas

Cada oferta/ubicación debe conservar `as_of`/`observed_at`, último run aceptado,
edad y estado de freshness.

Estados conceptuales:

```text
FRESH
STALE
UNAVAILABLE
```

Datos stale/unavailable o temporalmente incompatibles pueden seguir visibles como
último dato válido, pero no generan ranking/PCI/recomendación competitiva nueva.

Toda métrica comparativa debe conservar cobertura/denominador y faltantes.

## Compra Inteligente B2C

Compra Inteligente es un **planificador/comparador**, no una tienda ni checkout.

Flujo principal:

```text
FILTRAR
→ COMPARAR
→ PONER CANTIDAD
→ ELEGIR SUPERMERCADO
→ AGREGAR
→ SEGUIR COMPRANDO
→ VER MI LISTA
```

Los filtros son navegación principal. La búsqueda textual es secundaria.

Cascada preferida:

```text
Categoría
→ Producto / Tipo
→ Marca
→ Presentación
```

Reglas de decisión:

- la tabla/tarjetas muestran sólo resultados que cumplen los filtros activos;
- cantidad existe una sola vez por producto/fila;
- sólo una oferta/supermercado puede estar seleccionada por producto;
- elegir otra oferta reemplaza la selección de esa fila;
- nunca auto-seleccionar el precio más bajo;
- faltante se representa como `—`, nunca como precio cero;
- selección manual del usuario y ranking visual son estados distintos.

Ranking visual sólo para ofertas realmente comparables y frescas:

- mínimo: verde;
- máximo: rojo;
- intermedios: amarillo/ámbar;
- dos ofertas: verde/rojo;
- empates comparten estado;
- todas iguales: equivalente/neutro;
- una sola oferta, single-source, review-required o no comparable: neutral.

Los colores requieren texto/estado accesible.

Mi lista usa:

```text
Producto | Cantidad | Precio unitario | Total
```

con:

```text
line_total = current_price * quantity
retailer_subtotal = sum(line_total)
grand_total = sum(retailer_subtotal)
```

No inventar ISV, impuestos, delivery, service fees, membership fees ni costos de
checkout. Un faltante deja el total dependiente incompleto; no vale cero.

La lista debe conservar identidad exacta y retailer seleccionado. Refresh no puede
hacer sustituciones silenciosas. CSV/PDF y compartir por WhatsApp son salidas
locales del planificador, no órdenes de compra.

En desktop puede usarse matriz; en móvil transformar a tarjetas/ofertas apiladas,
no comprimir una tabla ancha hasta volverla ilegible.

## Publicación B2C

Cadena autorizada:

```text
Turso
→ homologación derivada
→ Python analytics
→ marts/catálogo
→ validación schema/scope/hash/secretos
→ publicación atómica en portfolio-data
→ navegador estático
```

El navegador realiza **cero lecturas a Turso** y no recibe RAW, secretos ni colas
privadas.

Consumer Catalog v3 debe seguir particionado: manifest/facetas pequeños, índices y
particiones bajo demanda, detalle/historia cuando sea necesario. No volver a un
JSON monolítico con catálogo + historia completa.

Una publicación fallida conserva el último contrato público válido.

## Power BI B2B

Power BI consume `rpi-business-mart/v1` y presenta las nueve superficies del
producto. Los assets reproducibles viven en `powerbi/rpi/`.

No inventar un `.pbix` binario ni afirmar que existe si no fue construido y
verificado realmente. Python sigue calculando identidad, PCI, ranking, freshness,
deltas y clasificación histórica.

## Operación recurrente y recuperación

La ejecución diaria productiva ya está autorizada y existe. No volver a tratar la
recurrencia como pendiente ni exigir autorización humana para cada run programado.

La adquisición diaria está aislada por cadena. Cada cadena emite handoff sólo si
supera validaciones; la persistencia global sigue fail-closed y exige todos los
handoffs requeridos.

La recuperación programada puede reejecutar jobs fallidos/dependencias dentro del
mismo run, reutilizando handoffs aceptados de intentos previos. Mantener los límites
y contratos de seguridad documentados en `PROJECT_STATE.md` y en los workflows
vigentes.

No provocar crawls manuales/live sólo para fabricar evidencia.

## Tráfico live y autorizaciones ad hoc

La recurrencia productiva ya configurada tiene su propia autoridad operativa.
Fuera de ella, una observación/probe/crawl manual nuevo requiere la autoridad que
corresponda al workflow y alcance actuales.

Nunca:

- reutilizar un marker temporal consumido como autorización abierta;
- evadir CAPTCHA, login, 403, 429, rate limits o anti-bot;
- explotar vulnerabilidades;
- usar fuentes privadas/no públicas;
- aumentar tráfico por estética o para obtener una métrica que puede derivarse
  offline.

Preferir fuentes estructuradas/batch, caching, concurrencia baja y retries
acotados.

## Vercel y fronteras con PAGOS

El proyecto Vercel `pagos-whatsapp-residencial` pertenece a PAGOS, no a RPI.
Cambios RPI no deben modificar su app, variables, dominios ni secretos.

Si Compra Inteligente se despliega en Vercel, debe ser un proyecto independiente
con root RPI/B2C y aislamiento propio del monorepo. `portfolio-data` es una rama de
datos publicados, no una aplicación PAGOS.

No reutilizar el proyecto Vercel de PAGOS para servir Compra Inteligente.

## Gate de simplicidad

Antes de crear archivo de producción, módulo, clase, adapter, workflow, tabla,
dependencia o servicio:

1. ¿Cuál es el blocker exacto?
2. ¿Existe ya una capacidad que lo resuelva?
3. ¿Puede resolverse con una operación puntual o módulo existente?
4. ¿Cuál es el cambio más pequeño correcto?
5. ¿Qué evidencia real justifica la complejidad?

Orden preferido:

```text
capacidad existente
> operación puntual
> función/módulo existente
> cambio específico pequeño
> abstracción nueva
> infraestructura nueva
```

No crear microservicios, backend B2C, login, app móvil, BigQuery/Cloud Run o ML por
anticipación. Incorporarlos sólo cuando resuelvan una necesidad demostrada.

## Desarrollo

```text
AUDITAR
→ IMPLEMENTAR
→ PROBAR
→ CORREGIR
→ CI
→ REVISAR
→ MERGE
→ VERIFICAR EN PRODUCCIÓN
→ SIGUIENTE BLOQUE
```

- Fusionar sólo con CI verde.
- No usar force push, reset destructivo ni rebase destructivo.
- No mezclar proyectos en un mismo PR.
- No usar PRs de PAGOS/MUNDIAL como checkpoints RPI.
- Actualizar documentación únicamente con evidencia real demostrada.
- No detener el trabajo por una deuda histórica que no bloquea el objetivo actual.

**Cuando compitan una arquitectura más completa y el camino más corto correcto al
producto, elegir el segundo.**
