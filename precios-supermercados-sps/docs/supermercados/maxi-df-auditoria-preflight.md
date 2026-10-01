# Maxi Despensa + Despensa Familiar — auditoría y frontera técnica

Decisión posterior: **NO-GO TEMPORAL PARA PRICE TRACKING WEB**. El usuario confirmó
que la web pública no muestra precios y ordenó detener scraper, persistencia y full
crawl si la radiografía mínima no encontraba otra fuente pública reproducible. La
captura no encontró precio/API/contexto comercial utilizable. Maxi/DF quedan cerrados
temporalmente; no se buscan precios en imágenes, PDF, OCR o fuentes indirectas.

El usuario autorizó el probe por 24 horas desde el registro
2026-08-31T04:20:37Z. **Probe cerrado: 21 GET/273.325 s, un retry, concurrencia 1**,
dentro del máximo 40 GET/15 minutos. Web y formatos demostrados; precio/contexto
comercial no demostrados. 97 filas / 96 códigos candidatos, cero aceptados.
**No apto para full ni persistencia con la fuente observada.**

[Reporte actual, RAW, comparación pendiente y siguiente frontera](../../reports/maxi-df/2026-08-31-probe/README.md).
Sin SQL Turso ni cambios productivos. No continuar estas cadenas. La clasificación
temporal puede revisarse sólo ante una fuente digital pública nueva y demostrable;
no se afirma inviabilidad universal.

Las secciones siguientes conservan la **auditoría y el preflight históricos**, de
antes de la autorización y captura. Sus frases «pendiente», «cero requests» y
«no demostrado» describen ese momento, no permiso para repetir el probe cerrado.

## Estado comprobado antes de implementar

