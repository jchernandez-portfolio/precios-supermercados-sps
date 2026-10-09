# Disparo diario puntual (Cloudflare Worker)

El cron de GitHub Actions del corte diario (`43 7 * * *`, 01:43 Honduras)
llegaba con **5–9 h de retraso** (2026-10-02 a 10-09: arranques entre 06:42 y
10:21). A esa hora los súper actualizan su catálogo y la mayoría de fallas eran
"el catálogo cambió durante la lectura". Este Worker usa un Cron Trigger de
Cloudflare (puntual al minuto) para arrancar el workflow
`precios-supermercados-sps-la-colonia-mvp-update.yml` con `workflow_dispatch`
a las **11:17 UTC = 05:17 Honduras** (hora original del corte; el adelanto a
la 01:43 sólo compensaba el retraso de GitHub y ya no hace falta).

- Archivo único: `worker.mjs` (sin dependencias). Pruebas: `node --test test/worker.test.mjs`.
- No expone endpoints (`fetch` → 404). Una sola llamada saliente: el dispatch a la API de GitHub.
- Reintenta 5xx/errores de red hasta 3 veces (20 s); 4xx no se reintenta.
- **Respaldo:** si el Worker no dispara, el workflow "Operador productivo
  confiable" (08:17 Honduras) arranca el corte del día si aún no existe, y como
  siempre reintenta sólo las cadenas fallidas (máximo 3 intentos).

## Puesta en marcha (una vez, la hace el responsable)

1. **Token de GitHub** (github.com → Settings → Developer settings →
   Fine-grained tokens → Generate new token):
   - Resource owner: `jchernandez-portfolio`; Repository access: *Only select
     repositories* → `precios-supermercados-sps`.
   - Repository permissions: **Actions: Read and write** (nada más).
   - Expiración: 1 año (anotar la fecha para renovarlo).
2. **Worker** (dash.cloudflare.com → Workers & Pages → Create → Worker):
   - Nombre `precios-sps-daily-trigger`; pegar el contenido de `worker.mjs` y *Deploy*.
   - Settings → Variables and Secrets → Add → tipo **Secret**, nombre
     `GITHUB_DISPATCH_TOKEN`, valor = el token del paso 1.
   - Settings → Trigger events → Add → **Cron Triggers** → `17 11 * * *`.
   - Settings → Domains & Routes: desactivar la URL `workers.dev` (no se necesita).
   - Alternativa por línea de comandos: `npx --yes wrangler@4.125.0 deploy`
     desde esta carpeta y `npx wrangler secret put GITHUB_DISPATCH_TOKEN`.
3. **Prueba:** en el Worker, pestaña *Settings → Trigger events*, usar
   "Trigger scheduled event" (o esperar a las 05:17) y verificar en GitHub
   Actions un run de "La Colonia - Actualización MVP" con evento
   `workflow_dispatch`. Logs del Worker: `daily_trigger_dispatched` o
   `daily_trigger_failed:<status>` (401 = token inválido/vencido).

## Renovación del token

Cuando venza, crear uno nuevo con los mismos permisos y reemplazar el secreto
`GITHUB_DISPATCH_TOKEN`. Mientras tanto el operador sigue arrancando el corte a
las 08:17 (respaldo).
