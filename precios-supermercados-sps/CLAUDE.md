# CLAUDE.md — Súper Compras (RPI)

Nota de traspaso para Claude Code. Responde siempre en **español, breve y directo**.
Gobernanza y contrato del proyecto: `AGENTS.md` (manda sobre este archivo).
Estado operativo: `docs/PROJECT_STATE.md`. Ramas `rpi/…`, títulos de PR `[RPI] …`.

## Protocolo de fases (obligatorio)

1. Una fase a la vez, en orden. Nunca empezar la siguiente por cuenta propia.
2. Al terminar, DETENTE y responde con:
   ```
   ✅ FASE N terminada
   - Qué hice: (3–5 líneas)
   - PR: (enlace o "sin PR")
   - Qué necesito de ti: (...)
   ➡️ SIGUIENTE: FASE N+1 — (nombre)
   🧠 Modelo recomendado: (Opus 5.5 o Fable 5.2) · esfuerzo: medium
   ```
   Si el modelo cambia: "Cambia el modelo con /model a ___ y escríbeme 'sigue con la fase N+1'."
3. Continuar sólo cuando el usuario diga "sigue con la fase N+1". Al retomar: leer
   este archivo y el estado de los PRs con `gh api` (REST; GraphQL no está disponible).
4. Atascado de verdad → "Recomiendo cambiar a Fable 5.2 para esta parte" + motivo.
5. Aprobaciones a mitad de fase (fusionar, workflow que escribe, guardar decisiones) → DETENTE.

## Reglas

1. **Secretos**: nunca leerlos, imprimirlos ni pedirlos (Turso, R2, GEMINI_API_KEY,
   ANTHROPIC_API_KEY, tokens). Sólo en GitHub Secrets, sólo los usan los workflows,
   sólo en encabezados HTTP (nunca en URLs ni logs).
2. Nunca fusionar un PR sin aprobación explícita en el chat. Un PR por fase.
3. Workflows de sólo lectura: se pueden lanzar. Los que ESCRIBEN en Turso (refresco de
   homologación, decisiones de tamaño, cola de tamaños) → pedir permiso antes.
4. Supermercados: sólo datos públicos, una petición a la vez, sin saltar anti-bot.
5. Antes de cada PR: pruebas, ruff y biome en verde. Si cambia `super-compras/`,
   subir versión con `scripts/versionar_super_compras.py --auto`.
6. Ninguna decisión automática/IA reemplaza una humana; la humana siempre reemplaza a la IA.
7. Si algo no está claro, parar y preguntar.

## Comandos (desde `precios-supermercados-sps/`)

```bash
PYTHONPATH=src python -m pytest -q
python -m ruff check .          # usar ruff==0.14.5 de requirements.txt (uno más nuevo da falsos errores)
npx @biomejs/biome@2.3.5 lint super-compras
python scripts/versionar_super_compras.py --auto   # o --check
```

## Gotchas

