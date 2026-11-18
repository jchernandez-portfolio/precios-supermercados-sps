// Panel interno de revisión de tamaños (#/admin/revision).
// Mismo código de barras con tamaños distintos entre súper. Lo claro lo resuelve la
// IA (Gemini) sola; aquí llega lo que no pudo leer o donde hay conflicto. Cada oferta
// muestra la foto que ya tenemos en el sistema (la misma que leyó la IA) y lo que la
// IA leyó en ella; una persona fija el tamaño oficial (o marca que no es el mismo
// producto). La búsqueda en la web del súper queda como respaldo. "Guardar" lanza el workflow que valida y guarda la decisión; el
// motor la aplica en el siguiente refresco: las ofertas con el tamaño oficial
// comparan y las demás quedan aparte.
// La llave de GitHub se guarda en esta pestaña (sessionStorage) o, si la persona marca
// «Recordar en este dispositivo», en localStorage; se borra con «Olvidar llave» o si
// GitHub la rechaza (401/403). Sólo viaja en el encabezado Authorization a api.github.com.
import {
  RETAILER_NAMES, SIZE_DECISIONS_RUNS_URL, SIZE_UNITS, loadSizeQueue, sendSizeDecisions,
} from "./data.js?v=20261118-4b1bb43";

const TOKEN_KEY = "super-compras-admin-github-token";
// Códigos ya enviados en este dispositivo: {gtin: ISO de envío}. Se ocultan de la cola
// hasta que una cola generada después del envío los refleje (o pasen 7 días).
const SENT_KEY = "super-compras-admin-sent";
const SENT_MAX_DAYS = 7;
// Margen para no fiarse de una cola que ya se estaba armando cuando se envió.
const SENT_MARGIN_MS = 10 * 60 * 1000;
const UNIT_LABEL = { g: "g", ml: "ml", oz: "oz", lb: "lb", unit: "unidades" };
const KIND_LABEL = {
  empate: "Empate: hoy no se compara",
  atipico: "Un súper distinto: quedó fuera de la comparación",
  correccion_ia: "Corrección de IA: la web contradice lo que la IA guardó por las fotos",
};
// Búsqueda del código de barras en cada súper (Los Andes no tiene buscador por código).
const STORE_SEARCH = {
  walmart: (ean) => `https://www.walmart.com.hn/${ean}?_q=${ean}&map=ft`,
  paiz: (ean) => `https://www.paiz.com.hn/${ean}?_q=${ean}&map=ft`,
  la_colonia: (ean) => `https://www.lacolonia.com/${ean}?_q=${ean}&map=ft`,
  colonial: (ean) => `https://supercolonial.com/search?q=${ean}`,
  comisariato_los_andes: () => "https://comisariatolosandes.com/todos",
};

// Fotos de Súper Compras en el bucket R2 (las mismas que lee la IA).
const PHOTO_BASE = "https://pub-0dcb2204775c484dbe6681e659eee236.r2.dev/fotos/v2/";
const PHOTO_NAME = /^[0-9a-f]{24}-[0-9a-f]{6,64}$/;

const review = { queue: null, error: null, loading: null, filter: "todos", decisions: {}, sent: new Set(), sentAt: {}, status: null, sending: false };

function storage(kind) {
  try { return kind === "local" ? localStorage : sessionStorage; } catch { return null; }
}

function rememberedToken() {
  try { return storage("local")?.getItem(TOKEN_KEY) || ""; } catch { return ""; }
}

function readToken() {
  try { return rememberedToken() || storage("session")?.getItem(TOKEN_KEY) || ""; } catch { return ""; }
}

// Una sola copia: en localStorage si se recuerda, si no en sessionStorage; vacío = borrar ambas.
export function writeToken(value, remember = false) {
  for (const kind of ["local", "session"]) {
    try {
      const store = storage(kind);
      if (!store) continue;
      if (value && (kind === "local") === remember) store.setItem(TOKEN_KEY, value); else store.removeItem(TOKEN_KEY);
    } catch { /* sin almacenamiento: la llave vive sólo en el campo */ }
  }
}