- Main `3e77145c237102f835ae147d4923119597c3cb5e`,
  [PR #355](https://github.com/jchernandez-portfolio/precios-supermercados-sps/pull/355) fusionado,
  [CI main 33352854520](https://github.com/jchernandez-portfolio/precios-supermercados-sps/actions/runs/33352854520)
  verde con 1,985 pruebas. Cero PRs abiertos al iniciar la auditoría.
- Búsqueda por nombres/dominio en proyecto y workflows: sin implementación,
  configuración, fixtures ni documentación Maxi/DF. Leídos AGENTS, PROJECT_STATE,
  esquema, validadores, SQL productivo y evidencia de eficiencia vigente.
- La Colonia conserva implementación y autorización diaria SPS/TGU. Sus últimos
  schedules observados 33260860123 y 33319436863 fallaron, por timeout y cuota
  Turso respectivamente; no se declara operación actual íntegramente saludable.
- Colonial conserva 9,199 productos / 9,205 variantes y SQL offline validado.
  Walmart conserva 41,752 observaciones SKU en tres contextos y SQL offline
  validado. Primera carga remota y segunda observación real de ambas pendientes.
- Walmart TGU: 12,867 SKU compartidos, 12,042 comparables y 255 diferencias
  comerciales; conservar ambos contextos. Los 331 casos sólo de disponibilidad
  no motivan la decisión. [Comparación RAW completa](../../reports/walmart/2026-08-31-full/TGU-COMPARISON.md).
- Inspección offline de `01.raw`, `04.raw` y `11.raw` del probe Walmart archivado:
  no contienen los nombres/dominio Maxi/DF. Es un resultado de esos tres recursos,
  no prueba sobre todo el ecosistema ni autorización para descargar otra web.
- `turso plan show`, sólo control de cuenta: Starter, overages deshabilitados,
  lecturas 713.7 M / 500 M (143%). Ahora imprime `Wed, 30 Sep 2026 18:00:00 CST`;
  antes imprimía 31 de agosto. Fecha de reset discrepante, sin confirmar mediante
  SQL ni afirmar una causa. No billing, overages, cambio de plan ni tuning remoto.

Biblioteca reusable auditada en `252b245e0f416b57c324db97bc9cee868fc8124d`:
`scraping-fast-path`, `web-source-recon`, `api-discovery`, `web-data-extraction`,
`browser-automation`, `extraction-completeness` y explícitamente
`production-data-engineering`. Aplicadas también auditoría, pruebas, provenance,
external-effects-safety, arquitectura, CI, debugging, git-github-delivery y
documentation-state. No se copian skills al proyecto ni se crean nuevas sin un
aprendizaje reusable demostrado. Browser se considera sólo como fallback futuro;
no forma parte del primer probe propuesto. Legal/Terms queda fuera de alcance por
instrucción del usuario; no se revisaron ni se usan como gate.

## Lo conocido y lo que falta demostrar

`maxidespensa.com.hn` como web compartida proviene del usuario, no de una captura
propia. **Requests nuevos a Maxi/DF: 0.** No asumir VTEX, cuenta, IDs, moneda,
seller, API, esquema ni selector por pertenecer a un mismo grupo empresarial.

| Ámbito solicitado | Tiendas/IDs | Fuente y contrato | Muestra/grupo comercial |
| --- | --- | --- | --- |
| Maxi Despensa SPS | No demostrados | No demostrado | Pendiente |
| Maxi Despensa TGU | No demostrados | No demostrado | Pendiente |
| Despensa Familiar SPS | No demostrados | No demostrado | Pendiente |
| Despensa Familiar TGU | No demostrados | No demostrado | Pendiente |

La radiografía debe vincular evidencia visible de formato/ciudad/tienda con la
configuración y la solicitud que produce el precio. Registrar store/branch/
location/warehouse/seller IDs sólo si existen, y parámetros, headers, variables
API/GraphQL, cookies o storage relevantes sin exponer secretos. Identificar fuente
de catálogo/precios, promociones, disponibilidad, entidades/variantes, categorías,
totales, membership y paginación. Un ID genérico en una oferta no demuestra tienda.

Reutilizar transporte/parser sólo si las respuestas demuestran API, esquema y
normalización estructural comunes. Mantener supermercados separados aunque
coincidan IDs; identidad `supermarket_id + source_key_type + source_key`.
No extender Walmart ni construir un framework por analogía sin evidencia.

## Primer probe conjunto propuesto — requiere autorización

**Objetivo:** una radiografía del dominio indicado, primer producto válido y
muestras de 20–50 SKU cuando la fuente lo permita, prueba de contexto/formato y
comparación barata de tiendas SPS/TGU. Maxi y DF se cubrirán conjuntamente sólo
si la propia fuente demuestra que ambas están allí; una marca no demostrada queda
pendiente. No catalogar otras ciudades ni comenzar Paiz u otra cadena.

**Límites:** máximo **40 GET totales**, incluidos hasta **dos saltos de redirect**
y **cuatro retries transitorios** globales (máximo uno por recurso). No son 40 más
extras. Concurrencia **1**, al menos un segundo entre inicios, conexión reutilizada,
timeout individual 20 s y **15 minutos** desde el inicio. Registrar autorización,
inicio y deadline antes del primer request; no iniciar uno cuyo timeout exceda el
saldo temporal. Detener al alcanzar cualquier límite, sin ampliación automática.
El techo es una propuesta de exploración, no una estimación de full basada en datos.

Superficie permitida a solicitar: `https://maxidespensa.com.hn/`, su variante
canónica `www` y fuentes públicas de catálogo/configuración identificadas por ese
frontend. Hasta tres scripts/configuraciones públicos indispensables enlazados
directamente por el HTML, incluidos sus CDN si la URL fuente los demuestra.
Validar destino antes de seguir cada redirect; no seguir un origen nuevo ajeno
a esa superficie. No adivinar endpoints ni transplantar IDs de Walmart.

GET públicos de lectura únicamente: no browser, imágenes, CSS, fonts, analytics,
login, carrito, checkout, sesión mutable, API privada, credenciales administrativas
ni POST. Si el contexto exige una interacción o método no cubierto, documentar el
requisito y pedir ampliación concreta; no eludirlo ni atribuir precios a una ciudad
por el nombre de la URL. No CAPTCHA/login/403/429 bypass, stealth, IP rotation ni
fingerprint spoofing.

Secuencia adaptativa, sin recorrer todas las alternativas por defecto:

1. Obtener HTML/estado más barato útil; derivar fuente estructurada y configuración
   sólo cuando sea indispensable. Preferir API/batch/JSON/GraphQL GET/estado embebido
   a HTML por producto; comprobar primero una identidad, presentación y precio.
2. Obtener una muestra de 20–50 por formato cuando sea posible, con varias categorías
   y normales/promocionados. No hacer una página individual por cada producto.
3. Enumerar en la configuración todas las tiendas relevantes SPS/TGU, su formato y
   binding. No consultar catálogos de otras ciudades aunque aparezcan en metadata.
4. Reutilizar un panel común por formato/ciudad en sus tiendas; usar batches y
   RAW/cache para evitar consultas idénticas. Repetir sólo el control mínimo que
   permita atribuir una diferencia al contexto, dentro del mismo presupuesto.
5. Usar saldo para evidencia mínima de paginación, total y límites. No iniciar un
   recorrido completo. Registrar tiendas no evaluadas y preguntas sin resolver.

Sólo timeout/5xx transitorio admiten retry. Detener ante 401/403/429, CAPTCHA,
login, control anti-bot o degradación sostenida; un 200 con página de bloqueo no
se acepta como catálogo. Parser fallido se corrige offline, sin volver a descargar
RAW válido. No seguir redirects/retries ocultos del cliente.

Guardar RAW antes de parsear, incluso intentos fallidos, SHA-256, URL/método/status,
fecha y duración, contexto demostrado y headers pertinentes sin secretos. Medir
requests totales/exitosos/retries, duplicados evitados, productos extraídos y por
request, tiempo y requests/producto. Requests por catálogo completo: no aplicable
en este probe. Publicar sólo evidencia segura y declarar cualquier redacción.

**Salida esperada:** evidencia fuente→formato→tienda→precio, muestras con provenance,
tabla de contextos evaluados/pendientes, comparación comercial y de disponibilidad
por separado, contrato candidato, señales de paginación y residual. No garantizar
que 40 solicitudes cubran todas las tiendas aún desconocidas: si falta evidencia,
pedir un siguiente probe acotado antes de solicitar full. Probe no autoriza full,
segunda observación ni recurrencia.

## Decidir granularidad antes de descargar catálogos completos

Comparar dentro de cada **formato y ciudad**, nunca consolidar marcas entre sí.
Usar mismas identidades SKU y presentación/unidad, sin matching por nombre. Panel
orientativo de 20–50 compartidos, con categorías y promociones/no promociones.
Registrar cobertura real, diferencias y no comparables por cada par y grupo.

- Comparar `current_price`, `reported_regular_price` e `is_promotion` separadamente
  y como firma comercial conjunta. Informar cantidad comparable, diferencias por
  campo y su unión; los conteos por campo pueden solaparse.
- Ausencia de precio/regular/promoción requiere resolver su semántica fuente; un
  desconocido no equivale a cero, false ni igualdad. Un regular declarado no basta
  por sí solo para promoción. No crear `previous_price` desde el precio regular.
- Disponibilidad se informa aparte: `in_stock`, `out_of_stock`, `unknown`, con
  conteo de diferencias totales y sólo disponibilidad entre precios comparables.
  Producto ausente no es OOS; cantidad disponible no se promete como stock exacto.
- Equivalencia consistente del panel común, sin evidencia técnica contraria:
  escoger un representante documentado del grupo. No afirmar precio universal de
  ciudad ni igualdad de todo el catálogo a partir de una muestra.
- Una diferencia comercial real, reproducible y atribuible al cambio de contexto
  exige grupos separados. Mantener producto, presentación, canal y demás variables
  constantes; conservar observaciones/timestamps y control mínimo reproducible.
  No gastar tráfico en repetir extensamente una separación ya demostrada.
- No aplicar transitividad A=B=C con paneles distintos o faltantes. Para agrupar
  exigir una firma común sustentada por observaciones comparables de todos los
  miembros; si no alcanza la cobertura, mantener decisión pendiente.

Registrar todas las tiendas descubiertas con formato, ciudad, identificador,
evaluación, grupo, representante y evidencia. Tienda no evaluada no se descarta ni
se declara equivalente por presupuesto. Elegir el representante por binding
reproducible y calidad/cobertura observada, sin duplicar por disponibilidad sola.

Antes de pedir full deben estar demostrados fuente, contrato, identidad,
contextos/formato, muestras, diferencias/equivalencias y todos los representantes
necesarios; además paginación, límites, totales/membership, reglas de completitud,
presupuesto y aceptación. Calcular requests/tiempo con page size observado y
recovery residual. Aceptar sólo reconciliando identidades y variantes, huecos,
duplicados, particiones y drift; fin de paginación no basta. No capturar fulls de
tiendas ya representadas sin una razón comercial demostrada.

## Persistencia offline: comprobado y pendiente

Regresión ejecutada con Python 3.12 y SQL productivo existente: **53 tests pasaron**
en `test_walmart_persistence`, `test_turso_cost_aware_persistence`,
`test_colonial_persistence`, `test_actualizar_mvp_sqlite_la_colonia` y
`test_actualizar_mvp_turso_la_colonia`. No HTTP Turso. Ningún cambio a scraper,
parser, fixture, SQL, esquema, dependencia o workflow de las tres cadenas.

El updater/validador actual admite La Colonia, Colonial y Walmart; Maxi/DF no están
admitidos. Las excepciones de unicidad por ciudad y precio NULL actuales son
específicas de Walmart. No ampliarlas por anticipación: sólo una fuente demostrada
puede justificar nuevas reglas, con migración controlada si resultara necesaria.
Sin muestras reales no fabricar fixtures ni declarar probada persistencia Maxi/DF.

Después del catálogo/contrato: extender mínimamente la ruta existente y validar
primera carga, run sin cambios, metadata-only, cambios de efectivo, regular,
promoción y disponibilidad; replay, rollback, out-of-order, entrada duplicada y
snapshot incompleto. Probar aislamiento Maxi↔DF y cada una↔La Colonia/Colonial/
Walmart en ambos sentidos, incluidas identidades fuente coincidentes y locations.

Mantener cinco tablas y `READ NECESSARY SCOPE → COMPARE ONCE → COMPUTE DELTA →
WRITE CHANGES ONLY → VERIFY AFFECTED SCOPE`. Si cambia SQL compartido, medir
N/2N/4N y crecimiento del histórico cerrado, verificar índices/planes, sin N+1,
comparaciones repetidas, scans globales ni writes idénticos. Run comercial sin
cambios: sólo `scrape_run`; metadata-only no abre historia. Medición SQLite no es
consumo facturado Turso. [Evidencia vigente del costo](../../reports/turso-cost-aware-persistence.md).

No solicitudes SQL mientras siga bloqueado. Tras restablecimiento comprobado y
autoridad vigente: backup/esquema/cronología, consumo inicial, carga controlada,
verificación del scope afectado y consumo final. No nuevo servicio, sexta tabla,
base paralela, tuning remoto, recurrencia ni cadena posterior a Maxi/DF.
