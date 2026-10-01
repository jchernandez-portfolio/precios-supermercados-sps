# Cloudflare edge provenance

Estado operativo mutable: consultar [`../../docs/PROJECT_STATE.md`](../../docs/PROJECT_STATE.md).

Este directorio contiene la frontera edge de Cloudflare Workers usada por el proyecto. La documentación aquí describe contratos **estables** de implementación y seguridad; no afirma por sí sola que un Worker esté desplegado, que exista una autorización live vigente ni que el catálogo tenga autoridad productiva.

## Worker productivo

`wrangler.json` declara:

- Worker `precios-sps-provenance`;
- entrypoint `src/index.mjs`;
- Durable Object `AuthorizationGateway` con almacenamiento SQLite;
- binding `AUTHORIZATION_GATEWAY`;
- `CF_VERSION_METADATA`;
- tracing/observability configurado;
- preview URLs deshabilitadas.

Secrets requeridos por la configuración versionada:

```text
EDGE_RECEIPT_PRIVATE_KEY_PKCS8_B64URL
EDGE_RECEIPT_PUBLIC_KEY_SPKI_B64URL
EDGE_COLLECTOR_CODE_SHA256
```

La private key de receipts debe existir únicamente en Cloudflare. **No debe copiarse a GitHub Secrets, al repositorio, a logs, artifacts ni al chat.**

El estado real de despliegue, variables de GitHub Environment y blockers vigentes vive exclusivamente en `docs/PROJECT_STATE.md`.

## Contrato productivo

La identidad OIDC queda fijada en código a:

- repositorio `jchernandez-portfolio/precios-supermercados-sps`;
- repository ID esperado;
- ref `refs/heads/main`;
- workflow live canónico en `main`;
- environment `la-colonia-live`;
- evento `workflow_dispatch`;
- audience `urn:precios-sps:cloudflare:collector:v1`.

La frontera productiva además mantiene cerrados:

- scheme/host/path/método del origen;
- query GraphQL/variables permitidas;
- ventanas/page size;
- órdenes y traversals;
- redirects;
- budget/deadline/pacing;
- single-flight, replay e idempotencia;
- fencing por autorización y `run_id:run_attempt`;
- firma Ed25519 y key ID;
- release por `CF_VERSION_METADATA`;
- tracing/Workers Observability;
- rutas separadas para catálogo y structural discovery.

El caller no puede convertir estos campos en autoridad comercial ni ampliar el allowlist por conveniencia.

## Cadena de provenance

La cadena técnica prevista es:

```text
GitHub Actions
-> GitHub OIDC
-> Worker productivo
-> AuthorizationGateway (Durable Object)
-> request allowlisted
-> respuesta exacta + SHA-256
-> receipt Ed25519
-> verificación externa
-> Workers Observability
-> manifest/readiness
-> decisión de autoridad separada
```

Un receipt firmado, un hash o una suite offline prueban integridad/igualdad dentro de su contrato; no conceden por sí solos:

```text
production_authority = true
catalog_accepted = true
extraction_enabled = true
```

## Contexto SPS

El edge soporta receipts context-bound para el catálogo y rutas estructurales. El valor raw que determine el contexto de ubicación no debe persistirse en receipts, traces, artifacts ni logs; se conservan únicamente fingerprints/evidencia sanitizada según los contratos Python/JavaScript compartidos.

El estado técnico vigente del binding SPS y cualquier evidencia live pendiente se consulta en `docs/PROJECT_STATE.md`. No se infiere placement de `regionId` a partir de evidencia que no lo haya preservado.

## Sonda controlada no-La-Colonia

La sonda usa infraestructura separada:

```text
precios-sps-controlled-origin
precios-sps-controlled-probe
ProbeLedger
```

También usa audience, environment, llaves y dominio criptográfico propios. El origen permitido queda separado de La Colonia y un receipt de sonda no puede validarse como receipt productivo.

La política y evidencia histórica de la sonda viven en [`../../docs/cloudflare-controlled-probe-runbook.md`](../../docs/cloudflare-controlled-probe-runbook.md) y `PROJECT_STATE.md`. No repetirla por defecto sólo para recrear evidencia ya obtenida.

## Despliegue productivo

El repositorio **no expone un script automático `deploy:production`**. El despliegue productivo modifica infraestructura externa y requiere una cuenta/credenciales Cloudflare autorizadas; no se ejecuta desde CI ni se deriva de una autorización read-only del supermercado.

La preparación, secretos, despliegue manual, read-back y configuración posterior del GitHub Environment están documentados en:

[`../../docs/cloudflare-production-deploy-runbook.md`](../../docs/cloudflare-production-deploy-runbook.md)

Ese runbook no autoriza tráfico a La Colonia. Una vez cerrada la infraestructura, cualquier observación live sigue requiriendo la autorización humana explícita que corresponda al alcance exacto.

## Toolchain

La CLI versionada se invoca únicamente por el script fijado en `package.json`:

```text
npm run wrangler -- ...
```

La versión exacta está pinneada en `package.json`. No usar `wrangler@latest`, instalaciones globales ni comandos productivos fuera del runbook versionado durante una operación real.

## Validación offline

Desde la raíz del repositorio, la CI del proyecto ejecuta la suite Python completa, que a su vez cubre la suite Node canónica de este directorio y la auditoría de workflows.

Las pruebas del edge cubren, entre otros:

- JSON/timestamps/hashes canónicos;
- OIDC/JWKS/front door/fencing;
- Durable Object/ledger/replay;
- URL GraphQL y límites;
- receipts/firmas;
- structural runtime;
- catálogo context-bound;
- tracing y atributos de observability;
- configuración Wrangler;
- sonda aislada;
- invariantes de no-authority.

No se mantiene aquí un conteo mutable de tests; el conteo observado más reciente pertenece a `PROJECT_STATE.md`/runs de CI.

## Referencias

- [`../../docs/PROJECT_STATE.md`](../../docs/PROJECT_STATE.md): estado operativo vigente.
- [`../../docs/trusted-collector-productivo.md`](../../docs/trusted-collector-productivo.md): arquitectura productiva estable.
- [`../../docs/cloudflare-tracing-provenance.md`](../../docs/cloudflare-tracing-provenance.md): contrato de tracing/provenance.
- [`../../docs/cloudflare-controlled-probe-runbook.md`](../../docs/cloudflare-controlled-probe-runbook.md): política de sonda controlada.
- [`../../docs/cloudflare-production-deploy-runbook.md`](../../docs/cloudflare-production-deploy-runbook.md): preparación y operación manual del deploy productivo.