// Lo enviado que la cola publicada todavía no refleja. Una cola generada más de 10
// minutos después del envío ya leyó size_decisions: si el código sigue en ella es
// que no se guardó y debe volver a verse.
export function pendingSent(stored, queue, now = Date.now()) {
  const generated = Date.parse(queue?.generated_utc || "");
  const keep = {};
  for (const [gtin, at] of Object.entries(stored && typeof stored === "object" ? stored : {})) {
    const sentAt = Date.parse(at);
    if (!/^\d{8,14}$/.test(gtin) || !Number.isFinite(sentAt)) continue;
    if (now - sentAt > SENT_MAX_DAYS * 86400000) continue;
    if (Number.isFinite(generated) && generated > sentAt + SENT_MARGIN_MS) continue;
    keep[gtin] = at;
  }
  return keep;
}

function readSent() {
  try {
    const value = JSON.parse(localStorage.getItem(SENT_KEY) || "{}");
    return value && typeof value === "object" ? value : {};
  } catch { return {}; }
}

function writeSent(map) {
  try {
    if (Object.keys(map).length) localStorage.setItem(SENT_KEY, JSON.stringify(map)); else localStorage.removeItem(SENT_KEY);
  } catch { /* sin almacenamiento: se recuerda sólo en esta pestaña */ }
}

// Pestañas del panel: qué lista muestra cada una. Todo sale de la cola publicada más
// lo que la persona marcó (decisions) o ya envió (sent) en este dispositivo.
export const FILTERS = [
  ["todos", "Todos"], ["empate", "Empate"], ["atipico", "Un súper distinto"], ["pendiente", "Sin decidir"],
  ["correccion_ia", "Corrección de IA"], ["ia", "Resueltos por IA"],
];

export function itemsFor(filter, queue, decisions = {}, sent = new Set()) {
  if (filter === "ia") {
    // Sólo lo que la IA decidió sola (engine:* en size_decisions según la cola).
    const engine = Array.isArray(queue?.decididas_ia) ? new Set(queue.decididas_ia) : null;
    return (queue?.auto_resueltas || []).filter((item) => !engine || engine.has(item.gtin));
  }
  const open = (queue?.items || []).filter((item) => !sent.has(item.gtin));  // lo enviado ya no se revisa
  if (filter === "todos") return open;
  if (filter === "pendiente") return open.filter((item) => !decisionOf(decisions[item.gtin]));
  return open.filter((item) => item.kind === filter);
}

export function filterCounts(queue, decisions = {}, sent = new Set()) {
  return Object.fromEntries(FILTERS.map(([key]) => [key, itemsFor(key, queue, decisions, sent).length]));
}

// Aviso cuando la última cola no pudo leer el índice de fotos.
export function photoNotice(queue) {
  if (!queue?.fotos_error) return "";
  return queue.fotos_fuente === "cola_anterior"
    ? "Las fotos no cargaron en esta actualización: se muestran las de la anterior."
    : "Las fotos no cargaron en esta actualización. La próxima actualización lo vuelve a intentar.";
}

// Progreso real de la revisión: decididos (persona + IA, según la cola publicada) más
// lo enviado en esta sesión, sobre el total (decididos + pendientes en la cola).
export function reviewProgress(queue, sent = 0) {
  const decided = Number.isInteger(queue?.decided) && queue.decided > 0 ? queue.decided : 0;
  const pending = Array.isArray(queue?.items) ? queue.items.length : 0;
  const total = decided + pending;
  const done = Math.min(total, decided + Math.max(0, sent));
  return { done, total, pct: total ? Math.round((done / total) * 100) : 0 };
}

