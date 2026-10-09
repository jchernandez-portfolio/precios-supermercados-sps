// Disparo puntual del corte diario (aprobado por el responsable 2026-10-09).
//
// El cron de GitHub Actions llegaba con 5–9 h de retraso (programado 01:43,
// arrancaba entre 06:42 y 10:21 hora de Honduras). Este Worker usa un Cron
// Trigger de Cloudflare (puntual al minuto) para pedir a GitHub que arranque
// el workflow diario con `workflow_dispatch` a las 11:17 UTC = 05:17 Honduras
// (la hora original del corte; la 01:43 sólo compensaba el retraso de GitHub).
//
// - Único secreto: GITHUB_DISPATCH_TOKEN, token fine-grained con permiso
//   "Actions: Read and write" SÓLO sobre jchernandez-portfolio/precios-supermercados-sps.
// - No expone ningún endpoint: fetch() responde 404.
// - Reintenta errores transitorios de GitHub (5xx/red) hasta 3 veces.
// - Respaldo: si este Worker no dispara, el "Operador productivo confiable"
//   (08:17 Honduras) arranca el corte del día si aún no existe.

export const OWNER = "jchernandez-portfolio";
export const REPO = "precios-supermercados-sps";
export const WORKFLOW = "precios-supermercados-sps-la-colonia-mvp-update.yml";
export const CRON = "17 11 * * *";
const API = `https://api.github.com/repos/${OWNER}/${REPO}/actions/workflows/${WORKFLOW}/dispatches`;
const MAX_ATTEMPTS = 3;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export async function dispatchDaily(env, { fetchImpl = fetch, wait = sleep, retryDelayMs = 20000 } = {}) {
  const token = env && env.GITHUB_DISPATCH_TOKEN;
  if (typeof token !== "string" || token.trim() === "") {
    throw new Error("daily_trigger_token_missing");
  }
  let lastStatus = "none";
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt += 1) {
    let response;
    try {
      response = await fetchImpl(API, {
        method: "POST",
        headers: {
          authorization: `Bearer ${token.trim()}`,
          accept: "application/vnd.github+json",
          "content-type": "application/json",
          "user-agent": "precios-sps-daily-trigger",
          "x-github-api-version": "2022-11-28",
        },
        body: JSON.stringify({ ref: "main", inputs: { live_read_only_authorized: "true" } }),
      });
    } catch (error) {
      lastStatus = "network_error";
      if (attempt < MAX_ATTEMPTS) await wait(retryDelayMs);
      continue;
    }
    if (response.status === 204 || response.status === 200) {
      return { dispatched: true, attempt, status: response.status };
    }
    lastStatus = String(response.status);
    // 4xx (token inválido, sin permiso, workflow inexistente): no se reintenta.
    if (response.status < 500) break;
    if (attempt < MAX_ATTEMPTS) await wait(retryDelayMs);
  }
  throw new Error(`daily_trigger_dispatch_failed:${lastStatus}`);
}

export default {
  async scheduled(controller, env, ctx) {
    const work = dispatchDaily(env).then(
      (result) => console.log(JSON.stringify({ event: "daily_trigger_dispatched", cron: controller.cron, ...result })),
      (error) => {
        console.error(JSON.stringify({ event: "daily_trigger_failed", cron: controller.cron, error: String(error.message || error) }));
        throw error;
      },
    );
    ctx.waitUntil(work);
  },
  async fetch() {
    return new Response("Not found", { status: 404 });
  },
};