- **Guarda de esquema Turso**: toda tabla derivada nueva va en `OPTIONAL_DERIVED_TABLES`
  de `scripts/actualizar_mvp_turso_la_colonia.py` (y `verificar_integridad_turso.py`),
  o se rompen las capturas diarias (pasó con `size_decisions`; hotfix #72).
- **Registro de seguridad de workflows**: `tests/test_workflow_security_audit.py`. Cada
  workflow nuevo/cambiado se registra (permisos, triggers, secretos permitidos); un
  secreto sólo en el paso que lo usa.
- **Versión de la app**: vive en `index.html`, `app.js`, `data.js`, `sw.js`,
  `illustration.js`, `admin.js`, `scanner.js`; cambiarla siempre con el script, nunca a mano.
- **Frontend** (`super-compras/`): vanilla JS, sin `innerHTML`, `fetch` sólo en
  `data.js` y `sw.js`; CSP en `index.html` (no añadir orígenes sin necesidad).
- Turso: leer sólo el alcance necesario, escribir sólo deltas (ver `AGENTS.md`).
- `.claude/settings.json` (raíz) pre-aprueba `gh api` de lectura de Actions y el dispatch de
  workflows del repo; lo que escribe en Turso sigue necesitando el permiso del dueño.

## Estado (14-nov-2026, fin de las fases 0–8)

- Motor de identidad **v2.10** (`product_identity_v2.py`): "corrige con evidencia" (foto por oferta,
  `size_offer_evidence`, `published_as` en el catálogo; ver `docs/revision-tamanos.md`). Antes v2.9: regla de onzas (v2.8, #65) y PR B
  (g ≈ ml ≤5 % en lácteos/jugos/salsas + peso sobre conteo "medio"), todo con "nunca
  empeorar" (#85). Medición en `scripts/medir_pr_b.py` / workflow de auditoría (`--pr-b`).
- Decisiones de tamaño: panel `#/admin/revision` (humanas) o modo sesión
  (`scripts/preparar_revision_claude.py` → tabla → aprobación del dueño →
  `scripts/guardar_revision_claude.py --enviar`, `engine:claude_sesion`, #76). La IA nunca
  pisa una decisión humana. La medición de identidad aplica las decisiones (#77).
- Cola pública `revision-tamanos/cola.json` + IA automática (#75, #78): **Claude Sonnet 5.5**
  primero (`ANTHROPIC_API_KEY`, créditos de API del plan Max; tope `claude_max`=200) y Gemini
  de respaldo (404 de modelo retirado → siguiente). `resueltas_motor` alimenta "Resueltos por
  IA". La cola sólo hace commit si cambian códigos o el resumen de IA (#84).
- Web pública: escáner de código de barras (`scanner.js`, BarcodeDetector + ZXing propio en
  `vendor/`, índice `barcodes.json` por ciudad, #82); QA móvil/accesibilidad (#81); Mi compra
  avisa productos que salieron del catálogo (#84).
- Datos: auditoría del catálogo público y arreglos (libra en TGU, fotos heredadas ajenas,
  "Café", alternativas, "de hoy") en #83; informe en
  `docs/calidad/auditoria-catalogo-publico-2026-11-13.md`.
- La Colonia: marcas recortadas por VTEX se recuperan por `category-2` (#74).
- Diseño pendiente de implementar: `docs/propuestas-revision-tamanos.md` (#79).
- Open Food Facts eliminado (#84).
- 14-nov (noche): Claude en la cola **funciona** tras #93 (esquema `anyOf` + modo simple ante 400;
  prueba: 20 consultas Sonnet 5.5, 0 errores). Gemini sin cuota (429). Decisiones de tamaño: el
  workflow acepta `Jchernand3z19` y `novawebstudio-HN` (#94); 14 decisiones de la tanda 1 guardadas.
  Ficha muestra «X lo publica como N» (#95) y el título sale de un súper con el tamaño oficial (#96).
- Un refresco lanzado por el workflow de decisiones no dispara la publicación: lanzar a mano
  «Publicar analítica segura» (workflow_dispatch). El deploy de Pages cambia `?v=` por el commit.

**Pendientes**: activar ofertas no vistas (código listo y apagado; requiere permiso del
dueño, `docs/calidad/ofertas-no-vistas.md`), 3,913 fotos heredadas TGU por verificar,
títulos duplicados VTEX, implementar las 4 propuestas de #79, borrar ramas `rpi/*` ya
fusionadas (requiere permiso del dueño), probar el escáner en un iPhone/Android reales.

## Objetivo: Claude lee las imágenes, Gemini es respaldo

- **Modo SESIÓN** (suscripción): Claude Code revisa fotos por tandas; el usuario aprueba
  antes de guardar (`decided_by = engine:claude_sesion`).
- **Modo AUTOMÁTICO** (diario): API de Claude (si existe `ANTHROPIC_API_KEY`) → Gemini.
  Si Claude falla (sin crédito, llave inválida/ausente, 429, 529/5xx tras reintentos),
  la corrida sigue con Gemini sin detenerse.

## Fases

| Fase | Tema | Modelo |
|---|---|---|
| 0 | Nota de traspaso (este archivo) | Opus 5.5 |
| 1 | Verificar captura diaria tras #72 (sólo lectura, sin PR) | Opus 5.5 |
| 2 | Proveedores de visión: Claude primero, Gemini respaldo | Opus 5.5 |
| 3 | Modo SESIÓN: revisión de fotos con Claude Code | Opus 5.5 (Fable 5.2 si falla la lectura) |
| 4 | Medición de identidad con decisiones | Opus 5.5 |
| 5 | Medición PR B: g≈ml y peso sobre conteo (sólo lectura) | Opus 5.5 |
| 6 | Implementar PR B (motor v2.9) | Opus 5.5 |
| 7 | Diseño: docs/propuestas-revision-tamanos.md | Fable 5.2 |
| 8 | Limpieza (ramas, workflow OFF, commits de la cola) | Opus 5.5 |