// Formas del código para buscar en el súper. El GTIN guardado tiene 14 dígitos y
// cada súper indexa una forma distinta (probado el 12-nov: Walmart encuentra
// "0049733830119" de 13 y Colonial sólo "049733830119" de 12). 13 y 12 existen
// sólo si los ceros que se quitan están ahí (un 742… no tiene forma de 12).
export function searchVariants(gtin) {
  const digits = String(gtin || "").replace(/\D/g, "");
  if (digits.length !== 14) return [];
  return [
    { label: "14", code: digits },
    digits.startsWith("0") ? { label: "13", code: digits.slice(1) } : null,
    digits.startsWith("00") ? { label: "12", code: digits.slice(2) } : null,
  ].filter(Boolean);
}

// El código tal como lo publica el súper (sin espacios); null si no lo trae.
export function storeCode(offer) {
  const digits = String(offer?.code || "").replace(/\D/g, "");
  return digits.length >= 8 && digits.length <= 14 ? digits : null;
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Respaldo sin permisos de portapapeles: selección temporal + copiar.
    const field = document.createElement("textarea");
    field.value = text;
    field.setAttribute("readonly", "");
    field.style.position = "fixed";
    field.style.opacity = "0";
    document.body.append(field);
    field.select();
    let copied = false;
    try { copied = document.execCommand("copy"); } catch { copied = false; }
    field.remove();
    return copied;
  }
}

const AI_REASONS = {
  ilegible: "La etiqueta no se lee en ninguna foto.",
  otro_producto: "La foto parece de otro producto (otra marca o variante).",
  sin_respuesta: "La IA no dio una lectura para esta foto.",
  respuesta_invalida: "La IA no dio una respuesta válida.",
  fotos_distintas: "Las fotos dicen tamaños distintos.",
  minoria: "Lo leído coincide sólo con una parte de los súper (no con la mayoría).",
};

export function photoUrls(offer) {
  const name = offer?.foto;
  if (typeof name !== "string" || !PHOTO_NAME.test(name)) return null;
  const path = `${name.slice(0, 2)}/${name}.webp`;
  return { thumb: `${PHOTO_BASE}320/${path}`, full: `${PHOTO_BASE}800/${path}` };
}

const sizeText = (value, unit) => `${value} ${UNIT_LABEL[unit] || unit}`;

// Quién leyó la foto (Claude por API o en sesión, Gemini); lecturas viejas sin fuente: "IA".
const AI_NAME = { claude: "Claude", claude_sesion: "Claude (sesión)", gemini: "Gemini", off: "Open Food Facts" };
const aiName = (source) => (Object.hasOwn(AI_NAME, source) ? AI_NAME[source] : "IA");

// Lo que la IA leyó en la foto de una oferta: verde si coincide con su título, alerta si no.
export function offerReading(offer) {
  const ia = offer?.ia;
  if (!ia || typeof ia !== "object") return null;
  if (ia.status === "leido" && Number(ia.value) > 0 && SIZE_UNITS.includes(ia.unit)) {
    return {
      kind: ia.coincide ? "ok" : "alert",
      label: `🤖 ${aiName(ia.fuente)} leyó: ${sizeText(ia.value, ia.unit)}${ia.coincide ? " ✓" : " ⚠"}`,
      title: [ia.text ? `Texto en la foto: ${ia.text}` : "", ia.coincide ? "Coincide con el título." : "No coincide con el título de este súper."]
        .filter(Boolean).join(" · "),
    };
  }
  return { kind: "muted", label: "🤖 No legible", title: AI_REASONS[ia.reason] || AI_REASONS.ilegible };
}

// Resumen de la IA para el código: botón con el tamaño leído, aviso o nada.
export function aiSuggestion(item) {
  const ai = item?.ia;
  if (!ai || typeof ai !== "object") return null;
  const valid = Number(ai.value) > 0 && SIZE_UNITS.includes(ai.unit);
  const text = typeof ai.text === "string" ? ai.text : "";
  if (valid && ["auto", "sugerencia", "conflicto", "discrepancia"].includes(ai.status)) {
    const name = aiName(ai.fuente);
    const labels = {
      auto: `🤖 Resuelto por ${name}`, sugerencia: name === "IA" ? "🤖 Sugerencia" : `🤖 Sugerencia de ${name}`,
      conflicto: `🤖 ${name} leyó (conflicto)`, discrepancia: `🤖 ${name} leyó (no coincide con ningún título)`,
    };
    return {
      kind: "suggest", status: ai.status, value: String(ai.value), unit: ai.unit, text,
      label: `${labels[ai.status]}: ${sizeText(ai.value, ai.unit)}`, title: [text && `Leído: ${text}`, AI_REASONS[ai.reason]].filter(Boolean).join(" · "),
    };
  }
  if (ai.status === "sin_foto") return { kind: "note", label: "🤖 Sin foto", title: "Ninguna oferta tiene foto para leer." };
  return { kind: "note", label: "🤖 No legible", title: AI_REASONS[ai.reason] || AI_REASONS.ilegible };
}

// Búsqueda web por GTIN. Lo verificado ya se guardó solo y no llega aquí; lo que queda
// es sugerencia: un clic sólo llena el formulario. La URL va en el título y en un enlace.
const MANUAL_NOTE = "🤖 No se pudo verificar el dato en la web. Requiere revisión humana";
const safeUrl = (url) => (typeof url === "string" && /^https?:\/\/[^\s]+$/i.test(url) && url.length <= 500 ? url : "");
export function webSuggestion(item) {
  const web = item?.web;
  if (!web || typeof web !== "object") return null;
  const url = safeUrl(web.url);
  if (web.status === "encontrado" && Number(web.value) > 0 && SIZE_UNITS.includes(web.unit) && url) {
    const matches = Array.isArray(web.coincide_con) ? web.coincide_con.filter((name) => typeof name === "string") : [];
    const sources = Array.isArray(web.fuentes) ? web.fuentes.filter((source) => safeUrl(source?.url)) : [];
    const host = (() => { try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return ""; } })();
    return {
      kind: "suggest", value: String(web.value), unit: web.unit, url, host,
      label: `🌐 Sugerencia Web: ${sizeText(web.value, web.unit)}`,
      title: [
        `Fuente: ${url}`,
        typeof web.text === "string" && web.text ? `Dice: ${web.text}` : "",
        matches.length ? `Coincide con: ${matches.join(", ")}` : "No coincide con ningún título publicado.",
        sources.length ? `Google usó: ${sources.map((source) => source.title || source.url).join(", ")}` : "",
        MANUAL_NOTE,
        "Revisa la fuente: al tocarlo sólo se llena el formulario.",
      ].filter(Boolean).join("\n"),
      matches: matches.length,
    };
  }
  if (web.status === "no_encontrado") return { kind: "note", label: MANUAL_NOTE, title: "La búsqueda web no encontró una fuente independiente con el tamaño oficial de este código." };
  return null;
}

// Tamaños publicados, el más repetido primero: son los botones rápidos.
export function sizeOptions(item) {
  const groups = new Map();
  for (const offer of item.offers) {
    if (!offer.value || !offer.unit) continue;
    const key = `${offer.value} ${offer.unit}`;
    const entry = groups.get(key) || { value: offer.value, unit: offer.unit, stores: [] };
    entry.stores.push(RETAILER_NAMES[offer.store] || offer.store);
    groups.set(key, entry);
  }
  return [...groups.values()].sort((a, b) => b.stores.length - a.stores.length);
}

export function decisionOf(entry) {
  if (!entry) return null;
  if (entry.different) return "different_products";
  return Number(String(entry.value ?? "").replace(",", ".")) > 0 && SIZE_UNITS.includes(entry.unit) ? "size" : null;
}

function pendingDecisions() {
  return Object.entries(review.decisions)
    .filter(([gtin, entry]) => decisionOf(entry) && !review.sent.has(gtin))
    .map(([gtin, entry]) => ({ gtin, decision: decisionOf(entry), value: entry.value, unit: entry.unit, note: entry.note }));
}

export function mountSizeReview(main, { h, fill }) {
  const list = h("div", { class: "rv-list" });
  const count = h("span", { class: "muted" });
  const status = h("p", { class: "rv-status", role: "status", "aria-live": "polite" });
  const send = h("button", { type: "button", class: "primary" }, "Guardar y resolver");
  const token = h("input", {
    id: "rv-token", type: "password", autocomplete: "off", spellcheck: "false", placeholder: "github_pat_…", value: readToken(),
  });
  const remember = h("input", { id: "rv-remember", type: "checkbox", checked: Boolean(rememberedToken()) });
  token.addEventListener("input", () => writeToken(token.value.trim(), remember.checked));
  remember.addEventListener("change", () => writeToken(token.value.trim(), remember.checked));
  const forget = h("button", { type: "button", class: "rv-link" }, "Olvidar llave");
  const forgetToken = () => { token.value = ""; remember.checked = false; writeToken(""); };
  forget.addEventListener("click", () => { forgetToken(); token.focus(); });
  const progressText = h("span", { class: "rv-progress-text" });
  const photoNote = h("p", { class: "rv-status warn small", role: "status", hidden: true });
  const progressFill = h("span", { class: "rv-progress-fill" });
  const progressTrack = h("span", {
    class: "rv-progress-track", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-label": "Progreso de revisión",
  }, progressFill);

  const HINT = {
    todos: "Todo lo pendiente de la cola",
    empate: "Mismo código, los súper publican tamaños distintos y ninguno tiene mayoría",
    atipico: "Un súper publica un tamaño distinto al resto",
    pendiente: "Pendientes a los que todavía no les marcaste tamaño aquí",
    correccion_ia: "La IA los resolvió con las fotos, pero la búsqueda web dice otro tamaño",
    ia: "Lo que la IA resolvió sola (para auditar)",
  };
  const chipCounts = {};
  const chips = FILTERS.map(([key, label]) => {
    const badge = h("span", { class: "rv-chip-count" }, "…");
    chipCounts[key] = badge;
    const chip = h("button", { type: "button", class: "rv-chip", title: HINT[key], "aria-pressed": String(review.filter === key) },
      label, " ", badge);
    chip.addEventListener("click", () => {
      review.filter = key;
      for (const other of chips) other.setAttribute("aria-pressed", String(other === chip));
      renderList();
      // La lista cambia arriba de lo que se estaba viendo: volver al inicio de la lista.
      if (chipsBar.getBoundingClientRect().top < 0) chipsBar.scrollIntoView({ block: "start" });
    });
    return chip;
  });
  const chipsBar = h("div", { class: "rv-chips", role: "group", "aria-label": "Filtrar" }, chips);
  const showing = h("p", { class: "rv-showing muted small", role: "status", "aria-live": "polite" });

  function copyButton(text, label = "Copiar") {
    const button = h("button", { type: "button", class: "rv-copy", title: `Copiar ${text}` }, label);
    button.addEventListener("click", async () => {
      const copied = await copyText(text);
      button.textContent = copied ? "Copiado ✓" : "No se pudo";
      setTimeout(() => { button.textContent = label; }, 1500);
    });
    return button;
  }

  function offerRow(item, offer) {
    const search = STORE_SEARCH[offer.store];
    const own = storeCode(offer);
    const photos = photoUrls(offer);
    const reading = offerReading(offer);
    const links = search && offer.store !== "comisariato_los_andes"
      ? searchVariants(item.gtin).map(({ label, code }) => h("a", {
        class: `rv-search${own === code ? " own" : ""}`, href: search(code), target: "_blank", rel: "noopener noreferrer",
        title: own === code ? `Buscar ${code} (así lo publica este súper)` : `Buscar ${code}`,
      }, `${label} díg.`))
      : [search ? h("a", { class: "rv-search", href: search(""), target: "_blank", rel: "noopener noreferrer" }, "Catálogo ↗") : null];
    const sharedFrom = photos && offer.foto_de && offer.foto_de !== offer.store ? (RETAILER_NAMES[offer.foto_de] || offer.foto_de) : null;
    const thumb = photos
      ? h("a", { class: "rv-thumb", href: photos.full, target: "_blank", rel: "noopener noreferrer", title: "Abrir la foto en grande" },
        h("img", { src: photos.thumb, alt: `Foto de ${RETAILER_NAMES[offer.store] || offer.store}: ${offer.name}`, loading: "lazy", width: "96", height: "96" }))
      : h("span", { class: "rv-thumb empty" }, "Sin foto");
    return h("div", { class: `rv-offer${offer.excluded ? " out" : ""}${reading?.kind === "alert" ? " alert" : ""}` },
      thumb,
      h("div", { class: "rv-offer-body" },
        h("div", { class: "rv-offer-main" },
          h("span", {},
            h("strong", {}, RETAILER_NAMES[offer.store] || offer.store),
            h("span", { class: "rv-source" }, offer.name)),
          h("span", { class: "rv-size", title: "Tamaño publicado por el súper" }, offer.size || "sin tamaño")),
        reading ? h("span", { class: `rv-reading ${reading.kind}`, title: reading.title }, reading.label) : null,
        sharedFrom ? h("span", { class: "rv-shared" }, `Foto compartida con la de ${sharedFrom}`) : null,
        h("details", { class: "rv-more" },
          h("summary", {}, "Buscar en el súper (respaldo)"),
          h("div", { class: "rv-codes" },
            own ? h("span", { class: "rv-code" }, h("span", { class: "muted" }, "Código del súper: "), h("code", {}, own), copyButton(own)) : null,
            h("span", { class: "rv-links" }, links)))));
  }

  function rowFor(item) {
    const entry = review.decisions[item.gtin] || {};
    const decided = decisionOf(entry);
    const sent = review.sent.has(item.gtin);
    const update = (changes) => {
      review.decisions[item.gtin] = { ...(review.decisions[item.gtin] || {}), ...changes };
      row.replaceWith(rowFor(item));
      renderCount();
    };
    const value = h("input", { type: "text", inputmode: "decimal", placeholder: "Tamaño", "aria-label": "Tamaño correcto", value: entry.value ?? "" });
    value.addEventListener("change", () => update({ value: value.value.trim(), different: false }));
    const unit = h("select", { "aria-label": "Unidad" },
      h("option", { value: "" }, "Unidad"),
      SIZE_UNITS.map((key) => h("option", { value: key, selected: entry.unit === key }, UNIT_LABEL[key])));
    unit.addEventListener("change", () => update({ unit: unit.value, different: false }));
    const different = h("button", { type: "button", class: "rv-pick rv-different", "aria-pressed": String(Boolean(entry.different)) },
      "No es el mismo producto");
    different.addEventListener("click", () => update({ different: !entry.different }));
    const note = h("input", { type: "text", maxlength: "200", placeholder: "Nota (opcional): p. ej. la etiqueta dice 5 fl oz", "aria-label": "Nota", value: entry.note ?? "" });
    note.addEventListener("change", () => update({ note: note.value }));
    const picks = sizeOptions(item).map((option) => {
      const active = !entry.different && entry.value === option.value && entry.unit === option.unit;
      const button = h("button", { type: "button", class: "rv-pick", "aria-pressed": String(active), title: option.stores.join(", ") },
        `${option.value} ${UNIT_LABEL[option.unit] || option.unit}`, h("span", { class: "muted" }, ` (${option.stores.length})`));
      button.addEventListener("click", () => update({ value: option.value, unit: option.unit, different: false }));
      return button;
    });
    const suggestion = aiSuggestion(item);
    let aiNode = null;
    if (suggestion?.kind === "suggest") {
      const active = !entry.different && entry.value === suggestion.value && entry.unit === suggestion.unit;
      aiNode = h("button", {
        type: "button", class: `rv-pick rv-ai ${suggestion.status}`, "aria-pressed": String(active),
        title: suggestion.title || "Leído en la foto del producto",
      }, suggestion.label);
      aiNode.addEventListener("click", () => update({ value: suggestion.value, unit: suggestion.unit, different: false }));
    } else if (suggestion) {
      aiNode = h("span", { class: "rv-ai-note", title: suggestion.title }, suggestion.label);
    }
    const web = webSuggestion(item);
    let webNode = null;
    if (web?.kind === "suggest") {
      const active = !entry.different && entry.value === web.value && entry.unit === web.unit;
      const pick = h("button", { type: "button", class: "rv-pick rv-web", "aria-pressed": String(active), title: web.title },
        web.label, h("span", { class: "muted" }, web.matches ? ` (${web.matches})` : " (ninguno)"));
      pick.addEventListener("click", () => update({
        value: web.value, unit: web.unit, different: false, note: entry.note || `Fuente web: ${web.url}`.slice(0, 200),
      }));
      webNode = h("span", { class: "rv-web-wrap" }, pick,
        h("a", { class: "rv-web-source", href: web.url, target: "_blank", rel: "noopener noreferrer", title: web.url }, `${web.host || "fuente"} ↗`));
    } else if (web) {
      webNode = h("span", { class: "rv-ai-note", title: web.title }, web.label);
    }
    const row = h("article", { class: `rv-row${decided ? " done" : ""}${sent ? " sent" : ""}` },
      h("div", {},
        h("h2", { class: "rv-name" }, item.name),
        h("p", { class: "rv-meta" }, h("span", { class: "rv-gtin" }, "GTIN-14 ", h("code", {}, item.gtin), copyButton(item.gtin)),
          ` · ${KIND_LABEL[item.kind] || item.kind}`),
        h("div", { class: "rv-offers" }, item.offers.map((offer) => offerRow(item, offer)))),
      h("div", { class: "rv-decide" },
        sent ? h("p", { class: "rv-sent" }, "Enviado: se aplica en el próximo refresco.") : null,
        h("p", { class: "rv-label" }, "Tamaño correcto"),
        item.ia?.status === "auto"
          ? h("p", { class: "rv-auto" }, "La IA guardó este tamaño sola. Si está mal, elige el correcto y guarda: tu decisión reemplaza la de la IA.")
          : null,
        item.kind === "correccion_ia" && typeof item.web?.nota === "string"
          ? h("p", { class: "rv-auto" }, `⚠️ ${item.web.nota}`)
          : null,
        h("div", { class: "rv-picks" }, aiNode, webNode, picks, different),
        h("div", { class: "rv-manual" }, value, unit),
        note));
    return row;
  }

  function visibleItems() {
    return itemsFor(review.filter, review.queue, review.decisions, review.sent);
  }

  function renderCount() {
    const pending = pendingDecisions().length;
    const items = review.queue?.items || [];
    const waiting = items.filter((item) => review.sent.has(item.gtin)).length;
    const total = items.length - waiting;
    const auto = review.queue?.auto_resueltas?.length || 0;
    const counts = review.queue ? filterCounts(review.queue, review.decisions, review.sent) : null;
    for (const [key, badge] of Object.entries(chipCounts)) badge.textContent = counts ? counts[key].toLocaleString("es-HN") : "…";
    count.textContent = `${pending} por enviar · ${review.sent.size} enviados · ${total} en la cola · ${auto} resueltos por IA`;
    send.disabled = review.sending || pending === 0;
    const progress = reviewProgress(review.queue, waiting);
    progressText.textContent = review.queue
      ? `Progreso de revisión: ${progress.done.toLocaleString("es-HN")}/${progress.total.toLocaleString("es-HN")} productos (${progress.pct}%)`
      : "Progreso de revisión: cargando…";
    progressFill.style.width = `${progress.pct}%`;
    progressTrack.setAttribute("aria-valuenow", String(progress.pct));
  }

  function renderList() {
    const notice = photoNotice(review.queue);
    photoNote.textContent = notice;
    photoNote.hidden = !notice;
    if (review.error) {
      fill(list, h("p", { class: "rv-status warn" }, review.error));
    } else if (!review.queue) {
      fill(list, h("p", { class: "muted" }, "Cargando la cola…"));
    } else {
      const items = visibleItems();
      const label = FILTERS.find(([key]) => key === review.filter)?.[1] || "";
      showing.textContent = `Mostrando ${items.length.toLocaleString("es-HN")} · ${label}`;
      fill(list, items.length ? items.map(rowFor) : h("p", { class: "muted" }, "No hay productos en esta vista."));
    }
    renderCount();
  }

  function showStatus(text, kind = "", link = null) {
    const check = kind === "ok" ? h("span", { class: "rv-check", "aria-hidden": "true" }, "✓") : null;
    fill(status, check, text, link ? [" ", h("a", { href: link, target: "_blank", rel: "noopener noreferrer" }, "Ver ejecución ↗")] : null);
    status.className = `rv-status ${kind}`;
  }

  send.addEventListener("click", async () => {
    const decisions = pendingDecisions();
    if (!token.value.trim()) { showStatus("Pega tu llave de GitHub antes de guardar.", "warn"); token.focus(); return; }
    review.sending = true;
    renderCount();
    showStatus(`Enviando ${decisions.length} decisiones…`);
    try {
      const result = await sendSizeDecisions(token.value, decisions);
      const at = new Date().toISOString();
      for (const item of decisions) { review.sent.add(item.gtin); review.sentAt[item.gtin] = at; }
      writeSent(review.sentAt);
      showStatus(`Listo: ${result.sent} decisiones enviadas. Ya no aparecen aquí; GitHub las guarda y la cola pública se actualiza sola en unos 10 minutos.`, "ok", SIZE_DECISIONS_RUNS_URL);
    } catch (error) {
      // Llave rechazada: no se guarda una llave que no sirve.
      if (error.status === 401 || error.status === 403) forgetToken();
      showStatus(error.message, "warn");
    } finally {
      review.sending = false;
      renderList();
    }
  });

  const page = h("div", { class: "page rv-page" },
    h("header", { class: "rv-head" },
      h("div", {},
        h("h1", {}, "Revisión de tamaños"),
        h("p", { class: "muted" }, "Mismo código de barras, distinto tamaño publicado. Lo claro lo resolvió la IA; aquí queda lo que no pudo leer o tiene conflicto. Mira las fotos y elige el tamaño correcto.")),
      h("div", { class: "rv-auth" },
        h("label", { for: "rv-token" }, "Llave de GitHub"),
        token,
        h("label", { class: "rv-remember", for: "rv-remember" }, remember, " Recordar en este dispositivo"),
        forget,
        h("p", { class: "rv-hint muted small" },
          "Usa una llave fine-grained sólo para este repositorio, con permiso de Actions y vencimiento corto."))),
    h("div", { class: "rv-progress" }, progressText, progressTrack),
    photoNote,
    chipsBar,
    showing,
    list,
    h("div", { class: "rv-bar" }, h("div", { class: "rv-bar-in" }, count, status, send)));
  fill(main, page);
  renderList();

  if (!review.queue && !review.loading) {
    review.loading = loadSizeQueue().then((queue) => {
      review.queue = queue;
      review.error = null;
      review.sentAt = pendingSent(readSent(), queue);
      writeSent(review.sentAt);
      review.sent = new Set(Object.keys(review.sentAt));
    })
      .catch((error) => { review.error = error.message; })
      .finally(() => { review.loading = null; if (page.isConnected) renderList(); });
  } else if (review.loading) {
    review.loading.then(() => { if (page.isConnected) renderList(); });
  }
}
